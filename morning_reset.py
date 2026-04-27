# morning_reset.py
# Run this every morning before starting main.py
# Closes all stale OPEN trades from previous sessions and resets state

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DB_PATH = Path("trade_log.db")


def reset_stale_trades():
    """Mark all OPEN trades from previous days as CANCELLED."""
    if not DB_PATH.exists():
        logger.warning(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    today = date.today().isoformat()

    cursor.execute(
        """
        SELECT id, strategy, symbol, entry_time
        FROM trades
        WHERE status = 'OPEN'
        AND date(entry_time) < ?
    """,
        (today,),
    )

    stale = cursor.fetchall()

    if not stale:
        logger.info("No stale trades found. Database is clean.")
        conn.close()
        return

    logger.info(f"Found {len(stale)} stale open trades from previous sessions:")
    for trade in stale:
        logger.info(f"  - {trade[1]} | {trade[2]} | entered: {trade[3]}")

    cursor.execute(
        """
        UPDATE trades
        SET status = 'CANCELLED',
            exit_time = ?,
            exit_price = entry_price,
            realised_pnl = 0.0,
            notes = 'Auto-cancelled by morning_reset'
        WHERE status = 'OPEN'
        AND date(entry_time) < ?
    """,
        (datetime.now().isoformat(), today),
    )

    affected = cursor.rowcount
    conn.commit()
    conn.close()
    logger.info(f"Cancelled {affected} stale trades.")


def reset_todays_open_trades():
    """Cancel any OPEN trades from today for a fresh start."""
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = date.today().isoformat()

    cursor.execute(
        """
        SELECT COUNT(*) FROM trades
        WHERE status = 'OPEN' AND date(entry_time) = ?
    """,
        (today,),
    )
    count = cursor.fetchone()[0]

    if count > 0:
        logger.info(
            f"Found {count} open trades from today — cancelling for fresh start."
        )
        cursor.execute(
            """
            UPDATE trades
            SET status = 'CANCELLED',
                exit_time = ?,
                exit_price = entry_price,
                realised_pnl = 0.0,
                notes = 'Cancelled by morning_reset - fresh start'
            WHERE status = 'OPEN' AND date(entry_time) = ?
        """,
            (datetime.now().isoformat(), today),
        )
        conn.commit()

    conn.close()


def show_summary():
    """Show P&L summary after reset."""
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    today = date.today().isoformat()

    cursor.execute("SELECT SUM(realised_pnl) FROM trades WHERE status = 'CLOSED'")
    total_pnl = cursor.fetchone()[0] or 0.0

    cursor.execute(
        """
        SELECT SUM(realised_pnl) FROM trades
        WHERE status = 'CLOSED' AND date(exit_time) = ?
    """,
        (today,),
    )
    today_pnl = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT status, COUNT(*) FROM trades GROUP BY status")
    counts = cursor.fetchall()

    conn.close()

    logger.info("=" * 50)
    logger.info("DATABASE SUMMARY AFTER RESET:")
    for status, count in counts:
        logger.info(f"  {status}: {count} trades")
    logger.info(f"  Today P&L:  Rs {today_pnl:.2f}")
    logger.info(f"  Total P&L:  Rs {total_pnl:.2f}")
    logger.info("=" * 50)


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("MORNING RESET STARTED")
    logger.info(f"Date: {date.today().isoformat()}")
    logger.info("=" * 50)

    reset_stale_trades()
    reset_todays_open_trades()
    show_summary()

    logger.info("Morning reset complete. Ready to run main.py")
