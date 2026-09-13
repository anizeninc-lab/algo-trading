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

sv2_init_old = '''        self._ce_sold_flag     = False
        self._open_trade_ids   = []'''

sv2_init_new = '''        self._ce_sold_flag     = False
        # Rate-limit tracking for VIX/capital-skip signals (Phase 4 audit fix,
        # 2026-09) -- paired with the fix that stopped advancing the anchor on
        # a skip, since without that fix the same gap condition re-fires every
        # tick and would otherwise spam this signal continuously while VIX
        # stays high or capital stays tight.
        self._last_pe_skip_signal_at = 0.0
        self._last_ce_skip_signal_at = 0.0
        self._open_trade_ids   = []'''

sv2_pece_old = '''            _vix_qty = self._get_vix_adjusted_quantity(self.cfg.pe_quantity)
            _adj_qty = _vix_qty * _pe_mult if _vix_qty > 0 else 0
            if _adj_qty == 0:
                self._signal(f"⚠ VIX HIGH — PE trade skipped (qty=0 risk gate)")
            else:
                _cap_ok, _cap_reason = risk_manager.check_capital_limit("SELL", self.name, multiplier=_pe_mult)
                if not _cap_ok:
                    self._signal(f"⚠ CAPITAL LIMIT — PE overshoot trade skipped | {_cap_reason}")
                else:
                    await self._sell_option(
                        direction="PE",
                        nifty_price=nifty_price,
                        gap=pe_symbol_gap,
                        quantity=_adj_qty,
                        overshoot_multiplier=_pe_mult,
                    )
            self._pe_last_value += current_pe_gap * _pe_mult
            self._pe_sold_flag  = True
            self._time_based_pe_fired = True  # block time trigger same side
            self._update_position(Direction.SHORT)

        # CE SELL — Nifty moved down enough from last CE anchor
        elif self.cfg.ce_enabled and self._ce_last_value - nifty_price >= current_ce_gap and not self._ce_sold_flag and _open_ce == 0 \\
                and regime_engine.get_regime_stability() >= self.cfg.min_regime_stability:
            _ce_diff = round(self._ce_last_value - nifty_price, 0)
            _ce_raw_mult = int(_ce_diff / current_ce_gap) if current_ce_gap else 1
            _ce_mult = max(1, min(_ce_raw_mult, self.cfg.sell_multiplier_threshold))
            if _ce_raw_mult > self.cfg.sell_multiplier_threshold:
                logger.warning(f"[survivor] CE overshoot multiplier capped: raw={_ce_raw_mult} -> {_ce_mult}")
            _vix_qty = self._get_vix_adjusted_quantity(self.cfg.ce_quantity)
            _adj_qty = _vix_qty * _ce_mult if _vix_qty > 0 else 0
            if _adj_qty == 0:
                self._signal(f"⚠ VIX HIGH — CE trade skipped (qty=0 risk gate)")
            else:
                _cap_ok, _cap_reason = risk_manager.check_capital_limit("SELL", self.name, multiplier=_ce_mult)
                if not _cap_ok:
                    self._signal(f"⚠ CAPITAL LIMIT — CE overshoot trade skipped | {_cap_reason}")
                else:
                    await self._sell_option(
                        direction="CE",
                        nifty_price=nifty_price,
                        gap=ce_symbol_gap,
                        quantity=_adj_qty,
                        overshoot_multiplier=_ce_mult,
                    )
            self._ce_last_value -= current_ce_gap * _ce_mult
            self._ce_sold_flag  = True
            self._time_based_ce_fired = True  # block time trigger same side
            self._update_position(Direction.SHORT)'''

sv2_pece_new = '''            _vix_qty = self._get_vix_adjusted_quantity(self.cfg.pe_quantity)
            _adj_qty = _vix_qty * _pe_mult if _vix_qty > 0 else 0
            if _adj_qty == 0:
                # Bugfix (Phase 4 audit fix, 2026-09): previously the anchor
                # advance + _pe_sold_flag=True below ran UNCONDITIONALLY, even
                # on this skip path -- meaning a single VIX spike at any point
                # in the day permanently blocked PE for the rest of the
                # session, even if VIX normalized minutes later. VIX-high is a
                # transient condition, not a one-shot event; skipping the
                # trade should not also silently forfeit the rest of the
                # day's PE opportunity. Now: only log the skip, leave the
                # anchor/flag untouched, so the same (still-valid) gap
                # condition can fire correctly once VIX drops. Rate-limited
                # to avoid repeating this signal every tick while VIX stays
                # high (ticks fire every ~150-300ms).
                if time.time() - self._last_pe_skip_signal_at > 30:
                    self._last_pe_skip_signal_at = time.time()
                    self._signal(f"⚠ VIX HIGH — PE trade skipped (qty=0 risk gate)")
            else:
                _cap_ok, _cap_reason = risk_manager.check_capital_limit("SELL", self.name, multiplier=_pe_mult)
                if not _cap_ok:
                    # Same fix as the VIX-skip case above -- capital limits are
                    # even more transient (can free up the moment another
                    # position closes), so don't burn the day's PE opportunity
                    # on a temporary cap hit.
                    if time.time() - self._last_pe_skip_signal_at > 30:
                        self._last_pe_skip_signal_at = time.time()
                        self._signal(f"⚠ CAPITAL LIMIT — PE overshoot trade skipped | {_cap_reason}")
                else:
                    await self._sell_option(
                        direction="PE",
                        nifty_price=nifty_price,
                        gap=pe_symbol_gap,
                        quantity=_adj_qty,
                        overshoot_multiplier=_pe_mult,
                    )
                    self._pe_last_value += current_pe_gap * _pe_mult
                    self._pe_sold_flag  = True
                    self._time_based_pe_fired = True  # block time trigger same side
                    self._update_position(Direction.SHORT)

        # CE SELL — Nifty moved down enough from last CE anchor
        # Bugfix (Phase 4 audit fix, 2026-09): this was previously `elif`, coupled
        # to the PE block above. That meant on any tick where PE's gap condition
        # was true, CE was NEVER evaluated -- even if the PE trade then got
        # skipped inside the block due to VIX-high or capital-limit rejection
        # (note PE's anchor/_pe_sold_flag still advance unconditionally in that
        # skip case too, per the code above -- so a skipped PE trade silently
        # consumed the tick for both sides). Changed to an independent `if` so
        # PE and CE are evaluated on every tick regardless of each other's
        # outcome, matching the original pre-refactor design intent. Each side
        # already has its own independent risk/capital gating and open-position
        # count, so there's no shared state that breaks if both fire on the
        # same tick.
        if self.cfg.ce_enabled and self._ce_last_value - nifty_price >= current_ce_gap and not self._ce_sold_flag and _open_ce == 0 \\
                and regime_engine.get_regime_stability() >= self.cfg.min_regime_stability:
            _ce_diff = round(self._ce_last_value - nifty_price, 0)
            _ce_raw_mult = int(_ce_diff / current_ce_gap) if current_ce_gap else 1
            _ce_mult = max(1, min(_ce_raw_mult, self.cfg.sell_multiplier_threshold))
            if _ce_raw_mult > self.cfg.sell_multiplier_threshold:
                logger.warning(f"[survivor] CE overshoot multiplier capped: raw={_ce_raw_mult} -> {_ce_mult}")
            _vix_qty = self._get_vix_adjusted_quantity(self.cfg.ce_quantity)
            _adj_qty = _vix_qty * _ce_mult if _vix_qty > 0 else 0
            if _adj_qty == 0:
                # See matching PE-side comment above -- same fix, same reasoning.
                if time.time() - self._last_ce_skip_signal_at > 30:
                    self._last_ce_skip_signal_at = time.time()
                    self._signal(f"⚠ VIX HIGH — CE trade skipped (qty=0 risk gate)")
            else:
                _cap_ok, _cap_reason = risk_manager.check_capital_limit("SELL", self.name, multiplier=_ce_mult)
                if not _cap_ok:
                    if time.time() - self._last_ce_skip_signal_at > 30:
                        self._last_ce_skip_signal_at = time.time()
                        self._signal(f"⚠ CAPITAL LIMIT — CE overshoot trade skipped | {_cap_reason}")
                else:
                    await self._sell_option(
                        direction="CE",
                        nifty_price=nifty_price,
                        gap=ce_symbol_gap,
                        quantity=_adj_qty,
                        overshoot_multiplier=_ce_mult,
                    )
                    self._ce_last_value -= current_ce_gap * _ce_mult
                    self._ce_sold_flag  = True
                    self._time_based_ce_fired = True  # block time trigger same side
                    self._update_position(Direction.SHORT)'''

sv2_hedge_old = '''                    if hedge_current_price == 0.0:
                        # Fallback to synthetic/broker pricer -- _ltp_cache is only
                        # ever populated from real option TICKS (on_tick(), price <5000),
                        # which never happens during backtest replay (IndexReplay only
                        # emits index ticks; option prices are synthetic and computed
                        # on-demand via broker.get_ltp()/_price_for()). Without this,
                        # the hedge leg's price silently stays 0.0 for the entire
                        # backtest, corrupting hedge P&L and SL/breakeven checks.
                        hedge_current_price = await self.broker.get_ltp(trade["hedge_symbol"])'''

sv2_hedge_new = '''                    if hedge_current_price == 0.0:
                        if is_paper:
                            # CRITICAL BUGFIX (Phase 4 audit fix, 2026-09): never call
                            # the real broker.get_ltp() here for a paper-mode hedge leg.
                            # Paper-mode hedge legs use a synthetic symbol built by
                            # _build_symbol() (e.g. "NSE_FO|NIFTY01SEP2624250CE") that
                            # is NOT a real Upstox instrument key, and are never
                            # tick-subscribed (_open_hedge_leg only calls
                            # subscribe_ticks() in the live/non-paper branch). Every
                            # call to get_ltp() with this fake symbol was hitting
                            # Upstox's real API, which doesn't recognise it, returning
                            # 0.0 -- every single monitoring check, for the entire life
                            # of every hedge leg, in every paper trade. That drove
                            # _hedge_fail_count to the 3-strike threshold almost
                            # immediately after every hedge leg opened, forcing
                            # STALE_HEDGE_PRICE_AUTOCLOSE on nearly every trade before
                            # it could ever reach its real exit condition -- this was
                            # very likely THE dominant cause of survivor's ~3.6% win
                            # rate over the past month, not a strategy or
                            # market-regime problem. Mirrors the main leg's existing
                            # safe pattern (see is_paper branch above): hold at entry
                            # rather than risk a fake API call or a false zero reading.
                            hedge_current_price = hedge_entry_price
                        else:
                            # Fallback to synthetic/broker pricer -- _ltp_cache is only
                            # ever populated from real option TICKS (on_tick(), price <5000),
                            # which never happens during backtest replay (IndexReplay only
                            # emits index ticks; option prices are synthetic and computed
                            # on-demand via broker.get_ltp()/_price_for()). Without this,
                            # the hedge leg's price silently stays 0.0 for the entire
                            # backtest, corrupting hedge P&L and SL/breakeven checks.
                            hedge_current_price = await self.broker.get_ltp(trade["hedge_symbol"])'''

print("Patching strategy/survivor.py (root-cause fixes: hedge stale-price + PE/CE coupling)...")
patch_file('strategy/survivor.py', sv2_init_old, sv2_init_new, 'add skip-signal rate-limit attrs')
patch_file('strategy/survivor.py', sv2_pece_old, sv2_pece_new, 'PE/CE decoupling + anchor-on-skip fix')
patch_file('strategy/survivor.py', sv2_hedge_old, sv2_hedge_new, 'paper-mode hedge stale-price fix')

print()
print("Done. Now run: python3 -m py_compile strategy/survivor.py && echo SYNTAX OK")
