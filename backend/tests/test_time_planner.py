"""时间感知 OR-Tools 规划测试。"""
from app.services.analysis import AnalysisRules, Carrier, analyze
from app.services.planner import BandLimits, plan
from app.services.timewindows import make_window

RULES = AnalysisRules(guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                      reuse_policy={"H|V": "unknown"})


def C(name, center, windows, power=20.0, pol="H", mask="loose", bw=4.0):
    return Carrier(id=None, name=name, center_mhz=center, bandwidth_mhz=bw,
                   power_dbm=power, polarization=pol, mask_name=mask, windows=windows)


DAY = [make_window("08:00", "18:00", 480)]
NIGHT = [make_window("18:00", "08:00", 480)]


def test_time_disjoint_pair_can_share_frequency():
    """时间不重叠的同极化载波不需要排开，目标偏移为 0。"""
    carriers = [C("T1", 100.0, DAY, 30.0), C("T2", 100.0, NIGHT, 20.0)]
    r = plan(carriers, RULES, BandLimits(80, 220), "guard_only")
    assert r["feasible"]
    assert all(a["shift_mhz"] == 0.0 for a in r["assignments"])
    pc = {(p["a"], p["b"]): p["constraint"] for p in r["pair_constraints"]}
    assert pc[("T1", "T2")] == "time-disjoint reuse"


def test_time_overlap_pair_still_spaced():
    """时间重合的载波对必须沿用保护间隔/掩模口径。"""
    carriers = [C("A", 100.0, DAY, 30.0, mask="loose"),
                C("B", 104.5, DAY, 20.0, mask="loose")]
    r = plan(carriers, RULES, BandLimits(80, 220), "mask_aware")
    assert r["feasible"]
    gap = (next(a for a in r["assignments"] if a["name"] == "B")["low_mhz"]
           - next(a for a in r["assignments"] if a["name"] == "A")["high_mhz"])
    assert gap >= 1.0 - 1e-9
    # post-check 口径下零越界
    assert r.get("post_check") is None  # plan() 本身不做 post_check（由 API 组装）
    planned = [C(a["name"], a["center_mhz"], DAY, a["power_dbm"],
                 a["polarization"], a["mask_name"], a["bandwidth_mhz"])
               for a in r["assignments"]]
    post = analyze(planned, RULES)
    assert post["counts"]["error"] == 0 and post["counts"]["warning"] == 0


def test_instant_mode_plans_only_active_subset():
    carriers = [C("T1", 100.0, DAY, 30.0), C("T2", 100.0, NIGHT, 20.0)]
    r = plan(carriers, RULES, BandLimits(80, 220), "guard_only",
             at_utc_minute=2 * 60)       # 本地 10:00，只有 T1 激活
    assert r["feasible"]
    assert [a["name"] for a in r["assignments"]] == ["T1"]
    assert [x["name"] for x in r["inactive_carriers"]] == ["T2"]


def test_extend_window_then_replan_passes_postcheck():
    """验收 2 的规划侧：时段延长后重新规划可通过 post-check。"""
    carriers = [C("A", 100.0, [make_window("08:00", "20:00", 480)], 30.0),
                C("B", 104.5, NIGHT, 20.0)]
    r = plan(carriers, RULES, BandLimits(80, 300), "mask_aware")
    assert r["feasible"]
    planned = [C(a["name"], a["center_mhz"],
                 DAY if a["name"] == "A" else NIGHT,
                 a["power_dbm"], a["polarization"], a["mask_name"],
                 a["bandwidth_mhz"]) for a in r["assignments"]]
    post = analyze(planned, RULES)
    assert post["counts"]["error"] == 0
    assert post["counts"]["warning"] == 0
