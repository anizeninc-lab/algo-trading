# core/pattern_detection.py
#
# Step 7 of the staged self-learning plan (Aug 14 2026 Session 3).
#
# SCOPING NOTE (agreed with user this session): the original idea for this
# step was full unsupervised clustering (KMeans/DBSCAN/autoencoders) on
# regime + IV + time features. With only ~26 context-captured trades so far,
# that's thin for real clustering -- it would likely fit noise, not signal.
# Scoped down instead to what Step 6 already set up for: cross-dimensional
# breakdowns (pattern_memory.py's new entry_hour_exit_reason dimension) plus
# simple, explicit threshold-based flagging. True clustering is deferred to
# a later step, once context-captured trade volume is much higher (see
# Step 12 in the tracker).
#
# This module reads the pattern_memory table (built by core/pattern_memory.py,
# Step 6) and flags dimension combos that cross explicit thresholds. Nothing
# statistical beyond that -- no p-values, no significance testing, no model
# fitting. A human decides what (if anything) to do with a flag. Step 8 will
# read confirmed flags from pattern_flags to draft candidate proposals into
# candidate_config.json -- nothing here writes there or touches live trading.
#
# EXPLICITLY NOT INCLUDED, ON PURPOSE -- same philosophy as post_mortem.py,
# pattern_memory.py, and candidate_config.py:
#   - No clustering / ML of any kind (see scoping note above).
#   - No autonomous parameter changes. This module only reads
#     research_archive.db's pattern_memory table and writes to a new
#     pattern_flags table in the same database. It never touches
#     trade_log.db or any live strategy/risk-manager code.
#   - exit_reason -- and the new entry_hour_exit_reason cross-dim, since it
#     contains exit_reason -- are deliberately excluded from flagging.
#     "STOP_LOSS trades have a 0% win rate" is true by definition -- that's
#     what STOP_LOSS means -- not a discovered pattern. Only dimensions that
#     say something about *when/where/under what conditions* a trade was
#     entered (strategy, entry_hour, regime, strategy_regime) are eligible
#     to be flagged. See FLAGGABLE_DIMENSIONS below for the fuller
#     reasoning on entry_hour_exit_reason specifically.
#   - no_context_captured is never flagged as a value, even if it crosses a
#     threshold -- it's an absence-of-data marker (87% of trades as of
#     Aug 14 2026), not a real trading condition. See NON_FLAGGABLE_VALUES.
#   - detect() does NOT refresh pattern_memory first. Callers decide whether
#     stale pattern_memory data is acceptable, same as pattern_memory.show()
#     itself. Run `python3 -m core.pattern_memory refresh` first if you want
#     current data.

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from core.pattern_memory import DB_PATH

logger = logging.getLogger(__name__)

# -- Thresholds -- deliberately simple, explicit, and hand-tuned. Not
# learned, not adaptive. Revisit by hand as more context-captured data
# accumulates (see Step 6 handoff note: 87% of trades still show
# no_context_captured as of Aug 14 2026, expected to fall over time).
MIN_TRADES_FOR_FLAG = 5      # below this, any win rate is just noise
CONCERNING_WIN_RATE = 20.0   # <= this AND net_pnl < 0 -> CONCERNING
PROMISING_WIN_RATE = 60.0    # >= this AND net_pnl > 0 -> PROMISING

# Dimension types eligible for flagging. exit_reason is deliberately
# excluded -- see module docstring. entry_hour_exit_reason is ALSO excluded
# from flagging (though it stays in pattern_memory as a browsable
# breakdown): exit_reason is a component of it and dominates the win/loss
# outcome trivially (a STOP_LOSS trade is a loss by definition, a
# TARGET_HIT trade is a win by definition), so every "09|STOP_LOSS" style
# row would flag as CONCERNING for the same non-reason bare exit_reason is
# excluded for. Real signal here would be about *concentration* (is
# STOP_LOSS disproportionately common at a given hour, relative to its
# overall rate) rather than win rate -- that's a genuinely different
# question and out of scope for simple threshold flagging; left for a
# human to eyeball via `python3 -m core.pattern_memory show
# entry_hour_exit_reason`, or for a future step if it proves worth
# automating.
FLAGGABLE_DIMENSIONS = (
    "strategy", "entry_hour", "regime", "strategy_regime",
)

# Dimension values that represent an absence of data rather than a
# discovered pattern -- never flag these even if they cross a threshold.
# no_context_captured is a grab-bag of every regime we don't have
# entry_context for yet (still 87% of trades as of Aug 14 2026 -- see Step 6
# handoff note), not a real condition a trade was entered under.
#
# Composite dimension_values (e.g. strategy_regime's "bn_survivor|
# no_context_captured") need a component-wise check, not exact match --
# "bn_survivor|no_context_captured" != "no_context_captured" but still
# carries zero regime information and would otherwise slip through.
NON_FLAGGABLE_VALUES = {"no_context_captured"}


def _is_flaggable_value(dimension_value: str) -> bool:
    """False if any '|'-separated component of dimension_value is a
    non-flaggable placeholder (currently just no_context_captured)."""
    return not any(part in NON_FLAGGABLE_VALUES
                   for part in dimension_value.split("|"))


def _init_db(db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_flags (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                flagged_at       TEXT NOT NULL,
                dimension_type   TEXT NOT NULL,
                dimension_value  TEXT NOT NULL,
                trade_count      INTEGER NOT NULL,
                win_rate         REAL,
                net_pnl          REAL NOT NULL,
                profit_factor    REAL,
                flag_type        TEXT NOT NULL,
                flag_reason      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_flags_type "
            "ON pattern_flags(flag_type)"
        )
        conn.commit()


def detect(min_trades: int = MIN_TRADES_FOR_FLAG,
           concerning_win_rate: float = CONCERNING_WIN_RATE,
           promising_win_rate: float = PROMISING_WIN_RATE,
           db_path: Path = DB_PATH) -> int:
    """
    Scans the current pattern_memory table and flags dimension combos
    crossing the thresholds above. Fully rebuilds pattern_flags each call
    (DELETE + re-INSERT) -- same "always consistent, never drifting"
    philosophy as pattern_memory.refresh().

    Returns the number of flags written.
    """
    _init_db(db_path)
    flagged_at = datetime.now().isoformat()

    placeholders = ",".join("?" * len(FLAGGABLE_DIMENSIONS))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM pattern_memory WHERE dimension_type IN ({placeholders}) "
            f"AND trade_count >= ?",
            (*FLAGGABLE_DIMENSIONS, min_trades),
        ).fetchall()

    flags = []
    for r in rows:
        win_rate = r["win_rate"]
        net_pnl = r["net_pnl"]
        if win_rate is None:
            continue
        if not _is_flaggable_value(r["dimension_value"]):
            continue

        if win_rate <= concerning_win_rate and net_pnl < 0:
            flags.append((
                flagged_at, r["dimension_type"], r["dimension_value"],
                r["trade_count"], win_rate, net_pnl, r["profit_factor"],
                "CONCERNING",
                f"{r['trade_count']} trades, {win_rate}% win rate, net "
                f"\u20b9{net_pnl:,.2f} -- at or below the "
                f"{concerning_win_rate}% concerning threshold.",
            ))
        elif win_rate >= promising_win_rate and net_pnl > 0:
            flags.append((
                flagged_at, r["dimension_type"], r["dimension_value"],
                r["trade_count"], win_rate, net_pnl, r["profit_factor"],
                "PROMISING",
                f"{r['trade_count']} trades, {win_rate}% win rate, net "
                f"\u20b9{net_pnl:,.2f} -- at or above the "
                f"{promising_win_rate}% promising threshold.",
            ))

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM pattern_flags")
        conn.executemany(
            "INSERT INTO pattern_flags "
            "(flagged_at, dimension_type, dimension_value, trade_count, "
            "win_rate, net_pnl, profit_factor, flag_type, flag_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            flags,
        )
        conn.commit()

    n_concerning = sum(1 for f in flags if f[7] == "CONCERNING")
    n_promising = sum(1 for f in flags if f[7] == "PROMISING")
    logger.info(f"[pattern_detection] {len(flags)} flags written "
                f"({n_concerning} concerning, {n_promising} promising)")
    return len(flags)


def show(flag_type: str = None, db_path: Path = DB_PATH) -> str:
    """Prints current pattern_flags contents. Read-only, does not call
    detect() first -- call detect() explicitly if you want current flags."""
    _init_db(db_path)
    query = "SELECT * FROM pattern_flags"
    params = []
    if flag_type:
        query += " WHERE flag_type = ?"
        params.append(flag_type.upper())
    query += " ORDER BY flag_type, net_pnl ASC"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return ("pattern_flags is empty -- run "
                "`python3 -m core.pattern_memory refresh` then "
                "`python3 -m core.pattern_detection detect` first.")

    lines = []
    lines.append("=" * 90)
    lines.append(f"PATTERN FLAGS{f' — {flag_type.upper()}' if flag_type else ''}")
    lines.append(f"(last detected: {rows[0]['flagged_at']})")
    lines.append("=" * 90)

    current_type = None
    for r in rows:
        if r["flag_type"] != current_type:
            current_type = r["flag_type"]
            lines.append("")
            lines.append(f"-- {current_type} --")
        lines.append(f"  [{r['dimension_type']}] {r['dimension_value']:30s} | {r['flag_reason']}")

    lines.append("")
    lines.append("(Descriptive only — these are hypotheses to review by hand, not conclusions. "
                  "Step 8 will read CONCERNING/PROMISING flags to draft candidate proposals; "
                  "nothing here changes live trading.)")
    lines.append("=" * 90)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2 or sys.argv[1] not in ("detect", "show"):
        print("Usage: python3 -m core.pattern_detection detect")
        print("       python3 -m core.pattern_detection show [CONCERNING|PROMISING]")
        sys.exit(1)

    if sys.argv[1] == "detect":
        n = detect()
        print(f"Wrote {n} flags.")
    elif sys.argv[1] == "show":
        ft = sys.argv[2] if len(sys.argv) > 2 else None
        print(show(flag_type=ft))
