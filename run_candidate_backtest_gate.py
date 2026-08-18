"""
run_candidate_backtest_gate.py

Step 9 of the staged self-learning plan: automates the exact manual A/B
comparison done by hand on Aug 15 2026 (run baseline, run candidate,
compare P&L) for any 'proposed' survivor candidate in
configs/candidate_config.json that maps to a known backtest override flag.

WHAT THIS DOES: runs run_survivor_backtest.py TWICE as separate
subprocesses -- once with the candidate's current_value, once with its
proposed_value, same date range both times -- and prints a side-by-side
comparison. Nothing is approved, rejected, or applied automatically; this
is purely informational; a human still runs
`python3 -m core.candidate_config decide <id> approved|rejected` themselves
after reading the comparison.

WHY SUBPROCESSES, NOT TWO IN-PROCESS RUNS: run_survivor_backtest.py leans
on several real singletons (risk_manager, market_context, vix_manager)
that don't have a clean "full reset" method between runs -- see the
BACKTEST_TRADE_DB/BACKTEST_RISK_STATE contamination bug found and fixed on
Aug 15 (a second run in the same process picked up the first run's
leftover trades and immediately halted). A fresh OS process per backtest
sidesteps that category of bug entirely, at the cost of being slower.

ONLY SUPPORTS SURVIVOR CANDIDATES WHOSE PARAMETER MAPS TO A KNOWN BACKTEST
FLAG (see PARAMETER_TO_FLAG below) -- currently pe_quantity, ce_quantity,
pe_enabled, ce_enabled. Anything else (a different strategy, or a
parameter with no backtest lever) prints a clear "can't gate this one"
message rather than guessing.

Usage:
    python3 run_candidate_backtest_gate.py --candidate-id bed3749a --start "2026-08-14 09:15" --end "2026-08-14 15:10"
"""
import argparse
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime

from core.candidate_config import list_candidates
from core.pattern_memory import DB_PATH as RESEARCH_DB_PATH

PARAMETER_TO_FLAG = {
    "pe_quantity": "--pe-quantity",
    "ce_quantity": "--ce-quantity",
    "pe_enabled": "--pe-enabled",
    "ce_enabled": "--ce-enabled",
    "min_regime_stability": "--min-regime-stability",
}

PNL_RE = re.compile(r"Simulated P&L:\s*([+-]?[\d,]+\.\d+)")
TRADES_RE = re.compile(r"Trades:\s*(\d+)")


def _init_gate_results_table(db_path=RESEARCH_DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_gate_results (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id     TEXT NOT NULL,
                tested_at        TEXT NOT NULL,
                window_start     TEXT NOT NULL,
                window_end       TEXT NOT NULL,
                parameter        TEXT NOT NULL,
                current_value    TEXT NOT NULL,
                proposed_value   TEXT NOT NULL,
                baseline_pnl     REAL NOT NULL,
                baseline_trades  INTEGER NOT NULL,
                candidate_pnl    REAL NOT NULL,
                candidate_trades INTEGER NOT NULL,
                diff             REAL NOT NULL,
                favors_candidate INTEGER NOT NULL
            )
        """)
        conn.commit()


def _save_gate_result(candidate_id: str, window_start: str, window_end: str,
                       parameter: str, current_value, proposed_value,
                       baseline: dict, candidate_result: dict,
                       db_path=RESEARCH_DB_PATH) -> None:
    """
    Persists one gate run so confidence can build up across many real days
    (see core/confidence_gate.py, Step 11). Idempotent by (candidate_id,
    window_start, window_end) -- re-running the gate on the same day again
    replaces that day's result rather than double-counting it, so a
    candidate's confidence reflects distinct days tested, not how many
    times someone happened to re-run the gate.
    """
    _init_gate_results_table(db_path)
    diff = candidate_result["pnl"] - baseline["pnl"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM candidate_gate_results WHERE candidate_id = ? "
            "AND window_start = ? AND window_end = ?",
            (candidate_id, window_start, window_end),
        )
        conn.execute(
            "INSERT INTO candidate_gate_results "
            "(candidate_id, tested_at, window_start, window_end, parameter, "
            "current_value, proposed_value, baseline_pnl, baseline_trades, "
            "candidate_pnl, candidate_trades, diff, favors_candidate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, datetime.now().isoformat(), window_start, window_end,
             parameter, str(current_value), str(proposed_value),
             baseline["pnl"], baseline["trades"],
             candidate_result["pnl"], candidate_result["trades"],
             diff, 1 if diff > 0 else 0),
        )
        conn.commit()


def _run_backtest(start: str, end: str, tick_sleep: float,
                   flag: str = None, value=None) -> dict:
    cmd = [sys.executable, "run_survivor_backtest.py",
           "--start", start, "--end", end, "--tick-sleep", str(tick_sleep)]
    if flag is not None:
        cmd += [flag, str(value)]

    print(f"[gate] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + result.stderr

    pnl_match = PNL_RE.search(output)
    trades_match = TRADES_RE.search(output)
    if pnl_match is None or trades_match is None:
        print("[gate] WARNING: couldn't parse a result from this run -- full output below:")
        print(output[-3000:])
        return {"pnl": None, "trades": None, "raw": output}

    return {
        "pnl": float(pnl_match.group(1).replace(",", "")),
        "trades": int(trades_match.group(1)),
        "raw": output,
    }


def gate(candidate_id: str, start: str, end: str, tick_sleep: float = 0.1) -> None:
    candidates = list_candidates()
    candidate = next((c for c in candidates if c["id"] == candidate_id), None)
    if candidate is None:
        print(f"[gate] No candidate found with id={candidate_id}")
        return

    if candidate["strategy"] != "survivor":
        print(f"[gate] Only survivor candidates are backtest-gateable today "
              f"(this one is strategy={candidate['strategy']}). No backtest run.")
        return

    flag = PARAMETER_TO_FLAG.get(candidate["parameter"])
    if flag is None:
        print(f"[gate] No known backtest lever for parameter="
              f"{candidate['parameter']!r}. Known: {list(PARAMETER_TO_FLAG)}. "
              f"Can't gate this one -- review it by hand.")
        return

    print(f"[gate] Candidate {candidate_id}: {candidate['strategy']}.{candidate['parameter']} "
          f"{candidate['current_value']} -> {candidate['proposed_value']}")
    print(f"[gate] Window: {start} to {end}\n")

    print("=== BASELINE (current_value) ===")
    baseline = _run_backtest(start, end, tick_sleep, flag, candidate["current_value"])

    print("\n=== CANDIDATE (proposed_value) ===")
    candidate_result = _run_backtest(start, end, tick_sleep, flag, candidate["proposed_value"])

    print("\n" + "=" * 70)
    print(f"GATE RESULT — candidate {candidate_id}")
    print("=" * 70)
    if baseline["pnl"] is None or candidate_result["pnl"] is None:
        print("Could not compare -- one or both runs failed to produce a parseable result. "
              "See raw output above.")
        return

    print(f"Baseline  ({candidate['parameter']}={candidate['current_value']}):  "
          f"{baseline['trades']} trades, P&L {baseline['pnl']:+.2f}")
    print(f"Candidate ({candidate['parameter']}={candidate['proposed_value']}): "
          f"{candidate_result['trades']} trades, P&L {candidate_result['pnl']:+.2f}")
    diff = candidate_result["pnl"] - baseline["pnl"]
    print(f"Difference: {diff:+.2f}")

    _save_gate_result(candidate_id, start, end, candidate["parameter"],
                       candidate["current_value"], candidate["proposed_value"],
                       baseline, candidate_result)
    print(f"[gate] Result saved to candidate_gate_results (window {start} to {end})")
    print()
    print("This is ONE day's data -- not a statistically meaningful sample. "
          "Treat as a sanity check, not a verdict. Run over more days as they "
          "accumulate before deciding.")
    print(f"\nWhen ready: python3 -m core.candidate_config decide {candidate_id} "
          f"approved|rejected --note \"...\"")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest-gate a proposed survivor candidate")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--tick-sleep", type=float, default=0.1)
    args = parser.parse_args()
    gate(args.candidate_id, args.start, args.end, args.tick_sleep)
