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
    async def get_option_chain(self, expiry: str, option_type: str) -> list:
        """
        Fetch all available strikes for a given expiry and type.
        option_type: 'CE' or 'PE'
        Returns list of dicts: [{'strike': 24000, 'symbol': 'NIFTY...', 'ltp': 45.0}]
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
