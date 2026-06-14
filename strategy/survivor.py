from core.strategy_filter import strategy_filter
# strategy/survivor.py
import asyncio
import logging
import os
from dataclasses import dataclass

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.event_bus import EventType
from core.risk_manager import risk_manager
from core.auto_config import fetch_instruments, find_symbol_from_instruments
from core.state_store import Direction, state_store
from core.trade_log import trade_logger
from core.vix_manager import vix_manager
from core.alerting import (
    alert_trade_opened, alert_trade_closed, alert_order_rejected,
    alert_gtt_failed, alert_daily_loss_hit, alert_reconcile_mismatch,
    alert_system_start, alert_eod_close, alert_breakeven_locked
)
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class SurvivorConfig:
    symbol_initials:      str   = "NIFTY13APR26"
    pe_gap:               float = 15.0
    ce_gap:               float = 15.0
    pe_symbol_gap:        float = 300.0
    ce_symbol_gap:        float = 300.0
    pe_reset_gap:         float = 90.0
    ce_reset_gap:         float = 90.0
    pe_quantity:          int   = 65
    ce_quantity:          int   = 65
    pe_start:             float = 0.0
    ce_start:             float = 0.0
    min_price_to_sell:    float = 30.0
    nifty_instrument_key: str   = "NSE_INDEX|Nifty 50"
    strike_interval:      float = 50.0
    # Instrument identity fields — set these for BankNifty
    instrument_name:      str   = "NIFTY"               # "NIFTY" or "BANKNIFTY"
    index_instrument_key: str   = "NSE_INDEX|Nifty 50"  # "NSE_INDEX|Nifty Bank" for BankNifty
    lot_size:             int   = 65                     # 15 for BankNifty
    paper_trade_override: bool  = False                  # force paper mode for this instance
    strategy_name:        str   = "survivor"             # override for BankNifty: "bn_survivor" 


class SurvivorAlgo(BaseStrategy):

    def __init__(self, broker: AbstractBrokerGateway, config: SurvivorConfig):
        super().__init__(name=config.strategy_name, broker=broker, config=vars(config))
        self.cfg               = config
        self._loop = None
        self._pe_last_value    = config.pe_start
        self._ce_last_value    = config.ce_start
        self._pe_sold_flag     = False
        self._ce_sold_flag     = False
        self._open_trade_ids   = []
        self._open_trades_data = []
        self._realised_pnl     = 0.0
        self._unrealised_pnl   = 0.0
        self._last_nifty_price = 0.0
        self._closed_trades    = 0
        self._ltp_cache        = {}  # symbol -> latest LTP
        self._instruments      = []  # cached instruments list
        self._ikey_cache       = {}  # text symbol -> instrument key
        # Time-based trigger state (reset each day by risk_manager daily reset)
        self._time_based_pe_fired = False  # True after time trigger sold PE today
        self._time_based_ce_fired = False  # True after time trigger sold CE today
        self._last_time_trigger_day = -1   # day-of-month when flags were last reset
        # Idempotent order gate — prevents duplicate orders on same signal
        self._pending_orders: set = set()  # keys of in-flight orders
        # Exit precedence gate — only one exit path can close a trade at a time
        self._closing_trades: set = set()  # trade IDs currently being closed
        # Exit precedence gate — only one exit path can close a trade at a time
        self._closing_trades: set = set()  # trade IDs currently being closed

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        self._loop = asyncio.get_running_loop()

        # Use index_instrument_key (supports both NIFTY and BANKNIFTY)
        _index_key = self.cfg.index_instrument_key or self.cfg.nifty_instrument_key
        nifty_price = await self.broker.get_ltp(_index_key)
        if nifty_price == 0.0:
            raise RuntimeError(f"[survivor] Could not fetch {self.cfg.instrument_name} price on startup.")

        if self.cfg.pe_start == 0.0:
            self.cfg.pe_start = nifty_price
        if self.cfg.ce_start == 0.0:
            self.cfg.ce_start = nifty_price

        self._pe_last_value = self.cfg.pe_start
        self._ce_last_value = self.cfg.ce_start

        _index_key = self.cfg.index_instrument_key or self.cfg.nifty_instrument_key
        self.broker.subscribe_ticks(
            symbols=[_index_key],
            callback=self._on_tick_sync
        )
        asyncio.create_task(self._refresh_ltp_loop())
        asyncio.create_task(self._auto_stop_watchdog())
        logger.info("[survivor] LTP refresh loop started")
        logger.info("[survivor] Auto-stop watchdog started")
        await asyncio.sleep(5)  # Wait for WebSocket
        await self._reload_open_trades()

        logger.info(
            f"[survivor] PE Anchor: {self._pe_last_value} | "
            f"CE Anchor: {self._ce_last_value}"
        )
        self._signal(
            f"Started | Spot: {nifty_price:.2f} | "
            f"PE Anchor: {self._pe_last_value:.2f} | "
            f"CE Anchor: {self._ce_last_value:.2f}"
        )

    async def on_stop(self) -> None:
        import pytz
        from datetime import datetime as _dt
        now = _dt.now(pytz.timezone("Asia/Kolkata"))
        is_eod = (now.hour > 15) or (now.hour == 15 and now.minute >= 5)
        if is_eod:
            logger.info("[survivor] on_stop: EOD — closing all positions")
            await self._close_all_positions()
        else:
            logger.info(
                f"[survivor] on_stop: {now.strftime('%H:%M')} IST — "
                f"NOT EOD, skipping close. Positions reload on next start."
            )
        _index_key = self.cfg.index_instrument_key or self.cfg.nifty_instrument_key
        self.broker.unsubscribe_ticks([_index_key])

    # ── Tick Handling ─────────────────────────────────────────────────────────

    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return
        # Option ticks have low prices; index ticks have high prices
        # Use 5000 as threshold to separate options from index for both NIFTY and BANKNIFTY
        if tick.last_price < 5000:
            self._ltp_cache[tick.symbol] = tick.last_price
            self._calculate_pnl()
            return
        logger.info(f"[survivor] Nifty tick: {tick.last_price:.2f} | PE anchor: {self._pe_last_value:.2f} | diff: {tick.last_price - self._pe_last_value:.2f}")
        self._last_nifty_price = tick.last_price
        loop = self._loop
        if loop is None or not loop.is_running():
            import asyncio as _asyncio
            try:
                loop = _asyncio.get_running_loop()
            except Exception:
                pass
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), loop)
        else:
            logger.error(f"[survivor] No loop available")

    async def on_tick(self, tick: Tick) -> None:
        try:
            if self._stop_flag or not self.is_market_open():
                return

            if risk_manager.check_auto_stop():
                self._signal("Auto-stop triggered (3:10 PM) — closing all positions")
                await self._close_all_positions()
                await self.stop(reason="AUTO_STOP")
                return

            if risk_manager.check_max_daily_loss():
                self._signal("Max daily loss hit — closing all positions")
                await self._close_all_positions()
                await self.stop(reason="MAX_DAILY_LOSS")
                return

            nifty_price            = tick.last_price
            self._last_nifty_price = nifty_price

            # Use session planner as single source of truth for gap params.
            # VIX manager only used for halt_trading safety check.
            vix_params = vix_manager.get_params()
            if vix_params.get("halt_trading", False):
                self._signal("VIX EXTREME — trading halted by vix_manager")
                return

            try:
                from core.session_planner import session_planner as _sp
                sp_params = dict(_sp.current_plan.params.__dict__)
            except Exception:
                sp_params = {}

            current_pe_gap = sp_params.get("pe_gap", self.cfg.pe_gap)
            current_ce_gap = sp_params.get("ce_gap", self.cfg.ce_gap)
            pe_symbol_gap  = sp_params.get("pe_symbol_gap", self.cfg.pe_symbol_gap)
            ce_symbol_gap  = sp_params.get("ce_symbol_gap", self.cfg.ce_symbol_gap)

            # Monitor open trades for SL and trailing profit
            await self._monitor_open_trades(nifty_price)
            self._calculate_pnl(nifty_price)

            can_trade, reason = risk_manager.can_trade(self.name)
            if can_trade:
                sf_ok, sf_reason = strategy_filter.can_trade("survivor")
                if not sf_ok:
                    can_trade = False
                    reason = f"[context] {sf_reason}"

            if can_trade and len(self._open_trades_data) >= 2:
                can_trade = False
                reason = "Max open trades reached (2)"
            if can_trade:
                # ── TRIGGER 1: Movement-based ─────────────────────────────
                # PE SELL — Nifty moved up enough from last PE anchor
                if nifty_price - self._pe_last_value >= current_pe_gap:
                    await self._sell_option(
                        direction="PE",
                        nifty_price=nifty_price,
                        gap=pe_symbol_gap,
                        quantity=self.cfg.pe_quantity,
                    )
                    self._pe_last_value = nifty_price
                    self._pe_sold_flag  = True
                    self._time_based_pe_fired = True  # block time trigger same side
                    self._update_position(Direction.SHORT)

                # CE SELL — Nifty moved down enough from last CE anchor
                if self._ce_last_value - nifty_price >= current_ce_gap:
                    await self._sell_option(
                        direction="CE",
                        nifty_price=nifty_price,
                        gap=ce_symbol_gap,
                        quantity=self.cfg.ce_quantity,
                    )
                    self._ce_last_value = nifty_price
                    self._ce_sold_flag  = True
                    self._time_based_ce_fired = True  # block time trigger same side
                    self._update_position(Direction.SHORT)

                # ── TRIGGER 2: Time-based (flat market catcher) ───────────
                # Fires once per side per session in 9:45–11:30 AM window
                # when market is flat but PCR confirms direction is safe
                await self._check_time_based_trigger(
                    nifty_price, pe_symbol_gap, ce_symbol_gap
                )

            else:
                self._signal(f"Trade blocked: {reason}")

            # PE Reset — market reversed down after a PE sell
            if self._pe_sold_flag and (self._pe_last_value - nifty_price >= self.cfg.pe_reset_gap):
                self._pe_last_value = nifty_price
                self._pe_sold_flag  = False
                self._signal(f"PE anchor reset at {nifty_price:.2f}")

            # CE Reset — market reversed up after a CE sell
            if self._ce_sold_flag and (nifty_price - self._ce_last_value >= self.cfg.ce_reset_gap):
                self._ce_last_value = nifty_price
                self._ce_sold_flag  = False
                self._signal(f"CE anchor reset at {nifty_price:.2f}")

        except Exception as e:
            logger.exception(f"[survivor] ERROR in on_tick: {e}")

    # ── Option Selling ────────────────────────────────────────────────────────

    async def _sell_option(
        self,
        direction:   str,
        nifty_price: float,
        gap:         float,
        quantity:    int,
    ) -> None:
        interval     = self.cfg.strike_interval
        base_strike  = nifty_price - gap if direction == "PE" else nifty_price + gap
        strike       = round(base_strike / interval) * interval
        symbol       = None
        final_strike = strike

        # Search up to 5 strikes for one that meets min premium
        for _ in range(5):
            candidate = self._build_symbol(direction, final_strike)
            _is_paper = (
                os.getenv("PAPER_TRADE", "false").lower() == "true"
                or self.cfg.paper_trade_override
            )
            if _is_paper:
                # Simulate premium in paper mode
                premium = max(5.0, 50.0 - abs(nifty_price - final_strike) * 0.1)
            else:
                # Resolve real instrument key before LTP fetch
                ikey = await self._get_instrument_key(candidate, direction, final_strike)
                premium = await self.broker.get_ltp(ikey)

            if premium >= self.cfg.min_price_to_sell:
                symbol = ikey  # Use resolved ikey for order
                break
            final_strike += interval if direction == "PE" else -interval

        if not symbol:
            logger.warning(
                f"[survivor] No {direction} strike found above "
                f"₹{self.cfg.min_price_to_sell} — skipping"
            )
            return

        # ── Idempotent gate ───────────────────────────────────────────────
        # Unique key = direction + strike + date + minute
        # Prevents duplicate orders if same signal fires twice in same minute
        import pytz as _pytz
        from datetime import datetime as _dt
        _now = _dt.now(_pytz.timezone("Asia/Kolkata"))
        _order_key = f"{direction}_{int(final_strike)}_{_now.strftime('%Y%m%d_%H%M')}"
        if _order_key in self._pending_orders:
            logger.warning(
                f"[survivor] DUPLICATE ORDER BLOCKED | key={_order_key} | "
                f"already in flight this minute"
            )
            self._signal(f"⚠ Duplicate {direction} order blocked — same signal already placed this minute")
            return
        self._pending_orders.add(_order_key)
        # Auto-clear old keys (keep only today's)
        today_prefix = _now.strftime('%Y%m%d')
        self._pending_orders = {k for k in self._pending_orders if today_prefix in k}

        try:
            if _is_paper:
                sell_price  = round(premium * 0.98, 1)
                entry_price = sell_price
                self._signal(f"[PAPER] SELL {quantity} {symbol} @ {sell_price} (simulated)")
                order_id = "PAPER_SELL"
            else:
                ltp        = await self.broker.get_ltp(symbol)
                sell_price = round(ltp * 0.98, 1) if ltp > 0 else 0.0
                resp       = await self.broker.place_order(Order(
                    symbol=symbol,
                    exchange="NFO",
                    order_type="SELL",
                    quantity=quantity,
                    product="I",
                    price=sell_price,
                ))
                if resp.status == "REJECTED":
                    self._signal(f"{direction} order REJECTED: {resp.message}")
                    self._pending_orders.discard(_order_key)  # allow retry on rejection
                    alert_order_rejected(symbol, resp.message)
                    return
                entry_price = await self.broker.get_ltp(symbol)
                order_id    = resp.order_id

            trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=symbol,
                order_type="SELL",
                quantity=quantity,
                entry_price=entry_price,
                broker_order_id=order_id,
                notes=f"VIX Regime Trigger | Nifty @ {nifty_price:.2f}",
            )

            self._open_trade_ids.append(trade_id)
            self._open_trades_data.append({
                "id":         trade_id,
                "order_type": "SELL",
                "entry":      entry_price,
                "symbol":     symbol,
                "quantity":   quantity,
                "direction":  direction,
            })

            # Subscribe to live ticks using instrument key
            ikey = await self._get_instrument_key(symbol, direction, final_strike)
            self.broker.subscribe_ticks(
                symbols=[ikey],
                callback=self._on_tick_sync
            )
            self._ikey_cache[symbol] = ikey

            risk_manager.register_trade(self.name, "SELL")
            self._signal(
                f"SOLD {direction} {int(final_strike)} @ ₹{entry_price:.2f} | "
                f"Order: {order_id}"
            )
            alert_trade_opened(symbol, direction, entry_price, quantity, int(final_strike))

            # Place GTT Trailing SL immediately after live trade opens
            _is_paper = (
                os.getenv("PAPER_TRADE", "false").lower() == "true"
                or self.cfg.paper_trade_override
            )
            if not _is_paper and hasattr(self.broker, 'place_gtt_trailing_sl'):
                try:
                    gtt_id = await self.broker.place_gtt_trailing_sl(
                        instrument_key=ikey,
                        quantity=quantity,
                        entry_price=entry_price,
                        order_type="SELL",
                        trailing_gap=0.25,
                        sl_pct=0.15,
                    )
                    if gtt_id:
                        self._signal(f"🛡 GTT Trailing SL placed | id={gtt_id} | trigger={round(entry_price*1.15,1)}")
                    else:
                        alert_gtt_failed(symbol, "GTT returned empty ID")
                except Exception as ge:
                    logger.warning(f"[survivor] GTT placement failed: {ge}")
                    alert_gtt_failed(symbol, str(ge))

        except Exception as e:
            logger.error(f"[survivor] _sell_option failed for {direction}: {e}")

    async def _get_instrument_key(self, symbol: str, direction: str, strike: float) -> str:
        """Lookup instrument key for a text symbol for WebSocket subscription."""
        if symbol in self._ikey_cache:
            return self._ikey_cache[symbol]
        try:
            from datetime import datetime as dt, date, timedelta
            import pytz
            if not self._instruments:
                self._instruments = fetch_instruments()
            now = dt.now(pytz.timezone("Asia/Kolkata"))
            # Dynamically calculate the next upcoming Tuesday expiry
            days_ahead = (1 - now.weekday()) % 7
            if days_ahead == 0:
                expiry = now.date()
            else:
                expiry = (now + timedelta(days=days_ahead)).date()
            # Ensure strike is reasonable (not full symbol number)
            clean_strike = int(strike) if strike < 100000 else int(str(int(strike))[-5:])
            ikey = find_symbol_from_instruments(
                self._instruments, expiry, clean_strike, direction
            )
            if ikey:
                self._ikey_cache[symbol] = ikey
                logger.info(f"[survivor] instrument key found: {symbol} -> {ikey}")
                return ikey
        except Exception as e:
            logger.warning(f"[survivor] instrument key lookup failed: {e}")
        logger.warning(f"[survivor] using text fallback for {symbol}")
        return symbol  # fallback to text symbol

    def _build_symbol(self, option_type: str, strike: float) -> str:
        return f"NSE_FO|{self.cfg.symbol_initials}{int(strike):05d}{option_type}"

    async def _reload_open_trades(self) -> None:
        """
        On startup, reload OPEN trades from DB and reconcile against
        broker positions. Closes DB trades that no longer exist on broker.
        """
        try:
            open_trades = trade_logger.get_trades(strategy=self.name, status="OPEN")
            if not open_trades:
                logger.info("[survivor] No open trades found in database to reload.")
                return

            # Fetch live broker positions for reconciliation
            broker_symbols = set()
            _is_paper = (
                os.getenv("PAPER_TRADE", "false").lower() == "true"
                or self.cfg.paper_trade_override
            )
            if not _is_paper:
                try:
                    import upstox_client
                    cfg = upstox_client.Configuration()
                    cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
                    client = upstox_client.ApiClient(cfg)
                    api = upstox_client.PortfolioApi(client)
                    resp = api.get_short_term_positions(api_version="2.0")
                    if resp and resp.data:
                        for pos in resp.data:
                            if pos.quantity != 0:
                                broker_symbols.add(pos.instrument_token)
                    logger.info(f"[survivor] Broker positions on startup: {len(broker_symbols)} open")
                except Exception as be:
                    logger.warning(f"[survivor] Could not fetch broker positions: {be} — using DB only")

            reloaded = 0
            for t in open_trades:
                if any(x["id"] == t["id"] for x in self._open_trades_data):
                    continue

                # If broker data available and symbol not in broker — close in DB
                if broker_symbols and t["symbol"] not in broker_symbols:
                    logger.warning(
                        f"[survivor] RECONCILE: DB trade {t['id'][:8]} ({t['symbol']}) "
                        f"not found in broker — marking CLOSED"
                    )
                    trade_logger.close_trade(
                        t["id"], t["entry_price"],
                        "RECONCILE_CLOSE — not in broker positions on startup"
                    )
                    alert_reconcile_mismatch(t["id"], t["symbol"])
                    continue

                direction = "CE" if t["symbol"].endswith("CE") else "PE"
                self._open_trades_data.append({
                    "id":         t["id"],
                    "order_type": t["order_type"],
                    "entry":      t["entry_price"],
                    "symbol":     t["symbol"],
                    "quantity":   t["quantity"],
                    "direction":  direction,
                })
                self._open_trade_ids.append(t["id"])
                try:
                    sym_name = t["symbol"].split("|")[-1]
                    strike = float(''.join(filter(str.isdigit, sym_name))[-5:])
                    ikey = await self._get_instrument_key(t["symbol"], direction, strike)
                    self.broker.subscribe_ticks(
                        symbols=[ikey],
                        callback=self._on_tick_sync
                    )
                    self._ikey_cache[t["symbol"]] = ikey
                    logger.info(f"[survivor] Subscribed to ticks for reloaded trade: {ikey}")
                except Exception as sub_e:
                    logger.error(f"[survivor] Could not subscribe for reloaded trade: {sub_e}")
                reloaded += 1

            if reloaded > 0:
                logger.info(f"[survivor] Reloaded {reloaded} open trade(s) after reconciliation.")
                self._signal(f"Reloaded {reloaded} open trade(s) from previous session.")
            else:
                logger.info("[survivor] No open trades to reload after reconciliation.")
        except Exception as e:
            logger.error(f"[survivor] Failed to reload open trades: {e}")


    async def _auto_stop_watchdog(self) -> None:
        logger.info("[survivor] Auto-stop watchdog started")
        while not self._stop_flag:
            await asyncio.sleep(30)
            try:
                from core.risk_manager import risk_manager
                if risk_manager.check_auto_stop():
                    logger.warning("[survivor] WATCHDOG: EOD auto-stop triggered")
                    self._signal("WATCHDOG: EOD 3:05 PM — closing all positions")
                    await self._close_all_positions()
                    await self.stop(reason="AUTO_STOP_WATCHDOG")
                    return
            except Exception as e:
                logger.error(f"[survivor] Watchdog error: {e}")

    async def _refresh_ltp_loop(self) -> None:
        """Background task: update P&L every 3 seconds using WebSocket LTP cache.
        Also acts as independent auto-stop watchdog."""
        from core.risk_manager import risk_manager
        while not self._stop_flag:
            try:
                if risk_manager.check_auto_stop():
                    logger.warning("[survivor] WATCHDOG: EOD auto-stop 3:05 PM")
                    self._signal("EOD auto-stop 3:05 PM [WATCHDOG]")
                    await self._close_all_positions()
                    await self.stop(reason="AUTO_STOP")
                    return
                if risk_manager.check_max_daily_loss():
                    logger.warning("[survivor] WATCHDOG: Max daily loss hit")
                    self._signal("Max daily loss [WATCHDOG]")
                    await self._close_all_positions()
                    await self.stop(reason="MAX_DAILY_LOSS")
                    return
                for trade in list(self._open_trades_data):
                    symbol = trade["symbol"]
                    ikey = self._ikey_cache.get(symbol, symbol)
                    # Check both symbol and ikey in cache
                    cached = self._ltp_cache.get(ikey, self._ltp_cache.get(symbol, 0.0))
                    if cached == 0.0:
                        # Fallback: fetch via REST API (throttled — max once per 30s per symbol)
                        now_ts = __import__('time').time()
                        last_fetch_key = f"_last_rest_fetch_{symbol}"
                        last_fetch = getattr(self, last_fetch_key, 0)
                        if now_ts - last_fetch > 30:
                            try:
                                ltp = await self.broker.get_ltp(ikey)
                                if ltp > 0:
                                    self._ltp_cache[ikey] = ltp
                                    self._ltp_cache[symbol] = ltp
                                    logger.info(f"[survivor] REST fallback LTP: {ikey} = {ltp}")
                                setattr(self, last_fetch_key, now_ts)
                            except Exception as fe:
                                logger.debug(f"[survivor] REST fallback failed: {fe}")
                                self._ltp_cache[symbol] = trade["entry"]
                        else:
                            self._ltp_cache[symbol] = trade["entry"]
            except Exception as e:
                logger.debug(f"[survivor] _refresh_ltp_loop error: {e}")
            await asyncio.sleep(3)

    # ── Trade Monitoring (SL + Breakeven Lock + Profit Target) ───────────────

    async def _monitor_open_trades(self, nifty_price: float = 0.0) -> None:
        if not self._open_trades_data:
            return

        import time
        now = time.time()
        if not hasattr(self, '_last_monitor_ts'):
            self._last_monitor_ts = 0
        if now - self._last_monitor_ts < 1.0:
            return
        self._last_monitor_ts = now

        for trade in list(self._open_trades_data):
            try:
                is_paper = (
                    os.getenv("PAPER_TRADE", "false").lower() == "true"
                    or self.cfg.paper_trade_override
                )
                if is_paper:
                    # Simulate slight option decay for paper trades
                    curr_price = trade["entry"] * 0.95
                else:
                    # Use WebSocket tick cache instead of API call to avoid 429 errors
                    ikey = self._ikey_cache.get(trade["symbol"], trade["symbol"])
                    curr_price = self._ltp_cache.get(ikey, self._ltp_cache.get(trade["symbol"], 0.0))
                    if curr_price == 0.0:
                        # Fallback to API only if cache empty
                        curr_price = await self.broker.get_ltp(trade["symbol"])

                if curr_price == 0:
                    continue

                # ── Calculate current P&L ─────────────────────────────────
                if trade["order_type"] == "SELL":
                    curr_pnl = (trade["entry"] - curr_price) * trade["quantity"]
                else:
                    curr_pnl = (curr_price - trade["entry"]) * trade["quantity"]

                # ── Breakeven Lock ────────────────────────────────────────
                # Once profit reaches ₹400, lock SL at entry price (breakeven).
                # This means the trade can never turn into a loss after this point.
                if curr_pnl >= 400.0 and not trade.get("_be_locked"):
                    trade["_be_locked"] = True
                    trade["_sl_floor"] = trade["entry"]
                    self._signal(
                        f"🔒 BREAKEVEN LOCKED | {trade['symbol']} | "
                        f"P&L: ₹{curr_pnl:.0f} — SL moved to entry ₹{trade['entry']:.2f}"
                    )
                    alert_breakeven_locked(trade['symbol'], curr_pnl)

                # ── Check breakeven SL (if locked) ───────────────────────
                if trade.get("_be_locked"):
                    sl_floor = trade["_sl_floor"]
                    # For SELL: option price rising above entry = loss territory
                    breached = (
                        curr_price >= sl_floor
                        if trade["order_type"] == "SELL"
                        else curr_price <= sl_floor
                    )
                    if breached:
                        self._signal(
                            f"🔒 BREAKEVEN SL HIT | {trade['symbol']} | "
                            f"Entry: {trade['entry']:.2f} | "
                            f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                        )
                        await self._close_trade(trade, "SL_HIT", curr_price)
                        continue  # trade closed, move to next

                # ── Normal stop loss (no breakeven lock yet) ──────────────
                elif risk_manager.check_trade_stop_loss(
                    trade["entry"], curr_price, trade["quantity"], trade["order_type"]
                ):
                    self._signal(
                        f"🛑 STOP LOSS hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "SL_HIT", curr_price)
                    continue  # trade closed, move to next

                # ── Profit target ─────────────────────────────────────────
                if risk_manager.check_trailing_profit(
                    trade["entry"], curr_price, trade["order_type"], trade["quantity"]
                ):
                    self._signal(
                        f"✅ PROFIT TARGET hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "TP_HIT", curr_price)

            except Exception as e:
                logger.error(f"[survivor] Monitor error for {trade['symbol']}: {e}")

    # ── Trade Exit ────────────────────────────────────────────────────────────

    async def _close_trade(
        self, trade: dict, reason: str, current_price: float = 0.0
    ) -> None:
        if trade not in self._open_trades_data:
            return
        # Exit precedence gate — prevent double-close from SL + GTT + EOD firing simultaneously
        trade_id = trade.get("id", "")
        if trade_id in self._closing_trades:
            logger.warning(
                f"[survivor] DOUBLE-CLOSE BLOCKED | {trade.get('symbol')} | "
                f"reason={reason} | already being closed"
            )
            return
        self._closing_trades.add(trade_id)
        # Exit precedence gate — prevent double-close from SL + GTT + EOD firing simultaneously
        trade_id = trade.get("id", "")
        if trade_id in self._closing_trades:
            logger.warning(
                f"[survivor] DOUBLE-CLOSE BLOCKED | {trade.get('symbol')} | "
                f"reason={reason} | already being closed"
            )
            return
        self._closing_trades.add(trade_id)

        exit_order_type = "BUY" if trade["order_type"] == "SELL" else "SELL"

        _close_paper = (
            os.getenv("PAPER_TRADE", "false").lower() == "true"
            or self.cfg.paper_trade_override
        )
        if _close_paper:
            if current_price == 0.0:
                current_price = trade["entry"] * 0.95
            exit_price = round(current_price * 1.02, 1)
            self._signal(
                f"[PAPER] {exit_order_type} {trade['quantity']} "
                f"{trade['symbol']} @ {exit_price} (simulated)"
            )
            order_id = "PAPER_EXIT"
        else:
            if current_price == 0.0:
                current_price = await self.broker.get_ltp(trade["symbol"])
            if current_price == 0.0:
                logger.error(f"[survivor] get_ltp returned 0 for {trade['symbol']} — aborting close to prevent wrong P&L")
                self._signal(f"⚠ EXIT ABORTED: could not get LTP for {trade['symbol']}")
                return
            if exit_order_type == "BUY":
                exit_price = round(current_price * 1.02, 1)
            else:
                exit_price = round(current_price * 0.98, 1)
            try:
                resp = await self.broker.place_order(Order(
                    symbol=trade["symbol"],
                    exchange="NFO",
                    order_type=exit_order_type,
                    quantity=trade["quantity"],
                    product="I",
                    price=exit_price,
                ))
                if resp.status == "REJECTED":
                    self._signal(
                        f"Exit order REJECTED for {trade['symbol']}: {resp.message}"
                    )
                    return
                order_id = resp.order_id
            except Exception as e:
                logger.error(f"[survivor] _close_trade failed for {trade['symbol']}: {e}")
                return

        # Calculate P&L
        pnl = (trade["entry"] - exit_price) * trade["quantity"]  # SELL trade profit
        self._realised_pnl += pnl

        # Remove from open trades
        self._open_trades_data = [
            t for t in self._open_trades_data if t["id"] != trade["id"]
        ]
        if trade["id"] in self._open_trade_ids:
            self._open_trade_ids.remove(trade["id"])
        # Clear closing flag
        self._closing_trades.discard(trade["id"])
        # Clear closing flag
        self._closing_trades.discard(trade["id"])

        self._closed_trades += 1

        # Release capital back to risk manager
        risk_manager.release_trade(self.name, trade["order_type"])

        trade_logger.close_trade(trade["id"], exit_price, reason)
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
        self._signal(
            f"CLOSED {trade['symbol']} | Reason: {reason} | "
            f"P&L: ₹{pnl:.2f} | Order: {order_id}"
        )
        alert_trade_closed(trade['symbol'], trade['entry'], exit_price, trade['quantity'], pnl, reason)

    async def _close_all_positions(self) -> None:
        if not self._open_trades_data:
            return
        self._signal(f"Closing all {len(self._open_trades_data)} open trade(s)...")
        for trade in list(self._open_trades_data):
            await self._close_trade(trade, "EOD")
        self._unrealised_pnl = 0.0
        self._update_pnl(self._realised_pnl, 0.0)
        # EOD summary alert
        try:
            from core.alerting import alert_eod_close
            import pytz
            from datetime import datetime
            today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
            trades = trade_logger.get_trades(status="CLOSED", limit=100)
            today_trades = [t for t in trades if t.get("exit_time","").startswith(today)]
            total_pnl = sum(t.get("realised_pnl", 0) for t in today_trades)
            alert_eod_close(total_pnl, len(today_trades))
        except Exception:
            pass

    # ── P&L Calculation ───────────────────────────────────────────────────────

    def _calculate_pnl(self, nifty_price: float = 0.0) -> None:
        unrealised = 0.0
        for trade in self._open_trades_data:
            entry  = trade["entry"]
            qty    = trade["quantity"]
            symbol = trade["symbol"]
            try:
                ikey = self._ikey_cache.get(symbol, symbol)
                # Check both ikey and symbol in cache
                curr = self._ltp_cache.get(ikey, self._ltp_cache.get(symbol, 0.0))
                # If still 0, do NOT use entry as fallback — use 0 so dashboard shows stale
                # The REST fallback in _refresh_ltp_loop will populate cache every 30s
            except Exception:
                curr = 0.0
            if curr == 0.0:
                curr = 0.0  # leave as 0 — REST fallback will fix within 30s
            if trade["order_type"] == "SELL":
                unrealised += (entry - curr) * qty if curr > 0 else 0.0
            else:
                unrealised += (curr - entry) * qty if curr > 0 else 0.0
        self._unrealised_pnl = round(unrealised, 2)
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
        # Update live P&L registry for dashboard
        try:
            from dashboard.api import pnl_registry, ltp_registry
            for trade in self._open_trades_data:
                entry = trade["entry"]
                qty   = trade["quantity"]
                ikey  = self._ikey_cache.get(trade["symbol"], trade["symbol"])
                curr  = self._ltp_cache.get(ikey, self._ltp_cache.get(trade["symbol"], 0.0))
                if curr > 0:
                    pnl = (entry - curr) * qty if trade["order_type"] == "SELL" else (curr - entry) * qty
                    pnl_registry[trade["id"]] = round(pnl, 2)
                    ltp_registry[trade["id"]] = curr
                # If curr == 0, keep existing registry value (don't overwrite with wrong 0)
        except Exception:
            pass

    async def _check_time_based_trigger(
        self,
        nifty_price: float,
        pe_symbol_gap: float,
        ce_symbol_gap: float,
    ) -> None:
        """
        Time-based trigger: fires once per side per session in 9:45-11:30 AM window.
        Designed to catch flat range days where movement trigger never fires.
        Safety gates: regime=range, PCR confirms direction, no open trade same side,
        not already fired today, within time window only.
        """
        import pytz
        from datetime import datetime as dt
        now = dt.now(pytz.timezone("Asia/Kolkata"))

        # Reset flags at start of each new day
        if now.day != self._last_time_trigger_day:
            self._time_based_pe_fired = False
            self._time_based_ce_fired = False
            self._last_time_trigger_day = now.day

        # Only fire in 9:45 AM – 11:30 AM window
        from datetime import time as dtime
        now_time = now.time()
        if not (dtime(9, 45) <= now_time <= dtime(11, 30)):
            return

        # Only in range regime
        from core.market_context import market_context
        if market_context.regime not in ("range", "reversal_watch"):
            return

        # Get current PCR for direction filter
        pcr = getattr(market_context, "_pcr", 1.0)

        # Count open trades by direction
        open_pe = sum(1 for tr in self._open_trades_data if tr.get("direction") == "PE")
        open_ce = sum(1 for tr in self._open_trades_data if tr.get("direction") == "CE")

        # PE time trigger: PCR > 1.2 means more puts = market leaning bullish = safe to sell PE
        if (
            not self._time_based_pe_fired
            and open_pe == 0
            and pcr >= 1.2
            and len(self._open_trades_data) < 2
        ):
            self._signal(
                f"⏰ TIME TRIGGER: PE sell | PCR={pcr:.2f} | "
                f"Nifty={nifty_price:.2f} | window=9:45-11:30"
            )
            await self._sell_option(
                direction="PE",
                nifty_price=nifty_price,
                gap=pe_symbol_gap,
                quantity=self.cfg.pe_quantity,
            )
            self._time_based_pe_fired = True
            self._update_position(Direction.SHORT)
            return  # one trigger per tick max

        # CE time trigger: PCR < 0.8 means more calls = market leaning bearish = safe to sell CE
        if (
            not self._time_based_ce_fired
            and open_ce == 0
            and pcr <= 0.8
            and len(self._open_trades_data) < 2
        ):
            self._signal(
                f"⏰ TIME TRIGGER: CE sell | PCR={pcr:.2f} | "
                f"Nifty={nifty_price:.2f} | window=9:45-11:30"
            )
            await self._sell_option(
                direction="CE",
                nifty_price=nifty_price,
                gap=ce_symbol_gap,
                quantity=self.cfg.ce_quantity,
            )
            self._time_based_ce_fired = True
            self._update_position(Direction.SHORT)

    def get_config(self) -> dict:
        return vars(self.cfg)