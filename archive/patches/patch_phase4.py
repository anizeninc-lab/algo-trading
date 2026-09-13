import sys

def patch_file(path, old, new, label, crlf=False):
    kwargs = {'encoding': 'utf-8'}
    if crlf:
        kwargs['newline'] = ''
    with open(path, 'r', **kwargs) as f:
        content = f.read()
    count = content.count(old)
    if count == 0:
        print(f'  [SKIP/FAIL] {label}: old string not found in {path}. '
              f'Already patched, or file differs -- check manually.')
        return False
    if count > 1:
        print(f'  [WARN] {label}: found {count} times in {path}, expected 1.')
    content = content.replace(old, new)
    with open(path, 'w', **kwargs) as f:
        f.write(content)
    print(f'  [OK] {label}: applied to {path}')
    return True


alerting_old = '''def alert_ws_instrument_cap(existing: int, requested: int, cap: int) -> None:
    send_telegram(
        f"*WS INSTRUMENT CAP HIT*\\n"
        f"Existing: `{existing}` | New requested: `{requested}` | Cap: `{cap}`\\n"
        f"\U0001f6a8 New symbols REFUSED \u2014 some strikes will NOT receive live ticks. "
        f"SL/trailing logic for those symbols will not fire until unsubscribed "
        f"capacity frees up.",
        LEVEL_CRITICAL
    )
'''

alerting_new = '''_ws_cap_alert_last_sent = 0.0

def alert_ws_instrument_cap(existing: int, requested: int, cap: int) -> None:
    # Rate-limited to once per 5 minutes (2026-09 fix, same day this was
    # first deployed): this fires on EVERY refused subscribe_ticks() call,
    # and if the cap is being hit repeatedly (e.g. stale subscriptions
    # accumulated over a session, or a strategy retrying subscribes), that
    # means unbounded Telegram spam with zero limit -- unlike every other
    # alert in this file, which either fires once per real event or has an
    # explicit cooldown (see alert_tick_stale).
    global _ws_cap_alert_last_sent
    import time as _time
    now = _time.time()
    if now - _ws_cap_alert_last_sent < 300:
        return
    _ws_cap_alert_last_sent = now
    send_telegram(
        f"*WS INSTRUMENT CAP HIT*\\n"
        f"Existing: `{existing}` | New requested: `{requested}` | Cap: `{cap}`\\n"
        f"\U0001f6a8 New symbols REFUSED \u2014 some strikes will NOT receive live ticks. "
        f"SL/trailing logic for those symbols will not fire until unsubscribed "
        f"capacity frees up. (This alert is rate-limited to once per 5 min \u2014 "
        f"if you're seeing this repeatedly, the cap is being hit continuously, "
        f"which likely means stale subscriptions from closed positions are "
        f"never being unsubscribed. Check brokers/upstox.py's _tick_callbacks "
        f"size and consider a bot restart to clear it.)",
        LEVEL_CRITICAL
    )

def alert_tick_stale(symbol: str, seconds_stale: float) -> None:
    send_telegram(
        f"*TICK FEED STALE*\\n"
        f"Symbol: `{symbol}` | No tick for `{seconds_stale:.0f}s`\\n"
        f"\u26a0\ufe0f This is per-instrument staleness (distinct from a full WS "
        f"disconnect) \u2014 SL/trailing logic for this symbol may not be "
        f"reacting to current price. Check the strike's liquidity and "
        f"the WS subscription for it.",
        LEVEL_WARNING
    )
'''

txcost_old = '''    return round(brokerage + exchange_txn + sebi + stt + stamp_duty + gst, 2)'''

txcost_new = '''    return round(brokerage + exchange_txn + sebi + stt + stamp_duty + gst, 2)


def estimate_slippage_cost(bid: float, ask: float, quantity: int) -> float:
    """
    Estimated one-sided market-impact cost from crossing the bid-ask spread,
    added 2026-09 (Phase 4 audit fix). Previously this cost model covered
    only regulatory/broker fees -- brokerage, STT, exchange charges, GST,
    stamp duty -- none of which account for the fact that a marketable
    order on an OTM weekly option typically fills a few paise to a few
    rupees through the quoted mid, especially on wide-spread strikes or in
    the first/last 15 minutes of the session.

    Uses half the quoted spread as a simple, conservative estimate of one
    fill's slippage (the other half is the market maker's take, not a cost
    you pay directly, but crossing the full spread on both entry and exit
    is a reasonable worst-case if you call this once per leg per side).

    Returns 0.0 if bid/ask aren't available or look invalid (e.g. ask < bid,
    which happens on stale/crossed quotes) -- callers should treat that as
    "no spread data available" and fall back to fee-only costing, not as
    "confirmed zero slippage."
    """
    if bid <= 0 or ask <= 0 or ask < bid:
        return 0.0
    spread = ask - bid
    return round((spread / 2.0) * quantity, 2)
'''

bs_init_old = '''        self._stop_flag = False
        self._session_id: str = ""
        state_store.register_strategy(name=self.name, broker=type(broker).__name__)'''

bs_init_new = '''        self._stop_flag = False
        self._session_id: str = ""
        self._last_tick_time: dict = {}       # symbol -> unix timestamp of last received tick
        self._staleness_alerted: dict = {}    # symbol -> unix timestamp last staleness alert fired
        state_store.register_strategy(name=self.name, broker=type(broker).__name__)'''

bs_append_old = '''                await on_failure()
            except Exception as e:
                logger.error(f"[{self.name}] on_failure callback (post-GTT-fail) raised: {e}")

        return False'''

bs_append_new = '''                await on_failure()
            except Exception as e:
                logger.error(f"[{self.name}] on_failure callback (post-GTT-fail) raised: {e}")

        return False

    def _record_tick(self, symbol: str) -> None:
        """
        Call from _on_tick_sync whenever a tick for `symbol` arrives. Powers
        _check_tick_staleness() below (Phase 4 audit fix, 2026-09).

        Deliberately independent of whatever per-strategy price cache each
        strategy already maintains (_ltp_cache, _current_price, etc.) --
        this only tracks *when* a tick last arrived, not the price itself,
        so it doesn't need to understand or touch each strategy's existing
        (differently-shaped) price-caching logic.
        """
        import time as _time
        self._last_tick_time[symbol] = _time.time()

    def _check_tick_staleness(self, symbol: str, threshold_sec: float = 45.0) -> None:
        """
        Call periodically (alongside SL/trailing-profit checks) for any
        symbol with an open position. Alerts -- does NOT auto-close -- if no
        tick has been received for `symbol` in over threshold_sec, during
        market hours (Phase 4 audit fix, 2026-09).

        This is a DIFFERENT signal from the existing WS-level heartbeat
        monitor in brokers/upstox.py (which tracks the whole connection via
        the INDEX tick and can look perfectly healthy while a single
        illiquid STRIKE goes quiet) -- this one is per-instrument, which
        matters because SL/trailing-profit evaluation is entirely tick-driven.

        Policy is alert-only, deliberately -- not auto-close. Staleness
        detection can have false positives (e.g. genuinely zero trading
        activity on a deep OTM strike for a stretch), and auto-closing a
        position on an unreliable signal is its own risk. Revisit auto-close
        only after this has been observed alert-only for a while and proven
        not to be noisy.
        """
        import time as _time
        last = self._last_tick_time.get(symbol)
        if last is None:
            return  # no tick recorded yet for this symbol -- nothing to compare against

        staleness = _time.time() - last
        if staleness <= threshold_sec:
            return

        try:
            import pytz
            from datetime import datetime as _dt, time as _dtime
            now = _dt.now(pytz.timezone("Asia/Kolkata"))
            market_open = _dtime(9, 15) <= now.time() <= _dtime(15, 30) and now.weekday() < 5
            if not market_open:
                return
        except Exception:
            pass  # if market-hours check itself fails, fail open and still alert

        last_alerted = self._staleness_alerted.get(symbol, 0)
        if _time.time() - last_alerted < 60:
            return  # rate-limit: at most one alert per symbol per 60s
        self._staleness_alerted[symbol] = _time.time()

        logger.warning(
            f"[{self.name}] Tick staleness: no tick for {symbol} in "
            f"{staleness:.0f}s (threshold {threshold_sec:.0f}s)"
        )
        try:
            from core.alerting import alert_tick_stale
            alert_tick_stale(symbol, staleness)
        except Exception as e:
            logger.error(f"[{self.name}] alert_tick_stale itself failed: {e}")'''

we_tick_old = '''            self._current_price = tick.mid_price
            self._record_price_sample(tick.mid_price)

            if "INDEX" in tick.symbol:'''

we_tick_new = '''            self._current_price = tick.mid_price
            self._record_price_sample(tick.mid_price)
            self._record_tick(tick.symbol)

            if "INDEX" in tick.symbol:'''

we_check_old = '''            entry = trade["entry_price"]
            otype = trade["order_type"]
            qty   = trade["quantity"]
            price = self._current_price

            risk_manager.record_mfe_mae('''

we_check_new = '''            entry = trade["entry_price"]
            otype = trade["order_type"]
            qty   = trade["quantity"]
            price = self._current_price

            # Per-instrument tick-staleness check (Phase 4 audit fix, 2026-09).
            self._check_tick_staleness(trade.get("symbol", self.cfg.option_symbol))

            risk_manager.record_mfe_mae('''

gex_tick_old = '''            self._current_spot = tick.mid_price

            # Monitor any active trade FIRST, unconditionally -- same rule as
            # wave_extractor: exits must never be skipped by entry-side gating.'''

gex_tick_new = '''            self._current_spot = tick.mid_price

            # Per-instrument tick-staleness check (Phase 4 audit fix, 2026-09).
            # nifty_gex's own SL/target logic is index-spot-driven (see
            # _monitor_active_trade), not option-premium-driven like other
            # strategies -- but still track the traded option's own tick
            # feed for consistency and to catch a broken subscription.
            if self._active_trade and tick.symbol == self._active_trade.get("symbol"):
                self._record_tick(tick.symbol)

            # Monitor any active trade FIRST, unconditionally -- same rule as
            # wave_extractor: exits must never be skipped by entry-side gating.'''

gex_check_old = '''    async def _monitor_active_trade(self) -> None:
        trade = self._active_trade
        plan  = trade.get("plan")
        spot  = self._current_spot

'''

gex_check_new = '''    async def _monitor_active_trade(self) -> None:
        trade = self._active_trade
        plan  = trade.get("plan")
        spot  = self._current_spot

        # Per-instrument tick-staleness check (Phase 4 audit fix, 2026-09).
        if trade.get("symbol"):
            self._check_tick_staleness(trade["symbol"])

'''

pc_comment_old = '''#   - ASSUMPTION FLAGGED: the spec says "exit Friday/Monday before expiry",
#     which described the OLD Thursday-expiry Nifty weekly cycle. This repo's
#     weekly expiry is now Tuesday (see core/auto_config.py), so "Friday/
#     Monday before Tuesday" doesn't map cleanly. Implemented instead as a
#     configurable N calendar days before front expiry (default 1 = exits
#     Monday for a Tuesday expiry, which is the closest honest equivalent).
#     Revisit forced_exit_days_before_expiry if that's not what you meant.'''

pc_comment_new = '''#   - CONFIRMED WITH USER 2026-09 (Phase 4 audit fixes): the original spec
#     said "exit Friday/Monday before expiry", written for the OLD
#     Thursday-expiry Nifty weekly cycle. This repo's weekly expiry is now
#     Tuesday (see core/auto_config.py), so "Friday/Monday before Tuesday"
#     didn't map cleanly -- user confirmed Monday (1 day before front
#     expiry) is correct. forced_exit_days_before_expiry=1 (below) is
#     confirmed, not a guess.
#   - Manual override: the scheduled exit above is not the only way to
#     close this position -- POST /api/put_calendar/close-now (dashboard
#     button "Close Put Calendar", or Telegram /close_put_calendar) closes
#     both legs on demand, on top of the scheduled exit conditions, not
#     instead of them. Added 2026-09 per explicit user request.'''

pc_tick_old = '''            if tick.symbol == self.cfg.nifty_instrument_key or "Nifty" in tick.symbol:
                self._current_spot = tick.mid_price
            self._ltp_cache[tick.symbol] = tick.last_price'''

pc_tick_new = '''            if tick.symbol == self.cfg.nifty_instrument_key or "Nifty" in tick.symbol:
                self._current_spot = tick.mid_price
            self._ltp_cache[tick.symbol] = tick.last_price
            self._record_tick(tick.symbol)'''

pc_premium_old = '''        front_premium = front_leg.get("pe_ltp", 0.0)
        back_premium  = back_leg.get("pe_ltp", 0.0)'''

pc_premium_new = '''        front_premium = front_leg.get("pe_ltp", 0.0)
        back_premium  = back_leg.get("pe_ltp", 0.0)
        front_bid     = front_leg.get("pe_bid", 0.0) or 0.0
        front_ask     = front_leg.get("pe_ask", 0.0) or 0.0'''

pc_trade_dict_old = '''            "short_strike": strike,
            "net_debit": net_debit,
            "capital_employed": capital_employed,
            "front_expiry": front_expiry.isoformat(),
            "back_expiry": back_expiry.isoformat(),
        }'''

pc_trade_dict_new = '''            "short_strike": strike,
            "net_debit": net_debit,
            "capital_employed": capital_employed,
            "front_expiry": front_expiry.isoformat(),
            "back_expiry": back_expiry.isoformat(),
            "front_bid": front_bid,
            "front_ask": front_ask,
        }'''

pc_monitor_old = '''    async def _monitor_active_trade(self) -> None:
        t = self._active_trade
        if not t:
            return

        front_ltp = self._current_leg_ltp(t["front_symbol"]) or await self.broker.get_ltp(t["front_symbol"])'''

pc_monitor_new = '''    async def _monitor_active_trade(self) -> None:
        t = self._active_trade
        if not t:
            return

        # Per-instrument tick-staleness check (Phase 4 audit fix, 2026-09).
        # Both legs matter here -- the SL below is evaluated on the COMBINED
        # spread value, so a stale tick on either leg (front or back) can
        # distort that combined P&L calculation even if the other leg is fine.
        self._check_tick_staleness(t["front_symbol"])
        self._check_tick_staleness(t["back_symbol"])

        front_ltp = self._current_leg_ltp(t["front_symbol"]) or await self.broker.get_ltp(t["front_symbol"])'''

sv_docstring_old = '''        Scale position size based on current VIX regime.
        VERY_LOW (<12)  : 2 lots (130) — low vol, more aggressive
        NORMAL   (12-16): 1 lot  (65)  — standard'''

sv_docstring_new = '''        Scale position size based on current VIX regime.
        VERY_LOW (<12)  : 1 lot  (65)  — capped at 1 lot deliberately (was 2 lots/130 qty
                                          previously — trading >1 lot per order distorts
                                          per-trade cost economics, and MAX_QTY_PER_ORDER
                                          in brokers/upstox.py hardcaps at 65 anyway, so the
                                          old 2-lot value here was silently unreachable in
                                          live trading -- only ever fired in paper mode,
                                          which skips place_order() entirely)
        NORMAL   (12-16): 1 lot  (65)  — standard'''

sv_tick_old = '''            self._ltp_cache[tick.symbol] = tick.mid_price'''

sv_tick_new = '''            self._ltp_cache[tick.symbol] = tick.mid_price
            self._record_tick(tick.symbol)'''

sv_select_docstring_old = '''        Fetch option chain and return (strike, ikey, premium) for the strike
        whose delta is closest to cfg.target_delta (within cfg.delta_tolerance).'''

sv_select_docstring_new = '''        Fetch option chain and return (strike, ikey, premium, bid, ask) for the
        strike whose delta is closest to cfg.target_delta (within
        cfg.delta_tolerance). bid/ask default to 0.0 if the chain row didn't
        have them (Phase 4 audit fix, 2026-09 -- used by callers to feed
        check_trailing_profit's optional spread-cost estimate).'''

sv_bidask_capture_old = '''            delta_strike = best_row["strike"]
            delta_value  = best_row.get("ce_delta") if direction == "CE" else best_row.get("pe_delta")
            delta_ltp    = best_row.get("ce_ltp")   if direction == "CE" else best_row.get("pe_ltp")'''

sv_bidask_capture_new = '''            delta_strike = best_row["strike"]
            delta_value  = best_row.get("ce_delta") if direction == "CE" else best_row.get("pe_delta")
            delta_ltp    = best_row.get("ce_ltp")   if direction == "CE" else best_row.get("pe_ltp")
            delta_bid    = best_row.get("ce_bid")   if direction == "CE" else best_row.get("pe_bid")
            delta_ask    = best_row.get("ce_ask")   if direction == "CE" else best_row.get("pe_ask")'''

sv_return_old = '''            return (delta_strike, ikey, premium)'''

sv_return_new = '''            return (delta_strike, ikey, premium, delta_bid or 0.0, delta_ask or 0.0)'''

sv_locals_old = '''        premium      = 0.0
        _strike_method = "premium"  # for logging'''

sv_locals_new = '''        premium      = 0.0
        entry_bid    = 0.0
        entry_ask    = 0.0
        _strike_method = "premium"  # for logging'''

sv_unpack_old = '''                final_strike, symbol, premium = delta_result'''

sv_unpack_new = '''                final_strike, symbol, premium, entry_bid, entry_ask = delta_result'''

sv_tradedata_old = '''                "direction":  direction,
                "entry_cost": calculate_order_cost(entry_price, quantity, "SELL"),
            }'''

sv_tradedata_new = '''                "direction":  direction,
                "entry_cost": calculate_order_cost(entry_price, quantity, "SELL"),
                "entry_bid":  entry_bid,
                "entry_ask":  entry_ask,
            }'''

sv_staleness_old = '''                if curr_price == 0:
                    continue
'''

sv_staleness_new = '''                if curr_price == 0:
                    continue

                # Per-instrument tick-staleness check (Phase 4 audit fix, 2026-09).
                # Alert-only -- see _check_tick_staleness docstring in base_strategy.py.
                self._check_tick_staleness(trade["symbol"])
'''

sv_trailing_call_old = '''                    hedge_entry_cost=trade.get("hedge_entry_cost", 0.0),
                ):'''

sv_trailing_call_new = '''                    hedge_entry_cost=trade.get("hedge_entry_cost", 0.0),
                    entry_bid=trade.get("entry_bid", 0.0),
                    entry_ask=trade.get("entry_ask", 0.0),
                ):'''

rm_import_old = '''from core.transaction_costs import calculate_order_cost'''

rm_import_new = '''from core.transaction_costs import calculate_order_cost, estimate_slippage_cost'''

rm_init_old = '''        self._system_halted:  bool             = False
        self._halt_reason:    str              = ""
        self._last_reset_day: str              = "1970-01-01"'''

rm_init_new = '''        self._system_halted:  bool             = False
        self._halt_reason:    str              = ""
        self._last_reset_day: str              = "1970-01-01"
        # Manual pause (Phase 4 audit fix, 2026-09) -- distinct from
        # _system_halted: this is a deliberate user action via /pause
        # (Telegram), stops NEW entries across all strategies (including
        # put_calendar) but does NOT close existing open positions -- their
        # own SL/exit logic keeps running normally. Deliberately NOT
        # persisted across restarts -- a fresh RiskManager instance each
        # process start naturally resets this to False, so a pause never
        # silently carries over to a new session.
        self._manually_paused: bool             = False'''

rm_blocked_old = '''    def is_trading_blocked(self, strategy_name: str = "") -> tuple[bool, str]:
        """Single pre-trade gate. Returns (blocked: bool, reason: str).
        Checks in priority order: system halted → auto-stop time → VIX halt.
        Strategies call this once at the top of on_tick and bail early if blocked.

        put_calendar is fully exempt (own capital pool, own weekly cycle,
        own SL/exit rules) -- including circuit breaker and VIX halt, per
        explicit decision to run it fully independently of shared risk gates.
        """
        if strategy_name == "put_calendar":
            return False, ""
        if self._system_halted:'''

rm_blocked_new = '''    def is_trading_blocked(self, strategy_name: str = "") -> tuple[bool, str]:
        """Single pre-trade gate. Returns (blocked: bool, reason: str).
        Checks in priority order: system halted → auto-stop time → VIX halt.
        Strategies call this once at the top of on_tick and bail early if blocked.

        put_calendar exemption narrowed (Phase 4 audit fix, 2026-09): it keeps
        its exemption from the shared daily-loss halt (_system_halted), weekly
        drawdown, and auto-stop-time checks -- those are genuinely tied to its
        own separate capital pool (PUT_CALENDAR_CAPITAL) and its own weekly
        cycle, and that part of the original design is sound. It NO LONGER
        skips the API circuit breaker or the VIX-extreme halt, both of which
        are session-health signals rather than strategy-specific risk budget:
        - API circuit breaker firing means the broker connection itself is
          unreliable (repeated place_order/get_ltp failures) -- put_calendar
          has no way to know that on its own and would otherwise keep trying
          to trade through a broken connection.
        - VIX-extreme is exactly the regime a calendar spread (long vega on
          the back month, short vega on the front) is most exposed to --
          exempting it from the one halt built for that exact condition was
          the sharper gap, not a deliberate risk choice.

        Manual pause (Phase 4 audit fix, 2026-09) is checked FIRST, before
        even the put_calendar branch -- unlike VIX/circuit-breaker/daily-loss,
        which are risk-budget or session-health signals with legitimate
        per-strategy nuance, a manual /pause is a deliberate blanket "stop
        taking new trades" user action that should apply uniformly to every
        strategy with no exceptions.
        """
        if self._manually_paused:
            return True, "Manually paused via /pause"
        if strategy_name == "put_calendar":
            _cb_tripped, _cb_reason = self.check_api_circuit_breaker()
            if _cb_tripped:
                return True, _cb_reason
            from core.vix_manager import vix_manager as _vm_pc
            if _vm_pc.get_params().get("halt_trading", False):
                return True, "VIX EXTREME — trading halted by vix_manager"
            return False, ""
        if self._system_halted:'''

rm_sig_old = '''        hedge_entry_cost:      float = 0.0,
    ) -> bool:
        """
        Two-stage exit logic, cost-aware and hedge-aware:'''

rm_sig_new = '''        hedge_entry_cost:      float = 0.0,
        entry_bid:             float = 0.0,
        entry_ask:             float = 0.0,
    ) -> bool:
        """
        Two-stage exit logic, cost-aware and hedge-aware:'''

rm_docstring_old = '''        also includes the hedge leg: hedge_entry_cost is reused from what
        was already computed once at trade open (avoids recomputing against
        a possibly-stale current hedge premium), plus an estimated hedge
        exit cost (hedge is always BUY-to-open, SELL-to-close).
        """'''

rm_docstring_new = '''        also includes the hedge leg: hedge_entry_cost is reused from what
        was already computed once at trade open (avoids recomputing against
        a possibly-stale current hedge premium), plus an estimated hedge
        exit cost (hedge is always BUY-to-open, SELL-to-close).

        Spread-aware (Phase 4 audit fix, 2026-09): entry_bid/entry_ask are
        OPTIONAL and default to 0.0, which keeps every existing call site
        working unchanged -- callers that don't have chain data (no
        get_option_chain call at entry) simply don't pass these and get the
        exact same fee-only costing as before. When provided, an estimated
        one-sided slippage cost (half the quoted spread, see
        estimate_slippage_cost) is folded into cost_of_trade, making the
        40%-of-premium target and trailing floor slightly more conservative
        on wide-spread strikes -- which is the point: a target computed
        against fee-only costs can look reachable on paper while actually
        requiring a fill better than the market currently offers.
        """'''

rm_cost_old = '''        if hedge_quantity > 0:
            hedge_exit_cost = calculate_order_cost(hedge_current_price, hedge_quantity, "SELL")
            cost_of_trade += hedge_entry_cost + hedge_exit_cost

        premium_collected = entry_price * quantity
        if fixed_target > 0:'''

rm_cost_new = '''        if hedge_quantity > 0:
            hedge_exit_cost = calculate_order_cost(hedge_current_price, hedge_quantity, "SELL")
            cost_of_trade += hedge_entry_cost + hedge_exit_cost

        if entry_bid > 0.0 and entry_ask > 0.0:
            cost_of_trade += estimate_slippage_cost(entry_bid, entry_ask, quantity)

        premium_collected = entry_price * quantity
        if fixed_target > 0:'''

api_endpoints_old = '''        risk_manager._save_state()
        send_telegram("⚠️ Kill switch RESET — trading re-enabled", LEVEL_WARNING)
        return {"status": "ok", "halted": False}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/strategy/{name}/stop")'''

api_endpoints_new = '''        risk_manager._save_state()
        send_telegram("⚠️ Kill switch RESET — trading re-enabled", LEVEL_WARNING)
        return {"status": "ok", "halted": False}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/put_calendar/close-now")
async def put_calendar_close_now():
    """
    Manual override -- immediately closes the current put_calendar position
    (both legs) regardless of P&L or the strategy's own exit conditions
    (stop-loss / pre-expiry forced exit). Added per explicit user request
    (Phase 4 audit fixes, 2026-09): scheduled forced exit stays at Monday
    before front-week expiry, but the user wants the ability to close the
    spread manually at any time on top of that, not instead of it.

    Does NOT touch risk_manager._system_halted or any other strategy --
    this is scoped to put_calendar's own open position only, unlike the
    global kill switch above.
    """
    try:
        if combo_ref is None or getattr(combo_ref, "put_calendar", None) is None:
            return {"status": "error", "error": "put_calendar strategy is not running or not enabled"}
        if not combo_ref.put_calendar._active_trade:
            return {"status": "error", "error": "No open put_calendar position to close"}
        await combo_ref.put_calendar._close_active_trade("MANUAL")
        logger.info("[dashboard] put_calendar position closed via manual close-now endpoint")
        return {"status": "ok", "message": "put_calendar position closed"}
    except Exception as e:
        logger.exception(f"[dashboard] Manual put_calendar close failed: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/api/pause")
async def pause_trading():
    """
    Soft pause (Phase 4 audit fix, 2026-09) -- stops ALL strategies from
    taking new entries (via risk_manager._manually_paused, checked first in
    is_trading_blocked() before any strategy-specific logic), but does NOT
    close existing open positions -- their own SL/exit logic keeps running
    normally. Deliberately softer than /api/killswitch, which halts AND
    closes everything immediately. Distinct trigger for a distinct need:
    this is for "I want to stop new risk but let what's open play out,"
    not "get me out of everything right now."
    """
    try:
        from core.risk_manager import risk_manager
        risk_manager._manually_paused = True
        logger.info("[dashboard] Trading paused via /api/pause")
        return {"status": "ok", "paused": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/pause/clear")
async def unpause_trading():
    """Clears a manual pause set via /api/pause. Does not affect
    risk_manager._system_halted or any other halt mechanism -- if the bot
    is also halted for an unrelated reason (daily loss, VIX, etc.), this
    alone will not resume trading; use /api/killswitch/reset for that."""
    try:
        from core.risk_manager import risk_manager
        risk_manager._manually_paused = False
        logger.info("[dashboard] Trading un-paused via /api/pause/clear")
        return {"status": "ok", "paused": False}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/strategy/{name}/stop")'''

api_modvar_old = '''# Global broker reference — set by main.py on startup
broker_ref = None
combo_ref  = None   # reference to SaviourCombo instance for kill switch
'''

api_modvar_new = '''# Global broker reference — set by main.py on startup
broker_ref = None
combo_ref  = None   # reference to SaviourCombo instance for kill switch
startup_block_reason = ""  # set by main.py while blocked on login() at startup
                            # (Phase 4 audit fix, 2026-09) -- empty string means
                            # not blocked; non-empty means strategies have not
                            # been armed yet and won't be until login() succeeds.
'''

api_status_old = '''    # Trading status label
    if risk_manager.is_halted():
        status = "HALTED"
        status_col = "red"
    elif vix_halted:'''

api_status_new = '''    # Trading status label
    if risk_manager.is_halted():
        status = "HALTED"
        status_col = "red"
    elif getattr(risk_manager, "_manually_paused", False):
        status = "PAUSED"
        status_col = "orange"
    elif vix_halted:'''

api_return_old = '''    return {
        "trading_status":   status,
        "status_colour":    status_col,
        "is_halted":        risk_manager.is_halted(),
        "halt_reason":      risk_manager._halt_reason if hasattr(risk_manager, "_halt_reason") else "",
        "block_reason":     block_reason,'''

api_return_new = '''    return {
        "trading_status":   status,
        "status_colour":    status_col,
        "is_halted":        risk_manager.is_halted(),
        "is_paused":        getattr(risk_manager, "_manually_paused", False),
        "startup_blocked":  bool(startup_block_reason),
        "startup_block_reason": startup_block_reason,
        "halt_reason":      risk_manager._halt_reason if hasattr(risk_manager, "_halt_reason") else "",
        "block_reason":     block_reason,'''

tg_docstring_old = '''#   /token <code> — exchange Upstox auth code (used by auto_token.py flow)

import os'''

tg_docstring_new = '''#   /token <code> — exchange Upstox auth code (used by auto_token.py flow)
#   /addcapital <amount> — top up capital pool
#   /close_put_calendar — manually close the put_calendar spread on demand
#     (separate from the scheduled Monday-before-expiry forced exit; this is
#     purely a manual override, added 2026-09 per explicit request)
#   /pause — stop all strategies from taking new entries; existing open
#     positions keep running their own SL/exit logic normally. Softer than
#     /kill, which closes everything immediately. Added 2026-09.
#   /unpause — clear a /pause (does not affect /kill's halt -- use /resume
#     for that)

import os'''

tg_handlers_old = '''    except Exception as e:
        return f"❌ Token exchange error: {e}"


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():'''

tg_handlers_new = '''    except Exception as e:
        return f"❌ Token exchange error: {e}"


def handle_close_put_calendar() -> str:
    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/put_calendar/close-now", timeout=15)
        data = resp.json()
        if data.get("status") == "ok":
            return "✅ <b>put_calendar position closed</b> — both legs unwound."
        return f"❌ Close failed: {data.get('error', 'unknown error')}"
    except Exception as e:
        return f"❌ Close failed: {e}"


def handle_pause() -> str:
    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/pause", timeout=15)
        data = resp.json()
        if data.get("status") == "ok":
            return "⏸ <b>Trading paused</b> — no new entries across any strategy. Existing open positions keep running their own SL/exit logic normally. Reply /unpause to resume."
        return f"❌ Pause failed: {data.get('error', 'unknown error')}"
    except Exception as e:
        return f"❌ Pause failed: {e}"


def handle_unpause() -> str:
    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/pause/clear", timeout=15)
        data = resp.json()
        if data.get("status") == "ok":
            return "▶️ <b>Trading resumed</b> — new entries allowed again."
        return f"❌ Unpause failed: {data.get('error', 'unknown error')}"
    except Exception as e:
        return f"❌ Unpause failed: {e}"


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():'''

tg_banner_old = '''    tg_send("🤖 <b>Telegram Commander online</b>\\nCommands: /kill /status /resume /token &lt;code&gt; /addcapital &lt;amount&gt;")'''

tg_banner_new = '''    tg_send("🤖 <b>Telegram Commander online</b>\\nCommands: /kill /status /resume /token &lt;code&gt; /addcapital &lt;amount&gt; /close_put_calendar /pause /unpause")'''

tg_dispatch_old = '''            elif text.startswith("/addcapital "):
                amount_str = text.split("/addcapital ", 1)[1].strip()
                tg_send("⏳ Adding capital...")
                tg_send(handle_add_capital(amount_str))
            elif text.startswith("/"):
                tg_send(
                    "❓ Unknown command. Available:\\n"'''

tg_dispatch_new = '''            elif text.startswith("/addcapital "):
                amount_str = text.split("/addcapital ", 1)[1].strip()
                tg_send("⏳ Adding capital...")
                tg_send(handle_add_capital(amount_str))
            elif text == "/close_put_calendar":
                tg_send("⏳ Closing put_calendar position...")
                tg_send(handle_close_put_calendar())
            elif text == "/pause":
                tg_send(handle_pause())
            elif text == "/unpause":
                tg_send(handle_unpause())
            elif text.startswith("/"):
                tg_send(
                    "❓ Unknown command. Available:\\n"'''

tg_help_old = '''                    "/addcapital &lt;amount&gt; — top up capital pool"
                )'''

tg_help_new = '''                    "/addcapital &lt;amount&gt; — top up capital pool\\n"
                    "/close_put_calendar — manually close the put_calendar spread now\\n"
                    "/pause — stop new entries, keep existing positions running\\n"
                    "/unpause — resume new entries"
                )'''

main_gate_old = '''    broker = get_broker()
    # Share broker with dashboard API for funds endpoint
    import dashboard.api as dashboard_api
    dashboard_api.broker_ref = broker
    # Wire market_context to the broker directly here rather than relying on'''

main_gate_new = '''    broker = get_broker()
    # Share broker with dashboard API for funds endpoint
    import dashboard.api as dashboard_api
    dashboard_api.broker_ref = broker

    # ── Startup token-validation gate (Phase 4 audit fix, 2026-09) ──
    # Previously broker.login() was never called anywhere in this file --
    # the bot would proceed straight to rollover/strategies/WS subscription
    # using whatever token happened to be in .env, discovering a stale or
    # invalid token only when individual API calls started failing
    # mid-session, with no single clear signal at startup. This matters
    # most right after the 8:45 AM daily token-refresh window: if that
    # didn't complete (auto_token.py failed, or the operator hasn't replied
    # to /token yet), the bot should NOT start trading blind on yesterday's
    # dead token.
    #
    # Now: validate the token via a real API call (login() -> get_profile())
    # BEFORE anything else runs. If it fails, alert once and enter a safe
    # idle loop -- retries every 2 min, never arms strategies, places
    # orders, or subscribes to ticks while blocked. Deliberately does NOT
    # just exit and let pm2 restart-loop this process: that would burn
    # through ecosystem.config.js's max_restarts within minutes and leave
    # the bot fully STOPPED with no clear signal why. Staying alive in an
    # idle loop keeps pm2 seeing a healthy `online` process throughout.
    #
    # In practice, the existing /token flow (tg_commander.py -> auto_token.py)
    # already does a full `pm2 restart trading-bot --update-env` once a new
    # token is written, so the realistic resolution path is a fresh process
    # starting clean with a valid token -- this loop's own retries are a
    # secondary safety net for the less common case where .env changes
    # without a full restart, not the primary recovery path.
    from core.alerting import send_telegram, LEVEL_CRITICAL, LEVEL_INFO
    login_ok = await broker.login()
    if not login_ok:
        dashboard_api.startup_block_reason = "Upstox login failed — waiting for valid token"
        send_telegram(
            "🔴 <b>BOT NOT TRADING</b>\\n"
            "Upstox login failed — token invalid or expired.\\n"
            "Reply /token &lt;code&gt; to fix. Retrying login every 2 min until it succeeds.",
            LEVEL_CRITICAL,
        )
        logger.critical("[main] Startup blocked: broker.login() failed. Entering idle retry loop.")
        while not login_ok:
            await asyncio.sleep(120)
            login_ok = await broker.login()
        dashboard_api.startup_block_reason = ""
        logger.info("[main] Startup unblocked: broker.login() succeeded.")
        send_telegram("✅ Upstox login succeeded — proceeding with normal startup.", LEVEL_INFO)

    # Wire market_context to the broker directly here rather than relying on'''


print("Patching core/alerting.py...")
patch_file('core/alerting.py', alerting_old, alerting_new, 'WS cap cooldown + alert_tick_stale')

print("Patching core/transaction_costs.py...")
patch_file('core/transaction_costs.py', txcost_old, txcost_new, 'estimate_slippage_cost')

print("Patching strategy/base_strategy.py...")
patch_file('strategy/base_strategy.py', bs_init_old, bs_init_new, 'init dicts')
patch_file('strategy/base_strategy.py', bs_append_old, bs_append_new, 'staleness methods')

print("Patching strategy/wave_extractor.py...")
patch_file('strategy/wave_extractor.py', we_tick_old, we_tick_new, 'record_tick')
patch_file('strategy/wave_extractor.py', we_check_old, we_check_new, 'staleness check')

print("Patching strategy/nifty_gex.py...")
patch_file('strategy/nifty_gex.py', gex_tick_old, gex_tick_new, 'record_tick')
patch_file('strategy/nifty_gex.py', gex_check_old, gex_check_new, 'staleness check')

print("Patching strategy/put_calendar.py...")
patch_file('strategy/put_calendar.py', pc_comment_old, pc_comment_new, 'comment update')
patch_file('strategy/put_calendar.py', pc_tick_old, pc_tick_new, 'record_tick')
patch_file('strategy/put_calendar.py', pc_premium_old, pc_premium_new, 'bid/ask capture')
patch_file('strategy/put_calendar.py', pc_trade_dict_old, pc_trade_dict_new, 'trade dict fields')
patch_file('strategy/put_calendar.py', pc_monitor_old, pc_monitor_new, 'staleness checks')

print("Patching strategy/survivor.py...")
patch_file('strategy/survivor.py', sv_docstring_old, sv_docstring_new, 'docstring VERY_LOW fix')
patch_file('strategy/survivor.py', sv_tick_old, sv_tick_new, 'record_tick')
patch_file('strategy/survivor.py', sv_select_docstring_old, sv_select_docstring_new, 'select_strike docstring')
patch_file('strategy/survivor.py', sv_bidask_capture_old, sv_bidask_capture_new, 'bid/ask capture')
patch_file('strategy/survivor.py', sv_return_old, sv_return_new, 'return tuple')
patch_file('strategy/survivor.py', sv_locals_old, sv_locals_new, 'local var init')
patch_file('strategy/survivor.py', sv_unpack_old, sv_unpack_new, 'unpack 5-tuple')
patch_file('strategy/survivor.py', sv_tradedata_old, sv_tradedata_new, 'trade_data fields')
patch_file('strategy/survivor.py', sv_staleness_old, sv_staleness_new, 'staleness check')
patch_file('strategy/survivor.py', sv_trailing_call_old, sv_trailing_call_new, 'trailing profit call')

print("Patching core/risk_manager.py...")
patch_file('core/risk_manager.py', rm_import_old, rm_import_new, 'import estimate_slippage_cost')
patch_file('core/risk_manager.py', rm_init_old, rm_init_new, 'add _manually_paused')
patch_file('core/risk_manager.py', rm_blocked_old, rm_blocked_new, 'is_trading_blocked rewrite')
patch_file('core/risk_manager.py', rm_sig_old, rm_sig_new, 'check_trailing_profit signature')
patch_file('core/risk_manager.py', rm_docstring_old, rm_docstring_new, 'check_trailing_profit docstring')
patch_file('core/risk_manager.py', rm_cost_old, rm_cost_new, 'cost_of_trade slippage')

print("Patching dashboard/api.py...")
patch_file('dashboard/api.py', api_endpoints_old, api_endpoints_new, 'new endpoints (close-now, pause, unpause)')
patch_file('dashboard/api.py', api_modvar_old, api_modvar_new, 'startup_block_reason var')
patch_file('dashboard/api.py', api_status_old, api_status_new, 'PAUSED status branch')
patch_file('dashboard/api.py', api_return_old, api_return_new, 'return dict fields')

print("Patching tg_commander.py...")
patch_file('tg_commander.py', tg_docstring_old, tg_docstring_new, 'docstring update')
patch_file('tg_commander.py', tg_handlers_old, tg_handlers_new, 'new handler functions')
patch_file('tg_commander.py', tg_banner_old, tg_banner_new, 'startup banner')
patch_file('tg_commander.py', tg_dispatch_old, tg_dispatch_new, 'command dispatch')
patch_file('tg_commander.py', tg_help_old, tg_help_new, 'help text')

print("Patching main.py...")
patch_file('main.py', main_gate_old, main_gate_new, 'startup token gate')

print()
print("Done. Now run the verification commands.")
