from core.strategy_filter import strategy_filter
# strategy/wave_extractor.py
import asyncio
import logging
import os
import pytz as _pytz
from dataclasses import dataclass, field
from datetime import datetime

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.event_bus import EventType
from core.risk_manager import risk_manager
from dashboard.api import pnl_registry, ltp_registry
from core.state_store import Direction, state_store
from core.trade_log import trade_logger
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class WaveConfig:
    option_symbol:        str   = ""
    sell_gap:             float = 20.0
    buy_gap:              float = 20.0
    quantity:             int   = 65
    cool_off_time:        float = 5.0
    multiplier_scale:     list  = field(default_factory=lambda: [1.0, 1.3, 1.7, 2.2, 2.8])
    max_net_position:     int   = 2
    delta_limit:          float = 200.0
    nifty_instrument_key: str   = "NSE_INDEX|Nifty 50"


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
        self._current_price    = 0.0
        self._realised_pnl     = 0.0
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
        if os.getenv("PAPER_TRADE", "false").lower() == "true":
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
            # ── Global pre-trade gate (#24) ────────────────────────────────────
            blocked, reason = risk_manager.is_trading_blocked()
            if blocked:
                if reason != self._last_block_reason:
                    self._last_block_reason = reason
                    logger.info(f"[wave_extractor] Trading blocked: {reason}")
                    if "daily loss" in reason.lower() or "halted" in reason.lower():
                        await self._close_all_positions()
                        await self.stop(reason="MAX_DAILY_LOSS")
                    elif "auto-stop" in reason.lower():
                        await self._close_all_positions()
                        await self.stop(reason="AUTO_STOP")
                return
            else:
                self._last_block_reason = ""
            self._current_price = tick.last_price

            if "INDEX" in tick.symbol:
                state_store.update_nifty_price(tick.last_price)
            else:
                state_store.update_option_price(self.cfg.option_symbol, tick.last_price)

            await self._monitor_open_trades()
            self._calculate_pnl()

            # ── Paper Trade Fill Simulator ──────────────────────────────────
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                filled = await self._handle_paper_fill(tick.last_price)
                if filled:
                    return
            # ── End Paper Trade Fill Simulator ─────────────────────────────

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
                self._signal(f"Trade blocked: {reason}")

        except Exception as e:
            logger.exception(f"[wave_extractor] ERROR in on_tick: {e}")

    # ── Order Update Handler ──────────────────────────────────────────────────

    def _on_order_update(self, update: dict) -> None:
        try:
            if self._stop_flag:
                return
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_order_update(update), self._loop
            )
        except Exception as e:
            logger.error(f"[wave_extractor] Order update error: {e}")

    async def _handle_order_update(self, update: dict) -> None:
        if self._stop_flag:
            return

        order_id = update.get("order_id", "")
        status   = update.get("status", "")

        if status != "COMPLETE":
            return

        price = float(update.get("average_price", 0))

        if order_id == self._sell_order_id:
            self._net_position  -= 1
            self._bracket_active = False
            self._signal(f"SELL filled @ {price:.2f}")
            risk_manager.register_trade(self.name, "SELL")
            _live_trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=self.cfg.option_symbol,
                order_type="SELL",
                quantity=self.cfg.quantity,
                entry_price=price,
                broker_order_id=order_id,
            )
            self._open_trades_data.append({
                "id":          _live_trade_id,
                "order_id":    order_id,
                "order_type":  "SELL",
                "entry_price": price,
                "quantity":    self.cfg.quantity,
                "symbol":      self.cfg.option_symbol,
            })
            if self._buy_order_id:
                await self.broker.cancel_order(self._buy_order_id)
                self._signal(f"Opposing BUY bracket cancelled: {self._buy_order_id}")
                self._buy_order_id = ""

        elif order_id == self._buy_order_id:
            self._net_position  += 1
            self._bracket_active = False
            self._signal(f"BUY filled @ {price:.2f}")
            risk_manager.register_trade(self.name, "BUY")
            _live_trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=self.cfg.option_symbol,
                order_type="BUY",
                quantity=self.cfg.quantity,
                entry_price=price,
                broker_order_id=order_id,
            )
            self._open_trades_data.append({
                "id":          _live_trade_id,
                "order_id":    order_id,
                "order_type":  "BUY",
                "entry_price": price,
                "quantity":    self.cfg.quantity,
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
                order_type=trade["order_type"],
                quantity=trade["quantity"],
                entry_price=trade["entry_price"],
                broker_order_id=trade["order_id"],
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
                order_type=trade["order_type"],
                quantity=trade["quantity"],
                entry_price=trade["entry_price"],
                broker_order_id=trade["order_id"],
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

            if risk_manager.check_trade_stop_loss(entry, price, qty, otype):
                self._signal(
                    f"STOP LOSS hit | {otype} | Entry: {entry:.2f} | Current: {price:.2f}"
                )
                await self._close_trade(trade, "STOP_LOSS")

            elif risk_manager.check_trailing_profit(entry, price, otype):
                self._signal(
                    f"TRAILING PROFIT hit | {otype} | Entry: {entry:.2f} | Current: {price:.2f}"
                )
                await self._close_trade(trade, "TRAILING_PROFIT")

    # ── Order Placement ───────────────────────────────────────────────────────

    async def _place_duo_bracket(self) -> None:
        if self._current_price == 0:
            return

        # Core State Mutex Verification Guard
        if any(t.get("symbol") == self.cfg.option_symbol for t in self._open_trades_data):
            logger.warning("[wave_extractor] MUTEX LOCK: Active trade tracking present in core block. Bracket creation halted.")
            return

        self._bracket_active = True

        sell_price = round(self._current_price + self.cfg.sell_gap, 2)
        buy_price  = round(self._current_price - self.cfg.buy_gap, 2)

        if buy_price <= 0:
            self._bracket_active = False
            return

        self._sell_price = sell_price
        self._buy_price  = buy_price

        self._signal(f"Bracket | SELL {sell_price} | BUY {buy_price}")

        # Deterministic Identity strings matching today's lifecycle window
        _now_tz = datetime.now(_pytz.timezone("Asia/Kolkata"))
        today_prefix = _now_tz.strftime('%Y%m%d')
        deterministic_sell_tag = f"WAVE_SELL_{today_prefix}"
        deterministic_buy_tag  = f"WAVE_BUY_{today_prefix}"

        # ── Place SELL limit order ────────────────────────────────────────────
        try:
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
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
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
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
            exit_price = round(self._current_price * 1.02, 1)
        else:
            exit_price = round(self._current_price * 0.98, 1)

        try:
            _now_tz = datetime.now(_pytz.timezone("Asia/Kolkata"))
            today_prefix = _now_tz.strftime('%Y%m%d')
            deterministic_exit_tag = f"WAVE_EXIT_{trade['order_type']}_{today_prefix}"

            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                self._signal(f"[PAPER] EXIT {exit_order_type} {trade['quantity']} {self.cfg.option_symbol} @ {exit_price} (simulated)")
                exit_order_id = f"PAPER_EXIT_{today_prefix}"
            else:
                resp = await self.broker.place_order(Order(
                    symbol=self.cfg.option_symbol,
                    exchange="NFO",
                    order_type=exit_order_type,
                    quantity=trade["quantity"],
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

            risk_manager.release_trade(self.name, trade["order_type"])

            if trade.get("id"):
                trade_logger.close_trade(
                    trade["id"], exit_price, reason,
                    net_pnl=pnl, gross_pnl=gross_pnl, total_costs=total_costs,
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
            except Exception:
                pass
        self._unrealised_pnl = pnl
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)

    def get_config(self) -> dict:
        return vars(self.cfg)