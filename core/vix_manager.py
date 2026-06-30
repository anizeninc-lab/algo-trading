# core/vix_manager.py
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime

import pytz
import requests

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
MAX_STALENESS_MINUTES = 5.0  # if no successful fetch in this window during market hours, fail safe


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


def _is_market_hours() -> bool:
    now = datetime.now(IST).time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


# --- VIX Manager --------------------------------------------------------------


class VixManager:

    def __init__(self, update_interval: int = 60):
        self.update_interval = update_interval
        self._current_vix = 15.0
        self._current_regime = VIX_REGIMES[1]
        self._last_updated = None          # last poll attempt (success or fail)
        self._last_successful_fetch = None  # last fetch that actually returned real data
        self._last_fetch_source = "none"
        self._running = False
        self._update_task = None
        self._broker = None
        self._is_stale = False
        self._stale_alert_fired = False

    def set_broker(self, broker) -> None:
        """Called once at startup so the manager can use the official Upstox
        India VIX quote as primary source, with the NSE scrape as fallback.
        Safe to call even before the broker has logged in -- fetches will
        simply fall through to the NSE scrape until login succeeds."""
        self._broker = broker

    async def _fetch_vix_via_broker(self) -> float:
        """Primary source: official Upstox India VIX quote. Returns 0.0 on
        any failure (not logged in yet, API error, etc) so the caller can
        fall back to the NSE scrape."""
        if not self._broker:
            return 0.0
        try:
            vix = await self._broker.get_ltp("NSE_INDEX|India VIX")
            if vix and vix > 0:
                return float(vix)
        except Exception as e:
            logger.warning(f"[VixManager] Broker VIX fetch failed: {e}")
        return 0.0

    def fetch_vix_nse_scrape(self) -> float:
        """Fallback source: unofficial NSE allIndices scrape. Kept as a
        secondary source only -- this endpoint is undocumented and can break
        without notice, which is why it is no longer the sole source of truth."""
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
                        vix = float(index.get("last", 0.0))
                        if vix > 0:
                            return vix
            logger.warning(
                f"[VixManager] NSE scrape status={resp.status_code} len={len(resp.text)}"
            )
        except Exception as e:
            logger.warning(f"[VixManager] NSE scrape failed: {e}")
        return 0.0

    def _get_regime(self, vix: float) -> VixRegime:
        for regime in VIX_REGIMES:
            if regime.vix_min <= vix < regime.vix_max:
                return regime
        return VIX_REGIMES[1]

    def _minutes_since_last_success(self) -> float:
        if not self._last_successful_fetch:
            return 999.0
        return (datetime.now() - self._last_successful_fetch).total_seconds() / 60.0

    async def _update_vix(self) -> None:
        self._last_updated = datetime.now()

        vix = await self._fetch_vix_via_broker()
        source = "upstox"
        if vix <= 0.0:
            vix = self.fetch_vix_nse_scrape()
            source = "nse_scrape"

        if vix > 0.0:
            # Real successful fetch -- update everything normally.
            old_regime = self._current_regime.name
            self._current_vix = vix
            self._last_successful_fetch = datetime.now()
            self._last_fetch_source = source
            if self._is_stale:
                logger.warning(f"[VixManager] Feed recovered via {source} | VIX: {vix}")
            self._is_stale = False
            self._stale_alert_fired = False
            self._current_regime = self._get_regime(vix)
            logger.info(f"[VixManager] VIX: {vix} | Source: {source}")
            if old_regime != self._current_regime.name:
                logger.warning(
                    f"[VixManager] REGIME CHANGE: {old_regime} -> "
                    f"{self._current_regime.name} | VIX: {vix}"
                )
                self._apply_regime_to_risk_manager()
            return

        # Both sources failed this poll. Do NOT silently keep trading on
        # last-known-good as if it were current -- check staleness instead.
        logger.warning(
            f"[VixManager] Both VIX sources failed this poll | "
            f"Using last known: {self._current_vix} | "
            f"Minutes since last success: {self._minutes_since_last_success():.1f}"
        )

        if not _is_market_hours():
            return  # don't force a halt regime outside trading hours

        minutes_stale = self._minutes_since_last_success()
        if minutes_stale >= MAX_STALENESS_MINUTES:
            if not self._is_stale:
                logger.critical(
                    f"[VixManager] VIX FEED STALE for {minutes_stale:.1f} min during "
                    f"market hours -- forcing EXTREME regime (halt) as a fail-safe"
                )
            self._is_stale = True
            old_regime = self._current_regime.name
            self._current_regime = next(r for r in VIX_REGIMES if r.name == "EXTREME")
            if old_regime != "EXTREME":
                self._apply_regime_to_risk_manager()
            if not self._stale_alert_fired:
                try:
                    from core.alerting import alert_vix_stale
                    alert_vix_stale(minutes_stale, self._current_vix)
                except Exception as e:
                    logger.error(f"[VixManager] Failed to send stale alert: {e}")
                self._stale_alert_fired = True

    def _apply_regime_to_risk_manager(self) -> None:
        try:
            from core.risk_manager import risk_manager
            risk_manager.max_open_positions = self._current_regime.max_open_positions
            risk_manager.trailing_profit_pct = self._current_regime.trailing_profit
        except Exception as e:
            logger.error(f"[VixManager] Risk manager update failed: {e}")

    async def start(self) -> None:
        self._running = True
        await self._update_vix()
        logger.info(
            f"[VixManager] Started | VIX: {self._current_vix:.2f} | "
            f"Regime: {self._current_regime.name} | Source: {self._last_fetch_source}"
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
                    await self._update_vix()
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
    def is_stale(self) -> bool:
        return self._is_stale

    @property
    def fetch_source(self) -> str:
        return self._last_fetch_source

    @property
    def minutes_since_last_success(self) -> float:
        return self._minutes_since_last_success()

    @property
    def last_updated(self) -> str:
        if self._last_updated:
            return self._last_updated.strftime("%H:%M:%S")
        return "Never"


# --- Global singleton ---------------------------------------------------------
vix_manager = VixManager(update_interval=60)
