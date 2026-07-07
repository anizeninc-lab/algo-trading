const fs = require('fs')
let content = fs.readFileSync('dashboard/frontend/src/App.jsx', 'utf8').replace(/\r\n/g, '\n')
const opp = fs.readFileSync('opp_meter.js', 'utf8')

// 1. Inject component before CapitalIntelligencePanel
const insertBefore = '// ── Capital Intelligence Panel'
if (content.includes(insertBefore)) {
  content = content.replace(insertBefore, opp + '\n\n' + insertBefore)
  console.log('OpportunityMeter component injected OK')
} else {
  console.log('ERROR: injection point not found')
  process.exit(1)
}

// 2. Add OpportunityMeter to risk tab
const oldTab = '{tab === "risk"      && <div style={{ display: "flex", flexDirection: "column", gap: 16 }}><CapitalIntelligencePanel trades={trades} /><RiskPanel trades={trades} global={g} /></div>}'
const newTab = '{tab === "risk"      && <div style={{ display: "flex", flexDirection: "column", gap: 16 }}><OpportunityMeter /><CapitalIntelligencePanel trades={trades} /><RiskPanel trades={trades} global={g} /></div>}'

if (content.includes(oldTab)) {
  content = content.replace(oldTab, newTab)
  console.log('Risk tab updated OK')
} else {
  console.log('ERROR: risk tab not found')
}

fs.writeFileSync('dashboard/frontend/src/App.jsx', content)
console.log('Done — lines: ' + content.split('\n').length)
