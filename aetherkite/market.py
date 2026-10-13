"""
NSE session rules, tick sizes and closed-bar selection.
"""

import datetime as dt
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Dict, Iterable

import pandas as pd

BAR = dt.timedelta(minutes=15)
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)
LAST_BAR_START = dt.time(15, 15)

# Price-banded tick sizes, written from memory of NSE's 2025 revision and NOT
# verified here. Every band is floored at Rs 0.05, so the result is always a
# multiple of both the old flat Rs 0.05 tick and the banded tick: rounding to it
# gives a valid price under either regime, at worst slightly coarser.
_TICK_BANDS = (
    (250.0, 0.01),
    (1000.0, 0.05),
    (5000.0, 0.10),
    (10000.0, 0.50),
    (20000.0, 1.00),
    (float("inf"), 5.00),
)
MIN_TICK = 0.05


def parse_hhmm(text: str, default: dt.time) -> dt.time:
    try:
        return dt.datetime.strptime(str(text).strip(), "%H:%M").time()
    except (TypeError, ValueError):
        return default


def tick_size(price: float) -> float:
    for upper, tick in _TICK_BANDS:
        if price < upper:
            return max(tick, MIN_TICK)
    return MIN_TICK


def round_to_tick(price: float, direction: str = "nearest", tick: float = None) -> float:
    """Rounds with Decimal arithmetic so 99.975 and 100.025 both round half-up."""
    t = Decimal(str(tick if tick is not None else tick_size(price)))
    mode = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[direction]
    steps = (Decimal(str(price)) / t).to_integral_value(rounding=mode)
    return float(steps * t)


def is_trading_day(day: dt.date, holidays: Iterable[str] = ()) -> bool:
    return day.weekday() < 5 and day.isoformat() not in set(holidays or ())


def session_rules(cfg: dict):
    entry_start = parse_hhmm(cfg.get("entry_start_time", "09:45"), dt.time(9, 45))
    if not cfg.get("morning_buffer_enabled", True):
        entry_start = MARKET_OPEN
    entry_cutoff = parse_hhmm(cfg.get("entry_cutoff_time", "14:00"), dt.time(14, 0))
    square_off = parse_hhmm(cfg.get("intraday_cutoff_time", "15:10"), dt.time(15, 10))
    return entry_start, entry_cutoff, square_off


def session_status(now: dt.datetime, cfg: dict) -> Dict:
    """What is allowed right now, with a human-readable reason when entries are blocked."""
    entry_start, entry_cutoff, square_off = session_rules(cfg)
    t = now.time()
    trading_day = is_trading_day(now.date(), cfg.get("market_holidays", []))
    is_open = trading_day and MARKET_OPEN <= t < MARKET_CLOSE

    if not trading_day:
        reason = "Market holiday" if now.weekday() < 5 else "Weekend"
    elif t < MARKET_OPEN or t >= MARKET_CLOSE:
        reason = "Market closed"
    elif t < entry_start:
        reason = f"Opening buffer until {entry_start:%H:%M}"
    elif t >= entry_cutoff:
        reason = f"No new entries after {entry_cutoff:%H:%M}"
    else:
        reason = None

    return {
        "ist_time": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "is_open": is_open,
        "can_trade": reason is None,
        "block_reason": reason,
        "is_weekend": now.weekday() >= 5,
        "entry_window": f"{entry_start:%H:%M}-{entry_cutoff:%H:%M}",
        "square_off_time": f"{square_off:%H:%M}",
        "square_off_due": trading_day and t >= square_off,  # also after the close, to reconcile leftovers
    }


def closed_bars(df: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    """Bars whose 15 minutes have fully elapsed (index = bar start, naive IST)."""
    if df is None or df.empty:
        return df
    return df[df.index + BAR <= now]


def is_latest_closed_bar(bar_start: dt.datetime, now: dt.datetime) -> bool:
    """True if this bar closed within the last 15 minutes, i.e. no newer bar has closed since."""
    bar_close = bar_start + BAR
    return bar_close <= now < bar_close + BAR and bar_start.date() == now.date()
