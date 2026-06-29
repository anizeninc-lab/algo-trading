# core/transaction_costs.py
"""
Real Indian F&O transaction cost model (Upstox standard plan, verified June 2026).
Single source of truth for all trading charges -- previously NO transaction
costs were modeled anywhere in this codebase; P&L was computed as pure
(entry - exit) x quantity with zero real-world cost accounting.

Verified rates (June 2026, Upstox standard F&O plan):
  - Brokerage:                  flat Rs 20 per executed order
  - STT (options):               0.0625% of premium value, SELL side only
  - Exchange transaction charge:  0.05%  of premium value, both sides
  - GST:                         18%    of (brokerage + exchange txn charge)
  - Stamp duty:                  0.003% of premium value, BUY side only
  - SEBI charges:                Rs 10 per crore (negligible at retail lot
                                  sizes, included anyway for completeness)

If your broker plan or these government/exchange rates change, update ONLY
this file -- every caller goes through calculate_order_cost().
"""

BROKERAGE_PER_ORDER = 20.0
STT_SELL_PCT        = 0.15 / 100.0
EXCHANGE_TXN_PCT    = 0.03553   / 100.0
GST_PCT             = 18.0   / 100.0
STAMP_DUTY_BUY_PCT  = 0.003  / 100.0
SEBI_PCT            = 10.0   / 10_000_000.0  # Rs 10 per crore


def calculate_order_cost(premium: float, quantity: int, side: str) -> float:
    """
    Returns the total real-world cost (brokerage + all government/exchange
    charges) for ONE executed order on ONE leg.

    premium:  the option premium per unit (NOT total notional)
    quantity: lot-adjusted quantity (e.g. 65 for one Nifty lot)
    side:     'BUY' or 'SELL'
    """
    notional = max(0.0, premium) * quantity

    brokerage    = BROKERAGE_PER_ORDER
    exchange_txn = notional * EXCHANGE_TXN_PCT
    sebi         = notional * SEBI_PCT
    stt          = notional * STT_SELL_PCT       if side == "SELL" else 0.0
    stamp_duty   = notional * STAMP_DUTY_BUY_PCT if side == "BUY"  else 0.0
    gst          = (brokerage + exchange_txn) * GST_PCT

    return round(brokerage + exchange_txn + sebi + stt + stamp_duty + gst, 2)
