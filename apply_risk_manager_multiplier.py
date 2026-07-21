path = "/home/ubuntu/trading-algo/core/risk_manager.py"

with open(path, "r") as f:
    content = f.read()

replacements = []

# ── 1. check_capital_limit ──────────────────────────────────────────────
old = '''    def check_capital_limit(self, order_type: str = "SELL", strategy_name: str = "") -> tuple[bool, str]:
        """
        HARDCODED CAPITAL GUARD — checks if adding one more trade
        would exceed the ₹1,50,000 capital limit.
        Returns (True, "") if capital is available.
        Returns (False, reason) if limit would be breached.
        """
        margin_needed = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT'''
new = '''    def check_capital_limit(self, order_type: str = "SELL", strategy_name: str = "", multiplier: int = 1) -> tuple[bool, str]:
        """
        HARDCODED CAPITAL GUARD — checks if adding one more trade
        would exceed the ₹1,50,000 capital limit.
        multiplier: number of lots this trade represents (e.g. overshoot scaling
        in survivor.py). Default 1 preserves existing behavior for all callers
        that don't pass it explicitly.
        Returns (True, "") if capital is available.
        Returns (False, reason) if limit would be breached.
        """
        margin_needed = (MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT) * multiplier'''
replacements.append((old, new))

# ── 2. register_capital ─────────────────────────────────────────────────
old = '''    def register_capital(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Reserve capital when a new trade is opened."""
        margin = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT'''
new = '''    def register_capital(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        """Reserve capital when a new trade is opened. multiplier = lots this trade represents."""
        margin = (MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT) * multiplier'''
replacements.append((old, new))

# ── 3. release_capital ──────────────────────────────────────────────────
old = '''    def release_capital(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Release capital when a trade is closed."""
        margin  = MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT'''
new = '''    def release_capital(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        """Release capital when a trade is closed. multiplier = lots this trade represented."""
        margin  = (MARGIN_PER_SELL_LOT if order_type == "SELL" else MARGIN_PER_BUY_LOT) * multiplier'''
replacements.append((old, new))

# ── 4. register_trade ───────────────────────────────────────────────────
old = '''    def register_trade(self, strategy_name: str, order_type: str = "SELL") -> None:
        self._opp_executed[strategy_name] = self._opp_executed.get(strategy_name, 0) + 1
        """Call this when a new trade is opened."""
        self._trade_counts[strategy_name] = (
            self._trade_counts.get(strategy_name, 0) + 1
        )
        self.register_capital(strategy_name, order_type)'''
new = '''    def register_trade(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        self._opp_executed[strategy_name] = self._opp_executed.get(strategy_name, 0) + 1
        """Call this when a new trade is opened. multiplier = lots this trade represents."""
        self._trade_counts[strategy_name] = (
            self._trade_counts.get(strategy_name, 0) + 1
        )
        self.register_capital(strategy_name, order_type, multiplier)'''
replacements.append((old, new))

# ── 5. release_trade ────────────────────────────────────────────────────
old = '''    def release_trade(self, strategy_name: str, order_type: str = "SELL") -> None:
        """Call this when a trade is closed to free up capital."""
        self.release_capital(strategy_name, order_type)'''
new = '''    def release_trade(self, strategy_name: str, order_type: str = "SELL", multiplier: int = 1) -> None:
        """Call this when a trade is closed to free up capital. multiplier = lots this trade represented."""
        self.release_capital(strategy_name, order_type, multiplier)'''
replacements.append((old, new))

# ── 6. reconcile_active_state_from_db — derive multiplier from quantity/lot_size ──
old = '''            for trade in active_trades:
                strat = trade["strategy"]
                otype = trade["order_type"]
                margin = MARGIN_PER_SELL_LOT if otype == "SELL" else MARGIN_PER_BUY_LOT
                
                reconciled_capital[strat] = reconciled_capital.get(strat, 0.0) + margin
                logger.info(f"[RiskManager] Reconstructed open seat: Strategy={strat} | Symbol={trade['symbol']} | Reserved=₹{margin:,.0f}")'''
new = '''            for trade in active_trades:
                strat = trade["strategy"]
                otype = trade["order_type"]
                # Derive lot multiplier from quantity so overshoot-scaled trades
                # reconcile to the correct margin after a restart, not just 1x.
                _lot_size = 15 if strat == "bn_survivor" else 65
                _qty = trade.get("quantity", _lot_size) or _lot_size
                _multiplier = max(1, int(_qty // _lot_size))
                margin = (MARGIN_PER_SELL_LOT if otype == "SELL" else MARGIN_PER_BUY_LOT) * _multiplier
                
                reconciled_capital[strat] = reconciled_capital.get(strat, 0.0) + margin
                logger.info(f"[RiskManager] Reconstructed open seat: Strategy={strat} | Symbol={trade['symbol']} | Multiplier={_multiplier}x | Reserved=₹{margin:,.0f}")'''
replacements.append((old, new))

for old, new in replacements:
    count = content.count(old)
    assert count == 1, f"Match count {count} for block starting: {old[:60]!r}"
    content = content.replace(old, new)

with open(path, "w") as f:
    f.write(content)

print(f"risk_manager.py patched successfully — {len(replacements)} changes applied.")
