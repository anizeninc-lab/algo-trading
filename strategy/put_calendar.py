# strategy/put_calendar.py
#
# "Super Simple Monthly Put Calendar Spread"
# ────────────────────────────────────────────────────────────────────────
# ENTRY  (checked once per gex-style poll interval, not every tick --
#         this is a slow, low-frequency setup, mirrors nifty_gex.py's
#         throttled-evaluation pattern rather than survivor.py's
#         tick-driven scalping pattern):
#   - Front weekly option has <= front_dte_trigger days to expiry
#   - AND (IV percentile < iv_percentile_max) OR (front IV > back IV,
#     i.e. term-structure backwardation)
#   - SELL front-week ATM PE / BUY back-month (monthly) ATM PE, same strike
#
# EXIT:
#   - No profit target, by design (confirmed with user) -- runs to one of:
#   - Stop loss: unrealised loss >= sl_pct_of_capital (20%) of capital
#     employed (net debit paid to open the spread)
#   - Forced exit forced_exit_days_before_expiry calendar days before the
#     FRONT expiry, to avoid front-leg gamma risk into expiry
#   - ASSUMPTION FLAGGED: the spec says "exit Friday/Monday before expiry",
#     which described the OLD Thursday-expiry Nifty weekly cycle. This repo's
#     weekly expiry is now Tuesday (see core/auto_config.py), so "Friday/
#     Monday before Tuesday" doesn't map cleanly. Implemented instead as a
#     configurable N calendar days before front expiry (default 1 = exits
#     Monday for a Tuesday expiry, which is the closest honest equivalent).
#     Revisit forced_exit_days_before_expiry if that's not what you meant.
#
# ADJUSTMENT:
#   - "Roll" the calendar (close both legs, reopen new ATM legs at current
#     spot, same expiries) when spot approaches an approximate breakeven
#     and SL has not yet hit.
#   - ASSUMPTION FLAGGED: true calendar-spread breakevens require a full
#     options pricing model (they shift with IV and time, aren't a fixed
#     distance from the strike). This uses a simple, commonly-used
#     approximation: breakeven ~= short_strike +/- (breakeven_width_multiplier
#     * net_debit). Treat this as a rough trigger, not a precise breakeven --
#     tune breakeven_width_multiplier or replace with a real pricing model
#     (mibian is already a dependency, see core/greeks_engine.py) if this
#     proves too loose/tight in paper testing.
#
# REGIME: unlike survivor/wave_extractor, this strategy is NOT gated by
# market regime in strategy_filter.py (see the added "put_calendar" entry
# there) -- a calendar spread is a volatility/theta play, not a directional
# one, so regime classification isn't a natural fit for its entry logic.
# It is still gated by everything else in strategy_filter.can_trade()
# (trading hours, PCR extremes, event filter, session plan LOW-confidence
# freeze, opening range lock).
#
# CAPITAL MODEL: risk_manager's capital guard uses a fixed per-lot margin
# model (MARGIN_PER_SELL_LOT / MARGIN_PER_BUY_LOT), same as every other
# strategy in this repo -- used here for capital-pool registration so this
# strategy participates in the same shared pool correctly. This is DIFFERENT
# from the strategy's own SL math, which correctly uses actual net debit
# paid (real cash at risk for a calendar spread), not the fixed margin
# figures. Both are intentional and serve different purposes -- flagged
# here so the distinction isn't missed later.
#
# PAPER MODE ONLY as of this draft (confirmed with user, mirrors
# wave_extractor's paper-mode pattern). Live-mode order flow is written but
# unexercised -- test thoroughly in paper before ever flipping PAPER_TRADE.

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytz

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.auto_config import (
    fetch_instruments,
    find_symbol_from_instruments,
    get_nearest_tuesday,
    get_nearest_monthly_expiry,
    round_to_strike,
)
from core.iv_tracker import iv_tracker
from core.risk_manager import risk_manager
from core.strategy_filter import strategy_filter
from core.trade_log import trade_logger
from core.transaction_costs import calculate_order_cost
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class PutCalendarConfig:
    nifty_instrument_key: str = "NSE_INDEX|Nifty 50"
    lot_size:             int = 65
    strike_interval:      float = 50.0
    front_dte_trigger:    int = 10     # enter once front weekly DTE <= this
    iv_percentile_max:    float = 40.0
    sl_pct_of_capital:    float = 0.20  # 20% of net debit paid
    forced_exit_days_before_expiry: int = 1  # see ASSUMPTION FLAGGED note above
    adjustment_trigger_pct: float = 0.90     # roll once spot is 90% of the way to approx breakeven
    breakeven_width_multiplier: float = 1.2  # rough approximation, see module docstring
    eval_poll_interval:   float = 60.0       # seconds between entry-condition checks
    paper_trade_override: bool = False
    strategy_name:        str = "put_calendar"


class PutCalendar(BaseStrategy):

    def __init__(self, broker: AbstractBrokerGateway, config: PutCalendarConfig):
        super().__init__(name=config.strategy_name, broker=broker, config=vars(config))
        self.cfg = config
        self._loop = None
        self._is_paper = (
            os.getenv("PAPER_TRADE", "false").lower() == "true"
            or self.cfg.paper_trade_override
        )
        self._current_spot: float = 0.0
        self._instruments: list = []
        self._last_eval_at: float = 0.0
        self._ltp_cache: dict = {}

        # Only one calendar position at a time -- matches nifty_gex's
        # "selective, infrequent setup" philosophy, not a scaling strategy.
        self._active_trade: dict = {}
        self._closing_lock = asyncio.Lock()
        self._last_block_reason = ""

        self._realised_pnl = 0.0
        self._unrealised_pnl = 0.0
        self._closed_trades = 0

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
            logger.warning(f"[{self.name}] Could not fetch initial NIFTY LTP")

        try:
            self._instruments = fetch_instruments()
        except Exception as e:
            logger.error(f"[{self.name}] fetch_instruments failed at startup: {e}")

        self.broker.subscribe_ticks(
            symbols=[self.cfg.nifty_instrument_key],
            callback=self._on_tick_sync,
        )

        # Restore any open position from DB (handles restarts) -- both legs
        # share a parent_trade_id like survivor's hedge-leg pattern.
        today = date.today().isoformat()
        open_trades = [
            t for t in trade_logger.get_trades(strategy=self.name, status="OPEN")
            if t.get("entry_time", "")[:10] <= today
        ]
        front_leg = next((t for t in open_trades if t.get("order_type") == "SELL"), None)
        back_leg  = next((t for t in open_trades if t.get("order_type") == "BUY"), None)
        if front_leg and back_leg:
            try:
                ctx = json.loads(front_leg.get("entry_context") or "{}")
            except Exception:
                ctx = {}
            self._active_trade = {
                "front_trade_id": front_leg.get("id"),
                "back_trade_id":  back_leg.get("id"),
                "front_symbol":   front_leg.get("symbol"),
                "back_symbol":    back_leg.get("symbol"),
                "front_entry":    front_leg.get("entry_price", 0.0),
                "back_entry":     back_leg.get("entry_price", 0.0),
                "quantity":       front_leg.get("quantity", self.cfg.lot_size),
                "short_strike":   ctx.get("short_strike", 0.0),
                "net_debit":      ctx.get("net_debit", 0.0),
                "capital_employed": ctx.get("capital_employed", 0.0),
                "front_expiry":   ctx.get("front_expiry", ""),
                "back_expiry":    ctx.get("back_expiry", ""),
            }
            self._signal(
                f"Restored open calendar: SELL {self._active_trade['front_symbol']} / "
                f"BUY {self._active_trade['back_symbol']}"
            )
            self._update_position("SHORT_CALENDAR")

        self._signal(f"Started | Spot: {self._current_spot:.2f} | Paper: {self._is_paper}")

    async def on_stop(self) -> None:
        self.broker.unsubscribe_ticks([self.cfg.nifty_instrument_key], callback=self._on_tick_sync)
        if self._active_trade:
            for sym in (self._active_trade.get("front_symbol"), self._active_trade.get("back_symbol")):
                if sym:
                    try:
                        self.broker.unsubscribe_ticks([sym], callback=self._on_tick_sync)
                    except Exception:
                        pass

    async def _on_recover_trade(self, row: dict) -> None:
        """
        BaseStrategy's crash-recovery hook fires per-row, but this strategy's
        two legs need to be recovered together. Real recovery already happens
        in on_start() above (which reads both legs at once); this override
        just prevents BaseStrategy's default force-close-single-row behavior
        from firing on the half-recovered first leg it sees before on_start
        has paired them up.
        """
        pass

    # ── Tick handling ───────────────────────────────────────────────────

    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._loop)
        else:
            logger.error(f"[{self.name}] Event loop not running")

    async def on_tick(self, tick: Tick) -> None:
        try:
            logger.debug(f"[{self.name}] on_tick fired | stop_flag={self._stop_flag} market_open={self.is_market_open()}")
            if self._stop_flag or not self.is_market_open():
                return

            if tick.symbol == self.cfg.nifty_instrument_key or "Nifty" in tick.symbol:
                self._current_spot = tick.mid_price
            self._ltp_cache[tick.symbol] = tick.last_price

            # Exits/adjustment checked unconditionally, every tick -- must
            # never be skipped by entry-side throttling or gating.
            if self._active_trade:
                await self._monitor_active_trade()
                return  # one calendar at a time

            blocked, reason = risk_manager.is_trading_blocked(self.name)
            if blocked:
                if reason != self._last_block_reason:
                    self._last_block_reason = reason
                    logger.info(f"[{self.name}] Trading blocked: {reason}")
                return
            self._last_block_reason = ""

            now = time.time()
            if now - self._last_eval_at < self.cfg.eval_poll_interval:
                return
            self._last_eval_at = now

            await self._evaluate_entry()

        except Exception as e:
            logger.exception(f"[{self.name}] ERROR in on_tick: {e}")

    # ── Entry evaluation ────────────────────────────────────────────────

    def _atm_leg(self, chain: list, atm_strike: float) -> dict | None:
        """Find the PE row closest to atm_strike in a get_option_chain() result."""
        if not chain:
            return None
        candidates = [row for row in chain if row.get("pe_ltp", 0.0) > 0]
        if not candidates:
            return None
        return min(candidates, key=lambda r: abs(r.get("strike", 0.0) - atm_strike))

    async def _evaluate_entry(self) -> None:
        front_expiry = get_nearest_tuesday(date.today())
        dte = (front_expiry - date.today()).days
        if dte > self.cfg.front_dte_trigger or dte <= 0:
            return  # not yet within the entry window (or expiry day itself -- too late)

        spot = self._current_spot or await self.broker.get_ltp(self.cfg.nifty_instrument_key)
        if not spot:
            return
        atm_strike = round_to_strike(spot, int(self.cfg.strike_interval))

        back_expiry = get_nearest_monthly_expiry(date.today())
        if back_expiry <= front_expiry:
            # Front week IS the monthly expiry week -- no distinct back month
            # available yet to calendar against. Skip until a real gap exists.
            logger.debug(f"[{self.name}] Front expiry {front_expiry} == back expiry {back_expiry} — skipping")
            return

        try:
            front_chain = await self.broker.get_option_chain(
                self.cfg.nifty_instrument_key, front_expiry.strftime("%Y-%m-%d")
            )
            back_chain = await self.broker.get_option_chain(
                self.cfg.nifty_instrument_key, back_expiry.strftime("%Y-%m-%d")
            )
        except Exception as e:
            logger.error(f"[{self.name}] Option chain fetch failed: {e}")
            return

        front_leg = self._atm_leg(front_chain, atm_strike)
        back_leg  = self._atm_leg(back_chain, atm_strike)
        if not front_leg or not back_leg:
            logger.debug(f"[{self.name}] No liquid ATM PE found on one or both legs near {atm_strike}")
            return

        front_iv = front_leg.get("pe_iv", 0.0)
        back_iv  = back_leg.get("pe_iv", 0.0)
        if front_iv <= 0:
            logger.debug(f"[{self.name}] Front IV unavailable — skipping this cycle")
            return

        iv_tracker.record_today(front_iv)
        iv_pct = iv_tracker.percentile(front_iv)

        low_iv = (iv_pct is not None and iv_pct < self.cfg.iv_percentile_max)
        backwardation = (back_iv > 0 and front_iv > back_iv)

        logger.debug(
            f"[{self.name}] Entry check | front_iv={front_iv:.2f} back_iv={back_iv:.2f} "
            f"iv_pct={iv_pct if iv_pct is not None else 'n/a'} "
            f"(need < {self.cfg.iv_percentile_max}) low_iv={low_iv} backwardation={backwardation}"
        )

        if not (low_iv or backwardation):
            return  # neither entry condition met

        # ── strategy_filter + risk_manager gates ────────────────────────
        sf_ok, sf_reason = strategy_filter.can_trade(self.name)
        if not sf_ok:
            if sf_reason != self._last_block_reason:
                self._last_block_reason = sf_reason
                logger.info(f"[{self.name}] strategy_filter blocked: {sf_reason}")
            return

        rm_ok, rm_reason = risk_manager.can_trade(self.name, "SELL")
        if not rm_ok:
            return

        reason_str = (
            f"IV percentile={iv_pct}" if low_iv else ""
        ) + (
            (" + " if low_iv and backwardation else "") +
            (f"backwardation front={front_iv:.1f} > back={back_iv:.1f}" if backwardation else "")
        )
        self._signal(
            f"Entry conditions met | DTE={dte} | ATM={atm_strike} | {reason_str}"
        )
        await self._place_calendar(
            atm_strike, front_expiry, back_expiry,
            front_leg, back_leg, forced=False,
        )

    # ── Order placement ──────────────────────────────────────────────────

    async def _resolve_symbol(self, expiry: date, strike: float) -> str:
        name_out = {}
        ikey = find_symbol_from_instruments(
            self._instruments or fetch_instruments(),
            expiry, int(strike), "PE", underlying="NIFTY", name_out=name_out,
        )
        return ikey

    async def _place_calendar(
        self, strike: float, front_expiry: date, back_expiry: date,
        front_leg: dict, back_leg: dict, forced: bool,
    ) -> None:
        """
        forced=True is used by _roll_calendar() for a defensive re-center --
        it bypasses the IV/DTE entry condition (rolling is a risk-management
        action, not a fresh discretionary entry) but still goes through
        strategy_filter/risk_manager gates.
        """
        quantity = self.cfg.lot_size
        front_symbol = await self._resolve_symbol(front_expiry, strike)
        back_symbol  = await self._resolve_symbol(back_expiry, strike)
        if not front_symbol or not back_symbol:
            logger.error(f"[{self.name}] Could not resolve symbols for strike {strike} — abandoning entry")
            return

        front_premium = front_leg.get("pe_ltp", 0.0)
        back_premium  = back_leg.get("pe_ltp", 0.0)

        try:
            if self._is_paper:
                front_sell_price = round(front_premium * 0.98, 1)
                back_buy_price   = round(back_premium * 1.02, 1)
                front_order_id = f"PAPER_PCAL_FRONT_{int(time.time()*1000) % 100000}"
                back_order_id  = f"PAPER_PCAL_BACK_{int(time.time()*1000) % 100000}"
                self._signal(f"[PAPER] SELL {quantity} {front_symbol} @ {front_sell_price} (simulated)")
                self._signal(f"[PAPER] BUY {quantity} {back_symbol} @ {back_buy_price} (simulated)")
            else:
                front_resp = await self.broker.place_order(Order(
                    symbol=front_symbol, exchange="NFO", order_type="SELL",
                    quantity=quantity, product="I",
                    price=round(front_premium * 0.98, 1),
                    tag=f"PCAL_FRONT_{int(strike)}_{int(time.time()*1000) % 100000}",
                ))
                if front_resp.status == "REJECTED":
                    self._signal(f"Front leg SELL REJECTED: {front_resp.message}")
                    return
                front_sell_price = await self.broker.get_ltp(front_symbol)
                front_order_id = front_resp.order_id

                back_resp = await self.broker.place_order(Order(
                    symbol=back_symbol, exchange="NFO", order_type="BUY",
                    quantity=quantity, product="I",
                    price=round(back_premium * 1.02, 1),
                    tag=f"PCAL_BACK_{int(strike)}_{int(time.time()*1000) % 100000}",
                ))
                if back_resp.status == "REJECTED":
                    self._signal(f"Back leg BUY REJECTED: {back_resp.message} — front leg is now UNHEDGED, closing it")
                    await self.broker.place_order(Order(
                        symbol=front_symbol, exchange="NFO", order_type="BUY",
                        quantity=quantity, product="I", price=0.0,
                        tag=f"PCAL_FRONT_UNWIND_{int(time.time()*1000) % 100000}",
                    ))
                    return
                back_buy_price = await self.broker.get_ltp(back_symbol)
                back_order_id = back_resp.order_id

                self.broker.subscribe_ticks(symbols=[front_symbol, back_symbol], callback=self._on_tick_sync)
        except Exception as e:
            logger.error(f"[{self.name}] _place_calendar order placement failed: {e}")
            return

        net_debit = round(back_buy_price - front_sell_price, 2)
        capital_employed = round(net_debit * quantity, 2)
        if net_debit <= 0:
            logger.warning(
                f"[{self.name}] Net debit is non-positive (₹{net_debit}) — unusual for a "
                f"calendar spread, double-check front/back pricing. Proceeding anyway."
            )

        entry_ctx = json.dumps({
            "short_strike": strike,
            "net_debit": net_debit,
            "capital_employed": capital_employed,
            "front_expiry": front_expiry.isoformat(),
            "back_expiry": back_expiry.isoformat(),
            "rolled": forced,
        }, default=str)

        front_trade_id = trade_logger.open_trade(
            strategy=self.name, broker=type(self.broker).__name__,
            symbol=front_symbol, readable_symbol=front_symbol,
            order_type="SELL", quantity=quantity, entry_price=front_sell_price,
            broker_order_id=front_order_id,
            client_order_id=f"PCAL_FRONT_{uuid.uuid4().hex[:8]}",
            notes=f"Calendar front leg{' (rolled)' if forced else ''}",
            paper_trade=self._is_paper, entry_context=entry_ctx,
        )
        back_trade_id = trade_logger.open_trade(
            strategy=self.name, broker=type(self.broker).__name__,
            symbol=back_symbol, readable_symbol=back_symbol,
            order_type="BUY", quantity=quantity, entry_price=back_buy_price,
            broker_order_id=back_order_id,
            client_order_id=f"PCAL_BACK_{uuid.uuid4().hex[:8]}",
            notes=f"Calendar back leg{' (rolled)' if forced else ''}",
            parent_trade_id=front_trade_id,
            paper_trade=self._is_paper, entry_context=entry_ctx,
        )

        # Capital pool registration -- fixed per-lot margin model, see module
        # docstring "CAPITAL MODEL" note. SELL leg counts as this position's
        # "1 trade" for the daily trade-count limit; BUY leg is capital-only.
        risk_manager.register_trade(self.name, "SELL", multiplier=1)
        risk_manager.register_capital(self.name, "BUY", multiplier=1)

        self._active_trade = {
            "front_trade_id": front_trade_id,
            "back_trade_id": back_trade_id,
            "front_symbol": front_symbol,
            "back_symbol": back_symbol,
            "front_entry": front_sell_price,
            "back_entry": back_buy_price,
            "quantity": quantity,
            "short_strike": strike,
            "net_debit": net_debit,
            "capital_employed": capital_employed,
            "front_expiry": front_expiry.isoformat(),
            "back_expiry": back_expiry.isoformat(),
        }
        self._update_position("SHORT_CALENDAR")
        self._signal(
            f"CALENDAR OPENED | strike={strike:.0f} | SELL {front_symbol}@{front_sell_price} / "
            f"BUY {back_symbol}@{back_buy_price} | net debit=₹{net_debit} | "
            f"capital employed=₹{capital_employed:,.0f}{' [ROLLED]' if forced else ''}"
        )

    # ── Monitoring / exit / adjustment ──────────────────────────────────

    def _current_leg_ltp(self, symbol: str) -> float:
        return self._ltp_cache.get(symbol, 0.0)

    async def _monitor_active_trade(self) -> None:
        t = self._active_trade
        if not t:
            return

        front_ltp = self._current_leg_ltp(t["front_symbol"]) or await self.broker.get_ltp(t["front_symbol"])
        back_ltp  = self._current_leg_ltp(t["back_symbol"])  or await self.broker.get_ltp(t["back_symbol"])
        if front_ltp <= 0 or back_ltp <= 0:
            return  # no reliable pricing yet this cycle

        current_spread_value = back_ltp - front_ltp
        unrealised = round((current_spread_value - t["net_debit"]) * t["quantity"], 2)
        self._unrealised_pnl = unrealised
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)

        # ── 1. Stop loss: 20% of capital employed ───────────────────────
        sl_threshold = -abs(self.cfg.sl_pct_of_capital * t["capital_employed"])
        if unrealised <= sl_threshold:
            await self._close_active_trade("STOP_LOSS")
            return

        # ── 2. Forced pre-expiry exit ────────────────────────────────────
        front_expiry = date.fromisoformat(t["front_expiry"])
        force_exit_date = front_expiry - timedelta(days=self.cfg.forced_exit_days_before_expiry)
        if date.today() >= force_exit_date:
            await self._close_active_trade("PRE_EXPIRY_GAMMA_EXIT")
            return

        # ── 3. Adjustment: roll if approaching approximate breakeven ────
        strike = t["short_strike"]
        breakeven_dist = self.cfg.breakeven_width_multiplier * t["net_debit"]
        upper_be = strike + breakeven_dist
        lower_be = strike - breakeven_dist
        spot = self._current_spot
        if spot and breakeven_dist > 0:
            dist_to_upper = (upper_be - spot) / breakeven_dist
            dist_to_lower = (spot - lower_be) / breakeven_dist
            nearest_frac_travelled = 1.0 - min(max(dist_to_upper, 0.0), max(dist_to_lower, 0.0))
            if nearest_frac_travelled >= self.cfg.adjustment_trigger_pct:
                self._signal(
                    f"ADJUSTMENT TRIGGERED | spot={spot:.1f} approaching approx breakeven "
                    f"(upper={upper_be:.0f} lower={lower_be:.0f}) — rolling calendar"
                )
                await self._roll_calendar()

    async def _roll_calendar(self) -> None:
        async with self._closing_lock:
            if not self._active_trade:
                return
            old_front_expiry = self._active_trade["front_expiry"]
            old_back_expiry = self._active_trade["back_expiry"]
            await self._close_active_trade("ADJUSTMENT_ROLL", allow_reopen=True)

        # Re-open centered at current spot, same expiry pair, bypassing the
        # IV/DTE entry filter (this is a defensive re-center, not a fresh
        # discretionary entry) -- still respects strategy_filter/risk_manager.
        spot = self._current_spot or await self.broker.get_ltp(self.cfg.nifty_instrument_key)
        if not spot:
            logger.error(f"[{self.name}] Roll aborted — no spot price available to re-center")
            return
        new_strike = round_to_strike(spot, int(self.cfg.strike_interval))
        front_expiry = date.fromisoformat(old_front_expiry)
        back_expiry = date.fromisoformat(old_back_expiry)
        if front_expiry <= date.today():
            logger.info(f"[{self.name}] Front expiry too close after roll — not reopening")
            return

        try:
            front_chain = await self.broker.get_option_chain(
                self.cfg.nifty_instrument_key, front_expiry.strftime("%Y-%m-%d")
            )
            back_chain = await self.broker.get_option_chain(
                self.cfg.nifty_instrument_key, back_expiry.strftime("%Y-%m-%d")
            )
        except Exception as e:
            logger.error(f"[{self.name}] Roll: chain fetch failed: {e}")
            return

        front_leg = self._atm_leg(front_chain, new_strike)
        back_leg = self._atm_leg(back_chain, new_strike)
        if not front_leg or not back_leg:
            logger.error(f"[{self.name}] Roll: no liquid ATM PE found near {new_strike} — not reopening")
            return

        sf_ok, _ = strategy_filter.can_trade(self.name)
        rm_ok, _ = risk_manager.can_trade(self.name, "SELL")
        if not (sf_ok and rm_ok):
            logger.warning(f"[{self.name}] Roll: gated by filter/risk manager — not reopening this cycle")
            return

        await self._place_calendar(new_strike, front_expiry, back_expiry, front_leg, back_leg, forced=True)

    async def _close_active_trade(self, reason: str, allow_reopen: bool = False) -> None:
        if not allow_reopen:
            async with self._closing_lock:
                await self._do_close(reason)
        else:
            await self._do_close(reason)

    async def _do_close(self, reason: str) -> None:
        t = self._active_trade
        if not t:
            return

        front_symbol, back_symbol = t["front_symbol"], t["back_symbol"]
        quantity = t["quantity"]

        try:
            if self._is_paper:
                front_close_price = self._current_leg_ltp(front_symbol) or await self.broker.get_ltp(front_symbol)
                back_close_price  = self._current_leg_ltp(back_symbol)  or await self.broker.get_ltp(back_symbol)
                self._signal(f"[PAPER] BUY-TO-CLOSE {quantity} {front_symbol} @ {front_close_price} ({reason})")
                self._signal(f"[PAPER] SELL-TO-CLOSE {quantity} {back_symbol} @ {back_close_price} ({reason})")
            else:
                front_resp = await self.broker.place_order(Order(
                    symbol=front_symbol, exchange="NFO", order_type="BUY",
                    quantity=quantity, product="I", price=0.0,
                    tag=f"PCAL_CLOSE_FRONT_{int(time.time()*1000) % 100000}",
                ))
                front_close_price = await self.broker.get_ltp(front_symbol)
                back_resp = await self.broker.place_order(Order(
                    symbol=back_symbol, exchange="NFO", order_type="SELL",
                    quantity=quantity, product="I", price=0.0,
                    tag=f"PCAL_CLOSE_BACK_{int(time.time()*1000) % 100000}",
                ))
                back_close_price = await self.broker.get_ltp(back_symbol)
                self.broker.unsubscribe_ticks([front_symbol, back_symbol], callback=self._on_tick_sync)
        except Exception as e:
            logger.error(f"[{self.name}] Close failed for {reason}: {e}")
            return

        realised = round(
            ((t["front_entry"] - front_close_price) + (back_close_price - t["back_entry"])) * quantity,
            2,
        )
        self._realised_pnl += realised
        self._closed_trades += 1
        self._update_pnl(self._realised_pnl, 0.0)

        try:
            now_iso = datetime.now(IST).isoformat()
            trade_logger.close_trade(
                trade_id=t["front_trade_id"], exit_price=front_close_price,
                exit_time=now_iso, realised_pnl=round((t["front_entry"] - front_close_price) * quantity, 2),
                notes=reason,
            )
            trade_logger.close_trade(
                trade_id=t["back_trade_id"], exit_price=back_close_price,
                exit_time=now_iso, realised_pnl=round((back_close_price - t["back_entry"]) * quantity, 2),
                notes=reason,
            )
        except Exception as e:
            logger.error(f"[{self.name}] trade_logger.close_trade failed: {e}")

        risk_manager.release_trade(self.name, "SELL", multiplier=1)
        risk_manager.release_capital(self.name, "BUY", multiplier=1)

        self._signal(
            f"CALENDAR CLOSED ({reason}) | {front_symbol}/{back_symbol} | "
            f"Realised P&L: ₹{realised:,.2f}"
        )
        self._active_trade = {}
        self._update_position("FLAT")

    # ── Config ───────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        return vars(self.cfg)