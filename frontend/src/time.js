// 激活区间（每日循环，本地挂钟时间 + 显式 UTC 偏移，可跨午夜）的前端工具。
// 语义与后端 app/services/time_model.py 严格一致。

export const MIN_PER_DAY = 1440

export function parseHHMM(v) {
  if (typeof v !== 'string' || !/^\d{2}:\d{2}$/.test(v)) return NaN
  const [hh, mm] = v.split(':').map(Number)
  if (hh < 0 || hh > 24 || mm < 0 || mm > 59 || (hh === 24 && mm !== 0)) return NaN
  return hh * 60 + mm
}

export function fmtHHMM(min) {
  const m = ((min % MIN_PER_DAY) + MIN_PER_DAY) % MIN_PER_DAY
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export function tzLabel(offsetMin) {
  const sign = offsetMin >= 0 ? '+' : '-'
  const v = Math.abs(offsetMin)
  return `UTC${sign}${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`
}

export const TZ_CHOICES = [
  { m: -12 * 60, label: 'UTC-12:00' }, { m: -11 * 60, label: 'UTC-11:00' },
  { m: -10 * 60, label: 'UTC-10:00' }, { m: -9 * 60, label: 'UTC-09:30' },
  { m: -8 * 60, label: 'UTC-08:00' }, { m: -7 * 60, label: 'UTC-07:00' },
  { m: -6 * 60, label: 'UTC-06:00' }, { m: -5 * 60, label: 'UTC-05:00' },
  { m: -4 * 60, label: 'UTC-04:00' }, { m: -3 * 60, label: 'UTC-03:30' },
  { m: -3 * 60, label: 'UTC-03:00' }, { m: -2 * 60, label: 'UTC-02:00' },
  { m: -60, label: 'UTC-01:00' }, { m: 0, label: 'UTC+00:00' },
  { m: 60, label: 'UTC+01:00' }, { m: 2 * 60, label: 'UTC+02:00' },
  { m: 3 * 60, label: 'UTC+03:00' }, { m: 3 * 60 + 30, label: 'UTC+03:30' },
  { m: 4 * 60, label: 'UTC+04:00' }, { m: 5 * 60 + 30, label: 'UTC+05:30' },
  { m: 6 * 60, label: 'UTC+06:00' }, { m: 7 * 60, label: 'UTC+07:00' },
  { m: 8 * 60, label: 'UTC+08:00' }, { m: 9 * 60, label: 'UTC+09:00' },
  { m: 10 * 60, label: 'UTC+10:00' }, { m: 11 * 60, label: 'UTC+11:00' },
  { m: 12 * 60, label: 'UTC+12:00' }, { m: 14 * 60, label: 'UTC+14:00' },
]

// 展开为 UTC 当日分钟区间（跨午夜/偏移跨 UTC 日界时两段）
export function utcIntervals(win) {
  const start = parseHHMM(win.start)
  let end = parseHHMM(win.end)
  const tz = win.tz_offset_minutes ?? 0
  const dur = end < start ? MIN_PER_DAY - start + end : end - start
  const lo = ((start - tz) % MIN_PER_DAY + MIN_PER_DAY) % MIN_PER_DAY
  const hi = lo + dur
  return hi > MIN_PER_DAY ? [[lo, MIN_PER_DAY], [0, hi - MIN_PER_DAY]] : [[lo, hi]]
}

function expand(win) {
  return utcIntervals(win).flatMap(([lo, hi]) =>
    [-1, 0, 1].map((d) => [lo + d * MIN_PER_DAY, hi + d * MIN_PER_DAY]))
}

export function windowsOverlap(a, b) {
  return expand(a).some(([x0, x1]) =>
    expand(b).some(([y0, y1]) => x0 < y1 && y0 < x1))
}

export function validateSchedule(windows) {
  if (!windows || windows.length === 0) return null
  for (const w of windows) {
    const s = parseHHMM(w.start)
    const e = parseHHMM(w.end)
    if (Number.isNaN(s) || Number.isNaN(e)) return '时间格式需为 HH:MM（终点可用 24:00）'
    if (s === e) return '零时长区间非法：始终激活请留空，跨午夜请令终点早于起点（如 22:00–02:00）'
    const tz = w.tz_offset_minutes ?? 0
    if (tz < -720 || tz > 840) return 'UTC 偏移超出范围'
  }
  for (let i = 0; i < windows.length; i++)
    for (let j = i + 1; j < windows.length; j++)
      if (windowsOverlap(windows[i], windows[j]))
        return `区间 #${i + 1} 与 #${j + 1} 互相重叠（同一载波不得自重叠）`
  return null
}

export function describeSchedule(windows) {
  if (!windows || windows.length === 0) return '始终激活'
  return windows
    .map((w) => `${w.start}–${w.end} ${tzLabel(w.tz_offset_minutes ?? 0)}`)
    .join('；')
}

/** 当前浏览器时区的 UTC 偏移（分钟）。 */
export function browserTzOffset() {
  return -new Date().getTimezoneOffset()
}

/** 生成“某 UTC 偏移下今天 HH:MI”的带偏移 ISO 字符串。 */
export function instantIso(offsetMin, hh, mm) {
  const sign = offsetMin >= 0 ? '+' : '-'
  const v = Math.abs(offsetMin)
  const oh = String(Math.floor(v / 60)).padStart(2, '0')
  const om = String(v % 60).padStart(2, '0')
  const d = new Date()
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return `${day}T${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00${sign}${oh}:${om}`
}
