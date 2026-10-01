"""场景内容指纹与修订号。

- revision：面向用户的修订号，内容每次实际变化 +1（相同内容重复保存不递增）。
- fingerprint：规范化 JSON 后 SHA-256，保存规划时绑定；读取已存规划时与当前
  场景指纹对比，任意时间/频率/规则改动都会让旧规划标为过期（stale），
  即使修订号被外部重放也不会误判。
"""
from __future__ import annotations

import hashlib
import json

from .schemas import ScenarioIn


def scenario_fingerprint(req: ScenarioIn) -> str:
    payload = {
        "name": req.name.strip(),
        "description": req.description,
        "band_low_mhz": req.band_low_mhz,
        "band_high_mhz": req.band_high_mhz,
        "guard_required_mhz": req.guard_required_mhz,
        "leakage_limit_dbm": req.leakage_limit_dbm,
        "reuse_policy": dict(sorted(req.reuse_policy.items())),
        "carriers": [
            {
                "position": i,
                "name": c.name.strip(),
                "center_mhz": c.center_mhz,
                "bandwidth_mhz": c.bandwidth_mhz,
                "power_dbm": c.power_dbm,
                "polarization": c.polarization,
                "mask_name": c.mask_name,
                "schedule": [
                    {"start": w.start, "end": w.end,
                     "tz_offset_minutes": w.tz_offset_minutes}
                    for w in sorted(c.schedule,
                                    key=lambda w: (w.tz_offset_minutes, w.start))
                ],
            }
            for i, c in enumerate(req.carriers)
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
