# core/pattern_overlap.py
#
# Sanity-check tool, sits between Step 7 and Step 8. Not itself a numbered
# step in the tracker -- it's a diagnostic to run before turning flags into
# candidate proposals.
#
# WHY THIS EXISTS: pattern_detection.py flags dimension combos independently
# of each other. It has no way to know that "entry_hour 13", "regime PE",
# and "strategy_regime survivor|PE" might all be describing the *same*
# handful of losing trades from three different angles. If that's the case,
# Step 8 would draft three candidate proposals that are really one root
# cause wearing different labels -- worth knowing before that happens, not
# after.
#
# This rebuilds the same dimension -> trade-id groupings pattern_memory.py
# uses internally (kept in sync by hand -- see _build_membership below;
# if pattern_memory.py's grouping logic changes, this needs to change too),
# then computes pairwise overlap between every pair of CONCERNING (or
# PROMISING) flags. Overlap is intersection / min(len(a), len(b)) -- i.e.
# "what fraction of the SMALLER bucket also appears in the larger bucket" --
# so a small bucket that's entirely contained in a bigger one (e.g.
# survivor|PE fully inside regime=PE) reads as 100% overlap, which is the
# useful signal here: it means the smaller flag adds no new information
# beyond the bigger one.
#
# Purely descriptive. Writes nothing, changes nothing. Read-only over
# trade_log.db and research_archive.db's pattern_flags table.

import logging
import sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from datetime import datetime

from core.pattern_memory import DB_PATH, _parse_entry_context
from core.trade_log import trade_logger

logger = logging.getLogger(__name__)

HIGH_OVERLAP_THRESHOLD = 0.5  # >= this fraction of the smaller bucket shared -> flag as likely same root cause


def _build_membership(limit: int = 2000) -> dict:
    """
    Returns {(dimension_type, dimension_value): set(trade_id)}.
    Mirrors pattern_memory.refresh()'s grouping logic for the dimension
    types pattern_detection.py can flag (strategy, entry_hour, regime,
    strategy_regime) -- deliberately NOT exit_reason or
    entry_hour_exit_reason, since those are excluded from flagging anyway
    (see pattern_detection.py's FLAGGABLE_DIMENSIONS).
    """
    all_trades = trade_logger.get_trades(status="CLOSED", limit=limit)
    membership = defaultdict(set)

    for t in all_trades:
        tid = t.get("id")
        if tid is None:
            continue

        strat = t.get("strategy", "unknown")
        membership[("strategy", strat)].add(tid)

        try:
            hour = datetime.fromisoformat(t.get("entry_time", "")).hour
            membership[("entry_hour", f"{hour:02d}")].add(tid)
        except Exception:
            pass

        ctx = _parse_entry_context(t.get("entry_context", ""))
        regime = ctx.get("gex_regime") or ctx.get("direction") or "no_context_captured"
        membership[("regime", regime)].add(tid)
        membership[("strategy_regime", f"{strat}|{regime}")].add(tid)

    return membership


def check(flag_type: str = "CONCERNING", db_path: Path = DB_PATH) -> str:
    """
    Reads current pattern_flags of the given type, computes pairwise trade-id
    overlap, and returns a readable report. Does not call detect() first --
    run `python3 -m core.pattern_detection detect` beforehand if you want
    current flags.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        flags = conn.execute(
            "SELECT dimension_type, dimension_value, trade_count, win_rate, net_pnl "
            "FROM pattern_flags WHERE flag_type = ? ORDER BY net_pnl ASC",
            (flag_type.upper(),),
        ).fetchall()

    if len(flags) < 2:
        return f"Fewer than 2 {flag_type.upper()} flags — nothing to compare."

    membership = _build_membership()

    pairs = []
    for a, b in combinations(flags, 2):
        set_a = membership.get((a["dimension_type"], a["dimension_value"]), set())
        set_b = membership.get((b["dimension_type"], b["dimension_value"]), set())
        if not set_a or not set_b:
            continue
        shared = set_a & set_b
        smaller = min(len(set_a), len(set_b))
        overlap = len(shared) / smaller if smaller else 0.0
        pairs.append((overlap, a, b, len(shared), len(set_a), len(set_b)))

    pairs.sort(key=lambda p: -p[0])

    lines = []
    lines.append("=" * 100)
    lines.append(f"PATTERN OVERLAP CHECK — {flag_type.upper()} flags, pairwise")
    lines.append("(overlap = shared trades / smaller bucket's trade count)")
    lines.append("=" * 100)

    high = [p for p in pairs if p[0] >= HIGH_OVERLAP_THRESHOLD]
    low = [p for p in pairs if p[0] < HIGH_OVERLAP_THRESHOLD]

    lines.append("")
    lines.append(f"-- HIGH OVERLAP (>= {int(HIGH_OVERLAP_THRESHOLD*100)}%, likely same root cause) --")
    if not high:
        lines.append("  (none)")
    for overlap, a, b, shared, na, nb in high:
        lines.append(
            f"  {overlap:5.0%}  [{a['dimension_type']}] {a['dimension_value']:22s} <-> "
            f"[{b['dimension_type']}] {b['dimension_value']:22s}  "
            f"({shared} shared trades of {na}/{nb})"
        )

    lines.append("")
    lines.append("-- LOW / NO OVERLAP (likely independent findings) --")
    if not low:
        lines.append("  (none)")
    for overlap, a, b, shared, na, nb in low:
        lines.append(
            f"  {overlap:5.0%}  [{a['dimension_type']}] {a['dimension_value']:22s} <-> "
            f"[{b['dimension_type']}] {b['dimension_value']:22s}  "
            f"({shared} shared trades of {na}/{nb})"
        )

    lines.append("")
    lines.append("(Descriptive only. High overlap doesn't mean either flag is wrong -- it means "
                  "they're two views of the same trades. Worth deciding by hand which framing is "
                  "more actionable before Step 8 drafts a proposal from it.)")
    lines.append("=" * 100)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    ft = sys.argv[1] if len(sys.argv) > 1 else "CONCERNING"
    print(check(flag_type=ft))
