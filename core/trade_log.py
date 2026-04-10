# core/trade_log.py
# Permanent trade and event storage using SQLite.
# Every order, fill, and strategy event is written here.
# The dashboard reads this for the trade history table and session reports.

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
    Creates the database and tables automatically on first run.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
        logger.info(f"TradeLogger initialised: {self.db_path}")

    # ─── Setup ───────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables if they don't exist yet."""
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
                    notes            TEXT
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

    def _connect(self) -> sqlite3.Connection:
        """Open a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # allows dict-style access to rows
        return conn

    def health(self) -> str:
        """Quick check that the database is accessible."""
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1")
            return "OK"
        except Exception as e:
            return f"ERROR: {e}"

    # ─── Trades ──────────────────────────────────────────────────────────────

    def open_trade(
        self,
        strategy: str,
        broker: str,
        symbol: str,
        order_type: str,  # BUY or SELL
        quantity: int,
        entry_price: float,
        broker_order_id: str = "",
        notes: str = "",
    ) -> str:
        """
        Record a new trade when an order is filled.
        Returns the trade_id for later updates.
        """
        trade_id = str(uuid.uuid4())
        entry_time = datetime.now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (id, strategy, broker, symbol, order_type, quantity,
                     entry_price, entry_time, status, broker_order_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
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
                    notes,
                ),
            )

        logger.info(
            f"TradeLogger: opened trade {trade_id} | {symbol} {order_type} {quantity} @ {entry_price}"
        )
        return trade_id

    def close_trade(self, trade_id: str, exit_price: float, notes: str = "") -> float:
        """
        Mark a trade as closed and calculate realised P&L.
        Returns the realised P&L amount.
        """
        exit_time = datetime.now().isoformat()

        with self._connect() as conn:
            # Fetch the trade to calculate P&L
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()

            if not row:
                logger.warning(f"TradeLogger: trade {trade_id} not found")
                return 0.0

            entry_price = row["entry_price"]
            quantity = row["quantity"]
            order_type = row["order_type"]

            # P&L calculation
            # SELL trade profits when price goes down (options selling)
            # BUY trade profits when price goes up
            if order_type == "SELL":
                pnl = (entry_price - exit_price) * quantity
            else:
                pnl = (exit_price - entry_price) * quantity

            pnl = round(pnl, 2)

            conn.execute(
                """
                UPDATE trades
                SET exit_price   = ?,
                    exit_time    = ?,
                    realised_pnl = ?,
                    status       = 'CLOSED',
                    notes        = ?
                WHERE id = ?
            """,
                (exit_price, exit_time, pnl, notes, trade_id),
            )

        logger.info(f"TradeLogger: closed trade {trade_id} | P&L: {pnl}")
        return pnl

    def get_trades(
        self,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Fetch trades, optionally filtered by strategy or status.
        Returns list of dicts for easy JSON serialisation.
        """
        query = "SELECT * FROM trades WHERE 1=1"
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

    def get_pnl_summary(self, strategy: Optional[str] = None) -> dict:
        """
        Calculate total realised P&L and trade counts.
        Used for dashboard summary and daily reports.
        """
        query = "SELECT * FROM trades WHERE status = 'CLOSED'"
        params = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

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
        """Record when a strategy session starts. Returns session_id."""
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
        """Record when a strategy session ends."""
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
        """Log any system event to the events table."""
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
        """Fetch recent events, optionally filtered."""
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
# Import anywhere:
#   from core.trade_log import trade_logger

trade_logger = TradeLogger()
