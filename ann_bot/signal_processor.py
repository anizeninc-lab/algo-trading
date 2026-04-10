import numpy as np

class SignalProcessor:
    def __init__(self, q=1.0, r=1.0):
        self.kf_est      = 0.0
        self.kf_err      = 1.0
        self.q           = q
        self.r           = r
        self.raw_history = []

    def process(self, raw_pred):
        self.raw_history.append(raw_pred)
        if len(self.raw_history) > 500:
            self.raw_history.pop(0)
        arr = np.array(self.raw_history)
        std = np.std(arr) if np.std(arr) > 0 else 1.0
        z   = np.clip(raw_pred / std, -3.0, 3.0)
        p_err        = self.kf_err + self.q
        k_gain       = p_err / (p_err + self.r)
        self.kf_est += k_gain * (z - self.kf_est)
        self.kf_err  = (1 - k_gain) * p_err
        return self.kf_est