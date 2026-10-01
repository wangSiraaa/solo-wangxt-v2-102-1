import { useEffect, useState } from 'react'
import { api } from '../api.js'

/** 已保存规划列表：绑定场景修订与时间窗口；过期规划醒目展示且禁止直接复用。 */
export default function SavedPlansPanel({ scenarioId, revision, onApply, refreshKey }) {
  const [plans, setPlans] = useState([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => {
    if (scenarioId == null) { setPlans([]); return }
    api.listPlans(scenarioId).then(setPlans).catch((e) => setMsg(String(e)))
  }
  useEffect(load, [scenarioId, revision, refreshKey])

  const del = async (id) => {
    await api.deletePlan(id)
    load()
  }

  const recheck = async (p) => {
    setBusy(true); setMsg('')
    try {
      const r = await api.recheckPlan(p.id)
      if (!r.usable) {
        setMsg(`规划 #${p.id} 已过期，拒绝直接复用：${r.stale_reason || '场景已修订'}。请重新求解。`)
      } else {
        setMsg(`规划 #${p.id} 仍可执行（post-check 已重新确认）。`)
      }
    } catch (e) { setMsg(e.message) } finally { setBusy(false) }
  }

  if (scenarioId == null) {
    return <div className="hint">保存场景后即可保存并复用规划（规划绑定场景修订与时间窗口）。</div>
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="muted">当前场景修订 rev {revision ?? '—'}</span>
        <button onClick={load} disabled={busy}>刷新</button>
      </div>
      {msg && <div className="err-msg">{msg}</div>}
      {plans.length === 0 && <div className="hint">暂无已保存规划。</div>}
      <table className="plan-table saved-plans">
        <thead>
          <tr><th>#</th><th>模式</th><th>时间窗口</th><th>修订</th><th>状态</th><th></th></tr>
        </thead>
        <tbody>
          {plans.map((p) => (
            <tr key={p.id} className={p.stale ? 'stale-row' : ''}>
              <td>{p.id}</td>
              <td>{p.mode === 'mask_aware' ? '掩模感知' : '仅保护带'}</td>
              <td>
                {p.scope === 'instant'
                  ? <span title="绑定的具体时刻（UTC）">时刻 {p.at_utc?.replace('T', ' ').slice(0, 16)} UTC</span>
                  : <span title="全天口径">全天（每日排程）</span>}
              </td>
              <td>
                rev {p.scenario_revision}
                {p.stale && <span className="stale-tag" title={p.stale_reason}>≠ 当前 {revision}</span>}
              </td>
              <td>
                {p.stale ? (
                  <span className="stale-pill">已过期</span>
                ) : p.feasible ? (
                  <span className="fresh-pill">可执行</span>
                ) : (
                  <span className="muted">无解方案</span>
                )}
                {p.stale && <div className="stale-reason">{p.stale_reason}</div>}
              </td>
              <td className="saved-actions">
                <button disabled={busy || p.stale}
                        title={p.stale ? '过期规划禁止直接复用' : '按当前数据复核后载入'}
                        onClick={() => recheck(p).then(() => !p.stale && onApply?.(p))}>
                  复用
                </button>
                <button className="danger" disabled={busy} onClick={() => del(p.id)}>删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="hint">
        规划保存后不可变；之后编辑载波频率/时间/规则会提升场景修订号，旧规划立即标为过期，
        只能重新求解——重新规划会强制跑一遍同时间口径的 post-check。
      </div>
    </div>
  )
}
