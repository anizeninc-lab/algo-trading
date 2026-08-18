# core/confidence_gate.py
#
# Step 11 of the staged self-learning plan (Aug 16 2026): confidence-gated
# LIMITED autonomy.
#
# WHAT "LIMITED" MEANS HERE, PRECISELY -- agreed explicitly with the user
# before writing this, given real money is involved and Aug 15's session
# found several subtle bugs in changes that looked correct on first read:
#   1. Only survivor.pe_enabled / survivor.ce_enabled going True -> False
#      can EVER auto-qualify (see RISK_REDUCING_ALLOWLIST). Enabling a side
#      (False -> True), or any other strategy/parameter, can never
#      auto-apply no matter how confident -- those always need a human.
#   2. Requires 10+ DISTINCT real trading days of backtest-gate evidence
#      (not 10 runs -- see run_candidate_backtest_gate.py's
#      _save_gate_result, which replaces same-day results rather than
#      letting someone pad the count by re-running the same day).
#   3. Requires the candidate's direction to be favoured on >=80% of those
#      days.
#   4. "Auto-apply" means: mark the candidate 'approved' with a clear audit
#      note, AND write the change into configs/saviour_combo.json. It does
#      NOT restart the bot. survivor's config is only read at process
#      startup (see main.py's load_config()) -- there is no live-reload
#      path for pe_enabled/ce_enabled the way risk_manager.
#      set_per_strategy_cap() exists for the capital cap. So even a fully
#      "autonomous" change here still needs a human (or your normal
#      restart cadence) to actually restart the bot before it takes
#      effect live -- one more real checkpoint before anything changes
#      for actual trading.
#   5. Master switch: nothing in this module does anything unless
#      configs/autonomy_config.json has {"enabled": true}. Defaults to
#      false. Ships false. Stays false until the user explicitly flips it
#      themselves.

import json
import logging
import sqlite3
from pathlib import Path

from core.candidate_config import list_candidates, decide
from core.pattern_memory import DB_PATH as RESEARCH_DB_PATH

logger = logging.getLogger(__name__)

AUTONOMY_CONFIG_PATH = Path("configs/autonomy_config.json")
LIVE_CONFIG_PATH = Path("configs/saviour_combo.json")
AUDIT_LOG_PATH = Path("logs/autonomy_actions.log")

MIN_DISTINCT_DAYS = 10
MIN_FAVOR_PCT = 0.8

# The ENTIRE set of changes this module is ever allowed to auto-apply.
# Deliberately hardcoded, not user-configurable via autonomy_config.json --
# loosening this should require editing code (and re-reading this
# docstring), not flipping a JSON value.
RISK_REDUCING_ALLOWLIST = {
    ("survivor", "pe_enabled", "True", "False"),
    ("survivor", "ce_enabled", "True", "False"),
}


def _autonomy_enabled() -> bool:
    if not AUTONOMY_CONFIG_PATH.exists():
        return False
    try:
        with open(AUTONOMY_CONFIG_PATH) as f:
            return json.load(f).get("enabled", False) is True
    except Exception as e:
        logger.warning(f"[confidence_gate] Could not read {AUTONOMY_CONFIG_PATH}: {e} -- treating as disabled")
        return False


def _ensure_gate_results_table(db_path=RESEARCH_DB_PATH) -> None:
    """Same table definition as run_candidate_backtest_gate.py's
    _init_gate_results_table -- duplicated deliberately rather than
    imported, so this read-only module doesn't depend on a script file.
    Idempotent (CREATE TABLE IF NOT EXISTS)."""
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


def compute_confidence(candidate_id: str, db_path=RESEARCH_DB_PATH) -> dict:
    """
    Reads all persisted gate results for this candidate and returns:
    {distinct_days, favor_pct, avg_diff, eligible, reason}
    'eligible' only reflects the DATA criteria (days tested + consistency)
    -- it does NOT check the allowlist or the master switch. See
    maybe_auto_apply() for the full decision.
    """
    _ensure_gate_results_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT window_start, window_end, diff, favors_candidate "
            "FROM candidate_gate_results WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchall()

    distinct_days = len(rows)
    if distinct_days == 0:
        return {"distinct_days": 0, "favor_pct": 0.0, "avg_diff": 0.0,
                "eligible": False, "reason": "No gate results recorded yet"}

    favoring = sum(1 for r in rows if r["favors_candidate"])
    favor_pct = favoring / distinct_days
    avg_diff = sum(r["diff"] for r in rows) / distinct_days

    if distinct_days < MIN_DISTINCT_DAYS:
        return {"distinct_days": distinct_days, "favor_pct": round(favor_pct, 3),
                "avg_diff": round(avg_diff, 2), "eligible": False,
                "reason": f"Only {distinct_days}/{MIN_DISTINCT_DAYS} distinct days tested"}

    if favor_pct < MIN_FAVOR_PCT:
        return {"distinct_days": distinct_days, "favor_pct": round(favor_pct, 3),
                "avg_diff": round(avg_diff, 2), "eligible": False,
                "reason": f"Only favoured on {favor_pct:.0%} of days, need {MIN_FAVOR_PCT:.0%}"}

    return {"distinct_days": distinct_days, "favor_pct": round(favor_pct, 3),
             "avg_diff": round(avg_diff, 2), "eligible": True,
             "reason": f"{distinct_days} days tested, {favor_pct:.0%} favourable"}


def _apply_live_config(parameter: str, proposed_value) -> bool:
    """Atomically writes ONE key into configs/saviour_combo.json. Same
    atomic-write pattern as risk_manager._save_state() (temp file + os
    .replace) so a process interruption mid-write can never leave the live
    config file truncated or corrupted."""
    import os
    try:
        cfg = {}
        if LIVE_CONFIG_PATH.exists():
            with open(LIVE_CONFIG_PATH) as f:
                cfg = json.load(f)
        cfg[parameter] = proposed_value
        tmp_path = LIVE_CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, LIVE_CONFIG_PATH)
        return True
    except Exception as e:
        logger.error(f"[confidence_gate] Failed to write {LIVE_CONFIG_PATH}: {e}")
        return False


def _audit_log(message: str) -> None:
    from datetime import datetime
    AUDIT_LOG_PATH.parent.mkdir(exist_ok=True)
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(f"{datetime.now().isoformat()} {message}\n")


def _alert(message: str) -> None:
    try:
        from core.alerting import send_telegram, LEVEL_WARNING
        send_telegram(message, LEVEL_WARNING)
    except Exception as e:
        logger.warning(f"[confidence_gate] Alert send failed (check Telegram config): {e}")


def maybe_auto_apply(candidate_id: str) -> dict:
    """
    The one function that can actually change something. Returns
    {"applied": bool, "reason": str}. Never raises on the "didn't apply"
    path -- only real failures during the apply step itself propagate as
    applied=False with an error reason.
    """
    if not _autonomy_enabled():
        return {"applied": False, "reason": "Autonomy master switch is off "
                f"({AUTONOMY_CONFIG_PATH} missing or enabled=false)"}

    candidates = list_candidates(status="proposed")
    candidate = next((c for c in candidates if c["id"] == candidate_id), None)
    if candidate is None:
        return {"applied": False, "reason": "Candidate not found or not in 'proposed' status"}

    key = (candidate["strategy"], candidate["parameter"],
           str(candidate["current_value"]), str(candidate["proposed_value"]))
    if key not in RISK_REDUCING_ALLOWLIST:
        return {"applied": False, "reason": f"{key} is not on RISK_REDUCING_ALLOWLIST "
                "-- this category of change can never auto-apply"}

    confidence = compute_confidence(candidate_id)
    if not confidence["eligible"]:
        return {"applied": False, "reason": f"Confidence not met: {confidence['reason']}"}

    # Everything checks out -- apply.
    config_ok = _apply_live_config(candidate["parameter"], candidate["proposed_value"])
    if not config_ok:
        _audit_log(f"FAILED to auto-apply {candidate_id}: config write failed")
        return {"applied": False, "reason": "Config write failed -- see logs"}

    note = (f"AUTO-APPROVED via confidence gate: {confidence['distinct_days']} days tested, "
            f"{confidence['favor_pct']:.0%} favourable, avg diff {confidence['avg_diff']:+.2f}. "
            f"Written to {LIVE_CONFIG_PATH} -- requires a bot restart to take effect.")
    decide(candidate_id, "approved", note=note)

    audit_msg = (f"AUTO-APPLIED candidate {candidate_id}: "
                 f"{candidate['strategy']}.{candidate['parameter']} "
                 f"{candidate['current_value']} -> {candidate['proposed_value']} | {note}")
    _audit_log(audit_msg)
    _alert(f"\u26a0\ufe0f Autonomy: auto-approved & config-written for candidate {candidate_id} "
           f"({candidate['strategy']}.{candidate['parameter']} -> {candidate['proposed_value']}). "
           f"RESTART THE BOT to make this live. See {AUDIT_LOG_PATH} for details.")

    return {"applied": True, "reason": note}


def check_all_proposed() -> list:
    """Runs maybe_auto_apply() over every currently-proposed candidate.
    Meant to be called once daily (see run_autonomy_check.py), never
    continuously during market hours."""
    results = []
    for c in list_candidates(status="proposed"):
        result = maybe_auto_apply(c["id"])
        results.append({"candidate_id": c["id"], **result})
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        for c in list_candidates(status="proposed"):
            conf = compute_confidence(c["id"])
            print(f"[{c['id']}] {c['strategy']}.{c['parameter']}: {conf}")
    else:
        results = check_all_proposed()
        for r in results:
            print(r)
