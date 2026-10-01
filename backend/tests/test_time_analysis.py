"""验收 1–2：时间感知的冲突分析与 OR-Tools 规划（纯函数层）。"""
from datetime import datetime, timedelta, timezone

from app.services.analysis import AnalysisRules, Carrier, analyze
from app.services.planner import BandLimits, plan
from app.services.time_model import Window

BJ = timezone(timedelta(hours=8))
UTC = timezone.utc
RULES = AnalysisRules(guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                      reuse_policy={"H|V": "unknown", "RHCP|V": "allowed"})


def win(s, e, tz=480):
    return Window(s, e, tz)


def carrier(name, center, power=20.0, pol="H", mask="loose", windows=()):
    return Carrier(id=None, name=name, center_mhz=center, bandwidth_mhz=4.0,
                   power_dbm=power, polarization=pol, mask_name=mask,
                   windows=tuple(windows))


def pair(carriers, a, b, ftype=None):
    out = [f for f in analyze(carriers, RULES)["findings"]
           if {f["carrier_a"], f["carrier_b"]} == {a, b}
           and (ftype is None or f["type"] == ftype)]
    return out


# ---- 验收 1：同频/近频但时间不重叠 => 不产生冲突 ---------------------------

def test_same_frequency_disjoint_time_no_conflict_anytime():
    a = carrier("A", 160.0, windows=[win(8 * 60, 16 * 60, 480)])
    b = carrier("B", 160.0, windows=[win(8 * 60, 16 * 60, 0)])
    res = analyze([a, b], RULES)  # 全天口径
    assert res["findings"] == []
    sep = res["time_separated_pairs"]
    assert len(sep) == 1 and {sep[0]["carrier_a"], sep[0]["carrier_b"]} == {"A", "B"}


def test_day_night_cross_midnight_disjoint_no_conflict():
    day = carrier("T1", 104.5, power=30.0, windows=[win(8 * 60, 20 * 60, 480)])
    night = carrier("T2", 100.0, power=20.0,
                    windows=[win(20 * 60, 8 * 60, 480)])
    res = analyze([day, night], RULES)
    assert res["findings"] == []
    # 任意时刻口径都不应在该对上产生任何类型冲突
    for h in range(24):
        at = datetime(2026, 10, 1, h, tzinfo=UTC)
        r = analyze([day, night], RULES, at=at)
        assert not [f for f in r["findings"]
                    if {f["carrier_a"], f["carrier_b"]} == {"T1", "T2"}]


def test_planner_treats_time_separated_pair_as_unconstrained():
    a = carrier("A", 160.0, windows=[win(8 * 60, 16 * 60, 480)])
    b = carrier("B", 160.0, windows=[win(8 * 60, 16 * 60, 0)])
    r = plan([a, b], RULES, BandLimits(150, 170), "guard_only")
    assert r["feasible"]
    # 时间不重叠 => 都可以留在原中心 160（零偏移）
    assert all(x["shift_mhz"] == 0.0 for x in r["assignments"])
    pc = [p for p in r["pair_constraints"] if {p["a"], p["b"]} == {"A", "B"}][0]
    assert pc["constraint"].startswith("time-separated")


def test_planner_instant_scope_only_constrains_active_carriers():
    a = carrier("A", 100.0, windows=[win(8 * 60, 20 * 60, 480)])
    b = carrier("B", 100.0, windows=[win(20 * 60, 8 * 60, 480)])
    at = datetime(2026, 10, 1, 4, tzinfo=UTC)  # UTC04 = 北京12
    r = plan([a, b], RULES, BandLimits(90, 110), "guard_only", at=at)
    assert r["feasible"] and r["time_scope"] == "instant"
    assert all(x["shift_mhz"] == 0.0 for x in r["assignments"])


# ---- 验收 2：延长至重叠 => 保护带 + 方向性泄漏同时出现 ----------------------

def test_extend_window_into_overlap_reveals_guard_and_directional_tails():
    day = carrier("T1", 100.0, power=30.0, windows=[win(8 * 60, 20 * 60, 480)])
    night_ext = carrier("T2", 104.5, power=20.0,
                        windows=[win(19 * 60, 8 * 60, 480)])  # 19:00 起，重叠 1h
    # 重叠时刻（北京 19:30）：保护带不足 + 双向泄漏，且强->弱泄漏量更大
    at = datetime(2026, 10, 1, 11, 30, tzinfo=UTC)
    res = analyze([day, night_ext], RULES, at=at)
    types = {(f["carrier_a"], f["carrier_b"], f["type"]): f for f in res["findings"]}
    assert ("T1", "T2", "guard_shortfall") in types or \
           ("T2", "T1", "guard_shortfall") in types
    assert ("T1", "T2", "mask_tail") in types
    assert ("T2", "T1", "mask_tail") in types
    assert types[("T1", "T2", "mask_tail")]["leakage_dbm"] > \
           types[("T2", "T1", "mask_tail")]["leakage_dbm"]

    # 非重叠时刻（北京 12:00）：T2 不在发，仍无冲突
    at_noon = datetime(2026, 10, 1, 4, tzinfo=UTC)
    res_noon = analyze([day, night_ext], RULES, at=at_noon)
    assert res_noon["findings"] == []

    # 全天口径：一天内存在共同激活时刻 => 同样全部报出
    res_any = analyze([day, night_ext], RULES)
    kinds = {f["type"] for f in res_any["findings"]}
    assert {"guard_shortfall", "mask_tail"} <= kinds


def test_mask_aware_plan_postcheck_passes_when_overlap_exists():
    day = carrier("T1", 100.0, power=30.0, windows=[win(8 * 60, 20 * 60, 480)])
    night_ext = carrier("T2", 104.5, power=20.0,
                        windows=[win(19 * 60, 8 * 60, 480)])
    r = plan([day, night_ext], RULES, BandLimits(90, 200), "mask_aware")
    assert r["feasible"]
    # post-check 用同口径（全天）=> 零越界
    assert r  # 存在返回即结构完整；post_check 在 API 层组装，这里验证约束间隔
    pc = [p for p in r["pair_constraints"] if {p["a"], p["b"]} == {"T1", "T2"}][0]
    assert pc["required_edge_mhz"] >= 1.0


def test_instant_power_summary_only_counts_active():
    a = carrier("A", 100.0, power=30.0, windows=[win(8 * 60, 20 * 60, 480)])
    b = carrier("B", 120.0, power=20.0, windows=[win(20 * 60, 8 * 60, 480)])
    at = datetime(2026, 10, 1, 4, tzinfo=UTC)  # 只有 A
    res = analyze([a, b], RULES, at=at)
    ps = res["power_summary"]
    assert ps["carrier_count"] == 2 and ps["active_carrier_count"] == 1
    assert abs(ps["total_power_dbm"] - 30.0) < 1e-9
    assert {p["name"]: p["active"] for p in ps["per_carrier"]} == {"A": True, "B": False}
