from core.strategy_filter import strategy_filter
# strategy/survivor.py
import asyncio
import logging
import os
import time
import uuid
from datetime import datetime
from dataclasses import dataclass

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.event_bus import EventType
from core.risk_manager import risk_manager
from core.auto_config import fetch_instruments, find_symbol_from_instruments, get_nearest_tuesday, get_nearest_wednesday
from core.state_store import Direction, state_store
from core.trade_log import trade_logger
from core.vix_manager import vix_manager
from core.alerting import (
    alert_trade_opened, alert_trade_closed, alert_order_rejected,
    alert_gtt_failed, alert_daily_loss_hit, alert_reconcile_mismatch,
    alert_system_start, alert_eod_close, alert_breakeven_locked
)
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

STALE_POSITION_MAX_AGE_HOURS = 6  # recovered positions older than this are force-closed, not re-adopted


@dataclass
class SurvivorConfig:
    symbol_initials:      str   = "NIFTY13APR26"
    pe_gap:               float = 15.0
    ce_gap:               float = 15.0
    pe_symbol_gap:        float = 300.0
    ce_symbol_gap:        float = 300.0
    pe_reset_gap:         float = 90.0
    ce_reset_gap:         float = 90.0
    pe_quantity:          int   = 65
    ce_quantity:          int   = 65
    pe_start:             float = 0.0
    ce_start:             float = 0.0
    min_price_to_sell:    float = 15.0  # aligned with main.py instantiation (was 30.0)
    nifty_instrument_key: str   = "NSE_INDEX|Nifty 50"
    strike_interval:      float = 50.0
    # Instrument identity fields — set these for BankNifty
    instrument_name:      str   = "NIFTY"               # "NIFTY" or "BANKNIFTY"
    index_instrument_key: str   = "NSE_INDEX|Nifty 50"  # "NSE_INDEX|Nifty Bank" for BankNifty
    lot_size:             int   = 65                     # 15 for BankNifty
    paper_trade_override: bool  = False                  # force paper mode for this instance
    strategy_name:        str   = "survivor"             # override for BankNifty: "bn_survivor" 
    hedge_enabled:        bool  = True                   # buy a protective far-OTM leg with every short (caps tail risk)
    hedge_gap:            float = 150.0                  # points further OTM than the short strike, for the hedge leg
    use_delta_selection:  bool  = True                   # LIVE: use delta-based strike selection with premium fallback
    target_delta:         float = 0.15                   # target delta for future delta-based strike selection
    delta_tolerance:      float = 0.05                   # acceptable search window around target_delta
    expiry_weekday:       int   = 1                      # weekly expiry weekday: Nifty=1 (Tuesday), BankNifty=2 (Wednesday)
    sell_multiplier_threshold: int = 2                    # caps overshoot scaling (master port) — max lots multiplier per single trigger


class SurvivorAlgo(BaseStrategy):

    def __init__(self, broker: AbstractBrokerGateway, config: SurvivorConfig):
        super().__init__(name=config.strategy_name, broker=broker, config=vars(config))
        self.cfg               = config
        self._loop = None
        self._pe_last_value    = config.pe_start
        self._ce_last_value    = config.ce_start
        self._pe_sold_flag     = False
        self._ce_sold_flag     = False
        self._open_trade_ids   = []
        self._open_trades_data = []
        self._trades_reloaded  = False  # gate: block entries until DB reload completes
        _seeded = state_store.get_strategy(self.name)
        self._realised_pnl     = _seeded.realised_pnl if _seeded else 0.0
        self._unrealised_pnl   = 0.0
        self._last_nifty_price = 0.0
        self._closed_trades    = 0
        self._ltp_cache        = {}  # symbol -> latest LTP
        self._instruments      = []  # cached instruments list
        self._ikey_cache       = {}  # text symbol -> instrument key
        # Time-based trigger state
        self._time_based_pe_fired = False  
        self._time_based_ce_fired = False  
        self._last_time_trigger_day = -1   
        # Idempotent order gate
        self._pending_orders: set = set()  
        # Exit precedence gate
        self._closing_trades: set = set()  
        self._last_block_reason   = ""   # pre-trade gate: log only on state change
        # Resolved once at init — avoids repeated os.getenv calls in signal logic
        self._is_paper = (
            os.getenv("PAPER_TRADE", "false").lower() == "true"
            or self.cfg.paper_trade_override
        )

    def reset_daily_strategy_state(self, morning_open_price: float) -> None:
        """
        Master daily reset hook called at market open to clear yesterday's data.
        """
        logger.info(f"[{self.name}] Running daily reset. Re-anchoring spot to: ₹{morning_open_price:.2f}")
        
        # 1. Clear flat market time trigger flags
        self._time_based_pe_fired = False
        self._time_based_ce_fired = False
        
        # 2. Re-anchor price gaps cleanly to today's morning opening price
        self._pe_last_value = morning_open_price
        self._ce_last_value = morning_open_price
        self.cfg.pe_start = morning_open_price
        self.cfg.ce_start = morning_open_price
        
        # 3. Reset execution statuses
        self._pe_sold_flag = False
        self._ce_sold_flag = False
        
        # 4. Flush memory grids
        self._pending_orders.clear()
        self._closing_trades.clear()
        
        self._signal(f"🔄 Daily State Sync Complete | Base Anchor: {morning_open_price:.2f}")
    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        self._loop = asyncio.get_running_loop()

        # Use index_instrument_key (supports both NIFTY and BANKNIFTY)
        _index_key = self.cfg.index_instrument_key or self.cfg.nifty_instrument_key
        nifty_price = await self.broker.get_ltp(_index_key)
        if nifty_price == 0.0:
            raise RuntimeError(f"[survivor] Could not fetch {self.cfg.instrument_name} price on startup.")

        if self.cfg.pe_start == 0.0:
            self.cfg.pe_start = nifty_price
        if self.cfg.ce_start == 0.0:
            self.cfg.ce_start = nifty_price

        self._pe_last_value = self.cfg.pe_start
        self._ce_last_value = self.cfg.ce_start

        _index_key = self.cfg.index_instrument_key or self.cfg.nifty_instrument_key
        self.broker.subscribe_ticks(
            symbols=[_index_key],
            callback=self._on_tick_sync
        )
        asyncio.create_task(self._refresh_ltp_loop())
        asyncio.create_task(self._auto_stop_watchdog())
        logger.info("[survivor] LTP refresh loop started")
        logger.info("[survivor] Auto-stop watchdog started")
        await asyncio.sleep(5)  # Wait for WebSocket
        await self._reload_open_trades()
        self._trades_reloaded = True
        try:
            import sqlite3 as _sq, pathlib as _pl
            from datetime import date as _dt
            _db = _pl.Path('trade_log.db')
            with _sq.connect(str(_db)) as _c:
                _r = _c.execute("SELECT COUNT(*) FROM trades WHERE status='CLOSED' AND DATE(entry_time)=?", (_dt.today().isoformat(),)).fetchone()
                self._closed_trades = _r[0] if _r else 0
                print(f'[survivor] Seeded _closed_trades={self._closed_trades}')
        except Exception as _e:
            print(f'[survivor] Could not seed counter: {_e}')

        logger.info(
            f"[survivor] PE Anchor: {self._pe_last_value} | "
            f"CE Anchor: {self._ce_last_value}"
        )
        # Register regime change callback — close positions if regime flips to trending
        try:
            from core.market_context import market_context
            def _on_regime_change(old_regime: str, new_regime: str) -> None:
                if new_regime in ("trending_bull", "trending_bear") and self._open_trades_data:
                    logger.warning(
                        f"[survivor] Regime flipped {old_regime} → {new_regime} "
                        f"— starting 60s confirmation timer before exit"
                    )
                    self._signal(f"⚠ REGIME CHANGE: {old_regime} → {new_regime} — waiting 60s to confirm")
                    import threading, time as _time
                    _timer_start = _time.time()
                    def _confirm_and_exit():
                        _time.sleep(90)
                        # Guard: abort if strategy stopped or restarted during sleep
                        if self._stop_flag:
                            logger.info(f"[survivor] Regime timer aborted — strategy stopped")
                            return
                        if _time.time() - _timer_start > 120:
                            logger.info(f"[survivor] Regime timer aborted — too stale")
                            return
                        try:
                            from core.market_context import market_context as _mc
                            current = _mc._regime
                        except Exception:
                            current = new_regime
                        if current == new_regime and self._open_trades_data:
                            logger.warning(
                                f"[survivor] Regime {new_regime} confirmed after 90s "
                                f"— closing {len(self._open_trades_data)} trades"
                            )
                            self._signal(f"⚠ REGIME CHANGE CONFIRMED: {old_regime} → {new_regime} — closing all")
                            loop = self._loop
                            if loop and loop.is_running():
                                import asyncio as _asyncio
                                _asyncio.run_coroutine_threadsafe(
                                    self._close_all_positions(reason="REGIME_CHANGE"), loop
                                )
                            self._pe_sold_flag = False
                            self._ce_sold_flag = False
                        else:
                            logger.info(
                                f"[survivor] Regime reverted from {new_regime} within 90s "
                                f"— staying in trades (current={current})"
                            )
                            self._signal(f"✅ Regime reverted {new_regime} → {current} — trades kept open")
                    threading.Thread(target=_confirm_and_exit, daemon=True).start()
            market_context.register_regime_callback(_on_regime_change)
            logger.info("[survivor] Regime change callback registered")
        except Exception as e:
            logger.error(f"[survivor] Could not register regime callback: {e}")

        self._signal(
            f"Started | Spot: {nifty_price:.2f} | "
            f"PE Anchor: {self._pe_last_value:.2f} | "
            f"CE Anchor: {self._ce_last_value:.2f}"
        )

    async def on_stop(self) -> None:
        import pytz
        from datetime import datetime as _dt
        now = _dt.now(pytz.timezone("Asia/Kolkata"))
        is_eod = (now.hour > 15) or (now.hour == 15 and now.minute >= 5)
        if is_eod:
            logger.info("[survivor] on_stop: EOD — closing all positions")
            await self._close_all_positions(reason="EOD")
        else:
            logger.info(
                f"[survivor] on_stop: {now.strftime('%H:%M')} IST — "
                f"NOT EOD, skipping close. Positions reload on next start."
            )
        _index_key = self.cfg.index_instrument_key or self.cfg.nifty_instrument_key
        self.broker.unsubscribe_ticks([_index_key])

    # ── Tick Handling ─────────────────────────────────────────────────────────

    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return
        # Option ticks have low prices; index ticks have high prices
        # Use 5000 as threshold to separate options from index for both NIFTY and BANKNIFTY
        if tick.last_price < 5000:
            # ─── MITIGATION POINT ────────────────────────────────────────────────
            # Capture the clean bid-ask mid_price instead of volatile last_price.
            # This protects risk tracking, breakeven logs, and stop losses from 
            # artificial market spikes.
            # ─────────────────────────────────────────────────────────────────────
            self._ltp_cache[tick.symbol] = tick.mid_price
            self._calculate_pnl()
            # Also run SL/TP monitoring on option ticks
            loop = self._loop
            if loop is None or not loop.is_running():
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_running_loop()
                except Exception:
                    pass
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._monitor_open_trades(self._last_nifty_price), loop
                )
            return
        if not self._trades_reloaded:
            return  # block all entries until DB reload completes on startup
        # Log tick only once per minute to reduce log volume
        _now_ts = __import__('time').time()
        if not hasattr(self, '_last_tick_log') or _now_ts - self._last_tick_log >= 60:
            logger.info(f"[{self.cfg.strategy_name}] {self.cfg.instrument_name} tick: {tick.last_price:.2f} | PE anchor: {self._pe_last_value:.2f} | diff: {tick.last_price - self._pe_last_value:.2f}")
            self._last_tick_log = _now_ts
        self._last_nifty_price = tick.last_price
        loop = self._loop
        if loop is None or not loop.is_running():
            import asyncio as _asyncio
            try:
                loop = _asyncio.get_running_loop()
            except Exception:
                pass
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), loop)
        else:
            logger.error(f"[survivor] No loop available")

    async def on_tick(self, tick: Tick) -> None:
        try:
            if self._stop_flag or not self.is_market_open():
                return
            # ── Global pre-trade gate (#24) ────────────────────────────────────
            blocked, reason = risk_manager.is_trading_blocked()
            if blocked:
                if reason != self._last_block_reason:
                    self._last_block_reason = reason
                    logger.info(f"[survivor] Trading blocked: {reason}")
                    if "daily loss" in reason.lower():
                        await self._close_all_positions(reason="LOSS_LIMIT")
                        await self.stop(reason="MAX_DAILY_LOSS")
                    elif "circuit breaker" in reason.lower() or "halted" in reason.lower():
                        await self.stop(reason="API_CIRCUIT_BREAKER")
                    elif "auto-stop" in reason.lower():
                        await self._close_all_positions(reason="EOD")
                        await self.stop(reason="AUTO_STOP")
                return
            else:
                self._last_block_reason = ""
            nifty_price            = tick.last_price
            self._last_nifty_price = nifty_price

            # Use session planner as single source of truth for gap params.
            # VIX manager only used for halt_trading safety check.
            vix_params = vix_manager.get_params()
            if vix_params.get("halt_trading", False):
                self._signal("VIX EXTREME — trading halted by vix_manager")
                return

            try:
                from core.session_planner import session_planner as _sp
                sp_params = dict(_sp.current_plan.params.__dict__)
            except Exception:
                sp_params = {}

            current_pe_gap = sp_params.get("pe_gap", self.cfg.pe_gap)
            current_ce_gap = sp_params.get("ce_gap", self.cfg.ce_gap)
            pe_symbol_gap  = sp_params.get("pe_symbol_gap", self.cfg.pe_symbol_gap)
            ce_symbol_gap  = sp_params.get("ce_symbol_gap", self.cfg.ce_symbol_gap)

            # Monitor open trades for SL and trailing profit
            await self._monitor_open_trades(nifty_price)
            self._calculate_pnl(nifty_price)

            can_trade, reason = risk_manager.can_trade(self.name)
            if can_trade:
                sf_ok, sf_reason = strategy_filter.can_trade(self.name)
                if not sf_ok:
                    can_trade = False
                    reason = f"[context] {sf_reason}"
                    risk_manager._log_blocked_once(self.name, reason)

            if can_trade and len(self._open_trades_data) >= 2:
                can_trade = False
                reason = "Max open trades reached (2)"
            # Block if already have an open trade in same direction
            _open_ce = sum(1 for t in self._open_trades_data if t["direction"] == "CE")
            _open_pe = sum(1 for t in self._open_trades_data if t["direction"] == "PE")
            if can_trade:
                # ── TRIGGER 1: Movement-based (overshoot-scaled, master port) ──
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
                    self._update_position(Direction.SHORT)

                # ── TRIGGER 2: Time-based (flat market catcher) ───────────
                # Fires once per side per session in 9:45–11:30 AM window
                # when market is flat but PCR confirms direction is safe
                await self._check_time_based_trigger(
                    nifty_price, pe_symbol_gap, ce_symbol_gap
                )

            else:
                logger.debug(f"[{self.name}] Trade blocked: {reason}")

            # PE Reset — market reversed down after a PE sell
            if self._pe_sold_flag and (self._pe_last_value - nifty_price >= self.cfg.pe_reset_gap):
                self._pe_last_value = nifty_price
                self._pe_sold_flag  = False
                self._signal(f"PE anchor reset at {nifty_price:.2f}")

            # CE Reset — market reversed up after a CE sell
            if self._ce_sold_flag and (nifty_price - self._ce_last_value >= self.cfg.ce_reset_gap):
                self._ce_last_value = nifty_price
                self._ce_sold_flag  = False
                self._signal(f"CE anchor reset at {nifty_price:.2f}")

        except Exception as e:
            logger.exception(f"[survivor] ERROR in on_tick: {e}")

    # ── Option Selling ────────────────────────────────────────────────────────

    async def _select_strike_by_delta(
        self, direction: str, nifty_price: float
    ):
        """
        Fetch option chain and return (strike, ikey, premium) for the strike
        whose delta is closest to cfg.target_delta (within cfg.delta_tolerance).
        Returns None on any failure — caller must fall back to premium-threshold method.
        """
        if not hasattr(self.broker, "get_option_chain"):
            return None
        try:
            import pytz as _pytz
            from datetime import datetime as _dt
            from core.auto_config import get_nearest_monthly_expiry
            _today = _dt.now(_pytz.timezone("Asia/Kolkata")).date()
            expiry_date = (
                get_nearest_monthly_expiry(_today)
                if self.cfg.expiry_weekday == 2
                else get_nearest_tuesday(_today)
            )
            expiry_str = expiry_date.strftime("%Y-%m-%d")
            chain = await self.broker.get_option_chain(
                self.cfg.index_instrument_key or self.cfg.nifty_instrument_key,
                expiry_str,
            )
            if not chain:
                logger.warning("[survivor] Delta selection: empty chain — falling back to premium method")
                return None
            target    = self.cfg.target_delta
            tolerance = self.cfg.delta_tolerance
            best_row  = None
            best_diff = float("inf")
            for row in chain:
                raw_delta = row.get("ce_delta") if direction == "CE" else row.get("pe_delta")
                if not raw_delta:
                    continue
                diff = abs(abs(raw_delta) - target)
                if diff < best_diff:
                    best_diff = diff
                    best_row  = row
            if best_row is None or best_diff > tolerance:
                logger.warning(
                    f"[survivor] Delta selection: no strike within tolerance "
                    f"{tolerance} of target Δ={target} — falling back to premium method"
                )
                return None
            delta_strike = best_row["strike"]
            delta_value  = best_row.get("ce_delta") if direction == "CE" else best_row.get("pe_delta")
            delta_ltp    = best_row.get("ce_ltp")   if direction == "CE" else best_row.get("pe_ltp")
            ikey = await self._get_instrument_key(
                self._build_symbol(direction, delta_strike), direction, delta_strike
            )
            # Verify premium meets minimum threshold
            premium = self._ltp_cache.get(ikey, delta_ltp or 0.0)
            if premium <= 0.0:
                premium = await self.broker.get_ltp(ikey)
            if premium < self.cfg.min_price_to_sell:
                logger.warning(
                    f"[survivor] Delta strike {delta_strike} (Δ={delta_value:.3f}) "
                    f"premium ₹{premium:.1f} below min ₹{self.cfg.min_price_to_sell} — falling back"
                )
                return None
            logger.info(
                f"[survivor] Delta selection: {direction} {delta_strike:.0f} "
                f"Δ={delta_value:.3f} premium=₹{premium:.1f} target_Δ={target}"
            )
            return (delta_strike, ikey, premium)
        except Exception as e:
            logger.warning(f"[survivor] Delta selection failed (non-blocking): {e} — falling back to premium method")
            return None

    def _get_vix_adjusted_quantity(self, base_qty: int) -> int:
        """
        Scale position size based on current VIX regime.
        VERY_LOW (<12)  : 2 lots (130) — low vol, more aggressive
        NORMAL   (12-16): 1 lot  (65)  — standard
        ELEVATED (16-20): 1 lot  (65)  — gap widens, size holds
        HIGH     (20-25): 0 lots       — skip trade (extra risk gate)
        EXTREME  (25+)  : 0 lots       — halted anyway
        Returns 0 to signal caller to skip the trade.
        """
        try:
            vix = vix_manager.current_vix
            regime = vix_manager.get_params()
            regime_name = "UNKNOWN"
            # Derive regime name from VIX thresholds
            if vix < 12:
                regime_name = "VERY_LOW"
            elif vix < 16:
                regime_name = "NORMAL"
            elif vix < 20:
                regime_name = "ELEVATED"
            elif vix < 25:
                regime_name = "HIGH"
            else:
                regime_name = "EXTREME"

            LOT = self.cfg.lot_size
            multipliers = {
                "VERY_LOW": 2,
                "NORMAL":   1,
                "ELEVATED": 1,
                "HIGH":     0,
                "EXTREME":  0,
            }
            lots = multipliers.get(regime_name, 1)
            adjusted = lots * LOT
            if adjusted != base_qty:
                logger.info(
                    f"[survivor] VIX-adjusted qty: {base_qty} -> {adjusted} "
                    f"| VIX={vix:.1f} | Regime={regime_name} | lots={lots}"
                )
            return adjusted
        except Exception as e:
            logger.warning(f"[survivor] VIX qty adjustment failed: {e} — using base qty {base_qty}")
            return base_qty

    async def _sell_option(
        self,
        direction:   str,
        nifty_price: float,
        gap:         float,
        quantity:    int,
        overshoot_multiplier: int = 1,
    ) -> None:
        _is_paper    = self._is_paper
        interval     = self.cfg.strike_interval
        symbol       = None
        final_strike = None
        premium      = 0.0
        _strike_method = "premium"  # for logging

        # ── Delta-based strike selection (with premium-threshold fallback) ──
        if self.cfg.use_delta_selection:
            delta_result = await self._select_strike_by_delta(direction, nifty_price)
            if delta_result is not None:
                final_strike, symbol, premium = delta_result
                _strike_method = "delta"

        # ── Premium-threshold fallback (original method) ──────────────────
        if symbol is None:
            base_strike  = nifty_price - gap if direction == "PE" else nifty_price + gap
            final_strike = round(base_strike / interval) * interval
            for _ in range(5):
                candidate = self._build_symbol(direction, final_strike)
                if _is_paper:
                    ikey = await self._get_instrument_key(candidate, direction, final_strike)
                    premium = self._ltp_cache.get(ikey, self._ltp_cache.get(candidate, 0.0))
                    if premium <= 0.0:
                        premium = await self.broker.get_ltp(ikey)
                else:
                    ikey = await self._get_instrument_key(candidate, direction, final_strike)
                    premium = await self.broker.get_ltp(ikey)
                if premium >= self.cfg.min_price_to_sell:
                    symbol = ikey
                    break
                final_strike += interval if direction == "PE" else -interval

        if not symbol:
            logger.warning(
                f"[survivor] No {direction} strike found above "
                f"₹{self.cfg.min_price_to_sell} — skipping"
            )
            return

        self._signal(f"Strike selected via [{_strike_method}] method | {direction} {int(final_strike)} premium=₹{premium:.1f}")

        # ── Idempotent gate ───────────────────────────────────────────────
        # Unique key = direction + strike + date + minute
        # Prevents duplicate orders if same signal fires twice in same minute
        import pytz as _pytz
        from datetime import datetime as _dt
        _now = _dt.now(_pytz.timezone("Asia/Kolkata"))
        _order_key = f"{direction}_{int(final_strike)}_{_now.strftime('%Y%m%d_%H%M')}"
        if _order_key in self._pending_orders:
            logger.warning(
                f"[survivor] DUPLICATE ORDER BLOCKED | key={_order_key} | "
                f"already in flight this minute"
            )
            self._signal(f"⚠ Duplicate {direction} order blocked — same signal already placed this minute")
            return
        self._pending_orders.add(_order_key)
        # Auto-clear old keys (keep only today's)
        today_prefix = _now.strftime('%Y%m%d')
        self._pending_orders = {k for k in self._pending_orders if today_prefix in k}

        # ── NEW: DETERMINISTIC CLIENT ORDER ID & MUTEX LOCK FOR PM2 RESTARTS ──
        deterministic_client_id = f"SURV_{direction}_{int(final_strike)}_{today_prefix}"

        # Duplicate check — applies to both paper and live mode
        if any(t.get("symbol") == symbol for t in self._open_trades_data):
            logger.warning(f"[survivor] MUTEX LOCK: Already holding {symbol} in paper/live mode. Order blocked.")
            self._pending_orders.discard(_order_key)
            return
        # ── Quantity sanity check — guards against wrong lot size on live trades ──
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
            return
        try:
            if _is_paper:
                sell_price  = round(premium * 0.98, 1)
                entry_price = sell_price
                self._signal(f"[PAPER] SELL {quantity} {symbol} @ {sell_price} (simulated)")
                order_id = f"PAPER_{deterministic_client_id}"
            else:
                # Double-check against already reloaded database entries to prevent multi-execution
                if any(t.get("symbol") == symbol for t in self._open_trades_data):
                    logger.warning(f"[survivor] MUTEX LOCK: Already holding an active trade for {symbol}. Order blocked.")
                    self._pending_orders.discard(_order_key)
                    return

                ltp        = await self.broker.get_ltp(symbol)
                sell_price = round(ltp * 0.98, 1) if ltp > 0 else 0.0
                resp       = await self.broker.place_order(Order(
                    symbol=symbol,
                    exchange="NFO",
                    order_type="SELL",
                    quantity=quantity,
                    product="I",
                    price=sell_price,
                    tag=deterministic_client_id,  # Sent to Upstox API as session-unique identity
                ))
                if resp.status == "REJECTED":
                    self._signal(f"{direction} order REJECTED: {resp.message}")
                    self._pending_orders.discard(_order_key)  # allow retry on rejection
                    alert_order_rejected(symbol, resp.message)
                    return
                order_id    = resp.order_id
                # ── Fill timeout poller: wait up to 60s for fill, else cancel ──
                _fill_timeout  = 60  # seconds
                _poll_interval = 2   # seconds between checks
                _elapsed       = 0
                entry_price    = 0.0
                filled_qty     = 0
                _fill_confirmed = False
                while _elapsed < _fill_timeout:
                    await asyncio.sleep(_poll_interval)
                    _elapsed += _poll_interval
                    try:
                        orders = await self.broker.get_orders()
                        for o in orders:
                            if getattr(o, "order_id", "") == order_id:
                                _status    = getattr(o, "status", "")
                                filled_qty = getattr(o, "filled_quantity", 0) or 0
                                avg_price  = getattr(o, "average_price", 0.0) or 0.0
                                if _status == "COMPLETE" and filled_qty > 0:
                                    entry_price     = avg_price if avg_price > 0 else await self.broker.get_ltp(symbol)
                                    quantity        = filled_qty
                                    _fill_confirmed = True
                                    logger.info(f"[survivor] Fill confirmed: {filled_qty} @ {entry_price:.2f} in {_elapsed}s")
                                elif _status in ("REJECTED", "CANCELLED"):
                                    self._signal(f"Order {order_id} {_status} — aborting")
                                    self._pending_orders.discard(_order_key)
                                    return
                                elif filled_qty > 0 and filled_qty < quantity:
                                    logger.warning(f"[survivor] PARTIAL FILL so far: {filled_qty}/{quantity} — continuing to wait")
                                break
                    except Exception as fe:
                        logger.debug(f"[survivor] Fill poll error: {fe}")
                    if _fill_confirmed:
                        break
                if not _fill_confirmed:
                    self._signal(f"⏱ Order timeout ({_fill_timeout}s) — cancelling unfilled SELL {order_id}")
                    try:
                        await self.broker.cancel_order(order_id)
                    except Exception as ce:
                        logger.warning(f"[survivor] Cancel after timeout failed: {ce}")
                    self._pending_orders.discard(_order_key)
                    return

            trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=symbol,
                readable_symbol=self._build_symbol(direction, final_strike),
                order_type="SELL",
                quantity=quantity,
                entry_price=entry_price,
                broker_order_id=order_id,
                client_order_id=f"SURVIVOR_SELL_{uuid.uuid4().hex[:8]}",
                notes=f"VIX Regime Trigger | Nifty @ {nifty_price:.2f}",
                paper_trade=_is_paper,
            )

            from core.transaction_costs import calculate_order_cost
            trade_data = {
                "id":         trade_id,
                "order_type": "SELL",
                "entry":      entry_price,
                "symbol":     symbol,
                "quantity":   quantity,
                "direction":  direction,
                "entry_cost": calculate_order_cost(entry_price, quantity, "SELL"),
            }
            hedge_ok = await self._open_hedge_leg(
                trade_data, direction, final_strike, nifty_price, quantity, _is_paper
            )
            if hedge_ok is False:
                logger.error(f"[survivor] Hedge failed for {trade_id} -- auto-closing naked short (paper={_is_paper})")
                self._signal(f"\U0001F6A8 HEDGE FAILED -- auto-closing naked short {direction} {int(final_strike)} for safety")
                try:
                    if _is_paper:
                        ltp = self._current_price if getattr(self, "_current_price", 0) else entry_price
                        trade_logger.close_trade(trade_id, ltp, "HEDGE_FAILED_AUTOCLOSE")
                    else:
                        ltp = await self.broker.get_ltp(symbol)
                        await self.broker.place_order(Order(
                            symbol=symbol, exchange="NFO", order_type="BUY",
                            quantity=quantity, product="I", price=round(ltp * 1.02, 1),
                            tag=f"HEDGE_FAIL_CLOSE_{trade_id[-6:]}",
                        ))
                        trade_logger.close_trade(trade_id, ltp, "HEDGE_FAILED_AUTOCLOSE")
                except Exception as ce:
                    logger.error(f"[survivor] Auto-close after hedge failure failed: {ce}")
                    self._signal(f"\U0001F6A8\U0001F6A8 CRITICAL: naked short {symbol} -- hedge AND auto-close FAILED -- manual intervention required")
                self._pending_orders.discard(_order_key)
                return
            self._open_trade_ids.append(trade_id)
            self._open_trades_data.append(trade_data)

            # Subscribe to live ticks using instrument key
            ikey = await self._get_instrument_key(symbol, direction, final_strike)
            self.broker.subscribe_ticks(
                symbols=[ikey],
                callback=self._on_tick_sync
            )
            self._ikey_cache[symbol] = ikey

            risk_manager.register_trade(self.name, "SELL", multiplier=overshoot_multiplier)
            self._signal(
                f"SOLD {direction} {int(final_strike)} @ ₹{entry_price:.2f} | "
                f"Order: {order_id}" + (f" | {overshoot_multiplier}x lots" if overshoot_multiplier > 1 else "")
            )
            alert_trade_opened(symbol, direction, entry_price, quantity, int(final_strike))

            # Place GTT Trailing SL immediately after live trade opens
            _is_paper = self._is_paper
            if not _is_paper and hasattr(self.broker, 'place_gtt_trailing_sl'):
                gtt_placed = False
                for _gtt_attempt in range(2):  # 1 retry
                    try:
                        gtt_id = await self.broker.place_gtt_trailing_sl(
                            instrument_key=ikey,
                            quantity=quantity,
                            entry_price=entry_price,
                            order_type="SELL",
                            trailing_gap=0.25,
                            sl_pct=0.15,
                        )
                        if gtt_id:
                            self._signal(f"🛡 GTT Trailing SL placed | id={gtt_id} | trigger={round(entry_price*1.15,1)}")
                            gtt_placed = True
                            break
                        else:
                            logger.warning(f"[survivor] GTT attempt {_gtt_attempt+1} returned empty ID")
                            await asyncio.sleep(2)
                    except Exception as ge:
                        logger.warning(f"[survivor] GTT attempt {_gtt_attempt+1} failed: {ge}")
                        await asyncio.sleep(2)
                if not gtt_placed:
                    alert_gtt_failed(symbol, "GTT failed after 2 attempts — trade has NO broker-side stop")
                    self._signal(f"🚨 GTT FAILED — {symbol} has no broker stop protection — auto-closing position for safety")
                    _trade_to_close = next(
                        (t for t in self._open_trades_data if t["id"] == trade_id), None
                    )
                    if _trade_to_close:
                        try:
                            await self._close_trade(_trade_to_close, "GTT_FAILED_AUTOCLOSE", 0.0)
                            self._signal(f"🛑 Position auto-closed | {symbol} | reason=GTT_FAILED")
                        except Exception as ce:
                            logger.error(f"[survivor] Auto-close after GTT failure also failed for {symbol}: {ce}")
                            self._signal(f"🚨🚨 CRITICAL: {symbol} has NO GTT and auto-close FAILED — manual intervention required")
                    else:
                        logger.error(f"[survivor] Could not find trade {trade_id} in open_trades_data for auto-close")

        except Exception as e:
            logger.error(f"[survivor] _sell_option failed for {direction}: {e}")

    async def _open_hedge_leg(
        self,
        trade_data:   dict,
        direction:    str,
        short_strike: float,
        nifty_price:  float,
        quantity:     int,
        is_paper:     bool,
    ) -> None:
        """
        Buys a far-OTM protective leg against the short option just sold,
        converting the naked short into a defined-risk credit spread.
        Caps max loss structurally -- independent of GTT, server uptime,
        or broker connectivity. Mutates trade_data in place with hedge fields.
        """
        trade_data["hedge_symbol"]     = None
        trade_data["hedge_entry"]      = 0.0
        trade_data["hedge_quantity"]   = 0
        trade_data["hedge_trade_id"]   = None
        trade_data["hedge_entry_cost"] = 0.0

        if not self.cfg.hedge_enabled:
            return

        interval  = self.cfg.strike_interval
        hedge_gap = self.cfg.hedge_gap
        # Further OTM than the short strike, in the same direction away from spot
        base_hedge_strike = (
            short_strike - hedge_gap if direction == "PE" else short_strike + hedge_gap
        )
        hedge_strike = round(base_hedge_strike / interval) * interval

        hedge_symbol  = None
        hedge_premium = 0.0

        for _ in range(4):
            candidate = self._build_symbol(direction, hedge_strike)
            if is_paper:
                hedge_premium = max(1.0, 50.0 - abs(nifty_price - hedge_strike) * 0.1)
                hedge_ikey = candidate
            else:
                hedge_ikey = await self._get_instrument_key(candidate, direction, hedge_strike)
                hedge_premium = await self.broker.get_ltp(hedge_ikey)

            if hedge_premium > 0.0:
                hedge_symbol = hedge_ikey
                break
            # Step further OTM if illiquid / zero LTP
            hedge_strike += (-interval if direction == "PE" else interval)

        if not hedge_symbol:
            logger.error(
                f"[survivor] HEDGE LEG FAILED -- no liquid {direction} strike found near "
                f"{hedge_strike:.0f}. Short position has NO structural hedge."
            )
            self._signal(f"\U0001F6A8 HEDGE LEG FAILED for {direction} -- position is UNHEDGED, monitor closely")
            return False

        try:
            if is_paper:
                buy_price = round(hedge_premium * 1.02, 1)
                self._signal(f"[PAPER] BUY {quantity} {hedge_symbol} @ {buy_price} (hedge, simulated)")
                hedge_order_id = f"PAPER_HEDGE_{int(time.time()*1000) % 100000}"
            else:
                resp = await self.broker.place_order(Order(
                    symbol=hedge_symbol,
                    exchange="NFO",
                    order_type="BUY",
                    quantity=quantity,
                    product="I",
                    price=round(hedge_premium * 1.02, 1),
                    tag=f"HEDGE_{direction}_{int(hedge_strike)}_{int(time.time()*1000) % 100000}",
                ))
                if resp.status == "REJECTED":
                    logger.error(f"[survivor] Hedge BUY REJECTED for {hedge_symbol}: {resp.message}")
                    self._signal(f"\U0001F6A8 HEDGE ORDER REJECTED for {direction} -- position is UNHEDGED")
                    return False
                buy_price = await self.broker.get_ltp(hedge_symbol)
                hedge_order_id = resp.order_id
                self.broker.subscribe_ticks(symbols=[hedge_ikey], callback=self._on_tick_sync)
                self._ikey_cache[hedge_symbol] = hedge_ikey

            hedge_trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=hedge_symbol,
                readable_symbol=candidate,
                order_type="BUY",
                quantity=quantity,
                entry_price=buy_price,
                broker_order_id=hedge_order_id,
                client_order_id=f"SURVIVOR_HEDGE_BUY_{uuid.uuid4().hex[:8]}",
                notes=f"HEDGE leg for {direction} short @ {short_strike:.0f}",
                parent_trade_id=trade_data.get("id", ""),
                paper_trade=is_paper,
            )

            from core.transaction_costs import calculate_order_cost
            trade_data["hedge_symbol"]     = hedge_symbol
            trade_data["hedge_entry"]      = buy_price
            trade_data["hedge_ok"]         = True
            trade_data["hedge_quantity"]   = quantity
            trade_data["hedge_trade_id"]   = hedge_trade_id
            trade_data["hedge_entry_cost"] = calculate_order_cost(buy_price, quantity, "BUY")

            # Reserve hedge-leg capital (BUY-side premium) -- previously never
            # registered, meaning _deployed_capital under-counted real exposure
            # whenever a hedge was active (see capital-tracking investigation, 31-Jul).
            # Using register_capital (not register_trade) deliberately -- the main
            # SELL leg already counts as this position's "1 trade" for the daily
            # trade-count limit; the hedge is capital-only, not a second trade.
            _hedge_lots = max(1, int(quantity // self.cfg.lot_size))
            risk_manager.register_capital(self.name, "BUY", multiplier=_hedge_lots)

            max_loss = abs(hedge_strike - short_strike) * quantity
            self._signal(
                f"\U0001F6E1 HEDGE BOUGHT {hedge_symbol} @ \u20b9{buy_price:.2f} | "
                f"Spread width: {abs(hedge_strike - short_strike):.0f} pts | "
                f"Structural max loss: \u2248\u20b9{max_loss:,.0f}"
            )
        except Exception as e:
            logger.error(f"[survivor] _open_hedge_leg failed: {e}")
            self._signal(f"\U0001F6A8 HEDGE LEG ERROR for {direction} -- position may be UNHEDGED")
            return False

    async def _log_delta_shadow_comparison(
        self, direction: str, actual_strike: float, nifty_price: float
    ) -> None:
        """
        SHADOW MODE -- logs what delta-based strike selection would have
        picked, side-by-side with the actual premium-threshold pick the bot
        just used. Does NOT change which strike gets traded. Pure
        observation only. Only meaningful in live mode (no real Greeks
        exist in paper mode). Any failure here is swallowed and never
        affects the real trade.
        """
        if not hasattr(self.broker, "get_option_chain"):
            return
        try:
            import pytz as _pytz
            from datetime import datetime as _dt
            # Use canonical expiry function from auto_config (handles holidays + rollover)
            # rather than a raw weekday formula that has no holiday awareness.
            _today = _dt.now(_pytz.timezone("Asia/Kolkata")).date()
            from core.auto_config import get_nearest_monthly_expiry
            expiry_date = (
                get_nearest_monthly_expiry(_today)
                if self.cfg.expiry_weekday == 2
                else get_nearest_tuesday(_today)
            )
            expiry_str = expiry_date.strftime("%Y-%m-%d")

            chain = await self.broker.get_option_chain(
                self.cfg.index_instrument_key or self.cfg.nifty_instrument_key,
                expiry_str,
            )
            if not chain:
                logger.debug("[survivor] SHADOW: empty option chain, skipping comparison")
                return

            target = self.cfg.target_delta
            best_row  = None
            best_diff = float("inf")
            for row in chain:
                raw_delta = row.get("ce_delta") if direction == "CE" else row.get("pe_delta")
                if not raw_delta:
                    continue
                diff = abs(abs(raw_delta) - target)
                if diff < best_diff:
                    best_diff = diff
                    best_row  = row

            if best_row is None:
                logger.info(f"[survivor] SHADOW: no usable delta data in chain for {direction}")
                return

            delta_strike  = best_row["strike"]
            delta_value   = best_row.get("ce_delta") if direction == "CE" else best_row.get("pe_delta")
            delta_premium = best_row.get("ce_ltp") if direction == "CE" else best_row.get("pe_ltp")

            agree = "MATCH" if delta_strike == actual_strike else "DIFFERS"
            self._signal(
                f"\U0001F52C SHADOW [{agree}] {direction} | Premium-pick: {actual_strike:.0f} | "
                f"Delta-pick: {delta_strike:.0f} (Δ={delta_value:.3f}, "
                f"premium=₹{delta_premium:.1f}) | target Δ={target}"
            )
        except Exception as e:
            logger.debug(f"[survivor] SHADOW comparison failed (non-blocking): {e}")

    async def _on_recover_trade(self, row: dict) -> None:
        """Restore an orphaned OPEN trade from the DB into live tracking."""
        symbol = row.get("symbol", "")
        direction = "PE" if "PE" in symbol else ("CE" if "CE" in symbol else "")

        # ── Staleness check — don't silently re-adopt positions from a prior day/session ──
        entry_time_str = row.get("entry_time")
        if entry_time_str:
            entry_dt = datetime.fromisoformat(entry_time_str)
            age_hours = (datetime.now() - entry_dt).total_seconds() / 3600
            if age_hours > STALE_POSITION_MAX_AGE_HOURS:
                logger.warning(
                    f"[{self.name}] Recovered position {row.get('id')} is "
                    f"{age_hours:.1f}h old (>{STALE_POSITION_MAX_AGE_HOURS}h) — "
                    f"treating as stale, forcing close instead of re-adopting."
                )
                raise ValueError(f"stale position {row.get('id')} — force close")

        self._open_trade_ids.append(row.get("id"))
        recovered_trade = {
            "id":         row.get("id"),
            "order_type": row.get("order_type", "SELL"),
            "entry":      row.get("entry_price"),
            "symbol":     symbol,
            "quantity":   row.get("quantity"),
            "direction":  direction,
        }
        # Re-attach hedge leg if one exists -- without this, a hedge opened
        # before a restart would be permanently orphaned (open forever, never
        # closed), since hedge_symbol/hedge_entry/hedge_quantity/hedge_trade_id
        # previously only ever lived in the in-memory dict and had no DB-side
        # link back from the primary leg's own row.
        hedge_row = trade_logger.get_open_hedge_for(row.get("id"))
        if hedge_row:
            recovered_trade["hedge_symbol"]     = hedge_row.get("symbol")
            recovered_trade["hedge_entry"]      = hedge_row.get("entry_price")
            recovered_trade["hedge_quantity"]   = hedge_row.get("quantity")
            recovered_trade["hedge_trade_id"]   = hedge_row.get("id")
            recovered_trade["hedge_entry_cost"] = 0.0  # recomputed fresh at close time, restart-safe
            self._signal(
                f"\U0001F517 Re-attached hedge {hedge_row.get('symbol')} to recovered trade {symbol}"
            )
        else:
            recovered_trade["hedge_symbol"]     = None
            recovered_trade["hedge_entry"]      = 0.0
            recovered_trade["hedge_quantity"]   = 0
            recovered_trade["hedge_trade_id"]   = None
            recovered_trade["hedge_entry_cost"] = 0.0
        self._open_trades_data.append(recovered_trade)
        if direction == "PE":
            self._pe_sold_flag = True
        elif direction == "CE":
            self._ce_sold_flag = True
        self._update_position("SHORT")
        state_store.update_orders(self.name, len(self._open_trades_data))
        state_store.update_trades(
            self.name,
            total=len(self._open_trades_data),
            open_count=len(self._open_trades_data),
            closed=0,
        )
        try:
            ikey = await self._get_instrument_key(symbol, direction, 0)
            self.broker.subscribe_ticks(symbols=[ikey], callback=self._on_tick_sync)
            self._ikey_cache[symbol] = ikey
        except Exception as e:
            logger.error(f"[survivor] Could not resubscribe ticks for recovered trade {symbol}: {e}")

    async def _get_instrument_key(self, symbol: str, direction: str, strike: float) -> str:
        """Lookup instrument key for a text symbol for WebSocket subscription."""
        if symbol in self._ikey_cache:
            return self._ikey_cache[symbol]
        try:
            import pytz
            from datetime import datetime as dt
            if not self._instruments:
                self._instruments = fetch_instruments()
            # Use canonical expiry function from auto_config (handles holidays + rollover)
            # rather than a raw weekday formula that has no holiday awareness.
            _today = dt.now(pytz.timezone("Asia/Kolkata")).date()
            from core.auto_config import get_nearest_monthly_expiry
            expiry = (
                get_nearest_monthly_expiry(_today)
                if self.cfg.expiry_weekday == 2
                else get_nearest_tuesday(_today)
            )
            # Ensure strike is reasonable (not full symbol number)
            clean_strike = int(strike) if strike < 100000 else int(str(int(strike))[-5:])
            _underlying = "BANKNIFTY" if self.cfg.expiry_weekday == 2 else "NIFTY"
            ikey = find_symbol_from_instruments(
                self._instruments, expiry, clean_strike, direction, underlying=_underlying
            )
            if ikey:
                self._ikey_cache[symbol] = ikey
                logger.info(f"[survivor] instrument key found: {symbol} -> {ikey}")
                return ikey
        except Exception as e:
            logger.warning(f"[survivor] instrument key lookup failed: {e}")
        logger.warning(f"[survivor] using text fallback for {symbol}")
        self._ikey_cache[symbol] = symbol  # cache fallback to prevent repeated lookup
        return symbol  # fallback to text symbol

    def _build_symbol(self, option_type: str, strike: float) -> str:
        return f"NSE_FO|{self.cfg.symbol_initials}{int(strike):05d}{option_type}"

    async def _reload_open_trades(self) -> None:
        """
        On startup, reload OPEN trades from DB and reconcile against
        broker positions. Closes DB trades that no longer exist on broker.
        """
        try:
            open_trades = trade_logger.get_trades(strategy=self.name, status="OPEN")
            if not open_trades:
                logger.info("[survivor] No open trades found in database to reload.")
                return

            # Fetch live broker positions for reconciliation
            broker_symbols = set()
            _is_paper = self._is_paper
            if not _is_paper:
                try:
                    import upstox_client
                    cfg = upstox_client.Configuration()
                    cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
                    client = upstox_client.ApiClient(cfg)
                    api = upstox_client.PortfolioApi(client)
                    resp = api.get_short_term_positions(api_version="2.0")
                    if resp and resp.data:
                        for pos in resp.data:
                            if pos.quantity != 0:
                                broker_symbols.add(pos.instrument_token)
                    logger.info(f"[survivor] Broker positions on startup: {len(broker_symbols)} open")
                except Exception as be:
                    logger.warning(f"[survivor] Could not fetch broker positions: {be} — using DB only")

            reloaded = 0
            for t in open_trades:
                if any(x["id"] == t["id"] for x in self._open_trades_data):
                    continue

                # If broker data available and symbol not in broker — close in DB
                if broker_symbols and t["symbol"] not in broker_symbols:
                    logger.warning(
                        f"[survivor] RECONCILE: DB trade {t['id'][:8]} ({t['symbol']}) "
                        f"not found in broker — marking CLOSED"
                    )
                    trade_logger.close_trade(
                        t["id"], t["entry_price"],
                        "RECONCILE_CLOSE — not in broker positions on startup"
                    )
                    alert_reconcile_mismatch(t["id"], t["symbol"])
                    continue

                direction = "CE" if t["symbol"].endswith("CE") else "PE"
                self._open_trades_data.append({
                    "id":         t["id"],
                    "order_type": t["order_type"],
                    "entry":      t["entry_price"],
                    "symbol":     t["symbol"],
                    "quantity":   t["quantity"],
                    "direction":  direction,
                })
                self._open_trade_ids.append(t["id"])
                try:
                    sym_name = t["symbol"].split("|")[-1]
                    strike = float(''.join(filter(str.isdigit, sym_name))[-5:])
                    ikey = await self._get_instrument_key(t["symbol"], direction, strike)
                    self.broker.subscribe_ticks(
                        symbols=[ikey],
                        callback=self._on_tick_sync
                    )
                    self._ikey_cache[t["symbol"]] = ikey
                    logger.info(f"[survivor] Subscribed to ticks for reloaded trade: {ikey}")
                except Exception as sub_e:
                    logger.error(f"[survivor] Could not subscribe for reloaded trade: {sub_e}")
                reloaded += 1

            if reloaded > 0:
                logger.info(f"[survivor] Reloaded {reloaded} open trade(s) after reconciliation.")
                self._signal(f"Reloaded {reloaded} open trade(s) from previous session.")
            else:
                logger.info("[survivor] No open trades to reload after reconciliation.")

            # ── Reverse check: broker positions not in DB ─────────────────
            # This catches the dangerous case: broker has open position but
            # bot DB has no record — would allow new trades over existing ones
            if broker_symbols and not _is_paper:
                db_symbols = {t["symbol"] for t in self._open_trades_data}
                orphan_broker = broker_symbols - db_symbols
                if orphan_broker:
                    logger.warning(
                        f"[survivor] RECONCILE WARNING: {len(orphan_broker)} broker "
                        f"position(s) not in DB: {orphan_broker}"
                    )
                    self._signal(
                        f"⚠ RECONCILE: {len(orphan_broker)} broker position(s) not in DB — "
                        f"max_open_trades reduced to account for unknown positions"
                    )
                    # Count orphan positions against open trades limit
                    for orphan in orphan_broker:
                        self._open_trades_data.append({
                            "id":         f"ORPHAN_{orphan}",
                            "order_type": "SELL",
                            "entry":      0.0,
                            "symbol":     orphan,
                            "quantity":   65,
                            "direction":  "CE" if "CE" in orphan else "PE",
                        })
                        logger.warning(f"[survivor] Orphan position added to open trades: {orphan}")
                    from core.alerting import send_telegram
                    send_telegram(
                        f"🚨 RECONCILE MISMATCH\n"
                        f"{len(orphan_broker)} broker position(s) not tracked in DB:\n"
                        f"{', '.join(str(s) for s in orphan_broker)}\n"
                        f"Bot has reduced available trade slots accordingly.",
                        level="🚨"
                    )
        except Exception as e:
            logger.error(f"[survivor] Failed to reload open trades: {e}")


    async def _auto_stop_watchdog(self) -> None:
        logger.info("[survivor] Auto-stop watchdog started")
        _last_reconcile = 0
        while not self._stop_flag:
            await asyncio.sleep(30)
            try:
                from core.risk_manager import risk_manager
                if risk_manager.check_auto_stop():
                    logger.warning("[survivor] WATCHDOG: EOD auto-stop triggered")
                    self._signal("WATCHDOG: EOD 3:05 PM — closing all positions")
                    await self._close_all_positions(reason="EOD")
                    await self.stop(reason="AUTO_STOP_WATCHDOG")
                    # Generate daily report at EOD
                    try:
                        from core.trade_journal import generate_daily_report, print_report
                        report = generate_daily_report()
                        print_report(report)
                    except Exception as je:
                        logger.warning(f"[survivor] EOD journal failed: {je}")
                    return

                # Mid-session reconciliation every 5 minutes
                import time as _t
                import pytz
                from datetime import datetime as _dt, time as _dtime
                now = _dt.now(pytz.timezone("Asia/Kolkata"))
                market_open = _dtime(9, 30) <= now.time() <= _dtime(15, 5)
                _is_paper = (
                    self._is_paper
                    or self.cfg.paper_trade_override
                )
                if (
                    market_open
                    and not _is_paper
                    and self._open_trades_data
                    and _t.time() - _last_reconcile > 300  # every 5 minutes
                ):
                    _last_reconcile = _t.time()
                    try:
                        import upstox_client
                        cfg = upstox_client.Configuration()
                        cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
                        client = upstox_client.ApiClient(cfg)
                        api = upstox_client.PortfolioApi(client)
                        resp = api.get_short_term_positions(api_version="2.0")
                        broker_symbols = set()
                        if resp and resp.data:
                            for pos in resp.data:
                                if pos.quantity != 0:
                                    broker_symbols.add(pos.instrument_token)
                        # Check each open trade against broker
                        for trade in list(self._open_trades_data):
                            if trade["symbol"] not in broker_symbols:
                                logger.warning(
                                    f"[survivor] MID-SESSION RECONCILE: "
                                    f"{trade['symbol']} not in broker — closing in DB"
                                )
                                trade_logger.close_trade(
                                    trade["id"], trade["entry"],
                                    "MID_SESSION_RECONCILE — not in broker positions"
                                )
                                self._open_trades_data = [
                                    t for t in self._open_trades_data
                                    if t["id"] != trade["id"]
                                ]
                                self._open_trade_ids = [
                                    i for i in self._open_trade_ids
                                    if i != trade["id"]
                                ]
                                _rel_mult = max(1, int(trade.get("quantity", self.cfg.lot_size) // self.cfg.lot_size))
                                risk_manager.release_trade(self.name, trade["order_type"], multiplier=_rel_mult)
                                try:
                                    from core.alerting import alert_reconcile_mismatch
                                    alert_reconcile_mismatch(trade["id"], trade["symbol"])
                                except Exception:
                                    pass
                        # Reverse check: broker positions not in DB
                        db_symbols = {t["symbol"] for t in self._open_trades_data}
                        orphan_broker = broker_symbols - db_symbols
                        if orphan_broker:
                            logger.warning(
                                f"[survivor] MID-SESSION: {len(orphan_broker)} broker "
                                f"position(s) not in DB: {orphan_broker}"
                            )
                            for orphan in orphan_broker:
                                if not any(t["symbol"] == orphan for t in self._open_trades_data):
                                    self._open_trades_data.append({
                                        "id":         f"ORPHAN_{orphan}",
                                        "order_type": "SELL",
                                        "entry":      0.0,
                                        "symbol":     orphan,
                                        "quantity":   65,
                                        "direction":  "CE" if "CE" in orphan else "PE",
                                    })
                                    logger.warning(f"[survivor] Orphan position tracked: {orphan}")
                            alert_reconcile_mismatch("MID_SESSION", str(orphan_broker))
                        logger.info(
                            f"[survivor] Mid-session reconcile: "
                            f"{len(self._open_trades_data)} open trades confirmed"
                        )
                    except Exception as re:
                        logger.debug(f"[survivor] Mid-session reconcile error: {re}")

            except Exception as e:
                logger.error(f"[survivor] Watchdog error: {e}")

    async def _refresh_ltp_loop(self) -> None:
        """Background task: update P&L every 3 seconds using WebSocket LTP cache.
        Also acts as independent auto-stop watchdog."""
        from core.risk_manager import risk_manager
        while not self._stop_flag:
            try:
                if risk_manager.check_auto_stop():
                    logger.warning("[survivor] WATCHDOG: EOD auto-stop 3:05 PM")
                    self._signal("EOD auto-stop 3:05 PM [WATCHDOG]")
                    await self._close_all_positions(reason="EOD")
                    await self.stop(reason="AUTO_STOP")
                    return
                if risk_manager.check_max_daily_loss():
                    logger.warning("[survivor] WATCHDOG: Max daily loss hit")
                    self._signal("Max daily loss [WATCHDOG]")
                    await self._close_all_positions(reason="EOD")
                    await self.stop(reason="MAX_DAILY_LOSS")
                    return
                for trade in list(self._open_trades_data):
                    symbol = trade["symbol"]
                    ikey = self._ikey_cache.get(symbol, symbol)
                    # Check both symbol and ikey in cache
                    cached = self._ltp_cache.get(ikey, self._ltp_cache.get(symbol, 0.0))
                    # Always refresh via REST every 10s (WS may not deliver option ticks)
                    now_ts = __import__('time').time()
                    last_fetch_key = f"_last_rest_fetch_{symbol}"
                    last_fetch = getattr(self, last_fetch_key, 0)
                    if now_ts - last_fetch > 10:
                        try:
                            ltp = await self.broker.get_ltp(ikey)
                            if ltp > 0:
                                self._ltp_cache[ikey] = ltp
                                self._ltp_cache[symbol] = ltp
                                logger.info(f"[survivor] REST LTP: {ikey} = {ltp}")
                            setattr(self, last_fetch_key, now_ts)
                        except Exception as fe:
                            logger.debug(f"[survivor] REST fallback failed: {fe}")

                # Independent periodic SL/TP enforcement -- fires even if no
                # ticks are arriving. Acts like a broker-side GTT would: a
                # check enforced on a timer, independent of the live tick
                # stream. _monitor_open_trades has its own internal 1s
                # throttle, so this is safe to call alongside tick-driven calls.
                await self._monitor_open_trades(self._last_nifty_price)
            except Exception as e:
                logger.debug(f"[survivor] _refresh_ltp_loop error: {e}")
            await asyncio.sleep(3)

    # ── Trade Monitoring (SL + Breakeven Lock + Profit Target) ───────────────

    async def _monitor_open_trades(self, nifty_price: float = 0.0) -> None:
        if not self._open_trades_data:
            return

        import time
        now = time.time()
        if not hasattr(self, '_last_monitor_ts'):
            self._last_monitor_ts = 0
        if now - self._last_monitor_ts < 1.0:
            return
        self._last_monitor_ts = now

        for trade in list(self._open_trades_data):
            try:
                is_paper = (
                    self._is_paper
                    or self.cfg.paper_trade_override
                )
                if is_paper:
                    # Use real LTP from cache (live ticks or REST fallback via
                    # _refresh_ltp_loop) so paper-mode SL/TP actually reacts to
                    # real market movement, instead of a frozen 95%-of-entry value
                    _symbol = trade["symbol"]
                    _ikey = self._ikey_cache.get(_symbol, _symbol)
                    curr_price = self._ltp_cache.get(_ikey, self._ltp_cache.get(_symbol, 0.0))
                    if curr_price <= 0.0:
                        curr_price = trade["entry"]  # safe fallback, no fake decay
                else:
                    # Use WebSocket tick cache instead of API call to avoid 429 errors
                    ikey = self._ikey_cache.get(trade["symbol"], trade["symbol"])
                    curr_price = self._ltp_cache.get(ikey, self._ltp_cache.get(trade["symbol"], 0.0))
                    if curr_price == 0.0:
                        # Fallback to API only if cache empty
                        curr_price = await self.broker.get_ltp(trade["symbol"])

                if curr_price == 0:
                    continue

                # ── Calculate current P&L ─────────────────────────────────
                if trade["order_type"] == "SELL":
                    curr_pnl = (trade["entry"] - curr_price) * trade["quantity"]
                else:
                    curr_pnl = (curr_price - trade["entry"]) * trade["quantity"]

                # ── Breakeven Lock ────────────────────────────────────────
                # Once profit reaches ₹400, lock SL at entry price (breakeven).
                # This means the trade can never turn into a loss after this point.
                if curr_pnl >= 400.0 and not trade.get("_be_locked"):
                    trade["_be_locked"] = True
                    trade["_sl_floor"] = trade["entry"]
                    self._signal(
                        f"🔒 BREAKEVEN LOCKED | {trade['symbol']} | "
                        f"P&L: ₹{curr_pnl:.0f} — SL moved to entry ₹{trade['entry']:.2f}"
                    )
                    alert_breakeven_locked(trade['symbol'], curr_pnl)

                # ── Check breakeven SL (if locked) ───────────────────────
                if trade.get("_be_locked"):
                    sl_floor = trade["_sl_floor"]
                    # For SELL: option price rising above entry = loss territory
                    breached = (
                        curr_price >= sl_floor
                        if trade["order_type"] == "SELL"
                        else curr_price <= sl_floor
                    )
                    if breached:
                        self._signal(
                            f"🔒 BREAKEVEN SL HIT | {trade['symbol']} | "
                            f"Entry: {trade['entry']:.2f} | "
                            f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                        )
                        await self._close_trade(trade, "SL_HIT", curr_price)
                        continue  # trade closed, move to next

                # ── Normal stop loss (no breakeven lock yet) ──────────────
                hedge_entry_price   = trade.get("hedge_entry", 0.0) or 0.0
                hedge_current_price = 0.0
                if trade.get("hedge_symbol"):
                    _h_ikey = self._ikey_cache.get(trade["hedge_symbol"], trade["hedge_symbol"])
                    hedge_current_price = self._ltp_cache.get(
                        _h_ikey, self._ltp_cache.get(trade["hedge_symbol"], 0.0)
                    )
                    hedge_pnl_preview = (hedge_current_price - hedge_entry_price) * trade.get("hedge_quantity", 0)
                    logger.info(
                        f"[survivor] Hedge LTP: {trade['hedge_symbol']} = {hedge_current_price} | "
                        f"Hedge P&L: ₹{hedge_pnl_preview:.2f}"
                    )
                if not trade.get("_be_locked") and risk_manager.check_trade_stop_loss(
                    trade["entry"], curr_price, trade["quantity"], trade["order_type"],
                    hedge_entry_price=hedge_entry_price,
                    hedge_current_price=hedge_current_price,
                    hedge_quantity=trade.get("hedge_quantity", 0),
                ):
                    self._signal(
                        f"🛑 STOP LOSS hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "SL_HIT", curr_price)
                    continue  # trade closed, move to next

                # ── Profit target ─────────────────────────────────────────
                _fixed_tp = 800.0 if "BANKNIFTY" in self.cfg.instrument_name.upper() else 0.0
                if risk_manager.check_trailing_profit(
                    trade["entry"], curr_price, trade["order_type"], trade["quantity"],
                    fixed_target=_fixed_tp, trade_id=trade.get("id", ""),
                    hedge_entry_price=hedge_entry_price,
                    hedge_current_price=hedge_current_price,
                    hedge_quantity=trade.get("hedge_quantity", 0),
                    hedge_entry_cost=trade.get("hedge_entry_cost", 0.0),
                ):
                    self._signal(
                        f"✅ PROFIT TARGET hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "TP_HIT", curr_price)

            except Exception as e:
                logger.error(f"[survivor] Monitor error for {trade['symbol']}: {e}")

    # ── Trade Exit ────────────────────────────────────────────────────────────

    async def _close_trade(
        self, trade: dict, reason: str, current_price: float = 0.0
    ) -> None:
        if trade not in self._open_trades_data:
            return
        # Exit precedence gate — prevent double-close from SL + GTT + EOD firing simultaneously
        trade_id = trade.get("id", "")
        if trade_id in self._closing_trades:
            logger.warning(
                f"[survivor] DOUBLE-CLOSE BLOCKED | {trade.get('symbol')} | "
                f"reason={reason} | already being closed"
            )
            return
        self._closing_trades.add(trade_id)

        exit_order_type = "BUY" if trade["order_type"] == "SELL" else "SELL"

        _close_paper = (
            self._is_paper
            or self.cfg.paper_trade_override
        )
        if _close_paper:
            if current_price == 0.0:
                current_price = trade["entry"] * 0.95
            exit_price = round(current_price * 1.02, 1)
            self._signal(
                f"[PAPER] {exit_order_type} {trade['quantity']} "
                f"{trade['symbol']} @ {exit_price} (simulated)"
            )
            order_id = "PAPER_EXIT"
        else:
            if current_price == 0.0:
                current_price = await self.broker.get_ltp(trade["symbol"])
            if current_price == 0.0:
                logger.error(f"[survivor] get_ltp returned 0 for {trade['symbol']} — aborting close to prevent wrong P&L")
                self._signal(f"⚠ EXIT ABORTED: could not get LTP for {trade['symbol']}")
                self._closing_trades.discard(trade_id)
                return
            if exit_order_type == "BUY":
                exit_price = round(current_price * 1.02, 1)
            else:
                exit_price = round(current_price * 0.98, 1)
            try:
                # ── Exit size validation: confirm broker position before closing ──
                _exit_qty = trade["quantity"]
                try:
                    _positions = await self.broker.get_positions()
                    _broker_qty = 0
                    for _p in _positions:
                        if _p.symbol == trade["symbol"]:
                            _broker_qty = abs(_p.quantity)
                            break
                    if _broker_qty == 0:
                        logger.warning(f"[survivor] EXIT SKIPPED: broker shows 0 position for {trade['symbol']} — may already be closed")
                        self._signal(f"⚠ Exit skipped — broker confirms no open position for {trade['symbol']}")
                        self._closing_trades.discard(trade_id)
                        return
                    if _exit_qty > _broker_qty:
                        logger.warning(f"[survivor] EXIT SIZE MISMATCH: local={_exit_qty} broker={_broker_qty} — scaling down")
                        _exit_qty = _broker_qty
                except Exception as _ve:
                    logger.warning(f"[survivor] Exit validation skipped: {_ve}")
                resp = await self.broker.place_order(Order(
                    symbol=trade["symbol"],
                    exchange="NFO",
                    order_type=exit_order_type,
                    quantity=_exit_qty,
                    product="I",
                    price=exit_price,
                    tag=f"EXIT_{trade['id'][:8]}_{int(time.time()*1000) % 100000}",
                ))
                if resp.status == "REJECTED":
                    self._signal(
                        f"Exit order REJECTED for {trade['symbol']}: {resp.message}"
                    )
                    self._closing_trades.discard(trade_id)
                    return
                order_id = resp.order_id
            except Exception as e:
                logger.error(f"[survivor] _close_trade failed for {trade['symbol']}: {e}")
                self._closing_trades.discard(trade_id)
                return

        # Close hedge leg, if one exists
        hedge_pnl = 0.0
        hedge_exit_cost = 0.0
        if trade.get("hedge_symbol"):
            try:
                if _close_paper:
                    hedge_exit = round(trade["hedge_entry"] * 0.5, 1)  # simple paper decay model
                else:
                    hedge_exit = await self.broker.get_ltp(trade["hedge_symbol"])
                    if hedge_exit > 0.0:
                        await self.broker.place_order(Order(
                            symbol=trade["hedge_symbol"],
                            exchange="NFO",
                            order_type="SELL",
                            quantity=trade["hedge_quantity"],
                            product="I",
                            price=round(hedge_exit * 0.98, 1),
                            tag=f"HEDGE_EXIT_{trade['id'][:8]}",
                        ))
                hedge_pnl = (hedge_exit - trade["hedge_entry"]) * trade["hedge_quantity"]
                from core.transaction_costs import calculate_order_cost
                hedge_exit_cost = calculate_order_cost(hedge_exit, trade["hedge_quantity"], "SELL")
                if trade.get("hedge_trade_id"):
                    trade_logger.close_trade(trade["hedge_trade_id"], hedge_exit, f"HEDGE_EXIT_{reason}")
                self._signal(
                    f"\U0001F6E1 Hedge closed {trade['hedge_symbol']} @ \u20b9{hedge_exit:.2f} | "
                    f"Hedge P&L: \u20b9{hedge_pnl:.2f}"
                )
            except Exception as he:
                logger.error(f"[survivor] Hedge close failed for {trade.get('hedge_symbol')}: {he}")
            finally:
                # Release hedge-leg capital reserved at open, regardless of whether
                # the hedge close itself succeeded -- mirrors the register_capital
                # call added at hedge-open; must always pair (see capital-tracking
                # investigation, 31-Jul)
                _hedge_rel_lots = max(1, int(trade.get("hedge_quantity", self.cfg.lot_size) // self.cfg.lot_size))
                risk_manager.release_capital(self.name, "BUY", multiplier=_hedge_rel_lots)

        # Calculate P&L (short leg + hedge leg combined, NET of real transaction costs).
        # Costs are recomputed fresh here rather than read from trade_data, because
        # entry_cost/hedge_entry_cost only ever lived in the in-memory dict -- any
        # trade recovered via _recover_open_positions() after a PM2 restart would
        # silently lose them. entry/quantity/hedge_entry/hedge_quantity DO survive
        # restarts correctly (confirmed working since the June 22 recovery fix), so
        # costs are derived from those instead.
        from core.transaction_costs import calculate_order_cost
        # Branch on the trade's actual order_type -- this function closes both
        # the normal short leg (order_type=SELL) AND independently-tracked
        # long/hedge legs (order_type=BUY) reaching their own TP/SL/EOD exit.
        # Using SELL-side math unconditionally flips the P&L sign for BUY trades.
        if trade["order_type"] == "SELL":
            entry_cost = calculate_order_cost(trade["entry"], trade["quantity"], "SELL")
            short_exit_cost = calculate_order_cost(exit_price, trade["quantity"], "BUY")
            gross_pnl = (trade["entry"] - exit_price) * trade["quantity"] + hedge_pnl
        else:
            entry_cost = calculate_order_cost(trade["entry"], trade["quantity"], "BUY")
            short_exit_cost = calculate_order_cost(exit_price, trade["quantity"], "SELL")
            gross_pnl = (exit_price - trade["entry"]) * trade["quantity"] + hedge_pnl
        hedge_entry_cost = (
            calculate_order_cost(trade["hedge_entry"], trade["hedge_quantity"], "BUY")
            if trade.get("hedge_symbol") else 0.0
        )
        total_costs = entry_cost + hedge_entry_cost + short_exit_cost + hedge_exit_cost
        pnl = gross_pnl - total_costs
        self._realised_pnl += pnl
        self._signal(
            f"\U0001F4B0 Costs: \u20b9{total_costs:.2f} | Gross P&L: \u20b9{gross_pnl:.2f} | Net P&L: \u20b9{pnl:.2f}"
        )

        # Remove from open trades
        self._open_trades_data = [
            t for t in self._open_trades_data if t["id"] != trade["id"]
        ]
        if trade["id"] in self._open_trade_ids:
            self._open_trade_ids.remove(trade["id"])
        # Clear closing flag
        self._closing_trades.discard(trade["id"])

        self._closed_trades += 1

        # Release capital back to risk manager
        _rel_mult = max(1, int(trade.get("quantity", self.cfg.lot_size) // self.cfg.lot_size))
        risk_manager.release_trade(self.name, trade["order_type"], multiplier=_rel_mult)

        # MFE (peak P&L) capture for future trailing-threshold tuning -- read-only,
        # never affects trade P&L or any exit decision already taken above.
        _peak_pnl = risk_manager.get_mfe(trade["id"])
        _trough_pnl = risk_manager.get_mae(trade["id"])
        risk_manager.clear_watermark(trade["id"])

        trade_logger.close_trade(
            trade["id"], exit_price, reason,
            net_pnl=pnl, gross_pnl=gross_pnl, total_costs=total_costs,
            peak_pnl=_peak_pnl,
            trough_pnl=_trough_pnl,
        )
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
        self._signal(
            f"CLOSED {trade['symbol']} | Reason: {reason} | "
            f"P&L: ₹{pnl:.2f} | Order: {order_id}"
        )
        alert_trade_closed(trade['symbol'], trade['entry'], exit_price, trade['quantity'], pnl, reason)

    async def close_all_positions(self) -> None:
        """Public wrapper for dashboard/killswitch use — avoids cross-layer private method calls."""
        await self._close_all_positions(reason="MANUAL")

    async def _close_all_positions(self, reason: str = "EOD") -> None:
        if not self._open_trades_data:
            return
        self._signal(f"Closing all {len(self._open_trades_data)} open trade(s)...")
        for trade in list(self._open_trades_data):
            _ikey = self._ikey_cache.get(trade["symbol"], trade["symbol"])
            _ltp = self._ltp_cache.get(_ikey, self._ltp_cache.get(trade["symbol"], 0.0))
            await self._close_trade(trade, reason, _ltp)
        self._unrealised_pnl = 0.0
        self._update_pnl(self._realised_pnl, 0.0)
        # EOD summary alert — only send for actual EOD, not regime change or manual stops
        if reason != "EOD":
            return
        try:
            from core.alerting import alert_eod_close
            import sqlite3, pytz
            from datetime import datetime
            today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
            with sqlite3.connect(trade_logger.db_path) as _conn:
                _conn.row_factory = sqlite3.Row
                _row = _conn.execute(
                    "SELECT SUM(realised_pnl) as total, COUNT(*) as cnt FROM trades "
                    "WHERE status='CLOSED' AND DATE(exit_time)=? AND notes != 'DUPLICATE_CLEANUP'",
                    (today,)
                ).fetchone()
            total_pnl = _row["total"] or 0.0
            trade_cnt = _row["cnt"] or 0
            alert_eod_close(total_pnl, trade_cnt)
            logger.info(f"[survivor] EOD alert sent — P&L: ₹{total_pnl:.2f} | Trades: {trade_cnt}")
        except Exception as _e:
            logger.warning(f"[survivor] EOD alert failed: {_e}")

    # ── P&L Calculation ───────────────────────────────────────────────────────

    def _get_hedge_unrealised_pnl(self, trade: dict) -> float:
        """Returns the unrealized P&L of a trade's hedge leg (long), or 0.0 if none."""
        hedge_symbol = trade.get("hedge_symbol")
        if not hedge_symbol:
            return 0.0
        hedge_ikey = self._ikey_cache.get(hedge_symbol, hedge_symbol)
        hedge_curr = self._ltp_cache.get(hedge_ikey, self._ltp_cache.get(hedge_symbol, 0.0))
        if hedge_curr <= 0.0:
            return 0.0
        return (hedge_curr - trade["hedge_entry"]) * trade["hedge_quantity"]

    def _calculate_pnl(self, nifty_price: float = 0.0) -> None:
        unrealised = 0.0
        for trade in self._open_trades_data:
            entry  = trade["entry"]
            qty    = trade["quantity"]
            symbol = trade["symbol"]
            try:
                ikey = self._ikey_cache.get(symbol, symbol)
                # Check both ikey and symbol in cache
                curr = self._ltp_cache.get(ikey, self._ltp_cache.get(symbol, 0.0))
                # If still 0, do NOT use entry as fallback — use 0 so dashboard shows stale
                # The REST fallback in _refresh_ltp_loop will populate cache every 30s
            except Exception:
                curr = 0.0
            if curr == 0.0:
                curr = 0.0  # leave as 0 — REST fallback will fix within 30s
            if trade["order_type"] == "SELL":
                unrealised += (entry - curr) * qty if curr > 0 else 0.0
            else:
                unrealised += (curr - entry) * qty if curr > 0 else 0.0
            unrealised += self._get_hedge_unrealised_pnl(trade)
        self._unrealised_pnl = round(unrealised, 2)
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
        # Update live P&L registry for dashboard
        try:
            from dashboard.api import pnl_registry, ltp_registry, trailing_registry
            for trade in self._open_trades_data:
                entry = trade["entry"]
                qty   = trade["quantity"]
                ikey  = self._ikey_cache.get(trade["symbol"], trade["symbol"])
                curr  = self._ltp_cache.get(ikey, self._ltp_cache.get(trade["symbol"], 0.0))
                if curr > 0:
                    pnl = (entry - curr) * qty if trade["order_type"] == "SELL" else (curr - entry) * qty
                    pnl += self._get_hedge_unrealised_pnl(trade)
                    pnl_registry[trade["id"]] = round(pnl, 2)
                    ltp_registry[trade["id"]] = curr
                    # Same hedge-price lookup _get_hedge_unrealised_pnl uses,
                    # so cost/trailing numbers never diverge from live P&L.
                    hedge_symbol = trade.get("hedge_symbol")
                    hedge_curr = 0.0
                    if hedge_symbol:
                        hedge_ikey = self._ikey_cache.get(hedge_symbol, hedge_symbol)
                        hedge_curr = self._ltp_cache.get(hedge_ikey, self._ltp_cache.get(hedge_symbol, 0.0))
                    trailing_registry[trade["id"]] = risk_manager.get_trailing_status(
                        entry, curr, trade["order_type"], qty, trade_id=trade["id"],
                        hedge_entry_price=trade.get("hedge_entry", 0.0) or 0.0,
                        hedge_current_price=hedge_curr,
                        hedge_quantity=trade.get("hedge_quantity", 0),
                        hedge_entry_cost=trade.get("hedge_entry_cost", 0.0),
                    )
                # If curr == 0, keep existing registry value (don't overwrite with wrong 0)
        except Exception:
            pass

    async def _check_time_based_trigger(
        self,
        nifty_price: float,
        pe_symbol_gap: float,
        ce_symbol_gap: float,
    ) -> None:
        """
        Time-based trigger: fires once per side per session in 9:45-11:30 AM window.
        Designed to catch flat range days where movement trigger never fires.
        Safety gates: regime=range, PCR confirms direction, no open trade same side,
        not already fired today, within time window only.
        """
        import pytz
        from datetime import datetime as dt
        now = dt.now(pytz.timezone("Asia/Kolkata"))

        # Reset flags at start of each new day
        if now.day != self._last_time_trigger_day:
            self._time_based_pe_fired = False
            self._time_based_ce_fired = False
            self._last_time_trigger_day = now.day

        # Only fire in 9:45 AM – 11:30 AM window
        from datetime import time as dtime
        now_time = now.time()
        if not (dtime(9, 45) <= now_time <= dtime(11, 30)):
            return

        # Only in range regime
        from core.market_context import market_context
        if market_context.regime not in ("range", "reversal_watch"):
            return

        # Get current PCR for direction filter
        pcr = getattr(market_context, "_pcr", 1.0)

        # Count open trades by direction
        open_pe = sum(1 for tr in self._open_trades_data if tr.get("direction") == "PE")
        open_ce = sum(1 for tr in self._open_trades_data if tr.get("direction") == "CE")

        # PE time trigger: PCR > 1.2 means more puts = market leaning bullish = safe to sell PE
        if (
            not self._time_based_pe_fired
            and open_pe == 0
            and pcr >= 1.2
            and len(self._open_trades_data) < 2
        ):
            self._signal(
                f"⏰ TIME TRIGGER: PE sell | PCR={pcr:.2f} | "
                f"Nifty={nifty_price:.2f} | window=9:45-11:30"
            )
            _adj_qty = self._get_vix_adjusted_quantity(self.cfg.pe_quantity)
            if _adj_qty == 0:
                self._signal(f"⚠ VIX HIGH — TIME TRIGGER PE skipped (qty=0 risk gate)")
            else:
                await self._sell_option(
                    direction="PE",
                    nifty_price=nifty_price,
                    gap=pe_symbol_gap,
                    quantity=_adj_qty,
                )
            self._time_based_pe_fired = True
            self._update_position(Direction.SHORT)
            return  # one trigger per tick max

        # CE time trigger: PCR < 0.8 means more calls = market leaning bearish = safe to sell CE
        if (
            not self._time_based_ce_fired
            and open_ce == 0
            and pcr <= 0.8
            and len(self._open_trades_data) < 2
        ):
            self._signal(
                f"⏰ TIME TRIGGER: CE sell | PCR={pcr:.2f} | "
                f"Nifty={nifty_price:.2f} | window=9:45-11:30"
            )
            _adj_qty = self._get_vix_adjusted_quantity(self.cfg.ce_quantity)
            if _adj_qty == 0:
                self._signal(f"⚠ VIX HIGH — TIME TRIGGER CE skipped (qty=0 risk gate)")
            else:
                await self._sell_option(
                    direction="CE",
                    nifty_price=nifty_price,
                    gap=ce_symbol_gap,
                    quantity=_adj_qty,
                )
            self._time_based_ce_fired = True
            self._update_position(Direction.SHORT)

    def get_config(self) -> dict:
        return vars(self.cfg)