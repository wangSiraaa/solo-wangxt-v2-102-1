import { useMemo } from 'react'
import { TZ_CHOICES, browserTzOffset, instantIso, tzLabel } from '../time.js'

/**
 * 分析/规划的时间口径：
 * - anytime：全天口径，只要一天中存在共同激活时刻的载波对就参与检查；
 * - instant：给定带时区时刻，只评估此刻实际激活的载波。
 */
export default function TimeScopePanel({ scope, setScope, atIso, setAtIso, disabled, context }) {
  const tz = useMemo(() => browserTzOffset(), [])

  // 从 at ISO 里解析本地显示（直接让用户编辑“某个偏移下的时刻”）
  const parsed = useMemo(() => parseAt(atIso), [atIso])

  const rebuild = (next) => {
    const v = { hh: parsed?.hh ?? 12, mm: parsed?.mm ?? 0, offset: parsed?.offset ?? tz, ...next }
    setAtIso(instantIso(v.offset, v.hh, v.mm))
  }

  const activeList = context?.active_carriers || []
  const inactiveList = context?.inactive_carriers || []

  return (
    <div>
      <div className="seg" style={{ marginBottom: 8 }}>
        <button className={scope === 'anytime' ? 'on' : ''} disabled={disabled}
                onClick={() => setScope('anytime')}>全天口径（按每日排程）</button>
        <button className={scope === 'instant' ? 'on' : ''} disabled={disabled}
                onClick={() => { setScope('instant'); if (!atIso) rebuild({}) }}>
          指定时刻（此刻激活）
        </button>
      </div>

      {scope === 'instant' && (
        <>
          <div className="row" style={{ marginBottom: 6 }}>
            <label className="field-label">本地时刻</label>
            <input className="field" type="number" min={0} max={23} style={{ width: 64 }}
                   value={parsed?.hh ?? 12} disabled={disabled}
                   onChange={(e) => rebuild({ hh: Math.min(23, Math.max(0, Number(e.target.value))) })} />
            <span className="muted">:</span>
            <input className="field" type="number" min={0} max={59} style={{ width: 64 }}
                   value={parsed?.mm ?? 0} disabled={disabled}
                   onChange={(e) => rebuild({ mm: Math.min(59, Math.max(0, Number(e.target.value))) })} />
            <select className="field" value={parsed?.offset ?? tz} disabled={disabled}
                    onChange={(e) => rebuild({ offset: Number(e.target.value) })}>
              {TZ_CHOICES.map((t, k) => <option key={k} value={t.m}>{t.label}</option>)}
            </select>
            <button disabled={disabled} onClick={() => rebuild({ offset: tz })}>浏览器时区 {tzLabel(tz)}</button>
          </div>
          <div className="hint" style={{ marginTop: 0 }}>
            提交时刻（带偏移 ISO）：<code>{atIso || '—'}</code>
            ；分析、频段图、掩模图与 OR-Tools 规划都只针对该时刻同时在发射的载波。
          </div>
          {context && (
            <div className="active-lists">
              <div className="active-now">
                <b>此刻激活（{activeList.length}）：</b>
                {activeList.length ? activeList.join('、') : '无'}
              </div>
              <div className="muted">
                <b>未激活（{inactiveList.length}）：</b>
                {inactiveList.length ? inactiveList.join('、') : '无'}
                ；频段图灰显，不参与冲突/泄漏/功率叠加。
              </div>
            </div>
          )}
        </>
      )}
      {scope === 'anytime' && (
        <div className="hint" style={{ marginTop: 0 }}>
          全天口径：频率相同但每日排程互不重合（含跨午夜首尾相接、不同 UTC 偏移错开）的载波
          不报几何/泄漏冲突，规划中也不加间隔约束；只有存在共同激活时刻的载波对才检查。
        </div>
      )}
    </div>
  )
}

function parseAt(iso) {
  if (!iso) return null
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):\d{2}([+-])(\d{2}):(\d{2})$/)
  if (!m) return null
  const sign = m[6] === '+' ? 1 : -1
  return { hh: Number(m[4]), mm: Number(m[5]), offset: sign * (Number(m[7]) * 60 + Number(m[8])) }
}
