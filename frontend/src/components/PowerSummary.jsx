export default function PowerSummary({ summary, scopeMode }) {
  if (!summary) return null
  const instant = scopeMode === 'instant'
  return (
    <div className="power-box">
      <div>
        <div className="muted">
          总功率（线性 W 求和 → dBm）{instant && ' · 仅该时刻激活载波'}
        </div>
        <div className="big">{summary.total_power_dbm === null || summary.total_power_dbm === -Infinity
          ? '—'
          : summary.total_power_dbm.toFixed(2)} dBm</div>
        <div className="muted">
          {summary.total_power_w.toFixed(4)} W · {summary.carrier_count} 个载波
          {summary.carrier_count_total != null && summary.carrier_count_total !== summary.carrier_count
            ? `（共 ${summary.carrier_count_total} 个，${summary.inactive_names?.length || 0} 个未激活）`
            : ''}
        </div>
        {instant && summary.inactive_names?.length > 0 && (
          <div className="muted" style={{ marginTop: 4 }}>
            ⏸ 未激活：{summary.inactive_names.join('、')}
          </div>
        )}
      </div>
      <div>
        <div className="muted">常见错误：直接对 dBm 求和</div>
        <div className="wrong">{summary.naive_dbm_sum ?? '—'} dBm</div>
        <div className="hint">dB 是对数尺度，必须先换算 W 相加：P = 10·log₁₀(Σ10^(pᵢ/10))</div>
      </div>
    </div>
  )
}
