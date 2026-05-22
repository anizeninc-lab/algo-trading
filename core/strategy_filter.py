# core/strategy_filter.py
# Layer 2 — Strategy Filter Engine
# Sits between market_context (Layer 1) and strategy execution (Layer 3).
# Each strategy calls can_trade() before placing any entry order.
# Returns (allowed: bool, reason: str) so the caller can log why it was blocked.
#
# Usage in any strategy (e.g. survivor.py):
#   from core.strategy_filter import strategy_filter
#   allowed, reason = strategy_filter.can_trade("survivor")
#   if not allowed:
#       logger.info(f"[survivor] Entry blocked: {reason}")
#       return

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
# Each strategy has a set of regimes where it is ALLOWED to trade.
# If current regime is not in the allowed set → blocked.
#
# Survivor    — mean-reversion, works in range-bound markets
# Wave        — trend-following, needs a clear directional move
# Saviour     — breakout combo, works in trending + range (not reversals)

STRATEGY_ALLOWED_REGIMES = {
    "survivor":      {REGIME_RANGE},
    "wave_extractor": {REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR},
    "saviour_combo": {REGIME_TRENDING_BULL, REGIME_TRENDING_BEAR, REGIME_RANGE},
}

# ─── PCR hard limits ─────────────────────────────────────────────────────────
# These override regime rules — if PCR is extreme, no strategy trades.
PCR_MAX = 1.3   # Above this → bullish reversal likely → block all entries
PCR_MIN = 0.7   # Below this → bearish reversal likely → block all entries

# ─── Opening range guard ─────────────────────────────────────────────────────
# No entries until opening range is locked (after 9:30 AM)
REQUIRE_OR_LOCKED = True


class StrategyFilter:
    """
    Gate that every strategy must pass before placing an entry.
    Reads live state from market_context singleton.
    Thread-safe — all reads are from market_context which handles its own lock.
    """

    def can_trade(self, strategy_name: str) -> tuple[bool, str]:
        """
        Main gate check. Call this at the top of any entry signal handler.

        Args:
            strategy_name: one of 'survivor', 'wave_extractor', 'saviour_combo'

        Returns:
            (True, "ok") if entry is allowed
            (False, reason_string) if blocked — log the reason
        """
        # ── 1. Clock checks ───────────────────────────────────────────────
        if not market_context.trade_allowed:
            return False, "outside trading hours (before 9:30 AM or after 3:10 PM)"

        regime = market_context.regime

        if regime == REGIME_OPENING:
            return False, "opening range collection in progress (9:15–9:30 AM)"

        if regime == REGIME_CLOSED:
            return False, "market closed"

        # ── 2. Opening range must be locked ───────────────────────────────
        if REQUIRE_OR_LOCKED and not market_context.opening_range.is_ready:
            return False, "opening range not yet locked — waiting for 9:30 AM data"

        # ── 3. PCR spike freeze ───────────────────────────────────────────
        if market_context.is_pcr_spike:
            pcr = market_context.pcr
            return False, f"PCR spike detected (PCR={pcr:.2f}) — 15-min freeze active"

        # ── 4. PCR extreme values ─────────────────────────────────────────
        pcr = market_context.pcr
        if pcr > PCR_MAX:
            return False, f"PCR={pcr:.2f} > {PCR_MAX} — bullish reversal watch, no new entries"
        if pcr < PCR_MIN:
            return False, f"PCR={pcr:.2f} < {PCR_MIN} — bearish reversal watch, no new entries"

        # ── 5. Regime check ───────────────────────────────────────────────
        if regime == REGIME_REVERSAL_WATCH:
            return False, f"regime=reversal_watch — all entries paused"

        allowed_regimes = STRATEGY_ALLOWED_REGIMES.get(strategy_name)
        if allowed_regimes is None:
            logger.warning(f"[strategy_filter] Unknown strategy: {strategy_name} — allowing by default")
            return True, "ok (unknown strategy — no regime rule)"

        if regime not in allowed_regimes:
            allowed_str = ", ".join(sorted(allowed_regimes))
            return False, (
                f"regime={regime} not suitable for {strategy_name} "
                f"(needs: {allowed_str})"
            )

        # ── 6. Direction alignment check (optional, for trending regimes) ──
        # For wave_extractor: only take CE sells in trending_bear, PE sells in trending_bull
        # This is a soft log — doesn't block, just warns
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
        self._log_context(strategy_name, regime, pcr)
        return True, "ok"

    def can_trade_direction(self, strategy_name: str, direction: str) -> tuple[bool, str]:
        """
        Additional direction-level check for strategies that need it.
        direction: 'BUY' or 'SELL'

        For example: in trending_bull, only allow PE sells (not CE sells).
        Call this after can_trade() passes.
        """
        regime = market_context.regime
        or_ = market_context.opening_range

        if not or_.is_ready:
            return True, "ok (OR not ready — skipping direction check)"

        spot = market_context.oi.atm_strike  # approximate
        if spot is None:
            return True, "ok (no spot data — skipping direction check)"

        # Only allow trades in the direction of the opening range break
        if regime == REGIME_TRENDING_BULL and direction == "SELL":
            # In bull trend: selling CEs is risky — only allow PE sells
            # (This is a soft advisory — strategies can override if they have specific logic)
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
        """
        Returns a dict snapshot of current context — useful for dashboard API
        and for logging at the start of each strategy cycle.
        """
        or_ = market_context.opening_range
        oi  = market_context.oi
        return {
            "regime":         market_context.regime,
            "pcr":            market_context.pcr,
            "pcr_spike":      market_context.is_pcr_spike,
            "trade_allowed":  market_context.trade_allowed,
            "or_locked":      or_.locked,
            "or_high":        or_.high,
            "or_low":         or_.low,
            "or_midpoint":    or_.midpoint,
            "total_ce_oi":    oi.total_ce_oi,
            "total_pe_oi":    oi.total_pe_oi,
            "ce_oi_delta":    oi.ce_oi_delta,
            "pe_oi_delta":    oi.pe_oi_delta,
            "atm_strike":     oi.atm_strike,
            "max_pain":       oi.max_pain_strike,
            "oi_updated_at":  oi.timestamp.strftime("%H:%M:%S") if oi.timestamp else None,
        }

    def _log_context(self, strategy_name: str, regime: str, pcr: float):
        or_ = market_context.opening_range
        logger.info(
            f"[strategy_filter] {strategy_name} ALLOWED | "
            f"regime={regime} | pcr={pcr:.2f} | "
            f"OR={or_.low}–{or_.high} | "
            f"spike={market_context.is_pcr_spike}"
        )


# ── Singleton ─────────────────────────────────────────────────────────────────
strategy_filter = StrategyFilter()
