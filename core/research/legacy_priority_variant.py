# core/research/legacy_priority_variant.py
#
# BACKTEST-ONLY. Never imported by main.py or anything that touches live
# trading -- only run_survivor_backtest.py, opted into via an explicit
# --pe-ce-priority independent flag (default stays "elif", i.e. current
# production behaviour, unchanged).
#
# WHAT THIS DOES: provides an alternative implementation of
# SurvivorAlgo._evaluate_pe_ce_entries (see strategy/survivor.py, extracted
# 2026-08-29 specifically to make this possible) that checks PE and CE
# independently every tick -- both can act on the same tick if both
# conditions are met -- instead of the current production elif chain where
# PE, if its condition is met, prevents CE from being evaluated at all that
# tick. This matches original_strategies/survivor_original.py's design
# (self._handle_pe_trade(...); self._handle_ce_trade(...) as two
# unconditional, independent calls every tick -- see lessons.md LESSON-001
# for the full history of how this was found).
#
# HOW IT'S WIRED IN: run_survivor_backtest.py monkeypatches this function
# onto the SurvivorAlgo instance (types.MethodType) when --pe-ce-priority
# independent is passed. This is a backtest-only instance patch -- it does
# NOT alter strategy/survivor.py, and has zero effect on the live/paper
# bot process, which never imports this file.
#
# The function body below is otherwise IDENTICAL to
# SurvivorAlgo._evaluate_pe_ce_entries -- every condition, every helper
# call, every side-effect line is the same. The ONLY change is `elif` ->
# a second independent `if`, so this is a clean, single-variable test.

import logging

logger = logging.getLogger(__name__)


async def independent_pe_ce_entries(
    self, nifty_price: float, current_pe_gap: float, current_ce_gap: float,
    pe_symbol_gap: float, ce_symbol_gap: float,
) -> None:
    """Drop-in replacement for SurvivorAlgo._evaluate_pe_ce_entries.
    `self` is a SurvivorAlgo instance -- this function is monkeypatched
    onto it via types.MethodType, so `self.` attribute/method access below
    resolves exactly as it would inside the real class."""
    from strategy.survivor import Direction  # local import, avoids any
    from core.risk_manager import risk_manager  # import-order coupling
    from core.regime_engine import regime_engine  # with the live module

    _open_ce = sum(1 for t in self._open_trades_data if t["direction"] == "CE")
    _open_pe = sum(1 for t in self._open_trades_data if t["direction"] == "PE")

    # PE SELL — Nifty moved up enough from last PE anchor
    if self.cfg.pe_enabled and nifty_price - self._pe_last_value >= current_pe_gap and not self._pe_sold_flag and _open_pe == 0 \
            and regime_engine.get_regime_stability() >= self.cfg.min_regime_stability:
        _pe_diff = round(nifty_price - self._pe_last_value, 0)
        _pe_raw_mult = int(_pe_diff / current_pe_gap) if current_pe_gap else 1
        _pe_mult = max(1, min(_pe_raw_mult, self.cfg.sell_multiplier_threshold))
        if _pe_raw_mult > self.cfg.sell_multiplier_threshold:
            logger.warning(f"[survivor:independent] PE overshoot multiplier capped: raw={_pe_raw_mult} -> {_pe_mult}")
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
        self._time_based_pe_fired = True
        self._update_position(Direction.SHORT)

    # CE SELL — Nifty moved down enough from last CE anchor
    # ONLY CHANGE from production: independent `if`, not `elif`. Both
    # sides can now act on the same tick if both conditions are met.
    if self.cfg.ce_enabled and self._ce_last_value - nifty_price >= current_ce_gap and not self._ce_sold_flag and _open_ce == 0 \
            and regime_engine.get_regime_stability() >= self.cfg.min_regime_stability:
        _ce_diff = round(self._ce_last_value - nifty_price, 0)
        _ce_raw_mult = int(_ce_diff / current_ce_gap) if current_ce_gap else 1
        _ce_mult = max(1, min(_ce_raw_mult, self.cfg.sell_multiplier_threshold))
        if _ce_raw_mult > self.cfg.sell_multiplier_threshold:
            logger.warning(f"[survivor:independent] CE overshoot multiplier capped: raw={_ce_raw_mult} -> {_ce_mult}")
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
        self._time_based_ce_fired = True
        self._update_position(Direction.SHORT)