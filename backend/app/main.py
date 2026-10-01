"""FastAPI 频谱工作台（离线简化模型）。

不连接无线电设备、不生成发射指令；只对录入的载波数据做计算与可视化。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .assemble import (bands_view, build_spectrum, resolve_at_utc, to_domain,
                       to_rules, validate_carriers)
from .config import CORS_ORIGINS, DATABASE_URL
from .db import Base, CarrierRow, MaskRow, PlanRow, Scenario
from .migrations import run_migrations
from .persistence import record_to_out, scenario_fingerprint
from .schemas import (AnalyzeRequest, MaskOut, PlanRequest, SavePlanRequest,
                      ScenarioIn, ScenarioOut, ScenarioSummary)
from .seed import seed
from .services.analysis import Carrier, analyze
from .services.planner import BandLimits, plan
from .services.timewindows import describe_window, make_window

app = FastAPI(
    title="频谱工作台 API（离线教学模型）",
    version="1.1.0",
    description="载波频带冲突检查、掩模尾部泄漏、线性域功率汇总与 OR-Tools 频率规划；"
                "全部检查按激活时间区间在同一时刻实际发射的载波进行。"
                "不连接无线电设备，不生成发射指令。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


@app.on_event("startup")
def _startup() -> None:
    run_migrations(engine)
    seed(engine)


def _window_dicts(c) -> list[dict]:
    return [{"start": w.start, "end": w.end,
             "tz_offset_minutes": w.tz_offset_minutes} for w in c.windows]


# ---- 计算接口（无状态，数据由前端提交） -----------------------------------

def _time_scope_dict(at_utc: int | None) -> dict:
    if at_utc is None:
        return {"mode": "all_day"}
    from .services.timewindows import fmt_hhmm
    return {"mode": "instant", "at_utc_minute": at_utc, "at_utc_label": fmt_hhmm(at_utc)}


@app.post("/api/analyze")
def analyze_endpoint(req: AnalyzeRequest) -> dict:
    try:
        validate_carriers(req.carriers)
        carriers = [to_domain(c) for c in req.carriers]
        at_utc = resolve_at_utc(req.at_time)
        result = analyze(carriers, to_rules(req.rules), at_utc_minute=at_utc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    active_names = set(result["power_summary"]["active_names"])
    result["bands"] = bands_view(carriers, active_names=active_names)
    result["spectrum"] = build_spectrum(carriers, req.plot_grid_mhz,
                                        active_names=active_names)
    result["time_scope"] = _time_scope_dict(at_utc)
    return result


@app.post("/api/plan")
def plan_endpoint(req: PlanRequest) -> dict:
    try:
        req.validate_band()
        validate_carriers(req.carriers)
        carriers = [to_domain(c) for c in req.carriers]
        at_utc = resolve_at_utc(req.at_time)
        result = plan(carriers, to_rules(req.rules),
                      BandLimits(req.band_low_mhz, req.band_high_mhz),
                      mode=req.mode, at_utc_minute=at_utc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result["feasible"]:
        by_name = {c.name: c for c in carriers}
        planned = [
            Carrier(id=None, name=a["name"], center_mhz=a["center_mhz"],
                    bandwidth_mhz=a["bandwidth_mhz"], power_dbm=a["power_dbm"],
                    polarization=a["polarization"], mask_name=a["mask_name"],
                    # post-check 必须沿用同一时间口径：时间不重叠对允许同频
                    windows=by_name[a["name"]].windows)
            for a in result["assignments"]
        ]
        result["post_check"] = analyze(planned, to_rules(req.rules))
        # 规划后的频段视图与发射谱（用于前端叠加对照）
        result["bands"] = bands_view(planned)
        result["spectrum"] = build_spectrum(planned, 0.05)
        result["inactive_carriers"] = result.get("inactive_carriers", [])
    result["time_scope"] = _time_scope_dict(at_utc)
    return result


# ---- 掩模 ------------------------------------------------------------------

@app.get("/api/masks", response_model=list[MaskOut])
def list_masks() -> list[MaskRow]:
    with Session(engine) as s:
        return list(s.scalars(select(MaskRow).order_by(MaskRow.name)))


# ---- 场景持久化 ------------------------------------------------------------

def _carrier_out(c: CarrierRow):
    from .schemas import CarrierOut, WindowOut
    return CarrierOut(
        id=c.id, name=c.name, center_mhz=c.center_mhz,
        bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
        polarization=c.polarization, mask_name=c.mask_name,
        windows=[WindowOut(**describe_window(make_window(
            w["start"], w["end"], w["tz_offset_minutes"])))
            for w in (c.windows or [])])


def _row_to_out(sc: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=sc.id, name=sc.name, description=sc.description,
        band_low_mhz=sc.band_low_mhz, band_high_mhz=sc.band_high_mhz,
        guard_required_mhz=sc.guard_required_mhz,
        leakage_limit_dbm=sc.leakage_limit_dbm,
        reuse_policy=sc.reuse_policy or {},
        revision=sc.revision,
        carriers=[_carrier_out(c) for c in sorted(sc.carriers, key=lambda c: c.position)],
    )


def _carrier_rows(req: ScenarioIn) -> list[CarrierRow]:
    return [
        CarrierRow(position=i, name=c.name, center_mhz=c.center_mhz,
                   bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
                   polarization=c.polarization, mask_name=c.mask_name,
                   windows=_window_dicts(c))
        for i, c in enumerate(req.carriers)
    ]


@app.get("/api/scenarios", response_model=list[ScenarioSummary])
def list_scenarios() -> list[ScenarioSummary]:
    with Session(engine) as s:
        rows = list(s.scalars(select(Scenario).order_by(Scenario.id)))
        return [ScenarioSummary(id=r.id, name=r.name, description=r.description,
                                carrier_count=len(r.carriers), revision=r.revision,
                                created_at=r.created_at.isoformat() if r.created_at else None,
                                updated_at=r.updated_at.isoformat() if r.updated_at else None)
                for r in rows]


@app.get("/api/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: int) -> ScenarioOut:
    with Session(engine) as s:
        sc = s.get(Scenario, scenario_id)
        if sc is None:
            raise HTTPException(404, "场景不存在")
        return _row_to_out(sc)


@app.post("/api/scenarios", response_model=ScenarioOut)
def create_scenario(req: ScenarioIn) -> ScenarioOut:
    try:
        validate_carriers(req.carriers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with Session(engine) as s:
        if s.scalar(select(Scenario).where(Scenario.name == req.name)) is not None:
            raise HTTPException(409, f"场景名 {req.name!r} 已存在")
        sc = Scenario(
            name=req.name.strip(), description=req.description,
            band_low_mhz=req.band_low_mhz, band_high_mhz=req.band_high_mhz,
            guard_required_mhz=req.guard_required_mhz,
            leakage_limit_dbm=req.leakage_limit_dbm,
            reuse_policy=dict(req.reuse_policy), revision=1,
            carriers=_carrier_rows(req),
        )
        s.add(sc)
        s.commit()
        s.refresh(sc)
        return _row_to_out(sc)


@app.delete("/api/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int) -> dict:
    with Session(engine) as s:
        sc = s.get(Scenario, scenario_id)
        if sc is None:
            raise HTTPException(404, "场景不存在")
        s.delete(sc)
        s.commit()
        return {"deleted": scenario_id}


@app.put("/api/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: int, req: ScenarioIn) -> ScenarioOut:
    try:
        validate_carriers(req.carriers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with Session(engine) as s:
        sc = s.get(Scenario, scenario_id)
        if sc is None:
            raise HTTPException(404, "场景不存在")
        other = s.scalar(select(Scenario).where(
            Scenario.name == req.name.strip(), Scenario.id != scenario_id))
        if other is not None:
            raise HTTPException(409, f"场景名 {req.name!r} 已存在")

        new_rows = _carrier_rows(req)
        new_fingerprint = scenario_fingerprint({
            "band_low_mhz": req.band_low_mhz, "band_high_mhz": req.band_high_mhz,
            "guard_required_mhz": req.guard_required_mhz,
            "leakage_limit_dbm": req.leakage_limit_dbm,
            "reuse_policy": dict(req.reuse_policy),
            "carriers": req.carriers})
        content_changed = new_fingerprint != scenario_fingerprint(sc)
        sc.name = req.name.strip()
        sc.description = req.description
        sc.band_low_mhz = req.band_low_mhz
        sc.band_high_mhz = req.band_high_mhz
        sc.guard_required_mhz = req.guard_required_mhz
        sc.leakage_limit_dbm = req.leakage_limit_dbm
        sc.reuse_policy = dict(req.reuse_policy)
        sc.carriers = new_rows
        if content_changed:
            # 修订号只在实质内容变化时前进；改名/描述不使历史规划过期
            sc.revision = (sc.revision or 1) + 1
        s.commit()
        s.refresh(sc)
        return _row_to_out(sc)


# ---- 已保存规划（绑定场景修订 + 时间窗口） --------------------------------

def _plan_time_scope(result: dict) -> dict:
    scope = dict(result.get("time_scope") or {})
    scope["inactive_carriers"] = result.get("inactive_carriers", [])
    scope["assignment_windows"] = {
        a["name"]: a.get("windows", []) for a in result.get("assignments", [])}
    return scope


@app.get("/api/scenarios/{scenario_id}/plans")
def list_plans(scenario_id: int) -> list[dict]:
    with Session(engine) as s:
        sc = s.get(Scenario, scenario_id)
        if sc is None:
            raise HTTPException(404, "场景不存在")
        return [record_to_out(r, sc) for r in sc.plans]


@app.post("/api/scenarios/{scenario_id}/plans")
def save_plan(scenario_id: int, req: SavePlanRequest) -> dict:
    with Session(engine) as s:
        sc = s.get(Scenario, scenario_id)
        if sc is None:
            raise HTTPException(404, "场景不存在")
        result = req.result
        if not result.get("feasible"):
            raise HTTPException(400, "只允许保存可行（feasible=true）的规划结果")
        counts = (result.get("post_check") or {}).get("counts")
        row = PlanRow(
            scenario_id=sc.id, label=req.label.strip(),
            mode=req.mode, band_low_mhz=req.band_low_mhz,
            band_high_mhz=req.band_high_mhz,
            scenario_revision=sc.revision,
            fingerprint=scenario_fingerprint(sc),
            time_scope=_plan_time_scope(result),
            result=result,
            post_check_counts=dict(counts or {}),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return record_to_out(row, sc)


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int) -> dict:
    with Session(engine) as s:
        row = s.get(PlanRow, plan_id)
        if row is None:
            raise HTTPException(404, "规划不存在")
        return record_to_out(row, row.scenario)


@app.post("/api/plans/{plan_id}/reuse")
def reuse_plan(plan_id: int) -> dict:
    """复用已保存规划：过期方案一律拒绝（不返回可执行结果）。"""
    with Session(engine) as s:
        row = s.get(PlanRow, plan_id)
        if row is None:
            raise HTTPException(404, "规划不存在")
        out = record_to_out(row, row.scenario)
        if out["status"] != "executable":
            raise HTTPException(
                409, {"message": "该规划已过期，不能直接复用；请基于当前场景重新规划。",
                      "record": out})
        return out


@app.delete("/api/plans/{plan_id}")
def delete_plan(plan_id: int) -> dict:
    with Session(engine) as s:
        row = s.get(PlanRow, plan_id)
        if row is None:
            raise HTTPException(404, "规划不存在")
        s.delete(row)
        s.commit()
        return {"deleted": plan_id}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": "offline simplified — no radio, no transmit commands"}
