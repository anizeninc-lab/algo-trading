# strategy/saviour_combo.py
import asyncio
import logging
import os
from dataclasses import dataclass, field

from brokers.base import AbstractBrokerGateway, Tick
from core.event_bus import Event, EventType, event_bus
from core.state_store import Direction, StrategyState, state_store
from core.trade_log import trade_logger
from strategy.base_strategy import BaseStrategy
from strategy.survivor import SurvivorAlgo, SurvivorConfig
from strategy.wave_extractor import WaveConfig, WaveExtractor

logger = logging.getLogger(__name__)


@dataclass
class SaviourComboConfig:
    wave:                 WaveConfig     = field(default_factory=WaveConfig)
    survivor:             SurvivorConfig = field(default_factory=SurvivorConfig)
    banknifty_survivor:   SurvivorConfig = None   # None = disabled
    max_combined_loss:    float          = -5000.0
    auto_start_survivor:  bool           = True
    wave_net_threshold:   int            = 2
    monitor_interval:     float          = 10.0


class SaviourCombo:

    def __init__(self, broker: AbstractBrokerGateway, config: SaviourComboConfig):
        self.broker  = broker
        self.cfg     = config
        self.name    = "saviour_combo"
        self.wave     = WaveExtractor(broker, config.wave)
        self.survivor = SurvivorAlgo(broker, config.survivor)
        # BankNifty survivor — paper mode only, None if not configured
        self.bn_survivor = (
            SurvivorAlgo(broker, config.banknifty_survivor)
            if config.banknifty_survivor is not None
            else None
        )
        self._bn_survivor_started = False
        self._running           = False
        self._survivor_started  = False
        self._monitor_task      = None
        state_store.register_strategy(name=self.name, broker=type(broker).__name__)
        logger.info("[saviour_combo] Initialised")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info("[saviour_combo] Starting...")
        state_store.update_state(self.name, StrategyState.RUNNING)


        # Start Wave Extractor — opens WebSocket with all symbols
        await self.wave.start()

        # Share running loop with Wave Extractor
        import asyncio as _aio_wave
        try:
            _wave_loop = _aio_wave.get_running_loop()
            self.wave._loop = _wave_loop
            logger.info(f"[saviour_combo] Shared loop with Wave Extractor: {_wave_loop}")
        except Exception as _we:
            logger.error(f"[saviour_combo] Could not share loop with Wave: {_we}")
        # Start Survivor immediately if threshold is 0 or auto_start enabled
        if self.cfg.auto_start_survivor or self.cfg.wave_net_threshold == 0:
            await self.survivor.start()
            self._survivor_started = True
            import asyncio as _aio
            try:
                running_loop = _aio.get_running_loop()
                self.survivor._loop = running_loop
                logger.info(f"[saviour_combo] Shared running loop with Survivor: {running_loop}")
            except Exception as e:
                logger.error(f"[saviour_combo] Could not get running loop: {e}")
        # Register NIFTY ticks AFTER survivor.start() so loop is ready
        self.broker.subscribe_ticks(
            symbols=["NSE_INDEX|Nifty 50"],
            callback=self.survivor._on_tick_sync,
        )

        # In paper trade mode, always auto-start Survivor immediately
        if os.getenv("PAPER_TRADE", "false").lower() == "true" and not self._survivor_started:
            logger.info("[saviour_combo] PAPER MODE: Auto-starting Survivor immediately")
            await self.survivor.start()
            self._survivor_started = True

        # ── BankNifty Survivor (always paper mode) ────────────────────────
        if self.bn_survivor is not None:
            try:
                await self.bn_survivor.start()
                self._bn_survivor_started = True
                import asyncio as _aio
                try:
                    running_loop = _aio.get_running_loop()
                    self.bn_survivor._loop = running_loop
                except Exception:
                    pass
                # Subscribe BankNifty index ticks
                self.broker.subscribe_ticks(
                    symbols=["NSE_INDEX|Nifty Bank"],
                    callback=self.bn_survivor._on_tick_sync,
                )
                logger.info("[saviour_combo] BankNifty Survivor started (PAPER MODE)")
            except Exception as e:
                logger.error(f"[saviour_combo] BankNifty Survivor failed to start: {e}")

        event_bus.subscribe(self._on_child_event)
        self._running      = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        state_store.update_last_signal(
            self.name,
            "Survivor auto-started | Wave net: 0"
            if self._survivor_started
            else "Wave Extractor running | Survivor on standby",
        )
        logger.info("[saviour_combo] Started successfully")
        # Send startup alert
        try:
            from core.alerting import alert_system_start
            is_paper = os.getenv("PAPER_TRADE", "false").lower() == "true"
            from core.market_context import market_context
            alert_system_start(0.0, market_context.regime, is_paper)
        except Exception:
            pass

    async def stop(self, reason: str = "MANUAL") -> None:
        logger.info(f"[saviour_combo] Stopping ({reason})...")
        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self._survivor_started:
            await self.survivor.stop(reason)

        if self._bn_survivor_started and self.bn_survivor is not None:
            await self.bn_survivor.stop(reason)

        await self.wave.stop(reason)

        event_bus.unsubscribe(self._on_child_event)
        state_store.update_state(self.name, StrategyState.STOPPED)
        # ── Generate EOD report on any stop ──────────────────────────────
        try:
            from core.eod_report import generate_eod_report
            generate_eod_report(reason=reason, combo=self)
        except Exception as _re:
            logger.warning(f"[saviour_combo] EOD report failed: {_re}")
        logger.info("[saviour_combo] Stopped")

    # ── Monitor Loop ──────────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        logger.info("[saviour_combo] Monitor loop started")
        while self._running:
            try:
                await asyncio.sleep(self.cfg.monitor_interval)
                if not self._running:
                    break

                combined_pnl = self._get_combined_pnl()

                # Combined loss breached — stop everything
                if combined_pnl <= self.cfg.max_combined_loss:
                    logger.warning(
                        f"[saviour_combo] Max combined loss hit: ₹{combined_pnl:.2f}"
                    )
                    state_store.update_last_signal(
                        self.name,
                        f"MAX LOSS HIT: ₹{combined_pnl:.2f} — stopping all strategies",
                    )
                    await self.stop(reason="MAX_LOSS")
                    break

                # Auto-start Survivor when Wave net position hits threshold
                # (only in live mode — paper mode starts it immediately on start)
                if (
                    self.cfg.auto_start_survivor
                    and not self._survivor_started
                    and os.getenv("PAPER_TRADE", "false").lower() != "true"
                    and abs(self.wave._net_position) >= self.cfg.wave_net_threshold
                ):
                    logger.info(
                        f"[saviour_combo] Auto-starting Survivor | "
                        f"Wave net position: {self.wave._net_position}"
                    )
                    await self.survivor.start()
                    self._survivor_started = True
                    state_store.update_last_signal(
                        self.name,
                        f"Survivor auto-started | Wave net: {self.wave._net_position}",
                    )

                self._update_combo_status(combined_pnl)
                # ── Periodic broker parity check (every 15 min) ──────────────
                import time as _time
                if _time.time() - self._last_parity_check >= self._PARITY_INTERVAL:
                    self._last_parity_check = _time.time()
                    asyncio.create_task(self._broker_parity_check())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[saviour_combo] Monitor loop error: {e}")

        logger.info("[saviour_combo] Monitor loop ended")

    # ── Event Handling ────────────────────────────────────────────────────────

    async def _on_child_event(self, event: Event) -> None:
        if event.strategy not in ("survivor", "wave_extractor"):
            return

        if event.event_type == EventType.ERROR:
            state_store.update_last_signal(
                self.name,
                f"ERROR in {event.strategy}: {event.payload.get('message', '')}",
            )

        if event.event_type == EventType.STATE_CHANGE:
            if event.payload.get("state") == StrategyState.ERROR:
                state_store.update_state(
                    self.name,
                    StrategyState.ERROR,
                    f"{event.strategy} entered ERROR state",
                )

    # ── P&L and Status ────────────────────────────────────────────────────────

    async def _broker_parity_check(self) -> None:
        """Cross-check local positions + P&L against broker every 15 minutes.
        Logs warnings and sends Telegram alert if divergence exceeds tolerance.
        """
        try:
            broker_positions = await self.broker.get_positions()
        except Exception as e:
            logger.warning(f"[saviour_combo] Parity check: get_positions failed: {e}")
            return

        # Build local symbol -> qty map from all strategies
        local_map: dict = {}
        for strategy in [self.wave, self.survivor] + ([self.bn_survivor] if self.bn_survivor else []):
            for trade in getattr(strategy, "_open_trades_data", []):
                sym = trade.get("symbol", "")
                qty = trade.get("quantity", 0)
                local_map[sym] = local_map.get(sym, 0) + qty

        # Build broker symbol -> qty + pnl map
        broker_map: dict = {}
        broker_pnl = 0.0
        for p in broker_positions:
            if abs(p.quantity) > 0:
                broker_map[p.symbol] = abs(p.quantity)
                broker_pnl += p.pnl

        mismatches = []
        for sym, local_qty in local_map.items():
            broker_qty = broker_map.get(sym, 0)
            if local_qty != broker_qty:
                mismatches.append(f"{sym}: local={local_qty} broker={broker_qty}")
        for sym, broker_qty in broker_map.items():
            if sym not in local_map:
                mismatches.append(f"{sym}: local=0 broker={broker_qty} [GHOST]")

        local_pnl = self._get_combined_pnl()
        pnl_diff  = abs(local_pnl - broker_pnl)
        has_issues = mismatches or pnl_diff > self._PARITY_PNL_TOL

        if has_issues:
            msg_lines = ["⚠️ PARITY CHECK FAILED"]
            if mismatches:
                msg_lines.append("Position mismatches:")
                msg_lines.extend(f"  • {m}" for m in mismatches)
            if pnl_diff > self._PARITY_PNL_TOL:
                msg_lines.append(
                    f"P&L divergence: local=₹{local_pnl:.2f} broker=₹{broker_pnl:.2f} "
                    f"diff=₹{pnl_diff:.2f} (tolerance ₹{self._PARITY_PNL_TOL:.0f})"
                )
            full_msg = "\n".join(msg_lines)
            logger.warning(f"[saviour_combo] {full_msg}")
            try:
                from core.alerting import send_telegram, LEVEL_WARNING
                send_telegram(full_msg, LEVEL_WARNING)
            except Exception:
                pass
        else:
            logger.info(
                f"[saviour_combo] Parity OK | positions={len(local_map)} matched | "
                f"local_pnl=₹{local_pnl:.2f} broker_pnl=₹{broker_pnl:.2f}"
            )

    def _get_combined_pnl(self) -> float:
        wave_s     = state_store.get_strategy("wave_extractor")
        survivor_s = state_store.get_strategy("survivor")
        bn_s       = state_store.get_strategy("bn_survivor") if self.bn_survivor else None
        wave_pnl     = (wave_s.realised_pnl     + wave_s.unrealised_pnl)     if wave_s     else 0.0
        survivor_pnl = (survivor_s.realised_pnl + survivor_s.unrealised_pnl) if survivor_s else 0.0
        bn_pnl       = (bn_s.realised_pnl       + bn_s.unrealised_pnl)       if bn_s       else 0.0
        # NOTE: max_combined_loss (-5000) is intentionally looser than risk_manager daily limit (-3000).
        # If BankNifty ever goes live, audit both limits for consistency.
        return round(wave_pnl + survivor_pnl + bn_pnl, 2)

    def _update_combo_status(self, combined_pnl: float) -> None:
        wave_s     = state_store.get_strategy("wave_extractor")
        survivor_s = state_store.get_strategy("survivor")
        bn_s       = state_store.get_strategy("bn_survivor") if self.bn_survivor else None

        total_trades = 0
        open_trades  = 0
        open_orders  = 0

        for s in [wave_s, survivor_s, bn_s]:
            if s:
                total_trades += getattr(s, "total_trades", 0)
                open_trades  += getattr(s, "open_trades",  0)
                open_orders  += getattr(s, "open_orders",  0)

        state_store.update_pnl(self.name, realised=combined_pnl, unrealised=0.0)
        state_store.update_trades(
            name=self.name,
            total=total_trades,
            open_count=open_trades,
            closed=total_trades - open_trades,
        )
        state_store.update_orders(self.name, open_orders)

    def get_config(self) -> dict:
        return {
            "wave":                vars(self.cfg.wave),
            "survivor":            vars(self.cfg.survivor),
            "max_combined_loss":   self.cfg.max_combined_loss,
            "auto_start_survivor": self.cfg.auto_start_survivor,
            "wave_net_threshold":  self.cfg.wave_net_threshold,
            "monitor_interval":    self.cfg.monitor_interval,
        }