import numpy as np

class ANN:
    def __init__(self, input_size, hidden_layers, nodes,
                 activation, lr, l2, dropout):
        self.lr         = lr
        self.l2         = l2
        self.dropout    = dropout
        self.activation = activation
        self.step       = 0

        sizes = [input_size + 1] + [nodes] * hidden_layers
        self.layers = []
        for i in range(len(sizes) - 1):
            self.layers.append({
                "w": np.random.uniform(-1, 1, (sizes[i], sizes[i+1])),
                "m": np.zeros((sizes[i], sizes[i+1])),
                "v": np.zeros((sizes[i], sizes[i+1]))
            })
        self.out = {
            "w": np.random.uniform(-1, 1, (nodes + 1, 1)),
            "m": np.zeros((nodes + 1, 1)),
            "v": np.zeros((nodes + 1, 1))
        }

    def _act(self, x):
        if self.activation == "tanh":
            return np.tanh(x)
        if self.activation == "relu":
            return np.where(x > 0, x, 0.01 * x)
        return 1 / (1 + np.exp(-np.clip(x, -500, 500)))

    def _dact(self, h):
        if self.activation == "tanh":
            return 1 - h ** 2
        if self.activation == "relu":
            return np.where(h > 0, 1.0, 0.01)
        return h * (1 - h)

    def _forward(self, x, training=False):
        h = np.append(x, 1.0)
        self.hs = [h]
        for L in self.layers:
            u = h @ L["w"]
            h = self._act(u)
            if training and self.dropout > 0:
                mask = np.random.rand(*h.shape) > self.dropout
                h = h * mask / (1 - self.dropout)
            h = np.append(h, 1.0)
            self.hs.append(h)
        return (self.hs[-1] @ self.out["w"])[0]

    def _adam(self, layer, grad, b1=0.9, b2=0.999, eps=1e-8):
        layer["m"] = b1 * layer["m"] + (1 - b1) * grad
        layer["v"] = b2 * layer["v"] + (1 - b2) * grad ** 2
        mh = layer["m"] / (1 - b1 ** self.step)
        vh = layer["v"] / (1 - b2 ** self.step)
        layer["w"] -= self.lr * mh / (np.sqrt(vh) + eps)
        layer["w"] -= self.l2 * layer["w"]

    def train(self, x, target):
        self.step += 1
        pred  = self._forward(x, training=True)
        err   = pred - target
        dout  = err * self.hs[-1][:, None]
        delta = err * self.out["w"][:-1, 0]
        self._adam(self.out, dout)
        for i in reversed(range(len(self.layers))):
            h      = self.hs[i]
            act_h  = self.hs[i + 1][:-1]
            d_act  = self._dact(act_h) * delta
            dw     = h[:, None] * d_act[None, :]
            self._adam(self.layers[i], dw)
            if i > 0:
                delta = d_act @ self.layers[i]["w"][:-1].T

    def predict(self, x):
        return self._forward(x, training=False)