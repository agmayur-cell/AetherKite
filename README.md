# AetherKite Terminal

A local signal dashboard and paper-trading journal for **NSE intraday longs**, with optional live order routing through **Zerodha Kite Connect**.

> **Status: LIVE trading is experimental and disabled by default.**
>
> - The strategy has **no demonstrated edge**. A no-edge simulation shows its 1:2 stop/target geometry losing about 0.2R per trade after Indian intraday costs.
> - Validate it with the backtest harness (branch `feat/backtest-harness`) before risking money.
> - Read [REVIEW.md](REVIEW.md) for the full audit and the list of remaining gaps.

## What it does

- **Signals.** On every closed 15-minute candle, the server checks each watchlist symbol for a fresh BUY setup:
  - price above its 200-bar EMA;
  - a bullish MACD(12,26,9) cross;
  - RSI(14) between 40 and 65.

  The stop is 1.5×ATR(14) and the target 3×ATR(14).
- **Nifty regime filter.** Longs are allowed only while Nifty's last closed bar is above both its 20-bar EMA and the previous session's close. If Nifty data is missing, entries are blocked.
- **AI news veto.** Gemini reads up to 5 Google News headlines per signal and can veto it.
  - It needs a confidence of at least 70% to approve.
  - Without a Gemini key, PAPER signals are marked *not evaluated*, and LIVE entries are blocked.
- **Execution is always a manual click.** The server re-prices at the current market price and re-checks every rule before ordering:
  - entry window 09:45–14:00;
  - no duplicate symbols;
  - at most 3 open positions;
  - a daily loss halt;
  - a chase guard;
  - an approved AI verdict.
- **Exits run without the browser.** A background engine handles:
  - paper stop/target hits;
  - broker stop fills in LIVE;
  - the take-profit in LIVE (monitored by software; there is no broker order for it);
  - automatic square-off at 15:10;
  - reconciling leftover positions from earlier days.
- **Journal.** Every trade is logged with its rationale and net P&L after *estimated* charges, plus your notes. It exports to CSV.
- **Kill switch.** Halts new entries and closes every position. LIVE positions are closed at Zerodha, and failures are reported.

### PAPER vs LIVE

| | PAPER | LIVE |
|---|---|---|
| Entry fill | Last price from Yahoo (about 15 minutes delayed) | Zerodha MARKET order with automatic market protection, polled until filled |
| Stop-loss | Simulated on 15m bars by the engine | SL-L order at Zerodha, 0.3% limit buffer; if it can't be placed, the shares are sold again immediately |
| Target | Simulated by the engine | Watched by the engine (no resting order at Zerodha) |
| Exit button / kill switch | Local only | Cancels the stop, re-reads your Zerodha position and sells exactly that quantity |
| Cash | Local simulation | Still a local estimate; it is **not** synced with Zerodha funds |

LIVE needs all of the following:
- the environment variable `AETHERKITE_ENABLE_LIVE=1`;
- no open positions when you switch modes;
- today's Kite session (click **Connect Zerodha** each trading morning);
- a Gemini key.

## Quick start (Windows)

```bat
git clone <this repo> AetherKite
cd AetherKite
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy trading_config.example.json trading_config.json
start_dashboard.bat
```

The launcher uses `.venv` if it exists and opens `http://127.0.0.1:8000` once the server is up. Stop it by closing the console window or pressing Ctrl+C.

**Requirements**
- Python 3.11 or newer (tested on 3.13).
- An internet connection: Yahoo Finance, Google News, Gemini, and the Tailwind/Lucide CDNs.
- A correct system clock.

## Configuration

Edit settings in the dashboard (**Settings**) or in `trading_config.json`. Each value and its default is in `DEFAULT_CONFIG` in `app.py`.

| Key | Default | Meaning |
|---|---|---|
| `starting_capital` | 100000 | Paper capital. Changing it resets cash, and is only allowed while flat. |
| `risk_per_trade_pct` | 0.005 | Fraction of equity risked per trade (0.5%) |
| `max_open_positions` | 3 | |
| `daily_loss_limit_pct` | 0.015 | No new entries after losing 1.5% of the day's starting equity |
| `max_notional_x` | 1.0 | One trade's notional is at most 1× equity |
| `max_chase_atr` | 0.3 | Reject the order if price moved more than 0.3 ATR from the signal close |
| `entry_start_time` / `entry_cutoff_time` | 09:45 / 14:00 | Entry window. `morning_buffer_enabled: false` allows entries from 09:15. |
| `intraday_cutoff_time` | 15:10 | Automatic square-off time |
| `nifty_regime_filter` | true | |
| `market_holidays` | [] | NSE trading holidays as `"YYYY-MM-DD"` strings. **You must fill these in** from NSE's published holiday list. |
| `gemini_model` | gemini-3.8-flash | Change this if Google retires the model |

### Credentials

- **Gemini.** Create a key in [Google AI Studio](https://aistudio.google.com/).
- **Zerodha Kite Connect.**
  1. Create an app at the Kite developer console.
  2. Set its **Redirect URL** to exactly `http://127.0.0.1:8000/api/kite-callback`.
  3. Enter the API key and secret in Settings.

  Kite access tokens are valid for one trading day, so log in each morning. The **Kite** pill in the header shows whether today's session is active.
- **Telegram.**
  1. Create a bot with @BotFather to get the token.
  2. Send your bot a message, then read your chat ID from `https://api.telegram.org/bot<token>/getUpdates`.

  Alerts are sent once per signal, are suppressed while halted, and broker problems are always sent.

**Security.**
- Secrets are stored in plain text in `trading_config.json`, which git ignores. Keep the folder out of cloud-synced directories.
- The dashboard only accepts requests from `127.0.0.1`/`localhost` that carry a per-launch token, and the API never returns secrets.
- Never expose it on `0.0.0.0` or a public network.

## Data files

The app writes these next to `app.py` (or in `AETHERKITE_DATA_DIR`):
- `trading_state.json`: cash and open positions;
- `trading_journal.json`: the trade journal;
- `logs/aetherkite.log`: the application log.

Writes are atomic, and the previous copy is kept as `*.bak`. If a file is unreadable, the app reports it and refuses to overwrite it; restore the `.bak` file, or fix the file by hand.

To reset the paper account, stop the app, delete `trading_state.json` and `trading_journal.json`, then start it again.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Rows show `DATA UNAVAILABLE` | Yahoo has no data for that symbol (renamed or delisted?). TATAMOTORS now trades as `TMPV`. |
| `AI not evaluated` / `VETOED (CRITICAL) Gemini request failed` | Missing or invalid Gemini key, retired model ID, or a network problem |
| `Nifty regime: no data (blocked)` | Index data couldn't be fetched; entries are blocked until it returns |
| "Kite session missing or expired" | Click **Connect Zerodha** (once per trading day) |
| "Missing or invalid API token" | The server was restarted; reload the page |
| Banner says a `.json` file could not be read | See **Data files** above |

## Development

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m unittest discover -s tests -t .
```

`app.py` holds the FastAPI routes, execution and the engine. The testable logic lives in `aetherkite/`:

| Module | Contents |
|---|---|
| `strategy.py` | Signal rules |
| `indicators.py` | Technical indicators |
| `market.py` | Session rules and tick sizes |
| `broker.py` | Kite order flows |
| `gatekeeper.py` | Gemini veto |
| `marketdata.py` | Batched Yahoo cache |
| `store.py` | Atomic JSON storage |
| `notify.py` | Telegram alerts |
| `costs.py` | Estimated charges |

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for the API and invariants.

## Documentation

- [UI_User_Guide.md](UI_User_Guide.md): daily use of the dashboard.
- [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md): architecture, API, data schemas, invariants.
- [REVIEW.md](REVIEW.md): expert review, known issues and roadmap.
- [AI-Powered Trading System Setup Guide.md](AI-Powered%20Trading%20System%20Setup%20Guide.md): original design notes. They are a design sketch, not the running code.

## Disclaimer

This software is provided as-is, with no warranty, for educational use. It is not investment advice, and the authors are not SEBI-registered advisers or research analysts. Intraday trading can lose more than you expect: stops can gap, orders can be rejected, and data can be delayed or wrong. You are solely responsible for any trades placed with it. Check your positions in the Kite app after every action; the Kite app is your real kill switch.
