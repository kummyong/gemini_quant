# Gemini-Quant ETF Trend & Multifactor Trading Strategy Manual

본 문서는 Gemini-Quant 시스템의 전략 프로파일 구조, 신호 판단 및 청산 로직, 재진입(쿨다운)/락아웃 규칙, 그리고 성과 백테스트 재현 방법을 설명합니다.

---

## 1. 전략 프로파일 구조

시스템은 두 가지 실행 프로파일을 지원하며, `stock_trader/config.py`의 `ACTIVE_STRATEGY_PROFILE` 변수(또는 환경변수)를 통해 스위칭할 수 있습니다.

### STOCK_MULTIFACTOR (개별 종목 멀티팩터)
*   **유니버스**: 거래대금 및 시가총액 기준 필터링된 KOSPI/KOSDAQ 주식 30종
*   **분석 방법**: DART 공시 데이터 기반 재무 건전성 및 성장성 스코어링 + 네이버 수급 데이터 파싱 + RSI/BB 단기 기술지표 조합
*   **스케줄**: 개장 전 (08:30) 실행하여 당일 매매 지시 생성

### ETF_TREND (ETF 추세추종 - 신규)
*   **유니버스**: KODEX 200, TIGER 미국나스닥100, TIGER 미국S&P500 등 주요 ETF 6종
*   **분석 방법**: 5년 이상의 가격 데이터를 활용한 순수 기술적 지표 추세 분석 (DART, 네이버 크롤링 등 외부 API 스킵)
*   **스케줄**: 장 마감 후 (15:45) 실행하여 당일 종가 기준으로 지표 산출 및 신호 생성

---

## 2. 신호 및 청산 로직 (Signals & Exits)

모든 신호 판단 모듈은 `stock_trader/core/signals.py` 및 `stock_trader/core/indicators.py` 내의 **순수 함수** 레이어로 분리되어 실전 엔진(`strategy_engine.py`)과 백테스터(`backtest.py`)가 동일하게 사용합니다.

### 2.1 진입 조건 (trend_ok)
*   종가가 200일 단순이동평균선(SMA 200)을 상회할 때만 매수 진입이 허용됩니다.
    $$\text{Close} > \text{SMA}(200)$$

### 2.2 청산 조건 (Chandelier Exit)
*   기존의 고정 %p 클램핑 트레일링 스탑의 한계를 보완하기 위해 ATR 변동성 기반의 **샹들리에 스탑(Chandelier Exit)**을 적용했습니다.
*   매수 이후 달성한 최고 종가($\text{Peak Close}$) 대비 ATR의 $N$배수만큼 하락 시 청산됩니다.
    $$\text{Stop Level} = \text{Peak Close} - \text{ATR}(20) \times 3.0$$

---

## 3. 재진입(쿨다운) 및 시장 락아웃 규칙

### 3.1 쿨다운 (Cooldown Limit)
*   샹들리에 스탑으로 손절 청산된 종목은 청산일 기준 **최소 5 거래일** 동안 재매수가 금지됩니다. (휩쏘 및 손절 직후 재물림 현상 차단)
*   5 거래일 경과 후, 다음 2가지 조건이 모두 만족될 때 재진입이 가능합니다.
    1.  종가 > SMA(50)
    2.  종가 > SMA(200) (진입 필터)

### 3.2 시장 락아웃 (Market Lockout)
*   서킷브레이커 작동(KODEX 200 지수 하루 -3% 이하 폭락) 또는 시스템 전체 글로벌 하드스탑 시 시장 락아웃 상태가 활성화됩니다.
*   **락아웃 해제 조건**: 기준 지수(KODEX 200, 069500) 종가가 SMA(50)을 재상향 돌파할 때까지 모든 신규 매수 신호가 전면 차단됩니다.

---

## 4. 백테스트 재현 방법

로컬 DB의 5개년 OHLCV 데이터를 사용하여 5가지 비교 전략의 성과를 시뮬레이션할 수 있습니다.

### 실행 명령어
```bash
# TIGER 미국나스닥100(133690) 종목에 대해 2022-05-01부터 전체 전략 백테스트 수행
$env:PYTHONPATH="."
py -m stock_trader.core.backtest --ticker 133690 --start 2022-05-01 --strategy all
```

### 출력 리포트 항목
*   **CAGR**: 연평균 복리 수익률
*   **MDD**: 최대 낙폭
*   **Trades**: 총 거래 횟수
*   **Trade Cost**: 총 거래 수수료 및 슬리피지 비용 (왕복 0.3% 적용)
*   **Out Ratio**: 시장 밖 현금 대기 기간 비율
*   **CSV 결과 저장**: 실행 완료 후 `backtest_report_<ticker>.csv` 파일로 세부 지표가 자동 저장됩니다.
