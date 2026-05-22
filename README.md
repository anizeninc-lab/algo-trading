# ⬡ Nifty Options Trading Bot v4

A modular, production-ready algorithmic trading bot for **Nifty 50 index options** on Indian markets.
Uses **Upstox API** for live market data and order execution, running 24/7 on an Oracle Cloud server.

> **Live since: 13 May 2026 — Total PnL Rs.3,990+ | First Live Day: 20 May 2026**
> **v4 upgrade: 22 May 2026 — 3-Layer Architecture with Market Context Engine**

---

## ARCHITECTURE (v4)

```
LAYER 1 → MARKET CONTEXT ENGINE    (core/market_context.py)
           - Opening Range 9:15–9:30 AM
           - PCR + OI monitor every 2 min
           - Regime classifier

LAYER 2 → STRATEGY FILTER ENGINE   (core/strategy_filter.py)
           - Gates entries by regime
           - Blocks on PCR extremes / spikes
           - Astro calendar integration

LAYER 3 → EXECUTION ENGINE          (strategy/*.py)
           - Survivor, Saviour Combo, Wave Extractor
           - Only fires when Layer 1+2 approve
```

---

## SYSTEM OVERVIEW

| Component | Details |
|---|---|
| Broker | Upstox |
| Server | Oracle Cloud Ubuntu 22.04 — 92.4.90.188 |
| Dashboard | http://92.4.90.188:8081 |
| VS Code Server | http://92.4.90.188:8080 |
| Process Manager | PM2 |
| Strategies | Survivor, Saviour Combo, Wave Extractor |

---

## MORNING STARTUP (EVERY TRADING DAY)

### IMPORTANT — Do these BEFORE 9:15 AM

---

### Step 1 — Open Git Bash on your PC
```
cd "/c/Users/Prince/Desktop/trading-algo-backup-20260504"
```

---

### Step 2 — Get fresh Upstox token
```
python get_token.py
```
- Browser opens Upstox login automatically
- Log in with your credentials
- Wait for: **Access token saved to .env successfully**

---

### Step 3 — Upload token and restart bot
```
sed "s/UPSTOX_ACCESS_TOKEN='\(.*\)'/UPSTOX_ACCESS_TOKEN=\1/" .env > .env.clean && scp -i ~/.ssh/oci_trading .env.clean ubuntu@92.4.90.188:/home/ubuntu/trading-algo/.env && ssh -i ~/.ssh/oci_trading ubuntu@92.4.90.188 "pm2 restart trading-bot --update-env"
```

---

### Step 4 — Verify clean startup
```
ssh -i ~/.ssh/oci_trading ubuntu@92.4.90.188 "pm2 list"
```
✅ Status should be: **online**
✅ Restart count should NOT be climbing

---

### Step 5 — Check dashboard
Open: **http://92.4.90.188:8081**

Check all of these:
- ✅ BROKER ON — green
- ✅ All 3 strategies — RUNNING
- ✅ 0 open positions
- ✅ Capital used less than 20%
- ✅ PAPER: ON or OFF depending on your mode

---

### Step 6 — Watch opening range (9:15–9:30 AM)
- Context bar shows: **⏳ OPENING RANGE — COLLECTING...**
- No trades fire during this window — this is correct
- At 9:30 AM the range locks and regime appears

---

### Step 7 — Confirm regime at 9:30 AM
- **↔ RANGE** → Survivor will trade, Wave Extractor blocked
- **▲ TREND BULL** → Wave Extractor active, Survivor may trade
- **▼ TREND BEAR** → Wave Extractor active, Survivor may trade
- **⚡ REVERSAL** → All entries paused — wait for clarity

---

## GOING LIVE CHECKLIST

Before switching to live mode verify ALL:
- [ ] Paper mode ran cleanly with no errors
- [ ] Dashboard shows correct P&L updating live
- [ ] No Restored stale trades on startup
- [ ] Upstox balance > Rs.3,50,000
- [ ] Fresh token uploaded today

**Switch to live (VS Code terminal on server):**
```
sed -i 's/PAPER_TRADE=true/PAPER_TRADE=false/' /home/ubuntu/trading-algo/.env
pm2 restart trading-bot --update-env
```

**Verify:**
```
grep PAPER_TRADE /home/ubuntu/trading-algo/.env
```
Should show: PAPER_TRADE=false

⚠️ WATCH DASHBOARD FOR FIRST 30 MINUTES AFTER GOING LIVE

---

## RISK LIMITS (HARDCODED)

| Limit | Value |
|---|---|
| Max capital deployed | Rs.1,50,000 |
| Max daily loss | Rs.3,000 |
| Per-trade stop loss | Rs.1,500 |
| Auto-stop time | 3:10 PM IST |
| Max trades per day | 3 per strategy |

---

## STRATEGY REGIME RULES

| Strategy | Allowed Regimes | Blocked When |
|---|---|---|
| Survivor | Range | Trending, Reversal |
| Wave Extractor | Trending Bull, Trending Bear | Range, Reversal |
| Saviour Combo | Range, Trending | Reversal watch |
| ALL strategies | — | PCR > 1.3 or < 0.7 |
| ALL strategies | — | PCR spike (±0.3 in 2 min) |
| ALL strategies | — | Before 9:30 AM |
| ALL strategies | — | After 3:10 PM |

---

## ASTRO CALENDAR

Hardcoded trading strength calendar (activates 21 Jul 2026).

| Strength | Bot Behaviour |
|---|---|
| Excellent / Very Strong / Strong | Normal trading |
| Moderate+ / Recovery | Reduced qty (50%) |
| High Risk / Risky | All entries paused |

Visible on dashboard context bar under ASTRO TODAY.

---

## ESSENTIAL COMMANDS

### Bot Control
```bash
# START
pm2 restart trading-bot --update-env

# STOP
pm2 stop trading-bot

# STATUS
pm2 list

# LIVE LOGS
pm2 logs trading-bot --lines 50
```

### Switch Modes (VS Code terminal)
```bash
# Go LIVE
sed -i 's/PAPER_TRADE=true/PAPER_TRADE=false/' /home/ubuntu/trading-algo/.env && pm2 restart trading-bot --update-env

# Go PAPER
sed -i 's/PAPER_TRADE=false/PAPER_TRADE=true/' /home/ubuntu/trading-algo/.env && pm2 restart trading-bot --update-env
```

### Today's Trades
```bash
python3 -c "
import sqlite3
from datetime import datetime
import pytz
ist = pytz.timezone('Asia/Kolkata')
today = datetime.now(ist).strftime('%Y-%m-%d')
conn = sqlite3.connect('/home/ubuntu/trading-algo/trade_log.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT strategy, symbol, order_type, quantity, entry_price, realised_pnl, status FROM trades WHERE DATE(entry_time)=?\", (today,)).fetchall()
print(f'Trades on {today}: {len(rows)}')
total = 0
for r in rows:
    pnl = r['realised_pnl'] or 0
    total += pnl
    print(f'{r[\"strategy\"]:15} {r[\"order_type\"]:5} qty:{r[\"quantity\"]} entry:{r[\"entry_price\"]} pnl:{pnl:.2f} [{r[\"status\"]}]')
print(f'Total PnL: Rs.{total:.2f}')
"
```

### Clear Stale Trades (if capital stuck at 100%)
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/ubuntu/trading-algo/trade_log.db')
r = conn.execute(\"UPDATE trades SET status='CLOSED' WHERE status='OPEN'\")
conn.commit()
print('Closed', r.rowcount, 'stale trades')
"
```

---

## EMERGENCY PROCEDURES

### Kill all trades immediately
```bash
pm2 stop trading-bot
```
OR open http://92.4.90.188:8081 and click KILL ALL

### Bot crash-looping (restart count climbing)
```bash
# Check logs for error
pm2 logs trading-bot --lines 30 --nostream

# Most common cause: expired token
# Fix: get fresh token (Step 2 above) then upload and restart
```

### Dashboard not loading
```bash
# Check bot is running
pm2 list

# Check health
curl http://localhost:8081/api/health
```

---

## DAILY BACKUP (run from PC Git Bash after each session)

```bash
cd "/c/Users/Prince/Desktop/trading-algo-backup-20260504"

scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/strategy/survivor.py strategy/survivor.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/strategy/wave_extractor.py strategy/wave_extractor.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/strategy/saviour_combo.py strategy/saviour_combo.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/main.py main.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/core/market_context.py core/market_context.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/core/strategy_filter.py core/strategy_filter.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/core/astro_calendar.py core/astro_calendar.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/dashboard/api.py dashboard/api.py
scp -i ~/.ssh/oci_trading ubuntu@92.4.90.188:/home/ubuntu/trading-algo/dashboard/frontend/src/App.jsx dashboard/frontend/src/App.jsx

git add -A
git commit -m "backup $(date '+%Y-%m-%d %H:%M')"
git push mygithub main

cp -r "/c/Users/Prince/Desktop/trading-algo-backup-20260504" "/i/trading-algo-backup-$(date '+%Y%m%d')"
echo "Backup complete"
```

---

## SERVER ACCESS

| Method | How |
|---|---|
| Dashboard | http://92.4.90.188:8081 |
| VS Code Server | http://92.4.90.188:8080 |
| SSH (Git Bash) | ssh -i ~/.ssh/oci_trading ubuntu@92.4.90.188 |

**Rule: Use VS Code terminal for server commands. Use Git Bash for token, scp, git push.**

---

## PERFORMANCE

| Date | Trades | PnL | Mode |
|---|---|---|---|
| 13 May 2026 | 4 | +Rs.418.60 | Paper |
| 14 May 2026 | 4 | +Rs.410.97 | Paper |
| 15 May 2026 | 4 | +Rs.410.97 | Paper |
| 18 May 2026 | 4 | +Rs.403.34 | Paper |
| 19 May 2026 | 6 | +Rs.624.34 | Paper |
| 20 May 2026 | 4 | +Rs.1722.50 | LIVE |
| 22 May 2026 | 0 | Rs.0 | LIVE (flat day) |
| **Total** | **26** | **+Rs.3990.38** | |

---

## WEEKEND TODO (before Monday 25 May)

- [ ] Fix balance display in dashboard (funds fetch)
- [ ] Wire strategy_filter.can_trade() into survivor.py and wave_extractor.py
- [ ] Add market_context.start() to main.py
- [ ] Test full startup sequence before 9:15 AM Monday

---

Built with Python, FastAPI, React, Upstox API
Running on Oracle Cloud 24/7
First live trade: 20 May 2026 — +Rs.1722.50
v4 architecture: 22 May 2026
