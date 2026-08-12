# core/gex_trade_management.py
#
# Trade management for the Nifty GEX A+ Setup strategy, per
# NIFTY_GEX_STRATEGY_SPEC.md sections 5 & 7.
#
# Covers: position sizing, stop-loss placement, target selection (next GEX
# level in trade direction), R:R gate, regime-based size/target adjustment,
# and time-stop.
#
# ASSUMPTIONS FLAGGED, NOT SILENTLY DECIDED:
#   - "Structural" stop-loss (spec: "below the flag low / swing low... or
#     below/above the key EMA") is implemented as whichever of (recent
#     swing low, EMA50) is TIGHTER (closer to entry) for longs [mirrored
#     for shorts] -- spec offers both without saying which takes
#     precedence. This is the more conservative reading (protects capital
#     first), not the only valid one.
#   - Regime-based size/target multipliers (positive gamma: tighter
#     targets + smaller size; negative gamma: wider targets + reduced
#     size) are spec section 5's directional guidance turned into concrete
#     numbers here, since the spec gives direction but not exact values.
#     Defaults are conservative starting points, meant to be tuned once
#     real paper-trade data exists -- same category of "not yet
#     backtested" caveat as trailing_activation_pct in risk_manager.py.
#   - Time-stop's "unless momentum is strong" override is NOT implemented
#     -- "strong momentum" isn't defined precisely enough in the spec to
#     encode safely. check_time_stop() only enforces the two unambiguous
#     conditions (candle count, hard cutoff); a momentum override is left
#     as a caller-side decision if wanted later.

import logging
from dataclasses import dataclass
from typing import List, Optional

from core.regime_engine import Candle

logger = logging.getLogger(__name__)

NIFTY_LOT_SIZE = 65
MIN_RISK_REWARD = 2.0          # spec section 5: minimum 1:2, skip if not met
DEFAULT_RISK_PCT = 0.015       # 1.5%, matches spec section 7's worked example
SWING_LOOKBACK = 10            # candles to look back for swing low/high

# Minimum-lot tolerance (added Aug 12 2026 session): NIFTY only trades in
# 65-qty lots, so a structurally correct stop can produce a risk_per_unit
# where 1 lot's real risk exceeds the intended risk_amount -- that's lot-size
# granularity, not a sizing mistake. Allow exactly 1 lot through if its risk
# is within MIN_LOT_TOLERANCE_MULTIPLE x the intended risk_amount, bounded by
# a hard MAX_SINGLE_TRADE_RISK ceiling (deliberately reused from
# risk_manager.py's existing per-trade SL cap of -2500.0, not a new
# independent number, so the two risk systems stay consistent). Trades whose
# 1-lot risk exceeds either bound are still correctly skipped, same as before.
MIN_LOT_TOLERANCE_MULTIPLE = 2.0
MAX_SINGLE_TRADE_RISK = 2500.0   # matches risk_manager.py's check_trade_stop_loss cap

# Regime-based adjustment multipliers (see module docstring)
POSITIVE_GAMMA_TARGET_MULTIPLE = 0.7   # tighter targets in positive gamma (mean-reverting)
POSITIVE_GAMMA_SIZE_MULTIPLE   = 0.8   # smaller size
NEGATIVE_GAMMA_TARGET_MULTIPLE = 1.3   # larger targets in negative gamma (trending)
NEGATIVE_GAMMA_SIZE_MULTIPLE   = 0.7   # reduced size (wider stops = more risk per unit)

# Time stop
TIME_STOP_CANDLES_5MIN = 6      # spec section 5 example: 30 min on 5-min candles
HARD_CUTOFF_HOUR   = 14
HARD_CUTOFF_MINUTE = 45


@dataclass
class TradePlan:
    direction:        str                  # "bullish" | "bearish"
    entry_price:       float
    stop_price:        float
    target1_price:      float
    target2_price:       Optional[float]
    risk_per_unit:       float
    reward_to_risk:       float
    quantity:            int
    risk_amount:          float
    regime:               str              # "net_positive" | "net_negative"
    valid:                bool
    reason:               str = ""


def find_structural_stop(
    candles: List[Candle], direction: str, ema50: float, lookback: int = SWING_LOOKBACK
) -> float:
    """
    Per spec section 5: stop is structural -- below the flag low / swing low
    for longs (above for shorts), OR below/above the key EMA. Takes
    whichever is TIGHTER (closer to current price) -- see module docstring.
    """
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    if not recent:
        return ema50

    if direction == "bullish":
        swing_low = min(c.low for c in recent)
        return max(swing_low, ema50)   # tighter of the two = the HIGHER value for a long stop
    else:
        swing_high = max(c.high for c in recent)
        return min(swing_high, ema50)  # tighter of the two = the LOWER value for a short stop


def find_target_from_gex(
    entry_price: float, direction: str, gex_regime: dict, exclude_strike: Optional[float] = None
) -> tuple:
    """
    Per spec section 5: target = next major GEX level in trade direction.
    Returns (target1, target2) -- target1 is the nearest qualifying level
    past entry in the trade direction, target2 is the next one after that
    (for the 50%-scale-out rule), or None if there isn't a second level.
    """
    candidates = gex_regime.get(
        "top_positive_strikes" if direction == "bullish" else "top_negative_strikes", []
    )
    strikes = sorted(
        {s for s, _g in candidates if s != exclude_strike},
        reverse=(direction == "bearish"),
    )

    ahead = [s for s in strikes if (s > entry_price if direction == "bullish" else s < entry_price)]
    if not ahead:
        return None, None
    target1 = ahead[0]
    target2 = ahead[1] if len(ahead) > 1 else None
    return target1, target2


def compute_position_size(
    account_equity: float, entry_price: float, stop_price: float,
    risk_pct: float = DEFAULT_RISK_PCT, lot_size: int = NIFTY_LOT_SIZE,
) -> tuple:
    """
    Per spec section 7:
      risk_amount = account_equity * risk_pct
      qty = floor(risk_amount / risk_per_unit), rounded DOWN to nearest lot_size
    Returns (quantity, risk_amount, risk_per_unit).
    """
    risk_per_unit = abs(entry_price - stop_price)
    if risk_per_unit <= 0:
        return 0, 0.0, 0.0

    risk_amount = account_equity * risk_pct
    raw_qty = int(risk_amount // risk_per_unit)
    quantity = (raw_qty // lot_size) * lot_size

    if quantity == 0:
        one_lot_risk = risk_per_unit * lot_size
        within_tolerance = one_lot_risk <= (risk_amount * MIN_LOT_TOLERANCE_MULTIPLE)
        within_hard_cap = one_lot_risk <= MAX_SINGLE_TRADE_RISK
        if within_tolerance and within_hard_cap:
            quantity = lot_size
            logger.info(
                f"[gex_trade_management] Minimum-lot override: 1 lot risk=Rs{one_lot_risk:.0f} "
                f"exceeds intended risk_amount=Rs{risk_amount:.0f} but within "
                f"{MIN_LOT_TOLERANCE_MULTIPLE}x tolerance and Rs{MAX_SINGLE_TRADE_RISK} hard cap -- allowing 1 lot"
            )
        else:
            logger.info(
                f"[gex_trade_management] 1 lot risk=Rs{one_lot_risk:.0f} exceeds tolerance "
                f"(Rs{risk_amount * MIN_LOT_TOLERANCE_MULTIPLE:.0f}) or hard cap (Rs{MAX_SINGLE_TRADE_RISK}) -- rejecting"
            )

    return quantity, risk_amount, risk_per_unit


def apply_regime_adjustment(target_distance: float, quantity: int, regime: str) -> tuple:
    """
    Per spec section 5: positive gamma -> tighter targets, smaller size;
    negative gamma -> wider/larger targets, reduced size. See module
    docstring for the multiplier-tuning caveat.
    """
    if regime == "net_positive":
        return target_distance * POSITIVE_GAMMA_TARGET_MULTIPLE, int(quantity * POSITIVE_GAMMA_SIZE_MULTIPLE)
    elif regime == "net_negative":
        return target_distance * NEGATIVE_GAMMA_TARGET_MULTIPLE, int(quantity * NEGATIVE_GAMMA_SIZE_MULTIPLE)
    return target_distance, quantity


def build_trade_plan(
    direction: str, entry_price: float, candles: List[Candle], ema50: float,
    gex_regime: dict, account_equity: float, target_strike: Optional[float] = None,
    risk_pct: float = DEFAULT_RISK_PCT, lot_size: int = NIFTY_LOT_SIZE,
) -> TradePlan:
    """
    Assembles the full trade plan: stop, targets, size, R:R gate.
    Returns a TradePlan with valid=False and a reason if any gate fails
    (spec section 5: "If the next GEX level does not offer this [1:2 R:R],
    skip the trade" -- no partial trades, matching the entry checklist's
    all-or-nothing philosophy).
    """
    regime = gex_regime.get("regime", "net_positive")

    stop_price = find_structural_stop(candles, direction, ema50)
    risk_per_unit = abs(entry_price - stop_price)
    if risk_per_unit <= 0:
        return TradePlan(direction, entry_price, stop_price, 0, None, 0, 0, 0, 0,
                          regime, False, "stop_price equals entry -- no valid risk distance")

    target1, target2 = find_target_from_gex(entry_price, direction, gex_regime, target_strike)
    if target1 is None:
        return TradePlan(direction, entry_price, stop_price, 0, None, risk_per_unit, 0, 0, 0,
                          regime, False, "no qualifying GEX level ahead in trade direction")

    target_distance = abs(target1 - entry_price)
    target_distance, _ = apply_regime_adjustment(target_distance, 0, regime)
    adjusted_target1 = entry_price + target_distance if direction == "bullish" else entry_price - target_distance

    reward_to_risk = abs(adjusted_target1 - entry_price) / risk_per_unit

    quantity, risk_amount, _ = compute_position_size(account_equity, entry_price, stop_price, risk_pct, lot_size)
    _, quantity = apply_regime_adjustment(0, quantity, regime)

    if reward_to_risk < MIN_RISK_REWARD:
        return TradePlan(direction, entry_price, stop_price, adjusted_target1, target2,
                          risk_per_unit, reward_to_risk, quantity, risk_amount, regime, False,
                          f"R:R {reward_to_risk:.2f} below minimum {MIN_RISK_REWARD}")

    if quantity <= 0:
        return TradePlan(direction, entry_price, stop_price, adjusted_target1, target2,
                          risk_per_unit, reward_to_risk, quantity, risk_amount, regime, False,
                          "position size rounds to 0 lots -- risk_amount too small for this risk_per_unit")

    return TradePlan(direction, entry_price, stop_price, adjusted_target1, target2,
                      risk_per_unit, reward_to_risk, quantity, risk_amount, regime, True, "OK")


def check_time_stop(now, candles_elapsed: int, max_candles: int = TIME_STOP_CANDLES_5MIN) -> tuple:
    """
    Per spec section 5: exit if no meaningful progress within N candles, or
    hard cutoff before 14:45 IST. See module docstring re: momentum override
    not being implemented.
    """
    if candles_elapsed >= max_candles:
        return True, f"No progress within {max_candles} candles -- time stop"
    if now.hour > HARD_CUTOFF_HOUR or (now.hour == HARD_CUTOFF_HOUR and now.minute >= HARD_CUTOFF_MINUTE):
        return True, f"Hard cutoff reached ({HARD_CUTOFF_HOUR}:{HARD_CUTOFF_MINUTE} IST)"
    return False, ""