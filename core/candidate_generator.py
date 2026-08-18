# core/candidate_generator.py
#
# Step 8 of the staged self-learning plan (Aug 14 2026 Session 3).
#
# WHAT THIS DOES: reads CONCERNING pattern_flags (Step 7) and, for the ones
# that map onto a KNOWN, SAFE, ALREADY-EXISTING config lever, calls
# candidate_config.propose_change() to record a reviewable proposal. For
# everything else, it prints a manual-review note instead of inventing a
# lever that doesn't exist.
#
# DELIBERATELY NOT "smart": this is a small, hand-written, auditable table
# (KNOWN_LEVERS below) mapping specific flag shapes to specific existing
# config parameters -- not a general inference engine that guesses what
# parameter might fix what problem. If a flag doesn't match a known shape,
# this prints it for a human to look at rather than fabricating a plausible-
# sounding proposal. Extending coverage means adding an entry to the table
# by hand, reading the actual strategy code first -- same as the
# pe_quantity case below.
#
# WHY pe_quantity -> 0 AND NOT A SMALLER NUMBER: checked strategy/survivor.py
# directly before writing this. The order-placement path has a hard runtime
# guard: `quantity % self.cfg.lot_size != 0` aborts the order (see
# "QUANTITY MISMATCH" critical log around line 633). For the live Nifty
# survivor, lot_size == pe_quantity == 65 -- i.e. one lot IS the whole
# configured quantity. There is no fractional lot. So the only valid values
# for pe_quantity/ce_quantity are 0 or multiples of lot_size; a "reduce by
# half" proposal would silently generate an invalid quantity that gets
# aborted at runtime, not a smaller position. Every quantity proposal this
# module generates is therefore binary -- disable the side entirely (0) --
# never a partial resize. A partial-size lever would need an actual code
# change (splitting lot_size from pe_quantity), which is out of scope here.
#
# EXPLICITLY NOT INCLUDED, ON PURPOSE -- same philosophy as the rest of this
# self-learning layer:
#   - No automatic application. Calls propose_change() only, same as a
#     human typing the CLI command by hand -- status starts at "proposed",
#     nothing trades differently until a human runs `decide ... approved`
#     AND then manually makes the code/config change themselves.
#   - No proposals for flags without a known lever (see KNOWN_LEVERS). A
#     broadly-underperforming strategy (e.g. bn_survivor across the board)
#     or a bad time-of-day window (entry_hour 13/14/15) has no existing,
#     safe, single-parameter fix in the current codebase -- inventing one
#     would be guessing, not proposing. These print as manual-review items.
#   - Idempotent: won't create a duplicate proposal for the same
#     (strategy, parameter) if one is already sitting in "proposed" status.
#     Re-run safely as often as you like.

import json
import logging
from pathlib import Path

from core.candidate_config import propose_change, list_candidates
from core.pattern_memory import DB_PATH as PATTERN_DB_PATH
import sqlite3

logger = logging.getLogger(__name__)

# lot_size per strategy, read directly from strategy/survivor.py's
# instantiation in main.py (Nifty: lot_size=65 default; BankNifty:
# lot_size=15, hardcoded in main.py's banknifty_cfg). This is the quantum
# that pe_quantity/ce_quantity MUST be a multiple of, per survivor.py's
# runtime quantity-sanity check.
KNOWN_LOT_SIZE = {"survivor": 65, "bn_survivor": 15}

# Where to read survivor's LIVE configured quantities from, if available.
# bn_survivor has no equivalent file -- its pe_quantity/ce_quantity are
# hardcoded directly in main.py's banknifty_cfg instantiation, so we fall
# back to the documented value with an explicit "verify by hand" caveat.
LIVE_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "saviour_combo.json"


def _current_quantity(strategy: str, side: str) -> tuple:
    """Returns (current_value, source_note) for {side}_quantity, or
    (None, None) if we don't have a reliable way to know it."""
    key = f"{side.lower()}_quantity"

    if strategy == "survivor" and LIVE_CONFIG_PATH.exists():
        try:
            with open(LIVE_CONFIG_PATH) as f:
                cfg = json.load(f)
            if key in cfg:
                return cfg[key], f"read live from {LIVE_CONFIG_PATH.name}"
        except Exception as e:
            logger.warning(f"[candidate_generator] Could not read {LIVE_CONFIG_PATH}: {e}")

    if strategy in KNOWN_LOT_SIZE:
        return (KNOWN_LOT_SIZE[strategy],
                "hardcoded default -- NOT read from a live config file for this "
                "strategy, verify against main.py's actual instantiation before applying")

    return None, None


def _already_proposed(strategy: str, parameter: str) -> str:
    """Returns an existing candidate id if a 'proposed' (undecided) one
    already exists for this (strategy, parameter), else ''."""
    for c in list_candidates(status="proposed", strategy=strategy):
        if c.get("parameter") == parameter:
            return c["id"]
    return ""


def generate(db_path: Path = PATTERN_DB_PATH) -> dict:
    """
    Reads CONCERNING pattern_flags and generates candidate proposals for
    the ones with a known lever. Returns
    {"proposed": [...], "skipped_duplicate": [...], "manual_review": [...]}.
    Does not call pattern_detection.detect() first -- run that beforehand
    if you want current flags.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        flags = conn.execute(
            "SELECT * FROM pattern_flags WHERE flag_type = 'CONCERNING' "
            "ORDER BY net_pnl ASC"
        ).fetchall()

    proposed, skipped_duplicate, manual_review = [], [], []

    for f in flags:
        dtype, dvalue = f["dimension_type"], f["dimension_value"]

        # Known lever: strategy_regime "{strategy}|PE" or "{strategy}|CE"
        # where strategy is a survivor-family strategy with a known
        # lot_size. See module docstring for why this is 0-or-nothing.
        if dtype == "strategy_regime" and "|" in dvalue:
            strategy, side = dvalue.split("|", 1)
            if side in ("PE", "CE") and strategy in KNOWN_LOT_SIZE:
                parameter = f"{side.lower()}_quantity"
                current, source_note = _current_quantity(strategy, side)
                if current is None:
                    manual_review.append((f, "matched a known lever shape but couldn't "
                                              "determine the current value safely"))
                    continue

                existing_id = _already_proposed(strategy, parameter)
                if existing_id:
                    skipped_duplicate.append((f, existing_id))
                    continue

                lot_size = KNOWN_LOT_SIZE[strategy]
                rationale = (
                    f"{f['trade_count']} trades, {f['win_rate']}% win rate, "
                    f"net \u20b9{f['net_pnl']:,.2f} on the {side} side of {strategy} -- "
                    f"at or below the concerning-pattern threshold."
                )
                evidence = (
                    f"pattern_flags CONCERNING as of {f['flagged_at']} "
                    f"(dimension: strategy_regime={dvalue}). Current value {source_note}. "
                    f"NOTE: {parameter} must be 0 or a multiple of lot_size ({lot_size}) -- "
                    f"strategy/survivor.py's order-placement path aborts any quantity that "
                    f"isn't (see 'QUANTITY MISMATCH' guard). A partial resize is NOT "
                    f"mechanically possible today; this proposal is binary -- disable this "
                    f"side (0) or leave it at {lot_size}+."
                )
                cid = propose_change(
                    strategy=strategy, parameter=parameter,
                    current_value=current, proposed_value=0,
                    rationale=rationale, evidence=evidence,
                )
                proposed.append((f, cid))
                continue

        # No known lever for this flag shape.
        manual_review.append((f, "no known lever for this dimension_type/value -- "
                                  "needs a human to look at the underlying trades"))

    return {
        "proposed": proposed,
        "skipped_duplicate": skipped_duplicate,
        "manual_review": manual_review,
    }


def print_report(result: dict) -> None:
    print("=" * 100)
    print("STEP 8 — CANDIDATE PROPOSAL GENERATION")
    print("=" * 100)

    print(f"\n-- PROPOSED ({len(result['proposed'])}) --")
    if not result["proposed"]:
        print("  (none)")
    for f, cid in result["proposed"]:
        print(f"  [{cid}] {f['dimension_type']}={f['dimension_value']} "
              f"({f['trade_count']} trades, {f['win_rate']}% win, "
              f"net \u20b9{f['net_pnl']:,.2f})")

    print(f"\n-- SKIPPED, ALREADY PROPOSED ({len(result['skipped_duplicate'])}) --")
    if not result["skipped_duplicate"]:
        print("  (none)")
    for f, existing_id in result["skipped_duplicate"]:
        print(f"  [{existing_id}] {f['dimension_type']}={f['dimension_value']} "
              f"-- already has an undecided proposal, not duplicating")

    print(f"\n-- NEEDS MANUAL REVIEW, NO AUTO PROPOSAL ({len(result['manual_review'])}) --")
    if not result["manual_review"]:
        print("  (none)")
    for f, reason in result["manual_review"]:
        print(f"  {f['dimension_type']}={f['dimension_value']} "
              f"({f['trade_count']} trades, {f['win_rate']}% win, "
              f"net \u20b9{f['net_pnl']:,.2f}) -- {reason}")

    print()
    print("Run `python3 -m core.candidate_config list --status proposed` to review "
          "and `decide <id> approved|rejected` on each. Nothing here has changed "
          "live trading.")
    print("=" * 100)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = generate()
    print_report(result)
