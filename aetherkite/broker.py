"""
Zerodha Kite order handling for LIVE mode.

Rules this module enforces:
- Entries are polled to a final state. An entry that hasn't filled in time is
  cancelled, and only the quantity actually filled is protected and tracked.
- If the protective stop can't be placed, the filled quantity is flattened
  immediately. If that also fails, BrokerError(critical=True) is raised.
- Exits never sell blindly. The resting stop is cancelled first, then the net MIS
  quantity is re-read from the broker, so a stop that fired in the meantime
  can't turn the exit into a short.

String literals are used for Kite constants (they match kiteconnect 5.x) so
tests can drive this with a fake client.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .market import round_to_tick

log = logging.getLogger("aetherkite.broker")

EXCHANGE = "NSE"
PRODUCT = "MIS"
VARIETY = "regular"
MARKET_PROTECTION_AUTO = -1  # kiteconnect: -1 = exchange/broker automatic market protection

TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}


class BrokerError(Exception):
    def __init__(self, message: str, critical: bool = False):
        super().__init__(message)
        self.critical = critical


@dataclass
class EntryResult:
    order_id: str
    quantity: int
    average_price: float
    sl_order_id: str
    sl_trigger: float
    sl_limit: float


@dataclass
class ExitResult:
    quantity: int                  # shares sold by this exit (0 if the broker was already flat)
    average_price: Optional[float]  # fill price of the exit, or of the stop if it fired first
    outcome: str                   # EXITED | STOP_FILLED | BROKER_FLAT


class KiteBroker:
    def __init__(self, kite, poll_interval: float = 0.25, poll_timeout: float = 8.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.kite = kite
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.sleep = sleep

    # -- order status ---------------------------------------------------------
    def last_state(self, order_id: str) -> Dict:
        history = self.kite.order_history(order_id=order_id) or []
        return history[-1] if history else {"status": "UNKNOWN", "filled_quantity": 0, "average_price": 0}

    def wait_terminal(self, order_id: str) -> Dict:
        waited = 0.0
        state = self.last_state(order_id)
        while state.get("status") not in TERMINAL and waited < self.poll_timeout:
            self.sleep(self.poll_interval)
            waited += self.poll_interval
            state = self.last_state(order_id)
        return state

    def net_mis_quantity(self, symbol: str) -> int:
        net = (self.kite.positions() or {}).get("net", [])
        return int(sum(p.get("quantity", 0) for p in net
                       if p.get("tradingsymbol") == symbol and p.get("product") == PRODUCT
                       and p.get("exchange") == EXCHANGE))

    def ltp(self, symbol: str) -> float:
        key = f"{EXCHANGE}:{symbol}"
        data = self.kite.ltp(key) or {}
        if key not in data:
            raise BrokerError(f"No last price for {symbol} from Kite")
        return float(data[key]["last_price"])

    # -- orders -----------------------------------------------------------------
    def _market(self, symbol: str, side: str, qty: int, tag: str) -> str:
        return self.kite.place_order(
            variety=VARIETY, exchange=EXCHANGE, tradingsymbol=symbol, transaction_type=side,
            quantity=qty, product=PRODUCT, order_type="MARKET",
            market_protection=MARKET_PROTECTION_AUTO, tag=tag,
        )

    def flatten(self, symbol: str, qty: int, tag: str) -> Dict:
        """Market sell `qty` and wait for the result. Raises if it doesn't complete."""
        oid = self._market(symbol, "SELL", qty, tag)
        state = self.wait_terminal(oid)
        if state.get("status") != "COMPLETE":
            raise BrokerError(f"Flatten SELL for {symbol} ended {state.get('status')}: {state.get('status_message')}",
                              critical=True)
        return state

    def enter_long(self, symbol: str, qty: int, stop_distance: float, sl_buffer_pct: float, tag: str) -> EntryResult:
        try:
            oid = self._market(symbol, "BUY", qty, tag)
        except Exception as e:
            raise BrokerError(f"Entry order rejected by Kite: {e}") from e

        state = self.wait_terminal(oid)
        if state.get("status") not in TERMINAL:
            try:
                self.kite.cancel_order(variety=VARIETY, order_id=oid)
            except Exception as e:
                log.warning("cancel of unfilled entry %s failed: %s", oid, e)
            state = self.wait_terminal(oid)

        if state.get("status") not in TERMINAL:
            # Still working after a cancel attempt, so the final size is unknown. Don't guess; make the user check Kite.
            raise BrokerError(f"Entry {oid} still {state.get('status')} after cancel; check Kite immediately.", critical=True)
        filled = int(state.get("filled_quantity") or 0)
        if filled <= 0:
            raise BrokerError(f"Entry not filled (status {state.get('status')}: {state.get('status_message') or 'no message'})")
        avg = float(state.get("average_price") or 0)

        trigger = round_to_tick(avg - stop_distance, "down")
        limit = round_to_tick(trigger * (1 - sl_buffer_pct), "down")
        try:
            sl_id = self.kite.place_order(
                variety=VARIETY, exchange=EXCHANGE, tradingsymbol=symbol, transaction_type="SELL",
                quantity=filled, product=PRODUCT, order_type="SL", price=limit, trigger_price=trigger, tag=tag,
            )
            sl_state = self.last_state(sl_id)
            if sl_state.get("status") == "REJECTED":
                raise BrokerError(f"stop rejected: {sl_state.get('status_message')}")
        except Exception as e:
            log.error("stop placement failed for %s, flattening %d: %s", symbol, filled, e)
            try:
                self.flatten(symbol, filled, tag)
            except Exception as e2:
                raise BrokerError(f"UNPROTECTED POSITION: stop failed ({e}) and flatten failed ({e2}). "
                                  f"Close {filled} {symbol} in Kite now.", critical=True) from e2
            raise BrokerError(f"Stop-loss could not be placed ({e}); the {filled} shares bought were sold again.") from e

        return EntryResult(oid, filled, avg, sl_id, trigger, limit)

    def exit_long(self, symbol: str, max_qty: int, sl_order_id: Optional[str], tag: str) -> ExitResult:
        sl_state = None
        if sl_order_id:
            try:
                self.kite.cancel_order(variety=VARIETY, order_id=sl_order_id)
            except Exception as e:
                log.info("cancel of stop %s failed (may already be complete): %s", sl_order_id, e)
            sl_state = self.wait_terminal(sl_order_id)
            if sl_state.get("status") not in TERMINAL:
                raise BrokerError(f"Stop order {sl_order_id} is still {sl_state.get('status')}; not selling to avoid a double exit.")

        net = self.net_mis_quantity(symbol)
        qty = min(net, max_qty)
        if qty <= 0:
            if sl_state and sl_state.get("status") == "COMPLETE":
                return ExitResult(0, float(sl_state.get("average_price") or 0) or None, "STOP_FILLED")
            return ExitResult(0, None, "BROKER_FLAT")

        state = self.flatten(symbol, qty, tag)
        return ExitResult(qty, float(state.get("average_price") or 0), "EXITED")
