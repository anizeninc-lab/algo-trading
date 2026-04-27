# brokers/__init__.py
# Factory that returns the correct broker adapter based on .env config.

import os

from dotenv import load_dotenv

from brokers.base import AbstractBrokerGateway

load_dotenv()


def get_broker() -> AbstractBrokerGateway:
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
