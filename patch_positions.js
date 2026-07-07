const fs = require('fs')
let content = fs.readFileSync('dashboard/frontend/src/App.jsx', 'utf8')

// Normalize to LF
content = content.replace(/\r\n/g, '\n')

const oldCard = `        const unreal  = t.unrealised_pnl || 0
        const ltp     = t.current_ltp || 0
        const fresh   = t.ltp_fresh === true
        const premium = (t.entry_price || 0) * (t.quantity || 0)
        const beHit   = unreal >= 400`

const newCard = `        const unreal  = t.unrealised_pnl || 0
        const ltp     = t.current_ltp || 0
        const fresh   = t.ltp_fresh === true
        const premium = (t.entry_price || 0) * (t.quantity || 0)
        const beHit   = unreal >= 400
        const capDeployed = 40000
        const pctReturn = premium > 0 ? ((unreal / premium) * 100).toFixed(1) : "0.0"
        const tpTarget  = (premium * 0.40).toFixed(0)
        const pctToTp   = premium > 0 ? Math.min(100, (unreal / (premium * 0.40)) * 100).toFixed(0) : 0`

if (content.includes(oldCard)) {
  content = content.replace(oldCard, newCard)
  console.log('Card vars patched OK')
} else {
  console.log('ERROR: still not found')
  process.exit(1)
}

const oldCols = `                ["PREMIUM",  fmtRs(premium)],`
const newCols = `                ["PREMIUM",  fmtRs(premium)],
                ["CAP USED",  fmtRs(capDeployed)],
                ["% RETURN",  pctReturn + "%"],
                ["TP TARGET", fmtRs(Number(tpTarget))],`

content = content.replace(oldCols, newCols)
console.log('Columns patched OK')

fs.writeFileSync('dashboard/frontend/src/App.jsx', content)
console.log('Done — lines: ' + content.split('\n').length)
