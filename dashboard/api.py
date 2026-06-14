# dashboard/api.py
import asyncio
import json
import logging
import os
import re
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware

from core.event_bus import event_bus
from core.state_store import state_store, StrategyState
from core.trade_log import trade_logger
from core.vix_manager import vix_manager

# ── New: market context + astro ───────────────────────────────────────────────
try:
    from core.market_context import market_context
    _MARKET_CONTEXT_AVAILABLE = True
except ImportError:
    _MARKET_CONTEXT_AVAILABLE = False

try:
    from core.astro_calendar import astro_calendar
    _ASTRO_AVAILABLE = True
except ImportError:
    _ASTRO_AVAILABLE = False

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

    # ── Market context (Layer 1) ───────────────────────────────────────────────
    market_ctx = None
    if _MARKET_CONTEXT_AVAILABLE:
        try:
            from core.strategy_filter import strategy_filter
            market_ctx = strategy_filter.context_summary()
        except Exception:
            pass

    # ── Astro calendar ────────────────────────────────────────────────────────
    astro = None
    if _ASTRO_AVAILABLE:
        try:
            today = astro_calendar.today()
            week  = astro_calendar.week_ahead()
            astro = {
                "today":      today.to_dict() if today else None,
                "week_ahead": [d.to_dict() for d in week],
            }
        except Exception:
            pass

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
        "market":      state_store.get_market_data(),
        "market_ctx":  market_ctx,   # NEW: PCR, regime, OI, opening range
        "astro":       astro,        # NEW: astro day strength + windows
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


@app.post("/api/killswitch")
async def kill_switch(flatten: bool = True):
    """
    Emergency kill switch — halts all trading immediately.
    If flatten=True, closes all open positions at market price.
    """
    try:
        from core.risk_manager import risk_manager
        from core.alerting import send_telegram, LEVEL_CRITICAL

        # 1. Halt risk manager — blocks all new trades
        risk_manager._system_halted = True
        risk_manager._halt_reason   = "KILL SWITCH ACTIVATED"
        risk_manager._save_state()

        # 2. Close all positions if flatten=True
        closed = 0
        if flatten and combo_ref is not None:
            try:
                await combo_ref.survivor._close_all_positions()
                closed += len(combo_ref.survivor._open_trades_data)
            except Exception as e:
                logger.error(f"Kill switch: survivor close failed: {e}")
            try:
                if combo_ref.bn_survivor:
                    await combo_ref.bn_survivor._close_all_positions()
            except Exception as e:
                logger.error(f"Kill switch: bn_survivor close failed: {e}")

        # 3. Alert
        send_telegram(
            f"KILL SWITCH ACTIVATED\nFlatten: {flatten}\nAll new trading HALTED\nRestart bot to resume",
            LEVEL_CRITICAL
        )

        return {"status": "ok", "halted": True, "flatten": flatten, "positions_closed": closed}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/killswitch/reset")
async def kill_switch_reset():
    """Reset kill switch — re-enables trading. Use carefully."""
    try:
        from core.risk_manager import risk_manager
        from core.alerting import send_telegram, LEVEL_WARNING
        risk_manager._system_halted = False
        risk_manager._halt_reason   = ""
        risk_manager._save_state()
        send_telegram("⚠️ Kill switch RESET — trading re-enabled", LEVEL_WARNING)
        return {"status": "ok", "halted": False}
    except Exception as e:
        return {"status": "error", "error": str(e)}

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
            cached_pnl = pnl_registry.get(t["id"], None)
            cached_ltp = ltp_registry.get(t["id"], None)
            t["unrealised_pnl"] = cached_pnl if cached_pnl is not None else 0.0
            t["current_ltp"]    = cached_ltp if cached_ltp is not None else 0.0
            t["ltp_fresh"]      = cached_ltp is not None and cached_ltp > 0
    return {"trades": trades, "count": len(trades)}
@app.get("/api/trades/summary")
async def get_trades_summary(strategy: str = None):
    return trade_logger.get_pnl_summary(strategy=strategy)

@app.get("/api/banknifty/trades")
async def get_banknifty_trades(status: str = None, limit: int = 200):
    """Returns BankNifty paper trades separately from Nifty trades."""
    trades = trade_logger.get_trades(strategy="bn_survivor", status=status, limit=limit)
    for t in trades:
        if t.get("status") == "OPEN":
            t["unrealised_pnl"] = pnl_registry.get(t["id"], 0.0)
            t["current_ltp"]    = ltp_registry.get(t["id"], 0.0)
            t["ltp_fresh"]      = ltp_registry.get(t["id"], 0.0) > 0
    return {"trades": trades, "count": len(trades)}

@app.get("/api/banknifty/summary")
async def get_banknifty_summary():
    """Returns BankNifty paper P&L summary."""
    summary = trade_logger.get_pnl_summary(strategy="bn_survivor")
    return summary

@app.get("/api/alerts")
async def get_alerts():
    """Returns last 50 critical alerts for dashboard display."""
    return {"alerts": list(reversed(alert_store))}

@app.post("/api/alerts/clear")
async def clear_alerts():
    alert_store.clear()
    return {"ok": True}

@app.get("/api/broker-positions")
async def get_broker_positions():
    """
    Returns live positions from Upstox broker.
    Used to reconcile dashboard P&L with Upstox app.
    """
    try:
        if broker_ref is None:
            return {"positions": [], "error": "broker not connected"}
        positions = await broker_ref.get_positions()
        result = []
        for p in positions:
            if p.quantity == 0:
                continue  # skip flat positions
            result.append({
                "symbol":        p.symbol,
                "quantity":      p.quantity,
                "average_price": p.average_price,
                "ltp":           p.last_price,
                "pnl":           p.pnl,
                "pnl_pct":       round((p.pnl / (p.average_price * abs(p.quantity)) * 100), 2)
                                 if p.average_price and p.quantity else 0,
            })
        total_pnl = sum(p["pnl"] for p in result)
        return {
            "positions":  result,
            "count":      len(result),
            "total_pnl":  round(total_pnl, 2),
        }
    except Exception as e:
        return {"positions": [], "error": str(e)}
@app.get("/api/trades/performance")
async def get_trades_performance():
    """Returns gross P&L, charges, net P&L, margin used, and ROI."""
    trades = trade_logger.get_trades(status="CLOSED", limit=500)
    
    gross_pnl = 0.0
    total_margin = 0.0
    total_brokerage = 0.0
    total_stt = 0.0
    total_exchange = 0.0
    total_gst = 0.0
    total_stamp = 0.0

    for t in trades:
        entry = t.get("entry_price") or 0
        exit_p = t.get("exit_price") or 0
        qty = t.get("quantity") or 0
        pnl = t.get("realised_pnl") or 0
        order_type = t.get("order_type") or "SELL"

        gross_pnl += pnl

        sell_price = entry if order_type == "SELL" else exit_p
        buy_price  = exit_p if order_type == "SELL" else entry
        sell_turnover = sell_price * qty
        buy_turnover  = buy_price  * qty

        # Margin estimate (5x premium)
        total_margin += entry * qty * 5

        # Brokerage: ₹20 per order, 2 orders per trade
        brokerage = 40.0
        total_brokerage += brokerage

        # STT: 0.1% on sell turnover only
        stt = round(sell_turnover * 0.001, 2)
        total_stt += stt

        # Exchange charges: 0.05% of total turnover
        exchange = round((sell_turnover + buy_turnover) * 0.0005, 2)
        total_exchange += exchange

        # GST: 18% on (brokerage + exchange)
        gst = round((brokerage + exchange) * 0.18, 2)
        total_gst += gst

        # Stamp duty: 0.003% on buy turnover
        stamp = round(buy_turnover * 0.00003, 2)
        total_stamp += stamp

    total_charges = round(total_brokerage + total_stt + total_exchange + total_gst + total_stamp, 2)
    net_pnl = round(gross_pnl - total_charges, 2)
    roi_on_margin = round((net_pnl / total_margin * 100), 2) if total_margin > 0 else 0.0

    return {
        "gross_pnl": round(gross_pnl, 2),
        "total_charges": total_charges,
        "net_pnl": net_pnl,
        "total_margin": round(total_margin, 2),
        "roi_on_margin": roi_on_margin,
        "charges_breakdown": {
            "brokerage": round(total_brokerage, 2),
            "stt": round(total_stt, 2),
            "exchange": round(total_exchange, 2),
            "gst": round(total_gst, 2),
            "stamp_duty": round(total_stamp, 2),
        },
        "trade_count": len(trades),
    }


# Global broker reference — set by main.py on startup
broker_ref = None
combo_ref  = None   # reference to SaviourCombo instance for kill switch

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
    # Start market context engine if available
    if _MARKET_CONTEXT_AVAILABLE:
        try:
            market_context.start()
            logger.info("MarketContextEngine started from dashboard startup")
        except Exception as e:
            logger.warning(f"MarketContextEngine start failed: {e}")
    logger.info("Dashboard API started. Event bus running.")


# ── Unrealised PnL Registry ───────────────────────────────────────────────────
pnl_registry: dict = {}
ltp_registry: dict = {}

# In-memory alert store — last 50 critical alerts
from collections import deque
alert_store: deque = deque(maxlen=50)


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

# ── ADD THESE TWO ROUTES TO dashboard/api.py ─────────────────────────────────
# Paste them just before the last @app.on_event("startup") line

@app.get("/api/token-info")
async def get_token_info():
    """Return masked token so frontend can decode expiry."""
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        return {"token": None, "error": "No token found"}
    return {"token": token}

@app.get("/api/session-plan")
async def get_session_plan():
    """Return today's session plan."""
    from pathlib import Path
    import json
    plan_path = Path(__file__).parent.parent / "configs" / "session_plan.json"
    try:
        with open(plan_path) as f:
            return json.load(f)
    except Exception:
        return {"is_ready": False, "error": "Session plan not found"}



# ── Strategy Recommender API ──────────────────────────────────────────────────
from core.strategy_recommender import get_recommendations
from fastapi import Body as _Body

@app.get("/api/strategy-recommendations")
async def get_strategy_recommendations():
    """Return ranked strategy recommendations based on live market data."""
    try:
        from core.market_context import market_context
        ctx = market_context
        nifty = state_store.get_market_data().get("nifty_price", 0) or 0
        vix   = vix_manager.current_vix or 16.0
        pcr   = ctx.pcr if hasattr(ctx, "pcr") else 1.0
        # Get ATM from state_store market data or auto_config
        market_data = state_store.get_market_data()
        atm = market_data.get("atm_strike") or market_data.get("nifty_price") or nifty
        regime = ctx.regime if hasattr(ctx, "regime") else "range"
        # Get opening range
        try:
            or_snap = ctx._or
            or_width = (or_snap.high - or_snap.low) if or_snap and or_snap.locked else None
        except Exception:
            or_width = None
        # Get max pain
        try:
            max_pain = ctx._oi_snapshot.max_pain_strike if ctx._oi_snapshot else None
        except Exception:
            max_pain = None

        recs = get_recommendations(
            nifty=nifty, vix=vix, pcr=pcr, atm=atm or nifty,
            regime=regime, or_width=or_width, max_pain=max_pain
        )
        return {
            "status": "ok",
            "nifty": nifty,
            "vix": vix,
            "pcr": pcr,
            "atm": atm,
            "regime": regime,
            "or_width": or_width,
            "max_pain": max_pain,
            "strategies": recs,
        }
    except Exception as e:
        logger.error(f"strategy-recommendations error: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "strategies": []}


@app.post("/api/strategy/deploy")
async def deploy_strategy(body: dict = _Body(...)):
    """Deploy a strategy by placing multiple option orders."""
    global broker_ref
    if broker_ref is None:
        return {"success": False, "error": "Broker not connected"}
    
    strategy_id = body.get("id", "")
    legs = body.get("legs", [])
    paper = os.getenv("PAPER_TRADE", "false").lower() == "true"
    
    if paper:
        logger.info(f"[PAPER] Strategy deploy: {strategy_id} | {len(legs)} legs")
        return {
            "success": True,
            "paper": True,
            "message": f"PAPER MODE: {strategy_id} would place {len(legs)} orders",
            "orders": [{"leg": l, "status": "paper_simulated"} for l in legs]
        }
    
    results = []
    try:
        from brokers.base import Order
        for leg in legs:
            # Find instrument key for this strike
            symbol = f"NSE_FO|{leg.get('strike', 0)}{leg.get('type', 'CE')}"
            order = Order(
                symbol=symbol,
                quantity=leg.get("qty", 50),
                order_type=leg.get("action", "SELL"),
                price=0,  # Market order
            )
            order_id = await broker_ref.place_order(order)
            results.append({"leg": leg, "order_id": order_id, "status": "placed"})
            logger.info(f"[STRATEGY] {strategy_id} | {leg['action']} {leg['strike']}{leg['type']} | Order: {order_id}")
        
        return {"success": True, "paper": False, "strategy": strategy_id, "orders": results}
    except Exception as e:
        logger.error(f"deploy_strategy error: {e}")
        return {"success": False, "error": str(e), "orders": results}
