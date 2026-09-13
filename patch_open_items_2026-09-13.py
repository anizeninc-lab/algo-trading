"""
patch_open_items_2026-09-13.py

Applies the fixes for the "OPEN / UNRESOLVED ITEMS" batch from the
2026-09-12/13 sessions:
  1. Creates core/banknifty_regime_feed.py (new file) -- isolated
     BankNifty regime feed, OFF by default (ENABLE_BANKNIFTY_OWN_REGIME
     flag), zero risk to the live NIFTY market_context singleton.
  2. core/regime_engine.py -- adds the regime_engine_banknifty singleton.
  3. strategy/survivor.py -- fixes a stale docstring (PE/CE coupling
     comment contradicted the already-fixed code), adds two helper
     methods, and wires the 4 regime-gate call sites through them.
     Default behavior UNCHANGED unless ENABLE_BANKNIFTY_OWN_REGIME=true.
  4. dashboard/api.py -- hardens /api/toggle-paper (now requires
     ?confirm=LIVE or ?confirm=PAPER) and starts the new BankNifty feed
     from the startup event, but ONLY if both ENABLE_BANKNIFTY and
     ENABLE_BANKNIFTY_OWN_REGIME are true.
  5. .gitignore -- adds backtest state file patterns.

Run this from the repo root (~/trading-algo), same way as
patch_survivor_rootcause_fixes.py earlier:
    python3 patch_open_items_2026-09-13.py
Then verify:
    python3 -m py_compile core/regime_engine.py strategy/survivor.py dashboard/api.py core/banknifty_regime_feed.py && echo SYNTAX OK
"""
import os


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


def create_file(path, content, label):
    if os.path.exists(path):
        print(f'  [SKIP] {label}: {path} already exists -- not overwriting. Delete it first if you want to re-create it.')
        return
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  [OK] {label}: created {path}')


# ── 1. New file: core/banknifty_regime_feed.py ──────────────────────────────
BANKNIFTY_REGIME_FEED_CONTENT = '"""\ncore/banknifty_regime_feed.py\n\nGives bn_survivor a real, independent BankNifty regime reading instead of\nsilently reusing NIFTY\'s (see LESSONS.md LESSON-F7 / HANDOFF 2026-09-12\nfor the full root-cause writeup: core/market_context.py\'s\n_classify_regime() has only ever fetched NIFTY spot and fed it to the one\nshared regime_engine singleton, so bn_survivor\'s min_regime_stability gate\nhas never, in its whole history, evaluated BankNifty\'s own market\nbehavior).\n\nWHY THIS IS A SEPARATE MODULE, NOT A REWRITE OF market_context.py:\ncore/market_context.py is deeply entangled with the live tick-subscription\npipeline, opening-range tracking, real option-chain OI/PCR, previous-day\nlevels, and the dashboard/state-store surface -- all built around one\nNIFTY-only instance. The 2026-09-12 session deliberately deferred\ntouching that file, in these words: "rushing this on a weekend with no\nway to test against live ticks (market closed) was a deliberate decision\nnot to do, not an oversight. This needs its own properly-scoped session,\nideally starting on a day with live market data to actually verify\nagainst." That reasoning still holds -- this module respects it by\nstaying completely isolated: it does not import, subclass, or modify\nMarketContextEngine or the shared `market_context` singleton in any way.\nZero shared state, zero blast radius to the NIFTY path.\n\nWHAT THIS MODULE DOES, DELIBERATELY SIMPLIFIED VS NIFTY\'S VERSION:\n- Polls BankNifty spot via REST (mirrors _fetch_nifty_spot()\'s pattern,\n  same instrument key convention as backfill_banknifty_candles.py:\n  "NSE_INDEX|Nifty Bank"), and self-aggregates 1-minute OHLC candles from\n  those polls -- it does NOT subscribe to live BankNifty ticks. Simpler,\n  safer to reason about, and avoids touching the live tick pipeline at\n  all, at the cost of slightly coarser candles than a true tick feed.\n- OI/PCR-derived regime signals (pcr, ce_oi_delta, pe_oi_delta, pcr_spike,\n  the pe/ce migration flags) are passed as neutral defaults -- there is\n  no BankNifty option-chain OI pipeline wired up yet. This is the same\n  category of approximation already accepted and documented in\n  run_survivor_backtest.py\'s IndexReplay for backtesting; it is NOT a\n  hidden simplification, it is a known, flagged gap. Building a real\n  BankNifty OI/PCR feed is future work, not done here.\n- Opening range (or_high/or_low) is tracked as a simple running\n  high/low over the polls collected between market open and OR_END,\n  not NIFTY\'s more elaborate opening_range object.\n\nSTATUS AS OF 2026-09-13: written and unit-testable (classify() itself is\npure, given candles), but NOT YET VERIFIED against a live BankNifty feed\nor a live Upstox token -- written without one, same caveat as\nbackfill_banknifty_candles.py. Per the explicit rule in LESSONS.md /\nHANDOFF: do NOT wire this into bn_survivor\'s live min_regime_stability\ngate, and do NOT trust any BankNifty-specific threshold derived from it,\nuntil it has been (a) run live for real against actual BankNifty data\nand sanity-checked, and (b) ideally replayed against\nbackfill_banknifty_candles.py\'s archived history through the (already\nregime-aware, as of 2026-09-12) backtest harness for an honest gate test.\nWiring it into survivor.py\'s gate checks (see accompanying patch) is\ngated behind an ENABLE_BANKNIFTY_OWN_REGIME feature flag, default OFF,\nfor exactly this reason -- so this can be committed and reviewed without\nsilently changing bn_survivor\'s live behavior the moment it\'s deployed.\n"""\nimport os\nimport threading\nimport time\nfrom collections import deque\nfrom datetime import datetime, date\nfrom typing import Optional\n\nimport requests\nimport pytz\n\nfrom core.regime_engine import regime_engine_banknifty, Candle\n\nIST = pytz.timezone("Asia/Kolkata")\nBANKNIFTY_INSTRUMENT_KEY = "NSE_INDEX|Nifty Bank"\nPOLL_INTERVAL_SEC = 15          # how often we hit the REST quote endpoint\nCANDLE_BUCKET_SECONDS = 60      # 1-minute candles, aggregated from polls\nMAX_SESSION_CANDLES = 500       # plenty for a full trading day at 1min\nOR_END_MINUTE = (9, 30)         # same convention as market_context\'s OR window\n\n\nclass BankNiftyRegimeFeed:\n    """\n    Minimal, independent regime feed for BankNifty. Mirrors just enough of\n    MarketContextEngine\'s public interface (`.regime`, `register_regime_\n    callback`, `.start()`/`.stop()`) that survivor.py\'s bn_survivor path\n    can query it the same way it queries `market_context` today -- without\n    either object knowing about the other.\n    """\n\n    def __init__(self):\n        self._lock = threading.RLock()\n        self._regime: str = "closed"\n        self._regime_change_callbacks = []\n        self._stop_flag = threading.Event()\n        self._thread: Optional[threading.Thread] = None\n\n        self._session_candles: deque = deque(maxlen=MAX_SESSION_CANDLES)\n        self._current_bucket_minute: Optional[int] = None\n        self._current_bucket: dict = {}\n        self._or_high: Optional[float] = None\n        self._or_low: Optional[float] = None\n        self._or_locked = False\n        self._session_date: Optional[date] = None\n\n    # ── Public interface (mirrors market_context\'s shape) ──────────────\n    @property\n    def regime(self) -> str:\n        with self._lock:\n            return self._regime\n\n    def register_regime_callback(self, fn) -> None:\n        self._regime_change_callbacks.append(fn)\n\n    def get_regime_stability(self) -> float:\n        return regime_engine_banknifty.get_regime_stability()\n\n    def start(self) -> None:\n        if self._thread and self._thread.is_alive():\n            return\n        self._stop_flag.clear()\n        self._thread = threading.Thread(target=self._run_loop, daemon=True)\n        self._thread.start()\n\n    def stop(self) -> None:\n        self._stop_flag.set()\n\n    # ── Internal loop ───────────────────────────────────────────────────\n    def _run_loop(self) -> None:\n        while not self._stop_flag.is_set():\n            try:\n                self._reset_session_if_new_day()\n                spot = self._fetch_banknifty_spot()\n                if spot is not None:\n                    self._update_candle_bucket(spot)\n                    self._update_opening_range(spot)\n                    self._classify_regime()\n            except Exception as e:\n                import logging\n                logging.getLogger(__name__).exception(\n                    f"[banknifty_regime_feed] loop error: {e}"\n                )\n            self._stop_flag.wait(timeout=POLL_INTERVAL_SEC)\n\n    def _reset_session_if_new_day(self) -> None:\n        today = datetime.now(IST).date()\n        if self._session_date != today:\n            with self._lock:\n                self._session_date = today\n                self._session_candles.clear()\n                self._current_bucket_minute = None\n                self._current_bucket = {}\n                self._or_high = None\n                self._or_low = None\n                self._or_locked = False\n\n    def _fetch_banknifty_spot(self) -> Optional[float]:\n        """Mirrors market_context._fetch_nifty_spot()\'s pattern exactly,\n        just pointed at BankNifty\'s instrument key instead."""\n        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")\n        if not token:\n            return None\n        try:\n            url = "https://api.upstox.com/v2/market-quote/quotes"\n            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}\n            params = {"instrument_key": BANKNIFTY_INSTRUMENT_KEY}\n            resp = requests.get(url, headers=headers, params=params, timeout=5)\n            resp.raise_for_status()\n            data = resp.json().get("data", {})\n            key = BANKNIFTY_INSTRUMENT_KEY.replace("|", ":")\n            for k, v in data.items():\n                if k.replace("|", ":") == key or BANKNIFTY_INSTRUMENT_KEY in k:\n                    return float(v.get("last_price", 0.0)) or None\n            return None\n        except Exception:\n            return None\n\n    def _update_candle_bucket(self, spot: float) -> None:\n        now = datetime.now(IST)\n        bucket_minute = int(now.timestamp() // CANDLE_BUCKET_SECONDS)\n        with self._lock:\n            if self._current_bucket_minute is None:\n                self._current_bucket_minute = bucket_minute\n                self._current_bucket = {"open": spot, "high": spot, "low": spot, "close": spot}\n                return\n            if bucket_minute != self._current_bucket_minute:\n                b = self._current_bucket\n                self._session_candles.append(Candle(\n                    ts=self._current_bucket_minute, open=b["open"],\n                    high=b["high"], low=b["low"], close=b["close"],\n                ))\n                self._current_bucket_minute = bucket_minute\n                self._current_bucket = {"open": spot, "high": spot, "low": spot, "close": spot}\n            else:\n                b = self._current_bucket\n                b["high"] = max(b["high"], spot)\n                b["low"] = min(b["low"], spot)\n                b["close"] = spot\n\n    def _update_opening_range(self, spot: float) -> None:\n        now_t = datetime.now(IST).time()\n        or_end = now_t.replace(hour=OR_END_MINUTE[0], minute=OR_END_MINUTE[1], second=0, microsecond=0)\n        with self._lock:\n            if now_t < or_end:\n                self._or_high = spot if self._or_high is None else max(self._or_high, spot)\n                self._or_low = spot if self._or_low is None else min(self._or_low, spot)\n            elif not self._or_locked:\n                self._or_locked = True\n                if self._or_high is None:\n                    self._or_high = self._or_low = spot\n\n    def _classify_regime(self) -> None:\n        with self._lock:\n            candles = list(self._session_candles)\n            if self._current_bucket_minute is not None and self._current_bucket:\n                b = self._current_bucket\n                candles = candles + [Candle(\n                    ts=self._current_bucket_minute, open=b["open"],\n                    high=b["high"], low=b["low"], close=b["close"],\n                )]\n            or_high = self._or_high\n            or_low = self._or_low\n\n        if len(candles) < 5:\n            return  # matches regime_engine.classify()\'s own guard\n\n        spot = candles[-1].close\n        new_regime, _signals = regime_engine_banknifty.classify(\n            candles=candles,\n            or_high=or_high if or_high is not None else spot,\n            or_low=or_low if or_low is not None else spot,\n            spot=spot,\n            # Neutral defaults -- no BankNifty OI/PCR pipeline yet, see\n            # module docstring. Not a hidden shortcut, a flagged gap.\n            pcr=1.0,\n            ce_oi_delta=0.0,\n            pe_oi_delta=0.0,\n            pcr_spike=False,\n        )\n        with self._lock:\n            if new_regime != self._regime:\n                old_regime = self._regime\n                self._regime = new_regime\n                for cb in list(self._regime_change_callbacks):\n                    try:\n                        cb(old_regime, new_regime)\n                    except Exception:\n                        pass\n            else:\n                self._regime = new_regime\n\n\n# Module-level singleton, mirroring core.market_context\'s `market_context`.\n# Only ever started from main.py, and only when bn_survivor is actually\n# enabled (see main.py wiring) -- no cost/risk when bn_survivor is off.\nbanknifty_context = BankNiftyRegimeFeed()\n'

print("Creating core/banknifty_regime_feed.py...")
create_file('core/banknifty_regime_feed.py', BANKNIFTY_REGIME_FEED_CONTENT, 'new BankNifty regime feed module')

# ── 2. core/regime_engine.py: add the BankNifty singleton ──────────────────
re_old = """# Singleton
regime_engine = RegimeEngine()"""

re_new = """# Singleton
regime_engine = RegimeEngine()

# Second, independent singleton for BankNifty (added 2026-09-13, paired
# with core/banknifty_regime_feed.py). Fully separate state file
# (configs/regime_state_banknifty.json, via _state_file_path() above),
# confirmed NOT the same object as `regime_engine`. Zero effect on the
# NIFTY singleton above -- this is purely additive.
regime_engine_banknifty = RegimeEngine(symbol="BANKNIFTY")"""

print("\nPatching core/regime_engine.py...")
patch_file('core/regime_engine.py', re_old, re_new, 'add regime_engine_banknifty singleton')


# ── 3. strategy/survivor.py: docstring fix + helper methods + 4 call sites ──
sv_docstring_old = '''        Extracted 2026-08-29 from the body of _on_tick_sync, verbatim --
        pure extraction, no logic change. See the call site's comment.
        PRODUCTION BEHAVIOUR: PE is checked first; if PE's full condition
        is met, CE is NOT evaluated this tick (elif). This is the exact
        coupling documented in lessons.md LESSON-001 -- left unchanged
        here on purpose, pending the deferred design decision. A backtest-
        only subclass overrides this method to test the alternative
        (independent, non-exclusive) variant without touching this file.
        """'''

sv_docstring_new = '''        Extracted 2026-08-29 from the body of _on_tick_sync, verbatim --
        pure extraction, no logic change at the time. See the call site's
        comment.

        STALE NOTE FIXED 2026-09-13: this docstring previously said PE/CE
        were still coupled via `elif` "left unchanged on purpose." That
        was true when this docstring was written, but is no longer
        accurate -- the coupling was fixed on 2026-09-08 (see the inline
        comment directly above the CE block below, "Bugfix (Phase 4 audit
        fix, 2026-09)"). PE and CE are now independent `if` blocks,
        evaluated every tick regardless of each other's outcome. Leaving
        a stale docstring contradicting the actual code is exactly the
        kind of thing that causes a future reader to trust the comment
        over the code -- fixing it here so this doesn't happen again.
        """'''

sv_helpers_old = "    async def _evaluate_pe_ce_entries("

sv_helpers_new = '''    def _current_regime_stability(self) -> float:
        """
        Regime-stability score to gate entries against (added 2026-09-13,
        see LESSONS.md LESSON-F7 for the full architecture-gap writeup).

        Default behaviour is UNCHANGED from today: always reads the
        shared NIFTY `regime_engine` singleton, exactly as before, for
        every instrument. bn_survivor only gets its own independent
        BankNifty regime reading (core/banknifty_regime_feed.py) if
        ENABLE_BANKNIFTY_OWN_REGIME=true is explicitly set -- default
        off, because that feed has not yet been verified against a live
        BankNifty feed. Merging this code causes NO live behavior change
        until that flag is deliberately turned on and the feed has been
        validated. Do not flip it on without re-reading that module's
        docstring and LESSONS.md's rule on this first.
        """
        is_banknifty = "BANKNIFTY" in self.cfg.instrument_name.upper()
        if is_banknifty and os.getenv("ENABLE_BANKNIFTY_OWN_REGIME", "false").lower() == "true":
            from core.banknifty_regime_feed import banknifty_context
            return banknifty_context.get_regime_stability()
        return regime_engine.get_regime_stability()

    def _current_regime(self) -> str:
        """Same fallback/flag logic as _current_regime_stability(), for
        the regime-category gate (market_context.regime in ("range",
        "reversal_watch")). See that method's docstring."""
        is_banknifty = "BANKNIFTY" in self.cfg.instrument_name.upper()
        if is_banknifty and os.getenv("ENABLE_BANKNIFTY_OWN_REGIME", "false").lower() == "true":
            from core.banknifty_regime_feed import banknifty_context
            return banknifty_context.regime
        from core.market_context import market_context
        return market_context.regime

    async def _evaluate_pe_ce_entries('''

print("\nPatching strategy/survivor.py...")
patch_file('strategy/survivor.py', sv_docstring_old, sv_docstring_new, 'fix stale PE/CE docstring')
patch_file('strategy/survivor.py', sv_helpers_old, sv_helpers_new, 'add regime helper methods')

# 4 call-site replacements (2 identical PE/CE gate checks + 2 in the time-based trigger)
sv_gate_old = "and regime_engine.get_regime_stability() >= self.cfg.min_regime_stability:"
sv_gate_new = "and self._current_regime_stability() >= self.cfg.min_regime_stability:"
print("  Replacing PE/CE gap-trigger regime checks (expect 2 occurrences)...")
with open('strategy/survivor.py', 'r', encoding='utf-8') as f:
    content = f.read()
n = content.count(sv_gate_old)
content = content.replace(sv_gate_old, sv_gate_new)
with open('strategy/survivor.py', 'w', encoding='utf-8') as f:
    f.write(content)
print(f'  [OK] replaced {n} occurrence(s) (expected 2)')

sv_cat_old = 'if market_context.regime not in ("range", "reversal_watch"):'
sv_cat_new = 'if self._current_regime() not in ("range", "reversal_watch"):'
patch_file('strategy/survivor.py', sv_cat_old, sv_cat_new, 'time-trigger regime-category check')

sv_stab_old = "if regime_engine.get_regime_stability() < self.cfg.min_regime_stability:"
sv_stab_new = "if self._current_regime_stability() < self.cfg.min_regime_stability:"
patch_file('strategy/survivor.py', sv_stab_old, sv_stab_new, 'time-trigger stability check')

# ── 4. dashboard/api.py: harden /api/toggle-paper + start BankNifty feed ────
api_toggle_old = '''@app.post("/api/toggle-paper")
async def toggle_paper_mode():
    try:
        env_path = Path(".env")
        if not env_path.exists():
            return {"error": ".env file not found"}
        env_text = env_path.read_text()
        current = os.getenv("PAPER_TRADE", "false").lower() == "true"
        new_val = "false" if current else "true"
        if "PAPER_TRADE=" in env_text:
            env_text = re.sub(r"PAPER_TRADE=.*", f"PAPER_TRADE={new_val}", env_text)
        else:
            env_text += f"\\nPAPER_TRADE={new_val}\\n"
        env_path.write_text(env_text)
        os.environ["PAPER_TRADE"] = new_val
        mode = "PAPER" if new_val == "true" else "LIVE"
        logger.info(f"Trading mode switched to: {mode}")
        os.system("pm2 restart all")
        return {"success": True, "paper_trade": new_val == "true", "mode": mode}
    except Exception as e:
        logger.error(f"toggle_paper_mode error: {e}")
        return {"error": str(e)}'''

api_toggle_new = '''@app.post("/api/toggle-paper")
async def toggle_paper_mode(confirm: str = ""):
    """
    Flips PAPER_TRADE and restarts the whole bot -- this is the single
    switch between simulated and real-money order placement, so it must
    never fire on a stray click or an automated retry.

    SAFETY (added 2026-09, was previously a bare one-click no-confirm
    endpoint -- see LESSONS.md, this was a standing risk item):
    caller must pass ?confirm=<TARGET_MODE>, where TARGET_MODE is the
    mode being switched TO (e.g. confirm=LIVE to go paper->live,
    confirm=PAPER to go live->paper). Omitting it, or getting it wrong
    for the current state, rejects the request with no state change.
    This also means the same accidental double-click can't silently
    flip it back a second time, since the required token changes with
    the state.
    """
    try:
        current = os.getenv("PAPER_TRADE", "false").lower() == "true"
        target_mode = "PAPER" if not current else "LIVE"  # what this call would switch TO
        if confirm.strip().upper() != target_mode:
            return {
                "error": (
                    f"Confirmation required. Current mode is "
                    f"{'PAPER' if current else 'LIVE'}; to switch to "
                    f"{target_mode}, resend with ?confirm={target_mode}."
                ),
                "current_mode": "PAPER" if current else "LIVE",
                "required_confirm": target_mode,
            }

        env_path = Path(".env")
        if not env_path.exists():
            return {"error": ".env file not found"}
        env_text = env_path.read_text()
        new_val = "false" if current else "true"
        if "PAPER_TRADE=" in env_text:
            env_text = re.sub(r"PAPER_TRADE=.*", f"PAPER_TRADE={new_val}", env_text)
        else:
            env_text += f"\\nPAPER_TRADE={new_val}\\n"
        env_path.write_text(env_text)
        os.environ["PAPER_TRADE"] = new_val
        mode = "PAPER" if new_val == "true" else "LIVE"
        logger.info(f"Trading mode switched to: {mode} (confirmed via ?confirm={confirm})")
        try:
            from core.alerting import send_telegram, LEVEL_CRITICAL
            send_telegram(
                f"\\u26a0\\ufe0f TRADING MODE SWITCHED: now {mode}\\n"
                f"Triggered via dashboard /api/toggle-paper, confirmed. Bot restarting.",
                LEVEL_CRITICAL,
            )
        except Exception as alert_e:
            logger.warning(f"toggle_paper_mode: alert failed (mode switch still applied): {alert_e}")
        os.system("pm2 restart all")
        return {"success": True, "paper_trade": new_val == "true", "mode": mode}
    except Exception as e:
        logger.error(f"toggle_paper_mode error: {e}")
        return {"error": str(e)}'''

print("\nPatching dashboard/api.py...")
patch_file('dashboard/api.py', api_toggle_old, api_toggle_new, 'harden /api/toggle-paper')

api_startup_old = '''    # Start market context engine if available
    if _MARKET_CONTEXT_AVAILABLE:
        try:
            market_context.start()
            logger.info("MarketContextEngine started from dashboard startup")
        except Exception as e:
            logger.warning(f"MarketContextEngine start failed: {e}")'''

api_startup_new = '''    # Start market context engine if available
    if _MARKET_CONTEXT_AVAILABLE:
        try:
            market_context.start()
            logger.info("MarketContextEngine started from dashboard startup")
        except Exception as e:
            logger.warning(f"MarketContextEngine start failed: {e}")

    # Independent BankNifty regime feed (added 2026-09-13, see LESSONS.md
    # LESSON-F7 and core/banknifty_regime_feed.py's docstring). Only ever
    # starts when bn_survivor itself is enabled AND the feature flag is
    # explicitly on -- default off, not yet live-verified. Fully isolated
    # from market_context above; failure here can't affect the NIFTY path.
    if (os.getenv("ENABLE_BANKNIFTY", "false").lower() == "true"
            and os.getenv("ENABLE_BANKNIFTY_OWN_REGIME", "false").lower() == "true"):
        try:
            from core.banknifty_regime_feed import banknifty_context
            banknifty_context.start()
            logger.info("BankNiftyRegimeFeed started from dashboard startup "
                        "(ENABLE_BANKNIFTY_OWN_REGIME=true)")
        except Exception as e:
            logger.warning(f"BankNiftyRegimeFeed start failed (bn_survivor "
                            f"will fall back to NIFTY regime, same as before "
                            f"this feature existed): {e}")'''

patch_file('dashboard/api.py', api_startup_old, api_startup_new, 'start BankNifty feed from dashboard startup')


# ── 5. .gitignore: add backtest state file patterns ─────────────────────────
gi_old = """configs/backtest_risk_state.json
backtest_trade_log.db"""

gi_new = """configs/backtest_risk_state.json
configs/backtest_survivor_risk_state.json
configs/backtest_*_risk_state.json
backtest_trade_log.db
backtest_survivor_trade_log.db"""

print("\nPatching .gitignore...")
patch_file('.gitignore', gi_old, gi_new, 'add backtest state file patterns')

print()
print("=" * 70)
print("Done. Now run:")
print("  python3 -m py_compile core/regime_engine.py strategy/survivor.py dashboard/api.py core/banknifty_regime_feed.py && echo SYNTAX OK")
print("=" * 70)
