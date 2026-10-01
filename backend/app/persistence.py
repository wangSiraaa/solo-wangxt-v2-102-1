"""场景内容指纹与已保存规划的过期判定。

规划保存时记录两个绑定：
- ``scenario_revision``：场景修订号（每次编辑 +1）；
- ``fingerprint``：场景全部规划相关内容的规范化哈希（频段、规则、每个载波的
  频率/带宽/功率/极化/掩模，以及时间窗口在 UTC 圆周上的实际激活弧）。

读取已保存规划时动态比较当前场景：
- 修订号一致 **且** 指纹一致 -> ``executable``，可直接复用；
- 否则 -> ``expired``，附人类可读原因；记录本身保留为历史档案，不删除不改写。

指纹基于 UTC 弧而非录入文本：只修改时区偏移写法、但物理激活时段不变，
方案仍可执行；真正改变“同一时刻谁在发射”的编辑才会使方案失效。
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .db import PlanRow, Scenario
from .services.timewindows import TimeWindow


def _window_arc(w: TimeWindow) -> list[list[int]]:
    return [list(seg) for seg in w.utc_segments()]


def scenario_fingerprint(sc: Scenario | dict, carriers: Iterable | None = None) -> str:
    """场景规划相关内容的 SHA256 指纹。

    接受两种输入：
    - ORM Scenario（carriers 从关系取）；
    - dict（API 请求体，carriers 为 CarrierIn 列表）。
    """
    if isinstance(sc, dict):
        if "rules" in sc and sc.get("rules") is not None:
            rules = sc["rules"]
            guard = rules.guard_required_mhz
            leak = rules.leakage_limit_dbm
            reuse = dict(rules.reuse_policy)
        else:
            guard = sc.get("guard_required_mhz", 1.0)
            leak = sc.get("leakage_limit_dbm", -45.0)
            reuse = sc.get("reuse_policy", {})
        cs = sc["carriers"]
        payload = {
            "band": [sc.get("band_low_mhz", 80.0), sc.get("band_high_mhz", 220.0)],
            "rules": {"guard_required_mhz": guard,
                      "leakage_limit_dbm": leak,
                      "reuse_policy": {k: reuse[k] for k in sorted(reuse)}},
            "carriers": [
                {"name": c.name.strip(), "center_mhz": c.center_mhz,
                 "bandwidth_mhz": c.bandwidth_mhz, "power_dbm": c.power_dbm,
                 "polarization": c.polarization, "mask_name": c.mask_name,
                 "windows": sorted(
                     [list(seg) for win in c.windows for seg in win.to_domain().utc_segments()]
                 )}
                for c in sorted(cs, key=lambda c: c.name.strip())
            ],
        }
    else:
        cs = carriers if carriers is not None else sc.carriers
        payload = {
            "band": [sc.band_low_mhz, sc.band_high_mhz],
            "rules": {"guard_required_mhz": sc.guard_required_mhz,
                      "leakage_limit_dbm": sc.leakage_limit_dbm,
                      "reuse_policy": {k: sc.reuse_policy[k]
                                       for k in sorted(sc.reuse_policy or {})}},
            "carriers": [
                {"name": r.name, "center_mhz": r.center_mhz,
                 "bandwidth_mhz": r.bandwidth_mhz, "power_dbm": r.power_dbm,
                 "polarization": r.polarization, "mask_name": r.mask_name,
                 "windows": sorted(_stored_window_arcs(r.windows))}
                for r in sorted(cs, key=lambda r: r.name)
            ],
        }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stored_window_arcs(stored) -> list[list[int]]:
    """DB 中存的窗口 JSON（start/end/offset）-> UTC 弧。容错地容忍旧/脏数据。"""
    from .services.timewindows import make_window
    arcs: list[list[int]] = []
    for w in stored or []:
        if isinstance(w, dict):
            win = make_window(w.get("start", "00:00"), w.get("end", "00:00"),
                              int(w.get("tz_offset_minutes", 480)))
            arcs.extend([list(seg) for seg in win.utc_segments()])
    return arcs


def plan_record_status(row: PlanRow, sc: Scenario) -> tuple[str, str | None]:
    """对比当前场景，返回 (status, reason)。"""
    if sc is None:
        return "expired", "所属场景已被删除"
    if row.fingerprint != scenario_fingerprint(sc):
        return "expired", "场景的频率、规则或时间窗口已被修改，该方案不再保证可行"
    if row.scenario_revision != sc.revision:
        return "expired", f"场景已修订到第 {sc.revision} 版（方案基于第 {row.scenario_revision} 版）"
    return "executable", None


def record_to_out(row: PlanRow, sc: Scenario | None) -> dict:
    status, reason = plan_record_status(row, sc)
    return {
        "id": row.id,
        "scenario_id": row.scenario_id,
        "label": row.label,
        "mode": row.mode,
        "band_low_mhz": row.band_low_mhz,
        "band_high_mhz": row.band_high_mhz,
        "scenario_revision": row.scenario_revision,
        "fingerprint": row.fingerprint,
        "time_scope": row.time_scope or {},
        "result": row.result,
        "post_check_counts": row.post_check_counts or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "status": status,
        "expire_reason": reason,
    }
