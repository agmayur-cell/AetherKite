"""
Single source of "now" for the app.

All times are naive datetimes in IST (UTC+05:30, no DST), matching the bar
timestamps from the market-data layer and the strings stored in the journal.
Tests pin the clock with `set_fixed()`.
"""

import datetime as dt
from typing import Optional

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

_fixed: Optional[dt.datetime] = None


def now_ist() -> dt.datetime:
    if _fixed is not None:
        return _fixed
    return dt.datetime.now(IST).replace(tzinfo=None)


def set_fixed(value: Optional[dt.datetime]):
    global _fixed
    _fixed = value


def fmt(ts: dt.datetime) -> str:
    return ts.strftime(TS_FORMAT)


def parse(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, TS_FORMAT)
