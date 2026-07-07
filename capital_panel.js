
// ── Capital Intelligence Panel ────────────────────────────────────────────────
function CapitalIntelligencePanel({ trades }) {
  const [capData, setCapData] = React.useState(null)
  const [showConfig, setShowConfig] = React.useState(false)
  const [newCap, setNewCap] = React.useState(150000)
  const [preview, setPreview] = React.useState(null)

  React.useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/capital`)
        setCapData(r.data)
        setNewCap(r.data.per_strategy_cap)
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 5000)
    return () => clearInterval(id)
  }, [])

  function calcPreview(cap) {
    const lots = Math.floor(cap / 40000)
    const risk = lots * 800
    return { lots, risk, daily: risk * 3, cap }
  }

  function handleCapChange(val) {
    setNewCap(val)
    setPreview(calcPreview(val))
  }

  const statusCol = { HEALTHY: C.green, ACTIVE: C.orange, FULL: C.red }

  if (!capData) return (
    <div style={{ background: C.card, borderRadius: 10, padding: 20, border: `1px solid ${C.border}`, color: C.muted, fontSize: 11 }}>
      Loading capital data...
    </div>
  )

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>CAPITAL INTELLIGENCE</span>
        <button onClick={() => setShowConfig(p => !p)}
          style={{ background: showConfig ? C.orange : C.card, color: showConfig ? "#000" : C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          {showConfig ? "✕ CLOSE" : "⚙ CONFIGURE"}
        </button>
      </div>
      <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>TOTAL PORTFOLIO</span>
          <span style={{ fontSize: 10, color: capData.total_pct > 80 ? C.red : capData.total_pct > 50 ? C.orange : C.green, fontWeight: 700 }}>{capData.total_pct}% DEPLOYED</span>
        </div>
        <div style={{ display: "flex", gap: 20, marginBottom: 10, flexWrap: "wrap" }}>
          {[
            { label: "TOTAL CAP",  val: fmtRs(capData.total_cap),     col: C.text },
            { label: "DEPLOYED",   val: fmtRs(capData.total_deployed), col: C.orange },
            { label: "FREE",       val: fmtRs(capData.total_free),     col: C.green },
          ].map(({ label, val, col }) => (
            <div key={label}>
              <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
            </div>
          ))}
        </div>
        <div style={{ height: 6, background: C.border, borderRadius: 3 }}>
          <div style={{ height: "100%", width: `${capData.total_pct}%`, background: capData.total_pct > 80 ? C.red : capData.total_pct > 50 ? C.orange : C.green, borderRadius: 3, transition: "width 0.5s" }} />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
        {capData.strategies.map(s => (
          <div key={s.key} style={{ background: C.card, borderRadius: 10, padding: "14px 16px", border: `1px solid ${(statusCol[s.status] || C.border)}40` }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{s.name.toUpperCase()}</span>
              <span style={{ fontSize: 9, color: statusCol[s.status], fontWeight: 700, background: `${statusCol[s.status]}20`, padding: "2px 6px", borderRadius: 4 }}>{s.status}</span>
            </div>
            <div style={{ display: "flex", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
              <div><div style={{ fontSize: 9, color: C.muted }}>DEPLOYED</div><div style={{ fontSize: 13, fontWeight: 800, color: C.orange, fontFamily: "monospace" }}>{fmtRs(s.deployed)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted }}>FREE</div><div style={{ fontSize: 13, fontWeight: 800, color: C.green, fontFamily: "monospace" }}>{fmtRs(s.free)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted }}>LOTS</div><div style={{ fontSize: 13, fontWeight: 800, color: C.cyan, fontFamily: "monospace" }}>{s.current_lots}/{s.max_lots}</div></div>
            </div>
            <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
              <div style={{ height: "100%", width: `${s.pct}%`, background: statusCol[s.status], borderRadius: 2, transition: "width 0.5s" }} />
            </div>
            <div style={{ fontSize: 9, color: C.muted, marginTop: 4 }}>{s.pct}% of {fmtRs(s.cap)}</div>
          </div>
        ))}
      </div>
      {showConfig && (
        <div style={{ background: C.card, borderRadius: 10, padding: "16px 18px", border: `1px solid ${C.orange}40` }}>
          <div style={{ fontSize: 10, color: C.orange, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>⚙ CAPITAL CONFIGURATION</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
            <span style={{ fontSize: 10, color: C.muted }}>Per-Strategy Cap:</span>
            <input type="range" min={50000} max={500000} step={10000} value={newCap}
              onChange={e => handleCapChange(Number(e.target.value))}
              style={{ width: 200, accentColor: C.orange }} />
            <span style={{ fontSize: 13, fontWeight: 800, color: C.orange, fontFamily: "monospace" }}>{fmtRs(newCap)}</span>
          </div>
          <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
            {[100000, 150000, 200000, 250000, 300000].map(v => (
              <button key={v} onClick={() => handleCapChange(v)}
                style={{ background: newCap === v ? C.orange : C.panel, color: newCap === v ? "#000" : C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 10px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
                {fmtRs(v)}
              </button>
            ))}
          </div>
          {preview && (
            <div style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", marginBottom: 12, border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 8 }}>LIVE PREVIEW</div>
              <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
                {[
                  { label: "MAX LOTS",       val: preview.lots },
                  { label: "MAX RISK/TRADE", val: fmtRs(preview.risk) },
                  { label: "MAX DAILY RISK", val: fmtRs(preview.daily) },
                  { label: "NEW CAP",        val: fmtRs(preview.cap) },
                ].map(({ label, val }) => (
                  <div key={label}>
                    <div style={{ fontSize: 9, color: C.muted }}>{label}</div>
                    <div style={{ fontSize: 12, fontWeight: 800, color: C.orange, fontFamily: "monospace" }}>{val}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => { setShowConfig(false); setPreview(null) }}
              style={{ background: C.panel, color: C.muted, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 14px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
              CANCEL
            </button>
            <button onClick={async () => {
                try {
                  await axios.post(`${API}/api/capital/configure`, { per_strategy_cap: newCap })
                  alert(`Capital updated to ${fmtRs(newCap)} — restart bot to apply`)
                  setShowConfig(false)
                } catch { alert("Failed to update capital") }
              }}
              style={{ background: C.orange, color: "#000", border: "none", borderRadius: 6, padding: "6px 14px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
              APPLY
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
