# gemini_quant 작업 인수인계 프롬프트 (Gemini Antigravity용)

> 이 문서는 Claude 세션의 토큰 사용량이 과다해질 경우, 동일 작업을 Gemini Antigravity로
> 이어가기 위한 자기완결적 브리핑입니다. 아래 내용을 그대로 프롬프트로 붙여넣으세요.

---

## 프로젝트 개요

`D:\workspace_py\gemini_quant` — 한국 주식시장(KRX) 대상 AI 기반 자동 매매 시스템.
- `stock_trader/`: 전략 산출 → 주문 집행 → 리스크 관리 (핵심)
- `secretary/`: Google Calendar/Tasks 연동, 대화 기록 관리
- 텔레그램 챗봇 UI, Gemini API(Function Calling) + 로컬 TF-IDF/Naive Bayes 혼합 AI
- 실행 환경: **Windows, `py` 런처 사용** (`python`/`python3` 별칭이 깨져 있음).
  `PYTHONPATH=<repo-root>` 설정 필요. 콘솔 인코딩은 cp949라 한글 출력 시
  `PYTHONIOENCODING=utf-8` 필요.
- 상세 아키텍처는 [CODEBASE.md](CODEBASE.md), 전략 설명은 [README_STRATEGY.md](README_STRATEGY.md) 참조.
  단, 최근 변경으로 일부 내용이 stale할 수 있으니 코드가 우선.

## 지금까지 진행한 작업 (시간 순)

1. **보안·매매 안전성 버그 수정** (커밋 `29cc1d6`):
   텔레그램 발신자 미검증(누구나 주문 가능했음) → CHAT_ID 검증 추가.
   예수금 미차감으로 중복 주문 가능하던 버그 수정. 주문 접수를 체결로 잘못 기록하던 것을
   계좌 재조회 기반 체결가 추정으로 수정. 리스크 감시(Hard/Trailing Stop)를 별도 스레드로 분리해
   레이트리밋 백오프에 블로킹되지 않게 함. `market_halt` 서킷브레이커를 DB에 영속화.
   raw sqlite3 호출을 DbRepository로 일원화.

2. **일일 마감 보고 정확도 수정**: 보고가 DB 사본만 읽어서 실계좌와 어긋나던 문제.
   `summary_trader.py`가 이제 실계좌 API를 직접 조회하고, 보고 시점에 `reconciler.reconcile()`로
   DB 포지션도 대사함. API 실패 시 "DB 기준" 라벨을 명시하고 폴백.

3. **전략 로직 개선 (Gemini 분석 기반 교차검증 후 진행)**:
   - 14:30 마감 임박 강제매수에 추격 상한(`MAX_CHASE_PCT`) 추가 — 목표가 대비 너무 벌어지면 매수 보류
   - BEAR 국면 포지션 축소 파라미터(`BEAR_POSITION_FACTOR`, 기본 0.5) 추가
   - 예수금 부족 시 남은 매수 신호 수로 공평 분할(기존엔 앞 순번이 다 가져감)
   - **부수 발견 및 수정**: 브로커 API 실패 시 반환하는 "전부 0" 폴백 응답을 계좌 리셋으로
     오판해 정상 DB 포지션을 전량 삭제할 위험이 있었음 → reconciler에 가드 추가

4. **`backtest_multifactor.py` 백테스터 구축**: STOCK_MULTIFACTOR 전략의 워크포워드 백테스터.
   프로덕션 모듈(`indicators.py`, `market_features.py`, `scorer.py`)을 재사용해 look-ahead
   없이 재현. 단, **펀더멘털 팩터(EPS/DART/수급 = 스코어 가중치의 75%)는 과거 시점 데이터가
   없어 중립 고정** — 기술적 코어(RSI/BB/트레일링/교체규율)만 검증 가능한 한계가 있음.

5. **백테스트 기반 알파 실험** (30종목, 2024-01~2026-07, KOSPI 벤치마크 +180%):

   | 실험 | 총수익 | Sharpe | MDD | 노출도 |
   |---|---|---|---|---|
   | 기존(트레일링 1.5×ATR, top5, 비중10%) | +19.3% | 1.35 | -4.2% | 12.2% |
   | 트레일링 2.5×ATR·상한8% | +21.5% | 1.41 | -4.1% | 13.3% |
   | top8·비중15% | +35.5% | 1.22 | -8.4% | 27.2% |
   | 위 2개 결합 | **+46.6%** | **1.44** | -8.2% | 30.3% |

   **핵심 진단**: 전략의 매매 자체는 건강함(승률 54~57%, PF 1.7~1.8). 문제는
   **자금의 85~90%가 항상 현금으로 노는 것**. 진입 조건이 엄격하고 유니버스가
   30종목뿐이라 신호가 드묾. 상승장에서 수익률 격차의 대부분이 이 "유휴 현금"에서 발생.

6. **위 실험 결과를 라이브 코드에 반영 (진행 중 — 아직 커밋 안 됨)**:
   - `strategy_engine.py`의 개별 종목 관리신호(매도 판정)가 트레일링스탑을
     **하드코딩 1.5×ATR/상한5%**로 계산하고 있었는데, 이는 `auto_trader.py`의 실시간 감시
     (DB `CHANDELIER_ATR_MULT`=2.5/상한8%)와 **기준이 달라 이중 감시 비일관 상태**였음.
     → `strategy_engine.py`도 동일 DB 키를 쓰도록 통일 (`CHANDELIER_ATR_MULT`,
     `TRAILING_MIN_DROP`, `TRAILING_MAX_DROP`을 `_load_hyperparams`에 추가).
   - `TOP_N`(5→8), `TARGET_WEIGHT`(10%→15%)를 하이퍼파라미터화. **단, ETF_TREND
     프로파일은 이 실험 대상이 아니므로 TARGET_WEIGHT는 STOCK_MULTIFACTOR 프로파일에만
     적용되도록 분기 처리함** (`if self.profile != ETF_TREND:`).
   - `db_repository.py`의 `strategy_hyperparams` 초기값에 `CHANDELIER_ATR_MULT=2.5`,
     `TRAILING_MIN_DROP=2.0`, `TRAILING_MAX_DROP=8.0`, `TOP_N=8.0`, `TARGET_WEIGHT=0.15` 추가.
   - `backtest_multifactor.py`의 `BacktestConfig` 기본값도 라이브와 동기화 (top_n=8,
     target_weight=0.15, chandelier_atr_mult=2.5, trailing_max_drop=8.0), 개별 스탑
     파라미터 6개를 실험용으로 config화함.
   - 검증: `py -m py_compile` 통과, 파라미터 로딩 유닛 검증(ETF 프로파일 격리 포함),
     `pytest stock_trader/tests/` **44개 전부 통과**. 최종 확인용 전체 유니버스(30종목,
     2024-01~2026-07) 백테스트 결과 확보 완료:

     ```
     총수익률: +39.85% (KOSPI: +180.02%) | CAGR: +14.24% | MDD: -7.92% | Sharpe: 1.28
     거래 369건, 승률 56.6%, PF 1.73, 평균 노출도 29.0%
     청산: trailing_stop 119건(+11.27%) / overshooting_partial 112건(+8.38%) /
           hard_stop 70건(-7.43%) / replacement 68건(-0.76%)
     ```

     **주의 — 실험 ⑦(+46.57%)과의 차이**: 이 최종 수치는 실험 ②(트레일링 통일)+⑥(배치 확대)만
     반영한 것으로, 실험 ④(`replacement_grace_days=3`, 교체유예)는 **1·2순위 범위 밖이라
     라이브 코드에 반영하지 않았음**. 실험 ④는 단독으로는 중립(-0.6%p)이었지만 ⑦ 결합
     실험에서는 +6.7%p를 더 기여했음 — 배치가 커지면(top8) 교체 회전이 잦아지고, 유예가
     그 노이즈성 교체를 걸러주는 상호작용으로 추정됨. **다음 세션에서 우선 검토할 후보**:
     `strategy_engine.py`의 교체 매도 판정(`generate_management_signals` 내
     `top_tickers` 이탈 즉시 매도 로직)에 `REPLACEMENT_GRACE_DAYS` 하이퍼파라미터를
     추가해 `backtest_multifactor.py`의 `replacement_grace_days`와 동일 로직으로
     맞추면 실험 ⑦ 수준(+46%대)에 근접할 가능성이 있음. 단, 이 역시 반드시 백테스트로
     먼저 재검증 후 반영할 것 (강세장 구간 특유의 효과일 수 있음).

## 지금 상태 (git)

```
git status --short
 M stock_trader/core/backtest_multifactor.py
 M stock_trader/core/strategy_engine.py
 M stock_trader/data/db_repository.py
```

**아직 커밋되지 않음.** 최종 백테스트는 위에서 확인 완료(+39.85%, MDD -7.92%, Sharpe 1.28,
테스트 44개 통과). MDD가 -4%→-8%로 커진 것은 의도된 트레이드오프(노출 확대의 대가)이니
되돌리지 말 것. 이어받는 즉시 할 일:
1. `git diff`로 위 변경 내역을 재확인 (`strategy_engine.py`, `backtest_multifactor.py`,
   `db_repository.py` 3개 파일)
2. **사용자에게 커밋 여부를 먼저 물을 것.** 아직 "커밋해줘" 요청을 받지 않은 상태.
   승인되면 커밋 메시지에 "strategy_engine 트레일링을 auto_trader와 통일",
   "TOP_N/TARGET_WEIGHT 하이퍼파라미터화 — 백테스트로 검증(+19.3%→+39.85%, Sharpe
   1.35→1.28, MDD -4.2%→-7.92%)" 명시. 배포 시 DB에 신규 하이퍼파라미터 키
   5개(`CHANDELIER_ATR_MULT`, `TRAILING_MIN_DROP`, `TRAILING_MAX_DROP`, `TOP_N`,
   `TARGET_WEIGHT`)가 자동 추가됨을 안내.
3. **주의**: 사용자가 명시적으로 요청하기 전엔 `git push` 하지 말 것. 이전 세션에서도
   커밋 후 사용자 확인 후 푸시하는 흐름을 따름.

## 다음 우선순위 (사용자에게 이미 제시했고 동의 대기 중이거나 다음 단계로 유력함)

**1순위(진행 중, 위 참조): 트레일링 통일 + 배치 확대** — 완료 후 커밋 대기.

**2순위: 유휴 현금 처리 (코어-새틀라이트 구조)** — 가장 큰 잠재력.
배치를 8종목/15%로 늘려도 노출 30%가 한계(신호 자체가 드묾). 나머지 ~70% 현금을
BULL 국면에서 지수 ETF(KODEX 200 등)에 파킹하고 개별 종목 신호 발생 시 회수하는 구조.
기존 `ETF_TREND` 프로파일 로직(`config.py`의 `ETF_UNIVERSE`, `strategy_engine.py`의
`_generate_etf_buy_signals` 등)을 재활용할 여지가 있으나, STOCK_MULTIFACTOR와 ETF_TREND를
동시에 운용하는 자금 배분 설계가 필요한 중규모 작업. **설계 먼저 사용자와 합의 후 구현할 것.**

**3순위: 펀더핸털 팩터 IC 분석** — 라이브 스코어 가중치의 75%(EPS/DART/수급)가
백테스트로 검증 불가능한 상태. 다행히 매 신호마다 `trade_history.features`(JSON)에
전체 팩터 스냅샷이 저장되고 있어서, 실거래 데이터가 쌓이면(현재는 초기 단계라 부족할 수 있음)
각 팩터와 실현 수익률의 상관(Information Coefficient)을 계산해 가중치 재조정 근거를 마련할 수 있음.
데이터 축적 상황을 `SELECT COUNT(*) FROM trade_history` 등으로 먼저 확인.

**참고 — 기각/보류된 제안**:
- Gemini Antigravity가 이전에 제안한 SQLAlchemy ORM 도입, Redis/ZeroMQ IPC 교체는
  이 프로젝트 규모(로컬 단일 사용자 SQLite, 저사양 기기)엔 과하다고 판단해 보류함.
  DbRepository 패턴 강화로 충분.
- `strategy_engine.py` 모듈 분해(God Class 해소)는 별도 세션(`task_33b004ed`)에서
  이미 상당 부분 진행되어 `naver_finance.py`, `scorer.py`, `market_features.py`로 분리 완료됨
  (git log의 `1dd0537 feat(strategy): refine trading logic and refactor core modules` 참고).

## 작업 시 지켜야 할 컨벤션

- **테스트 우선**: 코드 변경 후 반드시 `py -m pytest stock_trader/tests/ -q
  --ignore=stock_trader/tests/test_strategy_real_data.py` 실행 (마지막 파일은 실거래
  API 필요해 오프라인 환경에서 스킵). 새 로직은 기능 테스트나 회귀 테스트로 직접 검증
  (`py -c "..."` 스니펫으로 즉석 검증한 전례 다수 — `stock_trader/tests/test_reconciler.py`,
  `test_backtest_multifactor.py` 스타일 참고).
- **전략 파라미터는 하드코딩 대신 `strategy_hyperparams` DB 테이블 + 하이퍼파라미터 방식**으로
  추가할 것. 텔레그램 `[SYSTEM_UPDATE] KEY=VALUE` 명령으로 실거래 중 즉시 조정 가능해야 함.
  `auto_trader.py`의 `get_hyperparams()`, `strategy_engine.py`의 `_load_hyperparams()` 패턴 참고.
- **버그 수정과 전략 가설 변경을 구분**할 것. 전자는 바로 고치되, 후자(스탑 배수, 배치 크기 등)는
  `backtest_multifactor.py`로 먼저 수치 검증 후에만 라이브에 반영.
- **Windows 환경**: `py` 런처 사용, `python`/`python3` 명령은 실패함. 백테스트처럼 오래 걸리는
  명령은 background 실행 권장.
- git 커밋은 사용자가 명시 요청할 때만. `git push`도 별도 명시 요청 필요 (이전 세션에서
  커밋과 푸시를 분리된 요청으로 처리한 전례).
- 커밋 메시지는 "왜"를 설명하는 스타일 유지 (기존 로그: `29cc1d6`, `44c2712` 등 참고).

## 이 세션에서 아직 못 한 것 / 확인 필요

- 위 "지금 상태(git)"의 3개 파일 최종 백테스트 재확인 및 커밋
- 코어-새틀라이트(유휴 현금 처리) 설계는 사용자와 논의 전 단계
- 펀더멘털 팩터 IC 분석은 시작 전 단계
