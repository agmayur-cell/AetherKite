# AetherKite Terminal: User Guide

This guide covers daily use of the dashboard. For installation, credentials and troubleshooting, see the [README](README.md). Read [REVIEW.md](REVIEW.md) before considering LIVE mode.

---

## 1. What the system does, and doesn't do

- **Every closed 15-minute candle, the server scans your watchlist** for a technical BUY setup. Each setup goes through two more checks:
  - **the Nifty regime filter**: longs only while Nifty is above its 20-bar EMA and the previous close;
  - **a Gemini check** of up to 5 Google News headlines.
- **Entries are always your decision.** Nothing is bought unless you click **Execute Buy**.
- **Exits are managed by the server**, even with the browser closed, as long as the server is running:
  - stop-loss and target;
  - automatic square-off at 15:10;
  - cleanup of positions left over from earlier days.
- **Prices come from Yahoo Finance and are delayed (about 15 minutes).** Paper results are therefore approximate.
- **The strategy has no demonstrated edge.** Paper results over a few weeks prove very little. Use the backtest harness before trusting it.

## 2. Starting and stopping

1. Double-click **`start_dashboard.bat`**. The browser opens `http://127.0.0.1:8000` after a few seconds.
2. Keep the console window open while you trade. Closing it stops the server, and with it the stop/target management.
3. Keep the computer awake during market hours. A sleeping laptop manages nothing.

If the page says "Missing or invalid API token", the server was restarted; reload the page.

## 3. Daily routine (IST)

| Time | What happens | What you do |
|---|---|---|
| Before 09:15 | — | Start the app. In LIVE, click **Connect Zerodha** (the Kite token is valid for one trading day). Check that the **Kite** pill says *session OK*. |
| 09:15–09:45 | Opening buffer: no entries | Watch. Signals are shown but can't be executed. |
| 09:45–14:00 | Entry window | Review executable signals and click **Execute Buy** if you agree. |
| 14:00–15:10 | No new entries | Open positions keep being managed. |
| 15:10 | Automatic square-off | The engine closes every open position. |
| After 15:30 | — | Review the journal and add notes. |

On weekends and on dates listed in `market_holidays`, entries are blocked. You must add NSE holidays to the config yourself.

## 4. Screen layout

### Header
- **Mode badge:** shows one of:
  - `PAPER MODE`;
  - `LIVE MODE`;
  - `LIVE (ORDERS DISABLED)`, when the config says LIVE but the app was started without `AETHERKITE_ENABLE_LIVE=1`.
- **Gemini pill:** green when a key is saved. A saved key can still be invalid; check that signals show `APPROVED` or `VETOED`, not `NOT EVALUATED`.
- **Market pill:** open or closed.
- **Kite pill:** *session OK*, *login needed* or *not set up*.
- **Buttons:**
  - **Connect Zerodha**;
  - **Settings**;
  - **Scan Radar**, which forces a scan now (the server also scans after every candle);
  - **KILL SWITCH**.

### Ticker bar
- **Sensex and Nifty:** change against the previous session's close, delayed. "Data unavailable" means the feed failed.
- **Next Candle Close:** a countdown using the server's IST clock.
- **Sound Alerts:** plays a chime when a new executable signal appears while the page is open.
- **Regime badge:** whether the Nifty filter currently allows longs. Hover over it for the numbers.

### Summary tiles
- **Available Cash:** cash not locked as margin.
- **Open P&L:** unrealised P&L of open positions, plus today's realised P&L and total equity.
- **Risk Rule:** percent of equity risked per trade.
- **System Guard:** whether entries are allowed right now and, if not, why. It also shows the square-off time and the engine heartbeat.

A **red banner** means trading is halted (kill switch, or an unresolved broker problem). An **amber banner** means the server or a data file has a problem.

## 5. The signal radar

| Column | Meaning |
|---|---|
| Price | Close of the last *closed* 15-minute candle (hover to see which one) |
| 200 EMA Trend | Above or below the 200-bar EMA. 200 bars of 15 minutes is about 8 sessions. |
| MACD Momentum | `CROSSOVER UP` if the MACD line crossed above its signal line on that candle |
| RSI (14) | Must be between 40 and 65 |
| Quant Signal | `BUY TRIGGER` when all three conditions hold |
| AI Veto Status | `APPROVED nn%`, `VETOED (level)`, or `NOT EVALUATED` (no key, feed down or API error). Hover to read the reasoning. |
| Risk & Sizing | Suggested shares, stop and target at the signal price |
| Action | **Execute Buy** when everything passes. Otherwise it shows the reason, such as *AI veto*, *Nifty regime filter*, *stale*, or *No new entries after 14:00*. |

Rows marked `DATA UNAVAILABLE` or `INSUFFICIENT DATA` couldn't be evaluated; they are not neutral signals.

### Executing
1. Click **Execute Buy**. The confirmation says whether it is a **PAPER** trade or a **LIVE order with real money**.
2. The server then:
   - re-prices at the current market price;
   - re-sizes the position;
   - refuses the order if the price has moved more than 0.3 ATR from the signal close;
   - checks every risk limit again.

   The fill can therefore differ slightly from the preview.
3. In LIVE, the protective stop is placed at Zerodha right after the fill. If the stop can't be placed, the shares are sold again at once, and you are told.

## 6. Open positions

- **What each position shows:** quantity, mode (PAPER/LIVE), entry, current price, stop, target and unrealised P&L.
- **What Exit does:**
  - **PAPER:** closes at the latest price.
  - **LIVE:** cancels the Zerodha stop, re-reads your actual Zerodha position and sells exactly that quantity. If the stop has already fired, nothing more is sold. If Zerodha rejects the exit, the position stays open in the app and you are alerted.
- **Exit reasons** in the journal:
  - `MANUAL_EXIT`, `STOP_LOSS_HIT`, `TARGET_HIT`, `EOD_SQUARE_OFF`, `EMERGENCY_KILL_SWITCH`;
  - `STALE_SESSION_CLOSE` / `STALE_SESSION_RECONCILE` for positions found from an earlier day;
  - `..._BROKER_ALREADY_FLAT` when Zerodha had already closed the position. In that case the exit price is an estimate and is marked "(est.)".

## 7. Journal

- **Ribbon:**
  - closed trades;
  - win rate;
  - profit factor ("∞" when there are no losses yet);
  - net realised P&L after **estimated** charges (the charge rates are placeholders; see `aetherkite/costs.py`);
  - average win and average loss.
- **Judging the strategy:** use average R and profit factor over many trades, not win rate. With a 2:1 target, break-even is about a 33% win rate.
- **Notes:** saved when you press Enter or click away. The table doesn't refresh while you are typing.
- **Export CSV:** downloads every trade, including the AI rationale and estimated charges.

## 8. Settings

- **Trading Mode and Starting Capital:** can only change while no positions are open. Changing the capital resets paper cash.
- **Blank key fields keep the saved key.** This applies to the Gemini, Kite and Telegram fields. Use **Remove** to delete the Telegram credentials.
- **Kite Redirect URL:** shown in the dialog. It must match your Kite developer app exactly.
- **Watchlist:** comma-separated NSE symbols. Duplicates are removed and symbols are upper-cased.
- **Other risk settings** live in `trading_config.json`; see the README: max positions, daily loss limit, entry window, square-off time and holidays.

## 9. Kill switch

Pressing **KILL SWITCH**:
1. Halts new entries immediately.
2. Closes every position. LIVE positions are closed at Zerodha the same way as **Exit**.
3. Lists any position it **could not** close, and sends a Telegram alert. Close those in the Kite app.

Scanning continues while halted, but Telegram signal alerts stop. Click **Resume Trading** on the banner to allow entries again. If a live order was left unresolved by a crash, you'll be asked to confirm that you've checked it in Kite first.

## 10. Good practice

1. **Backtest before you trust it.** Then paper trade for weeks, not days, and compare results with the backtest.
2. **Treat the Kite app as your source of truth.** After any LIVE action, check positions and orders there.
3. **Keep risk small:** 0.25–0.5% per trade, and a few positions at most.
4. **Use the AI veto as a filter, not a guarantee.** It only reads five headlines.
