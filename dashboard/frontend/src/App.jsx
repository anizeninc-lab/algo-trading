import { useState, useEffect, useRef, useCallback } from "react"
import axios from "axios"

const API = "http://92.4.90.188:8081"
const WS  = "ws://92.4.90.188:8081/ws/updates"

const C = {
  bg:       "#060b14",
  panel:    "#0a1220",
  card:     "#0d1526",
  border:   "#1a2840",
  border2:  "#0f1e35",
  text:     "#c8d8f0",
  muted:    "#3a5070",
  dim:      "#1e3050",
  green:    "#00e87a",
  red:      "#ff3d5a",
  blue:     "#3b82f6",
  orange:   "#f59e0b",
  cyan:     "#06b6d4",
  purple:   "#8b5cf6",
  yellow:   "#eab308",
}

const pnlC  = v => (v || 0) >= 0 ? C.green : C.red
const pnlBg = v => (v || 0) >= 0 ? "rgba(0,232,122,0.07)" : "rgba(255,61,90,0.07)"

const STATE_C = { RUNNING: C.green, STOPPED: C.muted, ERROR: C.red, IDLE: C.blue }

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : "—"
const fmtRs = (n, d = 2) => n != null ? `₹${fmt(n, d)}` : "—"
const fmtTime = s => s ? s.slice(11, 19) : "—"
const fmtDate = s => s ? s.slice(0, 10) : "—"

function useCapital() {
  const [capital, setCapital] = useState({ available: 0, used: 0, total: 0 })
  useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/health`)
        if (r.data) setCapital(prev => ({ ...prev }))
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 30000)
    return () => clearInterval(id)
  }, [])
  return capital
}

function Pill({ label, colour, size = 11 }) {
  return (
    <span style={{
      background: colour + "18", color: colour, borderRadius: 3,
      padding: "2px 8px", fontSize: size, fontWeight: 700, letterSpacing: 0.8,
      border: `1px solid ${colour}30`, whiteSpace: "nowrap",
    }}>{label}</span>
  )
}

function MiniSpark({ history, w = 80, h = 28 }) {
  if (!history || history.length < 2) return <div style={{ width: w, height: h }} />
  const vals = history.map(x => x.pnl)
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w
    const y = h - ((v - mn) / rng) * (h - 4) - 2
    return `${x},${y}`
  }).join(" ")
  const last = vals[vals.length - 1]
  const col = last >= 0 ? C.green : C.red
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={col} strokeWidth="1.5" strokeLinejoin="round" opacity="0.8"/>
    </svg>
  )
}

function CapitalBar({ trades, global: g }) {
  const total = 200000
  const used = trades.filter(t => t.status === "OPEN").reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0), 0)
  const available = Math.max(0, total - used)
  const pct = Math.min(100, (used / total) * 100)
  const barC = pct > 80 ? C.red : pct > 50 ? C.orange : C.green

  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}`, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>CAPITAL OVERVIEW</span>
        <span style={{ fontSize: 10, color: barC, fontWeight: 700 }}>{pct.toFixed(1)}% USED</span>
      </div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {[
          { label: "TOTAL",     val: fmtRs(total),     col: C.text },
          { label: "AVAILABLE", val: fmtRs(available), col: C.green },
          { label: "IN TRADES", val: fmtRs(used),      col: used > 0 ? C.orange : C.muted },
          { label: "FREE MARGIN", val: fmtRs(available), col: C.cyan },
        ].map(({ label, val, col }) => (
          <div key={label}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: barC, borderRadius: 2, transition: "width 0.5s" }} />
      </div>
    </div>
  )
}

function NiftyBox({ market }) {
  const [prev, setPrev] = useState(0)
  const [flash, setFlash] = useState(null)
  const price = market?.nifty_price || 0
  useEffect(() => {
    if (prev && price && price !== prev) {
      setFlash(price > prev ? "up" : "down")
      setTimeout(() => setFlash(null), 500)
    }
    setPrev(price)
  }, [price])
  const col = flash === "up" ? C.green : flash === "down" ? C.red : C.text
  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${flash ? (flash === "up" ? C.green : C.red) : C.border}`, minWidth: 160, transition: "border-color 0.3s" }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>NIFTY 50</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: col, fontFamily: "monospace", transition: "color 0.3s" }}>
        {price > 0 ? price.toFixed(2) : "—"}{flash === "up" ? " ▲" : flash === "down" ? " ▼" : ""}
      </div>
      {market?.option_price > 0 && (
        <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>
          OPT: <span style={{ color: C.text }}>₹{market.option_price.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

function StatTile({ label, value, colour, sub, bg }) {
  return (
    <div style={{ background: bg || C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${C.border}`, minWidth: 100 }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color: colour || C.text, fontFamily: "monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: C.muted, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function VixBox({ vix }) {
  if (!vix) return null
  const v = vix.value
  const col = v >= 25 ? C.red : v >= 20 ? C.orange : v >= 16 ? C.yellow : C.green
  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${col}30`, minWidth: 120 }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>INDIA VIX</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 22, fontWeight: 800, color: col, fontFamily: "monospace" }}>{v != null ? v.toFixed(2) : "—"}</span>
        <span style={{ fontSize: 9, color: col, fontWeight: 700 }}>{vix.regime}</span>
      </div>
    </div>
  )
}

function StratCard({ name, data, onStop, onReset, trades }) {
  const title = name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
  const myTrades = trades.filter(t => t.strategy === name)
  const openTrades = myTrades.filter(t => t.status === "OPEN")
  const closedTrades = myTrades.filter(t => t.status === "CLOSED")
  const winCount = closedTrades.filter(t => (t.realised_pnl || 0) > 0).length
  const winRate = closedTrades.length > 0 ? ((winCount / closedTrades.length) * 100).toFixed(0) : "—"
  const capitalUsed = openTrades.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0), 0)
  const capitalLimit = 40000
  const capPct = Math.min(100, (capitalUsed / capitalLimit) * 100)
  const capCol = capPct > 80 ? C.red : capPct > 50 ? C.orange : C.green

  if (!data) return (
    <div style={{ background: C.card, borderRadius: 12, padding: 18, border: `1px solid ${C.border}`, minHeight: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span style={{ color: C.muted, fontSize: 12 }}>{title} — waiting...</span>
    </div>
  )

  const realised   = data.realised_pnl   || 0
  const unrealised = data.unrealised_pnl || 0
  const net        = realised + unrealised
  const col        = STATE_C[data.state] || C.muted
  const capEff     = capitalUsed > 0 ? ((net / capitalUsed) * 100).toFixed(1) : "—"

  return (
    <div style={{ background: C.card, borderRadius: 12, padding: 18, border: `1px solid ${col}25`, display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 800, fontSize: 14, color: C.text }}>{title}</span>
        <Pill label={data.state || "IDLE"} colour={col} />
      </div>

      {/* PnL */}
      <div style={{ background: pnlBg(net), borderRadius: 8, padding: "10px 14px", border: `1px solid ${pnlC(net)}20`, display: "flex", gap: 16, alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>NET P&L</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: pnlC(net), fontFamily: "monospace" }}>{fmtRs(net)}</div>
        </div>
        <div style={{ borderLeft: `1px solid ${C.border}`, paddingLeft: 16, display: "flex", gap: 14 }}>
          <div>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>REALISED</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: pnlC(realised), fontFamily: "monospace" }}>{fmtRs(realised)}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>UNREALISED</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: pnlC(unrealised), fontFamily: "monospace" }}>{fmtRs(unrealised)}</div>
          </div>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <MiniSpark history={data.pnl_history} />
        </div>
      </div>

      {/* Stats grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        {[
          { label: "POSITION",   val: data.position || "FLAT" },
          { label: "OPEN TRADES", val: data.open_trades || 0 },
          { label: "TOTAL",      val: data.total_trades || 0 },
          { label: "WIN RATE",   val: winRate !== "—" ? `${winRate}%` : "—" },
        ].map(({ label, val }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 6, padding: "7px 9px", border: `1px solid ${C.border2}` }}>
            <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 0.8 }}>{label}</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginTop: 2 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Capital allocation */}
      <div style={{ background: C.panel, borderRadius: 8, padding: "10px 12px", border: `1px solid ${C.border2}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <span style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>CAPITAL ALLOCATION</span>
          <span style={{ fontSize: 9, color: capCol, fontWeight: 700 }}>{capPct.toFixed(0)}% OF {fmtRs(capitalLimit)}</span>
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 6 }}>
          <div><div style={{ fontSize: 8, color: C.muted }}>USED</div><div style={{ fontSize: 11, fontWeight: 700, color: capCol, fontFamily: "monospace" }}>{fmtRs(capitalUsed)}</div></div>
          <div><div style={{ fontSize: 8, color: C.muted }}>REMAINING</div><div style={{ fontSize: 11, fontWeight: 700, color: C.green, fontFamily: "monospace" }}>{fmtRs(capitalLimit - capitalUsed)}</div></div>
          <div><div style={{ fontSize: 8, color: C.muted }}>EFFICIENCY</div><div style={{ fontSize: 11, fontWeight: 700, color: C.cyan, fontFamily: "monospace" }}>{capEff !== "—" ? `${capEff}%` : "—"}</div></div>
        </div>
        <div style={{ height: 3, background: C.border, borderRadius: 2 }}>
          <div style={{ height: "100%", width: `${capPct}%`, background: capCol, borderRadius: 2, transition: "width 0.5s" }} />
        </div>
      </div>

      {/* Last signal */}
      <div style={{ background: C.panel, borderRadius: 6, padding: "7px 10px", fontSize: 10, color: "#4a8080", borderLeft: `2px solid ${C.dim}`, fontFamily: "monospace", minHeight: 26 }}>
        {data.last_signal || "— no signal yet —"}
      </div>

      {/* Error */}
      {data.error_message && (
        <div style={{ color: C.red, fontSize: 10, background: "#ff3d5a10", borderRadius: 6, padding: "6px 10px" }}>⚠ {data.error_message}</div>
      )}

      {/* Buttons */}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={() => onStop(name)} disabled={data.state !== "RUNNING"}
          style={{ flex: 1, padding: "7px 0", borderRadius: 6, border: data.state === "RUNNING" ? `1px solid ${C.red}40` : `1px solid ${C.border}`, background: data.state === "RUNNING" ? "#ff3d5a18" : C.border, color: data.state === "RUNNING" ? C.red : C.muted, fontWeight: 700, cursor: data.state === "RUNNING" ? "pointer" : "not-allowed", fontSize: 11, fontFamily: "monospace" }}>
          STOP
        </button>
        <button onClick={() => onReset(name)} disabled={data.state !== "ERROR"}
          style={{ flex: 1, padding: "7px 0", borderRadius: 6, border: data.state === "ERROR" ? `1px solid ${C.orange}40` : `1px solid ${C.border}`, background: data.state === "ERROR" ? "#f59e0b18" : C.border, color: data.state === "ERROR" ? C.orange : C.muted, fontWeight: 700, cursor: data.state === "ERROR" ? "pointer" : "not-allowed", fontSize: 11, fontFamily: "monospace" }}>
          RESET
        </button>
      </div>
    </div>
  )
}

function TradeLedger({ trades }) {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState("ALL")

  const filtered = trades.filter(t => {
    if (filter === "ALL") return true
    if (filter === "OPEN") return t.status === "OPEN"
    if (filter === "CLOSED") return t.status === "CLOSED"
    if (filter === "CANCELLED") return t.status === "CANCELLED"
    return true
  })

  const cols = ["TRADE ID", "STRATEGY", "INSTRUMENT", "DIR", "QTY", "ENTRY TIME", "ENTRY ₹", "EXIT TIME", "EXIT ₹", "PREMIUM", "STATUS", "P&L"]

  if (selected) {
    const t = selected
    const premium = (t.entry_price || 0) * (t.quantity || 0)
    const margin = premium * 5
    const maxRisk = premium * 0.35
    const rr = maxRisk > 0 ? ((t.realised_pnl || 0) / maxRisk).toFixed(2) : "—"
    return (
      <div>
        <button onClick={() => setSelected(null)} style={{ background: C.panel, border: `1px solid ${C.border}`, color: C.cyan, padding: "6px 14px", borderRadius: 6, cursor: "pointer", fontSize: 11, fontFamily: "monospace", marginBottom: 16 }}>
          ← BACK TO LEDGER
        </button>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* Trade Info */}
          <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>TRADE DETAILS</div>
            {[
              ["Trade ID", t.id || "—"],
              ["Strategy", t.strategy || "—"],
              ["Instrument", t.symbol || "—"],
              ["Direction", t.order_type || "—"],
              ["Quantity", t.quantity || "—"],
              ["Status", t.status || "—"],
              ["Broker Order ID", t.broker_order_id || "—"],
            ].map(([label, val]) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: `1px solid ${C.border2}` }}>
                <span style={{ fontSize: 11, color: C.muted }}>{label}</span>
                <span style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{val}</span>
              </div>
            ))}
          </div>
          {/* Capital Info */}
          <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>CAPITAL & RISK</div>
            {[
              ["Entry Price", fmtRs(t.entry_price)],
              ["Exit Price", fmtRs(t.exit_price)],
              ["Premium Paid/Recv", fmtRs(premium)],
              ["Est. Margin Used", fmtRs(margin)],
              ["Max Capital at Risk", fmtRs(maxRisk)],
              ["Realised P&L", fmtRs(t.realised_pnl)],
              ["Risk/Reward", rr],
            ].map(([label, val]) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: `1px solid ${C.border2}` }}>
                <span style={{ fontSize: 11, color: C.muted }}>{label}</span>
                <span style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{val}</span>
              </div>
            ))}
          </div>
          {/* Timing */}
          <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>EXECUTION TIMING</div>
            {[
              ["Entry Time", t.entry_time ? t.entry_time.slice(0, 19).replace("T", " ") : "—"],
              ["Exit Time", t.exit_time ? t.exit_time.slice(0, 19).replace("T", " ") : "—"],
              ["Duration", t.entry_time && t.exit_time ? `${Math.round((new Date(t.exit_time) - new Date(t.entry_time)) / 60000)} min` : "—"],
              ["Notes", t.notes || "—"],
            ].map(([label, val]) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: `1px solid ${C.border2}` }}>
                <span style={{ fontSize: 11, color: C.muted }}>{label}</span>
                <span style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{val}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Filter bar */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {["ALL", "OPEN", "CLOSED", "CANCELLED"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "5px 12px", borderRadius: 5, border: `1px solid ${filter === f ? C.cyan : C.border}`,
            background: filter === f ? C.cyan + "20" : "transparent",
            color: filter === f ? C.cyan : C.muted,
            fontSize: 10, fontWeight: 700, cursor: "pointer", fontFamily: "monospace",
          }}>{f} ({trades.filter(t => f === "ALL" ? true : t.status === f).length})</button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No trades found</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ background: C.panel }}>
                {cols.map(h => (
                  <th key={h} style={{ padding: "9px 12px", textAlign: "left", fontWeight: 700, color: C.muted, fontSize: 9, letterSpacing: 1, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((t, i) => {
                const premium = (t.entry_price || 0) * (t.quantity || 0)
                const statusCol = t.status === "OPEN" ? C.blue : t.status === "CLOSED" ? C.green : C.muted
                return (
                  <tr key={t.id || i}
                    onClick={() => setSelected(t)}
                    style={{ borderBottom: `1px solid ${C.border2}`, cursor: "pointer" }}
                    onMouseEnter={e => e.currentTarget.style.background = C.panel}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    <td style={{ padding: "8px 12px", color: C.cyan, fontFamily: "monospace", fontSize: 10 }}>{(t.id || "").slice(0, 8)}…</td>
                    <td style={{ padding: "8px 12px", color: C.text }}>{t.strategy}</td>
                    <td style={{ padding: "8px 12px", color: C.text, fontFamily: "monospace", fontSize: 10 }}>{t.symbol}</td>
                    <td style={{ padding: "8px 12px", fontWeight: 700, color: t.order_type === "SELL" ? C.red : C.green }}>{t.order_type}</td>
                    <td style={{ padding: "8px 12px", color: C.text }}>{t.quantity}</td>
                    <td style={{ padding: "8px 12px", color: C.muted, fontFamily: "monospace", fontSize: 10 }}>{fmtTime(t.entry_time)}</td>
                    <td style={{ padding: "8px 12px", color: C.text, fontFamily: "monospace" }}>{fmtRs(t.entry_price)}</td>
                    <td style={{ padding: "8px 12px", color: C.muted, fontFamily: "monospace", fontSize: 10 }}>{fmtTime(t.exit_time)}</td>
                    <td style={{ padding: "8px 12px", color: C.text, fontFamily: "monospace" }}>{fmtRs(t.exit_price)}</td>
                    <td style={{ padding: "8px 12px", color: C.orange, fontFamily: "monospace" }}>{fmtRs(premium)}</td>
                    <td style={{ padding: "8px 12px" }}><Pill label={t.status} colour={statusCol} size={9} /></td>
                    <td style={{ padding: "8px 12px", fontWeight: 700, color: pnlC(t.realised_pnl), fontFamily: "monospace" }}>{fmtRs(t.realised_pnl)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ExecutionLog({ trades }) {
  const logs = []
  trades.forEach(t => {
    if (t.entry_time) logs.push({
      time: t.entry_time,
      type: "ENTRY",
      msg: `${t.order_type} ${t.symbol} @ ₹${fmt(t.entry_price)} (Qty ${t.quantity})`,
      strategy: t.strategy,
      orderId: t.broker_order_id,
      col: t.order_type === "SELL" ? C.red : C.green,
    })
    if (t.exit_time) logs.push({
      time: t.exit_time,
      type: "EXIT",
      msg: `EXIT ${t.symbol} @ ₹${fmt(t.exit_price)} (P&L: ${fmtRs(t.realised_pnl)})`,
      strategy: t.strategy,
      orderId: t.broker_order_id,
      col: pnlC(t.realised_pnl),
    })
  })
  logs.sort((a, b) => new Date(b.time) - new Date(a.time))

  if (logs.length === 0) return (
    <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No execution history yet</div>
  )

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 400, overflowY: "auto" }}>
      {logs.map((log, i) => (
        <div key={i} style={{ display: "flex", gap: 12, padding: "8px 12px", background: C.panel, borderRadius: 6, border: `1px solid ${C.border2}`, alignItems: "flex-start" }}>
          <div style={{ fontSize: 10, color: C.muted, fontFamily: "monospace", whiteSpace: "nowrap", marginTop: 1 }}>{fmtTime(log.time)}</div>
          <Pill label={log.type} colour={log.col} size={9} />
          <div style={{ fontSize: 11, color: C.text, fontFamily: "monospace", flex: 1 }}>{log.msg}</div>
          <div style={{ fontSize: 10, color: C.muted }}>{log.strategy}</div>
          {log.orderId && <div style={{ fontSize: 9, color: C.dim, fontFamily: "monospace" }}>{log.orderId}</div>}
        </div>
      ))}
    </div>
  )
}

function PerformancePanel({ trades }) {
  const closed = trades.filter(t => t.status === "CLOSED")
  const wins = closed.filter(t => (t.realised_pnl || 0) > 0)
  const losses = closed.filter(t => (t.realised_pnl || 0) < 0)
  const totalPnl = closed.reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.realised_pnl, 0) / wins.length : 0
  const avgLoss = losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.realised_pnl, 0) / losses.length) : 0
  const winRate = closed.length > 0 ? (wins.length / closed.length * 100).toFixed(1) : 0
  const profitFactor = avgLoss > 0 ? (avgWin * wins.length / (avgLoss * losses.length)).toFixed(2) : "—"

  // Daily equity
  const byDate = {}
  closed.forEach(t => {
    if (!t.exit_time) return
    const d = t.exit_time.slice(0, 10)
    byDate[d] = (byDate[d] || 0) + (t.realised_pnl || 0)
  })
  const days = Object.entries(byDate).sort((a, b) => a[0].localeCompare(b[0])).slice(-14)
  const maxAbs = Math.max(...days.map(d => Math.abs(d[1])), 1)

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 }}>
        {[
          { label: "TOTAL TRADES", val: closed.length, col: C.text },
          { label: "WIN RATE", val: `${winRate}%`, col: Number(winRate) >= 50 ? C.green : C.red },
          { label: "AVG WIN", val: fmtRs(avgWin), col: C.green },
          { label: "AVG LOSS", val: fmtRs(-avgLoss), col: C.red },
          { label: "PROFIT FACTOR", val: profitFactor, col: Number(profitFactor) >= 1 ? C.green : C.red },
          { label: "TOTAL P&L", val: fmtRs(totalPnl), col: pnlC(totalPnl) },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Daily equity curve */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>DAILY P&L (LAST 14 DAYS)</div>
        {days.length === 0 ? (
          <div style={{ color: C.muted, textAlign: "center", padding: "16px 0", fontSize: 12 }}>No data yet</div>
        ) : (
          <div style={{ display: "flex", gap: 6, alignItems: "flex-end", height: 80 }}>
            {days.map(([date, pnl]) => {
              const h = Math.max(4, (Math.abs(pnl) / maxAbs) * 68)
              const col = pnl >= 0 ? C.green : C.red
              return (
                <div key={date} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                  <div style={{ fontSize: 9, color: col, fontWeight: 700, fontFamily: "monospace" }}>{pnl >= 0 ? "+" : ""}{pnl.toFixed(0)}</div>
                  <div style={{ width: "100%", height: h, background: col + "50", borderRadius: 3, border: `1px solid ${col}60` }} />
                  <div style={{ fontSize: 8, color: C.muted }}>{date.slice(5).replace("-", "/")}</div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Per-strategy breakdown */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>STRATEGY BREAKDOWN</div>
        {["wave_extractor", "survivor", "saviour_combo"].map(name => {
          const st = closed.filter(t => t.strategy === name)
          const stPnl = st.reduce((s, t) => s + (t.realised_pnl || 0), 0)
          const stWin = st.filter(t => (t.realised_pnl || 0) > 0).length
          const stWR = st.length > 0 ? (stWin / st.length * 100).toFixed(0) : "—"
          return (
            <div key={name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottom: `1px solid ${C.border2}` }}>
              <span style={{ fontSize: 11, color: C.text }}>{name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}</span>
              <div style={{ display: "flex", gap: 16 }}>
                <span style={{ fontSize: 11, color: C.muted }}>{st.length} trades</span>
                <span style={{ fontSize: 11, color: C.cyan }}>{stWR !== "—" ? `${stWR}% WR` : "—"}</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: pnlC(stPnl), fontFamily: "monospace" }}>{fmtRs(stPnl)}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function OpenPositions({ trades }) {
  const open = trades.filter(t => t.status === "OPEN")
  if (open.length === 0) return (
    <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No open positions</div>
  )
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {open.map((t, i) => {
        const unreal = t.unrealised_pnl || 0
        const premium = (t.entry_price || 0) * (t.quantity || 0)
        const estMargin = premium * 5
        return (
          <div key={t.id || i} style={{ background: C.panel, borderRadius: 10, padding: "12px 16px", border: `1px solid ${pnlC(unreal)}20`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>STRATEGY</div><div style={{ fontSize: 12, color: C.text, fontWeight: 700 }}>{t.strategy}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>SYMBOL</div><div style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{t.symbol}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>DIR</div><div style={{ fontSize: 12, fontWeight: 700, color: t.order_type === "SELL" ? C.red : C.green }}>{t.order_type}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>QTY</div><div style={{ fontSize: 12, color: C.text }}>{t.quantity}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>ENTRY ₹</div><div style={{ fontSize: 12, color: C.text, fontFamily: "monospace" }}>{fmtRs(t.entry_price)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>ENTRY TIME</div><div style={{ fontSize: 11, color: C.muted, fontFamily: "monospace" }}>{fmtTime(t.entry_time)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>PREMIUM</div><div style={{ fontSize: 11, color: C.orange, fontFamily: "monospace" }}>{fmtRs(premium)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>EST MARGIN</div><div style={{ fontSize: 11, color: C.cyan, fontFamily: "monospace" }}>{fmtRs(estMargin)}</div></div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>UNREALISED P&L</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: pnlC(unreal), fontFamily: "monospace" }}>{fmtRs(unreal)}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function RiskPanel({ trades, global: g }) {
  const open = trades.filter(t => t.status === "OPEN")
  const totalCapitalAtRisk = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0) * 0.35, 0)
  const totalMarginUsed = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0) * 5, 0)
  const maxDailyLoss = 5000
  const todayPnl = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === new Date().toISOString().slice(0, 10)).reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const ddPct = Math.min(100, (Math.abs(Math.min(0, todayPnl)) / maxDailyLoss) * 100)
  const ddCol = ddPct > 80 ? C.red : ddPct > 50 ? C.orange : C.green

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
        {[
          { label: "MAX CAPITAL AT RISK", val: fmtRs(totalCapitalAtRisk), col: totalCapitalAtRisk > 10000 ? C.red : C.orange },
          { label: "EST MARGIN LOCKED", val: fmtRs(totalMarginUsed), col: C.cyan },
          { label: "DAILY LOSS LIMIT", val: fmtRs(maxDailyLoss), col: C.muted },
          { label: "TODAY P&L", val: fmtRs(todayPnl), col: pnlC(todayPnl) },
          { label: "OPEN POSITIONS", val: open.length, col: open.length > 2 ? C.orange : C.green },
          { label: "MAX CONCURRENT", val: "4", col: C.muted },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Drawdown meter */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>DAILY LOSS METER</span>
          <span style={{ fontSize: 10, color: ddCol, fontWeight: 700 }}>{ddPct.toFixed(1)}% of ₹{maxDailyLoss} limit</span>
        </div>
        <div style={{ height: 8, background: C.border, borderRadius: 4 }}>
          <div style={{ height: "100%", width: `${ddPct}%`, background: ddCol, borderRadius: 4, transition: "width 0.5s" }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
          <span style={{ fontSize: 9, color: C.muted }}>₹0</span>
          <span style={{ fontSize: 9, color: C.orange }}>₹2,500 (50%)</span>
          <span style={{ fontSize: 9, color: C.red }}>₹5,000 (100%)</span>
        </div>
      </div>

      {/* Alerts */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>ACTIVE ALERTS</div>
        {[
          { condition: open.length >= 3, msg: "⚠ High position count — approaching max concurrent trades", col: C.orange },
          { condition: ddPct > 80, msg: "🔴 Daily loss near limit — consider stopping", col: C.red },
          { condition: ddPct > 50, msg: "⚠ 50% of daily loss limit reached", col: C.orange },
          { condition: totalCapitalAtRisk > 15000, msg: "⚠ High capital at risk across open positions", col: C.orange },
        ].filter(a => a.condition).map((a, i) => (
          <div key={i} style={{ fontSize: 11, color: a.col, padding: "6px 10px", background: a.col + "12", borderRadius: 6, marginBottom: 6, border: `1px solid ${a.col}25` }}>{a.msg}</div>
        ))}
        {open.length < 3 && ddPct <= 50 && (
          <div style={{ fontSize: 11, color: C.green, padding: "6px 10px", background: C.green + "12", borderRadius: 6, border: `1px solid ${C.green}25` }}>✓ All systems normal — no active risk alerts</div>
        )}
      </div>
    </div>
  )
}

const DEFAULT = {
  global: { total_pnl: 0, active_strategies: 0, total_strategies: 0, system_health: "OK", broker_status: {}, paper_trade: false },
  strategies: {},
  vix: null,
  market: { nifty_price: 0, nifty_updated: "", option_price: 0, option_symbol: "" },
}

// ─── PASTE THIS COMPONENT INTO App.jsx ───────────────────────────────────────
// Insert anywhere BEFORE the App() function (e.g. after VixBox component)
// Then inside App() return, replace the {/* Kill Switch Bar */} section
// with <ContextBar marketCtx={data.market_ctx} astro={data.astro} />

// ─── Regime colours ───────────────────────────────────────────────────────────
const REGIME_META = {
  trending_bull:   { label: "▲ TREND BULL",    colour: "#00e87a" },
  trending_bear:   { label: "▼ TREND BEAR",    colour: "#ff3d5a" },
  range:           { label: "↔ RANGE",          colour: "#3b82f6" },
  reversal_watch:  { label: "⚡ REVERSAL",      colour: "#f59e0b" },
  opening:         { label: "⏳ OPENING RANGE", colour: "#8b5cf6" },
  closed:          { label: "○ CLOSED",         colour: "#3a5070" },
}

const ASTRO_COLOUR = {
  green: "#00e87a",
  amber: "#f59e0b",
  red:   "#ff3d5a",
}

// ─── PCR Gauge (inline SVG arc) ───────────────────────────────────────────────
function PcrGauge({ pcr = 1.0 }) {
  // Arc from 0.5 to 1.5 mapped to 0–180 degrees
  const pct   = Math.min(Math.max((pcr - 0.5) / 1.0, 0), 1)
  const angle = pct * 180 - 90   // -90 = left, 0 = top, 90 = right
  const rad   = (angle * Math.PI) / 180
  const r     = 28
  const cx    = 36, cy = 36
  const nx    = cx + r * Math.sin(rad)
  const ny    = cy - r * Math.cos(rad)
  const col   = pcr > 1.3 ? "#00e87a" : pcr < 0.7 ? "#ff3d5a" : "#3b82f6"

  // Arc path (semicircle bottom half)
  const arcPath = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`

  return (
    <svg width={72} height={44} viewBox="0 0 72 44">
      <path d={arcPath} fill="none" stroke="#1a2840" strokeWidth={5} strokeLinecap="round" />
      <path d={arcPath} fill="none" stroke={col} strokeWidth={5} strokeLinecap="round"
        strokeDasharray={`${pct * 88} 88`} opacity={0.8} />
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={col} strokeWidth={2} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={3} fill={col} />
      <text x={cx} y={cy + 14} textAnchor="middle" fill={col} fontSize={10} fontWeight="800" fontFamily="monospace">
        {pcr?.toFixed(2)}
      </text>
    </svg>
  )
}

// ─── OI Bar ───────────────────────────────────────────────────────────────────
function OiBar({ ceOi = 0, peOi = 0 }) {
  const total = (ceOi + peOi) || 1
  const cePct = (ceOi / total) * 100
  const pePct = (peOi / total) * 100
  const fmtL  = n => n > 1e6 ? `${(n / 1e6).toFixed(1)}M` : n > 1e3 ? `${(n / 1e3).toFixed(0)}K` : String(n)
  return (
    <div style={{ width: 140 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 8, color: "#3a5070", marginBottom: 3, letterSpacing: 0.5 }}>
        <span style={{ color: "#ff3d5a" }}>CE {fmtL(ceOi)}</span>
        <span style={{ color: "#00e87a" }}>PE {fmtL(peOi)}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "#1a2840", overflow: "hidden", display: "flex" }}>
        <div style={{ width: `${cePct}%`, background: "#ff3d5a", opacity: 0.8 }} />
        <div style={{ width: `${pePct}%`, background: "#00e87a", opacity: 0.8 }} />
      </div>
    </div>
  )
}

// ─── Context Bar (the new panel) ──────────────────────────────────────────────
function ContextBar({ marketCtx, astro }) {
  const ctx    = marketCtx || {}
  const today  = astro?.today
  const regime = REGIME_META[ctx.regime] || { label: ctx.regime || "—", colour: "#3a5070" }
  const aCol   = today ? ASTRO_COLOUR[today.alert_level] : "#3a5070"

  const cell = (label, children, extra = {}) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, ...extra }}>
      <span style={{ fontSize: 8, color: "#3a5070", letterSpacing: 1, fontWeight: 700 }}>{label}</span>
      {children}
    </div>
  )

  return (
    <div style={{
      marginBottom: 10,
      background: "#0a1220",
      borderRadius: 8,
      padding: "10px 16px",
      border: "1px solid #1a2840",
      display: "flex",
      gap: 24,
      alignItems: "center",
      flexWrap: "wrap",
      overflowX: "auto",
    }}>

      {/* Regime */}
      {cell("REGIME",
        <span style={{
          fontSize: 11, fontWeight: 800, color: regime.colour,
          background: regime.colour + "18", borderRadius: 4,
          padding: "2px 8px", border: `1px solid ${regime.colour}30`,
          letterSpacing: 0.5, whiteSpace: "nowrap",
        }}>{regime.label}</span>
      )}

      {/* PCR Gauge */}
      {cell("PCR", <PcrGauge pcr={ctx.pcr} />)}

      {/* OI Bars */}
      {cell("OPEN INTEREST", <OiBar ceOi={ctx.total_ce_oi} peOi={ctx.total_pe_oi} />)}

      {/* OI Deltas */}
      {cell("OI DELTA",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 9, color: (ctx.ce_oi_delta || 0) > 0 ? "#ff3d5a" : "#00e87a", fontWeight: 700 }}>
            CE {(ctx.ce_oi_delta || 0) > 0 ? "+" : ""}{ctx.ce_oi_delta?.toLocaleString() || "—"}
          </span>
          <span style={{ fontSize: 9, color: (ctx.pe_oi_delta || 0) > 0 ? "#00e87a" : "#ff3d5a", fontWeight: 700 }}>
            PE {(ctx.pe_oi_delta || 0) > 0 ? "+" : ""}{ctx.pe_oi_delta?.toLocaleString() || "—"}
          </span>
        </div>
      )}

      {/* Divider */}
      <div style={{ width: 1, height: 40, background: "#1a2840", flexShrink: 0 }} />

      {/* Opening Range */}
      {cell("OPENING RANGE",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {ctx.or_locked ? (
            <>
              <span style={{ fontSize: 9, color: "#00e87a", fontWeight: 700 }}>
                H: {ctx.or_high?.toFixed(0) || "—"}
              </span>
              <span style={{ fontSize: 9, color: "#ff3d5a", fontWeight: 700 }}>
                L: {ctx.or_low?.toFixed(0) || "—"}
              </span>
            </>
          ) : (
            <span style={{ fontSize: 9, color: "#f59e0b", fontWeight: 700 }}>
              {ctx.regime === "opening" ? "⏳ COLLECTING..." : "NOT LOCKED"}
            </span>
          )}
        </div>
      )}

      {/* ATM + Max Pain */}
      {cell("ATM / MAX PAIN",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 9, color: "#3b82f6", fontWeight: 700 }}>
            ATM: {ctx.atm_strike || "—"}
          </span>
          <span style={{ fontSize: 9, color: "#8b5cf6", fontWeight: 700 }}>
            MP: {ctx.max_pain || "—"}
          </span>
        </div>
      )}

      {/* PCR Spike */}
      {ctx.pcr_spike && (
        <div style={{
          background: "#f59e0b18", border: "1px solid #f59e0b40",
          borderRadius: 4, padding: "4px 10px",
          fontSize: 9, fontWeight: 800, color: "#f59e0b", letterSpacing: 0.5,
          animation: "pulse 1s infinite",
        }}>
          ⚡ PCR SPIKE — ENTRIES FROZEN
        </div>
      )}

      {/* Divider */}
      <div style={{ width: 1, height: 40, background: "#1a2840", flexShrink: 0 }} />

      {/* Astro today */}
      {today ? (
        <>
          {cell("ASTRO TODAY",
            <span style={{
              fontSize: 11, fontWeight: 800, color: aCol,
              background: aCol + "18", borderRadius: 4,
              padding: "2px 8px", border: `1px solid ${aCol}30`,
              letterSpacing: 0.5, whiteSpace: "nowrap",
            }}>{today.strength}</span>
          )}
          {cell("BEST WINDOW",
            <span style={{ fontSize: 9, color: "#00e87a", fontWeight: 700 }}>
              {today.best_window}
            </span>
          )}
          {cell("AVOID",
            <span style={{ fontSize: 9, color: "#ff3d5a", fontWeight: 700 }}>
              {today.avoid}
            </span>
          )}
          {!today.trading_allowed && (
            <div style={{
              background: "#ff3d5a18", border: "1px solid #ff3d5a40",
              borderRadius: 4, padding: "4px 10px",
              fontSize: 9, fontWeight: 800, color: "#ff3d5a", letterSpacing: 0.5,
              animation: "pulse 1s infinite",
            }}>
              🚫 ASTRO: NO TRADING TODAY
            </div>
          )}
          {today.trading_allowed && today.qty_multiplier < 1 && (
            <div style={{
              background: "#f59e0b18", border: "1px solid #f59e0b40",
              borderRadius: 4, padding: "4px 10px",
              fontSize: 9, fontWeight: 800, color: "#f59e0b", letterSpacing: 0.5,
            }}>
              ⚠ REDUCED QTY ({today.qty_multiplier * 100}%)
            </div>
          )}
        </>
      ) : (
        cell("ASTRO TODAY",
          <span style={{ fontSize: 9, color: "#3a5070" }}>No data</span>
        )
      )}

      {/* Last update */}
      <div style={{ marginLeft: "auto", fontSize: 8, color: "#3a5070", textAlign: "right", whiteSpace: "nowrap" }}>
        OI: {ctx.oi_updated_at || "—"}
      </div>
    </div>
  )
}


export default function App() {
  const [data,     setData]     = useState(DEFAULT)
  const [trades,   setTrades]   = useState([])
  const [wsStatus, setWsStatus] = useState("CONNECTING")
  const [tab,      setTab]      = useState("positions")
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
        const res = await axios.get(`${API}/api/trades?limit=500`)
        setTrades(res.data.trades || [])
      } catch {}
    }
    fetchTrades()
    const id = setInterval(fetchTrades, 3000)
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

  const g         = data.global
  const s         = data.strategies
  const vix       = data.vix
  const market    = data.market || {}
  const openCount = trades.filter(t => t.status === "OPEN").length
  const todayStr  = now.toISOString().slice(0, 10)
  const todayPnl  = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr).reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const brokerOn  = Object.values(g.broker_status || {}).some(v => v === "CONNECTED")
  const closedCount = trades.filter(t => t.status === "CLOSED").length

  const TABS = [
    { key: "positions", label: `POSITIONS (${openCount})` },
    { key: "ledger",    label: `LEDGER (${trades.length})` },
    { key: "execlog",   label: "EXEC LOG" },
    { key: "perf",      label: "PERFORMANCE" },
    { key: "risk",      label: "RISK & CAPITAL" },
  ]

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace", padding: "16px 20px" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;800&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: #060b14; }
        ::-webkit-scrollbar-thumb { background: #1a2840; border-radius: 4px; }
        button { font-family: inherit; }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, paddingBottom: 14, borderBottom: `1px solid ${C.border}` }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 800, color: "#fff", letterSpacing: -0.5 }}>◈ ALGO TRADING SYSTEM</div>
          <div style={{ fontSize: 9, color: C.muted, marginTop: 3, letterSpacing: 2 }}>SAVIOUR COMBO · SURVIVOR ALGO · WAVE EXTRACTOR</div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: C.muted }}>{now.toLocaleTimeString("en-IN")}</span>
          <Pill label={wsStatus === "CONNECTED" ? "● LIVE" : wsStatus === "RECONNECTING" ? "◌ RECONNECTING" : "○ OFFLINE"} colour={wsStatus === "CONNECTED" ? C.green : wsStatus === "RECONNECTING" ? C.orange : C.red} />
          <Pill label={brokerOn ? "BROKER ON" : "BROKER OFF"} colour={brokerOn ? C.green : C.red} />
          <Pill label={`PAPER: ${g.paper_trade ? "ON" : "OFF"}`} colour={g.paper_trade ? C.orange : C.blue} />
        </div>
      </div>

      {/* Top stats */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <NiftyBox market={market} />
        <StatTile label="CURRENT P&L"    value={fmtRs(g.total_pnl)}  colour={pnlC(g.total_pnl)}  bg={pnlBg(g.total_pnl)} />
        <StatTile label="TODAY REALISED" value={fmtRs(todayPnl)}     colour={pnlC(todayPnl)}     bg={pnlBg(todayPnl)} />
        <StatTile label="OPEN POSITIONS" value={openCount}           colour={openCount > 0 ? C.blue : C.muted} />
        <StatTile label="ACTIVE STRATS"  value={`${g.active_strategies || 0}/${g.total_strategies || 0}`} colour={C.text} />
        <StatTile label="SYSTEM HEALTH"  value={g.system_health || "OK"} colour={g.system_health === "OK" ? C.green : C.red} />
        <VixBox vix={vix} />
      </div>

      <ContextBar marketCtx={data.market_ctx} astro={data.astro} />
      {/* Capital bar */}
      <div style={{ marginBottom: 14 }}>
        <CapitalBar trades={trades} global={g} />
      </div>

      {/* Strategy cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12, marginBottom: 16 }}>
        {["saviour_combo", "survivor", "wave_extractor"].map(name => (
          <StratCard key={name} name={name} data={s[name]} onStop={handleStop} onReset={handleReset} trades={trades} />
        ))}
      </div>

      {/* Bottom panel */}
      <div style={{ background: C.card, borderRadius: 12, border: `1px solid ${C.border}`, overflow: "hidden" }}>
        <div style={{ display: "flex", borderBottom: `1px solid ${C.border}`, background: C.panel, overflowX: "auto" }}>
          {TABS.map(({ key, label }) => (
            <button key={key} onClick={() => setTab(key)} style={{
              padding: "11px 18px", border: "none", background: "transparent",
              color: tab === key ? C.green : C.muted,
              fontWeight: 700, fontSize: 10, letterSpacing: 1, cursor: "pointer",
              borderBottom: tab === key ? `2px solid ${C.green}` : "2px solid transparent",
              whiteSpace: "nowrap",
            }}>{label}</button>
          ))}
        </div>
        <div style={{ padding: 18 }}>
          {tab === "positions" && <OpenPositions trades={trades} />}
          {tab === "ledger"    && <TradeLedger trades={trades} />}
          {tab === "execlog"   && <ExecutionLog trades={trades} />}
          {tab === "perf"      && <PerformancePanel trades={trades} />}
          {tab === "risk"      && <RiskPanel trades={trades} global={g} />}
        </div>
      </div>

      <div style={{ textAlign: "center", marginTop: 12, fontSize: 9, color: C.dim, letterSpacing: 1 }}>
        LAST UPDATE: {data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : "—"} · {trades.length} TRADES LOADED
      </div>
    </div>
  )
}