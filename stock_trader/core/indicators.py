import pandas as pd
import numpy as np

def atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """df must contain 'High', 'Low', 'Close' columns. Returns ATR Series."""
    if df.empty:
        return pd.Series(0.0, index=df.index)
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def sma(series: pd.Series, period: int) -> pd.Series:
    """Returns Simple Moving Average Series."""
    return series.rolling(window=period).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Returns RSI Series."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def bollinger(series: pd.Series, period: int, std: float):
    """Returns (lower_band, middle_band, upper_band) Series."""
    middle = series.rolling(window=period).mean()
    rolling_std = series.rolling(window=period).std()
    lower = middle - std * rolling_std
    upper = middle + std * rolling_std
    return lower, middle, upper

def drawdown_from_peak(series: pd.Series) -> pd.Series:
    """Calculates drawdown from peak in percent."""
    cum_max = series.cummax()
    return ((series - cum_max) / (cum_max + 1e-9)) * 100

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """df must contain 'High', 'Low', 'Close' columns. Returns ADX Series."""
    high = df['High']
    low = df['Low']
    close = df['Close']

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move

    minus_dm = pd.Series(0.0, index=df.index)
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    tr_smooth = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / (tr_smooth + 1e-9))
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / (tr_smooth + 1e-9))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    return dx.rolling(window=period).mean()

def obv(df: pd.DataFrame) -> pd.Series:
    """OBV (On-Balance Volume) 계산"""
    if df.empty or len(df) < 2:
        return pd.Series(0.0, index=df.index)
    close = df['Close']
    volume = df['Volume']
    
    # 주가 상승시 거래량 양수, 하락시 음수, 보합시 0
    direction = np.sign(close.diff()).fillna(0)
    obv_val = (volume * direction).cumsum()
    return obv_val

def is_obv_rising(df: pd.DataFrame, short_window: int = 5, long_window: int = 20) -> bool:
    """OBV의 단기(5일) 이동평균이 장기(20일) 이동평균을 상회하고 있는지 (상승 추세 판단)"""
    if len(df) < long_window:
        return False
    obv_series = obv(df)
    obv_short = obv_series.rolling(window=short_window).mean().iloc[-1]
    obv_long = obv_series.rolling(window=long_window).mean().iloc[-1]
    return obv_short > obv_long
