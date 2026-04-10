import logging
from pathlib import Path
import upstox_client
from ann_config_loader import cfg

log_path = Path(__file__).parent.parent / "logs" / "ann_orders.log"
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)

class ANNOrderManager:
    def __init__(self):
        config = upstox_client.Configuration()
        config.access_token = cfg["access_token"]
        client = upstox_client.ApiClient(config)
        self.order_api     = upstox_client.OrderApi(client)
        self.open_position = None
        self.entry_premium = 0.0

    def _get_atm_key(self, spot, opt_type):
        strike  = round(spot / cfg["strike_step"]) * cfg["strike_step"]
        initials = cfg["symbol_initials"]
        return f"{cfg['instrument_fo']}|{initials}{strike}{opt_type}"

    def _place(self, instrument_key, transaction):
        order = upstox_client.PlaceOrderRequest(
            quantity         = cfg["quantity"],
            product          = cfg["product"],
            validity         = "DAY",
            price            = 0,
            instrument_token = instrument_key,
            order_type       = "MARKET",
            transaction_type = transaction,
            disclosed_quantity = 0,
            trigger_price    = 0,
            is_amo           = False,
            tag              = cfg["ann_tag"]
        )
        resp = self.order_api.place_order(order)
        logging.info(f"{transaction} | {instrument_key} | order_id={resp.data.order_id}")
        return resp

    def buy_ce(self, spot):
        if self.open_position:
            return
        key = self._get_atm_key(spot, "CE")
        self._place(key, "BUY")
        self.open_position = {"type": "CE", "key": key}
        self.entry_premium = 0.0
        logging.info(f"ENTERED CE | spot={spot}")

    def buy_pe(self, spot):
        if self.open_position:
            return
        key = self._get_atm_key(spot, "PE")
        self._place(key, "BUY")
        self.open_position = {"type": "PE", "key": key}
        self.entry_premium = 0.0
        logging.info(f"ENTERED PE | spot={spot}")

    def exit_position(self, reason="SIGNAL"):
        if not self.open_position:
            return
        key = self.open_position["key"]
        self._place(key, "SELL")
        logging.info(f"EXITED {self.open_position['type']} | reason={reason}")
        self.open_position = None
        self.entry_premium = 0.0