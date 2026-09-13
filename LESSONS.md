# LESSONS.md — Running Ledger (read this before building anything new)

**Purpose:** every time a strategy or fix fails, write down *why*, in enough
detail that the same mistake can't quietly happen twice. Every time
something passes/works, write down what it had in common with other things
that worked. This file is additive — append, don't delete history, even
once something is fixed, so the pattern of *how* things break stays visible.

Last consolidated: 2026-09-12 (synthesized from HANDOFF docs, quant
evaluation, and patch history through that date — see note at bottom on
provenance/limitations).

---

## FAILURE LESSONS (chronological, root-cause level)

### LESSON-F1 — Paper-mode hedge stale-pricing (survivor.py)
**Symptom:** survivor's win rate looked like ~3.6% over a month.
**Root cause:** when the hedge leg's cached tick price was 0.0, the code
fell back to querying the *real* Upstox API with a synthetic symbol
(e.g. `NSE_FO|NIFTY01SEP2624250CE`) that isn't a real instrument key and
is never tick-subscribed in paper mode. That call always returns 0.0,
which tripped the 3-strike `STALE_HEDGE_PRICE_AUTOCLOSE` almost
immediately on nearly every paper trade, before the trade could ever
reach its real exit condition.
**Why it mattered more than it looked:** this wasn't a rare edge case —
it fired on *every* hedge leg, every trade, the whole time paper mode
ran this way. A pricing bug, not a strategy problem, was very likely the
dominant driver of the apparent "the strategy doesn't work" result.
**Fix:** paper-mode hedge leg now holds at entry price instead of
querying the broker, mirroring the already-safe pattern used for the
main leg. (`patch_survivor_rootcause_fixes.py`)
**Category:** bug (plumbing), not strategy.

### LESSON-F2 — PE/CE `elif` coupling (survivor.py)
**Symptom:** every A/B test of enabling/disabling PE or CE independently
came back confounded or produced zero trades on one side.
**Root cause:** CE's entry block was `elif`, chained directly to PE's.
Any tick where PE's gap condition evaluated true meant CE was never
even checked that tick — *even if PE was then internally rejected*
(VIX-high, capital limit). One side silently starved the other.
**Fix:** split into two independent `if` blocks, each with its own gating,
so both sides are evaluated every tick regardless of the other's outcome.
**Category:** bug (control flow), not strategy.

### LESSON-F3 — Anchor advances even on a skipped trade
**Symptom:** a side could go quiet for the rest of the day after a
single VIX spike, even after VIX normalized minutes later.
**Root cause:** when a trade was skipped (VIX-high qty=0, or a
capital-limit rejection), the anchor value and `_pe_sold_flag`/
`_ce_sold_flag` still advanced *unconditionally*, so the same valid gap
condition could never re-fire later that day.
**Fix:** anchor/flag now only advance on an actual executed trade; the
skip signal itself is rate-limited (30s) so it doesn't spam on every
tick while the condition persists.
**Category:** bug, and it directly caused missed real opportunity, not
just noise.

### LESSON-F4 — GTT orders never worked (UPDATED 2026-09-13, repo-verified)
**Two separate things, don't conflate them:**
1. A "virtual GTT" (independent SL/TP re-check inside the tick-refresh
   loop, works regardless of broker connectivity) was built back in
   June and has been in place a while.
2. **The real broker-side GTT placement (`place_gtt_trailing_sl`) had
   never successfully placed a single real GTT order, in any direction,
   live or otherwise, in its entire history** — not found/fixed until
   commit `0994e47` (2026-09-08). Four independent bugs, any one fatal
   on its own: an invalid `GttRule.strategy` value, an invalid
   `trigger_type`, a nonexistent `GttApi` class in the installed SDK
   (GTT actually lives on `OrderApiV3`), and reading the wrong field off
   the response (`resp.data.id` instead of `resp.data.gtt_order_ids`).
   A companion bug in `cancel_gtt_order` (same missing-class issue) meant
   any GTT that *did* somehow get placed could never be cancelled either
   — it would sit live at the broker for a position that no longer
   exists.
**Verification status:** the fix's docstring says it was "verified
directly against the installed SDK" — i.e., checked that the method
calls, enums, and response shape genuinely match
`upstox-python-sdk==2.29.0`. That's real and solid. **What is NOT yet
confirmed: an actual live GTT order being placed and later firing**,
since this code path only runs `if not _is_paper` (live mode only), and
there's no evidence in committed trade logs/reports of a live sell-fill
since Sep 8 exercising it. Treat as "code-correct, not yet
live-proven" until the first live trade after this fix confirms it.
**Category:** bug (SDK misuse), now fixed at the code level; live
confirmation still open.

### LESSON-F4b — A second silent gate in `nifty_gex.evaluate_entry()` (found 2026-09-09)
**Symptom:** a logging fix added the day before (to see *why* nifty_gex
wasn't trading) produced zero log lines for 50+ minutes, even though
the entry-evaluation function was confirmed still running every ~60s.
**Root cause:** the new logging lived at the bottom of `evaluate_entry()`,
but the EMA-stack-not-aligned early return — the single most common
rejection reason — returned *before* that code ever ran.
**Result once fixed:** confirmed live that nifty_gex has been correctly
rejecting every setup because `ema_stack.agreement=False` — a genuine
"no clean cross-timeframe trend right now" condition, not a bug. First
real visibility ever into why this strategy hasn't traded.
**Category:** same recurring pattern as LESSON-F5 — a real signal was
present the whole time, but the wiring to *see* it was broken.

### LESSON-F5 — Backtest engine invalidated every regime-based gate test
**Symptom:** the most promising open candidate (`795b6591`,
`min_regime_stability` 0→65) kept coming back "gate result invalid" —
looked like the idea itself might be wrong.
**Root cause:** the backtest harness forced a fake, flat "range" regime
for the entire replay, and `regime_engine.get_regime_stability()` always
returned a hardcoded 50.0 because the live polling loop that would
normally drive it never ran inside the harness. Any threshold above 50
therefore blocked 100% of trades in *every* backtest, regardless of
real market conditions — a mechanical artifact, not a real result.
**Compounding mistake:** the actual fix (wiring `regime_engine.classify()`
into the backtest's per-candle replay loop, correctly isolated from the
live state file) was drafted, reasoned through carefully, and dated —
but never committed to git and never deployed to the server. It sat as
an uncommitted local draft for **~3 weeks** before being found and
actually shipped.
**Lesson:** a correctly-diagnosed problem with a working fix sitting in
an uncommitted draft is functionally identical to "not fixed at all."
Commit and push real fixes immediately, even mid-investigation — don't
let good work rot as an untracked file.
**Category:** bug (test infrastructure) + process failure (commit
discipline).

### LESSON-F6 — `min_regime_stability` sat at 0.0 (effectively off) for most of this period
**Symptom:** `REGIME_CHANGE`/`HEDGE_EXIT_REGIME_CHANGE` exits on
survivor accounted for ~71% of a -₹31,362 loss over 20 days (code audit,
Aug 17). The gate existed (`regime_engine.get_regime_stability()`) but
the live config value was 0.0, so `stability >= 0.0` was always true —
the filter did nothing.
**Evidence for the fix, once LESSON-F5 was resolved:** trades later
exited via `REGIME_CHANGE` averaged 63.9 stability-at-entry vs 70.8 for
other exits — directionally supportive of a ~65 cutoff, though the
signal is noisy (small n, overlapping ranges) — described honestly as
"a starting point for gate-testing, not a data-derived optimum."
**Applied live 2026-09-09.** Result for survivor: REGIME_CHANGE loss
fell from ~₹2,155/day average to ~₹611/day average — a real, verified
~72% reduction.
**Category:** the single highest-confidence, highest-impact fix found
this quarter for survivor. Real strategy-parameter fix, correctly
gated behind real evidence before being applied.

### LESSON-F7 — `bn_survivor` has never evaluated BankNifty's own regime (found 2026-09-12, UNRESOLVED)
**Symptom:** the Sep 9 fix helped survivor (~72% reduction) but did
*nothing* for `bn_survivor` — its regime-exit loss average actually
stayed flat or slightly worsened, including its single worst day
happening *after* the fix went live.
**Root cause:** there is exactly ONE `regime_engine` singleton in the
whole codebase, fed exclusively by NIFTY spot/candles
(`core/market_context.py._fetch_nifty_spot()`). `bn_survivor`'s
`min_regime_stability` gate reads that same singleton. Applying
"NIFTY's validated threshold" to bn_survivor only ever changed the
*cutoff number* — the thing being measured was still 100% NIFTY,
before and after. NIFTY and BankNifty correlate often but diverge,
especially around banking-sector-specific moves.
**Status as of 2026-09-12:** first infrastructure piece built
(`backfill_banknifty_candles.py` for real BankNifty 1-min history;
`RegimeEngine` generalized to accept `symbol=`, confirmed a second
`BANKNIFTY` instance is a genuinely separate object with its own state
file) — **NOT YET VERIFIED against a live token** (written blind over a
weekend). `market_context.py`'s generalization (the harder part —
entangled with live tick subscriptions, opening-range tracking, OI/PCR,
previous-day levels) deliberately NOT started; needs its own properly
scoped session with live market data to verify against.
**Explicit rule going forward:** do NOT apply any BankNifty-specific
threshold live before the full chain (backfill → replay → wire
bn_survivor to its own instance → honestly backtest/validate) is done.
Applying a number before that is the exact "borrowed, untested number"
mistake this whole investigation started from.
**Category:** architecture gap, not a parameter tuning problem. This is
currently the highest-priority open item.

### LESSON-F8 — BankNifty lot-size mismatch + stale expiry assumptions
**Symptom:** 17 trades tagged `ORPHANED|WRONG_LOT_SIZE_65_SHOULD_BE_15`
in earlier data — NIFTY's lot size (65) bled into BankNifty trades.
Also flagged: BankNifty weekly expiry structure changed (SEBI removed
weeklies) and config was, at the time of the original quant review,
still partially assuming the old structure.
**Status:** verify this is still current before trusting bn_survivor's
expiry handling in any new work.
**Category:** bug (config/data, not strategy logic).

### LESSON-F9 — `_last_tick_time` never initialized (`base_strategy.py`)
**Symptom:** every strategy crashed with `AttributeError` on its first
tick, causing a live crash-loop, restarts every few seconds.
**Root cause:** `__init__` never set `self._last_tick_time = {}`, but
`_record_tick`/other methods read/write it as if it existed.
**Fix:** add `self._last_tick_time: dict = {}` in `__init__`.
**Category:** pure bug, unrelated to strategy or regime logic — but it
still cost real uptime and trading time while unresolved.

### LESSON-F10 — pm2 running on a stale cached memory limit
**Symptom:** thousands of restarts (`↺` climbing continuously), bot
barely completing startup before being killed.
**Root cause:** `ecosystem.config.js` said `max_memory_restart: "700M"`,
but pm2's running daemon was still enforcing an old cached `"200M"` from
before someone last edited the file — because a plain `pm2 restart`
never reloads the config file; only `pm2 start <file>` / `pm2 reload`
actually re-reads it. The bot legitimately needs ~420-450MB to run 5
strategies + a 76k-row instrument list, so it tripped the stale 200MB
ceiling roughly every 30 seconds.
**Fix:** `pm2 delete trading-bot` then `pm2 start ecosystem.config.js
--only trading-bot` to force a genuine fresh read of the real config.
**Rule going forward:** after ANY pm2 config edit, verify with
`pm2 jlist ... max_memory_restart` (or similar) that the running
process actually reflects the new value — never trust a plain restart.
**Category:** pure ops/infra bug, zero relation to strategy quality, but
produced huge apparent "instability" that looked strategy-related.

### LESSON-F11 — Server RAM is genuinely too small for what runs on it
**Fact:** 956MB total RAM, already swapping (1GB swap in active use)
under just the live bot + `tg_commander`. Running a full VS Code
remote/code-server session at the same time pushes it over the edge.
**Rule going forward:** don't run heavy dev tooling on this box
alongside the live bot when stability matters; if this keeps recurring,
the real fix is a bigger instance, not more tuning.
**Category:** infra capacity, not strategy.

### LESSON-F12 — Server/GitHub code drift
**Symptom:** server had 22 modified-but-uncommitted files relative to
git HEAD, while GitHub's HEAD commit hash matched the server's exactly
— meaning real edits were made directly on the server (via `nano`) and
never committed. This is exactly how LESSON-F5's already-drafted fix sat
undeployed for ~3 weeks: it existed as a local file, not in git, not on
the server that mattered.
**Rule going forward:** commit + push every real code change
immediately, even mid-investigation. A fix that isn't committed doesn't
exist yet, no matter how carefully it was reasoned through.
**Category:** process failure, root cause of multiple other lessons.

---

## STRUCTURAL / IRREDUCIBLE COSTS (not bugs — don't chase these to zero)

- **Survivor / bn_survivor are short-premium, range-selling strategies.**
  A regime-stability filter reduces the odds of entering right before a
  shift; it cannot prevent a regime shifting *after* a perfectly good
  entry. Some `REGIME_CHANGE` loss is the unavoidable cost of this
  strategy design, not a bug to eliminate entirely.
- **wave_extractor (trend-follower) is negative-expectancy in the
  current persistently range-dominated NIFTY regime** (59 trades,
  -₹26,588, 25.4% win rate, dominant exits `STOP_LOSS`). It is correctly
  kept gated behind a high-confidence trending regime. This is a
  market-fit decision, not a bug — do not re-enable without a genuine
  trending-regime confirmation and a realistic-cost walk-forward test.

---

## RECURRING PATTERN ACROSS FAILURES

Almost every large loss category above (hedge stale-pricing, PE/CE
coupling, anchor-on-skip, fake backtest regime, bn_survivor's borrowed
regime) is "**the wiring between two correct-looking pieces was
broken**," not "the pattern-detection or strategy judgment was wrong."
The self-learning/research pipeline's real failure mode has consistently
been broken plumbing between analysis and reality — five separate
plumbing breaks were found in a single week (backtest forced-flat regime,
`main.py` not reading an "approved" config field, `nifty_gex` rejection
logging invisible, research pipeline idle for 4 days, bn_survivor's
wrong-instrument regime) — not bad pattern-detection.

## WHAT WORKED / SURVIVOR PATTERNS

Fixes that actually reduced losses (hedge stale-price, PE/CE decoupling,
`min_regime_stability=65` for survivor) shared three traits:
1. **Traceable to real logged data before being proposed** — not
   intuition or a plausible-sounding guess.
2. **Verified live or in a real replay before being trusted** — not
   assumed correct from reading the code alone.
3. **Scoped to one lever at a time** so its effect could be isolated
   and actually measured.

Candidates that got stuck or rejected (`pe_quantity`/`ce_quantity` as
levers) shared one trait: they acted on a parameter that turned out not
to be read by the actual order-sizing code path at all — a "dead
lever." **Rule:** before proposing to flip any config value, trace that
it is actually read on the live code path, not just that it sounds like
it should matter.

---

### LESSON-F13 — `pe_iv`/`ce_iv` never extracted from the option chain (found 2026-09-13, live-log confirmed)
**Symptom:** `put_calendar` has never once fired a real entry, in its
entire history. Log showed `"Front IV unavailable — skipping this
cycle"` firing on every single evaluation cycle, every session, with
zero `"Entry check"` lines ever printed — meaning it never even reached
its real IV/DTE decision logic.
**Root cause:** `brokers/upstox.py`'s `get_option_chain()` reads `delta`
off each strike's `option_greeks` object but never read `iv` off the
same object — the returned per-strike dict simply never had a
`ce_iv`/`pe_iv` key. `put_calendar.py`'s `_evaluate_entry()` reads
`front_leg.get("pe_iv", 0.0)`, which silently defaulted to `0.0` on
every call since the key never existed, and `front_iv <= 0` immediately
skips the cycle.
**Confirmed against the real SDK** (`upstox-python-sdk==2.29.0`, same
version verified for the GTT fix, LESSON-F4): `option_greeks`'s actual
type is `AnalyticsData`, with fields `vega, theta, gamma, delta, iv,
pop` — `iv` sits right next to `delta`, which was already being read
correctly a few lines above. This was never a missing SDK capability,
just a missing line.
**Fix:** added `"ce_iv": getattr(ce_greeks, "iv", 0.0) or 0.0` and the
`pe_iv` equivalent, right next to the existing delta extraction. Also
updated the function's docstring, which previously listed the returned
keys without `iv` — same "keep the docstring honest" discipline as
LESSON-F5's fix.
**Category:** same recurring pattern as almost everything else in this
file — a real signal (IV) existed in the data the whole time, the
wiring to actually read it was just never built. Not a strategy design
problem, not a threshold problem.
**Not yet live-verified**, same caveat as always: confirmed correct
against the real installed SDK's model, but the actual live effect
(whether put_calendar starts finding real entry conditions once IV is
populated) can only be confirmed once it runs through a real trading
session with a valid token.

## OPEN / UNRESOLVED ITEMS — status as of 2026-09-13

### Actioned this session (code written, needs deployment + your review)
- **`bn_survivor`'s own BankNifty regime instance** — real progress, but
  NOT flipped on. New isolated module `core/banknifty_regime_feed.py`
  (own polling loop, own candle buffer, own `regime_engine_banknifty`
  singleton) built deliberately SEPARATE from `core/market_context.py`
  so it carries zero risk to the live NIFTY singleton/dashboard. Wired
  into `strategy/survivor.py` via two new helper methods
  (`_current_regime()`, `_current_regime_stability()`) that fall back
  to today's exact behavior unless `ENABLE_BANKNIFTY_OWN_REGIME=true` is
  explicitly set — **default OFF, no live behavior change from merging
  this alone.** Offline-tested (isolated import, synthetic-candle
  classification) — passed, but **NOT live-verified against a real
  BankNifty feed or token.** Do not set the flag to `true` before: (a)
  running it live for at least a few sessions and sanity-checking its
  regime calls against reality, and (b) ideally backtesting it against
  `backfill_banknifty_candles.py`'s archived history through the
  now-regime-aware harness for an honest gate test, per your existing
  rule. Also note: it approximates OI/PCR-derived signals as neutral
  (no BankNifty option-chain OI pipeline yet) — flagged in the module
  docstring, not hidden.
- **PE/CE docstring drift, found while working on the above** —
  `_evaluate_pe_ce_entries()`'s method-level docstring still said the
  coupling was "left unchanged on purpose," contradicting the actual
  (already-fixed) independent `if` blocks a few lines below it. Fixed
  the docstring so it no longer contradicts the code — a stale comment
  next to correct code is exactly the kind of thing that misleads the
  next person who trusts the comment over reading further.
- **`/api/toggle-paper` one-click switch** — hardened. Now requires
  `?confirm=<TARGET_MODE>` matching the mode being switched TO, and
  sends a Telegram alert (same pattern as `/api/killswitch`) on every
  successful switch. No behavior change for any other endpoint.
- **`.gitignore` for backtest state files** — added
  `configs/backtest_survivor_risk_state.json`,
  `configs/backtest_*_risk_state.json`, and
  `backtest_survivor_trade_log.db`. Left `reports/*.json`/`reports/*.md`
  OUT of gitignore deliberately — those are small, genuinely useful
  audit-trail files already tracked in git; gitignoring them was your
  team's open question, not a clear win, so flagging it back to you
  rather than deciding it for you.

**To deploy the above:** these were written and offline-tested against
a clone of `anizeninc-lab/algo-trading` (HEAD `f5d79a2`), not your live
server — I have no push/server access. Files: `core/banknifty_regime_feed.py`
(new), `core/regime_engine.py`, `strategy/survivor.py`, `dashboard/api.py`,
`.gitignore` (all modified). Review, copy onto the server, `git add -A`,
commit, push — same workflow as `backfill_banknifty_candles.py` earlier.

### Still genuinely open — need your live server / cloud console access
- Candidates `8d8fb8a5` (`survivor.pe_enabled` True→False) and
  `5d51e1fd` (`survivor.ce_enabled` True→False) — real levers, each
  backed by a concerning win rate (0%/10%), backtest gate previously
  confounded. Now that LESSON-F5 (regime-aware backtest harness) is
  fixed, re-run: `python3 run_candidate_backtest_gate.py --candidate-id
  8d8fb8a5 --start <date> --end <date>` (repeat for `5d51e1fd`) against
  your archived NIFTY candle range.
- HTTPS/port-80 "filtered" mystery — unresolved since 2026-09-10. I have
  no network path to `92.4.90.188` from this environment to diagnose it
  directly. Next steps unchanged from that day's handoff: recheck,
  check Egress Rules (never checked), try a different external vantage
  point, compare directly against port 8081 at the same moment.
- Server public IP labeled Oracle "Ephemeral" — relevant to the
  broker/regulatory static-IP requirement. Fix is in the OCI console,
  not code: Networking → IP Management → reserve a Reserved Public IP,
  then re-attach it to the instance's VNIC in place of the ephemeral
  one. Needs your OCI console access, not something I can do from here.
- `patch_phase2.py` … `patch_survivor_rootcause_fixes.py` — confirmed
  these are NOT committed to git at all (checked `git ls-files`, none
  show up), meaning they only exist as loose files on the server itself.
  Recommendation: commit them under something like `archive/patches/`
  as a historical record of what was actually run (cheap, useful audit
  trail, matches the "don't let real work disappear" lesson from
  LESSON-F5/F12) rather than deleting — but this needs to happen on the
  server where the files actually live, not from this clone.

---

## REPO-VERIFIED STATUS (checked 2026-09-13 against
github.com/anizeninc-lab/algo-trading, HEAD=f5d79a2)

Confirmed genuinely merged (not just drafted) by reading the actual code
and commit history, not just prior write-ups:
- PE/CE decoupling — confirmed in `strategy/survivor.py`: the CE block
  is now an independent `if`, not `elif`.
- `min_regime_stability` — confirmed live-configured at 65.0 for BOTH
  `survivor` and `bn_survivor` (`configs/saviour_combo.json`). Note this
  means bn_survivor is being gated at 65.0 too, even though (per
  LESSON-F7) that gate still reads NIFTY's regime, not BankNifty's — the
  number changed, the underlying gap has not.
- Hedge stale-price, anchor-on-skip, GTT SDK bugs — all present in repo
  as described, with real commit hashes and dates (see LESSON-F1/F4
  above).
- BankNifty regime infra (`backfill_banknifty_candles.py`, generalized
  `RegimeEngine`) — merged, matches LESSON-F7, still not wired into
  bn_survivor itself.

**One open question the repo cannot answer:** several docs disagree on
whether this is running on real money at all right now. Older session
notes say "Mode: Paper trading (all strategies)"; `bn_survivor`'s config
has `paper_trade_override: True`; the original quant evaluation states
all 266 trades it analyzed were paper. `.env` (correctly) isn't
committed, so this can't be confirmed from the repo. **This is worth
confirming directly before treating any of these losses as real
financial risk** — it changes the urgency of everything else in this
file.

## Provenance / limitations of this file

This ledger was consolidated on 2026-09-12 from prior session handoffs,
a detailed quant evaluation, and patch scripts already produced in this
project. It is a synthesis of documented findings, not a fresh
independent re-analysis — no live server or trade-database access was
available when this file was written. Before trusting any "current
status" claim above for a decision, re-verify it against the live
system (`git log`, `pm2 jlist`, an actual query against
`research_archive.db` / the live trade log) rather than assuming it's
still accurate.

**How to use this file going forward:** read it before starting any new
investigation or fix. When a fix lands, add a new `LESSON-Fn` entry
(even if small) or update the status of an existing open item — don't
just fix silently. When something is confirmed to help, add it to
"WHAT WORKED" with what it had in common with other real fixes.
