# strategy/base_strategy.py
import asyncio
import json
import logging
import os
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
import pytz

from brokers.base import AbstractBrokerGateway, Tick
from core.event_bus import Event, EventType, event_bus
from core.state_store import Direction, StrategyState, state_store
from core.trade_log import trade_logger

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):

    def __init__(self, name: str, broker: AbstractBrokerGateway, config: dict):
        self.name = name
        self.broker = broker
        self.config = config
        self.__stop_flag_value = False
        self._session_id: str = ""
        self._last_tick_time: dict = {}
        self._staleness_alerted: dict = {}
        state_store.register_strategy(name=self.name, broker=type(broker).__name__)
        logger.info(f"[{self.name}] Initialised with config: {config}")

    @property
    def _stop_flag(self):
        return self.__stop_flag_value

    @_stop_flag.setter
    def _stop_flag(self, value):
        if value and not self.__stop_flag_value:
            logger.warning(
                f"[{self.name}] _stop_flag set True -- call stack:\n"
                f"{''.join(traceback.format_stack())}"
            )
        self.__stop_flag_value = value

    async def _recover_open_positions(self) -> None:
        """
        Startup safety check: find any trades left OPEN in the database from
        before a crash/restart, loudly alert, and let subclasses restore tracking.
        """
        try:
            orphans = trade_logger.get_active_positions(strategy=self.name)
        except Exception as e:
            logger.error(f"[{self.name}] _recover_open_positions query failed: {e}")
            return
        if not orphans:
            return
        for row in orphans:
            msg = (
                f"ORPHANED OPEN POSITION FOUND ON STARTUP | "
                f"symbol={row.get('symbol')} | order_type={row.get('order_type')} | "
                f"qty={row.get('quantity')} | entry={row.get('entry_price')} | "
                f"entry_time={row.get('entry_time')} | trade_id={row.get('id')}"
            )
            logger.critical(f"[{self.name}] {msg}")
            self._signal(f"⚠️ {msg}")
            recovered = False
            try:
                await self._on_recover_trade(dict(row))
                recovered = True
            except Exception as e:
                logger.error(f"[{self.name}] _on_recover_trade failed for {row.get('id')}: {e}")
            if not recovered:
                await self._force_close_orphan(dict(row))

    async def _force_close_orphan(self, row: dict) -> None:
        """Force-close an orphaned open trade at current LTP via REST and mark it in DB."""
        trade_id = row.get("id")
        symbol   = row.get("symbol", "")
        qty      = row.get("quantity", 0)
        entry    = row.get("entry_price", 0)
        try:
            ltp = await self.broker.get_ltp(symbol)
        except Exception as e:
            logger.error(f"[{self.name}] _force_close_orphan: get_ltp failed for {symbol}: {e}")
            ltp = entry  # fallback to entry price — P&L = 0, at least closes cleanly
        pnl = round((entry - ltp) * qty, 2)  # SELL position: profit if price fell
        try:
            trade_logger.close_trade(
                trade_id=trade_id,
                exit_price=ltp,
                exit_time=datetime.now(pytz.timezone("Asia/Kolkata")).isoformat(),
                realised_pnl=pnl,
                notes="ORPHANED|FORCE_CLOSED",
            )
            logger.warning(
                f"[{self.name}] FORCE CLOSED orphan {trade_id} | {symbol} | "
                f"entry={entry} exit={ltp} pnl=₹{pnl}"
            )
            self._signal(
                f"🔴 ORPHAN FORCE CLOSED | {symbol} | entry={entry} exit={ltp} | P&L: ₹{pnl}"
            )
        except Exception as e:
            logger.error(f"[{self.name}] _force_close_orphan: close_trade failed for {trade_id}: {e}")

    async def _on_recover_trade(self, row: dict) -> None:
        """
        Default no-op. Subclasses with their own open-trade tracking
        (survivor, wave_extractor) should override this to restore
        the row into their own structures.
        """
        pass

    async def start(self) -> None:
        state_store.update_state(self.name, StrategyState.IDLE)
        state_store.set_broker_status(type(self.broker).__name__, "CONNECTING")
        connected = await self.broker.login()
        if not connected:
            await self._set_error("Broker login failed")
            return
        state_store.set_broker_status(type(self.broker).__name__, "CONNECTED")
        self._session_id = trade_logger.start_session(
            strategy=self.name, config_snapshot=json.dumps(self.config)
        )
        try:
            await self.on_start()
        except Exception as e:
            await self._set_error(f"on_start failed: {e}")
            return
        await self._recover_open_positions()
        self._stop_flag = False
        state_store.update_state(self.name, StrategyState.RUNNING)
        await self._publish(
            EventType.STATE_CHANGE,
            {
                "state":   StrategyState.RUNNING,
                "message": "Strategy started successfully",
            },
        )
        logger.info(f"[{self.name}] Running")

    async def stop(self, reason: str = "MANUAL") -> None:
        logger.info(f"[{self.name}] Stopping ({reason})...")
        self._stop_flag = True

        # Only cancel real orders in live mode — paper mode has no real orders
        try:
            if os.getenv("PAPER_TRADE", "false").lower() != "true":
                orders = await self.broker.get_orders()
                for order in orders:
                    if order.order_id:
                        await self.broker.cancel_order(order.order_id)
        except Exception as e:
            logger.warning(f"[{self.name}] Error cancelling orders on stop: {e}")

        try:
            await self.on_stop()
        except Exception as e:
            logger.warning(f"[{self.name}] on_stop error: {e}")

        summary = trade_logger.get_pnl_summary(strategy=self.name, today_only=True)
        trade_logger.end_session(
            session_id=self._session_id,
            total_pnl=summary["total_pnl"],
            stop_reason=reason,
        )
        state_store.update_state(self.name, StrategyState.STOPPED)
        state_store.set_broker_status(type(self.broker).__name__, "DISCONNECTED")
        await self._publish(
            EventType.STATE_CHANGE,
            {"state": StrategyState.STOPPED, "message": f"Strategy stopped: {reason}"},
        )
        logger.info(f"[{self.name}] Stopped")

    async def reset(self) -> None:
        if state_store.get_strategy(self.name).state == StrategyState.ERROR:
            self._stop_flag = True
            state_store.update_state(self.name, StrategyState.IDLE)
            logger.info(f"[{self.name}] Reset from ERROR to IDLE")

    @abstractmethod
    async def on_tick(self, tick: Tick) -> None:
        pass

    @abstractmethod
    def get_config(self) -> dict:
        pass

    async def on_start(self) -> None:
        pass

    async def on_stop(self) -> None:
        pass

    async def _publish(self, event_type: str, payload: dict) -> None:
        await event_bus.publish(
            Event(event_type=event_type, strategy=self.name, payload=payload)
        )

    async def _set_error(self, message: str) -> None:
        logger.error(f"[{self.name}] ERROR: {message}")
        state_store.update_state(self.name, StrategyState.ERROR, message)
        state_store.set_broker_status(type(self.broker).__name__, "DISCONNECTED")
        await self._publish(EventType.ERROR, {"message": message})
        trade_logger.log_event(
            event_type=EventType.ERROR, strategy=self.name, payload=message
        )

    def _signal(self, message: str) -> None:
        state_store.update_last_signal(self.name, message)
        trade_logger.log_event(
            event_type=EventType.SIGNAL, strategy=self.name, payload=message
        )
        logger.info(f"[{self.name}] SIGNAL: {message}")

    def _update_pnl(self, realised: float, unrealised: float) -> None:
        state_store.update_pnl(self.name, realised, unrealised)

    def _update_position(self, direction: str) -> None:
        state_store.update_position(self.name, direction)

    def is_market_open(self) -> bool:
        now     = datetime.now(pytz.timezone("Asia/Kolkata"))
        weekday = now.weekday()
        if weekday >= 5:
            return False
        hour   = now.hour
        minute = now.minute
        market_open  = (hour > 9) or (hour == 9 and minute >= 15)
        market_close = (hour < 15) or (hour == 15 and minute <= 30)
        return market_open and market_close

    def is_expiry_day(self) -> bool:
        """
        Returns True only if today is the actual expiry date configured
        in symbol_initials (e.g. 'NIFTY13APR26' → April 13, 2026).
        Falls back to Tuesday check only if config is missing or unparseable.
        """
        symbol_initials = self.config.get("symbol_initials", "")
        if symbol_initials:
            try:
                date_part   = symbol_initials.replace("NIFTY", "").replace("BANKNIFTY", "")
                expiry_date = datetime.strptime(date_part, "%d%b%y").date()
                return datetime.now().date() == expiry_date
            except Exception:
                logger.warning(
                    f"[{self.name}] Could not parse expiry from "
                    f"symbol_initials='{symbol_initials}'. "
                    f"Falling back to Tuesday check."
                )
        # Fallback: treat every Tuesday as expiry (Nifty weekly expiry)
        return datetime.now().weekday() == 1

    async def _arm_broker_stop_loss(
        self,
        ikey: str,
        quantity: int,
        entry_price: float,
        symbol: str,
        on_failure=None,
        sl_pct: float = 0.15,
        trailing_gap: float = 0.25,
        order_type: str = "SELL",
    ) -> bool:
        """
        Shared broker-side GTT trailing stop-loss arming, lifted from the
        pattern already proven in survivor.py (Phase 2 of the audit fixes,
        2026-09).

        `order_type` must match the entry direction of the POSITION being
        protected, not necessarily "SELL": pass "SELL" for a short-option
        entry (exit is BUY, stop triggers on a price rise) and "BUY" for a
        long-option entry (exit is SELL, stop triggers on a price fall) --
        see brokers/upstox.py's place_gtt_trailing_sl for the direction
        logic. Defaulting to "SELL" matches every strategy wired so far
        except nifty_gex, which holds long options and must pass "BUY"
        explicitly.

        This is a BACKSTOP, not the primary exit mechanism -- each strategy's
        own tick-driven software SL/trailing-profit logic should still fire
        first under normal conditions and is expected to win that race. The
        GTT only matters if the bot process itself is down, crashed, or
        disconnected when a position needs to exit -- e.g. across the daily
        token-refresh restart window, a VPS reboot, or a WS/process crash.

        Deliberately does NOT assume a uniform _close_trade(trade, reason,
        price) signature across strategies -- survivor.py, wave_extractor.py,
        put_calendar.py, and nifty_gex.py all have different close-method
        names and signatures (some track a list of open trades, some track
        one active trade). Callers pass their own `on_failure` async callable
        (typically a small lambda wrapping their own close method) so this
        stays a thin, safe-to-reuse helper rather than forcing a large
        cross-file signature refactor.

        Returns True if a GTT was armed (or paper mode / broker doesn't
        support GTTs, in which case there's nothing to arm and this is a
        no-op success). Returns False if arming failed after retries --
        callers should treat False as "this position currently has no
        broker-side protection" and decide whether to auto-close or just
        alert, via on_failure.
        """
        if self._is_paper or not hasattr(self.broker, "place_gtt_trailing_sl"):
            return True

        for attempt in range(2):
            try:
                gtt_id = await self.broker.place_gtt_trailing_sl(
                    instrument_key=ikey,
                    quantity=quantity,
                    entry_price=entry_price,
                    order_type=order_type,
                    trailing_gap=trailing_gap,
                    sl_pct=sl_pct,
                )
                if gtt_id:
                    self._signal(f"🛡 GTT armed | {symbol} | id={gtt_id}")
                    return True
            except Exception as e:
                logger.warning(f"[{self.name}] GTT arm attempt {attempt + 1} failed for {symbol}: {e}")
            await asyncio.sleep(2)

        logger.error(
            f"[{self.name}] GTT arming FAILED after retries for {symbol} -- "
            f"position has no broker-side protection."
        )
        try:
            from core.alerting import alert_gtt_failed
            alert_gtt_failed(symbol, "GTT failed after 2 attempts")
        except Exception as e:
            logger.error(f"[{self.name}] alert_gtt_failed itself failed: {e}")

        if on_failure is not None:
            try:
                await on_failure()
            except Exception as e:
                logger.error(f"[{self.name}] on_failure callback (post-GTT-fail) raised: {e}")

        return False

    def _record_tick(self, symbol: str) -> None:
        """
        Call from _on_tick_sync whenever a tick for `symbol` arrives. Powers
        _check_tick_staleness() below (Phase 4 audit fix, 2026-09).

        Deliberately independent of whatever per-strategy price cache each
        strategy already maintains (_ltp_cache, _current_price, etc.) --
        this only tracks *when* a tick last arrived, not the price itself,
        so it doesn't need to understand or touch each strategy's existing
        (differently-shaped) price-caching logic.
        """
        import time as _time
        self._last_tick_time[symbol] = _time.time()

    def _check_tick_staleness(self, symbol: str, threshold_sec: float = 45.0) -> None:
        """
        Call periodically (alongside SL/trailing-profit checks) for any
        symbol with an open position. Alerts -- does NOT auto-close -- if no
        tick has been received for `symbol` in over threshold_sec, during
        market hours (Phase 4 audit fix, 2026-09).

        This is a DIFFERENT signal from the existing WS-level heartbeat
        monitor in brokers/upstox.py (which tracks the whole connection via
        the INDEX tick and can look perfectly healthy while a single
        illiquid STRIKE goes quiet) -- this one is per-instrument, which
        matters because SL/trailing-profit evaluation is entirely tick-driven.

        Policy is alert-only, deliberately -- not auto-close. Staleness
        detection can have false positives (e.g. genuinely zero trading
        activity on a deep OTM strike for a stretch), and auto-closing a
        position on an unreliable signal is its own risk. Revisit auto-close
        only after this has been observed alert-only for a while and proven
        not to be noisy.
        """
        import time as _time
        last = self._last_tick_time.get(symbol)
        if last is None:
            return  # no tick recorded yet for this symbol -- nothing to compare against

        staleness = _time.time() - last
        if staleness <= threshold_sec:
            return

        try:
            import pytz
            from datetime import datetime as _dt, time as _dtime
            now = _dt.now(pytz.timezone("Asia/Kolkata"))
            market_open = _dtime(9, 15) <= now.time() <= _dtime(15, 30) and now.weekday() < 5
            if not market_open:
                return
        except Exception:
            pass  # if market-hours check itself fails, fail open and still alert

        last_alerted = self._staleness_alerted.get(symbol, 0)
        if _time.time() - last_alerted < 60:
            return  # rate-limit: at most one alert per symbol per 60s
        self._staleness_alerted[symbol] = _time.time()

        logger.warning(
            f"[{self.name}] Tick staleness: no tick for {symbol} in "
            f"{staleness:.0f}s (threshold {threshold_sec:.0f}s)"
        )
        try:
            from core.alerting import alert_tick_stale
            alert_tick_stale(symbol, staleness)
        except Exception as e:
            logger.error(f"[{self.name}] alert_tick_stale itself failed: {e}")
