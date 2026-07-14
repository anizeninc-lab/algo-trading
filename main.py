# main.py
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import subprocess
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler("logs/trading.log", maxBytes=50*1024*1024, backupCount=7, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

def free_port(port: int):
    """Kill any process occupying the given port before startup."""
    import platform
    system = platform.system()
    try:
        if system == "Windows":
            cmd = f'for /f "tokens=5" %a in (\'netstat -aon | find ":{port} "\') do taskkill /F /PID %a'
        else:  # Linux/OCI
            cmd = f'lsof -ti:{port} | xargs -r kill -9'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            logger.info(f"Port {port} was in use — process killed successfully.")
        else:
            logger.info(f"Port {port} is free. Proceeding.")
    except Exception as e:
        logger.warning(f"Could not auto-free port {port}: {e}")

def load_config() -> dict:
    config_path = Path("configs/saviour_combo.json")
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    logger.warning("No config file found. Using defaults.")
    return {}

async def run_dashboard():
    config = uvicorn.Config(
        app="dashboard.api:app",
        host="0.0.0.0",  # ✅ PUBLIC ACCESS (was 127.0.0.1)
        port=8081,
        log_level="warning",
        reload=False,
    )
    server = uvicorn.Server(config)
    logger.info("🚀 Dashboard LIVE: http://92.4.90.188:8081")  # ✅ PUBLIC URL
    await server.serve()

async def run_strategies(config: dict):
    from brokers import get_broker
    from core.vix_manager import vix_manager
    from strategy.saviour_combo import SaviourCombo, SaviourComboConfig
    from strategy.survivor import SurvivorConfig
    from strategy.wave_extractor import WaveConfig

    broker = get_broker()
    # Share broker with dashboard API for funds endpoint
    import dashboard.api as dashboard_api
    dashboard_api.broker_ref = broker

    from core.session_planner import session_planner
    session_planner.start()
    logger.info("[main] Session planner started")

    # Give VIX manager the broker reference so it can use the official Upstox
    # India VIX quote as its primary source (NSE scrape is now fallback only).
    # Safe even though the broker may not have called login() yet -- fetches
    # fall through to the scrape until the first strategy logs in.
    vix_manager.set_broker(broker)

    # Start VIX manager first
    await vix_manager.start()
    logger.info(
        f"VIX Manager started | VIX: {vix_manager.current_vix:.2f} | Regime: {vix_manager.regime_name}"
    )

    wave_cfg = WaveConfig(
        option_symbol=config.get("option_symbol", ""),
        sell_gap=config.get("sell_gap", 20.0),
        buy_gap=config.get("buy_gap", 20.0),
        quantity=config.get("wave_quantity", 65),
        cool_off_time=config.get("cool_off_time", 5.0),
        max_net_position=config.get("max_net_position", 4),
    )

    survivor_cfg = SurvivorConfig(
        symbol_initials=config.get("symbol_initials", "NIFTY26MAR25"),
        pe_gap=config.get("pe_gap", 15.0),
        ce_gap=config.get("ce_gap", 15.0),
        pe_symbol_gap=config.get("pe_symbol_gap", 300.0),
        ce_symbol_gap=config.get("ce_symbol_gap", 300.0),
        pe_reset_gap=config.get("pe_reset_gap", 90.0),
        ce_reset_gap=config.get("ce_reset_gap", 90.0),
        pe_quantity=config.get("pe_quantity", 65),
        ce_quantity=config.get("ce_quantity", 65),
        pe_start=config.get("pe_start", 0.0),
        ce_start=config.get("ce_start", 0.0),
        min_price_to_sell=config.get("min_price_to_sell", 15.0),
    )

    # ── BankNifty Survivor Config (PAPER MODE always) ──────────────────────
    from core.auto_config import get_nearest_monthly_expiry
    from datetime import date as _date
    _bn_exp = get_nearest_monthly_expiry(_date.today())
    _bn_default = f"BANKNIFTY{_bn_exp.strftime('%d%b%y').upper()}"
    bn_symbol_initials = config.get("bn_symbol_initials") or _bn_default
    banknifty_cfg = SurvivorConfig(
        symbol_initials      = bn_symbol_initials,
        pe_gap               = 20.0,
        ce_gap               = 20.0,
        pe_symbol_gap        = 500.0,
        ce_symbol_gap        = 500.0,
        pe_reset_gap         = 150.0,
        ce_reset_gap         = 150.0,
        pe_quantity          = 15,
        ce_quantity          = 15,
        pe_start             = 0.0,
        ce_start             = 0.0,
        min_price_to_sell    = 50.0,
        strike_interval      = 100.0,
        hedge_gap            = 300.0,    # 3 strikes (100pt spacing) -- same ratio as Nifty's 150/50
        expiry_weekday       = 2,        # BankNifty expires Wednesday, not Tuesday
        instrument_name      = "BANKNIFTY",
        index_instrument_key = "NSE_INDEX|Nifty Bank",
        lot_size             = 15,
        paper_trade_override = True,   # ALWAYS paper for BankNifty
        strategy_name        = "bn_survivor",  # separate name — own DB records, own risk counters
    )

    # BankNifty paused on request — set ENABLE_BANKNIFTY=true in .env to re-enable
    _bn_enabled = os.getenv("ENABLE_BANKNIFTY", "false").lower() == "true"
    combo_cfg = SaviourComboConfig(
        wave=wave_cfg,
        survivor=survivor_cfg,
        banknifty_survivor=banknifty_cfg if _bn_enabled else None,
        max_combined_loss=config.get("max_combined_loss", -5000.0),
        auto_start_survivor=config.get("auto_start_survivor", True),
        wave_net_threshold=config.get("wave_net_threshold", 2),
        monitor_interval=config.get("monitor_interval", 10.0),
    )

    combo = SaviourCombo(broker, combo_cfg)
    dashboard_api.combo_ref = combo  # wire Greeks + kill switch
    await combo.start()

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Strategy runner cancelled. Stopping...")
        await combo.stop(reason="MANUAL")
        await vix_manager.stop()

async def main():
    # ── Time sync check ───────────────────────────────────────────────────
    try:
        import pytz, requests as _req
        from datetime import datetime as _dt
        ist = pytz.timezone("Asia/Kolkata")
        server_time = _dt.now(ist)
        # Compare against Upstox server time
        try:
            r = _req.get("https://api.upstox.com/v2/market/status", timeout=3)
            # Just use response time header as reference
            server_ts = r.headers.get("date", "")
            logger.info(f"[timesync] Server IST: {server_time.strftime('%H:%M:%S')} | Upstox header: {server_ts}")
        except Exception:
            logger.info(f"[timesync] Server IST: {server_time.strftime('%H:%M:%S')} (Upstox unreachable)")
        # Warn if server time looks wrong (outside 5 AM - 11 PM IST)
        h = server_time.hour
        if h < 5 or h > 23:
            logger.warning(f"[timesync] WARNING: Unusual server time {server_time} — check VPS clock")
    except Exception as te:
        logger.warning(f"[timesync] Time check failed: {te}")

    # Automatically update config files with nearest weekly/monthly Nifty expiries
    try:
        from auto_rollover import perform_rollover
        perform_rollover()
    except Exception as e:
        logger.error(f"Auto rollover skipped/failed: {e}")

    config = load_config()

    broker_name = os.getenv("BROKER_NAME", "")
    if not broker_name:
        logger.error("BROKER_NAME not set in .env file.")
        sys.exit(1)

    logger.info("🚀 Rahul Sharma Trading System STARTING")
    logger.info(f"Broker: {broker_name}")
    logger.info("📊 Dashboard: http://92.4.90.188:8081")  # ✅ PUBLIC URL
    logger.info("Press Ctrl+C to stop all strategies")

    await asyncio.gather(
        run_dashboard(),
        run_strategies(config),
        return_exceptions=True,
    )

if __name__ == "__main__":
    # Auto-free port 8081 (Windows only — on Linux PM2 handles this)
    import platform as _platform
    if _platform.system() == "Windows":
        free_port(8081)

    def _handle_sigterm(signum, frame):
        logger.info("SIGTERM received")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 System stopped by user.")
# main.py
import asyncio
