# strategy/survivor.py
import asyncio
import logging
import os
from dataclasses import dataclass

from brokers.base import AbstractBrokerGateway, Order, Tick
from core.event_bus import EventType
from core.risk_manager import risk_manager
from core.state_store import Direction, state_store
from core.trade_log import trade_logger
from core.vix_manager import vix_manager
from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class SurvivorConfig:
    symbol_initials:   str   = "NIFTY13APR26"
    pe_gap:            float = 15.0
    ce_gap:            float = 15.0
    pe_symbol_gap:     float = 300.0
    ce_symbol_gap:     float = 300.0
    pe_reset_gap:      float = 90.0
    ce_reset_gap:      float = 90.0
    pe_quantity:       int   = 65
    ce_quantity:       int   = 65
    pe_start:          float = 0.0
    ce_start:          float = 0.0
    min_price_to_sell: float = 10.0
    nifty_instrument_key: str = "NSE_INDEX|Nifty 50"
    strike_interval:   float = 50.0


class SurvivorAlgo(BaseStrategy):

    def __init__(self, broker: AbstractBrokerGateway, config: SurvivorConfig):
        super().__init__(name="survivor", broker=broker, config=vars(config))
        self.cfg               = config
        self._loop             = None
        self._pe_last_value    = config.pe_start
        self._ce_last_value    = config.ce_start
        self._pe_sold_flag     = False
        self._ce_sold_flag     = False
        self._open_trade_ids   = []
        self._open_trades_data = []
        self._realised_pnl     = 0.0
        self._unrealised_pnl   = 0.0
        self._last_nifty_price = 0.0
        self._closed_trades    = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        self._loop = asyncio.get_running_loop()

        nifty_price = await self.broker.get_ltp(self.cfg.nifty_instrument_key)
        if nifty_price == 0.0:
            raise RuntimeError("[survivor] Could not fetch NIFTY price on startup.")

        if self.cfg.pe_start == 0.0:
            self.cfg.pe_start = nifty_price
        if self.cfg.ce_start == 0.0:
            self.cfg.ce_start = nifty_price

        self._pe_last_value = self.cfg.pe_start
        self._ce_last_value = self.cfg.ce_start

        self.broker.subscribe_ticks(
            symbols=[self.cfg.nifty_instrument_key],
            callback=self._on_tick_sync
        )

        logger.info(
            f"[survivor] PE Anchor: {self._pe_last_value} | "
            f"CE Anchor: {self._ce_last_value}"
        )
        self._signal(
            f"Started | Spot: {nifty_price:.2f} | "
            f"PE Anchor: {self._pe_last_value:.2f} | "
            f"CE Anchor: {self._ce_last_value:.2f}"
        )

    async def on_stop(self) -> None:
        await self._close_all_positions()
        self.broker.unsubscribe_ticks([self.cfg.nifty_instrument_key])

    # ── Tick Handling ─────────────────────────────────────────────────────────

    def _on_tick_sync(self, tick: Tick) -> None:
        try:
            if tick.last_price < 10000:
                return
            if self._loop is None or self._loop.is_closed():
                return
            self._last_nifty_price = tick.last_price
            future = asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._loop)
            future.add_done_callback(
                lambda f: f.exception() if f.cancelled() or f.exception() else None
            )
        except Exception as e:
            logger.error(f"[survivor] Tick sync error: {e}")

    async def on_tick(self, tick: Tick) -> None:
        try:
            if self._stop_flag or not self.is_market_open():
                return

            if risk_manager.check_auto_stop():
                self._signal("Auto-stop triggered (3:10 PM) — closing all positions")
                await self._close_all_positions()
                await self.stop(reason="AUTO_STOP")
                return

            if risk_manager.check_max_daily_loss():
                self._signal("Max daily loss hit — closing all positions")
                await self._close_all_positions()
                await self.stop(reason="MAX_DAILY_LOSS")
                return

            nifty_price = tick.last_price
            self._last_nifty_price = nifty_price

            # Fetch VIX-adjusted parameters
            vix_params     = vix_manager.get_params()
            current_pe_gap = vix_params.get("pe_gap", self.cfg.pe_gap)
            current_ce_gap = vix_params.get("ce_gap", self.cfg.ce_gap)
            pe_symbol_gap  = vix_params.get("pe_symbol_gap", self.cfg.pe_symbol_gap)
            ce_symbol_gap  = vix_params.get("ce_symbol_gap", self.cfg.ce_symbol_gap)

            # Monitor open trades for SL and trailing profit
            await self._monitor_open_trades(nifty_price)
            self._calculate_pnl(nifty_price)

            can_trade, reason = risk_manager.can_trade(self.name)

            if can_trade:
                # PE SELL TRIGGER — Nifty moved up enough from last PE anchor
                if nifty_price - self._pe_last_value >= current_pe_gap:
                    await self._sell_option(
                        direction="PE",
                        nifty_price=nifty_price,
                        gap=pe_symbol_gap,
                        quantity=self.cfg.pe_quantity,
                    )
                    self._pe_last_value = nifty_price
                    self._pe_sold_flag  = True
                    self._update_position(Direction.SHORT)

                # CE SELL TRIGGER — Nifty moved down enough from last CE anchor
                if self._ce_last_value - nifty_price >= current_ce_gap:
                    await self._sell_option(
                        direction="CE",
                        nifty_price=nifty_price,
                        gap=ce_symbol_gap,
                        quantity=self.cfg.ce_quantity,
                    )
                    self._ce_last_value = nifty_price
                    self._ce_sold_flag  = True
                    self._update_position(Direction.SHORT)

            else:
                self._signal(f"Trade blocked: {reason}")

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

    async def _sell_option(
        self,
        direction: str,
        nifty_price: float,
        gap: float,
        quantity: int,
    ) -> None:
        interval     = self.cfg.strike_interval
        base_strike  = nifty_price - gap if direction == "PE" else nifty_price + gap
        strike       = round(base_strike / interval) * interval
        symbol       = None
        final_strike = strike

        # Search up to 5 strikes for one that meets min premium
        for _ in range(5):
            candidate = self._build_symbol(direction, final_strike)
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                # In paper mode simulate a premium based on distance from spot
                premium = max(5.0, 50.0 - abs(nifty_price - final_strike) * 0.1)
            else:
                premium = await self.broker.get_ltp(candidate)

            if premium >= self.cfg.min_price_to_sell:
                symbol = candidate
                break
            final_strike += interval if direction == "PE" else -interval

        if not symbol:
            logger.warning(
                f"[survivor] No {direction} strike found above "
                f"₹{self.cfg.min_price_to_sell} — skipping"
            )
            return

        try:
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                sell_price  = round(premium * 0.98, 1)
                entry_price = sell_price
                self._signal(f"[PAPER] SELL {quantity} {symbol} @ {sell_price} (simulated)")
                order_id = "PAPER_SELL"
            else:
                ltp        = await self.broker.get_ltp(symbol)
                sell_price = round(ltp * 0.98, 1) if ltp > 0 else 0.0
                resp       = await self.broker.place_order(Order(
                    symbol=symbol,
                    exchange="NFO",
                    order_type="SELL",
                    quantity=quantity,
                    product="I",
                    price=sell_price,
                ))
                if resp.status == "REJECTED":
                    self._signal(f"{direction} order REJECTED: {resp.message}")
                    return
                entry_price = await self.broker.get_ltp(symbol)
                order_id    = resp.order_id

            trade_id = trade_logger.open_trade(
                strategy=self.name,
                broker=type(self.broker).__name__,
                symbol=symbol,
                order_type="SELL",
                quantity=quantity,
                entry_price=entry_price,
                broker_order_id=order_id,
                notes=f"VIX Regime Trigger | Nifty @ {nifty_price:.2f}",
            )

            self._open_trade_ids.append(trade_id)
            self._open_trades_data.append({
                "id":         trade_id,
                "order_type": "SELL",
                "entry":      entry_price,
                "symbol":     symbol,
                "quantity":   quantity,
                "direction":  direction,
            })

            risk_manager.register_trade(self.name)
            self._signal(
                f"SOLD {direction} {int(final_strike)} @ ₹{entry_price:.2f} | "
                f"Order: {order_id}"
            )

        except Exception as e:
            logger.error(f"[survivor] _sell_option failed for {direction}: {e}")

    def _build_symbol(self, option_type: str, strike: float) -> str:
        return f"NSE_FO|{self.cfg.symbol_initials}{int(strike):05d}{option_type}"

    # ── Trade Monitoring (SL + Trailing Profit) ───────────────────────────────

    async def _monitor_open_trades(self, nifty_price: float = 0.0) -> None:
        if not self._open_trades_data:
            return

        for trade in list(self._open_trades_data):
            try:
                if os.getenv("PAPER_TRADE", "false").lower() == "true":
                    # Simulate current option price based on nifty movement
                    curr_price = trade["entry"] * 0.95  # Assume slight decay
                else:
                    curr_price = await self.broker.get_ltp(trade["symbol"])

                if curr_price == 0:
                    continue

                if risk_manager.check_trade_stop_loss(
                    trade["entry"], curr_price, trade["quantity"], trade["order_type"]
                ):
                    self._signal(
                        f"STOP LOSS hit | {trade['direction']} {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | Current: {curr_price:.2f}"
                    )
                    await self._close_trade(trade, "SL_HIT", curr_price)

                elif risk_manager.check_trailing_profit(
                    trade["entry"], curr_price, trade["order_type"]
                ):
                    self._signal(
                        f"TRAILING PROFIT hit | {trade['direction']} {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | Current: {curr_price:.2f}"
                    )
                    await self._close_trade(trade, "TP_HIT", curr_price)

            except Exception as e:
                logger.error(f"[survivor] Monitor error for {trade['symbol']}: {e}")

    # ── Trade Exit ────────────────────────────────────────────────────────────

    async def _close_trade(self, trade: dict, reason: str, current_price: float = 0.0) -> None:
        if trade not in self._open_trades_data:
            return

        exit_order_type = "BUY" if trade["order_type"] == "SELL" else "SELL"

        if os.getenv("PAPER_TRADE", "false").lower() == "true":
            if current_price == 0.0:
                current_price = trade["entry"] * 0.95
            exit_price = round(current_price * 1.02, 1)
            self._signal(f"[PAPER] {exit_order_type} {trade['quantity']} {trade['symbol']} @ {exit_price} (simulated)")
            order_id = "PAPER_EXIT"
        else:
            if current_price == 0.0:
                current_price = await self.broker.get_ltp(trade["symbol"])
            if exit_order_type == "BUY":
                exit_price = round(current_price * 1.02, 1)
            else:
                exit_price = round(current_price * 0.98, 1)
            try:
                resp = await self.broker.place_order(Order(
                    symbol=trade["symbol"],
                    exchange="NFO",
                    order_type=exit_order_type,
                    quantity=trade["quantity"],
                    product="I",
                    price=exit_price,
                ))
                if resp.status == "REJECTED":
                    self._signal(f"Exit order REJECTED for {trade['symbol']}: {resp.message}")
                    return
                order_id = resp.order_id
            except Exception as e:
                logger.error(f"[survivor] _close_trade failed for {trade['symbol']}: {e}")
                return

        pnl = trade_logger.close_trade(trade["id"], current_price, reason)
        self._realised_pnl += pnl

        self._open_trades_data = [
            t for t in self._open_trades_data if t["id"] != trade["id"]
        ]
        if trade["id"] in self._open_trade_ids:
            self._open_trade_ids.remove(trade["id"])

        self._closed_trades += 1
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
        self._signal(
            f"CLOSED {trade['symbol']} | Reason: {reason} | "
            f"P&L: ₹{pnl:.2f} | Order: {order_id}"
        )

    async def _close_all_positions(self) -> None:
        if not self._open_trades_data:
            return
        self._signal(f"Closing all {len(self._open_trades_data)} open trade(s)...")
        for trade in list(self._open_trades_data):
            await self._close_trade(trade, "EOD")

    # ── P&L Calculation ───────────────────────────────────────────────────────

    def _calculate_pnl(self, nifty_price: float = 0.0) -> None:
        unrealised = 0.0
        for trade in self._open_trades_data:
            entry = trade["entry"]
            qty   = trade["quantity"]
            if os.getenv("PAPER_TRADE", "false").lower() == "true":
                # Simulate slight option decay for paper trades
                curr = entry * 0.95
            else:
                curr = entry  # Will be updated by real LTP calls
            if trade["order_type"] == "SELL":
                unrealised += (entry - curr) * qty
            else:
                unrealised += (curr - entry) * qty
        self._unrealised_pnl = round(unrealised, 2)
        self._update_pnl(self._realised_pnl, self._unrealised_pnl)

    def get_config(self) -> dict:
        return vars(self.cfg)