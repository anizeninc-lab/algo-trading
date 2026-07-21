path = "/home/ubuntu/trading-algo/strategy/wave_extractor.py"

with open(path, "r") as f:
    content = f.read()

# ── 1. Insert new methods right before _place_duo_bracket ──────────────────
anchor = "    async def _place_duo_bracket(self) -> None:"

new_methods = '''    def _generate_multiplier_scale(self) -> dict:
        """Build imbalance-level -> [buy_mult, sell_mult] map from cfg.multiplier_scale.
        Positive net_position (long) widens the buy gap (discourage adding longs) and
        keeps sell gap tight (encourage flattening). Negative does the reverse.
        Ported from master WaveStrategy._generate_multiplier_scale, adapted to
        reuse the existing (previously unused) cfg.multiplier_scale list."""
        scale = self.cfg.multiplier_scale
        levels = len(scale)
        m = {"0": [1.0, 1.0]}
        for i in range(1, levels + 1):
            m[str(i)]  = [scale[i - 1], 1.0]   # long imbalance -> widen buy, sell stays tight
            m[str(-i)] = [1.0, scale[i - 1]]   # short imbalance -> widen sell, buy stays tight
        return m

    def _get_scaled_gaps(self, current_diff_scale: int) -> tuple:
        """Scale sell_gap/buy_gap based on current position imbalance.
        Ported from master WaveStrategy._get_scaled_gaps."""
        scale_map = self._generate_multiplier_scale()
        key = str(current_diff_scale)
        if key not in scale_map:
            mult = (
                [self.cfg.multiplier_scale[-1], 1.0] if current_diff_scale > 0
                else [1.0, self.cfg.multiplier_scale[-1]]
            )
        else:
            mult = scale_map[key]
        scaled_buy_gap  = round(self.cfg.buy_gap * mult[0], 1)
        scaled_sell_gap = round(self.cfg.sell_gap * mult[1], 1)
        return scaled_buy_gap, scaled_sell_gap

    async def _place_duo_bracket(self) -> None:'''

assert content.count(anchor) == 1, f"anchor match count: {content.count(anchor)}"
content = content.replace(anchor, new_methods)

# ── 2. Replace static gap usage inside _place_duo_bracket with scaled gaps ──
old_gap_calc = '''        sell_price = round(self._current_price + self.cfg.sell_gap, 2)
        buy_price  = round(self._current_price - self.cfg.buy_gap, 2)'''

new_gap_calc = '''        scaled_buy_gap, scaled_sell_gap = self._get_scaled_gaps(self._net_position)
        sell_price = round(self._current_price + scaled_sell_gap, 2)
        buy_price  = round(self._current_price - scaled_buy_gap, 2)'''

assert content.count(old_gap_calc) == 1, f"gap calc match count: {content.count(old_gap_calc)}"
content = content.replace(old_gap_calc, new_gap_calc)

# ── 3. Update the signal log to show imbalance + scaled gaps ───────────────
old_signal = '''        self._signal(
            f"Bracket placed | spot={self._current_price:.1f} | "
            f"SELL={sell_price} (+{self.cfg.sell_gap}) | BUY={buy_price} (-{self.cfg.buy_gap}) | "
            f"regime={_regime}"
        )'''

new_signal = '''        self._signal(
            f"Bracket placed | spot={self._current_price:.1f} | imbalance={self._net_position} | "
            f"SELL={sell_price} (+{scaled_sell_gap}) | BUY={buy_price} (-{scaled_buy_gap}) | "
            f"regime={_regime}"
        )'''

assert content.count(old_signal) == 1, f"signal match count: {content.count(old_signal)}"
content = content.replace(old_signal, new_signal)

with open(path, "w") as f:
    f.write(content)

print("Wave gap scaling patch applied successfully — 2 new methods added, bracket logic updated.")
