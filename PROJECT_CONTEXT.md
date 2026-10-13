# Project Context and Developer Reference

This is the technical reference for developers and coding agents working on AetherKite. User-facing docs: [README.md](README.md) and [UI_User_Guide.md](UI_User_Guide.md). The known gaps and the roadmap are in [REVIEW.md](REVIEW.md).

## 1. Architecture (as built)

```
Browser (static/index.html)
  polls /api/status 5s, /api/positions 10s, /api/journal 15s, /api/scan 60s
  sends X-AetherKite-Token on every /api call (token injected into the page at "/")
        │
        ▼
FastAPI app (app.py), single process
  ├─ security middleware: Host allowlist, token check, CSP and security headers
  ├─ routes: status, scan, execute, close, kill switch, journal, settings, Kite OAuth
  ├─ TRADING_LOCK: serialises every load→modify→save and every broker order flow
  └─ engine thread (every 30 s): paper SL/TP, LIVE stop-fill detection and target, 15:10 square-off,
                                  stale-day reconciliation, auto-scan 20 s after each candle
        │
        ├─ aetherkite.marketdata  one batched yfinance download, cached 60 s; GETs never trigger per-symbol downloads
        ├─ aetherkite.strategy    closed-bar rule + Nifty regime (pure, on pandas)
        ├─ aetherkite.gatekeeper  Google News RSS + Gemini (timeout, cache per candle, fail-closed in LIVE)
        ├─ aetherkite.broker      Kite order flows (entry polling, stop placement, flatten, broker-truth exits)
        ├─ aetherkite.store       atomic JSON files (temp + fsync + os.replace, .bak, refuse to overwrite corrupt)
        └─ aetherkite.notify      Telegram queue (escaped, de-duplicated)
```

**Signal flow.**
1. A scan evaluates each symbol on its **closed** bars (`market.closed_bars`).
2. A BUY must be on the most recent closed candle (`market.is_latest_closed_bar`), pass the regime filter and have a non-zero size.
3. It is then sent to Gemini.
4. Approved signals are stored server-side in `SIGNALS[signal_id]`, where `signal_id = "SYMBOL|YYYYMMDDHHMM"`.

**Execution.** `POST /api/execute-trade {signal_id}` re-checks everything:
- halt state, pending live orders;
- signal freshness, entry window, AI approval (LIVE also requires `evaluated`);
- duplicates, max positions, daily loss limit;
- LIVE flag and Kite session;
- chase guard.

It then sizes the position from current equity at the current price. The client never supplies a price, size or stop.

## 2. Layout

```
app.py                     FastAPI routes, execution, engine, settings
aetherkite/
  clock.py                 now_ist() (naive IST), fixed clock for tests, timestamp format
  market.py                session rules, entry window, square-off, banded tick size, closed-bar helpers
  indicators.py            EMA / Wilder RMA / RSI / MACD / ATR (replaces pandas-ta)
  strategy.py              analyze() BUY rule, nifty_regime()
  marketdata.py            MarketData cache, yfinance batch fetch, ticker map (TATAMOTORS -> TMPV.NS)
  gatekeeper.py            fetch_news(), Gatekeeper.evaluate()
  broker.py                KiteBroker.enter_long / exit_long / flatten, BrokerError(critical)
  store.py                 JsonStore, StoreCorrupt
  notify.py                Notifier, send_telegram, esc
  costs.py                 round_trip_charges() (PLACEHOLDER rates)
static/index.html          single-page UI (Tailwind/Lucide from CDN)
tests/                     unittest suite with fakes for market data, Gemini, Kite (python -m unittest discover -s tests -t .)
start_dashboard.bat        launcher (uses .venv if present, no --reload)
trading_config.example.json
```

## 3. Runtime settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `AETHERKITE_ENABLE_LIVE` | unset | Must be `1` to place LIVE orders or switch to LIVE |
| `AETHERKITE_DATA_DIR` | app folder | Location of the JSON files and `logs/` |
| `AETHERKITE_ENGINE` | `1` | `0` disables the background engine (tests) |
| `AETHERKITE_ENGINE_INTERVAL` | `30` | Engine tick in seconds |
| `AETHERKITE_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host-header allowlist (blocks DNS rebinding) |
| `AETHERKITE_API_TOKEN` | random per launch | API token (fixed in tests) |
| `AETHERKITE_PORT` | `8000` | Only used to display the Kite redirect URL |

## 4. Data contracts

- **`trading_config.json`.** Keys and defaults are in `DEFAULT_CONFIG` in `app.py`; `trading_config.example.json` mirrors them.
  - Secret keys are never returned by any endpoint: `gemini_api_key`, `kite_api_key`, `kite_api_secret`, `kite_access_token`, `telegram_bot_token`.
  - `kite_token_date` records the IST date of the last successful login. The session counts as valid only on that date.
- **`trading_state.json`** contains:
  - `cash_balance`;
  - `positions` (one per symbol);
  - `order_history` (newest first, capped at 500);
  - `system_halted` and `halt_reason`;
  - `pending_live_orders`: write-ahead records of in-flight LIVE entries. If any exist at startup, the app halts;
  - `day`: `{date, realized_pnl, start_equity}`.

  Fields of each position:

  | Fields | Notes |
  |---|---|
  | `symbol`, `trade_id`, `mode` | `mode` is `PAPER` or `LIVE` |
  | `quantity`, `entry_price`, `current_price` | |
  | `stop_loss`, `take_profit`, `atr` | In LIVE, `stop_loss` is the trigger actually placed at Zerodha |
  | `margin_locked`, `unrealized_pnl` | |
  | `entry_time` | `YYYY-MM-DD HH:MM:SS`, IST |
  | `entry_order_id`, `sl_order_id`, `signal_id` | |
- **`trading_journal.json`.** A list, newest first, with OPEN and CLOSED entries.

  | Fields | Notes |
  |---|---|
  | `trade_id`, `symbol`, `direction`, `mode` | |
  | `quantity`, `entry_price`, `entry_time` | |
  | `stop_loss`, `take_profit`, `risk_inr`, `target_inr` | |
  | `signal_candle`, `quant_rationale` | |
  | `ai_rationale`, `ai_confidence` | `ai_confidence` is `null` when not evaluated |
  | `status` | `OPEN` or `CLOSED` |
  | `exit_price`, `exit_time`, `exit_reason`, `exit_price_estimated` | |
  | `gross_pnl`, `est_charges` | |
  | `realized_pnl`, `return_pct` | `realized_pnl` is net of estimated charges |
  | `notes` | |

## 5. API

Every `/api/*` route except `/api/kite-callback` and `/api/health` requires the `X-AetherKite-Token` header. Request bodies are validated with Pydantic, and NaN or Infinity is rejected.

| Endpoint | Method | Body / params | Notes |
|---|---|---|---|
| `/` | GET | | Dashboard with the token injected |
| `/api/health` | GET | | Store and engine health |
| `/api/status` | GET | | Session status, indices (vs previous close), regime, portfolio (equity = cash + margin + unrealised), engine heartbeat. Read-only. |
| `/api/scan` | GET | `refresh=true` to force | Cached results (recomputed if older than 60 s or when forced). Rows include `status`, `executable`, `block_reason` and `signal_id`. |
| `/api/execute-trade` | POST | `{signal_id}` | 404 unknown or expired, 410 stale, 409 rule violation, 403 LIVE disabled, 502 broker error |
| `/api/close-position` | POST | `{symbol, reason?}` | LIVE: broker exit first; on failure returns 502 and the position is kept |
| `/api/kill-switch` | POST | | Halts, then closes everything; returns `closed_orders` and `failed` |
| `/api/resume-system` | POST | `{clear_pending?: bool}` | 409 if unresolved live orders exist and `clear_pending` isn't set |
| `/api/positions` | GET | | Marked positions and the last 100 orders. Read-only. |
| `/api/journal` | GET | | Trades and analytics (`profit_factor` is `null` when there are no losses) |
| `/api/journal/note` | POST | `{trade_id, notes}` | |
| `/api/journal/export` | GET | | CSV (UTF-8 BOM, formula-neutralised) |
| `/api/settings` | GET / POST | `SettingsUpdate` model | Blank secrets keep their saved values; `clear_telegram` removes Telegram credentials. Mode and capital changes require no open positions; LIVE requires the flag. |
| `/api/telegram-test` | POST | `{telegram_bot_token?, telegram_chat_id?}` | Blank fields fall back to the saved values. Returns 502 if Telegram rejects the message. |
| `/api/kite-login` | GET | | `{login_url}` |
| `/api/kite-callback` | GET | `request_token`, `status` | Called by Zerodha's redirect; saves the token and today's date |

## 6. Invariants (do not break)

1. **Closed bars only.** Signals use `market.closed_bars(df, now)`, which keeps only bars where start + 15 min ≤ now. Never use the forming bar, and never rely on positional `iloc[-2]`.
2. **The server owns trade parameters.** Execute takes only a `signal_id`. Price, size, stop and target are computed on the server at execution time.
3. **One lock, atomic files.** Every load→modify→save of state, journal or config happens under `TRADING_LOCK` and goes through `JsonStore`. GET routes never write.
4. **Broker first, then local state (LIVE).** Local positions and cash change only after Zerodha confirms. Exits cancel the stop, wait for it to reach a final state, re-read net MIS quantity, and sell `min(net, tracked)`. Stop placement failure leads to an immediate flatten, and a failed flatten raises `BrokerError(critical=True)`, which halts the system.
5. **Exits are never blocked by a halt.** `system_halted` blocks entries only.
6. **Fail closed.** Missing regime data blocks entries. In LIVE, a missing or failed AI verdict blocks entries, as does an expired Kite session.
7. **Escape everything.** Every server value that enters `innerHTML` goes through `esc()`; Telegram text goes through `notify.esc`; CSV cells go through `_csv_safe`. Buttons use `data-*` attributes and delegated listeners, never inline JS built from data.
8. **Time.** Use `aetherkite.clock.now_ist()`; stored timestamps are naive IST strings.
9. **Tick sizes.** Round through `market.round_to_tick()`, which uses the banded tick floored at ₹0.05, so the result is valid under both the old and the revised NSE tables.
10. **Gemini is a veto only.** It never creates signals.

## 7. Known gaps

These are not fixed yet; see REVIEW.md for the roadmap.
- **No broker reconciliation.** Apart from the stop-fill check and stale-day cleanup, nothing compares local state with Zerodha. LIVE cash is a local estimate, not `kite.margins()`.
- **Target in LIVE is software-only.** It is watched by the 30 s engine, with no resting broker order.
- **SL-L can go unfilled.** A stop that triggers but doesn't fill on a fast gap has no watchdog that converts it to a market order.
- **Unofficial data, single-day tokens.** yfinance data is delayed and unofficial; there is no Kite WebSocket or instrument master. The daily Kite token needs a manual login.
- **No event calendar.** Results days, RBI and expiry are not handled. NSE holidays must be entered by hand.
- **Placeholder charges.** The rates in `costs.py` are unverified.
- **Unpinned frontend assets.** Tailwind and Lucide load from CDNs without SRI.
- **Strategy edge unproven.** Use the backtest harness on the `feat/backtest-harness` branch.
