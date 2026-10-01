"""激活时间区间的纯函数测试：跨午夜、相接、UTC 偏移与自重叠校验。"""
import pytest

from app.services.timewindows import (DAY_MIN, active_at, coactive,
                                      fmt_hhmm, fmt_offset, make_window,
                                      parse_hhmm, shared_utc_segments,
                                      validate_carrier_windows)


def test_parse_and_format():
    assert parse_hhmm("08:30") == 510
    assert parse_hhmm("00:00") == 0
    assert parse_hhmm("23:59") == 1439
    assert fmt_hhmm(1500) == "01:00"
    assert fmt_offset(480) == "UTC+08:00"
    assert fmt_offset(-300) == "UTC-05:00"
    with pytest.raises(ValueError):
        parse_hhmm("25:00")
    with pytest.raises(ValueError):
        parse_hhmm("8am")


def test_cross_midnight_window_utc_arcs():
    # 北京 22:00–02:00（UTC+8）= UTC 14:00–18:00，一段
    w = make_window("22:00", "02:00", 480)
    assert w.cross_midnight
    assert w.duration_min == 4 * 60
    assert w.utc_segments() == [(14 * 60, 18 * 60)]

    # 夜班 18:00–次日08:00（UTC+8）= UTC 10:00–24:00，恰好到 UTC 日界
    night = make_window("18:00", "08:00", 480)
    assert night.cross_midnight
    assert night.duration_min == 14 * 60
    assert night.utc_segments() == [(600, DAY_MIN)]

    # 本地 06:00–10:00（不跨本地午夜）= UTC 22:00–次日02:00，跨 UTC 日界 -> 两段
    cross_utc = make_window("06:00", "10:00", 480)
    assert not cross_utc.cross_midnight
    assert cross_utc.utc_segments() == [(22 * 60, DAY_MIN), (0, 2 * 60)]


def test_non_wrapping_window():
    w = make_window("08:00", "18:00", 480)
    assert not w.cross_midnight
    assert w.utc_segments() == [(0, 600)]


def test_zero_duration_rejected():
    with pytest.raises(ValueError):
        make_window("10:00", "10:00", 480)


def test_bad_offset_rejected():
    with pytest.raises(ValueError):
        make_window("10:00", "11:00", 900)


def test_self_overlap_rejected_adjacent_allowed():
    with pytest.raises(ValueError):
        validate_carrier_windows(
            [make_window("08:00", "12:00"), make_window("11:00", "13:00")], "X")
    # 半开相接不重叠
    validate_carrier_windows(
        [make_window("08:00", "12:00"), make_window("12:00", "18:00")], "X")


def test_cross_midnight_self_overlap():
    # 22:00–02:00 与 01:00–03:00 在本地 01–02 点重叠
    with pytest.raises(ValueError):
        validate_carrier_windows(
            [make_window("22:00", "02:00"), make_window("01:00", "03:00")], "X")


def test_active_at_half_open_boundary():
    w = make_window("08:00", "18:00", 480)  # UTC 00:00–10:00
    assert active_at([w], 0)
    assert active_at([w], 9 * 60 + 59)
    assert not active_at([w], 10 * 60)       # 结束边界（半开）
    assert not active_at([w], 10 * 60 + 1)
    assert active_at(None, 12 * 60)          # 始终激活
    assert active_at([], 12 * 60)


def test_coactive_basic_and_boundary():
    day = make_window("08:00", "18:00", 480)
    night = make_window("18:00", "08:00", 480)
    # 18:00 相接：半开区间不算同时激活
    assert not coactive([day], [night])
    assert shared_utc_segments([day], [night]) == []

    overlap_night = make_window("22:00", "02:00", 480)
    shared = shared_utc_segments([night], [overlap_night])
    assert shared == [(14 * 60, 18 * 60)]    # 22:00–02:00 local = UTC 14–18
    assert coactive([night], [overlap_night])


def test_offset_aware_overlap():
    # 同一墙钟写法但不同偏移：物理时间可能错开
    a = make_window("09:00", "10:00", 480)    # UTC 01:00–02:00
    b_same = make_window("11:00", "12:00", 600)  # UTC 01:00–02:00
    assert coactive([a], [b_same])
    c_diff = make_window("09:00", "10:00", 300)  # UTC 04:00–05:00
    assert not coactive([a], [c_diff])


def test_always_active_intersects_anything():
    w = make_window("09:00", "10:00", 480)
    assert coactive(None, [w])
    assert coactive([w], None)
    assert coactive(None, None)
    assert shared_utc_segments(None, [w]) == [(60, 120)]


def test_multi_window_carrier():
    wins = [make_window("08:00", "10:00", 480), make_window("20:00", "22:00", 480)]
    validate_carrier_windows(wins, "X")
    assert active_at(wins, 1 * 60)       # UTC 01 = 本地 09
    assert not active_at(wins, 5 * 60)   # 间隙
    assert active_at(wins, 13 * 60)      # UTC 13 = 本地 21
