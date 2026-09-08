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

# ─── WS instrument cap ─────────────────────────────────────────────────────
# Upstox's v3 market-data feed documents a ~100-instrument ceiling per WS
# connection. Set with headroom below that, not right at it. Enforced in
# subscribe_ticks() below (Phase 3 audit fix, 2026-09) -- previously
# unenforced, so five concurrent strategies (survivor, wave_extractor,
# put_calendar, nifty_gex, saviour_combo) subscribing independently could
# silently exceed the cap on days with wide strike dispersion, leaving some
# symbols with no live ticks and no indication anything was wrong -- a real
# risk given SL/trailing-profit logic is entirely tick-driven.
MAX_INSTRUMENTS_PER_WS = 95
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
        self._last_index_tick_time = 0.0  # epoch time of last index tick (Nifty/BankNifty)
        self._ws_healthy     = False # True when ticks flowing normally
        self._ws_loop = None   # asyncio event loop running the WS connection (own thread)
        self._ws_conn = None   # active `websockets` connection object
        self._ltp_cache = {}
        self._tick_callbacks = {}  # sym -> list of callbacks
        self._order_callback = None
        self._connected = False
        self._placed_order_tags: set = set()  # idempotent order gate
        self._start_time: float = __import__("time").time()  # for startup grace period
        # ── API Rate Limiter (token bucket, 8 calls/sec max) ─────────────────
        # Upstox limit is ~10/sec; we cap at 8 to stay safe.
        # All broker API calls must await self._throttle() before executing.
        self._rl_max_tokens:  float = 8.0   # max burst
        self._rl_tokens:      float = 8.0   # current tokens
        self._rl_refill_rate: float = 8.0   # tokens added per second
        self._rl_last_refill: float = __import__("time").time()
        self._rl_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        """Token bucket rate limiter. Await before every broker API call.
        Allows up to 8 calls/sec burst, refills at 8 tokens/sec.
        Prevents 429 rate-limit errors from simultaneous multi-strategy calls.
        """
        import time as _time
        async with self._rl_lock:
            now = _time.time()
            elapsed = now - self._rl_last_refill
            self._rl_tokens = min(
                self._rl_max_tokens,
                self._rl_tokens + elapsed * self._rl_refill_rate
            )
            self._rl_last_refill = now
            if self._rl_tokens < 1.0:
                wait = (1.0 - self._rl_tokens) / self._rl_refill_rate
                logger.debug(f"[RateLimiter] throttling {wait:.3f}s — tokens={self._rl_tokens:.2f}")
                await asyncio.sleep(wait)
                self._rl_tokens = 0.0
            else:
                self._rl_tokens -= 1.0

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
        await self._throttle()
        try:
            if self._market_api is None:
                return float(self._ltp_cache.get(symbol, 0.0))
            resp = await asyncio.to_thread(self._market_api.ltp, symbol, api_version="2.0")
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
            try:
                from core.risk_manager import risk_manager as _rm
                _rm.record_api_failure()
            except Exception:
                pass
            return float(self._ltp_cache.get(symbol, 0.0))
        except Exception as e:
            logger.error(f"get_ltp unexpected error for {symbol}: {e}")
            return float(self._ltp_cache.get(symbol, 0.0))

    async def get_option_chain(self, instrument_key: str, expiry: str) -> list:
        """
        Fetch the live option chain (with Greeks) for instrument_key/expiry.
        Returns one dict per strike: strike, ce_ltp, ce_delta, ce_oi, ce_bid, ce_ask,
        pe_ltp, pe_delta, pe_oi, pe_bid, pe_ask. Returns [] on any failure --
        callers must handle gracefully and fall back to non-delta logic.
        """
        try:
            await self._throttle()
            import upstox_client as _uc
            options_api = _uc.OptionsApi(_uc.ApiClient(self._configuration))
            resp = options_api.get_put_call_option_chain(instrument_key, expiry)
            if not resp or not resp.data:
                logger.warning(f"[upstox] get_option_chain: empty response for {instrument_key} {expiry}")
                return []

            chain = []
            for row in resp.data:
                ce = getattr(row, "call_options", None)
                pe = getattr(row, "put_options", None)
                ce_greeks = getattr(ce, "option_greeks", None) if ce else None
                pe_greeks = getattr(pe, "option_greeks", None) if pe else None
                ce_market = getattr(ce, "market_data", None) if ce else None
                pe_market = getattr(pe, "market_data", None) if pe else None
                chain.append({
                    "strike":   getattr(row, "strike_price", 0.0) or 0.0,
                    "ce_ltp":   getattr(ce_market, "ltp", 0.0) or 0.0,
                    "ce_delta": getattr(ce_greeks, "delta", 0.0) or 0.0,
                    "ce_oi":    getattr(ce_market, "oi", 0.0) or 0.0,
                    "ce_bid":   getattr(ce_market, "bid_price", 0.0) or 0.0,
                    "ce_ask":   getattr(ce_market, "ask_price", 0.0) or 0.0,
                    "pe_ltp":   getattr(pe_market, "ltp", 0.0) or 0.0,
                    "pe_delta": getattr(pe_greeks, "delta", 0.0) or 0.0,
                    "pe_oi":    getattr(pe_market, "oi", 0.0) or 0.0,
                    "pe_bid":   getattr(pe_market, "bid_price", 0.0) or 0.0,
                    "pe_ask":   getattr(pe_market, "ask_price", 0.0) or 0.0,
                })
            return chain
        except ApiException as e:
            logger.error(f"[upstox] get_option_chain failed: {e}")
            return []
        except Exception as e:
            logger.error(f"[upstox] get_option_chain unexpected error: {e}")
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
            await self._throttle()
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
            try:
                from core.risk_manager import risk_manager as _rm
                _rm.record_api_failure()
            except Exception:
                pass
            return OrderResponse(order_id="", status="REJECTED", message=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._throttle()
            self._order_api.cancel_order(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except ApiException as e:
            logger.error(f"cancel_order failed for {order_id}: {e}")
            try:
                from core.risk_manager import risk_manager as _rm
                _rm.record_api_failure()
            except Exception:
                pass
            return False

    async def get_positions(self) -> list:
        try:
            await self._throttle()
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
            try:
                from core.risk_manager import risk_manager as _rm
                _rm.record_api_failure()
            except Exception:
                pass
            return []

    async def get_orders(self) -> list:
        try:
            try:
                await self._throttle()
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
            await self._throttle()
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

    def _close_ws_connection(self):
        """Thread-safe close of the active WebSocket connection, if any.
        Schedules the close on the asyncio loop running in the WS thread."""
        if self._ws_loop and self._ws_conn:
            try:
                asyncio.run_coroutine_threadsafe(self._ws_conn.close(), self._ws_loop)
            except Exception:
                pass

    def _send_subscribe(self, instrument_keys, mode="full"):
        """Thread-safe: send an additive subscribe request over the already-open
        WS connection, instead of tearing down and reconnecting (which caused
        multiple overlapping connections and tripped Upstox's per-account limit)."""
        if not (self._ws_loop and self._ws_conn):
            return
        import json as _json, uuid as _uuid
        req = {
            "guid": str(_uuid.uuid4()),
            "method": "sub",
            "data": {"instrumentKeys": instrument_keys, "mode": mode},
        }
        payload = _json.dumps(req).encode("utf-8")
        try:
            asyncio.run_coroutine_threadsafe(self._ws_conn.send(payload), self._ws_loop)
            logger.info(f"Sent additive subscribe for: {instrument_keys}")
        except Exception as e:
            logger.error(f"[upstox] _send_subscribe failed: {e}")

    def subscribe_ticks(self, symbols: list, callback) -> None:
        # WS instrument cap enforcement (Phase 3 audit fix, 2026-09). Compute
        # how many genuinely NEW symbols this call would add, and refuse the
        # whole call if that would push total subscribed instruments over the
        # cap -- refusing loudly (with an alert) beats silently going over
        # Upstox's ~100-instrument ceiling, which would otherwise just mean
        # some strikes stop receiving ticks with no visible symptom until an
        # SL fails to fire.
        new_syms_check = [s for s in symbols if s not in self._tick_callbacks]
        total_after = len(self._tick_callbacks) + len(new_syms_check)
        if total_after > MAX_INSTRUMENTS_PER_WS:
            logger.critical(
                f"[upstox] WS instrument cap would be exceeded: "
                f"{len(self._tick_callbacks)} existing + {len(new_syms_check)} new "
                f"= {total_after} > {MAX_INSTRUMENTS_PER_WS}. Refusing this subscribe "
                f"call entirely -- none of {symbols} will receive live ticks until "
                f"capacity is freed (unsubscribe stale symbols) or a second WS "
                f"connection is added."
            )
            try:
                from core.alerting import alert_ws_instrument_cap
                alert_ws_instrument_cap(
                    existing=len(self._tick_callbacks),
                    requested=len(new_syms_check),
                    cap=MAX_INSTRUMENTS_PER_WS,
                )
            except Exception as e:
                logger.error(f"[upstox] alert_ws_instrument_cap itself failed: {e}")
            return

        new_syms = []
        for sym in symbols:
            if sym not in self._tick_callbacks:
                self._tick_callbacks[sym] = []
                new_syms.append(sym)
            if callback not in self._tick_callbacks[sym]:
                self._tick_callbacks[sym].append(callback)

        all_symbols = list(self._tick_callbacks.keys())

        if self._ws_thread and self._ws_thread.is_alive():
            # WS already running — send an additive subscribe instead of restarting
            # the whole connection (restarting caused overlapping connections and
            # tripped Upstox's per-account connection limit, seen as 403s).
            if new_syms:
                self._send_subscribe(new_syms)
            return

        def _run_streamer():
            """Custom asyncio WebSocket client. The Upstox SDK's sync `websocket-client`
            transport gets a persistent 403 on the current v3 feed — this connects
            directly with the modern `websockets` library instead (confirmed working),
            while reusing the SDK's exact subscribe-request format and protobuf decoding."""
            import ssl as _ssl
            import json as _json
            import uuid as _uuid
            import time as _time
            import websockets as _websockets
            from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as _pb2
            from google.protobuf import json_format as _json_format

            def _build_subscribe_request(instrument_keys, mode="full"):
                req = {
                    "guid": str(_uuid.uuid4()),
                    "method": "sub",
                    "data": {"instrumentKeys": instrument_keys, "mode": mode},
                }
                return _json.dumps(req).encode("utf-8")

            def _process_tick_message(data_dict):
                try:
                    self._last_tick_time = _time.time()
                    self._ws_healthy     = True
                    feeds = data_dict.get("feeds", {})
                    for sym, feed_data in feeds.items():
                        ltp = 0.0
                        bid_price = 0.0
                        ask_price = 0.0
                        try:
                            ff = feed_data.get("fullFeed", {}) or {}
                            source = ff.get("marketFF") or ff.get("indexFF") or {}

                            ltpc = source.get("ltpc", {}) or {}
                            ltp = float(ltpc.get("ltp", 0) or 0)
                            if not ltp:
                                ltpc2 = feed_data.get("ltpc", {}) or {}
                                ltp = float(ltpc2.get("ltp", 0) or 0)

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
                                self._last_index_tick_time = _time.time()
                                from core.state_store import state_store
                                state_store.update_nifty_price(float(ltp))

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

            def _alert_ws_down(msg):
                try:
                    import pytz
                    from datetime import time as dtime
                    now = datetime.now(pytz.timezone("Asia/Kolkata"))
                    market_open = dtime(9, 15) <= now.time() <= dtime(15, 15)
                    is_weekday = now.weekday() < 5
                    uptime = _time.time() - self._start_time
                    if market_open and is_weekday and uptime > 90:
                        from core.alerting import alert_websocket_down
                        alert_websocket_down(msg)
                except Exception:
                    pass

            async def _stream():
                # TLS verification fix (Phase 3 audit fix, 2026-09). Previously
                # check_hostname=False + verify_mode=CERT_NONE disabled certificate
                # validation entirely on the connection carrying live price data
                # that SL/trailing-profit logic acts on -- open to MITM tick
                # injection on any untrusted network path. Restored to proper
                # verification using the system's default CA trust store.
                #
                # NOT independently tested against a live Upstox connection --
                # this environment has no network path to api.upstox.com to
                # verify the handshake actually succeeds with strict verification.
                # If this was originally set to CERT_NONE to work around a real
                # cert-chain problem (e.g. a stale CA bundle on this VPS), that
                # will resurface here as repeated connection failures. The
                # existing reconnect loop below will retry with backoff and
                # alert_websocket_down() will fire after ~90s of market-hours
                # downtime either way, so a regression here is visible rather
                # than silent -- but test this specific change in a low-stakes
                # window (pre-market, or watched closely) rather than trusting
                # it blind. If it does fail: check `openssl s_client -connect
                # api.upstox.com:443` on this server first, and consider
                # `sudo apt install --reinstall ca-certificates` before
                # reverting to CERT_NONE.
                # TEMPORARY ROLLBACK 2026-09-04: strict cert verification caused
                # continuous WS disconnect/reconnect flapping during live market
                # hours (see incident notes) -- reverted to restore stable ticks
                # while root cause is investigated properly, off-hours.
                ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE
                url = "wss://api.upstox.com/v3/feed/market-data-feed"
                headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "*/*"}
                backoff = 5  # reconnect delay (seconds) — grows on repeated failures to avoid rate-limit triggers

                while True:
                    try:
                        async with _websockets.connect(
                            url, additional_headers=headers, ssl=ssl_ctx, open_timeout=10
                        ) as ws:
                            self._ws_conn = ws
                            logger.info("WebSocket connected to Upstox")
                            backoff = 5  # reset backoff on a successful connection
                            await asyncio.sleep(3)  # Wait for all strategies to register their symbols
                            current_symbols = list(self._tick_callbacks.keys())
                            subscribe_syms = current_symbols if current_symbols else all_symbols
                            await ws.send(_build_subscribe_request(subscribe_syms, "full"))
                            logger.info(f"Subscribed to symbols: {subscribe_syms}")

                            async for message in ws:
                                try:
                                    decoded = _pb2.FeedResponse.FromString(message)
                                    data_dict = _json_format.MessageToDict(decoded)
                                    _process_tick_message(data_dict)
                                except Exception as e:
                                    logger.error(f"Tick processing error: {e}", exc_info=True)
                    except Exception as e:
                        logger.error(f"WebSocket error: {e}")
                        self._ws_healthy = False
                        logger.warning("WebSocket closed")
                        _alert_ws_down("WebSocket closed — auto-reconnect in progress")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)  # exponential backoff, capped at 60s

            loop = asyncio.new_event_loop()
            self._ws_loop = loop
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_stream())

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
                    if self._last_index_tick_time == 0:
                        continue
                    elapsed = time.time() - self._last_index_tick_time
                    if elapsed > 60:
                        _fail_count += 1
                        self._ws_healthy = False
                        logger.warning(f"[heartbeat] No ticks for {elapsed:.0f}s — failure #{_fail_count}")
                        if _fail_count == 3:
                            try:
                                import pytz as _ptz
                                from datetime import time as _dtime
                                _now = datetime.now(_ptz.timezone("Asia/Kolkata"))
                                _mkt_open = _dtime(9, 15) <= _now.time() <= _dtime(15, 15)
                                _is_weekday = _now.weekday() < 5
                                if _mkt_open and _is_weekday:
                                    from core.alerting import alert_websocket_down
                                    alert_websocket_down(f"No ticks for {elapsed:.0f}s — 3 consecutive failures")
                            except Exception:
                                pass
                        if _fail_count >= 5:
                            logger.warning("[heartbeat] CRITICAL — forcing WebSocket reconnect")
                            try:
                                import pytz as _ptz2
                                from datetime import time as _dtime2
                                _now2 = datetime.now(_ptz2.timezone("Asia/Kolkata"))
                                _mkt_open2 = _dtime2(9, 15) <= _now2.time() <= _dtime2(15, 15)
                                _is_weekday2 = _now2.weekday() < 5
                                if _mkt_open2 and _is_weekday2:
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
                            self._close_ws_connection()
                            _fail_count = 0
                    else:
                        _fail_count = 0  # reset on healthy tick
                        self._ws_healthy = True
                    # ── REST fallback: push ticks via REST when WS is unhealthy ──
                    if not self._ws_healthy and self._tick_callbacks:
                        try:
                            import asyncio as _asyncio
                            _all_syms = list(self._tick_callbacks.keys())
                            logger.info(f"[heartbeat] REST fallback: polling {len(_all_syms)} symbols")
                            for _sym in _all_syms:
                                try:
                                    _resp = self._market_api.ltp(_sym, api_version="2.0")
                                    if _resp and _resp.data:
                                        _keys = list(_resp.data.keys())
                                        if _keys:
                                            _ltp = float(_resp.data[_keys[0]].last_price)
                                            if _ltp > 0:
                                                self._ltp_cache[_sym] = _ltp
                                                self._last_tick_time  = time.time()
                                                _tick = Tick(
                                                    symbol=_sym,
                                                    last_price=_ltp,
                                                    timestamp=datetime.now().isoformat(),
                                                )
                                                for _cb in self._tick_callbacks.get(_sym, []):
                                                    try:
                                                        _cb(_tick)
                                                    except Exception:
                                                        pass
                                                logger.debug(f"[heartbeat] REST tick: {_sym} @ {_ltp}")
                                except Exception as _se:
                                    logger.warning(f"[heartbeat] REST fallback failed for {_sym}: {_se}")
                            time.sleep(2)  # throttle REST polling to ~0.5 req/s
                        except Exception as _fe:
                            logger.warning(f"[heartbeat] REST fallback error: {_fe}")
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

    def unsubscribe_ticks(self, symbols: list, callback=None) -> None:
        """Remove a callback's subscription to the given symbols.

        If `callback` is given, only that callback is removed from each
        symbol's list -- other strategies still watching the same symbol
        (e.g. shared NSE_INDEX|Nifty 50) keep receiving ticks. The symbol
        entry itself is only dropped once its callback list is empty.

        If `callback` is omitted (legacy behavior, kept for any caller not
        yet updated), the whole symbol entry is wiped -- this is the old
        buggy behavior that could silently cut off other strategies sharing
        the same symbol, so a warning is logged to make that visible.
        """
        for sym in symbols:
            if callback is None:
                self._tick_callbacks.pop(sym, None)
                logger.warning(
                    f"unsubscribe_ticks({sym}) called without a callback -- "
                    f"removing ALL subscribers for this symbol, which may "
                    f"silently cut off other strategies sharing it."
                )
                continue
            callbacks = self._tick_callbacks.get(sym)
            if not callbacks:
                continue
            if callback in callbacks:
                callbacks.remove(callback)
            if not callbacks:
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
        order_type: str = "SELL",  # "SELL" = we sold (short), exit is BUY.
                                    # "BUY"  = we bought (long), exit is SELL.
        trailing_gap: float = 0.25,
        sl_pct: float = 0.15,       # SL distance from entry, direction depends on order_type
    ) -> str:
        """
        CRITICAL BUGFIX 2026-09: this function has never successfully placed a
        real GTT order, in any direction, live or otherwise, since it was
        written. Verified directly against the installed upstox_client SDK
        (upstox-python-sdk==2.29.0). It had FOUR independent bugs, any one of
        which alone would have been fatal:

        1. GttRule.strategy only accepts ["ENTRY", "STOPLOSS", "TARGET"].
           Old code passed "TRAILING_STOP_LOSS" -- invalid, raises ValueError
           the instant GttRule() is constructed, before any network call.

        2. GttRule.trigger_type only accepts ["ABOVE", "BELOW", "IMMEDIATE"].
           Old code passed "RISING" -- also invalid, same failure mode.
           (For a standalone STOPLOSS-only leg -- no ENTRY leg, since we
           already hold the position -- the correct value is always
           "IMMEDIATE": the leg is live the moment it's placed, and
           trigger_price alone determines whether it needs price to rise or
           fall to fire. ABOVE/BELOW is for a GTT's ENTRY leg, which waits
           for price to cross a threshold before an order is even placed --
           doesn't apply here. Confirmed against Upstox's own official
           sample code for a standalone stop-loss GTT: GttRule(strategy=
           "STOPLOSS", trigger_type="IMMEDIATE", trigger_price=...).)

        3. There is no `GttApi` class in this SDK version at all -- GTT
           order placement lives on `OrderApiV3` (imported here as
           `upstox_client.OrderApiV3`, already instantiated once in login()
           as `self._order_api` -- reused here rather than constructing a
           fresh client). `upstox_client.GttApi(client)` would have raised
           AttributeError. Also, `place_gtt_order()` takes no `api_version`
           kwarg (unlike `get_profile`, which does) -- passing one raises
           TypeError.

        4. The response's actual shape is `resp.data.gtt_order_ids` (a
           list[str]), not `resp.data.id`. Old code's `resp.data.id` would
           have raised AttributeError had it ever gotten this far.

        Net effect: every previous call to this function failed at bug #1
        or #2, was caught by the broad except below, logged as "Failed to
        place GTT", and returned "". survivor.py's retry-then-alert-then-
        auto-close fallback (if reached with a live SELL fill) would
        therefore always have fired -- or if never reached, GTTs were
        simply never armed at all despite the code appearing to do so.

        Direction handling (unchanged from the previous fix, still correct):
          SELL entry (short) -> exit BUY  | trigger_price = entry*(1+sl_pct)
          BUY  entry (long)  -> exit SELL | trigger_price = entry*(1-sl_pct)
        """
        try:
            if order_type == "SELL":
                exit_transaction = "BUY"
                trigger_price     = round(entry_price * (1 + sl_pct), 1)
            elif order_type == "BUY":
                exit_transaction = "SELL"
                trigger_price     = round(entry_price * (1 - sl_pct), 1)
            else:
                raise ValueError(f"place_gtt_trailing_sl: unsupported order_type={order_type!r}")

            rule = upstox_client.GttRule(
                strategy="STOPLOSS",
                trigger_type="IMMEDIATE",
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

            resp = self._order_api.place_gtt_order(req)
            gtt_ids = resp.data.gtt_order_ids if resp and resp.data else []
            gtt_id = gtt_ids[0] if gtt_ids else ""
            logger.info(
                f"[GTT] Trailing SL placed | {instrument_key} | order_type={order_type} | "
                f"trigger={trigger_price} | trailing_gap={trailing_gap} | id={gtt_id}"
            )
            return str(gtt_id)
        except Exception as e:
            logger.warning(f"[GTT] Failed to place GTT: {e}")
            return ""

    async def cancel_gtt_order(self, gtt_order_id: str) -> bool:
        """Cancel a previously-placed GTT trailing-SL order at Upstox.

        Call this whenever a trade closes through any path OTHER than the
        GTT itself firing (in-memory SL, profit target, EOD close, manual
        stop, etc.) -- otherwise the GTT order stays live at the broker
        for a position that no longer exists.

        Safe to call with an empty/blank gtt_order_id (e.g. GTT placement
        never succeeded for this trade) -- just returns False without
        making any API call.
        """
        if not gtt_order_id:
            return False
        try:
            # Same bug class as place_gtt_trailing_sl's fix above: there is no
            # GttApi class in this SDK version (upstox-python-sdk==2.29.0) --
            # GTT cancel lives on OrderApiV3, same as placement. The old code
            # here (upstox_client.GttApi(client).cancel_gtt_order(...,
            # api_version="2.0")) would have raised AttributeError on the
            # GttApi(client) line itself, caught by the broad except below,
            # silently returning False on every call -- meaning a GTT placed
            # via the now-fixed place_gtt_trailing_sl was never actually
            # cancelled when a position closed through any other path,
            # leaving a stale stop-loss order live at the broker for a
            # position that no longer exists. Found while reviewing the
            # neighboring fix, not independently reported -- verified
            # cancel_gtt_order() exists on OrderApiV3 (signature: body, no
            # api_version kwarg) via the installed SDK directly, same as the
            # placement fix above.
            body = upstox_client.GttCancelOrderRequest(gtt_order_id=gtt_order_id)
            await self._throttle()
            resp = self._order_api.cancel_gtt_order(body=body)
            logger.info(f"[GTT] Cancelled | id={gtt_order_id}")
            return True
        except ApiException as e:
            logger.warning(f"[GTT] Cancel failed for {gtt_order_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"[GTT] Cancel unexpected error for {gtt_order_id}: {e}")
            return False
            