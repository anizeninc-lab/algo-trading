# core/pattern_memory.py
#
# Step 6 of the staged self-learning plan (Aug 14 2026 session).
#
# Turns post_mortem.py's descriptive breakdowns into a PERSISTED, queryable
# table -- research_archive.db's pattern_memory table -- instead of a
# printed-and-forgotten report. This is the foundation everything downstream
# (Step 7 statistical pattern detection, Step 8 candidate proposals) reads
# from, instead of every future step re-scanning and re-grouping raw trades
# from scratch.
#
# EXPLICITLY NOT INCLUDED, ON PURPOSE -- same philosophy as post_mortem.py
# and candidate_config.py:
#   - No autonomous parameter changes. This module only reads trade_log.db
#     and WRITES to research_archive.db (a separate, read-only-from-live-
#     trading's-perspective database). It never touches trade_log.db,
#     risk_manager.py, or any strategy file.
#   - refresh() is safe to call as often as you like -- it fully rebuilds
#     the table from current trade history each time (DELETE + re-INSERT
#     in one transaction), so it's always consistent with trade_log.db,
#     never incrementally drifting out of sync.
#   - Every dimension here mirrors post_mortem.py's own grouping logic
#     directly (same _summarize/_parse_entry_context functions, imported
#     not duplicated) so the two can never silently disagree with each
#     other over time.

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core.post_mortem import _parse_entry_context, _summarize, _holding_minutes
from core.trade_log import trade_logger

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "research_archive.db"


def _init_db(db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_memory (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                refreshed_at         TEXT NOT NULL,
                dimension_type       TEXT NOT NULL,
                dimension_value      TEXT NOT NULL,
                trade_count          INTEGER NOT NULL,
                win_rate             REAL,
                avg_win              REAL,
                avg_loss             REAL,
                profit_factor        REAL,
                net_pnl              REAL NOT NULL,
                avg_holding_minutes  REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_memory_dim "
            "ON pattern_memory(dimension_type, dimension_value)"
        )
        conn.commit()


def _row_for(dimension_type: str, dimension_value: str, trades: list, refreshed_at: str) -> tuple:
    s = _summarize(trades)
    holds = [
        _holding_minutes(t.get("entry_time", ""), t.get("exit_time", ""))
        for t in trades if t.get("exit_time")
    ]
    avg_hold = round(sum(holds) / len(holds), 1) if holds else None
    pf = s["profit_factor"]
    pf_stored = None if pf is None else (999999.0 if pf == float("inf") else pf)
    return (
        refreshed_at, dimension_type, str(dimension_value),
        s["count"], s["win_rate"], s["avg_win"], s["avg_loss"],
        pf_stored, s["net_pnl"], avg_hold,
    )


def refresh(strategy: str = None, limit: int = 2000, db_path: Path = DB_PATH) -> int:
    """
    Rebuilds the entire pattern_memory table from current closed-trade
    history. Safe to call anytime, as often as you like -- always a full,
    consistent rebuild, never an incremental patch.

    Returns the number of dimension rows written.
    """
    _init_db(db_path)
    refreshed_at = datetime.now().isoformat()

    all_trades = trade_logger.get_trades(strategy=strategy, status="CLOSED", limit=limit)
    if not all_trades:
        logger.info("[pattern_memory] No closed trades found — nothing to refresh")
        return 0

    rows = []

    by_strategy = defaultdict(list)
    for t in all_trades:
        by_strategy[t.get("strategy", "unknown")].append(t)
    for strat, group in by_strategy.items():
        rows.append(_row_for("strategy", strat, group, refreshed_at))

    by_reason = defaultdict(list)
    for t in all_trades:
        reason = (t.get("notes") or "UNKNOWN").split("|")[0].strip()
        by_reason[reason].append(t)
    for reason, group in by_reason.items():
        rows.append(_row_for("exit_reason", reason, group, refreshed_at))

    by_hour = defaultdict(list)
    for t in all_trades:
        try:
            hour = datetime.fromisoformat(t.get("entry_time", "")).hour
            by_hour[hour].append(t)
        except Exception:
            continue
    for hour, group in by_hour.items():
        rows.append(_row_for("entry_hour", f"{hour:02d}", group, refreshed_at))

    by_regime = defaultdict(list)
    for t in all_trades:
        ctx = _parse_entry_context(t.get("entry_context", ""))
        regime = ctx.get("gex_regime") or ctx.get("direction") or "no_context_captured"
        by_regime[regime].append(t)
    for regime, group in by_regime.items():
        rows.append(_row_for("regime", regime, group, refreshed_at))

    by_strat_regime = defaultdict(list)
    for t in all_trades:
        ctx = _parse_entry_context(t.get("entry_context", ""))
        regime = ctx.get("gex_regime") or ctx.get("direction") or "no_context_captured"
        key = f"{t.get('strategy', 'unknown')}|{regime}"
        by_strat_regime[key].append(t)
    for key, group in by_strat_regime.items():
        rows.append(_row_for("strategy_regime", key, group, refreshed_at))

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM pattern_memory")
        conn.executemany(
            "INSERT INTO pattern_memory "
            "(refreshed_at, dimension_type, dimension_value, trade_count, win_rate, "
            "avg_win, avg_loss, profit_factor, net_pnl, avg_holding_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    logger.info(f"[pattern_memory] Refreshed: {len(rows)} dimension rows from {len(all_trades)} trades")
    return len(rows)


def show(dimension_type: str = None, db_path: Path = DB_PATH) -> str:
    """Prints the current pattern_memory table contents, optionally filtered
    to one dimension_type. Read-only, does not refresh first -- call
    refresh() explicitly if you want current data."""
    _init_db(db_path)
    query = "SELECT * FROM pattern_memory"
    params = []
    if dimension_type:
        query += " WHERE dimension_type = ?"
        params.append(dimension_type)
    query += " ORDER BY dimension_type, net_pnl ASC"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return "pattern_memory is empty — run refresh() first."

    lines = []
    lines.append("=" * 90)
    lines.append(f"PATTERN MEMORY BANK{f' — {dimension_type}' if dimension_type else ''}")
    lines.append(f"(last refreshed: {rows[0]['refreshed_at']})")
    lines.append("=" * 90)

    current_dim = None
    for r in rows:
        if r["dimension_type"] != current_dim:
            current_dim = r["dimension_type"]
            lines.append("")
            lines.append(f"-- {current_dim} --")
        pf = r["profit_factor"]
        pf_str = "inf" if pf == 999999.0 else pf
        lines.append(
            f"  {r['dimension_value']:30s} | {r['trade_count']:3d} trades | "
            f"net ₹{r['net_pnl']:>10,.2f} | win rate {r['win_rate']}% | PF {pf_str} | "
            f"avg hold {r['avg_holding_minutes']} min"
        )

    lines.append("")
    lines.append("(Descriptive only — no automatic changes made. Review and decide manually.)")
    lines.append("=" * 90)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2 or sys.argv[1] not in ("refresh", "show"):
        print("Usage: python3 -m core.pattern_memory refresh")
        print("       python3 -m core.pattern_memory show [dimension_type]")
        sys.exit(1)

    if sys.argv[1] == "refresh":
        n = refresh()
        print(f"Refreshed {n} dimension rows.")
    elif sys.argv[1] == "show":
        dim = sys.argv[2] if len(sys.argv) > 2 else None
        print(show(dimension_type=dim))
