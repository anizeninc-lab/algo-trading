# Rahul Sharma Trading System
### Nifty 50 Options Algo Trading Bot — v2.0

> Automated intraday options selling bot deployed on Oracle Cloud Ubuntu server.
> Built in Python with FastAPI dashboard, PM2 process management, and Upstox broker API.

---

## System Overview

| Item | Value |
|------|-------|
| Server | Oracle Cloud Ubuntu 22.04 — 92.4.90.188 |
| Dashboard | http://92.4.90.188:8081 |
| Code editor | http://92.4.90.188:8082 |
| Process manager | PM2 |
| Broker | Upstox (NFO intraday) |
| Language | Python 3.10 + React frontend |
| Database | SQLite (trade_log.db) |

---

## Daily Checklist

### Before 9:00 AM IST — Token Refresh

**Step 1** — Open Git Bash on Windows and run:
```bash
ssh -i ~/.ssh/oci_trading -L 8080:127.0.0.1:8080 ubuntu@92.4.90.188
```

**Step 2** — On the server, stop nginx:
```bash
sudo systemctl stop nginx
```

**Step 3** — Open this URL in your Windows browser and login with Upstox:
https://api.upstox.com/v2/login/authorization/dialog?client_id=986662e3-727b-4343-8f0b-40eb8b1e5e0f&redirect_uri=http://127.0.0.1:8080/callback&response_type=code

**Step 4** — Copy the code from the browser address bar (e.g. `code=ABC123`), then run on server (replace CODE):
```bash
python3 -c "
import requests
r = requests.post('https://api.upstox.com/v2/login/authorization/token', data={
    'code': 'CODE',
    'client_id': '986662e3-727b-4343-8f0b-40eb8b1e5e0f',
    'client_secret': '42kpbwm3n6',
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
