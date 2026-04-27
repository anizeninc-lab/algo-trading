# core/risk_manager.py
# Central risk management for all strategies.
# Enforces: max daily loss, per-trade stop loss,
# trailing profit, max trades per day, auto-stop at 3:10 PM

import asyncio
import logging
from datetime import datetime

from core.state_store import StrategyState, state_store
from core.trade_log import trade_logger

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Single source of truth for all risk rules.
    Each strategy calls check_* methods before and after trades.
    """

    def __init__(
        self,
        max_daily_loss: float = -5000.0,
        per_trade_loss: float = -3000.0,
        trailing_profit_pct: float = 25.0,
        max_trades_per_day: int = 4,
        auto_stop_hour: int = 15,
        auto_stop_minute: int = 10,
    ):
        self.max_daily_loss = max_daily_loss
        self.per_trade_loss = per_trade_loss
        self.trailing_profit_pct = trailing_profit_pct
        self.max_trades_per_day = max_trades_per_day
        self.auto_stop_hour = auto_stop_hour
        self.auto_stop_minute = auto_stop_minute

        # Per-strategy trade counters (reset each day)
        self._trade_counts: dict[str, int] = {}
        self._daily_pnl: dict[str, float] = {}
        self._system_halted: bool = False
        self._halt_reason: str = ""

    # ─── System-level checks ─────────────────────────────────────────────────

    def is_halted(self) -> bool:
        """Returns True if system has been halted due to risk breach."""
        return self._system_halted

    def check_auto_stop(self) -> bool:
        """
        Returns True if current time is past auto-stop time (3:10 PM IST).
        Strategies should call this on every tick.
        """
        now = datetime.now()
        if now.hour > self.auto_stop_hour:
            return True
        if now.hour == self.auto_stop_hour and now.minute >= self.auto_stop_minute:
            return True
        return False

    def check_max_daily_loss(self) -> bool:
        """
        Returns True if total P&L across all strategies
        has breached the max daily loss limit.
        """
        if self._system_halted:
            return True

        summary = state_store.get_global_summary()
        total_pnl = summary.get("total_pnl", 0.0)

        if total_pnl <= self.max_daily_loss:
            self._system_halted = True
            self._halt_reason = f"Max daily loss hit: ₹{total_pnl:.2f}"
            logger.warning(f"[RiskManager] SYSTEM HALTED — {self._halt_reason}")
            return True

        return False

    # ─── Strategy-level checks ────────────────────────────────────────────────

    def can_trade(self, strategy_name: str) -> tuple[bool, str]:
        """
        Returns (True, "") if strategy can place a new trade.
        Returns (False, reason) if blocked.
        """
        if self._system_halted:
            return False, f"System halted: {self._halt_reason}"

        if self.check_auto_stop():
            return False, "Auto-stop time reached (3:10 PM)"

        if self.check_max_daily_loss():
            return False, "Max daily loss breached"

        count = self._trade_counts.get(strategy_name, 0)
        if count >= self.max_trades_per_day:
            return False, f"Max trades per day reached ({self.max_trades_per_day})"

        return True, ""

    def register_trade(self, strategy_name: str) -> None:
        """Call this when a new trade is opened."""
        self._trade_counts[strategy_name] = self._trade_counts.get(strategy_name, 0) + 1
        logger.info(
            f"[RiskManager] {strategy_name} trade count: "
            f"{self._trade_counts[strategy_name]}/{self.max_trades_per_day}"
        )

    def reset_daily_counts(self) -> None:
        """Call this at market open each day to reset counters."""
        self._trade_counts = {}
        self._daily_pnl = {}
        self._system_halted = False
        self._halt_reason = ""
        logger.info("[RiskManager] Daily counters reset")

    # ─── Trade-level checks ───────────────────────────────────────────────────

    def check_trade_stop_loss(
        self,
        entry_price: float,
        current_price: float,
        quantity: int,
        order_type: str = "SELL",
    ) -> bool:
        """
        Returns True if this trade has hit the per-trade stop loss.
        For SELL trades: loss occurs when price goes UP.
        For BUY trades: loss occurs when price goes DOWN.
        """
        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity

        if pnl <= self.per_trade_loss:
            logger.warning(
                f"[RiskManager] Stop loss hit | "
                f"Entry: {entry_price} | Current: {current_price} | "
                f"P&L: ₹{pnl:.2f}"
            )
            return True
        return False

    def check_trailing_profit(
        self, entry_price: float, current_price: float, order_type: str = "SELL"
    ) -> bool:
        """
        Returns True if trailing profit target is hit.
        For SELL trades: profit when price decays by trailing_profit_pct% from entry.
        e.g. entry=100, trailing=25% → close when price <= 75
        """
        if entry_price <= 0:
            return False

        if order_type == "SELL":
            target = entry_price * (1 - self.trailing_profit_pct / 100)
            if current_price <= target:
                logger.info(
                    f"[RiskManager] Trailing profit hit | "
                    f"Entry: {entry_price} | Target: {target:.2f} | "
                    f"Current: {current_price}"
                )
                return True
        return False


# ─── Global singleton ─────────────────────────────────────────────────────────
risk_manager = RiskManager(
    max_daily_loss=-5000.0,
    per_trade_loss=-3000.0,
    trailing_profit_pct=25.0,
    max_trades_per_day=4,
    auto_stop_hour=15,
    auto_stop_minute=10,
)
