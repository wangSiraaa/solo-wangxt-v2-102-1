"""激活区间时间模型的单元测试（跨午夜、相邻、时区偏移、自重叠校验）。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.time_model import (Window, active_at, overlap_minutes_utc,
                                     parse_hhmm, schedules_overlap,
                                     validate_schedule, windows_overlap)

BJ = timezone(timedelta(hours=8))
UTC = timezone.utc


def dt(y, mo, d, h, mi=0, tz=BJ):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


def test_parse_hhmm_including_2400():
    assert parse_hhmm("08:00") == 480
    assert parse_hhmm("24:00") == 1440
    for bad in ("8:00", "abc", "24:30", "25:00", "12:60"):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_zero_length_window_rejected():
    with pytest.raises(ValueError):
        Window(600, 600, 0)


def test_cross_midnight_membership_half_open():
    night = Window(22 * 60, 2 * 60, 0)
    assert night.crosses_midnight and night.duration_min == 4 * 60
    assert night.contains(dt(2026, 10, 1, 22, tz=UTC))
    assert night.contains(dt(2026, 10, 1, 23, 59, tz=UTC))
    assert night.contains(dt(2026, 10, 2, 1, tz=UTC))
    # 终点 02:00 不含（半开）
    assert not night.contains(dt(2026, 10, 2, 2, tz=UTC))
    # 起点含
    assert night.contains(dt(2026, 10, 1, 22, tz=UTC))
    assert not night.contains(dt(2026, 10, 1, 3, tz=UTC))


def test_adjacent_windows_do_not_overlap():
    a = Window(8 * 60, 10 * 60, 0)
    b = Window(10 * 60, 12 * 60, 0)
    assert not windows_overlap(a, b)
    assert overlap_minutes_utc([a], [b]) == 0
    # 跨午夜段 22–02 与凌晨段 02–06 首尾相接，也不算重叠
    night = Window(22 * 60, 2 * 60, 0)
    dawn = Window(2 * 60, 6 * 60, 0)
    assert not windows_overlap(night, dawn)


def test_day_and_night_shifts_never_coexist():
    day = Window(8 * 60, 20 * 60, 480)
    night = Window(20 * 60, 8 * 60, 480)  # 跨午夜，与白班首尾相接
    assert not windows_overlap(day, night)
    assert not schedules_overlap([day], [night])
    # 任何整点最多一个在发
    for h in range(24):
        assert active_at([day], dt(2026, 10, 1, h, tz=BJ)) != \
               active_at([night], dt(2026, 10, 1, h, tz=BJ))


def test_extended_night_overlaps_day_by_one_hour():
    day = Window(8 * 60, 20 * 60, 480)
    night_ext = Window(19 * 60, 8 * 60, 480)
    assert windows_overlap(day, night_ext)
    assert overlap_minutes_utc([day], [night_ext]) == 60
    assert active_at([day], dt(2026, 10, 1, 19, 30, tz=BJ))
    assert active_at([night_ext], dt(2026, 10, 1, 19, 30, tz=BJ))


def test_different_utc_offsets_separate_same_local_windows():
    beijing_day = Window(8 * 60, 16 * 60, 480)   # UTC 00–08
    london_day = Window(8 * 60, 16 * 60, 0)      # UTC 08–16
    assert beijing_day.utc_intervals() == ((0, 480),)
    assert london_day.utc_intervals() == ((480, 960),)
    assert not windows_overlap(beijing_day, london_day)
    # 同一挂钟 12:00 但不同绝对时刻：北京 12:00 伦敦不发，伦敦 12:00 北京不发
    assert active_at([beijing_day], dt(2026, 10, 1, 12, tz=BJ))
    assert not active_at([london_day], dt(2026, 10, 1, 12, tz=BJ))
    assert active_at([london_day], dt(2026, 10, 1, 12, tz=UTC))
    assert not active_at([beijing_day], dt(2026, 10, 1, 12, tz=UTC))


def test_same_local_different_tz_can_still_overlap_in_utc():
    # 北京 08–20(+8)=UTC00–12 与伦敦 08–20(+0)=UTC08–20 在 UTC 08–12 重合 4h
    assert windows_overlap(Window(8 * 60, 20 * 60, 480),
                           Window(8 * 60, 20 * 60, 0))


def test_always_active_empty_schedule():
    assert schedules_overlap([], [])
    assert active_at([], dt(2026, 10, 1, 3, tz=UTC))


def test_self_schedule_overlap_rejected():
    with pytest.raises(ValueError):
        validate_schedule([Window(8 * 60, 10 * 60, 0),
                           Window(9 * 60, 11 * 60, 0)], "X")
    with pytest.raises(ValueError):
        validate_schedule([Window(22 * 60, 2 * 60, 0),
                           Window(1 * 60, 3 * 60, 0)], "Y")
    # 相邻的多段合法（含跨午夜衔接）
    validate_schedule([Window(22 * 60, 2 * 60, 0), Window(2 * 60, 10 * 60, 0)], "Z")
