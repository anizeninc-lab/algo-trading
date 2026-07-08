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
MAX_CAPITAL_DEPLOYED = 250000.0  # ₹2,50,000 — raised for paper trading (Nifty 150k + BankNifty 100k)

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
        max_weekly_loss:     float = -10000.0,
    ):
        self.max_daily_loss      = max_daily_loss
        self.per_trade_loss      = per_trade_loss
        self.trailing_profit_pct = trailing_profit_pct
        self.max_trades_per_day  = max_trades_per_day
        self.auto_stop_hour      = auto_stop_hour
        self.auto_stop_minute    = auto_stop_minute
        self.max_weekly_loss     = max_weekly_loss

        # Per-strategy trade counters (reset each day)
        self._trade_counts:   dict[str, int]   = {}
        self._daily_pnl:      dict[str, float] = {}
        self._system_halted:  bool             = False
        self._halt_reason:    str              = ""
        self._last_reset_day: int              = -1

        # Capital tracking — tracks deployed capital per strategy
        self._deployed_capital: dict[str, float] = {}
        # Per-strategy capital cap — configurable, persisted to configs/capital_config.json
        self._per_strategy_cap: float = self._load_capital_config()

        # Per-strategy spam prevention
        self._last_blocked: dict[str, str] = {}
        # Opportunity tracking
        self._opp_detected:  dict[str, int] = {}  # signals that passed all filters
        self._opp_blocked:   dict[str, int] = {}  # signals blocked (any reason)
        self._opp_executed:  dict[str, int] = {}  # actual trades placed
        self._block_reasons: dict[str, dict] = {} # reason -> count
        # API circuit breaker
        self._api_fail_times: list  = []   # timestamps of recent failures
        self._api_cb_tripped: bool  = False
        self._api_cb_tripped_at: float = 0.0
        self._CB_WINDOW:    int   = 60    # seconds — sliding window
        self._CB_THRESHOLD: int   = 5     # failures in window to trip
        self._CB_RESET:     int   = 300   # seconds before auto-reset
        
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

    def _load_capital_config(self) -> float:
        """Load per-strategy capital cap from configs/capital_config.json."""
        try:
            import json
            cfg_path = Path("configs/capital_config.json")
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text())
                cap = float(data.get("per_strategy_cap", 150000.0))
                logger.info(f"[RiskManager] Loaded per_strategy_cap: ₹{cap:,.0f}")
                return cap
        except Exception as e:
            logger.warning(f"[RiskManager] Could not load capital_config.json: {e}")
        return 150000.0

    def set_per_strategy_cap(self, new_cap: float) -> None:
        """Update per-strategy capital cap and persist to disk."""
        new_cap = max(50000.0, min(200000.0, float(new_cap)))
        self._per_strategy_cap = new_cap
        try:
            import json
            cfg_path = Path("configs/capital_config.json")
            cfg_path.parent.mkdir(exist_ok=True)
            cfg_path.write_text(json.dumps({"per_strategy_cap": new_cap}, indent=2))
            logger.info(f"[RiskManager] per_strategy_cap updated to ₹{new_cap:,.0f} and saved")
        except Exception as e:
            logger.error(f"[RiskManager] Could not save capital_config.json: {e}")

    def get_per_strategy_cap(self) -> float:
        return self._per_strategy_cap

    def check_capital_limit(self, order_type: str = "SELL", strategy_name: str = "") -> tuple[bool, str]:
        """
        HARDCODED CAPITAL GUARD — checks if adding one more trade
        would exceed the ₹1,50,000 capital limit.
        Returns (True, "") if capital is available.
        Returns (False, reason) if limit would be breached.
        """
        margin_needed = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT
        PER_STRATEGY_CAP = self._per_strategy_cap
        strategy_deployed = self._deployed_capital.get(strategy_name, 0.0)
        projected = strategy_deployed + margin_needed
        if projected > PER_STRATEGY_CAP:
            reason = (
                f"Capital limit breach — {strategy_name} deployed: ₹{strategy_deployed:,.0f} + "
                f"new: ₹{margin_needed:,.0f} = ₹{projected:,.0f} "
                f"exceeds per-strategy cap of ₹{PER_STRATEGY_CAP:,.0f}"
            )
            logger.debug(f"[RiskManager] CAPITAL GUARD: {reason}")
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

    # ─── Dynamic risk wiring (session_planner) ──────────────────────────────
    # Session planner can only TIGHTEN these limits, never loosen them beyond
    # the hardcoded safety floor/ceiling set in this file.

    def _get_effective_daily_loss_limit(self) -> float:
        """
        Returns the more conservative of: the hardcoded -3000 floor, and
        session_planner's dynamic daily_loss_limit for today. Since both
        values are negative, max() picks whichever is closer to zero (tighter).
        """
        try:
            from core.session_planner import session_planner
            plan = session_planner.current_plan
            if plan.is_ready:
                effective = max(self.max_daily_loss, plan.daily_loss_limit)
                if effective != self.max_daily_loss:
                    logger.debug(
                        f"[RiskManager] Daily loss limit tightened by session plan: "
                        f"₹{self.max_daily_loss:.0f} -> ₹{effective:.0f}"
                    )
                return effective
        except Exception as e:
            logger.debug(f"[RiskManager] Could not read session plan daily loss limit: {e}")
        return self.max_daily_loss

    def _get_effective_max_capital(self) -> float:
        """
        Returns the more conservative of: the hardcoded 150000 ceiling, and
        session_planner's dynamic max_capital for today. min() ensures the
        hardcoded ceiling is NEVER exceeded, only ever reduced further.
        """
        try:
            from core.session_planner import session_planner
            plan = session_planner.current_plan
            if plan.is_ready:
                effective = min(MAX_CAPITAL_DEPLOYED, plan.max_capital)
                if effective != MAX_CAPITAL_DEPLOYED:
                    logger.debug(
                        f"[RiskManager] Max capital tightened by session plan: "
                        f"₹{MAX_CAPITAL_DEPLOYED:.0f} -> ₹{effective:.0f}"
                    )
                return effective
        except Exception as e:
            logger.debug(f"[RiskManager] Could not read session plan max capital: {e}")
        return MAX_CAPITAL_DEPLOYED

    # ─── System-level checks ─────────────────────────────────────────────────

    def is_halted(self) -> bool:
        return self._system_halted

    def check_weekly_drawdown(self) -> bool:
        """Check rolling 5-day P&L from trade DB. Halts if total exceeds max_weekly_loss.
        Uses closed trade realised_pnl — no extra persistence needed (#9).
        """
        # One-time session override — set WEEKLY_LOSS_OVERRIDE=1 in env to skip check
        if os.getenv('WEEKLY_LOSS_OVERRIDE', '0') == '1':
            return False
        if self._system_halted:
            return True
        try:
            from core.trade_log import trade_logger as _tl
            from datetime import date, timedelta
            import sqlite3
            # Last 5 calendar days (covers Mon-Fri week)
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            with sqlite3.connect(_tl.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT SUM(realised_pnl) as total FROM trades "
                    "WHERE status='CLOSED' AND exit_time >= ? AND paper_trade = 0",
                    (cutoff,)
                ).fetchone()
            weekly_pnl = rows["total"] if rows and rows["total"] is not None else 0.0
            if weekly_pnl <= self.max_weekly_loss:
                self._system_halted = True
                self._halt_reason   = (
                    f"Weekly drawdown limit hit: ₹{weekly_pnl:.2f} "
                    f"(limit: ₹{self.max_weekly_loss:.2f}, last 7 calendar days)"
                )
                logger.warning(f"[RiskManager] SYSTEM HALTED — {self._halt_reason}")
                self._save_state()
                return True
        except Exception as e:
            logger.warning(f"[RiskManager] Weekly drawdown check failed: {e}")
        return False

    # ── API Circuit Breaker ───────────────────────────────────────────────────
    def record_api_failure(self) -> None:
        """Call after any broker API error. Trips circuit breaker if threshold exceeded."""
        import time as _time
        now = _time.time()
        # Prune old failures outside the sliding window
        self._api_fail_times = [t for t in self._api_fail_times if now - t < self._CB_WINDOW]
        self._api_fail_times.append(now)
        if len(self._api_fail_times) >= self._CB_THRESHOLD and not self._api_cb_tripped:
            self._api_cb_tripped    = True
            self._api_cb_tripped_at = now
            self._system_halted     = True
            self._halt_reason       = (
                f"API circuit breaker tripped: {len(self._api_fail_times)} failures "
                f"in {self._CB_WINDOW}s window — trading halted for {self._CB_RESET}s"
            )
            logger.warning(f"[RiskManager] CIRCUIT BREAKER TRIPPED — {self._halt_reason}")
            self._save_state()
            try:
                from core.alerting import alert_risk_breach
                alert_risk_breach(self._halt_reason)
            except Exception:
                pass

    def record_api_success(self) -> None:
        """Call after a successful broker API call. Resets failure streak."""
        import time as _time
        # Auto-reset circuit breaker after CB_RESET seconds
        if self._api_cb_tripped:
            if _time.time() - self._api_cb_tripped_at >= self._CB_RESET:
                self._api_cb_tripped  = False
                self._api_fail_times  = []
                self._system_halted   = False
                self._halt_reason     = ""
                logger.info("[RiskManager] API circuit breaker auto-reset after cooldown")
                self._save_state()
        else:
            # Clear recent failures on success to avoid stale counts
            self._api_fail_times = []

    def check_api_circuit_breaker(self) -> tuple[bool, str]:
        """Returns (tripped, reason). Auto-resets after CB_RESET seconds."""
        import time as _time
        if self._api_cb_tripped:
            elapsed = _time.time() - self._api_cb_tripped_at
            if elapsed >= self._CB_RESET:
                self.record_api_success()  # triggers reset
                return False, ""
            return True, self._halt_reason
        return False, ""

    def is_trading_blocked(self) -> tuple[bool, str]:
        """Single pre-trade gate. Returns (blocked: bool, reason: str).
        Checks in priority order: system halted → auto-stop time → VIX halt.
        Strategies call this once at the top of on_tick and bail early if blocked.
        """
        if self._system_halted:
            return True, self._halt_reason or "System halted"
        _cb_tripped, _cb_reason = self.check_api_circuit_breaker()
        if _cb_tripped:
            return True, _cb_reason
        if self.check_weekly_drawdown():
            return True, self._halt_reason
        if self.check_auto_stop():
            return True, "Auto-stop time reached (3:10 PM)"
        # VIX halt — import here to avoid circular import at module level
        from core.vix_manager import vix_manager as _vm
        if _vm.get_params().get("halt_trading", False):
            return True, "VIX EXTREME — trading halted by vix_manager"
        return False, ""

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
        effective_limit = self._get_effective_daily_loss_limit()

        if total_pnl <= effective_limit:
            self._system_halted = True
            self._halt_reason   = (
                f"Max daily loss hit: ₹{total_pnl:.2f} "
                f"(limit: ₹{effective_limit:.2f}, hardcoded floor: ₹{self.max_daily_loss:.2f})"
            )
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
        capital_ok, capital_reason = self.check_capital_limit(order_type, strategy_name)
        if not capital_ok:
            self._log_blocked_once(strategy_name, capital_reason)
            return False, capital_reason

        # Clear last blocked if now allowed
        self._last_blocked.pop(strategy_name, None)
        # Count as detected opportunity
        self._opp_detected[strategy_name] = self._opp_detected.get(strategy_name, 0) + 1
        return True, ""

    def _log_blocked_once(self, strategy_name: str, reason: str) -> None:
        """Only logs 'trade blocked' if reason changed — prevents tick spam."""
        if self._last_blocked.get(strategy_name) != reason:
            logger.info(f"[RiskManager] {strategy_name} blocked: {reason}")
            self._last_blocked[strategy_name] = reason
        # Always count block (deduplicated by reason change for logging, but count every tick)
        self._opp_blocked[strategy_name] = self._opp_blocked.get(strategy_name, 0) + 1
        # Track reason breakdown
        if strategy_name not in self._block_reasons:
            self._block_reasons[strategy_name] = {}
        short = reason.split("—")[0].split(":")[0].strip()[:40]
        self._block_reasons[strategy_name][short] = self._block_reasons[strategy_name].get(short, 0) + 1

    def register_trade(self, strategy_name: str, order_type: str = "SELL") -> None:
        self._opp_executed[strategy_name] = self._opp_executed.get(strategy_name, 0) + 1
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
        self._opp_detected      = {}
        self._opp_executed      = {}
        self._opp_blocked       = {}
        self._block_reasons     = {}
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
            # Re-validate halt against today's actual DB P&L — don't blindly restore halted=True
            persisted_halt   = state.get("system_halted", False)
            persisted_reason = state.get("halt_reason", "")
            weekly_override  = os.getenv("WEEKLY_LOSS_OVERRIDE", "0") == "1"
            if persisted_halt and not weekly_override and "KILL SWITCH" not in persisted_reason:
                # Re-check today's realised P&L from DB before restoring halt
                try:
                    import sqlite3 as _sq
                    from core.trade_log import trade_logger as _tl2
                    _today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
                    with _sq.connect(_tl2.db_path) as _conn:
                        _row = _conn.execute(
                            "SELECT SUM(realised_pnl) as total FROM trades "
                            "WHERE status='CLOSED' AND DATE(exit_time)=? AND paper_trade=0",
                            (_today,)
                        ).fetchone()
                    _today_pnl = _row[0] if _row and _row[0] is not None else 0.0
                    if _today_pnl > self.max_daily_loss:
                        persisted_halt = False
                        persisted_reason = ""
                        logger.info(f"[RiskManager] Halt cleared on startup — today P&L ₹{_today_pnl:.2f} within limit")
                except Exception as _ve:
                    logger.warning(f"[RiskManager] Could not re-validate halt on startup: {_ve}")
            self._system_halted    = persisted_halt and not weekly_override
            self._halt_reason      = "" if weekly_override else persisted_reason
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
        order_type:    str   = "SELL",
        sl_multiplier: float = 1.5,
    ) -> bool:
        """
        Returns True if this trade has hit the per-trade stop loss.

        SL is premium-proportional: triggers when loss >= entry_premium * sl_multiplier * qty.
        Floored at per_trade_loss (default -₹800) for cheap options,
        capped at -₹2,500 to prevent runaway loss on high-premium entries.

        For SELL trades: loss occurs when price goes UP.
        For BUY trades: loss occurs when price goes DOWN.
        """
        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity

        # Dynamic SL: proportional to premium collected, bounded by floor/cap
        dynamic_sl  = -(entry_price * sl_multiplier * quantity)
        effective_sl = max(self.per_trade_loss, min(dynamic_sl, -2500.0))

        if pnl <= effective_sl:
            logger.warning(
                f"[RiskManager] Stop loss hit | "
                f"Entry: {entry_price} | Current: {current_price} | "
                f"P&L: ₹{pnl:.2f} | Effective SL: ₹{effective_sl:.2f} "
                f"(dynamic={dynamic_sl:.0f}, floor={self.per_trade_loss})"
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
        Close when profit >= 40% of premium collected.
        e.g. entry Rs20 x 65 qty = Rs1300 collected -> TP at Rs520 (40%)
        """
        if entry_price <= 0:
            return False
        profit_target = round(entry_price * quantity * 0.40, 2)
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
# TP now 40% of premium collected — see check_trailing_profit()