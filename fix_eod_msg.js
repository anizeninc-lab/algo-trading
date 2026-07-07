const fs = require('fs')

// Fix alerting.py — add reason parameter
let alerting = fs.readFileSync('core/alerting.py', 'utf8').replace(/\r\n/g, '\n')
const oldAlert = `def alert_eod_close(total_pnl: float, trades: int) -> None:
    level = LEVEL_PROFIT if total_pnl >= 0 else LEVEL_LOSS
    send_telegram(
        f"*EOD — ALL POSITIONS CLOSED*\\n"
        f"Today's P&L: \`₹{total_pnl:+.2f}\`\\n"
        f"Trades: \`{trades}\`",
        level
    )`
const newAlert = `def alert_eod_close(total_pnl: float, trades: int, reason: str = "EOD") -> None:
    level = LEVEL_PROFIT if total_pnl >= 0 else LEVEL_LOSS
    emoji = "✅" if reason == "TP" else "🕐" if reason == "EOD" else "🛑"
    label = "ALL TARGETS HIT" if reason == "TP" else "EOD — ALL POSITIONS CLOSED" if reason == "EOD" else "ALL POSITIONS CLOSED"
    send_telegram(
        f"*{emoji} {label}*\\n"
        f"Today's P&L: \`₹{total_pnl:+.2f}\`\\n"
        f"Trades: \`{trades}\`",
        level
    )`

if (alerting.includes(oldAlert)) {
  alerting = alerting.replace(oldAlert, newAlert)
  console.log('alerting.py patched OK')
} else {
  console.log('ERROR: alerting pattern not found')
}
fs.writeFileSync('core/alerting.py', alerting)

// Fix survivor.py — pass reason to alert_eod_close based on context
let survivor = fs.readFileSync('strategy/survivor.py', 'utf8').replace(/\r\n/g, '\n')

// _close_all_positions is called from both EOD watchdog and TP — add reason param
const oldCloseAll = `    async def _close_all_positions(self) -> None:`
const newCloseAll = `    async def _close_all_positions(self, reason: str = "EOD") -> None:`
survivor = survivor.replace(oldCloseAll, newCloseAll)

// Pass reason to alert_eod_close
const oldAlertCall = `            alert_eod_close(total_pnl, trade_cnt)`
const newAlertCall = `            alert_eod_close(total_pnl, trade_cnt, reason=reason)`
survivor = survivor.replace(oldAlertCall, newAlertCall)

// When TP closes all positions, pass reason="TP"
// Find where _close_all_positions is called
const calls = ['await self._close_all_positions()', 'await self.close_all_positions()']
let count = 0
calls.forEach(call => {
  while (survivor.includes(call)) {
    survivor = survivor.replace(call, call.replace('()', '()'))
    count++
  }
})
console.log(`survivor.py patched — ${count} calls found`)
fs.writeFileSync('strategy/survivor.py', survivor)
console.log('Done')
