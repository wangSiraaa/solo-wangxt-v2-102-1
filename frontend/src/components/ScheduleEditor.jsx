import { TZ_CHOICES, tzLabel, validateSchedule } from '../time.js'

const newWindow = (tz) => ({ start: '08:00', end: '20:00', tz_offset_minutes: tz })

/** 单个载波的激活区间编辑：空=始终激活；每段 [开始, 结束) + UTC 偏移；end<start 为跨午夜。 */
export default function ScheduleEditor({ schedule, defaultTz, disabled, onChange }) {
  const windows = schedule || []
  const err = validateSchedule(windows)

  const update = (i, patch) =>
    onChange(windows.map((w, j) => (j === i ? { ...w, ...patch } : w)))
  const add = () => onChange([...windows, newWindow(defaultTz ?? 480)])
  const remove = (i) => onChange(windows.filter((_, j) => j !== i))

  if (windows.length === 0) {
    return (
      <div className="sched sched-empty">
        <span className="muted">始终激活</span>
        <button disabled={disabled} onClick={add} title="增加每日激活区间">＋ 时段</button>
      </div>
    )
  }

  return (
    <div className="sched">
      {windows.map((w, i) => {
        const cross = (() => {
          const s = Number(w.start.slice(0, 2)) * 60 + Number(w.start.slice(3))
          const e = w.end === '24:00' ? 1440 : Number(w.end.slice(0, 2)) * 60 + Number(w.end.slice(3))
          return e < s
        })()
        return (
          <div key={i} className={`sched-row${cross ? ' cross' : ''}`}>
            <input className="num time-input" value={w.start} disabled={disabled}
                   onChange={(e) => update(i, { start: e.target.value })} />
            <span className="muted">–</span>
            <input className="num time-input" value={w.end} disabled={disabled}
                   onChange={(e) => update(i, { end: e.target.value })} />
            <select className="tz-select" value={w.tz_offset_minutes} disabled={disabled}
                    onChange={(e) => update(i, { tz_offset_minutes: Number(e.target.value) })}>
              {TZ_CHOICES.map((t, k) => <option key={k} value={t.m}>{t.label}</option>)}
            </select>
            {cross && <span className="cross-tag" title="终点早于起点：跨午夜，到次日终点止">↺跨午夜</span>}
            <button className="danger sched-del" title="删除该时段" disabled={disabled}
                    onClick={() => remove(i)}>×</button>
          </div>
        )
      })}
      <div className="row">
        <button disabled={disabled} onClick={add}>＋ 再添时段</button>
        <button disabled={disabled} title="清空后该载波全天激活" onClick={() => onChange([])}>
          设为始终激活
        </button>
      </div>
      {err && <div className="err-msg">{err}</div>}
      {!err && (
        <div className="hint">
          半开区间（起点含、终点不含，{tzLabel(windows[0].tz_offset_minutes ?? 0)} 等各段独立计时），
          跨午夜用终点早于起点；同一载波区间首尾相接允许、重叠禁止。
        </div>
      )}
    </div>
  )
}
