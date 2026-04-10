# dashboard/api.py
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx
import contextlib # Add this line
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.event_bus import event_bus
from core.state_store import StrategyState, state_store
from core.trade_log import trade_logger
from core.vix_manager import vix_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # This runs when the API starts
    asyncio.create_task(event_bus.start())
    logger.info("Dashboard API started. Event bus running.")
    yield
    # This runs when the API stops (optional)
    logger.info("Dashboard API shutting down.")

app = FastAPI(title="Trading Dashboard API", lifespan=lifespan)

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
    summary = state_store.get_global_summary()
    strategies = {}

    for name, status in state_store.get_all_strategies().items():
        strategies[name] = {
            "state": status.state,
            "position": status.position,
            "realised_pnl": status.realised_pnl,
            "unrealised_pnl": status.unrealised_pnl,
            "open_orders": status.open_orders,
            "total_trades": status.total_trades,
            "open_trades": status.open_trades,
            "closed_trades": status.closed_trades,
            "last_signal": status.last_signal,
            "last_updated": status.last_updated,
            "error_message": status.error_message,
            "pnl_history": status.pnl_history,
        }

    summary["paper_trade"] = os.getenv("PAPER_TRADE", "false").lower() == "true"

    return {
        "timestamp": datetime.now().isoformat(),
        "global": summary,
        "strategies": strategies,
        "vix": {
            "value": vix_manager.current_vix,
            "regime": vix_manager.regime_name,
            "updated": vix_manager.last_updated,
            "halt": vix_manager.should_halt(),
            "params": vix_manager.get_params(),
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
        "name": status.name,
        "state": status.state,
        "position": status.position,
        "realised_pnl": status.realised_pnl,
        "unrealised_pnl": status.unrealised_pnl,
        "open_orders": status.open_orders,
        "total_trades": status.total_trades,
        "last_signal": status.last_signal,
        "error_message": status.error_message,
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
async def get_trades(strategy: str = None, status: str = None, limit: int = 200):
    trades = trade_logger.get_trades(strategy=strategy, status=status, limit=limit)
    return {"trades": trades, "count": len(trades)}


@app.get("/api/trades/summary")
async def get_trades_summary(strategy: str = None):
    return trade_logger.get_pnl_summary(strategy=strategy)


@app.get("/api/events")
async def get_events(strategy: str = None, event_type: str = None, limit: int = 100):
    events = trade_logger.get_events(
        strategy=strategy, event_type=event_type, limit=limit
    )
    return {"events": events, "count": len(events)}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "db": trade_logger.health(),
        "time": datetime.now().isoformat(),
    }


@app.post("/api/emergency/kill")
async def emergency_kill():
    """
    Emergency Kill Switch:
    Stops all running strategies immediately and logs the event.
    Use Upstox app to cancel/square off open orders after this.
    """
    logger.warning("⚠ EMERGENCY KILL SWITCH TRIGGERED via dashboard")
    killed = []

    for name, status in state_store.get_all_strategies().items():
        if status.state == StrategyState.RUNNING:
            state_store.update_state(name, StrategyState.STOPPED)
            killed.append(name)
            logger.warning(f"Emergency stop: strategy '{name}' halted")

    return {
        "status": "KILLED",
        "strategies_stopped": killed,
        "time": datetime.now().isoformat(),
        "message": "All strategies stopped. Use Upstox app to square off open positions.",
    }


@app.get("/api/account/balance")
async def get_account_balance():
    """
    Fetch live account balance from Upstox.
    Returns equity fund details including allocated capital and current balance.
    """
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        return {"error": "No access token found. Run get_token.py first."}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(
                "https://api.upstox.com/v2/user/get-funds-and-margin",
                params={"segment": "SEC"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
        if res.status_code == 200:
            body = res.json()
            equity = body.get("data", {}).get("equity", {})
            return {
                "allocated_capital": equity.get("payin_amount", 0),        # funds added today
                "current_balance":   equity.get("available_margin", 0),    # usable balance now
                "used_margin":       equity.get("used_margin", 0),          # capital deployed
                "total_balance":     equity.get("net_available_margin", 0), # net balance
                "raw": equity,
            }
        else:
            return {"error": f"Upstox API error {res.status_code}: {res.text}"}
    except Exception as e:
        return {"error": str(e)}


FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")
