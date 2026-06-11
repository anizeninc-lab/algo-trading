# This patches _run_streamer in brokers/upstox.py
# to use direct WebSocket connection instead of MarketDataStreamerV3 SDK

NEW_STREAMER = '''
        def _run_streamer():
            import requests as _req
            import websocket as _ws
            import json, time, threading

            def _get_ws_url():
                try:
                    r = _req.get(
                        "https://api.upstox.com/v3/feed/market-data-feed/authorize",
                        headers={"Authorization": f"Bearer {self.access_token}", "Accept": "*/*"},
                        timeout=10
                    )
                    if r.status_code == 200:
                        return r.json()["data"]["authorizedRedirectUri"]
                    logger.error(f"WebSocket authorize failed: {r.status_code} {r.text[:200]}")
                except Exception as e:
                    logger.error(f"WebSocket authorize error: {e}")
                return None

            def on_open(ws):
                logger.info("WebSocket connected to Upstox")
                # Subscribe to symbols
                import struct
                sub_msg = json.dumps({
                    "guid": "rahultrading-sub-001",
                    "method": "sub",
                    "data": {
                        "mode": "full",
                        "instrumentKeys": all_symbols
                    }
                })
                ws.send(sub_msg)
                logger.info(f"Subscribed to symbols: {all_symbols}")

            def on_message(ws, msg):
                try:
                    if isinstance(msg, bytes):
                        # Try JSON decode first
                        try:
                            data = json.loads(msg.decode("utf-8"))
                        except Exception:
                            return
                    else:
                        data = json.loads(msg)

                    msg_type = data.get("type", "")

                    if msg_type == "market_info":
                        logger.info(f"Market status received")
                        return

                    if msg_type == "live_feed":
                        feeds = data.get("feeds", {})
                        for sym, feed_data in feeds.items():
                            ltp = 0.0
                            try:
                                # Try fullFeed path
                                ff = feed_data.get("fullFeed", {}) or {}
                                source = ff.get("marketFF") or ff.get("indexFF") or {}
                                ltpc = source.get("ltpc", {}) or {}
                                ltp = float(ltpc.get("ltp", 0) or 0)

                                # Fallback to ltpc direct
                                if not ltp:
                                    ltpc2 = feed_data.get("ltpc", {}) or {}
                                    ltp = float(ltpc2.get("ltp", 0) or 0)
                            except Exception:
                                pass

                            if ltp:
                                self._ltp_cache[sym] = ltp
                                if "Nifty 50" in sym or "NIFTY" in sym:
                                    from core.state_store import state_store
                                    state_store.update_nifty_price(float(ltp))
                                tick = Tick(
                                    symbol=sym,
                                    last_price=float(ltp),
                                    timestamp=datetime.now().isoformat(),
                                )
                                for cb in self._tick_callbacks.get(sym, []):
                                    try:
                                        cb(tick)
                                    except Exception as e:
                                        logger.error(f"Callback error for {sym}: {e}")

                except Exception as e:
                    logger.error(f"Tick processing error: {e}", exc_info=True)

            def on_error(ws, e):
                logger.error(f"WebSocket error: {e}")

            def on_close(ws, *args):
                logger.warning("WebSocket closed — reconnecting in 5s...")
                if self._ws_thread and self._ws_thread.is_alive():
                    time.sleep(5)
                    _connect()

            def _connect():
                ws_url = _get_ws_url()
                if not ws_url:
                    logger.error("Could not get authorized WebSocket URL — retrying in 10s")
                    time.sleep(10)
                    _connect()
                    return
                logger.info(f"Connecting to WebSocket: {ws_url[:80]}...")
                wsa = _ws.WebSocketApp(
                    ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._streamer = wsa
                wsa.run_forever(ping_interval=30, ping_timeout=10)

            _connect()
'''

# Read the file
with open('/home/ubuntu/trading-algo/brokers/upstox.py', 'r') as f:
    content = f.read()

# Find and replace the _run_streamer function
old_start = "        def _run_streamer():\n            self._streamer = upstox_client.MarketDataStreamerV3("
old_end = "            self._streamer.connect()\n"

start_idx = content.index(old_start)
end_idx = content.index(old_end) + len(old_end)

new_content = content[:start_idx] + NEW_STREAMER + "\n" + content[end_idx:]

with open('/home/ubuntu/trading-algo/brokers/upstox.py', 'w') as f:
    f.write(new_content)

print("✅ Patch applied successfully")
print(f"Original length: {len(content)} chars")
print(f"New length: {len(new_content)} chars")
