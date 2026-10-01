"""频带冲突分析（纯函数、离线简化模型）。

检查三类冲突，每条结果都能定位到具体载波（或载波对）：

1. overlap        频带重叠：相邻边缘间隙 <= 0
2. guard_shortfall 保护带不足：0 < 边缘间隙 < 要求保护间隔
3. mask_tail      掩模尾部越界：发射机掩模拖尾落入对方频带内的功率超过限值

极化复用规则（reuse_policy，针对一对不同极化）：
- "forbidden" 不允许同频复用：重叠时按冲突处理
- "allowed"   已知隔离度足够：重叠允许，不报几何/泄漏冲突
- "unknown"   隔离度未知：几何重叠时只给“待评估”，不下违规定性

功率汇总：线性域 (W) 求和后再换算 dBm 显示，见 units.py。
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from .masks import get_mask, spectrum_curve
from .timewindows import (DAY_MIN, TimeWindow, active_at, coactive,
                          describe_window, fmt_hhmm, shared_utc_segments)
from .units import dbm_to_watt, total_power_watt, watt_to_dbm

Polarization = Literal["H", "V", "LHCP", "RHCP"]
ReusePolicy = Literal["forbidden", "allowed", "unknown"]

POLARIZATIONS: tuple[str, ...] = ("H", "V", "LHCP", "RHCP")


@dataclass
class Carrier:
    id: Optional[int]
    name: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    polarization: str
    mask_name: str
    # 每日重复的激活区间（本地墙钟 + 显式 UTC 偏移）；空 = 始终激活（旧数据默认）
    windows: list[TimeWindow] = field(default_factory=list)

    @property
    def low(self) -> float:
        return self.center_mhz - self.bandwidth_mhz / 2.0

    @property
    def high(self) -> float:
        return self.center_mhz + self.bandwidth_mhz / 2.0

    @property
    def always_active(self) -> bool:
        return not self.windows


@dataclass
class AnalysisRules:
    # 要求的最小保护间隔 (MHz)：相邻频带边缘之间的净距
    guard_required_mhz: float = 1.0
    # 掩模尾部落入邻载波频带的功率限值 (dBm)
    leakage_limit_dbm: float = -45.0
    # 不同极化对的复用规则，key 形如 "H|V"（极化名排序后拼接）
    # forbidden / allowed / unknown；查不到按 unknown 处理
    reuse_policy: Optional[dict[str, str]] = None

    def policy_for(self, pol_a: str, pol_b: str) -> ReusePolicy:
        if self.reuse_policy is None:
            return "unknown"
        key = "|".join(sorted((pol_a, pol_b)))
        return self.reuse_policy.get(key, "unknown")  # type: ignore[return-value]


def edge_gap_mhz(a: Carrier, b: Carrier) -> float:
    """两个频带边缘之间的净距 (MHz)；正值=净空，0=相切，负值=重叠。"""
    return max(a.low, b.low) - min(a.high, b.high)


def leakage_power_dbm(tx: Carrier, victim: Carrier,
                      grid_step_mhz: float = 0.01) -> float:
    """tx 载波的掩模发射谱落入 victim 频带内的总功率 (dBm)。

    PSD 在线性域 (W/Hz) 上对频率积分：
        P_leak = ∫_victim_band 10**(psd_dbm_hz/10) * 1e-3 df
    df 以 Hz 计。超出掩模跨度的网格点 psd = -inf，贡献为 0。
    """
    mask = get_mask(tx.mask_name)
    f, psd = spectrum_curve(mask, tx.center_mhz, tx.bandwidth_mhz,
                            tx.power_dbm, grid_step_mhz)
    inside = (f >= victim.low - 1e-12) & (f <= victim.high + 1e-12)
    if not np.any(inside):
        return float("-inf")
    psd_w_hz = dbm_to_watt(psd[inside])
    df_hz = grid_step_mhz * 1e6
    p_w = float(np.trapezoid(psd_w_hz, dx=df_hz))
    return watt_to_dbm(p_w)


def _finding(ftype: str, severity: str, a: Carrier, b: Carrier,
             message: str, time_note: Optional[dict] = None, **metrics) -> dict:
    out = {
        "type": ftype,
        "severity": severity,
        "carrier_a": a.name,
        "carrier_b": b.name,
        "message": message,
    }
    if time_note is not None:
        out["time_scope"] = time_note
    out.update(metrics)
    return out


def _pair_time_note(a: Carrier, b: Carrier,
                    shared: list[tuple[int, int]]) -> dict:
    """findings 上附带的时间口径：两载波同时激活的 UTC 时段。"""
    return {
        "mode": "all_day",
        "carrier_a_windows": [describe_window(w) for w in a.windows],
        "carrier_b_windows": [describe_window(w) for w in b.windows],
        "a_always_active": a.always_active,
        "b_always_active": b.always_active,
        "shared_utc": [
            {"start": fmt_hhmm(lo), "end": fmt_hhmm(hi) if hi < DAY_MIN else "24:00",
             "duration_min": hi - lo}
            for lo, hi in shared
        ],
    }


def analyze(carriers: list[Carrier], rules: AnalysisRules,
            at_utc_minute: Optional[int] = None) -> dict:
    """对整组载波做冲突检查与功率汇总。

    时间口径：
    - ``at_utc_minute`` 为 None（全天配对）：只对一天内存在同时激活时段的载波对
      做几何/保护带/掩模检查；时间互不交叠的同频载波不构成任何冲突。
    - ``at_utc_minute`` 给定时刻（UTC 当日分钟）：只统计该时刻真正处于激活状态
      的载波（频段图/谱图/功率汇总同口径），结论绑定到这一时刻。
    """
    if at_utc_minute is not None:
        active_carriers = [c for c in carriers if active_at(c.windows, at_utc_minute)]
    else:
        active_carriers = list(carriers)
    active_names = {c.name for c in active_carriers}
    findings: list[dict] = []
    pair_reports: list[dict] = []

    for a, b in itertools.combinations(carriers, 2):
        a_on = a.name in active_names
        b_on = b.name in active_names
        both_on = a_on and b_on
        shared = shared_utc_segments(a.windows, b.windows)
        overlapping_in_time = bool(shared)
        pair_reports.append({
            "carrier_a": a.name, "carrier_b": b.name,
            "a_always_active": a.always_active, "b_always_active": b.always_active,
            "a_active": a_on, "b_active": b_on,
            "coactive": overlapping_in_time and (at_utc_minute is None or both_on),
            "shared_utc": [
                {"start": fmt_hhmm(lo), "end": fmt_hhmm(hi) if hi < DAY_MIN else "24:00",
                 "duration_min": hi - lo}
                for lo, hi in shared
            ],
            "carrier_a_windows": [describe_window(w) for w in a.windows],
            "carrier_b_windows": [describe_window(w) for w in b.windows],
        })
        if not (overlapping_in_time and both_on):
            # 时间不交叠（或该时刻未同时发射）：频率相同也不报几何/泄漏冲突
            continue

        # 边缘净距：频带不相交时 >0（净空），相切时 0，重叠时 <0
        gap = max(a.low, b.low) - min(a.high, b.high)
        overlap = max(0.0, -gap)
        same_pol = a.polarization == b.polarization
        # 同极化没有极化隔离可言，按禁止同频复用处理；异极化查输入规则
        policy: str = "forbidden" if same_pol else rules.policy_for(a.polarization, b.polarization)
        pol_key = "|".join(sorted((a.polarization, b.polarization)))
        time_note = None if (a.always_active and b.always_active) else _pair_time_note(a, b, shared)

        # ---- 几何关系：重叠 / 保护带 ----
        if gap <= 0.0:
            if same_pol:
                findings.append(_finding(
                    "overlap", "error", a, b,
                    f"{a.name} 与 {b.name} 频带重叠 {overlap:.3f} MHz（同极化 {a.polarization}）",
                    time_note=time_note,
                    gap_mhz=round(gap, 4), overlap_mhz=round(overlap, 4),
                    polarization_pair=pol_key, reuse_policy="forbidden"))
            elif policy == "forbidden":
                findings.append(_finding(
                    "overlap", "error", a, b,
                    f"{a.name} 与 {b.name} 频带重叠 {overlap:.3f} MHz，"
                    f"且极化 {a.polarization}/{b.polarization} 规则禁止同频复用",
                    time_note=time_note,
                    gap_mhz=round(gap, 4), overlap_mhz=round(overlap, 4),
                    polarization_pair=pol_key, reuse_policy="forbidden"))
            elif policy == "unknown":
                findings.append(_finding(
                    "reuse_unknown", "pending", a, b,
                    f"{a.name} 与 {b.name} 同频段重叠 {overlap:.3f} MHz，"
                    f"极化 {a.polarization}/{b.polarization} 的隔离度未知，复用待评估",
                    time_note=time_note,
                    gap_mhz=round(gap, 4), overlap_mhz=round(overlap, 4),
                    polarization_pair=pol_key, reuse_policy="unknown"))
            # policy == "allowed"：已知隔离度足够，允许复用，不报冲突
        elif gap < rules.guard_required_mhz:
            if same_pol or policy in ("forbidden", "unknown"):
                findings.append(_finding(
                    "guard_shortfall", "warning", a, b,
                    f"{a.name} 与 {b.name} 边缘净距 {gap:.3f} MHz "
                    f"< 要求保护间隔 {rules.guard_required_mhz:.3f} MHz"
                    + ("" if same_pol else f"（极化 {a.polarization}/{b.polarization}，"
                       f"复用规则: {policy}）"),
                    time_note=time_note,
                    gap_mhz=round(gap, 4),
                    required_mhz=rules.guard_required_mhz,
                    deficit_mhz=round(rules.guard_required_mhz - gap, 4),
                    polarization_pair=pol_key, reuse_policy=policy))

        # ---- 掩模尾部越界（两个方向都算，功率/掩模不同则结果不对称）----
        # 允许复用（已知隔离足够）或已几何重叠的配对不再重复报尾部泄漏。
        geometrically_overlap = gap <= 0.0
        skip_tail = (policy == "allowed") or geometrically_overlap
        if not skip_tail:
            for tx, victim in ((a, b), (b, a)):
                leak = leakage_power_dbm(tx, victim)
                if leak > rules.leakage_limit_dbm:
                    findings.append(_finding(
                        "mask_tail", "error", tx, victim,
                        f"{tx.name} 的掩模尾部泄漏到 {victim.name} 频带内 "
                        f"{leak:.1f} dBm，超过限值 {rules.leakage_limit_dbm:.1f} dBm",
                        time_note=time_note,
                        leakage_dbm=round(leak, 2),
                        limit_dbm=rules.leakage_limit_dbm,
                        excess_dbm=round(leak - rules.leakage_limit_dbm, 2),
                        polarization_pair=pol_key, reuse_policy=policy,
                        direction=f"{tx.name}->{victim.name}"))

    # ---- 功率汇总：线性域求和。时刻口径只统计当时激活的载波 ----
    counted = active_carriers
    per_carrier = [
        {"name": c.name, "power_dbm": c.power_dbm, "power_w": dbm_to_watt(c.power_dbm),
         "active": c.name in active_names, "always_active": c.always_active}
        for c in carriers
    ]
    powers_dbm = [c.power_dbm for c in counted]
    total_w = total_power_watt(powers_dbm)
    summary = {
        "carrier_count": len(counted),
        "carrier_count_total": len(carriers),
        "active_names": sorted(active_names),
        "inactive_names": sorted(c.name for c in carriers if c.name not in active_names),
        "total_power_w": total_w,
        # 该时刻无激活载波时总功率为 null（避免输出非标准 JSON 的 -Infinity）
        "total_power_dbm": watt_to_dbm(total_w) if total_w > 0 else None,
        # 仅供教学对比：直接对 dBm 求和是常见错误做法
        "naive_dbm_sum": round(sum(powers_dbm), 3) if powers_dbm else None,
        "per_carrier": per_carrier,
    }

    counts = {"error": 0, "warning": 0, "pending": 0, "ok": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    time_report = {
        "mode": "instant" if at_utc_minute is not None else "all_day",
        "at_utc_minute": None if at_utc_minute is None else at_utc_minute % DAY_MIN,
        "at_utc_label": None if at_utc_minute is None else fmt_hhmm(at_utc_minute),
        "carriers": [
            {"name": c.name, "always_active": c.always_active,
             "windows": [describe_window(w) for w in c.windows],
             "active": c.name in active_names}
            for c in carriers
        ],
        "pairs": pair_reports,
    }

    return {
        "findings": findings,
        "power_summary": summary,
        "counts": counts,
        "time_report": time_report,
        "status": "conflict" if counts["error"] else ("attention" if (counts["warning"] or counts["pending"]) else "ok"),
    }
