path = '/home/ubuntu/trading-algo/brokers/upstox.py'
content = open(path).read()

old = """        self._ws_thread = threading.Thread(target=_run_streamer, daemon=True)
        self._ws_thread.start()
        logger.info(f"WebSocket started with symbols: {all_symbols}")"""

new = """        self._ws_thread = threading.Thread(target=_run_streamer, daemon=True)
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
                        if prev != "COMPLETE" and getattr(o, "status", "") == "COMPLETE":
                            logger.info(f"[ORDER POLL] Order filled: {oid}")
                            self._order_callback({
                                "order_id": oid,
                                "status": "COMPLETE",
                                "average_price": getattr(o, "price", 0),
                                "symbol": getattr(o, "symbol", ""),
                                "order_type": getattr(o, "order_type", ""),
                            })
                        self._known_order_states[oid] = getattr(o, "status", "")
                except Exception as e:
                    logger.warning(f"[ORDER POLL] Error: {e}")

        self._order_poll_thread = threading.Thread(target=_poll_orders, daemon=True)
        self._order_poll_thread.start()
        logger.info("Order polling thread started")"""

count = content.count(old)
print(f'Block found: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')
