# Trading Bot — Session Notes & Procedures

---

## 🔁 Daily Morning Startup Checklist

1. Run `get_token.py` locally on Windows → copies token to `.env`
2. `sed` strip quotes + `scp` `.env` to server + `pm2 restart trading-bot --update-env`
3. Start tg-commander if stopped: `pm2 start tg-commander`
4. Check logs: `pm2 logs trading-bot --lines 40 --nostream | tail -20`
5. Verify `PAPER_TRADE=true`: `grep PAPER_TRADE .env`
6. Check `configs/risk_state.json` — ensure `system_halted: false`
7. Send `/status` in Telegram to confirm all strategies running

---

## ⚠️ Weekly Loss Override Procedure

**When needed:** Weekly live P&L exceeds ₹-10,000 and bot halts with:
`Weekly drawdown limit hit: ₹-XXXXX (limit: ₹-10000.00, last 7 calendar days)`

### Turn ON (morning, paper mode only):
```bash
# Add/enable override
sed -i 's/WEEKLY_LOSS_OVERRIDE=0/WEEKLY_LOSS_OVERRIDE=1/' .env
# If line missing entirely:
echo "WEEKLY_LOSS_OVERRIDE=1" >> .env

# Clear the halt
python3 -c "
import json
with open('configs/risk_state.json') as f: s = json.load(f)
s['system_halted'] = False
s['halt_reason'] = ''
with open('configs/risk_state.json', 'w') as f: json.dump(s, f, indent=2)
print('halted:', s['system_halted'])
"
pm2 restart trading-bot --update-env
```

### Turn OFF (EOD, after 3:10 PM):
```bash
sed -i 's/WEEKLY_LOSS_OVERRIDE=1/WEEKLY_LOSS_OVERRIDE=0/' .env
pm2 restart trading-bot --update-env
```

**Note:** The weekly drawdown query filters `paper_trade=0` so only live trades count.
Override is safe to use in paper mode. Never leave ON overnight.

---

## 🔧 Common Startup Errors & Fixes

### `AttributeError: object has no attribute '_is_paper'`
Any strategy file has self-referential `self._is_paper = self._is_paper`.
Fix: `sed -i 's/self._is_paper = self._is_paper/self._is_paper = os.getenv("PAPER_TRADE", "false").lower() == "true"/' strategy/<file>.py`

### `RiskManager State restored | halted=True`
Clear risk state JSON:
```bash
python3 -c "
import json
with open('configs/risk_state.json') as f: s = json.load(f)
s['system_halted'] = False; s['halt_reason'] = ''
with open('configs/risk_state.json', 'w') as f: json.dump(s, f, indent=2)
"
pm2 restart trading-bot --update-env
```

### `PAPER_TRADE not loading correctly`
Ensure `main.py` uses `load_dotenv(override=True)` — without override, PM2 cached env wins.

---

## 📋 Known Backlog Items

| Item | Detail |
|------|--------|
| BankNifty monthly expiry | SEBI removed BankNifty weeklies Nov 2024. bn_survivor needs to use monthly expiry (last Wednesday of month). `ENABLE_BANKNIFTY=false` for now. |
| Parity check paper skip | Fixed Jul 3 — skips when `_is_paper=True` |
| paper_trade=0 bug | Old trades logged as live due to missing `load_dotenv(override=True)`. Fixed Jul 3 — new trades will log correctly. Historical trades unaffected. |
| Weekly drawdown investigation | ₹-11,146 live losses accumulated before paper fix. Review DB to identify source. |
| Backtest harness | Large standalone project — blocked |

---

## 📊 Risk Parameters (Current)

| Parameter | Value |
|-----------|-------|
| Max daily loss | -₹3,000 |
| Per trade SL | Dynamic: entry × 1.5× qty (floor -₹800, cap -₹2,500) |
| Max weekly loss (live only) | -₹10,000 |
| Max trades/day | 3 |
| Auto-stop time | 3:10 PM IST |
| Max capital deployed | ₹1,50,000 |
| Nifty lot size | 65 |
| BankNifty lot size | 15 (disabled) |
| Parity check interval | 15 min (skipped in paper mode) |
| API circuit breaker | 5 failures/60s → halt, auto-reset 300s |

---

## 🔗 Key Commands

```bash
# Check bot status
pm2 list
pm2 logs trading-bot --lines 50 --nostream | tail -20

# Clear halt
python3 -c "import json; f=open('configs/risk_state.json'); s=json.load(f); f.close(); s['system_halted']=False; s['halt_reason']=''; open('configs/risk_state.json','w').write(json.dumps(s,indent=2))"

# Verify env vars in running process
cat /proc/$(pm2 pid trading-bot)/environ | tr '\0' '\n' | grep -E "PAPER|WEEKLY|BANKNIFTY"

# Run tests
cd ~/trading-algo && python3 -m pytest tests/ -v
```
