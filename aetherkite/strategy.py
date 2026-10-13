"""
The quantitative entry rule and the Nifty regime filter.

Both work only on *closed* bars (see market.closed_bars). The rule is the
same one the app has always used; see backtest/ on the feat/backtest-harness
branch for evidence before trusting it with money.
"""

import math
from typing import Dict, Optional, Tuple

import pandas as pd

from . import indicators as ind

EMA_TREND = 200
RSI_LOW, RSI_HIGH = 40.0, 65.0
STOP_ATR, TARGET_ATR = 1.5, 3.0
REGIME_EMA = 20


def analyze(df: pd.DataFrame, symbol: str) -> Dict:
    """Evaluates the last closed bar. `df` must contain only closed bars."""
    if df is None or len(df) < EMA_TREND + 2:
        return {"status": "INSUFFICIENT_DATA", "symbol": symbol, "bars": 0 if df is None else len(df)}

    close = df["close"]
    ema_trend = ind.ema(close, EMA_TREND)
    rsi = ind.rsi(close, 14)
    macd, signal = ind.macd(close, 12, 26, 9)
    atr = ind.atr(df["high"], df["low"], close, 14)

    last, prev = -1, -2
    values = [close.iloc[last], ema_trend.iloc[last], rsi.iloc[last], macd.iloc[last],
              signal.iloc[last], macd.iloc[prev], signal.iloc[prev], atr.iloc[last]]
    if any(v is None or not math.isfinite(float(v)) for v in values) or atr.iloc[last] <= 0:
        return {"status": "INSUFFICIENT_DATA", "symbol": symbol, "bars": len(df)}

    trend_ok = close.iloc[last] > ema_trend.iloc[last]
    crossed_up = macd.iloc[prev] <= signal.iloc[prev] and macd.iloc[last] > signal.iloc[last]
    rsi_ok = RSI_LOW <= rsi.iloc[last] <= RSI_HIGH

    return {
        "status": "OK",
        "symbol": symbol,
        "signal": "BUY" if (trend_ok and crossed_up and rsi_ok) else "NEUTRAL",
        "close": float(close.iloc[last]),
        "ema_trend": float(ema_trend.iloc[last]),
        "trend_bullish": bool(trend_ok),
        "rsi": round(float(rsi.iloc[last]), 1),
        "macd": float(macd.iloc[last]),
        "macd_signal": float(signal.iloc[last]),
        "macd_crossover": bool(crossed_up),
        "atr": float(atr.iloc[last]),
        "candle_time": df.index[last].to_pydatetime(),
    }


def nifty_regime(df: Optional[pd.DataFrame]) -> Tuple[Optional[bool], str]:
    """
    Long entries are allowed when Nifty's last closed 15m bar is above both its
    20-bar EMA and the previous session's close. Returns (None, reason) when
    data is missing so callers can fail closed.
    """
    if df is None or len(df) < REGIME_EMA + 1:
        return None, "Nifty data unavailable"
    last_ts = df.index[-1]
    prev_session = df[df.index.normalize() < last_ts.normalize()]
    if prev_session.empty:
        return None, "No previous Nifty session in data"
    last = float(df["close"].iloc[-1])
    ema20 = float(ind.ema(df["close"], REGIME_EMA).iloc[-1])
    prev_close = float(prev_session["close"].iloc[-1])
    ok = last > ema20 and last > prev_close
    detail = f"Nifty {last:,.1f} vs EMA{REGIME_EMA} {ema20:,.1f} and prev close {prev_close:,.1f}"
    return ok, detail
