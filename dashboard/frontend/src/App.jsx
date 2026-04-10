import { useState, useEffect, useRef } from "react"
import axios from "axios"

const API = "http://127.0.0.1:8081"
const WS  = "ws://127.0.0.1:8081/ws/updates"

const STATE_COLOUR = {
  RUNNING: "#00ff88",
  STOPPED: "#64748b",
  ERROR:   "#ff4455",
  IDLE:    "#3b82f6",
}

const pnlColour = (val) => (val || 0) >= 0 ? "#00ff88" : "#ff4455"
const pnlBg     = (val) => (val || 0) >= 0 ? "rgba(0,255,136,0.08)" : "rgba(255,68,85,0.08)"
const fmt        = (v)  => v == null ? "—" : `₹${Number(v).toFixed(2)}`
const pct        = (v)  => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`

function Badge({ label, colour }) {
  return (
    <span style={{
      background: colour + "22", color: colour, borderRadius: 4,
      padding: "2px 10px", fontSize: 11, fontWeight: 700, letterSpacing: 1,
      border: `1px solid ${colour}44`,
    }}>
      {label}
    </span>
  )
}

function StatBox({ label, value, sub, colour, size = 20, bg, tag }) {
  return (
    <div style={{
      background: bg || "#131929", borderRadius: 10, padding: "12px 18px",
      border: "1px solid #1e2d47", minWidth: 130, flex: 1, position: "relative",
    }}>
      {tag && (
        <div style={{
          position: "absolute", top: 8, right: 10,
          fontSize: 9, fontWeight: 700, letterSpacing: 1.5,
          color: tag === "LIVE" ? "#3b82f6" : "#f59e0b",
          background: tag === "LIVE" ? "#3b82f611" : "#f59e0b11",
          border: `1px solid ${tag === "LIVE" ? "#3b82f644" : "#f59e0b44"}`,
          borderRadius: 3, padding: "1px 6px",
        }}>{tag}</div>
      )}
      <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: size, fontWeight: 800, color: colour || "#e2e8f0", fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "#4a6080", marginTop: 3, fontFamily: "'JetBrains Mono', monospace" }}>{sub}</div>}
    </div>
  )
}

// ── New 4-card top section ──────────────────────────────────────────────────
function CapitalCards({ account, trades, isPaper }) {
  const todayStr = new Date().toISOString().slice(0, 10)

  // Invested = allocated (payin) + currently deployed (used_margin)
  const allocated   = account?.allocated_capital ?? null
  const usedMargin  = account?.used_margin        ?? null
  const currentBal  = account?.current_balance    ?? null

  // Total return = all closed trade realised P&L
  const totalRealised = trades
    .filter(t => t.status === "CLOSED")
    .reduce((s, t) => s + (t.realised_pnl || 0), 0)

  // Today's return = today closed trades realised P&L
  const todayRealised = trades
    .filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr)
    .reduce((s, t) => s + (t.realised_pnl || 0), 0)

  // % returns based on allocated capital
  const totalReturnPct = allocated ? (totalRealised / allocated) * 100 : null
  const todayReturnPct = allocated ? (todayRealised / allocated) * 100 : null

  const modeTag = isPaper ? "PAPER" : "LIVE"

  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 20, flexWrap: "wrap" }}>

      {/* 1. Invested Amount */}
      <div style={{
        background: "#131929", borderRadius: 10, padding: "12px 18px",
        border: "1px solid #1e2d47", minWidth: 160, flex: 1, position: "relative",
      }}>
        <div style={{ position: "absolute", top: 8, right: 10, fontSize: 9, fontWeight: 700,
          letterSpacing: 1.5, color: "#4a6080" }}>{modeTag}</div>
        <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 6 }}>
          INVESTED AMOUNT
        </div>
        <div style={{ fontSize: 22, fontWeight: 800, color: "#e2e8f0", fontFamily: "'JetBrains Mono', monospace" }}>
          {allocated != null ? fmt(allocated) : "—"}
        </div>
        <div style={{ fontSize: 10, color: "#4a6080", marginTop: 4, display: "flex", gap: 12 }}>
          <span>Allocated: <span style={{ color: "#e2e8f0" }}>{allocated != null ? fmt(allocated) : "—"}</span></span>
          <span>Deployed: <span style={{ color: usedMargin > 0 ? "#f59e0b" : "#e2e8f0" }}>{usedMargin != null ? fmt(usedMargin) : "—"}</span></span>
        </div>
      </div>

      {/* 2. Current Amount (live balance from Upstox) */}
      <div style={{
        background: "#131929", borderRadius: 10, padding: "12px 18px",
        border: "1px solid #1e2d47", minWidth: 160, flex: 1, position: "relative",
      }}>
        <div style={{ position: "absolute", top: 8, right: 10, fontSize: 9, fontWeight: 700,
          letterSpacing: 1.5, color: isPaper ? "#f59e0b" : "#3b82f6",
          background: isPaper ? "#f59e0b11" : "#3b82f611",
          border: `1px solid ${isPaper ? "#f59e0b44" : "#3b82f644"}`,
          borderRadius: 3, padding: "1px 6px",
        }}>{modeTag}</div>
        <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 6 }}>
          CURRENT BALANCE
        </div>
        <div style={{ fontSize: 22, fontWeight: 800, color: "#e2e8f0", fontFamily: "'JetBrains Mono', monospace" }}>
          {currentBal != null ? fmt(currentBal) : "—"}
        </div>
        <div style={{ fontSize: 10, color: "#4a6080", marginTop: 4 }}>
          Available margin from Upstox
        </div>
      </div>

      {/* 3. Total Return */}
      <div style={{
        background: pnlBg(totalRealised), borderRadius: 10, padding: "12px 18px",
        border: `1px solid ${pnlColour(totalRealised)}22`, minWidth: 160, flex: 1, position: "relative",
      }}>
        <div style={{ position: "absolute", top: 8, right: 10, fontSize: 9, fontWeight: 700,
          letterSpacing: 1.5, color: isPaper ? "#f59e0b" : "#3b82f6",
          background: isPaper ? "#f59e0b11" : "#3b82f611",
          border: `1px solid ${isPaper ? "#f59e0b44" : "#3b82f644"}`,
          borderRadius: 3, padding: "1px 6px",
        }}>{modeTag}</div>
        <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 6 }}>
          TOTAL RETURN
        </div>
        <div style={{ fontSize: 22, fontWeight: 800, color: pnlColour(totalRealised), fontFamily: "'JetBrains Mono', monospace" }}>
          {fmt(totalRealised)}
        </div>
        <div style={{ fontSize: 10, color: pnlColour(totalRealised), marginTop: 4, fontFamily: "'JetBrains Mono', monospace" }}>
          {totalReturnPct != null ? pct(totalReturnPct) : "—"} on capital
        </div>
      </div>

      {/* 4. Today's Return */}
      <div style={{
        background: pnlBg(todayRealised), borderRadius: 10, padding: "12px 18px",
        border: `1px solid ${pnlColour(todayRealised)}22`, minWidth: 160, flex: 1, position: "relative",
      }}>
        <div style={{ position: "absolute", top: 8, right: 10, fontSize: 9, fontWeight: 700,
          letterSpacing: 1.5, color: isPaper ? "#f59e0b" : "#3b82f6",
          background: isPaper ? "#f59e0b11" : "#3b82f611",
          border: `1px solid ${isPaper ? "#f59e0b44" : "#3b82f644"}`,
          borderRadius: 3, padding: "1px 6px",
        }}>{modeTag}</div>
        <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 6 }}>
          TODAY'S RETURN
        </div>
        <div style={{ fontSize: 22, fontWeight: 800, color: pnlColour(todayRealised), fontFamily: "'JetBrains Mono', monospace" }}>
          {fmt(todayRealised)}
        </div>
        <div style={{ fontSize: 10, color: pnlColour(todayRealised), marginTop: 4, fontFamily: "'JetBrains Mono', monospace" }}>
          {todayReturnPct != null ? pct(todayReturnPct) : "—"} today
        </div>
      </div>

    </div>
  )
}

function NiftyTicker({ market, strategies }) {
  const [prev, setPrev] = useState(0)
  const [flash, setFlash] = useState(null)
  const price = market?.nifty_price || 0
  
  // Extract Anchor levels from the Saviour Combo strategy data
  const saviour = strategies?.saviour_combo; 
  const lastSignal = saviour?.last_signal || "";
  
  const peStart = lastSignal.match(/PE Start: ([\d.]+)/)?.[1];
  const ceStart = lastSignal.match(/CE Start: ([\d.]+)/)?.[1];

  useEffect(() => {
    if (prev && price && price !== prev) {
      setFlash(price > prev ? "up" : "down")
      setTimeout(() => setFlash(null), 600)
    }
    setPrev(price)
  }, [price])

  const colour = flash === "up" ? "#00ff88" : flash === "down" ? "#ff4455" : "#e2e8f0"

  return (
    <div style={{
      background: "#131929", borderRadius: 10, padding: "12px 18px",
      border: "1px solid #1e2d47", minWidth: 200,
      transition: "border-color 0.3s",
      borderColor: flash ? (flash === "up" ? "#00ff88" : "#ff4455") : "#1e2d47",
    }}>
      <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>
        NIFTY 50 (LIVE)
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, color: colour, fontFamily: "'JetBrains Mono', monospace" }}>
        {price > 0 ? price.toLocaleString() : "WAITING..."}
      </div>
      
      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid #1e2d47", paddingTop: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10 }}>
          <span style={{ color: "#ff4455", fontWeight: 700 }}>PE ANCHOR:</span>
          <span style={{ color: "#e2e8f0" }}>{peStart || "—"}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10 }}>
          <span style={{ color: "#00ff88", fontWeight: 700 }}>CE ANCHOR:</span>
          <span style={{ color: "#e2e8f0" }}>{ceStart || "—"}</span>
        </div>
      </div>
    </div>
  )
}

function Sparkline({ history }) {
  if (!history || history.length < 2) return null
  const vals   = history.map(h => h.pnl)
  const min    = Math.min(...vals)
  const max    = Math.max(...vals)
  const range  = max - min || 1
  const w = 300, h = 44
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w
    const y = h - ((v - min) / range) * (h - 4) - 2
    return `${x},${y}`
  }).join(" ")
  const last   = vals[vals.length - 1]
  const colour = last >= 0 ? "#00ff88" : "#ff4455"
  const fillId = `grad-${Math.random().toString(36).slice(2)}`
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={colour} stopOpacity="0.3"/>
          <stop offset="100%" stopColor={colour} stopOpacity="0"/>
        </linearGradient>
      </defs>
      <polygon points={`0,${h} ${points} ${w},${h}`} fill={`url(#${fillId})`}/>
      <polyline points={points} fill="none" stroke={colour} strokeWidth="1.5" strokeLinejoin="round"/>
    </svg>
  )
}

function VixBadge({ vix }) {
  if (!vix) return null
  const v = vix.value
  let colour = "#00ff88"
  if (v >= 25) colour = "#ff4455"
  else if (v >= 20) colour = "#f97316"
  else if (v >= 16) colour = "#f59e0b"
  return (
    <div style={{
      background: "#131929", borderRadius: 10, padding: "12px 18px",
      border: `1px solid ${colour}44`, minWidth: 130,
    }}>
      <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>INDIA VIX</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 22, fontWeight: 800, color: colour, fontFamily: "'JetBrains Mono', monospace" }}>
          {v != null ? v.toFixed(2) : "—"}
        </span>
        <span style={{ fontSize: 10, color: colour, fontWeight: 700 }}>{vix.regime}</span>
      </div>
      {vix.halt && (
        <div style={{ fontSize: 10, color: "#ff4455", marginTop: 4, fontWeight: 700 }}>TRADING HALTED</div>
      )}
      {vix.updated && (
        <div style={{ fontSize: 10, color: "#4a6080", marginTop: 2 }}>↻ {vix.updated}</div>
      )}
    </div>
  )
}

function StrategyCard({ name, data, onStop, onReset }) {
  const title = name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
  if (!data) return (
    <div style={{ background: "#0d1526", borderRadius: 14, padding: 20, border: "1px solid #1e2d47", minHeight: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ color: "#4a6080", fontSize: 13 }}>{title} — waiting...</div>
    </div>
  )
  const realised   = data.realised_pnl   || 0
  const unrealised = data.unrealised_pnl || 0
  const netPnl     = realised + unrealised
  const colour     = STATE_COLOUR[data.state] || "#64748b"
  return (
    <div style={{ background: "#0d1526", borderRadius: 14, padding: 20, border: `1px solid ${colour}33`, display: "flex", flexDirection: "column", gap: 14, boxShadow: `0 0 20px ${colour}0a` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 800, fontSize: 15, color: "#e2e8f0" }}>{title}</span>
        <Badge label={data.state || "IDLE"} colour={colour} />
      </div>
      <div style={{ background: pnlBg(netPnl), borderRadius: 10, padding: "12px 14px", border: `1px solid ${pnlColour(netPnl)}22`, display: "flex", gap: 20, alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1 }}>NET P&L</div>
          <div style={{ fontSize: 24, fontWeight: 800, color: pnlColour(netPnl), fontFamily: "'JetBrains Mono', monospace" }}>₹{netPnl.toFixed(2)}</div>
        </div>
        <div style={{ borderLeft: "1px solid #1e2d47", paddingLeft: 20, display: "flex", gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>REALISED</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: pnlColour(realised), fontFamily: "'JetBrains Mono', monospace" }}>₹{realised.toFixed(2)}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>UNREALISED</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: pnlColour(unrealised), fontFamily: "'JetBrains Mono', monospace" }}>₹{unrealised.toFixed(2)}</div>
          </div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 12 }}>
        {[
          { label: "POSITION",    value: data.position    || "FLAT" },
          { label: "OPEN ORDERS", value: data.open_orders || 0 },
          { label: "OPEN TRADES", value: data.open_trades || 0 },
          { label: "TOTAL",       value: data.total_trades || 0 },
        ].map(({ label, value }) => (
          <div key={label} style={{ flex: 1, background: "#131929", borderRadius: 8, padding: "8px 10px", border: "1px solid #1e2d47" }}>
            <div style={{ fontSize: 9, color: "#4a6080", fontWeight: 700, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#e2e8f0", marginTop: 2 }}>{value}</div>
          </div>
        ))}
      </div>
      <div style={{ background: "#131929", borderRadius: 8, padding: "8px 12px", fontSize: 11, color: "#4a8080", minHeight: 28, borderLeft: "2px solid #1e4060", fontFamily: "'JetBrains Mono', monospace" }}>
        {data.last_signal || "— no signal yet —"}
      </div>
      <div style={{ borderRadius: 8, overflow: "hidden", background: "#131929", padding: "4px 0" }}>
        <Sparkline history={data.pnl_history} />
      </div>
      {data.error_message && (
        <div style={{ color: "#ff4455", fontSize: 11, background: "#ff445510", borderRadius: 6, padding: "6px 10px" }}>
          ⚠ {data.error_message}
        </div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={() => onStop(name)} disabled={data.state !== "RUNNING"}
          style={{ flex: 1, padding: "8px 0", borderRadius: 8, border: data.state === "RUNNING" ? "1px solid #ff445544" : "1px solid #1e2d47", background: data.state === "RUNNING" ? "#ff445522" : "#1e2d47", color: data.state === "RUNNING" ? "#ff4455" : "#4a6080", fontWeight: 700, cursor: data.state === "RUNNING" ? "pointer" : "not-allowed", fontSize: 12, fontFamily: "'JetBrains Mono', monospace" }}>
          STOP
        </button>
        <button onClick={() => onReset(name)} disabled={data.state !== "ERROR"}
          style={{ flex: 1, padding: "8px 0", borderRadius: 8, border: data.state === "ERROR" ? "1px solid #f59e0b44" : "1px solid #1e2d47", background: data.state === "ERROR" ? "#f59e0b22" : "#1e2d47", color: data.state === "ERROR" ? "#f59e0b" : "#4a6080", fontWeight: 700, cursor: data.state === "ERROR" ? "pointer" : "not-allowed", fontSize: 12, fontFamily: "'JetBrains Mono', monospace" }}>
          RESET
        </button>
      </div>
    </div>
  )
}

function OpenTradesPanel({ trades }) {
  const open = trades.filter(t => t.status === "OPEN")
  if (open.length === 0) return (
    <div style={{ color: "#4a6080", textAlign: "center", padding: "28px 0", fontSize: 13 }}>No open positions</div>
  )
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {open.map((t, i) => {
        const unreal = t.unrealised_pnl || 0
        return (
          <div key={t.id || i} style={{ background: "#131929", borderRadius: 10, padding: "12px 16px", border: `1px solid ${pnlColour(unreal)}22`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
              <div><div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>STRATEGY</div><div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 700 }}>{t.strategy}</div></div>
              <div><div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>SYMBOL</div><div style={{ fontSize: 12, color: "#e2e8f0", fontFamily: "'JetBrains Mono', monospace" }}>{t.symbol}</div></div>
              <div><div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>TYPE</div><div style={{ fontSize: 12, fontWeight: 700, color: t.order_type === "SELL" ? "#ff4455" : "#00ff88" }}>{t.order_type}</div></div>
              <div><div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>QTY</div><div style={{ fontSize: 12, color: "#e2e8f0" }}>{t.quantity}</div></div>
              <div><div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>ENTRY</div><div style={{ fontSize: 12, color: "#e2e8f0", fontFamily: "'JetBrains Mono', monospace" }}>{t.entry_time ? t.entry_time.slice(11, 19) : "—"}</div></div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700 }}>UNREALISED P&L</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: pnlColour(unreal), fontFamily: "'JetBrains Mono', monospace" }}>₹{unreal.toFixed(2)}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function TradeLog({ trades }) {
  const closed = trades.filter(t => t.status === "CLOSED")
  if (closed.length === 0) return (
    <div style={{ color: "#4a6080", textAlign: "center", padding: "28px 0", fontSize: 13 }}>No closed trades yet</div>
  )
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ background: "#0a1020" }}>
            {["Strategy", "Symbol", "Type", "Qty", "Entry", "Exit", "P&L"].map(h => (
              <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontWeight: 700, color: "#4a6080", fontSize: 10, letterSpacing: 1.2, borderBottom: "1px solid #1e2d47" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {closed.map((t, i) => (
            <tr key={t.id || i} style={{ borderBottom: "1px solid #131929" }}
              onMouseEnter={e => e.currentTarget.style.background = "#131929"}
              onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
              <td style={{ padding: "10px 14px", color: "#94a3b8" }}>{t.strategy}</td>
              <td style={{ padding: "10px 14px", color: "#e2e8f0", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.symbol}</td>
              <td style={{ padding: "10px 14px", fontWeight: 700, color: t.order_type === "SELL" ? "#ff4455" : "#00ff88" }}>{t.order_type}</td>
              <td style={{ padding: "10px 14px", color: "#e2e8f0" }}>{t.quantity}</td>
              <td style={{ padding: "10px 14px", color: "#64748b", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.entry_time ? t.entry_time.slice(11, 19) : "—"}</td>
              <td style={{ padding: "10px 14px", color: "#64748b", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.exit_time ? t.exit_time.slice(11, 19) : "—"}</td>
              <td style={{ padding: "10px 14px", fontWeight: 700, color: pnlColour(t.realised_pnl), fontFamily: "'JetBrains Mono', monospace" }}>
                {t.realised_pnl != null ? `₹${t.realised_pnl.toFixed(2)}` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DailyPnlBar({ trades }) {
  const byDate = {}
  trades.filter(t => t.status === "CLOSED" && t.exit_time).forEach(t => {
    const date = t.exit_time.slice(0, 10)
    byDate[date] = (byDate[date] || 0) + (t.realised_pnl || 0)
  })
  const days = Object.entries(byDate).sort((a, b) => a[0].localeCompare(b[0])).slice(-7)
  if (days.length === 0) return (
    <div style={{ color: "#4a6080", textAlign: "center", padding: "28px 0", fontSize: 13 }}>No daily P&L data yet</div>
  )
  const max = Math.max(...days.map(d => Math.abs(d[1]))) || 1
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-end", height: 100, padding: "0 4px" }}>
      {days.map(([date, pnl]) => {
        const h   = Math.max(4, (Math.abs(pnl) / max) * 80)
        const col = pnl >= 0 ? "#00ff88" : "#ff4455"
        return (
          <div key={date} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
            <div style={{ fontSize: 10, color: col, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{pnl >= 0 ? "+" : ""}{pnl.toFixed(0)}</div>
            <div style={{ width: "100%", height: h, background: col + "44", borderRadius: 4, border: `1px solid ${col}66` }} />
            <div style={{ fontSize: 9, color: "#4a6080", fontWeight: 700 }}>{date.slice(5).replace("-", "/")}</div>
          </div>
        )
      })}
    </div>
  )
}

const DEFAULT = {
  global: { total_pnl: 0, active_strategies: 0, total_strategies: 0, system_health: "OK", broker_status: {} },
  strategies: {},
  vix: null,
  market: { nifty_price: 0, nifty_updated: "", option_price: 0, option_symbol: "" },
}

export default function App() {
  const [data,     setData]     = useState(DEFAULT)
  const [trades,      setTrades]      = useState([])
  const [account,     setAccount]     = useState(null)
  const [wsStatus,    setWsStatus]    = useState("CONNECTING")
  const [tab,         setTab]         = useState("open")
  const [killConfirm, setKillConfirm] = useState(false)
  const [killDone,    setKillDone]    = useState(false)
  const wsRef = useRef(null)
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(WS)
      wsRef.current = ws
      ws.onopen    = () => setWsStatus("CONNECTED")
      ws.onmessage = (e) => { try { setData(JSON.parse(e.data)) } catch {} }
      ws.onclose   = () => { setWsStatus("RECONNECTING"); setTimeout(connect, 3000) }
      ws.onerror   = () => { ws.close() }
    }
    connect()
    return () => { if (wsRef.current) wsRef.current.close() }
  }, [])

  useEffect(() => {
    async function fetchTrades() {
      try {
        const res = await axios.get(`${API}/api/trades?limit=200`)
        setTrades(res.data.trades || [])
      } catch {}
    }
    fetchTrades()
    const id = setInterval(fetchTrades, 3000)
    return () => clearInterval(id)
  }, [])

  // Fetch account balance every 30 seconds
  useEffect(() => {
    async function fetchAccount() {
      try {
        const res = await axios.get(`${API}/api/account/balance`)
        if (!res.data.error) setAccount(res.data)
      } catch {}
    }
    fetchAccount()
    const id = setInterval(fetchAccount, 30000)
    return () => clearInterval(id)
  }, [])

  async function handleStop(name) {
    try { await axios.post(`${API}/api/strategy/${name}/stop`) }
    catch (e) { alert(`Stop failed: ${e.response?.data?.error || e.message}`) }
  }

  async function handleReset(name) {
    try { await axios.post(`${API}/api/strategy/${name}/reset`) }
    catch (e) { alert(`Reset failed: ${e.response?.data?.error || e.message}`) }
  }

  async function handleEmergencyKill() {
    try {
      await axios.post(`${API}/api/emergency/kill`)
      setKillConfirm(false)
      setKillDone(true)
      setTimeout(() => setKillDone(false), 5000)
    } catch (e) {
      alert(`Emergency kill failed: ${e.message}`)
      setKillConfirm(false)
    }
  }

  const g         = data.global
  const s         = data.strategies
  const vix       = data.vix
  const market    = data.market || {}
  const openCount = trades.filter(t => t.status === "OPEN").length
  const isPaper   = g.paper_trade === true
  const brokerConnected = Object.values(g.broker_status || {}).some(v => v === "CONNECTED")

  return (
    <div style={{ minHeight: "100vh", background: "#080e1a", color: "#e2e8f0", fontFamily: "'JetBrains Mono', 'Fira Code', monospace", padding: "20px 24px" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;800&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: #0a1020; }
        ::-webkit-scrollbar-thumb { background: #1e2d47; border-radius: 4px; }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid #1e2d47" }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#fff", letterSpacing: -0.5 }}>◈ ALGO TRADING SYSTEM</div>
          <div style={{ fontSize: 10, color: "#4a6080", marginTop: 3, letterSpacing: 2 }}>SAVIOUR COMBO · SURVIVOR ALGO · WAVE EXTRACTOR</div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <div style={{ fontSize: 11, color: "#4a6080" }}>{now.toLocaleTimeString("en-IN")}</div>
          <Badge label={wsStatus === "CONNECTED" ? "● LIVE" : wsStatus === "RECONNECTING" ? "◌ RECONNECTING" : "○ OFFLINE"} colour={wsStatus === "CONNECTED" ? "#00ff88" : wsStatus === "RECONNECTING" ? "#f59e0b" : "#ff4455"} />
          <Badge label={brokerConnected ? "BROKER ON" : "BROKER OFF"} colour={brokerConnected ? "#00ff88" : "#ff4455"} />
          <Badge label={isPaper ? "PAPER MODE" : "LIVE MODE"} colour={isPaper ? "#f59e0b" : "#3b82f6"} />

          {/* ── Emergency Kill Switch ── */}
          {killDone ? (
            <span style={{
              background: "#ff445522", color: "#ff4455", borderRadius: 6,
              padding: "6px 14px", fontSize: 11, fontWeight: 800, letterSpacing: 1,
              border: "1px solid #ff445566",
            }}>✓ ALL STOPPED</span>
          ) : killConfirm ? (
            <div style={{ display: "flex", gap: 6, alignItems: "center", background: "#1a0a0a", borderRadius: 8, padding: "4px 8px", border: "1px solid #ff445566" }}>
              <span style={{ fontSize: 10, color: "#ff4455", fontWeight: 700 }}>CONFIRM KILL?</span>
              <button onClick={handleEmergencyKill} style={{
                background: "#ff4455", color: "#fff", border: "none", borderRadius: 5,
                padding: "4px 10px", fontSize: 11, fontWeight: 800, cursor: "pointer",
              }}>YES</button>
              <button onClick={() => setKillConfirm(false)} style={{
                background: "#1e2d47", color: "#94a3b8", border: "none", borderRadius: 5,
                padding: "4px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer",
              }}>NO</button>
            </div>
          ) : (
            <button onClick={() => setKillConfirm(true)} style={{
              background: "#ff445533", color: "#ff4455",
              border: "1px solid #ff445566", borderRadius: 6,
              padding: "6px 14px", fontSize: 11, fontWeight: 800,
              letterSpacing: 1, cursor: "pointer",
              fontFamily: "'JetBrains Mono', monospace",
            }}>⚠ KILL ALL</button>
          )}
        </div>
      </div>

      {/* Secondary info bar — Nifty + VIX + system stats */}
      <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <NiftyTicker market={data.market} strategies={data.strategies} />
        <VixBadge vix={vix} />
        <div style={{ background: "#131929", borderRadius: 10, padding: "12px 18px", border: "1px solid #1e2d47", minWidth: 110 }}>
          <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>OPEN POS</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: openCount > 0 ? "#3b82f6" : "#4a6080", fontFamily: "'JetBrains Mono', monospace" }}>{openCount}</div>
        </div>
        <div style={{ background: "#131929", borderRadius: 10, padding: "12px 18px", border: "1px solid #1e2d47", minWidth: 110 }}>
          <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>ACTIVE STRATS</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#e2e8f0", fontFamily: "'JetBrains Mono', monospace" }}>{g.active_strategies || 0}/{g.total_strategies || 0}</div>
        </div>
        <div style={{ background: "#131929", borderRadius: 10, padding: "12px 18px", border: "1px solid #1e2d47", minWidth: 110 }}>
          <div style={{ fontSize: 10, color: "#4a6080", fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>SYSTEM</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: g.system_health === "OK" ? "#00ff88" : "#ff4455", fontFamily: "'JetBrains Mono', monospace" }}>{g.system_health || "OK"}</div>
        </div>
      </div>

      {/* ── 4 Capital Cards (replaces old P&L cards) ── */}
      <CapitalCards account={account} trades={trades} isPaper={isPaper} />

      {/* Strategy Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16, marginBottom: 20 }}>
        {["saviour_combo", "survivor", "wave_extractor"].map(name => (
          <StrategyCard key={name} name={name} data={s[name]} onStop={handleStop} onReset={handleReset} />
        ))}
      </div>

      {/* Bottom Panel */}
      <div style={{ background: "#0d1526", borderRadius: 14, border: "1px solid #1e2d47", overflow: "hidden" }}>
        <div style={{ display: "flex", borderBottom: "1px solid #1e2d47", background: "#0a1020" }}>
          {[
            { key: "open",  label: `OPEN POSITIONS (${openCount})` },
            { key: "log",   label: `TRADE LOG (${trades.filter(t => t.status === "CLOSED").length})` },
            { key: "daily", label: "DAILY P&L" },
          ].map(({ key, label }) => (
            <button key={key} onClick={() => setTab(key)} style={{
              padding: "12px 20px", border: "none", background: "transparent",
              color: tab === key ? "#00ff88" : "#4a6080",
              fontWeight: 700, fontSize: 11, letterSpacing: 1.2, cursor: "pointer",
              borderBottom: tab === key ? "2px solid #00ff88" : "2px solid transparent",
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              {label}
            </button>
          ))}
        </div>
        <div style={{ padding: 20 }}>
          {tab === "open"  && <OpenTradesPanel trades={trades} />}
          {tab === "log"   && <TradeLog trades={trades} />}
          {tab === "daily" && <DailyPnlBar trades={trades} />}
        </div>
      </div>

      <div style={{ textAlign: "center", marginTop: 16, fontSize: 10, color: "#1e2d47", letterSpacing: 1 }}>
        LAST UPDATE: {data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : "—"}
      </div>
    </div>
  )
}