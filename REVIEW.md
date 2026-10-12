# AetherKite Terminal: Expert Review

*Review date: 2026-10-13 · Commit reviewed: `f52f8d5` · Reviewers: Indian market trader / risk manager, technical architect, line-level code reviewer (all read-only)*

*Verification pass: 2026-10-13. Every finding was re-checked line by line against `app.py` and `static/index.html`, and the numeric claims were reproduced in Python. Findings marked **[corrected]** were wrong or overstated and have been fixed in place. Findings marked **[external]** depend on facts outside the repo (NSE/SEBI/Zerodha rules, library internals) that couldn't be checked here. New issues found during verification are listed under "Added in verification".*

*Second round: 2026-10-13. Four further independent reviews (trader/risk, architecture, code, docs) are merged under "Second-round expert reviews". The target architecture, roadmap and top-10 list were revised based on them.*

---

## Verdict

All three reviewers reached the same conclusion. The app works as a **signal dashboard and paper-trading tool**. **It is not safe for real money yet.** The LIVE path can leave you with an untracked position that has no stop-loss, or with a short you never meant to take. Several safety features the docs describe are not in the code.

The second round adds two conclusions:
- **Fixing the bugs isn't enough.** The strategy has no demonstrated edge. A no-edge simulation of its 1:2 ATR geometry loses about 0.2–0.35R per trade after Indian intraday costs. Paper mode as built can't show otherwise, because it never simulates stops, targets or costs.
- **The first-round fix plan was optimistic, and in places unsafe.** "Cancel the SL, then exit" can double-sell. Realistic time to safe live is 8–12 weeks plus a paper soak, and a backtest should be the first go/no-go gate.

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
  - It does not re-check the AI veto, market hours, types or NaN. Reproduced: `{"shares": NaN}` passes both the `<= 0` check and the margin check, because every comparison with NaN is false. A string `shares` raises a TypeError, which surfaces as a 500. **[upgraded in second round]** NaN cash is written to disk and bricks the app; see the code review table.
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
- P&L ignores STT, stamp duty, GST, exchange/SEBI fees and slippage. These can be 10–20% of risk per trade. No backtest exists. **[upgraded in second round]** The trader puts costs at about 0.17R per trade on typical names and 0.3–0.4R on low-volatility names, which makes this strategy-blocking.
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

## Second-round expert reviews

*2026-10-13. Four independent read-only reviews (trader/risk manager, technical architect, line-level code reviewer, documentation reviewer) were run against the verified findings above. Each reported only new issues or disagreements. Key code claims were spot-checked against the source before merging. The trader's Monte Carlo (MC) figures are no-edge random-walk baselines, not forecasts. **[external]** has the same meaning as above.*

### Trading strategy and risk (trader/risk manager)

**Strategy verdict.** Even with every bug fixed, this is a generic 15m MACD-cross, long-only system on India's most efficient stocks. With no edge, the 1:2 ATR geometry gives about 0R per trade before costs and **about −0.2 to −0.35R after costs** (MC). It needs a real gross edge of at least 0.3R, and nothing in the repo shows one. It shouldn't get real capital until a backtest passes the gate in "Go-live evidence" below.

**Disagreements with the first round**
- **Costs should be High and strategy-blocking, not Medium "10–20% of risk".**
  - RELIANCE-type name (15m ATR ≈ 0.32%, ₹2,500 risk, ≈ ₹5.2L notional): charges ≈ ₹230 = 0.09R, which I checked by hand. Add ≈ 0.04% round-trip slippage and it is **≈ 0.17R**.
  - Low-volatility names (HINDUNILVR, ITC, POWERGRID, NESTLEIND): notional reaches ₹8–10L for the same risk, so costs are **≈ 0.3–0.4R**.
  - Each RMS square-off adds ₹59. Charge rates are **[external]**.
- **F&O-ban, circuit and ASM/GSM checks are overweighted.**
  - A ban restricts new derivative positions, not cash MIS buying **[external]**.
  - F&O stocks have dynamic price bands **[external]**.
  - Keep these only as guards against arbitrary symbols typed into the watchlist.
- **GTT-OCO is believed to be CNC/NRML only, not MIS [external].** The MIS-native options are a Cover Order (if Zerodha still offers it) or OMS-managed SL + TP.
- **Partial fills and rate throttling are overweighted at 5–20 orders a day.** The real entry failures are status lag, RMS rejection and SL-leg failure.
  - Static IP and algo registration, by contrast, are pass/fail gates for go-live **[external]**.
- **Flat 4x leverage errs on the conservative side.** MIS on Nifty-50 is typically ≈ 5x **[external]**, so the local check rejects trades the broker would accept, not the reverse.
- **For SL type, prefer SL-M with `market_protection` [external].** On liquid names the bigger risk is an SL-L that triggers and never fills, not a freak trade. Otherwise use a buffer of ≥ max(0.3%, 0.5×ATR) plus a watchdog.

**New findings**

| Severity | Finding | Location | Scenario / numbers |
|---|---|---|---|
| High | **No evidence of edge; the indicator stack is mostly one filter** | `app.py:326-330` | MC: about 1 MACD cross per stock per day. The trend filter removes about half and the RSI 40–65 band only about 16%. That leaves about 20 signals a day across 50 names. The RSI cap discards the strongest crosses. EMA200 on 15m bars is about 8 sessions, not a "macro regime". |
| High | **Time exits dominate; entries allowed too late** | `app.py:262-264, 531-532` | MC TP-hit rates by entry time: 20% (10:00), 11% (12:00), 5% (13:00), 1% (14:00). Time exits rise to 80% by 14:00. Win rate drifts toward 50% while expectancy stays ≈ 0R, so win rate says nothing. Stop entries at ≈ 14:00 or use a time-aware target. |
| High | **Signals cluster on one bar; selection is alphabetical; sector concentration** | `app.py:575`, example watchlist | The watchlist has 11 financials, 6 autos and 5 IT names. One Bank Nifty swing fires 5–10 crosses on the same bar. Rows are sorted A→Z, so ADANI*/AXIS/BAJ* get capital first and the largest sizes. The positions you end up with are one sector bet. |
| High | **No day rollover: stale MIS positions live forever locally** | `app.py:443-459, 777-800` | If the app is off at 15:15, the RMS squares off and charges ₹59 **[external]**, but the position stays OPEN locally. It is marked from yfinance for days and its margin stays locked. The next day, Exit sends a **real SELL, which opens a fresh short**. |
| High | **Pre-open and stale-signal execution** | `index.html:955-959`, `app.py:621-708` | Before market, `iloc[-2]` is yesterday's bar, so yesterday's crosses show as BUY. If you execute at 09:00–09:08, the order joins the pre-open auction **[external]**. The 0.8s check sees OPEN and raises an error, and the order then fills untracked with no SL. PAPER fills at yesterday's close. |
| High | **Paper mode can't produce go-live evidence** | `app.py:650, 780` | Paper fills at the stale signal close, which is usually better than the real fill. It has no SL/TP/time exits and no costs. "30 paper sessions" measures the operator, not the strategy. |
| Medium | **Event-day risk is not covered by the AI gate** | `app.py:374-378` | The prompt vetoes only *past* bad news. It ignores results or board meetings today, RBI policy, the Budget (including special Saturday sessions, where `is_market_hours` says closed but execute still works) and expiry days **[external]**. The gate can't be backtested, so log its verdicts in shadow mode. |
| Medium | **ATR and MACD are contaminated across sessions** | `app.py:305-317` | A 1.5% overnight gap lifts Wilder ATR by about 36%, and about a third of that is still present by 12:45. SL/TP widen exactly when TP is already hard to reach. Gap bars also create false MACD crosses. |
| Medium | **Sizing ∝ 1/ATR concentrates notional where costs per R are worst** | `app.py:529-535, 343` | Low-ATR names get 2–3× the notional. ATR is rounded to ₹0.05, a 5–10% risk error on ₹150–400 stocks. |
| Medium | **No chase guard between signal and fill** | `app.py:667-691` | The fill comes about 15 minutes after the signal. If price has already run 1 ATR, you are buying the move, not the cross. Reject when \|LTP − signal close\| > 0.3 ATR. |
| Medium | **No watchdog for a triggered-but-unfilled SL** | `app.py:690-704` | An SL-L whose limit is jumped sits OPEN below the market and nothing polls it. |
| Medium | **The regime filter would be noise even if wired up** | `app.py:190-242` | INDEX_CACHE "trend" is a 2-day change with a ±0.10% band, and it falls back to hardcoded prices. |
| Low | **Watchlist labelled Nifty-50 may be stale** | example config | BPCL, BRITANNIA, HEROMOTOCO and INDUSINDBK may have left the index; ETERNAL, JIOFIN, INDIGO and MAXHEALTH may have joined **[external]**. |
| Low | **Holidays, muhurat and special sessions** | `app.py:256-270` | Stale signals show on holidays. Special sessions show as closed, but execute still works. |

### Architecture (technical architect)

**Assessment of the first-round target architecture**
- **Keep:**
  - The `BrokerAdapter` seam, split three ways: FakeBroker (scripted failures for tests), PaperBroker (market simulation) and KiteBroker.
  - SQLite with WAL and `synchronous=FULL` (laptop power loss), one connection, backups via the backup API, and no OneDrive-synced folder.
  - Broker as the source of truth, the 08:45 token check, the holiday calendar, and pure indicators with golden tests. RSI and ATR need Wilder smoothing.
- **Change: concurrency.**
  - Drop the actor/command bus. Use a `TradingService` that is the only code mutating state, with one lock held across each broker-mutating operation.
  - **GET routes must never write.** Today `update_live_positions` saves on every poll (`app.py:458-459`). Removing that write eliminates most of Critical #6 on its own.
- **Change: OMS states.**
  - Add `PENDING_SUBMIT` (persisted *before* `place_order`) and `SUBMIT_UNKNOWN` (when `place_order` raised; a read timeout doesn't mean the order wasn't placed).
  - Kite has no PARTIAL status **[external]**, so derive fill state from `filled_quantity`.
  - Map the transient statuses (`TRIGGER PENDING`, `VALIDATION PENDING`, `MODIFY/CANCEL PENDING`, …) to "working", and treat `AMO REQ RECEIVED` as an error.
  - Persist `status_message`. "SL trigger above LTP" means the price is already through the stop, so exit at once.
- **Change: order tags.** A UUID won't fit Kite's tag of about 20 characters **[external]**. Use a short ID such as `AK` + yymmdd + base32 sequence.
- **Change: exit mechanics.**
  - "Cancel SL, then exit" is a race. The SL can fill between the two calls, and the exit then opens a short.
  - **Modify the resting SL to MARKET** instead, so only one order exists **[external: confirm Kite allows changing order_type]**.
  - Fallback: cancel, wait for a confirmed `CANCELLED` (not `COMPLETE`), re-read the net quantity from `positions()`, and exit exactly that.
  - This supersedes "cancel the linked SL first" in Phase 1 #1 and in the trader's list.
- **Change: failures and inputs.**
  - "If SL fails, flatten" needs bounded retry, escalation, entries halted and a Telegram page, because the flatten can fail too.
  - Execute should take a server-side **`signal_id`** (symbol, candle time, verdict, TTL, server-computed size) as its idempotency key. The client never sends price, size or SL.
  - Keep Gemini synchronous, with a hard timeout in a bounded pool outside the trading lock. There's no need for asyncio.
- **Drop or defer:**
  - GTT-OCO for MIS (see the trader section).
  - Kite WebSocket and push UI: polling `orders()` every 2–5s while an order is working, and `positions()` every 30s, is enough. Postbacks need a public URL.
  - Ten packages is too many for about 3k LOC.

**Reconciliation semantics.** The first round said "halt on drift", which would misfire on normal days.
- **Local position open, broker flat** (SL fired, RMS square-off, manual exit in the Kite app): this is expected. Close locally from the actual fills and don't halt.
- **Broker position with no local record:** halt entries, alert, and make sure a protective SL exists. Never adopt it into the strategy automatically.
- **Quantity mismatch:** halt entries, alert, and resize the SL.
- **Position with no live SL:** re-place the SL immediately. This is the top priority.
- **Halts:** `HALT_ENTRIES` or `FLATTEN_ALL`. **No halt ever blocks exits.**
- **Account hygiene:** manual MIS trades in watchlist symbols corrupt attribution, so dedicate the account to the app.

**Minimum viable safe architecture**

```
aetherkite/
  main.py        app factory + lifespan: single-instance lock, clock checks, startup reconcile, engine thread
  settings.py    pydantic-settings; secrets from env/keyring; trading_mode fixed at start
  store.py       SQLite (WAL, synchronous=FULL): signals, orders, positions, journal, audit; migrations
  marketdata.py  one batched fetcher; bars + LTP cache with as_of and stale flag
  strategy.py    pure: bars -> Signal; closed bar decided by timestamp
  gate.py        Gemini: hard timeout, cache per (symbol, candle), fail-closed in LIVE
  risk.py        pure pre-trade checks + sizing
  instruments.py kite.instruments(): symbol->token, tick size, bands; refreshed daily
  broker.py      Protocol + KiteBroker + PaperBroker (tests/fake_broker.py)
  oms.py         TradingService: sole mutator, one lock; intents -> orders; exits; reconcile()
  engine.py      scheduler thread: scan @ bar close + grace, working-order poll, reconcile 30s,
                 15:10 flatten, 08:45 token check, heartbeat
  api.py         thin routes, auth token, Host allowlist, Pydantic schemas; read-only GETs
  notify.py      Telegram queue, html.escape, dedupe
tests/
```

**Invariants**
1. **Protection doesn't depend on the process.** Every live position has a broker-side stop within N seconds of the fill, or is being flattened.
2. **Write-ahead intent.** No broker call happens without a committed `PENDING_SUBMIT` row carrying its tag. An ambiguous outcome is resolved by tag before any retry.
3. **Local quantity and cash change only on broker-confirmed fills.** LIVE equity comes from `kite.margins()`.
4. **One process, one mutator, no writes on GET.** Enforce with a lock file. No `--reload`, no `--workers > 1`.
5. **Exits are always allowed.** Halts block entries only.
6. **Entries fail closed.** Stale data, a missing AI verdict, a bad token, drift, outside the window or clock drift all mean no entry.
7. **Entries are idempotent per server-side signal.**
8. **Mode is fixed at process start.** Every row records its mode. LIVE start requires flat or reconciled broker state.

**New findings**

| Severity | Finding | Location | Scenario |
|---|---|---|---|
| Critical | **A crash or ambiguous submit leaves an untracked live position** | `app.py:667-735`, `start_dashboard.bat:10` | Any exception or process death between `place_order` and `save_state` (`:735`) leaves a broker position with no local record. Causes include an `order_history` timeout, an SL exception, the console being closed, or `--reload` firing on an edit. A read timeout on `place_order` after Kite accepted the order leads the user to retry, which creates a duplicate order. |
| High | **Exits starve behind scans and polls** | `app.py:381-391, 566-572, 443-459`; `index.html:956, 1064-1066` | Gemini has no timeout. `with ThreadPoolExecutor` waits for all futures. Neither the auto-scan nor the polls have an in-flight guard (checked: `triggerScan` disables the button but doesn't check it). During a slowdown, polls and scans can exhaust the threadpool, and close or kill-switch requests queue behind them. Concurrent downloads also invite Yahoo 429s. |
| High | **Laptop sleep or a closed console stops all management silently** | `index.html:936-959`, `start_dashboard.bat` | A server-side scheduler doesn't help if the machine is asleep. Needed: broker-side stops (invariant 1), keep the machine awake or use an always-on host, an external heartbeat that alerts your phone, and replay of missed bars. |
| High | **The static-IP requirement forks the deployment model** | `start_dashboard.bat`, `app.py:419` | If Kite API orders need a whitelisted static IP **[external]**, a laptop on a dynamic IP can't comply. Decide between an ISP static IP and a Mumbai VM *before* building the OMS. The VM makes auth and TLS mandatory. |
| High | **"Halt on drift" is undefined** | first-round design; `app.py:627` | Normal SL or RMS exits would halt the system daily. If the halt gated exits, protection would be blocked. See the reconciliation rules above. |
| Medium | **An expired token shows as connected; config writes are unlocked** | `app.py:481, 437-438, 77-79, 973-990` | `kite_connected` = "a token string exists". A settings save racing the OAuth callback writes the old token back. Validate with `kite.profile()`. |
| Medium | **No observability or audit trail** | `app.py:139, 167, 252, 293, 456, 800` | Logs go to a console window. No raw broker request or response is kept. Prices have no `as_of`, so stale P&L looks live and the kill switch books at it. |
| Medium | **Time handling is fragile** | `app.py:186-188, 721, 732, 815, 856` | Timestamps are naive IST strings. `zoneinfo` on Windows needs the `tzdata` package. Nothing checks clock drift. |
| Medium | **Untestable without network and filesystem** | `app.py:46-48, 177, 186, 382, 435, 663, 679, 788` | Paths and the static directory are bound at import, clients are built inline, and there is a `time.sleep` in a handler. |
| Medium | **Launcher is not reproducible and allows a second instance** | `start_dashboard.bat:8,10` | It uses the bare `python` with no venv and unpinned dependencies. The browser opens before the server is up. A second launch gives two engines that can both trade. |
| Low | **Write amplification and unbounded growth** | `app.py:459, 734, 884-914` | The full state, including all of `order_history`, is rewritten on every poll, and the journal is resent every 15s. |
| Low | **Dependency risk** | `requirements.txt`, `app.py:24, 126, 384` | pandas-ta is used for only 4 indicators. yfinance and Google News RSS are unofficial. `google-genai` is pre-1.0. Kite historical data is a paid add-on **[external]**. |

**Migrating from the JSON files.**
1. Migrate after market close. Products are MIS-only, so confirm with `positions()` and `orders()` that the broker is flat.
2. The same evening, close local phantom LIVE positions using the real fills from `trades()` (current day only **[external]**) or a Console export.
3. Close paper positions with reason `MIGRATION`, or carry them as `mode=PAPER`. Never carry them into LIVE.
4. Don't trust `cash_balance`, which the kill-switch and settings bugs corrupt. For PAPER, recompute it as starting capital + Σ realised P&L. For LIVE, use `kite.margins()`.
5. Give journal entries new keys and keep the legacy ID (`ORD-` IDs collide). Fix duplicate OPEN entries and convert timestamps to tz-aware.
6. The script must be idempotent, with a dry run and a written report. Keep the JSON as a read-only backup. Refuse to start if JSON exists without a migration marker. Force PAPER afterwards, and move secrets to env or keyring.

### Code (line-level reviewer)

| Severity | Location | Defect | Failure scenario |
|---|---|---|---|
| **High** (upgrades the first-round NaN item) | `app.py:636-643, 711, 735` | **One NaN request bricks the app** | `{"shares": NaN}` gives `cash_balance -= NaN`, and `json.dump` writes `NaN` to disk. From then on, `/api/status` and `/api/positions` always return 500 (Starlette renders with `allow_nan=False`) and the UI freezes silently. Every margin check passes, and every scan row fails on `int(NaN)`. Recovery means editing the state file by hand. Same for `price: NaN` or `Infinity`. |
| Medium | `index.html:676-677` (also 693, 760, 777, 870, 993) | **`res.json()` runs before `res.ok` is checked** | In LIVE, the broker orders are placed and then a later exception follows, e.g. `stop_loss: null` raises a TypeError at `app.py:748` *after* `save_state`. The user sees "Network error: Unexpected token 'I'…" and clicks again, which sends a **second real buy**. |
| Medium | `app.py:68-75, 108-116, 437-438, 990` | **A corrupt journal or config silently loads as empty or default, then overwrites the real file** | A truncated journal loads as `[]`, so the next trade erases all history. A bad config loads defaults, so the next save erases every secret and the watchlist. Config writes have no lock. |
| Medium | `app.py:604-605`, `index.html:985-986` | **"Test Alert Ping" always fails once Telegram is configured** | The modal never pre-fills the token, and `payload.get(k, default)` returns `""` because the key is present. |
| Low | `index.html:956-958, 554` | **The auto-scan runs 24/7** | Overnight it re-sends the same last-bar BUY to Gemini and Telegram every 15 minutes. If the server is down, a blocking `alert()` appears every 15 minutes. |
| Low | `index.html:976`, `371, 430` | **The settings modal opens with HTML defaults if `/api/status` fails** | Saving then resets cash to ₹1,00,000 and the watchlist to the 6 defaults. |
| Low | `app.py:750-752` | **The journal fabricates an AI approval** | When the payload omits it, the server records "Gemini 3.8 Flash Approved…" at 85% confidence. |
| Low | `app.py:944-951` | **CSV formula injection** | Notes, reason and symbol are written raw, so `=HYPERLINK(...)` runs when the file is opened in Excel. |
| Low | `index.html:587-599, 636` | **DATA_UNAVAILABLE and CALCULATING rows render as real data** | They show as ₹0.00 / BEARISH / NEUTRAL. |
| Low | `index.html:628` | **Execute shows for 0-share signals** | The confirm dialog says "Execute BUY for 0 shares". |
| Low | `index.html:542` | **A scan ignores the active search and BUY-only filter** | `renderRadar` is called instead of `filterRadar`. |
| Low | `app.py:304` | **The EMA fallback is dead code** | With fewer than 200 bars, only about 2 non-NaN rows remain, so the result is always INSUFFICIENT_DATA. The trader notes this is safe, but the symbol shows CALCULATING forever. |
| Low | `app.py:220-247` | **NaN index close → `/api/status` 500** | `round(nan)` is cached and fails JSON rendering. |
| Low | `app.py:209, 251` | **A failed index refresh retries on every poll with no lock** | Every tab triggers two `yf.download` calls every 5 seconds. |
| Low | `app.py:382-391` | **No Gemini timeout; a new client on every call** | One hung call blocks a scan worker. |
| Low | `app.py:976-981` | **The server doesn't normalise the watchlist** | Duplicates give double rows and double alerts. Lower-case symbols reach Kite as typed. |
| Low | `start_dashboard.bat:7-10` | **Launcher defects** | It has no `cd /d "%~dp0"`, so it fails when run as administrator. It also opens the browser before the server is up. |
| Low | `app.py:734, 969` | **`order_history` grows forever** | It is rewritten and re-sent on every poll. |
| Nit | `index.html:517, 968` | **Float display and silent rounding of risk** | Risk 0.07 displays as `7.000000000000001%` (checked). Saving the modal silently rounds 1.25% to 1.3%. |
| Nit | `index.html:900, 905, 914` | **Audio code defects** | `className` assigned on a Lucide `<svg>` does nothing. A new `AudioContext` is created per chime and never closed. |
| Nit | `app.py:423, 429-441, 619` | **Callback and Telegram error handling** | Callback errors return HTTP 200, a cancelled login gives a raw 422, and Kite's `status` is never checked. An upstream Telegram failure is reported as 500, not 502. |
| Nit | `index.html:852-883, 1043`; `app.py:807-857, 939-951` | **Smaller gaps** | Unchecked responses in resume, note and settings. The kill switch doesn't refresh the journal. EXIT and KILL records have no `mode`. The CSV omits `ai_rationale` and has no BOM. |

**Cleanup**
- Unused code: imports `os`, `sys`, `Dict`, `Any`; `requests` in requirements; `global INDEX_CACHE`; the identity `M&M` map; unused `e`, `idx` and `res`; IDs `btn-kite-login` and `kpi-guard` that nothing references.
- Structure: imports scattered mid-file, and the Sensex and Nifty blocks are copy-pasted.
- Naming: "50 stocks" labels against the 6-symbol default; four different product names; "Gemini 3.8 Flash" hard-coded in about 6 places.
- Missing types: order and status strings should be enums.
- Threads: daemon Telegram threads are unbounded, and a single worker queue would do.

### Documentation (documentation reviewer)

**Inaccurate or misleading claims** (beyond "Docs vs reality" above). Doc names: RM = README, PC = PROJECT_CONTEXT, UG = UI_User_Guide, SG = Setup Guide.

| Doc location | Claim | Reality | Severity |
|---|---|---|---|
| UG:2, 170, 189 | "Automated", "hands-free" | Every entry and exit is a manual click. No server loop exists. | High |
| UG:139 | Exit reasons `TARGET_HIT`, `STOP_LOSS_HIT`, `KILL_SWITCH` | Never written (checked). The real values are `MANUAL_DISCRETIONARY_EXIT` and `EMERGENCY_KILL_SWITCH`. A broker SL fill is never recorded. | High |
| UG:197, SG:423 | "Paper trade 30 sessions; win rate > 50% and PF > 1.5" | Paper never simulates SL or TP. Break-even at 2R is about a 33% win rate, so ">50%" is the wrong gate. | High |
| UG:157 | A stop "never exceeds" the risk % | Sized on cash; market entry; SL-L can gap; costs ignored. | High |
| UG:61; RM:52 | The Gemini pill is green when the key is active; the key is "Required" | The pill only checks that the string is non-empty. The example placeholder shows green while every BUY is VETOED. With no key, everything is auto-approved, and no doc says so. | High |
| SG:80, 84 | "Production Python Implementation" | The listing is not `app.py` and won't run as written. | High |
| UG:11, 65, 78, 164 | "All 50 Nifty stocks pre-loaded" | The default has 6. Getting 50 means copying the example config, and no doc says so. | Medium |
| RM:15, UG:84, 154 | "Real-time", "last traded price" | yfinance close of the last closed 15m bar. | Medium |
| UG:185 | After the kill switch, evaluations are halted | `run_scan` never checks `system_halted`. Scans and Telegram alerts continue. | Medium |
| (absent) | Regime badge | Cosmetic, but users will read it as a trading gate. | Medium |
| RM:82 | The SG covers "backtest logic" | No backtest exists anywhere. | Medium |
| SG:67, 421 | Sizing prevents margin shortfalls and over-leverage | There is no margin or leverage cap in sizing. | Medium |
| PC:74-91 | Config contract | JSON with `//` comments, which is invalid (checked). Pasted in, it silently falls back to defaults, and the next save overwrites it. | Medium |
| PC:97, 102-103 | State and journal schemas | Wrong key names, and many fields are missing. | Medium |
| PC:107-124 | Endpoint table | `/api/positions` is missing and no payloads are documented. Settings also overwrites cash. Scan is a GET with side effects. | Medium |
| PC:143 | The frontend is "self-contained" | It loads 3 CDNs. Offline, `lucide.createIcons()` throws and polling never starts. | Medium |
| RM:5, PC:66 | Gemini 3.8 Flash | The model ID is hard-coded and not configurable. Whether it is valid is **[external]**; if it isn't, every BUY is vetoed. | Medium |
| UG:35; PC:124 | Daily login gives an authenticated session | No UI shows token state, and an expired token looks connected. | Medium |
| UG:44-57, 65, 92, 142, 171; RM:44 | Mockup, "2–3 s scan", "VETOED (HIGH/CRITICAL)", "notes auto-save instantly", "timer syncs with the exchange", "browser opens automatically" | All inaccurate in detail. | Low |

**The Setup Guide listing vs `app.py`**
- **Inconsistent:** RSI 45–65 in the guide vs 40–65 in the app (checked).
- **The guide's code is better in several places**, and these ideas are worth porting into `app.py`: a hard error below 200 bars, a 4x quantity cap, a confidence ≥ 0.70 veto, `model_validate_json`, and `kite.margins()`.
  - Don't port its silent ₹1L fallback when `kite.margins()` fails.
- **The guide's code won't run.** It has no entrypoint, no Kite construction or token flow, no instrument lookup and no news fetcher.

**Setup gaps**
1. Python and venv: no tested Python version, no venv steps, the launcher ignores any venv, and `.venv/` isn't gitignored.
2. Dependencies: nothing is pinned. pandas-ta may also break on `pkg_resources` in Python 3.12+ venvs **[external]**.
3. Paths and clone: the docs use a stale `cd c:\projects\Traiding` path and give no clone URL.
4. Config: no doc says to copy the example to `trading_config.json`. The README lists the generated JSON files as repo files. The example's `500000` capital isn't applied until a Settings save.
5. **Kite:**
   - The Redirect URL `http://127.0.0.1:8000/api/kite-callback` appears only in the Settings modal and in no `.md` (checked).
   - Daily token expiry is undocumented, and so are app creation and cost **[external]**.
6. Telegram: no doc explains how to get the chat ID, and none warns that each save wipes the token.
7. PAPER vs LIVE: neither is defined anywhere.
8. Runtime: internet access is required, the system clock must be correct (the scan timer runs on the browser clock), and the port is fixed at 8000.
9. Data files: no doc explains how to reset the paper account or what happens on file corruption.
10. Troubleshooting: none.

**Safety and compliance messaging**
- **Disclaimer:** there is one short disclaimer in the README and nothing in the UG, SG or UI. There is no "not investment advice / not SEBI-registered" statement, and no LICENSE file (checked).
- **Overclaiming language:** "high-probability setups", "protects your capital", "institutional", "Production", "Respect the AI veto".
- **LIVE mode:** there is no prominent warning, and the Execute confirm dialog doesn't show PAPER vs LIVE. The docs should tell users to treat the Kite app as the real kill switch.
- **Secrets:** they are stored in plaintext next to the code and not explained. The README invites committing `trading_config.json` (it is gitignored). Git history checked by the reviewer: no secrets committed.
- **Regulation and data sources:** no mention of SEBI's retail-algo framework **[external]**, or that yfinance and Google News are unofficial.

**Internal inconsistencies**
- RSI band: 40–65 vs 45–65.
- Default risk: six different values across the docs, the code and the example config.
- Capital: ₹1L vs ₹5L.
- Watchlist size: 6 vs 50.
- Leverage: 4 vs 4.5 vs 5.
- Kill-switch exit reason: `KILL_SWITCH` vs `EMERGENCY_KILL_SWITCH`.
- Architecture: "Tri-Layer" vs "Two-Layer".
- Launch command: with vs without `--reload`.
- Product name: four different names.
- UG section numbering: two sections numbered 9 (checked).

**Proposed doc set**
- **README:** an honest one-liner, a status banner linking this review, a Windows quick start, a PAPER vs LIVE table, and the disclaimer and licence.
- **docs/SETUP.md:** a config reference generated from `DEFAULT_CONFIG` (with unused keys flagged), credentials including the exact Redirect URL, and troubleshooting.
- **docs/USER_GUIDE.md:** real screenshots and labels, the limits of each control, and a daily routine in which the user enforces 09:45 and 15:15 until the code does.
- **docs/STRATEGY.md:** the rules exactly as coded, the break-even win rate, and a "not implemented" list. Delete the SG listing, or move it to `docs/design/` marked "design sketch, not executed".
- **ARCHITECTURE.md:** an accurate diagram, a full endpoint reference with payloads, and the real schemas.
- **SECURITY.md**, **CHANGELOG.md** (starting at 2.0.0) and a **LICENSE**.
- **Hygiene:** one product name, 24h IST times throughout, and a launcher that activates the venv and waits for the server before opening the browser.

---

## Target architecture

The first-round proposal (a 10-package layout with an actor-style single-writer engine) is **superseded** by the architect's *minimum viable safe architecture* and its eight invariants in the second-round section above. What survives from the first round: the `BrokerAdapter` protocol, SQLite, broker-as-truth reconciliation, the scheduler duties (bar-close scan, 15:10 flatten, 08:45 token check, holiday calendar), and the security list:
- a per-launch auth token in a custom header;
- a `Content-Type` check;
- a Host allowlist;
- a strict CSP;
- no secrets returned by the API;
- an OAuth `state` nonce.

---

## Roadmap (revised)

The first-round estimates (days, then 1–2 weeks, then 2–4 weeks) were **optimistic by about 2x**. Phase 1 as written would also have been rewritten during the refactor. Realistic time to safe live: **8–12 weeks of development plus a paper soak.** The backtest moves to the front, because it decides whether the rest is worth building.

### Phase 0: ≤1 day (stop the bleeding)
- **Make LIVE impossible by default.** Orders are refused unless an explicit env flag is set. Manage any existing live positions in the Kite app.
- **Fix how state is written.**
  - Drop `--reload`.
  - Remove `save_state` from the GET paths (`app.py:458-459`).
  - Write state atomically (temp file + `os.replace`).
  - Refuse to start, or to overwrite, when a state, journal or config file fails to load.
- **Reject bad input and runaway calls.**
  - Reject non-finite numbers on input.
  - Hard timeout on Gemini.
  - In-flight guards on the polls and the auto-scan, and gate the auto-scan on market hours.
- **Fix the settings wipes:** Telegram credentials and capital.

### Phase 1: 1.5–2 weeks (permanent foundations and the go/no-go gate)
- **Make the code testable.** Seams: injectable data directory, clock, broker, market data and LLM. Then write TestClient characterisation tests before refactoring.
- **Replace pandas-ta** with in-house indicators and golden-file tests (Wilder smoothing).
- **Extract `strategy.py` and `risk.py` as pure code**, with:
  - a server-side `signal_id`;
  - max positions, a sector cap and a daily-loss halt;
  - no duplicate symbols;
  - entries only from 09:45 to ≈14:00;
  - a stale-signal check (`candle_time` must be today's just-closed bar);
  - a chase guard.
- **Security and hygiene:** auth token, Host allowlist, Content-Type check, Pydantic schemas, no secrets in responses, escaping (HTML, Telegram, CSV), pinned dependencies, self-hosted assets, and logging to a file.
- **Backtest harness. This is the go/no-go decision**; see "Go-live evidence" below.
- **Decide the deployment target:** a static-IP local machine or a Mumbai VM.

### Phase 2: 3–4 weeks (build the live path once; only if the backtest passes)
- **Storage:** SQLite and the migration from JSON, as described above.
- **OMS:**
  - write-ahead intents;
  - the full Kite status mapping;
  - modify-SL-to-MARKET exits;
  - an SL watchdog;
  - reconciliation using the drift rules.

  FakeBroker failure-script tests are the merge gate.
- **Simulation:** a pessimistic PaperBroker that models:
  - LTP fills plus slippage;
  - SL-L gap-through;
  - SL hit first on bars that touch both SL and TP;
  - time exits;
  - full charges and the RMS fee;
  - replay of missed bars after sleep.
- **Engine thread:** bar-close scan, 15:10 flatten with retries, 08:45 `profile()` check, heartbeat.
- **Instrument master:** symbols, tick sizes and bands.
- **Runtime safety:** single-instance lock, audit table for broker calls, an exchange calendar, and an event-day block.

### Phase 3: 4+ weeks (mostly calendar time)
- **Forward paper trading** for 8–12 weeks on the corrected simulator, with daily reconciliation reports.
- **Live at minimum size** (0.25% risk, max 2 positions) for at least a month, then scale.
- **Optional:** Kite WebSocket ticks and a push UI.

---

## Go-live evidence (trader)

**Backtest requirements**
- **Data:** at least 3 years of Kite 15m data, including the June 2024 election crash and the 2025 drawdowns.
- **Universe:** historical Nifty-50 constituents, to avoid survivorship bias.
- **Fill and exit modelling:**
  - fill at the next bar's open plus slippage;
  - SL first when a bar touches both SL and TP;
  - time exits;
  - full charges and RMS fees;
  - portfolio limits.
- **Out-of-sample:** walk-forward, with at least 300 out-of-sample trades.

**Pass bar.** All of these must hold:
- **Expectancy:** net expectancy ≥ +0.10R.
- **Profit factor:** net profit factor ≥ 1.3.
- **Max drawdown:** ≤ 15R.
- **Monthly consistency:** ≥ 60% of months positive.
- **Concentration:** no stock or sector contributes more than 25% of profit.
- **Robustness:** no performance cliff for ±20% parameter changes.
- **Down markets:** acceptable results in months when Nifty fell.

**Then:** 8–12 weeks of forward paper trading, with at least 150 trades and expectancy inside the backtest's 1σ band. **Win rate is not a gate.**

---

## Top 10 before going live (revised)

Merges the trader's re-ranked list with the architect's corrections.

1. **Backtest go/no-go**, run in parallel with items 2–4. It may make the rest unnecessary.
2. **Exits and day rollover work from broker state.**
   - Close, kill and EOD read `kite.positions()`. Exit by **modifying the resting SL to MARKET**, not by cancelling and then selling.
   - Local state changes only after the broker confirms.
   - Reconcile MIS state after 15:30 and at startup. Journal RMS square-offs using the broker's fill.
3. **Atomic, idempotent entry.**
   - A write-ahead `PENDING_SUBMIT` row with a short tag.
   - Poll the order to a terminal state, and cancel if it isn't filled within N seconds.
   - If the SL fails, flatten with retry and escalation.
   - Consider a Cover Order **[external]**.
   - Stale-signal and chase guards.
4. **A server scheduler independent of the browser.**
   - Entries only from 09:45 to ≈14:00, and no pre-open orders.
   - Flatten from 15:10.
   - `profile()` token check at 08:45, and an exchange calendar.
   - Keep the machine awake or use an always-on host, plus an external heartbeat.
5. **Portfolio risk limits.**
   - Size on equity.
   - Max 3 positions, max 1 per sector, total open risk ≤ 1.5%, daily halt at −2R, notional cap of 1× equity.
   - Minimum ATR% filter; rank signals instead of A→Z order.
   - Default risk 0.25–0.5%.
6. **SL execution.** SL-M with market protection, or SL-L with a max(0.3%, 0.5 ATR) buffer, plus a watchdog for triggered-but-unfilled stops.
7. **Event calendar block.** Results or board meeting today, RBI, Budget, expiry-day last hour. The AI gate fails closed in LIVE.
8. **Kite data and an instrument master.** Fix TATAMOTORS→TMPV and tick sizes. Treat the 09:15 bar as session-only for ATR, and drop yfinance for signals.
9. **Compliance gate (pass/fail).** Static IP, algo tag and registration, `market_protection`, and every position tied to its mode **[external]**.
10. **A paper simulator that matches live**, with charges in the journal. Then forward paper trading against the backtest distribution before going live at small size.
