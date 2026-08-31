# HANDOFF — Sunday Aug 30 2026 (into next session)

## TL;DR for whoever picks this up

The losing streak is **not fixed yet**. Today was almost entirely spent finding
and fixing real, previously-hidden bugs in the *research/backtest tooling
itself* — necessary work, since every finding before today's fixes would have
been untrustworthy, but it means the actual "why is survivor losing money"
question is still open. The single most promising lead (`795b6591`,
regime-stability gating, potentially ~71% of the loss) is now correctly
identified as **blocked on an infrastructure gap**, not tested and failed.
**Fixing that gap is the highest-priority next step — see Priority 1 below.**

Read `research_memory/lessons.md` in full before touching anything — it has
the complete reasoning trail for everything below, with code line numbers
and exact evidence. This file is a summary/index into it, not a replacement.

---

## What's actually working right now (don't re-verify, just use)

- Live bot: healthy, PAPER mode, no open positions as of this handoff.
  Token expires daily — if you see `401 Unauthorized` / `Invalid token used
  to access API` in `pm2 logs trading-bot`, that's routine, not a crisis.
  Fix: `python3 auto_token.py`, then reply `/token <code>` in Telegram
  using the code from the failed OAuth redirect URL.
- Self-learning research layer (Step 6-12): `core/pattern_memory.py`,
  `core/pattern_detection.py`, `core/hypothesis_engine.py`,
  `core/candidate_generator.py`, `core/research_memory.py`,
  `run_research_session.py` — all deployed, tested, working.
- `core/structural_review.py` — run `python3 -m core.structural_review
  <strategy>` (survivor/bn_survivor/wave_extractor/nifty_gex) to generate a
  full context packet (source code + flags + hypotheses + lessons) you can
  paste into a fresh Claude conversation for a deep-dive review.
- Backtest gate (`run_survivor_backtest.py` + `run_candidate_backtest_gate.py`)
  now has a **fixed date-range bug** (see below) — trust results from
  today's session onward; be skeptical of any candidate_gate_results rows
  timestamped before ~13:00 IST 2026-08-29, since those ran against windows
  silently missing their final day.
- `original_strategies/survivor_original.py` and `wave_extractor_original.py`
  — the pre-port reference implementations, useful for "was this always
  like this or did it change" questions. Not runnable (different broker,
  different class shape) — comparison-only.

---

## What was fixed today (verified, deployed, low-risk)

1. **Dead lever**: `candidate_generator.py` used to propose
   `pe_quantity`/`ce_quantity` changes, which `survivor.py`'s order-quantity
   calculation never reads (confirmed via code trace) — a total no-op.
   Fixed to propose `pe_enabled`/`ce_enabled` instead, which IS checked at
   the entry trigger. Old dead candidates `7e16b7f2`/`021ca1d6` rejected
   with clear notes.

2. **Backtest date-range bug**: `core/research/survivor_backtest.py`
   compared a bare end date (e.g. `"2026-08-25"`, the normal way to call
   the script) as a raw string against timestamped candle rows. Since
   `"2026-08-25 09:15" > "2026-08-25"` lexically, the `<=` filter silently
   dropped the ENTIRE final day of every multi-day backtest, with no error.
   Fixed via `_normalize_end_ts()` (bare date -> end-of-day, not midnight).
   **Any backtest-gate result from before this fix is unreliable and has
   already been re-run or marked invalid -- don't resurrect old results.**

---

## What was found but is still OPEN (needs work, not a quick fix)

### LESSON-001 -- PE/CE priority coupling (`if`/`elif`)

`strategy/survivor.py`'s PE and CE entry checks are `if pe_enabled and
<PE conditions>: ... elif ce_enabled and <CE conditions>: ...` -- PE checked
first, and if its condition is met (even if the resulting trade is then
internally skipped for another reason), CE is never evaluated that tick.

Compared against `original_strategies/survivor_original.py`: the original
design has PE and CE as two fully independent, unconditional method calls
every tick -- no coupling at all. This looks like an unintentional
regression introduced during the port, not a deliberate design choice
(checked git history on these exact lines -- nothing suggests intent).

**Extracted (pure refactor, zero behavior change, verified) into its own
method** `SurvivorAlgo._evaluate_pe_ce_entries()` specifically so this could
be tested safely. Built `core/research/legacy_priority_variant.py` +
`run_survivor_backtest.py --pe-ce-priority independent` to A/B test it
without touching live code.

**Two real experiments run, results DISAGREED:**
- Aug 21-27 window: independent better by +Rs292.57
- Aug 19-28 window (superset): independent WORSE by -Rs396.68

**Also found a specific anomaly** in the Aug 19-28 runs: a PE trade with
identical entry setup (same anchor, same strike, same entry price) closed
at a different exit price between the two modes (Rs59.6 vs Rs65.7),
turning a -Rs210 trade into a -Rs607 trade. This suggests the "independent"
variant isn't as clean a single-variable test as intended -- something about
changing entry control-flow appears to ripple into exit-price determinism.

**Next step, not started**: understand that exit-price anomaly BEFORE
running more `--pe-ce-priority` comparisons. Once understood, re-run over
more windows (Aug 19-28 and 21-27 aren't independent -- they overlap; need
genuinely separate windows once more archived data accumulates, or accept
overlap and weight accordingly).

**Candidates `bed3749a` (PE) and `4c47ab0e` (CE)** are both rejected with
notes explaining they're deferred, not disproven -- pending this question.

### LESSON-002 -- regime_engine never wired into the backtest (bigger deal)

Found while gate-testing `795b6591` (the `min_regime_stability` candidate
-- the single most promising untested lead, from the Aug 17 code audit,
potentially explaining ~71% of survivor's 20-day loss).

`get_regime_stability()` depends on `core/regime_engine.py`'s
`_regime_history` list, which starts empty and is ONLY ever appended to by
the live/paper polling loop's classification method. **The backtest never
calls anything on `regime_engine` at all** (confirmed via grep -- zero
references in `survivor_backtest.py` or `run_survivor_backtest.py`).
`get_regime_stability()` explicitly returns a flat default of `50.0`
whenever `len(_regime_history) < 2` -- which is permanently true throughout
any backtest run. Result: **any `min_regime_stability` threshold above
50.0 mechanically blocks every single trade in a backtest, regardless of
real market conditions.**

This is exactly what happened: gate-tested `795b6591` (0.0 -> 65.0) over
Aug 19-28 -- baseline 6 trades/-Rs981, candidate **0 trades/+Rs0.00**. Looked
like a win at first glance; it's actually just proof the harness can't
test this at all. Candidate rejected with a note explaining it's not
disproven, just untestable right now.

**Fix needed (not started, real engineering work)**: wire
`regime_engine`'s actual classification logic into the backtest tick loop,
feeding it the same synthetic candle stream already used for
`market_context`. Need to read `core/regime_engine.py`'s classification
method (the one that writes to `_regime_history`, around line 390) to see
exactly what inputs it needs (candles? VWAP? ADX? EMA? -- some may already
be computed elsewhere in the backtest, some may not be) and replicate that
feed within `core/research/survivor_backtest.py`'s tick loop, the same way
`market_context` was already patched for backtest use.

---

## PRIORITIZED NEXT STEPS

### Priority 1 -- Fix the regime_engine backtest gap (LESSON-002)

This unblocks testing the strongest lead on the table. Concretely:
1. Read `core/regime_engine.py`'s classification method (~line 350-400,
   whatever writes to `_regime_history`) to understand its exact inputs.
2. Read how `core/research/survivor_backtest.py` currently feeds
   `market_context` during replay -- same pattern likely applies.
3. Add equivalent regime-classification calls into the backtest tick loop,
   driven by the same candle stream, so `_regime_history` accumulates
   realistically as backtest time progresses.
4. Verify: run a plain baseline backtest, log `regime_engine.
   get_regime_stability()` at a few points, confirm it's producing varied,
   plausible values (not always 50.0).
5. Re-run `795b6591`'s gate test -- THIS is the test that matters most for
   the original "turn around the losing streak" question.

### Priority 2 -- Resolve the PE/CE exit-price anomaly (LESSON-001)

Before running more `--pe-ce-priority` comparisons: figure out why an
identically-entered trade closed at a different price between `elif` and
`independent` modes in the Aug 19-28 run. Once understood (and either
explained as legitimate or fixed if it's itself a bug), re-run the
comparison properly.

### Priority 3 -- Untouched manual-review flags, still open, no known lever

These haven't been investigated at all yet -- good `structural_review`
candidates:
- `entry_hour=13`, `entry_hour=14`, `entry_hour=15` -- all flagged losing
- `regime=PE`, `regime=CE` -- likely overlapping with the strategy-level
  flags (confirmed via `pattern_overlap` earlier), but not fully explored
- `bn_survivor` broadly (48 trades, 10.4% win, -Rs3,429) -- completely
  unexplored, different strategy instance same underlying class

---

## How to get oriented fast next session

```bash
# Read the full reasoning trail (most important single command)
cat research_memory/lessons.md

# Check current hypothesis states
python3 -c "from core import hypothesis_engine as he; print(he.show())"

# Check current candidate proposals and their decisions
python3 -m core.candidate_config list

# Check bot health
pm2 status
pm2 logs trading-bot --lines 20 --nostream

# Refresh pattern memory + flags with latest closed trades (safe, read-only against trade_log.db)
python3 run_research_session.py
```

---

## Safety boundary -- unchanged, still holds

Nothing today touched live trading behavior without this same careful
process: sandbox-verify -> deploy -> test -> confirm -> commit. The
`_evaluate_pe_ce_entries` extraction in `strategy/survivor.py` was a
byte-for-byte pure refactor (verified via matching backtest output) -- the
bot's actual behavior has not changed. `core/confidence_gate.py`'s narrow,
human-gated autonomy boundary was not touched. No candidate has been
applied to `saviour_combo.json` or any live config. Every finding above
requires a human decision before any further code change.

---

## Git state

All of today's work is committed and pushed to `origin/main`:
- `5909dea` -- Step 12 hypothesis engine (earlier session)
- `c669f6e` -- PE/CE extraction, priority variant, date-bug fix, original
  strategy reference files
- `3a9dbde` -- LESSON-002 (regime_engine backtest gap)

`strategy/survivor.py.bak_before_pe_ce_extraction` is sitting in the repo
root as a safety-net backup of the pre-refactor file -- safe to delete once
you're confident in the extraction (it's git-tracked either way, this is
just a convenience copy).