export default function PowerSummary({ summary }) {
  if (!summary) return null
  const instant = summary.active_carrier_count != null &&
    summary.active_carrier_count !== summary.carrier_count
  return (
    <div>
      {instant && (
        <div className="hint" style={{ marginTop: 0, marginBottom: 6 }}>
          时刻口径：{summary.carrier_count} 个载波中仅 {summary.active_carrier_count} 个此刻激活，
          总功率只叠加激活载波。
        </div>
      )}
      <div className="power-box">
        <div>
          <div className="muted">总功率（线性 W 求和 → dBm）</div>
          <div className="big">
            {summary.active_carrier_count === 0
              ? '—（此刻无激活载波）'
              : `${summary.total_power_dbm.toFixed(2)} dBm`}
          </div>
          <div className="muted">
            {summary.total_power_w.toFixed(4)} W · {summary.active_carrier_count ?? summary.carrier_count}
            {instant ? `/${summary.carrier_count}` : ''} 个激活载波
          </div>
        </div>
        <div>
          <div className="muted">常见错误：直接对 dBm 求和</div>
          <div className="wrong">{summary.naive_dbm_sum ?? '—'} dBm</div>
          <div className="hint">dB 是对数尺度，必须先换算 W 相加：P = 10·log₁₀(Σ10^(pᵢ/10))</div>
        </div>
      </div>
    </div>
  )
}
