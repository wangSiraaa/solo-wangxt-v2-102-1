"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .services.time_model import (MAX_TZ_OFFSET_MIN, MIN_TZ_OFFSET_MIN,
                                  parse_hhmm, validate_schedule, Window)

POLARIZATIONS = ("H", "V", "LHCP", "RHCP")
REUSE_VALUES = ("forbidden", "allowed", "unknown")


class WindowIn(BaseModel):
    """一段激活区间：本地挂钟 'HH:MM'（终点允许 '24:00'）+ 显式 UTC 偏移。

    end 早于 start（或 end='00:00'）表示跨午夜；end == start 非法。
    """
    start: str = Field(description="本地开始时间 HH:MM，半开区间含端点")
    end: str = Field(description="本地结束时间 HH:MM（可 24:00），半开区间不含端点；早于 start 为跨午夜")
    tz_offset_minutes: int = Field(default=0, ge=MIN_TZ_OFFSET_MIN, le=MAX_TZ_OFFSET_MIN,
                                   description="UTC 偏移（分钟），如 UTC+8 = 480")

    @field_validator("start", "end")
    @classmethod
    def _check_clock(cls, v: str, info):
        parse_hhmm(v)  # 抛 ValueError 即 422
        return v

    def to_window(self) -> Window:
        return Window(parse_hhmm(self.start), parse_hhmm(self.end), self.tz_offset_minutes)

    @model_validator(mode="after")
    def _check_window(self):
        # 触发统一的区间规则（零时长、跨午夜合法性由 Window 解释）
        self.to_window()
        return self


class CarrierIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    center_mhz: float = Field(gt=0)
    bandwidth_mhz: float = Field(gt=0)
    power_dbm: float
    polarization: Literal["H", "V", "LHCP", "RHCP"]
    mask_name: str = "strict"
    # 每日激活区间；空列表 = 始终激活（旧无时间数据的唯一升级语义）
    schedule: list[WindowIn] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def defuzz_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("载波名不能为空")
        return v

    @model_validator(mode="after")
    def _check_schedule(self):
        validate_schedule([w.to_window() for w in self.schedule], self.name)
        return self


class RulesIn(BaseModel):
    guard_required_mhz: float = Field(default=1.0, ge=0)
    leakage_limit_dbm: float = -45.0
    # key 形如 "H|V"（极化名按字母排序后拼接），value forbidden/allowed/unknown
    reuse_policy: dict[str, Literal["forbidden", "allowed", "unknown"]] = Field(default_factory=dict)


class TimeScopeIn(BaseModel):
    """分析/规划的时间口径。

    - 给 at（带时区偏移的 ISO-8601 时刻）：只评估该时刻实际激活的载波，
      冲突按“此刻同时在发射”的载波对计算。
    - 不给 at：全天口径——任何两个在一天中存在共同激活时刻的载波对都要
      满足约束；时间完全不重叠的载波对永远放行。
    """
    at: Optional[datetime] = None

    @field_validator("at")
    @classmethod
    def _aware(cls, v: Optional[datetime]):
        if v is not None and v.tzinfo is None:
            raise ValueError("at 必须带时区偏移，例如 2026-10-01T09:00:00+08:00")
        return v


class AnalyzeRequest(TimeScopeIn):
    carriers: list[CarrierIn] = Field(min_length=1)
    rules: RulesIn = RulesIn()
    # 绘图网格步长 (MHz)
    plot_grid_mhz: float = Field(default=0.05, gt=0, le=1.0)


class PlanRequest(TimeScopeIn):
    carriers: list[CarrierIn] = Field(min_length=1)
    rules: RulesIn = RulesIn()
    band_low_mhz: float = 80.0
    band_high_mhz: float = 220.0
    mode: Literal["guard_only", "mask_aware"] = "guard_only"

    def validate_band(self) -> None:
        if self.band_high_mhz <= self.band_low_mhz:
            raise ValueError("band_high_mhz 必须大于 band_low_mhz")


class WindowOut(BaseModel):
    start: str
    end: str
    tz_offset_minutes: int


class CarrierOut(BaseModel):
    id: Optional[int] = None
    name: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    polarization: str
    mask_name: str
    schedule: list[WindowOut] = Field(default_factory=list)
    always_active: bool = True


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    band_low_mhz: float = 80.0
    band_high_mhz: float = 220.0
    guard_required_mhz: float = Field(default=1.0, ge=0)
    leakage_limit_dbm: float = -45.0
    reuse_policy: dict[str, Literal["forbidden", "allowed", "unknown"]] = Field(default_factory=dict)
    carriers: list[CarrierIn] = Field(default_factory=list)


class ScenarioSummary(BaseModel):
    id: int
    name: str
    description: str
    carrier_count: int
    revision: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ScenarioOut(BaseModel):
    id: int
    name: str
    description: str
    band_low_mhz: float
    band_high_mhz: float
    guard_required_mhz: float
    leakage_limit_dbm: float
    reuse_policy: dict[str, str]
    revision: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    carriers: list[CarrierOut]


class MaskOut(BaseModel):
    name: str
    points: list[list[float]]
    span_mhz: float
    description: str


# ---- 规划结果持久化 --------------------------------------------------------

class PlanSaveIn(BaseModel):
    """把一次 /api/plan 的结果绑定场景修订与时间窗口保存。"""
    scenario_id: int
    mode: Literal["guard_only", "mask_aware"]
    band_low_mhz: float
    band_high_mhz: float
    # 保存时刻的时间口径：None = 全天口径
    at: Optional[datetime] = None

    @field_validator("at")
    @classmethod
    def _aware(cls, v: Optional[datetime]):
        if v is not None and v.tzinfo is None:
            raise ValueError("at 必须带时区偏移")
        return v


class SavedPlanOut(BaseModel):
    id: int
    scenario_id: int
    scenario_revision: int
    scenario_fingerprint: str
    mode: str
    band_low_mhz: float
    band_high_mhz: float
    at_utc: Optional[str] = None
    at_local_label: Optional[str] = None
    scope: str  # "instant" | "anytime"
    feasible: bool
    status: str
    objective_khz: Optional[float] = None
    result: dict
    post_check: Optional[dict] = None
    created_at: Optional[str] = None
    # 以下三项在读取时按当前场景实时计算
    stale: bool
    stale_reason: Optional[str] = None
