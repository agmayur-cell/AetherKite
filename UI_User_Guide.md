# AetherKite Terminal — User Manual & Operations Guide
### A Non-Technical Business Guide to Operating Your Automated AI-Powered Trading System

---

## 1. Introduction & Overview

The **AetherKite Terminal** is a hybrid algorithmic trading dashboard designed specifically for the **National Stock Exchange of India (NSE)**. 

It combines two layers of decision-making:
1. **Mathematical Discipline (The Quant Engine):** Monitors closed 15-minute price charts for high-probability setups across the **Nifty 50** using trend filters (200 EMA), momentum crossovers (MACD), RSI health, and volatility (ATR).
2. **Artificial Intelligence Safeguard (The AI Gatekeeper):** Employs **Google Gemini 3.8 Flash** to read breaking news, corporate filings, regulatory updates, and earnings reports before any trade is executed. If adverse events are detected, the AI vetoes the trade to protect your capital.

As a business user, **you do not need to interact with any code**. You control the entire strategy, monitor real-time signals, manage positions, and review your performance through a clean web dashboard in your browser.

---

## 2. Quick Start: How to Launch the Dashboard

1. Open your project folder (`c:\projects\Traiding`).
2. Double-click **`start_dashboard.bat`**.
3. A terminal window will open in the background, and your web browser will automatically load:
   ```
   http://127.0.0.1:8000
   ```
4. **To close the dashboard:** Simply close the browser tab and close the black terminal window.

---

## 3. Daily Morning Routine (5-Minute Checklist)

| Time (IST) | Action | Purpose |
| :--- | :--- | :--- |
| **09:00 – 09:14 AM** | Launch `start_dashboard.bat`. | Boots the market data feeds and verifies system health. |
| **09:14 AM** | Click orange **"Connect Zerodha"** *(Live Mode only)*. | Authenticates your daily broker session via 1-click login. |
| **09:15 – 09:45 AM** | **Observational Period (No Trades).** | The system enforces a 30-minute morning blackout to avoid market open whipsaws and gap traps. |
| **09:45 AM – 15:15 PM** | **Active Scanning Window.** | Scan the radar, review AI evaluations, and execute qualified setups. |
| **15:15 PM** | **Intraday Square-off Cutoff.** | New entries are automatically blocked. All open positions must be closed before Zerodha RMS square-off at 15:20 PM. |

---

## 4. Visual Layout & Control Elements

```
+---------------------------------------------------------------------------------------------------------------+
| [Activity Icon] AETHERKITE TERMINAL       [PAPER MODE]               [Connect Zerodha] [Settings] [Scan Radar] [KILL SWITCH] |
+---------------------------------------------------------------------------------------------------------------+
|  Available Cash: ₹1,00,000.00  |  Today's P&L: +₹2,450.00  |  Risk Rule: 1.0% / Trade  |  System: 09:45 AM Guard Active   |
+---------------------------------------------------------------------------------------------------------------+
|  LIVE SIGNAL RADAR & AI GATEKEEPER                             [ Search Symbol... ] [ All 50 Stocks ▼ ]        |
|  Symbol    Price       200 EMA Trend  MACD Momentum  RSI   Quant Signal   AI Veto Status    Sizing (Qty / SL / TP) Action |
+---------------------------------------------------------------------------------------------------------------+
|  OPEN POSITIONS (MIS)                                          | TRADE AUDIT LOG                              |
+---------------------------------------------------------------------------------------------------------------+
|  QUANTITATIVE & PSYCHOLOGICAL TRADING JOURNAL                  [ Export CSV ] [ Refresh Journal ]             |
+---------------------------------------------------------------------------------------------------------------+
```

### **A. Top Navigation Bar**
* **Trading Mode Badge:** Displays either `PAPER MODE` (safe green badge) or `LIVE MODE` (red badge).
* **Gemini Status Pill:** Displays `● Gemini 3.8 Flash` in green when your Google AI API key is active.
* **Market Status Pill:** Displays `● Market Open` during NSE hours (09:15–15:30 IST) or `● Market Closed`.
* **Connect Zerodha (Orange Button):** Used once each morning for 1-click broker login.
* **Settings (Dark Button):** Opens the configuration drawer to adjust capital, risk, and keys.
* **Scan Radar (Blue Button):** Triggers a multi-threaded scan across all 50 Nifty stocks in 2–3 seconds.
* **KILL SWITCH (Red Button):** Emergency panic button to immediately flatten all open positions and halt trading.

### **B. KPI Summary Cards**
1. **Available Cash:** The liquid cash currently uncommitted and available to fund new trade entries.
2. **Today's Live P&L:** Real-time unrealized profit/loss across all active open positions (green if positive, red if negative).
3. **Risk Rule:** The percentage of capital risked per individual trade (default is 1.0%).
4. **System Guard:** Real-time status of intraday timing guards (09:45 AM morning filter and 15:15 PM cutoff).

---

## 5. How to Use the Live Signal Radar

The **Live Signal Radar** evaluates your watchlist (the entire Nifty 50) and presents actionable setups.

### **Step 1: Run the Scan**
Click the blue **"Scan Radar"** button. The icon will spin briefly, scanning all 50 stocks in parallel.

### **Step 2: Read the Columns**
* **Symbol & Price:** The stock ticker (e.g., `RELIANCE`) and its last traded price.
* **200 EMA Trend:** 
  * `BULLISH (>200 EMA)`: Price is trading above its long-term baseline.
  * `BEARISH`: Price is below its baseline (long trades are disqualified).
* **MACD Momentum:** Displays `CROSSOVER UP` when shorter-term momentum crosses above the signal line on a closed 15-minute candle.
* **RSI (14):** Relative Strength Index. The strategy requires RSI to be between **40 and 65** (healthy upward momentum without being overbought).
* **Quant Signal:** Displays `BUY TRIGGER` when all three technical criteria match.
* **AI Veto Status (Gemini 3.8 Flash):**
  * `APPROVED (XX%)`: Gemini checked breaking news and corporate disclosures and found no disqualifying red flags. Hover your mouse over the text to read Gemini's 1-2 sentence rationale.
  * `VETOED (HIGH/CRITICAL)`: Gemini detected a breaking negative event (earnings surprise, management resignation, regulatory investigation) and blocked the trade.
* **Risk & Sizing:** Displays the exact calculated share quantity, Stop-Loss price in ₹, and Take-Profit target in ₹.

### **Step 3: Filter & Search**
* **Quick Search:** Type any stock symbol (e.g., `TCS` or `INFY`) in the search box to find it instantly.
* **Only Buy Triggers:** Click the dropdown and select **`🎯 Only Buy Triggers`**. This hides neutral stocks and isolates candidate trades.

### **Step 4: Execute a Trade**
When a stock displays `BUY TRIGGER` and `APPROVED`, a green **"Execute Buy"** button appears.
1. Click **"Execute Buy"**.
2. A confirmation dialog will pop up showing:
   * Stock symbol
   * Number of shares
   * Entry price
   * Stop-Loss & Take-Profit levels
3. Click **OK**. The trade will execute and immediately appear in your **Open Positions** card and **Trading Journal**.

---

## 6. Managing Active Positions

Once an order is executed, it moves into the **Open Positions (MIS)** card:

* **Position Details:** Shows ticker, quantity of shares held, entry price, Stop-Loss price, and Target price.
* **Live P&L:** Updates in real-time as prices move.
* **Discretionary Exit Button:** Next to each position is a dark-red **"Exit"** button. 
  * If you wish to take profits early or exit ahead of a major news event, click **"Exit"**.
  * The position will be closed immediately, and the final realized profit or loss will be recorded in your journal.

---

## 7. The Quantitative & Psychological Trading Journal

Located at the bottom of your screen, the **Trading Journal** provides an automated institutional record of your trading performance.

### **Performance Ribbon**
* **Total Closed Trades:** Number of completed trades in the session/history.
* **Win Rate (%):** Percentage of winning trades (aim for > 50%).
* **Profit Factor:** Ratio of gross profits to gross losses (a healthy strategy is > 1.5).
* **Net Realized P&L:** Total cash made or lost on finalized trades.
* **Avg Win vs. Avg Loss:** Verifies that your winners are larger than your losers.

### **The Journal Table**
Every trade automatically logs:
1. **Trade ID & Symbol:** Unique order identifier.
2. **Entry Time & Price:** Exactly when and at what price you entered.
3. **Exit & Reason:** Exit price, timestamp, and reason (`MANUAL_EXIT`, `TARGET_HIT`, `STOP_LOSS_HIT`, or `KILL_SWITCH`).
4. **Quant & AI Rationale:** The exact technical setup and Gemini AI rationale that justified taking the trade.
5. **Realized P&L:** Net Rupee profit/loss with a `PROFIT` (green) or `LOSS` (red) badge.
6. **Trader Reflection / Notes:** An editable text box where you can type your observations (e.g., *"Good breakout on volume"*, *"Exited early due to resistance at ₹3,000"*). Your notes auto-save instantly.

### **Exporting to Excel / CSV**
Click the **"Export CSV"** button in the journal header. Your browser will download `trading_journal.csv`, ready to open in Microsoft Excel or Google Sheets.

---

## 8. Configuration & Settings

Click the **"Settings"** button in the top navigation bar to open the settings modal:

* **Trading Mode:**
  * `Paper Trading (Safe Simulation)`: Uses real live market prices with virtual money. Recommended for your first 30 days.
  * `Live Trading (Zerodha Kite)`: Routes real orders through your Zerodha trading account.
* **Capital (₹):** Set your virtual or live capital pool (e.g., `100000`).
* **Risk per Trade (%):** Recommended between `0.5%` and `1.5%`. Position sizing will adjust automatically so that hitting a stop loss never exceeds this percentage of your capital.
* **Google Gemini API Key:** Powers the AI news analysis (saved in `trading_config.json`).
* **Kite API Key & Secret:** Configured for your Zerodha app.
* **Telegram Trade Alerts (Mobile / Desktop):**
  * Toggle the switch **ON** to receive instant smartphone notifications for approved BUY triggers.
  * Enter your **Bot Token** (from `@BotFather`) and **Chat ID**.
  * Click **"Test Alert Ping"** to verify that alerts reach your phone.
* **Watchlist:** Comma-separated list of symbols (pre-loaded with all 50 Nifty leaders).

---

## 9. Hands-Free Automation & Audio Alerts

You **do not need to stare at the screen or check every 15 minutes**:
1. **Next Candle Countdown Timer:** Located in the market ticker bar (`Next Candle Close: 08:42`). It synchronizes with official 15-minute exchange bars and automatically triggers a fresh radar scan at `:00`, `:15`, `:30`, and `:45` of every hour.
2. **Audio Chime Toggle:** Click the **"Sound Alerts: Off"** pill in the ticker bar to switch it to **"Sound Alerts: ON"**. Whenever a new BUY setup is confirmed by Gemini AI, your computer plays an institutional 3-tone chime.
3. **Telegram Smartphone Alerts:** When enabled, the backend dispatches a rich message to your phone with the symbol, price, stop-loss, target, and Gemini reasoning so you can step away from your desk completely.

---

## 9. Emergency Safety Protocol (The Kill Switch)

In high-volatility events, black swan macro news, or sudden connection issues, click the red **"KILL SWITCH"** button in the top-right corner.

### **What Happens When Pressed:**
1. A red alert banner appears across the dashboard: **`EMERGENCY KILL SWITCH ACTIVATED`**.
2. All open positions are **immediately closed (flattened)** at current market prices.
3. All pending orders are canceled.
4. New trade evaluations are completely **halted**.
5. Each liquidated trade is logged into your journal with the reason `EMERGENCY_KILL_SWITCH`.

### **How to Resume Trading:**
Once market conditions normalize, click the red **"Resume Engine"** button on the alert banner to re-enable automated scanning.

---

## 10. Best Practices for Business Success

1. **Trade the Trend, Not Opinions:** If a stock is below its 200 EMA, do not attempt to buy the dip. The algorithm is built for momentum.
2. **Respect the AI Veto:** If Gemini flags a stock as `VETOED` due to management instability or regulatory scrutiny, do not override it. Protecting capital is more important than taking every trade.
3. **Paper Trade First:** Run the system in Paper Mode for **30 trading sessions**. Verify that your Win Rate is above 50% and your Profit Factor is above 1.5 before allocating real capital.
4. **Never Disable the 15:15 Cutoff:** Holding intraday MIS positions past 15:20 IST incurs Zerodha RMS penalty fees. Let the system square off before the close.
