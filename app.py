"""
AetherKite Terminal backend (FastAPI).

Signal dashboard for NSE intraday longs: closed-candle technical rule, Nifty
regime filter, Gemini news veto, paper or live Zerodha MIS execution, and a
background engine that manages exits without the browser.

LIVE order placement is disabled unless the environment variable
AETHERKITE_ENABLE_LIVE=1 is set. Read REVIEW.md before enabling it.
"""

import csv
import datetime as dt
import io
import itertools
import logging
import math
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from aetherkite import clock, costs, market, strategy
from aetherkite.broker import BrokerError, KiteBroker
from aetherkite.gatekeeper import Gatekeeper
from aetherkite.marketdata import NIFTY, SENSEX, MarketData
from aetherkite.notify import Notifier, esc, send_telegram
from aetherkite.store import JsonStore, StoreCorrupt

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None

log = logging.getLogger("aetherkite")

# ==============================================================================
# RUNTIME SETTINGS (environment)
# ==============================================================================
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("AETHERKITE_DATA_DIR", str(APP_DIR)))
LIVE_ENABLED = os.environ.get("AETHERKITE_ENABLE_LIVE") == "1"
ENGINE_ENABLED = os.environ.get("AETHERKITE_ENGINE", "1") != "0"
ENGINE_INTERVAL = float(os.environ.get("AETHERKITE_ENGINE_INTERVAL", "30"))
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("AETHERKITE_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
API_TOKEN = os.environ.get("AETHERKITE_API_TOKEN") or secrets.token_urlsafe(32)
PORT = int(os.environ.get("AETHERKITE_PORT", "8000"))

# ==============================================================================
# CONFIGURATION & PERSISTENCE
# ==============================================================================
DEFAULT_CONFIG = {
    "trading_mode": "PAPER",                # "PAPER" or "LIVE"
    "starting_capital": 100000.0,
    "risk_per_trade_pct": 0.005,            # 0.5% of equity per trade
    "max_portfolio_leverage": 4.0,          # intraday margin multiple assumed for sizing
    "max_open_positions": 3,
    "daily_loss_limit_pct": 0.015,          # no new entries after losing 1.5% of the day's starting equity
    "max_notional_x": 1.0,                  # a single trade's notional is capped at 1x equity
    "max_chase_atr": 0.3,                   # reject if price moved > 0.3 ATR from the signal close
    "sl_limit_buffer_pct": 0.003,           # SL-L limit sits 0.3% below the trigger
    "watchlist": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "TMPV"],
    "gemini_api_key": "",
    "gemini_model": "gemini-3.8-flash",
    "kite_api_key": "",
    "kite_api_secret": "",
    "kite_access_token": "",
    "kite_token_date": "",
    "nifty_regime_filter": True,
    "morning_buffer_enabled": True,         # no entries before entry_start_time
    "entry_start_time": "09:45",
    "entry_cutoff_time": "14:00",           # no new entries at or after this time
    "intraday_cutoff_time": "15:10",        # automatic square-off time
    "market_holidays": [],                  # "YYYY-MM-DD" strings (NSE trading holidays)
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_alerts_enabled": False,
}
SECRET_KEYS = ("gemini_api_key", "kite_api_key", "kite_api_secret", "kite_access_token", "telegram_bot_token")
# Symbols whose NSE trading symbol changed (data is mapped in aetherkite.marketdata).
ORDER_SYMBOL_MAP = {"TATAMOTORS": "TMPV"}
ORDER_HISTORY_LIMIT = 500

CONFIG_STORE: JsonStore
STATE_STORE: JsonStore
JOURNAL_STORE: JsonStore


def configure_storage(directory: Path):
    """Points the stores at a data directory (tests use a temporary one)."""
    global CONFIG_STORE, STATE_STORE, JOURNAL_STORE, DATA_DIR
    DATA_DIR = Path(directory)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_STORE = JsonStore(DATA_DIR / "trading_config.json", dict)
    STATE_STORE = JsonStore(DATA_DIR / "trading_state.json", default_state)
    JOURNAL_STORE = JsonStore(DATA_DIR / "trading_journal.json", list)


def load_config() -> dict:
    return {**DEFAULT_CONFIG, **CONFIG_STORE.load()}


def save_config(cfg: dict):
    CONFIG_STORE.save(cfg)


def default_state() -> dict:
    capital = float(load_config().get("starting_capital") or DEFAULT_CONFIG["starting_capital"])
    return {"cash_balance": capital, "positions": {}, "order_history": [], "system_halted": False,
            "halt_reason": None, "pending_live_orders": [], "day": {}}


def load_state() -> dict:
    st = STATE_STORE.load()
    for key, value in (("positions", {}), ("order_history", []), ("system_halted", False),
                       ("halt_reason", None), ("pending_live_orders", []), ("day", {})):
        st.setdefault(key, value)
    return st


def save_state(st: dict):
    st["order_history"] = st["order_history"][:ORDER_HISTORY_LIMIT]
    STATE_STORE.save(st)


def load_journal() -> list:
    return JOURNAL_STORE.load()


def save_journal(journal: list):
    JOURNAL_STORE.save(journal)


configure_storage(DATA_DIR)

# One lock around every load -> modify -> save of state/journal/config, and
# around broker order flows, so concurrent requests and the engine can't
# overwrite each other.
TRADING_LOCK = threading.RLock()

MARKET = MarketData()
GATE = Gatekeeper()
NOTIFIER = Notifier()


def kite_factory(cfg: dict):
    if KiteConnect is None:
        raise BrokerError("kiteconnect library is not installed.")
    if not cfg.get("kite_api_key") or not cfg.get("kite_access_token"):
        raise BrokerError("Kite API key or access token missing. Click 'Connect Zerodha'.")
    kite = KiteConnect(api_key=cfg["kite_api_key"])
    kite.set_access_token(cfg["kite_access_token"])
    return kite


def make_broker(cfg: dict) -> KiteBroker:
    return KiteBroker(kite_factory(cfg))


# ==============================================================================
# HELPERS
# ==============================================================================
_tag_counter = itertools.count()


def new_tag() -> str:
    """Short unique ID (<= 20 chars, Kite's tag limit) used as trade ID and Kite order tag."""
    return f"AK{int(time.time() * 1000):x}{next(_tag_counter) % 1296:02x}"[:20]


def order_symbol(symbol: str) -> str:
    return ORDER_SYMBOL_MAP.get(symbol, symbol)


def kite_session_valid(cfg: dict, now: dt.datetime) -> bool:
    """Kite access tokens are valid for one trading day, so a token from an earlier date is treated as expired."""
    return bool(cfg.get("kite_api_key") and cfg.get("kite_access_token")
                and cfg.get("kite_token_date") == now.date().isoformat())


def margin_locked(st: dict) -> float:
    return sum(p.get("margin_locked", 0.0) for p in st["positions"].values())


def realized_equity(st: dict) -> float:
    return st["cash_balance"] + margin_locked(st)


def roll_day(st: dict, now: dt.datetime):
    today = now.date().isoformat()
    if st["day"].get("date") != today:
        st["day"] = {"date": today, "realized_pnl": 0.0, "start_equity": round(realized_equity(st), 2)}


def size_shares(equity: float, cash: float, price: float, stop_distance: float, cfg: dict) -> int:
    if price <= 0 or stop_distance <= 0 or equity <= 0:
        return 0
    qty = math.floor(equity * cfg["risk_per_trade_pct"] / stop_distance)
    qty = min(qty, math.floor(max(cash, 0.0) * cfg["max_portfolio_leverage"] / price))
    if cfg.get("max_notional_x"):
        qty = min(qty, math.floor(cfg["max_notional_x"] * equity / price))
    return max(qty, 0)


def mark_position(pos: dict) -> dict:
    price, as_of = MARKET.last_price(pos["symbol"])
    marked = dict(pos)
    if price is not None:
        marked["current_price"] = round(price, 2)
        marked["price_as_of"] = as_of
    marked["unrealized_pnl"] = round(pos["quantity"] * (marked["current_price"] - pos["entry_price"]), 2)
    return marked


def public_config(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
    out["gemini_configured"] = bool(cfg.get("gemini_api_key"))
    out["kite_configured"] = bool(cfg.get("kite_api_key") and cfg.get("kite_api_secret"))
    out["telegram_configured"] = bool(cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"))
    return out


def alert(message: str, cfg: Optional[dict] = None, key: Optional[str] = None):
    """Operational alert: always sent if Telegram is configured, even when signal alerts are off."""
    try:
        NOTIFIER.notify(message, cfg or load_config(), dedupe_key=key, force=True)
    except StoreCorrupt:
        pass


# ==============================================================================
# MARKET INDICES
# ==============================================================================
def index_snapshot(df) -> dict:
    if df is None or df.empty:
        return {"price": None, "change": None, "pct_change": None, "as_of": None, "stale": True}
    last_ts = df.index[-1]
    prev = df[df.index.normalize() < last_ts.normalize()]
    price = float(df["close"].iloc[-1])
    if prev.empty:
        return {"price": round(price, 2), "change": None, "pct_change": None,
                "as_of": last_ts.strftime("%Y-%m-%d %H:%M"), "stale": False}
    prev_close = float(prev["close"].iloc[-1])
    change = price - prev_close
    return {"price": round(price, 2), "change": round(change, 2), "pct_change": round(change / prev_close * 100, 2),
            "as_of": last_ts.strftime("%Y-%m-%d %H:%M"), "stale": False}


# ==============================================================================
# SCANNER
# ==============================================================================
SIGNALS: Dict[str, dict] = {}
SCAN: dict = {"results": [], "scan_timestamp": None, "computed_at": 0.0, "candle_time": None, "regime": None}
SCAN_LOCK = threading.Lock()


def _signal_row(symbol: str, df, now: dt.datetime, cfg: dict, equity: float, cash: float):
    """Technical evaluation and pre-AI checks. Returns (row, signal_context or None)."""
    row = {"symbol": symbol, "status": "DATA_UNAVAILABLE", "price": None, "quant_signal": "NONE",
           "ai_verdict": None, "executable": False, "block_reason": "No market data", "signal_id": None,
           "sizing": None, "candle_time": None}
    if df is None:
        return row, None
    closed = market.closed_bars(df, now)
    q = strategy.analyze(closed, symbol)
    if q["status"] != "OK":
        row.update(status="INSUFFICIENT_DATA", block_reason=f"Not enough closed bars ({q.get('bars', 0)})")
        if closed is not None and len(closed):
            row["price"] = round(float(closed["close"].iloc[-1]), 2)
        return row, None

    stop_distance = strategy.STOP_ATR * q["atr"]
    target_distance = strategy.TARGET_ATR * q["atr"]
    stop_loss = market.round_to_tick(q["close"] - stop_distance, "down")
    take_profit = market.round_to_tick(q["close"] + target_distance)
    shares = size_shares(equity, cash, q["close"], q["close"] - stop_loss, cfg)
    row.update(
        status="READY", price=round(q["close"], 2), quant_signal=q["signal"], block_reason=None,
        candle_time=clock.fmt(q["candle_time"]), trend_bullish=q["trend_bullish"],
        ema_trend=round(q["ema_trend"], 2), rsi=q["rsi"], macd_crossover=q["macd_crossover"], atr=round(q["atr"], 2),
        sizing={"shares": shares, "stop_loss": stop_loss, "take_profit": take_profit,
                "risk_inr": round(shares * (q["close"] - stop_loss), 2), "capital_required": round(shares * q["close"], 2)},
    )
    if q["signal"] != "BUY":
        return row, None
    if not market.is_latest_closed_bar(q["candle_time"], now):
        row["block_reason"] = "Signal is from an earlier candle (stale)"
        return row, None
    if shares < 1:
        row["block_reason"] = "Position size is 0 (risk budget or margin too small)"
        return row, None
    context = {"symbol": symbol, "price": q["close"], "atr": q["atr"], "candle_time": q["candle_time"],
               "stop_distance": stop_distance, "target_distance": target_distance}
    return row, context


def run_scan(force: bool = False) -> dict:
    with SCAN_LOCK:
        if not force and SCAN["computed_at"] and time.time() - SCAN["computed_at"] < 60:
            return SCAN
        cfg = load_config()
        st = load_state()
        now = clock.now_ist()
        symbols = list(dict.fromkeys(cfg.get("watchlist") or []))
        data = MARKET.get(symbols + [NIFTY])

        if cfg.get("nifty_regime_filter", True):
            regime_ok, regime_detail = strategy.nifty_regime(market.closed_bars(data.get(NIFTY), now))
        else:
            regime_ok, regime_detail = True, "Regime filter disabled"

        equity, cash = realized_equity(st), st["cash_balance"]
        rows, candidates = [], []
        for sym in symbols:
            row, ctx = _signal_row(sym, data.get(sym), now, cfg, equity, cash)
            if ctx is not None and regime_ok is not True:
                row["block_reason"] = f"Nifty regime filter: {regime_detail}"
                ctx = None
            rows.append(row)
            if ctx is not None:
                candidates.append((row, ctx))

        if candidates:
            with ThreadPoolExecutor(max_workers=4) as pool:
                verdicts = list(pool.map(lambda rc: GATE.evaluate(rc[1]["symbol"], rc[1]["price"], rc[1]["candle_time"], cfg), candidates))
        else:
            verdicts = []

        session = market.session_status(now, cfg)
        live = cfg["trading_mode"] == "LIVE"
        for (row, ctx), verdict in zip(candidates, verdicts):
            row["ai_verdict"] = verdict
            if not verdict["approve_trade"]:
                row["block_reason"] = "AI veto" if verdict["evaluated"] else "AI not evaluated"
                continue
            if live and not verdict["evaluated"]:
                row["block_reason"] = "LIVE requires an evaluated AI verdict"
                continue
            signal_id = f"{ctx['symbol']}|{ctx['candle_time']:%Y%m%d%H%M}"
            SIGNALS[signal_id] = {**ctx, "verdict": verdict, "signal_id": signal_id}
            row["signal_id"] = signal_id
            if not session["can_trade"]:
                row["block_reason"] = session["block_reason"]
                continue
            row["executable"] = True
            if not st.get("system_halted"):
                sz = row["sizing"]
                NOTIFIER.notify(
                    f"🚨 <b>AetherKite signal: {esc(ctx['symbol'])}</b>\n\n"
                    f"📈 Quant BUY on the {esc(row['candle_time'][11:16])} candle\n"
                    f"💵 Price: ₹{row['price']:.2f}\n"
                    f"🛡️ Stop: ₹{sz['stop_loss']:.2f} · 🎯 Target: ₹{sz['take_profit']:.2f}\n"
                    f"📦 Suggested: {sz['shares']} shares\n"
                    f"🤖 AI: {esc(verdict['reasoning'])}\n\n"
                    f"<i>{esc(cfg['trading_mode'])} mode · {now:%H:%M:%S} IST</i>",
                    cfg, dedupe_key=signal_id)

        cutoff = now - 2 * market.BAR
        for sid in [s for s, sig in SIGNALS.items() if sig["candle_time"] < cutoff]:
            SIGNALS.pop(sid, None)

        rows.sort(key=lambda r: (not r["executable"], r["quant_signal"] != "BUY", r["symbol"]))
        SCAN.update(results=rows, scan_timestamp=now.strftime("%H:%M:%S IST"), computed_at=time.time(),
                    candle_time=max((r["candle_time"] for r in rows if r["candle_time"]), default=None),
                    regime={"ok": regime_ok, "detail": regime_detail})
        return SCAN


# ==============================================================================
# EXECUTION
# ==============================================================================
def _book_exit(st: dict, journal: list, symbol: str, exit_price: float, reason: str, now: dt.datetime,
               estimated: bool = False) -> dict:
    pos = st["positions"].pop(symbol)
    qty, entry = pos["quantity"], pos["entry_price"]
    gross = round(qty * (exit_price - entry), 2)
    charges = costs.round_trip_charges(qty * entry, qty * exit_price)
    net = round(gross - charges, 2)
    st["cash_balance"] = round(st["cash_balance"] + pos["margin_locked"] + net, 2)
    roll_day(st, now)
    st["day"]["realized_pnl"] = round(st["day"].get("realized_pnl", 0.0) + net, 2)

    record = {"order_id": f"X-{new_tag()}", "trade_id": pos.get("trade_id"), "symbol": symbol, "action": "SELL",
              "quantity": qty, "price": round(exit_price, 2), "realized_pnl": net, "reason": reason,
              "mode": pos.get("mode", "PAPER"), "price_estimated": estimated, "status": "CLOSED",
              "timestamp": clock.fmt(now)}
    st["order_history"].insert(0, record)

    entry_row = next((t for t in journal if t.get("trade_id") == pos.get("trade_id")), None)
    if entry_row is None:
        entry_row = next((t for t in journal if t.get("symbol") == symbol and t.get("status") == "OPEN"), None)
    if entry_row is not None:
        entry_row.update(status="CLOSED", exit_price=round(exit_price, 2), exit_time=record["timestamp"],
                         exit_reason=reason, gross_pnl=gross, est_charges=charges, realized_pnl=net,
                         return_pct=round((exit_price - entry) / entry * 100, 2), exit_price_estimated=estimated)
    return record


def close_position_locked(st: dict, journal: list, symbol: str, reason: str, cfg: dict, now: dt.datetime) -> dict:
    """Closes one position. LIVE positions are closed at the broker first; local state changes only after that succeeds."""
    pos = st["positions"][symbol]
    if pos.get("mode") == "LIVE":
        result = make_broker(cfg).exit_long(order_symbol(symbol), pos["quantity"], pos.get("sl_order_id"), new_tag())
        if result.outcome == "EXITED":
            return _book_exit(st, journal, symbol, result.average_price, reason, now)
        if result.outcome == "STOP_FILLED":
            return _book_exit(st, journal, symbol, result.average_price, "STOP_LOSS_HIT", now)
        price, _ = MARKET.last_price(symbol)
        return _book_exit(st, journal, symbol, price or pos["entry_price"], f"{reason}_BROKER_ALREADY_FLAT", now,
                          estimated=True)

    price, _ = MARKET.last_price(symbol)
    if price is None:
        raise HTTPException(status_code=503, detail=f"No current price for {symbol}; try again shortly.")
    return _book_exit(st, journal, symbol, price, reason, now)


def paper_exit_check(pos: dict, df, now: dt.datetime):
    """Stop/target hits for a PAPER position, using bars after the entry bar plus the latest price."""
    if df is None or df.empty:
        return None
    entry_ts = clock.parse(pos["entry_time"])
    entry_bar = entry_ts.replace(minute=entry_ts.minute - entry_ts.minute % 15, second=0, microsecond=0)
    stop, target = pos["stop_loss"], pos["take_profit"]
    for _, bar in df[df.index > entry_bar].iterrows():
        if bar["open"] <= stop:
            return float(bar["open"]), "STOP_LOSS_HIT"
        if bar["low"] <= stop:
            return stop, "STOP_LOSS_HIT"
        if bar["open"] >= target:
            return float(bar["open"]), "TARGET_HIT"
        if bar["high"] >= target:
            return target, "TARGET_HIT"
    return None


# ==============================================================================
# BACKGROUND ENGINE (exits, square-off, auto-scan; runs without the browser)
# ==============================================================================
ENGINE_STATE = {"enabled": ENGINE_ENABLED, "last_tick": None, "last_error": None, "last_auto_scan_bar": None}
STOP_EVENT = threading.Event()


def engine_tick(now: Optional[dt.datetime] = None):
    now = now or clock.now_ist()
    ENGINE_STATE["last_tick"] = clock.fmt(now)
    ENGINE_STATE["last_error"] = None
    cfg = load_config()
    session = market.session_status(now, cfg)

    open_symbols = list(load_state()["positions"])
    if open_symbols:
        MARKET.get(open_symbols, max_age=30)
        with TRADING_LOCK:
            st, journal = load_state(), load_journal()
            changed = False
            for symbol, pos in list(st["positions"].items()):
                try:
                    entry_day = clock.parse(pos["entry_time"]).date()
                    if entry_day < now.date():
                        if pos.get("mode") == "LIVE":
                            close_position_locked(st, journal, symbol, "STALE_SESSION_RECONCILE", cfg, now)
                        else:
                            price, _ = MARKET.last_price(symbol)
                            _book_exit(st, journal, symbol, price or pos["entry_price"], "STALE_SESSION_CLOSE", now,
                                       estimated=True)
                        changed = True
                    elif session["square_off_due"]:
                        close_position_locked(st, journal, symbol, "EOD_SQUARE_OFF", cfg, now)
                        changed = True
                    elif pos.get("mode") == "LIVE":
                        if session["is_open"] and pos.get("sl_order_id"):
                            sl = make_broker(cfg).last_state(pos["sl_order_id"])
                            if sl.get("status") == "COMPLETE":
                                _book_exit(st, journal, symbol, float(sl.get("average_price") or pos["stop_loss"]),
                                           "STOP_LOSS_HIT", now)
                                changed = True
                                continue
                        price, _ = MARKET.last_price(symbol)
                        if session["is_open"] and price is not None and price >= pos["take_profit"]:
                            close_position_locked(st, journal, symbol, "TARGET_HIT", cfg, now)
                            changed = True
                    elif session["is_open"]:
                        hit = paper_exit_check(pos, MARKET.cached(symbol), now)
                        if hit is not None:
                            _book_exit(st, journal, symbol, hit[0], hit[1], now)
                            changed = True
                except (BrokerError, HTTPException) as e:
                    detail = getattr(e, "detail", None) or str(e)
                    log.error("engine: could not manage %s: %s", symbol, detail)
                    ENGINE_STATE["last_error"] = f"{symbol}: {detail}"
                    if isinstance(e, BrokerError) and e.critical:
                        st["system_halted"], st["halt_reason"] = True, f"Broker error on {symbol}: {e}"
                        changed = True
                    alert(f"⚠️ <b>AetherKite</b>: could not manage {esc(symbol)}: {esc(detail)}", cfg,
                          key=f"engine-{symbol}-{now:%Y%m%d%H}")
            if changed:
                save_state(st)
                save_journal(journal)

    # Auto-scan once per candle, 20 s to 3 min after it closes (gives the data feed time to publish the bar).
    if session["is_open"]:
        boundary = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        bar_start = boundary - market.BAR
        since_close = (now - boundary).total_seconds()
        if (20 <= since_close <= 180 and bar_start.time() >= market.MARKET_OPEN
                and ENGINE_STATE.get("last_auto_scan_bar") != clock.fmt(bar_start)):
            ENGINE_STATE["last_auto_scan_bar"] = clock.fmt(bar_start)
            run_scan(force=True)


def engine_loop():
    while not STOP_EVENT.wait(ENGINE_INTERVAL):
        try:
            engine_tick()
        except StoreCorrupt as e:
            ENGINE_STATE["last_error"] = str(e)
        except Exception as e:
            log.exception("engine tick failed")
            ENGINE_STATE["last_error"] = str(e)


def setup_logging():
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    root = logging.getLogger("aetherkite")
    if not root.handlers:
        handler = RotatingFileHandler(log_dir / "aetherkite.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.addHandler(logging.StreamHandler())
        root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    try:
        with TRADING_LOCK:
            st = load_state()
            if st["pending_live_orders"] and not st["system_halted"]:
                st["system_halted"] = True
                st["halt_reason"] = "Unresolved live order(s) from a previous run. Check Kite, then resume."
                save_state(st)
    except StoreCorrupt as e:
        log.error("%s", e)
    thread = None
    if ENGINE_ENABLED:
        STOP_EVENT.clear()
        thread = threading.Thread(target=engine_loop, name="engine", daemon=True)
        thread.start()
    log.info("AetherKite started (LIVE orders %s)", "ENABLED" if LIVE_ENABLED else "disabled")
    yield
    STOP_EVENT.set()


# ==============================================================================
# FASTAPI APPLICATION
# ==============================================================================
app = FastAPI(title="AetherKite Terminal", version="2.1.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

PUBLIC_API = {"/api/kite-callback", "/api/health"}
CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; "
       "form-action 'self'")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        if not secrets.compare_digest(request.headers.get("x-aetherkite-token", ""), API_TOKEN):
            return JSONResponse({"detail": "Missing or invalid API token. Reload the dashboard."}, status_code=401)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/api/") or path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StoreCorrupt)
async def store_corrupt_handler(_request: Request, exc: StoreCorrupt):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, exc: RequestValidationError):
    # FastAPI's default handler echoes the rejected input, which can't be rendered when it is NaN/Infinity.
    errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "invalid value")} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


# ---- Request models ------------------------------------------------------------
SYMBOL_PATTERN = r"^[A-Z0-9&\-]{1,20}$"
Finite = dict(allow_inf_nan=False)


class ExecuteRequest(BaseModel):
    signal_id: str = Field(min_length=3, max_length=80)


class ClosePositionRequest(BaseModel):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    reason: str = Field(default="MANUAL_EXIT", pattern=r"^[A-Z_]{1,32}$")


class ResumeRequest(BaseModel):
    clear_pending: bool = False


class NoteRequest(BaseModel):
    trade_id: str = Field(min_length=1, max_length=40)
    notes: str = Field(default="", max_length=2000)


class TelegramTestRequest(BaseModel):
    telegram_bot_token: Optional[str] = Field(default=None, max_length=200)
    telegram_chat_id: Optional[str] = Field(default=None, max_length=50)


class SettingsUpdate(BaseModel):
    trading_mode: Optional[Literal["PAPER", "LIVE"]] = None
    starting_capital: Optional[float] = Field(default=None, gt=0, le=1e9, **Finite)
    risk_per_trade_pct: Optional[float] = Field(default=None, gt=0, le=0.05, **Finite)
    max_portfolio_leverage: Optional[float] = Field(default=None, ge=1, le=5, **Finite)
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=20)
    daily_loss_limit_pct: Optional[float] = Field(default=None, gt=0, le=0.1, **Finite)
    max_notional_x: Optional[float] = Field(default=None, gt=0, le=5, **Finite)
    watchlist: Optional[List[str]] = Field(default=None, max_length=100)
    gemini_api_key: Optional[str] = Field(default=None, max_length=200)
    gemini_model: Optional[str] = Field(default=None, max_length=60, pattern=r"^[A-Za-z0-9.\-]*$")
    kite_api_key: Optional[str] = Field(default=None, max_length=100)
    kite_api_secret: Optional[str] = Field(default=None, max_length=100)
    telegram_bot_token: Optional[str] = Field(default=None, max_length=200)
    telegram_chat_id: Optional[str] = Field(default=None, max_length=50)
    telegram_alerts_enabled: Optional[bool] = None
    clear_telegram: bool = False
    nifty_regime_filter: Optional[bool] = None
    morning_buffer_enabled: Optional[bool] = None
    entry_start_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    entry_cutoff_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    intraday_cutoff_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    market_holidays: Optional[List[str]] = Field(default=None, max_length=60)

    @field_validator("watchlist")
    @classmethod
    def _watchlist(cls, value):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(s.strip().upper() for s in value if s and s.strip()))
        bad = [s for s in cleaned if not re.match(SYMBOL_PATTERN, s)]
        if bad:
            raise ValueError(f"invalid symbols: {', '.join(bad[:5])}")
        if not cleaned:
            raise ValueError("watchlist cannot be empty")
        return cleaned

    @field_validator("market_holidays")
    @classmethod
    def _holidays(cls, value):
        if value is None:
            return value
        for d in value:
            dt.date.fromisoformat(d)
        return sorted(set(value))


# ---- Pages -----------------------------------------------------------------------
@app.get("/")
def get_dashboard():
    index_file = STATIC_DIR / "index.html"
    html = index_file.read_text(encoding="utf-8").replace("__AETHERKITE_TOKEN__", API_TOKEN)
    return HTMLResponse(html)


@app.get("/api/health")
def health():
    try:
        load_state()
        load_journal()
        load_config()
        store = "ok"
    except StoreCorrupt as e:
        store = str(e)
    return {"ok": store == "ok", "store": store, "engine": ENGINE_STATE}


# ---- Zerodha login -------------------------------------------------------------
@app.get("/api/kite-login")
def kite_login_url():
    cfg = load_config()
    if not cfg.get("kite_api_key"):
        raise HTTPException(status_code=400, detail="Enter your Kite API key in Settings first.")
    return {"login_url": f"https://kite.zerodha.com/connect/login?v=3&api_key={quote(cfg['kite_api_key'])}"}


def _callback_page(title: str, message: str, status: int) -> HTMLResponse:
    return HTMLResponse(f"<h2>{esc(title)}</h2><p>{esc(message)}</p><p><a href='/'>Back to dashboard</a></p>",
                        status_code=status)


@app.get("/api/kite-callback")
def kite_callback(request_token: Optional[str] = None, status: Optional[str] = None):
    """Zerodha redirects here after login; exchanges the request token for today's access token."""
    if status != "success" or not request_token:
        return _callback_page("Zerodha login was not completed", f"status={status or 'missing'}", 400)
    with TRADING_LOCK:
        cfg = load_config()
        if not cfg.get("kite_api_key") or not cfg.get("kite_api_secret"):
            return _callback_page("Kite API key or secret missing", "Configure them in Settings first.", 400)
        if KiteConnect is None:
            return _callback_page("kiteconnect is not installed", "pip install kiteconnect", 500)
        try:
            data = KiteConnect(api_key=cfg["kite_api_key"]).generate_session(request_token, api_secret=cfg["kite_api_secret"])
        except Exception as e:
            log.warning("Kite session exchange failed: %s", e)
            return _callback_page("Zerodha authentication failed", str(e), 400)
        cfg["kite_access_token"] = data["access_token"]
        cfg["kite_token_date"] = clock.now_ist().date().isoformat()
        save_config(cfg)
    return RedirectResponse(url="/?auth_success=1")


# ---- Status / scan ---------------------------------------------------------------
@app.get("/api/status")
def get_status():
    cfg = load_config()
    st = load_state()
    now = clock.now_ist()
    data = MARKET.get(list(st["positions"]) + [NIFTY, SENSEX])
    positions = [mark_position(p) for p in st["positions"].values()]
    unrealized = sum(p["unrealized_pnl"] for p in positions)
    locked = margin_locked(st)
    regime_ok, regime_detail = strategy.nifty_regime(market.closed_bars(data.get(NIFTY), now))
    return {
        "market": market.session_status(now, cfg),
        "indices": {"nifty": index_snapshot(data.get(NIFTY)), "sensex": index_snapshot(data.get(SENSEX)),
                    "regime": {"ok": regime_ok, "detail": regime_detail, "filter_enabled": cfg.get("nifty_regime_filter", True)}},
        "config": {
            "trading_mode": cfg["trading_mode"],
            "starting_capital": cfg["starting_capital"],
            "risk_per_trade_pct": cfg["risk_per_trade_pct"],
            "gemini_connected": bool(cfg.get("gemini_api_key")),
            "gemini_model": cfg.get("gemini_model"),
            "kite_connected": kite_session_valid(cfg, now),
            "kite_configured": bool(cfg.get("kite_api_key") and cfg.get("kite_api_secret")),
            "telegram_alerts_enabled": cfg.get("telegram_alerts_enabled", False),
            "telegram_connected": bool(cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id")),
            "watchlist": cfg["watchlist"],
            "live_enabled": LIVE_ENABLED,
        },
        "portfolio": {
            "cash_balance": round(st["cash_balance"], 2),
            "margin_locked": round(locked, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_equity": round(st["cash_balance"] + locked + unrealized, 2),
            "day_realized_pnl": st["day"].get("realized_pnl", 0.0) if st["day"].get("date") == now.date().isoformat() else 0.0,
            "open_positions_count": len(positions),
            "system_halted": st.get("system_halted", False),
            "halt_reason": st.get("halt_reason"),
            "pending_live_orders": len(st["pending_live_orders"]),
        },
        "engine": ENGINE_STATE,
    }


@app.get("/api/scan")
def get_scan(refresh: bool = False):
    snapshot = run_scan(force=refresh)
    return {k: snapshot[k] for k in ("results", "scan_timestamp", "candle_time", "regime")}


# ---- Trading -----------------------------------------------------------------------
@app.post("/api/execute-trade")
def execute_trade(req: ExecuteRequest):
    """Executes a server-side signal. The client only names the signal; price, size and stop are computed here."""
    cfg = load_config()
    now = clock.now_ist()
    with TRADING_LOCK:
        st = load_state()
        roll_day(st, now)
        mode = cfg["trading_mode"]

        if st.get("system_halted"):
            raise HTTPException(status_code=409, detail=f"System is halted: {st.get('halt_reason') or 'kill switch'}")
        if st["pending_live_orders"]:
            raise HTTPException(status_code=409, detail="A previous live order is unresolved. Check Kite, then resume.")
        sig = SIGNALS.get(req.signal_id)
        if sig is None:
            raise HTTPException(status_code=404, detail="Signal not found or expired. Re-scan the radar.")
        if not market.is_latest_closed_bar(sig["candle_time"], now):
            raise HTTPException(status_code=410, detail="Signal is stale: a newer candle has closed. Re-scan the radar.")
        session = market.session_status(now, cfg)
        if not session["can_trade"]:
            raise HTTPException(status_code=409, detail=f"Entries not allowed now: {session['block_reason']}")
        verdict = sig["verdict"]
        if not verdict["approve_trade"] or (mode == "LIVE" and not verdict["evaluated"]):
            raise HTTPException(status_code=409, detail="Trade is not approved by the AI gate.")
        symbol = sig["symbol"]
        if symbol in st["positions"]:
            raise HTTPException(status_code=409, detail=f"A position in {symbol} is already open.")
        if len(st["positions"]) >= cfg["max_open_positions"]:
            raise HTTPException(status_code=409, detail=f"Maximum of {cfg['max_open_positions']} open positions reached.")
        loss_limit = cfg["daily_loss_limit_pct"] * st["day"]["start_equity"]
        if st["day"]["realized_pnl"] <= -loss_limit:
            raise HTTPException(status_code=409, detail="Daily loss limit reached; no new entries today.")

        broker = None
        if mode == "LIVE":
            if not LIVE_ENABLED:
                raise HTTPException(status_code=403, detail="LIVE orders are disabled. Start the app with AETHERKITE_ENABLE_LIVE=1 to enable them.")
            if not kite_session_valid(cfg, now):
                raise HTTPException(status_code=409, detail="Kite session missing or expired. Click 'Connect Zerodha'.")
            try:
                broker = make_broker(cfg)
                ltp = broker.ltp(order_symbol(symbol))
            except BrokerError as e:
                raise HTTPException(status_code=502, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Kite error: {e}")
        else:
            MARKET.get([symbol], max_age=20)
            ltp, _ = MARKET.last_price(symbol)
            if ltp is None:
                raise HTTPException(status_code=503, detail=f"No current price for {symbol}.")

        if abs(ltp - sig["price"]) > cfg["max_chase_atr"] * sig["atr"]:
            raise HTTPException(status_code=409, detail=f"Price moved too far from the signal (₹{sig['price']:.2f} → ₹{ltp:.2f}). Not chasing.")
        shares = size_shares(realized_equity(st), st["cash_balance"], ltp, sig["stop_distance"], cfg)
        if shares < 1:
            raise HTTPException(status_code=409, detail="Position size is 0 at the current price (risk budget or margin).")

        tag = new_tag()
        if mode == "LIVE":
            st["pending_live_orders"].append({"tag": tag, "symbol": symbol, "quantity": shares, "time": clock.fmt(now)})
            save_state(st)
            try:
                res = broker.enter_long(order_symbol(symbol), shares, sig["stop_distance"], cfg["sl_limit_buffer_pct"], tag)
            except BrokerError as e:
                if e.critical:
                    st["system_halted"], st["halt_reason"] = True, str(e)
                else:
                    st["pending_live_orders"] = [p for p in st["pending_live_orders"] if p["tag"] != tag]
                save_state(st)
                alert(f"⚠️ <b>AetherKite LIVE entry failed</b> ({esc(symbol)}): {esc(str(e))}", cfg)
                raise HTTPException(status_code=502, detail=str(e))
            st["pending_live_orders"] = [p for p in st["pending_live_orders"] if p["tag"] != tag]
            fill, qty, stop = res.average_price, res.quantity, res.sl_trigger
            entry_order_id, sl_order_id = res.order_id, res.sl_order_id
        else:
            fill, qty = ltp, shares
            stop = market.round_to_tick(fill - sig["stop_distance"], "down")
            entry_order_id, sl_order_id = f"P-{tag}", None

        target = market.round_to_tick(fill + sig["target_distance"])
        margin = round(qty * fill / cfg["max_portfolio_leverage"], 2)
        st["cash_balance"] = round(st["cash_balance"] - margin, 2)
        position = {
            "symbol": symbol, "trade_id": tag, "mode": mode, "quantity": qty, "entry_price": round(fill, 2),
            "current_price": round(fill, 2), "stop_loss": stop, "take_profit": target, "atr": round(sig["atr"], 2),
            "margin_locked": margin, "unrealized_pnl": 0.0, "entry_time": clock.fmt(now),
            "entry_order_id": entry_order_id, "sl_order_id": sl_order_id, "signal_id": sig["signal_id"],
        }
        st["positions"][symbol] = position
        record = {"order_id": entry_order_id, "trade_id": tag, "symbol": symbol, "action": "BUY", "quantity": qty,
                  "price": round(fill, 2), "mode": mode, "status": "FILLED", "timestamp": clock.fmt(now)}
        st["order_history"].insert(0, record)
        save_state(st)

        journal = load_journal()
        journal.insert(0, {
            "trade_id": tag, "symbol": symbol, "direction": "LONG", "mode": mode, "quantity": qty,
            "entry_price": round(fill, 2), "entry_time": clock.fmt(now), "stop_loss": stop, "take_profit": target,
            "risk_inr": round(qty * (fill - stop), 2), "target_inr": round(qty * (target - fill), 2),
            "signal_candle": clock.fmt(sig["candle_time"]),
            "quant_rationale": f"Close above EMA{strategy.EMA_TREND}, MACD bullish cross, RSI in {strategy.RSI_LOW:g}-{strategy.RSI_HIGH:g}",
            "ai_rationale": verdict["reasoning"] if verdict["evaluated"] else f"NOT EVALUATED: {verdict['reasoning']}",
            "ai_confidence": round(verdict["confidence_score"] * 100) if verdict["confidence_score"] is not None else None,
            "status": "OPEN", "exit_price": None, "exit_time": None, "exit_reason": None,
            "realized_pnl": 0.0, "return_pct": 0.0, "notes": "",
        })
        save_journal(journal)
    return {"status": "SUCCESS", "order": record, "position": position}


@app.post("/api/close-position")
def close_position(req: ClosePositionRequest):
    cfg = load_config()
    now = clock.now_ist()
    with TRADING_LOCK:
        st, journal = load_state(), load_journal()
        if req.symbol not in st["positions"]:
            raise HTTPException(status_code=404, detail=f"No open position found for {req.symbol}")
        if st["positions"][req.symbol].get("mode") != "LIVE":
            MARKET.get([req.symbol], max_age=20)
        try:
            record = close_position_locked(st, journal, req.symbol, req.reason, cfg, now)
        except BrokerError as e:
            if e.critical:
                st["system_halted"], st["halt_reason"] = True, str(e)
                save_state(st)
            alert(f"⚠️ <b>AetherKite exit failed</b> ({esc(req.symbol)}): {esc(str(e))}", cfg)
            raise HTTPException(status_code=502, detail=f"Broker exit failed; position kept open locally. {e}")
        save_state(st)
        save_journal(journal)
    return {"status": "SUCCESS", "closed_order": record, "realized_pnl": record["realized_pnl"]}


@app.post("/api/kill-switch")
def emergency_kill_switch():
    """Halts new entries, then closes every position (at the broker for LIVE positions)."""
    cfg = load_config()
    now = clock.now_ist()
    with TRADING_LOCK:
        st = load_state()
        st["system_halted"], st["halt_reason"] = True, f"Kill switch activated at {now:%H:%M:%S} IST"
        save_state(st)
        paper = [s for s, p in st["positions"].items() if p.get("mode") != "LIVE"]
        if paper:
            MARKET.get(paper, max_age=20)
        journal = load_journal()
        closed, failed = [], []
        for symbol in list(st["positions"]):
            try:
                record = close_position_locked(st, journal, symbol, "EMERGENCY_KILL_SWITCH", cfg, now)
                closed.append(record)
            except (BrokerError, HTTPException) as e:
                failed.append({"symbol": symbol, "error": getattr(e, "detail", None) or str(e)})
        save_state(st)
        save_journal(journal)
    if failed:
        alert("🛑 <b>AetherKite kill switch</b>: could not close " + esc(", ".join(f["symbol"] for f in failed))
              + ". Close them in Kite now.", cfg)
    message = f"Kill switch activated. Closed {len(closed)} position(s)."
    if failed:
        message += f" FAILED to close {len(failed)}: {', '.join(f['symbol'] for f in failed)}. Close them in Kite."
    return {"status": "HALTED", "message": message, "closed_orders": closed, "failed": failed}


@app.post("/api/resume-system")
def resume_system(req: ResumeRequest = ResumeRequest()):
    with TRADING_LOCK:
        st = load_state()
        if st["pending_live_orders"] and not req.clear_pending:
            raise HTTPException(status_code=409, detail="Unresolved live orders exist. Check Kite, then resume with clear_pending.")
        st["pending_live_orders"] = []
        st["system_halted"], st["halt_reason"] = False, None
        save_state(st)
    return {"status": "ACTIVE", "message": "Trading engine resumed."}


@app.get("/api/positions")
def get_positions():
    st = load_state()
    MARKET.get(list(st["positions"]))
    return {"positions": [mark_position(p) for p in st["positions"].values()], "orders": st["order_history"][:100]}


# ---- Journal -----------------------------------------------------------------------
@app.get("/api/journal")
def get_journal():
    journal = load_journal()
    closed = [t for t in journal if t.get("status") == "CLOSED"]
    winning = [t for t in closed if t.get("realized_pnl", 0) > 0]
    losing = [t for t in closed if t.get("realized_pnl", 0) < 0]
    gross_profit = sum(t["realized_pnl"] for t in winning)
    gross_loss = -sum(t["realized_pnl"] for t in losing)
    analytics = {
        "total_trades": len(closed),
        "win_count": len(winning),
        "loss_count": len(losing),
        "win_rate_pct": round(len(winning) / len(closed) * 100, 1) if closed else 0.0,
        "total_realized_pnl": round(sum(t.get("realized_pnl", 0) for t in closed), 2),
        "total_est_charges": round(sum(t.get("est_charges", 0) or 0 for t in closed), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win": round(gross_profit / len(winning), 2) if winning else 0.0,
        "avg_loss": round(gross_loss / len(losing), 2) if losing else 0.0,
        "open_trades_count": sum(1 for t in journal if t.get("status") == "OPEN"),
    }
    return {"trades": journal, "analytics": analytics}


@app.post("/api/journal/note")
def update_journal_note(req: NoteRequest):
    with TRADING_LOCK:
        journal = load_journal()
        for t in journal:
            if t.get("trade_id") == req.trade_id:
                t["notes"] = req.notes
                save_journal(journal)
                return {"status": "SUCCESS"}
    raise HTTPException(status_code=404, detail="Trade not found.")


def _csv_safe(value):
    """Neutralises spreadsheet formulas (=, +, -, @, tab, CR) in text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@app.get("/api/journal/export")
def export_journal_csv():
    journal = load_journal()
    columns = [("Trade ID", "trade_id"), ("Symbol", "symbol"), ("Mode", "mode"), ("Direction", "direction"),
               ("Quantity", "quantity"), ("Entry Price", "entry_price"), ("Entry Time", "entry_time"),
               ("Exit Price", "exit_price"), ("Exit Time", "exit_time"), ("Exit Reason", "exit_reason"),
               ("Stop Loss", "stop_loss"), ("Take Profit", "take_profit"), ("Risk (INR)", "risk_inr"),
               ("Gross P&L (INR)", "gross_pnl"), ("Est. Charges (INR)", "est_charges"),
               ("Net P&L (INR)", "realized_pnl"), ("Return %", "return_pct"), ("AI Confidence", "ai_confidence"),
               ("AI Rationale", "ai_rationale"), ("Quant Rationale", "quant_rationale"), ("Notes", "notes")]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([c[0] for c in columns])
    for t in journal:
        writer.writerow([_csv_safe(t.get(key, "")) for _, key in columns])
    return Response(content="﻿" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=trading_journal.csv"})


# ---- Settings & alerts -----------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    cfg = load_config()
    st = load_state()
    return {"config": public_config(cfg), "cash_balance": st["cash_balance"], "open_positions": len(st["positions"]),
            "live_enabled": LIVE_ENABLED, "kite_redirect_url": f"http://127.0.0.1:{PORT}/api/kite-callback"}


@app.post("/api/settings")
def update_settings(req: SettingsUpdate):
    now = clock.now_ist()
    with TRADING_LOCK:
        cfg = load_config()
        st = load_state()
        changes = req.model_dump(exclude_unset=True, exclude={"clear_telegram"})

        if "trading_mode" in changes and changes["trading_mode"] != cfg["trading_mode"]:
            if st["positions"]:
                raise HTTPException(status_code=409, detail="Close all positions before switching trading mode.")
            if changes["trading_mode"] == "LIVE" and not LIVE_ENABLED:
                raise HTTPException(status_code=403, detail="LIVE mode is disabled. Start the app with AETHERKITE_ENABLE_LIVE=1 to enable it.")
        capital_changed = "starting_capital" in changes and changes["starting_capital"] != cfg["starting_capital"]
        if capital_changed and st["positions"]:
            raise HTTPException(status_code=409, detail="Close all positions before changing starting capital.")

        for key, value in changes.items():
            if key in SECRET_KEYS or key == "telegram_chat_id":
                if value is None or not str(value).strip():
                    continue  # blank secret fields mean "keep the saved value"
                value = str(value).strip()
            cfg[key] = value
        if req.clear_telegram:
            cfg["telegram_bot_token"], cfg["telegram_chat_id"], cfg["telegram_alerts_enabled"] = "", "", False

        if capital_changed:
            st["cash_balance"] = float(cfg["starting_capital"])
            st["day"] = {}
            roll_day(st, now)
            save_state(st)
        save_config(cfg)
    return {"status": "SUCCESS", "config": public_config(cfg), "cash_balance": st["cash_balance"]}


@app.post("/api/telegram-test")
def test_telegram_alert(req: TelegramTestRequest):
    cfg = load_config()
    token = (req.telegram_bot_token or "").strip() or cfg.get("telegram_bot_token", "")
    chat_id = (req.telegram_chat_id or "").strip() or cfg.get("telegram_chat_id", "")
    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="Telegram bot token and chat ID are required.")
    ok, error = send_telegram(
        "🔔 <b>AetherKite test alert</b>\n\nTelegram notifications are configured.\n"
        f"Time: {clock.now_ist():%Y-%m-%d %H:%M:%S} IST", token, chat_id)
    if ok:
        return {"status": "SUCCESS", "message": "Test message sent to Telegram."}
    raise HTTPException(status_code=502, detail=f"Telegram rejected the message. Check the bot token and chat ID. ({error})")
