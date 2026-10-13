"""
Technical indicators on pandas Series (replaces pandas-ta, which breaks on numpy 2).

EMA and Wilder averages are seeded with the simple average of the first
`length` values. pandas-ta seeds RSI/ATR slightly differently, so the first
few dozen values can differ; with 30 days of 15-minute history the values the
strategy uses are effectively identical.
"""

import numpy as np
import pandas as pd


def _seeded_ewm(series: pd.Series, length: int, alpha: float) -> pd.Series:
    s = series.astype(float)
    out = pd.Series(np.nan, index=s.index)
    valid = s.dropna()
    if len(valid) < length:
        return out
    seeded = valid.copy()
    seeded.iloc[: length - 1] = np.nan
    seeded.iloc[length - 1] = valid.iloc[:length].mean()
    out.loc[valid.index] = seeded.ewm(alpha=alpha, adjust=False).mean()
    return out


def ema(close: pd.Series, length: int) -> pd.Series:
    return _seeded_ewm(close, length, 2.0 / (length + 1))


def rma(series: pd.Series, length: int) -> pd.Series:
    return _seeded_ewm(series, length, 1.0 / length)


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.astype(float).diff()
    avg_gain = rma(delta.clip(lower=0.0), length)
    avg_loss = rma((-delta).clip(lower=0.0), length)
    out = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    out = out.where(avg_loss != 0, np.where(avg_gain == 0, 50.0, 100.0))
    return out.where(avg_gain.notna() & avg_loss.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    return line, ema(line, signal)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.astype(float).shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return rma(tr, length)
