"""时间感知冲突分析测试（验收 1/2 + 时刻口径）。"""
from app.services.analysis import AnalysisRules, Carrier, analyze
from app.services.timewindows import make_window

RULES = AnalysisRules(guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                      reuse_policy={"H|V": "unknown"})


def C(name, center, windows, power=20.0, pol="H", mask="loose", bw=4.0):
    return Carrier(id=None, name=name, center_mhz=center, bandwidth_mhz=bw,
                   power_dbm=power, polarization=pol, mask_name=mask, windows=windows)


DAY = [make_window("08:00", "18:00", 480)]
NIGHT = [make_window("18:00", "08:00", 480)]
LATE = [make_window("22:00", "02:00", 480)]


def test_same_frequency_non_overlapping_windows_no_conflict():
    """验收 1：同频、时间不重叠 -> 无任何几何/泄漏结论。"""
    res = analyze([C("T1", 100.0, DAY, 30.0), C("T2", 100.0, NIGHT, 20.0)], RULES)
    assert res["findings"] == []
    assert res["status"] == "ok"
    # 但时间报告要明确二者从不同时激活
    p = res["time_report"]["pairs"][0]
    assert p["coactive"] is False and p["shared_utc"] == []


def test_extending_window_into_overlap_reintroduces_findings():
    """验收 2：把日班延长到 20:00，与夜班在 18:00–20:00 同时激活。

    同极化 + 频带完全重合（同中心）-> overlap；
    而间隔 0.5 MHz 的同频强/弱 loose 配对 -> guard_shortfall + 双向 mask_tail，
    泄漏方向性沿用现有口径。
    """
    day_ext = [make_window("08:00", "20:00", 480)]
    # 完全同频：重叠冲突
    res = analyze([C("T1", 100.0, day_ext, 30.0), C("T2", 100.0, NIGHT, 20.0)], RULES)
    types = [f["type"] for f in res["findings"]]
    assert "overlap" in types
    for f in res["findings"]:
        assert f["time_scope"]["shared_utc"]  # 附带共同激活时段

    # 净距 0.5 MHz：保护带不足 + 双向掩模泄漏，且强->弱泄漏更大
    res2 = analyze([C("A", 100.0, day_ext, 30.0, mask="loose"),
                    C("B", 104.5, NIGHT, 20.0, mask="loose")], RULES)
    types2 = [f["type"] for f in res2["findings"]]
    assert "guard_shortfall" in types2
    tails = {(f["carrier_a"], f["carrier_b"]): f["leakage_dbm"]
             for f in res2["findings"] if f["type"] == "mask_tail"}
    assert set(tails) == {("A", "B"), ("B", "A")}
    assert tails[("A", "B")] > tails[("B", "A")]
    # 共同激活时段：本地 18:00–20:00 = UTC 10:00–12:00
    shared = res2["findings"][0]["time_scope"]["shared_utc"]
    assert shared == [{"start": "10:00", "end": "12:00", "duration_min": 120}]


def test_night_and_late_overlap():
    # T2 夜班与 T3(22:00-02:00) 在 UTC 14-18 同时激活 -> 同频报重叠
    res = analyze([C("T2", 100.0, NIGHT), C("T3", 100.0, LATE)], RULES)
    assert [f["type"] for f in res["findings"]] == ["overlap"]


def test_default_empty_windows_remains_conflict():
    # 无时间数据（默认始终激活）：历史行为不变
    res = analyze([C("a", 100.0, [], mask="strict"),
                   C("b", 100.0, [], mask="strict")], RULES)
    assert [f["type"] for f in res["findings"]] == ["overlap"]


def test_instant_mode_only_active_carriers():
    res = analyze([C("T1", 100.0, DAY, 30.0), C("T2", 100.0, NIGHT, 20.0)],
                  RULES, at_utc_minute=2 * 60)   # 本地 10:00，仅日班
    assert res["power_summary"]["active_names"] == ["T1"]
    assert res["findings"] == []
    assert res["time_report"]["mode"] == "instant"
    assert res["time_report"]["at_utc_label"] == "02:00"
    # 功率汇总只算激活载波
    assert res["power_summary"]["carrier_count"] == 1
    assert res["power_summary"]["total_power_dbm"] == 30.0

    res2 = analyze([C("T1", 100.0, DAY, 30.0), C("T2", 100.0, NIGHT, 20.0)],
                   RULES, at_utc_minute=15 * 60)  # 本地 23:00，仅夜班
    assert res2["power_summary"]["active_names"] == ["T2"]


def test_time_disjoint_pair_skipped_but_other_pair_checked():
    # 三载波：T1/T2 时间错开可同频；但 T4(12-14, 134.5) 与 T1 同时激活且间隔不足
    mid = [make_window("12:00", "14:00", 480)]
    res = analyze([C("T1", 100.0, DAY, 30.0, mask="strict"),
                   C("T2", 100.0, NIGHT, 20.0, mask="strict"),
                   C("T4", 104.5, mid, 25.0, mask="loose")], RULES)
    pairs = {(f["carrier_a"], f["carrier_b"]) for f in res["findings"]}
    assert ("T1", "T2") not in pairs
    assert ("T1", "T4") in pairs


def test_allowed_polarization_time_overlap_still_ok():
    # 时间重合但极化 allowed：仍不报
    rules = AnalysisRules(1.0, -45.0, {"H|V": "allowed"})
    res = analyze([C("a", 100.0, DAY, pol="H"),
                   C("b", 100.0, DAY, pol="V")], rules)
    assert res["findings"] == []


def test_cross_midnight_local_display_in_report():
    res = analyze([C("T3", 100.0, LATE), C("x", 120.0, [], mask="strict")], RULES)
    win = res["time_report"]["carriers"][0]["windows"][0]
    assert win["cross_midnight"] is True
    assert win["start"] == "22:00" and win["end"] == "02:00"
    assert win["tz_label"] == "UTC+08:00"
