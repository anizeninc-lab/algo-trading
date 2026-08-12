# HANDOFF — Aug 12, 2026 Session (GEX Strategy Completion + Self-Improvement Layer)

## Where the previous session (Aug 10) left off

`core/gex_calculator.py`, `core/gex_ema_stack.py`, `core/gex_entry_rules.py` existed and were
validated against live data. `core/gex_trade_management.py` was built but **not yet tested**.
`strategy/nifty_gex.py` did not exist. Nothing was wired into `SaviourCombo` or `main.py`.
Nothing was committed to git.

## What this session actually did — Part 1: finish the GEX strategy build

1. **Tested `gex_trade_management.py` against live data** — built `test_gex_trade_management_live.py`,
   mirroring the pattern of the other `test_*_live.py` scripts. Ran clean end-to-end: live GEX
   regime, live EMA stack, and a real `TradePlan` all computed correctly from live ticks.

2. **Found and fixed a real position-sizing bug.** With the ₹50,000 fixed capital pool and
   `risk_pct=1.5%`, `risk_amount` was only ₹750 — meaning any stop distance wider than
   `750/65 ≈ 11.5 points` rounded `quantity` to 0 lots and rejected the trade, *regardless of how
   good the setup was*. Confirmed live: a 3.72 R:R setup (excellent) was rejected purely on
   lot-size rounding. Fixed in `core/gex_trade_management.py`'s `compute_position_size()` with a
   principled, bounded fix — not a blind risk_pct increase:
   - `MIN_LOT_TOLERANCE_MULTIPLE = 2.0` — allow exactly 1 lot if its real risk is within 2x the
     intended `risk_amount`
   - `MAX_SINGLE_TRADE_RISK = 2500.0` — hard ceiling, deliberately reused from
     `risk_manager.py`'s existing per-trade stop-loss cap, so the two risk systems stay consistent
     rather than introducing a second, disconnected number
   - Genuinely oversized stops still correctly get rejected either way

3. **Built `strategy/nifty_gex.py`** — the full `BaseStrategy` subclass, modeled on
   `wave_extractor.py`'s structure. Key design decisions, all flagged in the file's own docstring:
   - GEX/EMA/entry-checklist evaluation throttled to once per `gex_poll_interval` (60s default) —
     not tick-driven, since the GEX fetch is a real REST round-trip
   - Stops/targets from `build_trade_plan()` are in **index points** (spot price), not option
     premium — exit monitoring compares live NIFTY spot against `plan.stop_price`/`target1_price`
   - Entry order: LIMIT BUY at the option's current `last_price` (safer, may not fill — explicit
     decision, not a market order)
   - Only one active trade at a time (matches the spec's "no partial trades" selectivity)
   - `candles_elapsed` for the time-stop is a proxy (session candle count growth ÷ 5), not a true
     5-min candle counter — acceptable since the 14:45 IST hard cutoff is the real safety net
   - Strike chain resolved once per day at `on_start()`; not re-resolved on a wide intraday spot
     move — flagged as a known first-draft limitation

4. **Wired `nifty_gex` into `SaviourCombo`** (`strategy/saviour_combo.py`) — added
   `self.nifty_gex`, start/stop calls, gated behind `config.enable_nifty_gex` (default `False`).

5. **Wired into `main.py`** behind a new `ENABLE_NIFTY_GEX` env var (mirrors the existing
   `ENABLE_BANKNIFTY` pattern) — `.env` now has `ENABLE_NIFTY_GEX=true`.

6. **Fixed dashboard visibility** — the handoff assumption that dashboard visibility would be
   "automatic" was **wrong**. Two hardcoded lists needed `nifty_gex` added manually:
   - `dashboard/api.py` line ~957 — the `/api/capital` strategy dict (added with
     `margin_per_lot: 15000`, matching `risk_manager.MARGIN_PER_BUY_LOT`, since `nifty_gex` only
     ever registers BUY trades, unlike the SELL-based `survivor`/`wave_extractor`)
   - `dashboard/frontend/src/App.jsx` line ~2668 — the strategy-cards array
   - Frontend rebuilt via `npm run build` in `dashboard/frontend/`, bot restarted with
     `pm2 restart trading-bot --update-env`, confirmed the "Nifty Gex" card renders correctly
     with accurate capital numbers (₹50,000 cap, matching `risk_manager.get_per_strategy_cap()`)

7. **Restarted the bot, confirmed clean start.** Strike chain resolved 42/42 contracts, `nifty_gex`
   went `IDLE → RUNNING`, zero errors in `pm2_trading_bot_err.log`. Confirmed running in
   **paper mode** (`PAPER_TRADE=true`).

8. **Git hygiene** — `trade_log.db` was tracked in git despite being in `.gitignore` (a pre-existing
   leftover); untracked it with `git rm --cached` (file stays on disk, just stops polluting future
   commits). Removed a duplicate `test_gex_calculator_live.pyxx` file and a stray `tatus` artifact
   (leftover `git log` redirect). Committed and pushed: **`db57a85`**.

## What this session did — Part 2: self-improvement / analysis layer

User asked for the "self-improvement layer" concept from a general quant-workflow prompt
(NOT any specific external strategy — the referenced article turned out to contain no actual
methodology, just the generic Research→Code→Backtest→Live→Post-mortem→Fine-tune loop framing).

**Deliberately scoped down from the original ask.** The full prompt wanted autonomous
walk-forward optimization with auto-promotion of validated config changes. **This repo has no
historical backtesting engine** — everything verified so far is live-tick testing, not historical
replay. Building a fake walk-forward validator would create false statistical confidence, which
is worse than not having the feature. Built the honest, safe subset instead:

1. **DB migration**: added `entry_context` (TEXT, JSON) column to the `trades` table in
   `core/trade_log.py` — additive, follows the exact same pattern as the existing
   `peak_pnl`/`trough_pnl` migrations. `open_trade()` now accepts an optional `entry_context: str`
   parameter and stores it.

2. **Wired entry-context capture into all 3 strategies** (user explicitly wanted all 3 tonight,
   not just `nifty_gex`):
   - `strategy/nifty_gex.py` — captures GEX regime, net_gex, checklist structure_type, plan
     stop/target/R:R/quantity, spot at entry
   - `strategy/wave_extractor.py` — captures net position imbalance, bracket sell/buy prices,
     adaptive_gap flag (both SELL and BUY fill branches)
   - `strategy/survivor.py` — captures direction/strike/nifty_price on the main SELL leg, and
     direction/short_strike/hedge_strike/hedge_premium on the hedge BUY leg (covers both
     `survivor` and `bn_survivor`, which share this class)

3. **`core/post_mortem.py`** — descriptive-only report generator. Groups closed trades by
   strategy, exit reason (parsed from `notes`), entry hour, `entry_context` regime/direction, and
   holding time. **No autonomous changes of any kind** — explicitly documented in the module
   docstring as read-only/descriptive.

   Ran against real history tonight: **68 closed trades over 14 days, net P&L -₹23,315, 16.2% win
   rate, profit factor 0.27.** Notable, un-interpreted findings surfaced:
   - `STOP_LOSS` exits: 22 trades, 0% win rate (by definition), **-₹20,986 — ~90% of total loss**
   - `survivor`: 24 trades, only **4.2% win rate** (vs. `wave_extractor`'s 26.7%,
     `bn_survivor`'s 14.3%)
   - All 68 trades show `no_context_captured` since they predate tonight's wiring — **every trade
     from now on will have real context**, so future reports can actually break down by regime

4. **`core/candidate_config.py`** — human-reviewed config-change proposal store
   (`configs/candidate_config.json`). `propose_change()` / `list_candidates()` / `decide()`
   functions plus a CLI (`python3 -m core.candidate_config propose/list/decide`). **Approving a
   candidate is a decision record, not an action** — the module has zero write access to any
   strategy file; applying a change still means manually editing real code, same as tonight's
   sizing fix. First real candidate logged: `nifty_gex.risk_pct` 1.5%→2% tuning question,
   deferred pending real paper-mode skip-rate data (matches the decision made earlier in this
   same session).

5. **`.vscode/settings.json`** — hides `*.bak*` clutter from VS Code Explorer (cosmetic only,
   doesn't touch git or disk).

6. Committed and pushed: **`b94008a`**.

## Current git state

```
db57a85  Add Nifty GEX A+ Setup strategy...
b94008a  Add self-improvement/analysis layer...
```

Both pushed to `origin/main`. Working tree should be clean except for ongoing runtime state file
churn (`configs/regime_state.json` etc. — expected, the bot rewrites these constantly).

## What's confirmed working right now

- `nifty_gex` is **live in paper mode**, dashboard-visible, capital-gated via `risk_manager`,
  evaluating real entries every 60s. No entries fired yet as of session end (normal — the A+
  checklist is selective by design).
- All 4 `core/gex_*.py` modules verified against live data this session or last.
- Dashboard shows accurate capital numbers for all 4 strategies including `nifty_gex`.
- `post_mortem.py` and `candidate_config.py` both verified working against real DB data.

## Known gaps / NOT done, flagged honestly

- **No historical backtesting engine exists.** Everything verified has been live-tick testing.
  This blocks any real walk-forward validation or statistically-grounded auto-promotion — those
  remain explicitly unbuilt, on purpose.
- **`nifty_gex` has never actually fired an entry yet.** Everything verified is the pipeline
  (data→regime→plan), not a real signal-to-fill cycle. First real signal is still pending.
- **Strike chain resolution is once-per-day only** in `nifty_gex.py` — a large intraday spot move
  could leave the GEX-flagged target strike outside the originally resolved ATM±10 range,
  silently skipping otherwise-valid entries. Not fixed, just documented in the file.
- **`survivor`'s 4.2% win rate** (from tonight's post-mortem) is a real, concerning number that
  hasn't been investigated — purely surfaced, not diagnosed. Worth a real look.
- **`nifty_gex.risk_pct` tuning** — genuinely open question, logged as candidate `d59db2e1`
  (or check `python3 -m core.candidate_config list` for current ID), deliberately deferred
  pending real observed skip rate.
- Entry-context capture is only useful going forward — the 68 pre-existing trades have none.

## Next session should start with

1. **Check `nifty_gex`'s activity since this session ended** —
   `grep "nifty_gex" ~/trading-algo/logs/pm2_trading_bot_out.log | tail -50` — has it fired any
   entries yet? If yes, that's the first real end-to-end validation of the whole pipeline built
   over these two sessions.
2. **Re-run the post-mortem** — `python3 -m core.post_mortem` — now that more trades exist,
   check whether any now show real `entry_context` (confirms the wiring is actually working live,
   not just in the isolated test we ran).
3. **Look at `survivor`'s 4.2% win rate** honestly — is this a real strategy problem, a data
   artifact (small sample, 24 trades), or something regime-specific? Don't jump to a fix without
   evidence — that's exactly what `candidate_config.py` exists for: log the hypothesis with
   evidence, decide later.
4. Revisit the `nifty_gex.risk_pct` candidate once a few days of real paper skip-rate data exists.
5. Consider whether `wave_extractor`/`survivor` need the same `.pyxx`-style file hygiene check
   (unlikely, but worth a quick `find` given how it was found here).

## Housekeeping

- Bot was restarted this session (`pm2 restart trading-bot --update-env`) — twice: once to load
  `ENABLE_NIFTY_GEX=true`, once implicitly covered by the same restart for the dashboard capital
  fix (backend picks up on process restart, not hot-reload).
- Frontend was rebuilt (`npm run build` in `dashboard/frontend/`) — if the dashboard ever looks
  stale after a future `App.jsx` edit, remember this rebuild step; editing the `.jsx` source alone
  does nothing until rebuilt.
- Market was open for part of this session (confirmed via live tick data flowing) — all live
  tests performed were against genuinely live data, not stale.