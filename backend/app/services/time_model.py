"""载波激活区间（activation window）的时间模型。

教学语义（离线模型，时间只影响“同一时刻哪些载波同时在发射”）：

- 区间以 **本地挂钟时间 + 显式 UTC 偏移** 表达，例如 ``08:00–12:00 @ UTC+8``。
- 区间按天循环（每日例行时段），不含日期；跨午夜用 end <= start 表示，
  例如本地 ``22:00–02:00`` 表示 22:00 起、次日 02:00 止。
- 区间为半开区间 ``[start, end)``：
  * 相邻区间（一个 10:00 止、另一个 10:00 起）**不算重叠**；
  * 跨午夜区间在 00:00 处与另一段衔接同样不算重叠。
- 一个载波可以有多段区间，但自己的区间之间不得重叠（首尾相接允许）。
- 载波的区间列表为空表示 **始终激活（always-on）**：这是旧数据（没有时间
  信息）升级后的唯一默认语义，历史分析结论因此保持不变。
- 两载波只有在时间轴上存在共同激活的时刻（``schedules_overlap``）才算
  “同时段载波”：频率相同但时间不重叠的两载波不产生几何/泄漏冲突。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

# UTC 偏移的合法范围（分钟）：UTC-12:00 … UTC+14:00
MIN_TZ_OFFSET_MIN = -12 * 60
MAX_TZ_OFFSET_MIN = 14 * 60

MINUTES_PER_DAY = 24 * 60


def parse_hhmm(value: str) -> int:
    """'HH:MM' -> 当日分钟数。允许 '24:00'（=1440，仅用于当日结束端点）。"""
    import re
    if not isinstance(value, str):
        raise ValueError(f"时间必须是 'HH:MM' 字符串，收到 {value!r}")
    s = value.strip()
    m = re.fullmatch(r"(\d{2}):(\d{2})", s)
    if m is None:
        raise ValueError(f"时间格式应为零填充的 'HH:MM'，收到 {value!r}")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 24) or not (0 <= mm <= 59):
        raise ValueError(f"时间越界：{value!r}")
    if hh == 24 and mm != 0:
        raise ValueError(f"'24' 点后只允许 '24:00'，收到 {value!r}")
    return hh * 60 + mm


def format_hhmm(minutes: int) -> str:
    """分钟数 -> 'HH:MM'（先按当日取模，1440 归一为 00:00 显示）。"""
    m = minutes % MINUTES_PER_DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def format_tz(offset_minutes: int) -> str:
    """UTC 偏移分钟 -> 'UTC+08:00' 形式。"""
    sign = "+" if offset_minutes >= 0 else "-"
    v = abs(offset_minutes)
    return f"UTC{sign}{v // 60:02d}:{v % 60:02d}"


def validate_tz_offset(offset_minutes: int) -> None:
    if not isinstance(offset_minutes, int) or isinstance(offset_minutes, bool):
        raise ValueError(f"UTC 偏移必须是整数分钟，收到 {offset_minutes!r}")
    if not MIN_TZ_OFFSET_MIN <= offset_minutes <= MAX_TZ_OFFSET_MIN:
        raise ValueError(
            f"UTC 偏移需在 {format_tz(MIN_TZ_OFFSET_MIN)} … "
            f"{format_tz(MAX_TZ_OFFSET_MIN)} 之间，收到 {format_tz(offset_minutes)}")


@dataclass(frozen=True)
class Window:
    """一段每日循环的激活区间（半开 [start, end)，本地挂钟分钟）。

    end == start 非法（零时长）；end < start 表示跨午夜；
    end 允许为 1440（'24:00'），语义等同次日 00:00。
    """
    start_min: int
    end_min: int
    tz_offset_minutes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.start_min, int) or not isinstance(self.end_min, int):
            raise ValueError("区间端点必须是整数分钟")
        if not 0 <= self.start_min < MINUTES_PER_DAY:
            raise ValueError(f"区间起点需在 00:00–23:59，收到 {format_hhmm(self.start_min)}")
        if not 0 < self.end_min <= MINUTES_PER_DAY:
            raise ValueError(f"区间终点需在 00:01–24:00，收到 {self.end_min}")
        validate_tz_offset(self.tz_offset_minutes)
        if self.end_min == self.start_min:
            raise ValueError(
                f"零时长区间 {format_hhmm(self.start_min)}–{format_hhmm(self.end_min)} 不合法；"
                "始终激活请留空区间列表，跨午夜请令终点早于起点（如 22:00–02:00）")

    @property
    def crosses_midnight(self) -> bool:
        return self.end_min < self.start_min

    @property
    def duration_min(self) -> int:
        if self.crosses_midnight:
            return MINUTES_PER_DAY - self.start_min + self.end_min
        return self.end_min - self.start_min

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(minutes=self.tz_offset_minutes))

    def utc_intervals(self) -> tuple[tuple[int, int], ...]:
        """把本地挂钟区间展开为 UTC 绝对分钟区间（半开 [lo, hi)）。

        跨午夜（或因时区偏移让区间跨过 UTC 00:00）时返回两段。
        所有端点对 MINUTES_PER_DAY 取模后，用“展开到相邻若干天”的方式做重叠判断。
        """
        lo = (self.start_min - self.tz_offset_minutes) % MINUTES_PER_DAY
        hi = lo + self.duration_min
        if hi > MINUTES_PER_DAY:
            return ((lo, MINUTES_PER_DAY), (0, hi - MINUTES_PER_DAY))
        return ((lo, hi),)

    def contains_utc_minute(self, day_minute_utc: int) -> bool:
        """某日 UTC 分钟 m（0..1439）是否落在区间内（半开，1440 端点不含）。"""
        for lo, hi in self.utc_intervals():
            if lo <= day_minute_utc < hi:
                return True
        return False

    def contains(self, dt: datetime) -> bool:
        """带时区的绝对时刻是否处于激活状态（按该载波本地挂钟循环判断）。"""
        if dt.tzinfo is None:
            raise ValueError("判断激活状态的时刻必须带时区信息（aware datetime）")
        local = dt.astimezone(self.tz)
        m = local.hour * 60 + local.minute
        if self.crosses_midnight:
            return m >= self.start_min or m < self.end_min
        return self.start_min <= m < self.end_min

    def describe(self) -> str:
        end = "24:00" if self.end_min == MINUTES_PER_DAY else format_hhmm(self.end_min)
        return f"{format_hhmm(self.start_min)}–{end} {format_tz(self.tz_offset_minutes)}"


def windows_overlap(a: Window, b: Window) -> bool:
    """两段区间在 UTC 绝对时间轴上是否有共同激活时刻（半开，相接不算）。

    区间按天循环，因此把每段展开到 [-1d, 2d) 后逐对求交。
    """
    def expand(w: Window) -> list[tuple[int, int]]:
        out = []
        for lo, hi in w.utc_intervals():
            for day in (-1, 0, 1):
                out.append((lo + day * MINUTES_PER_DAY, hi + day * MINUTES_PER_DAY))
        return out

    for x0, x1 in expand(a):
        for y0, y1 in expand(b):
            if x0 < y1 and y0 < x1:
                return True
    return False


def validate_schedule(windows: Iterable[Window], carrier_name: str = "") -> None:
    """校验同一载波自己的区间列表：不得互相重叠（首尾相接允许）。"""
    ws = list(windows)
    for i in range(len(ws)):
        for j in range(i + 1, len(ws)):
            if windows_overlap(ws[i], ws[j]):
                who = f"载波 {carrier_name} " if carrier_name else ""
                raise ValueError(
                    f"{who}自己的激活区间重叠：{ws[i].describe()} 与 {ws[j].describe()}")


def active_at(windows: Iterable[Window], dt: datetime) -> bool:
    """载波在给定绝对时刻是否激活；空区间列表 = 始终激活。"""
    ws = list(windows)
    if not ws:
        return True
    return any(w.contains(dt) for w in ws)


def schedules_overlap(windows_a: Iterable[Window], windows_b: Iterable[Window]) -> bool:
    """两个载波的排程在一天循环中是否存在共同激活时刻。

    任一方始终激活（空列表）即以另一方为准；两者都空则当然重叠。
    """
    a, b = list(windows_a), list(windows_b)
    if not a or not b:
        return True
    return any(windows_overlap(x, y) for x in a for y in b)


def overlap_minutes_utc(windows_a: Iterable[Window],
                        windows_b: Iterable[Window]) -> Optional[int]:
    """每日共同激活总分钟数（UTC 轴，0..1440）；任一始终激活返回 None。"""
    a, b = list(windows_a), list(windows_b)
    if not a or not b:
        return None

    def bits(ws: list[Window]) -> set[int]:
        s: set[int] = set()
        for w in ws:
            for lo, hi in w.utc_intervals():
                for day in (-1, 0, 1):
                    s.update(range(lo + day * MINUTES_PER_DAY, hi + day * MINUTES_PER_DAY))
        return {m % MINUTES_PER_DAY for m in s}

    return len(bits(a) & bits(b))


def describe_schedule(windows: Iterable[Window]) -> str:
    ws = list(windows)
    if not ws:
        return "始终激活"
    return "；".join(w.describe() for w in ws)


def ensure_aware_utc(dt: datetime) -> datetime:
    """把请求里的时刻统一成带时区的 UTC datetime；naive 直接拒绝（避免歧义）。"""
    if dt.tzinfo is None:
        raise ValueError("时刻必须带时区偏移，例如 '2026-10-01T09:00:00+08:00'")
    return dt.astimezone(timezone.utc)
