"""SQLAlchemy 模型：场景、载波、示例频谱掩模、已保存规划。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    band_low_mhz: Mapped[float] = mapped_column(Float, default=80.0)
    band_high_mhz: Mapped[float] = mapped_column(Float, default=220.0)
    guard_required_mhz: Mapped[float] = mapped_column(Float, default=1.0)
    leakage_limit_dbm: Mapped[float] = mapped_column(Float, default=-45.0)
    # 极化复用规则，如 {"H|V": "unknown", "RHCP|V": "allowed"}
    reuse_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    # 场景修订号：内容每次实际变化 +1；保存规划时绑定，用于过期判定
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # 场景内容指纹（与 revision 双保险：即使修订号被重放也能识别内容变化）
    fingerprint: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    carriers: Mapped[list["CarrierRow"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan",
        order_by="CarrierRow.position")
    plans: Mapped[list["PlanRow"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan",
        order_by="PlanRow.id.desc()")


class CarrierRow(Base):
    __tablename__ = "carriers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    center_mhz: Mapped[float] = mapped_column(Float, nullable=False)
    bandwidth_mhz: Mapped[float] = mapped_column(Float, nullable=False)
    power_dbm: Mapped[float] = mapped_column(Float, nullable=False)
    polarization: Mapped[str] = mapped_column(String(8), nullable=False)
    mask_name: Mapped[str] = mapped_column(String(32), nullable=False)
    # 每日激活区间 [{"start": "08:00", "end": "12:00", "tz_offset_minutes": 480}, ...]
    # 旧数据该列为 NULL（或空列表）= 始终激活（always-on）
    schedule: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    scenario: Mapped[Scenario] = relationship(back_populates="carriers")


class MaskRow(Base):
    """示例频谱发射掩模：名称 + 折线点 + 说明（教学示例）。"""
    __tablename__ = "masks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # 一侧（非负偏移）折线点 [[offset_mhz, attenuation_db], ...]
    points: Mapped[list] = mapped_column(JSON, nullable=False)
    span_mhz: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")


class PlanRow(Base):
    """已保存的 OR-Tools 规划：绑定场景修订、内容指纹与时间窗口。

    规划是不可变历史记录；场景后来被编辑时不删除它，读取时实时判定是否过期
    （revision/fingerprint 不一致 -> stale=True，禁止直接复用，只能重新规划）。
    """
    __tablename__ = "saved_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    scenario_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    band_low_mhz: Mapped[float] = mapped_column(Float)
    band_high_mhz: Mapped[float] = mapped_column(Float)
    # 时间口径：instant=某时刻（at_utc 存 UTC ISO），anytime=全天口径
    scope: Mapped[str] = mapped_column(String(8), default="anytime")
    at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    at_local_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="")
    objective_khz: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    post_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    scenario: Mapped[Scenario] = relationship(back_populates="plans")
