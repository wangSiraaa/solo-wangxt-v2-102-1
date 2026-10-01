import { Fragment, useState } from 'react'
import { describeWindowShort } from '../timeutils.js'

const POLS = ['H', 'V', 'LHCP', 'RHCP']
const OFFSETS = [-720, -660, -600, -540, -480, -420, -360, -300, -240, -180, -120,
  -60, 0, 60, 120, 180, 240, 300, 330, 360, 420, 480, 540, 570, 600, 660, 720, 780, 840]

function offsetLabel(off) {
  const sign = off >= 0 ? '+' : '-'
  const v = Math.abs(off)
  return `UTC${sign}${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`
}

function WindowEditor({ windows, disabled, onChange }) {
  const set = (i, patch) =>
    onChange(windows.map((w, j) => (j === i ? { ...w, ...patch } : w)))
  return (
    <div className="win-editor">
      {windows.length === 0 && <span className="muted">∅ 始终激活（全天发射）</span>}
      {windows.map((w, i) => {
        const spansMidnight = (() => {
          const [sh, sm] = w.start.split(':').map(Number)
          const [eh, em] = w.end.split(':').map(Number)
          return eh * 60 + em <= sh * 60 + sm
        })()
        return (
          <div key={i} className="win-row" title={describeWindowShort(w)}>
            <input type="time" value={w.start} disabled={disabled}
                   onChange={(e) => set(i, { start: e.target.value })} />
            <span className="muted">→</span>
            <input type="time" value={w.end} disabled={disabled}
                   onChange={(e) => set(i, { end: e.target.value })} />
            <select value={w.tz_offset_minutes ?? 480} disabled={disabled}
                    onChange={(e) => set(i, { tz_offset_minutes: Number(e.target.value) })}>
              {OFFSETS.map((o) => <option key={o} value={o}>{offsetLabel(o)}</option>)}
            </select>
            {spansMidnight && <span className="tag-cross">跨午夜</span>}
            <button className="danger" title="删除该区间" disabled={disabled}
                    onClick={() => onChange(windows.filter((_, j) => j !== i))}>×</button>
          </div>
        )
      })}
      <button disabled={disabled} onClick={() =>
        onChange([...windows, { start: '09:00', end: '17:00', tz_offset_minutes: 480 }])}>
        ＋ 区间
      </button>
    </div>
  )
}

export default function CarrierTable({ carriers, masks, onChange, onAdd, onRemove, disabled }) {
  const update = (i, patch) => onChange(carriers.map((c, j) => (j === i ? { ...c, ...patch } : c)))
  const [openWin, setOpenWin] = useState(() => new Set())
  const toggle = (name) =>
    setOpenWin((prev) => {
      const n = new Set(prev)
      if (n.has(name)) n.delete(name)
      else n.add(name)
      return n
    })

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
            const wins = c.windows || []
            const expanded = openWin.has(c.name)
            return (
              <Fragment key={i}>
                <tr>
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
                    <button className={wins.length ? 'win-btn has' : 'win-btn'}
                            title={wins.length ? wins.map(describeWindowShort).join('；') : '始终激活'}
                            disabled={disabled}
                            onClick={() => toggle(c.name)}>
                      {wins.length ? `🕐 ${wins.length} 段` : '🕐 全天'}
                    </button>
                  </td>
                  <td className="del">
                    <button className="danger" title="删除" disabled={disabled}
                            onClick={() => onRemove(i)}>×</button>
                  </td>
                </tr>
                {expanded && (
                  <tr key={`${i}-win`} className="win-row-wrap">
                    <td colSpan={8}>
                      <WindowEditor windows={wins} disabled={disabled}
                                    onChange={(w) => update(i, { windows: w })} />
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
      <div className="hint">
        激活时段按当地墙钟时间录入并显式携带 UTC 偏移；end ≤ start 表示跨午夜。
        留空 = 始终激活（旧数据默认）。同一载波区间不得重叠，相接允许；
        频率相同但时间不重叠的载波不会被判冲突。
      </div>
    </div>
  )
}
