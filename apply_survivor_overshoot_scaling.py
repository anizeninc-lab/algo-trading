path = "/home/ubuntu/trading-algo/strategy/survivor.py"

with open(path, "r") as f:
    content = f.read()

replacements = []

# ── 1. Add sell_multiplier_threshold config field ───────────────────────
old = '''    expiry_weekday:       int   = 1                      # weekly expiry weekday: Nifty=1 (Tuesday), BankNifty=2 (Wednesday)'''
new = '''    expiry_weekday:       int   = 1                      # weekly expiry weekday: Nifty=1 (Tuesday), BankNifty=2 (Wednesday)
    sell_multiplier_threshold: int = 2                    # caps overshoot scaling (master port) — max lots multiplier per single trigger'''
replacements.append((old, new))

# ── 2. _sell_option signature — add overshoot_multiplier param ─────────
old = '''    async def _sell_option(
        self,
        direction:   str,
        nifty_price: float,
        gap:         float,
        quantity:    int,
    ) -> None:'''
new = '''    async def _sell_option(
        self,
        direction:   str,
        nifty_price: float,
        gap:         float,
        quantity:    int,
        overshoot_multiplier: int = 1,
    ) -> None:'''
replacements.append((old, new))

# ── 3. Quantity sanity check — allow clean multiples up to threshold ───
old = '''        # ── Quantity sanity check — guards against wrong lot size on live trades ──
        expected_qty = self.cfg.lot_size
        if quantity != expected_qty:
            logger.critical(
                f"[survivor] QUANTITY MISMATCH — expected {expected_qty} (cfg.lot_size) "
                f"but got {quantity} for {symbol}. ORDER ABORTED to prevent oversized position."
            )
            self._signal(
                f"🚨 QUANTITY MISMATCH ABORT | {symbol} | "
                f"Expected: {expected_qty} | Got: {quantity} | Order cancelled for safety"
            )
            self._pending_orders.discard(_order_key)
            return'''
new = '''        # ── Quantity sanity check — guards against wrong lot size on live trades ──
        # Allows clean multiples of lot_size (for overshoot scaling), capped at
        # sell_multiplier_threshold lots, to prevent both wrong-lot-size AND runaway sizing.
        expected_qty = self.cfg.lot_size
        _max_qty = expected_qty * self.cfg.sell_multiplier_threshold
        if quantity <= 0 or quantity % expected_qty != 0 or quantity > _max_qty:
            logger.critical(
                f"[survivor] QUANTITY MISMATCH — expected a multiple of {expected_qty} "
                f"(cfg.lot_size, max {self.cfg.sell_multiplier_threshold}x = {_max_qty}) "
                f"but got {quantity} for {symbol}. ORDER ABORTED to prevent oversized position."
            )
            self._signal(
                f"🚨 QUANTITY MISMATCH ABORT | {symbol} | "
                f"Expected multiple of: {expected_qty} (max {_max_qty}) | Got: {quantity} | Order cancelled for safety"
            )
            self._pending_orders.discard(_order_key)
            return'''
replacements.append((old, new))

# ── 4. register_trade call inside _sell_option — pass multiplier ───────
old = '''            risk_manager.register_trade(self.name, "SELL")
            self._signal(
                f"SOLD {direction} {int(final_strike)} @ ₹{entry_price:.2f} | "
                f"Order: {order_id}"
            )'''
new = '''            risk_manager.register_trade(self.name, "SELL", multiplier=overshoot_multiplier)
            self._signal(
                f"SOLD {direction} {int(final_strike)} @ ₹{entry_price:.2f} | "
                f"Order: {order_id}" + (f" | {overshoot_multiplier}x lots" if overshoot_multiplier > 1 else "")
            )'''
replacements.append((old, new))

# ── 5. PE/CE trigger block — overshoot multiplier + capital gate + gradual anchor step ──
old = '''                # ── TRIGGER 1: Movement-based ─────────────────────────────
                # PE SELL — Nifty moved up enough from last PE anchor
                if nifty_price - self._pe_last_value >= current_pe_gap and not self._pe_sold_flag and _open_pe == 0:
                    _adj_qty = self._get_vix_adjusted_quantity(self.cfg.pe_quantity)
                    if _adj_qty == 0:
                        self._signal(f"⚠ VIX HIGH — PE trade skipped (qty=0 risk gate)")
                    else:
                        await self._sell_option(
                            direction="PE",
                            nifty_price=nifty_price,
                            gap=pe_symbol_gap,
                            quantity=_adj_qty,
                        )
                    self._pe_last_value = nifty_price
                    self._pe_sold_flag  = True
                    self._time_based_pe_fired = True  # block time trigger same side
                    self._update_position(Direction.SHORT)

                # CE SELL — Nifty moved down enough from last CE anchor
                elif self._ce_last_value - nifty_price >= current_ce_gap and not self._ce_sold_flag and _open_ce == 0:
                    _adj_qty = self._get_vix_adjusted_quantity(self.cfg.ce_quantity)
                    if _adj_qty == 0:
                        self._signal(f"⚠ VIX HIGH — CE trade skipped (qty=0 risk gate)")
                    else:
                        await self._sell_option(
                            direction="CE",
                            nifty_price=nifty_price,
                            gap=ce_symbol_gap,
                            quantity=_adj_qty,
                        )
                    self._ce_last_value = nifty_price
                    self._ce_sold_flag  = True
                    self._time_based_ce_fired = True  # block time trigger same side
                    self._update_position(Direction.SHORT)'''
new = '''                # ── TRIGGER 1: Movement-based (overshoot-scaled, master port) ──
                # PE SELL — Nifty moved up enough from last PE anchor
                if nifty_price - self._pe_last_value >= current_pe_gap and not self._pe_sold_flag and _open_pe == 0:
                    _pe_diff = round(nifty_price - self._pe_last_value, 0)
                    _pe_raw_mult = int(_pe_diff / current_pe_gap) if current_pe_gap else 1
                    _pe_mult = max(1, min(_pe_raw_mult, self.cfg.sell_multiplier_threshold))
                    if _pe_raw_mult > self.cfg.sell_multiplier_threshold:
                        logger.warning(f"[survivor] PE overshoot multiplier capped: raw={_pe_raw_mult} -> {_pe_mult}")
                    _vix_qty = self._get_vix_adjusted_quantity(self.cfg.pe_quantity)
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
                elif self._ce_last_value - nifty_price >= current_ce_gap and not self._ce_sold_flag and _open_ce == 0:
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
replacements.append((old, new))

# ── 6. release_trade call site #1 (mid-session reconcile watchdog) ─────
old = '''                                risk_manager.release_trade(self.name, trade["order_type"])'''
new = '''                                _rel_mult = max(1, int(trade.get("quantity", self.cfg.lot_size) // self.cfg.lot_size))
                                risk_manager.release_trade(self.name, trade["order_type"], multiplier=_rel_mult)'''
replacements.append((old, new))

# ── 7. release_trade call site #2 (_close_trade) ────────────────────────
old = '''        risk_manager.release_trade(self.name, trade["order_type"])'''
new = '''        _rel_mult = max(1, int(trade.get("quantity", self.cfg.lot_size) // self.cfg.lot_size))
        risk_manager.release_trade(self.name, trade["order_type"], multiplier=_rel_mult)'''
replacements.append((old, new))

for i, (old, new) in enumerate(replacements, 1):
    count = content.count(old)
    assert count == 1, f"Replacement #{i}: match count {count} (expected 1) for block starting: {old[:70]!r}"
    content = content.replace(old, new)

with open(path, "w") as f:
    f.write(content)

print(f"survivor.py patched successfully — {len(replacements)} changes applied.")
