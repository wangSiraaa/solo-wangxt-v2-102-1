"""schema <-> 领域模型转换，以及绘图数据组装。"""
from __future__ import annotations

import numpy as np

from .schemas import CarrierIn, RulesIn
from .services.analysis import AnalysisRules, Carrier
from .services.masks import MASKS, get_mask, psd_on_grid
from .services.timewindows import (describe_window, validate_carrier_windows)
from .services.units import dbm_to_watt


def to_domain(c: CarrierIn, cid: int | None = None) -> Carrier:
    return Carrier(
        id=cid, name=c.name, center_mhz=c.center_mhz,
        bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
        polarization=c.polarization, mask_name=c.mask_name,
        windows=[w.to_domain() for w in c.windows],
    )


def to_rules(r: RulesIn) -> AnalysisRules:
    return AnalysisRules(
        guard_required_mhz=r.guard_required_mhz,
        leakage_limit_dbm=r.leakage_limit_dbm,
        reuse_policy=dict(r.reuse_policy),
    )


def validate_carriers(carriers: list[CarrierIn]) -> None:
    for c in carriers:
        if c.mask_name not in MASKS:
            raise ValueError(f"载波 {c.name!r} 引用了未知掩模 {c.mask_name!r}")
        windows = [w.to_domain() for w in c.windows]
        validate_carrier_windows(windows, c.name)


# 兼容旧调用名
def validate_masks(carriers: list[CarrierIn]) -> None:
    validate_carriers(carriers)


def resolve_at_utc(at_time) -> int | None:
    """AtTimeIn（当地时刻 + UTC 偏移）-> UTC 当日分钟；None 透传。"""
    if at_time is None:
        return None
    from .services.timewindows import parse_hhmm
    return (parse_hhmm(at_time.local_time) - at_time.tz_offset_minutes) % 1440


def build_spectrum(carriers: list[Carrier], grid_step_mhz: float,
                   active_names: set[str] | None = None) -> dict:
    """在统一频率网格上组装各载波 PSD 曲线与聚合谱（线性域功率叠加）。

    active_names 给定时：非激活载波曲线照常返回但置 active=false 且不参与聚合，
    供前端按同一时刻口径淡化显示。
    """
    if not carriers:
        return {"f_mhz": [], "curves": [], "aggregate_dbm_hz": []}

    span = max(get_mask(c.mask_name).span_mhz for c in carriers)
    f_lo = min(c.center_mhz - span for c in carriers)
    f_hi = max(c.center_mhz + span for c in carriers)
    f = np.arange(f_lo, f_hi + grid_step_mhz / 2, grid_step_mhz)

    curves = []
    total_w_hz = np.zeros_like(f)
    for c in carriers:
        psd = psd_on_grid(get_mask(c.mask_name), f, c.center_mhz,
                          c.bandwidth_mhz, c.power_dbm)
        is_active = active_names is None or c.name in active_names
        if is_active:
            total_w_hz += dbm_to_watt(psd)  # -inf -> 0 W
        # 绘图用 null 表示无信号，避免 Plotly 把 -inf 画成贴底线
        curves.append({
            "name": c.name,
            "mask_name": c.mask_name,
            "polarization": c.polarization,
            "active": is_active,
            "always_active": not c.windows,
            "windows": [describe_window(w) for w in c.windows],
            "psd_dbm_hz": [None if not np.isfinite(v) else round(float(v), 2)
                           for v in psd],
        })
    with np.errstate(divide="ignore"):
        agg_dbm = 10.0 * np.log10(np.where(total_w_hz > 0, total_w_hz / 1e-3, np.nan))
    aggregate = [None if not np.isfinite(v) else round(float(v), 2) for v in agg_dbm]
    return {
        "f_mhz": [round(float(v), 4) for v in f],
        "curves": curves,
        "aggregate_dbm_hz": aggregate,
    }


def bands_view(carriers: list[Carrier],
               active_names: set[str] | None = None) -> list[dict]:
    return [{
        "name": c.name,
        "center_mhz": c.center_mhz,
        "low_mhz": round(c.low, 4),
        "high_mhz": round(c.high, 4),
        "bandwidth_mhz": c.bandwidth_mhz,
        "power_dbm": c.power_dbm,
        "polarization": c.polarization,
        "mask_name": c.mask_name,
        "active": active_names is None or c.name in active_names,
        "always_active": not c.windows,
        "windows": [describe_window(w) for w in c.windows],
    } for c in carriers]
