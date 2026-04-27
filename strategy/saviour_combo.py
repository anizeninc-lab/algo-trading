# strategy/saviour_combo.py
import asyncio
import logging
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
        self._running           = False
        self._survivor_started  = False
        self._monitor_task      = None
        state_store.register_strategy(name=self.name, broker=type(broker).__name__)
        logger.info("[saviour_combo] Initialised")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info("[saviour_combo] Starting...")
        state_store.update_state(self.name, StrategyState.RUNNING)

        # Pre-register NIFTY index tick for Survivor BEFORE Wave starts,
        # so both symbols are included in the single WebSocket connection
        self.broker.subscribe_ticks(
            symbols=["NSE_INDEX|Nifty 50"],
            callback=self.survivor._on_tick_sync,
        )
        logger.info("[saviour_combo] NIFTY index pre-registered for Survivor")

        # Start Wave Extractor — opens WebSocket with all symbols
        await self.wave.start()

        # If auto_start is disabled, start Survivor immediately
        # If auto_start is enabled, monitor loop will start it
        # when wave net position hits the threshold
        if not self.cfg.auto_start_survivor:
            await self.survivor.start()
            self._survivor_started = True

        event_bus.subscribe(self._on_child_event)
        self._running      = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        state_store.update_last_signal(
            self.name,
            "Wave Extractor running"
            + (
                " | Survivor on standby"
                if self.cfg.auto_start_survivor
                else " | Survivor running"
            ),
        )
        logger.info("[saviour_combo] Started successfully")

    async def stop(self, reason: str = "MANUAL") -> None:
        logger.info(f"[saviour_combo] Stopping ({reason})...")
        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop survivor first (closes its positions), then wave
        if self._survivor_started:
            await self.survivor.stop(reason)

        await self.wave.stop(reason)

        event_bus.unsubscribe(self._on_child_event)
        state_store.update_state(self.name, StrategyState.STOPPED)
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
                if (
                    self.cfg.auto_start_survivor
                    and not self._survivor_started
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

    def _get_combined_pnl(self) -> float:
        wave_s     = state_store.get_strategy("wave_extractor")
        survivor_s = state_store.get_strategy("survivor")
        wave_pnl     = (wave_s.realised_pnl     + wave_s.unrealised_pnl)     if wave_s     else 0.0
        survivor_pnl = (survivor_s.realised_pnl + survivor_s.unrealised_pnl) if survivor_s else 0.0
        return round(wave_pnl + survivor_pnl, 2)

    def _update_combo_status(self, combined_pnl: float) -> None:
        wave_s     = state_store.get_strategy("wave_extractor")
        survivor_s = state_store.get_strategy("survivor")

        total_trades = 0
        open_trades  = 0
        open_orders  = 0

        for s in [wave_s, survivor_s]:
            if s:
                # Use getattr with defaults to avoid AttributeError
                # if state_store hasn't populated these fields yet
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