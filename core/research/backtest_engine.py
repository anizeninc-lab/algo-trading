"""
core/research/backtest_engine.py

Replays archived 1-min option-premium candles through a real strategy
instance (wave_extractor) using a simulated broker, so we can test the
strategy's actual entry/exit logic against historical data -- without
touching a live or paper broker.

IMPORTANT: this file is only ever imported by run_backtest.py, which must
be run as its OWN separate process (never through PM2, never imported by
main.py). See run_backtest.py's docstring for why.

Known limitation: we only archive 1-min OHLC candles, not true tick-by-tick
history. Each candle is expanded into 4 synthetic ticks (open, high, low,
close) as an approximation of intra-minute movement. This is good enough
to exercise the strategy's real decision logic, but won't perfectly match
what would have happened with real tick-by-tick data.
"""
import asyncio
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from brokers.base import (
    AbstractBrokerGateway, Order, OrderResponse, Position, Tick, MarginData
)


class SimulatedBroker(AbstractBrokerGateway):
    """
    A fake broker for backtesting. Implements every method a real broker
    would, but everything happens instantly and in-memory -- no network
    calls, no real orders. Fills happen at whatever price was last fed in
    by CandleReplaySource.
    """

    def __init__(self):
        self._tick_callbacks: dict = {}       # symbol -> [callback, ...]
        self._order_update_callback = None
        self._positions: dict = {}            # symbol -> Position
        self._orders: dict = {}               # order_id -> Order
        self._last_prices: dict = {}          # symbol -> float
        self._order_counter = 0

    # ── Required by the interface, not used by wave_extractor's logic ──
    async def login(self) -> bool:
        return True

    async def get_option_chain(self, instrument_key: str, expiry: str) -> list:
        return []

    async def get_orders(self) -> list:
        return list(self._orders.values())

    async def get_margin(self) -> MarginData:
        return MarginData(available=1_000_000.0, used=0.0, total=1_000_000.0)

    # ── Prices ──────────────────────────────────────────────────────────
    async def get_ltp(self, symbol: str) -> float:
        return self._last_prices.get(symbol, 0.0)

    def set_last_price(self, symbol: str, price: float) -> None:
        """Called before the strategy starts, and by CandleReplaySource as
        it feeds each synthetic tick, to keep get_ltp() answering correctly."""
        self._last_prices[symbol] = price

    # ── Orders (instant fill at last known price) ────────────────────────
    async def place_order(self, order: Order) -> OrderResponse:
        self._order_counter += 1
        order_id = f"BT_{self._order_counter}_{uuid.uuid4().hex[:6]}"
        fill_price = order.price if order.price > 0 else self._last_prices.get(order.symbol, 0.0)

        qty_signed = order.quantity if order.order_type == "BUY" else -order.quantity
        pos = self._positions.get(order.symbol)
        if pos is None:
            self._positions[order.symbol] = Position(
                symbol=order.symbol, quantity=qty_signed,
                average_price=fill_price, last_price=fill_price, pnl=0.0,
            )
        else:
            pos.quantity += qty_signed
            pos.last_price = fill_price

        self._orders[order_id] = order

        if self._order_update_callback:
            self._order_update_callback({
                "order_id": order_id,
                "status": "COMPLETE",
                "symbol": order.symbol,
                "filled_price": fill_price,
                "quantity": order.quantity,
            })

        return OrderResponse(order_id=order_id, status="COMPLETE", message="Simulated fill")

    async def cancel_order(self, order_id: str) -> bool:
        return True  # backtest fills are instant -- nothing is ever left pending

    async def get_positions(self) -> list:
        return list(self._positions.values())

    # ── Ticks ─────────────────────────────────────────────────────────────
    def subscribe_ticks(self, symbols: list, callback) -> None:
        for s in symbols:
            self._tick_callbacks.setdefault(s, []).append(callback)

    def unsubscribe_ticks(self, symbols: list) -> None:
        for s in symbols:
            self._tick_callbacks.pop(s, None)

    def on_order_update(self, callback) -> None:
        self._order_update_callback = callback

    def emit_tick(self, tick: Tick) -> None:
        """Called by CandleReplaySource -- delivers a tick to any subscribed
        strategy, exactly like a real broker's WebSocket callback would."""
        self.set_last_price(tick.symbol, tick.last_price)
        for cb in self._tick_callbacks.get(tick.symbol, []):
            cb(tick)


class CandleReplaySource:
    """
    Reads archived 1-min candles for a symbol from research_archive.db and
    replays them as synthetic ticks through a SimulatedBroker.
    """

    def __init__(self, db_path: str, symbol: str, broker: SimulatedBroker,
                 start_ts: Optional[str] = None, end_ts: Optional[str] = None,
                 real_time_pace: bool = True):
        self.db_path = db_path
        self.symbol = symbol
        self.broker = broker
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.real_time_pace = real_time_pace

    def _load_candles(self) -> list:
        query = "SELECT ts, open, high, low, close FROM candles_1min WHERE symbol = ?"
        params = [self.symbol]
        if self.start_ts:
            query += " AND ts >= ?"
            params.append(self.start_ts)
        if self.end_ts:
            query += " AND ts <= ?"
            params.append(self.end_ts)
        query += " ORDER BY ts ASC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return rows

    async def run(self) -> int:
        """Feeds every candle through the broker as synthetic ticks.
        Returns the number of candles replayed."""
        candles = self._load_candles()
        print(f"[replay] {len(candles)} candles found for {self.symbol}")
        for ts, o, h, l, c in candles:
            # Approximate intra-minute movement: open -> high -> low -> close
            for price in (o, h, l, c):
                tick = Tick(symbol=self.symbol, last_price=price, timestamp=ts)
                self.broker.emit_tick(tick)
                if self.real_time_pace:
                    await asyncio.sleep(15)  # ~60s / 4 ticks per candle
        return len(candles)


def init_backtest_results_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id            TEXT PRIMARY KEY,
                strategy          TEXT NOT NULL,
                symbol            TEXT NOT NULL,
                start_ts          TEXT,
                end_ts            TEXT,
                candles_replayed  INTEGER,
                total_trades      INTEGER,
                realised_pnl      REAL,
                run_at            TEXT NOT NULL
            )
        """)
        conn.commit()


def save_backtest_result(db_path: str, run_id: str, strategy: str, symbol: str,
                          start_ts, end_ts, candles_replayed: int,
                          total_trades: int, realised_pnl: float) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO backtest_runs (run_id, strategy, symbol, start_ts, end_ts, "
            "candles_replayed, total_trades, realised_pnl, run_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, strategy, symbol, start_ts, end_ts, candles_replayed,
             total_trades, realised_pnl, datetime.now().isoformat()),
        )
        conn.commit()
