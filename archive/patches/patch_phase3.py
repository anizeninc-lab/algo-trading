import sys

def patch_file(path, old, new, label, crlf=False):
    kwargs = {'encoding': 'utf-8'}
    if crlf:
        kwargs['newline'] = ''  # preserve original line endings exactly, no normalization
    with open(path, 'r', **kwargs) as f:
        content = f.read()
    count = content.count(old)
    if count == 0:
        print(f'  [SKIP/FAIL] {label}: old string not found in {path}. '
              f'Already patched, or file differs -- check manually.')
        return
    if count > 1:
        print(f'  [WARN] {label}: found {count} times in {path}, expected 1.')
    content = content.replace(old, new)
    with open(path, 'w', **kwargs) as f:
        f.write(content)
    print(f'  [OK] {label}: applied to {path}')


# ---------------- brokers/upstox.py ----------------

ws_cap_old = '''# ─── HARDCAP — NEVER change this without careful testing ─────────────────────
MAX_QTY_PER_ORDER = 65  # 1 lot of Nifty options = 65 qty. Bot should NEVER place more.
# ─────────────────────────────────────────────────────────────────────────────'''

ws_cap_new = '''# ─── HARDCAP — NEVER change this without careful testing ─────────────────────
MAX_QTY_PER_ORDER = 65  # 1 lot of Nifty options = 65 qty. Bot should NEVER place more.
# ─────────────────────────────────────────────────────────────────────────────

# ─── WS instrument cap ─────────────────────────────────────────────────────
# Upstox's v3 market-data feed documents a ~100-instrument ceiling per WS
# connection. Set with headroom below that, not right at it. Enforced in
# subscribe_ticks() below (Phase 3 audit fix, 2026-09) -- previously
# unenforced, so five concurrent strategies (survivor, wave_extractor,
# put_calendar, nifty_gex, saviour_combo) subscribing independently could
# silently exceed the cap on days with wide strike dispersion, leaving some
# symbols with no live ticks and no indication anything was wrong -- a real
# risk given SL/trailing-profit logic is entirely tick-driven.
MAX_INSTRUMENTS_PER_WS = 95
# ─────────────────────────────────────────────────────────────────────────────'''

option_chain_throttle_old = '''    async def get_option_chain(self, instrument_key: str, expiry: str) -> list:
        """
        Fetch the live option chain (with Greeks) for instrument_key/expiry.
        Returns one dict per strike: strike, ce_ltp, ce_delta, ce_oi, ce_bid, ce_ask,
        pe_ltp, pe_delta, pe_oi, pe_bid, pe_ask. Returns [] on any failure --
        callers must handle gracefully and fall back to non-delta logic.
        """
        try:
            import upstox_client as _uc'''

option_chain_throttle_new = '''    async def get_option_chain(self, instrument_key: str, expiry: str) -> list:
        """
        Fetch the live option chain (with Greeks) for instrument_key/expiry.
        Returns one dict per strike: strike, ce_ltp, ce_delta, ce_oi, ce_bid, ce_ask,
        pe_ltp, pe_delta, pe_oi, pe_bid, pe_ask. Returns [] on any failure --
        callers must handle gracefully and fall back to non-delta logic.
        """
        try:
            await self._throttle()
            import upstox_client as _uc'''

margin_throttle_old = '''    async def get_margin(self) -> MarginData:
        try:
            user_api = upstox_client.UserApi(
                upstox_client.ApiClient(self._configuration)
            )'''

margin_throttle_new = '''    async def get_margin(self) -> MarginData:
        try:
            await self._throttle()
            user_api = upstox_client.UserApi(
                upstox_client.ApiClient(self._configuration)
            )'''

subscribe_cap_old = '''    def subscribe_ticks(self, symbols: list, callback) -> None:
        new_syms = []'''

subscribe_cap_new = '''    def subscribe_ticks(self, symbols: list, callback) -> None:
        # WS instrument cap enforcement (Phase 3 audit fix, 2026-09). Compute
        # how many genuinely NEW symbols this call would add, and refuse the
        # whole call if that would push total subscribed instruments over the
        # cap -- refusing loudly (with an alert) beats silently going over
        # Upstox's ~100-instrument ceiling, which would otherwise just mean
        # some strikes stop receiving ticks with no visible symptom until an
        # SL fails to fire.
        new_syms_check = [s for s in symbols if s not in self._tick_callbacks]
        total_after = len(self._tick_callbacks) + len(new_syms_check)
        if total_after > MAX_INSTRUMENTS_PER_WS:
            logger.critical(
                f"[upstox] WS instrument cap would be exceeded: "
                f"{len(self._tick_callbacks)} existing + {len(new_syms_check)} new "
                f"= {total_after} > {MAX_INSTRUMENTS_PER_WS}. Refusing this subscribe "
                f"call entirely -- none of {symbols} will receive live ticks until "
                f"capacity is freed (unsubscribe stale symbols) or a second WS "
                f"connection is added."
            )
            try:
                from core.alerting import alert_ws_instrument_cap
                alert_ws_instrument_cap(
                    existing=len(self._tick_callbacks),
                    requested=len(new_syms_check),
                    cap=MAX_INSTRUMENTS_PER_WS,
                )
            except Exception as e:
                logger.error(f"[upstox] alert_ws_instrument_cap itself failed: {e}")
            return

        new_syms = []'''

tls_old = '''            async def _stream():
                ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE'''

tls_new = '''            async def _stream():
                # TLS verification fix (Phase 3 audit fix, 2026-09). Previously
                # check_hostname=False + verify_mode=CERT_NONE disabled certificate
                # validation entirely on the connection carrying live price data
                # that SL/trailing-profit logic acts on -- open to MITM tick
                # injection on any untrusted network path. Restored to proper
                # verification using the system's default CA trust store.
                #
                # NOT independently tested against a live Upstox connection --
                # this environment has no network path to api.upstox.com to
                # verify the handshake actually succeeds with strict verification.
                # If this was originally set to CERT_NONE to work around a real
                # cert-chain problem (e.g. a stale CA bundle on this VPS), that
                # will resurface here as repeated connection failures. The
                # existing reconnect loop below will retry with backoff and
                # alert_websocket_down() will fire after ~90s of market-hours
                # downtime either way, so a regression here is visible rather
                # than silent -- but test this specific change in a low-stakes
                # window (pre-market, or watched closely) rather than trusting
                # it blind. If it does fail: check `openssl s_client -connect
                # api.upstox.com:443` on this server first, and consider
                # `sudo apt install --reinstall ca-certificates` before
                # reverting to CERT_NONE.
                ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = True
                ssl_ctx.verify_mode = _ssl.CERT_REQUIRED
                ssl_ctx.load_default_certs()'''

# ---------------- brokers/__init__.py (CRLF file) ----------------

broker_init_old = 'def get_broker() -> AbstractBrokerGateway:\r\n    name = os.getenv("BROKER_NAME", "").lower().strip()'

broker_init_new = '''def get_broker() -> AbstractBrokerGateway:\r
    """\r
    Constructs a new adapter instance on every call -- NOT a singleton/cached\r
    factory. This is safe today because get_broker() is called exactly once,\r
    at startup in main.py (`broker = get_broker()`), and that single instance\r
    is explicitly threaded through to every strategy, the dashboard API\r
    (dashboard_api.broker_ref), and market_context -- confirmed 2026-09\r
    (Phase 3 audit check) by grepping every call site.\r
\r
    This matters because UpstoxAdapter's rate limiter (_throttle()) is\r
    per-instance state -- two live UpstoxAdapter objects would each enforce\r
    their own independent request budget, and could collectively exceed\r
    Upstox's actual per-user rate limit even though each individually\r
    "throttles" correctly in isolation. If a second call site to get_broker()\r
    is ever added (e.g. a new entry point, a test harness, a second dashboard\r
    process), route it through the SAME broker instance rather than calling\r
    this factory again -- or turn this into a real singleton/cache if that\r
    becomes hard to guarantee by convention alone.\r
    """\r
    name = os.getenv("BROKER_NAME", "").lower().strip()'''

# ---------------- core/alerting.py ----------------

alert_old = '''def alert_websocket_down(error: str) -> None:
    send_telegram(
        f"*WEBSOCKET DISCONNECTED*\\n"
        f"Error: `{error}`\\n"
        f"\u26a0\ufe0f LTP feed interrupted \u2014 SL/TP may not fire",
        LEVEL_WARNING
    )
'''

alert_new = '''def alert_websocket_down(error: str) -> None:
    send_telegram(
        f"*WEBSOCKET DISCONNECTED*\\n"
        f"Error: `{error}`\\n"
        f"\u26a0\ufe0f LTP feed interrupted \u2014 SL/TP may not fire",
        LEVEL_WARNING
    )

def alert_ws_instrument_cap(existing: int, requested: int, cap: int) -> None:
    send_telegram(
        f"*WS INSTRUMENT CAP HIT*\\n"
        f"Existing: `{existing}` | New requested: `{requested}` | Cap: `{cap}`\\n"
        f"\U0001f6a8 New symbols REFUSED \u2014 some strikes will NOT receive live ticks. "
        f"SL/trailing logic for those symbols will not fire until unsubscribed "
        f"capacity frees up.",
        LEVEL_CRITICAL
    )
'''

EDITS = [
    ("brokers/upstox.py", ws_cap_old, ws_cap_new, "add MAX_INSTRUMENTS_PER_WS constant", False),
    ("brokers/upstox.py", option_chain_throttle_old, option_chain_throttle_new, "throttle get_option_chain", False),
    ("brokers/upstox.py", margin_throttle_old, margin_throttle_new, "throttle get_margin", False),
    ("brokers/upstox.py", subscribe_cap_old, subscribe_cap_new, "enforce WS instrument cap in subscribe_ticks", False),
    ("brokers/upstox.py", tls_old, tls_new, "fix disabled TLS verification", False),
    ("brokers/__init__.py", broker_init_old, broker_init_new, "document get_broker() singleton assumption (CRLF file)", True),
    ("core/alerting.py", alert_old, alert_new, "add alert_ws_instrument_cap", False),
]

for path, old, new, label, crlf in EDITS:
    print(f"Patching {path} ({label})...")
    patch_file(path, old, new, label, crlf=crlf)

print()
print("Done. Now run the verification commands.")