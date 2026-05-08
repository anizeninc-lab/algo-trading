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

// ─── Kill Switch ──────────────────────────────────────────────────────────────
function KillSwitch({ strategies }) {
  const [confirm, setConfirm]   = useState(false)
  const [killing,  setKilling]  = useState(false)
  const [done,     setDone]     = useState(false)
  const timerRef = useRef(null)

  async function doKill() {
    setKilling(true)
    const names = Object.keys(strategies).filter(n => strategies[n]?.state === "RUNNING")
    await Promise.allSettled(names.map(n => axios.post(`${API}/api/strategy/${n}/stop`)))
    setKilling(false)
    setDone(true)
    setConfirm(false)
    setTimeout(() => setDone(false), 4000)
  }

  function handleClick() {
    if (!confirm) {
      setConfirm(true)
      timerRef.current = setTimeout(() => setConfirm(false), 4000)
    } else {
      clearTimeout(timerRef.current)
      doKill()
    }
  }

  const runningCount = Object.values(strategies).filter(s => s?.state === "RUNNING").length

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {confirm && (
        <span style={{ fontSize: 10, color: C.orange, fontWeight: 700, animation: "pulse 0.5s infinite" }}>
          ⚠ CONFIRM — CLOSES ALL {runningCount} RUNNING
        </span>
      )}
      <button
        onClick={handleClick}
        disabled={killing || runningCount === 0}
        style={{
          padding: "7px 16px",
          borderRadius: 6,
          border: `1px solid ${done ? C.green : confirm ? C.orange : C.red}60`,
          background: done ? C.green + "20" : confirm ? C.orange + "20" : C.red + "18",
          color: done ? C.green : confirm ? C.orange : C.red,
          fontWeight: 800,
          fontSize: 11,
          cursor: runningCount === 0 ? "not-allowed" : "pointer",
          fontFamily: "monospace",
          letterSpacing: 1,
          opacity: runningCount === 0 ? 0.4 : 1,
          transition: "all 0.2s",
        }}
      >
        {killing ? "⏳ STOPPING..." : done ? "✓ ALL STOPPED" : confirm ? "⚡ CLICK AGAIN!" : "⛔ KILL ALL"}
      </button>
    </div>
  )
}

// ─── Sparkline ────────────────────────────────────────────────────────────────
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

// ─── Pill ─────────────────────────────────────────────────────────────────────
function Pill({ label, colour, size = 11 }) {
  return (
    <span style={{
      background: colour + "18", color: colour, borderRadius: 3,
      padding: "2px 8px", fontSize: size, fontWeight: 700, letterSpacing: 0.8,
      border: `1px solid ${colour}30`, whiteSpace: "nowrap",
    }}>{label}</span>
  )
}

// ─── Capital Bar ──────────────────────────────────────────────────────────────
function CapitalBar({ trades, funds }) {
  const TRADING_CAPITAL = 150000
  const open  = trades.filter(t => t.status === "OPEN")
  const inTrades = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0), 0)
  const marginLocked = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0) * 5, 0)
  const upstoxBalance = funds ? funds.total : 0
  const upstoxAvailable = funds ? funds.available : 0
  const closedTrades = trades.filter(t => t.status === "CLOSED")
  const totalPnl = closedTrades.reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const currentCapital = TRADING_CAPITAL + totalPnl
  const pnlPct = ((totalPnl / TRADING_CAPITAL) * 100).toFixed(2)
  const pct = Math.min(100, (marginLocked / TRADING_CAPITAL) * 100)
  const barC = pct > 80 ? C.red : pct > 50 ? C.orange : C.green
  const items = [
    { label: "UPSTOX BALANCE",  val: upstoxBalance > 0 ? fmtRs(upstoxBalance) : "—", col: C.cyan, sub: upstoxAvailable > 0 ? "Avail: " + fmtRs(upstoxAvailable) : null },
    { label: "TRADING CAPITAL", val: fmtRs(TRADING_CAPITAL), col: C.text, sub: "Fixed limit" },
    { label: "CURRENT CAPITAL", val: fmtRs(currentCapital), col: pnlC(totalPnl), sub: (totalPnl >= 0 ? "+" : "") + pnlPct + "%" },
    { label: "MARGIN USED",     val: fmtRs(marginLocked), col: marginLocked > 0 ? C.orange : C.muted, sub: marginLocked > 0 ? pct.toFixed(1) + "% of limit" : null },
  ]





  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>CAPITAL OVERVIEW</span>
        <span style={{ fontSize: 10, color: barC, fontWeight: 700 }}>{pct.toFixed(1)}% USED</span>
      </div>
      {/* Aligned grid — equal columns */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 10 }}>
        {items.map(({ label, val, col, sub }) => (
          <div key={label}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
            {sub && <div style={{ fontSize: 9, color: col, opacity: 0.8, marginTop: 2 }}>{sub}</div>}
          </div>
        ))}
      </div>
      <div style={{ height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: barC, borderRadius: 2, transition: "width 0.5s" }} />
      </div>
    </div>
  )
}

// ─── Nifty Box ────────────────────────────────────────────────────────────────
function NiftyBox({ market }) {
  const [prev, setPrev]   = useState(0)
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

// ─── Stat Tile ────────────────────────────────────────────────────────────────
function StatTile({ label, value, colour, sub, bg }) {
  return (
    <div style={{ background: bg || C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${C.border}`, minWidth: 100 }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color: colour || C.text, fontFamily: "monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: C.muted, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

// ─── VIX Box ─────────────────────────────────────────────────────────────────
function VixBox({ vix }) {
  if (!vix) return null
  const v   = vix.value
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

// ─── Strategy Card ────────────────────────────────────────────────────────────
function StratCard({ name, data, onStop, onReset, trades }) {
  const title       = name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
  const myTrades    = trades.filter(t => t.strategy === name)
  const openTrades  = myTrades.filter(t => t.status === "OPEN")
  const closedTrades = myTrades.filter(t => t.status === "CLOSED")
  const winCount    = closedTrades.filter(t => (t.realised_pnl || 0) > 0).length
  const winRate     = closedTrades.length > 0 ? ((winCount / closedTrades.length) * 100).toFixed(0) : "—"
  const capitalUsed = openTrades.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0), 0)
  const capitalLimit = 40000
  const capPct      = Math.min(100, (capitalUsed / capitalLimit) * 100)
  const capCol      = capPct > 80 ? C.red : capPct > 50 ? C.orange : C.green

  // Live unrealised PnL = sum of all open trades' unrealised_pnl
  const liveUnrealised = openTrades.reduce((s, t) => s + (t.unrealised_pnl || 0), 0)

  if (!data) return (
    <div style={{ background: C.card, borderRadius: 12, padding: 18, border: `1px solid ${C.border}`, minHeight: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span style={{ color: C.muted, fontSize: 12 }}>{title} — waiting...</span>
    </div>
  )

  const realised   = data.realised_pnl || 0
  // Use live unrealised from trades if available, else fall back to state_store
  const unrealised = liveUnrealised !== 0 ? liveUnrealised : (data.unrealised_pnl || 0)
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
            <div style={{ fontSize: 13, fontWeight: 700, color: pnlC(unrealised), fontFamily: "monospace" }}>
              {fmtRs(unrealised)}
              {openTrades.length > 0 && <span style={{ fontSize: 8, color: C.cyan, marginLeft: 4 }}>LIVE</span>}
            </div>
          </div>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <MiniSpark history={data.pnl_history} />
        </div>
      </div>

      {/* Stats grid — aligned */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        {[
          { label: "POSITION",    val: data.position || "FLAT" },
          { label: "OPEN TRADES", val: openTrades.length },
          { label: "WIN RATE",    val: winRate !== "—" ? `${winRate}%` : "—" },
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
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 6 }}>
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

// ─── Open Positions — Live PnL ────────────────────────────────────────────────
function OpenPositions({ trades, market }) {
  const open = trades.filter(t => t.status === "OPEN")
  if (open.length === 0) return (
    <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No open positions</div>
  )

  const totalUnrealised = open.reduce((s, t) => s + (t.unrealised_pnl || 0), 0)

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Summary row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 14px", background: pnlBg(totalUnrealised), borderRadius: 8, border: `1px solid ${pnlC(totalUnrealised)}20` }}>
        <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>
          {open.length} OPEN POSITION{open.length !== 1 ? "S" : ""}
        </span>
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: C.muted }}>TOTAL UNREALISED</span>
          <span style={{ fontSize: 18, fontWeight: 800, color: pnlC(totalUnrealised), fontFamily: "monospace" }}>
            {fmtRs(totalUnrealised)}
          </span>
          <span style={{ fontSize: 9, color: C.cyan, fontWeight: 700 }}>● LIVE</span>
        </div>
      </div>

      {open.map((t, i) => {
        const unreal    = t.unrealised_pnl || 0
        const entryPremium = (t.entry_price || 0) * (t.quantity || 0)
        const currentLtp   = t.current_ltp && t.current_ltp > 0 ? t.current_ltp : t.entry_price
        const premium      = currentLtp * (t.quantity || 0)
        const estMargin    = entryPremium * 5
        // Live PnL % change
        const pnlPct    = entryPremium > 0 ? ((unreal / entryPremium) * 100).toFixed(1) : null

        return (
          <div key={t.id || i} style={{
            background: C.panel,
            borderRadius: 10,
            padding: "12px 16px",
            border: `1px solid ${pnlC(unreal)}25`,
            display: "grid",
            gridTemplateColumns: "repeat(8, 1fr) auto",
            alignItems: "center",
            gap: 12,
          }}>
            <div>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>STRATEGY</div>
              <div style={{ fontSize: 11, color: C.text, fontWeight: 700 }}>{t.strategy}</div>
            </div>
            <div style={{ gridColumn: "span 2" }}>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>SYMBOL</div>
              <div style={{ fontSize: 10, color: C.text, fontFamily: "monospace" }}>{t.symbol}</div>
            </div>
            <div>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>DIR</div>
              <div style={{ fontSize: 12, fontWeight: 700, color: t.order_type === "SELL" ? C.red : C.green }}>{t.order_type}</div>
            </div>
            <div>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>QTY</div>
              <div style={{ fontSize: 12, color: C.text }}>{t.quantity}</div>
            </div>
            <div>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>ENTRY ₹</div>
              <div style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{fmtRs(t.entry_price)}</div>
            </div>
            <div>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>PREMIUM</div>
              <div style={{ fontSize: 11, color: C.orange, fontFamily: "monospace" }}>{fmtRs(premium)}</div>
            </div>
            <div>
              <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>EST MARGIN</div>
              <div style={{ fontSize: 11, color: C.cyan, fontFamily: "monospace" }}>{fmtRs(estMargin)}</div>
            </div>
            {/* Live PnL — right aligned, prominent */}
            <div style={{
              textAlign: "right",
              background: pnlBg(unreal),
              borderRadius: 8,
              padding: "8px 14px",
              border: `1px solid ${pnlC(unreal)}30`,
              minWidth: 130,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 2 }}>
                <span style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>UNREALISED P&L</span>
                <span style={{ fontSize: 8, color: C.cyan, fontWeight: 700 }}>● LIVE</span>
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, color: pnlC(unreal), fontFamily: "monospace" }}>
                {fmtRs(unreal)}
              </div>
              {pnlPct !== null && (
                <div style={{ fontSize: 9, color: pnlC(unreal), marginTop: 2 }}>
                  {unreal >= 0 ? "+" : ""}{pnlPct}%
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ─── Trade Ledger ─────────────────────────────────────────────────────────────
function TradeLedger({ trades }) {
  const [selected, setSelected] = useState(null)
  const [filter,   setFilter]   = useState("ALL")

  const filtered = trades.filter(t => {
    if (filter === "ALL")       return true
    if (filter === "OPEN")      return t.status === "OPEN"
    if (filter === "CLOSED")    return t.status === "CLOSED"
    if (filter === "CANCELLED") return t.status === "CANCELLED"
    return true
  })

  const cols = ["TRADE ID", "STRATEGY", "INSTRUMENT", "DIR", "QTY", "ENTRY TIME", "ENTRY ₹", "EXIT TIME", "EXIT ₹", "PREMIUM", "STATUS", "P&L"]

  if (selected) {
    const t = selected
    const premium = (t.entry_price || 0) * (t.quantity || 0)
    const margin  = premium * 5
    const maxRisk = premium * 0.35
    const rr      = maxRisk > 0 ? ((t.realised_pnl || 0) / maxRisk).toFixed(2) : "—"
    return (
      <div>
        <button onClick={() => setSelected(null)} style={{ background: C.panel, border: `1px solid ${C.border}`, color: C.cyan, padding: "6px 14px", borderRadius: 6, cursor: "pointer", fontSize: 11, fontFamily: "monospace", marginBottom: 16 }}>
          ← BACK TO LEDGER
        </button>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>TRADE DETAILS</div>
            {[
              ["Trade ID", t.id || "—"], ["Strategy", t.strategy || "—"], ["Instrument", t.symbol || "—"],
              ["Direction", t.order_type || "—"], ["Quantity", t.quantity || "—"], ["Status", t.status || "—"],
              ["Broker Order ID", t.broker_order_id || "—"],
            ].map(([label, val]) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: `1px solid ${C.border2}` }}>
                <span style={{ fontSize: 11, color: C.muted }}>{label}</span>
                <span style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{val}</span>
              </div>
            ))}
          </div>
          <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>CAPITAL & RISK</div>
            {[
              ["Entry Price", fmtRs(t.entry_price)], ["Exit Price", fmtRs(t.exit_price)],
              ["Premium", fmtRs(premium)], ["Est. Margin", fmtRs(margin)],
              ["Max Risk", fmtRs(maxRisk)], ["Realised P&L", fmtRs(t.realised_pnl)],
              ["Risk/Reward", rr],
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
                const premium   = (t.entry_price || 0) * (t.quantity || 0)
                const statusCol = t.status === "OPEN" ? C.blue : t.status === "CLOSED" ? C.green : C.muted
                // For open trades show live unrealised pnl, for closed show realised
                const pnlVal    = t.status === "OPEN" ? (t.unrealised_pnl || 0) : (t.realised_pnl || 0)
                return (
                  <tr key={t.id || i} onClick={() => setSelected(t)}
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
                    <td style={{ padding: "8px 12px", fontWeight: 700, color: pnlC(pnlVal), fontFamily: "monospace" }}>
                      {fmtRs(pnlVal)}
                      {t.status === "OPEN" && <span style={{ fontSize: 8, color: C.cyan, marginLeft: 3 }}>●</span>}
                    </td>
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

// ─── Execution Log ────────────────────────────────────────────────────────────
function ExecutionLog({ trades }) {
  const logs = []
  trades.forEach(t => {
    if (t.entry_time) logs.push({ time: t.entry_time, type: "ENTRY", msg: `${t.order_type} ${t.symbol} @ ₹${fmt(t.entry_price)} (Qty ${t.quantity})`, strategy: t.strategy, orderId: t.broker_order_id, col: t.order_type === "SELL" ? C.red : C.green })
    if (t.exit_time)  logs.push({ time: t.exit_time, type: "EXIT", msg: `EXIT ${t.symbol} @ ₹${fmt(t.exit_price)} (P&L: ${fmtRs(t.realised_pnl)})`, strategy: t.strategy, orderId: t.broker_order_id, col: pnlC(t.realised_pnl) })
  })
  logs.sort((a, b) => new Date(b.time) - new Date(a.time))
  if (logs.length === 0) return <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No execution history yet</div>
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

// ─── Performance Panel ────────────────────────────────────────────────────────
function PerformancePanel({ trades }) {
  const closed = trades.filter(t => t.status === "CLOSED")
  const wins   = closed.filter(t => (t.realised_pnl || 0) > 0)
  const losses = closed.filter(t => (t.realised_pnl || 0) < 0)
  const totalPnl = closed.reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const avgWin   = wins.length > 0 ? wins.reduce((s, t) => s + t.realised_pnl, 0) / wins.length : 0
  const avgLoss  = losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.realised_pnl, 0) / losses.length) : 0
  const winRate  = closed.length > 0 ? (wins.length / closed.length * 100).toFixed(1) : 0
  const profitFactor = avgLoss > 0 ? (avgWin * wins.length / (avgLoss * losses.length)).toFixed(2) : "—"
  const byDate = {}
  closed.forEach(t => { if (!t.exit_time) return; const d = t.exit_time.slice(0, 10); byDate[d] = (byDate[d] || 0) + (t.realised_pnl || 0) })
  const days   = Object.entries(byDate).sort((a, b) => a[0].localeCompare(b[0])).slice(-14)
  const maxAbs = Math.max(...days.map(d => Math.abs(d[1])), 1)
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 }}>
        {[
          { label: "TOTAL TRADES",   val: closed.length,    col: C.text },
          { label: "WIN RATE",       val: `${winRate}%`,    col: Number(winRate) >= 50 ? C.green : C.red },
          { label: "AVG WIN",        val: fmtRs(avgWin),    col: C.green },
          { label: "AVG LOSS",       val: fmtRs(-avgLoss),  col: C.red },
          { label: "PROFIT FACTOR",  val: profitFactor,     col: Number(profitFactor) >= 1 ? C.green : C.red },
          { label: "TOTAL P&L",      val: fmtRs(totalPnl),  col: pnlC(totalPnl) },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>DAILY P&L (LAST 14 DAYS)</div>
        {days.length === 0 ? (
          <div style={{ color: C.muted, textAlign: "center", padding: "16px 0", fontSize: 12 }}>No data yet</div>
        ) : (
          <div style={{ display: "flex", gap: 6, alignItems: "flex-end", height: 80 }}>
            {days.map(([date, pnl]) => {
              const h   = Math.max(4, (Math.abs(pnl) / maxAbs) * 68)
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
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>DAILY BREAKDOWN</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {["DATE","TRADES","TOTAL P&L","BEST TRADE","WORST TRADE","ROI"].map(h => (
                <th key={h} style={{ padding: "6px 8px", textAlign: h === "DATE" ? "left" : "right", color: C.muted, fontWeight: 700, fontSize: 9, letterSpacing: 1 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries((() => {
              const byD = {}
              closed.forEach(t => {
                if (!t.exit_time) return
                const d = t.exit_time.slice(0, 10)
                if (!byD[d]) byD[d] = { trades: 0, pnl: 0, best: -Infinity, worst: Infinity }
                byD[d].trades++
                byD[d].pnl += (t.realised_pnl || 0)
                byD[d].best = Math.max(byD[d].best, t.realised_pnl || 0)
                byD[d].worst = Math.min(byD[d].worst, t.realised_pnl || 0)
              })
              return byD
            })()).sort((a, b) => b[0].localeCompare(a[0])).map(([date, d]) => (
              <tr key={date} style={{ borderBottom: `1px solid ${C.border}20` }}>
                <td style={{ padding: "7px 8px", color: C.text, fontFamily: "monospace" }}>{date}</td>
                <td style={{ padding: "7px 8px", textAlign: "right", color: C.muted }}>{d.trades}</td>
                <td style={{ padding: "7px 8px", textAlign: "right", color: pnlC(d.pnl), fontWeight: 700, fontFamily: "monospace" }}>{fmtRs(d.pnl)}</td>
                <td style={{ padding: "7px 8px", textAlign: "right", color: C.green, fontFamily: "monospace" }}>{fmtRs(d.best)}</td>
                <td style={{ padding: "7px 8px", textAlign: "right", color: C.red, fontFamily: "monospace" }}>{fmtRs(d.worst)}</td>
                <td style={{ padding: "7px 8px", textAlign: "right", color: pnlC(d.pnl), fontFamily: "monospace" }}>{(d.pnl / 200000 * 100).toFixed(3)}%</td>
              </tr>
            ))}
            <tr style={{ borderTop: `1px solid ${C.border}` }}>
              <td style={{ padding: "7px 8px", color: C.text, fontWeight: 700 }}>TOTAL</td>
              <td style={{ padding: "7px 8px", textAlign: "right", color: C.muted, fontWeight: 700 }}>{closed.length}</td>
              <td style={{ padding: "7px 8px", textAlign: "right", color: pnlC(totalPnl), fontWeight: 700, fontFamily: "monospace" }}>{fmtRs(totalPnl)}</td>
              <td colSpan={2}></td>
              <td style={{ padding: "7px 8px", textAlign: "right", color: pnlC(totalPnl), fontWeight: 700, fontFamily: "monospace" }}>{(totalPnl / 200000 * 100).toFixed(3)}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Risk Panel ───────────────────────────────────────────────────────────────
function RiskPanel({ trades }) {
  const open               = trades.filter(t => t.status === "OPEN")
  const totalCapitalAtRisk = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0) * 0.35, 0)
  const totalMarginUsed    = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0) * 5, 0)
  const maxDailyLoss       = 5000
  const todayStr           = new Date().toISOString().slice(0, 10)
  const todayPnl           = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr).reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const ddPct              = Math.min(100, (Math.abs(Math.min(0, todayPnl)) / maxDailyLoss) * 100)
  const ddCol              = ddPct > 80 ? C.red : ddPct > 50 ? C.orange : C.green
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
        {[
          { label: "MAX CAPITAL AT RISK", val: fmtRs(totalCapitalAtRisk), col: totalCapitalAtRisk > 10000 ? C.red : C.orange },
          { label: "EST MARGIN LOCKED",   val: fmtRs(totalMarginUsed),    col: C.cyan },
          { label: "DAILY LOSS LIMIT",    val: fmtRs(maxDailyLoss),       col: C.muted },
          { label: "TODAY P&L",           val: fmtRs(todayPnl),           col: pnlC(todayPnl) },
          { label: "OPEN POSITIONS",      val: open.length,               col: open.length > 2 ? C.orange : C.green },
          { label: "MAX CONCURRENT",      val: "4",                       col: C.muted },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>DAILY LOSS METER</span>
          <span style={{ fontSize: 10, color: ddCol, fontWeight: 700 }}>{ddPct.toFixed(1)}% of ₹{maxDailyLoss} limit</span>
        </div>
        <div style={{ height: 8, background: C.border, borderRadius: 4 }}>
          <div style={{ height: "100%", width: `${ddPct}%`, background: ddCol, borderRadius: 4, transition: "width 0.5s" }} />
        </div>
      </div>
    </div>
  )
}



function PaperToggle({ isPaper }) {
  const [loading, setLoading] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const timerRef = useRef(null)

  async function handleToggle() {
    if (isPaper) {
      if (!confirm) {
        setConfirm(true)
        timerRef.current = setTimeout(() => setConfirm(false), 4000)
        return
      }
      clearTimeout(timerRef.current)
      setConfirm(false)
    }
    setLoading(true)
    try {
      await axios.post(`${API}/api/toggle-paper`)
    } catch (e) {
      alert("Toggle failed: " + (e.response?.data?.error || e.message))
    }
    setLoading(false)
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {confirm && (
        <span style={{ fontSize: 10, color: C.red, fontWeight: 700, animation: "pulse 0.5s infinite" }}>
          ⚠ SWITCHES TO REAL MONEY!
        </span>
      )}
      <div onClick={loading ? null : handleToggle} style={{
        display: "flex", alignItems: "center", gap: 8,
        background: isPaper ? "rgba(245,158,11,0.12)" : "rgba(255,61,90,0.12)",
        border: `1px solid ${isPaper ? C.orange : C.red}50`,
        borderRadius: 8, padding: "6px 12px",
        cursor: loading ? "not-allowed" : "pointer",
        transition: "all 0.2s", opacity: loading ? 0.6 : 1,
      }}>
        <div style={{
          width: 36, height: 18, borderRadius: 9,
          background: isPaper ? C.orange + "40" : C.red + "40",
          border: `1px solid ${isPaper ? C.orange : C.red}60`,
          position: "relative", transition: "all 0.3s",
        }}>
          <div style={{
            position: "absolute", top: 2,
            left: isPaper ? 18 : 2,
            width: 12, height: 12, borderRadius: "50%",
            background: isPaper ? C.orange : C.red,
            transition: "left 0.3s",
            boxShadow: `0 0 6px ${isPaper ? C.orange : C.red}`,
          }} />
        </div>
        <span style={{ fontSize: 11, fontWeight: 800, color: isPaper ? C.orange : C.red, fontFamily: "monospace", letterSpacing: 0.5 }}>
          {loading ? "..." : isPaper ? "PAPER 🟢" : "LIVE 🔴"}
        </span>
      </div>
    </div>
  )
}// ─── App Root ─────────────────────────────────────────────────────────────────
const DEFAULT = {
  global: { total_pnl: 0, active_strategies: 0, total_strategies: 0, system_health: "OK", broker_status: {}, paper_trade: false },
  strategies: {}, vix: null,
  market: { nifty_price: 0, nifty_updated: "", option_price: 0, option_symbol: "" },
}

export default function App() {
  const [data,     setData]     = useState(DEFAULT)
  const [trades,   setTrades]   = useState([])
  const [funds,    setFunds]    = useState(null)
  const [wsStatus, setWsStatus] = useState("CONNECTING")
  const [tab,      setTab]      = useState("positions")
  const wsRef = useRef(null)
  const [now, setNow] = useState(new Date())

  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id) }, [])

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

  useEffect(() => {
    async function fetchFunds() {
      try {
        const res = await axios.get(`${API}/api/funds`)
        setFunds(res.data)
      } catch {}
    }
    fetchFunds()
    const fid = setInterval(fetchFunds, 30000)
    return () => clearInterval(fid)
  }, [])

  async function handleStop(name) {
    try { await axios.post(`${API}/api/strategy/${name}/stop`) }
    catch (e) { alert(`Stop failed: ${e.response?.data?.error || e.message}`) }
  }
  async function handleReset(name) {
    try { await axios.post(`${API}/api/strategy/${name}/reset`) }
    catch (e) { alert(`Reset failed: ${e.response?.data?.error || e.message}`) }
  }

  const g          = data.global
  const s          = data.strategies
  const vix        = data.vix
  const market     = data.market || {}
  const openCount  = trades.filter(t => t.status === "OPEN").length
  const todayStr   = now.toISOString().slice(0, 10)
  const todayPnl   = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr).reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const brokerOn   = Object.values(g.broker_status || {}).some(v => v === "CONNECTED")

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
        @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.5 } }
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
          <PaperToggle isPaper={g.paper_trade} />
          {/* ── KILL SWITCH ── */}
          <KillSwitch strategies={s} />
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

      {/* Kill Switch Bar */}
      <div style={{ marginBottom: 10, display: "flex", justifyContent: "space-between", alignItems: "center", background: "#0d1526", borderRadius: 8, padding: "10px 16px", border: "1px solid #ff3d5a30" }}>
        <span style={{ fontSize: 10, color: "#3a5070", fontWeight: 700, letterSpacing: 1 }}>⚡ EMERGENCY CONTROLS</span>
        <KillSwitch strategies={s} />
      </div>

      {/* Capital bar */}
      <div style={{ marginBottom: 14 }}>
        <CapitalBar trades={trades} funds={funds} />
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
          {tab === "positions" && <OpenPositions trades={trades} market={market} />}
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
