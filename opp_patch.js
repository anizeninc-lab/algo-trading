const fs = require('fs')
let content = fs.readFileSync('core/risk_manager.py', 'utf8').replace(/\r\n/g, '\n')

// 1. Add counters to __init__
const oldInit = `        self._last_blocked: dict[str, str] = {}`
const newInit = `        self._last_blocked: dict[str, str] = {}
        # Opportunity tracking
        self._opp_detected:  dict[str, int] = {}  # signals that passed all filters
        self._opp_blocked:   dict[str, int] = {}  # signals blocked (any reason)
        self._opp_executed:  dict[str, int] = {}  # actual trades placed
        self._block_reasons: dict[str, dict] = {} # reason -> count`

if (content.includes(oldInit)) {
  content = content.replace(oldInit, newInit)
  console.log('Init patched OK')
} else { console.log('ERROR: init not found') }

// 2. Add tracking in can_trade — increment blocked counter
const oldBlocked = `        # Clear last blocked if now allowed
        self._last_blocked.pop(strategy_name, None)
        return True, ""`

const newBlocked = `        # Clear last blocked if now allowed
        self._last_blocked.pop(strategy_name, None)
        # Count as detected opportunity
        self._opp_detected[strategy_name] = self._opp_detected.get(strategy_name, 0) + 1
        return True, ""`

if (content.includes(oldBlocked)) {
  content = content.replace(oldBlocked, newBlocked)
  console.log('Opportunity detection tracking OK')
} else { console.log('ERROR: clear blocked not found') }

// 3. Patch _log_blocked_once to count block reasons
const oldLogBlocked = `    def _log_blocked_once(self, strategy_name: str, reason: str) -> None:
        """Only logs 'trade blocked' if reason changed — prevents tick spam."""
        if self._last_blocked.get(strategy_name) != reason:
            logger.info(f"[RiskManager] {strategy_name} blocked: {reason}")
            self._last_blocked[strategy_name] = reason`

const newLogBlocked = `    def _log_blocked_once(self, strategy_name: str, reason: str) -> None:
        """Only logs 'trade blocked' if reason changed — prevents tick spam."""
        if self._last_blocked.get(strategy_name) != reason:
            logger.info(f"[RiskManager] {strategy_name} blocked: {reason}")
            self._last_blocked[strategy_name] = reason
        # Always count block (deduplicated by reason change for logging, but count every tick)
        self._opp_blocked[strategy_name] = self._opp_blocked.get(strategy_name, 0) + 1
        # Track reason breakdown
        if strategy_name not in self._block_reasons:
            self._block_reasons[strategy_name] = {}
        short = reason.split("—")[0].split(":")[0].strip()[:40]
        self._block_reasons[strategy_name][short] = self._block_reasons[strategy_name].get(short, 0) + 1`

if (content.includes(oldLogBlocked)) {
  content = content.replace(oldLogBlocked, newLogBlocked)
  console.log('Block reason tracking OK')
} else { console.log('ERROR: _log_blocked_once not found') }

// 4. Add register_trade to count executions
const oldRegister = `    def register_trade(self, strategy_name: str, order_type: str = "SELL") -> None:`
const newRegister = `    def register_trade(self, strategy_name: str, order_type: str = "SELL") -> None:
        self._opp_executed[strategy_name] = self._opp_executed.get(strategy_name, 0) + 1`

if (content.includes(oldRegister)) {
  content = content.replace(oldRegister, newRegister)
  console.log('Execution tracking OK')
} else { console.log('ERROR: register_trade not found') }

fs.writeFileSync('core/risk_manager.py', content)
console.log('risk_manager.py patched')
