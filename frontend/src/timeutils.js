// 激活时间区间的前端展示工具（语义与后端 services/timewindows.py 一致）。
//
// 区间按“当地墙钟 HH:MM + 显式 UTC 偏移（分钟）”持久化；end <= start 为跨午夜；
// 空区间列表 = 始终激活。物理时间比较在 UTC 圆周（0..1440 分钟）上进行。

export const DAY_MIN = 1440

export function parseHHMM(v) {
  if (typeof v === 'number') return v
  const [h, m] = String(v).split(':').map(Number)
  if (!Number.isFinite(h) || !Number.isFinite(m)) return NaN
  return h * 60 + m
}

export function fmtHHMM(min) {
  const m = ((Math.round(min) % DAY_MIN) + DAY_MIN) % DAY_MIN
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export function fmt2400(min) {
  return min >= DAY_MIN ? '24:00' : fmtHHMM(min)
}

export function fmtOffset(off) {
  const sign = off >= 0 ? '+' : '-'
  const v = Math.abs(off)
  return `UTC${sign}${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`
}

export function windowDurationMin(w) {
  const s = parseHHMM(w.start)
  const e = parseHHMM(w.end)
  return (e - s + DAY_MIN) % DAY_MIN
}

export function isCrossMidnight(w) {
  return windowDurationMin(w) > 0 && parseHHMM(w.start) + windowDurationMin(w) > DAY_MIN
}

/** 换算为 UTC 圆周上的半开线段（端点可取到 1440）。 */
export function utcSegments(w) {
  const s = parseHHMM(w.start)
  const dur = windowDurationMin(w)
  if (dur === 0) return []
  const off = w.tz_offset_minutes ?? 480
  const u0 = s - off
  const u1 = u0 + dur
  const segs = []
  for (const k of [-1, 0, 1]) {
    let lo = Math.max(u0, k * DAY_MIN)
    let hi = Math.min(u1, (k + 1) * DAY_MIN)
    if (hi <= lo) continue
    if (hi <= 0) { lo += DAY_MIN; hi += DAY_MIN }
    else if (lo >= DAY_MIN) { lo -= DAY_MIN; hi -= DAY_MIN }
    segs.push([lo, hi])
  }
  return segs
}

export function allSegments(windows) {
  return (windows || []).flatMap(utcSegments)
}

/** 两载波 UTC 同时激活交集（半开，相接不算）。空窗口 = 始终激活。 */
export function sharedSegments(winA, winB) {
  if (!winA?.length && !winB?.length) return [[0, DAY_MIN]]
  const sa = winA?.length ? allSegments(winA) : [[0, 0]]
  const sb = winB?.length ? allSegments(winB) : [[0, 0]]
  const useA = winA?.length ? sa : sb
  const useB = winB?.length ? sb : sa
  const out = []
  for (const [a0, a1] of useA)
    for (const [b0, b1] of useB) {
      const lo = Math.max(a0, b0)
      const hi = Math.min(a1, b1)
      if (hi > lo) out.push([lo, hi])
    }
  return out.sort((x, y) => x[0] - y[0])
}

export function isCoactive(winA, winB) {
  return sharedSegments(winA, winB).length > 0
}

export function activeAtUTC(windows, utcMinute) {
  if (!windows?.length) return true
  const t = ((utcMinute % DAY_MIN) + DAY_MIN) % DAY_MIN
  return allSegments(windows).some(([lo, hi]) => lo <= t && t < hi)
}

/** 人类可读的区间摘要，如 “22:00–次日02:00 UTC+08:00”。 */
export function describeWindowShort(w) {
  const cross = isCrossMidnight(w)
  const off = fmtOffset(w.tz_offset_minutes ?? 480)
  return `${w.start}–${cross ? '次日' : ''}${w.end} ${off}`
}

export function describeCarrierWindows(windows) {
  if (!windows?.length) return '始终激活'
  return windows.map(describeWindowShort).join('；')
}

/** 本地时刻 + 偏移 -> UTC 分钟。 */
export function localToUTC(localHHMM, offsetMin) {
  return (parseHHMM(localHHMM) - offsetMin + DAY_MIN) % DAY_MIN
}
