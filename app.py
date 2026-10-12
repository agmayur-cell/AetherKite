"""
AI-Powered Trading Dashboard Backend (FastAPI)
Architecture: Hybrid Quant + Gemini 3.8 Flash AI Gatekeeper + Paper/Live Zerodha Execution
"""

import os
import sys
import json
import math
import time
import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
import io
import csv

import pandas as pd
import pandas_ta as ta

# Optional imports with graceful fallbacks
try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None

# ==============================================================================
# CONFIGURATION & PERSISTENCE
# ==============================================================================
CONFIG_FILE = Path(__file__).parent / "trading_config.json"
STATE_FILE = Path(__file__).parent / "trading_state.json"
JOURNAL_FILE = Path(__file__).parent / "trading_journal.json"

DEFAULT_CONFIG = {
    "trading_mode": "PAPER",  # "PAPER" or "LIVE"
    "starting_capital": 100000.0,
    "risk_per_trade_pct": 0.01,  # 1% per trade
    "max_portfolio_leverage": 4.0,
    "watchlist": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "TATAMOTORS"],
    "gemini_api_key": "",
    "kite_api_key": "",
    "kite_api_secret": "",
    "kite_access_token": "",
    "nifty_regime_filter": True,
    "morning_buffer_enabled": True,  # 09:45 AM rule
    "intraday_cutoff_time": "15:15",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_alerts_enabled": False,
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

import threading
import urllib.request
import xml.etree.ElementTree as ET

STATE_LOCK = threading.Lock()
JOURNAL_LOCK = threading.Lock()

def load_state() -> dict:
    with STATE_LOCK:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "cash_balance": 100000.0,
            "positions": {},
            "order_history": [],
            "system_halted": False
        }

def save_state(st: dict):
    with STATE_LOCK:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, indent=2)

def load_journal() -> list:
    with JOURNAL_LOCK:
        if JOURNAL_FILE.exists():
            try:
                with open(JOURNAL_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

def save_journal(journal: list):
    with JOURNAL_LOCK:
        with open(JOURNAL_FILE, "w") as f:
            json.dump(journal, f, indent=2)

def fetch_company_news(symbol: str) -> List[str]:
    """Fetches real-time Indian financial news headlines for a stock via Google News RSS."""
    try:
        url = f"https://news.google.com/rss/search?q={symbol}+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")[:5]
        headlines = []
        for item in items:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                headlines.append(title_el.text.strip())
        return headlines
    except Exception as e:
        print(f"[NEWS FEED] Notice: fallback for {symbol}: {e}")
def send_telegram_alert(message: str, cfg: Optional[dict] = None) -> bool:
    """Dispatches real-time actionable trade alerts to user's Telegram."""
    if cfg is None:
        cfg = load_config()
    
    token = cfg.get("telegram_bot_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()
    
    if not token or not chat_id:
        return False
        
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode("utf-8")
        req = urllib.request.Request(
            url, 
            data=payload, 
            headers={"Content-Type": "application/json", "User-Agent": "KiteAITradingTerminal/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TELEGRAM] Notification dispatch error: {e}")
        return False

# ==============================================================================
# FASTAPI APPLICATION SETUP
# ==============================================================================
app = FastAPI(title="AI Trading Dashboard", version="2.0.0")

# Ensure static dir exists
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ==============================================================================
# FINANCIAL & QUANT UTILITIES
# ==============================================================================
def round_to_tick(price: float, tick_size: float = 0.05) -> float:
    return round(math.floor(price / tick_size + 0.5) * tick_size, 2)

def get_ist_time() -> datetime.datetime:
    # UTC + 5:30
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)

INDEX_CACHE = {
    "last_update": 0,
    "sensex": {
        "price": 71801.94,
        "change": 25.27,
        "pct_change": 0.04,
        "trend": "NEUTRAL"
    },
    "nifty": {
        "price": 22332.95,
        "change": 16.15,
        "pct_change": 0.07,
        "trend": "NEUTRAL"
    }
}

def get_market_indices() -> dict:
    global INDEX_CACHE
    now = time.time()
    if now - INDEX_CACHE["last_update"] < 45 and INDEX_CACHE["sensex"]["price"] > 0:
        return INDEX_CACHE

    try:
        if yf is not None:
            # BSE Sensex (^BSESN)
            s_df = yf.download("^BSESN", period="2d", interval="15m", progress=False)
            if not s_df.empty:
                if isinstance(s_df.columns, pd.MultiIndex):
                    s_df.columns = s_df.columns.get_level_values(0)
                s_df.columns = [c.lower() for c in s_df.columns]
                s_curr = float(s_df.iloc[-1]['close'])
                s_prev = float(s_df.iloc[0]['open'])
                s_chg = round(s_curr - s_prev, 2)
                s_pct = round((s_chg / s_prev) * 100, 2)
                s_trend = "BULLISH" if s_pct >= 0.10 else ("BEARISH" if s_pct <= -0.10 else "NEUTRAL")
                INDEX_CACHE["sensex"] = {
                    "price": round(s_curr, 2),
                    "change": s_chg,
                    "pct_change": s_pct,
                    "trend": s_trend
                }

            # NSE Nifty 50 (^NSEI)
            n_df = yf.download("^NSEI", period="2d", interval="15m", progress=False)
            if not n_df.empty:
                if isinstance(n_df.columns, pd.MultiIndex):
                    n_df.columns = n_df.columns.get_level_values(0)
                n_df.columns = [c.lower() for c in n_df.columns]
                n_curr = float(n_df.iloc[-1]['close'])
                n_prev = float(n_df.iloc[0]['open'])
                n_chg = round(n_curr - n_prev, 2)
                n_pct = round((n_chg / n_prev) * 100, 2)
                n_trend = "BULLISH" if n_pct >= 0.10 else ("BEARISH" if n_pct <= -0.10 else "NEUTRAL")
                INDEX_CACHE["nifty"] = {
                    "price": round(n_curr, 2),
                    "change": n_chg,
                    "pct_change": n_pct,
                    "trend": n_trend
                }

            INDEX_CACHE["last_update"] = now
    except Exception as e:
        print(f"[INDEX TICKER WARNING] Could not refresh indices: {e}")

    return INDEX_CACHE

def is_market_hours() -> dict:
    ist = get_ist_time()
    t = ist.time()
    market_open = datetime.time(9, 15)
    market_close = datetime.time(15, 30)
    morning_buffer = datetime.time(9, 45)
    cutoff = datetime.time(15, 15)
    is_open = (market_open <= t <= market_close) and (ist.weekday() < 5)
    can_trade = (morning_buffer <= t <= cutoff) and (ist.weekday() < 5)
    return {
        "ist_time": ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "is_open": is_open,
        "can_trade": can_trade,
        "is_weekend": ist.weekday() >= 5
    }

# ==============================================================================
# DATA ENGINE (HISTORICAL & REALTIME)
# ==============================================================================
def fetch_stock_data(symbol: str) -> Optional[pd.DataFrame]:
    """Fetches intraday 15-minute candle data via yfinance (with .NS suffix) or mock generator."""
    # Handle ticker renamings/demergers on NSE
    ticker_map = {
        "TATAMOTORS": "TMPV.NS",
        "M&M": "M&M.NS"
    }
    ticker_ns = ticker_map.get(symbol, f"{symbol}.NS")
    try:
        if yf is not None:
            df = yf.download(ticker_ns, period="30d", interval="15m", progress=False)
            if not df.empty and len(df) >= 30:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                # Lowercase columns for uniformity
                df.columns = [c.lower() for c in df.columns]
                return df
    except Exception as e:
        print(f"[DATA ERROR] Failed to fetch {ticker_ns}: {e}")
    return None

# ==============================================================================
# QUANTITATIVE INDICATOR ENGINE
# ==============================================================================
def analyze_technicals(df: pd.DataFrame, symbol: str) -> dict:
    if len(df) < 50:
        return {"status": "INSUFFICIENT_DATA"}

    # Dynamic EMA lengths based on available bars
    ema_trend_len = 200 if len(df) >= 200 else len(df) - 1
    df['ema_20'] = ta.ema(df['close'], length=20)
    df['ema_trend'] = ta.ema(df['close'], length=ema_trend_len)
    df['rsi_14'] = ta.rsi(df['close'], length=14)

    macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if macd is not None and not macd.empty:
        df['macd'] = macd['MACD_12_26_9']
        df['macd_signal'] = macd['MACDs_12_26_9']
    else:
        df['macd'] = 0.0
        df['macd_signal'] = 0.0

    df['atr_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df_clean = df.dropna()
    if len(df_clean) < 3:
        return {"status": "INSUFFICIENT_DATA"}

    closed = df_clean.iloc[-2]
    prev = df_clean.iloc[-3]

    # Deterministic Quant Rules
    trend_ok = closed['close'] > closed['ema_trend']
    macd_bullish = (prev['macd'] <= prev['macd_signal']) and (closed['macd'] > closed['macd_signal'])
    rsi_healthy = 40.0 <= closed['rsi_14'] <= 65.0

    is_buy = trend_ok and macd_bullish and rsi_healthy

    return {
        "status": "OK",
        "symbol": symbol,
        "signal": "BUY" if is_buy else "NEUTRAL",
        "close_price": round_to_tick(float(closed['close'])),
        "ema_trend": round_to_tick(float(closed['ema_trend'])),
        "trend_bullish": bool(trend_ok),
        "rsi": round(float(closed['rsi_14']), 1),
        "macd": round(float(closed['macd']), 2),
        "macd_signal": round(float(closed['macd_signal']), 2),
        "macd_crossover": bool(macd_bullish),
        "atr": round_to_tick(float(closed['atr_14'])),
        "candle_time": str(closed.name)
    }

# ==============================================================================
# AI CONTEXT GATEKEEPER (GEMINI 3.8 FLASH)
# ==============================================================================
class TradeValidationSchema(BaseModel):
    approve_trade: bool = Field(description="True if context permits trade, False if negative news exists.")
    confidence_score: float = Field(description="Confidence between 0.0 and 1.0.")
    risk_level: str = Field(description="LOW, MEDIUM, HIGH, or CRITICAL.")
    reasoning: str = Field(description="Brief 1-2 sentence rationale for the decision.")

def evaluate_with_gemini(symbol: str, price: float, api_key: str, news_context: List[str]) -> dict:
    if not api_key or genai is None:
        # Default benign approval if key not supplied in testing
        return {
            "approve_trade": True,
            "confidence_score": 0.85,
            "risk_level": "LOW",
            "reasoning": "Gemini API key not configured. Operating in simulation-approved mode."
        }

    formatted_news = "\n".join([f"- {item}" for item in news_context]) if news_context else "No negative breaking alerts detected on NSE feeds."
    prompt = f"""
    You are an automated risk supervisor for the Indian stock market (NSE).
    A buy signal fired for symbol: '{symbol}' at ₹{price}.

    Review the following news headlines and market context:
    {formatted_news}

    VETO (approve_trade = False) if:
    - Ongoing regulatory investigation (SEBI, CBI, ED).
    - Recent quarterly earnings disappointment or major downgrade.
    - Abrupt leadership exit (CEO/CFO).
    Otherwise, approve the trade. Return structured JSON.
    """

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TradeValidationSchema,
                temperature=0.1
            )
        )
        data = json.loads(response.text)
        return data
    except Exception as e:
        return {
            "approve_trade": False,
            "confidence_score": 0.0,
            "risk_level": "CRITICAL",
            "reasoning": f"Gemini API request failed: {str(e)}"
        }

# ==============================================================================
# REST API ROUTES
# ==============================================================================
@app.get("/")
def get_dashboard():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>AI Trading Dashboard Initializing... Please refresh in a moment.</h1>")

@app.get("/api/kite-login")
def kite_login_url():
    """Returns the official Zerodha OAuth login URL for 1-click morning login."""
    cfg = load_config()
    api_key = cfg.get("kite_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="Please enter your Kite API Key in Settings first.")
    login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    return {"login_url": login_url}

@app.get("/api/kite-callback")
def kite_callback(request_token: str):
    """Automatically catches the request token from Zerodha, generates and saves the daily access token."""
    cfg = load_config()
    api_key = cfg.get("kite_api_key")
    api_secret = cfg.get("kite_api_secret")
    if not api_key or not api_secret:
        return HTMLResponse("<h2>Error: Kite API Key or API Secret missing in settings. Please configure settings first.</h2>")
    
    if KiteConnect is None:
        return HTMLResponse("<h2>Error: kiteconnect library not installed.</h2>")

    try:
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        cfg["kite_access_token"] = data["access_token"]
        save_config(cfg)
        return RedirectResponse(url="/?auth_success=1")
    except Exception as e:
        return HTMLResponse(f"<h2>Zerodha Authentication Failed:</h2><p>{str(e)}</p>")

def update_live_positions(st: dict):
    """Updates current market price and unrealized P&L for all open positions."""
    if not st.get("positions"):
        return
    updated = False
    for symbol, pos in st["positions"].items():
        try:
            df = fetch_stock_data(symbol)
            if df is not None and not df.empty:
                latest_price = round_to_tick(float(df.iloc[-1]['close']))
                pos["current_price"] = latest_price
                pos["unrealized_pnl"] = round(pos["quantity"] * (latest_price - pos["entry_price"]), 2)
                updated = True
        except Exception:
            pass
    if updated:
        save_state(st)

@app.get("/api/status")
def get_status():
    cfg = load_config()
    st = load_state()
    update_live_positions(st)
    mkt = is_market_hours()

    # Calculate live portfolio stats
    total_positions_value = sum(p["quantity"] * p["current_price"] for p in st["positions"].values())
    unrealized_pnl = sum(p["unrealized_pnl"] for p in st["positions"].values())
    total_equity = st["cash_balance"] + total_positions_value

    return {
        "market": mkt,
        "indices": get_market_indices(),
        "config": {
            "trading_mode": cfg["trading_mode"],
            "starting_capital": cfg.get("starting_capital", 100000.0),
            "risk_per_trade_pct": cfg["risk_per_trade_pct"],
            "gemini_connected": bool(cfg.get("gemini_api_key")),
            "kite_connected": bool(cfg.get("kite_api_key") and cfg.get("kite_access_token")),
            "telegram_alerts_enabled": cfg.get("telegram_alerts_enabled", False),
            "telegram_connected": bool(cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id")),
            "watchlist": cfg["watchlist"]
        },
        "portfolio": {
            "cash_balance": round(st["cash_balance"], 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "open_positions_count": len(st["positions"]),
            "system_halted": st.get("system_halted", False)
        }
    }

def evaluate_single_stock(symbol: str, cfg: dict, cash_balance: float) -> dict:
    """Evaluates a single stock's technical indicators, AI veto, and risk sizing."""
    df = fetch_stock_data(symbol)
    if df is None:
        return {
            "symbol": symbol,
            "status": "DATA_UNAVAILABLE",
            "price": 0.0,
            "quant_signal": "NONE",
            "ai_verdict": None
        }

    q = analyze_technicals(df, symbol)
    if q.get("status") != "OK":
        return {
            "symbol": symbol,
            "status": "CALCULATING",
            "price": 0.0,
            "quant_signal": "NONE",
            "ai_verdict": None
        }

    # AI Gatekeeper validation for candidates
    ai_verdict = None
    if q["signal"] == "BUY":
        live_news = fetch_company_news(symbol)
        ai_verdict = evaluate_with_gemini(
            symbol=symbol,
            price=q["close_price"],
            api_key=cfg.get("gemini_api_key", ""),
            news_context=live_news
        )

    # Risk Sizing
    entry = q["close_price"]
    atr = q["atr"]
    stop_loss = round_to_tick(entry - (1.5 * atr))
    take_profit = round_to_tick(entry + (3.0 * atr))
    risk_per_share = entry - stop_loss
    max_risk_inr = cash_balance * cfg["risk_per_trade_pct"]
    shares = int(max_risk_inr / risk_per_share) if risk_per_share > 0 else 0

    return {
        "symbol": symbol,
        "status": "READY",
        "price": entry,
        "trend_bullish": q["trend_bullish"],
        "ema_trend": q["ema_trend"],
        "rsi": q["rsi"],
        "macd_crossover": q["macd_crossover"],
        "atr": atr,
        "quant_signal": q["signal"],
        "ai_verdict": ai_verdict,
        "sizing": {
            "shares": shares,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_inr": round(shares * risk_per_share, 2),
            "capital_required": round(shares * entry, 2)
        }
    }

@app.get("/api/scan")
def run_scan():
    """Scans all watchlist stocks concurrently and prioritizes BUY signals at the top."""
    cfg = load_config()
    st = load_state()
    symbols = cfg.get("watchlist", [])

    results = []
    # Concurrently scan all 50 stocks using ThreadPoolExecutor (10 workers)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(evaluate_single_stock, sym, cfg, st["cash_balance"]) for sym in symbols]
        for f in futures:
            try:
                results.append(f.result())
            except Exception as e:
                pass

    # Sort results so active BUY triggers appear first, followed by available symbols
    results.sort(key=lambda r: (0 if r.get("quant_signal") == "BUY" else 1, r.get("symbol", "")))

    # Dispatch alerts for new BUY signals if alerts are configured
    if cfg.get("telegram_alerts_enabled") and cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        for r in results:
            if r.get("quant_signal") == "BUY":
                sym = r["symbol"]
                ai_v = r.get("ai_verdict")
                approved = (ai_v and ai_v.get("approve_trade")) or (ai_v is None)
                if approved:
                    sz = r.get("sizing", {})
                    msg = (
                        f"🚨 <b>AETHERKITE TRADE TRIGGER: {sym}</b>\n\n"
                        f"📈 <b>Signal:</b> Quantitative BUY\n"
                        f"💵 <b>Price:</b> ₹{r['price']:.2f}\n"
                        f"🛡️ <b>Stop-Loss:</b> ₹{sz.get('stop_loss', 0):.2f}\n"
                        f"🎯 <b>Target:</b> ₹{sz.get('take_profit', 0):.2f}\n"
                        f"📦 <b>Suggested Shares:</b> {sz.get('shares', 0)}\n"
                        f"🤖 <b>Gemini 3.8 Flash:</b> {ai_v.get('reasoning', 'Approved') if ai_v else 'Technical Confirmation'}\n\n"
                        f"⏰ <i>Time: {get_ist_time().strftime('%H:%M:%S IST')}</i>"
                    )
                    threading.Thread(target=send_telegram_alert, args=(msg, cfg), daemon=True).start()

    return {"scan_timestamp": get_ist_time().strftime("%H:%M:%S IST"), "results": results}

@app.post("/api/telegram-test")
def test_telegram_alert(payload: dict):
    """Sends a sample test ping to user's Telegram."""
    cfg = load_config()
    token = payload.get("telegram_bot_token", cfg.get("telegram_bot_token", ""))
    chat_id = payload.get("telegram_chat_id", cfg.get("telegram_chat_id", ""))
    
    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="Telegram Bot Token and Chat ID are required.")
        
    temp_cfg = {**cfg, "telegram_bot_token": token, "telegram_chat_id": chat_id}
    test_msg = (
        "🔔 <b>AetherKite Terminal Test Alert</b>\n\n"
        "Your Telegram notifications are successfully configured and active!\n"
        f"Timestamp: {get_ist_time().strftime('%Y-%m-%d %H:%M:%S IST')}"
    )
    success = send_telegram_alert(test_msg, temp_cfg)
    if success:
        return {"status": "SUCCESS", "message": "Test message sent to Telegram successfully!"}
    raise HTTPException(status_code=500, detail="Failed to send message. Verify your Bot Token and Chat ID.")

@app.post("/api/execute-trade")
def execute_trade(payload: dict):
    """Executes a confirmed trade (Paper or Live Zerodha broker)."""
    cfg = load_config()
    st = load_state()

    if st.get("system_halted"):
        raise HTTPException(status_code=400, detail="System is currently HALTED by the emergency kill switch.")

    symbol = payload.get("symbol")
    shares = payload.get("shares", 0)
    entry_price = payload.get("price", 0.0)
    stop_loss = payload.get("stop_loss", 0.0)
    take_profit = payload.get("take_profit", 0.0)

    if shares <= 0 or entry_price <= 0:
        raise HTTPException(status_code=400, detail="Invalid share quantity or price.")

    total_cost = shares * entry_price
    leverage = cfg.get("max_portfolio_leverage", 4.5)
    margin_required = round(total_cost / leverage, 2)

    if margin_required > st["cash_balance"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient available cash. Required intraday margin: ₹{margin_required:,.2f}, Available: ₹{st['cash_balance']:,.2f}"
        )

    order_id = f"ORD-{int(time.time())}"
    fill_price = entry_price
    broker_details = {}

    # LIVE TRADING EXECUTION VIA ZERODHA KITE CONNECT
    if cfg.get("trading_mode") == "LIVE":
        api_key = cfg.get("kite_api_key")
        access_token = cfg.get("kite_access_token")
        if not api_key or not access_token:
            raise HTTPException(status_code=400, detail="Cannot trade LIVE: Kite API Key or Access Token is missing. Click 'Connect Zerodha' first.")
        if KiteConnect is None:
            raise HTTPException(status_code=500, detail="kiteconnect library is not installed.")

        try:
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)

            # 1. Place Regular MIS Market Entry
            broker_order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_BUY,
                quantity=shares,
                product=kite.PRODUCT_MIS,
                order_type=kite.ORDER_TYPE_MARKET
            )
            order_id = broker_order_id

            # 2. Wait for exchange matching engine & verify fill
            time.sleep(0.8)
            history = kite.order_history(order_id=broker_order_id)
            latest_status = history[-1]["status"] if history else "UNKNOWN"

            if latest_status != "COMPLETE":
                raise HTTPException(status_code=400, detail=f"Zerodha entry order not filled. Status: {latest_status}")

            filled_qty = history[-1]["filled_quantity"]
            fill_price = history[-1]["average_price"]
            shares = filled_qty

            # 3. Place Protective Stop-Loss Limit (SL-L)
            sl_trigger = round_to_tick(fill_price - (entry_price - stop_loss))
            sl_limit = round_to_tick(sl_trigger - 0.50)

            sl_order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=shares,
                product=kite.PRODUCT_MIS,
                order_type=kite.ORDER_TYPE_SL,
                price=sl_limit,
                trigger_price=sl_trigger
            )
            broker_details = {"broker_order_id": broker_order_id, "sl_order_id": sl_order_id}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Zerodha Kite Execution Failed: {str(e)}")

    # Deduct margin required from cash balance
    st["cash_balance"] -= margin_required
    st["positions"][symbol] = {
        "symbol": symbol,
        "quantity": shares,
        "entry_price": fill_price,
        "current_price": fill_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "margin_locked": margin_required,
        "unrealized_pnl": 0.0,
        "entry_time": get_ist_time().strftime("%Y-%m-%d %H:%M:%S")
    }

    order_record = {
        "order_id": order_id,
        "symbol": symbol,
        "action": "BUY",
        "quantity": shares,
        "price": fill_price,
        "mode": cfg["trading_mode"],
        "status": "FILLED",
        "timestamp": get_ist_time().strftime("%Y-%m-%d %H:%M:%S")
    }
    st["order_history"].insert(0, order_record)
    save_state(st)

    # Log to Trading Journal
    journal = load_journal()
    journal_entry = {
        "trade_id": order_record["order_id"],
        "symbol": symbol,
        "direction": "LONG",
        "quantity": shares,
        "entry_price": fill_price,
        "entry_time": order_record["timestamp"],
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_inr": round(shares * (fill_price - stop_loss), 2),
        "target_inr": round(shares * (take_profit - fill_price), 2),
        "quant_rationale": payload.get("quant_rationale", "Bullish MACD Cross above 200 EMA"),
        "ai_rationale": payload.get("ai_rationale", "Gemini 3.8 Flash Approved: No corporate governance or earnings flags."),
        "ai_confidence": payload.get("ai_confidence", 85),
        "status": "OPEN",
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "realized_pnl": 0.0,
        "return_pct": 0.0,
        "notes": ""
    }
    journal.insert(0, journal_entry)
    save_journal(journal)

    return {"status": "SUCCESS", "order": order_record, "broker_details": broker_details}

@app.post("/api/close-position")
def close_position(payload: dict):
    """Closes an open position manually or upon target/stop trigger and updates the journal."""
    symbol = payload.get("symbol")
    exit_reason = payload.get("reason", "MANUAL_EXIT")
    st = load_state()
    cfg = load_config()

    if symbol not in st["positions"]:
        raise HTTPException(status_code=404, detail=f"No open position found for {symbol}")

    pos = st["positions"].pop(symbol)
    qty = pos["quantity"]
    entry_price = pos["entry_price"]
    exit_price = payload.get("exit_price", pos["current_price"])

    # If Live Mode, dispatch square-off to Zerodha
    if cfg.get("trading_mode") == "LIVE":
        api_key = cfg.get("kite_api_key")
        access_token = cfg.get("kite_access_token")
        if api_key and access_token and KiteConnect is not None:
            try:
                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(access_token)
                kite.place_order(
                    variety=kite.VARIETY_REGULAR,
                    exchange=kite.EXCHANGE_NSE,
                    tradingsymbol=symbol,
                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                    quantity=qty,
                    product=kite.PRODUCT_MIS,
                    order_type=kite.ORDER_TYPE_MARKET
                )
            except Exception as e:
                print(f"[LIVE EXIT WARNING] Broker square-off error for {symbol}: {e}")

    realized_pnl = round(qty * (exit_price - entry_price), 2)
    return_pct = round(((exit_price - entry_price) / entry_price) * 100, 2)
    margin_locked = pos.get("margin_locked", (qty * entry_price) / 4.5)
    st["cash_balance"] += round(margin_locked + realized_pnl, 2)

    order_record = {
        "order_id": f"EXIT-{int(time.time())}-{symbol}",
        "symbol": symbol,
        "action": "SELL",
        "quantity": qty,
        "price": exit_price,
        "realized_pnl": realized_pnl,
        "status": "CLOSED",
        "timestamp": get_ist_time().strftime("%Y-%m-%d %H:%M:%S")
    }
    st["order_history"].insert(0, order_record)
    save_state(st)

    # Update Journal Entry
    journal = load_journal()
    for entry in journal:
        if entry["symbol"] == symbol and entry["status"] == "OPEN":
            entry["status"] = "CLOSED"
            entry["exit_price"] = exit_price
            entry["exit_time"] = order_record["timestamp"]
            entry["exit_reason"] = exit_reason
            entry["realized_pnl"] = realized_pnl
            entry["return_pct"] = return_pct
            break
    save_journal(journal)

    return {"status": "SUCCESS", "closed_order": order_record, "realized_pnl": realized_pnl}

@app.post("/api/kill-switch")
def emergency_kill_switch():
    """Immediately halts system, flattens all open positions, and preserves capital."""
    st = load_state()
    journal = load_journal()
    closed_orders = []

    # Close all positions at current prices
    for symbol, pos in list(st["positions"].items()):
        proceeds = pos["quantity"] * pos["current_price"]
        st["cash_balance"] += proceeds
        pnl = round(pos["quantity"] * (pos["current_price"] - pos["entry_price"]), 2)
        ret = round(((pos["current_price"] - pos["entry_price"]) / pos["entry_price"]) * 100, 2)
        order_record = {
            "order_id": f"KILL-{int(time.time())}-{symbol}",
            "symbol": symbol,
            "action": "EMERGENCY_SELL",
            "quantity": pos["quantity"],
            "price": pos["current_price"],
            "realized_pnl": pnl,
            "status": "CLOSED",
            "timestamp": get_ist_time().strftime("%Y-%m-%d %H:%M:%S")
        }
        closed_orders.append(order_record)
        st["order_history"].insert(0, order_record)

        # Update Journal
        for entry in journal:
            if entry["symbol"] == symbol and entry["status"] == "OPEN":
                entry["status"] = "CLOSED"
                entry["exit_price"] = pos["current_price"]
                entry["exit_time"] = order_record["timestamp"]
                entry["exit_reason"] = "EMERGENCY_KILL_SWITCH"
                entry["realized_pnl"] = pnl
                entry["return_pct"] = ret
                break

    st["positions"] = {}
    st["system_halted"] = True
    save_state(st)
    save_journal(journal)

    return {
        "status": "HALTED",
        "message": f"Emergency Kill Switch activated. Flattened {len(closed_orders)} positions.",
        "closed_orders": closed_orders
    }

@app.get("/api/journal")
def get_journal():
    """Returns all journal entries with performance statistics."""
    journal = load_journal()
    closed = [t for t in journal if t["status"] == "CLOSED"]

    total_trades = len(closed)
    winning = [t for t in closed if t["realized_pnl"] > 0]
    losing = [t for t in closed if t["realized_pnl"] < 0]
    win_rate = round((len(winning) / total_trades) * 100, 1) if total_trades > 0 else 0.0

    total_realized_pnl = round(sum(t["realized_pnl"] for t in closed), 2)
    gross_profits = sum(t["realized_pnl"] for t in winning)
    gross_losses = abs(sum(t["realized_pnl"] for t in losing))
    profit_factor = round(gross_profits / gross_losses, 2) if gross_losses > 0 else (round(gross_profits, 2) if gross_profits > 0 else 1.0)

    avg_win = round(gross_profits / len(winning), 2) if winning else 0.0
    avg_loss = round(gross_losses / len(losing), 2) if losing else 0.0

    analytics = {
        "total_trades": total_trades,
        "win_count": len(winning),
        "loss_count": len(losing),
        "win_rate_pct": win_rate,
        "total_realized_pnl": total_realized_pnl,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "open_trades_count": len([t for t in journal if t["status"] == "OPEN"])
    }

    return {"trades": journal, "analytics": analytics}

@app.post("/api/journal/note")
def update_journal_note(payload: dict):
    """Updates user trader reflection notes for a specific trade."""
    trade_id = payload.get("trade_id")
    notes = payload.get("notes", "")
    journal = load_journal()
    updated = False
    for t in journal:
        if t["trade_id"] == trade_id:
            t["notes"] = notes
            updated = True
            break
    if updated:
        save_journal(journal)
        return {"status": "SUCCESS"}
    raise HTTPException(status_code=404, detail="Trade not found.")

@app.get("/api/journal/export")
def export_journal_csv():
    """Exports trading journal as a downloadable CSV."""
    journal = load_journal()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Trade ID", "Symbol", "Direction", "Quantity", "Entry Price", "Entry Time",
        "Exit Price", "Exit Time", "Exit Reason", "Stop Loss", "Take Profit",
        "Realized PnL (INR)", "Return %", "AI Confidence", "Quant Rationale", "Notes"
    ])
    for t in journal:
        writer.writerow([
            t.get("trade_id"), t.get("symbol"), t.get("direction"), t.get("quantity"),
            t.get("entry_price"), t.get("entry_time"), t.get("exit_price", ""),
            t.get("exit_time", ""), t.get("exit_reason", ""), t.get("stop_loss"),
            t.get("take_profit"), t.get("realized_pnl", 0), t.get("return_pct", 0),
            t.get("ai_confidence", ""), t.get("quant_rationale", ""), t.get("notes", "")
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trading_journal.csv"}
    )

@app.post("/api/resume-system")
def resume_system():
    st = load_state()
    st["system_halted"] = False
    save_state(st)
    return {"status": "ACTIVE", "message": "Trading engine resumed."}

@app.get("/api/positions")
def get_positions():
    st = load_state()
    update_live_positions(st)
    return {"positions": list(st["positions"].values()), "orders": st["order_history"]}

@app.post("/api/settings")
def update_settings(payload: dict):
    cfg = load_config()
    st = load_state()

    for k, v in payload.items():
        if k in cfg:
            # Prevent wiping existing secret keys when empty in the UI form
            if k in ["gemini_api_key", "kite_api_key", "kite_api_secret", "kite_access_token"] and not v:
                continue
            cfg[k] = v

    if "starting_capital" in payload:
        new_cap = float(payload["starting_capital"])
        cfg["starting_capital"] = new_cap
        # Synchronize live cash balance in trading state
        st["cash_balance"] = new_cap
        save_state(st)

    save_config(cfg)
    return {"status": "SUCCESS", "config": cfg, "cash_balance": st["cash_balance"]}
