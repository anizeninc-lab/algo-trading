#!/usr/bin/env python3
"""
test_gex_data_apis.py

Standalone, READ-ONLY test script. Does not place orders, does not touch
the trading bot's state, does not modify any files. Just verifies:
  1. Upstox's Option Greeks API returns real IV/gamma/delta/theta/vega for
     a real Nifty ATM option -- confirming we don't need to build our own
     Black-Scholes calculator.
  2. Upstox's Intraday Candle API returns real 5-min OHLC candles for the
     Nifty index -- confirming we don't need to build our own tick-to-
     candle resampler.

Safe to run any time, market open or closed (closed market may return
fewer/no intraday candles, which is itself useful information).
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.getcwd())

import upstox_client
from core.auto_config import fetch_instruments, find_symbol_from_instruments, get_nearest_tuesday


def main():
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not access_token:
        print("!! UPSTOX_ACCESS_TOKEN not set in environment -- cannot test live.")
        sys.exit(1)

    cfg = upstox_client.Configuration()
    cfg.access_token = access_token
    client = upstox_client.ApiClient(cfg)

    # ── Step 1: find a real, current Nifty ATM CE instrument key ──────────
    print("=== Step 1: Resolving a real Nifty ATM CE instrument key ===")
    try:
        market_api = upstox_client.MarketQuoteApi(client)
        resp = market_api.ltp("NSE_INDEX|Nifty 50", api_version="2.0")
        keys = list(resp.data.keys()) if resp.data else []
        if not keys:
            print("!! Could not fetch NIFTY spot price -- aborting")
            sys.exit(1)
        nifty_price = float(resp.data[keys[0]].last_price)
        atm_strike = round(nifty_price / 50) * 50
        print(f"NIFTY spot: {nifty_price} | ATM strike: {atm_strike}")

        instruments = fetch_instruments()
        if not instruments:
            print("!! fetch_instruments() returned nothing -- aborting")
            sys.exit(1)

        weekly_expiry = get_nearest_tuesday(date.today())
        ce_key = find_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "CE")
        if not ce_key:
            print(f"!! Could not resolve CE instrument key for strike {atm_strike}, expiry {weekly_expiry}")
            sys.exit(1)
        print(f"Resolved CE instrument key: {ce_key} (expiry {weekly_expiry})")
    except Exception as e:
        print(f"!! Step 1 failed: {e}")
        sys.exit(1)

    # ── Step 2: test the Option Greeks API with this real instrument key ──
    print()
    print("=== Step 2: Testing Option Greeks API ===")
    try:
        v3_market_api = upstox_client.MarketQuoteV3Api(client)
        greek_resp = v3_market_api.get_market_quote_option_greek(instrument_key=ce_key)
        print("RAW RESPONSE:")
        print(greek_resp)
    except Exception as e:
        print(f"!! Option Greeks API call failed: {e}")

    # ── Step 3: test the Intraday Candle API with the Nifty index ─────────
    print()
    print("=== Step 3: Testing Intraday Candle API (Nifty index, 5-min) ===")
    try:
        history_api = upstox_client.HistoryV3Api(client)
        candle_resp = history_api.get_intra_day_candle_data(
            instrument_key="NSE_INDEX|Nifty 50",
            unit="minutes",
            interval=5,
        )
        print("RAW RESPONSE:")
        print(candle_resp)
    except Exception as e:
        print(f"!! Intraday Candle API call failed: {e}")

    print()
    print("=== Done. Review the raw responses above for field names/shapes. ===")


if __name__ == "__main__":
    main()