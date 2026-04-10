import sys
import json
import random
import datetime
import numpy as np

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from ann_config_loader import cfg
from ann_engine        import ANN
from features          import FeatureEngine
from signal_processor  import SignalProcessor
from trade_logic       import TradeLogic
from order_manager     import ANNOrderManager

import upstox_client
from upstox_client import MarketDataStreamer

ann    = ANN(
    input_size    = 5,
    hidden_layers = cfg["hidden_layers"],
    nodes         = cfg["nodes_per_layer"],
    activation    = cfg["activation"],
    lr            = cfg["learning_rate"],
    l2            = cfg["l2_reg"],
    dropout       = cfg["dropout_rate"]
)
feats  = FeatureEngine(norm_len=cfg["norm_lookback"])
sig    = SignalProcessor(q=cfg["kalman_q"], r=cfg["kalman_r"])
logic  = TradeLogic(
    bull_thresh = cfg["bull_threshold"],
    bear_thresh = cfg["bear_threshold"]
)
orders = ANNOrderManager()

ohlcv_buffer = []
ma_window    = []
trades_today = 0

def past_cutoff():
    return datetime.datetime.now().strftime("%H:%M") >= cfg["no_trade_after"]

def on_tick(msg):
    global ohlcv_buffer, ma_window, trades_today

    try:
        data = json.loads(msg)
    except Exception:
        return

    feed = data.get("feeds", {}).get(cfg["instrument_index"], {})
    if not feed:
        return

    bar = {
        "open":   feed.get("open_price", 0),
        "high":   feed.get("high_price", 0),
        "low":    feed.get("low_price",  0),
        "close":  feed.get("ltp",        0),
        "volume": feed.get("volume",     0),
    }
    if bar["close"] == 0:
        return

    ohlcv_buffer.append(bar)
    if len(ohlcv_buffer) > 1000:
        ohlcv_buffer.pop(0)
    if len(ohlcv_buffer) < 100:
        print(f"\r[ANN] Warming up... {len(ohlcv_buffer)}/100 bars", end="")
        return

    spot = bar["close"]

    # SL / TP check
    if orders.open_position:
        try:
            upstox_config = upstox_client.Configuration()
            upstox_config.access_token = cfg["access_token"]
            mq_api = upstox_client.MarketQuoteApi(
                upstox_client.ApiClient(upstox_config)
            )
            key   = orders.open_position["key"]
            quote = mq_api.get_full_market_quote([key], "v2")
            curr  = quote.data[key].last_price
            if orders.entry_premium == 0:
                orders.entry_premium = curr
            pnl = (curr - orders.entry_premium) / orders.entry_premium
            if pnl <= -cfg["sl_pct"]:
                orders.exit_position(reason="SL_HIT")
                return
            if pnl >= cfg["tp_pct"]:
                orders.exit_position(reason="TP_HIT")
                return
        except Exception as e:
            print(f"\n[ANN] SL/TP check error: {e}")

    # Training
    if len(ohlcv_buffer) > 50:
        t       = random.randint(1, min(len(ohlcv_buffer) - 1,
                                        cfg["sampling_window"]))
        x_train = feats.compute(ohlcv_buffer[:-t])
        target  = 1.0 if ohlcv_buffer[-t]["close"] >= ohlcv_buffer[-t]["open"] else -1.0
        ann.train(x_train, target)

    # Inference
    x        = feats.compute(ohlcv_buffer)
    raw      = ann.predict(x)
    nn_value = sig.process(raw)

    ma_window.append(nn_value)
    if len(ma_window) > cfg["ma_period"]:
        ma_window.pop(0)
    nn_ma = np.mean(ma_window)

    pos_label = orders.open_position["type"] if orders.open_position else "FLAT"
    print(f"\r[ANN] NN={nn_value:+.3f}  MA={nn_ma:+.3f}  "
          f"Pos={pos_label}  Trades={trades_today}/{cfg['max_trades_day']}  "
          f"Spot={spot:.0f}   ", end="")

    # EOD exit
    if past_cutoff():
        if orders.open_position:
            orders.exit_position(reason="EOD_CUTOFF")
        return

    if trades_today >= cfg["max_trades_day"]:
        return

    # Signal
    action = logic.evaluate(nn_value, nn_ma)
    if action == "BUY_CE":
        orders.buy_ce(spot)
        trades_today += 1
    elif action == "BUY_PE":
        orders.buy_pe(spot)
        trades_today += 1
    elif action in ("EXIT_CE", "EXIT_PE"):
        orders.exit_position(reason="SIGNAL_CROSS")

def on_open():
    print("[ANN Bot] Feed connected — receiving ticks")

def on_error(e):
    print(f"\n[ANN Bot] Feed error: {e}")

print("[ANN Bot] Starting...")
upstox_config = upstox_client.Configuration()
upstox_config.access_token = cfg["access_token"]

streamer = MarketDataStreamer(
    upstox_config,
    [cfg["instrument_index"]],
    "full"
)
streamer.on("message", on_tick)
streamer.on("open",    on_open)
streamer.on("error",   on_error)
streamer.connect()