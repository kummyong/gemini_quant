"""OHLCV 기반 기술 지표 피처 계산.

strategy_engine.fetch_market_data()의 인라인 기술 지표 블록을 분리한 모듈.
indicators.py의 순수 함수(rsi/bollinger/atr/sma)를 재사용하며,
DB/네트워크 I/O 없이 DataFrame만 입력받는다.
"""
import logging
from typing import Dict

import pandas as pd

import stock_trader.core.indicators as ind

logger = logging.getLogger("MarketFeatures")


def compute_technical_features(df_hist: pd.DataFrame, current_price: float, bb_std: float,
                               kospi_return_90d: float = 0.0, name: str = "") -> Dict:
    """단일 종목의 기술 지표 피처를 계산한다.

    데이터 부족(20봉 미만) 또는 계산 실패 시 기존 엔진과 동일한 기본값을 반환한다.
    """
    feats = {
        "rsi": 50.0,
        "lower_band": 0.0,
        "upper_band": 0.0,
        "is_bottoming": True,
        "relative_momentum": 0.0,
        "atr_pct": 3.0,
        "is_aligned": False,
        "is_under_ma120": False,
        "is_vcp": False,
        "return_5d": 0.0
    }
    try:
        if not df_hist.empty and len(df_hist) >= 20:
            closes = df_hist['Close']

            # 1. RSI 계산
            rsi_series = ind.rsi(closes, period=14)
            feats["rsi"] = float(rsi_series.iloc[-1])

            # 2. 볼린저 밴드
            lower, _, upper = ind.bollinger(closes, period=20, std=bb_std)
            feats["lower_band"] = float(lower.iloc[-1])
            feats["upper_band"] = float(upper.iloc[-1])

            # 3. MACD 필터 (떨어지는 칼날 방지)
            ema12 = closes.ewm(span=12, adjust=False).mean()
            ema26 = closes.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            macd_hist = macd - signal

            if len(macd_hist) >= 2:
                feats["is_bottoming"] = bool((macd_hist.iloc[-1] > macd_hist.iloc[-2]) or (macd.iloc[-1] > signal.iloc[-1]))
            else:
                feats["is_bottoming"] = True

            # 4. 상대 모멘텀 계산 (90일 수익률 비교)
            if len(closes) >= 60:
                stock_return_90d = ((closes.iloc[-1] - float(closes.iloc[-60])) / float(closes.iloc[-60])) * 100
                feats["relative_momentum"] = stock_return_90d - kospi_return_90d
            else:
                feats["relative_momentum"] = 0.0

            # 5. ATR 변동성 계산 (14일 기준)
            atr_val = ind.atr(df_hist, period=14).iloc[-1]
            feats["atr_pct"] = (atr_val / closes.iloc[-1]) * 100 if closes.iloc[-1] > 0 else 3.0

            # 6. 이동평균선 정배열/역배열
            if len(closes) >= 120:
                ma20_val = ind.sma(closes, 20).iloc[-1]
                ma60_val = ind.sma(closes, 60).iloc[-1]
                ma120_val = ind.sma(closes, 120).iloc[-1]
                feats["is_aligned"] = (ma20_val > ma60_val) and (ma60_val > ma120_val)
                feats["is_under_ma120"] = current_price < ma120_val

            # 7. VCP (거래량 급감 필터)
            if len(closes) >= 20:
                avg_vol20 = ind.sma(df_hist['Volume'], 20).iloc[-1]
                today_vol = df_hist['Volume'].iloc[-1]
                if (current_price <= feats["lower_band"] * 1.01) and (today_vol < 0.6 * avg_vol20):
                    feats["is_vcp"] = True

            # 8. 5일 수익률 (섹터 모멘텀용)
            if len(closes) >= 6:
                feats["return_5d"] = ((closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]) * 100
            else:
                feats["return_5d"] = 0.0

    except Exception as tech_e:
        logger.warning(f"[{name}] 기술 지표 계산 실패: {tech_e}")

    return feats
