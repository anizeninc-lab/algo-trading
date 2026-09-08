"""
run_closed_loop_session.py

Closes the one real gap identified in the 2026-09-06 review of the
self-learning pipeline: run_research_session.run() stops at "candidates
proposed" -- picking a candidate and running its backtest gate was still
a manual step every time. This script adds that one missing step.

PIPELINE (each numbered step is an EXISTING, already-working module --
this script adds no new analysis logic, purely sequencing + picking):

  1. run_research_session.run()               -- the full existing
                                                   pipeline: refresh
                                                   pattern_memory, detect
                                                   flags, raise
                                                   hypotheses, propose
                                                   candidates, update
                                                   lessons/recommendations
  2. _select_candidate()                      -- NEW, but small: pick ONE
                                                   'proposed' candidate
                                                   that (a) the gate can
                                                   actually test (Step 9's
                                                   PARAMETER_TO_FLAG) and
                                                   (b) is currently
                                                   highest priority
  3. run_candidate_backtest_gate.gate()       -- the existing Step 9
                                                   A/B backtest, unchanged
  4. research_memory.update_lessons() +
     update_recommendations()                 -- re-run AFTER the gate,
                                                   since record_gate_result
                                                   (called inside gate())
                                                   may have just resolved
                                                   a hypothesis to
                                                   SUPPORTED/DISPROVEN,
                                                   which the first pass in
                                                   step 1 ran too early to
                                                   see

WHAT THIS DELIBERATELY DOES NOT DO -- same philosophy as candidate_config.py
and candidate_generator.py (see their docstrings):
  - Never calls candidate_config.decide(). A gate result is one more piece
    of evidence attached to a hypothesis; approving, rejecting, or
    applying a candidate is still a manual human step, same as always.
    This script's whole output is still just "here's what happened,"
    the same as a human running Steps 1 and 9 back to back by hand.
  - Never touches live trading, risk_manager state, or any strategy file.
  - Gates at most ONE candidate per run, not all outstanding ones -- keeps
    each run's output readable and matches "one day's gate = one sanity
    check" from run_candidate_backtest_gate.py's own closing note. Run
    this script again (e.g. daily, once more candles are archived) to
    work through others.
  - Only survivor candidates are gateable today (run_candidate_backtest_
    gate.py's own restriction) -- bn_survivor's pe_enabled/ce_enabled
    proposals still print via candidate_generator but this script skips
    them, same as calling the gate on them by hand would.

PICKING LOGIC (_select_candidate), in priority order:
  1. A candidate this SAME session just proposed (session_result[
     "candidates_proposed"]), which candidate_generator.generate() already
     returns ordered by net_pnl ASC (worst loss first) -- the same
     priority proxy the existing pipeline already uses, not a new one.
  2. Failing that, any pre-existing 'proposed' candidate from an earlier
     session that's still undecided, preferring one linked to a live
     (OPEN/TESTING) hypothesis -- gating exactly that link is the reason
     Step 12's hypothesis engine exists -- else the oldest undecided one.
  Both are filtered to strategy='survivor' and a parameter in
  run_candidate_backtest_gate.PARAMETER_TO_FLAG, since those are the only
  ones the gate can actually run.

BACKTEST WINDOW: if --start/--end aren't given, defaults to the single
most recent trading day present in candles_1min (MAX(ts), symbol='NIFTY')
-- i.e. "gate against the most recently archived day" with no manual
lookup. Pass --start/--end (same format run_survivor_backtest.py takes)
to override.

Usage:
    python3 run_closed_loop_session.py
    python3 run_closed_loop_session.py --start "2026-08-25 09:15" --end "2026-08-25"
    python3 run_closed_loop_session.py --min-trades 5 --tick-sleep 0
"""
import argparse
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RESEARCH_DB_PATH = Path(__file__).parent / "research_archive.db"


def _default_window(db_path: Path = RESEARCH_DB_PATH) -> tuple:
    """
    Most recent trading day with archived index candles, as (start, end).
    end is deliberately a BARE date ('YYYY-MM-DD'), not a time -- see
    IndexReplay._normalize_end_ts in core/research/survivor_backtest.py,
    which anchors a bare end date to end-of-day. Passing a bare date here
    is the documented, correct way to say "all of this day," not a bug.
    Returns (None, None) if no candles are archived at all.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT MAX(ts) FROM candles_1min WHERE symbol = 'NIFTY'"
            ).fetchone()
    except sqlite3.OperationalError:
        # candles_1min doesn't exist yet -- e.g. a fresh research_archive.db
        # that's never had index data archived into it. Same "nothing to
        # pick a window from" outcome as an empty table.
        return None, None
    if not row or not row[0]:
        return None, None
    last_day = row[0][:10]  # 'YYYY-MM-DD' prefix of 'YYYY-MM-DD HH:MM'
    return f"{last_day} 09:15", last_day


def _select_candidate(session_result: dict, db_path: Path = RESEARCH_DB_PATH) -> tuple:
    """
    Returns (candidate_dict, reason_str), or (None, None) if nothing
    gateable is available right now.
    """
    from run_candidate_backtest_gate import PARAMETER_TO_FLAG
    from core.candidate_config import list_candidates
    from core import hypothesis_engine

    def _gateable(c: dict) -> bool:
        return c.get("strategy") == "survivor" and c.get("parameter") in PARAMETER_TO_FLAG

    # Preference 1: freshly proposed this session, already net_pnl-ASC
    # ordered by candidate_generator.generate() (worst loss first).
    proposed_this_session = list_candidates(status="proposed")
    by_id = {c["id"]: c for c in proposed_this_session}
    for _flag, cid in session_result.get("candidates_proposed", []):
        cand = by_id.get(cid)
        if cand and _gateable(cand):
            return cand, "freshly proposed this session"

    # Preference 2: a pre-existing undecided proposal, favoring one
    # linked to a still-live hypothesis.
    candidates = [c for c in list_candidates(status="proposed") if _gateable(c)]
    if not candidates:
        return None, None

    def _has_live_hypothesis(c: dict) -> bool:
        hyp = hypothesis_engine.find_hypothesis_for_candidate(c["id"], db_path=db_path)
        return hyp is not None and hyp["status"] in ("OPEN", "TESTING")

    linked = [c for c in candidates if _has_live_hypothesis(c)]
    if linked:
        return linked[0], "pre-existing proposal linked to a live hypothesis"
    return candidates[0], "pre-existing undecided proposal (oldest first)"


def _gate_result_row(candidate_id: str, window_start: str, window_end: str,
                      db_path: Path = RESEARCH_DB_PATH) -> Optional[dict]:
    """
    Reads back the row run_candidate_backtest_gate._save_gate_result()
    just wrote, rather than reimplementing what it computes. Returns None
    if the gate run didn't produce a parseable, savable result (e.g. one
    of the two backtest subprocesses failed).
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM candidate_gate_results WHERE candidate_id = ? "
                "AND window_start = ? AND window_end = ? ORDER BY id DESC LIMIT 1",
                (candidate_id, window_start, window_end),
            ).fetchone()
    except sqlite3.OperationalError:
        # _save_gate_result() (which creates this table) is only reached
        # if BOTH backtest subprocesses produced a parseable result -- if
        # gate() printed "Could not compare," the table may not exist yet
        # at all. Same "no result to show" outcome as a genuine empty read.
        return None
    return dict(row) if row else None


def run(min_trades: int = 5, start: Optional[str] = None, end: Optional[str] = None,
        tick_sleep: float = 0.1, db_path: Path = RESEARCH_DB_PATH) -> dict:
    import run_research_session
    import run_candidate_backtest_gate as gate_module
    from core import research_memory

    session_result = run_research_session.run(min_trades=min_trades, db_path=db_path)

    candidate, pick_reason = _select_candidate(session_result, db_path)
    gate_note = None
    gate_row = None
    window_used = (None, None)

    if candidate is None:
        gate_note = ("No gateable survivor candidate available this run (nothing new "
                     "proposed, and no pre-existing undecided one with a known lever) "
                     "-- closed-loop gate step skipped.")
        logger.info(f"[closed_loop] {gate_note}")
    else:
        run_start, run_end = start, end
        if run_start is None or run_end is None:
            auto_start, auto_end = _default_window(db_path)
            if auto_start is None:
                gate_note = ("A gateable candidate exists but no archived candles_1min "
                             "data was found to pick a default window from -- pass "
                             "--start/--end explicitly. Gate step skipped.")
                logger.warning(f"[closed_loop] {gate_note}")
                candidate = None
            else:
                run_start, run_end = run_start or auto_start, run_end or auto_end

        if candidate is not None:
            window_used = (run_start, run_end)
            gate_note = (f"Gating candidate {candidate['id']} "
                         f"({candidate['strategy']}.{candidate['parameter']}: "
                         f"{candidate['current_value']} -> {candidate['proposed_value']}) "
                         f"-- {pick_reason}. Window {run_start} to {run_end}.")
            logger.info(f"[closed_loop] {gate_note}")
            gate_module.gate(candidate["id"], run_start, run_end, tick_sleep=tick_sleep)
            gate_row = _gate_result_row(candidate["id"], run_start, run_end, db_path=db_path)

    # Re-run AFTER the gate: record_gate_result() (called inside gate())
    # may have just resolved a hypothesis to SUPPORTED/DISPROVEN, which
    # step 1's pass ran too early in this same invocation to see.
    new_lessons_after_gate = research_memory.update_lessons(db_path=db_path)
    n_open_after_gate = research_memory.update_recommendations(db_path=db_path)

    return {
        "session_result": session_result,
        "candidate_gated": candidate,
        "pick_reason": pick_reason,
        "gate_note": gate_note,
        "gate_row": gate_row,
        "window_used": window_used,
        "new_lessons_after_gate": new_lessons_after_gate,
        "open_hypotheses_after_gate": n_open_after_gate,
    }


def print_report(result: dict) -> None:
    import run_research_session as rrs
    rrs.print_report(result["session_result"])

    print()
    print("=" * 100)
    print("CLOSED-LOOP GATE STEP")
    print("=" * 100)
    print(result["gate_note"])

    row = result["gate_row"]
    if row:
        print()
        print(f"  Baseline  ({row['parameter']}={row['current_value']}):  "
              f"{row['baseline_trades']} trades, P&L {row['baseline_pnl']:+.2f}")
        print(f"  Candidate ({row['parameter']}={row['proposed_value']}): "
              f"{row['candidate_trades']} trades, P&L {row['candidate_pnl']:+.2f}")
        print(f"  Difference: {row['diff']:+.2f} "
              f"({'favors candidate' if row['favors_candidate'] else 'favors baseline'})")
        print()
        print("  This is ONE day's data -- a sanity check, not a verdict. Run again as "
              "more days are archived before deciding.")
        print(f"  When ready: python3 -m core.candidate_config decide "
              f"{result['candidate_gated']['id']} approved|rejected --note \"...\"")

    if result["new_lessons_after_gate"]:
        print()
        print(f"-- NEW LESSONS FROM THIS GATE RESULT ({len(result['new_lessons_after_gate'])}) --")
        for h in result["new_lessons_after_gate"]:
            print(f"  [{h['hypothesis_id']}] {h['status']}: {h['statement']}")

    print()
    print(f"Open hypotheses remaining: {result['open_hypotheses_after_gate']}")
    print("Nothing here changed live trading, risk_manager state, or any strategy file. "
          "No candidate was approved, rejected, or applied automatically.")
    print("=" * 100)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Run one full research session, then backtest-gate the top gateable candidate"
    )
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--start", default=None, help="Backtest window start, e.g. '2026-08-25 09:15'")
    parser.add_argument("--end", default=None, help="Backtest window end, e.g. '2026-08-25' (bare date = end of day)")
    parser.add_argument("--tick-sleep", type=float, default=0.1)
    args = parser.parse_args()

    result = run(min_trades=args.min_trades, start=args.start, end=args.end,
                 tick_sleep=args.tick_sleep)
    print_report(result)
