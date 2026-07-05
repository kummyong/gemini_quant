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
