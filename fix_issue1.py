import re

# ── Fix 1: wave_extractor._on_tick_sync — check stop flag before queuing ──
path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old = """    def _on_tick_sync(self, tick: Tick) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._loop)
        else:
            logger.error("[wave_extractor] Event loop not running")"""

new = """    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._loop)
        else:
            logger.error("[wave_extractor] Event loop not running")"""

count = content.count(old)
print(f'wave_extractor _on_tick_sync: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)

# ── Fix 2: wave_extractor._on_order_update — check stop flag before queuing ──
content = open(path).read()

old2 = """    def _on_order_update(self, update: dict) -> None:
        try:
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_order_update(update), self._loop
            )"""

new2 = """    def _on_order_update(self, update: dict) -> None:
        try:
            if self._stop_flag:
                return
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_order_update(update), self._loop
            )"""

count2 = content.count(old2)
print(f'wave_extractor _on_order_update: {count2}')
content = content.replace(old2, new2)
open(path, 'w').write(content)
print('wave_extractor done')

# ── Fix 3: survivor._on_tick_sync — check stop flag before queuing ──
path2 = '/home/ubuntu/trading-algo/strategy/survivor.py'
content2 = open(path2).read()

old3 = """    def _on_tick_sync(self, tick: Tick) -> None:"""
new3 = """    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return"""

# Only replace first occurrence (main tick handler)
count3 = content2.count(old3)
print(f'survivor _on_tick_sync occurrences: {count3}')
content2 = content2.replace(old3, new3, 1)
open(path2, 'w').write(content2)
print('survivor done')

# ── Fix 4: main.py — add SIGTERM handler for graceful shutdown ──
path3 = '/home/ubuntu/trading-algo/main.py'
content3 = open(path3).read()

old4 = """import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import subprocess
import sys
from pathlib import Path"""

new4 = """import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import subprocess
import sys
from pathlib import Path"""

old5 = """if __name__ == "__main__":
    # Auto-free port 8081 (Windows + Linux)
    free_port(8081)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("� System stopped by user.")"""

new5 = """if __name__ == "__main__":
    # Auto-free port 8081 (Windows + Linux)
    free_port(8081)

    def _handle_sigterm(signum, frame):
        logger.info("��� SIGTERM received ��— shutting down gracefully")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("� System stopped by user.")
    except SystemExit:
        logger.info("��� System exit triggered.")"""

c4 = content3.count(old4)
c5 = content3.count(old5)
print(f'main.py import block: {c4}, main block: {c5}')
content3 = content3.replace(old4, new4).replace(old5, new5)
open(path3, 'w').write(content3)
print('main.py done')
