"""
Shared 15-minute bar cache backed by one batched yfinance download.

Every caller (scan, status, positions, the background engine) reads from this
cache, so polls never trigger their own downloads and Yahoo sees one request
per refresh instead of one per symbol per poll. yfinance data is delayed and
unofficial; prices carry an `as_of` so the UI can show staleness.
"""

import logging
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .market import LAST_BAR_START, MARKET_OPEN

log = logging.getLogger("aetherkite.marketdata")

NIFTY = "^NSEI"
SENSEX = "^BSESN"
# NSE symbols whose Yahoo ticker differs. TATAMOTORS is the pre-demerger name; it
# is also mapped to TMPV for orders (see app.order_symbol).
TICKER_MAP = {"TATAMOTORS": "TMPV.NS"}

Fetcher = Callable[[List[str]], Dict[str, pd.DataFrame]]


def yahoo_ticker(symbol: str) -> str:
    if symbol.startswith("^"):
        return symbol
    return TICKER_MAP.get(symbol, f"{symbol}.NS")


def normalize_frame(df: pd.DataFrame, session_only: bool = True) -> Optional[pd.DataFrame]:
    """Lower-case OHLCV columns, IST-naive index, rows with missing prices dropped."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    needed = ["open", "high", "low", "close"]
    if any(c not in df.columns for c in needed):
        return None
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[needed + ["volume"]].astype(float).dropna(subset=needed)
    index = pd.DatetimeIndex(df.index)
    if index.tz is not None:
        index = index.tz_convert("Asia/Kolkata").tz_localize(None)
    df.index = index
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if session_only:
        t = df.index.time
        df = df[(t >= MARKET_OPEN) & (t <= LAST_BAR_START)]
    return df if not df.empty else None


def yfinance_fetch(symbols: List[str], period: str = "30d", interval: str = "15m") -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    tickers = {yahoo_ticker(s).upper(): s for s in symbols}
    data = yf.download(list(tickers), period=period, interval=interval, group_by="ticker",
                       auto_adjust=False, threads=True, progress=False, timeout=10,
                       multi_level_index=True)
    out: Dict[str, pd.DataFrame] = {}
    if data is None or data.empty or not isinstance(data.columns, pd.MultiIndex):
        return out
    for ticker in data.columns.get_level_values(0).unique():
        symbol = tickers.get(str(ticker).upper())
        if symbol is None:
            continue
        frame = normalize_frame(data[ticker])
        if frame is not None:
            out[symbol] = frame
    return out


class MarketData:
    def __init__(self, fetcher: Optional[Fetcher] = None, max_age: float = 60.0):
        self.fetcher = fetcher or yfinance_fetch
        self.max_age = max_age
        self._cache: Dict[str, Tuple[float, Optional[pd.DataFrame]]] = {}
        self._lock = threading.Lock()        # protects _cache
        self._fetch_lock = threading.Lock()  # one download at a time; others wait and reuse it

    def get(self, symbols: Iterable[str], max_age: Optional[float] = None) -> Dict[str, Optional[pd.DataFrame]]:
        symbols = list(dict.fromkeys(symbols))
        age = self.max_age if max_age is None else max_age
        with self._fetch_lock:
            now = time.time()
            with self._lock:
                stale = [s for s in symbols if s not in self._cache or now - self._cache[s][0] > age]
            if stale:
                try:
                    fetched = self.fetcher(stale)
                except Exception as e:
                    log.warning("market data fetch failed for %d symbols: %s", len(stale), e)
                    fetched = None
                with self._lock:
                    for s in stale:
                        if fetched is not None:
                            self._cache[s] = (now, fetched.get(s))
                        elif s not in self._cache:
                            self._cache[s] = (now, None)
        with self._lock:
            return {s: self._cache.get(s, (0, None))[1] for s in symbols}

    def cached(self, symbol: str) -> Optional[pd.DataFrame]:
        with self._lock:
            return self._cache.get(symbol, (0, None))[1]

    def last_price(self, symbol: str) -> Tuple[Optional[float], Optional[str]]:
        """Close of the most recent (possibly still forming) bar, and that bar's start time."""
        df = self.cached(symbol)
        if df is None or df.empty:
            return None, None
        return float(df["close"].iloc[-1]), df.index[-1].strftime("%Y-%m-%d %H:%M")

    def clear(self):
        with self._lock:
            self._cache.clear()
