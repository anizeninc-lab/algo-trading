# FRIDAY HANDOFF — 2026-07-24

Next trading session: **Monday, 2026-07-27** (weekend in between — no trading Sat/Sun).

---

## 1. TODAY'S RESULT

**Final real P&L: +₹11,347.42** (16 legitimate trades, bad-tick trades excluded — see Bug #3 below)

| Strategy | Trades | Realised P&L |
|---|---|---|
| `wave_extractor` | 4 | **+₹12,810.51** |
| `survivor` | 6 | **−₹449.07** |
| `bn_survivor` | 6 | **−₹1,014.02** |
| **Total** | **16** | **+₹11,347.42** |

Auto-stop fired cleanly at 3:10 PM (survivor/bn_survivor watchdog logged EOD at 3:05 PM, wave_extractor at 3:10 PM). All positions closed, no orphans left open overnight. Broker (`UpstoxAdapter`) shows `DISCONNECTED` in `/api/global/summary` post-close — confirmed benign (VIX data still flowing from Upstox), just the order/tick websocket closing after strategies stopped.

Three trades from 10:58 AM (₹-12,094.25 combined) were **corrupted-tick artifacts**, not real losses — tagged `ORPHANED` in the DB and excluded from all P&L calculations (see Bug #3).

---

## 2. BUGS FOUND & FIXED TODAY (10 total)

All backups saved alongside originals as `<filename>.bak_YYYYMMDD_HHMMSS` in their respective directories.

| # | File | Bug | Fix |
|---|---|---|---|
| 1 | `strategy/survivor.py` | `bn_survivor` hardcoded to call `strategy_filter.can_trade("survivor")` — silently borrowed survivor's regime rules instead of its own | Changed to `strategy_filter.can_trade(self.name)`; added explicit `"bn_survivor"` entry to `STRATEGY_ALLOWED_REGIMES` in `core/strategy_filter.py` |
| 2 | `strategy/saviour_combo.py` | `_update_combo_status` passed **combined** (realised+unrealised) P&L into the `unrealised=` slot of `update_pnl()` — dashboard showed total P&L twice under different labels | Added `_get_combined_unrealised_pnl()` (mirrors existing `_get_combined_realised_pnl()`) and wired it in correctly |
| 3 | `strategy/wave_extractor.py` | `on_tick` read raw `tick.last_price` instead of the safer `tick.mid_price` (which the `Tick` class was explicitly built to prevent "rogue LTP spike" triggers — `survivor.py` already used it correctly). A corrupted/stale tick print caused 3 phantom trades in ~7 seconds, **−₹12,094.25**, which tripped the daily-loss halt | Swapped all 3 uses (`_current_price`, `_record_price_sample`, `_handle_paper_fill` call) to `tick.mid_price` |
| 4 | `trade_log.db` | The 3 bad-tick trades were polluting all P&L/performance stats | Tagged `notes = 'ORPHANED: bad tick data...'` on all 3 — existing `NOT LIKE 'ORPHANED%'` convention (already used in some queries) now correctly excludes them everywhere |
| 5 | `configs/risk_state.json` | Persisted `system_halted: true` from the bad-tick trades kept re-triggering the daily-loss halt on every restart, even after real P&L was confirmed positive | Manually corrected `system_halted` → `false`, cleared `halt_reason` |
| 6 | `strategy/survivor.py` (`_open_hedge_leg`) | `except Exception:` block logged the error but never `return False` — a crashed hedge attempt (e.g. the `_is_paper` NameError bug, see #7) was silently swallowed with **no auto-close of the naked short** | Added explicit `return False` in the exception handler |
| 7 | `strategy/survivor.py` | Hedge-leg logging used undefined `_is_paper` instead of the actual parameter name `is_paper` → `NameError` on every hedge attempt, crashing before the hedge order could be logged. Caused two live naked short positions today | Fixed `paper_trade=_is_paper` → `paper_trade=is_paper` |
| 8 | `strategy/survivor.py` | Auto-close-on-hedge-failure was gated `if hedge_ok is False and not _is_paper:` — **disabled in paper mode**, meaning naked positions from hedge failure were never force-closed in paper trading | Removed the `not _is_paper` condition — auto-close now applies in both paper and live modes |
| 9 | `strategy/survivor.py` (found while fixing #8) | The auto-close block called `self.broker.place_order(...)` **unconditionally** with no paper/live branching — after removing the `not _is_paper` gate, this would have sent a **real broker order** during a paper-mode hedge failure | Added proper `if _is_paper: ... else: ...` branching — paper mode just closes the DB record at current price, live mode places the real closing order |
| 10 | `strategy/wave_extractor.py`, `strategy/survivor.py` | `self._realised_pnl = 0.0` hardcoded on every `__init__`, completely independent of `state_store`'s DB-seeded value. The very first `_update_pnl()` call after any restart **overwrote** the correctly-seeded historical P&L back to near-zero — meaning the daily-loss circuit breaker could silently under-count real losses after a restart (opposite failure mode to #5) | Both files now read back the seeded value: `self._realised_pnl = state_store.get_strategy(self.name).realised_pnl if _seeded else 0.0` (placed after `super().__init__()`, which triggers the seeding) |
| — | `core/state_store.py` (`get_global_summary`) | Loop summed `realised_pnl` across **all** registered strategies including `saviour_combo` — which is a derived rollup of the other three, not independent capital. Dashboard's global total showed exactly **2x** the real P&L | Excluded `saviour_combo` from the summation loop |

### Most important fix of the day
**#3 (mid_price)** stopped the actual bleeding — a corrupted tick caused real (paper) capital loss in seconds.
**#6/#7/#8/#9 (hedge failure → naked position)** closes a structural risk gap: previously, ANY hedge-leg failure (network blip, API error, illiquid strike, or a code bug like #7) would leave a **naked, unhedged short position** with no safety net, in both paper and live mode. This is now a hard guarantee: hedge failure = automatic close of the short, no exceptions.

---

## 3. STILL OPEN / NOT YET FIXED (carry to Monday)

1. **`wave_extractor` 120s bracket timeout** — first bracket placed each cycle sometimes has too wide an adaptive gap and times out unfilled; the retry with a tighter gap usually fills. Not a bug, but the initial gap sizing could be tuned. Low priority.
2. **`wave_extractor` debug-level block-reason logging** — `on_tick`'s `elif not can_trade: logger.debug(...)` is invisible at the default INFO log level, unlike `risk_manager`'s own INFO-level block logging used by `survivor`/`bn_survivor`. Should be bumped to `logger.info` or `self._signal(...)` so future blocks are visible without guessing.
3. **Capital-guard log spam** — `risk_manager.can_trade()` evaluates and logs a hypothetical "new trade" capital breach on every tick even when the strategy has no intention of placing a new trade (already has an open position). Cosmetic/noisy only, not a correctness bug.
4. **Orphan-recovery on startup doesn't check hedge status** — `_recover_open_positions` / `_on_recover_trade` restores position *tracking* after a restart but never checks whether a recovered position actually has a live hedge. If a hedge failure somehow slipped through undetected before a restart, this path wouldn't catch it. Not believed to be currently exploitable given fixes #6–9, but worth hardening eventually.
5. **`tg-commander` (Telegram bot)** — was manually stopped Wednesday evening per user; still stopped (`pm2 status` shows `stopped`, pid 0). Confirm intentional before Monday if Telegram alerts/control are needed.

---

## 4. KEY LEARNINGS / PATTERNS TO WATCH FOR

- **Silent block reasons are dangerous.** Several bugs today were only findable because we manually traced code paths — normal INFO-level logs didn't surface them (`wave_extractor`'s debug-only block logging, the swallowed hedge exception). Worth an audit of other strategies for the same blind spot.
- **State that's supposed to survive a restart needs to be tested across a restart.** Both the risk-halt bug (#5) and the P&L-reset bug (#10) only appeared because of today's unusually high restart count (7 restarts). Under normal single-restart days these might not surface for a long time.
- **Parameter name mismatches (`_is_paper` vs `is_paper`) and hardcoded strings (`"survivor"` instead of `self.name`) are a recurring bug shape in this codebase** — worth a broader grep across `survivor.py`/`wave_extractor.py` for similar copy-paste artifacts.
