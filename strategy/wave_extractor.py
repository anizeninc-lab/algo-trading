from core.strategy_filter import strategy_filter
# strategy/wave_extractor.py
import asyncio
import json
import logging
import os
import time
import uuid
import pytz as _pytz
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.event_bus import EventType
from core.risk_manager import risk_manager
from dashboard.api import pnl_registry, ltp_registry, trailing_registry
from core.state_store import Direction, state_store
from core.trade_log import trade_logger
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

STALE_POSITION_MAX_AGE_HOURS = 6  # recovered positions older than this are force-closed, not re-adopted
EXIT_SLIPPAGE_PCT = 0.01  # 1% adverse slippage on exit fills -- was a hardcoded 2% (4x entry slippage), unified 30-Jul, see enhancement #5


@dataclass
class WaveConfig:
    option_symbol:        str   = ""
    readable_symbol:      str   = ""  # human-readable descriptor for backtesting lookups only; never used for orders/LTP
    sell_gap:             float = 15.0
    buy_gap:              float = 15.0
    quantity:             int   = 65
    cool_off_time:        float = 5.0
    order_timeout:        float = 120.0  # seconds before unfilled bracket is cancelled
    multiplier_scale:     list  = field(default_factory=lambda: [1.0, 1.3, 1.7, 2.2, 2.8])
    max_net_position:     int   = 2
    delta_limit:          float = 200.0
    nifty_instrument_key: str   = "NSE_INDEX|Nifty 50"
    # ── Adaptive gap sizing ──────────────────────────────────────────────
    # Instead of a flat sell_gap/buy_gap, size the bracket off recently
    # realized option price movement so it stays fillable as premium
    # levels and volatility regimes change through the day.
    adaptive_gap:          bool  = True
    gap_lookback_seconds:  float = 120.0   # window used to measure realized move
    gap_multiplier:        float = 1.5     # safety margin above realized move
    gap_sample_count:      int   = 8       # how many past measurements to average
    min_gap:               float = 2.0     # floor, avoids near-instant fills
    max_gap:               float = 20.0    # ceiling, avoids reverting to old problem


class WaveExtractor(BaseStrategy):

    def __init__(self, broker: AbstractBrokerGateway, config: WaveConfig):
        super().__init__(name="wave_extractor", broker=broker, config=vars(config))
        self.cfg               = config
        self._loop             = None
        self._net_position      = 0
        self._last_block_reason = ""    # pre-trade gate: log only on state change
        self._sell_order_id     = ""
        self._buy_order_id     = ""
        self._sell_price       = 0.0
        self._buy_price        = 0.0
        self._bracket_active   = False
        self._in_cool_off      = False
        self._bracket_placed_at: float = 0.0  # epoch seconds when bracket was placed
        # Resolved once at init — avoids repeated os.getenv calls in signal logic
        self._is_paper = os.getenv("PAPER_TRADE", "false").lower() == "true"
        self._current_price    = 0.0
        self._price_history: deque = deque()   # (epoch_ts, price) samples for adaptive gap
        self._recent_moves: list   = []          # rolling realized-move samples
        _seeded = state_store.get_strategy(self.name)
        self._realised_pnl     = _seeded.realised_pnl if _seeded else 0.0
        self._unrealised_pnl   = 0.0
        self._open_trade_ids   = []
        self._open_trades_data = []
        self._closed_trades    = 0
        self._sync_task        = None
        self._closing_lock     = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        self._loop = asyncio.get_running_loop()

        if not self.cfg.option_symbol:
            raise RuntimeError("option_symbol must be set in WaveConfig")

        self._current_price = await self.broker.get_ltp(self.cfg.option_symbol)

        if self._current_price == 0.0:
            logger.warning(f"[wave_extractor] Could not fetch LTP for {self.cfg.option_symbol}")

        self.broker.subscribe_ticks(
            symbols=[self.cfg.option_symbol],
            callback=self._on_tick_sync
        )

        self.broker.on_order_update(self._on_order_update)

        self._sync_task = asyncio.create_task(self._position_sync_loop())
        asyncio.create_task(self._auto_stop_watchdog())
        logger.info("[wave_extractor] Auto-stop watchdog started")

        # Reload open trades from DB on startup (handles restarts)
        today = __import__('datetime').date.today().isoformat()
        open_trades = [t for t in trade_logger.get_trades(strategy=self.name, status="OPEN") if t.get('entry_time', '')[:10] == today]
        for t in open_trades:
            if t.get("symbol") == self.cfg.option_symbol:
                self._open_trades_data.append({
                    "id":          t.get("id", ""),
                    "order_id":    t.get("broker_order_id", "RESTORED"),
                    "order_type":  t.get("order_type"),
                    "entry_price": t.get("entry_price"),
                    "quantity":    t.get("quantity"),
                    "symbol":      t.get("symbol"),
                })
                self._net_position += 1 if t.get("order_type") == "BUY" else -1
        if self._open_trades_data:
            self._signal(f"Restored {len(self._open_trades_data)} open trade(s) from DB")
            pos = "LONG" if self._net_position > 0 else "SHORT" if self._net_position < 0 else "FLAT"
            self._update_position(pos)
            self._update_pnl(self._realised_pnl, self._unrealised_pnl)

        self._signal(
            f"Started | Symbol: {self.cfg.option_symbol} | "
            f"Price: {self._current_price:.2f}"
        )

    async def on_stop(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        await self._cancel_active_bracket()
        self.broker.unsubscribe_ticks([self.cfg.option_symbol])

    # ── Background Position Sync ──────────────────────────────────────────────

    async def _auto_stop_watchdog(self) -> None:
        logger.info("[wave_extractor] Auto-stop watchdog started")
        while not self._stop_flag:
            await asyncio.sleep(30)
            try:
                from core.risk_manager import risk_manager
                if risk_manager.check_auto_stop():
                    logger.warning("[wave_extractor] WATCHDOG: Auto-stop triggered")
                    self._signal("WATCHDOG: Auto-stop 3:10 PM — closing all positions")
                    await self._close_all_positions()
                    await self.stop(reason="AUTO_STOP_WATCHDOG")
                    return
            except Exception as e:
                logger.error(f"[wave_extractor] Watchdog error: {e}")

    async def _position_sync_loop(self) -> None:
        await asyncio.sleep(15)
        while not self._stop_flag:
            try:
                await self._sync_positions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[wave_extractor] Position sync error: {e}")
            await asyncio.sleep(30)

    async def _sync_positions(self) -> None:
        if self._is_paper:
            return

        positions = await self.broker.get_positions()

        actual_qty = 0
        for pos in positions:
            if pos.symbol == self.cfg.option_symbol:
                actual_qty = pos.quantity

        lot_size   = self.cfg.quantity or 65  # 65 = Nifty lot size; fallback if cfg.quantity unset
        actual_net = actual_qty // lot_size if lot_size else actual_qty

        if actual_net != self._net_position:
            logger.warning(
                f"[wave_extractor] POSITION MISMATCH — "
                f"Bot: {self._net_position} | Upstox: {actual_net}. Resyncing..."
            )
            self._net_position = actual_net

            if actual_net == 0 and self._bracket_active:
                self._bracket_active   = False
                self._sell_order_id    = ""
                self._buy_order_id     = ""
                self._open_trades_data = []
                logger.info("[wave_extractor] Bracket reset after external position close.")
                self._signal("Position closed externally — resynced, ready to re-enter")
                asyncio.create_task(self._cool_off_and_rebracket())
            else:
                self._in_cool_off = True
                self._signal(f"Position resynced to {actual_net} from Upstox — cool-off started")
                asyncio.create_task(self._cool_off_and_rebracket())

    # ── Tick Handling ─────────────────────────────────────────────────────────

    def _on_tick_sync(self, tick: Tick) -> None:
        if self._stop_flag:
            return
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._loop)
        else:
            logger.error("[wave_extractor] Event loop not running")

    async def on_tick(self, tick: Tick) -> None:
        try:
            if self._stop_flag or not self.is_market_open():
                return

            # ── Always update price + monitor any already-open trade FIRST ──────
            # (must run regardless of new-entry gating below — an open trade's
            # stop-loss / trailing-profit checks must never be skipped just
            # because NEW entries are currently blocked, e.g. capital-limit hit)
            self._current_price = tick.mid_price
            self._record_price_sample(tick.mid_price)

            if "INDEX" in tick.symbol:
                state_store.update_nifty_price(tick.last_price)
            else:
                state_store.update_option_price(self.cfg.option_symbol, tick.last_price)

            await self._monitor_open_trades()
            self._calculate_pnl()

            # ── Paper Trade Fill Simulator ──────────────────────────────────
            if self._is_paper:
                filled = await self._handle_paper_fill(tick.mid_price)
                if filled:
                    return
            # ── End Paper Trade Fill Simulator ─────────────────────────────

            # ── Global pre-trade gate (#24) — gates NEW entries only ────────────
            blocked, reason = risk_manager.is_trading_blocked()
            if blocked:
                if reason != self._last_block_reason:
                    self._last_block_reason = reason
                    logger.info(f"[wave_extractor] Trading blocked: {reason}")
                    if "daily loss" in reason.lower():
                        await self._close_all_positions()
                        await self.stop(reason="MAX_DAILY_LOSS")
                    elif "circuit breaker" in reason.lower() or "halted" in reason.lower():
                        await self.stop(reason="API_CIRCUIT_BREAKER")
                    elif "auto-stop" in reason.lower():
                        await self._close_all_positions()
                        await self.stop(reason="AUTO_STOP")
                return
            else:
                self._last_block_reason = ""

            can_trade, reason = risk_manager.can_trade(self.name)
            if can_trade:
                sf_ok, sf_reason = strategy_filter.can_trade("wave_extractor")
                if not sf_ok:
                    can_trade = False
                    reason = f"[context] {sf_reason}"

            if (
                not self._bracket_active
                and not self._in_cool_off
                and self._net_position == 0
                and not self._open_trades_data
                and can_trade
            ):
                await self._place_duo_bracket()

            elif not can_trade and not self._bracket_active:
                logger.debug(f"[{self.name}] Trade blocked: {reason}")

        except Exception as e:
            logger.exception(f"[wave_extractor] ERROR in on_tick: {e}")

    # ── Order Update Handler ──────────────────────────────────────────────────

    def _on_order_update(self, update: dict) -> None:
        try:
            if self._stop_flag:
                return
            if self._is_paper:
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_order_update(update), self._loop
            )
        except Exception as e:
            logger.error(f"[wave_extractor] Order update error: {e}")

    async def _handle_order_update(self, update: dict) -> None:
        if self._stop_flag:
            return
        order_id   = update.get("order_id", "")
        status     = update.get("status", "")
        if status != "COMPLETE":
            return
        price      = float(update.get("average_price", 0))
        filled_qty = int(update.get("filled_qty", 0) or self.cfg.quantity)
        is_partial = update.get("is_partial", False)
        if is_partial:
            logger.warning(
                f"[wave_extractor] PARTIAL FILL: order={order_id} "
                f"filled={filled_qty} requested={self.cfg.quantity} @ {price:.2f} "
                "— logging actual filled qty, adjusting risk accordingly"
            )
        if order_id == self._sell_order_id:
            self._net_position  -= 1
            self._bracket_active = False
            self._signal(f"SELL filled @ {price:.2f} qty={filled_qty}" + (" [PARTIAL]" if is_partial else ""))
            risk_manager.register_trade(self.name, "SELL")
            _entry_context = json.dumps({
                "net_position_before": self._net_position,
                "sell_price":          self._sell_price,
                "buy_price":           self._buy_price,
                "adaptive_gap":        self.cfg.adaptive_gap,
            }, default=str)
            _live_trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=self.cfg.option_symbol,
                readable_symbol=self.cfg.readable_symbol,
                order_type="SELL",
                quantity=filled_qty,
                entry_price=price,
                broker_order_id=order_id,
                client_order_id=f"WAVE_LIVE_SELL_{uuid.uuid4().hex[:8]}",
                paper_trade=self._is_paper,
                entry_context=_entry_context,
            )
            self._open_trades_data.append({
                "id":          _live_trade_id,
                "order_id":    order_id,
                "order_type":  "SELL",
                "entry_price": price,
                "quantity":    filled_qty,
                "symbol":      self.cfg.option_symbol,
            })
            if self._buy_order_id:
                await self.broker.cancel_order(self._buy_order_id)
                self._signal(f"Opposing BUY bracket cancelled: {self._buy_order_id}")
                self._buy_order_id = ""
        elif order_id == self._buy_order_id:
            self._net_position  += 1
            self._bracket_active = False
            self._signal(f"BUY filled @ {price:.2f} qty={filled_qty}" + (" [PARTIAL]" if is_partial else ""))
            risk_manager.register_trade(self.name, "BUY")
            _entry_context = json.dumps({
                "net_position_before": self._net_position,
                "sell_price":          self._sell_price,
                "buy_price":           self._buy_price,
                "adaptive_gap":        self.cfg.adaptive_gap,
            }, default=str)
            _live_trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=self.cfg.option_symbol,
                readable_symbol=self.cfg.readable_symbol,
                order_type="BUY",
                quantity=filled_qty,
                entry_price=price,
                broker_order_id=order_id,
                client_order_id=f"WAVE_LIVE_BUY_{uuid.uuid4().hex[:8]}",
                paper_trade=self._is_paper,
                entry_context=_entry_context,
            )
            self._open_trades_data.append({
                "id":          _live_trade_id,
                "order_id":    order_id,
                "order_type":  "BUY",
                "entry_price": price,
                "quantity":    filled_qty,
                "symbol":      self.cfg.option_symbol,
            })
            if self._sell_order_id:
                await self.broker.cancel_order(self._sell_order_id)
                self._signal(f"Opposing SELL bracket cancelled: {self._sell_order_id}")
                self._sell_order_id = ""

    # ── Trade Monitoring (SL + Trailing Profit) ───────────────────────────────

    async def _handle_paper_fill(self, price: float) -> bool:
        """Paper-mode fill simulator. Returns True if a fill was detected and handled.
        Extracted from on_tick so future fill-path fixes have one place to land (#14).
        """
        if not self._bracket_active:
            return False
        _now_tz = datetime.now(_pytz.timezone("Asia/Kolkata"))
        today_prefix = _now_tz.strftime('%Y%m%d')

        if self._sell_order_id.startswith("PAPER_SELL") and self._sell_price > 0 and price >= self._sell_price:
            _slip_sell = round(self._sell_price * 0.995, 1)  # 0.5% adverse slippage — sell slightly lower
            self._signal(f"[PAPER] SELL filled @ {_slip_sell} (limit={self._sell_price}, slip=-0.5%)")
            self._bracket_active = False
            self._net_position  -= 1
            trade = {
                "order_id":    f"PAPER_WAVE_SELL_{today_prefix}",
                "order_type":  "SELL",
                "entry_price": _slip_sell,
                "quantity":    self.cfg.quantity,
                "symbol":      self.cfg.option_symbol,
            }
            self._open_trades_data.append(trade)
            trade["id"] = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=trade["symbol"],
                readable_symbol=self.cfg.readable_symbol,
                order_type=trade["order_type"],
                quantity=trade["quantity"],
                entry_price=trade["entry_price"],
                broker_order_id=trade["order_id"],
                client_order_id=f"WAVE_PAPER_SELL_{uuid.uuid4().hex[:8]}",
                paper_trade=True,
            )
            self._sell_order_id = ""
            self._buy_order_id  = ""
            risk_manager.register_trade(self.name, "SELL")
            self._update_pnl(self._realised_pnl, self._unrealised_pnl)
            self._update_position("SHORT")
            asyncio.create_task(self._cool_off_and_rebracket())
            return True

        if self._buy_order_id.startswith("PAPER_BUY") and self._buy_price > 0 and price <= self._buy_price:
            _slip_buy = round(self._buy_price * 1.005, 1)  # 0.5% adverse slippage — buy slightly higher
            self._signal(f"[PAPER] BUY filled @ {_slip_buy} (limit={self._buy_price}, slip=+0.5%)")
            self._bracket_active = False
            self._net_position  += 1
            trade = {
                "order_id":    f"PAPER_WAVE_BUY_{today_prefix}",
                "order_type":  "BUY",
                "entry_price": _slip_buy,
                "quantity":    self.cfg.quantity,
                "symbol":      self.cfg.option_symbol,
            }
            self._open_trades_data.append(trade)
            trade["id"] = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=trade["symbol"],
                readable_symbol=self.cfg.readable_symbol,
                order_type=trade["order_type"],
                quantity=trade["quantity"],
                entry_price=trade["entry_price"],
                broker_order_id=trade["order_id"],
                client_order_id=f"WAVE_PAPER_BUY_{uuid.uuid4().hex[:8]}",
                paper_trade=True,
            )
            self._buy_order_id  = ""
            self._sell_order_id = ""
            risk_manager.register_trade(self.name, "BUY")
            self._update_pnl(self._realised_pnl, self._unrealised_pnl)
            self._update_position("LONG")
            asyncio.create_task(self._cool_off_and_rebracket())
            return True

        return False

    async def _monitor_open_trades(self) -> None:
        if not self._open_trades_data:
            return

        for trade in list(self._open_trades_data):
            entry = trade["entry_price"]
            otype = trade["order_type"]
            qty   = trade["quantity"]
            price = self._current_price

            risk_manager.record_mfe_mae(
                entry, price, otype, qty, trade_id=trade.get("id", "")
            )
            if risk_manager.check_trade_stop_loss(entry, price, qty, otype):
                _sl_pnl = (entry - price) * qty if otype == "SELL" else (price - entry) * qty
                self._signal(
                    f"STOP LOSS hit | {otype} | Entry: {entry:.2f} | Current: {price:.2f} | "
                    f"P&L: ₹{_sl_pnl:.0f}"
                )
                await self._close_trade(trade, "STOP_LOSS")

            elif risk_manager.check_trailing_profit(entry, price, otype, qty, trade_id=trade.get("id", "")):
                _tp_pnl = (entry - price) * qty if otype == "SELL" else (price - entry) * qty
                self._signal(
                    f"TRAILING PROFIT hit | {otype} | Entry: {entry:.2f} | Current: {price:.2f} | "
                    f"P&L: ₹{_tp_pnl:.0f}"
                )
                await self._close_trade(trade, "TRAILING_PROFIT")

    # ── Order Placement ───────────────────────────────────────────────────────

    def _generate_multiplier_scale(self) -> dict:
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

    def _record_price_sample(self, price: float) -> None:
        """Track recent price ticks so the adaptive gap can measure realized
        movement. Pruned to 2x the lookback window to bound memory."""
        now = time.time()
        self._price_history.append((now, price))
        cutoff = now - (2 * self.cfg.gap_lookback_seconds)
        while self._price_history and self._price_history[0][0] < cutoff:
            self._price_history.popleft()

    def _compute_adaptive_gap(self) -> float:
        """Size the bracket gap off what the option has actually been doing,
        instead of a fixed point value that goes stale as premium/volatility
        changes. Measures the realized move over the last gap_lookback_seconds,
        keeps a short rolling average of these measurements, and applies
        gap_multiplier as a safety margin — clamped to [min_gap, max_gap]."""
        if len(self._price_history) < 2:
            return (self.cfg.sell_gap + self.cfg.buy_gap) / 2  # not enough data yet

        now = time.time()
        target_t = now - self.cfg.gap_lookback_seconds
        past_price = self._price_history[0][1]
        for t, p in self._price_history:
            if t >= target_t:
                past_price = p
                break

        realized_move = abs(self._current_price - past_price)
        self._recent_moves.append(realized_move)
        if len(self._recent_moves) > self.cfg.gap_sample_count:
            self._recent_moves.pop(0)

        avg_move = sum(self._recent_moves) / len(self._recent_moves)
        gap = round(avg_move * self.cfg.gap_multiplier, 1)
        return max(self.cfg.min_gap, min(self.cfg.max_gap, gap))

    def _get_scaled_gaps(self, current_diff_scale: int) -> tuple:
        """Scale sell_gap/buy_gap based on current position imbalance.
        Base gap comes from _compute_adaptive_gap() when adaptive_gap is on
        (sized off recently realized option movement), falling back to the
        flat cfg.sell_gap/buy_gap values otherwise.
        Ported from master WaveStrategy._get_scaled_gaps."""
        base_gap = self._compute_adaptive_gap() if self.cfg.adaptive_gap else None
        buy_base  = base_gap if base_gap is not None else self.cfg.buy_gap
        sell_base = base_gap if base_gap is not None else self.cfg.sell_gap

        scale_map = self._generate_multiplier_scale()
        key = str(current_diff_scale)
        if key not in scale_map:
            mult = (
                [self.cfg.multiplier_scale[-1], 1.0] if current_diff_scale > 0
                else [1.0, self.cfg.multiplier_scale[-1]]
            )
        else:
            mult = scale_map[key]
        scaled_buy_gap  = round(buy_base * mult[0], 1)
        scaled_sell_gap = round(sell_base * mult[1], 1)
        return scaled_buy_gap, scaled_sell_gap

    async def _place_duo_bracket(self) -> None:
        if self._current_price == 0:
            return

        # Core State Mutex Verification Guard
        if any(t.get("symbol") == self.cfg.option_symbol for t in self._open_trades_data):
            logger.warning("[wave_extractor] MUTEX LOCK: Active trade tracking present in core block. Bracket creation halted.")
            return

        self._bracket_active = True
        self._bracket_placed_at = time.time()
        asyncio.create_task(self._order_timeout_watchdog())

        scaled_buy_gap, scaled_sell_gap = self._get_scaled_gaps(self._net_position)
        sell_price = round(self._current_price + scaled_sell_gap, 2)
        buy_price  = round(self._current_price - scaled_buy_gap, 2)

        if buy_price <= 0:
            self._bracket_active = False
            return

        self._sell_price = sell_price
        self._buy_price  = buy_price

        from core.market_context import market_context as _mc
        _regime = getattr(_mc, "regime", "unknown") if _mc else "unknown"
        self._signal(
            f"Bracket placed | spot={self._current_price:.1f} | imbalance={self._net_position} | "
            f"SELL={sell_price} (+{scaled_sell_gap}) | BUY={buy_price} (-{scaled_buy_gap}) | "
            f"regime={_regime}"
        )

        # Deterministic Identity strings matching today's lifecycle window
        _now_tz = datetime.now(_pytz.timezone("Asia/Kolkata"))
        today_prefix = _now_tz.strftime('%Y%m%d')
        deterministic_sell_tag = f"WAVE_SELL_{today_prefix}"
        deterministic_buy_tag  = f"WAVE_BUY_{today_prefix}"

        # ── Place SELL limit order ────────────────────────────────────────────
        try:
            if self._is_paper:
                self._signal(f"[PAPER] SELL {self.cfg.quantity} {self.cfg.option_symbol} @ {sell_price} (simulated)")
                self._sell_order_id = f"PAPER_SELL_{today_prefix}"
            else:
                sell_resp = await self.broker.place_order(Order(
                    symbol=self.cfg.option_symbol,
                    exchange="NFO",
                    order_type="SELL",
                    quantity=self.cfg.quantity,
                    product="I",
                    price=sell_price,
                    tag=deterministic_sell_tag,  # Unique structural token sent to Upstox API
                ))
                self._sell_order_id = sell_resp.order_id
                if sell_resp.status == "REJECTED":
                    self._signal(f"SELL order REJECTED: {sell_resp.message}")
                    self._sell_order_id  = ""
                    self._bracket_active = False
                    asyncio.create_task(self._cool_off_and_rebracket())
                    return
            self._signal(f"SELL order placed: {self._sell_order_id} @ {sell_price}")

        except Exception as e:
            logger.error(f"[wave_extractor] SELL order failed: {e}")
            self._bracket_active = False
            return

        # ── Place BUY limit order ─────────────────────────────────────────────
        try:
            if self._is_paper:
                self._signal(f"[PAPER] BUY {self.cfg.quantity} {self.cfg.option_symbol} @ {buy_price} (simulated)")
                self._buy_order_id = f"PAPER_BUY_{today_prefix}"
            else:
                buy_resp = await self.broker.place_order(Order(
                    symbol=self.cfg.option_symbol,
                    exchange="NFO",
                    order_type="BUY",
                    quantity=self.cfg.quantity,
                    product="I",
                    price=buy_price,
                    tag=deterministic_buy_tag,  # Unique structural token sent to Upstox API
                ))
                self._buy_order_id = buy_resp.order_id
                if buy_resp.status == "REJECTED":
                    self._signal(f"BUY order REJECTED: {buy_resp.message}")
                    if self._sell_order_id and not self._sell_order_id.startswith("PAPER_SELL"):
                        await self.broker.cancel_order(self._sell_order_id)
                        self._signal(f"SELL {self._sell_order_id} cancelled due to BUY rejection")
                    self._sell_order_id  = ""
                    self._buy_order_id   = ""
                    self._bracket_active = False
                    asyncio.create_task(self._cool_off_and_rebracket())
                    return
            self._signal(f"BUY order placed: {self._buy_order_id} @ {buy_price}")

        except Exception as e:
            logger.error(f"[wave_extractor] BUY order failed: {e}")
            if self._sell_order_id and not self._sell_order_id.startswith("PAPER_SELL"):
                await self.broker.cancel_order(self._sell_order_id)
            self._sell_order_id  = ""
            self._buy_order_id   = ""
            self._bracket_active = False

    async def _on_recover_trade(self, row: dict) -> None:
        """Restore an orphaned OPEN trade from the DB into live tracking."""
        symbol = row.get("symbol", "")
        order_type = row.get("order_type", "BUY")

        # ── Staleness check — don't silently re-adopt positions from a prior day/session ──
        entry_time_str = row.get("entry_time")
        if entry_time_str:
            entry_dt = datetime.fromisoformat(entry_time_str)
            age_hours = (datetime.now() - entry_dt).total_seconds() / 3600
            if age_hours > STALE_POSITION_MAX_AGE_HOURS:
                logger.warning(
                    f"[wave_extractor] Recovered position {row.get('id')} is "
                    f"{age_hours:.1f}h old (>{STALE_POSITION_MAX_AGE_HOURS}h) — "
                    f"treating as stale, forcing close instead of re-adopting."
                )
                raise ValueError(f"stale position {row.get('id')} — force close")

        trade = {
            "id":          row.get("id"),
            "order_id":    row.get("broker_order_id", row.get("id")),
            "order_type":  order_type,
            "entry_price": row.get("entry_price"),
            "quantity":    row.get("quantity"),
            "symbol":      symbol,
        }
        self._open_trades_data.append(trade)
        self.cfg.option_symbol = symbol
        if order_type == "BUY":
            self._net_position += 1
            self._update_position("LONG")
        else:
            self._net_position -= 1
            self._update_position("SHORT")
        try:
            risk_manager.register_trade(self.name, order_type)
        except Exception as e:
            logger.error(f"[wave_extractor] risk_manager.register_trade failed during recovery: {e}")
        try:
            self.broker.subscribe_ticks(symbols=[symbol], callback=self._on_tick_sync)
        except Exception as e:
            logger.error(f"[wave_extractor] Could not resubscribe ticks for recovered trade {symbol}: {e}")

    # ── Trade Exit ────────────────────────────────────────────────────────────

    async def _close_trade(self, trade: dict, reason: str) -> None:
        async with self._closing_lock:
            if trade not in self._open_trades_data:
                return
            await self._do_close_trade(trade, reason)

    async def _do_close_trade(self, trade: dict, reason: str) -> None:
        if trade not in self._open_trades_data:
            return
        qty = trade.get("quantity", 65)
        if qty > 65:
            logger.error(f"[wave_extractor] HARDCAP: close qty {qty} exceeds 65 - capping")
            trade["quantity"] = 65
        self._open_trades_data.remove(trade)

        exit_order_type = "BUY" if trade["order_type"] == "SELL" else "SELL"

        if exit_order_type == "BUY":
            exit_price = round(self._current_price * (1 + EXIT_SLIPPAGE_PCT), 1)  # adverse -- buying back higher
        else:
            exit_price = round(self._current_price * (1 - EXIT_SLIPPAGE_PCT), 1)  # adverse -- selling lower

        try:
            _now_tz = datetime.now(_pytz.timezone("Asia/Kolkata"))
            today_prefix = _now_tz.strftime('%Y%m%d')
            deterministic_exit_tag = f"WAVE_EXIT_{trade['order_type']}_{today_prefix}"

            if self._is_paper:
                self._signal(f"[PAPER] EXIT {exit_order_type} {trade['quantity']} {self.cfg.option_symbol} @ {exit_price} (simulated)")
                exit_order_id = f"PAPER_EXIT_{today_prefix}"
            else:
                # ── Exit size validation: confirm broker position before closing ──
                _exit_qty = trade["quantity"]
                try:
                    _positions = await self.broker.get_positions()
                    _broker_qty = 0
                    for _p in _positions:
                        if _p.symbol == self.cfg.option_symbol:
                            _broker_qty = abs(_p.quantity)
                            break
                    if _broker_qty == 0:
                        logger.warning(f"[wave_extractor] EXIT SKIPPED: broker shows 0 position for {self.cfg.option_symbol} — may already be closed")
                        self._signal(f"⚠ Exit skipped — broker confirms no open position for {self.cfg.option_symbol}")
                        trade_logger.close_trade(trade["id"], exit_price, f"{reason}_ALREADY_CLOSED", net_pnl=0.0)
                        return
                    if _exit_qty > _broker_qty:
                        logger.warning(f"[wave_extractor] EXIT SIZE MISMATCH: local={_exit_qty} broker={_broker_qty} — scaling down")
                        _exit_qty = _broker_qty
                except Exception as _ve:
                    logger.warning(f"[wave_extractor] Exit validation skipped: {_ve}")
                resp = await self.broker.place_order(Order(
                    symbol=self.cfg.option_symbol,
                    exchange="NFO",
                    order_type=exit_order_type,
                    quantity=_exit_qty,
                    product="I",
                    price=exit_price,
                    tag=deterministic_exit_tag,
                ))
                exit_order_id = resp.order_id

            entry = trade["entry_price"]
            qty   = trade["quantity"]
            from core.transaction_costs import calculate_order_cost
            entry_side = trade["order_type"]
            exit_side  = exit_order_type
            entry_cost = calculate_order_cost(entry, qty, entry_side)
            exit_cost  = calculate_order_cost(exit_price, qty, exit_side)
            total_costs = entry_cost + exit_cost
            if trade["order_type"] == "SELL":
                gross_pnl = (entry - exit_price) * qty
                self._net_position += 1
            else:
                gross_pnl = (exit_price - entry) * qty
                self._net_position -= 1
            pnl = gross_pnl - total_costs

            self._realised_pnl  += pnl
            self._closed_trades += 1

            if trade.get("id"):
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
            else:
                logger.error(f"[wave_extractor] No trade id on close -- DB record not updated for {trade.get('symbol')}")

            self._signal(
                f"Exit order placed | {exit_order_type} | "
                f"Reason: {reason} | Costs: ₹{total_costs:.2f} | "
                f"Gross P&L: ₹{gross_pnl:.2f} | Net P&L: ₹{pnl:.2f} | Order ID: {exit_order_id}"
            )

            asyncio.create_task(self._cool_off_and_rebracket())

        except Exception as e:
            logger.error(f"[wave_extractor] _close_trade failed: {e}")
        finally:
            # Capital must always be released once a trade leaves _open_trades_data,
            # regardless of which exit path fired or whether an exception occurred --
            # fixes capital-drift bug where early-return ("already closed") and
            # exception paths silently skipped release, leaving _deployed_capital
            # stuck high vs. real open exposure (see capital-guard investigation, 31-Jul)
            risk_manager.release_trade(self.name, trade["order_type"])

    async def _close_all_positions(self) -> None:
        if not self._open_trades_data:
            return
        self._signal(f"Closing all {len(self._open_trades_data)} open trade(s)...")
        for trade in list(self._open_trades_data):
            await self._close_trade(trade, "EOD")

    async def _cancel_active_bracket(self) -> None:
        if self._sell_order_id and not self._sell_order_id.startswith("PAPER_SELL"):
            try:
                await self.broker.cancel_order(self._sell_order_id)
                self._signal(f"Pending SELL bracket cancelled: {self._sell_order_id}")
            except Exception as e:
                logger.error(f"[wave_extractor] Failed to cancel SELL bracket: {e}")
        self._sell_order_id = ""

        if self._buy_order_id and not self._buy_order_id.startswith("PAPER_BUY"):
            try:
                await self.broker.cancel_order(self._buy_order_id)
                self._signal(f"Pending BUY bracket cancelled: {self._buy_order_id}")
            except Exception as e:
                logger.error(f"[wave_extractor] Failed to cancel BUY bracket: {e}")
        self._buy_order_id = ""

        self._bracket_active = False

    # ── Utilities ─────────────────────────────────────────────────────────────

    async def _order_timeout_watchdog(self) -> None:
        """Cancel unfilled bracket orders if neither side fills within order_timeout seconds."""
        placed_at = self._bracket_placed_at
        await asyncio.sleep(self.cfg.order_timeout)
        # If bracket is still active and no fill has occurred since we were launched
        if not self._bracket_active or self._bracket_placed_at != placed_at:
            return  # already filled or replaced
        if self._open_trades_data:
            return  # at least one side filled — let monitor handle it
        self._signal(
            f"⏱ Order timeout ({self.cfg.order_timeout:.0f}s) — cancelling unfilled bracket "
            f"SELL={self._sell_order_id} BUY={self._buy_order_id}"
        )
        # Cancel both sides
        for oid, label in [(self._sell_order_id, "SELL"), (self._buy_order_id, "BUY")]:
            if oid and not oid.startswith("PAPER_"):
                try:
                    await self.broker.cancel_order(oid)
                    self._signal(f"Cancelled stale {label} order: {oid}")
                except Exception as e:
                    logger.warning(f"[wave_extractor] Cancel {label} failed: {e}")
        self._sell_order_id  = ""
        self._buy_order_id   = ""
        self._bracket_active = False
        self._bracket_placed_at = 0.0
        asyncio.create_task(self._cool_off_and_rebracket())

    async def _cool_off_and_rebracket(self) -> None:
        self._in_cool_off = True
        self._signal(f"Cool-off started ({self.cfg.cool_off_time}s)")
        await asyncio.sleep(self.cfg.cool_off_time)
        self._in_cool_off = False
        self._signal("Cool-off complete — ready for next bracket")

    def _calculate_pnl(self) -> None:
        pnl = 0.0
        for trade in self._open_trades_data:
            entry = trade["entry_price"]
            qty   = trade["quantity"]
            otype = trade["order_type"]
            if otype == "SELL":
                pnl += (entry - self._current_price) * qty
            else:
                pnl += (self._current_price - entry) * qty
            try:
                tid = trade.get("id", "")
                if tid:
                    pnl_registry[tid] = round(pnl, 2)
                    ltp_registry[tid] = self._current_price
                    trailing_registry[tid] = risk_manager.get_trailing_status(
                        entry, self._current_price, otype, qty, trade_id=tid
                    )
            except Exception:
                pass
        self._unrealised_pnl = pnl
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)

    def get_config(self) -> dict:
        return vars(self.cfg)
