const fs = require('fs')
let content = fs.readFileSync('dashboard/frontend/src/App.jsx', 'utf8').replace(/\r\n/g, '\n')

// 1. Add columns to header
const oldCols = '  const cols = ["TRADE ID", "STRATEGY", "INSTRUMENT", "DIR", "QTY", "ENTRY TIME", "ENTRY ₹", "EXIT TIME", "EXIT ₹", "PREMIUM", "STATUS", "P&L"]'
const newCols = '  const cols = ["TRADE ID", "STRATEGY", "INSTRUMENT", "DIR", "QTY", "ENTRY TIME", "ENTRY ₹", "EXIT TIME", "EXIT ₹", "PREMIUM", "CAP USED", "% RETURN", "STATUS", "P&L"]'

if (content.includes(oldCols)) {
  content = content.replace(oldCols, newCols)
  console.log('Header columns OK')
} else { console.log('ERROR: header cols not found') }

// 2. Add to CSV export
const oldCSV = '        premium.toFixed(2), t.status, t.realised_pnl'
const newCSV = `        premium.toFixed(2), 40000, premium > 0 ? ((t.realised_pnl||0)/premium*100).toFixed(1)+"%" : "0%", t.status, t.realised_pnl`
if (content.includes(oldCSV)) {
  content = content.replace(oldCSV, newCSV)
  console.log('CSV export OK')
} else { console.log('ERROR: CSV not found') }

// 3. Add to table row — after premium cell, before status cell
const oldRow = `                    <td style={{ padding: "8px 12px", color: C.orange, fontFamily: "monospace" }}>{fmtRs(premium)}</td>
                    <td style={{ padding: "8px 12px" }}><Pill label={t.status} colour={statusCol} size={9} /></td>`
const newRow = `                    <td style={{ padding: "8px 12px", color: C.orange, fontFamily: "monospace" }}>{fmtRs(premium)}</td>
                    <td style={{ padding: "8px 12px", color: C.cyan, fontFamily: "monospace" }}>₹40,000</td>
                    <td style={{ padding: "8px 12px", color: pnlC(t.realised_pnl), fontFamily: "monospace", fontWeight: 700 }}>{premium > 0 ? ((t.realised_pnl||0)/premium*100).toFixed(1)+"%" : "—"}</td>
                    <td style={{ padding: "8px 12px" }}><Pill label={t.status} colour={statusCol} size={9} /></td>`

if (content.includes(oldRow)) {
  content = content.replace(oldRow, newRow)
  console.log('Table row OK')
} else { console.log('ERROR: table row not found') }

// 4. Add to detail view — add % return to CAPITAL & RISK section
const oldDetail = '["Realised P&L", fmtRs(t.realised_pnl)], ["Risk/Reward", rr]]'
const newDetail = '["Realised P&L", fmtRs(t.realised_pnl)], ["% Return", premium > 0 ? ((t.realised_pnl||0)/premium*100).toFixed(1)+"%" : "—"], ["Risk/Reward", rr]]'
if (content.includes(oldDetail)) {
  content = content.replace(oldDetail, newDetail)
  console.log('Detail view OK')
} else { console.log('ERROR: detail view not found') }

fs.writeFileSync('dashboard/frontend/src/App.jsx', content)
console.log('Done — lines: ' + content.split('\n').length)
