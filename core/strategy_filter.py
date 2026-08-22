# core/strategy_filter.py
# Layer 2 — Strategy Filter Engine
# ═══════════════════════════════════════════════════════════════════════════════
# Sits between session_planner (Layer 0), market_context (Layer 1)
# and strategy execution (Layer 3).
#
# Each strategy calls can_trade() before placing any entry order.
# Returns (allowed: bool, reason: str) so the caller can log why it was blocked.
# ═══════════════════════════════════════════════════════════════════════════════

import logging
from datetime import datetime
import pytz

from core.market_context import (
    market_context,
    REGIME_TRENDING_BULL,
    REGIME_TRENDING_BEAR,
    REGIME_RANGE,
    REGIME_REVERSAL_WATCH,
    REGIME_OPENING,
    REGIME_CLOSED,
)
from core.regime_engine import regime_engine

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ─── Per-strategy regime rules ────────────────────────────────────────────────
# Hardcoded string fallbacks are kept lowercased for strict string checking alignment
STRATEGY_ALLOWED_REGIMES = {
    # Survivor trades range AND weak states (mild trend = still safe to sell premium)
    "survivor":       {REGIME_RANGE, "weak_bull", "weak_bear"},
    # BankNifty survivor — same regime profile as survivor, tuned independently
    "bn_survivor":    {REGIME_RANGE, "weak_bull", "weak_bear"},
    # Wave extractor trades strong trends only
    "wave_extractor": {REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR},
    # Saviour combo allows all except closed/opening
    "saviour_combo":  {REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR, REGIME_RANGE, "weak_bull", "weak_bear"},
}

# ─── PCR hard limits ──────────────────────────────────────────────────────────
PCR_MAX = 1.7
PCR_MIN = 0.6

# ─── Opening range guard ──────────────────────────────────────────────────────
REQUIRE_OR_LOCKED = True


class StrategyFilter:
    """
    Gate that every strategy must pass before placing an entry.
    Dynamic regime processing with strict capital ceiling synchronization.
    """

    def __init__(self):
        # Prevent identical log spam across tick streams
        self._last_state_logged = {}

    def can_trade(self, strategy_name: str) -> tuple[bool, str]:
        """
        Main gate check. Call this at the top of any entry signal handler.
        """

        # ── 1. Clock check ────────────────────────────────────────────────
        if not market_context.trade_allowed:
            return False, "outside trading hours (before 9:30 AM or after 3:10 PM)"

        regime = market_context.regime
        if isinstance(regime, str):
            regime = regime.lower()  # Force lowercase to match strategy strings safely

        if regime == REGIME_OPENING:
            return False, "opening range collection in progress (9:15–9:30 AM)"

        if regime == REGIME_CLOSED:
            return False, "market closed"

        # Event filter check
        try:
            from core.event_filter import can_trade_event_filter
            ev_ok, ev_reason = can_trade_event_filter()
            if not ev_ok:
                return False, ev_reason
        except Exception:
            pass

        # ── 2. Session planner check ──────────────────────────────────────
        plan = self._get_plan()

        if plan is not None and plan.is_ready:
            # LOW confidence — block all trading until 10 AM re-eval
            if getattr(plan, "confidence", "HIGH") == "LOW":
                now_t = datetime.now(IST).time()
                from datetime import time as dtime
                if now_t < dtime(10, 0):
                    return False, (
                        f"session plan LOW confidence — "
                        f"all strategies blocked until 10:00 AM re-evaluation"
                    )

            # Check if this specific strategy is activated by session plan
            if strategy_name == "survivor" and not getattr(plan, "survivor_active", True):
                # Override if live regime has shifted to range intraday
                if regime in (REGIME_RANGE, REGIME_REVERSAL_WATCH):
                    override_key = f"survivor_override_{regime}"
                    if self._last_state_logged.get("survivor_override") != override_key:
                        logger.info(
                            f"[strategy_filter] survivor: session plan says BLOCKED "
                            f"but live regime={regime} — OVERRIDING to ALLOW"
                        )
                        self._last_state_logged["survivor_override"] = override_key
                else:
                    return False, (
                        f"[context] regime={regime} not suitable for survivor (needs: range)"
                    )

            if strategy_name == "wave_extractor" and not getattr(plan, "wave_active", True):
                # Override session plan if live regime is now trending
                if regime in (REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR):
                    override_key = f"wave_extractor_override_{regime}"
                    if self._last_state_logged.get("wave_extractor_override") != override_key:
                        logger.info(
                            f"[strategy_filter] wave_extractor: session plan says BLOCKED "
                            f"but live regime={regime} — OVERRIDING to ALLOW"
                        )
                        self._last_state_logged["wave_extractor_override"] = override_key
                else:
                    return False, (
                        f"session plan: Wave Extractor BLOCKED "
                        f"(regime={getattr(plan, 'regime', 'unknown')}, confidence={getattr(plan, 'confidence', 'unknown')})"
                    )

        # ── 3. Opening range must be locked ───────────────────────────────
        if REQUIRE_OR_LOCKED and not market_context.opening_range.is_ready:
            return False, "opening range not yet locked — waiting for 9:30 AM data"

        # ── 4. PCR extreme values ─────────────────────────────────────────
        pcr = market_context.pcr
        if pcr > PCR_MAX:
            return False, f"PCR={pcr:.2f} > {PCR_MAX} — bullish reversal watch, no new entries"
        if pcr < PCR_MIN:
            return False, f"PCR={pcr:.2f} < {PCR_MIN} — bearish reversal watch, no new entries"

        # ── 5. Regime check ───────────────────────────────────────────────
        allowed_regimes = STRATEGY_ALLOWED_REGIMES.get(strategy_name)
        if allowed_regimes is None:
            logger.warning(
                f"[strategy_filter] Unknown strategy: {strategy_name} — allowing by default"
            )
            return True, "ok (unknown strategy — no regime rule)"

        if regime not in allowed_regimes:
            allowed_str = ", ".join(sorted([str(r) for r in allowed_regimes]))
            return False, (
                f"regime={regime} not suitable for {strategy_name} "
                f"(needs: {allowed_str})"
            )

        # ── 5b. Regime transition check — block entries right after a flip ─
        if regime_engine.signals.regime_transitioning:
            return False, (
                f"regime just transitioned to {regime} — blocking new entries "
                f"this cycle for stability"
            )

        
        # ── 6. Direction alignment (diagnostic-only, not blocking) ─────────
        # Logs when OI buildup on the "wrong" side (calls building in a bull
        # trend / puts building in a bear trend) disagrees with the regime
        # direction wave_extractor is about to trade. Log-only for now, not
        # a hard block -- there isn't yet backtested evidence this signal
        # reliably predicts bad entries, and turning it into a live block
        # without that evidence would be adding new, untested trading
        # behavior. Watch these logs; promote to a real check in
        # can_trade() once there's a clear pattern.
        oi = market_context.oi
        if strategy_name == "wave_extractor" and oi.timestamp is not None:
            if regime == REGIME_TRENDING_BULL and getattr(oi, "ce_oi_delta", 0) > 0:
                logger.info(
                    f"[strategy_filter] wave_extractor: CE OI building up "
                    f"(delta={oi.ce_oi_delta}) while regime={regime} — "
                    f"call writers may be fading this bull trend (diagnostic only)"
                )
            elif regime == REGIME_TRENDING_BEAR and getattr(oi, "pe_oi_delta", 0) > 0:
                logger.info(
                    f"[strategy_filter] wave_extractor: PE OI building up "
                    f"(delta={oi.pe_oi_delta}) while regime={regime} — "
                    f"put writers may be fading this bear trend (diagnostic only)"
                )

        # ── All checks passed ─────────────────────────────────────────────
        self._log_context_once(strategy_name, regime, pcr, plan)
        return True, "ok"

    def get_dynamic_params(self, strategy_name: str) -> dict:
        """
        Returns dynamic parameters from session plan for this strategy.
        """
        plan = self._get_plan()
        if plan is None or not plan.is_ready:
            return {}

        p = getattr(plan, "params", None)
        if not p:
            return {}

        if strategy_name == "survivor":
            return {
                "pe_gap":        getattr(p, "pe_gap", 15.0),
                "ce_gap":        getattr(p, "ce_gap", 15.0),
                "pe_symbol_gap": getattr(p, "pe_symbol_gap", 300.0),
                "ce_symbol_gap": getattr(p, "ce_symbol_gap", 300.0),
                "min_premium":   getattr(p, "min_premium", 0.0),
                "position_size": getattr(p, "position_size", 1),
            }
        elif strategy_name == "wave_extractor":
            return {
                "sell_gap":      getattr(p, "sell_gap", 0.0),
                "buy_gap":       getattr(p, "buy_gap", 0.0),
                "cool_off_time": getattr(p, "cool_off_time", 0),
                "position_size": getattr(p, "position_size", 1),
            }
        return {}

    def get_session_risk(self) -> dict:
        """
        Returns dynamic risk limits from session plan.
        Enforces absolute hardcoded capital cap constraint of ₹1,50,000.
        """
        plan = self._get_plan()
        if plan is None or not plan.is_ready:
            return {
                "daily_loss_limit": -3000.0,
                "max_capital":      150000.0,
            }
        
        # Pull dynamic planner details but cap tightly at risk parameter limits
        planner_capital = getattr(plan, "max_capital", 150000.0)
        safe_capital = min(planner_capital, 150000.0)
        
        return {
            "daily_loss_limit": getattr(plan, "daily_loss_limit", -3000.0),
            "max_capital":      safe_capital,
        }

    def get_session_summary(self) -> dict:
        """
        Returns full session plan summary for dashboard display.
        """
        plan = self._get_plan()
        if plan is None or not plan.is_ready:
            return {
                "plan_ready":      False,
                "regime":          "unknown",
                "confidence":      "unknown",
                "survivor_active": False,
                "wave_active":     False,
                "daily_loss_limit": -3000.0,
                "max_capital":      150000.0,
                "notes":           [],
                "produced_at":     None,
                "scores": {},
            }

        s = getattr(plan, "scores", None)
        return {
            "plan_ready":        plan.is_ready,
            "plan_version":      getattr(plan, "plan_version", 1),
            "regime":            getattr(plan, "regime", "unknown"),
            "confidence":        getattr(plan, "confidence", "unknown"),
            "trending_direction": getattr(plan, "trending_direction", "unknown"),
            "survivor_active":   getattr(plan, "survivor_active", False),
            "wave_active":       getattr(plan, "wave_active", False),
            "daily_loss_limit":  getattr(plan, "daily_loss_limit", -3000.0),
            "max_capital":       min(getattr(plan, "max_capital", 150000.0), 150000.0),
            "notes":             getattr(plan, "notes", []),
            "produced_at":       getattr(plan, "produced_at", None),
            "scores": {
                "range_score":       getattr(s, "range_score", 0),
                "trending_score":    getattr(s, "trending_score", 0),
                "or_width":          getattr(s, "or_width", 0),
                "or_width_signal":   getattr(s, "or_width_signal", ""),
                "vix_level":         getattr(s, "vix_level", 0),
                "vix_signal":        getattr(s, "vix_signal", ""),
                "gap_pct":           getattr(s, "gap_pct", 0),
                "gap_signal":        getattr(s, "gap_signal", ""),
                "midpoint_crosses":  getattr(s, "midpoint_crosses", 0),
                "crosses_signal":    getattr(s, "crosses_signal", ""),
                "pcr_at_open":       getattr(s, "pcr_at_open", 1.0),
                "pcr_signal":        getattr(s, "pcr_signal", ""),
            },
            "params": {
                "pe_gap":        getattr(plan.params, "pe_gap", 0.0) if hasattr(plan, "params") else 0.0,
                "ce_gap":        getattr(plan.params, "ce_gap", 0.0) if hasattr(plan, "params") else 0.0,
                "pe_symbol_gap": getattr(plan.params, "pe_symbol_gap", 0.0) if hasattr(plan, "params") else 0.0,
                "ce_symbol_gap": getattr(plan.params, "ce_symbol_gap", 0.0) if hasattr(plan, "params") else 0.0,
                "min_premium":   getattr(plan.params, "min_premium", 0.0) if hasattr(plan, "params") else 0.0,
                "sell_gap":      getattr(plan.params, "sell_gap", 0.0) if hasattr(plan, "params") else 0.0,
                "buy_gap":       getattr(plan.params, "buy_gap", 0.0) if hasattr(plan, "params") else 0.0,
                "position_size": getattr(plan.params, "position_size", 1) if hasattr(plan, "params") else 1,
            },
        }

    def can_trade_direction(self, strategy_name: str, direction: str) -> tuple[bool, str]:
        """Direction-level check — call after can_trade() passes."""
        return True, "ok"

    def context_summary(self) -> dict:
        """Returns market context snapshot for dashboard API."""
        or_ = market_context.opening_range
        oi  = market_context.oi
        sig = regime_engine.signals
        return {
            "regime":        market_context.regime,
            "pcr":           market_context.pcr,
            "pcr_spike":     market_context.is_pcr_spike,
            "trade_allowed": market_context.trade_allowed,
            "or_locked":     or_.locked,
            "or_high":       or_.high,
            "or_low":        or_.low,
            "or_midpoint":   or_.midpoint,
            "total_ce_oi":   getattr(oi, "total_ce_oi", 0),
            "total_pe_oi":   getattr(oi, "total_pe_oi", 0),
            "ce_oi_delta":   getattr(oi, "ce_oi_delta", 0),
            "pe_oi_delta":   getattr(oi, "pe_oi_delta", 0),
            "atm_strike":    getattr(oi, "atm_strike", None),
            "max_pain":      getattr(oi, "max_pain_strike", None),
            "oi_updated_at": oi.timestamp.strftime("%H:%M:%S") if getattr(oi, "timestamp", None) else None,
            "confidence":             sig.confidence,
            "confidence_label":       sig.confidence_label,
            "gap_pct":                sig.gap_pct,
            "prev_day_high":          sig.prev_day_high,
            "prev_day_low":           sig.prev_day_low,
            "prev_day_breakout_bull": sig.prev_day_breakout_bull,
            "prev_day_breakout_bear": sig.prev_day_breakout_bear,
            "regime_transitioning":   sig.regime_transitioning,
            "pe_support_migrating_up":      sig.pe_support_migrating_up,
            "pe_support_migrating_down":    sig.pe_support_migrating_down,
            "ce_resistance_migrating_up":   sig.ce_resistance_migrating_up,
            "ce_resistance_migrating_down": sig.ce_resistance_migrating_down,
            "highest_ce_oi_strike": getattr(oi, "highest_ce_oi_strike", None),
            "highest_pe_oi_strike": getattr(oi, "highest_pe_oi_strike", None),
            "swing_structure_bullish": sig.swing_structure_bullish,
            "swing_structure_bearish": sig.swing_structure_bearish,
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_plan(self):
        """Safely import and return current session plan."""
        try:
            from core.session_planner import session_planner
            return session_planner.current_plan
        except Exception as e:
            logger.debug(f"[strategy_filter] session_planner not available: {e}")
            return None

    def _log_context_once(self, strategy_name: str, regime: str, pcr: float, plan=None):
        """Only logs state confirmation on actual transition state changes to protect disk logs."""
        or_ = market_context.opening_range
        conf = getattr(plan, "confidence", "N/A") if plan else "N/A"
        state_key = f"{strategy_name}_{regime}_{conf}"
        
        if self._last_state_logged.get(strategy_name) != state_key:
            logger.info(
                f"[strategy_filter] {strategy_name} GATES ACTIVE & PASSED | "
                f"regime={regime} | pcr={pcr:.2f} | "
                f"OR={or_.low}–{or_.high} | "
                f"plan_confidence={conf}"
            )
            self._last_state_logged[strategy_name] = state_key


# ── Singleton ──────────────────────────────────────────────────────────────────
strategy_filter = StrategyFilter()