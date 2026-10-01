"""频带冲突分析（纯函数、离线简化模型）。

检查三类冲突，每条结果都能定位到具体载波（或载波对）：

1. overlap        频带重叠：相邻边缘间隙 <= 0
2. guard_shortfall 保护带不足：0 < 边缘间隙 < 要求保护间隔
3. mask_tail      掩模尾部越界：发射机掩模拖尾落入对方频带内的功率超过限值

极化复用规则（reuse_policy，针对一对不同极化）：
- "forbidden" 不允许同频复用：重叠时按冲突处理
- "allowed"   已知隔离度足够：重叠允许，不报几何/泄漏冲突
- "unknown"   隔离度未知：几何重叠时只给“待评估”，不下违规定性

时间口径（schedule，见 services/time_model.py）：
- 每个载波带每日循环激活区间（本地挂钟 + UTC 偏移，可跨午夜）；
  空列表 = 始终激活（旧数据升级的唯一默认语义）。
- analyze 接收 at（aware datetime）时，只评估该时刻实际激活的载波；
  at=None 为全天口径：只有在一天内存在共同激活时刻的载波对才参与冲突检查，
  频率相同但时间完全不重叠的两载波不产生几何/泄漏冲突。

功率汇总：线性域 (W) 求和后再换算 dBm 显示，见 units.py。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

import numpy as np

from .masks import get_mask, spectrum_curve
from .time_model import (Window, active_at, describe_schedule,
                         schedules_overlap)
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
    # 每日激活区间；空 tuple = 始终激活
    windows: tuple[Window, ...] = field(default_factory=tuple)

    @property
    def low(self) -> float:
        return self.center_mhz - self.bandwidth_mhz / 2.0

    @property
    def high(self) -> float:
        return self.center_mhz + self.bandwidth_mhz / 2.0

    @property
    def always_active(self) -> bool:
        return not self.windows

    def is_active_at(self, dt: datetime) -> bool:
        return active_at(self.windows, dt)

    def overlaps_schedule_of(self, other: "Carrier") -> bool:
        return schedules_overlap(self.windows, other.windows)


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
             message: str, **metrics) -> dict:
    out = {
        "type": ftype,
        "severity": severity,
        "carrier_a": a.name,
        "carrier_b": b.name,
        "message": message,
    }
    out.update(metrics)
    return out


def _time_context(carriers: list[Carrier], at: Optional[datetime]) -> dict:
    """组装响应里的时间口径说明（前端图表/结论按它展示时刻）。"""
    if at is not None:
        active_names = [c.name for c in carriers if c.is_active_at(at)]
        return {
            "scope": "instant",
            "at_utc": at.astimezone(timezone.utc).isoformat(),
            "active_carriers": active_names,
            "inactive_carriers": [c.name for c in carriers if c.name not in active_names],
        }
    return {
        "scope": "anytime",
        "at_utc": None,
        "active_carriers": [c.name for c in carriers],
        "inactive_carriers": [],
    }


def _eligible_pairs(carriers: list[Carrier], at: Optional[datetime]):
    """产出需要检查的载波对及其时间关系。

    instant 口径：双方在 at 时刻都激活；
    anytime 口径：双方在一天循环内存在共同激活时刻。
    返回 (a, b, time_separated)；time_separated=True 的对只登记、不检查冲突。
    """
    for a, b in itertools.combinations(carriers, 2):
        if at is not None:
            if a.is_active_at(at) and b.is_active_at(at):
                yield a, b, False
        elif a.overlaps_schedule_of(b):
            yield a, b, False
        else:
            yield a, b, True


def analyze(carriers: list[Carrier], rules: AnalysisRules,
            at: Optional[datetime] = None) -> dict:
    """对整组载波做冲突检查与功率汇总。

    at 为带时区的时刻时，只检查该时刻同时激活的载波（功率汇总也只算它们）；
    at 为 None 时按全天口径，时间完全不重叠的载波对不计任何冲突。
    """
    findings: list[dict] = []
    time_separated_pairs: list[dict] = []
    evaluated: list[Carrier] = []

    for a, b, time_separated in _eligible_pairs(carriers, at):
        if at is None and time_separated:
            # 频率可能相同，但排程一天内互不重合 -> 不可能互相干扰，登记后放行
            time_separated_pairs.append({
                "carrier_a": a.name, "carrier_b": b.name,
                "schedule_a": describe_schedule(a.windows),
                "schedule_b": describe_schedule(b.windows),
            })
            continue

        evaluated.extend(x for x in (a, b) if x not in evaluated)

        # 边缘净距：频带不相交时 >0（净空），相切时 0，重叠时 <0
        gap = max(a.low, b.low) - min(a.high, b.high)
        overlap = max(0.0, -gap)
        same_pol = a.polarization == b.polarization
        # 同极化没有极化隔离可言，按禁止同频复用处理；异极化查输入规则
        policy: str = "forbidden" if same_pol else rules.policy_for(a.polarization, b.polarization)
        pol_key = "|".join(sorted((a.polarization, b.polarization)))

        # ---- 几何关系：重叠 / 保护带 ----
        if gap <= 0.0:
            if same_pol:
                findings.append(_finding(
                    "overlap", "error", a, b,
                    f"{a.name} 与 {b.name} 频带重叠 {overlap:.3f} MHz（同极化 {a.polarization}）",
                    gap_mhz=round(gap, 4), overlap_mhz=round(overlap, 4),
                    polarization_pair=pol_key, reuse_policy="forbidden"))
            elif policy == "forbidden":
                findings.append(_finding(
                    "overlap", "error", a, b,
                    f"{a.name} 与 {b.name} 频带重叠 {overlap:.3f} MHz，"
                    f"且极化 {a.polarization}/{b.polarization} 规则禁止同频复用",
                    gap_mhz=round(gap, 4), overlap_mhz=round(overlap, 4),
                    polarization_pair=pol_key, reuse_policy="forbidden"))
            elif policy == "unknown":
                findings.append(_finding(
                    "reuse_unknown", "pending", a, b,
                    f"{a.name} 与 {b.name} 同频段重叠 {overlap:.3f} MHz，"
                    f"极化 {a.polarization}/{b.polarization} 的隔离度未知，复用待评估",
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
                        leakage_dbm=round(leak, 2),
                        limit_dbm=rules.leakage_limit_dbm,
                        excess_dbm=round(leak - rules.leakage_limit_dbm, 2),
                        polarization_pair=pol_key, reuse_policy=policy,
                        direction=f"{tx.name}->{victim.name}"))

    # ---- 功率汇总：线性域求和。instant 口径只汇总此刻激活的载波 ----
    if at is not None:
        summed = [c for c in carriers if c.is_active_at(at)]
    else:
        summed = list(carriers)
    per_carrier = [
        {"name": c.name, "power_dbm": c.power_dbm, "power_w": dbm_to_watt(c.power_dbm),
         "active": c.is_active_at(at) if at is not None else True}
        for c in carriers
    ]
    powers_dbm = [c.power_dbm for c in summed]
    total_w = total_power_watt(powers_dbm)
    summary = {
        "carrier_count": len(carriers),
        "active_carrier_count": len(summed),
        "total_power_w": total_w,
        # 无激活载波时不输出 -inf（不是合法 JSON），用 None 表示“此刻无功率”
        "total_power_dbm": watt_to_dbm(total_w) if summed and total_w > 0 else None,
        # 仅供教学对比：直接对 dBm 求和是常见错误做法
        "naive_dbm_sum": round(sum(powers_dbm), 3) if powers_dbm else None,
        "per_carrier": per_carrier,
    }

    counts = {"error": 0, "warning": 0, "pending": 0, "ok": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    return {
        "findings": findings,
        "time_separated_pairs": time_separated_pairs,
        "time_context": _time_context(carriers, at),
        "power_summary": summary,
        "counts": counts,
        "status": "conflict" if counts["error"] else ("attention" if (counts["warning"] or counts["pending"]) else "ok"),
    }
