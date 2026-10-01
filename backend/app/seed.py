"""建表并写入示例掩模与教学演示场景（幂等：重复执行不产生重复数据）。"""
from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .config import DATABASE_URL
from .db import Base, CarrierRow, MaskRow, Scenario
from .migrations import run_migrations
from .services.masks import MASKS

DEMO_POLICY = {
    "H|V": "unknown",     # 水平/垂直：隔离度未知 -> 同频复用待评估
    "RHCP|V": "allowed",  # 右旋圆极化/垂直：已知隔离足够 -> 允许同频复用
    "H|RHCP": "forbidden",
}

# 所有载波带宽相同 (4 MHz)、功率不同；分别制造保护带不足、尾部越界与重叠。
# 该场景是“无时间数据”的历史场景：windows 缺省 -> 始终激活，历史分析结论不变。
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

# 时间感知教学场景：同频载波按班次错开发射；全部 UTC+8 本地时间录入。
# T1 白班(08:00–18:00) / T2 夜班(18:00–次日08:00，跨午夜)：同频 120 MHz 但不重叠 -> 无冲突。
# T3 22:00–02:00 与 T2 在 22:00–02:00 同时激活且同频同极化 -> 重叠冲突。
# T4(12:00–14:00, 134.5 MHz) 与白班 T1(130 MHz) 同时激活：保护带不足 + loose 单向尾部越界。
TIME_DEMO_CARRIERS = [
    dict(name="T1", position=0, center_mhz=120.0, bandwidth_mhz=4.0,
         power_dbm=30.0, polarization="H", mask_name="strict",
         windows=[{"start": "08:00", "end": "18:00", "tz_offset_minutes": 480}]),
    dict(name="T2", position=1, center_mhz=120.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict",
         windows=[{"start": "18:00", "end": "08:00", "tz_offset_minutes": 480}]),
    dict(name="T3", position=2, center_mhz=120.0, bandwidth_mhz=4.0,
         power_dbm=20.0, polarization="H", mask_name="strict",
         windows=[{"start": "22:00", "end": "02:00", "tz_offset_minutes": 480}]),
    dict(name="T4", position=3, center_mhz=134.5, bandwidth_mhz=4.0,
         power_dbm=25.0, polarization="H", mask_name="loose",
         windows=[{"start": "12:00", "end": "14:00", "tz_offset_minutes": 480}]),
]


def seed(engine) -> None:
    run_migrations(engine)
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
                            "无时间区间数据，按“始终激活”处理。",
                band_low_mhz=80.0, band_high_mhz=220.0,
                guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                reuse_policy=DEMO_POLICY,
            )
            sc.carriers = [CarrierRow(windows=[], **kw) for kw in DEMO_CARRIERS]
            s.add(sc)

        existing_t = s.scalar(select(Scenario).where(Scenario.name == "时段干扰演示场景"))
        if existing_t is None:
            sct = Scenario(
                name="时段干扰演示场景",
                description="同频载波按班次错开发射（UTC+8，含跨午夜区间）：时间不重叠"
                            "不报几何/泄漏冲突；时段重合时沿用极化、保护带与双向掩模口径。",
                band_low_mhz=80.0, band_high_mhz=220.0,
                guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                reuse_policy=DEMO_POLICY,
            )
            sct.carriers = [CarrierRow(**kw) for kw in TIME_DEMO_CARRIERS]
            s.add(sct)
        s.commit()


if __name__ == "__main__":
    eng = create_engine(DATABASE_URL)
    seed(eng)
    print("seed done")
