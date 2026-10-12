# 🤖 Project Context & Developer Reference

This document provides a fast, comprehensive technical specification for AI coding agents and developers working on the **AetherKite Terminal**.

---

## 1. System Overview & Architecture

The **AetherKite Terminal** is a hybrid quantitative + LLM-gated intraday trading system designed for the National Stock Exchange of India (NSE).

```
[ NSE / BSE Feeds ] (Zerodha Kite WebSocket / Historical API or yfinance fallback)
       │
       ▼
[ Layer 1: Quant Engine ] 
   - 15-min closed candle evaluation (iloc[-2] to prevent repainting)
   - 200 EMA trend filter (Bullish regime)
   - MACD momentum crossover
   - RSI (14) bounded momentum [40 - 65]
   - ATR (14) dynamic volatility calculation
       │ Candidate Buy Setup
       ▼
[ Layer 2: Gemini 3.8 Flash AI Gatekeeper ]
   - Scrapes Google News RSS for symbol
   - Google Gemini 3.8 Flash parses headlines against regulatory/earnings risk
   - Strict Pydantic JSON schema: approve/veto + risk tier + explanation
       │ Approved Setup
       ▼
[ Layer 3: Risk & Execution State Machine ]
   - Available cash sizing: Position Size = (Available Cash * Risk%) / (1.5 * ATR)
   - NSE ₹0.05 tick-size rounding
   - Paper simulation OR Live Zerodha MIS (SL-Limit order after fill verification)
   - 09:45 AM morning whipsaw buffer & 15:15 PM mandatory intraday square-off
   - Emergency Kill Switch & Institutional Trading Journal
```

---

## 2. Directory Layout

```
c:\projects\Traiding\
├── app.py                              # FastAPI backend, quant logic, Gemini integration, broker execution
├── start_dashboard.bat                 # 1-click startup script (launches uvicorn and opens browser)
├── requirements.txt                    # Python dependency manifest
├── trading_config.json                 # Persistent system settings & API keys (JSON)
├── trading_state.json                  # Paper trading balances, open positions & order history
├── trading_journal.json                # Closed trade logs with entry/exit timestamps & P&L
├── static/
│   └── index.html                      # Single-page interactive trading terminal (Tailwind CSS + Vanilla JS)
├── README.md                           # User and developer onboarding guide
├── PROJECT_CONTEXT.md                  # This file (Agent reference specification)
├── UI_User_Guide.md                    # Business manual for non-technical traders
└── AI-Powered Trading System Setup Guide.md # Comprehensive quantitative & Zerodha architecture deep-dive
```

---

## 3. Technology Stack & Dependencies

* **Language:** Python 3.10+
* **Framework:** FastAPI with Uvicorn (`http://127.0.0.1:8000`)
* **Frontend:** Single-page application in [`static/index.html`](file:///c:/projects/Traiding/static/index.html) (Tailwind CSS CDN, Lucide icons, native audio synthesis, polling/refresh logic)
* **Market Data:** `yfinance` (real-time/historical 15m candles) & `kiteconnect` (Zerodha Kite Connect v5.0+)
* **Technical Analysis:** `pandas`, `pandas-ta`
* **AI Model:** Google Gemini 3.8 Flash via `google-genai` SDK
* **Notifications:** Telegram Bot API via standard `urllib.request` (zero external bloat)

---

## 4. Key Configuration & State Contracts

### `trading_config.json`
```json
{
  "trading_mode": "PAPER",                // "PAPER" or "LIVE"
  "starting_capital": 500000.0,
  "risk_per_trade_pct": 0.005,           // 0.5% - 1.5% per trade
  "max_portfolio_leverage": 4.0,
  "watchlist": ["RELIANCE", "TCS", ...], // Nifty 50 constituents
  "gemini_api_key": "AIza...",
  "kite_api_key": "...",
  "kite_api_secret": "...",
  "kite_access_token": "...",
  "nifty_regime_filter": true,
  "morning_buffer_enabled": true,        // Disallows entry before 09:45 AM IST
  "intraday_cutoff_time": "15:15",       // Blocks entry & auto-square-off cutoff
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "telegram_alerts_enabled": false
}
```

### `trading_state.json`
Contains real-time state:
* `cash_balance` (float): Liquid capital available.
* `positions` (dict): Map of symbol -> active open position `{symbol, qty, entry_price, sl_price, target_price, timestamp, ...}`.
* `order_history` (list): Executed orders with status.
* `system_halted` (bool): `true` when Kill Switch is active.

### `trading_journal.json`
Array of completed trades:
* Fields: `id`, `symbol`, `entry_time`, `exit_time`, `entry_price`, `exit_price`, `quantity`, `pnl`, `exit_reason`, `notes`, `quant_reason`, `ai_reason`.

---

## 5. Backend REST API Endpoints ([`app.py`](file:///c:/projects/Traiding/app.py))

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | Serves dashboard UI (`static/index.html`). |
| `/api/status` | `GET` | Returns cash balance, open positions, active P&L, system status, market status, and index summary. |
| `/api/scan` | `GET` | Multi-threaded radar scan across watchlist (returns 200 EMA, MACD, RSI, ATR, Quant signal, and Gemini AI evaluation). |
| `/api/execute-trade` | `POST` | Executes a BUY setup (creates open position in Paper or submits MIS order on Kite Connect). |
| `/api/close-position` | `POST` | Exits an individual open position, finalizes P&L, and logs to journal. |
| `/api/kill-switch` | `POST` | Emergency liquidator: flattens all open positions, cancels orders, and flips `system_halted=True`. |
| `/api/resume-system` | `POST` | Clears `system_halted` and resumes regular trading. |
| `/api/settings` | `POST` | Updates and persists `trading_config.json`. |
| `/api/telegram-test` | `POST` | Dispatches a diagnostic test alert to configured Telegram chat. |
| `/api/journal` | `GET` | Fetches closed trades and calculated metrics (Win Rate, Profit Factor, Net P&L). |
| `/api/journal/note` | `POST` | Updates trader reflection notes on a specific journal trade. |
| `/api/journal/export` | `GET` | Generates and downloads `trading_journal.csv`. |
| `/api/kite-login` | `GET` | Redirects to Zerodha login URL. |
| `/api/kite-callback` | `GET` | Exchanges `request_token` for `kite_access_token` and saves to config. |

---

## 6. Critical Invariants & Rules for Coding Agents

When editing or extending [`app.py`](file:///c:/projects/Traiding/app.py) or [`static/index.html`](file:///c:/projects/Traiding/static/index.html), you **must adhere to these invariants**:

1. **Repainting Elimination (`iloc[-2]`):**
   * Never evaluate quantitative signals on `iloc[-1]` (the incomplete developing candle). All momentum checks (EMA, MACD, RSI) must run against `iloc[-2]`.
2. **Tick Size Compliance (`₹0.05`):**
   * Prices sent to Zerodha or rendered for Stop-Loss / Take-Profit must be rounded using `round_to_tick(price, 0.05)`.
3. **Thread Safety & File Locks:**
   * Mutating state or journal files must use `STATE_LOCK` and `JOURNAL_LOCK` (`threading.Lock()`).
4. **Timezone Awareness:**
   * NSE operations operate under Indian Standard Time (`UTC+05:30`). Use `get_ist_time()`.
5. **Two-Layer AI Gatekeeper Contract:**
   * Gemini 3.8 Flash is strictly a **risk veto**. It never generates buy signals on its own. It only evaluates setups that have already passed Layer 1 quantitative screening.
6. **No Breaking Frontend Dependencies:**
   * The frontend in [`static/index.html`](file:///c:/projects/Traiding/static/index.html) is self-contained. Do not add complex build tools (Webpack/Vite/npm) unless explicitly requested; it currently runs cleanly via FastAPI static file serving.
