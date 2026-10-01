import { Fragment, useState } from 'react'
import ScheduleEditor from './ScheduleEditor.jsx'
import { describeSchedule } from '../time.js'

const POLS = ['H', 'V', 'LHCP', 'RHCP']

export default function CarrierTable({ carriers, masks, onChange, onAdd, onRemove, disabled, defaultTz }) {
  const [open, setOpen] = useState(() => new Set())
  const toggle = (i) =>
    setOpen((prev) => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })

  const update = (i, patch) => onChange(carriers.map((c, j) => (j === i ? { ...c, ...patch } : c)))

  return (
    <div>
      <table className="carriers">
        <thead>
          <tr>
            <th>名称</th><th>中心 MHz</th><th>带宽 MHz</th><th>功率 dBm</th>
            <th>极化</th><th>掩模</th><th>激活时段</th><th></th>
          </tr>
        </thead>
        <tbody>
          {carriers.map((c, i) => {
            const isOpen = open.has(i)
            const wins = c.schedule || []
            const cross = wins.some((w) => w.end !== '24:00' && w.end < w.start)
            return (
              <Fragment key={i}>
                <tr className={isOpen ? 'sched-open' : ''}>
                  <td><input name="name" value={c.name} disabled={disabled}
                             onChange={(e) => update(i, { name: e.target.value })} /></td>
                  <td><input className="num" type="number" step="0.1" value={c.center_mhz} disabled={disabled}
                             onChange={(e) => update(i, { center_mhz: parseFloat(e.target.value) })} /></td>
                  <td><input className="num" type="number" step="0.1" min="0.1" value={c.bandwidth_mhz} disabled={disabled}
                             onChange={(e) => update(i, { bandwidth_mhz: parseFloat(e.target.value) })} /></td>
                  <td><input className="num" type="number" step="0.5" value={c.power_dbm} disabled={disabled}
                             onChange={(e) => update(i, { power_dbm: parseFloat(e.target.value) })} /></td>
                  <td>
                    <select value={c.polarization} disabled={disabled}
                            onChange={(e) => update(i, { polarization: e.target.value })}>
                      {POLS.map((p) => <option key={p}>{p}</option>)}
                    </select>
                  </td>
                  <td>
                    <select value={c.mask_name} disabled={disabled}
                            onChange={(e) => update(i, { mask_name: e.target.value })}>
                      {masks.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                    </select>
                  </td>
                  <td>
                    <button className={`sched-toggle${wins.length ? ' has' : ''}${cross ? ' cross' : ''}`}
                            title={describeSchedule(wins)} disabled={disabled}
                            onClick={() => toggle(i)}>
                      {wins.length === 0 ? '始终激活' : describeSchedule(wins)}
                      {cross ? ' ↺' : ''}
                    </button>
                  </td>
                  <td className="del">
                    <button className="danger" title="删除" disabled={disabled}
                            onClick={() => onRemove(i)}>×</button>
                  </td>
                </tr>
                {isOpen && (
                  <tr className="sched-edit-row">
                    <td colSpan={8}>
                      <div className="sched-edit-head">
                        {c.name} 的每日激活区间（本地挂钟 + UTC 偏移）
                      </div>
                      <ScheduleEditor schedule={wins} defaultTz={defaultTz} disabled={disabled}
                                      onChange={(schedule) => update(i, { schedule })} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={onAdd} disabled={disabled}>＋ 添加载波</button>
        <span className="muted">{carriers.length} 个载波</span>
      </div>
    </div>
  )
}
