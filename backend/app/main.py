"""FastAPI 频谱工作台（离线简化模型）。

不连接无线电设备、不生成发射指令；只对录入的载波数据做计算与可视化。
"""
from __future__ import annotations

from datetime import timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from .assemble import (bands_view, build_spectrum, to_domain, to_rules,
                       validate_masks)
from .config import CORS_ORIGINS, DATABASE_URL
from .db import Base, CarrierRow, MaskRow, PlanRow, Scenario
from .revisions import scenario_fingerprint
from .schemas import (AnalyzeRequest, MaskOut, PlanRequest, PlanSaveIn,
                      SavedPlanOut, ScenarioIn, ScenarioOut, ScenarioSummary)
from .seed import seed
from .services.analysis import Carrier, analyze
from .services.planner import BandLimits, plan
from .services.time_model import ensure_aware_utc

app = FastAPI(
    title="频谱工作台 API（离线教学模型）",
    version="1.1.0",
    description="载波频带冲突检查、掩模尾部泄漏、线性域功率汇总与 OR-Tools 频率规划；"
                "载波带每日激活区间（显式 UTC 偏移，可跨午夜），分析/图表/规划均按"
                "同一时刻实际激活的载波工作。不连接无线电设备，不生成发射指令。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def _light_migrate(eng) -> None:
    """对已存在的旧库做幂等加列（旧场景升级：默认始终激活，历史数据不变）。

    生产环境可换 Alembic；教学环境里 create_all + 补列即可，且对 PostgreSQL /
    SQLite 都安全。重启后重复执行无副作用。
    """
    inspector = inspect(eng)
    tables = set(inspector.get_table_names())

    if "scenarios" in tables:
        cols = {c["name"] for c in inspector.get_columns("scenarios")}
        with eng.begin() as conn:
            if "revision" not in cols:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN revision INTEGER DEFAULT 1"))
                # 旧场景统一为修订 1，已存规划（若有）即绑定修订 1
                conn.execute(text("UPDATE scenarios SET revision = 1"))
            if "fingerprint" not in cols:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN fingerprint VARCHAR(64) DEFAULT ''"))
            if "updated_at" not in cols:
                conn.execute(text(
                    "ALTER TABLE scenarios ADD COLUMN updated_at TIMESTAMP"))
                conn.execute(text("UPDATE scenarios SET updated_at = created_at"))

    if "carriers" in tables:
        cols = {c["name"] for c in inspector.get_columns("carriers")}
        if "schedule" not in cols:
            with eng.begin() as conn:
                # NULL / 空列表都表示始终激活（旧无时间数据的唯一升级语义）
                conn.execute(text("ALTER TABLE carriers ADD COLUMN schedule JSON"))


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(engine)
    _light_migrate(engine)
    seed(engine)
    _backfill_fingerprints(engine)


def _backfill_fingerprints(eng) -> None:
    """为旧场景（fingerprint 为空）按当前库内内容回填指纹；修订号与数据不变。

    这样旧场景升级后默认“始终激活”，而后来绑定修订 1 的已存规划不会因为
    指纹列从空变为有值而被误判过期——指纹始终反映的是同一份内容。
    """
    from .schemas import CarrierIn, ScenarioIn, WindowIn
    with Session(eng) as s:
        rows = list(s.scalars(select(Scenario).where(
            (Scenario.fingerprint == "") | (Scenario.fingerprint.is_(None)))))
        for sc in rows:
            req = ScenarioIn(
                name=sc.name, description=sc.description or "",
                band_low_mhz=sc.band_low_mhz, band_high_mhz=sc.band_high_mhz,
                guard_required_mhz=sc.guard_required_mhz,
                leakage_limit_dbm=sc.leakage_limit_dbm,
                reuse_policy=sc.reuse_policy or {},
                carriers=[
                    CarrierIn(name=c.name, center_mhz=c.center_mhz,
                              bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
                              polarization=c.polarization, mask_name=c.mask_name,
                              schedule=[WindowIn(**w) for w in (c.schedule or [])])
                    for c in sorted(sc.carriers, key=lambda c: c.position)
                ])
            sc.fingerprint = scenario_fingerprint(req)
        if rows:
            s.commit()


# ---- 计算接口（无状态，数据由前端提交） -----------------------------------

def _request_at(req) -> "object | None":
    if req.at is None:
        return None
    return ensure_aware_utc(req.at)


@app.post("/api/analyze")
def analyze_endpoint(req: AnalyzeRequest) -> dict:
    try:
        validate_masks(req.carriers)
        carriers = [to_domain(c) for c in req.carriers]
        at = _request_at(req)
        result = analyze(carriers, to_rules(req.rules), at=at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # instant 口径：频段图保留全部载波（未激活灰显），发射谱/聚合只画激活载波
    plot_carriers = [c for c in carriers if at is None or c.is_active_at(at)]
    result["bands"] = bands_view(carriers, at)
    result["spectrum"] = build_spectrum(plot_carriers, req.plot_grid_mhz)
    return result


@app.post("/api/plan")
def plan_endpoint(req: PlanRequest) -> dict:
    try:
        req.validate_band()
        validate_masks(req.carriers)
        carriers = [to_domain(c) for c in req.carriers]
        at = _request_at(req)
        result = plan(carriers, to_rules(req.rules),
                      BandLimits(req.band_low_mhz, req.band_high_mhz),
                      mode=req.mode, at=at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result["feasible"]:
        windows_by_name = {c.name: c.windows for c in carriers}
        planned = [
            Carrier(id=None, name=a["name"], center_mhz=a["center_mhz"],
                    bandwidth_mhz=a["bandwidth_mhz"], power_dbm=a["power_dbm"],
                    polarization=a["polarization"], mask_name=a["mask_name"],
                    windows=windows_by_name[a["name"]])
            for a in result["assignments"]
        ]
        # post-check 与规划同口径（instant/全天），保证结论针对同一时间窗口
        post = analyze(planned, to_rules(req.rules), at=at)
        result["post_check"] = post
        # 规划后的频段视图与发射谱（用于前端叠加对照）
        plot_carriers = [c for c in planned if at is None or c.is_active_at(at)]
        result["bands"] = bands_view(planned, at)
        result["spectrum"] = build_spectrum(plot_carriers, 0.05)
    return result


# ---- 掩模 ------------------------------------------------------------------

@app.get("/api/masks", response_model=list[MaskOut])
def list_masks() -> list[MaskRow]:
    with Session(engine) as s:
        return list(s.scalars(select(MaskRow).order_by(MaskRow.name)))


# ---- 场景持久化 ------------------------------------------------------------

def _carrier_out(c: CarrierRow):
    from .schemas import CarrierOut, WindowOut
    raw = c.schedule or []
    windows = [WindowOut(start=w["start"], end=w["end"],
                         tz_offset_minutes=w.get("tz_offset_minutes", 0))
               for w in raw]
    return CarrierOut(id=c.id, name=c.name, center_mhz=c.center_mhz,
                      bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
                      polarization=c.polarization, mask_name=c.mask_name,
                      schedule=windows, always_active=(len(windows) == 0))


def _row_to_out(sc: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=sc.id, name=sc.name, description=sc.description,
        band_low_mhz=sc.band_low_mhz, band_high_mhz=sc.band_high_mhz,
        guard_required_mhz=sc.guard_required_mhz,
        leakage_limit_dbm=sc.leakage_limit_dbm,
        reuse_policy=sc.reuse_policy or {},
        revision=sc.revision or 1,
        created_at=sc.created_at.isoformat() if sc.created_at else None,
        updated_at=sc.updated_at.isoformat() if sc.updated_at else None,
        carriers=[_carrier_out(c) for c in sorted(sc.carriers, key=lambda c: c.position)],
    )


def _schedule_payload(c) -> list[dict]:
    return [{"start": w.start, "end": w.end, "tz_offset_minutes": w.tz_offset_minutes}
            for w in c.schedule]


@app.get("/api/scenarios", response_model=list[ScenarioSummary])
def list_scenarios() -> list[ScenarioSummary]:
    with Session(engine) as s:
        rows = list(s.scalars(select(Scenario).order_by(Scenario.id)))
        return [ScenarioSummary(id=r.id, name=r.name, description=r.description,
                                carrier_count=len(r.carriers),
                                revision=r.revision or 1,
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


def _fingerprint_from_row(sc: Scenario) -> str:
    """按数据库当前行内容计算指纹（用于旧场景指纹列为空时的“内容是否真的变了”判定）。"""
    req = ScenarioIn(
        name=sc.name, description=sc.description or "",
        band_low_mhz=sc.band_low_mhz, band_high_mhz=sc.band_high_mhz,
        guard_required_mhz=sc.guard_required_mhz,
        leakage_limit_dbm=sc.leakage_limit_dbm,
        reuse_policy=sc.reuse_policy or {},
        carriers=[_carrier_in_row(c) for c in sorted(sc.carriers, key=lambda c: c.position)],
    )
    return scenario_fingerprint(req)


def _apply_scenario(sc: Scenario, req: ScenarioIn,
                    bump_revision: bool) -> bool:
    """把请求内容写到行对象。

    bump_revision=True 且内容指纹真的变化时修订号 +1；
    bump_revision=False（新建）时保持调用方给定的修订号。
    旧场景（fingerprint 为空）第一次以相同内容保存时只回填指纹，
    不递增修订号——旧数据升级默认“始终激活”，历史结果不变；
    若旧场景第一次保存就改了内容（含改时间），仍按一次真实编辑递增。
    """
    fp = scenario_fingerprint(req)
    old_fp = sc.fingerprint or ""
    if old_fp:
        changed = old_fp != fp
    elif sc.id is None:
        changed = False  # 新建
    else:
        # 旧行：用当前库内内容现场算指纹做对比
        changed = _fingerprint_from_row(sc) != fp
    sc.name = req.name.strip()
    sc.description = req.description
    sc.band_low_mhz = req.band_low_mhz
    sc.band_high_mhz = req.band_high_mhz
    sc.guard_required_mhz = req.guard_required_mhz
    sc.leakage_limit_dbm = req.leakage_limit_dbm
    sc.reuse_policy = dict(req.reuse_policy)
    sc.carriers = [
        CarrierRow(position=i, name=c.name, center_mhz=c.center_mhz,
                   bandwidth_mhz=c.bandwidth_mhz, power_dbm=c.power_dbm,
                   polarization=c.polarization, mask_name=c.mask_name,
                   schedule=_schedule_payload(c))
        for i, c in enumerate(req.carriers)
    ]
    sc.fingerprint = fp
    if changed and bump_revision:
        sc.revision = (sc.revision or 1) + 1
        from datetime import datetime
        sc.updated_at = datetime.now(timezone.utc)
    return changed


@app.post("/api/scenarios", response_model=ScenarioOut)
def create_scenario(req: ScenarioIn) -> ScenarioOut:
    try:
        validate_masks(req.carriers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with Session(engine) as s:
        if s.scalar(select(Scenario).where(Scenario.name == req.name)) is not None:
            raise HTTPException(409, f"场景名 {req.name!r} 已存在")
        sc = Scenario(revision=1, fingerprint=scenario_fingerprint(req))
        _apply_scenario(sc, req, bump_revision=False)
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
        validate_masks(req.carriers)
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
        _apply_scenario(sc, req, bump_revision=True)
        s.commit()
        s.refresh(sc)
        return _row_to_out(sc)


# ---- 已保存规划（绑定场景修订 + 时间窗口，过期拒绝复用） -------------------

def _plan_to_out(row: PlanRow, sc: Scenario | None) -> SavedPlanOut:
    stale, reason = False, None
    if sc is None:
        stale, reason = True, "所属场景已被删除"
    else:
        if (sc.revision or 1) != row.scenario_revision:
            stale, reason = True, (
                f"场景已修订（保存时修订 {row.scenario_revision}，当前修订 {sc.revision or 1}），"
                "频率/时间/规则可能已变化")
        elif (sc.fingerprint or "") and sc.fingerprint != row.scenario_fingerprint:
            stale, reason = True, "场景内容指纹与保存时不一致"
    at_utc = row.at_utc.astimezone(timezone.utc).isoformat() if row.at_utc else None
    return SavedPlanOut(
        id=row.id, scenario_id=row.scenario_id,
        scenario_revision=row.scenario_revision,
        scenario_fingerprint=row.scenario_fingerprint,
        mode=row.mode, band_low_mhz=row.band_low_mhz, band_high_mhz=row.band_high_mhz,
        at_utc=at_utc, at_local_label=row.at_local_label, scope=row.scope,
        feasible=row.feasible, status=row.status, objective_khz=row.objective_khz,
        result=row.result, post_check=row.post_check,
        created_at=row.created_at.isoformat() if row.created_at else None,
        stale=stale, stale_reason=reason)


@app.get("/api/plans", response_model=list[SavedPlanOut])
def list_plans(scenario_id: int | None = None) -> list[SavedPlanOut]:
    with Session(engine) as s:
        stmt = select(PlanRow).order_by(PlanRow.id.desc())
        if scenario_id is not None:
            stmt = stmt.where(PlanRow.scenario_id == scenario_id)
        rows = list(s.scalars(stmt))
        out = []
        for r in rows:
            out.append(_plan_to_out(r, s.get(Scenario, r.scenario_id)))
        return out


@app.get("/api/plans/{plan_id}", response_model=SavedPlanOut)
def get_plan(plan_id: int) -> SavedPlanOut:
    with Session(engine) as s:
        row = s.get(PlanRow, plan_id)
        if row is None:
            raise HTTPException(404, "规划不存在")
        return _plan_to_out(row, s.get(Scenario, row.scenario_id))


@app.post("/api/plans", response_model=SavedPlanOut, status_code=201)
def save_plan(body: PlanSaveIn) -> SavedPlanOut:
    """保存当前规划结果。流程：按当前场景重新求解 + post-check，并绑定修订/时间窗口。"""
    with Session(engine) as s:
        sc = s.get(Scenario, body.scenario_id)
        if sc is None:
            raise HTTPException(404, "场景不存在")

        req = ScenarioIn(
            name=sc.name, description=sc.description or "",
            band_low_mhz=body.band_low_mhz, band_high_mhz=body.band_high_mhz,
            guard_required_mhz=sc.guard_required_mhz,
            leakage_limit_dbm=sc.leakage_limit_dbm,
            reuse_policy=sc.reuse_policy or {},
            carriers=[_carrier_in_row(c) for c in sorted(sc.carriers, key=lambda c: c.position)],
        )
        rules = to_rules(req)
        try:
            validate_masks(req.carriers)
            carriers = [to_domain(c) for c in req.carriers]
            at = ensure_aware_utc(body.at) if body.at is not None else None
            result = plan(carriers, rules,
                          BandLimits(req.band_low_mhz, req.band_high_mhz),
                          mode=body.mode, at=at)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        post = None
        if result["feasible"]:
            windows_by_name = {c.name: c.windows for c in carriers}
            planned = [
                Carrier(id=None, name=a["name"], center_mhz=a["center_mhz"],
                        bandwidth_mhz=a["bandwidth_mhz"], power_dbm=a["power_dbm"],
                        polarization=a["polarization"], mask_name=a["mask_name"],
                        windows=windows_by_name[a["name"]])
                for a in result["assignments"]
            ]
            # 持久化前强制 post-check：保存即可执行口径的方案必须此刻/全天分析达标
            post = analyze(planned, rules, at=at)

        label = None
        if at is not None:
            label = at.strftime("%Y-%m-%d %H:%M UTC")

        row = PlanRow(
            scenario_id=sc.id, scenario_revision=sc.revision or 1,
            scenario_fingerprint=sc.fingerprint or "",
            mode=body.mode, band_low_mhz=body.band_low_mhz,
            band_high_mhz=body.band_high_mhz,
            scope=("instant" if at is not None else "anytime"),
            at_utc=at, at_local_label=label,
            feasible=result["feasible"], status=result["status"],
            objective_khz=result.get("objective_khz"),
            result=result, post_check=post)
        s.add(row)
        s.commit()
        s.refresh(row)
        return _plan_to_out(row, sc)


def _carrier_in_row(c: CarrierRow):
    from .schemas import CarrierIn, WindowIn
    return CarrierIn(
        name=c.name, center_mhz=c.center_mhz, bandwidth_mhz=c.bandwidth_mhz,
        power_dbm=c.power_dbm, polarization=c.polarization, mask_name=c.mask_name,
        schedule=[WindowIn(**w) for w in (c.schedule or [])])


@app.post("/api/plans/{plan_id}/recheck")
def recheck_plan(plan_id: int) -> dict:
    """对已保存规划按当前场景重新 post-check。

    过期规划拒绝直接复用：返回 stale=True 且 usable=False；仍有效则返回
    最新 post-check 结果（error/warning/pending 计数）。
    """
    with Session(engine) as s:
        row = s.get(PlanRow, plan_id)
        if row is None:
            raise HTTPException(404, "规划不存在")
        sc = s.get(Scenario, row.scenario_id)
        out = _plan_to_out(row, sc)
        if out.stale:
            return {"usable": False, "stale": True, "stale_reason": out.stale_reason,
                    "post_check": None}
        return {"usable": True, "stale": False,
                "post_check": row.post_check,
                "saved_at": row.created_at.isoformat() if row.created_at else None}


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
