# core/trade_log.py
# Permanent trade and event storage using SQLite.
# Upgraded for Idempotent state reconciliation and PM2 crash safety.

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Database file lives in the project root
DB_PATH = Path(__file__).parent.parent / "trade_log.db"


class TradeLogger:
    """
    Handles all reads and writes to trade_log.db.
    Creates and migrates database tables automatically.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
        self._migrate_db()
        logger.info(f"TradeLogger initialised with crash recovery: {self.db_path}")

    # ─── Setup & Migrations ──────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create core tables if they do not exist yet."""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id               TEXT PRIMARY KEY,
                    strategy         TEXT NOT NULL,
                    broker           TEXT NOT NULL,
                    symbol           TEXT NOT NULL,
                    order_type       TEXT NOT NULL,
                    quantity         INTEGER NOT NULL,
                    entry_price      REAL,
                    exit_price       REAL,
                    entry_time       TEXT,
                    exit_time        TEXT,
                    realised_pnl     REAL DEFAULT 0,
                    status           TEXT DEFAULT 'OPEN',
                    broker_order_id  TEXT,
                    client_order_id  TEXT UNIQUE,
                    notes            TEXT,
                    gross_pnl        REAL DEFAULT 0,
                    total_costs      REAL DEFAULT 0,
                    parent_trade_id  TEXT DEFAULT '',
                    paper_trade      INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS strategy_sessions (
                    session_id       TEXT PRIMARY KEY,
                    strategy         TEXT NOT NULL,
                    start_time       TEXT NOT NULL,
                    end_time         TEXT,
                    config_snapshot  TEXT,
                    total_pnl        REAL DEFAULT 0,
                    stop_reason      TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    strategy    TEXT,
                    event_type  TEXT NOT NULL,
                    payload     TEXT
                );
            """
            )
        logger.debug("TradeLogger: database tables ready")

    def _migrate_db(self) -> None:
        """Ensures structural columns like client_order_id exist in active instances."""
        with self._connect() as conn:
            # Check if client_order_id exists from prior setups
            cursor = conn.execute("PRAGMA table_info(trades);")
            columns = [row["name"] for row in cursor.fetchall()]
            
            if "client_order_id" not in columns:
                try:
                    # SQLite does not support UNIQUE directly in ALTER TABLE ADD COLUMN,
                    # so add the plain column first, then enforce uniqueness via an index.
                    conn.execute("ALTER TABLE trades ADD COLUMN client_order_id TEXT;")
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_client_order_id "
                        "ON trades(client_order_id);"
                    )
                    logger.info("Database Migration applied: Added unique client_order_id index to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding client_order_id: {e}")

            # readable_symbol: human-readable descriptor (underlying+expiry+strike+type),
            # stored alongside the real broker symbol/instrument_key purely so future
            # trades can be matched against Upstox's Expired Historical Candle API for
            # backtesting. Never used for order placement -- display/backtest only.
            if "readable_symbol" not in columns:
                try:
                    conn.execute("ALTER TABLE trades ADD COLUMN readable_symbol TEXT DEFAULT '';")
                    logger.info("Database Migration applied: Added readable_symbol column to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding readable_symbol: {e}")

            # peak_pnl: the true peak net P&L (Rs) reached at any point during
            # the trade, recorded unconditionally every tick via
            # RiskManager.get_mfe() -- independent of whether trailing ever
            # armed. Purely for future trailing-threshold tuning (Phase 3);
            # never read by any exit decision. NULL means not yet recorded
            # (trade closed before this migration, or MFE tracking wasn't
            # wired in for that call site yet).
            if "peak_pnl" not in columns:
                try:
                    conn.execute("ALTER TABLE trades ADD COLUMN peak_pnl REAL DEFAULT NULL;")
                    logger.info("Database Migration applied: Added peak_pnl column to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding peak_pnl: {e}")
            # trough_pnl: the true trough net P&L (Rs) reached at any point during
            # the trade, recorded unconditionally every tick via
            # RiskManager.get_mae() -- mirrors peak_pnl/MFE but for max adverse
            # excursion. Purely for future stop-loss tuning (Phase 3); never read
            # by any exit decision. NULL means not yet recorded.
            if "trough_pnl" not in columns:
                try:
                    conn.execute("ALTER TABLE trades ADD COLUMN trough_pnl REAL DEFAULT NULL;")
                    logger.info("Database Migration applied: Added trough_pnl column to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding trough_pnl: {e}")

            # entry_context: JSON-encoded snapshot of WHY the trade was entered --
            # market regime, signal features, checklist state, confidence, R:R,
            # whatever the strategy that opened it had available at entry time.
            # Deliberately schema-less (JSON text, not fixed columns) since
            # different strategies (nifty_gex vs wave_extractor vs survivor) have
            # completely different feature sets. Purely for post-mortem pattern
            # analysis (Aug 12 2026 session) -- never read by any live trading
            # decision. NULL/empty means not yet captured for that trade.
            if "entry_context" not in columns:
                try:
                    conn.execute("ALTER TABLE trades ADD COLUMN entry_context TEXT DEFAULT '';")
                    logger.info("Database Migration applied: Added entry_context column to trades table.")
                except Exception as e:
                    logger.error(f"Migration error while adding entry_context: {e}")

    def _connect(self) -> sqlite3.Connection:
        """Open a clean database thread connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Allows dict-style mapping
        return conn

    def health(self) -> str:
        """Quick check that the database file is accessible on disk."""
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1")
            return "OK"
        except Exception as e:
            return f"ERROR: {e}"

    # ─── Idempotent Trades & Recovery ────────────────────────────────────────

    def open_trade(
        self,
        strategy: str,
        broker: str,
        symbol: str,
        order_type: str,  # BUY or SELL
        quantity: int,
        entry_price: float,
        broker_order_id: str = "",
        client_order_id: str = "",
        notes: str = "",
        parent_trade_id: str = "",
        paper_trade: bool = False,
        readable_symbol: str = "",
        entry_context: str = "",
        sl_price: float = None,
        target_price: float = None,
    ) -> str:
        """
        Record a new trade when an order fills. 
        Enforces absolute identity handling via unique client_order_id signatures.

        parent_trade_id links a hedge/secondary leg back to its primary trade's
        id. Without this, hedge linkage only ever lived in the caller's
        in-memory dict -- a restart between hedge-open and primary-close would
        silently orphan the hedge forever, since crash recovery rebuilds trades
        purely from DB rows and had no way to know a hedge existed at all.
        """
        # If client_order_id is missing, default back safely to an intentional unique string
        if not client_order_id:
            client_order_id = f"MANUAL-{str(uuid.uuid4())[:8]}"

        # Check if this exact client order hash was processed already
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM trades WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
            
            if existing:
                logger.warning(f"Idempotency Guard triggered! Trade with client_order_id {client_order_id} already logged. Skipping insert.")
                return existing["id"]

        trade_id = str(uuid.uuid4())
        entry_time = datetime.now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (id, strategy, broker, symbol, order_type, quantity,
                     entry_price, entry_time, status, broker_order_id, client_order_id, notes, parent_trade_id, paper_trade, readable_symbol, entry_context, sl_price, target_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    trade_id,
                    strategy,
                    broker,
                    symbol,
                    order_type,
                    quantity,
                    entry_price,
                    entry_time,
                    broker_order_id,
                    client_order_id,
                    notes,
                    parent_trade_id,
                    1 if paper_trade else 0,
                    readable_symbol,
                    entry_context,
                    sl_price,
                    target_price,
                ),
            )

        logger.info(
            f"TradeLogger: opened trade {trade_id} | {symbol} {order_type} {quantity} @ {entry_price} | ClientID: {client_order_id}"
        )
        return trade_id

    def get_open_hedge_for(self, parent_trade_id: str):
        """Find an OPEN hedge/secondary leg linked to a given parent trade id.
        Used by crash recovery to re-attach a hedge that the primary leg's own
        DB row has no way of referencing on its own."""
        if not parent_trade_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE parent_trade_id = ? AND status = 'OPEN'",
                (parent_trade_id,),
            ).fetchone()
        return dict(row) if row else None

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        notes: str = "",
        net_pnl: Optional[float] = None,
        gross_pnl: Optional[float] = None,
        total_costs: Optional[float] = None,
        peak_pnl: Optional[float] = None,
        trough_pnl: Optional[float] = None,
    ) -> float:
        """Mark a trade as closed and calculate realised P&L metrics.

        If net_pnl/gross_pnl/total_costs are provided (e.g. from wave_extractor
        which computes transaction costs externally), they are stored directly.
        Otherwise P&L is calculated from entry/exit price (legacy path).

        peak_pnl (optional): the true MFE (max favourable excursion, Rs) for
        this trade, typically from RiskManager.get_mfe(trade_id). Purely for
        future trailing-threshold tuning -- omit to leave the column NULL,
        same as before this param existed.
        trough_pnl (optional): the true MAE (max adverse excursion, Rs) for
        this trade, typically from RiskManager.get_mae(trade_id). Purely for
        future stop-loss tuning -- omit to leave the column NULL.
        """
        exit_time = datetime.now().isoformat()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()

            if not row:
                logger.warning(f"TradeLogger: trade {trade_id} not found")
                return 0.0

            if row["status"] == "CLOSED":
                logger.warning(f"TradeLogger: trade {trade_id} was already closed. Skipping duplicate processing.")
                return row["realised_pnl"]

            entry_price = row["entry_price"]
            quantity = row["quantity"]
            order_type = row["order_type"]

            if net_pnl is not None:
                pnl          = round(net_pnl, 2)
                _gross_pnl   = round(gross_pnl, 2)   if gross_pnl   is not None else pnl
                _total_costs = round(total_costs, 2)  if total_costs is not None else 0.0
            else:
                if order_type == "SELL":
                    pnl = (entry_price - exit_price) * quantity
                else:
                    pnl = (exit_price - entry_price) * quantity
                pnl          = round(pnl, 2)
                _gross_pnl   = pnl
                _total_costs = 0.0

            set_clauses = [
                "exit_price   = ?",
                "exit_time    = ?",
                "realised_pnl = ?",
                "gross_pnl    = ?",
                "total_costs  = ?",
                "status       = 'CLOSED'",
                "notes        = ?",
            ]
            sql_params = [exit_price, exit_time, pnl, _gross_pnl, _total_costs, notes]
            if peak_pnl is not None:
                set_clauses.append("peak_pnl = ?")
                sql_params.append(round(peak_pnl, 2))
            if trough_pnl is not None:
                set_clauses.append("trough_pnl = ?")
                sql_params.append(round(trough_pnl, 2))
            sql_params.append(trade_id)
            conn.execute(
                f"UPDATE trades SET {', '.join(set_clauses)} WHERE id = ?",
                sql_params,
            )

        logger.info(f"TradeLogger: closed trade {trade_id} | Gross: {_gross_pnl} | Costs: {_total_costs} | Net P&L: {pnl}")
        return pnl

    def get_active_positions(self, strategy: Optional[str] = None) -> list[dict]:
        """
        CRITICAL RECOVERY FUNCTION:
        Queries all currently active un-closed database trades to reconstruct state
        across system crashes or automatic hot reboots.
        """
        query = "SELECT * FROM trades WHERE status = 'OPEN'"
        params = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
            
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_trades(
        self,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch historical trades table entries for dashboard visualisations."""
        query = "SELECT * FROM trades WHERE notes NOT LIKE 'ORPHANED%'"
        params = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_pnl_summary(self, strategy: Optional[str] = None, today_only: bool = False) -> dict:
        """Calculate total realized dashboard P&L metrics.
        today_only=True scopes to the current calendar day only — use this
        for session-end / EOD reporting so alerts don't show lifetime totals."""
        query = "SELECT * FROM trades WHERE status = 'CLOSED' AND notes NOT LIKE 'ORPHANED%'"
        params = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        if today_only:
            query += " AND date(entry_time) = date('now', 'localtime')"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        if not rows:
            return {
                "total_pnl": 0.0,
                "total_trades": 0,
                "winning": 0,
                "losing": 0,
                "win_rate": 0.0,
            }

        pnls = [row["realised_pnl"] for row in rows]
        winning = len([p for p in pnls if p > 0])
        losing = len([p for p in pnls if p <= 0])
        total = len(pnls)

        return {
            "total_pnl": round(sum(pnls), 2),
            "total_trades": total,
            "winning": winning,
            "losing": losing,
            "win_rate": round((winning / total) * 100, 1) if total > 0 else 0.0,
        }

    # ─── Sessions ────────────────────────────────────────────────────────────

    def start_session(self, strategy: str, config_snapshot: str = "") -> str:
        """Record when a strategy session runs. Returns session_id."""
        session_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_sessions
                    (session_id, strategy, start_time, config_snapshot)
                VALUES (?, ?, ?, ?)
            """,
                (session_id, strategy, datetime.now().isoformat(), config_snapshot),
            )

        logger.info(f"TradeLogger: session started {session_id} for {strategy}")
        return session_id

    def end_session(
        self, session_id: str, total_pnl: float, stop_reason: str = "MANUAL"
    ) -> None:
        """Finalize state snapshots when a strategy session terminates."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE strategy_sessions
                SET end_time    = ?,
                    total_pnl   = ?,
                    stop_reason = ?
                WHERE session_id = ?
            """,
                (datetime.now().isoformat(), total_pnl, stop_reason, session_id),
            )

        logger.info(
            f"TradeLogger: session ended {session_id} | P&L: {total_pnl} | reason: {stop_reason}"
        )

    # ─── Events ──────────────────────────────────────────────────────────────

    def log_event(self, event_type: str, strategy: str = "", payload: str = "") -> None:
        """Log transactional execution steps directly to db history registers."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (timestamp, strategy, event_type, payload)
                VALUES (?, ?, ?, ?)
            """,
                (datetime.now().isoformat(), strategy, event_type, payload),
            )

    def get_events(
        self,
        strategy: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch audit log metrics sequence trace collections."""
        query = "SELECT * FROM events WHERE 1=1"
        params = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


# ─── Global singleton instance ───────────────────────────────────────────────
trade_logger = TradeLogger()