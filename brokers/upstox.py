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
        self._ws_thread      = None
        self._last_tick_time = 0.0   # epoch time of last received tick
        self._ws_healthy     = False # True when ticks flowing normally
        self._streamer = None
        self._ltp_cache = {}
        self._tick_callbacks = {}  # sym -> list of callbacks
        self._order_callback = None
        self._connected = False
        self._placed_order_tags: set = set()  # idempotent order gate
        self._start_time: float = __import__("time").time()  # for startup grace period

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
        import time
        now = time.time()
        if not hasattr(self, '_ltp_ts'):
            self._ltp_ts = {}
        if symbol in self._ltp_cache and self._ltp_cache[symbol] > 0:
            if now - self._ltp_ts.get(symbol, 0) < 5.0:
                return float(self._ltp_cache[symbol])
        try:
            resp = self._market_api.ltp(symbol, api_version="2.0")
            if not resp.data:
                logger.warning(f"get_ltp: empty data for {symbol}")
                return float(self._ltp_cache.get(symbol, 0.0))
            keys = list(resp.data.keys())
            if not keys:
                logger.warning(f"get_ltp: no keys in response for {symbol}")
                return float(self._ltp_cache.get(symbol, 0.0))
            price = float(resp.data[keys[0]].last_price)
            self._ltp_cache[symbol] = price
            self._ltp_ts[symbol] = now
            return price
        except ApiException as e:
            logger.error(f"get_ltp failed for {symbol}: {e}")
            return float(self._ltp_cache.get(symbol, 0.0))
        except Exception as e:
            logger.error(f"get_ltp unexpected error for {symbol}: {e}")
            return float(self._ltp_cache.get(symbol, 0.0))

    async def get_option_chain(self, expiry: str, option_type: str) -> list:
        return []

    async def place_order(self, order: Order) -> OrderResponse:
        # ── QUANTITY HARDCAP ─────────────────────────────────────────────────
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
            # Idempotent check — block if same tag already placed today
            if order.tag:
                if order.tag in self._placed_order_tags:
                    logger.warning(f"[upstox] DUPLICATE ORDER BLOCKED by tag: {order.tag}")
                    return OrderResponse(order_id="DUPLICATE_BLOCKED", status="REJECTED",
                                         message=f"Duplicate order tag: {order.tag}")
                self._placed_order_tags.add(order.tag)

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
                tag=order.tag or "",
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
            except Exception as inner_e:
                logger.warning(f"get_orders primary call failed ({type(inner_e).__name__}: {str(inner_e)[:150]}) — no fallback available, returning empty")
                return []
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
            if sym not in self._tick_callbacks:
                self._tick_callbacks[sym] = []
            if callback not in self._tick_callbacks[sym]:
                self._tick_callbacks[sym].append(callback)

        if self._ws_thread and self._ws_thread.is_alive():
            logger.info(f"Restarting WebSocket to add new symbols: {symbols}")
            try:
                if self._streamer:
                    self._streamer.close()
            except Exception:
                pass
            self._ws_thread = None
            self._streamer  = None
            import time; time.sleep(1)
        all_symbols = list(self._tick_callbacks.keys())

        def _run_streamer():
            """Use Upstox SDK MarketDataStreamerV3 directly — handles protobuf + reconnect."""
            streamer = upstox_client.MarketDataStreamerV3(
                upstox_client.ApiClient(self._configuration), [], "full"
            )

            def on_open():
                import time as _time
                logger.info("WebSocket connected to Upstox")
                _time.sleep(3)  # Wait for all strategies to register their symbols
                current_symbols = list(self._tick_callbacks.keys())
                subscribe_syms = current_symbols if current_symbols else all_symbols
                streamer.subscribe(subscribe_syms, "full")
                logger.info(f"Subscribed to symbols: {subscribe_syms}")

            def on_message(message):
                try:
                    import time as _t
                    self._last_tick_time = _t.time()
                    self._ws_healthy     = True
                    feeds = message.get("feeds", {})
                    for sym, feed_data in feeds.items():
                        ltp = 0.0
                        bid_price = 0.0
                        ask_price = 0.0
                        try:
                            ff = feed_data.get("fullFeed", {}) or {}
                            source = ff.get("marketFF") or ff.get("indexFF") or {}
                            
                            # 1. Fetch Last Traded Price (LTP)
                            ltpc = source.get("ltpc", {}) or {}
                            ltp = float(ltpc.get("ltp", 0) or 0)
                            if not ltp:
                                ltpc2 = feed_data.get("ltpc", {}) or {}
                                ltp = float(ltpc2.get("ltp", 0) or 0)
                                
                            # 2. Extract top tier Market Depth Bid/Ask for Option Contracts
                            market_level = source.get("marketLevel", {}) or {}
                            bids = market_level.get("bid", [])
                            asks = market_level.get("ask", [])
                            if bids and isinstance(bids, list):
                                bid_price = float(bids[0].get("price", 0.0) or 0.0)
                            if asks and isinstance(asks, list):
                                ask_price = float(asks[0].get("price", 0.0) or 0.0)
                        except Exception:
                            pass

                        if ltp:
                            self._ltp_cache[sym] = ltp
                            if "Nifty 50" in sym or "NIFTY" in str(sym):
                                from core.state_store import state_store
                                state_store.update_nifty_price(float(ltp))
                                
                            # Construct enriched Tick object with structural depth buffers
                            tick = Tick(
                                symbol=sym,
                                last_price=float(ltp),
                                timestamp=datetime.now().isoformat(),
                                best_bid=bid_price,
                                best_ask=ask_price
                            )
                            for cb in self._tick_callbacks.get(sym, []):
                                try:
                                    cb(tick)
                                except Exception as e:
                                    logger.error(f"Callback error for {sym}: {e}")
                except Exception as e:
                    logger.error(f"Tick processing error: {e}", exc_info=True)

            def on_error(e):
                logger.error(f"WebSocket error: {e}")

            def on_close(*args):
                logger.warning("WebSocket closed")
                self._ws_healthy = False
                try:
                    import time as _t
                    import pytz
                    from datetime import datetime, time as dtime
                    now = datetime.now(pytz.timezone("Asia/Kolkata"))
                    market_open = dtime(9, 15) <= now.time() <= dtime(15, 15)
                    uptime = _t.time() - self._start_time
                    if market_open and uptime > 90:
                        from core.alerting import alert_websocket_down
                        alert_websocket_down("WebSocket closed — auto-reconnect in progress")
                except Exception:
                    pass

            self._streamer = streamer  # Set before connect so subscribe_ticks can find it
            streamer.on("open", on_open)
            streamer.on("message", on_message)
            streamer.on("error", on_error)
            streamer.on("close", on_close)
            streamer.auto_reconnect(True, 10, 5)
            streamer.connect()
        self._ws_thread = threading.Thread(target=_run_streamer, daemon=True)
        self._ws_thread.start()
        logger.info(f"WebSocket started with symbols: {all_symbols}")

        # Start heartbeat monitor
        def _heartbeat_monitor():
            import time
            import pytz
            from datetime import datetime, time as dtime
            _fail_count = 0
            while True:
                time.sleep(30)
                try:
                    now = datetime.now(pytz.timezone("Asia/Kolkata"))
                    market_open = dtime(9, 15) <= now.time() <= dtime(15, 15)
                    if not market_open:
                        _fail_count = 0
                        current_time = datetime.now()
                    if self._last_tick_time == 0:
                        continue
                    elapsed = time.time() - self._last_tick_time
                    if elapsed > 60:
                        _fail_count += 1
                        self._ws_healthy = False
                        logger.warning(f"[heartbeat] No ticks for {elapsed:.0f}s — failure #{_fail_count}")
                        if _fail_count == 3:
                            try:
                                from core.alerting import alert_websocket_down
                                alert_websocket_down(f"No ticks for {elapsed:.0f}s — 3 consecutive failures")
                            except Exception:
                                pass
                        if _fail_count >= 5:
                            logger.warning("[heartbeat] CRITICAL — forcing WebSocket reconnect")
                            try:
                                from core.alerting import send_telegram, LEVEL_CRITICAL
                                send_telegram(
                                    f"🚨 WEBSOCKET CRITICAL\n"
                                    f"No ticks for {elapsed:.0f}s\n"
                                    f"{_fail_count} consecutive failures\n"
                                    f"Forcing reconnect now",
                                    LEVEL_CRITICAL
                                )
                            except Exception:
                                pass
                            try:
                                if self._streamer:
                                    self._streamer.close()
                            except Exception:
                                pass
                            _fail_count = 0
                    else:
                        _fail_count = 0  # reset on healthy tick
                        self._ws_healthy = True
                except Exception as e:
                    logger.debug(f"[heartbeat] error: {e}")

        hb_thread = threading.Thread(target=_heartbeat_monitor, daemon=True)
        hb_thread.start()
        logger.info("WebSocket heartbeat monitor started")

        # Start order polling thread
        self._known_order_states = {}
        self._order_open_times   = {}  # order_id -> epoch time when first seen as open
        def _poll_orders():
            import time
            import asyncio
            ORDER_TIMEOUT_SECONDS = 60  # cancel orders open longer than this
            while True:
                time.sleep(10)  # was 3s — caused 429 rate-limit errors, raised to 10s
                try:
                    if not self._order_callback:
                        continue
                    loop = asyncio.new_event_loop()
                    orders = loop.run_until_complete(self.get_orders())
                    loop.close()
                    now = time.time()
                    for o in orders:
                        oid    = o.order_id
                        status = getattr(o, "status", "")
                        prev   = self._known_order_states.get(oid)

                        if prev != "complete" and status == "complete":
                            filled_qty = getattr(o, "filled_quantity", 0) or getattr(o, "quantity", 0)
                            req_qty    = getattr(o, "quantity", 0)
                            is_partial = filled_qty < req_qty and filled_qty > 0
                            if is_partial:
                                logger.warning(
                                    f"[ORDER POLL] PARTIAL FILL: {oid} "
                                    f"filled={filled_qty} requested={req_qty}"
                                )
                            else:
                                logger.info(f"[ORDER POLL] Order filled: {oid} qty={filled_qty}")
                            self._order_callback({
                                "order_id":      oid,
                                "status":        "COMPLETE",
                                "average_price": getattr(o, "average_price", 0) or getattr(o, "price", 0),
                                "symbol":        getattr(o, "symbol", ""),
                                "order_type":    getattr(o, "order_type", ""),
                                "filled_qty":    filled_qty,
                                "requested_qty": req_qty,
                                "is_partial":    is_partial,
                            })
                            self._order_open_times.pop(oid, None)

                        elif status in ("open", "pending", "trigger pending"):
                            if oid not in self._order_open_times:
                                self._order_open_times[oid] = now
                            else:
                                age = now - self._order_open_times[oid]
                                if age > ORDER_TIMEOUT_SECONDS:
                                    logger.warning(
                                        f"[ORDER POLL] Order {oid} stale for {age:.0f}s — cancelling"
                                    )
                                    try:
                                        cancel_loop = asyncio.new_event_loop()
                                        cancel_loop.run_until_complete(self.cancel_order(oid))
                                        cancel_loop.close()
                                        self._order_open_times.pop(oid, None)
                                        try:
                                            from core.alerting import send_telegram, LEVEL_WARNING
                                            send_telegram(
                                                f"ORDER TIMEOUT\nOrder {oid[:8]} cancelled after {age:.0f}s unfilled",
                                                LEVEL_WARNING
                                            )
                                        except Exception:
                                            pass
                                    except Exception as ce:
                                        logger.error(f"[ORDER POLL] Cancel failed: {ce}")
                        else:
                            self._order_open_times.pop(oid, None)

                        self._known_order_states[oid] = status
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

    async def place_gtt_trailing_sl(
        self,
        instrument_key: str,
        quantity: int,
        entry_price: float,
        order_type: str = "SELL",  # "SELL" means we sold, so exit is BUY
        trailing_gap: float = 0.25,
        sl_pct: float = 0.15,      # SL at 15% above entry (for SELL trade)
    ) -> str:
        try:
            import upstox_client
            cfg = upstox_client.Configuration()
            cfg.access_token = self._access_token
            client = upstox_client.ApiClient(cfg)

            exit_transaction = "BUY" if order_type == "SELL" else "SELL"
            trigger_price = round(entry_price * (1 + sl_pct), 1)

            rule = upstox_client.GttRule(
                strategy="TRAILING_STOP_LOSS",
                trigger_type="RISING",
                trigger_price=trigger_price,
                trailing_gap=trailing_gap,
                market_protection=0.25,
            )

            req = upstox_client.GttPlaceOrderRequest(
                type="SINGLE",
                quantity=quantity,
                product="I",
                rules=[rule],
                instrument_token=instrument_key,
                transaction_type=exit_transaction,
            )

            gtt_api = upstox_client.GttApi(client)
            resp = gtt_api.place_gtt_order(req, api_version="2.0")
            gtt_id = resp.data.id if resp and resp.data else ""
            import logging
            logging.getLogger(__name__).info(
                f"[GTT] Trailing SL placed | {instrument_key} | "
                f"trigger={trigger_price} | trailing_gap={trailing_gap} | id={gtt_id}"
            )
            return str(gtt_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[GTT] Failed to place GTT: {e}")
            return ""