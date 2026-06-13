# core/strategy_filter.py
# Layer 2 — Strategy Filter Engine
# ═══════════════════════════════════════════════════════════════════════════════
# Sits between session_planner (Layer 0), market_context (Layer 1)
# and strategy execution (Layer 3).
#
# Each strategy calls can_trade() before placing any entry order.
# Returns (allowed: bool, reason: str) so the caller can log why it was blocked.
#
# Priority order of checks:
#   1. Clock check (trading hours)
#   2. Session plan check (LOW confidence = blocked until 10 AM)
#   3. Market regime (CLOSED, OPENING)
#   4. Opening range locked
#   5. PCR spike freeze
#   6. PCR extreme values
#   7. Regime vs strategy allowed regimes
#   8. Session planner strategy activation check
#
# Usage in any strategy (e.g. survivor.py):
#   from core.strategy_filter import strategy_filter
#   allowed, reason = strategy_filter.can_trade("survivor")
#   if not allowed:
#       logger.info(f"[survivor] Entry blocked: {reason}")
#       return
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

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ─── Per-strategy regime rules ────────────────────────────────────────────────
STRATEGY_ALLOWED_REGIMES = {
    "survivor":       {REGIME_RANGE},
    "wave_extractor": {REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR},
    "saviour_combo":  {REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR, REGIME_RANGE},
}

# ─── PCR hard limits ──────────────────────────────────────────────────────────
PCR_MAX = 1.7
PCR_MIN = 0.6

# ─── Opening range guard ──────────────────────────────────────────────────────
REQUIRE_OR_LOCKED = True


class StrategyFilter:
    """
    Gate that every strategy must pass before placing an entry.
    Now integrates with session_planner for dynamic regime-based decisions.
    Thread-safe — all reads are from singletons that handle their own locks.
    """

    def can_trade(self, strategy_name: str) -> tuple[bool, str]:
        """
        Main gate check. Call this at the top of any entry signal handler.

        Args:
            strategy_name: one of 'survivor', 'wave_extractor', 'saviour_combo'

        Returns:
            (True, "ok") if entry is allowed
            (False, reason_string) if blocked
        """

        # ── 1. Clock check ────────────────────────────────────────────────
        if not market_context.trade_allowed:
            return False, "outside trading hours (before 9:30 AM or after 3:10 PM)"

        regime = market_context.regime

        if regime == REGIME_OPENING:
            return False, "opening range collection in progress (9:15–9:30 AM)"

        if regime == REGIME_CLOSED:
            return False, "market closed"

        # ── 2. Session planner check ──────────────────────────────────────
        plan = self._get_plan()

        if plan is not None and plan.is_ready:
            # LOW confidence — block all trading until 10 AM re-eval
            if plan.confidence == "LOW":
                now_t = datetime.now(IST).time()
                from datetime import time as dtime
                if now_t < dtime(10, 0):
                    return False, (
                        f"session plan LOW confidence — "
                        f"all strategies blocked until 10:00 AM re-evaluation"
                    )

            # Check if this specific strategy is activated by session plan
            if strategy_name == "survivor" and not plan.survivor_active:
                # Override if live regime has shifted to range intraday
                if regime in (REGIME_RANGE, REGIME_REVERSAL_WATCH):
                    logger.info(
                        f"[strategy_filter] survivor: session plan says BLOCKED "
                        f"but live regime={regime} — OVERRIDING to ALLOW"
                    )
                else:
                    return False, (
                        f"[context] regime={regime} not suitable for survivor (needs: range)"
                    )

            if strategy_name == "wave_extractor" and not plan.wave_active:
                # Override session plan if live regime is now trending
                # Session plan is produced at 9:30 AM — market can shift intraday
                if regime in (REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR):
                    logger.info(
                        f"[strategy_filter] wave_extractor: session plan says BLOCKED "
                        f"but live regime={regime} — OVERRIDING to ALLOW"
                    )
                else:
                    return False, (
                        f"session plan: Wave Extractor BLOCKED "
                        f"(regime={plan.regime}, confidence={plan.confidence})"
                    )

        # ── 3. Opening range must be locked ───────────────────────────────
        if REQUIRE_OR_LOCKED and not market_context.opening_range.is_ready:
            return False, "opening range not yet locked — waiting for 9:30 AM data"

        # ── 4. PCR spike freeze ───────────────────────────────────────────
        if False:  # PCR spike disabled
            pcr = market_context.pcr
            return False, f"PCR spike detected (PCR={pcr:.2f}) — 15-min freeze active"

        # ── 5. PCR extreme values ─────────────────────────────────────────
        pcr = market_context.pcr
        if pcr > PCR_MAX:
            return False, f"PCR={pcr:.2f} > {PCR_MAX} — bullish reversal watch, no new entries"
        if pcr < PCR_MIN:
            return False, f"PCR={pcr:.2f} < {PCR_MIN} — bearish reversal watch, no new entries"

        # ── 6. Regime check ───────────────────────────────────────────────
        if regime == REGIME_REVERSAL_WATCH:
            return False, "regime=reversal_watch — all entries paused"

        allowed_regimes = STRATEGY_ALLOWED_REGIMES.get(strategy_name)
        if allowed_regimes is None:
            logger.warning(
                f"[strategy_filter] Unknown strategy: {strategy_name} — allowing by default"
            )
            return True, "ok (unknown strategy — no regime rule)"

        if regime not in allowed_regimes:
            allowed_str = ", ".join(sorted(allowed_regimes))
            return False, (
                f"regime={regime} not suitable for {strategy_name} "
                f"(needs: {allowed_str})"
            )

        # ── 7. Direction alignment check ──────────────────────────────────
        oi = market_context.oi
        if strategy_name == "wave_extractor" and oi.timestamp is not None:
            if regime == REGIME_TRENDING_BULL and oi.ce_oi_delta > 0:
                logger.info(
                    f"[strategy_filter] wave_extractor note: CE OI still building in bull "
                    f"(Δ{oi.ce_oi_delta:+,}) — trend may not be confirmed yet"
                )
            elif regime == REGIME_TRENDING_BEAR and oi.pe_oi_delta > 0:
                logger.info(
                    f"[strategy_filter] wave_extractor note: PE OI still building in bear "
                    f"(Δ{oi.pe_oi_delta:+,}) — trend may not be confirmed yet"
                )

        # ── All checks passed ─────────────────────────────────────────────
        self._log_context(strategy_name, regime, pcr, plan)
        return True, "ok"

    def get_dynamic_params(self, strategy_name: str) -> dict:
        """
        Returns dynamic parameters from session plan for this strategy.
        Call this when building trade parameters.

        Returns default params if session plan not ready.
        """
        plan = self._get_plan()
        if plan is None or not plan.is_ready:
            return {}

        p = plan.params
        if strategy_name == "survivor":
            return {
                "pe_gap":        p.pe_gap,
                "ce_gap":        p.ce_gap,
                "pe_symbol_gap": p.pe_symbol_gap,
                "ce_symbol_gap": p.ce_symbol_gap,
                "min_premium":   p.min_premium,
                "position_size": p.position_size,
            }
        elif strategy_name == "wave_extractor":
            return {
                "sell_gap":      p.sell_gap,
                "buy_gap":       p.buy_gap,
                "cool_off_time": p.cool_off_time,
                "position_size": p.position_size,
            }
        return {}

    def get_session_risk(self) -> dict:
        """
        Returns dynamic risk limits from session plan.
        risk_manager calls this to get today's limits.
        """
        plan = self._get_plan()
        if plan is None or not plan.is_ready:
            return {
                "daily_loss_limit": -3000.0,
                "max_capital":      150000.0,
            }
        return {
            "daily_loss_limit": plan.daily_loss_limit,
            "max_capital":      plan.max_capital,
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
                "max_capital":     150000.0,
                "notes":           [],
                "produced_at":     None,
                "scores": {},
            }

        s = plan.scores
        return {
            "plan_ready":        plan.is_ready,
            "plan_version":      plan.plan_version,
            "regime":            plan.regime,
            "confidence":        plan.confidence,
            "trending_direction": plan.trending_direction,
            "survivor_active":   plan.survivor_active,
            "wave_active":       plan.wave_active,
            "daily_loss_limit":  plan.daily_loss_limit,
            "max_capital":       plan.max_capital,
            "notes":             plan.notes,
            "produced_at":       plan.produced_at,
            "scores": {
                "range_score":       s.range_score,
                "trending_score":    s.trending_score,
                "or_width":          s.or_width,
                "or_width_signal":   s.or_width_signal,
                "vix_level":         s.vix_level,
                "vix_signal":        s.vix_signal,
                "gap_pct":           s.gap_pct,
                "gap_signal":        s.gap_signal,
                "midpoint_crosses":  s.midpoint_crosses,
                "crosses_signal":    s.crosses_signal,
                "pcr_at_open":       s.pcr_at_open,
                "pcr_signal":        s.pcr_signal,
            },
            "params": {
                "pe_gap":        plan.params.pe_gap,
                "ce_gap":        plan.params.ce_gap,
                "pe_symbol_gap": plan.params.pe_symbol_gap,
                "ce_symbol_gap": plan.params.ce_symbol_gap,
                "min_premium":   plan.params.min_premium,
                "sell_gap":      plan.params.sell_gap,
                "buy_gap":       plan.params.buy_gap,
                "position_size": plan.params.position_size,
            },
        }

    def can_trade_direction(self, strategy_name: str, direction: str) -> tuple[bool, str]:
        """Direction-level check — call after can_trade() passes."""
        regime = market_context.regime
        or_    = market_context.opening_range

        if not or_.is_ready:
            return True, "ok (OR not ready — skipping direction check)"

        spot = market_context.oi.atm_strike
        if spot is None:
            return True, "ok (no spot data — skipping direction check)"

        if regime == REGIME_TRENDING_BULL and direction == "SELL":
            logger.info(
                f"[strategy_filter] {strategy_name}: SELL in trending_bull — "
                f"ensure this is a PE sell, not CE sell"
            )
        if regime == REGIME_TRENDING_BEAR and direction == "BUY":
            logger.info(
                f"[strategy_filter] {strategy_name}: BUY in trending_bear — "
                f"ensure this is a CE buy (hedge), not directional buy"
            )

        return True, "ok"

    def context_summary(self) -> dict:
        """Returns market context snapshot for dashboard API."""
        or_ = market_context.opening_range
        oi  = market_context.oi
        return {
            "regime":        market_context.regime,
            "pcr":           market_context.pcr,
            "pcr_spike":     market_context.is_pcr_spike,
            "trade_allowed": market_context.trade_allowed,
            "or_locked":     or_.locked,
            "or_high":       or_.high,
            "or_low":        or_.low,
            "or_midpoint":   or_.midpoint,
            "total_ce_oi":   oi.total_ce_oi,
            "total_pe_oi":   oi.total_pe_oi,
            "ce_oi_delta":   oi.ce_oi_delta,
            "pe_oi_delta":   oi.pe_oi_delta,
            "atm_strike":    oi.atm_strike,
            "max_pain":      oi.max_pain_strike,
            "oi_updated_at": oi.timestamp.strftime("%H:%M:%S") if oi.timestamp else None,
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

    def _log_context(self, strategy_name: str, regime: str, pcr: float, plan=None):
        or_   = market_context.opening_range
        conf  = plan.confidence if plan and plan.is_ready else "N/A"
        logger.info(
            f"[strategy_filter] {strategy_name} ALLOWED | "
            f"regime={regime} | pcr={pcr:.2f} | "
            f"OR={or_.low}–{or_.high} | "
            f"spike={market_context.is_pcr_spike} | "
            f"plan_confidence={conf}"
        )


# ── Singleton ──────────────────────────────────────────────────────────────────
strategy_filter = StrategyFilter()