# AetherKite Terminal: Expert Review

*Review date: 2026-10-13 · Commit reviewed: `f52f8d5` · Reviewers: Indian market trader / risk manager, technical architect, line-level code reviewer (all read-only)*

*Verification pass: 2026-10-13. Every finding was re-checked line by line against `app.py` and `static/index.html`, and the numeric claims were reproduced in Python. Findings marked **[corrected]** were wrong or overstated and have been fixed in place. Findings marked **[external]** depend on facts outside the repo (NSE/SEBI/Zerodha rules, library internals) that couldn't be checked here. New issues found during verification are listed under "Added in verification".*

---

## Verdict

All three reviewers reached the same conclusion. The app works as a **signal dashboard and paper-trading tool**. **It is not safe for real money yet.** The LIVE path can leave you with an untracked position that has no stop-loss, or with a short you never meant to take. Several safety features the docs describe are not in the code.

---

## Consolidated findings

### Critical: can lose real money

| # | Issue | Location | Failure scenario |
|---|---|---|---|
| 1 | **Close doesn't cancel the broker SL** | `app.py:777-800` | You exit at 10:30. Later the price hits the old trigger, the SL fires, and you are **short** on MIS. If the SL already fired at Zerodha, Exit sells a second time, which is also a short. The `sl_order_id` is returned in the API response but **never saved** on the position (`app.py:705, 712-722`), so no later code could cancel it. |
| 2 | **Kill switch never contacts the broker** | `app.py:835-881` | In LIVE it only clears local JSON. Real positions and SL orders stay open at Zerodha. |
| 3 | **Kill switch inflates cash** | `app.py:844-845` | It adds back the full notional, but only margin was deducted at entry. Example: ₹1L becomes ₹1.75L of phantom capital, and later sizing uses it. |
| 4 | **Naked or untracked entry** | `app.py:679-708` | The single 0.8s fill check means a late fill is never tracked. If SL placement fails, the filled buy has no stop and isn't recorded. The HTTPException(400) also gets re-wrapped as a 500. |
| 5 | **Close is booked locally even if the broker fails** | `app.py:777, 786, 800` | The position is popped before the broker call. If the exit is rejected, the UI shows flat while you are still long. If LIVE credentials are missing, the broker is skipped silently and the close is still booked. |
| 6 | **Lost updates and state corruption** | `app.py:85-106, 443-465` | The 5s status poll and the 10s positions poll each do load → slow yfinance download → save of a stale copy, which erases concurrent trades. This needs at least one open position, because only then does the poll save. The lock prevents torn reads inside the process, but writes are not atomic, so a crash or restart mid-write truncates the file. A corrupt file silently resets to ₹1,00,000 with no positions, ignoring the configured capital, and the next save overwrites the evidence. |
| 7 | **No auth, CSRF on some endpoints, secrets returned by the API** **[corrected]** | all endpoints, `app.py:991` | Any web page you visit can POST to `/api/kill-switch` and `/api/resume-system`, which take no body, and can GET `/api/scan`, which spends Gemini quota and sends Telegram alerts. The endpoints that take a `dict` body (execute-trade, settings, close-position) are *not* reachable this way: FastAPI ≥0.65.2 won't parse a non-JSON body, and a cross-origin JSON request needs a CORS preflight that the app doesn't answer. There is no Host-header check, though, so a DNS-rebinding page can reach every endpoint and read responses. `/api/settings` echoes every secret, including the Kite secret, access token and Telegram token. |
| 8 | **Paper→Live switch with open paper positions** | `app.py:783` | Exit sends a **real** SELL for a position you never bought, which creates a short. |
| 9 | **TATAMOTORS orders get rejected** **[external]** | `app.py:278-282, 670` | Data comes from `TMPV.NS`, but the order goes out as `TATAMOTORS`, which no longer exists after the demerger. The mismatch is confirmed in code, and `TATAMOTORS` is in both the default and example watchlists. That the NSE symbol is retired is an outside fact. |
| 10 | **SEBI retail-algo / Kite compliance gaps** **[external]** | order placement | No `market_protection` on MARKET orders, no order `tag`, no static IP, no rate throttle. The code is confirmed to lack all four; the regulatory requirements themselves are outside facts. |

### High
- **Safeguards in the docs don't exist.** There is no 09:45 entry block, no 15:15 auto square-off, no SL/TP monitoring, and no Nifty regime filter. `can_trade` is computed but never checked. `morning_buffer_enabled`, `intraday_cutoff_time` and `nifty_regime_filter` are never read. Zerodha's RMS square-off charges ₹50 + GST per position. The "System Guard" tile in the UI is a hard-coded "09:45 AM Filter Active" label that nothing ever updates (`index.html:177`), so it always claims protection that doesn't exist.
- **`execute_trade` trusts the client payload** (`app.py:621-647`).
  - It does not re-check the AI veto, market hours, types or NaN. Reproduced: `{"shares": NaN}` passes both the `<= 0` check and the margin check, because every comparison with NaN is false. A string `shares` raises a TypeError, which surfaces as a 500.
  - It does not check for an existing position in the same symbol. A second buy overwrites the first, permanently leaking its margin and leaving an orphan OPEN journal entry.
- **The AI gate fails open.**
  - With no Gemini key or no library, it approves at 85% (`app.py:357-364`).
  - `fetch_company_news` returns `None` on error (`app.py:139`), and Gemini is then told "no negative alerts". The same happens when the search finds no headlines.
  - Partial mitigation: the UI labels this state "Gemini (Simulated)" (`index.html:462`), and Gemini *API errors* do fail closed (`app.py:394-400`).
- **The settings modal corrupts data.**
  - Every save clears the Telegram token and chat ID (`index.html:974, 1026-1027`; `app.py:979`).
  - **[corrected]** The "Capital" field is pre-filled with the current **cash balance**, not `starting_capital` (`index.html:967`). Each save copies that value into both `starting_capital` and cash (`app.py:983-988`). The result is that `starting_capital` silently drifts to current cash. Cash only changes if you edit the field, or if a trade happened while the modal was open, in which case a stale value overwrites it. Editing it with open positions breaks the margin accounting.
  - Settings accepts any key that already exists in the config, with no type or range checks. For example, `max_portfolio_leverage: 0` makes every trade fail with a ZeroDivisionError, and `trading_mode: "live"` in lower case is quietly treated as paper.
- **Execution microstructure:**
  - The ₹0.50 SL-limit buffer is fixed regardless of price (`app.py:692`), so it is negligible on MARUTI or BAJAJ-AUTO.
  - The tick size is hard-coded to ₹0.05, but NSE uses price-banded ticks. The hard-coding is confirmed; the current NSE tick table is **[external]**.
  - The take-profit is never placed at the broker.
  - In LIVE, the SL is shifted to the actual fill price but the take-profit is not. The position also stores the client's `stop_loss`, not the `sl_trigger` actually sent to Zerodha (`app.py:691, 717`), so the UI shows the wrong stop.
- **Margin and risk limits:**
  - Leverage is a flat 4x with no per-stock MIS margin.
  - **[corrected]** The size the scan suggests is not capped by available margin. `execute_trade` does reject the order when margin exceeds cash, so the outcome is a rejected trade, not an oversized one. Risk is sized on cash rather than equity, so each open position shrinks the next trade's risk budget.
  - There is no max-positions limit, daily loss limit, sector/correlation cap, or F&O-ban/circuit check.
- **Data:**
  - yfinance is delayed and unofficial.
  - Concurrent `yf.download` calls can mix tickers. This is plausible, because yfinance keeps results in module-level dicts, but it couldn't be reproduced here. **[external]**
  - **[corrected]** The auto-scan fires at **:14:59, :29:59, :44:59 and :59:59** by the browser's clock, one second *before* each candle closes (`index.html:940-956`; reproduced). At that moment `iloc[-2]` is the bar that closed about 15 minutes earlier, so every auto-scan is a full bar late. The exact-second check also needs a foreground tab, because browsers throttle background timers and can skip that second entirely.
  - Entry uses the candle close, not the LTP.

### Medium
- P&L ignores STT, stamp duty, GST, exchange/SEBI fees and slippage. These can be 10–20% of risk per trade. No backtest exists.
- XSS:
  - Gemini reasoning, notes, rationale and symbol are inserted via `innerHTML` (`index.html:607, 612, 629, 635, 835, 842`).
  - `kite_callback` reflects `str(e)` (`app.py:441`).
  - Telegram `parse_mode=HTML` is unescaped, so `M&M` or `<` makes alerts drop silently.
- The news URL isn't URL-encoded (`app.py:126`). `M&M` searches for "M".
- `total_equity` is cash + full notional (`app.py:469-471`). **[corrected wording]** The overstatement is notional − margin − unrealised P&L, which is 75% of notional at 4x. It is not a multiple of equity.
- Index "change" is measured against yesterday's 09:15 open, not the previous close (`app.py:221, 239`). Hardcoded fallback prices look live.
- Each 5s/10s poll downloads 30 days of 15m bars per position, which invites Yahoo throttling.
- The journal table re-renders every 15s, wiping any note being typed (`index.html:842, 1066`).
- `pandas-ta` 0.3.14b0 imports `numpy.NaN`, which numpy 2.x removed. Whether pip actually resolves to that version depends on the environment; it couldn't be tested here because no dependencies are installed. **[external]** Requirements are unpinned. Tailwind (the play CDN) and Lucide (`@latest`) come from CDNs with no SRI. **[corrected]** `--reload` in the launcher restarts the server whenever a `.py` file changes. That is uvicorn's default watch pattern, so it is not triggered by every file save, and the JSON state writes don't trigger it.

### Low
- Leverage defaults are inconsistent (4.5 in code vs 4.0 in config, `app.py:640, 804`). Both fallbacks are effectively dead code: the `DEFAULT_CONFIG` merge always supplies the key, and `margin_locked` is always stored.
- `int(time.time())` order IDs collide for two paper trades in the same second (`app.py:649`). The exit and kill IDs include the symbol. On a collision, journal notes are saved to the wrong trade.
- `profit_factor` returns gross profit when there are no losses (`app.py:897`).
- Scan exceptions are swallowed silently (`app.py:569-572`).
- Telegram alerts are re-sent on every scan and from every open tab. There is no de-duplication.
- **[corrected]** An empty risk field saves `null`. Every scan row then raises a TypeError that is swallowed, so the radar shows "No data returned". An empty capital field does *not* save `null`: `float(None)` raises before `save_config`, so the whole save fails with a 500 and nothing is stored.
- Position P&L is always styled green (`index.html:716`).
- There is no NSE holiday calendar.
- `round_to_tick` breaks exact half-tick ties inconsistently because of float error: `99.975→99.95` but `100.025→100.05` (reproduced). The practical impact is negligible next to the wrong tick size. Use `Decimal` when the function is rewritten for banded ticks.

### Rejected suspicions (verified as non-issues)
- `if news_context` doesn't crash on `None`. The real issue is the approval bias.
- The Telegram "`ai_v is None` → approved" branch is unreachable for BUY signals.
- `analyze_technicals` mutating `df` is harmless, because each call works on a fresh download.
- `risk_per_share` division is guarded.
- `get_ist_time`'s mislabelled tzinfo is only a latent issue. Its wall-clock output is correct.
- No frontend/backend field-name mismatches were found.
- The UI does hide Execute for vetoed trades. Only the server fails to enforce the veto.
- Gemini API exceptions fail closed (`app.py:394-400`). Only the missing key or missing library path fails open.

### Added in verification
- **Close trusts the client's exit price** (`app.py:780`). `exit_price` comes from the request body, so any caller sets the realised P&L and the cash credited. Without one, LIVE P&L uses the last yfinance close, not the broker's fill. Journal P&L in LIVE is therefore never the real fill.
- **LIVE cash is a local simulation.** It is never reconciled with Zerodha funds, and margin is computed from the signal price rather than the fill price (`app.py:639-641`).
- **The System Guard tile is static text**, and the frontend never reads `can_trade` either (see High).
- **The stored SL differs from the broker SL**, and the **SL order ID is never stored** (see Critical #1 and High).
- **The settings endpoint has no schema** (see High).
- **The auto-scan needs an open, foreground tab** (see High → Data).

### Docs vs reality
- "Institutional-grade" is not accurate.
- "15:15 auto square-off" is not implemented.
- "09:45 blackout" is not enforced.
- "Kill switch cancels orders" is false.
- "Kite WebSocket" is not used; only yfinance is.
- The AI reads "corporate filings, earnings reports": it actually reads 5 Google News titles.
- "Tick rounding eliminates rejections" is false under the current NSE tick table.
- The docs link to stale `file:///c:/projects/Traiding/...` paths.

---

## Target architecture (architect proposal)

```
aetherkite/
  config.py        pydantic-settings; secrets from env/keyring, never returned by the API
  domain/          models.py (Order, Fill, Position, Signal; OrderState enum), events.py
  marketdata/      service.py (bar cache + LTP), kite_ticker.py (WebSocket), yf_source.py (fallback/backtest)
  strategy/        indicators.py (pure), momentum.py: Bars -> Signal (pure, unit-tested)
  ai/              gatekeeper.py (async, timeout, cache per symbol/candle, fail-closed, model from config)
  risk/            manager.py: pre-trade checks (session window, veto, max positions, duplicates,
                   daily loss limit, broker margin), sizing
  oms/             oms.py (order state machine, idempotency), broker.py (Protocol),
                   kite_adapter.py, paper_adapter.py (simulated fills, SL/TP triggers)
  engine/          scheduler.py, reconciler.py, eod.py (15:15 flatten)
  persistence/     db.py (SQLite WAL), repositories, event_log (append-only)
  notify/          telegram.py (queue + html.escape)
  api/             routes.py (thin), auth.py, schemas.py (Pydantic request models)
  web/             pinned static assets, WebSocket/SSE client
tests/             strategy, risk, OMS state machine, paper adapter, API (TestClient + FakeBroker)
```

Key design choices:
- **Single writer.** One engine owns all state changes, and API routes send commands to it. This removes the race conditions by construction.
- **`BrokerAdapter` protocol.** `KiteAdapter` and `PaperAdapter` share it, so paper mode exercises the same OMS code as live.
- **OMS state machine:** `NEW → SUBMITTED → OPEN → PARTIAL → FILLED | CANCELLED | REJECTED`.
  - Driven by Kite order updates, with polling only as a fallback.
  - UUID client order IDs map to the Kite `tag`.
  - Entry and SL are tracked as a linked group. If the SL fails, flatten and alert.
  - Exits cancel the linked SL first.
- **Scheduler:**
  - Scan at candle close plus a few seconds of grace.
  - Monitor paper SL/TP.
  - Reconcile with `kite.positions()`/`orders()` every 30–60s (broker is the source of truth; halt on drift).
  - Cancel and flatten at 15:15.
  - Check the token at 08:45.
  - Use the NSE holiday calendar.
- **Security:**
  - A per-launch auth token in a custom header, which blocks CSRF.
  - Enforce `Content-Type: application/json`.
  - Allowlist the Host header.
  - Strict CSP.
  - Secrets never returned by the API.
  - OAuth `state` nonce.

---

## Roadmap

### Phase 1: days (stop the money-losing and security bugs)
1. Save `sl_order_id` on the position at entry; today it isn't saved. In LIVE, close and kill switch cancel it and exit at the broker. Local state changes only after the broker confirms. Fix the kill-switch cash math.
2. `execute_trade`:
   - Poll the order to a final state and handle partial fills.
   - If the SL fails, flatten immediately and alert.
   - Store `sl_order_id`. Use UUID IDs and a Kite `tag`.
   - Re-raise `HTTPException` before the generic except.
3. One lock around every load→modify→save, atomic writes (temp file + `os.replace`), and refuse to start on a corrupt state file.
4. Server-side checks:
   - `can_trade` and the cutoff.
   - The stored AI verdict.
   - No duplicate symbols.
   - Pydantic request models.
   - Fail closed when there is no Gemini key in LIVE.
   - Tie each position to its trading mode.
5. Settings and display safety:
   - Auth token header, Content-Type check, Host allowlist.
   - Strip secrets from `/api/settings`, allowlist settings keys, and fix the Telegram and capital wipe.
   - Escape all `innerHTML` and Telegram HTML.
6. Hygiene: drop `--reload`, pin dependencies, self-host CSS/JS, use `logging` with Telegram alerts on broker errors.
7. A shared price cache: one batched download per minute.

### Phase 2: 1–2 weeks (modularise and add a control loop)
- Split into the layout above, with the `BrokerAdapter`, `PaperAdapter` and `FakeBroker`.
- Pure indicators with golden-file tests, replacing pandas-ta.
- A background scheduler: server-side scan, paper SL/TP, 15:15 flatten, token check.
- SQLite (WAL) with migration from the JSON files, plus an `events` audit table.
- Broker reconciliation on startup and periodically.
- An instrument master from `kite.instruments()`: symbol→token, tick size, circuit limits.

### Phase 3: 2–4 weeks (event-driven, production-grade)
- Kite Ticker WebSocket for LTP and order updates, building bars in memory.
- A full OMS with partial fills and a SL/TP OCO pair (or GTT-OCO).
- Risk limits: daily max loss, max positions, sector exposure, a circuit breaker on repeated errors. The SL-limit buffer becomes percentage- or ATR-based.
- Async Gemini with a timeout, caching and an injection-resistant prompt. Add an NSE announcements/results calendar.
- A backtest harness that reuses the strategy and paper adapter, with full Zerodha costs and slippage. Costs are included in journal P&L.
- Push updates to the UI instead of polling, structured logs and metrics, and docs updated to match reality.

---

## Top 10 before going live (trader's list)
1. Close and kill switch work from broker state: cancel the SL, read `kite.positions()`, exit only the net quantity, and keep local state if the broker call fails.
2. Atomic entry: poll the order to a final state and handle partial fills. If the SL fails, flatten. Better: a LIMIT or market-protected entry plus GTT OCO.
3. Enforce 09:45 and 15:15 on the server, and add a scheduler that squares off at 15:10–15:15.
4. An exit monitor that runs without the browser: SL, TP, time exits, paper simulation.
5. An instrument master for symbols, tick sizes and circuit limits. Fix TATAMOTORS/TMPV/TMCV.
6. A percentage- or ATR-based SL-limit buffer, or SL-M.
7. Risk limits: max positions, a daily loss halt, no duplicate symbols, a notional cap, per-stock MIS margin, an F&O ban check.
8. Kite historical data and WebSocket ticks. Detect the closed bar by timestamp, not `iloc[-2]`.
9. Compliance: static IP, order `tag`, `market_protection`, a rate throttle, and each position tied to its mode.
10. A backtest with full costs and slippage, costs in the journal P&L, an AI gate that fails closed, and the Nifty regime filter actually wired up.
