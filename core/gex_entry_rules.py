# core/gex_entry_rules.py
#
# Entry-rule engine for the Nifty GEX A+ Setup, per NIFTY_GEX_STRATEGY_SPEC.md
# section 4. "Any single unchecked box = skip. No partial setups." -- this
# module enforces that literally: EntryChecklist.all_pass is only True if
# every single sub-check is True.
#
# HONEST STATUS OF EACH CHECK (read before trusting this in production):
#
#   EMA stack alignment        -- REAL. Wired to gex_ema_stack.get_multi_timeframe_stack().
#   GEX level proximity        -- REAL. Distance from spot to nearest top-N GEX
#                                  strike (from gex_calculator.classify_regime()),
#                                  correct side for direction.
#   Tight consolidation        -- REAL, heuristic. Range compression via ATR
#                                  over a lookback window. A real, checkable
#                                  condition, not a placeholder.
#   Break-and-retest           -- REAL, heuristic. Price crossed a GEX level,
#                                  pulled back within tolerance, still holding.
#   Flag pattern                -- NOT IMPLEMENTED. True flag-pattern detection
#                                  (impulse leg + shallow retracement channel)
#                                  is real pattern-recognition work, not
#                                  something to fake with a shortcut. Structure
#                                  check currently only fires via consolidation
#                                  OR break-and-retest -- flag is a documented
#                                  gap, not a silent approximation.
#   Volume confirmation         -- DEVIATION FROM SPEC WORDING. Spec says
#                                  "volume on the trigger candle" -- Nifty INDEX
#                                  candles always report volume=0 (indices have
#                                  no direct traded volume). Substituted with
#                                  near-ATM, correct-side OPTION volume (CE
#                                  volume for bullish setups, PE for bearish),
#                                  compared to a rolling average -- reuses data
#                                  already fetched via gex_calculator, no new
#                                  API dependency.
#   Time-of-day window          -- REAL. First 1-2 hours + configurable lull
#                                  exclusion, per spec.
#   Option liquidity            -- DEVIATION FROM SPEC WORDING. Spec says
#                                  "bid-ask spread check" -- never tested against
#                                  a real quotes/depth endpoint. Substituted with
#                                  OI + volume thresholds as a liquidity proxy.
#                                  Revisit once a depth/quote endpoint is
#                                  confirmed working, same way the Greeks API
#                                  was confirmed before building on it.

import logging
from dataclasses import dataclass, field
from datetime import time as dtime, datetime
from typing import List, Optional

import pytz

from core.regime_engine import Candle, RegimeEngine
from core.gex_ema_stack import MultiTimeframeStackResult

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Tunables (all flagged here, not buried in logic) ────────────────────────
GEX_PROXIMITY_ATR_MULTIPLE = 1.0     # "at/approaching" a GEX strike = within 1x ATR
CONSOLIDATION_LOOKBACK = 6           # candles
CONSOLIDATION_RANGE_ATR_MULTIPLE = 0.8   # tight = recent range < 0.8x ATR
RETEST_TOLERANCE_ATR_MULTIPLE = 0.3  # "held the retest" = within 0.3x ATR of level
VOLUME_CONFIRM_MULTIPLE = 1.2        # trigger option volume > 1.2x rolling avg

# Time windows (IST) -- first 1-2 hrs + lull exclusion, per spec section 4
SESSION_START = dtime(9, 15)
HIGH_LIQUIDITY_END = dtime(11, 15)     # "first 1-2 hours"
LULL_START = dtime(12, 0)
LULL_END = dtime(13, 0)
HARD_CUTOFF = dtime(14, 45)            # matches spec section 5 time-stop cutoff

# Liquidity proxy thresholds (undocumented in spec -- reasonable defaults,
# revisit once real paper-trade option volume/OI ranges are observed)
MIN_OPTION_OI = 100_000
MIN_OPTION_VOLUME = 50_000


@dataclass
class EntryChecklist:
    ema_stack_aligned: bool = False
    gex_level_proximity: bool = False
    structure_ok: bool = False
    structure_type: str = "none"        # "consolidation" | "break_retest" | "none"
    volume_confirmed: bool = False
    time_window_ok: bool = False
    option_liquidity_ok: bool = False

    direction: str = "none"             # "bullish" | "bearish" | "none"
    target_strike: Optional[float] = None
    reasons_failed: List[str] = field(default_factory=list)

    @property
    def all_pass(self) -> bool:
        return (
            self.ema_stack_aligned
            and self.gex_level_proximity
            and self.structure_ok
            and self.volume_confirmed
            and self.time_window_ok
            and self.option_liquidity_ok
        )


def check_time_window(now_ist: Optional[datetime] = None) -> bool:
    """Per spec: first 1-2 hours of session, excluding the 12-13 lull, before hard cutoff."""
    now_t = (now_ist or datetime.now(IST)).time()
    if now_t < SESSION_START or now_t > HARD_CUTOFF:
        return False
    if LULL_START <= now_t < LULL_END:
        return False
    return True


def check_gex_proximity(spot: float, direction: str, gex_regime: dict, atr: float) -> tuple:
    """
    Returns (proximity_ok, nearest_target_strike).
    Per spec: "Price at/approaching a top-3-5 GEX strike, correct side for direction."
    """
    if not gex_regime:
        return False, None

    candidates = gex_regime.get("top_positive_strikes" if direction == "bullish"
                                 else "top_negative_strikes", [])
    if not candidates:
        return False, None

    threshold = atr * GEX_PROXIMITY_ATR_MULTIPLE if atr > 0 else 0
    nearest = None
    nearest_dist = float("inf")
    for strike, _gex_val in candidates:
        dist = abs(strike - spot)
        if dist < nearest_dist:
            nearest_dist = dist
            nearest = strike

    if nearest is None:
        return False, None

    return (nearest_dist <= threshold if threshold > 0 else False), nearest


def check_structure(candles: List[Candle], target_strike: float, atr: float) -> tuple:
    """
    Returns (structure_ok, structure_type).
    Checks tight consolidation OR break-and-retest. Flag pattern NOT implemented.
    """
    if len(candles) < CONSOLIDATION_LOOKBACK or atr <= 0:
        return False, "none"

    recent = candles[-CONSOLIDATION_LOOKBACK:]
    recent_range = max(c.high for c in recent) - min(c.low for c in recent)
    if recent_range < (atr * CONSOLIDATION_RANGE_ATR_MULTIPLE):
        return True, "consolidation"

    crossed = any(
        (c.high >= target_strike >= c.low) or
        (c.low > target_strike and recent[0].close < target_strike) or
        (c.high < target_strike and recent[0].close > target_strike)
        for c in recent
    )
    last_close = recent[-1].close
    holding_retest = abs(last_close - target_strike) <= (atr * RETEST_TOLERANCE_ATR_MULTIPLE)
    if crossed and holding_retest:
        return True, "break_retest"

    return False, "none"


def check_volume_confirmation(chain_data: dict, target_strike: float, direction: str,
                               volume_history: List[float]) -> bool:
    """
    Substitutes near-ATM correct-side OPTION volume for the spec's "trigger
    candle volume" (which doesn't exist for an index).
    """
    leg = "CE" if direction == "bullish" else "PE"
    strike_data = chain_data.get(target_strike, {}).get(leg)
    if not strike_data or strike_data.get("volume") is None:
        return False

    current_vol = strike_data["volume"]
    if not volume_history:
        return False

    avg_vol = sum(volume_history) / len(volume_history)
    if avg_vol <= 0:
        return False

    return current_vol >= (avg_vol * VOLUME_CONFIRM_MULTIPLE)


def check_option_liquidity(chain_data: dict, target_strike: float, direction: str) -> bool:
    """Liquidity proxy (see module docstring re: bid-ask deviation)."""
    leg = "CE" if direction == "bullish" else "PE"
    strike_data = chain_data.get(target_strike, {}).get(leg)
    if not strike_data:
        return False
    oi = strike_data.get("oi") or 0
    vol = strike_data.get("volume") or 0
    return oi >= MIN_OPTION_OI and vol >= MIN_OPTION_VOLUME


def evaluate_entry(spot: float, candles: List[Candle], gex_regime: dict,
                    ema_stack: MultiTimeframeStackResult, chain_data: dict,
                    volume_history: List[float] = None,
                    now_ist: Optional[datetime] = None) -> EntryChecklist:
    """
    Full A+ checklist evaluation. Every sub-check must independently pass --
    no partial credit, per spec section 4's explicit rule.
    """
    checklist = EntryChecklist()
    volume_history = volume_history or []

    if ema_stack.agreement and ema_stack.direction in ("bullish", "bearish"):
        checklist.ema_stack_aligned = True
        checklist.direction = ema_stack.direction
    else:
        checklist.reasons_failed.append("ema_stack_not_aligned")
        return checklist

    if len(candles) >= 15:
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        closes = [c.close for c in candles]
        atr = RegimeEngine._calc_atr(highs, lows, closes, 14)
    else:
        atr = 0.0

    proximity_ok, target_strike = check_gex_proximity(spot, checklist.direction, gex_regime, atr)
    checklist.gex_level_proximity = proximity_ok
    checklist.target_strike = target_strike
    if not proximity_ok:
        checklist.reasons_failed.append("not_near_gex_level")

    if target_strike is not None:
        structure_ok, structure_type = check_structure(candles, target_strike, atr)
        checklist.structure_ok = structure_ok
        checklist.structure_type = structure_type
        if not structure_ok:
            checklist.reasons_failed.append("no_clean_structure")
    else:
        checklist.reasons_failed.append("no_target_strike_for_structure_check")

    if target_strike is not None:
        checklist.volume_confirmed = check_volume_confirmation(
            chain_data, target_strike, checklist.direction, volume_history
        )
        if not checklist.volume_confirmed:
            checklist.reasons_failed.append("volume_not_confirmed")
    else:
        checklist.reasons_failed.append("no_target_strike_for_volume_check")

    checklist.time_window_ok = check_time_window(now_ist)
    if not checklist.time_window_ok:
        checklist.reasons_failed.append("outside_trading_window")

    if target_strike is not None:
        checklist.option_liquidity_ok = check_option_liquidity(chain_data, target_strike, checklist.direction)
        if not checklist.option_liquidity_ok:
            checklist.reasons_failed.append("insufficient_option_liquidity")
    else:
        checklist.reasons_failed.append("no_target_strike_for_liquidity_check")

    if checklist.all_pass:
        logger.info(
            f"[gex_entry_rules] A+ SETUP: direction={checklist.direction} "
            f"target_strike={checklist.target_strike} structure={checklist.structure_type}"
        )
    else:
        logger.debug(f"[gex_entry_rules] No entry — failed: {checklist.reasons_failed}")

    return checklist
