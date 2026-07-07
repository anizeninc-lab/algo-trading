
// ── Opportunity Meter ─────────────────────────────────────────────────────────
function OpportunityMeter() {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/opportunities`)
        setData(r.data)
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 5000)
    return () => clearInterval(id)
  }, [])

  if (!data) return <div style={{ color: C.muted, fontSize: 11 }}>Loading opportunity data...</div>

  const stratName = { survivor: "Nifty", bn_survivor: "BankNifty", wave_extractor: "Wave" }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>OPPORTUNITY METER</div>

      {/* ── Total Summary ── */}
      <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}`, display: "flex", gap: 24, flexWrap: "wrap" }}>
        {[
          { label: "DETECTED",  val: data.total_detected,  col: C.cyan },
          { label: "EXECUTED",  val: data.total_executed,  col: C.green },
          { label: "BLOCKED",   val: data.total_blocked,   col: C.red },
          { label: "HIT RATE",  val: data.total_hit_rate + "%", col: data.total_hit_rate > 50 ? C.green : C.orange },
        ].map(({ label, val, col }) => (
          <div key={label}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>

      {/* ── Per Strategy ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        {data.strategies.map(s => (
          <div key={s.strategy} style={{ background: C.card, borderRadius: 10, padding: "14px 16px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>{stratName[s.strategy] || s.strategy}</div>
            <div style={{ display: "flex", gap: 14, marginBottom: 10, flexWrap: "wrap" }}>
              {[
                { label: "DETECTED", val: s.detected, col: C.cyan },
                { label: "EXECUTED", val: s.executed, col: C.green },
                { label: "HIT RATE", val: s.hit_rate + "%", col: s.hit_rate > 50 ? C.green : C.orange },
              ].map(({ label, val, col }) => (
                <div key={label}>
                  <div style={{ fontSize: 9, color: C.muted }}>{label}</div>
                  <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
                </div>
              ))}
            </div>
            {/* Execution bar */}
            <div style={{ marginBottom: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                <span style={{ fontSize: 9, color: C.muted }}>EXECUTION RATE</span>
                <span style={{ fontSize: 9, color: C.green }}>{s.hit_rate}%</span>
              </div>
              <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
                <div style={{ height: "100%", width: `${s.hit_rate}%`, background: C.green, borderRadius: 2, transition: "width 0.5s" }} />
              </div>
            </div>
            {/* Top block reasons */}
            {s.top_reasons.length > 0 && (
              <div>
                <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>TOP BLOCK REASONS</div>
                {s.top_reasons.map((r, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                    <span style={{ fontSize: 9, color: C.muted, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.reason}</span>
                    <span style={{ fontSize: 9, color: C.orange, fontFamily: "monospace" }}>{r.count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
