import numpy as np
import pandas as pd

class FeatureEngine:
    def __init__(self, norm_len=500):
        self.norm_len = norm_len

    def _zscore(self, series):
        s    = pd.Series(series)
        mean = s.rolling(self.norm_len, min_periods=10).mean()
        std  = s.rolling(self.norm_len, min_periods=10).std()
        return ((s - mean) / std.replace(0, 1)).fillna(0).values

    def compute(self, ohlcv_bars):
        if len(ohlcv_bars) < 30:
            return np.zeros(5)
        df = pd.DataFrame(ohlcv_bars)
        c  = df["close"]
        h  = df["high"]
        l  = df["low"]
        v  = df["volume"]

        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
        loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
        rsi   = 100 - (100 / (1 + gain / loss.replace(0, 1e-9)))

        tp  = (h + l + c) / 3
        cci = (tp - tp.rolling(20, min_periods=1).mean()) / (
              0.015 * tp.rolling(20, min_periods=1).std().replace(0, 1))

        sma   = c.rolling(20, min_periods=1).mean()
        std20 = c.rolling(20, min_periods=1).std()
        upper = sma + 2 * std20
        lower = sma - 2 * std20
        pctb  = (c - lower) / (upper - lower).replace(0, 1)

        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        hist  = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

        tp_v   = (h + l + c) / 3
        mf     = tp_v * v
        pos_mf = mf.where(tp_v > tp_v.shift(1), 0).rolling(14, min_periods=1).sum()
        neg_mf = mf.where(tp_v < tp_v.shift(1), 0).rolling(14, min_periods=1).sum()
        mfi    = 100 - (100 / (1 + pos_mf / neg_mf.replace(0, 1e-9)))

        return np.array([
            self._zscore(rsi.values)[-1],
            self._zscore(cci.values)[-1],
            self._zscore(pctb.values)[-1],
            self._zscore(hist.values)[-1],
            self._zscore(mfi.values)[-1],
        ])