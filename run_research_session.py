"""
run_research_session.py

A session-ID-stamped orchestrator that runs the full research pipeline in
order and records what happened, so there's a durable record of "what did
the research agent look at and do" across sessions -- not just the final
state of lessons.md/recommendations.md, but the actual sequence of runs.

PIPELINE (each step is an EXISTING module -- this script adds no new
analysis logic of its own, purely sequencing + a session record):

  1. core.pattern_memory.refresh()        (Step 6)  -- rebuild pattern_memory
                                                        from trade_log.db
  2. core.pattern_detection.detect()      (Step 7)  -- flag CONCERNING/
                                                        PROMISING dimensions
  3. core.pattern_overlap.check()                   -- diagnostic only,
                                                        logged not acted on
  4. core.hypothesis_engine.raise_from_flags() (Step 12) -- flags -> tracked
                                                        hypotheses
  5. core.candidate_generator.generate()  (Step 8)  -- CONCERNING flags with
                                                        a known lever ->
                                                        candidate proposals
                                                        (now hypothesis-
                                                        linked)
  6. core.research_memory.update_lessons()          -- newly resolved
     + update_recommendations()                        hypotheses -> durable
                                                        lessons.md, current
                                                        open ones ->
                                                        recommendations.md

Read-only w.r.t. trade_log.db, risk_manager.py, and every strategy file --
same isolation boundary as every module it calls. Writes only to
research_archive.db (a new research_sessions table) and the
research_memory/ markdown files.

Usage:
    python3 run_research_session.py
    python3 run_research_session.py --min-trades 5
    python3 run_research_session.py --list
"""
import argparse
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "research_archive.db"


def _init_sessions_table(db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_sessions (
                session_id             TEXT PRIMARY KEY,
                started_at             TEXT NOT NULL,
                finished_at            TEXT,
                pattern_memory_rows    INTEGER,
                pattern_flags_written  INTEGER,
                hypotheses_raised      INTEGER,
                candidates_proposed    INTEGER,
                new_lessons_recorded   INTEGER,
                open_hypotheses_after  INTEGER,
                summary                TEXT
            )
        """)
        conn.commit()


def _new_session_id() -> str:
    return f"SESSION-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


def run(min_trades: int = 5, db_path: Path = DB_PATH) -> dict:
    """
    Runs the full pipeline once, end to end, threading db_path through to
    every sub-step so a caller (e.g. a test) can point the whole session at
    a synthetic database instead of the real one.
    """
    from core import pattern_memory, pattern_detection, pattern_overlap
    from core import hypothesis_engine, candidate_generator, research_memory

    _init_sessions_table(db_path)
    session_id = _new_session_id()
    started_at = datetime.now().isoformat()
    logger.info(f"[research_session] {session_id} starting")

    # Read memory BEFORE doing anything else -- prior lessons, survivors,
    # failures, and open hypotheses should be considered before this
    # session's own actions.
    memory_before = research_memory.read_memory_summary(db_path=db_path)
    logger.info("[research_session] Memory considered before this session:\n" +
                research_memory.format_memory_considered(memory_before))

    pm_rows = pattern_memory.refresh(db_path=db_path)
    pf_written = pattern_detection.detect(min_trades=min_trades, db_path=db_path)

    overlap_report = ""
    if pf_written >= 2:
        overlap_report = pattern_overlap.check(flag_type="CONCERNING", db_path=db_path)
        logger.info(f"[research_session] Overlap check:\n{overlap_report}")

    new_hypotheses = hypothesis_engine.raise_from_flags(min_trades=min_trades, db_path=db_path)

    gen_result = candidate_generator.generate(db_path=db_path)
    n_proposed = len(gen_result["proposed"])

    new_lessons = research_memory.update_lessons(db_path=db_path)
    n_open_after = research_memory.update_recommendations(db_path=db_path)

    finished_at = datetime.now().isoformat()
    summary = (
        f"{pm_rows} pattern_memory rows, {pf_written} flags "
        f"({len(new_hypotheses)} new hypotheses raised), "
        f"{n_proposed} candidate(s) proposed, {len(new_lessons)} new lesson(s), "
        f"{n_open_after} open hypothesis/hypotheses remaining."
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO research_sessions "
            "(session_id, started_at, finished_at, pattern_memory_rows, "
            "pattern_flags_written, hypotheses_raised, candidates_proposed, "
            "new_lessons_recorded, open_hypotheses_after, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, started_at, finished_at, pm_rows, pf_written,
             len(new_hypotheses), n_proposed, len(new_lessons), n_open_after, summary),
        )
        conn.commit()

    logger.info(f"[research_session] {session_id} finished: {summary}")

    return {
        "session_id": session_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "pattern_memory_rows": pm_rows,
        "pattern_flags_written": pf_written,
        "new_hypotheses": new_hypotheses,
        "candidates_proposed": gen_result["proposed"],
        "candidates_manual_review": gen_result["manual_review"],
        "new_lessons": new_lessons,
        "open_hypotheses_after": n_open_after,
        "overlap_report": overlap_report,
        "summary": summary,
    }


def list_sessions(limit: int = 10, db_path: Path = DB_PATH) -> str:
    _init_sessions_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM research_sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return "No research sessions recorded yet. Run `python3 run_research_session.py` first."
    lines = ["=" * 100, "RESEARCH SESSIONS", "=" * 100]
    for r in rows:
        lines.append("")
        lines.append(f"[{r['session_id']}] {r['started_at']} -> {r['finished_at']}")
        lines.append(f"  {r['summary']}")
    lines.append("")
    lines.append("=" * 100)
    return "\n".join(lines)


def print_report(result: dict) -> None:
    print("=" * 100)
    print(f"RESEARCH SESSION {result['session_id']}")
    print("=" * 100)
    print(f"\n{result['summary']}\n")

    if result["new_hypotheses"]:
        print(f"-- NEW HYPOTHESES ({len(result['new_hypotheses'])}) --")
        for h in result["new_hypotheses"]:
            print(f"  [{h['hypothesis_id']}] {h['statement']}")
        print()

    if result["candidates_proposed"]:
        print(f"-- CANDIDATES PROPOSED ({len(result['candidates_proposed'])}) --")
        for f, cid in result["candidates_proposed"]:
            print(f"  [{cid}] {f['dimension_type']}={f['dimension_value']}")
        print()

    if result["new_lessons"]:
        print(f"-- NEW LESSONS RECORDED ({len(result['new_lessons'])}) --")
        for h in result["new_lessons"]:
            print(f"  [{h['hypothesis_id']}] {h['status']}: {h['statement']}")
        print()

    print(f"Open hypotheses remaining: {result['open_hypotheses_after']}")
    print("See research_memory/lessons.md and research_memory/recommendations.md for details.")
    print("Nothing here changed live trading, risk_manager state, or any strategy file.")
    print("=" * 100)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Run one full research-memory session")
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--list", action="store_true", help="List past sessions instead of running")
    args = parser.parse_args()

    if args.list:
        print(list_sessions())
    else:
        result = run(min_trades=args.min_trades)
        print_report(result)
