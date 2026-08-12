# strategy/nifty_gex.py
#
# BaseStrategy subclass for the Nifty GEX A+ Setup, per
# NIFTY_GEX_STRATEGY_SPEC.md. Wires together core/gex_calculator.py,
# core/gex_ema_stack.py, core/gex_entry_rules.py, and
# core/gex_trade_management.py.
#
# ARCHITECTURE, IN PLAIN TERMS:
#   - GEX/EMA/entry-checklist evaluation is a real REST round-trip (Option
#     Greeks API for the whole chain), so it is throttled to once per
#     gex_poll_interval seconds -- NOT run on every tick, unlike
#     wave_extractor's pure-tick-driven logic.
#   - Stops and targets from gex_trade_management.build_trade_plan() are in
#     INDEX POINTS (spot price), not option premium -- the GEX levels are
#     strikes. Exit monitoring therefore compares live NIFTY SPOT against
#     plan.stop_price / plan.target1_price, not the option's own price.
#     The option position is a leveraged proxy for the index move; P&L is
#     still realised in option premium terms at actual entry/exit fills.
#   - Entry order is a LIMIT BUY at the option's current last_price (per
#     session decision, Aug 12) -- safer/may-not-fill, not a market order.
#
# ASSUMPTIONS FLAGGED, NOT SILENTLY DECIDED (first draft -- review after
# first paper-mode run, per handoff's "watched closely" instruction):
#   - Strike chain is resolved ONCE at on_start() for the day's nearest
#     Tuesday expiry. If spot moves far enough that the original ATM+/-10
#     range no longer covers the GEX-flagged target strike, that entry is
#     skipped (target_strike not in self._chain_keys) rather than
#     re-resolving mid-day. Re-resolution on a wide spot move is a
#     reasonable future improvement, not implemented here.
#   - candles_elapsed for check_time_stop() is measured as the growth in
#     market_context.session_candles_1min length since entry, divided by 5
#     (spec's time-stop is specified in 5-min candles). This is a proxy,
#     not a true 5-min-candle counter -- acceptable since check_time_stop's
#     hard cutoff (14:45 IST) is the primary safety net regardless.
#   - Only ONE active trade at a time (spec's A+ setup is a selective,
#     infrequent setup, not a scaling strategy like wave_extractor) --
#     mirrors gex_trade_management's "no partial trades" philosophy.
#   - Exit order is also a LIMIT at current option last_price (not
#     adverse-slippage-adjusted like wave_extractor's EXIT_SLIPPAGE_PCT) --
#     first-draft simplification, flagged for revisit once real fills are
#     observed in paper mode.

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

import pytz
import upstox_client

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.auto_config import fetch_instruments, get_nearest_tuesday
from core.gex_calculator import (
    resolve_strike_chain, fetch_chain_greeks_and_oi,
    compute_signed_gex, classify_regime,
)
from core.gex_ema_stack import get_multi_timeframe_stack, seed_historical_15min_candles
from core.gex_entry_rules import evaluate_entry
from core.gex_trade_management import build_trade_plan, check_time_stop
from core.market_context import market_context
from core.risk_manager import risk_manager
from core.trade_log import trade_logger
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
NIFTY_LOT_SIZE = 65


@dataclass
class NiftyGexConfig:
    nifty_instrument_key: str = "NSE_INDEX|Nifty 50"
    account_equity:       float = 50000.0   # fixed capital pool, per handoff decision -- NOT a live balance query
    risk_pct:             float = 0.015     # matches gex_trade_management.DEFAULT_RISK_PCT
    gex_poll_interval:    float = 60.0      # seconds between GEX/entry-checklist evaluations
    order_timeout:        float = 120.0     # seconds before unfilled entry order is cancelled
    strike_range:         int   = 10
    strike_step:          int   = 50
    seed_refresh_hour:    int   = 9         # re-seed 15-min historical candles once per day, at/after this hour


class NiftyGex(BaseStrategy):

    def __init__(self, broker: AbstractBrokerGateway, config: NiftyGexConfig):
        super().__init__(name="nifty_gex", broker=broker, config=vars(config))
        self.cfg    = config
        self._loop  = None
        self._is_paper = os.getenv("PAPER_TRADE", "false").lower() == "true"

        self._current_spot: float = 0.0

        # Resolved once per day at on_start()
        self._instruments = None
        self._expiry: date = None
        self._chain_keys: dict = {}          # {strike: {"CE": ikey, "PE": ikey}}
        self._historical_15min_seed: list = []
        self._seed_date = None                # date the seed was last fetched, guards re-fetch

        # Throttled evaluation
        self._last_eval_at: float = 0.0

        # Pending entry order (placed, not yet filled)
        self._entry_order_id: str = ""
        self._entry_placed_at: float = 0.0
        self._pending_trade: dict = {}        # direction, plan, symbol, quantity, target_strike

        # Active (filled) trade -- only one at a time, per module docstring
        self._active_trade: dict = {}         # id, symbol, direction, entry_price(option), quantity, plan, entry_candle_count
        self._closing_lock = asyncio.Lock()

        self._realised_pnl   = 0.0
        self._unrealised_pnl = 0.0
        self._closed_trades  = 0

        _seeded = None
        try:
            from core.state_store import state_store as _ss
            _seeded = _ss.get_strategy(self.name)
        except Exception:
            pass
        if _seeded:
            self._realised_pnl = _seeded.realised_pnl or 0.0

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        self._loop = asyncio.get_running_loop()

        self._current_spot = await self.broker.get_ltp(self.cfg.nifty_instrument_key)
        if self._current_spot == 0.0:
            logger.warning(f"[nifty_gex] Could not fetch initial LTP for {self.cfg.nifty_instrument_key}")

        try:
            self._instruments = fetch_instruments()
            self._expiry = get_nearest_tuesday(date.today())
            self._chain_keys = resolve_strike_chain(
                self._instruments, self._expiry, self._current_spot or 24000.0,
                strike_range=self.cfg.strike_range, strike_step=self.cfg.strike_step,
            )
            resolved = sum(1 for legs in self._chain_keys.values() for k in legs.values() if k)
            logger.info(f"[nifty_gex] Strike chain resolved: {resolved}/{len(self._chain_keys) * 2} contracts | expiry={self._expiry}")
        except Exception as e:
            logger.error(f"[nifty_gex] Strike chain resolution failed at startup: {e}")

        try:
            self._historical_15min_seed = seed_historical_15min_candles(os.getenv("UPSTOX_ACCESS_TOKEN"))
            self._seed_date = date.today()
        except Exception as e:
            logger.error(f"[nifty_gex] Historical 15-min seed fetch failed at startup: {e}")

        self.broker.subscribe_ticks(
            symbols=[self.cfg.nifty_instrument_key],
            callback=self._on_tick_sync,
        )
        self.broker.on_order_update(self._on_order_update)

        # Reload today's open trade from DB, if any (handles restarts)
        today = date.today().isoformat()
        open_trades = [
            t for t in trade_logger.get_trades(strategy=self.name, status="OPEN")
            if t.get("entry_time", "")[:10] == today
        ]
        if open_trades:
            t = open_trades[0]
            self._active_trade = {
                "id":           t.get("id", ""),
                "order_id":     t.get("broker_order_id", "RESTORED"),
                "symbol":       t.get("symbol"),
                "direction":    "bullish" if t.get("order_type") == "BUY" else "bearish",
                "entry_price":  t.get("entry_price"),
                "quantity":     t.get("quantity"),
                "plan":         None,   # plan not persisted -- restored trade exits on time-stop / manual only
                "entry_candle_count": len(market_context.session_candles_1min),
            }
            self._signal(f"Restored open trade from DB: {self._active_trade['symbol']}")
            self._update_position("LONG" if self._active_trade["direction"] == "bullish" else "SHORT")

        self._signal(f"Started | Spot: {self._current_spot:.2f} | Expiry: {self._expiry}")

    async def on_stop(self) -> None:
        if self._entry_order_id and not self._entry_order_id.startswith("PAPER_"):
            try:
                await self.broker.cancel_order(self._entry_order_id)
            except Exception as e:
                logger.warning(f"[nifty_gex] Failed to cancel pending entry order on stop: {e}")
        self.broker.unsubscribe_ticks([self.cfg.nifty_instrument_key])

    async def _on_recover_trade(self, row: dict) -> None:
        """Restore an orphaned OPEN trade from the DB (BaseStrategy._recover_open_positions)."""
        self._active_trade = {
            "id":          row.get("id"),
            "order_id":    row.get("broker_order_id", row.get("id")),
            "symbol":      row.get("symbol", ""),
            "direction":   "bullish" if row.get("order_type") == "BUY" else "bearish",
            "entry_price": row.get("entry_price"),
            "quantity":    row.get("quantity"),
            "plan":        None,
            "entry_candle_count": len(market_context.session_candles_1min),
        }
        try:
            risk_manager.register_trade(self.name, "BUY")
        except Exception as e:
            logger.error(f"[nifty_gex] risk_manager.register_trade failed during recovery: {e}")
        try:
            self.broker.subscribe_ticks(symbols=[self._active_trade["symbol"]], callback=self._on_tick_sync)
        except Exception as e:
            logger.error(f"[nifty_gex] Could not resubscribe ticks for recovered trade: {e}")
        self._update_position("LONG" if self._active_trade["direction"] == "bullish" else "SHORT")

    # ── Tick Handling ───────────────────────────────────────────────────

    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._loop)
        else:
            logger.error("[nifty_gex] Event loop not running")

    async def on_tick(self, tick: Tick) -> None:
        try:
            if self._stop_flag or not self.is_market_open():
                return

            self._current_spot = tick.mid_price

            # Monitor any active trade FIRST, unconditionally -- same rule as
            # wave_extractor: exits must never be skipped by entry-side gating.
            if self._active_trade:
                await self._monitor_active_trade()
                return  # only one trade at a time -- no new entries while one is open

            blocked, reason = risk_manager.is_trading_blocked()
            if blocked:
                return

            if self._entry_order_id:
                return  # entry order pending -- wait for fill/timeout, don't re-evaluate

            now = time.time()
            if now - self._last_eval_at < self.cfg.gex_poll_interval:
                return
            self._last_eval_at = now

            can_trade, _reason = risk_manager.can_trade(self.name, "BUY")
            if not can_trade:
                return

            await self._evaluate_entry()

        except Exception as e:
            logger.exception(f"[nifty_gex] ERROR in on_tick: {e}")

    # ── Entry Evaluation ─────────────────────────────────────────────────

    async def _refresh_seed_if_new_day(self) -> None:
        if self._seed_date != date.today():
            try:
                self._historical_15min_seed = seed_historical_15min_candles(os.getenv("UPSTOX_ACCESS_TOKEN"))
                self._seed_date = date.today()
                self._expiry = get_nearest_tuesday(date.today())
                self._chain_keys = resolve_strike_chain(
                    self._instruments or fetch_instruments(), self._expiry, self._current_spot,
                    strike_range=self.cfg.strike_range, strike_step=self.cfg.strike_step,
                )
                logger.info(f"[nifty_gex] Daily refresh: new expiry={self._expiry}, seed={len(self._historical_15min_seed)} candles")
            except Exception as e:
                logger.error(f"[nifty_gex] Daily refresh failed: {e}")

    async def _evaluate_entry(self) -> None:
        await self._refresh_seed_if_new_day()

        candles = market_context.session_candles_1min
        if len(candles) < 15:
            return  # not enough data for ATR yet -- evaluate_entry needs this internally too

        try:
            cfg_api = upstox_client.Configuration()
            cfg_api.access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
            client = upstox_client.ApiClient(cfg_api)

            chain_data = fetch_chain_greeks_and_oi(client, self._chain_keys)
            if not chain_data:
                logger.warning("[nifty_gex] Empty chain_data -- skipping this evaluation cycle")
                return

            gex = compute_signed_gex(chain_data, self._current_spot)
            gex_regime = classify_regime(gex)
            if not gex_regime:
                return

            ema_stack = get_multi_timeframe_stack(candles, self._historical_15min_seed)

            checklist = evaluate_entry(
                spot=self._current_spot,
                candles=candles,
                gex_regime=gex_regime,
                ema_stack=ema_stack,
                chain_data=chain_data,
            )

            if not checklist.all_pass:
                logger.debug(f"[nifty_gex] No A+ setup this cycle: {checklist.reasons_failed}")
                return

            # ── Build trade plan (index-points stop/target) ────────────────
            highs  = [c.high for c in candles]
            lows   = [c.low for c in candles]
            closes = [c.close for c in candles]
            from core.regime_engine import RegimeEngine
            ema50 = RegimeEngine._calc_ema(closes, 50)

            plan = build_trade_plan(
                direction=checklist.direction,
                entry_price=self._current_spot,
                candles=candles,
                ema50=ema50,
                gex_regime=gex_regime,
                account_equity=self.cfg.account_equity,
                target_strike=checklist.target_strike,
                risk_pct=self.cfg.risk_pct,
            )
            if not plan.valid:
                logger.info(f"[nifty_gex] Setup found but trade plan invalid: {plan.reason}")
                return

            leg = "CE" if checklist.direction == "bullish" else "PE"
            option_symbol = self._chain_keys.get(checklist.target_strike, {}).get(leg)
            if not option_symbol:
                logger.warning(f"[nifty_gex] No resolved instrument key for strike={checklist.target_strike} leg={leg} -- skipping")
                return

            option_last_price = chain_data.get(checklist.target_strike, {}).get(leg, {}).get("last_price")
            if not option_last_price or option_last_price <= 0:
                logger.warning(f"[nifty_gex] No valid option last_price for {option_symbol} -- skipping")
                return

            await self._place_entry_order(checklist, plan, option_symbol, option_last_price, gex_regime)

        except Exception as e:
            logger.exception(f"[nifty_gex] _evaluate_entry failed: {e}")

    async def _place_entry_order(self, checklist, plan, option_symbol: str, limit_price: float, gex_regime: dict = None) -> None:
        now_tz = datetime.now(IST)
        tag = f"GEX_ENTRY_{checklist.direction.upper()}_{now_tz.strftime('%Y%m%d%H%M%S')}"

        self._pending_trade = {
            "direction":     checklist.direction,
            "plan":          plan,
            "symbol":        option_symbol,
            "quantity":      plan.quantity,
            "target_strike": checklist.target_strike,
            # Captured for post-mortem entry_context -- see _handle_order_update
            "gex_regime":    gex_regime,
            "structure_type": checklist.structure_type,
        }
        self._entry_placed_at = time.time()

        try:
            if self._is_paper:
                self._entry_order_id = f"PAPER_GEX_ENTRY_{now_tz.strftime('%Y%m%d%H%M%S')}"
                self._signal(
                    f"[PAPER] BUY {plan.quantity} {option_symbol} @ {limit_price} "
                    f"| direction={checklist.direction} | target_strike={checklist.target_strike} "
                    f"| stop={plan.stop_price:.1f} target={plan.target1_price:.1f} R:R={plan.reward_to_risk:.2f}"
                )
                # Paper fills simulated as immediate for the entry (option premium
                # doesn't tick in market_context the way NIFTY spot does) -- first-draft
                # simplification, flagged for revisit.
                await self._handle_order_update({
                    "order_id": self._entry_order_id, "status": "COMPLETE",
                    "average_price": limit_price, "filled_qty": plan.quantity,
                })
                return

            resp = await self.broker.place_order(Order(
                symbol=option_symbol,
                exchange="NFO",
                order_type="BUY",
                quantity=plan.quantity,
                product="I",
                price=limit_price,
                tag=tag,
            ))
            if resp.status == "REJECTED":
                self._signal(f"Entry order REJECTED: {resp.message}")
                self._entry_order_id = ""
                self._pending_trade = {}
                return
            self._entry_order_id = resp.order_id
            asyncio.create_task(self._entry_order_timeout_watchdog(self._entry_order_id))
            self._signal(
                f"Entry order placed: {resp.order_id} | BUY {plan.quantity} {option_symbol} @ {limit_price} "
                f"| direction={checklist.direction} | stop={plan.stop_price:.1f} target={plan.target1_price:.1f}"
            )
        except Exception as e:
            logger.error(f"[nifty_gex] Entry order placement failed: {e}")
            self._entry_order_id = ""
            self._pending_trade = {}

    async def _entry_order_timeout_watchdog(self, order_id: str) -> None:
        await asyncio.sleep(self.cfg.order_timeout)
        if self._entry_order_id != order_id:
            return  # already filled or replaced
        try:
            await self.broker.cancel_order(order_id)
            self._signal(f"Entry order timeout ({self.cfg.order_timeout:.0f}s) -- cancelled {order_id}")
        except Exception as e:
            logger.warning(f"[nifty_gex] Entry order cancel-on-timeout failed: {e}")
        self._entry_order_id = ""
        self._pending_trade = {}

    # ── Order Update / Fill Handling ─────────────────────────────────────

    def _on_order_update(self, update: dict) -> None:
        try:
            if self._stop_flag or self._is_paper:
                return
            asyncio.run_coroutine_threadsafe(self._handle_order_update(update), self._loop)
        except Exception as e:
            logger.error(f"[nifty_gex] Order update error: {e}")

    async def _handle_order_update(self, update: dict) -> None:
        order_id = update.get("order_id", "")
        status   = update.get("status", "")
        if status != "COMPLETE":
            return
        price      = float(update.get("average_price", 0))
        filled_qty = int(update.get("filled_qty", 0))

        if order_id == self._entry_order_id and self._pending_trade:
            pt = self._pending_trade
            plan = pt.get("plan")
            entry_context = json.dumps({
                "direction":        pt.get("direction"),
                "target_strike":    pt.get("target_strike"),
                "structure_type":   pt.get("structure_type"),
                "gex_regime":       (pt.get("gex_regime") or {}).get("regime"),
                "net_gex":          (pt.get("gex_regime") or {}).get("net_gex"),
                "stop_price":       plan.stop_price if plan else None,
                "target1_price":    plan.target1_price if plan else None,
                "reward_to_risk":   plan.reward_to_risk if plan else None,
                "quantity":         plan.quantity if plan else None,
                "risk_amount":      plan.risk_amount if plan else None,
                "spot_at_entry":    self._current_spot,
            }, default=str)
            trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=pt["symbol"],
                readable_symbol=pt["symbol"],
                order_type="BUY",
                quantity=filled_qty or pt["quantity"],
                entry_price=price,
                broker_order_id=order_id,
                client_order_id=f"GEX_ENTRY_{uuid.uuid4().hex[:8]}",
                paper_trade=self._is_paper,
                entry_context=entry_context,
            )
            self._active_trade = {
                "id":           trade_id,
                "order_id":     order_id,
                "symbol":       pt["symbol"],
                "direction":    pt["direction"],
                "entry_price":  price,
                "quantity":     filled_qty or pt["quantity"],
                "plan":         pt["plan"],
                "entry_candle_count": len(market_context.session_candles_1min),
            }
            risk_manager.register_trade(self.name, "BUY")
            self._update_position("LONG" if pt["direction"] == "bullish" else "SHORT")
            self._signal(f"ENTRY FILLED: {pt['symbol']} @ {price:.2f} qty={filled_qty or pt['quantity']}")
            self._entry_order_id = ""
            self._pending_trade = {}

    # ── Exit Monitoring ───────────────────────────────────────────────────

    async def _monitor_active_trade(self) -> None:
        trade = self._active_trade
        plan  = trade.get("plan")
        spot  = self._current_spot

        if plan is None:
            # Restored/recovered trade with no persisted plan -- time-stop
            # hard cutoff is the only safety net available for it.
            now = datetime.now(IST)
            hit, reason = check_time_stop(now, candles_elapsed=999)  # force hard-cutoff-only check
            if now.hour > 14 or (now.hour == 14 and now.minute >= 45):
                await self._close_active_trade("HARD_CUTOFF_NO_PLAN")
            return

        direction = trade["direction"]
        stop_hit = (
            spot <= plan.stop_price if direction == "bullish" else spot >= plan.stop_price
        )
        target_hit = (
            spot >= plan.target1_price if direction == "bullish" else spot <= plan.target1_price
        )

        if stop_hit:
            self._signal(f"STOP HIT | spot={spot:.1f} stop={plan.stop_price:.1f}")
            await self._close_active_trade("STOP_LOSS")
            return
        if target_hit:
            self._signal(f"TARGET HIT | spot={spot:.1f} target={plan.target1_price:.1f}")
            await self._close_active_trade("TARGET")
            return

        candles_since_entry = len(market_context.session_candles_1min) - trade.get("entry_candle_count", 0)
        candles_elapsed_5min = candles_since_entry // 5   # proxy -- see module docstring
        now = datetime.now(IST)
        hit, reason = check_time_stop(now, candles_elapsed=candles_elapsed_5min)
        if hit:
            self._signal(f"TIME STOP: {reason}")
            await self._close_active_trade("TIME_STOP")

    async def _close_active_trade(self, reason: str) -> None:
        async with self._closing_lock:
            trade = self._active_trade
            if not trade:
                return
            self._active_trade = {}

            symbol = trade["symbol"]
            qty    = trade["quantity"]
            try:
                exit_price = await self.broker.get_ltp(symbol)
            except Exception as e:
                logger.error(f"[nifty_gex] get_ltp failed on close for {symbol}: {e}")
                exit_price = trade["entry_price"]

            try:
                now_tz = datetime.now(IST)
                tag = f"GEX_EXIT_{reason}_{now_tz.strftime('%Y%m%d%H%M%S')}"
                if self._is_paper:
                    exit_order_id = f"PAPER_GEX_EXIT_{now_tz.strftime('%Y%m%d%H%M%S')}"
                    self._signal(f"[PAPER] EXIT SELL {qty} {symbol} @ {exit_price} (simulated) | reason={reason}")
                else:
                    resp = await self.broker.place_order(Order(
                        symbol=symbol, exchange="NFO", order_type="SELL",
                        quantity=qty, product="I", price=exit_price, tag=tag,
                    ))
                    exit_order_id = resp.order_id
                    self._signal(f"EXIT order placed: {exit_order_id} | SELL {qty} {symbol} @ {exit_price} | reason={reason}")

                entry_price = trade["entry_price"]
                from core.transaction_costs import calculate_order_cost
                entry_cost = calculate_order_cost(entry_price, qty, "BUY")
                exit_cost  = calculate_order_cost(exit_price, qty, "SELL")
                gross_pnl  = (exit_price - entry_price) * qty
                pnl        = gross_pnl - entry_cost - exit_cost

                self._realised_pnl  += pnl
                self._closed_trades += 1

                if trade.get("id"):
                    trade_logger.close_trade(
                        trade["id"], exit_price, reason,
                        net_pnl=pnl, gross_pnl=gross_pnl, total_costs=entry_cost + exit_cost,
                    )

                self._update_pnl(self._realised_pnl, 0.0)
                self._update_position("FLAT")
                self._signal(f"Trade closed | {reason} | Net P&L: \u20b9{pnl:.2f}")

            except Exception as e:
                logger.error(f"[nifty_gex] _close_active_trade failed: {e}")
            finally:
                risk_manager.release_trade(self.name, "BUY")

    # ── Required overrides ────────────────────────────────────────────────

    def get_config(self) -> dict:
        return vars(self.cfg)