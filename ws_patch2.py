import re

with open('brokers/upstox.py', 'r') as f:
    content = f.read()

old_restart = '''        if self._ws_thread and self._ws_thread.is_alive():
            logger.info(f"Restarting WebSocket to add new symbols: {symbols}")
            try:
                if self._streamer:
                    self._streamer.close()
            except Exception:
                pass
            self._ws_thread = None
            self._streamer = None
            import time; time.sleep(1)
        all_symbols = list(self._tick_callbacks.keys())'''

new_restart = '''        all_symbols = list(self._tick_callbacks.keys())

        # If WebSocket already running, just send new subscription — don't restart
        if self._ws_thread and self._ws_thread.is_alive() and self._streamer:
            try:
                import json as _json
                sub_msg = _json.dumps({
                    "guid": "rahultrading-sub-update",
                    "method": "sub",
                    "data": {
                        "mode": "full",
                        "instrumentKeys": all_symbols
                    }
                })
                self._streamer.send(sub_msg)
                logger.info(f"Added symbols to existing WebSocket: {symbols}")
                return
            except Exception as e:
                logger.warning(f"Could not add to existing WebSocket ({e}), restarting...")
                try:
                    self._streamer.close()
                except Exception:
                    pass
                self._ws_thread = None
                self._streamer = None
                import time; time.sleep(1)'''

if old_restart in content:
    content = content.replace(old_restart, new_restart)
    with open('brokers/upstox.py', 'w') as f:
        f.write(content)
    print("Patch 2 applied successfully")
else:
    print("Pattern not found")
    idx = content.find("Restarting WebSocket")
    if idx > 0:
        print(repr(content[idx-100:idx+200]))
