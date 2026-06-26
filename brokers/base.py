# brokers/base.py
# Abstract interface that ALL broker adapters must implement.
# Strategy code only ever talks to this interface - never directly to a broker SDK.

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# ─── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class Order:
    symbol: str
    exchange: str  # NSE or NFO
    order_type: str  # BUY or SELL
    quantity: int
    product: str  # I = Intraday, D = Delivery
    price: float = 0.0  # 0 for MARKET orders
    order_id: Optional[str] = None
    tag: Optional[str] = None  # Idempotent order key — blocks duplicate orders


@dataclass
class OrderResponse:
    order_id: str
    status: str  # OPEN, COMPLETE, REJECTED, CANCELLED
    message: str = ""


@dataclass
class Position:
    symbol: str
    quantity: int  # positive = long, negative = short
    average_price: float
    last_price: float
    pnl: float


@dataclass
class Tick:
    symbol: str
    last_price: float
    timestamp: str
    best_bid: float = 0.0  # Highest price a buyer is offering right now
    best_ask: float = 0.0  # Lowest price a seller is demanding right now

    @property
    def mid_price(self) -> float:
        """
        Calculates a smooth, safe price to prevent rogue LTP spike triggers.
        Falls back to last_price if the broker feed doesn't provide Bid/Ask depth.
        """
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2.0
        return self.last_price


@dataclass
class MarginData:
    available: float
    used: float
    total: float


# ─── Abstract Broker Gateway ──────────────────────────────────────────────────


class AbstractBrokerGateway(ABC):
    """
    Every broker adapter (Upstox, ICICIdirect) must implement all these methods.
    Strategy code only imports and uses this class - never the broker SDK directly.
    """

    @abstractmethod
    async def login(self) -> bool:
        """Authenticate with the broker. Returns True on success."""
        pass

    @abstractmethod
    async def get_ltp(self, symbol: str) -> float:
        """Get the Last Traded Price for a symbol."""
        pass

    @abstractmethod
    async def get_option_chain(self, instrument_key: str, expiry: str) -> list:
        """
        Fetch the live option chain (with Greeks) for a given underlying and expiry.
        instrument_key: underlying instrument key, e.g. 'NSE_INDEX|Nifty 50'
        expiry: expiry date string, format YYYY-MM-DD
        Returns list of dicts, one per strike:
          {'strike': 24000.0,
           'ce_ltp': 45.0, 'ce_delta': 0.42, 'ce_oi': 123456, 'ce_bid': 44.5, 'ce_ask': 45.5,
           'pe_ltp': 38.0, 'pe_delta': -0.38, 'pe_oi': 98765, 'pe_bid': 37.5, 'pe_ask': 38.5}
        Returns [] on any failure -- callers must handle gracefully / fall back.
        """
        pass

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResponse:
        """Place a market or limit order."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order by its ID. Returns True on success."""
        pass

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Fetch all open positions for current session."""
        pass

    @abstractmethod
    async def get_orders(self) -> list[Order]:
        """Fetch all orders placed in current session."""
        pass

    @abstractmethod
    async def get_margin(self) -> MarginData:
        """Fetch available and used margin."""
        pass

    @abstractmethod
    def subscribe_ticks(self, symbols: list[str], callback) -> None:
        """
        Subscribe to live price ticks via WebSocket.
        callback will be called with a Tick object on every price update.
        """
        pass

    @abstractmethod
    def unsubscribe_ticks(self, symbols: list[str]) -> None:
        """Stop receiving ticks for the given symbols."""
        pass

    @abstractmethod
    def on_order_update(self, callback) -> None:
        """Register a callback for real-time order status changes."""
        pass