# Nifty GEX A+ Setup — Strategy Specification (NSE Adaptation)

*Adapted from the Ninja GEX / Gamma Exposure A+ Setup method (@nickelninjaGEX). This document defines exactly what will be built as `strategy/nifty_gex.py`, and is written to be checked against, not just read once.*

---

## Fidelity check: are we using the exact same method?

**Short answer: the decision framework is identical. The GEX data source is necessarily different, because true dealer GEX doesn't exist in India.**

| Component | Original method | This implementation | Same or approximated? |
|---|---|---|---|
| Core philosophy (A+ only, skip rest) | Skip unless all components align | Identical — hard gate, no partial credit | **Same** |
| EMA stack (9/21/50) direction filter | TradingView 5-min/15-min | Computed from tick/candle data we already fetch, same periods, same timeframes | **Same** |
| GEX levels | Real dealer Gamma Exposure by strike, sourced from options market data providers with actual dealer positioning | **No US-style dealer GEX exists for NSE.** Approximated via OI-based synthetic GEX (see below) | **Approximated — necessarily, not by choice** |
| Gamma regime (net positive/negative) | Derived from real dealer GEX sign | Derived from the same OI-based proxy, same interpretation (positive = mean-reverting, negative = trending) | **Approximated, same interpretation logic** |
| Volume confirmation | Real-time volume on breakout candle | NSE/Upstox tick volume, same rule | **Same** |
| Entry structure (flag/break-retest at GEX level) | Price action rule, source-agnostic | Identical rule, applied to our approximated levels instead of real dealer levels | **Same rule, different input data** |
| Stop loss / targets / R:R ≥ 1:2 | Structural, GEX-level-based | Identical | **Same** |
| Regime-based sizing adjustments | Positive vs negative gamma | Identical logic | **Same** |
| Time stop | Fixed candle count / time-of-day | Identical, tuned to 9:15–15:30 IST session | **Same** |
| Risk limits (1-2 trades/day, daily loss cap) | Generic | Wired into your existing `risk_manager` (same `can_trade`/`check_max_daily_loss` gates your other strategies use) | **Same intent, existing infrastructure** |

**The one component that cannot be "the same" under any implementation:** true GEX requires knowing what market makers actually hold, which nobody publishes for NSE. Every GEX-for-India tool (including this one) is running an approximation. This isn't a shortcut we're taking — it's the ceiling of what's possible with public Indian market data.

---

## 1. Data & Tools

**GEX approximation — OI-based synthetic Gamma Exposure:**

For each strike, per expiry:GEX(strike) = (OI_call × Γ_call − OI_put × Γ_put) × spot² × 0.01 × lot_size
Where Γ (gamma) is computed via Black-Scholes using: strike, spot, time-to-expiry, IV, risk-free rate. This is the same formula methodology SpotGamma-style tools use in the US — the only difference is *their* sign/positioning assumption is confirmed by real dealer data; ours assumes the standard convention (market makers net short calls, net long puts against retail/institutional flow), which is the industry-standard assumption used whenever real dealer data isn't available, not something unique to this build.

**Data already available in your pipeline** (confirmed from `core/market_context.py`, seen in today's logs — `OI update — CE/PE/PCR/ATM/MaxPain`):
- OI per strike, per expiry (CE + PE)
- Spot price
- ATM strike, Max Pain

**Needs to be added:**
- IV per strike (from options chain LTP via Black-Scholes inversion, or directly from Upstox's option chain response if it includes IV — needs checking)
- Gamma calculation module (Black-Scholes Γ)
- EMA(9/21/50) on 5-min and 15-min candles — **not currently computed anywhere in the bot**; needs a new candle-aggregation + EMA module, since the bot currently only tracks tick-level LTP and OI, not resampled candles

**Charting:** N/A — this runs headless, no TradingView dependency. EMA stack becomes a pure numerical gate, not a visual one.

---

## 2. Pre-market preparation

Runs once at session start (same pattern as your existing `session_planner`):
- Fetch full option chain (current week + next week expiry)
- Compute GEX per strike for both expiries
- Identify top 3-5 positive and negative GEX strikes
- Classify regime: net GEX sign across all strikes = positive (mean-reverting) or negative (trending)
- Compute "clean air" zones: strike ranges with no significant GEX level in between
- Pull previous day H/L (already available — `core/regime_engine.py` already tracks `pdh/pdl`)

## 3. Direction filter (EMA stack)

- Bullish: 9 EMA > 21 EMA > 50 EMA, meaningful spacing (not clustered) → CE only
- Bearish: 50 > 21 > 9 → PE only
- Clustered/mixed → no trade
- Requires 15-min AND 5-min agreement before allowing entry

## 4. Entry rules (A+ only — every condition below must be true)

- [ ] EMA stack aligned (bullish or bearish, not mixed)
- [ ] Price at/approaching a top-3-5 GEX strike, correct side for direction
- [ ] Clean structure: flag, tight consolidation, or break-and-retest at that level
- [ ] Volume confirmation on the trigger candle
- [ ] Within first 1-2 hours of session, or other confirmed high-liquidity window (not the 12:00-13:00 lull unless exceptional)
- [ ] Option selected: near-ATM or slight OTM, current or next week depending on time-of-day, adequate liquidity (bid-ask spread check), high relative gamma

**Any single unchecked box = skip. No partial setups.**

## 5. Trade management

- SL: structural (below flag low / key EMA), sized to risk 1-2% of account equity
- Target: next major GEX level in trade direction; scale 50% at target 1, trail or exit remainder at target 2
- Minimum 1:2 R:R required at entry — if the next GEX level doesn't offer it, skip
- Positive gamma regime: tighter targets, faster profit-taking, smaller size
- Negative gamma regime: wider stops (if structure supports), larger targets, reduced size
- Time stop: exit if no meaningful progress within N candles (suggest 6 candles on 5-min = 30 min) or hard cutoff before 14:45 IST unless momentum is strong

## 6. Risk & psychology rules

- Max 1-2 trades/day — enforced via `risk_manager.max_trades_per_day`, same mechanism as your other strategies
- Hard daily loss limit 3-4% of account — enforced via `risk_manager.max_daily_loss`
- No averaging down, no widening stops — structural, enforced in code (no manual override path)
- Every signal (taken or skipped) logged with full checklist state — new `notes` field, or extends the existing `events` table

## 7. Position sizing formula
risk_amount = account_equity × risk_pct (risk_pct = 0.01 to 0.02)
qty = floor(risk_amount / (entry_premium − stop_premium)) rounded down to nearest lot_size (65)
**₹5,00,000 account, 1.5% risk, entry premium ₹150, stop premium ₹110 (₹40 risk/unit):**
risk_amount = 500,000 × 0.015 = ₹7,500
qty = floor(7,500 / 40) = 187 → rounded to 130 (2 lots of 65)
**₹20,00,000 account, same trade:**
risk_amount = 2,000,000 × 0.015 = ₹30,000
qty = floor(30,000 / 40) = 750 → rounded to 715 (11 lots of 65)
*(Actual sizing will also run through your existing `MAX_CAPITAL_DEPLOYED` and per-strategy cap guards — this formula produces the "ideal" size before those hard ceilings apply.)*

## 8. Common failure modes this checklist filters out

- **Chasing extended moves** — entry requires *at* a GEX level with structure, not mid-move
- **Countertrend trades** — EMA stack gate blocks trading against the stack
- **Low-conviction setups** — volume confirmation requirement filters weak breaks
- **Illiquid options** — explicit liquidity check in option selection
- **Death by a thousand small trades** — hard 1-2 trades/day cap
- **Revenge trading after a loss** — no override path in the risk gate, same as your existing bots

## 9. Backtesting requirements

To validate this properly (once `readable_symbol` data has accumulated per our other work today), you'd need per candle:
- OHLCV at 5-min resolution for Nifty spot/futures
- Full option chain snapshots (OI, IV, LTP) at the same resolution, both expiries
- EMA(9/21/50) computed from the same candles
- GEX computed per snapshot from the OI+IV data above

This is a heavier data requirement than the trailing-profit backtest we scoped earlier today — that only needed price paths for specific contracts already traded. This needs the *full chain* at regular intervals, which is a bigger fetch even with the Expired Historical Candle API (would need every strike's candles, not just the ones traded).

---

## Open questions before implementation

1. Does Upstox's option chain response include IV directly, or does gamma need a full Black-Scholes IV-then-gamma round trip from LTP?
2. What EMA/candle-aggregation approach fits best with your existing tick-based architecture — resample from `_ltp_cache` on a timer, or subscribe to Upstox's native candle feed if one exists?
3. Confirm the OI-based GEX sign convention (short calls/long puts) is what you want, or if you'd prefer a symmetric magnitude-only version (some practitioners skip the sign assumption entirely and just look at raw OI concentration as "levels" without claiming directional dealer bias).