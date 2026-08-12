# core/candidate_config.py
#
# CURRENT_CONFIG / CANDIDATE_CONFIG separation, per the self-improvement
# layer discussion (Aug 12 2026 session).
#
# WHAT THIS IS: a structured place to WRITE DOWN a proposed parameter
# change, with the evidence and reasoning behind it -- so ideas from
# reading a post_mortem report don't just live in your head or get lost
# in a chat log.
#
# WHAT THIS IS NOT, ON PURPOSE:
#   - This module NEVER modifies any strategy's actual behavior. It has no
#     write access to risk_manager, any strategy config dataclass, or any
#     .py file. Marking a candidate "approved" here is a record of a
#     decision, not an action -- actually applying a change still means
#     manually editing the real code, the same way tonight's sizing-
#     tolerance fix was done: reasoned about, written, tested, verified.
#   - No automatic promotion. No autonomous re-evaluation. No walk-forward
#     validation (this repo has no backtesting engine to validate against
#     yet -- see post_mortem.py's docstring for the same caveat).
#
# Candidates are stored in configs/candidate_config.json, written with the
# same atomic tmp-file + os.replace() pattern already used by
# risk_state.json / regime_state.json, so a crash mid-write can't corrupt
# the file.

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CANDIDATE_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "candidate_config.json"

VALID_STATUSES = ("proposed", "approved", "rejected", "applied")


def _load() -> list:
    if not CANDIDATE_CONFIG_PATH.exists():
        return []
    try:
        with open(CANDIDATE_CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[candidate_config] Failed to load: {e}")
        return []


def _save(candidates: list) -> None:
    try:
        CANDIDATE_CONFIG_PATH.parent.mkdir(exist_ok=True)
        tmp_path = CANDIDATE_CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(candidates, f, indent=2)
        import os
        os.replace(tmp_path, CANDIDATE_CONFIG_PATH)
    except Exception as e:
        logger.error(f"[candidate_config] Failed to save: {e}")


def propose_change(
    strategy: str,
    parameter: str,
    current_value,
    proposed_value,
    rationale: str,
    evidence: str = "",
) -> str:
    """
    Record a proposed config change for human review. Does NOT apply it.
    Returns the candidate's id.
    """
    candidates = _load()
    candidate_id = str(uuid.uuid4())[:8]
    candidates.append({
        "id":              candidate_id,
        "created_at":      datetime.now().isoformat(),
        "strategy":        strategy,
        "parameter":       parameter,
        "current_value":   current_value,
        "proposed_value":  proposed_value,
        "rationale":       rationale,
        "evidence":        evidence,
        "status":          "proposed",
        "decided_at":      None,
        "decision_note":   "",
    })
    _save(candidates)
    logger.info(f"[candidate_config] Proposed: {strategy}.{parameter} {current_value} -> {proposed_value} (id={candidate_id})")
    return candidate_id


def list_candidates(status: Optional[str] = None, strategy: Optional[str] = None) -> list:
    candidates = _load()
    if status:
        candidates = [c for c in candidates if c.get("status") == status]
    if strategy:
        candidates = [c for c in candidates if c.get("strategy") == strategy]
    return candidates


def decide(candidate_id: str, status: str, note: str = "") -> bool:
    """
    Record a human decision on a candidate: 'approved', 'rejected', or
    'applied' (meaning you've since gone and manually made the actual code
    change). This function itself changes nothing except this record.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")
    candidates = _load()
    found = False
    for c in candidates:
        if c["id"] == candidate_id:
            c["status"] = status
            c["decided_at"] = datetime.now().isoformat()
            c["decision_note"] = note
            found = True
            break
    if not found:
        logger.warning(f"[candidate_config] Candidate id={candidate_id} not found")
        return False
    _save(candidates)
    logger.info(f"[candidate_config] {candidate_id} -> {status}" + (f" ({note})" if note else ""))
    return True


def print_candidates(status: Optional[str] = None) -> None:
    candidates = list_candidates(status=status)
    if not candidates:
        print(f"No candidates found" + (f" with status={status}" if status else "") + ".")
        return
    print("=" * 70)
    print(f"CANDIDATE CONFIG CHANGES{' (' + status + ')' if status else ''}")
    print("=" * 70)
    for c in candidates:
        print(f"\n[{c['id']}] {c['status'].upper()} | {c['strategy']}.{c['parameter']}")
        print(f"  {c['current_value']}  ->  {c['proposed_value']}")
        print(f"  Rationale: {c['rationale']}")
        if c.get("evidence"):
            print(f"  Evidence:  {c['evidence']}")
        print(f"  Created:   {c['created_at']}")
        if c.get("decided_at"):
            print(f"  Decided:   {c['decided_at']} ({c.get('decision_note', '')})")
    print()
    print("=" * 70)
    print("Note: 'approved' means YOU decided to do it -- it does NOT mean")
    print("the code has been changed. Apply manually, same as any other fix.")
    print("=" * 70)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Human-reviewed config change proposals -- never auto-applied.")
    sub = parser.add_subparsers(dest="command")

    p_propose = sub.add_parser("propose")
    p_propose.add_argument("--strategy", required=True)
    p_propose.add_argument("--parameter", required=True)
    p_propose.add_argument("--current", required=True)
    p_propose.add_argument("--proposed", required=True)
    p_propose.add_argument("--rationale", required=True)
    p_propose.add_argument("--evidence", default="")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)

    p_decide = sub.add_parser("decide")
    p_decide.add_argument("id")
    p_decide.add_argument("status", choices=VALID_STATUSES)
    p_decide.add_argument("--note", default="")

    args = parser.parse_args()

    if args.command == "propose":
        cid = propose_change(
            strategy=args.strategy, parameter=args.parameter,
            current_value=args.current, proposed_value=args.proposed,
            rationale=args.rationale, evidence=args.evidence,
        )
        print(f"Proposed candidate id={cid}")
    elif args.command == "list":
        print_candidates(status=args.status)
    elif args.command == "decide":
        ok = decide(args.id, args.status, args.note)
        print("Done." if ok else "Candidate not found.")
    else:
        parser.print_help()