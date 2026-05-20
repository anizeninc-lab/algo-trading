# brokers/upstox.py
import asyncio
import logging
import os
import threading
from datetime import datetime

import upstox_client
from dotenv import load_dotenv
from upstox_client.rest import ApiException

from brokers.base import (AbstractBrokerGateway, MarginData, Order,
                          OrderResponse, Position, Tick)

load_dotenv(override=True)
logger = logging.getLogger(__name__)

# ─── HARDCAP — NEVER change this without careful testing ─────────────────────
MAX_QTY_PER_ORDER = 65  # 1 lot of Nifty options = 65 qty. Bot should NEVER place more.
# ─────────────────────────────────────────────────────────────────────────────


class UpstoxAdapter(AbstractBrokerGateway):

    def __init__(self):
        self.api_key = os.getenv("UPSTOX_API_KEY", "")
        self.api_secret = os.getenv("UPSTOX_API_SECRET", "")
        self.redirect_uri = os.getenv(
            "UPSTOX_REDIRECT_URI", "http://127.0.0.1:8080/callback"
        )
        self.access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        self._configuration = None
        self._order_api = None
        self._order_api_v2 = None
        self._portfolio_api = None
        self._market_api = None
        self._ws_thread = None
        self._streamer = None
        self._tick_callbacks = {}
        self._order_callback = None
        self._connected = False

    async def login(self) -> bool:
        try:
            if not self.access_token:
                logger.error("UPSTOX_ACCESS_TOKEN is empty in .env file")
                return False
            self._configuration = upstox_client.Configuration()
            self._configuration.access_token = self.access_token
            api_client = upstox_client.ApiClient(self._configuration)
            self._order_api = upstox_client.OrderApiV3(api_client)
            self._order_api_v2 = upstox_client.OrderApi(api_client)
            self._portfolio_api = upstox_client.PortfolioApi(api_client)
            self._market_api = upstox_client.MarketQuoteApi(api_client)
            user_api = upstox_client.UserApi(api_client)
            profile = user_api.get_profile(api_version="2.0")
            logger.info(f"Upstox login successful: {profile.data.user_name}")
            self._connected = True
            return True
        except ApiException as e:
            logger.error(f"Upstox login failed: {e}")
            self._connected = False
            return False

    async def get_ltp(self, symbol: str) -> float:
        try:
            resp = self._market_api.ltp(symbol, api_version="2.0")
            if not resp.data:
                logger.warning(f"get_ltp: empty data for {symbol}")
                return 0.0
            keys = list(resp.data.keys())
            if not keys:
                logger.warning(f"get_ltp: no keys in response for {symbol}")
                return 0.0
            return float(resp.data[keys[0]].last_price)
        except ApiException as e:
            logger.error(f"get_ltp failed for {symbol}: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"get_ltp unexpected error for {symbol}: {e}")
            return 0.0

    async def get_option_chain(self, expiry: str, option_type: str) -> list:
        return []

    async def place_order(self, order: Order) -> OrderResponse:
        # ── QUANTITY HARDCAP ─────────────────────────────────────────────────
        # This is a safety guard. Bot should NEVER place more than 1 lot (65 qty).
        # If quantity exceeds this, it means there is a bug — block the order.
        if order.quantity > MAX_QTY_PER_ORDER:
            logger.error(
                f"⛔ ORDER BLOCKED BY HARDCAP — "
                f"Requested qty: {order.quantity} exceeds max allowed: {MAX_QTY_PER_ORDER}. "
                f"Symbol: {order.symbol} | Type: {order.order_type}. "
                f"This is a safety block to prevent over-ordering bugs."
            )
            return OrderResponse(order_id="BLOCKED_HARDCAP", status="REJECTED",
                                 message=f"Quantity {order.quantity} exceeds hardcap of {MAX_QTY_PER_ORDER}")
        # ─────────────────────────────────────────────────────────────────────
        try:
            body = upstox_client.PlaceOrderV3Request(
                quantity=order.quantity,
                product=order.product,
                validity="DAY",
                price=order.price,
                instrument_token=order.symbol,
                order_type="MARKET" if order.price == 0 else "LIMIT",
                transaction_type=order.order_type,
                disclosed_quantity=0,
                trigger_price=0,
                is_amo=False,
            )
            resp = self._order_api.place_order(body)
            order_id = resp.data.order_ids[0] if hasattr(resp.data, 'order_ids') else getattr(resp.data, 'order_id', '')
            logger.info(
                f"Order placed: {order_id} | {order.symbol} {order.order_type} {order.quantity}"
            )
            return OrderResponse(
                order_id=order_id,
                status="OPEN",
                message="Order placed successfully",
            )
        except ApiException as e:
            logger.error(f"place_order failed: {e}")
            return OrderResponse(order_id="", status="REJECTED", message=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        try:
            self._order_api.cancel_order(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except ApiException as e:
            logger.error(f"cancel_order failed for {order_id}: {e}")
            return False

    async def get_positions(self) -> list:
        try:
            resp = self._portfolio_api.get_positions(api_version="2.0")
            positions = []
            for p in resp.data:
                positions.append(
                    Position(
                        symbol=p.instrument_token,
                        quantity=p.quantity,
                        average_price=p.average_price,
                        last_price=p.last_price,
                        pnl=p.pnl,
                    )
                )
            return positions
        except ApiException as e:
            logger.error(f"get_positions failed: {e}")
            return []

    async def get_orders(self) -> list:
        try:
            try:
                resp = self._order_api_v2.get_order_book(api_version="2.0")
            except Exception:
                resp = self._order_api.get_order_book_v3()
            orders = []
            if resp and resp.data:
                for o in resp.data:
                    ord_obj = Order(
                            symbol=getattr(o, "instrument_token", ""),
                            exchange=getattr(o, "exchange", ""),
                            order_type=getattr(o, "transaction_type", ""),
                            quantity=getattr(o, "quantity", 0),
                            product=getattr(o, "product", ""),
                            price=getattr(o, "average_price", 0.0) or getattr(o, "price", 0.0),
                            order_id=getattr(o, "order_id", ""),
                        )
                    ord_obj.status = getattr(o, "status", "")
                    ord_obj.average_price = getattr(o, "average_price", 0.0)
                    orders.append(ord_obj)
            return orders
        except ApiException as e:
            logger.error(f"get_orders failed: {e}")
            return []
        except Exception as e:
            logger.error(f"get_orders unexpected error: {e}")
            return []

    async def get_margin(self) -> MarginData:
        try:
            user_api = upstox_client.UserApi(
                upstox_client.ApiClient(self._configuration)
            )
            resp = user_api.get_user_fund_margin(segment="SEC", api_version="2.0")
            data = resp.data
            import re, ast
            equity_raw = data.get("equity", "{}") if isinstance(data, dict) else str(data.equity)
            try:
                equity = ast.literal_eval(equity_raw) if isinstance(equity_raw, str) else equity_raw
                available = float(equity.get("available_margin", 0))
                used = float(equity.get("used_margin", 0))
            except Exception:
                av = re.search(r"available_margin[^:]*:\s*([\d.]+)", str(equity_raw))
                us = re.search(r"used_margin[^:]*:\s*([\d.]+)", str(equity_raw))
                available = float(av.group(1)) if av else 0.0
                used = float(us.group(1)) if us else 0.0
            return MarginData(available=available, used=used, total=available + used)
        except ApiException as e:
            logger.error(f"get_margin failed: {e}")
            return MarginData(available=0.0, used=0.0, total=0.0)

    def subscribe_ticks(self, symbols: list, callback) -> None:
        for sym in symbols:
            self._tick_callbacks[sym] = callback


        if self._ws_thread and self._ws_thread.is_alive():
            logger.info(f"Restarting WebSocket to add new symbols: {symbols}")
            try:
                if self._streamer:
                    self._streamer.close()
            except Exception:
                pass
            self._ws_thread = None
            self._streamer = None
            import time; time.sleep(1)
        all_symbols = list(self._tick_callbacks.keys())

        def _run_streamer():
            self._streamer = upstox_client.MarketDataStreamerV3(
                upstox_client.ApiClient(self._configuration), all_symbols, "full"
            )

            def on_message(msg):
                logger.info(f"[MESSAGE RECEIVED] Type: {type(msg)} | Keys: {msg.keys() if hasattr(msg, 'keys') else 'N/A'}")
                logger.info(f"[RAW MESSAGE] {repr(msg)[:1000]}")
                try:
                    feeds = msg.get("feeds", {})
                    logger.info(f"[FEEDS] Type: {type(feeds)} | Count: {len(feeds)} | Keys: {list(feeds.keys())[:5]}")

                    items = list(feeds.items())
                    logger.info(f"[FEEDS ITEMS] {len(items)} items")

                    for sym, data in items:
                        logger.info(f"[FEED DATA] Symbol: {sym} | Data Type: {type(data)} | Data Keys: {list(data.keys()) if hasattr(data, 'keys') else 'N/A'}")

                        full_feed = {}
                        source_ff = {}
                        if isinstance(data, dict):
                            full_feed = data.get("fullFeed", {}) or {}
                            source_ff = (
                                full_feed.get("marketFF")
                                or full_feed.get("indexFF")
                                or data.get("ff", {}).get("marketFF")
                                or data.get("ff", {}).get("indexFF")
                                or {}
                            )
                        else:
                            logger.info(f"[FEED DATA NON-DICT] {type(data)}")

                        logger.info(
                            f"[SOURCE FF] Symbol: {sym} | Keys: {list(source_ff.keys()) if hasattr(source_ff, 'keys') else 'N/A'}"
                        )

                        ltp = 0.0
                        if isinstance(source_ff, dict):
                            ltpc = source_ff.get("ltpc")
                            if isinstance(ltpc, dict):
                                ltp = float(ltpc.get("ltp", 0))
                        logger.info(f"[LTP] Symbol: {sym} | LTP Value: {ltp} | Type: {type(ltp)}")

                        if ltp:
                            logger.info(f"[ALL TICKS] Symbol: {sym} | LTP: {ltp}")

                            if 'Nifty 50' in sym or 'NIFTY50' in sym or 'Nifty 50' in str(sym):
                                logger.info(f"[NIFTY UPDATE] Symbol: {sym} | LTP: {ltp}")
                                from core.state_store import state_store
                                state_store.update_nifty_price(float(ltp))

                            tick = Tick(
                                symbol=sym,
                                last_price=float(ltp),
                                timestamp=datetime.now().isoformat(),
                            )

                            cb = self._tick_callbacks.get(sym)
                            if cb:
                                try:
                                    cb(tick)
                                except Exception as e:
                                    logger.error(f"Callback error for {sym}: {e}")

                except Exception as e:
                    logger.error(f"Tick processing error: {e}", exc_info=True)

            self._streamer.on("message", on_message)
            self._streamer.on("open", lambda: logger.info("WebSocket connected to Upstox"))
            self._streamer.on("error", lambda e: logger.error(f"WebSocket error: {e}"))
            self._streamer.on("close", lambda *args: logger.warning("WebSocket closed"))
            self._streamer.connect()

        self._ws_thread = threading.Thread(target=_run_streamer, daemon=True)
        self._ws_thread.start()
        logger.info(f"WebSocket started with symbols: {all_symbols}")

        # Start order polling thread to detect live order fills
        self._known_order_states = {}
        def _poll_orders():
            import time
            import asyncio
            while True:
                time.sleep(3)
                try:
                    if not self._order_callback:
                        continue
                    loop = asyncio.new_event_loop()
                    orders = loop.run_until_complete(self.get_orders())
                    loop.close()
                    for o in orders:
                        oid = o.order_id
                        prev = self._known_order_states.get(oid)
                        if prev != "complete" and getattr(o, "status", "") == "complete":
                            logger.info(f"[ORDER POLL] Order filled: {oid}")
                            self._order_callback({
                                "order_id": oid,
                                "status": "COMPLETE",
                                "average_price": getattr(o, "average_price", 0) or getattr(o, "price", 0),
                                "symbol": getattr(o, "symbol", ""),
                                "order_type": getattr(o, "order_type", ""),
                            })
                        self._known_order_states[oid] = getattr(o, "status", "")
                except Exception as e:
                    logger.warning(f"[ORDER POLL] Error: {e}")

        self._order_poll_thread = threading.Thread(target=_poll_orders, daemon=True)
        self._order_poll_thread.start()
        logger.info("Order polling thread started")

    def unsubscribe_ticks(self, symbols: list) -> None:
        for sym in symbols:
            self._tick_callbacks.pop(sym, None)
        logger.info(f"Unsubscribed from ticks: {symbols}")

    def on_order_update(self, callback) -> None:
        self._order_callback = callback

    @property
    def is_connected(self) -> bool:
        return self._connected
