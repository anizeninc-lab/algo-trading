"""
patch_iv_fix_2026-09-13.py

Fixes LESSON-F13: put_calendar has never once fired a real entry
because brokers/upstox.py's get_option_chain() never extracted ce_iv/
pe_iv from the option_greeks object, even though the same object was
already being read correctly for delta. Confirmed against the real
installed SDK (upstox-python-sdk==2.29.0): option_greeks is an
AnalyticsData object with fields vega/theta/gamma/delta/iv/pop -- iv
was simply never read.

Run from the repo root:
    python3 patch_iv_fix_2026-09-13.py
Then verify:
    python3 -m py_compile brokers/upstox.py && echo SYNTAX OK
"""


def patch_file(path, old, new, label):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    count = content.count(old)
    if count == 0:
        print(f'  [SKIP/FAIL] {label}: old string not found in {path}. Already patched, or file differs -- check manually.')
        return
    if count > 1:
        print(f'  [WARN] {label}: found {count} times in {path}, expected 1.')
    content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  [OK] {label}: applied to {path}')


docstring_old = '''        """
        Fetch the live option chain (with Greeks) for instrument_key/expiry.
        Returns one dict per strike: strike, ce_ltp, ce_delta, ce_oi, ce_bid, ce_ask,
        pe_ltp, pe_delta, pe_oi, pe_bid, pe_ask. Returns [] on any failure --
        callers must handle gracefully and fall back to non-delta logic.
        """'''

docstring_new = '''        """
        Fetch the live option chain (with Greeks) for instrument_key/expiry.
        Returns one dict per strike: strike, ce_ltp, ce_delta, ce_iv, ce_oi,
        ce_bid, ce_ask, pe_ltp, pe_delta, pe_iv, pe_oi, pe_bid, pe_ask.
        Returns [] on any failure -- callers must handle gracefully and fall
        back to non-delta logic.

        BUGFIX 2026-09-13: ce_iv/pe_iv were missing entirely until now, even
        though the same option_greeks object they come from (verified
        against upstox-python-sdk==2.29.0's AnalyticsData model: fields
        vega/theta/gamma/delta/iv/pop) was already being read for delta a
        few lines below. put_calendar.py's entire entry logic depends on
        front_leg.get("pe_iv", 0.0) / back_leg.get("pe_iv", 0.0) -- with the
        key never present, it silently defaulted to 0.0 on every single
        cycle, in every session, since put_calendar was added. Confirmed via
        logs/trading.log: "Front IV unavailable — skipping this cycle" fires
        every cycle, with zero "Entry check" lines ever printed -- i.e.
        put_calendar has never once reached its actual entry decision. Not a
        strategy or threshold problem -- a one-field data-extraction gap.
        See LESSONS.md LESSON-F13.
        """'''

chain_old = '''                chain.append({
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
                })'''

chain_new = '''                chain.append({
                    "strike":   getattr(row, "strike_price", 0.0) or 0.0,
                    "ce_ltp":   getattr(ce_market, "ltp", 0.0) or 0.0,
                    "ce_delta": getattr(ce_greeks, "delta", 0.0) or 0.0,
                    "ce_iv":    getattr(ce_greeks, "iv", 0.0) or 0.0,
                    "ce_oi":    getattr(ce_market, "oi", 0.0) or 0.0,
                    "ce_bid":   getattr(ce_market, "bid_price", 0.0) or 0.0,
                    "ce_ask":   getattr(ce_market, "ask_price", 0.0) or 0.0,
                    "pe_ltp":   getattr(pe_market, "ltp", 0.0) or 0.0,
                    "pe_delta": getattr(pe_greeks, "delta", 0.0) or 0.0,
                    "pe_iv":    getattr(pe_greeks, "iv", 0.0) or 0.0,
                    "pe_oi":    getattr(pe_market, "oi", 0.0) or 0.0,
                    "pe_bid":   getattr(pe_market, "bid_price", 0.0) or 0.0,
                    "pe_ask":   getattr(pe_market, "ask_price", 0.0) or 0.0,
                })'''

print("Patching brokers/upstox.py...")
patch_file('brokers/upstox.py', docstring_old, docstring_new, 'update get_option_chain docstring')
patch_file('brokers/upstox.py', chain_old, chain_new, 'add ce_iv/pe_iv extraction')

print()
print("=" * 70)
print("Done. Now run:")
print("  python3 -m py_compile brokers/upstox.py && echo SYNTAX OK")
print("=" * 70)
