# core/vix_manager.py
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


# --- VIX Zones ----------------------------------------------------------------


@dataclass
class VixRegime:
    name: str
    vix_min: float
    vix_max: float
    sell_gap: float
    buy_gap: float
    trailing_profit: float
    pe_gap: float
    ce_gap: float
    pe_symbol_gap: float
    ce_symbol_gap: float
    max_open_positions: int
    halt_trading: bool = False


VIX_REGIMES = [
    VixRegime(
        name="VERY_LOW",
        vix_min=0,
        vix_max=12,
        sell_gap=15,
        buy_gap=15,
        trailing_profit=20.0,
        pe_gap=10,
        ce_gap=10,
        pe_symbol_gap=250,
        ce_symbol_gap=250,
        max_open_positions=4,
    ),
    VixRegime(
        name="NORMAL",
        vix_min=12,
        vix_max=16,
        sell_gap=20,
        buy_gap=20,
        trailing_profit=25.0,
        pe_gap=15,
        ce_gap=15,
        pe_symbol_gap=300,
        ce_symbol_gap=300,
        max_open_positions=4,
    ),
    VixRegime(
        name="ELEVATED",
        vix_min=16,
        vix_max=20,
        sell_gap=30,
        buy_gap=30,
        trailing_profit=30.0,
        pe_gap=20,
        ce_gap=20,
        pe_symbol_gap=350,
        ce_symbol_gap=350,
        max_open_positions=3,
    ),
    VixRegime(
        name="HIGH",
        vix_min=20,
        vix_max=25,
        sell_gap=40,
        buy_gap=40,
        trailing_profit=35.0,
        pe_gap=25,
        ce_gap=25,
        pe_symbol_gap=400,
        ce_symbol_gap=400,
        max_open_positions=2,
    ),
    VixRegime(
        name="EXTREME",
        vix_min=25,
        vix_max=999,
        sell_gap=50,
        buy_gap=50,
        trailing_profit=40.0,
        pe_gap=30,
        ce_gap=30,
        pe_symbol_gap=450,
        ce_symbol_gap=450,
        max_open_positions=0,
        halt_trading=True,
    ),
]


# --- VIX Manager --------------------------------------------------------------


class VixManager:

    def __init__(self, update_interval: int = 60):
        self.update_interval = update_interval
        self._current_vix = 15.0
        self._current_regime = VIX_REGIMES[1]
        self._last_updated = None
        self._running = False
        self._update_task = None

    def fetch_vix(self) -> float:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/market-data/live-market-indices",
            "X-Requested-With": "XMLHttpRequest",
            "Connection": "keep-alive",
        }
        try:
            session = requests.Session()
            session.cookies.set("AKA_A2", "A", domain=".nseindia.com")
            resp = session.get(
                "https://www.nseindia.com/api/allIndices", headers=headers, timeout=15
            )
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                for index in data.get("data", []):
                    if index.get("index") == "INDIA VIX":
                        vix = float(index.get("last", self._current_vix))
                        logger.info(f"[VixManager] India VIX: {vix}")
                        return vix
            logger.warning(
                f"[VixManager] allIndices status={resp.status_code} len={len(resp.text)}"
            )
        except Exception as e:
            logger.warning(f"[VixManager] fetch failed: {e}")
        logger.warning(f"[VixManager] Using last known VIX: {self._current_vix}")
        return self._current_vix

    def _get_regime(self, vix: float) -> VixRegime:
        for regime in VIX_REGIMES:
            if regime.vix_min <= vix < regime.vix_max:
                return regime
        return VIX_REGIMES[1]

    def _update_vix(self) -> None:
        vix = self.fetch_vix()
        old_regime = self._current_regime.name
        self._current_vix = vix
        self._current_regime = self._get_regime(vix)
        self._last_updated = datetime.now()
        if old_regime != self._current_regime.name:
            logger.warning(
                f"[VixManager] REGIME CHANGE: {old_regime} -> "
                f"{self._current_regime.name} | VIX: {vix}"
            )
            try:
                from core.risk_manager import risk_manager

                risk_manager.max_open_positions = (
                    self._current_regime.max_open_positions
                )
                risk_manager.trailing_profit_pct = self._current_regime.trailing_profit
            except Exception as e:
                logger.error(f"[VixManager] Risk manager update failed: {e}")

    async def start(self) -> None:
        self._running = True
        self._update_vix()
        logger.info(
            f"[VixManager] Started | VIX: {self._current_vix:.2f} | "
            f"Regime: {self._current_regime.name}"
        )
        self._update_task = asyncio.create_task(self._update_loop())

    async def stop(self) -> None:
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        logger.info("[VixManager] Stopped")

    async def _update_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.update_interval)
                if self._running:
                    self._update_vix()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[VixManager] Update loop error: {e}")

    def get_params(self) -> dict:
        r = self._current_regime
        return {
            "sell_gap": r.sell_gap,
            "buy_gap": r.buy_gap,
            "trailing_profit": r.trailing_profit,
            "pe_gap": r.pe_gap,
            "ce_gap": r.ce_gap,
            "pe_symbol_gap": r.pe_symbol_gap,
            "ce_symbol_gap": r.ce_symbol_gap,
            "max_open_positions": r.max_open_positions,
            "halt_trading": r.halt_trading,
        }

    def should_halt(self) -> bool:
        return self._current_regime.halt_trading

    @property
    def current_vix(self) -> float:
        return self._current_vix

    @property
    def regime_name(self) -> str:
        return self._current_regime.name

    @property
    def last_updated(self) -> str:
        if self._last_updated:
            return self._last_updated.strftime("%H:%M:%S")
        return "Never"


# --- Global singleton ---------------------------------------------------------
vix_manager = VixManager(update_interval=60)
