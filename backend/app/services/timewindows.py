"""载波激活时间区间（每日重复的教学模型）。

语义约定（持久化口径，后端/前端/测试共同遵守）：

- 每个载波持有 0..n 个**激活区间**；区间按“当地墙钟时间”录入，
  并显式携带该区间的 UTC 偏移（``tz_offset_minutes``），例如 UTC+8 = 480。
- 区间每天重复：``start``/``end`` 为 "HH:MM"（可含秒，须为 0）。
  ``end <= start`` 表示**跨午夜**：如 22:00–02:00 = 当日 22:00 起、次日 02:00 止。
- 区间为半开区间 ``[start, end)``：同一载波自己的区间不得重叠，但允许**相接**
  （一个的 end 恰好等于另一个的 start，相接不算重叠）。
- 时长范围 1..1439 分钟：不允许零长度，也不允许 24 小时全天区间
  （全天语义直接用“无区间”表示，避免出现两种等价写法）。
- **区间列表为空 ⇔ 始终激活（always active）**。这是旧数据（无时间字段）
  升级后的唯一默认语义，历史分析结论不因此改变。
- 物理时间比较一律先把本地区间换算到 UTC 圆周（0..1440 分钟）再做：
  两个载波只在 UTC 圆周上存在正长度交集时才算“同时激活”，
  相接（例如 A 12:00 结束、B 12:00 开始）不算同时激活。
  因此 UTC 偏移不同、但物理时段错开的两个区间不会产生干扰判定。

这是离线教学模型：区间仅用于比较“同一时刻谁在发射”，不连接任何设备。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

DAY_MIN = 1440
# 常见民用时区范围：UTC-12:00 .. UTC+14:00
MIN_OFFSET_MIN = -720
MAX_OFFSET_MIN = 840


def parse_hhmm(value: str | int) -> int:
    """"HH:MM"（或 "HH:MM:SS"，秒须为 0）→ 当日分钟数 (0..1439)。"""
    if isinstance(value, int):
        if not 0 <= value < DAY_MIN:
            raise ValueError(f"分钟数越界: {value}")
        return value
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"时间格式应为 HH:MM: {value!r}")
    try:
        h, m = int(parts[0]), int(parts[1])
        sec = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise ValueError(f"时间格式应为 HH:MM: {value!r}") from None
    if not 0 <= h <= 23 or not 0 <= m <= 59 or not 0 <= sec <= 59:
        raise ValueError(f"时间超出范围: {value!r}")
    if sec != 0:
        raise ValueError(f"教学模型时间粒度为分钟，秒必须为 0: {value!r}")
    return h * 60 + m


def fmt_hhmm(minute: int) -> str:
    """UTC 圆周分钟 -> "HH:MM"（自动取模到当日）。"""
    m = int(round(minute)) % DAY_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def fmt_offset(offset_min: int) -> str:
    """480 -> 'UTC+08:00'；-300 -> 'UTC-05:00'。"""
    sign = "+" if offset_min >= 0 else "-"
    v = abs(offset_min)
    return f"UTC{sign}{v // 60:02d}:{v % 60:02d}"


@dataclass(frozen=True)
class TimeWindow:
    """一个每日重复的激活区间（本地墙钟 + 显式 UTC 偏移）。"""
    start_min: int          # 当地开始分钟 (0..1439)
    duration_min: int       # 时长分钟（1..1439）
    tz_offset_minutes: int = 480

    @property
    def end_min(self) -> int:
        """当地结束分钟（圆周值；跨午夜时小于 start_min）。"""
        return (self.start_min + self.duration_min) % DAY_MIN

    @property
    def cross_midnight(self) -> bool:
        return self.start_min + self.duration_min > DAY_MIN - 1e-9

    def utc_segments(self) -> list[tuple[int, int]]:
        """换算为 UTC 圆周 [0,1440) 上的半开线段；可能为 1 段或 2 段。

        线段端点允许取到 1440（表示 ``[lo, 24:00)``），便于直接做相交运算。
        """
        u0 = self.start_min - self.tz_offset_minutes
        u1 = u0 + self.duration_min
        segs: list[tuple[int, int]] = []
        # 区间长度 < 1440，最多跨越一个 UTC 日界
        for k in (-1, 0, 1):
            lo = max(u0, k * DAY_MIN)
            hi = min(u1, (k + 1) * DAY_MIN)
            if hi > lo:
                if hi <= 0:
                    lo += DAY_MIN
                    hi += DAY_MIN
                elif lo >= DAY_MIN:
                    lo -= DAY_MIN
                    hi -= DAY_MIN
                segs.append((lo, hi))
        return segs


def make_window(start: str | int, end: str | int,
                tz_offset_minutes: int = 480) -> TimeWindow:
    """由本地起止字符串构造区间，含全部合法性校验。"""
    if not MIN_OFFSET_MIN <= tz_offset_minutes <= MAX_OFFSET_MIN:
        raise ValueError(
            f"UTC 偏移 {tz_offset_minutes} 超出允许范围 "
            f"[{MIN_OFFSET_MIN}, {MAX_OFFSET_MIN}] 分钟")
    s = parse_hhmm(start)
    e = parse_hhmm(end)
    duration = (e - s) % DAY_MIN
    if duration == 0:
        raise ValueError(
            f"激活区间 {fmt_hhmm(s)}–{fmt_hhmm(e)} 时长为 0；"
            "结束时刻不能等于开始时刻（需要全天请直接留空区间列表）")
    return TimeWindow(s, duration, tz_offset_minutes)


def is_always_active(windows: list[TimeWindow] | None) -> bool:
    return not windows


def _all_segments(windows: list[TimeWindow]) -> list[tuple[int, int]]:
    return [seg for w in windows for seg in w.utc_segments()]


def validate_carrier_windows(windows: list[TimeWindow], carrier_name: str) -> None:
    """同一载波自己的区间不得在物理时间（UTC 圆周）上重叠；相接允许。"""
    segs = sorted(_all_segments(windows))
    for (a0, a1), (b0, b1) in itertools.combinations(segs, 2):
        if max(a0, b0) < min(a1, b1):
            raise ValueError(
                f"载波 {carrier_name!r} 的激活区间互相重叠："
                f"UTC {fmt_hhmm(a0)}–{fmt_hhmm(a1)} 与 {fmt_hhmm(b0)}–{fmt_hhmm(b1)}"
                f"（半开区间相接不算重叠）")


def active_at(windows: list[TimeWindow] | None, utc_minute: int) -> bool:
    """给定 UTC 当日分钟，载波是否处于激活状态。空区间=始终激活。"""
    if not windows:
        return True
    t = utc_minute % DAY_MIN
    return any(lo <= t < hi for lo, hi in _all_segments(windows))


def _intersect(segs_a: list[tuple[int, int]],
               segs_b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a0, a1 in segs_a:
        for b0, b1 in segs_b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi > lo:  # 半开：端点相接（hi == lo）不算交集
                out.append((lo, hi))
    # 同一个载波自身已校验不重叠，两个载波的交集也不会自重叠，仅排序即可
    return sorted(out)


def shared_utc_segments(windows_a: list[TimeWindow] | None,
                        windows_b: list[TimeWindow] | None
                        ) -> list[tuple[int, int]]:
    """两载波在 UTC 圆周上同时激活的交集线段（空 = 从不同时激活）。

    任一方“始终激活”时，交集就是另一方（或整个圆周）。
    """
    if not windows_a and not windows_b:
        return [(0, DAY_MIN)]
    if not windows_a:
        return sorted(_all_segments(windows_b))
    if not windows_b:
        return sorted(_all_segments(windows_a))
    return _intersect(_all_segments(windows_a), _all_segments(windows_b))


def coactive(windows_a: list[TimeWindow] | None,
             windows_b: list[TimeWindow] | None) -> bool:
    """两载波在一天之内是否存在同时激活的正长度时段。"""
    return bool(shared_utc_segments(windows_a, windows_b))


def describe_window(w: TimeWindow) -> dict:
    """供 API/前端展示的区间描述（本地时刻、跨午夜标记、UTC 线段）。"""
    def _seg_label(lo: int, hi: int) -> tuple[str, str]:
        end = "24:00" if hi == DAY_MIN else fmt_hhmm(hi)
        return fmt_hhmm(lo), end

    return {
        "start": fmt_hhmm(w.start_min),
        "end": fmt_hhmm(w.end_min),
        "duration_min": w.duration_min,
        "tz_offset_minutes": w.tz_offset_minutes,
        "tz_label": fmt_offset(w.tz_offset_minutes),
        "cross_midnight": w.cross_midnight,
        "utc_segments": [
            {"start": _seg_label(lo, hi)[0], "end": _seg_label(lo, hi)[1],
             "spans_midnight_utc": hi == DAY_MIN and lo != 0}
            for lo, hi in w.utc_segments()
        ],
    }
