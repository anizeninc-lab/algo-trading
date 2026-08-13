# Rahul Sharma Trading System
### Nifty 50 Options Algo Trading Bot — v2.0

> Automated intraday options selling bot deployed on Oracle Cloud Ubuntu server.  
> Built in Python with FastAPI dashboard, PM2 process management, and Upstox broker API.

---

## System Overview

An algorithmic trading system that sells Nifty 50 weekly options (CE/PE) using a range-based strategy with multi-layer risk management. A BankNifty paper trading instance runs alongside in parallel for observation.

**Server:** Oracle Cloud Ubuntu 22.04 — `92.4.90.188`  
**Dashboard:** `http://92.4.90.188:8081`  
**Code editor:** `http://92.4.90.188:8082` (VS Code Server)  
**Process manager:** PM2 — `pm2 status`  
**Broker:** Upstox (NFO segment, intraday only)

---

## Architecture

```
main.py
├── SaviourCombo (orchestrator)
│   ├── WaveExtractor       — trend-based scalping (currently BLOCKED in range)
│   ├── SurvivorAlgo        — range-based options selling (ACTIVE)
│   └── BankNifty Survivor  — paper mode parallel instance
│
├── core/
│   ├── session_planner.py  — market open analysis, regime/confidence
│   ├── market_context.py   — live OI, PCR, opening range, regime (2min refresh)
│   ├── risk_manager.py     — all risk gates (SL, TP, daily loss, capital)
│   ├── vix_manager.py      — VIX fetch, safety override on extreme VIX
│   ├── strategy_filter.py  — PCR + regime filter for trade entry
│   ├── state_store.py      — strategy state tracking
│   └── trade_log.py        — SQLite trade journal
│
├── brokers/
│   └── upstox.py           — Upstox REST + WebSocket adapter, GTT support
│
└── dashboard/
    ├── api.py              — FastAPI endpoints
    └── frontend/           — React dashboard
```

---

## Daily Operational Checklist

### Before 9:00 AM IST

```bash
# 1. SSH into server
ssh -i ~/.ssh/oci_trading -L 8080:127.0.0.1:8080 ubuntu@92.4.90.188

# 2. Stop nginx
sudo systemctl stop nginx

# 3. Open in browser and login with Upstox
https://api.upstox.com/v2/login/authorization/dialog?client_id=986662e3-727b-4343-8f0b-40eb8b1e5e0f&redirect_uri=http://127.0.0.1:8080/callback&response_type=code

# 4. Save token (replace CODE with code from browser URL)
python3 -c "
import requests
r = requests.post('https://api.upstox.com/v2/login/authorization/token', data={
    'code': 'CODE',
    'client_id': '986662e3-727b-4343-8f0b-40eb8b1e5e0f',
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

# 5. Start nginx and restart bot
sudo systemctl start nginx
pm2 restart trading-bot --update-env
```

### During Market Hours (9:15 AM – 3:05 PM IST)

```bash
# Watch live logs
pm2 logs trading-bot --lines 20

# Check positions
pm2 logs trading-bot --lines 5 | grep -E "SOLD|CLOSED|GTT|BREAKEVEN|SL|PROFIT"

# Check open trades in DB
sqlite3 /home/ubuntu/trading-algo/trade_log.db \
"SELECT substr(id,1,8), symbol, entry_price, status FROM trades WHERE status='OPEN';"
```

### After 3:30 PM IST

```bash
# Bot auto-stops at 3:05 PM — verify clean state
sqlite3 /home/ubuntu/trading-algo/trade_log.db \
"SELECT substr(id,1,8), entry_price, exit_price, realised_pnl, status FROM trades WHERE date(entry_time)=date('now','localtime') ORDER BY entry_time;"

# Commit and push changes
cd /home/ubuntu/trading-algo && git add -A && git commit -m "session: $(date +%Y-%m-%d)" && git push mygithub main
```

---

## Strategy: Survivor (Options Selling)

**Logic:** Sells OTM options when Nifty moves a defined number of points from anchor.

| Parameter | Value | Notes |
|-----------|-------|-------|
| pe_gap | 6 pts (VIX < 18) | Nifty must rise this much to sell PE |
| ce_gap | 6 pts (VIX < 18) | Nifty must fall this much to sell CE |
| pe_symbol_gap | 350 pts OTM | Strike distance from current price |
| ce_symbol_gap | 350 pts OTM | Strike distance from current price |
| min_premium | ₹15 | Won't sell if option is cheaper |
| lot_size | 65 | Nifty lot size |
| max_open_trades | 2 | Maximum simultaneous positions |
| max_trades_per_day | 3 | Daily limit |
| expiry | Tuesday weekly | Auto-selected by auto_config |

**Time-based trigger:** Also fires at 9:45–11:30 AM if PCR ≥ 1.2 (PE) or ≤ 0.8 (CE) on flat days.

---

## Risk Management — 4 Layers

### Layer 1: Trade Entry
| Gate | Value |
|------|-------|
| Regime filter | Range only — blocks in trending_bull / trending_bear |
| PCR filter | ≥ 1.2 for PE sells, ≤ 0.8 for CE sells (time-based trigger) |
| Min premium | ₹15 |
| Strike distance | 350 pts OTM |
| Max open trades | 2 |
| Trading hours | 9:30 AM – 3:05 PM IST only |

### Layer 2: Trade Exit (per trade)
| Mechanism | Trigger | Action |
|-----------|---------|--------|
| Stop loss | Loss ≥ ₹800 | Close immediately |
| Breakeven lock | Profit ≥ ₹400 | Move SL to entry — trade cannot turn into loss |
| Profit target | Profit ≥ ₹600 | Close and lock profit |
| GTT trailing SL | 15% above entry, trails ₹0.25 | Upstox-managed, fires automatically |

### Layer 3: Session
| Mechanism | Value |
|-----------|-------|
| EOD auto-exit | 3:05 PM IST — all positions closed |
| Daily loss limit | −₹3,000 — bot halts for the day |
| Safe restart | Restart before 3:05 PM skips position close |

### Layer 4: Capital
| Rule | Value |
|------|-------|
| Max capital deployed | ₹1,50,000 (hardcoded, cannot be overridden) |
| Margin per SELL lot | ₹40,000 (conservative estimate) |
| BankNifty | Paper mode only — zero real capital risk |

---

## BankNifty Paper Mode

Running alongside live Nifty trading for observation. Always `paper_trade_override=True`.

| Parameter | Value |
|-----------|-------|
| Index key | `NSE_INDEX\|Nifty Bank` |
| Expiry | Wednesday weekly |
| Lot size | 15 |
| Strike interval | 100 pts |
| Symbol gap | 500 pts OTM |
| Min premium | ₹50 |
| pe_gap / ce_gap | 20 pts |

Promote to live only after 2–3 weeks of consistent paper profits.

---

## Performance Summary (June 2026)

| Date | Trades | Gross P&L | Notes |
|------|--------|-----------|-------|
| Jun 5 | 3 | +₹423 | First real trades — paper |
| Jun 8 | 3 | +₹900 (est) | PE sell loss on trending bear |
| Jun 10 | 4 | −₹3,103 | Multiple SL failures — fixed |
| Jun 12 | 6+ | +₹626 (est) | First day with all fixes live |

---

## Known Issues & P1 Fixes (Pending)

| Priority | Issue | Impact |
|----------|-------|--------|
| P1 | No idempotent order keys | Duplicate trades on restart |
| P1 | Stale trade count after restart | Max-trades counter resets |
| P1 | No broker position reconciliation at startup | Bot state vs Upstox mismatch |
| P1 | Daily loss not persisted across restart | Circuit breaker resets |
| P1 | Exit precedence — SL/TP/GTT/EOD can all fire simultaneously | Double-close risk |
| P1 | Max-open based on memory not broker positions | Wrong count after crash |
| P2 | No order timeout handling | Stale orders not cancelled |
| P2 | Partial fill handling missing | GTT/SL qty may not match fill |
| P2 | No critical failure alerting | Silent failures |
| P3 | No trade journal / post-trade report | No audit trail |

---

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point, strategy orchestration |
| `strategy/survivor.py` | Core options selling logic |
| `strategy/saviour_combo.py` | Strategy orchestrator |
| `core/risk_manager.py` | All risk gates and limits |
| `core/session_planner.py` | Market open analysis, dynamic params |
| `core/market_context.py` | Live OI, PCR, regime classification |
| `core/auto_config.py` | Auto-selects weekly expiry symbols |
| `auto_rollover.py` | Daily symbol rollover on startup |
| `brokers/upstox.py` | Upstox REST + WebSocket + GTT |
| `trade_log.db` | SQLite trade journal (excluded from git) |
| `configs/saviour_combo.json` | Runtime config (auto-updated by rollover) |
| `.env` | Credentials and feature flags (excluded from git) |

---

## Environment Variables (.env)

```bash
BROKER_NAME=upstox
UPSTOX_ACCESS_TOKEN=eyJ...    # Refresh daily before 9 AM
PAPER_TRADE=false              # true = paper mode, false = live
MAX_COMBINED_LOSS=-5000
```

---

## Useful Commands

```bash
# Bot status
pm2 status
pm2 logs trading-bot --lines 20

# Force stop all positions
pm2 stop trading-bot

# Check today's trades
sqlite3 trade_log.db "SELECT substr(id,1,8), symbol, entry_price, exit_price, realised_pnl, status FROM trades WHERE date(entry_time)=date('now','localtime');"

# Check total P&L
sqlite3 trade_log.db "SELECT round(sum(realised_pnl),2) FROM trades WHERE status='CLOSED';"

# Manual close stuck open trade
sqlite3 trade_log.db "UPDATE trades SET status='CLOSED', exit_price=XX.XX, exit_time=datetime('now'), notes='Manual reconcile' WHERE id='FULL-UUID-HERE';"

# Rebuild dashboard frontend
cd dashboard/frontend && npm run build

# Git backup
git add -A && git commit -m "backup: $(date +%Y-%m-%d)" && git push mygithub main
```

---

## Regime Classification

| Regime | Condition | Survivor | Wave |
|--------|-----------|----------|------|
| `range` | PCR neutral, low OI delta | ACTIVE | BLOCKED |
| `trending_bull` | PCR < 0.8, strong CE OI buildup | BLOCKED | ACTIVE |
| `trending_bear` | PCR > 1.3, strong PE OI buildup | BLOCKED | ACTIVE |
| `reversal_watch` | Sharp PCR shift | ACTIVE (careful) | BLOCKED |
| `opening` | Before 9:30 AM | BLOCKED | BLOCKED |
| `closed` | After 3:10 PM | BLOCKED | BLOCKED |

---

## Incident Log

| Date | Incident | Fix Applied |
|------|----------|-------------|
| May 20 | First live day — 6 critical bugs | Orders after stop, qty hardcap bypass, fill detection |
| Jun 5 | No trades firing at all | Hardcoded Thursday expiry, invalid instrument key |
| Jun 10 | SL failures — manual exits needed | REST fallback LTP, breakeven lock |
| Jun 11 | Premature EOD on restart | Safe `on_stop` — skips close if not 3:05 PM |
| Jun 12 | Duplicate trades at 9:30 open | Identified — P1 fix pending (idempotent orders) |
| Jun 12 | Stale "Max open trades" after EOD | DB reconcile — manual fix applied |

---

*Last updated: June 12, 2026*  
*Maintainer: DEV (Prince)*