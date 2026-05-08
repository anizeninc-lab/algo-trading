# dashboard/api.py
import asyncio
import json
import logging
import os
import re
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware

from core.event_bus import event_bus
from core.state_store import state_store, StrategyState
from core.trade_log import trade_logger
from core.vix_manager import vix_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Trading Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ws_clients: list[WebSocket] = []


@app.websocket("/ws/updates")
async def websocket_updates(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    logger.info(f"Dashboard client connected ({len(_ws_clients)} total)")
    try:
        while True:
            await asyncio.sleep(1)
            payload = _build_payload()
            await ws.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        _ws_clients.remove(ws)
        logger.info("Dashboard client disconnected")


def _build_payload() -> dict:
    summary    = state_store.get_global_summary()
    strategies = {}

    for name, status in state_store.get_all_strategies().items():
        strategies[name] = {
            "state":          status.state,
            "position":       status.position,
            "realised_pnl":   status.realised_pnl,
            "unrealised_pnl": status.unrealised_pnl,
            "open_orders":    status.open_orders,
            "total_trades":   status.total_trades,
            "open_trades":    status.open_trades,
            "closed_trades":  status.closed_trades,
            "last_signal":    status.last_signal,
            "last_updated":   status.last_updated,
            "error_message":  status.error_message,
            "pnl_history":    status.pnl_history,
        }

    summary["paper_trade"] = os.getenv("PAPER_TRADE", "false").lower() == "true"

    return {
        "timestamp":  datetime.now().isoformat(),
        "global":     summary,
        "strategies": strategies,
        "vix": {
            "value":   vix_manager.current_vix,
            "regime":  vix_manager.regime_name,
            "updated": vix_manager.last_updated,
            "halt":    vix_manager.should_halt(),
            "params":  vix_manager.get_params(),
        },
        "market": state_store.get_market_data(),
    }


@app.get("/api/global/summary")
async def get_global_summary():
    return state_store.get_global_summary()


@app.get("/api/strategy/{name}/status")
async def get_strategy_status(name: str):
    status = state_store.get_strategy(name)
    if not status:
        return {"error": f"Strategy '{name}' not found"}
    return {
        "name":           status.name,
        "state":          status.state,
        "position":       status.position,
        "realised_pnl":   status.realised_pnl,
        "unrealised_pnl": status.unrealised_pnl,
        "open_orders":    status.open_orders,
        "total_trades":   status.total_trades,
        "last_signal":    status.last_signal,
        "error_message":  status.error_message,
    }


@app.post("/api/strategy/{name}/stop")
async def stop_strategy(name: str):
    status = state_store.get_strategy(name)
    if not status:
        return {"error": f"Strategy '{name}' not found"}
    if status.state != StrategyState.RUNNING:
        return {"error": f"Strategy '{name}' is not running"}
    state_store.update_state(name, StrategyState.STOPPED)
    logger.info(f"Strategy '{name}' stopped via dashboard")
    return {"status": "stopped", "strategy": name}


@app.post("/api/strategy/{name}/reset")
async def reset_strategy(name: str):
    status = state_store.get_strategy(name)
    if not status:
        return {"error": f"Strategy '{name}' not found"}
    if status.state != StrategyState.ERROR:
        return {"error": f"Strategy '{name}' is not in ERROR state"}
    state_store.update_state(name, StrategyState.IDLE)
    logger.info(f"Strategy '{name}' reset via dashboard")
    return {"status": "reset", "strategy": name}


@app.get("/api/trades")
async def get_trades(
    strategy: str = None,
    status:   str = None,
    limit:    int = 200
):
    trades = trade_logger.get_trades(
        strategy=strategy,
        status=status,
        limit=limit
    )
    for t in trades:
        if t.get("status") == "OPEN":
            t["unrealised_pnl"] = pnl_registry.get(t["id"], 0.0)
            t["current_ltp"] = ltp_registry.get(t["id"], 0.0)
    return {"trades": trades, "count": len(trades)}


@app.get("/api/trades/summary")
async def get_trades_summary(strategy: str = None):
    return trade_logger.get_pnl_summary(strategy=strategy)


# Global broker reference — set by main.py on startup
broker_ref = None

@app.get("/api/funds")
async def get_funds():
    """Get live account balance from Upstox."""
    global broker_ref
    if broker_ref is None:
        return {"available": 0.0, "used": 0.0, "total": 0.0, "source": "unavailable"}
    try:
        margin = await broker_ref.get_margin()
        return {
            "available": round(margin.available, 2),
            "used": round(margin.used, 2),
            "total": round(margin.total, 2),
            "source": "upstox"
        }
    except Exception as e:
        logger.error(f"get_funds failed: {e}")
        return {"available": 0.0, "used": 0.0, "total": 0.0, "source": "error"}

@app.get("/api/events")
async def get_events(
    strategy:   str = None,
    event_type: str = None,
    limit:      int = 100
):
    events = trade_logger.get_events(
        strategy=strategy,
        event_type=event_type,
        limit=limit
    )
    return {"events": events, "count": len(events)}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "db":     trade_logger.health(),
        "time":   datetime.now().isoformat(),
    }


FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")


@app.on_event("startup")
async def startup():
    asyncio.create_task(event_bus.start())
    logger.info("Dashboard API started. Event bus running.")



# ── Unrealised PnL Registry ───────────────────────────────────────────────────
# Strategies call: pnl_registry[trade_id] = unrealised_pnl
pnl_registry: dict = {}
ltp_registry: dict = {}  # trade_id -> current LTP

@app.post("/api/toggle-paper")
async def toggle_paper_mode():
    try:
        env_path = Path(".env")
        if not env_path.exists():
            return {"error": ".env file not found"}
        env_text = env_path.read_text()
        current = os.getenv("PAPER_TRADE", "false").lower() == "true"
        new_val = "false" if current else "true"
        if "PAPER_TRADE=" in env_text:
            env_text = re.sub(r"PAPER_TRADE=.*", f"PAPER_TRADE={new_val}", env_text)
        else:
            env_text += f"\nPAPER_TRADE={new_val}\n"
        env_path.write_text(env_text)
        os.environ["PAPER_TRADE"] = new_val
        mode = "PAPER" if new_val == "true" else "LIVE"
        logger.info(f"Trading mode switched to: {mode}")
        os.system("pm2 restart all")
        return {"success": True, "paper_trade": new_val == "true", "mode": mode}
    except Exception as e:
        logger.error(f"toggle_paper_mode error: {e}")
        return {"error": str(e)}
