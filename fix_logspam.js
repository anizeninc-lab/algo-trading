const fs = require('fs')
let content = fs.readFileSync('core/risk_manager.py', 'utf8').replace(/\r\n/g, '\n')

const old = `            logger.warning(f"[RiskManager] CAPITAL GUARD: {reason}")\n            return False, reason\n        return True, ""`
const new_ = `            logger.debug(f"[RiskManager] CAPITAL GUARD: {reason}")\n            return False, reason\n        return True, ""`

if (content.includes(old)) {
  content = content.replace(old, new_)
  console.log('Log spam fixed OK')
} else {
  const idx = content.indexOf('CAPITAL GUARD')
  console.log('Found at:', idx)
  console.log(JSON.stringify(content.slice(idx-10, idx+100)))
}

fs.writeFileSync('core/risk_manager.py', content)
console.log('Done')
