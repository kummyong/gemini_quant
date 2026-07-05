import pytest
import pandas as pd
import numpy as np
from stock_trader.core.signals import StrategyParams, trend_ok, chandelier_stop_level, check_exit, can_reenter, market_lockout
import stock_trader.core.indicators as ind

def create_mock_df(size=250, start_val=10000.0, trend_type="up"):
    """상승/하락/횡보 모의 데이터프레임 생성"""
    dates = pd.date_range(start="2023-01-01", periods=size, freq="D")
    prices = []
    curr = start_val
    np.random.seed(42)
    for i in range(size):
        if trend_type == "up":
            curr += np.random.normal(50, 10)
        elif trend_type == "down":
            curr += np.random.normal(-50, 10)
        elif trend_type == "whipsaw":
            # 급격한 상승 후 하락
            if i < 150:
                curr += 100
            else:
                curr -= 150
        else:
            curr += np.random.normal(0, 10)
        prices.append(max(100.0, curr))
        
    df = pd.DataFrame({
        "Open": prices,
        "High": [p * 1.02 for p in prices],
        "Low": [p * 0.98 for p in prices],
        "Close": prices,
        "Volume": [10000] * size
    }, index=dates)
    return df

def test_indicators():
    df = create_mock_df(size=50)
    # ATR
    atr_series = ind.atr(df, period=14)
    assert len(atr_series) == 50
    assert not pd.isna(atr_series.iloc[-1])
    
    # SMA
    sma_series = ind.sma(df['Close'], 20)
    assert len(sma_series) == 50
    
    # RSI
    rsi_series = ind.rsi(df['Close'], 14)
    assert len(rsi_series) == 50
    
    # Bollinger
    low, mid, high = ind.bollinger(df['Close'], 20, 2.0)
    assert len(low) == 50
    assert len(high) == 50

def test_trend_ok():
    params = StrategyParams(trend_sma_period=50)
    
    df_up = create_mock_df(size=100, trend_type="up")
    assert trend_ok(df_up, params) is True
    
    df_down = create_mock_df(size=100, trend_type="down")
    assert trend_ok(df_down, params) is False

def test_chandelier_stop_level():
    df = create_mock_df(size=50)
    params = StrategyParams(atr_period=14, atr_multiplier=3.0)
    peak_close = 15000.0
    
    stop = chandelier_stop_level(df, peak_close, params)
    atr_val = ind.atr(df, 14).iloc[-1]
    expected_stop = peak_close - atr_val * 3.0
    assert abs(stop - expected_stop) < 1e-5

def test_check_exit():
    df = create_mock_df(size=50, start_val=10000, trend_type="down")
    params = StrategyParams(atr_period=10, atr_multiplier=2.0)
    
    position = {
        "pur_pric": 12000.0,
        "max_profit_rate": 5.0, # peak_close = 12600
        "peak_close": 12600.0
    }
    
    # 주가가 급격히 하락했으므로 스탑 탈출 신호가 발생해야 함
    exit_sig = check_exit(df, position, params)
    assert exit_sig is not None
    assert exit_sig["action"] == "SELL"
    assert "Chandelier Exit" in exit_sig["reason"]

def test_reentry_cooldown():
    df = create_mock_df(size=100, trend_type="up")
    params = StrategyParams(stop_cooldown_days=5, reentry_sma_period=20, trend_sma_period=50)
    
    dates_str = df.index.strftime('%Y-%m-%d').tolist()
    
    # 오늘 날짜
    today = dates_str[-1]
    
    # 1. 쿨다운 기간 안쪽 (3일 전 정지됨)
    stop_record = {"stop_date": dates_str[-3]}
    assert can_reenter(df, stop_record, params, today) is False
    
    # 2. 쿨다운 기간 통과 (10일 전 정지됨)
    stop_record = {"stop_date": dates_str[-10]}
    assert can_reenter(df, stop_record, params, today) is True

def test_reentry_cooldown_stop_before_window():
    """stop_date가 df 윈도우의 첫 날짜보다 과거이면 쿨다운 경과로 처리되어야 한다.
    (회귀 테스트: 과거 버그는 dates 리스트에서 stop_date를 찾지 못해 영구적으로 False를 반환했음)"""
    df = create_mock_df(size=100, trend_type="up")
    params = StrategyParams(stop_cooldown_days=5, reentry_sma_period=20, trend_sma_period=50)
    today = df.index[-1].strftime('%Y-%m-%d')

    stop_date_before_window = (df.index[0] - pd.Timedelta(days=30)).strftime('%Y-%m-%d')
    stop_record = {"stop_date": stop_date_before_window}

    assert can_reenter(df, stop_record, params, today) is True

def test_reentry_cooldown_stop_before_window_blocked_by_trend():
    """쿨다운은 경과 처리되더라도 추세/재진입 SMA 조건을 통과하지 못하면 여전히 차단되어야 한다"""
    df = create_mock_df(size=100, trend_type="down")
    params = StrategyParams(stop_cooldown_days=5, reentry_sma_period=20, trend_sma_period=50)
    today = df.index[-1].strftime('%Y-%m-%d')

    stop_date_before_window = (df.index[0] - pd.Timedelta(days=30)).strftime('%Y-%m-%d')
    stop_record = {"stop_date": stop_date_before_window}

    assert can_reenter(df, stop_record, params, today) is False

def test_market_lockout():
    df = create_mock_df(size=100, trend_type="up")
    params = StrategyParams()
    
    # 1. 비활성 락아웃
    state_inactive = {"active": False, "since": None, "reason": None}
    assert market_lockout(df, state_inactive, params) is False
    
    # 2. 활성 락아웃, 종가가 SMA50 위에 있음 (해제되어야 함)
    # up 트렌드이므로 종가는 SMA50보다 큼
    state_active = {"active": True, "since": "2023-01-01", "reason": "test"}
    assert market_lockout(df, state_active, params) is False
    
    # 3. 활성 락아웃, 종가가 SMA50 아래에 있음 (락아웃 유지)
    df_down = create_mock_df(size=100, trend_type="down")
    assert market_lockout(df_down, state_active, params) is True

def test_pure_module_imports():
    # signals.py와 indicators.py에 sqlite3 또는 requests가 임포트되어 있지 않은지 확인
    import stock_trader.core.signals as signals
    import stock_trader.core.indicators as indicators
    import sys
    
    # signals.py 소스코드 텍스트 검사
    with open(signals.__file__, 'r', encoding='utf-8') as f:
        src = f.read()
        assert "import sqlite3" not in src
        assert "import requests" not in src
        
    with open(indicators.__file__, 'r', encoding='utf-8') as f:
        src = f.read()
        assert "import sqlite3" not in src
        assert "import requests" not in src
