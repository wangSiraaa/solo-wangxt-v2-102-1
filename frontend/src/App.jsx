import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import CarrierTable from './components/CarrierTable.jsx'
import RulesPanel, { policyKey } from './components/RulesPanel.jsx'
import FindingsList from './components/FindingsList.jsx'
import PowerSummary from './components/PowerSummary.jsx'
import BandChart from './components/BandChart.jsx'
import SpectrumChart from './components/SpectrumChart.jsx'
import MaskPreview from './components/MaskPreview.jsx'
import TimeScopeBar from './components/TimeScopeBar.jsx'

const EMPTY_RULES = { guard_required_mhz: 1.0, leakage_limit_dbm: -45.0, reuse_policy: {} }
// 时间口径：全天配对 / 考察时刻（本地 HH:MM + UTC 偏移分钟）
const EMPTY_SCOPE = { mode: 'all_day', localTime: '10:00', tzOffset: 480 }

const newCarrier = (i) => ({
  name: `C${i + 1}`, center_mhz: 100 + i * 6, bandwidth_mhz: 4,
  power_dbm: 20, polarization: 'H', mask_name: 'strict', windows: [],
})

export default function App() {
  const [masks, setMasks] = useState([])
  const [carriers, setCarriers] = useState([newCarrier(0)])
  const [rules, setRules] = useState(EMPTY_RULES)
  const [band, setBand] = useState({ low: 80, high: 220 })
  const [scenarios, setScenarios] = useState([])
  const [scenarioId, setScenarioId] = useState(null)
  const [scenarioName, setScenarioName] = useState('未命名场景')
  const [revision, setRevision] = useState(1)
  const [scope, setScope] = useState(EMPTY_SCOPE)
  const [analysis, setAnalysis] = useState(null)
  const [plan, setPlan] = useState(null)
  const [planMeta, setPlanMeta] = useState(null)   // {savedId,status,...} 已保存方案信息
  const [savedPlans, setSavedPlans] = useState([])
  const [planMode, setPlanMode] = useState('guard_only')
  const [planView, setPlanView] = useState(false)
  const [tab, setTab] = useState('spectrum')
  const [selectedPair, setSelectedPair] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.masks().then(setMasks).catch((e) => setError(String(e)))
    refreshScenarios()
  }, [])

  const refreshScenarios = () =>
    api.listScenarios().then(setScenarios).catch(() => {})

  const refreshPlans = useCallback((id) => {
    if (!id) { setSavedPlans([]); return }
    api.listPlans(id).then(setSavedPlans).catch(() => setSavedPlans([]))
  }, [])

  const atTimePayload = useCallback(() => (
    scope.mode === 'instant'
      ? { at_time: { local_time: scope.localTime, tz_offset_minutes: scope.tzOffset } }
      : {}
  ), [scope])

  const runAnalyze = useCallback(async () => {
    setBusy('analyze'); setError('')
    try {
      const res = await api.analyze({
        carriers,
        rules: { ...rules, reuse_policy: normalizePolicy(rules.reuse_policy) },
        plot_grid_mhz: 0.05,
        ...atTimePayload(),
      })
      setAnalysis(res)
      setTab('spectrum')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }, [carriers, rules, atTimePayload])

  const runPlan = useCallback(async () => {
    setBusy('plan'); setError(''); setPlanMeta(null)
    try {
      const res = await api.plan({
        carriers,
        rules: { ...rules, reuse_policy: normalizePolicy(rules.reuse_policy) },
        band_low_mhz: band.low, band_high_mhz: band.high, mode: planMode,
        ...atTimePayload(),
      })
      setPlan(res)
      setPlanView(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }, [carriers, rules, band, planMode, atTimePayload])

  const saveCurrentPlan = async () => {
    if (!scenarioId || !plan?.feasible) return
    setBusy('saveplan'); setError('')
    try {
      const rec = await api.savePlan(scenarioId, {
        label: `${planMode} ${scope.mode === 'instant'
          ? `@${scope.localTime}${tzSuffix}`
          : '全天'}`,
        mode: planMode, band_low_mhz: band.low, band_high_mhz: band.high,
        result: plan,
      })
      setPlanMeta({ savedId: rec.id, status: rec.status })
      await refreshPlans(scenarioId)
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const reuseSavedPlan = async (rec) => {
    setBusy('reuse'); setError('')
    try {
      const out = await api.reusePlan(rec.id)
      setPlan(out.result)
      setPlanMeta({ savedId: out.id, status: out.status })
      setPlanView(false)
    } catch (e) {
      setError(e.status === 409
        ? `方案已过期，不能直接复用：${e.message}。请基于当前编辑重新求解。`
        : e.message)
    } finally { setBusy('') }
  }

  const removeSavedPlan = async (id) => {
    try {
      await api.deletePlan(id)
      await refreshPlans(scenarioId)
      if (planMeta?.savedId === id) setPlanMeta(null)
    } catch (e) { setError(e.message) }
  }

  const loadScenario = async (id) => {
    if (!id) { setScenarioId(null); setRevision(1); return }
    setBusy('load'); setError('')
    try {
      const sc = await api.getScenario(id)
      setScenarioId(sc.id); setScenarioName(sc.name); setRevision(sc.revision)
      setCarriers(sc.carriers.map(({ id: _id, ...c }) => ({
        ...c,
        windows: (c.windows || []).map((w) => ({
          start: w.start, end: w.end, tz_offset_minutes: w.tz_offset_minutes,
        })),
      })))
      setRules({ guard_required_mhz: sc.guard_required_mhz,
                 leakage_limit_dbm: sc.leakage_limit_dbm, reuse_policy: sc.reuse_policy || {} })
      setBand({ low: sc.band_low_mhz, high: sc.band_high_mhz })
      setAnalysis(null); setPlan(null); setPlanMeta(null); setSelectedPair(null)
      await refreshPlans(sc.id)
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const saveScenario = async () => {
    setBusy('save'); setError('')
    const payload = {
      name: scenarioName, description: '', band_low_mhz: band.low, band_high_mhz: band.high,
      guard_required_mhz: rules.guard_required_mhz, leakage_limit_mhz: rules.leakage_limit_dbm,
      reuse_policy: normalizePolicy(rules.reuse_policy), carriers,
    }
    try {
      const saved = scenarioId
        ? await api.updateScenario(scenarioId, payload)
        : await api.createScenario(payload)
      setScenarioId(saved.id)
      setRevision(saved.revision)
      await refreshScenarios()
      await refreshPlans(saved.id)
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const deleteScenario = async () => {
    if (!scenarioId) return
    setBusy('del'); setError('')
    try {
      await api.deleteScenario(scenarioId)
      setScenarioId(null); setSavedPlans([]); setPlanMeta(null); setRevision(1)
      await refreshScenarios()
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const status = analysis?.status
  const shownBands = analysis?.bands
  const shownFindings = analysis?.findings || []
  const plannedSpectrum = plan?.feasible ? plan.spectrum : null
  const plannedBands = plan?.feasible ? plan.bands : null
  const shownSpectrum = planView ? plannedSpectrum : analysis?.spectrum
  const spectrumBands = planView ? plannedBands : analysis?.bands
  const shownFindingsList = planView ? plan?.post_check?.findings : shownFindings
  const shownPower = planView ? plan?.post_check?.power_summary : analysis?.power_summary
  const shownStatus = planView ? plan?.post_check?.status : status
  const tzSuffix = (() => {
    const h = scope.tzOffset / 60
    return `${scope.tzOffset >= 0 ? '+' : ''}${Number.isInteger(h) ? h : h.toFixed(2)}`
  })()
  const timeLabel = scope.mode === 'instant'
    ? `考察时刻 ${scope.localTime}（UTC${tzSuffix}）`
    : '全天配对口径'

  return (
    <>
      <header className="app-header">
        <h1>📡 频谱工作台</h1>
        <span className="badge-offline">离线简化模型 · 不连接设备 · 不生成发射指令</span>
        <span className="spacer" />
        <span className="badge-time" title="分析、频段图、掩模图与规划均按此时间口径">{timeLabel}</span>
        {shownStatus && (
          <span className={`status-pill ${shownStatus}`}>
            {shownStatus === 'ok' ? '满足规则' : shownStatus === 'conflict' ? '存在冲突' : '需要关注'}
          </span>
        )}
      </header>

      <div className="layout">
        {/* 左列：录入与规则 */}
        <div>
          <div className="panel">
            <h2>场景（PostgreSQL）{scenarioId && <span className="rev-badge">修订 v{revision}</span>}</h2>
            <div className="row">
              <select className="field" style={{ flex: 1 }}
                      value={scenarioId ?? ''} onChange={(e) => loadScenario(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— 未保存的编辑 —</option>
                {scenarios.map((s) => <option key={s.id} value={s.id}>
                  {s.name}（{s.carrier_count}，v{s.revision}）
                </option>)}
              </select>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <input className="field" style={{ flex: 1 }} value={scenarioName}
                     onChange={(e) => setScenarioName(e.target.value)} placeholder="场景名" />
              <button className="primary" onClick={saveScenario} disabled={!!busy}>
                {scenarioId ? '更新' : '保存'}
              </button>
              {scenarioId && <button className="danger" onClick={deleteScenario} disabled={!!busy}>删除</button>}
            </div>
            {scenarioId && savedPlans.length > 0 && (
              <div className="saved-plans">
                <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>已保存规划：</div>
                {savedPlans.map((p) => (
                  <div key={p.id} className={`saved-plan ${p.status}`}>
                    <button className="link-btn" disabled={!!busy || p.status === 'expired'}
                            title={p.status === 'expired' ? p.expire_reason : '复用该方案'}
                            onClick={() => reuseSavedPlan(p)}>
                      {p.status === 'expired' ? '⏸' : '✓'} {p.label || `${p.mode}`}
                      <span className="muted">（基于 v{p.scenario_revision}）</span>
                    </button>
                    {p.status === 'expired'
                      ? <span className="plan-stale" title={p.expire_reason}>已过期</span>
                      : <span className="plan-ok">可执行</span>}
                    <button className="danger tiny" disabled={!!busy}
                            onClick={() => removeSavedPlan(p.id)}>删</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="panel">
            <h2>载波录入</h2>
            <CarrierTable carriers={carriers} masks={masks} onChange={setCarriers}
                          onAdd={() => setCarriers([...carriers, newCarrier(carriers.length)])}
                          onRemove={(i) => setCarriers(carriers.filter((_, j) => j !== i))}
                          disabled={!!busy} />
          </div>

          <div className="panel">
            <h2>规则与极化复用</h2>
            <RulesPanel rules={rules} onChange={setRules} disabled={!!busy} />
          </div>

          <div className="panel">
            <TimeScopeBar scope={scope} onChange={setScope} disabled={!!busy} />
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" onClick={runAnalyze} disabled={!!busy || !carriers.length}>
                {busy === 'analyze' ? '计算中…' : '▶ 检查冲突 / 绘制频段'}
              </button>
            </div>
            {error && <div className="err-msg">{error}</div>}
            <div className="hint">检查：仅同一时刻同时激活的载波对参与频带重叠 · 保护带不足 · 掩模尾部越界（定向）；功率在线性域汇总。</div>
          </div>
        </div>

        {/* 右列：结果 */}
        <div>
          <div className="panel">
            <div className="tabs">
              <button className={tab === 'spectrum' ? 'on' : ''} onClick={() => setTab('spectrum')}>频段与发射谱</button>
              <button className={tab === 'masks' ? 'on' : ''} onClick={() => setTab('masks')}>掩模库</button>
            </div>

            {tab === 'spectrum' && (
              <>
                <BandChart bands={shownBands} findings={shownFindings} plan={plan}
                           selectedPair={selectedPair}
                           onPick={(name) => setSelectedPair(
                             selectedPair && selectedPair.includes(name) && selectedPair.length === 2
                               ? null
                               : selectedPair
                                 ? [selectedPair[0], name]
                                 : [name])} />
                <div className="row" style={{ marginBottom: 4 }}>
                  {plan?.feasible && (
                    <span className="seg">
                      <button className={!planView ? 'on' : ''} onClick={() => setPlanView(false)}>
                        录入频带（冲突着色）
                      </button>
                      <button className={planView ? 'on allowed' : ''} onClick={() => setPlanView(true)}>
                        规划后频带（复核 {plan.post_check?.counts.error}/{plan.post_check?.counts.warning}/{plan.post_check?.counts.pending}）
                      </button>
                    </span>
                  )}
                </div>
                <SpectrumChart spectrum={shownSpectrum} bands={spectrumBands} />
                <div className="plot-note">
                  提示：灰色虚线频段/点线谱线表示该考察时刻未激活；点击频段条选择载波；点击冲突条目高亮载波对。
                </div>
              </>
            )}
            {tab === 'masks' && <MaskPreview masks={masks} />}
          </div>

          <div className="panel">
            <h2>OR-Tools 频率规划</h2>
            <div className="row">
              <label className="field-label">可用频段</label>
              <input className="field" type="number" style={{ width: 84 }} value={band.low}
                     onChange={(e) => setBand({ ...band, low: parseFloat(e.target.value) })} />
              <span className="muted">–</span>
              <input className="field" type="number" style={{ width: 84 }} value={band.high}
                     onChange={(e) => setBand({ ...band, high: parseFloat(e.target.value) })} />
              <span className="muted">MHz</span>
              <span className="seg">
                <button className={planMode === 'guard_only' ? 'on' : ''}
                        onClick={() => setPlanMode('guard_only')}>仅保护间隔</button>
                <button className={planMode === 'mask_aware' ? 'on' : ''}
                        onClick={() => setPlanMode('mask_aware')}>掩模感知</button>
              </span>
              <button className="primary" onClick={runPlan} disabled={!!busy || !carriers.length}>
                {busy === 'plan' ? '求解中…' : '求解频率位置'}
              </button>
              {scenarioId && plan?.feasible && (
                <button onClick={saveCurrentPlan} disabled={!!busy || planMeta?.status === 'executable'}
                        title="绑定场景修订与时间窗口后持久化">
                  {planMeta?.savedId ? '已保存 ✓' : '💾 保存方案'}
                </button>
              )}
            </div>
            <div className="hint">
              按{timeLabel}工作：时间不重叠的载波对可同频；掩模感知模式对同时激活对按双向尾部泄漏达标反算间隔（含 0.5 dB 裕量）。
              保存的方案绑定场景修订与时间窗口，后续编辑时间使其失效后会标为过期并拒绝直接复用。
            </div>
            {plan && <PlanResult plan={plan} />}
          </div>

          <div className="panel">
            <h2>冲突定位{planView ? '（规划后复核）' : ''} <span className="muted" style={{ fontWeight: 400 }}>· {timeLabel}</span></h2>
            <FindingsList findings={shownFindingsList} selectedPair={selectedPair}
                          onSelect={(p) => setSelectedPair(
                            JSON.stringify(selectedPair) === JSON.stringify(p) ? null : p)} />
          </div>

          <div className="panel">
            <h2>功率汇总{planView ? '（规划后）' : ''}</h2>
            <PowerSummary summary={shownPower} scopeMode={scope.mode} />
          </div>
        </div>
      </div>
    </>
  )
}

function PlanResult({ plan }) {
  const [open, setOpen] = useState(true)
  if (!plan.feasible) {
    return (
      <div className="err-msg" style={{ marginTop: 8 }}>
        ✗ {plan.status}：{plan.message}
      </div>
    )
  }
  const counts = plan.post_check?.counts
  return (
    <div style={{ marginTop: 10 }}>
      <div className="row">
        <span style={{ color: 'var(--ok)' }}>✓ {plan.message}</span>
        <span className="spacer" />
        {plan.inactive_carriers?.length > 0 && (
          <span className="muted" title={plan.inactive_carriers.map((c) => c.name).join('、')}>
            ⏸ {plan.inactive_carriers.length} 个载波该时刻未激活，未参与规划
          </span>
        )}
        {counts && (
          <span className="muted">
            规划后复核：冲突 {counts.error} · 警告 {counts.warning} · 待评估 {counts.pending}
          </span>
        )}
        <button onClick={() => setOpen(!open)}>{open ? '收起' : '展开'}</button>
      </div>
      {open && (
        <table className="plan-table" style={{ marginTop: 8 }}>
          <thead>
            <tr><th>载波</th><th>原中心</th><th>新中心 MHz</th><th>频带范围</th><th>激活时段</th><th>偏移 MHz</th></tr>
          </thead>
          <tbody>
            {plan.assignments.map((a) => (
              <tr key={a.name}>
                <td>{a.name} <span className="muted">{a.polarization}</span></td>
                <td>{a.original_center_mhz.toFixed(3)}</td>
                <td>{a.center_mhz.toFixed(3)}</td>
                <td>{a.low_mhz.toFixed(2)}–{a.high_mhz.toFixed(2)}</td>
                <td className="win-cell">
                  {!a.windows?.length
                    ? <span className="muted">始终激活</span>
                    : a.windows.map((w) => (
                      <span key={`${w.start}${w.end}`} className="win-chip">
                        {w.start}–{w.cross_midnight ? '次日' : ''}{w.end} {w.tz_label}
                      </span>
                    ))}
                </td>
                <td className={a.shift_mhz > 0 ? 'shift-pos' : a.shift_mhz < 0 ? 'shift-neg' : ''}>
                  {a.shift_mhz > 0 ? '+' : ''}{a.shift_mhz.toFixed(3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** 仅把用户显式设置的规则送给后端；未设置的极化对由后端按“待评估”处理。 */
function normalizePolicy(p) {
  const out = {}
  for (const [k, v] of Object.entries(p || {})) {
    const [a, b] = k.split('|')
    out[policyKey(a, b)] = v
  }
  return out
}
