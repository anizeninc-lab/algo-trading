class TradeLogic:
    def __init__(self, bull_thresh, bear_thresh):
        self.bull_thresh    = bull_thresh
        self.bear_thresh    = bear_thresh
        self.current_signal = 0

    def evaluate(self, nn_value, nn_ma):
        action = None
        if nn_value > self.bull_thresh and nn_ma > 0 and self.current_signal != 1:
            action = "BUY_CE"
            self.current_signal = 1
        elif nn_value < self.bear_thresh and nn_ma < 0 and self.current_signal != -1:
            action = "BUY_PE"
            self.current_signal = -1
        elif self.current_signal == 1 and nn_value < 0:
            action = "EXIT_CE"
            self.current_signal = 0
        elif self.current_signal == -1 and nn_value > 0:
            action = "EXIT_PE"
            self.current_signal = 0
        return action