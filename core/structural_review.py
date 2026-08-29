# core/structural_review.py
#
# WHAT THIS DOES: gathers everything relevant to a strategy's code-level
# behaviour -- its actual source file, its recent pattern_flags, its
# hypotheses (open/testing/supported/disproven), any lessons.md entries
# that mention it, its family from strategy_registry.json, and a quick
# snapshot of the last known market regime -- into a single markdown
# "review packet". It does NOT call any AI API itself. You paste the
# resulting file into a Claude conversation and ask for a structural
# review. This is deliberately a data-gathering step, not an automated
# decision-maker -- same "proposal/packet only, human (or human + Claude
# in a real conversation) decides" philosophy as every other module in
# this research layer.
#
# WHY NOT AN AUTOMATED API CALL: this repo has never called an external
# LLM API before -- adding one here would introduce a new secret
# (ANTHROPIC_API_KEY), a new cost, a new network dependency, and a new
# "AI proposes code changes with nobody watching" surface, none of which
# fit the "research layer only, human approves everything" boundary the
# rest of this system was deliberately built around (see
# core/confidence_gate.py, core/candidate_generator.py's module
# docstrings). A generated packet you paste into a real conversation keeps
# a human in the loop on every single review, by construction.
#
# WHAT IT FINDS THAT candidate_generator.py CAN'T: candidate_generator.py
# only maps flags onto a hand-written table of KNOWN, EXISTING config
# levers -- it can never find something like the PE/CE if-elif priority
# coupling discovered 2026-08-29 (see lessons.md), because that requires
# actually reading and reasoning about the strategy's control flow, not
# matching a flag shape to a lookup table. This tool's job is only to
# assemble the context a human/Claude needs to do that kind of review --
# it does not attempt the review itself.

import json
import logging
from datetime import datetime
from pathlib import Path

from core import hypothesis_engine
from core.pattern_memory import DB_PATH
from core.research_memory import LESSONS_PATH
import sqlite3

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
REGISTRY_PATH = REPO_ROOT / "configs" / "strategy_registry.json"
REGIME_STATE_PATH = REPO_ROOT / "configs" / "regime_state.json"
PACKET_DIR = REPO_ROOT / "research_memory" / "review_packets"

# Where each registered strategy's source lives. Hand-maintained, same
# spirit as candidate_generator.py's KNOWN_LOT_SIZE table -- explicit and
# auditable rather than guessed via filename pattern-matching.
STRATEGY_SOURCE_FILES = {
    "survivor": "strategy/survivor.py",
    "bn_survivor": "strategy/survivor.py",  # same class, different instantiation
    "wave_extractor": "strategy/wave_extractor.py",
    "nifty_gex": "strategy/nifty_gex.py",
}


def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text()).get("strategies", {})
    return {}


def _load_regime_snapshot() -> str:
    if not REGIME_STATE_PATH.exists():
        return "(no regime_state.json found)"
    try:
        state = json.loads(REGIME_STATE_PATH.read_text())
        return json.dumps(state, indent=2)
    except Exception as e:
        return f"(could not read regime_state.json: {e})"


def _load_flags_for_strategy(strategy: str, db_path: Path = DB_PATH) -> list:
    """Flags where dimension_value is exactly the strategy, or
    'strategy|SIDE' for it (matches candidate_generator.py's own
    strategy_regime dimension shape). Returns [] if pattern_detection.py
    hasn't run yet on this database (table doesn't exist) -- that's a
    normal state, not an error."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pattern_flags WHERE dimension_value = ? "
                "OR dimension_value LIKE ? ORDER BY flagged_at DESC",
                (strategy, f"{strategy}|%"),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def _load_hypotheses_for_strategy(strategy: str, db_path: Path = DB_PATH) -> list:
    all_hyps = hypothesis_engine.list_hypotheses(db_path=db_path)
    return [h for h in all_hyps
            if h["source_dimension_value"] == strategy
            or (h["source_dimension_value"] or "").startswith(f"{strategy}|")]


def _load_lessons_mentioning(strategy: str) -> str:
    if not LESSONS_PATH.exists():
        return "(no lessons.md yet)"
    text = LESSONS_PATH.read_text()
    if strategy not in text:
        return f"(no lessons.md entries currently mention '{strategy}')"
    # Pull each "### LESSON —" block that mentions the strategy anywhere
    # in its text -- simple substring scan over blocks, not a full parser.
    blocks = text.split("### LESSON")
    matches = [("### LESSON" + b) for b in blocks[1:] if strategy in b]
    return "\n".join(matches) if matches else f"(no lessons.md entries currently mention '{strategy}')"


def generate(strategy: str, db_path: Path = DB_PATH) -> Path:
    """
    Builds a single markdown review packet for `strategy` and writes it to
    research_memory/review_packets/{strategy}_{date}.md. Returns the path.
    Raises ValueError if the strategy isn't in STRATEGY_SOURCE_FILES (same
    "don't guess, fail loud" convention as candidate_generator.py).
    """
    if strategy not in STRATEGY_SOURCE_FILES:
        raise ValueError(
            f"Unknown strategy '{strategy}'. Known: {list(STRATEGY_SOURCE_FILES.keys())}. "
          f"Add it to STRATEGY_SOURCE_FILES first, same as candidate_generator.py's "
            f"KNOWN_LOT_SIZE -- don't guess a source file."
        )

    source_path = REPO_ROOT / STRATEGY_SOURCE_FILES[strategy]
    source_code = source_path.read_text() if source_path.exists() else \
        f"(source file {source_path} not found)"

    registry = _load_registry()
    reg_entry = registry.get(strategy, {})
    flags = _load_flags_for_strategy(strategy, db_path)
    hyps = _load_hypotheses_for_strategy(strategy, db_path)
    lessons_text = _load_lessons_mentioning(strategy)
    regime_snapshot = _load_regime_snapshot()

    lines = [
        f"# Structural Review Packet — {strategy}",
        f"_Generated {datetime.now().isoformat()} by core/structural_review.py_",
        "",
        "## How to use this packet",
        "",
        "Paste this entire file into a Claude conversation and ask something like:",
        "",
        "> Review this strategy's logic given the attached flags, hypotheses, and "
        "lessons. Are there hidden bugs, unintended couplings between code paths, "
        "or config/logic changes that could plausibly improve performance? Cite "
        "specific line numbers for anything you flag. Do not propose live-trading "
        "changes directly -- only research/backtest-testable ideas.",
        "",
        "This packet does not analyze anything itself -- it only gathers context. ",
        "Nothing here has been auto-applied to any config or live trading.",
        "",
        "---",
        "",
        f"## Strategy family",
        "",
        f"Family: {reg_entry.get('family', '(not registered)')}",
        f"Description: {reg_entry.get('description', '(none)')}",
        "",
        "---",
        "",
        f"## Recent CONCERNING/PROMISING flags for {strategy} ({len(flags)})",
        "",
    ]

    if not flags:
        lines.append("(none)")
    else:
        for f in flags:
            lines.append(
                f"- **{f['flag_type']}** `{f['dimension_type']}={f['dimension_value']}` "
                f"({f['trade_count']} trades, {f['win_rate']}% win, "
                f"net \u20b9{f['net_pnl']:,.2f}) — {f['flag_reason']} "
                f"_(flagged {f['flagged_at']})_"
            )

    lines += ["", "---", "", f"## Hypotheses referencing {strategy} ({len(hyps)})", ""]
    if not hyps:
        lines.append("(none)")
    else:
        for h in hyps:
            n_sup, n_con = len(h["supporting_experiments"]), len(h["contradicting_experiments"])
            lines.append(
                f"- **[{h['hypothesis_id']}] {h['status']}** ({h['confidence']} confidence, "
                f"{n_sup} supporting / {n_con} contradicting experiments): {h['statement']}"
            )

    lines += ["", "---", "", f"## lessons.md entries mentioning {strategy}", "", lessons_text]

    lines += [
        "", "---", "",
        "## Most recent known market regime (informational, may be stale)",
        "```json", regime_snapshot, "```",
        "", "---", "",
        f"## Full source — {STRATEGY_SOURCE_FILES[strategy]}", "",
        "```python", source_code, "```",
    ]

    PACKET_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PACKET_DIR / f"{strategy}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
    out_path.write_text("\n".join(lines))
    logger.info(f"[structural_review] Wrote packet for {strategy} to {out_path}")
    return out_path


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python3 -m core.structural_review <strategy>")
        print(f"Known strategies: {list(STRATEGY_SOURCE_FILES.keys())}")
        sys.exit(1)

    strategy = sys.argv[1]
    try:
        path = generate(strategy)
        print(f"Review packet written: {path}")
        print("Paste that file into a Claude conversation to get an actual review.")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
