# core/risk_manager.py
# Central risk management for all strategies.
# Enforces: max daily loss, per-trade stop loss,
# trailing profit, max trades per day, auto-stop at 3:10 PM
# UPGRADED: Absolute SQLite state reconciliation for crash protection.
# HARDCODED: Max capital deployed at any time = ₹1,50,000

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytz

from core.state_store import StrategyState, state_store
from core.trade_log import trade_logger
from core.transaction_costs import calculate_order_cost

RISK_STATE_FILE = Path("configs/risk_state.json")

logger = logging.getLogger(__name__)

# ─── HARDCODED CAPITAL LIMIT ──────────────────────────────────────────────────
# This is the ABSOLUTE maximum capital that can be deployed at any time.
# This limit is HARDCODED and will NOT change even if account balance increases.
MAX_CAPITAL_DEPLOYED = 250000.0  # ₹2,50,000 — raised for paper trading (Nifty 150k + BankNifty 100k)

# Estimated margin required per lot for options SELL (conservative estimate)
MARGIN_PER_SELL_LOT  = 40000.0  # ₹40,000 per SELL lot
MARGIN_PER_BUY_LOT   = 15000.0  # ₹15,000 per BUY lot (premium only)
PUT_CALENDAR_CAPITAL = 100000.0  # ₹1,00,000 — put_calendar's own independent pool
LOT_SIZE             = 65       # Nifty lot size

# ─── P&L-linked capital base (dynamic, trailing-window) ────────────────────────
# capital_base shrinks after losing days and grows back after profitable days,
# recomputed fresh each day from trailing realised P&L (never carried forward
# incrementally, to avoid the kind of silent drift found in the 31-Jul
# capital-tracking investigation). Bounded by the same floor/ceiling already
# used for the manually-configured per_strategy_cap. Includes paper trades in
# the P&L calc so the mechanism is active and visible during paper testing.
CAPITAL_BASE_DEFAULT       = 150000.0
CAPITAL_BASE_FLOOR         = 50000.0
CAPITAL_BASE_CEILING       = 200000.0
CAPITAL_BASE_LOOKBACK_DAYS = 7   # trailing calendar days (matches weekly-drawdown window)

# Periodic ground-truth capital drift check -- catches bugs like the
# wave_extractor release-on-close gap (31-Jul) automatically instead of
# requiring a manual log-grep investigation. Matches survivor's existing
# mid-session broker reconcile cadence (5 minutes).
CAPITAL_DRIFT_TOLERANCE          = 500.0  # ₹ -- ignore rounding-scale noise, catch real drift
CAPITAL_RECONCILE_INTERVAL_SECONDS = 300  # 5 minutes


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
        max_daily_loss:          float = -3000.0,
        per_trade_loss:          float = -800.0,
        trailing_profit_pct:     float = 25.0,
        trailing_activation_pct: float = 8.0,
        max_trades_per_day:      int   = 3,
        auto_stop_hour:          int   = 15,
        auto_stop_minute:        int   = 10,
        max_weekly_loss:         float = -10000.0,
    ):
        self.max_daily_loss          = max_daily_loss
        self.per_trade_loss          = per_trade_loss
        self.trailing_profit_pct     = trailing_profit_pct       # giveback % once armed
        self.trailing_activation_pct = trailing_activation_pct   # % of premium (+ cost) to arm trailing
        self.max_trades_per_day      = max_trades_per_day
        self.auto_stop_hour          = auto_stop_hour
        self.auto_stop_minute        = auto_stop_minute
        self.max_weekly_loss         = max_weekly_loss

        # Per-strategy trade counters (reset each day)
        self._trade_counts:   dict[str, int]   = {}
        self._pnl_watermarks: dict[str, float] = {}
        # Unconditional MFE (max favourable excursion) tracker -- unlike
        # _pnl_watermarks, this records the peak net P&L for every trade on
        # every tick regardless of whether trailing has armed yet. Read-only
        # bookkeeping for future trailing-threshold tuning; never affects
        # any exit decision. See get_mfe().
        self._mfe_watermarks: dict[str, float] = {}
        # Unconditional MAE (max adverse excursion) tracker -- mirrors
        # _mfe_watermarks but records the trough (most negative net P&L)
        # reached at any point in the trade. Read-only bookkeeping for
        # future stop-loss tuning; never affects any exit decision. See
        # get_mae().
        self._mae_watermarks: dict[str, float] = {}
        self._daily_pnl:      dict[str, float] = {}
        self._system_halted:  bool             = False
        self._halt_reason:    str              = ""
        self._last_reset_day: str              = "1970-01-01"

        # Capital tracking — tracks deployed capital per strategy
        self._deployed_capital: dict[str, float] = {}
        # Per-strategy capital cap — configurable, persisted to configs/capital_config.json
        # capital_base — P&L-linked dynamic ceiling, persisted alongside per_strategy_cap
        self._per_strategy_cap: float
        self._capital_base: float
        self._per_strategy_cap, self._capital_base, self._capital_topup = self._load_capital_config()
        self._last_pool_alert_ts: float = 0.0

        # Periodic ground-truth capital drift reconciliation (background loop)
        self._capital_reconcile_task    = None
        self._capital_reconcile_running = False

        # Per-strategy spam prevention
        self._last_blocked: dict[str, tuple] = {}  # (reason, timestamp)
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

        # Ensure capital_base reflects latest trailing P&L on every startup,
        # not just at the next scheduled daily reset
        self._recompute_capital_base()

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
                # Derive lot multiplier from quantity so overshoot-scaled trades
                # reconcile to the correct margin after a restart, not just 1x.
                _lot_size = 15 if strat == "bn_survivor" else 65
                _qty = trade.get("quantity", _lot_size) or _lot_size
                _multiplier = max(1, int(_qty // _lot_size))
                margin = (MARGIN_PER_SELL_LOT if otype == "SELL" else MARGIN_PER_BUY_LOT) * _multiplier
                
                reconciled_capital[strat] = reconciled_capital.get(strat, 0.0) + margin
                logger.info(f"[RiskManager] Reconstructed open seat: Strategy={strat} | Symbol={trade['symbol']} | Multiplier={_multiplier}x | Reserved=₹{margin:,.0f}")

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
        today_str = now.strftime("%Y-%m-%d")
        if now.weekday() < 5 and today_str != self._last_reset_day:
            self.reset_daily_counts()
            self._last_reset_day = today_str
            logger.info(f"[RiskManager] Auto daily reset completed | date={today_str}")

    # ─── Capital Guard ────────────────────────────────────────────────────────

    def get_total_deployed_capital(self) -> float:
        """Returns total capital currently deployed across all strategies."""
        return sum(self._deployed_capital.values())
    def get_shared_pool_deployed_capital(self) -> float:
        """Same as get_total_deployed_capital(), but excludes put_calendar,
        which has its own fully independent capital pool."""
        return sum(v for k, v in self._deployed_capital.items() if k != "put_calendar")

    def _compute_ground_truth_deployed_capital(self) -> dict:
        """Ground truth: rebuilds deployed capital per strategy directly from
        the database's actual active positions, independent of the
        incrementally-tracked counter. Used by the periodic drift-check loop
        (does not touch reconcile_active_state_from_db, which remains the
        startup-only crash-recovery path)."""
        active_trades = trade_logger.get_active_positions()
        reconciled: dict[str, float] = {}
        for trade in active_trades:
            strat = trade["strategy"]
            otype = trade["order_type"]
            _lot_size = 15 if strat == "bn_survivor" else 65
            _qty = trade.get("quantity", _lot_size) or _lot_size
            _multiplier = max(1, int(_qty // _lot_size))
            margin = (MARGIN_PER_SELL_LOT if otype == "SELL" else MARGIN_PER_BUY_LOT) * _multiplier
            reconciled[strat] = reconciled.get(strat, 0.0) + margin
        return reconciled

    def _reconcile_deployed_capital_drift(self) -> None:
        """Periodic drift check: compares the incrementally-tracked
        _deployed_capital against ground truth computed fresh from active DB
        positions. Self-heals any drift found and logs it loudly, so bugs
        like the wave_extractor release-on-close gap (31-Jul) surface
        automatically instead of requiring a manual log-grep investigation."""
        try:
            ground_truth = self._compute_ground_truth_deployed_capital()
            all_strategies = set(self._deployed_capital) | set(ground_truth)
            drifted = False
            for strat in all_strategies:
                tracked = self._deployed_capital.get(strat, 0.0)
                truth   = ground_truth.get(strat, 0.0)
                if abs(tracked - truth) > CAPITAL_DRIFT_TOLERANCE:
                    drifted = True
                    logger.warning(
                        f"[RiskManager] CAPITAL DRIFT DETECTED | {strat}: "
                        f"tracked=\u20b9{tracked:,.0f} vs ground-truth=\u20b9{truth:,.0f} "
                        f"(diff \u20b9{tracked - truth:,.0f}) -- self-healing to ground truth"
                    )
            if drifted:
                self._deployed_capital = ground_truth
                self._save_state()
            else:
                logger.debug("[RiskManager] Capital reconcile: no drift detected")
        except Exception as e:
            logger.error(f"[RiskManager] Capital drift reconcile failed: {e}")

    async def start_capital_reconcile_loop(self) -> None:
        self._capital_reconcile_running = True
        self._capital_reconcile_task = asyncio.create_task(self._capital_reconcile_loop())
        logger.info(
            f"[RiskManager] Capital drift reconcile loop started "
            f"(every {CAPITAL_RECONCILE_INTERVAL_SECONDS}s)"
        )

    async def stop_capital_reconcile_loop(self) -> None:
        self._capital_reconcile_running = False
        if self._capital_reconcile_task:
            self._capital_reconcile_task.cancel()
            try:
                await self._capital_reconcile_task
            except asyncio.CancelledError:
                pass
        logger.info("[RiskManager] Capital drift reconcile loop stopped")

    async def _capital_reconcile_loop(self) -> None:
        while self._capital_reconcile_running:
            try:
                await asyncio.sleep(CAPITAL_RECONCILE_INTERVAL_SECONDS)
                if self._capital_reconcile_running:
                    self._reconcile_deployed_capital_drift()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[RiskManager] Capital reconcile loop error: {e}")

    def _load_capital_config(self) -> tuple[float, float, float]:
        """Load per-strategy capital cap, P&L-linked capital_base, and manual
        top-up from configs/capital_config.json.
        Returns (per_strategy_cap, capital_base, capital_topup)."""
        cap = 150000.0
        base = CAPITAL_BASE_DEFAULT
        topup = 0.0
        try:
            import json
            cfg_path = Path("configs/capital_config.json")
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text())
                cap = float(data.get("per_strategy_cap", 150000.0))
                base = float(data.get("capital_base", CAPITAL_BASE_DEFAULT))
                topup = float(data.get("capital_topup", 0.0))
                logger.info(
                    f"[RiskManager] Loaded per_strategy_cap: ₹{cap:,.0f} | "
                    f"capital_base: ₹{base:,.0f} | capital_topup: ₹{topup:,.0f}"
                )
        except Exception as e:
            logger.warning(f"[RiskManager] Could not load capital_config.json: {e}")
        return cap, base, topup

    def _save_capital_config(self) -> None:
        """Persist per_strategy_cap, capital_base, and capital_topup together,
        so setting one via the dashboard/Telegram never clobbers the others."""
        try:
            import json
            cfg_path = Path("configs/capital_config.json")
            cfg_path.parent.mkdir(exist_ok=True)
            cfg_path.write_text(json.dumps({
                "per_strategy_cap": self._per_strategy_cap,
                "capital_base":     self._capital_base,
                "capital_topup":    self._capital_topup,
            }, indent=2))
        except Exception as e:
            logger.error(f"[RiskManager] Could not save capital_config.json: {e}")

    def get_effective_total_pool(self) -> float:
        """Total account-wide capital pool available across ALL strategies
        combined: the trailing-P&L-linked capital_base (shrinks after losses,
        grows after profit) plus any manual top-up added via /addcapital."""
        return self._capital_base + self._capital_topup

    def add_capital(self, amount: float) -> float:
        """Manually add capital to the account pool (e.g. via the /addcapital
        Telegram command or dashboard). Persists immediately and returns the
        new total pool value."""
        amount = float(amount)
        self._capital_topup += amount
        self._save_capital_config()
        new_total = self.get_effective_total_pool()
        logger.info(
            f"[RiskManager] Capital top-up: +₹{amount:,.0f} -> total pool now ₹{new_total:,.0f}"
        )
        return new_total

    def _maybe_alert_pool_low(self, deployed: float, pool: float) -> None:
        """Throttled Telegram alert when the account-wide pool (not a single
        strategy) is the reason a trade was blocked. 15-min cooldown so it
        doesn't spam on every tick while the pool stays exhausted."""
        import time as _time
        now = _time.monotonic()
        if now - self._last_pool_alert_ts > 900:
            self._last_pool_alert_ts = now
            try:
                from core.alerting import alert_capital_pool_low
                alert_capital_pool_low(deployed, pool)
            except Exception as e:
                logger.warning(f"[RiskManager] Could not send capital pool alert: {e}")

    def set_per_strategy_cap(self, new_cap: float) -> None:
        """Update per-strategy capital cap and persist to disk."""
        new_cap = max(50000.0, min(200000.0, float(new_cap)))
        self._per_strategy_cap = new_cap
        self._save_capital_config()
        logger.info(f"[RiskManager] per_strategy_cap updated to ₹{new_cap:,.0f} and saved")

    def get_per_strategy_cap(self) -> float:
        return self._per_strategy_cap

    def get_capital_base(self) -> float:
        return self._capital_base

    def get_effective_per_strategy_cap(self) -> float:
        """The actual limit used by check_capital_limit — the more conservative
        of the manually-configured per_strategy_cap and the P&L-linked capital_base.
        A manual dashboard cap always acts as a hard ceiling; capital_base can only
        pull it tighter after losses or restore it back up toward that ceiling
        after profits, never past it."""
        return min(self._per_strategy_cap, self._capital_base)

    def _recompute_capital_base(self) -> None:
        """Recompute capital_base fresh from trailing realised P&L (live + paper).
        Deliberately recomputed from the database each time, not carried forward
        incrementally, so it can't silently drift the way _deployed_capital did
        (see 31-Jul capital-tracking investigation)."""
        try:
            import sqlite3
            from datetime import timedelta
            cutoff = (datetime.now(pytz.timezone("Asia/Kolkata")).date()
                      - timedelta(days=CAPITAL_BASE_LOOKBACK_DAYS)).isoformat()
            with sqlite3.connect(trade_logger.db_path) as conn:
                row = conn.execute(
                    "SELECT SUM(realised_pnl) as total FROM trades "
                    "WHERE status='CLOSED' AND exit_time >= ?",
                    (cutoff,)
                ).fetchone()
            trailing_pnl = row[0] if row and row[0] is not None else 0.0
            new_base = max(CAPITAL_BASE_FLOOR, min(CAPITAL_BASE_CEILING, CAPITAL_BASE_DEFAULT + trailing_pnl))
            old_base = self._capital_base
            self._capital_base = new_base
            self._save_capital_config()
            logger.info(
                f"[RiskManager] capital_base recomputed | trailing {CAPITAL_BASE_LOOKBACK_DAYS}d P&L: ₹{trailing_pnl:,.0f} "
                f"| ₹{old_base:,.0f} -> ₹{new_base:,.0f}"
            )
        except Exception as e:
            logger.warning(f"[RiskManager] Could not recompute capital_base: {e}")

    def check_capital_limit(self, order_type: str = "SELL", strategy_name: str = "", multiplier: int = 1) -> tuple[bool, str]:
        """
        HARDCODED CAPITAL GUARD — checks if adding one more trade
        would exceed the ₹1,50,000 capital limit.
        multiplier: number of lots this trade represents (e.g. overshoot scaling
        in survivor.py). Default 1 preserves existing behavior for all callers
        that don't pass it explicitly.
        Returns (True, "") if capital is available.
        Returns (False, reason) if limit would be breached.
        """
        margin_needed = (MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT) * multiplier

        if strategy_name == "put_calendar":
            strategy_deployed = self._deployed_capital.get(strategy_name, 0.0)
            projected = strategy_deployed + margin_needed
            if projected > PUT_CALENDAR_CAPITAL:
                reason = (
                    f"put_calendar capital limit breach — deployed: ₹{strategy_deployed:,.0f} + "
                    f"new: ₹{margin_needed:,.0f} = ₹{projected:,.0f} "
                    f"exceeds its independent cap of ₹{PUT_CALENDAR_CAPITAL:,.0f}"
                )
                logger.info(f"[RiskManager] CAPITAL GUARD: {reason}")
                return False, reason
            return True, ""

        PER_STRATEGY_CAP = self.get_effective_per_strategy_cap()
        strategy_deployed = self._deployed_capital.get(strategy_name, 0.0)
        projected = strategy_deployed + margin_needed
        if projected > PER_STRATEGY_CAP:
            reason = (
                f"Capital limit breach — {strategy_name} deployed: ₹{strategy_deployed:,.0f} + "
                f"new: ₹{margin_needed:,.0f} = ₹{projected:,.0f} "
                f"exceeds per-strategy cap of ₹{PER_STRATEGY_CAP:,.0f}"
            )
            logger.info(f"[RiskManager] CAPITAL GUARD: {reason}")
            return False, reason

        total_pool     = self.get_effective_total_pool()
        total_deployed = self.get_shared_pool_deployed_capital()
        projected_total = total_deployed + margin_needed
        if projected_total > total_pool:
            reason = (
                f"TOTAL POOL exhausted — account deployed: ₹{total_deployed:,.0f} + "
                f"new: ₹{margin_needed:,.0f} = ₹{projected_total:,.0f} "
                f"exceeds total capital pool of ₹{total_pool:,.0f}. "
                f"Add capital with /addcapital <amount>."
            )
            logger.info(f"[RiskManager] CAPITAL GUARD: {reason}")
            self._maybe_alert_pool_low(total_deployed, total_pool)
            return False, reason

        return True, ""

    def register_capital(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        """Reserve capital when a new trade is opened. multiplier = lots this trade represents."""
        margin = (MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT) * multiplier
        self._deployed_capital[strategy_name] = (
            self._deployed_capital.get(strategy_name, 0.0) + margin
        )
        total = self.get_total_deployed_capital()
        logger.debug(
            f"[RiskManager] Capital deployed | {strategy_name}: "
            f"+₹{margin:,.0f} | Total: ₹{total:,.0f} / ₹{MAX_CAPITAL_DEPLOYED:,.0f}"
        )

    def release_capital(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        """Release capital when a trade is closed. multiplier = lots this trade represented."""
        margin  = (MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT) * multiplier
        current = self._deployed_capital.get(strategy_name, 0.0)
        self._deployed_capital[strategy_name] = max(0.0, current - margin)
        total = self.get_total_deployed_capital()
        logger.debug(
            f"[RiskManager] Capital released | {strategy_name}: "
            f"-₹{margin:,.0f} | Total: ₹{total:,.0f} / ₹{MAX_CAPITAL_DEPLOYED:,.0f}"
        )
        self._save_state()

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
            logger.info(f"[RiskManager] Could not read session plan daily loss limit: {e}")
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
            logger.info(f"[RiskManager] Could not read session plan max capital: {e}")
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

    def is_trading_blocked(self, strategy_name: str = "") -> tuple[bool, str]:
        """Single pre-trade gate. Returns (blocked: bool, reason: str).
        Checks in priority order: system halted → auto-stop time → VIX halt.
        Strategies call this once at the top of on_tick and bail early if blocked.

        put_calendar is fully exempt (own capital pool, own weekly cycle,
        own SL/exit rules) -- including circuit breaker and VIX halt, per
        explicit decision to run it fully independently of shared risk gates.
        """
        if strategy_name == "put_calendar":
            return False, ""
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

        # put_calendar: fully independent of shared halt/auto-stop/daily-loss
        # checks (weekly strategy, own capital pool, own SL rules). Only
        # capital_limit and its own strategy logic govern its entries/exits.
        # It can still be stopped manually via /api/strategy/put_calendar/stop.
        if strategy_name != "put_calendar":
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

        # ── Early-warning buffer ─────────────────────────────────────────
        # Blocks NEW entries once we're within striking distance of the
        # hardcoded -3000 floor, rather than only blocking after it's
        # already been breached. put_calendar is excluded -- it runs on
        # its own weekly cycle, not this daily reset, so it shouldn't be
        # blocked by same-day losses from the other strategies.
        _EARLY_WARNING_FLOOR = -2500.0
        if strategy_name != "put_calendar":
            _summary = state_store.get_global_summary()
            _today_pnl = _summary.get("total_pnl", 0.0)
            if _today_pnl <= _EARLY_WARNING_FLOOR:
                reason = (
                    f"Early-warning buffer: today's P&L ₹{_today_pnl:.2f} "
                    f"has crossed ₹{_EARLY_WARNING_FLOOR:.2f} -- blocking new "
                    f"entries to stay clear of the ₹{self.max_daily_loss:.2f} hard floor"
                )
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

        # Count as detected opportunity
        self._opp_detected[strategy_name] = self._opp_detected.get(strategy_name, 0) + 1
        return True, ""

    def _log_blocked_once(self, strategy_name: str, reason: str) -> None:
        """Only logs 'trade blocked' if reason changed — prevents tick spam."""
        import time as _time
        last_reason, last_ts = self._last_blocked.get(strategy_name, (None, 0))
        now = _time.monotonic()
        if last_reason != reason or (now - last_ts) > 60:
            logger.info(f"[RiskManager] {strategy_name} blocked: {reason}")
            self._last_blocked[strategy_name] = (reason, now)
        # Always count block (deduplicated by reason change for logging, but count every tick)
        self._opp_blocked[strategy_name] = self._opp_blocked.get(strategy_name, 0) + 1
        # Track reason breakdown
        if strategy_name not in self._block_reasons:
            self._block_reasons[strategy_name] = {}
        short = reason.split("—")[0].split(":")[0].strip()[:40]
        self._block_reasons[strategy_name][short] = self._block_reasons[strategy_name].get(short, 0) + 1

    def register_trade(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        self._opp_executed[strategy_name] = self._opp_executed.get(strategy_name, 0) + 1
        """Call this when a new trade is opened. multiplier = lots this trade represents."""
        self._trade_counts[strategy_name] = (
            self._trade_counts.get(strategy_name, 0) + 1
        )
        self.register_capital(strategy_name, order_type, multiplier)
        self._last_blocked.pop(strategy_name, None)
        logger.debug(
            f"[RiskManager] {strategy_name} trade count: "
            f"{self._trade_counts[strategy_name]}/{self.max_trades_per_day}"
        )
        self._save_state()  # Persist after every trade

    def release_trade(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        """Call this when a trade is closed to free up capital. multiplier = lots this trade represented."""
        self.release_capital(strategy_name, order_type, multiplier)

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
        self._recompute_capital_base()
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
                "last_reset_day":  self._last_reset_day,
            }
            RISK_STATE_FILE.parent.mkdir(exist_ok=True)
            # Atomic write: write to a temp file first, then rename over the
            # real file. os.replace() is atomic on POSIX, so a process kill
            # mid-write (e.g. PM2 restart/SIGKILL) can never leave risk_state.json
            # truncated or corrupted -- readers always see either the old
            # complete file or the new complete file, never a partial one.
            tmp_path = RISK_STATE_FILE.with_suffix(".json.tmp")
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, RISK_STATE_FILE)
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
            self._last_reset_day   = state.get("last_reset_day", "1970-01-01")
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
                            "WHERE status='CLOSED' AND DATE(exit_time)=?",
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
            logger.debug(
                f"[RiskManager] State restored from disk | "
                f"trades={self._trade_counts} | halted={self._system_halted}"
            )
        except Exception as e:
            logger.warning(f"[RiskManager] Failed to load state: {e}")

    # ─── Trade-level checks ───────────────────────────────────────────────────

    def check_trade_stop_loss(
        self,
        entry_price:         float,
        current_price:       float,
        quantity:            int,
        order_type:          str   = "SELL",
        sl_multiplier:       float = 1.5,
        hedge_entry_price:   float = 0.0,
        hedge_current_price: float = 0.0,
        hedge_quantity:      int   = 0,
    ) -> bool:
        """
        Returns True if this trade has hit the per-trade stop loss.

        SL is premium-proportional: triggers when loss >= entry_premium * sl_multiplier * qty.
        Floored at per_trade_loss (default -₹800) for cheap options,
        capped at -₹2,500 to prevent runaway loss on high-premium entries.

        For SELL trades: loss occurs when price goes UP.
        For BUY trades: loss occurs when price goes DOWN.

        If hedge_entry_price/hedge_current_price are provided (hedge is always
        a BUY leg), the hedge's P&L is netted against the main leg before
        comparing to the SL floor -- so a hedge that fails to offset the main
        leg's loss (e.g. deep-OTM hedge decaying on expiry day) triggers an
        exit at the intended net-loss cap, instead of only checking the main
        leg in isolation.
        """
        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity

        hedge_pnl = 0.0
        if hedge_entry_price > 0.0 and hedge_current_price > 0.0:
            hq = hedge_quantity or quantity
            hedge_pnl = (hedge_current_price - hedge_entry_price) * hq

        net_pnl = pnl + hedge_pnl

        # Dynamic SL: proportional to premium collected, bounded by floor/cap
        dynamic_sl  = -(entry_price * sl_multiplier * quantity)
        effective_sl = max(self.per_trade_loss, min(dynamic_sl, -2500.0))

        if net_pnl <= effective_sl:
            logger.warning(
                f"[RiskManager] Stop loss hit | "
                f"Entry: {entry_price} | Current: {current_price} | "
                f"Main P&L: ₹{pnl:.2f} | Hedge P&L: ₹{hedge_pnl:.2f} | "
                f"Net P&L: ₹{net_pnl:.2f} | Effective SL: ₹{effective_sl:.2f} "
                f"(dynamic={dynamic_sl:.0f}, floor={self.per_trade_loss})"
            )
            return True
        return False

    def check_trailing_profit(
        self,
        entry_price:          float,
        current_price:        float,
        order_type:            str   = "SELL",
        quantity:              int   = 65,
        fixed_target:          float = 0.0,
        trade_id:              str   = "",
        hedge_entry_price:     float = 0.0,
        hedge_current_price:   float = 0.0,
        hedge_quantity:        int   = 0,
        hedge_entry_cost:      float = 0.0,
    ) -> bool:
        """
        Two-stage exit logic, cost-aware and hedge-aware:
        1. FIXED TARGET: exit when net P&L >= 40% of premium collected
        2. HIGH WATERMARK TRAILING STOP: once net P&L exceeds
           (trailing_activation_pct% of premium + cost_of_trade), track peak
           P&L and exit if it retraces below max(cost_of_trade, giveback%
           of peak). The floor can never sit below cost_of_trade -- once
           trailing is armed, the trade is guaranteed to close at worst
           breakeven on real cost, never a manufactured loss from giveback
           alone eating into a gain that hadn't even covered its own costs.

        Hedge-aware: if hedge_entry_price/hedge_current_price/hedge_quantity
        are provided, hedge P&L is netted into pnl before any comparison --
        matching check_trade_stop_loss's existing behaviour. cost_of_trade
        also includes the hedge leg: hedge_entry_cost is reused from what
        was already computed once at trade open (avoids recomputing against
        a possibly-stale current hedge premium), plus an estimated hedge
        exit cost (hedge is always BUY-to-open, SELL-to-close).
        """
        if entry_price <= 0:
            return False
        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity

        hedge_pnl = 0.0
        if hedge_entry_price > 0.0 and hedge_current_price > 0.0:
            hq = hedge_quantity or quantity
            hedge_pnl = (hedge_current_price - hedge_entry_price) * hq

        net_pnl = pnl + hedge_pnl

        # ── Unconditional MFE tracking (bookkeeping only, no effect on the
        # exit decision below) -- records the true peak net P&L reached at
        # any point in the trade, even before trailing has armed. This is
        # what future trailing-threshold tuning should read, since
        # _pnl_watermarks only starts once the activation threshold is hit.
        if trade_id:
            prev_mfe = self._mfe_watermarks.get(trade_id, float("-inf"))
            if net_pnl > prev_mfe:
                self._mfe_watermarks[trade_id] = net_pnl
            prev_mae = self._mae_watermarks.get(trade_id, float("inf"))
            if net_pnl < prev_mae:
                self._mae_watermarks[trade_id] = net_pnl

        # ── Real round-trip cost of trade: main leg + hedge leg ────────────
        entry_side = order_type
        exit_side  = "BUY" if order_type == "SELL" else "SELL"
        cost_of_trade = (
            calculate_order_cost(entry_price, quantity, entry_side) +
            calculate_order_cost(current_price, quantity, exit_side)
        )
        if hedge_quantity > 0:
            hedge_exit_cost = calculate_order_cost(hedge_current_price, hedge_quantity, "SELL")
            cost_of_trade += hedge_entry_cost + hedge_exit_cost

        premium_collected = entry_price * quantity
        if fixed_target > 0:
            profit_target = fixed_target
        else:
            profit_target = round(premium_collected * 0.40, 2)

        # ── Stage 1: Fixed target ─────────────────────────────────────────
        if net_pnl >= profit_target:
            if trade_id and trade_id in self._pnl_watermarks:
                del self._pnl_watermarks[trade_id]
            logger.debug(
                f"[RiskManager] Fixed profit target hit | "
                f"Entry: {entry_price} | Current: {current_price} | "
                f"Net P&L: Rs{net_pnl:.2f} | Target: Rs{profit_target}"
            )
            return True

        # ── Stage 2: High watermark trailing stop (cost-aware) ─────────────
        activation_threshold = round(
            premium_collected * (self.trailing_activation_pct / 100.0) + cost_of_trade, 2
        )
        if trade_id:
            if net_pnl >= activation_threshold:
                prev_peak = self._pnl_watermarks.get(trade_id, 0.0)
                if net_pnl > prev_peak:
                    self._pnl_watermarks[trade_id] = net_pnl
                    logger.debug(
                        f"[RiskManager] Watermark updated | trade={trade_id} | "
                        f"peak=Rs{net_pnl:.2f} | cost_of_trade=Rs{cost_of_trade:.2f} | "
                        f"activation_threshold=Rs{activation_threshold:.2f}"
                    )

            # Once armed, the floor check must run on EVERY subsequent tick,
            # regardless of whether THIS tick's net_pnl still clears the
            # activation threshold. Previously this whole block was nested
            # inside "if net_pnl >= activation_threshold", so a single fast
            # adverse tick that dropped net_pnl straight past the (lower)
            # activation line -- skipping the (higher) giveback floor
            # entirely -- silently fell through to return False, handing
            # the trade to the plain stop-loss check with no memory that it
            # had ever been trailing.
            if trade_id in self._pnl_watermarks:
                peak = self._pnl_watermarks[trade_id]
                giveback_floor = round(peak * (self.trailing_profit_pct / 100.0), 2)
                floor = max(cost_of_trade, giveback_floor)
                if net_pnl < floor:
                    logger.warning(
                        f"[RiskManager] Trailing stop triggered | trade={trade_id} | "
                        f"peak=Rs{peak:.2f} | floor=Rs{floor:.2f} "
                        f"(giveback=Rs{giveback_floor:.2f}, cost_floor=Rs{cost_of_trade:.2f}) | "
                        f"current=Rs{net_pnl:.2f}"
                    )
                    del self._pnl_watermarks[trade_id]
                    return True

        return False

    def get_watermark(self, trade_id: str) -> float:
        """Returns current peak net P&L for a trade, or 0.0 if trailing
        hasn't armed yet. Exposed so callers can persist peak_pnl to the DB
        on trade close, for future MFE-based backtesting."""
        return self._pnl_watermarks.get(trade_id, 0.0)

    def get_trailing_status(
        self,
        entry_price:          float,
        current_price:        float,
        order_type:            str   = "SELL",
        quantity:              int   = 65,
        trade_id:              str   = "",
        hedge_entry_price:     float = 0.0,
        hedge_current_price:   float = 0.0,
        hedge_quantity:        int   = 0,
        hedge_entry_cost:      float = 0.0,
    ) -> dict:
        """
        Read-only snapshot of trailing state for a trade, for dashboard
        display. Mirrors the exact cost/hedge/activation/floor math used in
        check_trailing_profit -- so the dashboard can never show numbers
        that disagree with what the live trading logic is actually doing --
        but never mutates _pnl_watermarks. Safe to call every tick.
        """
        empty = {
            "net_pnl": 0.0, "cost_of_trade": 0.0, "peak_pnl": 0.0,
            "trailing_armed": False, "trailing_floor": 0.0,
            "activation_threshold": 0.0,
        }
        if entry_price <= 0:
            return empty

        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity

        hedge_pnl = 0.0
        if hedge_entry_price > 0.0 and hedge_current_price > 0.0:
            hq = hedge_quantity or quantity
            hedge_pnl = (hedge_current_price - hedge_entry_price) * hq

        net_pnl = pnl + hedge_pnl

        entry_side = order_type
        exit_side  = "BUY" if order_type == "SELL" else "SELL"
        cost_of_trade = (
            calculate_order_cost(entry_price, quantity, entry_side) +
            calculate_order_cost(current_price, quantity, exit_side)
        )
        if hedge_quantity > 0:
            hedge_exit_cost = calculate_order_cost(hedge_current_price, hedge_quantity, "SELL")
            cost_of_trade += hedge_entry_cost + hedge_exit_cost

        premium_collected = entry_price * quantity
        activation_threshold = round(
            premium_collected * (self.trailing_activation_pct / 100.0) + cost_of_trade, 2
        )

        peak  = self._pnl_watermarks.get(trade_id, 0.0)
        armed = trade_id in self._pnl_watermarks
        if armed:
            giveback_floor = round(peak * (self.trailing_profit_pct / 100.0), 2)
            trailing_floor = max(cost_of_trade, giveback_floor)
        else:
            # Not armed yet -- this is what the floor WOULD be if it armed
            # right now, informative only.
            trailing_floor = cost_of_trade

        return {
            "net_pnl":              round(net_pnl, 2),
            "cost_of_trade":        round(cost_of_trade, 2),
            "peak_pnl":             round(peak, 2),
            "trailing_armed":       armed,
            "trailing_floor":       round(trailing_floor, 2),
            "activation_threshold": activation_threshold,
        }

    def get_mfe(self, trade_id: str) -> float:
        """Returns the peak net P&L (Rs) ever reached by this trade, tracked
        unconditionally on every tick regardless of trailing-armed state.
        Call this at trade close, before clear_watermark(), to persist the
        real MFE for future trailing-threshold tuning. Returns 0.0 if the
        trade_id was never seen (e.g. trailing wasn't called for it)."""
        mfe = self._mfe_watermarks.get(trade_id, float("-inf"))
        return mfe if mfe != float("-inf") else 0.0

    def get_mae(self, trade_id: str) -> float:
        """Returns the trough net P&L (Rs) ever reached by this trade (most
        negative point), tracked unconditionally on every tick regardless of
        trailing-armed state. Call this at trade close, before
        clear_watermark(), to persist the real MAE for future stop-loss
        tuning. Returns 0.0 if the trade_id was never seen."""
        mae = self._mae_watermarks.get(trade_id, float("inf"))
        return mae if mae != float("inf") else 0.0

    def record_mfe_mae(
        self,
        entry_price:          float,
        current_price:        float,
        order_type:            str   = "SELL",
        quantity:              int   = 65,
        trade_id:              str   = "",
        hedge_entry_price:     float = 0.0,
        hedge_current_price:   float = 0.0,
        hedge_quantity:        int   = 0,
    ) -> None:
        """
        Unconditionally records this tick's net P&L into the MFE/MAE
        trackers, independent of check_trade_stop_loss and
        check_trailing_profit. Call this FIRST in the monitoring loop,
        before either exit check -- previously, when check_trade_stop_loss
        fired and the loop did `continue`, that tick's
        check_trailing_profit call (where MFE/MAE updates lived) never ran,
        so the exact tick that triggered a stop-loss exit was silently
        missed, understating trough_pnl on SL exits. Safe to call every
        tick; pure bookkeeping, never affects any exit decision.
        """
        if not trade_id or entry_price <= 0:
            return
        if order_type == "SELL":
            pnl = (entry_price - current_price) * quantity
        else:
            pnl = (current_price - entry_price) * quantity
        hedge_pnl = 0.0
        if hedge_entry_price > 0.0 and hedge_current_price > 0.0:
            hq = hedge_quantity or quantity
            hedge_pnl = (hedge_current_price - hedge_entry_price) * hq
        net_pnl = pnl + hedge_pnl
        prev_mfe = self._mfe_watermarks.get(trade_id, float("-inf"))
        if net_pnl > prev_mfe:
            self._mfe_watermarks[trade_id] = net_pnl
        prev_mae = self._mae_watermarks.get(trade_id, float("inf"))
        if net_pnl < prev_mae:
            self._mae_watermarks[trade_id] = net_pnl

    def clear_watermark(self, trade_id: str) -> None:
        """Call on trade close to clean up watermark state. Read get_mfe()
        and get_mae() BEFORE calling this, since it also clears both
        trackers."""
        self._pnl_watermarks.pop(trade_id, None)
        self._mfe_watermarks.pop(trade_id, None)
        self._mae_watermarks.pop(trade_id, None)


# ─── Global singleton ─────────────────────────────────────────────────────────
risk_manager = RiskManager(
    max_daily_loss          = -3000.0,
    per_trade_loss          = -800.0,
    trailing_profit_pct     = 50.0,   # giveback % once trailing is armed
    trailing_activation_pct = 8.0,    # % of premium (+ cost_of_trade) to arm trailing
    max_trades_per_day      = 3,
    auto_stop_hour          = 15,
    auto_stop_minute        = 10,
)
# TP now 40% of premium collected — see check_trailing_profit()
# trailing_activation_pct=8.0 is a starting estimate from the one real MFE
# data point available (Aug 3 wave_extractor trade peaked at ~8.4% before
# reversing to a stop-loss). Revisit once peak_pnl is persisted and a real
# backtest across many trades is possible.