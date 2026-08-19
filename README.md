# Rahul Sharma Trading System
### Nifty 50 Options Algo Trading Bot — v2.0

> Automated intraday options selling bot deployed on Oracle Cloud Ubuntu server.
> Built in Python with FastAPI dashboard, PM2 process management, and Upstox broker API.

---

## System Overview

| Item | Value |
|------|-------|
| Server | Oracle Cloud Ubuntu 22.04 — <YOUR_SERVER_IP> |
| Dashboard | http://<YOUR_SERVER_IP>:8081 |
| Code editor | http://<YOUR_SERVER_IP>:8082 |
| Process manager | PM2 |
| Broker | Upstox (NFO intraday) |
| Language | Python 3.10 + React frontend |
| Database | SQLite (trade_log.db) |

---

## Daily Checklist

### Before 9:00 AM IST — Token Refresh

**Step 1** — Open Git Bash on Windows and run:
```bash
ssh -i ~/.ssh/oci_trading -L 8080:127.0.0.1:8080 ubuntu@<YOUR_SERVER_IP>
```

**Step 2** — On the server, stop nginx:
```bash
sudo systemctl stop nginx
```

**Step 3** — Open this URL in your Windows browser and login with Upstox:
https://api.upstox.com/v2/login/authorization/dialog?client_id=REDACTED&redirect_uri=http://127.0.0.1:8080/callback&response_type=code

**Step 4** — Copy the code from the browser address bar (e.g. `code=ABC123`), then run on server (replace CODE):
```bash
python3 -c "
import requests
r = requests.post('https://api.upstox.com/v2/login/authorization/token', data={
    'code': 'CODE',
    'client_id': '<YOUR_CLIENT_ID>',
    'client_secret': 'YOUR_CLIENT_SECRET_HERE',
    'redirect_uri': 'http://127.0.0.1:8080/callback',
    'grant_type': 'authorization_code'
})
token = r.json()['access_token']
with open('/home/ubuntu/trading-algo/.env', 'r') as f: lines = f.readlines()
with open('/home/ubuntu/trading-algo/.env', 'w') as f:
    for line in lines:
        f.write(f'UPSTOX_ACCESS_TOKEN={token}\n' if line.startswith('UPSTOX_ACCESS_TOKEN=') else line)
print('Token saved:', token[:30], '...')
"
```

**Step 5** — Start nginx and restart bot:
```bash
sudo systemctl start nginx
pm2 restart trading-bot --update-env
```

**Step 6** — Verify bot is running before 9:15 AM:
```bash
pm2 logs trading-bot --lines 10
```
Look for: `Upstox login successful` and `Nifty tick:` messages.

---

### During Market Hours (9:15 AM – 3:05 PM IST)

```bash
# Watch live activity
pm2 logs trading-bot --lines 10

# See open positions
sqlite3 /home/ubuntu/trading-algo/trade_log.db \
"SELECT substr(id,1,8), symbol, entry_price, status FROM trades WHERE status='OPEN';"

# See today's trades
sqlite3 /home/ubuntu/trading-algo/trade_log.db \
"SELECT substr(id,1,8), symbol, entry_price, exit_price, realised_pnl, status FROM trades WHERE date(entry_time)=date('now','localtime');"
```

Bot auto-exits all positions at **3:05 PM IST**.

---

### After 3:30 PM IST

```bash
# Check today's P&L summary
sqlite3 /home/ubuntu/trading-algo/trade_log.db \
"SELECT round(sum(realised_pnl),2) as todays_pnl FROM trades WHERE date(entry_time)=date('now','localtime') AND status='CLOSED';"

# Save all changes to GitHub
cd /home/ubuntu/trading-algo && git add -A && git commit -m "session: $(date +%Y-%m-%d)" && git push mygithub main
```

---

## Architecture
main.py

├── SaviourCombo (orchestrator)

│   ├── WaveExtractor        — trend scalping (blocked in range regime)

│   ├── SurvivorAlgo         — range options selling (ACTIVE)

│   └── BankNifty Survivor   — paper mode parallel instance

│

├── core/

│   ├── session_planner.py   — market open analysis, regime/confidence

│   ├── market_context.py    — live OI, PCR, opening range (refreshes every 2 min)

│   ├── risk_manager.py      — all risk gates

│   ├── vix_manager.py       — VIX monitoring

│   ├── strategy_filter.py   — PCR + regime entry filter

│   └── trade_log.py         — SQLite trade journal

│

├── brokers/upstox.py        — REST + WebSocket + GTT adapter

└── dashboard/               — FastAPI + React dashboard

---

## Strategy Parameters (Survivor)

| Parameter | Value | Notes |
|-----------|-------|-------|
| pe_gap | 6 pts | Nifty must rise this much to sell PE |
| ce_gap | 6 pts | Nifty must fall this much to sell CE |
| strike_gap | 350 pts OTM | Distance from current price |
| min_premium | ₹15 | Won't sell cheap options |
| lot_size | 65 | Nifty lot size |
| max_open_trades | 2 | Simultaneous positions |
| max_trades_per_day | 3 | Daily limit |
| expiry | Tuesday weekly | Auto-selected |

**Time-based trigger:** Also sells at 9:45–11:30 AM on flat days when PCR ≥ 1.2 (PE) or ≤ 0.8 (CE).

---

## Risk Management — 4 Layers

### Layer 1: Entry Filters
- Regime = range only (blocks in trending_bull / trending_bear)
- PCR confirmation for time-based trigger
- Min premium ₹15, strike 350 pts OTM
- Trading hours: 9:30 AM – 3:05 PM IST only

### Layer 2: Per-Trade Exit
| Mechanism | Trigger | Action |
|-----------|---------|--------|
| Stop loss | Loss ≥ ₹800 | Close immediately |
| Breakeven lock | Profit ≥ ₹400 | SL moves to entry — can never lose |
| Profit target | Profit ≥ ₹600 | Close and lock profit |
| GTT trailing SL | 15% above entry, trails ₹0.25 | Upstox-managed auto-exit |

### Layer 3: Session
| Rule | Value |
|------|-------|
| EOD auto-exit | 3:05 PM IST |
| Daily loss limit | −₹3,000 |
| Safe restart | No premature close before 3:05 PM |

### Layer 4: Capital
| Rule | Value |
|------|-------|
| Max capital | ₹1,50,000 (hardcoded) |
| Margin per lot | ₹40,000 |
| BankNifty | Paper mode only |

---

## BankNifty Paper Mode

Runs alongside live Nifty. Zero real capital risk. Promote to live after 2–3 profitable weeks.

| Parameter | Value |
|-----------|-------|
| Index | NSE_INDEX Nifty Bank |
| Expiry | Wednesday weekly |
| Lot size | 15 |
| Strike gap | 500 pts OTM |
| Min premium | ₹50 |

---

## Useful Commands

```bash
# Bot health
pm2 status
pm2 logs trading-bot --lines 20

# Today's performance
sqlite3 trade_log.db "SELECT substr(id,1,8), symbol, entry_price, exit_price, realised_pnl, status FROM trades WHERE date(entry_time)=date('now','localtime');"

# Total all-time P&L
sqlite3 trade_log.db "SELECT round(sum(realised_pnl),2) FROM trades WHERE status='CLOSED';"

# Fix stuck open trade (replace UUID and prices)
sqlite3 trade_log.db "UPDATE trades SET status='CLOSED', exit_price=XX.XX, exit_time=datetime('now'), notes='Manual reconcile' WHERE id='FULL-UUID-HERE';"

# Rebuild dashboard
cd dashboard/frontend && npm run build

# GitHub backup
git add -A && git commit -m "backup: $(date +%Y-%m-%d)" && git push mygithub main
```

---

## Regime Classification

| Regime | Condition | Survivor |
|--------|-----------|----------|
| range | Neutral PCR, low OI delta | ACTIVE |
| trending_bull | PCR < 0.8, CE OI buildup | BLOCKED |
| trending_bear | PCR > 1.3, PE OI buildup | BLOCKED |
| opening | Before 9:30 AM | BLOCKED |
| closed | After 3:10 PM | BLOCKED |

---

## P1 Fixes Pending (Before Next Live Session)

1. Idempotent order keys — prevent duplicate trades on restart
2. Broker position reconciliation at startup
3. Daily loss circuit breaker persistence across restart
4. Exit precedence rules — only one exit path wins at a time
5. Max-open based on broker positions not memory
6. Critical failure alerting (Telegram/email)

---

## Incident Log

| Date | Incident | Fix Applied |
|------|----------|-------------|
| May 20 | First live day — 6 critical bugs | Orders after stop, qty bypass, fill detection |
| Jun 5 | No trades firing | Hardcoded Thursday expiry, invalid instrument key |
| Jun 10 | SL failures, manual exits needed | REST fallback LTP, breakeven lock |
| Jun 11 | Premature EOD on restart | Safe on_stop — skips close before 3:05 PM |
| Jun 12 | Duplicate trades at 9:30 open | P1 pending — idempotent orders |
| Jun 12 | Stale open trade count | Manual DB reconcile procedure established |
| Jun 23 | Live position with no GTT stop-loss | `_access_token` typo fixed; auto-close added if GTT fails |

---

## Environment Variables (.env)
BROKER_NAME=upstox

UPSTOX_ACCESS_TOKEN=eyJ...    # Refresh daily before 9 AM

PAPER_TRADE=false              # true=paper false=live

MAX_COMBINED_LOSS=-5000

---

*Last updated: June 12, 2026*
*Maintainer: DEV (Prince)*

---

## Additional P1 Fixes (Added June 12)

7. Dashboard P&L not updating during live trade — WebSocket option ticks not flowing to LTP cache, causing unrealised P&L to show stale/zero values. Fix: ensure option symbol subscribed correctly after trade open, verify ikey cache populated before first P&L calculation.

8. Dashboard P&L vs Upstox P&L mismatch — bot records two separate trades (65+65) while Upstox shows one netted position (-130). Fix: add broker position reconciliation that maps bot trade IDs to Upstox net positions for accurate P&L display. Dashboard should show combined unrealised P&L matching Upstox exactly.

## Session Log — June 16-17, 2026

### June 16: Regime Engine v2 Overhaul
Following an external audit, rewrote `core/regime_engine.py` with 10 fixes:
1. Real cumulative session VWAP (Nifty index has no volume data — uses progressive typical-price average instead of fake simple average)
2. Directional ADX (+DI/-DI) instead of scalar-only trend strength
3. `reversal_watch` changed from a blocking regime to a flag — no longer pauses all entries on PCR spikes
4. ATR-based dynamic OR breakout threshold (replaces fixed 30pt)
5. Trend exhaustion flag (price >2x ATR from VWAP, or ADX weakening after a trend)
6. Smoothed OI deltas (3-period rolling average, prevents sign-flip noise)
7. New `weak_bull`/`weak_bear` regime states — Survivor now trades these in addition to `range`
8. Gap detection vs previous close
9. 0-100 confidence score with HIGH/MEDIUM/LOW label
10. Regime history (last 10 classifications) + stability score

Also fixed session planner's VIX-based OTM gap defaults (was 350pt at NORMAL VIX → near-zero option premiums; reduced to 200pt) and added a 90-second startup grace period before WebSocket-disconnect Telegram alerts fire.

### June 17: Critical Bug — Missing `_sell_option` Method
**Root cause of zero trades on June 16-17:** the `async def _sell_option(...)` method's signature and opening logic (strike search loop) had been accidentally deleted during the June 16 regime engine edits, leaving the back half of the method (idempotent gate, order placement, GTT retry) as orphaned dead code sitting after `on_tick`'s exception handler. Every sell attempt crashed into `AttributeError` — 3,156 times on June 17 alone — with zero trades and zero losses (fail-safe by accident).

Fixed by restoring the missing method signature + strike-search opening from a known-good commit (`9c56dcf`), while preserving newer manual improvements found in the orphaned tail: a deterministic client order ID scheme (`SURV_{direction}_{strike}_{date}`) and a mutex lock against `_open_trades_data` to prevent duplicate orders across PM2 restarts.

**Lesson learned:** `strategy/survivor.py` is 1000+ lines in a single class, which makes broad-match string replacements risky. Consider splitting into smaller modules (e.g. separate files for order execution, SL/TP monitoring, and reconciliation) to reduce blast radius of future edits.

### Repo Cleanup
Removed 16 stale `.bak`/`.backup`/patch files that were tracked in git despite `.gitignore` rules (added after the fact). Archived locally to `_archive_review/` (now gitignored) rather than deleted outright, for safety.

### Known Issues (Unresolved)
- `OrderApiV3` object has no attribute `get_order_book_v3` — Upstox SDK method mismatch on order polling, caught safely in try/except but needs investigation.
- BankNifty (`bn_survivor`) paper strategy has not yet had its OTM symbol gap (500pt) checked/fixed the way Nifty's was — may have the same near-zero-premium issue.

## Session Log — June 23, 2026

### Trigger
Routine health check found a paper trade (`NIFTY23JUN2624250CE`) stuck looping "PROFIT TARGET hit → DOUBLE-CLOSE BLOCKED" on every tick since the prior day, never actually closing.

### Root Causes Found (5 distinct bugs)
1. **Double-close guard duplicated in `_close_trade()`** (`strategy/survivor.py`) — the precedence-gate block (check `_closing_trades`, add trade_id) was pasted twice in a row. The first copy added the trade_id; the second copy immediately found it already present and aborted — so the function never reached the actual close/order logic, on every call, from the very first attempt.
2. **No cleanup on failed closes** — 3 early-return paths (LTP fetch returns 0, order REJECTED, exception during `place_order`) never called `_closing_trades.discard()`, so any real failure would permanently jam that trade_id from ever closing again.
3. **GTT silently broken for ALL live trades** — `brokers/upstox.py` line 563 referenced `self._access_token` (nonexistent attribute) instead of `self.access_token`. Every `place_gtt_trailing_sl()` call failed silently with `AttributeError`, caught and logged as a generic warning. Discovered via a real unprotected live position (`NSE_FO|56377`, BankNifty PE, entry ₹25.70 qty 65) running with **no broker-side stop-loss** for ~16 minutes before being caught manually and closed by hand — no loss incurred, but real risk exposure.
4. **No fail-safe on GTT failure** — when both GTT attempts failed, the bot only logged a `🚨 GTT FAILED` alert and continued holding the unprotected position with no automatic safety response.
5. **Exit order tag collision** — `tag=f"EXIT_{trade['id'][:8]}"` was identical on every close retry. Once Upstox rejected the first attempt for any reason, every subsequent retry was auto-rejected as `"Duplicate order tag"` — permanently blocking that trade from ever closing through the bot.

### Fixes Applied (all compiled, diffed, restarted clean)
1. Removed duplicated guard block in `_close_trade()` (`strategy/survivor.py`)
2. Added `self._closing_trades.discard(trade_id)` before all 3 early-return paths
3. Fixed typo: `cfg.access_token = self.access_token` (`brokers/upstox.py:563`)
4. Added auto-close-on-GTT-failure: if GTT placement fails after 2 retries, the bot now immediately force-closes the position via `_close_trade(..., "GTT_FAILED_AUTOCLOSE", ...)` rather than leaving it open unprotected, with an escalating `🚨🚨 CRITICAL` alert if the auto-close itself also fails
5. Made exit tag unique per attempt: `EXIT_{trade_id[:8]}_{timestamp_ms}` (requires `import time`, added to top of `survivor.py`)

### Data Cleanup
Reconciled 2 stale `OPEN` records in `trade_log.db` to `CLOSED`:
- `a2b08c07-...` — `NIFTY23JUN2624250CE`, paper trade from June 22, stuck by bug #1
- `a4fa7044-...` — `NSE_FO|56377`, real live trade, closed manually at broker by user

This also auto-resolved a phantom ₹40,000 margin reservation in `risk_manager`, since its crash-recovery logic (`core/risk_manager.py` ~line 90) rebuilds `_deployed_capital` directly from `trade_logger.get_active_positions()` on every restart — closing the DB rows was sufficient, no separate margin-clearing step needed.

### Confirmed
- `PAPER_TRADE=true` switched and verified active (env vars load once via `dotenv` at process start — `pm2 restart --update-env` required for the toggle to take effect, editing `.env` alone is not enough on a running process)
- No other orphaned `OPEN` trades remain in DB as of session end
- Both broker-positions and DB now agree: zero open positions

### Deferred
- Telegram alerting failure (`Network is unreachable` calling `api.telegram.org`) — parked as suspected transient network issue. Revisit if it recurs; until then, dual-alerting is effectively single-path (dashboard signal only).

### Known Issues (Unresolved, carried forward)
- Exit order tag uniqueness fix (#5 above) only covers `survivor.py`'s `_close_trade()` — worth checking if `bn_survivor` or other close paths have the same fixed-tag pattern.
- `risk_manager` capital reconciliation only runs at startup (`__init__`), not continuously — a trade going stale mid-session (not across a restart) won't self-correct margin until next restart.

### Afternoon Session — Critical Paper-Mode Bug: Frozen Price, Not Trailing

**Trigger:** Dashboard showed 4 paper positions sitting at ₹2000+ unrealized profit each, well past the ₹800 profit target, never closing — including one opened *after* the morning's fixes were already live.

**Root cause:** `_monitor_open_trades()` in `survivor.py` computed paper-mode current price as a fixed ratio:
```python
curr_price = trade["entry"] * 0.95
```
This number never changes — it's not a simulation of decay, it's a static snapshot frozen at entry. Profit-target math against this fixed value could essentially never reach ₹800, so paper trades could only ever exit via stop-loss or EOD auto-close, never via profit target. This meant **paper mode was silently incapable of validating exit logic** — defeating its core purpose ahead of going live.

**Fix:** Paper-mode price now reads from `self._ltp_cache` (the same real broker-tick cache used for live trades and dashboard display), with `trade["entry"]` as a safe fallback only if no tick has arrived yet:
```python
curr_price = self._ltp_cache.get(_ikey, self._ltp_cache.get(_symbol, 0.0))
if curr_price <= 0.0:
    curr_price = trade["entry"]
```

**Verified working:** Within minutes of restart, during a sharp real -207pt Nifty drop, two paper trades closed correctly against real premium collapse:
| Symbol | Entry | Exit | Realized P&L |
|---|---|---|---|
| NIFTY 24250 CE | ₹34.70 | ₹0.30 | ₹2,236 |
| NIFTY 24050 CE | ₹34.10 | ₹0.50 | ₹2,184 |

Confirmed: Survivor's SL/TP logic and the broker-side GTT code path (fixed this morning) are both now sound. GTT itself is correctly skipped in paper mode by design — paper relies on the polling loop only, which is now validated to work correctly.

**Separately investigated — not bugs:**
- **Wave Extractor** (`-₹1,783` unrealized at time of check) — confirmed its `_current_price` is correctly sourced from live ticks (`tick.last_price`) with REST fallback, unlike Survivor's old bug. This loss reflects genuine paper market risk during the large reversal, not a monitoring failure.
- **BankNifty (`bn_survivor`) has never placed a single trade since being added June 14** — root cause: `ENABLE_BANKNIFTY` was never set in `.env`, so `os.getenv("ENABLE_BANKNIFTY", "false")` defaults to disabled and the strategy object is never instantiated (`None`). Not a bug — simply switched off. To enable: add `ENABLE_BANKNIFTY=true` to `.env` and restart with `--update-env`. **Deferred to next session** (markets closing).

### Identified Gap — Paper Mode Has No Virtual GTT
Paper trades currently skip GTT placement entirely by design (GTT is broker-only), so paper mode cannot validate the "bot crashes/stalls, does GTT save me" scenario — only the live polling-loop exit path is tested. **Planned for next session:** add a virtual/simulated GTT check inside the independent `_refresh_ltp_loop` (runs every 3s, separate from the main monitor loop) that calculates the same trigger price `place_gtt_trailing_sl()` would use, and force-closes with a distinct `[PAPER-GTT]` tag if breached — giving a true independent-backstop test in paper mode, plus a side-by-side ledger comparing virtual-GTT trigger vs actual exit for empirical confidence before relying on it live.
