# core/research/data_archive.py
# ═══════════════════════════════════════════════════════════════════════════════
# Step 2 of the research/self-improvement layer roadmap.
#
# Passively archives data the live bot already computes, into a SEPARATE
# database (research_archive.db) from trade_log.db. This is the prerequisite
# for everything downstream (backtest_engine, pattern_discovery, etc.) —
# nothing can be replayed or analyzed historically if it was never recorded.
#
# Design principles (deliberate, matching the rest of this codebase's
# philosophy of protecting live execution/risk from research code):
#   - Runs as a daemon background thread inside the main bot process, reading
#     shared in-memory state. It NEVER writes to or imports from trade_log.py,
#     risk_manager.py, or any strategy file, and none of those modules import
#     this one — the dependency is one-directional (archiver reads them).
#   - Every read of live state and every DB write is wrapped in try/except.
#     A failure here must never be able to raise into the bot's main loop or
#     affect a trading decision. Worst case: an archive gap, not a bot crash.
#   - Own SQLite file (research_archive.db), separate from trade_log.db, so
#     archival writes can never contend with or corrupt the trade ledger that
#     risk_manager and execution depend on.
#   - Candle grain only, not raw ticks — matches what regime_engine.py and
#     gex_ema_stack.py actually consume. Tick-level archiving (e.g. for
#     slippage modeling) is a separate future decision, not silently bundled.
#   - Capital/Greeks are periodic snapshots (default every 5 min), not
#     logged on every tick — both are cheap to recompute from existing live
#     state, so snapshotting captures "how was risk postured through the
#     day" without meaningfully increasing write volume.
# ═══════════════════════════════════════════════════════════════════════════════

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

DB_PATH = Path(__file__).parent.parent.parent / "research_archive.db"

DEFAULT_SNAPSHOT_INTERVAL_SEC = 300   # capital + greeks snapshot cadence
DEFAULT_CANDLE_POLL_SEC = 60          # how often we check for new finalized candles


class DataArchiver:
    """
    Background archiver. Construct once in main.py after market_context,
    risk_manager, vix_manager, session_planner, and the SaviourCombo instance
    all exist, then call .start(). Call .stop() on shutdown.

    All dependencies are passed in explicitly rather than imported at module
    level, so this file can be unit-tested with fakes/mocks without needing
    a running bot.
    """

    def __init__(
        self,
        market_context,
        risk_manager,
        vix_manager,
        session_planner,
        state_store,
        combo,                      # SaviourCombo instance, for open trades / greeks
        db_path: Path = DB_PATH,
        snapshot_interval_sec: int = DEFAULT_SNAPSHOT_INTERVAL_SEC,
        candle_poll_sec: int = DEFAULT_CANDLE_POLL_SEC,
    ):
        self.market_context = market_context
        self.risk_manager = risk_manager
        self.vix_manager = vix_manager
        self.session_planner = session_planner
        self.state_store = state_store
        self.combo = combo

        self.db_path = db_path
        self.snapshot_interval_sec = snapshot_interval_sec
        self.candle_poll_sec = candle_poll_sec

        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._last_snapshot_ts = 0.0
        self._last_candle_poll_ts = 0.0
        self._last_archived_candle_key: Optional[str] = None
        self._last_archived_option_candle_key: Optional[str] = None
        self._last_session_plan_version = 0

        self._init_db()

    # ── Schema ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS candles_1min (
                        ts       TEXT NOT NULL,
                        symbol   TEXT NOT NULL,
                        open     REAL NOT NULL,
                        high     REAL NOT NULL,
                        low      REAL NOT NULL,
                        close    REAL NOT NULL,
                        PRIMARY KEY (ts, symbol)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS capital_snapshots (
                        ts        TEXT NOT NULL,
                        strategy  TEXT NOT NULL,
                        deployed  REAL NOT NULL,
                        cap       REAL NOT NULL,
                        pct       REAL NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS greeks_snapshots (
                        ts           TEXT NOT NULL PRIMARY KEY,
                        spot         REAL,
                        vix          REAL,
                        total_delta  REAL,
                        total_gamma  REAL,
                        total_theta  REAL,
                        total_vega   REAL,
                        trade_count  INTEGER
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_plans (
                        ts                  TEXT NOT NULL,
                        plan_version        INTEGER NOT NULL,
                        regime               TEXT,
                        confidence          TEXT,
                        trending_direction  TEXT,
                        daily_loss_limit    REAL,
                        max_capital         REAL,
                        survivor_active     INTEGER,
                        wave_active         INTEGER,
                        raw_json            TEXT,
                        PRIMARY KEY (ts, plan_version)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gex_snapshots (
                        ts       TEXT NOT NULL PRIMARY KEY,
                        net_gex  REAL,
                        regime   TEXT,
                        spot     REAL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts ON candles_1min(symbol, ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_capital_strategy_ts ON capital_snapshots(strategy, ts)")
                conn.commit()
            logger.info(f"[DataArchiver] DB ready at {self.db_path}")
        except Exception as e:
            logger.error(f"[DataArchiver] Failed to init schema: {e}")

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("[DataArchiver] Already running")
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="data-archiver", daemon=True
        )
        self._thread.start()
        logger.info("[DataArchiver] Started")

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[DataArchiver] Stopped")

    def _run_loop(self) -> None:
        # Small sleep granularity so stop() is responsive; actual archiving
        # is gated by the interval checks inside each _maybe_* method.
        while not self._stop_flag.is_set():
            try:
                self._maybe_archive_candles()
                self._maybe_archive_option_candles()
                self._maybe_snapshot_capital_and_greeks()
                self._maybe_archive_session_plan()
            except Exception as e:
                # Belt-and-suspenders: _maybe_* methods already catch their
                # own errors, but the loop itself must never die.
                logger.error(f"[DataArchiver] Loop error: {e}")
            time.sleep(5)

    # ── Candles ─────────────────────────────────────────────────────────────

    def _maybe_archive_candles(self) -> None:
        now = time.time()
        if now - self._last_candle_poll_ts < self.candle_poll_sec:
            return
        self._last_candle_poll_ts = now
        try:
            candles = self.market_context.session_candles_1min
            if not candles:
                return
            # Only archive candles finalized since the last one we saw —
            # session_candles_1min only contains completed buckets, the
            # in-progress minute is held separately by market_context.
            new_candles = []
            found_last = self._last_archived_candle_key is None
            for c in candles:
                if found_last:
                    new_candles.append(c)
                elif c.ts == self._last_archived_candle_key:
                    found_last = True
            if not found_last:
                # last-seen key fell off market_context's rolling cap —
                # archive everything currently available rather than lose it
                new_candles = candles
            if not new_candles:
                return
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO candles_1min (ts, symbol, open, high, low, close) "
                    "VALUES (?, 'NIFTY', ?, ?, ?, ?)",
                    [(c.ts, c.open, c.high, c.low, c.close) for c in new_candles],
                )
                conn.commit()
            self._last_archived_candle_key = new_candles[-1].ts
        except Exception as e:
            logger.error(f"[DataArchiver] Candle archive error: {e}")

    def _maybe_archive_option_candles(self) -> None:
        """Archives 1-min option premium candles into the same candles_1min
        table used for NIFTY, keyed by the option's own symbol. Needed for
        wave_extractor backtesting (it trades the option's own price, not
        the NIFTY index -- see Aug 13 handoff)."""
        try:
            symbol = self.market_context.option_symbol_tracked
            if not symbol:
                return  # wave_extractor hasn't started / no option tracked yet
            candles = self.market_context.option_session_candles_1min
            if not candles:
                return
            new_candles = []
            found_last = self._last_archived_option_candle_key is None
            for c in candles:
                if found_last:
                    new_candles.append(c)
                elif c.ts == self._last_archived_option_candle_key:
                    found_last = True
            if not found_last:
                new_candles = candles
            if not new_candles:
                return
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO candles_1min (ts, symbol, open, high, low, close) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(c.ts, symbol, c.open, c.high, c.low, c.close) for c in new_candles],
                )
                conn.commit()
            self._last_archived_option_candle_key = new_candles[-1].ts
        except Exception as e:
            logger.error(f"[DataArchiver] Option candle archive error: {e}")

    # ── Capital + Greeks ────────────────────────────────────────────────────

    def _maybe_snapshot_capital_and_greeks(self) -> None:
        now = time.time()
        if now - self._last_snapshot_ts < self.snapshot_interval_sec:
            return
        self._last_snapshot_ts = now
        ts = datetime.now(IST).isoformat()
        self._snapshot_capital(ts)
        self._snapshot_greeks(ts)
        self._snapshot_gex(ts)

    def _snapshot_capital(self, ts: str) -> None:
        try:
            deployed = dict(self.risk_manager._deployed_capital)
            cap = self.risk_manager.get_per_strategy_cap()
            rows = [
                (ts, strategy, used, cap, round((used / cap) * 100, 2) if cap else 0.0)
                for strategy, used in deployed.items()
            ]
            if not rows:
                return
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    "INSERT INTO capital_snapshots (ts, strategy, deployed, cap, pct) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[DataArchiver] Capital snapshot error: {e}")

    def _snapshot_greeks(self, ts: str) -> None:
        try:
            from core.greeks_engine import aggregate_portfolio_greeks

            spot = self.state_store.get_market_data().get("nifty_price", 0.0)
            vix = self.vix_manager._current_vix or 0.0

            trades = []
            for strat in [self.combo.survivor, self.combo.bn_survivor, self.combo.wave]:
                if strat is None:
                    continue
                for t in getattr(strat, "_open_trades_data", []):
                    sym = t.get("symbol")
                    if sym:
                        trades.append({
                            "symbol": sym,
                            "quantity": t.get("quantity", 65),
                            "order_type": t.get("order_type", "SELL"),
                        })

            portfolio = aggregate_portfolio_greeks(trades, spot, vix)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO greeks_snapshots "
                    "(ts, spot, vix, total_delta, total_gamma, total_theta, total_vega, trade_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, spot, vix, portfolio.total_delta, portfolio.total_gamma,
                     portfolio.total_theta, portfolio.total_vega, len(portfolio.trades)),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[DataArchiver] Greeks snapshot error: {e}")

    # ── Session plan ────────────────────────────────────────────────────────

    def _snapshot_gex(self, ts: str) -> None:
        try:
            nifty_gex = self.combo.nifty_gex
            if nifty_gex is None:
                return
            snap = getattr(nifty_gex, "_last_gex_snapshot", None)
            if not snap:
                return
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO gex_snapshots (ts, net_gex, regime, spot) VALUES (?, ?, ?, ?)",
                    (snap.get("ts", ts), snap.get("net_gex"), snap.get("regime"), snap.get("spot")),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[DataArchiver] GEX snapshot error: {e}")

    def _maybe_archive_session_plan(self) -> None:
        try:
            plan = self.session_planner.current_plan
            if not plan.is_ready:
                return
            if plan.plan_version == self._last_session_plan_version:
                return
            self._last_session_plan_version = plan.plan_version
            ts = plan.produced_at or datetime.now(IST).isoformat()
            import json
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO session_plans "
                    "(ts, plan_version, regime, confidence, trending_direction, "
                    "daily_loss_limit, max_capital, survivor_active, wave_active, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, plan.plan_version, plan.regime, plan.confidence,
                     plan.trending_direction, plan.daily_loss_limit, plan.max_capital,
                     int(plan.survivor_active), int(plan.wave_active),
                     json.dumps(plan.to_dict())),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[DataArchiver] Session plan archive error: {e}")


# ── Wiring notes (NOT executed here — for main.py, when step 2 is approved) ──
#
#   from core.research.data_archive import DataArchiver
#   archiver = DataArchiver(
#       market_context=market_context,
#       risk_manager=risk_manager,
#       vix_manager=vix_manager,
#       session_planner=session_planner,
#       state_store=state_store,
#       combo=combo,
#   )
#   archiver.start()
#   ...
#   archiver.stop()   # in shutdown path, alongside combo.stop()
#
# Deliberately NOT wired into main.py yet, per the "no live-code changes
# until approved" rule for this roadmap step.