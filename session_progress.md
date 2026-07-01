# Trading Bot — Session Progress Log
Last updated: 2026-07-01

---

## ✅ Completed This Session (July 1, 2026)

| # | Item | Commit | Notes |
|---|---|---|---|
| 6 | **`saviour_combo` kill-switch excludes BankNifty P&L** — `_get_combined_pnl()` and `_update_combo_status()` now include `bn_survivor` | `5427c14` | Both Patch A (P&L) and Patch B (trade counts) applied |
| 24+26 | **Global pre-trade gate** — single `is_trading_blocked()` in `risk_manager`; both strategies bail early on halt/VIX/auto-stop; blocked reason logged only on state change (kills tick-spam) | `522ac06`, `9fa5b47` | `_last_block_reason` init bug fixed in follow-up commit |
| 25 | **Dashboard operational state bar** — new `/api/bot-status` endpoint + `BotStatusBar` React component showing trading status, halt reason, capital deployed/remaining bars, daily loss % bar, trades today | `9f10c5d` | Polls every 5s |
| 4 | **Hedge legging risk** — `_open_hedge_leg()` now returns `True/False`; live-mode hedge failure triggers auto-close of naked short with escalating Telegram alerts | `9f10c5d` | Paper mode: logs warning and continues |
| 13 | **`auto_start_survivor` flag logic inverted** — `True` now correctly means start immediately | `507a316` | Config already had `true` so behavior preserved |
| 21 | **Cron watchdog no market-hours awareness** — now skips restarts outside 09:00–15:35 IST and on weekends | `a1fbf90` | — |
| 10 | **Dashboard killswitch called private method** — added public `close_all_positions()` wrapper to `survivor.py`; dashboard updated to use it | `a4c60b6` | Same fix applied to `bn_survivor` |

---

## 🔲 Remaining Backlog

### 🔴 High Priority
| # | Item |
|---|---|
| 14 | Wave Extractor fill logic duplicated — live vs paper paths diverged, fix in one doesn't land in other |

### 🟡 Medium Priority
| # | Item |
|---|---|
| 7 | Fixed ₹800 SL not volatility/premium-normalized |
| 8 | No portfolio-level Greeks aggregation |
| 9 | No multi-day drawdown circuit breaker |
| 11 | State fragmented across 5+ persistence mechanisms (SQLite + 4 JSON files + in-memory) |

### 🟢 Low Priority
| # | Item |
|---|---|
| 16 | Config default drift: `min_price_to_sell` dataclass default (30) vs `main.py` (15) |
| 17 | No sensitivity testing on magic-number thresholds (depends on #2 backtest) |
| 18 | Possible duplicate regime logic between `regime_engine` and `market_context` — unverified |
| 19 | `base_strategy.py` never directly reviewed |
| 20 | Stray `trades.db` in repo root, never referenced |
| 22 | Wave Extractor paper fills have no slippage model |
| 23 | Magic number `lot_size = 25` in `wave_extractor._sync_positions()` |
| 27 | Signal logs lack rationale/confidence |

### ⏸ Deferred
| # | Item |
|---|---|
| 2 | Backtest harness — deferred by Prince |

---

## Bot State
- Mode: **Paper trading** (all strategies)
- Server: `92.4.90.188` — PM2 process `trading-bot`
- Dashboard: `http://92.4.90.188:8081`
- Last restart: 2026-07-01 ~08:47 IST
