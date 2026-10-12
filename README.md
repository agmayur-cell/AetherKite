# 🚀 AetherKite Terminal

An institutional-grade, hybrid algorithmic trading terminal built for the **National Stock Exchange of India (NSE)**.

The system combines a **deterministic quantitative momentum engine** with an **AI Risk Gatekeeper** powered by **Google Gemini 3.8 Flash**, executing either via **Paper Simulation** or live through **Zerodha Kite Connect**.

---

## 🌟 Key Features

* **Tri-Layer Architecture:**
  1. **Layer 1 (Quant Momentum):** Closed 15-minute candle analysis using 200 EMA (trend filter), MACD crossover, RSI momentum (40–65 range), and ATR volatility sizing.
  2. **Layer 2 (Gemini 3.8 Flash Gatekeeper):** Real-time NLP analysis of news headlines and corporate disclosures. Flags negative catalysts (earnings surprises, regulatory actions) and acts as an automatic risk veto.
  3. **Layer 3 (Risk & Execution Engine):** Available cash risk allocation (default 0.5%–1.0% risk per trade), ₹0.05 NSE tick rounding, SL-Limit protection, and strict intraday session boundaries.
* **Paper & Live Trading Modes:** Test strategies safely with real-time market data in paper trading mode before switching to Zerodha Kite Connect live execution.
* **Emergency Kill Switch:** 1-click panic button immediately closes all open positions and freezes new trades during black swan events.
* **Intraday Safeguards:** 
  * 09:45 AM morning volatility buffer to avoid opening whipsaws.
  * 15:15 PM mandatory auto-square-off cutoff.
* **Trading Journal & Performance Metrics:** Tracks all closed trades, win rate %, profit factor, realized P&L, editable trader reflection notes, and 1-click CSV export.
* **Multi-Channel Alerts:** Real-time browser audio chimes and instant smartphone notifications via Telegram.

---

## ⚡ Quick Start

### 1. Prerequisites
* Python 3.10 or higher installed.
* Chrome / Edge / Firefox browser.

### 2. Installation
Clone or navigate to the repository and install dependencies:
```bash
cd c:\projects\Traiding
pip install -r requirements.txt
```

### 3. Launching the Dashboard
* **Option A (Double-Click):** Run `start_dashboard.bat`.
* **Option B (Command Line):**
  ```bash
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
  ```
The dashboard will open automatically in your browser at `http://127.0.0.1:8000`.

---

## ⚙️ Configuration & API Keys

Settings can be managed directly in the dashboard UI (**Settings** button) or via [`trading_config.json`](file:///c:/projects/Traiding/trading_config.json):

* **Google Gemini API Key:** Required for AI news gatekeeper analysis ([Google AI Studio](https://aistudio.google.com/)).
* **Zerodha Kite Connect:** Required only for Live Mode (`kite_api_key`, `kite_api_secret`).
* **Telegram Alerts:** Optional (`telegram_bot_token`, `telegram_chat_id`).

---

## 📁 Repository Structure

```
c:\projects\Traiding\
├── app.py                              # FastAPI backend, quant calculations, Gemini AI & Zerodha execution
├── start_dashboard.bat                 # 1-click Windows launcher
├── requirements.txt                    # Project dependencies
├── trading_config.json                 # Persistent configuration file
├── trading_state.json                  # Paper trading balance & open positions
├── trading_journal.json                # Closed trade logs
├── static/
│   └── index.html                      # Modern responsive UI (Tailwind CSS, dark mode)
├── README.md                           # Main project documentation (this file)
├── PROJECT_CONTEXT.md                  # Technical architecture spec for AI coding agents
├── UI_User_Guide.md                    # Detailed operations manual for end traders
└── AI-Powered Trading System Setup Guide.md # Comprehensive quantitative & broker setup guide
```

---

## 📖 Documentation Index

* **For Traders & Operators:** See [UI User Guide](file:///c:/projects/Traiding/UI_User_Guide.md) for step-by-step instructions on daily routines, reading signals, managing positions, and journaling.
* **For AI Agents & Developers:** See [Project Context](file:///c:/projects/Traiding/PROJECT_CONTEXT.md) for API routes, state lifecycle, and coding invariants.
* **For Quantitative & Institutional Setup:** See [Setup Guide](file:///c:/projects/Traiding/AI-Powered%20Trading%20System%20Setup%20Guide.md) for backtest logic, formulas, and Zerodha Kite Connect SDK integration.

---

## ⚠️ Disclaimer

*This software is intended for educational and systematic algorithmic trading purposes. Trading equities and derivatives carries financial risk. Always test thoroughly in Paper Trading mode before committing real capital.*
