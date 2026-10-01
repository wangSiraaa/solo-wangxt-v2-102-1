import { fmtHHMM, fmtOffset, localToUTC } from '../timeutils.js'

const OFFSETS = [-720, -660, -600, -540, -480, -420, -360, -300, -240, -180, -120,
  -60, 0, 60, 120, 180, 240, 300, 330, 360, 420, 480, 540, 570, 600, 660, 720, 780, 840]

function offsetLabel(off) {
  const sign = off >= 0 ? '+' : '-'
  const v = Math.abs(off)
  return `UTC${sign}${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`
}

/**
 * 时间口径控制：
 * - 全天配对：检查“一天内存在同时激活时段”的载波对；
 * - 考察时刻：只让该时刻实际激活的载波参与分析/频段图/掩模图/规划。
 */
export default function TimeScopeBar({ scope, onChange, disabled }) {
  const setMode = (mode) => onChange({ ...scope, mode })
  const utc = localToUTC(scope.localTime, scope.tzOffset)
  return (
    <div className="time-scope">
      <span className="seg">
        <button className={scope.mode === 'all_day' ? 'on' : ''} disabled={disabled}
                onClick={() => setMode('all_day')}>全天配对</button>
        <button className={scope.mode === 'instant' ? 'on' : ''} disabled={disabled}
                onClick={() => setMode('instant')}>考察时刻</button>
      </span>
      {scope.mode === 'instant' ? (
        <span className="row" style={{ gap: 6 }}>
          <label className="field-label">当地时刻</label>
          <input type="time" className="field" value={scope.localTime} disabled={disabled}
                 onChange={(e) => onChange({ ...scope, localTime: e.target.value })} />
          <select className="field" value={scope.tzOffset} disabled={disabled}
                  onChange={(e) => onChange({ ...scope, tzOffset: Number(e.target.value) })}>
            {OFFSETS.map((o) => <option key={o} value={o}>{offsetLabel(o)}</option>)}
          </select>
          <span className="muted">= UTC {fmtHHMM(utc)}</span>
        </span>
      ) : (
        <span className="muted">
          仅对一天内同时激活的载波对做几何/保护带/掩模检查；时间不重叠的同频载波不冲突。
        </span>
      )}
    </div>
  )
}
