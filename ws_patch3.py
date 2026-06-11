# Fix reconnect to fetch fresh authorized URL each time

with open('brokers/upstox.py', 'r') as f:
    content = f.read()

old_close = '''            def on_close(ws, *args):
                logger.warning("WebSocket closed — reconnecting in 5s...")
                if self._ws_thread and self._ws_thread.is_alive():
                    time.sleep(5)
                    _connect()'''

new_close = '''            def on_close(ws, *args):
                logger.warning("WebSocket closed — reconnecting in 5s...")
                time.sleep(5)
                _connect()'''

if old_close in content:
    content = content.replace(old_close, new_close)
    print("✅ on_close fixed")
else:
    print("❌ on_close pattern not found")

# Also fix _connect to always fetch fresh URL
old_connect = '''            def _connect():
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
                wsa.run_forever(ping_interval=30, ping_timeout=10)'''

new_connect = '''            def _connect():
                # Always fetch a fresh authorized URL — they expire after ~15 minutes
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
                wsa.run_forever(ping_interval=20, ping_timeout=10)'''

if old_connect in content:
    content = content.replace(old_connect, new_connect)
    print("✅ _connect fixed")
else:
    print("❌ _connect pattern not found")

with open('brokers/upstox.py', 'w') as f:
    f.write(content)
print("Done")
