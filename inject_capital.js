const fs = require('fs')
let content = fs.readFileSync('dashboard/frontend/src/App.jsx', 'utf8')
const capital = fs.readFileSync('capital_panel.js', 'utf8')

const insertBefore = 'function RiskPanel({ trades, global: g }) {'
if (content.includes(insertBefore)) {
  content = content.replace(insertBefore, capital + '\n\n' + insertBefore)
  console.log('Component injected OK')
} else {
  console.log('ERROR: RiskPanel not found')
  process.exit(1)
}

const oldTab = '{tab === "risk"      && <RiskPanel trades={trades} global={g} />}'
const newTab = '{tab === "risk"      && <div style={{ display: "flex", flexDirection: "column", gap: 16 }}><CapitalIntelligencePanel trades={trades} /><RiskPanel trades={trades} global={g} /></div>}'
if (content.includes(oldTab)) {
  content = content.replace(oldTab, newTab)
  console.log('Tab updated OK')
} else {
  console.log('ERROR: risk tab not found')
}

fs.writeFileSync('dashboard/frontend/src/App.jsx', content)
console.log('Done — lines: ' + content.split('\n').length)
