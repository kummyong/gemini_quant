import logging
import datetime
from typing import Dict
import FinanceDataReader as fdr

logger = logging.getLogger("MacroIndicators")

# 국면 판정 파라미터 (라이브 fetch_market_regime과 백테스터가 동일 값을 공유하는 단일 소스).
# 변형 실험(run_backtest_regime_variants.py + run_backtest_sensitivity.py, 2026-07-19):
# 확인일 3일(confirm=3)이 전체 기간 수익률은 최고(220%)였으나 민감도가 불연속(c2=107%로 붕괴)
# 하고 워크포워드 전반기(2014-2020H1)에서 오히려 악화(-15.5%→-29.0%)되어 과적합으로 판단, 기각.
# 버퍼/기울기창 확대(V3~V5)도 모두 성과 악화. 따라서 구형 판정(버퍼 0, 확인 0)을 유지한다.
# 이 함수/파라미터는 판정 로직의 라이브-백테스트 단일화를 위한 것이며 동작은 구형과 동일.
REGIME_MA_SHORT = 20
REGIME_MA_LONG = 120
REGIME_BUFFER_PCT = 0.0   # MA120 대비 중립 지대 폭 (0.02 = ±2%)
REGIME_SLOPE_DAYS = 1     # MA20 상승 판정에 사용하는 비교 시차(일)
REGIME_CONFIRM_DAYS = 0   # 국면 전환에 필요한 연속 확인일 (0/1 = 즉시 전환)


def compute_regime_series(close, buffer_pct: float = REGIME_BUFFER_PCT,
                          slope_days: int = REGIME_SLOPE_DAYS,
                          confirm_days: int = REGIME_CONFIRM_DAYS):
    """종가 시리즈(pd.Series)로부터 일별 국면(BULL/NEUTRAL/BEAR) 시리즈를 계산한다.

    - buffer_pct: MA120 상하 buffer_pct 이내는 중립 지대로 취급해 경계 진동을 줄인다.
    - slope_days: MA20 기울기를 slope_days 시차로 판정한다 (1 = 전일 대비).
    - confirm_days: 원신호(raw)가 confirm_days일 연속 유지되어야 국면을 전환한다.
      0 또는 1이면 즉시 전환(히스테리시스 없음).
    라이브(fetch_market_regime)와 백테스터가 이 함수를 공유해 판정 로직 괴리를 방지한다.
    """
    import numpy as np
    import pandas as pd

    ma_s = close.rolling(REGIME_MA_SHORT).mean()
    ma_l = close.rolling(REGIME_MA_LONG).mean()
    rising = ma_s > ma_s.shift(slope_days)

    raw = np.select(
        [
            (close > ma_l * (1.0 + buffer_pct)) & rising,
            close < ma_l * (1.0 - buffer_pct),
        ],
        ["BULL", "BEAR"],
        default="NEUTRAL",
    )

    if confirm_days <= 1:
        return pd.Series(raw, index=close.index)

    out = []
    cur = "NEUTRAL"
    cand = None
    cnt = 0
    for r in raw:
        if r == cur:
            cand, cnt = None, 0
        else:
            if r == cand:
                cnt += 1
            else:
                cand, cnt = r, 1
            if cnt >= confirm_days:
                cur, cand, cnt = cand, None, 0
        out.append(cur)
    return pd.Series(out, index=close.index)

class MacroIndicatorProvider:
    """거시 파생 지표 (VIX 등) 수집 모듈"""
    
    def fetch_vix_disparity(self) -> Dict[str, float]:
        """FRED로부터 VIXCLS(CBOE Volatility Index)를 가져와 20일 이동평균 대비 괴리율을 계산한다.
        반환: {"vix_current": float, "vix_ma20": float, "vix_disparity": float}
        """
        result = {"vix_current": 20.0, "vix_ma20": 20.0, "vix_disparity": 0.0}
        try:
            # 최근 60일치 데이터 가져오기 (영업일 기준 20MA 계산을 위해 충분히)
            start_date = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
            df = fdr.DataReader('FRED:VIXCLS', start=start_date)
            
            if df is not None and not df.empty:
                df = df.dropna()
                if len(df) >= 20:
                    current_vix = float(df.iloc[-1].iloc[0]) # VIXCLS 열
                    ma20_vix = float(df.iloc[:, 0].rolling(window=20).mean().iloc[-1])
                    disparity = ((current_vix - ma20_vix) / ma20_vix) * 100.0
                    
                    result["vix_current"] = round(current_vix, 2)
                    result["vix_ma20"] = round(ma20_vix, 2)
                    result["vix_disparity"] = round(disparity, 2)
                    logger.info(f"🌐 [거시지표] VIX: {result['vix_current']}, 20일선 대비 괴리율: {result['vix_disparity']}%")
        except Exception as e:
            logger.error(f"VIX 지표 수집 실패: {e}")
            
        return result

    def fetch_market_regime(self) -> str:
        """KS11(코스피) 지수를 분석하여 대세 상승(BULL), 하락(BEAR), 횡보(NEUTRAL) 국면을 판독한다.

        판정 로직은 compute_regime_series()로 일원화되어 백테스터와 동일하다.
        확인일(REGIME_CONFIRM_DAYS) 상태머신이 과거 시계열 전체로부터 결정되므로
        별도 상태 저장 없이 매 호출이 결정적(deterministic)이다."""
        try:
            # 120일 이동평균선 + 확인일 상태머신 계산을 위해 넉넉히 500일치 데이터 수집
            start_date = (datetime.datetime.now() - datetime.timedelta(days=500)).strftime("%Y-%m-%d")
            df = fdr.DataReader('KS11', start=start_date)

            if df is not None and not df.empty and len(df) >= 120:
                regime = str(compute_regime_series(df['Close']).iloc[-1])
                if regime == "BULL":
                    logger.info(f"🌐 [거시지표] KOSPI 대세 상승(BULL) 국면 감지! (지수: {df['Close'].iloc[-1]:.2f})")
                elif regime == "BEAR":
                    logger.info(f"🌐 [거시지표] KOSPI 하락/약세(BEAR) 국면 감지. 보수적 운용 필요.")
                return regime

        except Exception as e:
            logger.error(f"KOSPI 시장 국면(Regime) 판독 실패: {e}")

        return "NEUTRAL"
