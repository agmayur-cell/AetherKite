"""
Test doubles and app bootstrap. Import this module before `app` so the
environment is set up first (no engine thread, test host and token allowed).
"""

import datetime as dt
import json
import os
import tempfile

os.environ.setdefault("AETHERKITE_ENGINE", "0")
os.environ.setdefault("AETHERKITE_ALLOWED_HOSTS", "testserver,127.0.0.1,localhost")
os.environ.setdefault("AETHERKITE_API_TOKEN", "test-token")

import pandas as pd  # noqa: E402

import app  # noqa: E402
from aetherkite import clock, strategy  # noqa: E402
from aetherkite.broker import KiteBroker  # noqa: E402
from aetherkite.marketdata import NIFTY, SENSEX, MarketData  # noqa: E402

TOKEN = "test-token"
HEADERS = {"X-AetherKite-Token": TOKEN}
TODAY = dt.date(2026, 10, 14)  # a Wednesday
NOW = dt.datetime.combine(TODAY, dt.time(10, 20, 30))


def bars(day: dt.date = TODAY, until: dt.time = dt.time(10, 15), price: float = 1000.0, prev_day: bool = True,
         lows: dict = None, highs: dict = None, last_close: float = None) -> pd.DataFrame:
    """Flat 15m bars from 09:15 to `until` (bar starts), optionally preceded by a full previous session."""
    rows, index = [], []
    days = ([day - dt.timedelta(days=1)] if prev_day else []) + [day]
    for d in days:
        t = dt.datetime.combine(d, dt.time(9, 15))
        end = dt.datetime.combine(d, dt.time(15, 15) if d != day else until)
        while t <= end:
            key = t.strftime("%H:%M") if d == day else None
            lo = (lows or {}).get(key, price - 1)
            hi = (highs or {}).get(key, price + 1)
            rows.append((price, hi, lo, price, 1000.0))
            index.append(t)
            t += dt.timedelta(minutes=15)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(index))
    if last_close is not None:
        df.iloc[-1, df.columns.get_loc("close")] = last_close
    return df


class FakeMarket(MarketData):
    def __init__(self, frames: dict):
        self.frames = frames
        self.fetch_calls = []
        super().__init__(fetcher=self._fetch, max_age=0)

    def _fetch(self, symbols):
        self.fetch_calls.append(list(symbols))
        return {s: self.frames[s] for s in symbols if s in self.frames}


class FakeGate:
    def __init__(self, approve=True, evaluated=True):
        self.approve, self.evaluated = approve, evaluated
        self.calls = []

    def evaluate(self, symbol, price, candle_time, cfg):
        self.calls.append(symbol)
        return {"approve_trade": self.approve, "evaluated": self.evaluated,
                "confidence_score": 0.9 if self.evaluated else None,
                "risk_level": "LOW" if self.approve else "HIGH",
                "reasoning": "<b>ok</b> no red flags" if self.approve else "SEBI probe reported", "news_count": 3}


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def notify(self, message, cfg, dedupe_key=None, force=False):
        self.messages.append((message, dedupe_key, force))
        return True


TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}


class FakeKite:
    """Enough of KiteConnect for the order flows, with switchable failure modes."""

    def __init__(self, ltp: float = 1000.0):
        self.price = ltp
        self.orders, self.placed, self.cancelled, self.net = {}, [], [], {}
        self.entry = "fill"        # fill | open | reject
        self.stop = "ok"           # ok | reject | raise
        self.flatten = "fill"      # fill | reject
        self._n = 0

    def place_order(self, **kw):
        self.placed.append(kw)
        self._n += 1
        oid = f"O{self._n}"
        sym, side, qty, kind = kw["tradingsymbol"], kw["transaction_type"], kw["quantity"], kw["order_type"]
        if kind == "MARKET" and side == "BUY":
            if self.entry == "reject":
                raise Exception("RMS: margin exceeds available funds")
            if self.entry == "open":
                self.orders[oid] = {"status": "OPEN", "filled_quantity": 0, "average_price": 0}
            else:
                self.orders[oid] = {"status": "COMPLETE", "filled_quantity": qty, "average_price": self.price + 0.5}
                self.net[sym] = self.net.get(sym, 0) + qty
        elif kind == "SL":
            if self.stop == "raise":
                raise Exception("Trigger price above last traded price")
            status = "REJECTED" if self.stop == "reject" else "TRIGGER PENDING"
            self.orders[oid] = {"status": status, "filled_quantity": 0, "average_price": 0, "quantity": qty,
                                "trigger_price": kw["trigger_price"], "tradingsymbol": sym, "status_message": "RMS rule"}
        elif kind == "MARKET" and side == "SELL":
            if self.flatten == "reject":
                self.orders[oid] = {"status": "REJECTED", "filled_quantity": 0, "average_price": 0, "status_message": "circuit"}
            else:
                self.orders[oid] = {"status": "COMPLETE", "filled_quantity": qty, "average_price": self.price - 0.5}
                self.net[sym] = self.net.get(sym, 0) - qty
        return oid

    def fire_stop(self, oid):
        o = self.orders[oid]
        o.update(status="COMPLETE", filled_quantity=o["quantity"], average_price=o["trigger_price"] - 0.1)
        self.net[o["tradingsymbol"]] -= o["quantity"]

    def order_history(self, order_id):
        return [dict(self.orders[order_id])]

    def cancel_order(self, variety, order_id, parent_order_id=None):
        self.cancelled.append(order_id)
        o = self.orders[order_id]
        if o["status"] in TERMINAL:
            raise Exception("Order cannot be cancelled as it is being processed")
        o["status"] = "CANCELLED"
        return order_id

    def positions(self):
        return {"net": [{"tradingsymbol": s, "product": "MIS", "exchange": "NSE", "quantity": q} for s, q in self.net.items()],
                "day": []}

    def ltp(self, *keys):
        return {k: {"last_price": self.price} for k in keys}


def fake_analyze(buy_symbols, atr=10.0):
    """Replacement for strategy.analyze: BUY for `buy_symbols` on the last closed bar, NEUTRAL otherwise."""
    def analyze(df, symbol):
        if df is None or df.empty:
            return {"status": "INSUFFICIENT_DATA", "symbol": symbol, "bars": 0}
        close = float(df["close"].iloc[-1])
        return {"status": "OK", "symbol": symbol, "signal": "BUY" if symbol in buy_symbols else "NEUTRAL",
                "close": close, "ema_trend": close - 20, "trend_bullish": True, "rsi": 55.0, "macd": 1.0,
                "macd_signal": 0.5, "macd_crossover": symbol in buy_symbols, "atr": atr,
                "candle_time": df.index[-1].to_pydatetime()}
    return analyze


class AppTestMixin:
    """Fresh data directory, fixed clock and fakes for every test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        app.configure_storage(self._tmp.name)
        clock.set_fixed(NOW)
        app.SIGNALS.clear()
        app.SCAN.update(results=[], scan_timestamp=None, computed_at=0.0, candle_time=None, regime=None)
        app.LIVE_ENABLED = False
        self.frames = {"RELIANCE": bars(), "TCS": bars(price=3000.0), NIFTY: bars(price=25000.0), SENSEX: bars(price=82000.0)}
        self.market = FakeMarket(self.frames)
        self.gate = FakeGate()
        self.notifier = FakeNotifier()
        self.kite = FakeKite()
        self._saved = (app.MARKET, app.GATE, app.NOTIFIER, app.make_broker, strategy.analyze, strategy.nifty_regime)
        app.MARKET, app.GATE, app.NOTIFIER = self.market, self.gate, self.notifier
        app.make_broker = lambda cfg: KiteBroker(self.kite, sleep=lambda s: None, poll_timeout=1.0)
        strategy.analyze = fake_analyze({"RELIANCE"})
        strategy.nifty_regime = lambda df: (True, "Nifty above EMA20 and previous close")
        self.write_config({"watchlist": ["RELIANCE", "TCS"]})
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)

    def tearDown(self):
        app.MARKET, app.GATE, app.NOTIFIER, app.make_broker, strategy.analyze, strategy.nifty_regime = self._saved
        clock.set_fixed(None)
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------------
    def write_config(self, values: dict):
        path = app.CONFIG_STORE.path
        cfg = json.loads(path.read_text()) if path.exists() else {}
        cfg.update(values)
        path.write_text(json.dumps(cfg))

    def go_live(self):
        app.LIVE_ENABLED = True
        self.write_config({"trading_mode": "LIVE", "kite_api_key": "k", "kite_api_secret": "s",
                           "kite_access_token": "t", "kite_token_date": TODAY.isoformat()})

    def get(self, path, **kw):
        return self.client.get(path, headers=HEADERS, **kw)

    def post(self, path, body=None, **kw):
        return self.client.post(path, headers=HEADERS, json=body if body is not None else {}, **kw)

    def scan(self):
        r = self.get("/api/scan?refresh=true")
        self.assertEqual(r.status_code, 200, r.text)
        return {row["symbol"]: row for row in r.json()["results"]}

    def buy(self, symbol="RELIANCE"):
        row = self.scan()[symbol]
        self.assertTrue(row["executable"], row)
        return self.post("/api/execute-trade", {"signal_id": row["signal_id"]})
