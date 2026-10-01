"""建表并写入示例掩模与教学演示场景（幂等：重复执行不产生重复数据）。"""
from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .config import DATABASE_URL
from .db import Base, CarrierRow, MaskRow, Scenario
from .services.masks import MASKS

DEMO_POLICY = {
    "H|V": "unknown",     # 水平/垂直：隔离度未知 -> 同频复用待评估
    "RHCP|V": "allowed",  # 右旋圆极化/垂直：已知隔离足够 -> 允许同频复用
    "H|RHCP": "forbidden",
}

# 所有载波带宽相同 (4 MHz)、功率不同；分别制造保护带不足、尾部越界与重叠
DEMO_CARRIERS = [
    # 保护带不足(0.5 MHz) + 双向掩模尾部越界，且因功率不同两个方向泄漏量不同
    dict(name="C1", position=0, center_mhz=100.0, bandwidth_mhz=4.0,
         power_dbm=30.0, polarization="H", mask_name="loose"),
    dict(name="C2", position=1, center_mhz=104.5, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="loose"),
    # 干净对照载波：间隔 11 MHz
    dict(name="C3", position=2, center_mhz=115.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict"),
    # 保护带不足(0.5 MHz)：弱载波 loose 尾部侵入强载波，反向不越界
    dict(name="C4", position=3, center_mhz=130.0, bandwidth_mhz=4.0,
         power_dbm=30.0, polarization="H", mask_name="strict"),
    dict(name="C5", position=4, center_mhz=134.5, bandwidth_mhz=4.0,
         power_dbm=25.0, polarization="H", mask_name="loose"),
    # 与 C1 同频段、异极化：H|V 规则未知 -> 待评估
    dict(name="C6", position=5, center_mhz=100.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="V", mask_name="strict"),
    # 保护带足够(2 MHz) 但强载波 loose 拖尾仍越界，反向不越界
    dict(name="C7", position=6, center_mhz=150.0, bandwidth_mhz=4.0,
         power_dbm=30.0, polarization="H", mask_name="loose"),
    dict(name="C8", position=7, center_mhz=156.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict"),
    # 真实频带重叠 0.5 MHz（同极化）
    dict(name="C9", position=8, center_mhz=170.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict"),
    dict(name="C10", position=9, center_mhz=173.5, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict"),
    # 同频段异极化：RHCP|V 规则允许复用 -> 不报冲突
    dict(name="C11", position=10, center_mhz=200.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="RHCP", mask_name="strict"),
    dict(name="C12", position=11, center_mhz=200.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="V", mask_name="strict"),
]


# 时间复用演示：载波只在各自时段发射，跨午夜 + 显式 UTC 偏移。
# 静态频率图上 T1/T2 呈“保护带不足 + 双向掩模泄漏”、T3/T4 同频重叠，
# 但时间不重叠时一律不报冲突；延长到重叠后沿用全部现有口径。
TIME_CARRIERS = [
    # 净距 0.5 MHz、loose 掩模、功率不同：白班 / 夜班，08–20 与 20–08(跨午夜)
    # 首尾相接。时段错开时无冲突；把任一段时间段延长到与对方重合后，
    # 同时出现保护带不足与方向性泄漏（30 dBm->20 dBm 与 20 dBm->30 dBm 数值不对称）。
    dict(name="T1", position=0, center_mhz=100.0, bandwidth_mhz=4.0,
         power_dbm=30.0, polarization="H", mask_name="loose",
         schedule=[{"start": "08:00", "end": "20:00", "tz_offset_minutes": 480}]),
    dict(name="T2", position=1, center_mhz=104.5, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="loose",
         schedule=[{"start": "20:00", "end": "08:00", "tz_offset_minutes": 480}]),
    # 同频 160 MHz 同极化，不同 UTC 偏移：本地白天段在 UTC 轴上完全错开
    # T3 北京 08:00–16:00(+8) = UTC 00:00–08:00；T4 伦敦 08:00–16:00(+0) = UTC 08:00–16:00
    # 静态图上频带完全重叠，因时间不重叠（连相邻都不算）而无任何冲突。
    dict(name="T3", position=2, center_mhz=160.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict",
         schedule=[{"start": "08:00", "end": "16:00", "tz_offset_minutes": 480}]),
    dict(name="T4", position=3, center_mhz=160.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict",
         schedule=[{"start": "08:00", "end": "16:00", "tz_offset_minutes": 0}]),
    # 始终激活的对照载波（旧数据升级语义）：strict 掩模、放在 180 MHz，与最近的
    # T3/T4 (160 MHz) 净距 16 MHz，超出 strict 掩模跨度 7 MHz，任何时刻都不构成冲突。
    dict(name="T5", position=4, center_mhz=180.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict",
         schedule=[]),
]


def seed(engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for m in MASKS.values():
            row = s.scalar(select(MaskRow).where(MaskRow.name == m.name))
            if row is None:
                s.add(MaskRow(name=m.name, points=[list(p) for p in m.points],
                              span_mhz=m.span_mhz, description=m.description))

        existing = s.scalar(select(Scenario).where(Scenario.name == "教学演示场景"))
        if existing is None:
            sc = Scenario(
                name="教学演示场景",
                description="同带宽不同功率：保护带不足、掩模尾部越界（方向性）、"
                            "频带重叠、极化复用待评估/允许，全部冲突可定位到载波对。"
                            "全部载波始终激活（旧场景默认语义）。",
                band_low_mhz=80.0, band_high_mhz=220.0,
                guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                reuse_policy=DEMO_POLICY,
            )
            sc.carriers = [CarrierRow(**kw) for kw in DEMO_CARRIERS]
            s.add(sc)

        # 时间复用演示场景（幂等）
        from .revisions import scenario_fingerprint
        from .schemas import ScenarioIn
        existing_t = s.scalar(select(Scenario).where(Scenario.name == "时间复用演示"))
        if existing_t is None:
            req = ScenarioIn(
                name="时间复用演示",
                description="静态频率图上同频重叠，但激活时段互不重合（跨午夜首尾相接、"
                            "不同 UTC 偏移错开）：时间不重叠不报冲突；把任一段延长到"
                            "重叠后应立即出现保护带/方向性泄漏结论。T5 始终激活（旧语义）。",
                band_low_mhz=80.0, band_high_mhz=220.0,
                guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                reuse_policy=DEMO_POLICY,
                carriers=[kw for kw in TIME_CARRIERS],
            )
            tsc = Scenario(
                name=req.name, description=req.description,
                band_low_mhz=req.band_low_mhz, band_high_mhz=req.band_high_mhz,
                guard_required_mhz=req.guard_required_mhz,
                leakage_limit_dbm=req.leakage_limit_dbm,
                reuse_policy=DEMO_POLICY,
                revision=1, fingerprint=scenario_fingerprint(req),
            )
            tsc.carriers = [CarrierRow(**kw) for kw in TIME_CARRIERS]
            s.add(tsc)
        s.commit()


if __name__ == "__main__":
    eng = create_engine(DATABASE_URL)
    seed(eng)
    print("seed done")
