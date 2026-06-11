# Add these routes to dashboard/api.py
# Paste before the @app.on_event("startup") line

STRATEGY_API_ROUTES = '''

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
        atm   = ctx._oi_snapshot.atm_strike if hasattr(ctx, "_oi_snapshot") and ctx._oi_snapshot else nifty
        regime = ctx.regime if hasattr(ctx, "regime") else "range"
        or_snap = ctx._or if hasattr(ctx, "_or") else None
        or_width = (or_snap.high - or_snap.low) if or_snap and or_snap.locked else None
        max_pain = ctx._oi_snapshot.max_pain_strike if hasattr(ctx, "_oi_snapshot") and ctx._oi_snapshot else None

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
'''

print(STRATEGY_API_ROUTES)
