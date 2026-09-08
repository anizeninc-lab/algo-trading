# brokers/__init__.py
# Factory that returns the correct broker adapter based on .env config.

import os

from dotenv import load_dotenv

from brokers.base import AbstractBrokerGateway

load_dotenv()


def get_broker() -> AbstractBrokerGateway:
    """
    Constructs a new adapter instance on every call -- NOT a singleton/cached
    factory. This is safe today because get_broker() is called exactly once,
    at startup in main.py (`broker = get_broker()`), and that single instance
    is explicitly threaded through to every strategy, the dashboard API
    (dashboard_api.broker_ref), and market_context -- confirmed 2026-09
    (Phase 3 audit check) by grepping every call site.

    This matters because UpstoxAdapter's rate limiter (_throttle()) is
    per-instance state -- two live UpstoxAdapter objects would each enforce
    their own independent request budget, and could collectively exceed
    Upstox's actual per-user rate limit even though each individually
    "throttles" correctly in isolation. If a second call site to get_broker()
    is ever added (e.g. a new entry point, a test harness, a second dashboard
    process), route it through the SAME broker instance rather than calling
    this factory again -- or turn this into a real singleton/cache if that
    becomes hard to guarantee by convention alone.
    """
    name = os.getenv("BROKER_NAME", "").lower().strip()

    if name == "upstox":
        from brokers.upstox import UpstoxAdapter

        return UpstoxAdapter()

    if name == "icicidirect":
        from brokers.icicidirect import ICICIDirectAdapter

        return ICICIDirectAdapter()

    raise ValueError(
        f"Unsupported broker '{name}'. "
        f"Set BROKER_NAME=upstox or icicidirect in your .env file."
    )
