# core/hypothesis_engine.py
#
# Step 12 of the staged self-learning plan.
#
# WHAT THIS DOES: sits between pattern_detection.py (Step 7) and
# candidate_generator.py (Step 8). A pattern_flag on its own is just an
# observation ("survivor|PE crossed the concerning threshold, once"). This
# module turns a flag into a HYPOTHESIS -- a named, tracked claim that can
# gather supporting or contradicting evidence over multiple real backtest-
# gate runs (Step 9) and be explicitly promoted to SUPPORTED/DISPROVEN, or
# left OPEN/INCONCLUSIVE. This is the "Observation vs Hypothesis vs
# Validated Pattern" distinction called for by the original research-memory
# spec, applied to what this repo actually has: 4 named strategies and a
# handful of known config levers, not a multi-family strategy-generation
# program.
#
# EXPLICITLY NOT INCLUDED, ON PURPOSE -- same philosophy as the rest of
# this self-learning layer:
#   - No autonomous parameter changes of any kind. This module only reads
#     research_archive.db's pattern_flags / candidate_gate_results tables
#     and writes to a new hypotheses table in the SAME database. It never
#     touches trade_log.db, risk_manager.py, or any strategy file.
#   - Does not decide what to DO about a hypothesis -- candidate_generator
#     (Step 8) still owns "does this map to a known, safe lever". This
#     module only tracks whether the underlying claim is gathering
#     evidence for or against it.
#   - Confidence here is a separate, lower-stakes question than
#     confidence_gate.py's (Step 11) auto-apply eligibility. A hypothesis
#     can be labeled MEDIUM confidence long before -- or even without ever
#     -- qualifying for autonomous application. Reuses the SAME
#     candidate_gate_results table confidence_gate.py reads (not a second,
#     disconnected count), just with a different, more permissive
#     threshold appropriate for "is this pattern probably real" rather
#     than "can this go live with real money unattended".
#   - raise_from_flags() does not refresh pattern_memory or re-run
#     pattern_detection first -- same "caller decides whether stale data
#     is acceptable" convention as pattern_detection.detect() itself.

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.pattern_memory import DB_PATH

logger = logging.getLogger(__name__)

# Lower-stakes thresholds than confidence_gate.py's MIN_DISTINCT_DAYS=10 /
# MIN_FAVOR_PCT=0.8 (Step 11) -- those gate real unattended config changes.
# These just label how much real evidence a hypothesis has accumulated so
# far, for a human (or a future session) deciding what to look at next.
MIN_EXPERIMENTS_FOR_LABEL = 3   # below this, confidence stays LOW no matter what
MEDIUM_CONFIDENCE_FAVOR_PCT = 0.6
HIGH_CONFIDENCE_FAVOR_PCT = 0.8
HIGH_CONFIDENCE_MIN_EXPERIMENTS = 8
DISPROVEN_FAVOR_PCT = 0.2   # below this (with enough experiments) -> DISPROVEN

VALID_STATUSES = ("OPEN", "TESTING", "SUPPORTED", "DISPROVEN", "INCONCLUSIVE")
VALID_CONFIDENCE = ("LOW", "MEDIUM", "HIGH")


def _init_db(db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses (
                hypothesis_id           TEXT PRIMARY KEY,
                statement                TEXT NOT NULL,
                reason                   TEXT NOT NULL,
                source_dimension_type    TEXT,
                source_dimension_value   TEXT,
                status                   TEXT NOT NULL,
                confidence               TEXT NOT NULL,
                linked_candidate_id      TEXT,
                supporting_experiments   TEXT NOT NULL,
                contradicting_experiments TEXT NOT NULL,
                next_test                TEXT,
                created_at               TEXT NOT NULL,
                updated_at               TEXT NOT NULL,
                recorded_in_lessons      INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hypotheses_source "
            "ON hypotheses(source_dimension_type, source_dimension_value)"
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["supporting_experiments"] = json.loads(d["supporting_experiments"] or "[]")
    d["contradicting_experiments"] = json.loads(d["contradicting_experiments"] or "[]")
    d["recorded_in_lessons"] = bool(d["recorded_in_lessons"])
    return d


def _statement_for_flag(dimension_type: str, dimension_value: str, flag_type: str,
                         flag_reason: str) -> tuple:
    """Returns (statement, reason, next_test) in the spec's hypothesis language."""
    if flag_type == "CONCERNING":
        statement = (
            f"{dimension_type}={dimension_value} is a losing condition that should be "
            f"reduced or avoided, not treated as normal variance."
        )
        next_test = (
            f"Run a controlled comparison (Step 9 backtest gate) isolating whatever "
            f"lever governs {dimension_value}, current vs. a reduced/disabled state, "
            f"across multiple distinct days."
        )
    else:  # PROMISING
        statement = (
            f"{dimension_type}={dimension_value} is a genuinely favourable condition, "
            f"not a small-sample fluke, and may be worth leaning into."
        )
        next_test = (
            f"Run a controlled comparison (Step 9 backtest gate) to see whether the "
            f"apparent edge in {dimension_value} survives on additional out-of-sample days "
            f"(EXPLOIT: does it persist? CHALLENGE: does removing whatever's distinctive "
            f"about it erase the edge?)."
        )
    reason = f"Raised from a {flag_type} pattern_flag: {flag_reason}"
    return statement, reason, next_test


def raise_from_flags(min_trades: int = 5, db_path: Path = DB_PATH) -> list:
    """
    Reads current pattern_flags (CONCERNING or PROMISING) and creates a new
    OPEN hypothesis for any (dimension_type, dimension_value) that doesn't
    already have ANY hypothesis (live OR resolved) -- idempotent, safe to
    call every session.

    Deliberately checks ALL statuses, not just OPEN/TESTING: once a
    hypothesis resolves to SUPPORTED or DISPROVEN, that's meant to be a
    durable, lessons.md-recorded conclusion. A genuinely new investigation
    into an already-resolved question needs a human to explicitly re-open
    it (not implemented here -- out of scope for this step).

    Returns the list of newly created hypothesis dicts.
    """
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        flags = conn.execute(
            "SELECT * FROM pattern_flags WHERE trade_count >= ? "
            "ORDER BY dimension_type, dimension_value",
            (min_trades,),
        ).fetchall()
        existing_any = conn.execute(
            "SELECT source_dimension_type, source_dimension_value FROM hypotheses"
        ).fetchall()

    live_keys = {(r["source_dimension_type"], r["source_dimension_value"]) for r in existing_any}

    created = []
    now = datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        for f in flags:
            key = (f["dimension_type"], f["dimension_value"])
            if key in live_keys:
                continue
            statement, reason, next_test = _statement_for_flag(
                f["dimension_type"], f["dimension_value"], f["flag_type"], f["flag_reason"]
            )
            hid = f"HYP-{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT INTO hypotheses "
                "(hypothesis_id, statement, reason, source_dimension_type, "
                "source_dimension_value, status, confidence, linked_candidate_id, "
                "supporting_experiments, contradicting_experiments, next_test, "
                "created_at, updated_at, recorded_in_lessons) "
                "VALUES (?, ?, ?, ?, ?, 'OPEN', 'LOW', NULL, '[]', '[]', ?, ?, ?, 0)",
                (hid, statement, reason, f["dimension_type"], f["dimension_value"],
                 next_test, now, now),
            )
            live_keys.add(key)  # guard against dupes within the same flag batch
            created.append({
                "hypothesis_id": hid, "statement": statement, "reason": reason,
                "source_dimension_type": f["dimension_type"],
                "source_dimension_value": f["dimension_value"],
                "next_test": next_test,
            })
        conn.commit()

    logger.info(f"[hypothesis_engine] Raised {len(created)} new hypotheses from flags")
    return created


def link_candidate(hypothesis_id: str, candidate_id: str, db_path: Path = DB_PATH) -> bool:
    """Links a hypothesis to a candidate_config.json proposal id and moves it
    to TESTING. Called by candidate_generator.py right after propose_change()
    succeeds for a flag that has a live hypothesis."""
    _init_db(db_path)
    now = datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE hypotheses SET linked_candidate_id = ?, status = 'TESTING', "
            "updated_at = ? WHERE hypothesis_id = ? AND status = 'OPEN'",
            (candidate_id, now, hypothesis_id),
        )
        conn.commit()
        return cur.rowcount > 0


def find_hypothesis_for_flag(dimension_type: str, dimension_value: str,
                              db_path: Path = DB_PATH) -> Optional[dict]:
    """Returns the live (OPEN/TESTING) hypothesis for this flag shape, if any."""
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM hypotheses WHERE source_dimension_type = ? "
            "AND source_dimension_value = ? AND status IN ('OPEN', 'TESTING') "
            "ORDER BY created_at DESC LIMIT 1",
            (dimension_type, dimension_value),
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_hypothesis_for_candidate(candidate_id: str, db_path: Path = DB_PATH) -> Optional[dict]:
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM hypotheses WHERE linked_candidate_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def record_gate_result(candidate_id: str, favors_candidate: bool, diff: float,
                        window_start: str = "", window_end: str = "",
                        db_path: Path = DB_PATH) -> Optional[dict]:
    """
    Called after run_candidate_backtest_gate.py saves a gate result (Step 9).
    Appends the result to the linked hypothesis's supporting/contradicting
    list and recomputes status/confidence purely from accumulated evidence
    -- same "always consistent, recomputed not incrementally patched"
    philosophy as pattern_memory/pattern_detection. Returns the updated
    hypothesis dict, or None if this candidate isn't linked to any
    hypothesis (perfectly normal -- not every candidate originates from a
    flag-derived hypothesis).
    """
    hyp = find_hypothesis_for_candidate(candidate_id, db_path)
    if hyp is None:
        return None

    _init_db(db_path)
    entry = {
        "candidate_id": candidate_id, "diff": diff,
        "window_start": window_start, "window_end": window_end,
        "recorded_at": datetime.now().isoformat(),
    }
    supporting = hyp["supporting_experiments"]
    contradicting = hyp["contradicting_experiments"]
    if favors_candidate:
        supporting.append(entry)
    else:
        contradicting.append(entry)

    total = len(supporting) + len(contradicting)
    favor_pct = len(supporting) / total if total else 0.0

    if total < MIN_EXPERIMENTS_FOR_LABEL:
        confidence = "LOW"
        status = "TESTING"
    elif favor_pct <= DISPROVEN_FAVOR_PCT:
        confidence = "MEDIUM" if total >= MIN_EXPERIMENTS_FOR_LABEL else "LOW"
        status = "DISPROVEN"
    elif favor_pct >= HIGH_CONFIDENCE_FAVOR_PCT and total >= HIGH_CONFIDENCE_MIN_EXPERIMENTS:
        confidence = "HIGH"
        status = "SUPPORTED"
    elif favor_pct >= MEDIUM_CONFIDENCE_FAVOR_PCT:
        confidence = "MEDIUM"
        status = "SUPPORTED"
    else:
        confidence = "LOW"
        status = "INCONCLUSIVE"

    now = datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE hypotheses SET supporting_experiments = ?, contradicting_experiments = ?, "
            "status = ?, confidence = ?, updated_at = ? WHERE hypothesis_id = ?",
            (json.dumps(supporting), json.dumps(contradicting), status, confidence,
             now, hyp["hypothesis_id"]),
        )
        conn.commit()

    logger.info(f"[hypothesis_engine] {hyp['hypothesis_id']} -> {status} "
                f"({confidence} confidence, {total} experiments, {favor_pct:.0%} favouring)")
    return find_hypothesis(hyp["hypothesis_id"], db_path)


def find_hypothesis(hypothesis_id: str, db_path: Path = DB_PATH) -> Optional[dict]:
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM hypotheses WHERE hypothesis_id = ?", (hypothesis_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_hypotheses(status: Optional[str] = None, db_path: Path = DB_PATH) -> list:
    _init_db(db_path)
    query = "SELECT * FROM hypotheses"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status.upper())
    query += " ORDER BY updated_at DESC"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_recorded_in_lessons(hypothesis_id: str, db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE hypotheses SET recorded_in_lessons = 1 WHERE hypothesis_id = ?",
            (hypothesis_id,),
        )
        conn.commit()


def show(status: Optional[str] = None, db_path: Path = DB_PATH) -> str:
    hyps = list_hypotheses(status=status, db_path=db_path)
    if not hyps:
        return ("No hypotheses found" + (f" with status={status}" if status else "") +
                ". Run `python3 -m core.hypothesis_engine raise` first.")

    lines = ["=" * 100, f"HYPOTHESES{f' — {status.upper()}' if status else ''}", "=" * 100]
    for h in hyps:
        lines.append("")
        lines.append(f"[{h['hypothesis_id']}] {h['status']} ({h['confidence']} confidence)")
        lines.append(f"  Statement: {h['statement']}")
        lines.append(f"  Reason:    {h['reason']}")
        n_sup, n_con = len(h["supporting_experiments"]), len(h["contradicting_experiments"])
        lines.append(f"  Evidence:  {n_sup} supporting, {n_con} contradicting experiments")
        if h.get("linked_candidate_id"):
            lines.append(f"  Candidate: {h['linked_candidate_id']}")
        if h.get("next_test"):
            lines.append(f"  Next test: {h['next_test']}")
    lines.append("")
    lines.append("=" * 100)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2 or sys.argv[1] not in ("raise", "show"):
        print("Usage: python3 -m core.hypothesis_engine raise")
        print("       python3 -m core.hypothesis_engine show [OPEN|TESTING|SUPPORTED|DISPROVEN|INCONCLUSIVE]")
        sys.exit(1)

    if sys.argv[1] == "raise":
        created = raise_from_flags()
        print(f"Raised {len(created)} new hypotheses.")
        for h in created:
            print(f"  [{h['hypothesis_id']}] {h['statement']}")
    elif sys.argv[1] == "show":
        st = sys.argv[2] if len(sys.argv) > 2 else None
        print(show(status=st))
