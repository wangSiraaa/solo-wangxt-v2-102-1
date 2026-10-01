"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .services.timewindows import (MAX_OFFSET_MIN, MIN_OFFSET_MIN, make_window)

POLARIZATIONS = ("H", "V", "LHCP", "RHCP")
REUSE_VALUES = ("forbidden", "allowed", "unknown")


class WindowIn(BaseModel):
    """每日重复的激活区间（本地墙钟 "HH:MM" + 显式 UTC 偏移，单位分钟）。

    end <= start 表示跨午夜（如 22:00–02:00）。时长须为 1..1439 分钟。
    语义校验（格式/跨午夜/零时长）在 validate_carriers 中统一做，
    返回与其它录入错误一致的 400。
    """
    start: str = Field(description='当地开始时刻 "HH:MM"')
    end: str = Field(description='当地结束时刻 "HH:MM"；end<=start 为跨午夜')
    tz_offset_minutes: int = Field(default=480, ge=MIN_OFFSET_MIN, le=MAX_OFFSET_MIN,
                                   description="相对 UTC 的偏移分钟，如 UTC+8=480")

    def to_domain(self):
        return make_window(self.start, self.end, self.tz_offset_minutes)


class CarrierIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    center_mhz: float = Field(gt=0)
    bandwidth_mhz: float = Field(gt=0)
    power_dbm: float
    polarization: Literal["H", "V", "LHCP", "RHCP"]
    mask_name: str = "strict"
    # 激活区间；空/缺省 = 始终激活（旧数据升级后的默认语义）
    windows: list[WindowIn] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def defuzz_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("载波名不能为空")
        return v


class RulesIn(BaseModel):
    guard_required_mhz: float = Field(default=1.0, ge=0)
    leakage_limit_dbm: float = -45.0
    # key 形如 "H|V"（极化名按字母排序后拼接），value forbidden/allowed/unknown
    reuse_policy: dict[str, Literal["forbidden", "allowed", "unknown"]] = Field(default_factory=dict)


class AtTimeIn(BaseModel):
    """考察时刻口径：给定当地时刻 + UTC 偏移（None 表示全天配对）。"""
    local_time: str = Field(description='当地时刻 "HH:MM"')
    tz_offset_minutes: int = Field(default=480, ge=MIN_OFFSET_MIN, le=MAX_OFFSET_MIN)


class AnalyzeRequest(BaseModel):
    carriers: list[CarrierIn] = Field(min_length=1)
    rules: RulesIn = RulesIn()
    # 绘图网格步长 (MHz)
    plot_grid_mhz: float = Field(default=0.05, gt=0, le=1.0)
    # 缺省=全天配对；给定时刻时只统计该时刻实际激活的载波
    at_time: Optional[AtTimeIn] = None


class PlanRequest(BaseModel):
    carriers: list[CarrierIn] = Field(min_length=1)
    rules: RulesIn = RulesIn()
    band_low_mhz: float = 80.0
    band_high_mhz: float = 220.0
    mode: Literal["guard_only", "mask_aware"] = "guard_only"
    at_time: Optional[AtTimeIn] = None

    def validate_band(self) -> None:
        if self.band_high_mhz <= self.band_low_mhz:
            raise ValueError("band_high_mhz 必须大于 band_low_mhz")


class WindowOut(BaseModel):
    model_config = {"extra": "ignore"}
    start: str
    end: str
    duration_min: int
    tz_offset_minutes: int
    tz_label: str
    cross_midnight: bool
    utc_segments: list[dict]


class CarrierOut(BaseModel):
    id: Optional[int] = None
    name: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    polarization: str
    mask_name: str
    # [] = 始终激活（旧数据升级后默认如此，历史分析不变）
    windows: list[WindowOut] = Field(default_factory=list)


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
    revision: int = 1
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
    revision: int = 1
    carriers: list[CarrierOut]


class MaskOut(BaseModel):
    name: str
    points: list[list[float]]
    span_mhz: float
    description: str


class SavePlanRequest(BaseModel):
    """把一次规划结果绑定到场景修订与时间窗口后持久化。"""
    label: str = Field(default="", max_length=128)
    mode: Literal["guard_only", "mask_aware"]
    band_low_mhz: float
    band_high_mhz: float
    result: dict = Field(description="POST /api/plan 的完整返回（feasible 必须为 true）")


class PlanRecordOut(BaseModel):
    id: int
    scenario_id: int
    label: str
    mode: str
    band_low_mhz: float
    band_high_mhz: float
    scenario_revision: int
    fingerprint: str
    time_scope: dict
    result: dict
    post_check_counts: dict
    created_at: Optional[str] = None
    status: Literal["executable", "expired"]
    expire_reason: Optional[str] = None
