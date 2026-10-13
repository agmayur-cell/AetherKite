import datetime as dt
import unittest

from tests.fakes import HEADERS, NOW, TODAY, AppTestMixin, FakeGate, bars

import app
from aetherkite import clock, costs, strategy


class SecurityTests(AppTestMixin, unittest.TestCase):
    def test_api_requires_token(self):
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        self.assertEqual(self.client.get("/api/status", headers={"X-AetherKite-Token": "nope"}).status_code, 401)
        self.assertEqual(self.get("/api/status").status_code, 200)

    def test_kill_switch_is_not_reachable_cross_site(self):
        # A malicious page can POST without custom headers; that must be refused.
        r = self.client.post("/api/kill-switch", data="x", headers={"Content-Type": "text/plain"})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(app.load_state()["system_halted"])

    def test_unknown_host_rejected(self):
        r = self.client.get("/api/status", headers={**HEADERS, "Host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    def test_dashboard_embeds_token_and_security_headers(self):
        r = self.client.get("/")
        self.assertIn('content="test-token"', r.text)
        self.assertNotIn("__AETHERKITE_TOKEN__", r.text)
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])

    def test_settings_never_return_secrets(self):
        self.write_config({"gemini_api_key": "G-SECRET", "kite_api_secret": "K-SECRET", "kite_access_token": "T-SECRET",
                           "telegram_bot_token": "TG-SECRET", "telegram_chat_id": "42"})
        for r in (self.get("/api/settings"), self.post("/api/settings", {"risk_per_trade_pct": 0.004}), self.get("/api/status")):
            self.assertEqual(r.status_code, 200, r.text)
            for secret in ("G-SECRET", "K-SECRET", "T-SECRET", "TG-SECRET"):
                self.assertNotIn(secret, r.text)

    def test_kite_callback_escapes_and_checks_status(self):
        r = self.client.get("/api/kite-callback?status=<script>x</script>")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("<script>", r.text)
        self.assertEqual(self.client.get("/api/kite-callback").status_code, 400)


class StartupTests(AppTestMixin, unittest.TestCase):
    def test_startup_halts_on_unresolved_live_orders(self):
        st = app.load_state()
        st["pending_live_orders"] = [{"tag": "AK1", "symbol": "RELIANCE", "quantity": 5, "time": "x"}]
        app.save_state(st)
        import logging
        from fastapi.testclient import TestClient
        try:
            with TestClient(app.app) as client:  # runs the lifespan (startup) hook
                status = client.get("/api/status", headers=HEADERS).json()["portfolio"]
            self.assertTrue((app.DATA_DIR / "logs" / "aetherkite.log").exists())
        finally:
            logger = logging.getLogger("aetherkite")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        self.assertTrue(status["system_halted"])
        self.assertIn("Unresolved live order", status["halt_reason"])


class SettingsTests(AppTestMixin, unittest.TestCase):
    def test_blank_secret_fields_keep_saved_values(self):
        self.write_config({"telegram_bot_token": "TG", "telegram_chat_id": "42", "gemini_api_key": "G"})
        r = self.post("/api/settings", {"telegram_bot_token": "", "telegram_chat_id": "", "gemini_api_key": "",
                                        "telegram_alerts_enabled": True})
        self.assertEqual(r.status_code, 200, r.text)
        cfg = app.load_config()
        self.assertEqual((cfg["telegram_bot_token"], cfg["telegram_chat_id"], cfg["gemini_api_key"]), ("TG", "42", "G"))
        self.post("/api/settings", {"clear_telegram": True})
        self.assertEqual(app.load_config()["telegram_bot_token"], "")

    def test_rejects_non_finite_and_out_of_range_numbers(self):
        for body in ('{"risk_per_trade_pct": NaN}', '{"starting_capital": Infinity}', '{"risk_per_trade_pct": 0.5}',
                     '{"max_portfolio_leverage": 0}'):
            r = self.client.post("/api/settings", content=body, headers={**HEADERS, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 422, body)

    def test_watchlist_is_normalised(self):
        r = self.post("/api/settings", {"watchlist": [" tcs", "TCS", "m&m", "BAJAJ-AUTO"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(app.load_config()["watchlist"], ["TCS", "M&M", "BAJAJ-AUTO"])
        self.assertEqual(self.post("/api/settings", {"watchlist": ["TC S;"]}).status_code, 422)

    def test_capital_change_resets_cash_only_when_flat(self):
        r = self.post("/api/settings", {"starting_capital": 250000})
        self.assertEqual(r.json()["cash_balance"], 250000)
        self.assertEqual(self.buy().status_code, 200)
        self.assertEqual(self.post("/api/settings", {"starting_capital": 300000}).status_code, 409)
        # Saving the same value (what the modal does) is fine and leaves cash alone.
        cash = app.load_state()["cash_balance"]
        self.assertEqual(self.post("/api/settings", {"starting_capital": 250000, "risk_per_trade_pct": 0.004}).status_code, 200)
        self.assertEqual(app.load_state()["cash_balance"], cash)

    def test_live_mode_requires_flag_and_no_positions(self):
        self.assertEqual(self.post("/api/settings", {"trading_mode": "LIVE"}).status_code, 403)
        app.LIVE_ENABLED = True
        self.assertEqual(self.buy().status_code, 200)
        self.assertEqual(self.post("/api/settings", {"trading_mode": "LIVE"}).status_code, 409)

    def test_corrupt_state_is_reported_not_overwritten(self):
        app.STATE_STORE.path.write_text("{ truncated")
        r = self.get("/api/status")
        self.assertEqual(r.status_code, 503)
        self.assertIn("will not overwrite", r.json()["detail"])
        self.assertEqual(self.post("/api/kill-switch").status_code, 503)
        self.assertEqual(app.STATE_STORE.path.read_text(), "{ truncated")


class ScanTests(AppTestMixin, unittest.TestCase):
    def test_buy_signal_is_executable_with_server_side_id(self):
        rows = self.scan()
        r = rows["RELIANCE"]
        self.assertTrue(r["executable"])
        self.assertEqual(r["signal_id"], "RELIANCE|202610141000")
        self.assertEqual(r["candle_time"], "2026-10-14 10:00:00")  # last *closed* bar, not the forming one
        self.assertEqual(rows["TCS"]["quant_signal"], "NEUTRAL")
        self.assertEqual(self.gate.calls, ["RELIANCE"])  # AI only for candidates

    def test_missing_data_is_shown_not_hidden(self):
        self.write_config({"watchlist": ["RELIANCE", "NOSUCH"]})
        row = self.scan()["NOSUCH"]
        self.assertEqual((row["status"], row["executable"]), ("DATA_UNAVAILABLE", False))

    def test_veto_and_unevaluated_verdicts(self):
        app.GATE = FakeGate(approve=False)
        r = self.scan()["RELIANCE"]
        self.assertEqual((r["executable"], r["block_reason"]), (False, "AI veto"))

    def test_regime_filter_blocks_and_fails_closed(self):
        strategy.nifty_regime = lambda df: (None, "Nifty data unavailable")
        r = self.scan()["RELIANCE"]
        self.assertFalse(r["executable"])
        self.assertIn("Nifty regime", r["block_reason"])
        self.assertEqual(self.gate.calls, [])

    def test_outside_entry_window_not_executable(self):
        clock.set_fixed(dt.datetime.combine(TODAY, dt.time(14, 5, 30)))
        self.frames["RELIANCE"] = bars(until=dt.time(14, 0))
        r = self.scan()["RELIANCE"]
        self.assertFalse(r["executable"])
        self.assertIn("No new entries after 14:00", r["block_reason"])

    def test_stale_signal_after_hours_skips_ai(self):
        clock.set_fixed(dt.datetime.combine(TODAY, dt.time(20, 0)))
        self.frames["RELIANCE"] = bars(until=dt.time(15, 15))
        r = self.scan()["RELIANCE"]
        self.assertIn("stale", r["block_reason"])
        self.assertEqual(self.gate.calls, [])

    def test_alerts_are_escaped_deduplicated_and_suppressed_when_halted(self):
        self.write_config({"telegram_alerts_enabled": True, "telegram_bot_token": "x", "telegram_chat_id": "1"})
        self.scan()
        self.scan()
        msgs = [m for m in self.notifier.messages if not m[2]]
        self.assertEqual({m[1] for m in msgs}, {"RELIANCE|202610141000"})  # same dedupe key both times
        self.assertIn("&lt;b&gt;ok&lt;/b&gt;", msgs[0][0])
        self.notifier.messages.clear()
        self.post("/api/kill-switch")
        self.scan()
        self.assertEqual([m for m in self.notifier.messages if not m[2]], [])


class PaperTradingTests(AppTestMixin, unittest.TestCase):
    def test_execute_sizes_and_prices_on_server(self):
        r = self.buy()
        self.assertEqual(r.status_code, 200, r.text)
        pos = app.load_state()["positions"]["RELIANCE"]
        # equity 1L x 0.5% = 500 risk / (1.5 x ATR 10) = 33 shares; margin at 4x.
        self.assertEqual((pos["quantity"], pos["entry_price"], pos["mode"]), (33, 1000.0, "PAPER"))
        self.assertEqual((pos["stop_loss"], pos["take_profit"]), (985.0, 1030.0))
        self.assertEqual(app.load_state()["cash_balance"], 100000 - 8250.0)
        entry = app.load_journal()[0]
        self.assertEqual((entry["trade_id"], entry["ai_confidence"]), (pos["trade_id"], 90))

    def test_client_cannot_supply_price_or_size(self):
        row = self.scan()["RELIANCE"]
        r = self.post("/api/execute-trade", {"signal_id": row["signal_id"], "shares": 99999, "price": 1, "stop_loss": 0})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(app.load_state()["positions"]["RELIANCE"]["quantity"], 33)

    def test_rejections(self):
        self.assertEqual(self.post("/api/execute-trade", {"signal_id": "RELIANCE|209901010000"}).status_code, 404)
        self.assertEqual(self.post("/api/execute-trade", {}).status_code, 422)
        self.assertEqual(self.buy().status_code, 200)
        dup = self.post("/api/execute-trade", {"signal_id": "RELIANCE|202610141000"})
        self.assertEqual(dup.status_code, 409)
        self.assertIn("already open", dup.json()["detail"])

    def test_stale_signal_rejected_at_execution(self):
        row = self.scan()["RELIANCE"]
        clock.set_fixed(NOW + dt.timedelta(minutes=15))
        r = self.post("/api/execute-trade", {"signal_id": row["signal_id"]})
        self.assertEqual(r.status_code, 410)

    def test_chase_guard(self):
        row = self.scan()["RELIANCE"]
        self.frames["RELIANCE"] = bars(last_close=1004.0)  # moved 0.4 ATR since the signal close
        self.market.clear()
        r = self.post("/api/execute-trade", {"signal_id": row["signal_id"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Not chasing", r.json()["detail"])

    def test_max_positions_and_daily_loss_limit(self):
        self.write_config({"max_open_positions": 1})
        strategy.analyze = __import__("tests.fakes", fromlist=["fake_analyze"]).fake_analyze({"RELIANCE", "TCS"})
        self.assertEqual(self.buy("RELIANCE").status_code, 200)
        r = self.post("/api/execute-trade", {"signal_id": self.scan()["TCS"]["signal_id"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Maximum of 1", r.json()["detail"])

        self.write_config({"max_open_positions": 3})
        st = app.load_state()
        st["day"]["realized_pnl"] = -2000.0
        app.save_state(st)
        r = self.post("/api/execute-trade", {"signal_id": self.scan()["TCS"]["signal_id"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Daily loss limit", r.json()["detail"])

    def test_close_books_net_pnl_and_ignores_client_price(self):
        self.buy()
        self.frames["RELIANCE"] = bars(last_close=1010.0)
        self.market.clear()
        r = self.post("/api/close-position", {"symbol": "RELIANCE", "exit_price": 99999})
        self.assertEqual(r.status_code, 200, r.text)
        charges = costs.round_trip_charges(33 * 1000.0, 33 * 1010.0)
        self.assertAlmostEqual(r.json()["realized_pnl"], round(330.0 - charges, 2))
        self.assertAlmostEqual(app.load_state()["cash_balance"], round(100000 + 330.0 - charges, 2))
        j = app.load_journal()[0]
        self.assertEqual((j["status"], j["exit_price"], j["exit_reason"]), ("CLOSED", 1010.0, "MANUAL_EXIT"))

    def test_kill_switch_returns_margin_not_notional(self):
        self.buy()
        r = self.post("/api/kill-switch")
        self.assertEqual(r.status_code, 200)
        st = app.load_state()
        self.assertTrue(st["system_halted"])
        self.assertEqual(st["positions"], {})
        charges = costs.round_trip_charges(33000.0, 33000.0)
        self.assertAlmostEqual(st["cash_balance"], round(100000 - charges, 2))  # was ~1.25L+ before the fix
        self.assertEqual(self.post("/api/execute-trade", {"signal_id": "x|y"}).status_code, 409)
        self.assertEqual(self.post("/api/resume-system", {}).status_code, 200)

    def test_status_equity_and_read_only_get(self):
        self.buy()
        before = app.STATE_STORE.path.read_text()
        self.frames["RELIANCE"] = bars(last_close=1020.0)
        self.market.clear()
        p = self.get("/api/status").json()["portfolio"]
        self.assertAlmostEqual(p["unrealized_pnl"], 660.0)
        self.assertAlmostEqual(p["total_equity"], 100000 + 660.0)  # cash + margin + unrealised, not + notional
        self.get("/api/positions")
        self.assertEqual(app.STATE_STORE.path.read_text(), before)  # GETs never write

    def test_journal_note_export_and_analytics(self):
        self.buy()
        trade_id = app.load_journal()[0]["trade_id"]
        self.assertEqual(self.post("/api/journal/note", {"trade_id": trade_id, "notes": "=HYPERLINK(\"x\")"}).status_code, 200)
        csv_text = self.get("/api/journal/export").text
        self.assertTrue(csv_text.startswith("﻿"))
        self.assertIn("'=HYPERLINK", csv_text)
        self.frames["RELIANCE"] = bars(last_close=1010.0)
        self.market.clear()
        self.post("/api/close-position", {"symbol": "RELIANCE"})
        a = self.get("/api/journal").json()["analytics"]
        self.assertIsNone(a["profit_factor"])  # no losing trades: undefined, not "gross profit"


class EngineTests(AppTestMixin, unittest.TestCase):
    def test_paper_stop_is_hit_without_the_browser(self):
        self.buy()
        clock.set_fixed(dt.datetime.combine(TODAY, dt.time(10, 50)))
        self.frames["RELIANCE"] = bars(until=dt.time(10, 45), lows={"10:30": 980.0})
        self.market.clear()
        app.engine_tick()
        j = app.load_journal()[0]
        self.assertEqual((j["status"], j["exit_reason"], j["exit_price"]), ("CLOSED", "STOP_LOSS_HIT", 985.0))

    def test_target_hit(self):
        self.buy()
        clock.set_fixed(dt.datetime.combine(TODAY, dt.time(10, 50)))
        self.frames["RELIANCE"] = bars(until=dt.time(10, 45), highs={"10:45": 1031.0})
        self.market.clear()
        app.engine_tick()
        self.assertEqual(app.load_journal()[0]["exit_reason"], "TARGET_HIT")

    def test_square_off_at_cutoff(self):
        self.buy()
        clock.set_fixed(dt.datetime.combine(TODAY, dt.time(15, 10, 5)))
        self.frames["RELIANCE"] = bars(until=dt.time(15, 0))
        app.engine_tick()
        self.assertEqual(app.load_journal()[0]["exit_reason"], "EOD_SQUARE_OFF")
        self.assertEqual(app.load_state()["positions"], {})

    def test_leftover_position_from_previous_day_is_closed(self):
        self.buy()
        clock.set_fixed(dt.datetime.combine(TODAY + dt.timedelta(days=1), dt.time(9, 0)))
        app.engine_tick()
        j = app.load_journal()[0]
        self.assertEqual((j["exit_reason"], j["exit_price_estimated"]), ("STALE_SESSION_CLOSE", True))

    def test_auto_scan_once_per_candle(self):
        calls = []
        original = app.run_scan
        app.run_scan = lambda force=False: calls.append(force)
        try:
            for second in (10, 25, 40):
                app.engine_tick(dt.datetime.combine(TODAY, dt.time(10, 30, second)))
        finally:
            app.run_scan = original
        self.assertEqual(calls, [True])


class LiveTradingTests(AppTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.go_live()

    def test_live_disabled_without_flag(self):
        app.LIVE_ENABLED = False
        r = self.buy()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.kite.placed, [])

    def test_expired_kite_session_blocks_entries(self):
        self.write_config({"kite_token_date": "2026-10-13"})
        self.assertEqual(self.buy().status_code, 409)

    def test_live_requires_evaluated_ai(self):
        app.GATE = FakeGate(approve=True, evaluated=False)
        row = self.scan()["RELIANCE"]
        self.assertFalse(row["executable"])

    def test_entry_places_protective_stop_and_records_it(self):
        r = self.buy()
        self.assertEqual(r.status_code, 200, r.text)
        entry, stop = self.kite.placed
        self.assertEqual((entry["order_type"], entry["market_protection"], entry["product"]), ("MARKET", -1, "MIS"))
        self.assertLessEqual(len(entry["tag"]), 20)
        self.assertEqual(stop["order_type"], "SL")
        self.assertLess(stop["price"], stop["trigger_price"])
        pos = app.load_state()["positions"]["RELIANCE"]
        self.assertEqual((pos["sl_order_id"], pos["stop_loss"], pos["entry_price"]), ("O2", stop["trigger_price"], 1000.5))
        self.assertEqual(app.load_state()["pending_live_orders"], [])

    def test_unfilled_entry_is_cancelled_and_not_booked(self):
        self.kite.entry = "open"
        r = self.buy()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(self.kite.cancelled, ["O1"])
        st = app.load_state()
        self.assertEqual((st["positions"], st["pending_live_orders"], st["system_halted"]), ({}, [], False))

    def test_failed_stop_flattens_immediately(self):
        self.kite.stop = "raise"
        r = self.buy()
        self.assertEqual(r.status_code, 502)
        self.assertIn("sold again", r.json()["detail"])
        self.assertEqual([(o["transaction_type"], o["order_type"]) for o in self.kite.placed],
                         [("BUY", "MARKET"), ("SELL", "SL"), ("SELL", "MARKET")])  # entry, failed stop, flatten
        self.assertEqual(self.kite.net["RELIANCE"], 0)
        self.assertEqual(app.load_state()["positions"], {})

    def test_failed_stop_and_failed_flatten_halts(self):
        self.kite.stop, self.kite.flatten = "reject", "reject"
        r = self.buy()
        self.assertEqual(r.status_code, 502)
        self.assertIn("UNPROTECTED", r.json()["detail"])
        st = app.load_state()
        self.assertTrue(st["system_halted"])
        self.assertEqual(len(st["pending_live_orders"]), 1)
        self.assertEqual(self.post("/api/resume-system", {}).status_code, 409)
        self.assertEqual(self.post("/api/resume-system", {"clear_pending": True}).status_code, 200)

    def test_exit_cancels_stop_then_sells_broker_quantity(self):
        self.buy()
        r = self.post("/api/close-position", {"symbol": "RELIANCE"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.kite.cancelled, ["O2"])
        self.assertEqual(self.kite.placed[-1]["transaction_type"], "SELL")
        self.assertEqual(self.kite.net["RELIANCE"], 0)
        self.assertEqual(app.load_journal()[0]["exit_price"], 999.5)

    def test_exit_after_stop_already_fired_does_not_short(self):
        self.buy()
        self.kite.fire_stop("O2")
        r = self.post("/api/close-position", {"symbol": "RELIANCE"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.kite.placed), 2)  # no extra SELL
        self.assertEqual(self.kite.net["RELIANCE"], 0)
        self.assertEqual(app.load_journal()[0]["exit_reason"], "STOP_LOSS_HIT")

    def test_broker_failure_keeps_position(self):
        self.buy()
        self.kite.flatten = "reject"
        r = self.post("/api/close-position", {"symbol": "RELIANCE"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("RELIANCE", app.load_state()["positions"])

    def test_engine_books_broker_stop_fill(self):
        self.buy()
        self.kite.fire_stop("O2")
        clock.set_fixed(NOW + dt.timedelta(minutes=5))
        app.engine_tick()
        j = app.load_journal()[0]
        self.assertEqual((j["exit_reason"], j["exit_price"]), ("STOP_LOSS_HIT", round(self.kite.orders["O2"]["average_price"], 2)))

    def test_kill_switch_reports_failures(self):
        self.buy()
        self.kite.flatten = "reject"
        r = self.post("/api/kill-switch").json()
        self.assertEqual([f["symbol"] for f in r["failed"]], ["RELIANCE"])
        self.assertIn("FAILED", r["message"])
        self.assertTrue(app.load_state()["system_halted"])
        self.assertTrue(any("kill switch" in m[0] for m in self.notifier.messages))

    def test_paper_position_survives_mode_switch_without_live_sell(self):
        self.write_config({"trading_mode": "PAPER"})
        self.buy()
        self.go_live()  # simulates an old config edited by hand
        r = self.post("/api/close-position", {"symbol": "RELIANCE"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.kite.placed, [])  # a PAPER position never sends a broker order


if __name__ == "__main__":
    unittest.main()
