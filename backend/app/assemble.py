"""schema <-> 领域模型转换，以及绘图数据组装。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from .schemas import CarrierIn, RulesIn
from .services.analysis import AnalysisRules, Carrier
from .services.masks import MASKS, get_mask, psd_on_grid
from .services.time_model import format_hhmm
from .services.units import dbm_to_watt


def _windows_dicts(c: CarrierIn):
    return [w.to_window() for w in c.schedule]


def to_domain(c: CarrierIn, cid: int | None = None) -> Carrier:
    windows = tuple(_windows_dicts(c))
    return Carrier(
        id=cid, name=c.name, center_mhz=c.center_mhz,
        bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
        polarization=c.polarization, mask_name=c.mask_name,
        windows=windows,
    )


def to_rules(r: RulesIn) -> AnalysisRules:
    return AnalysisRules(
        guard_required_mhz=r.guard_required_mhz,
        leakage_limit_dbm=r.leakage_limit_dbm,
        reuse_policy=dict(r.reuse_policy),
    )


def validate_masks(carriers: list[CarrierIn]) -> None:
    for c in carriers:
        if c.mask_name not in MASKS:
            raise ValueError(f"载波 {c.name!r} 引用了未知掩模 {c.mask_name!r}")


def _schedule_view(c: Carrier) -> list[dict]:
    return [{
        "start": format_hhmm(w.start_min),
        "end": "24:00" if w.end_min == 1440 else format_hhmm(w.end_min),
        "tz_offset_minutes": w.tz_offset_minutes,
        "crosses_midnight": w.crosses_midnight,
    } for w in c.windows]


def build_spectrum(carriers: list[Carrier], grid_step_mhz: float) -> dict:
    """在统一频率网格上组装各载波 PSD 曲线与聚合谱（线性域功率叠加）。

    调用方负责按时间口径过滤：instant 视图只传此刻激活的载波，
    使聚合谱真实反映“同一时刻同时在发射”的叠加。
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
        w_hz = dbm_to_watt(psd)  # -inf -> 0 W
        total_w_hz += w_hz
        # 绘图用 null 表示无信号，避免 Plotly 把 -inf 画成贴底线
        curves.append({
            "name": c.name,
            "mask_name": c.mask_name,
            "polarization": c.polarization,
            "schedule": _schedule_view(c),
            "always_active": c.always_active,
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


def bands_view(carriers: list[Carrier], at: Optional[datetime] = None) -> list[dict]:
    """频段条数据。instant 口径下全部返回，但用 active 标记此刻是否激活，

    供前端把未激活载波灰显；冲突检查与聚合谱只按激活载波工作。
    """
    return [{
        "name": c.name,
        "center_mhz": c.center_mhz,
        "low_mhz": round(c.low, 4),
        "high_mhz": round(c.high, 4),
        "bandwidth_mhz": c.bandwidth_mhz,
        "power_dbm": c.power_dbm,
        "polarization": c.polarization,
        "mask_name": c.mask_name,
        "schedule": _schedule_view(c),
        "always_active": c.always_active,
        "active": (c.is_active_at(at) if at is not None else True),
    } for c in carriers]
