# gemini-quant 코드베이스 분석

> 분석 기준일: 2026-06-11

---

## 목차

1. [시스템 개요](#시스템-개요)
2. [디렉토리 구조](#디렉토리-구조)
3. [전체 아키텍처](#전체-아키텍처)
4. [데이터 흐름](#데이터-흐름)
5. [모듈별 상세 분석](#모듈별-상세-분석)
   - [진입점 / 워치독](#진입점--워치독)
   - [config](#config)
   - [core — 전략·매매 엔진](#core--전략매매-엔진)
   - [ai — 인텐트 라우터 & 스킬](#ai--인텐트-라우터--스킬)
   - [broker — 증권사 추상화 계층](#broker--증권사-추상화-계층)
   - [communication — 알림 & 챗봇](#communication--알림--챗봇)
   - [data — DART & DB](#data--dart--db)
   - [monitoring — 시스템 감시](#monitoring--시스템-감시)
   - [secretary — Google Calendar & 대화 기록](#secretary--google-calendar--대화-기록)
6. [핵심 의존 관계 요약](#핵심-의존-관계-요약)
7. [외부 라이브러리 목록](#외부-라이브러리-목록)

---

## 시스템 개요

한국 주식시장(KRX) 대상의 **AI 기반 자동 매매 시스템**으로, 크게 두 서브시스템으로 구성된다.

| 서브시스템 | 디렉토리 | 역할 |
|-----------|---------|------|
| **stock_trader** | `gemini-quant/stock_trader/` | 전략 산출 → 주문 집행 → 리스크 관리 |
| **secretary** | `gemini-quant/secretary/` | Google Calendar/Tasks 연동, 대화 기록 관리 |

사용자 인터페이스는 **텔레그램 챗봇**이며, AI 의사결정에는 **Gemini API**(Function Calling)와 **로컬 TF-IDF + Naive Bayes** 모델을 혼합하여 사용한다.

---

## 디렉토리 구조

```
gemini-quant/
├── unified_watchdog.py          # 최상위 프로세스 감시·스케줄러
├── start.sh / stop.sh
├── run_watchdog.sh
├── requirements.txt
├── stock_trader/
│   ├── config.py                # 전역 경로 상수
│   ├── core/
│   │   ├── strategy_engine.py   # 전략 엔진 (핵심)
│   │   ├── auto_trader.py       # 주문 집행 엔진
│   │   ├── stock_universe.py    # 종목 유니버스 정의
│   │   ├── universe_feeder.py   # 유니버스 갱신 피더
│   │   ├── summary_trader.py    # 일일 마감 보고
│   │   ├── trainer.py           # 로컬 NLP 모델 재학습
│   │   └── korean_market_calendar.py
│   ├── ai/
│   │   ├── local_intent_router.py  # 온디바이스 인텐트 분류
│   │   ├── agent_skills.py         # Gemini Function Calling 스킬
│   │   └── daily_brief.py
│   ├── broker/
│   │   ├── broker_interface.py     # 추상 인터페이스 (ABC)
│   │   ├── broker_factory.py       # 싱글톤 팩토리
│   │   ├── adapters/
│   │   │   ├── korea_investment_adapter.py
│   │   │   └── mock_adapters.py
│   │   ├── api/
│   │   │   └── korea_investment_api.py
│   │   └── kiwoom/
│   │       ├── kiwoom_adapter.py
│   │       ├── kiwoom_api_core.py
│   │       └── Kiwoom_MCP_Server/
│   ├── communication/
│   │   ├── telegram_listener.py    # 텔레그램 챗봇 데몬
│   │   ├── telegram_utils.py       # Bot API 유틸
│   │   ├── notification_gateway.py # IPC → 텔레그램 게이트웨이
│   │   ├── ipc_messenger.py        # TCP 소켓 IPC
│   │   └── mcp_telegram_server.py  # MCP 서버 (텔레그램)
│   ├── data/
│   │   ├── db_repository.py        # SQLite 데이터 액세스 레이어
│   │   ├── dart_api.py             # DART OpenAPI 클라이언트
│   │   ├── dart_financial_scorer.py
│   │   ├── dart_disclosure_monitor.py
│   │   └── dart_universe_expander.py
│   └── monitoring/
│       ├── system_monitor.py
│       ├── system_monitor_loop.py  # 모니터링 데몬
│       ├── system_trend_reporter.py
│       └── check_balance.py
└── secretary/
    ├── mcp_google_server.py        # MCP 서버 (Google API)
    ├── google_api_manager.py       # Google API CLI 도구
    ├── auto_sync_history.py        # 대화 기록 자동 동기화 데몬
    ├── save_history.py
    ├── search_history.py
    └── ...
```

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    unified_watchdog.py                           │
│  (프로세스 감시 + 스케줄: 전략엔진 08:30, 마감보고 15:40,        │
│   DART 공시 30분, 유니버스 확장 월1회)                           │
└───────┬──────────────────────────────┬───────────────────────────┘
        │ 서브프로세스 실행             │
        ▼                              ▼
┌───────────────┐              ┌────────────────────┐
│ strategy_     │  DB PENDING  │   auto_trader.py   │
│ engine.py     │ ──────────► │  (주문 집행 엔진)   │
│ (전략 산출)   │  신호        │  Trailing/Hard Stop│
└───────┬───────┘              └────────┬───────────┘
        │ IPC (TCP)                     │ Broker API
        ▼                              ▼
┌─────────────────┐           ┌─────────────────────┐
│ notification_   │           │   broker_factory    │
│ gateway.py      │           │   (키움/KIS/NH)      │
│ (이벤트→텔레그램)│           └─────────────────────┘
└─────────────────┘
        
┌─────────────────────────────────────────────────────────────────┐
│                  telegram_listener.py (챗봇 데몬)                │
│                                                                   │
│  사용자 메시지 → 인텐트 라우팅 (4단계 폴백)                      │
│  1. 상태 머신 (확인 대기 / 선택 대기)                            │
│  2. local_intent_router (TF-IDF + Naive Bayes)                   │
│  3. DB 문자 매칭 (training_data)                                 │
│  4. Gemini AI Function Calling                                    │
│              │                                                    │
│              ▼                                                    │
│        agent_skills.skill_router → 실제 기능 실행                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 데이터 흐름

### 매매 신호 파이프라인

```
KRX 시장 데이터 (FinanceDataReader)
네이버 금융 크롤링 (EPS, 수급)          ┐
DART API (재무제표, 공시)               ├─► strategy_engine
DB OHLCV 캐시                          ┘       │
                                               │ 멀티팩터 스코어링
                                               │ (EPS / 재무 / 수급 / 모멘텀 / VCP)
                                               ▼
                                        DB trade_signals
                                        (PENDING 상태)
                                               │
                                               ▼
                                        auto_trader (장중 루프)
                                               │
                                        키움증권 REST API
                                               │
                                        체결 → DB 기록 + 텔레그램 알림
```

### 종목 유니버스 갱신 흐름

```
universe_feeder (월 1회)
    │
    ├─ FDR KOSPI 시총 상위 200 수집
    ├─ DB trade_signals 갱신
    └─ active_universe.json 저장
                │
                ▼
    dart_universe_expander (월 1회)
    │   KRX 300 → DART 재무 필터
    │   (매출 1,500억↑, 영업이익 흑자, 부채비율 200%↓)
    └─► active_universe.json 편입

stock_universe.py
    └─ SAMPLE_TICKERS + active_universe.json 런타임 병합
    └─► strategy_engine, local_intent_router 참조
```

### NLP 모델 피드백 루프

```
사용자 피드백 (텔레그램)
    │
    ▼
save_training_feedback → DB training_data
    │
    ▼ (즉시 비동기)
trainer.retrain_model
    │  DB 피드백 + TRAIN_DATA 합산
    │  TF-IDF + MultinomialNB 재학습
    └─► MODEL_PATH (.pkl) 저장
                │
                ▼
    local_intent_router 로드 → 다음 메시지부터 반영
```

---

## 모듈별 상세 분석

---

### 진입점 / 워치독

#### `unified_watchdog.py`

- **역할**: 시스템 최상위 프로세스 관리자
- **감시 대상 5개 프로세스**:

| 프로세스 | 설명 |
|---------|------|
| `telegram_listener.py` | 챗봇 데몬 |
| `notification_gateway.py` | IPC → 텔레그램 게이트웨이 |
| `system_monitor_loop.py` | 시스템 모니터링 루프 |
| `auto_sync_history.py` | 대화 기록 동기화 |
| `mcp_google_server.py` | Google MCP 서버 |

- **스케줄**:

| 시각 | 작업 |
|------|------|
| 08:30 KST (영업일) | `strategy_engine.py` 실행 |
| 15:40 KST (영업일) | `summary_trader.py` 마감 보고 |
| 30분 간격 (장중) | `dart_disclosure_monitor.py` 공시 감시 |
| 매월 1일 | `dart_universe_expander.py` 유니버스 확장 |

- **재시작 간격**: 10초 주기 체크, 비정상 종료 시 즉시 재구동

---

### config

#### `stock_trader/config.py`

모든 모듈이 참조하는 전역 경로 상수 정의.

| 상수 | 용도 |
|------|------|
| `STOCK_TRADER_DIR` | stock_trader 루트 경로 |
| `DB_PATH` | SQLite DB 파일 경로 |
| `MODEL_PATH` | NLP 모델 pkl 파일 경로 |
| `LOG_DIR` | 로그 디렉토리 |
| `RUNTIME_DIR` | 런타임 상태 파일 디렉토리 |
| `SECRETARY_DIR` | secretary 루트 경로 |

---

### core — 전략·매매 엔진

#### `core/strategy_engine.py` ★ 핵심

- **역할**: 시장 국면 판단 → 종목 스코어링 → 매매 신호 생성 전체 파이프라인
- **주요 클래스**: `StrategyEngine`

**실행 순서**:
1. `_load_hyperparams` — DB에서 RSI·BB·Trailing Stop 파라미터 로드
2. `_determine_market_regime` — KOSPI 50일선 + ADX로 BULL/BEAR/SIDEWAY 판정
3. `check_market_circuit_breaker` — 코스피 -3% 이하 시 신규 매수 잠금
4. `fetch_market_data` — OHLCV + 네이버 크롤링(EPS/수급) + DART 재무 + 기술지표 수집
5. `calculate_scores` — 멀티팩터 가중 합산 스코어 산출
6. `fetch_current_holdings` — 보유 종목 로드 + max_profit_rate 갱신
7. `generate_management_signals` — 매도 신호 (Hard Stop / Trailing Stop / 오버슈팅 / 저효율 교체)
8. `update_signals` — DB 저장 + IPC 발행

**멀티팩터 구성**:

| 팩터 | 데이터 소스 |
|------|-----------|
| EPS 성장 | 네이버 금융 크롤링 |
| DART 재무 (매출/영업이익/부채/현금흐름) | DART API |
| 수급 (기관/외국인) | 네이버 금융 |
| 섹터 모멘텀 | FinanceDataReader |
| 상대 모멘텀 | FinanceDataReader |
| VCP (Volatility Contraction Pattern) | OHLCV 계산 |

---

#### `core/auto_trader.py`

- **역할**: 장중 실시간 루프 — PENDING 신호 집행 + 리스크 관리
- **주요 기능**:
  - **Chandelier Exit (Trailing Stop)**: `get_cached_atr_pct` — 14일 ATR% 기반
  - **Hard Stop**: -5% 손절
  - **서킷 브레이커**: KOSPI/KOSDAQ 실시간 변동률 (네이버 금융 API)
  - 신호 집행 → DB 기록 → 텔레그램 알림

---

#### `core/stock_universe.py`

- **역할**: 종목명↔코드 매핑 (`STOCK_MAP`) + 전략 대상 종목 리스트 (`SAMPLE_TICKERS`)
- `active_universe.json`의 동적 종목을 런타임에 병합하여 제공

---

#### `core/universe_feeder.py`

- **역할**: 코스피 시총 상위 200 종목을 수집해 DB·JSON에 적재
- **클래스**: `UniverseFeeder`
- FDR 실패 시 네이버 금융 크롤링으로 폴백

---

#### `core/summary_trader.py`

- **역할**: 장 마감 후 보유 현황·매매 이력·미처리 신호를 텔레그램으로 전송
- 주요 함수: `send_daily_summary_to_telegram`

---

#### `core/trainer.py`

- **역할**: 사용자 피드백을 반영한 로컬 NLP 모델 재학습
- **모델**: TF-IDF + MultinomialNB Pipeline (scikit-learn)
- `retrain_model` 호출 시 DB `training_data` + `TRAIN_DATA` 합산 → pkl 저장

---

#### `core/korean_market_calendar.py`

- 2025~2027년 KRX 휴장일 집합 + `is_market_holiday(dt)` 함수 제공

---

### ai — 인텐트 라우터 & 스킬

#### `ai/local_intent_router.py`

- **역할**: 온디바이스 한국어 자연어 인텐트 분류기
- **학습 데이터**: 2,500개 이상 한국어 예문 (`TRAIN_DATA`)
- **모델**: TF-IDF + Naive Bayes (MODEL_PATH에 pkl로 저장)
- `get_local_decision`, `get_top_n_decisions`, `router` 함수 제공
- **특징**: 인터넷 없이 디바이스 내에서 실시간 추론 가능

---

#### `ai/agent_skills.py`

- **역할**: Gemini Function Calling 스키마(`SYSTEM_TOOLS`)와 실제 실행 함수 정의
- **클래스**: `ResponseFormatter` — 텔레그램 마크다운 포맷 변환

**주요 스킬 함수**:

| 함수 | 기능 |
|------|------|
| `get_account_summary` | 계좌 예수금·평가금·손익 조회 |
| `get_balance` | 보유 종목 현황 |
| `get_stock_price` | 종목 현재가 |
| `place_order` | 매수·매도 주문 실행 |
| `get_order_history` | 당일 체결 내역 |
| `search_history` | 과거 대화 기록 검색 |
| `list_google_events` | 구글 캘린더 일정 조회 |
| `get_system_status` | CPU·메모리·배터리 상태 |
| `switch_ai_model` | Gemini 모델 교체 |
| `save_training_feedback` | 강화학습 피드백 저장 |
| `save_voc_request` | VOC 기능 요청 저장 |

---

#### `ai/daily_brief.py`

- Gemini 뉴스 요약을 받아 아침 증시 브리핑 텔레그램 전송

---

### broker — 증권사 추상화 계층

```
BrokerInterface (ABC)
    ├── KiwoomAdapter → KiwoomApiCore (키움증권 REST API)
    ├── KoreaInvestmentAdapter → KoreaInvestmentAPI (한투 REST API / Mock)
    └── NhInvestmentAdapter (Mock)

BrokerFactory (싱글톤)
    └── get_broker("KIWOOM") → KiwoomAdapter 인스턴스 반환
```

#### `broker/broker_interface.py`

공통 추상 메서드 4개: `get_account_summary`, `place_order`, `cancel_order`, `get_current_price`

#### `broker/broker_factory.py`

- 싱글톤 패턴으로 어댑터 인스턴스 캐싱
- 현재 활성 증권사: **키움증권 단일** (`get_active_brokers` 반환값)

#### `broker/kiwoom/kiwoom_api_core.py`

- `config.ini` 기반 MOCK/REAL 환경 로드
- OAuth2 토큰 관리 (401 재인증, 429 지수 백오프, return_code=3 재발급)
- 주요 엔드포인트:
  - 계좌 조회: `/api/dostk/acnt` (api_id: `kt00018`)
  - 매수: `/api/dostk/ordr` (api_id: `kt10000`)
  - 매도: `/api/dostk/ordr` (api_id: `kt10001`)

#### `broker/adapters/korea_investment_adapter.py`

- `use_mock=True`: 인메모리 포트폴리오 시뮬레이션
- `use_mock=False`: 실제 KIS REST API 연동

---

### communication — 알림 & 챗봇

#### `communication/telegram_listener.py` ★ 챗봇 데몬

- **역할**: 텔레그램 long polling → 인텐트 라우팅 → 응답 생성
- **인텐트 라우팅 4단계 폴백**:

```
1. 상태 머신 체크 (WAITING_CONFIRMATION / WAITING_SELECTION)
       ↓ 미해당
2. local_intent_router (TF-IDF+NB 로컬 모델, 신뢰도 임계값)
       ↓ 미해당
3. DB training_data 문자 겹침 매칭
       ↓ 미해당
4. Gemini API Function Calling (get_ai_teacher_decision)
```

- **즉시 재학습**: 피드백 수신 시 `retrain_model`을 별도 스레드에서 실행

---

#### `communication/ipc_messenger.py`

- **역할**: 프로세스 간 비동기 TCP 소켓 통신
- `IpcPublisher` (전략 프로세스 → 송신)
- `IpcListener` (게이트웨이 프로세스 → 수신, 콜백 호출)
- 동기 컨텍스트용 `send_message_sync` 래퍼 제공

---

#### `communication/notification_gateway.py`

- IPC 이벤트 타입 → 텔레그램 메시지 변환
- 이벤트 종류: `TRADE_SIGNAL`, `GLOBAL_STOP_LOSS`, `SYSTEM_ALERT` 등

---

#### `communication/mcp_telegram_server.py`

- FastMCP 기반 MCP 서버
- Tool: `telegram_send_message`, `telegram_get_updates`

---

#### `communication/telegram_utils.py`

- 공통 유틸: `send_telegram_message`, `send_telegram_photo`
- `TOKEN`, `CHAT_ID` 환경변수 공급 (dotenv)

---

### data — DART & DB

#### `data/db_repository.py` ★ 데이터 레이어

- **역할**: SQLite 전체 데이터 액세스 레이어 (9개 테이블)
- WAL 모드 + NORMAL 동기화 커넥션 관리

**테이블 구성**:

| 테이블 | 용도 |
|--------|------|
| `trade_signals` | 매매 신호 (PENDING/DONE/EXPIRED) |
| `portfolio_status` | 보유 종목 현황 |
| `strategy_hyperparams` | 전략 파라미터 |
| `ohlcv_data` | 종목별 OHLCV 캐시 |
| `training_data` | NLP 학습 피드백 |
| `system_metrics` | 하드웨어 메트릭 |
| `voc_requests` | 사용자 기능 요청 |
| (+ 2개) | |

---

#### `data/dart_api.py`

- DART OpenAPI 래퍼: 재무제표, 배당, 공시 검색, 대주주 조회
- 종목코드→고유번호 매핑 30일 캐시

---

#### `data/dart_financial_scorer.py`

- **4개 팩터 점수** 산출: 매출 성장률, 영업이익 성장률, 부채비율, 현금흐름 품질
- 대량보유(5% 룰) 지분 변동 → 수급 보너스 점수 (-10 ~ +15)
- 메모리 캐싱 포함

---

#### `data/dart_disclosure_monitor.py`

- 보유 종목 장중 30분 간격 공시 감시
- **키워드 감지**: 유상증자, 부도, 합병 등 심각도별 텔레그램 알림

---

#### `data/dart_universe_expander.py`

- 매월 1일 실행: KRX 300 → DART 재무 필터 → `active_universe.json` 편입
- **편입 기준**: 매출 1,500억↑, 영업이익 흑자, 부채비율 200%↓

---

### monitoring — 시스템 감시

#### `monitoring/system_monitor_loop.py` (데몬)

- 파일 락으로 중복 실행 방지
- **스케줄**:

| 주기 | 작업 |
|------|------|
| 1분 | 메트릭 수집 (system_monitor.py) |
| 매시 정각 | 트렌드 보고서 생성 |
| 08:00 | 장기 트렌드 브리핑 |
| 03:00 | NLP 모델 재학습 |

---

#### `monitoring/system_monitor.py`

- CPU, 메모리, 온도, 배터리 수집 (psutil + sysfs)
- DB 저장 + IPC 이벤트 발행

---

#### `monitoring/system_trend_reporter.py`

- 지정 기간(1h/24h/7d/30d) 메트릭 → matplotlib 이중 Y축 그래프 → 텔레그램 PNG 전송

---

#### `monitoring/check_balance.py`

- 활성 브로커 전체의 계좌 잔고 집계 출력 유틸리티

---

### secretary — Google Calendar & 대화 기록

#### `secretary/mcp_google_server.py` (MCP 서버)

FastMCP 기반, Google Calendar/Tasks 연동 MCP 서버.

| MCP Tool | 기능 |
|----------|------|
| `list_google_events` | 다가오는 캘린더 일정 조회 |
| `add_google_event` | 일정 추가 (주간 반복 지원) |
| `delete_google_event` | 일정 삭제 |
| `list_google_tasks` | 태스크 목록 조회 |
| `add_google_task` | 태스크 추가 |
| `search_history` | 과거 대화 DB 키워드 검색 |
| `sync_current_session` | 현재 세션 즉시 DB 저장 |
| `proactive_schedule_check` | 정기 일정 누락 자동 감지·등록 |

---

#### `secretary/auto_sync_history.py` (데몬)

- 파일 락으로 단일 인스턴스 보장
- 10분 주기로 `save_history.save_latest_session()` 호출 → SQLite DB 동기화

---

## 핵심 의존 관계 요약

```
config.py ◄─────────────── 거의 모든 모듈
db_repository.py ◄──────── strategy_engine, auto_trader, monitoring, dart_*
broker_interface.py ◄────── KiwoomAdapter, KoreaInvestmentAdapter
broker_factory.py ◄──────── auto_trader, strategy_engine, check_balance
ipc_messenger.py ◄───────── strategy_engine (Publisher), notification_gateway (Listener)
telegram_utils.py ◄──────── notification_gateway, summary_trader, dart_*
local_intent_router.py ◄─── telegram_listener, trainer.py (TRAIN_DATA)
agent_skills.py ◄────────── telegram_listener (skill_router)
stock_universe.py ◄──────── strategy_engine, local_intent_router, dart_universe_expander
dart_api.py ◄────────────── dart_financial_scorer, dart_disclosure_monitor, dart_universe_expander
```

---

## 외부 라이브러리 목록

| 라이브러리 | 주요 사용처 |
|-----------|-----------|
| `FinanceDataReader` | OHLCV 수집, 시총 조회, 현재가 |
| `requests` | 모든 REST API 호출 |
| `pandas` | 데이터 처리, OHLCV DataFrame |
| `scikit-learn` | TF-IDF + Naive Bayes NLP 모델 |
| `joblib` | 모델 pkl 직렬화 |
| `numpy` | 수치 연산 |
| `matplotlib` | 트렌드 차트 생성 |
| `beautifulsoup4` | 네이버 금융 크롤링 |
| `pytz` | KST 시간대 처리 |
| `psutil` | CPU/메모리/배터리 수집 |
| `mcp (FastMCP)` | MCP 서버 (텔레그램, Google) |
| `google-api-python-client` | Google Calendar/Tasks API |
| `google-auth-oauthlib` | Google OAuth2 인증 |
| `python-dotenv` | 환경변수 로드 |
| `sqlite3` | 로컬 DB (표준 라이브러리) |
| `asyncio` | 비동기 IPC 서버/클라이언트 |
