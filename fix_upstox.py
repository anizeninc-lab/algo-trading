# Complete fix for brokers/upstox.py WebSocket issues

with open('brokers/upstox.py', 'r') as f:
    content = f.read()

old_code = '''        def _run_streamer():
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

            _connect()'''

new_code = '''        def _run_streamer():
            import requests as _req
            import websocket as _ws
            import json, time
            from upstox_client.feeder import MarketDataFeedV3_pb2
            from google.protobuf import json_format

            def _get_ws_url():
                """Always fetch a fresh authorized URL — they expire after ~15 min."""
                for attempt in range(5):
                    try:
                        r = _req.get(
                            "https://api.upstox.com/v3/feed/market-data-feed/authorize",
                            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "*/*"},
                            timeout=10
                        )
                        if r.status_code == 200:
                            url = r.json()["data"]["authorizedRedirectUri"]
                            logger.info("WebSocket authorized URL obtained")
                            return url
                        logger.error(f"WebSocket authorize failed: {r.status_code}")
                    except Exception as e:
                        logger.error(f"WebSocket authorize error (attempt {attempt+1}): {e}")
                    time.sleep(3)
                return None

            def _process_feed(data):
                """Process decoded protobuf dict and fire callbacks."""
                feeds = data.get("feeds", {})
                for sym, feed_data in feeds.items():
                    ltp = 0.0
                    try:
                        ff = feed_data.get("fullFeed", {}) or {}
                        source = ff.get("marketFF") or ff.get("indexFF") or {}
                        ltpc = source.get("ltpc", {}) or {}
                        ltp = float(ltpc.get("ltp", 0) or 0)
                        if not ltp:
                            ltpc2 = feed_data.get("ltpc", {}) or {}
                            ltp = float(ltpc2.get("ltp", 0) or 0)
                    except Exception:
                        pass

                    if ltp:
                        self._ltp_cache[sym] = ltp
                        if "Nifty 50" in sym or "NIFTY" in str(sym):
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

            def on_open(ws):
                logger.info("WebSocket connected to Upstox")
                sub_msg = json.dumps({
                    "guid": "rahultrading-sub-001",
                    "method": "sub",
                    "data": {"mode": "full", "instrumentKeys": all_symbols}
                })
                ws.send(sub_msg)
                logger.info(f"Subscribed to symbols: {all_symbols}")

            def on_message(ws, msg):
                try:
                    if isinstance(msg, bytes):
                        # Decode protobuf binary message
                        decoded = MarketDataFeedV3_pb2.FeedResponse.FromString(msg)
                        data = json_format.MessageToDict(decoded)
                        _process_feed(data)
                    else:
                        # Text message — usually market status
                        data = json.loads(msg)
                        logger.debug(f"Text message: {data.get('type', 'unknown')}")
                except Exception as e:
                    logger.error(f"Tick processing error: {e}", exc_info=True)

            def on_error(ws, e):
                logger.error(f"WebSocket error: {e}")

            def on_close(ws, *args):
                logger.warning("WebSocket closed — fetching fresh URL and reconnecting in 5s...")
                time.sleep(5)
                _connect()  # Always get fresh URL on reconnect

            def _connect():
                ws_url = _get_ws_url()
                if not ws_url:
                    logger.error("Could not get authorized WebSocket URL after retries")
                    time.sleep(30)
                    _connect()
                    return
                logger.info(f"Connecting to: {ws_url[:80]}...")
                wsa = _ws.WebSocketApp(
                    ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._streamer = wsa
                wsa.run_forever(ping_interval=20, ping_timeout=10)

            _connect()'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open('brokers/upstox.py', 'w') as f:
        f.write(content)
    print("✅ WebSocket fix applied")
else:
    print("❌ Pattern not found — checking what's there")
    idx = content.find("def _run_streamer")
    print(repr(content[idx:idx+100]))
