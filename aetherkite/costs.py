"""
Estimated Zerodha equity-intraday (MIS) charges for one round trip.

!!! PLACEHOLDER RATES !!!
Written from memory and not verified against Zerodha's current charge list.
Check them on Zerodha's brokerage calculator and update before relying on the
journal's net P&L. Slippage is not included (it is already in the fill prices).
"""

BROKERAGE_PCT = 0.0003       # 0.03% per executed order ...
BROKERAGE_CAP = 20.0         # ... capped at Rs 20
STT_SELL_PCT = 0.00025       # sell side only
EXCHANGE_TXN_PCT = 0.0000297  # NSE, both sides
SEBI_PCT = 0.000001          # Rs 10 per crore, both sides
STAMP_BUY_PCT = 0.00003      # buy side only
GST_PCT = 0.18               # on brokerage + exchange + SEBI


def round_trip_charges(buy_value: float, sell_value: float) -> float:
    brokerage = min(BROKERAGE_CAP, buy_value * BROKERAGE_PCT) + min(BROKERAGE_CAP, sell_value * BROKERAGE_PCT)
    stt = sell_value * STT_SELL_PCT
    txn = (buy_value + sell_value) * EXCHANGE_TXN_PCT
    sebi = (buy_value + sell_value) * SEBI_PCT
    stamp = buy_value * STAMP_BUY_PCT
    gst = (brokerage + txn + sebi) * GST_PCT
    return round(brokerage + stt + txn + sebi + stamp + gst, 2)
