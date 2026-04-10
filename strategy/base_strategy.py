# strategy/base_strategy.py
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime

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
        self._stop_flag = False
        self._session_id: str = ""
        state_store.register_strategy(name=self.name, broker=type(broker).__name__)
        logger.info(f"[{self.name}] Initialised with config: {config}")

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
        self._stop_flag = False
        state_store.update_state(self.name, StrategyState.RUNNING)
        await self._publish(
            EventType.STATE_CHANGE,
            {
                "state": StrategyState.RUNNING,
                "message": "Strategy started successfully",
            },
        )
        logger.info(f"[{self.name}] Running")

    async def stop(self, reason: str = "MANUAL") -> None:
        logger.info(f"[{self.name}] Stopping ({reason})...")
        self._stop_flag = True
        try:
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
        summary = trade_logger.get_pnl_summary(strategy=self.name)
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
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:
            return False
        hour = now.hour
        minute = now.minute
        market_open = (hour > 9) or (hour == 9 and minute >= 15)
        market_close = (hour < 15) or (hour == 15 and minute <= 30)
        return market_open and market_close

    def is_expiry_day(self) -> bool:
        """
        Returns True only if today is the actual expiry date configured
        in symbol_initials (e.g. 'NIFTY13APR26' → April 13, 2026).
        Falls back to Thursday check only if config is missing or unparseable.
        """
        # Try to read expiry date from config (set in saviour_combo.json)
        symbol_initials = self.config.get("symbol_initials", "")
        if symbol_initials:
            try:
                # Format: NIFTY13APR26  → date part = last 7 chars = "13APR26"
                date_part = symbol_initials.replace("NIFTY", "").replace("BANKNIFTY", "")
                expiry_date = datetime.strptime(date_part, "%d%b%y").date()
                return datetime.now().date() == expiry_date
            except Exception:
                logger.warning(
                    f"[{self.name}] Could not parse expiry from symbol_initials='{symbol_initials}'. "
                    f"Falling back to Thursday check."
                )

        # Fallback: treat every Thursday as expiry (original behaviour)
        return datetime.now().weekday() == 3