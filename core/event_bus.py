# core/event_bus.py
# Central message bus for the trading system.
# Strategies post events here. Dashboard and loggers subscribe to receive them.
#
# Event flow:
#   Strategy → event_bus.publish(event) → all subscribers receive it
#
# No direct imports between strategies and dashboard - everything goes via here.

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Event Types ─────────────────────────────────────────────────────────────


class EventType:
    # Strategy lifecycle
    STATE_CHANGE = "STATE_CHANGE"  # strategy started, stopped, errored

    # Trading signals
    SIGNAL = "SIGNAL"  # algo generated a trade signal

    # Order events
    ORDER_PLACED = "ORDER_PLACED"  # order sent to broker
    ORDER_FILLED = "ORDER_FILLED"  # order confirmed filled
    ORDER_CANCELLED = "ORDER_CANCELLED"  # order cancelled
    ORDER_FAILED = "ORDER_FAILED"  # order rejected by broker

    # Position events
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"

    # System events
    BROKER_CONNECTED = "BROKER_CONNECTED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    ERROR = "ERROR"
    TICK_RECEIVED = "TICK_RECEIVED"


# ─── Event Model ─────────────────────────────────────────────────────────────


@dataclass
class Event:
    event_type: str  # one of EventType constants above
    strategy: str  # e.g. "survivor", "wave_extractor"
    payload: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    event_id: str = field(default_factory=lambda: _generate_id())


def _generate_id() -> str:
    import uuid

    return str(uuid.uuid4())[:8]


# ─── Event Bus ───────────────────────────────────────────────────────────────


class EventBus:
    """
    Async publish/subscribe event bus.

    Subscribers register a callback and receive every event published.
    Strategies call publish() - they don't know who is listening.
    """

    def __init__(self):
        self._subscribers: list = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running: bool = False
        self._history: list[Event] = []  # keeps last 500 events
        self._max_history: int = 500

    def subscribe(self, callback) -> None:
        """
        Register a callback to receive all events.
        callback will be called with an Event object.
        """
        if callback not in self._subscribers:
            self._subscribers.append(callback)
            logger.debug(f"EventBus: subscriber added ({len(self._subscribers)} total)")

    def unsubscribe(self, callback) -> None:
        """Remove a previously registered callback."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def publish(self, event: Event) -> None:
        """
        Post an event to the bus.
        All subscribers will receive it asynchronously.
        """
        await self._queue.put(event)

    def publish_sync(self, event: Event) -> None:
        """
        Synchronous version of publish for use outside async context.
        Used by WebSocket tick threads that can't await.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.publish(event), loop)
            else:
                loop.run_until_complete(self.publish(event))
        except Exception as e:
            logger.error(f"EventBus publish_sync error: {e}")

    async def start(self) -> None:
        """Start the event dispatch loop. Run this as an asyncio task."""
        self._running = True
        logger.info("EventBus started")
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"EventBus dispatch error: {e}")

    async def stop(self) -> None:
        """Stop the event dispatch loop."""
        self._running = False
        logger.info("EventBus stopped")

    async def _dispatch(self, event: Event) -> None:
        """Send event to all subscribers and store in history."""
        # Store in history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        # Notify all subscribers
        for callback in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"EventBus subscriber error: {e}")

    def get_history(
        self,
        strategy: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[Event]:
        """
        Retrieve recent events, optionally filtered.
        Used by dashboard to show event log.
        """
        events = self._history
        if strategy:
            events = [e for e in events if e.strategy == strategy]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-limit:]


# ─── Global singleton instance ───────────────────────────────────────────────
# Import this anywhere in the project:
#   from core.event_bus import event_bus, Event, EventType

event_bus = EventBus()
