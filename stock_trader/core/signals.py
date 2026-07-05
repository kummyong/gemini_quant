from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np
import stock_trader.core.indicators as ind

@dataclass
class StrategyParams:
    atr_period: int = 20
    atr_multiplier: float = 3.0        # 트레일링 폭 = ATR × 배수
    trend_sma_period: int = 200        # 진입 필터
    reentry_sma_period: int = 50       # 재진입 조건
    stop_cooldown_days: int = 5        # 스탑 후 최소 대기 거래일

def trend_ok(df: pd.DataFrame, params: StrategyParams) -> bool:
    """종가 > SMA(trend_sma_period) 검증"""
    if df.empty or len(df) < params.trend_sma_period:
        return False
    closes = df['Close']
    sma_series = ind.sma(closes, params.trend_sma_period)
    return float(closes.iloc[-1]) > float(sma_series.iloc[-1])

def chandelier_stop_level(df: pd.DataFrame, peak_close: float, params: StrategyParams) -> float:
    """샹들리에 스탑 레벨 계산: peak_close - ATR(atr_period) * atr_multiplier"""
    if df.empty:
        return 0.0
    atr_series = ind.atr(df, params.atr_period)
    latest_atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
    return peak_close - latest_atr * params.atr_multiplier

def check_exit(df: pd.DataFrame, position: Dict[str, Any], params: StrategyParams) -> Optional[Dict[str, Any]]:
    """종가가 샹들리에 스탑 레벨을 하회하면 청산 신호 반환"""
    if df.empty:
        return None
    latest_close = float(df['Close'].iloc[-1])
    
    # max_profit_rate 등을 이용하여 매수 후 최고 종가(peak_close) 추적
    # 실시간 포트폴리오 관리 또는 백테스트에서 전달받은 peak_close를 사용
    purchase_price = float(position.get("pur_pric", 0.0))
    max_profit_rate = float(position.get("max_profit_rate", 0.0))
    # 최고 수익률 기준 역산하거나 전달된 peak_close 직접 활용
    peak_close = position.get("peak_close")
    if peak_close is None:
        if purchase_price > 0:
            peak_close = purchase_price * (1 + max_profit_rate / 100.0)
        else:
            peak_close = float(df['Close'].max()) # Fallback
            
    stop_level = chandelier_stop_level(df, peak_close, params)
    
    if latest_close < stop_level:
        return {
            "action": "SELL",
            "reason": f"Chandelier Exit 작동 (종가 {latest_close:,.0f} < 스탑레벨 {stop_level:,.0f}, 최고가 대비 하락)",
            "stop_level": stop_level,
            "peak_close": peak_close
        }
    return None

def can_reenter(df: pd.DataFrame, stop_record: Optional[Dict[str, Any]], params: StrategyParams, today: str) -> bool:
    """재진입 가능 여부 확인: 쿨다운일 경과 AND 종가 > SMA(reentry_sma_period) AND trend_ok"""
    # 1. 쿨다운 검사
    if stop_record:
        stop_date_str = stop_record.get("stop_date")
        if stop_date_str:
            # df의 DatetimeIndex 또는 string 형식 index에서 stop_date 이후의 거래일 계산
            dates = df.index.strftime('%Y-%m-%d').tolist()
            if stop_date_str in dates:
                stop_idx = dates.index(stop_date_str)
                # 현재 봉의 인덱스 - 정지 인덱스 = 경과된 거래일 수
                elapsed_days = len(df) - 1 - stop_idx
                if elapsed_days < params.stop_cooldown_days:
                    return False
            else:
                # 데이터프레임 내에 정지일이 없으면 보수적으로 경과하지 않은 것으로 처리
                return False

    # 2. 종가 > SMA(reentry_sma_period) 검사
    if df.empty or len(df) < params.reentry_sma_period:
        return False
    closes = df['Close']
    reentry_sma = ind.sma(closes, params.reentry_sma_period)
    if float(closes.iloc[-1]) <= float(reentry_sma.iloc[-1]):
        return False

    # 3. trend_ok 검사
    return trend_ok(df, params)

def market_lockout(index_df: pd.DataFrame, lockout_state: Dict[str, Any], params: StrategyParams) -> bool:
    """글로벌 시장 lockout 검사 및 해제 조건 확인
    lockout_state: {'active': bool, 'since': str, 'reason': str}
    해제 조건: 지수 종가 > SMA(50)
    """
    if not lockout_state.get("active"):
        return False

    if index_df.empty or len(index_df) < 50:
        # 데이터가 부족하면 안전을 위해 락아웃 해제하지 않음
        return True

    closes = index_df['Close']
    sma50 = ind.sma(closes, 50)
    
    # 지수 종가가 SMA(50)을 돌파하면 lockout 해제(False 반환)
    if float(closes.iloc[-1]) > float(sma50.iloc[-1]):
        return False
        
    return True
