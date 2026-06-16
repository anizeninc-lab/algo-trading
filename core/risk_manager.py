# core/risk_manager.py
# Central risk management for all strategies.
# Enforces: max daily loss, per-trade stop loss,
# trailing profit, max trades per day, auto-stop at 3:10 PM
# UPGRADED: Absolute SQLite state reconciliation for crash protection.
# HARDCODED: Max capital deployed at any time = ₹1,50,000

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytz

from core.state_store import StrategyState, state_store
from core.trade_log import trade_logger

RISK_STATE_FILE = Path("configs/risk_state.json")

logger = logging.getLogger(__name__)

# ─── HARDCODED CAPITAL LIMIT ──────────────────────────────────────────────────
# This is the ABSOLUTE maximum capital that can be deployed at any time.
# This limit is HARDCODED and will NOT change even if account balance increases.
MAX_CAPITAL_DEPLOYED = 150000.0  # ₹1,50,000 — DO NOT CHANGE

# Estimated margin required per lot for options SELL (conservative estimate)
MARGIN_PER_SELL_LOT  = 40000.0  # ₹40,000 per SELL lot
MARGIN_PER_BUY_LOT   = 15000.0  # ₹15,000 per BUY lot (premium only)
LOT_SIZE             = 65       # Nifty lot size


class RiskManager:
    """
    Single source of truth for all risk rules.
    Each strategy calls check_* methods before and after trades.

    Capital Guard (HARDCODED):
    - Maximum capital deployed at any time: ₹1,50,000
    - This applies regardless of account balance
    - Each SELL trade reserves ₹40,000
    - Each BUY trade reserves ₹15,000
    """

    def __init__(
        self,
        max_daily_loss:      float = -3000.0,
        per_trade_loss:      float = -800.0,
        trailing_profit_pct: float = 25.0,
        max_trades_per_day:  int   = 3,
        auto_stop_hour:      int   = 15,
        auto_stop_minute:    int   = 10,
    ):
        self.max_daily_loss      = max_daily_loss
        self.per_trade_loss      = per_trade_loss
        self.trailing_profit_pct = trailing_profit_pct
        self.max_trades_per_day  = max_trades_per_day
        self.auto_stop_hour      = auto_stop_hour
        self.auto_stop_minute    = auto_stop_minute

        # Per-strategy trade counters (reset each day)
        self._trade_counts:   dict[str, int]   = {}
        self._daily_pnl:      dict[str, float] = {}
        self._system_halted:  bool             = False
        self._halt_reason:    str              = ""
        self._last_reset_day: int              = -1

        # Capital tracking — tracks deployed capital per strategy
        self._deployed_capital: dict[str, float] = {}

        # Per-strategy spam prevention
        self._last_blocked: dict[str, str] = {}
        
        # Restore state file sequence
        self._load_state()
        
        # CRITICAL ADDITION: Run Database Position Reconciliation for absolute safety
        self.reconcile_active_state_from_db()

    # ─── Crash & Database Reconciliation ──────────────────────────────────────

    def reconcile_active_state_from_db(self) -> None:
        """
        ANTI-CRASH RECOVERY GATEWAY:
        Queries the SQLite database directly on initialization to determine if there
        are active trades left open from a prior runtime instance or an unplanned PM2 restart.
        Rebuilds active internal state parameters seamlessly.
        """
        try:
            active_trades = trade_logger.get_active_positions()
            if not active_trades:
                logger.info("[RiskManager] Database reconciliation complete: No hanging open positions detected.")
                # Clear out any stale/ghost volatile capital trackers
                self._deployed_capital = {}
                return

            logger.warning(f"[RiskManager] CRASH INTERCEPTED! Found {len(active_trades)} hanging active trades in database. Rebuilding state maps...")
            
            # Recalculate capital parameters directly from live rows
            reconciled_capital = {}
            for trade in active_trades:
                strat = trade["strategy"]
                otype = trade["order_type"]
                margin = MARGIN_PER_SELL_LOT if otype == "SELL" else MARGIN_PER_BUY_LOT
                
                reconciled_capital[strat] = reconciled_capital.get(strat, 0.0) + margin
                logger.info(f"[RiskManager] Reconstructed open seat: Strategy={strat} | Symbol={trade['symbol']} | Reserved=₹{margin:,.0f}")

            # Atomically re-anchor volatile trackers with the source-of-truth database snapshot
            self._deployed_capital = reconciled_capital
                
            logger.info(f"[RiskManager] Reconciliation complete. Deployed capital successfully re-anchored to: ₹{self.get_total_deployed_capital():,.0f}")
            self._save_state()
        except Exception as e:
            logger.critical(f"[RiskManager] FATAL ERROR during live database state reconciliation: {e}")

    # ─── Daily Reset ──────────────────────────────────────────────────────────

    def _auto_reset_if_new_day(self) -> None:
        """Automatically reset counters at the start of each trading day."""
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        if now.weekday() < 5 and now.day != self._last_reset_day:
            self.reset_daily_counts()
            self._last_reset_day = now.day
            logger.info("[RiskManager] Auto daily reset completed")

    # ─── Capital Guard ────────────────────────────────────────────────────────

    def get_total_deployed_capital(self) -> float:
        """Returns total capital currently deployed across all strategies."""
        return sum(self._deployed_capital.values())

    def check_capital_limit(self, order_type: str = "SELL") -> tuple[bool, str]:
        """
        HARDCODED CAPITAL GUARD — checks if adding one more trade
        would exceed the ₹1,50,000 capital limit.
        Returns (True, "") if capital is available.
        Returns (False, reason) if limit would be breached.
        """
        margin_needed = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT
        current       = self.get_total_deployed_capital()
        projected     = current + margin_needed

        if projected > MAX_CAPITAL_DEPLOYED:
            reason = (
                f"Capital limit breach — deployed: ₹{current:,.0f} + "
                f"new: ₹{margin_needed:,.0f} = ₹{projected:,.0f} "
                f"exceeds hardcoded limit of ₹{MAX_CAPITAL_DEPLOYED:,.0f}"
            )
            logger.warning(f"[RiskManager] CAPITAL GUARD: {reason}")
            return False, reason

        return True, ""

    def register_capital(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Reserve capital when a new trade is opened."""
        margin = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT
        self._deployed_capital[strategy_name] = (
            self._deployed_capital.get(strategy_name, 0.0) + margin
        )
        total = self.get_total_deployed_capital()
        logger.info(
            f"[RiskManager] Capital deployed | {strategy_name}: "
            f"+₹{margin:,.0f} | Total: ₹{total:,.0f} / ₹{MAX_CAPITAL_DEPLOYED:,.0f}"
        )

    def release_capital(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Release capital when a trade is closed."""
        margin  = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT
        current = self._deployed_capital.get(strategy_name, 0.0)
        self._deployed_capital[strategy_name] = max(0.0, current - margin)
        total = self.get_total_deployed_capital()
        logger.info(
            f"[RiskManager] Capital released | {strategy_name}: "
            f"-₹{margin:,.0f} | Total: ₹{total:,.0f} / ₹{MAX_CAPITAL_DEPLOYED:,.0f}"
        )

    # ─── System-level checks ─────────────────────────────────────────────────

    def is_halted(self) -> bool:
        return self._system_halted

    def check_auto_stop(self) -> bool:
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        if now.hour > self.auto_stop_hour:
            return True
        if now.hour == self.auto_stop_hour and now.minute >= self.auto_stop_minute:
            return True
        return False

    def check_max_daily_loss(self) -> bool:
        if self._system_halted:
            return True

        summary   = state_store.get_global_summary()
        total_pnl = summary.get("total_pnl", 0.0)

        if total_pnl <= self.max_daily_loss:
            self._system_halted = True
            self._halt_reason   = f"Max daily loss hit: ₹{total_pnl:.2f}"
            logger.warning(f"[RiskManager] SYSTEM HALTED — {self._halt_reason}")
            self._save_state()  # Persist halted state immediately
            return True

        return False

    # ─── Strategy-level checks ────────────────────────────────────────────────

    def can_trade(self, strategy_name: str, order_type: str = "SELL") -> tuple[bool, str]:
        """
        Returns (True, "") if strategy can place a new trade.
        Returns (False, reason) if blocked.
        Includes HARDCODED capital limit check.
        Suppresses repeated identical log spam.
        """
        self._auto_reset_if_new_day()

        if self._system_halted:
            reason = f"System halted: {self._halt_reason}"
            self._log_blocked_once(strategy_name, reason)
            return False, reason

        if self.check_auto_stop():
            reason = "Auto-stop time reached (3:10 PM)"
            self._log_blocked_once(strategy_name, reason)
            return False, reason

        if self.check_max_daily_loss():
            reason = "Max daily loss breached"
            self._log_blocked_once(strategy_name, reason)
            return False, reason

        count = self._trade_counts.get(strategy_name, 0)
        if count >= self.max_trades_per_day:
            reason = f"Max trades per day reached ({self.max_trades_per_day})"
            self._log_blocked_once(strategy_name, reason)
            return False, reason

        # HARDCODED CAPITAL GUARD — always checked last
        capital_ok, capital_reason = self.check_capital_limit(order_type)
        if not capital_ok:
            self._log_blocked_once(strategy_name, capital_reason)
            return False, capital_reason

        # Clear last blocked if now allowed
        self._last_blocked.pop(strategy_name, None)
        return True, ""

    def _log_blocked_once(self, strategy_name: str, reason: str) -> None:
        """Only logs 'trade blocked' if reason changed — prevents tick spam."""
        if self._last_blocked.get(strategy_name) != reason:
            logger.info(f"[RiskManager] {strategy_name} blocked: {reason}")
            self._last_blocked[strategy_name] = reason

    def register_trade(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Call this when a new trade is opened."""
        self._trade_counts[strategy_name] = (
            self._trade_counts.get(strategy_name, 0) + 1
        )
        self.register_capital(strategy_name, order_type)
        self._last_blocked.pop(strategy_name, None)
        logger.info(
            f"[RiskManager] {strategy_name} trade count: "
            f"{self._trade_counts[strategy_name]}/{self.max_trades_per_day}"
        )
        self._save_state()  # Persist after every trade

    def release_trade(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Call this when a trade is closed to free up capital."""
        self.release_capital(strategy_name, order_type)

    def reset_daily_counts(self) -> None:
        """Call this at market open each day to reset counters."""
        self._trade_counts      = {}
        self._daily_pnl         = {}
        self._deployed_capital  = {}
        self._system_halted     = False
        self._halt_reason       = ""
        self._last_blocked      = {}
        logger.info("[RiskManager] Daily counters reset")
        self._save_state()

    def _save_state(self) -> None:
        """Persist daily state to disk so restarts don't lose counters."""
        try:
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            state = {
                "date":            now.strftime("%Y-%m-%d"),
                "trade_counts":    self._trade_counts,
                "daily_pnl":       self._daily_pnl,
                "system_halted":   self._system_halted,
                "halt_reason":     self._halt_reason,
                "deployed_capital": self._deployed_capital,
            }
            RISK_STATE_FILE.parent.mkdir(exist_ok=True)
            with open(RISK_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"[RiskManager] Failed to save state: {e}")

    def _load_state(self) -> None:
        """Reload persisted state on startup — prevents counter reset after crash."""
        try:
            if not RISK_STATE_FILE.exists():
                return
            with open(RISK_STATE_FILE) as f:
                state = json.load(f)
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            today = now.strftime("%Y-%m-%d")
            if state.get("date") != today:
                logger.info("[RiskManager] State file is from previous day — ignoring")
                return
            self._trade_counts     = state.get("trade_counts", {})
            self._daily_pnl        = state.get("daily_pnl", {})
            self._system_halted    = state.get("system_halted", False)
            self._halt_reason      = state.get("halt_reason", "")
            self._deployed_capital = state.get("deployed_capital", {})
            logger.info(
                f"[RiskManager] State restored from disk | "
                f"trades={self._trade_counts} | halted={self._system_halted}"
            )
        except Exception as e:
            logger.warning(f"[RiskManager] Failed to load state: {e}")

    # ─── Trade-level checks ───────────────────────────────────────────────────

    def check_trade_stop_loss(
        self,
        entry_price:   float,
        current_price: float,
        quantity:      int,
        order_type:    str = "SELL",
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
        self,
        entry_price:   float,
        current_price: float,
        order_type:    str = "SELL",
        quantity:      int = 65,
    ) -> bool:
        """
        Close when profit >= profit_target (default Rs800 per trade).
        """
        if entry_price <= 0:
            return False
        profit_target = getattr(self, "profit_target", 800.0)
        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity
        if pnl >= profit_target:
            logger.info(
                f"[RiskManager] Profit target hit | "
                f"Entry: {entry_price} | Current: {current_price} | "
                f"P&L: Rs{pnl:.2f} | Target: Rs{profit_target}"
            )
            return True
        return False


# ─── Global singleton ─────────────────────────────────────────────────────────
risk_manager = RiskManager(
    max_daily_loss      = -3000.0,
    per_trade_loss      = -800.0,
    trailing_profit_pct = 50.0,
    max_trades_per_day  = 3,
    auto_stop_hour      = 15,
    auto_stop_minute    = 10,
)
risk_manager.profit_target = 600.0  # Close trade when profit >= Rs600