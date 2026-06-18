import numpy as np
import pandas as pd


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average: linearly weighted, most recent bar has highest weight."""
    weights = np.arange(1, period + 1, dtype=float)
    total = weights.sum()
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / total, raw=True)


def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV → weekly (week ending Friday)."""
    return df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(how="all")


def resample_to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV → monthly (month-end)."""
    return df.resample("ME").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(how="all")
