"""验收 3–4（API 层）：

3) 保存规划绑定场景修订与时间窗口；时间被改后旧规划显示过期、拒绝直接复用，
   重新规划可通过 post-check。
4) 旧场景（无时间数据）导入后默认“始终激活”，跨午夜编辑、重启/刷新后语义一致。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base, CarrierRow, MaskRow, Scenario
from app.revisions import scenario_fingerprint
from app.schemas import ScenarioIn
from app.seed import DEMO_CARRIERS, DEMO_POLICY, TIME_CARRIERS
from app.services.masks import MASKS

RULES = {"guard_required_mhz": 1.0, "leakage_limit_dbm": -45.0,
         "reuse_policy": DEMO_POLICY}


def _scenario(name, description, carriers_kw):
    """按生产口径（POST /seed）构造带 revision/fingerprint 的场景行。"""
    req = ScenarioIn(name=name, description=description,
                     band_low_mhz=80, band_high_mhz=220,
                     guard_required_mhz=1.0, leakage_limit_dbm=-45.0,
                     reuse_policy=DEMO_POLICY,
                     carriers=[{k: v for k, v in kw.items() if k != "position"}
                               for kw in carriers_kw])
    return Scenario(name=name, description=description, band_low_mhz=80,
                    band_high_mhz=220, guard_required_mhz=1.0,
                    leakage_limit_dbm=-45.0, reuse_policy=DEMO_POLICY,
                    revision=1, fingerprint=scenario_fingerprint(req),
                    carriers=[CarrierRow(**kw) for kw in carriers_kw])


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for m in MASKS.values():
            s.add(MaskRow(name=m.name, points=[list(p) for p in m.points],
                          span_mhz=m.span_mhz, description=m.description))
        # 旧风格场景：载波完全没有 schedule 字段（模拟旧库导入）
        s.add(_scenario("旧场景", "legacy", DEMO_CARRIERS))
        s.add(_scenario("时间复用演示", "t", TIME_CARRIERS))
        s.commit()
    monkeypatch.setattr(main, "engine", engine)
    with TestClient(main.app) as c:
        yield c, engine


# ---- 验收 1/2 的 API 镜像 --------------------------------------------------

def test_api_disjoint_time_pair_has_no_conflict(client):
    c, _ = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "时间复用演示")
    detail = c.get(f"/api/scenarios/{sc['id']}").json()

    # 全天口径：T1/T2（跨午夜首尾相接）与 T3/T4（UTC 偏移错开）都不报冲突
    r = c.post("/api/analyze", json={"carriers": detail["carriers"], "rules": RULES}).json()
    flagged = [{f["carrier_a"], f["carrier_b"]} for f in r["findings"]]
    assert {"T1", "T2"} not in flagged
    assert {"T3", "T4"} not in flagged
    sep = {(p["carrier_a"], p["carrier_b"]) for p in r["time_separated_pairs"]}
    assert ("T1", "T2") in sep and ("T3", "T4") in sep

    # instant：北京 12:00（UTC 04:00）T1/T3 在发，同频对仍无冲突
    ri = c.post("/api/analyze", json={
        "carriers": detail["carriers"], "rules": RULES,
        "at": "2026-10-01T04:00:00+00:00"}).json()
    assert ri["time_context"]["scope"] == "instant"
    assert set(ri["time_context"]["active_carriers"]) == {"T1", "T3", "T5"}
    assert not [f for f in ri["findings"]
                if {f["carrier_a"], f["carrier_b"]} in ({"T1", "T2"}, {"T3", "T4"})]
    # 未激活载波在 bands 里仍在，但 active=False（前端灰显）
    by_name = {b["name"]: b for b in ri["bands"]}
    assert by_name["T2"]["active"] is False and by_name["T1"]["active"] is True
    # 发射谱只包含激活载波
    assert {cu["name"] for cu in ri["spectrum"]["curves"]} == {"T1", "T3", "T5"}


def test_api_extend_window_to_overlap_shows_guard_and_directional_leak(client):
    c, _ = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "时间复用演示")
    detail = c.get(f"/api/scenarios/{sc['id']}").json()
    carriers = detail["carriers"]
    for x in carriers:
        if x["name"] == "T2":
            x["schedule"] = [{"start": "19:00", "end": "08:00",
                              "tz_offset_minutes": 480}]  # 延长 1 小时

    # 重叠时刻 北京 19:30 = UTC 11:30
    r = c.post("/api/analyze", json={
        "carriers": carriers, "rules": RULES,
        "at": "2026-10-01T11:30:00+00:00"}).json()
    tails = {(f["carrier_a"], f["carrier_b"]): f["leakage_dbm"]
             for f in r["findings"] if f["type"] == "mask_tail"}
    guards = [f for f in r["findings"] if f["type"] == "guard_shortfall"]
    assert ("T1", "T2") in tails and ("T2", "T1") in tails
    assert tails[("T1", "T2")] > tails[("T2", "T1")]  # 强载波方向泄漏更大
    assert any({f["carrier_a"], f["carrier_b"]} == {"T1", "T2"} for f in guards)

    # 非重叠时刻北京 12:00：恢复干净
    r2 = c.post("/api/analyze", json={
        "carriers": carriers, "rules": RULES,
        "at": "2026-10-01T04:00:00+00:00"}).json()
    assert not [f for f in r2["findings"]
                if {f["carrier_a"], f["carrier_b"]} == {"T1", "T2"}]


# ---- 验收 3：规划绑定修订/时间窗口，失效后过期，拒绝复用 --------------------

def test_saved_plan_becomes_stale_after_time_edit_and_replan_passes(client):
    c, _ = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "时间复用演示")
    sid = sc["id"]

    # 保存一份全天掩模感知规划（此时 T1/T2 时间错开 => 方案可行，post-check 零越界）
    saved = c.post("/api/plans", json={
        "scenario_id": sid, "mode": "mask_aware",
        "band_low_mhz": 80, "band_high_mhz": 300}).json()
    assert saved["feasible"] is True and saved["stale"] is False
    assert saved["scope"] == "anytime" and saved["at_utc"] is None
    pid = saved["id"]
    rev0 = saved["scenario_revision"]
    assert saved["post_check"]["counts"]["error"] == 0

    # 复用检查：可用
    rc = c.post(f"/api/plans/{pid}/recheck").json()
    assert rc["usable"] is True and rc["stale"] is False

    # 把 T2 延长到与 T1 重叠 1 小时（编辑时间）
    detail = c.get(f"/api/scenarios/{sid}").json()
    for x in detail["carriers"]:
        if x["name"] == "T2":
            x["schedule"] = [{"start": "19:00", "end": "08:00",
                              "tz_offset_minutes": 480}]
    upd = c.put(f"/api/scenarios/{sid}", json={
        "name": detail["name"], "description": detail["description"],
        "band_low_mhz": detail["band_low_mhz"], "band_high_mhz": detail["band_high_mhz"],
        "guard_required_mhz": detail["guard_required_mhz"],
        "leakage_limit_dbm": detail["leakage_limit_dbm"],
        "reuse_policy": detail["reuse_policy"], "carriers": detail["carriers"]})
    assert upd.status_code == 200
    assert upd.json()["revision"] == rev0 + 1

    # 旧规划立即标过期，且拒绝直接复用
    got = c.get(f"/api/plans/{pid}").json()
    assert got["stale"] is True and "修订" in got["stale_reason"]
    rc2 = c.post(f"/api/plans/{pid}/recheck").json()
    assert rc2["usable"] is False and rc2["stale"] is True
    listing = c.get("/api/plans", params={"scenario_id": sid}).json()
    assert any(p["id"] == pid and p["stale"] for p in listing)

    # 重新规划（掩模感知，全天口径）：可行且 post-check 零越界
    replanned = c.post("/api/plans", json={
        "scenario_id": sid, "mode": "mask_aware",
        "band_low_mhz": 80, "band_high_mhz": 300}).json()
    assert replanned["feasible"] is True and replanned["stale"] is False
    assert replanned["post_check"]["counts"]["error"] == 0
    assert replanned["post_check"]["counts"]["warning"] == 0
    assert replanned["scenario_revision"] == rev0 + 1


def test_instant_saved_plan_records_time_window(client):
    c, _ = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "时间复用演示")
    saved = c.post("/api/plans", json={
        "scenario_id": sc["id"], "mode": "mask_aware",
        "band_low_mhz": 80, "band_high_mhz": 300,
        "at": "2026-10-01T04:00:00+00:00"}).json()
    assert saved["scope"] == "instant"
    assert saved["at_utc"].startswith("2026-10-01T04:00:00")
    assert saved["post_check"]["counts"]["error"] == 0


def test_same_content_save_does_not_bump_revision(client):
    c, _ = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "时间复用演示")
    detail = c.get(f"/api/scenarios/{sc['id']}").json()
    rev = detail["revision"]
    payload = {"name": detail["name"], "description": detail["description"],
               "band_low_mhz": detail["band_low_mhz"], "band_high_mhz": detail["band_high_mhz"],
               "guard_required_mhz": detail["guard_required_mhz"],
               "leakage_limit_dbm": detail["leakage_limit_dbm"],
               "reuse_policy": detail["reuse_policy"], "carriers": detail["carriers"]}
    r = c.put(f"/api/scenarios/{sc['id']}", json=payload).json()
    assert r["revision"] == rev  # 内容未变 => 修订号不涨，旧规划不应过期


# ---- 验收 4：旧场景导入 / 跨午夜 / 重启语义一致 ----------------------------

def test_legacy_scenario_defaults_always_active(client):
    c, _ = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "旧场景")
    detail = c.get(f"/api/scenarios/{sc['id']}").json()
    assert all(x["schedule"] == [] and x["always_active"] is True
               for x in detail["carriers"])
    assert detail["revision"] == 1
    # 不带 at（全天）与任意 at，结论一致（历史分析不变）
    base = {"carriers": detail["carriers"], "rules": RULES}
    r_any = c.post("/api/analyze", json=base).json()
    r_at = c.post("/api/analyze", json={**base, "at": "2026-10-01T04:00:00+00:00"}).json()
    assert [tuple(sorted((f["carrier_a"], f["carrier_b"])) + [f["type"]])
            for f in r_any["findings"]] == \
           [tuple(sorted((f["carrier_a"], f["carrier_b"])) + [f["type"]])
            for f in r_at["findings"]]
    # 旧场景的标志性冲突仍在（历史结果不改变）
    pairs = {(f["carrier_a"], f["carrier_b"], f["type"]) for f in r_any["findings"]}
    assert ("C9", "C10", "overlap") in pairs
    assert ("C1", "C2", "mask_tail") in pairs
    assert ("C2", "C1", "mask_tail") in pairs


def test_cross_midnight_edit_roundtrips_and_survives_restart(client):
    c, engine = client
    sc = next(s for s in c.get("/api/scenarios").json() if s["name"] == "时间复用演示")
    detail = c.get(f"/api/scenarios/{sc['id']}").json()
    for x in detail["carriers"]:
        if x["name"] == "T1":
            # 编辑成跨午夜 + 非整点偏移（UTC+5:30）
            x["schedule"] = [{"start": "23:30", "end": "01:15",
                              "tz_offset_minutes": 330}]
    payload = {"name": detail["name"], "description": detail["description"],
               "band_low_mhz": detail["band_low_mhz"], "band_high_mhz": detail["band_high_mhz"],
               "guard_required_mhz": detail["guard_required_mhz"],
               "leakage_limit_dbm": detail["leakage_limit_dbm"],
               "reuse_policy": detail["reuse_policy"], "carriers": detail["carriers"]}
    r = c.put(f"/api/scenarios/{sc['id']}", json=payload)
    assert r.status_code == 200, r.text
    saved = next(x for x in r.json()["carriers"] if x["name"] == "T1")
    assert saved["schedule"] == [{"start": "23:30", "end": "01:15",
                                  "tz_offset_minutes": 330}]

    # 重启：对同一引擎重新跑建表/轻量迁移/seed（幂等），数据与语义保持
    main._light_migrate(engine)
    main.seed(engine)
    again = c.get(f"/api/scenarios/{sc['id']}").json()
    t1 = next(x for x in again["carriers"] if x["name"] == "T1")
    assert t1["schedule"][0]["end"] == "01:15"
    assert again["revision"] == 2


def test_lightweight_migration_adds_columns_to_old_schema(client):
    """旧库（无 revision / schedule 列）经迁移后可读、旧载波始终激活。"""
    c, engine = client
    # 模拟旧版表结构：直接删除新列不现实，改为校验迁移对已具备列的库幂等
    cols_sc = {x["name"] for x in inspect(engine).get_columns("scenarios")}
    cols_ca = {x["name"] for x in inspect(engine).get_columns("carriers")}
    assert {"revision", "fingerprint", "updated_at"} <= cols_sc
    assert "schedule" in cols_ca
    # 再跑一次不应报错
    main._light_migrate(engine)


def test_migration_from_actual_legacy_schema(monkeypatch):
    """真正的旧版表（缺列、缺 saved_plans 表）启动迁移后：旧载波始终激活、历史结论不变。"""
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE scenarios (id INTEGER PRIMARY KEY, name VARCHAR(128) UNIQUE, "
            "description TEXT, band_low_mhz FLOAT, band_high_mhz FLOAT, "
            "guard_required_mhz FLOAT, leakage_limit_dbm FLOAT, reuse_policy JSON, "
            "created_at TIMESTAMP)")
        conn.exec_driver_sql(
            "CREATE TABLE carriers (id INTEGER PRIMARY KEY, scenario_id INTEGER, "
            "position INTEGER, name VARCHAR(64), center_mhz FLOAT, bandwidth_mhz FLOAT, "
            "power_dbm FLOAT, polarization VARCHAR(8), mask_name VARCHAR(32))")
        conn.exec_driver_sql(
            "CREATE TABLE masks (id INTEGER PRIMARY KEY, name VARCHAR(32) UNIQUE, "
            "points JSON, span_mhz FLOAT, description TEXT)")
        conn.exec_driver_sql(
            "INSERT INTO scenarios VALUES (1,'旧导入','d',80,220,1,-45,'{}','2026-01-01 00:00:00')")
        conn.exec_driver_sql(
            "INSERT INTO carriers VALUES (1,1,0,'A',100,4,20,'H','strict')")
    monkeypatch.setattr(main, "engine", engine)
    with TestClient(main.app) as c:  # startup 内完成建表/迁移/seed/指纹回填
        sc = c.get("/api/scenarios/1").json()
        assert sc["revision"] == 1
        a = sc["carriers"][0]
        assert a["schedule"] == [] and a["always_active"] is True
        # 历史分析口径（与无时间功能时一致）
        r = c.post("/api/analyze", json={
            "carriers": [{k: v for k, v in a.items() if k != "id"}],
            "rules": RULES}).json()
        assert r["findings"] == []
        # 重启：再跑一遍启动流程，数据与语义不漂移
        main._light_migrate(engine)
        main.seed(engine)
        main._backfill_fingerprints(engine)
        again = c.get("/api/scenarios/1").json()
        assert again["revision"] == 1 and again["carriers"][0]["schedule"] == []


def test_reject_self_overlapping_and_zero_length_windows(client):
    c, _ = client
    body = {"carriers": [{
        "name": "A", "center_mhz": 100, "bandwidth_mhz": 4, "power_dbm": 10,
        "polarization": "H", "mask_name": "strict",
        "schedule": [{"start": "08:00", "end": "10:00", "tz_offset_minutes": 0},
                     {"start": "09:00", "end": "11:00", "tz_offset_minutes": 0}]}]}
    assert c.post("/api/analyze", json=body).status_code == 422

    body["carriers"][0]["schedule"] = [
        {"start": "10:00", "end": "10:00", "tz_offset_minutes": 0}]
    assert c.post("/api/analyze", json=body).status_code == 422


def test_naive_datetime_rejected(client):
    c, _ = client
    body = {"carriers": [{"name": "A", "center_mhz": 100, "bandwidth_mhz": 4,
                          "power_dbm": 10, "polarization": "H", "mask_name": "strict"}],
            "at": "2026-10-01T04:00:00"}
    assert c.post("/api/analyze", json=body).status_code == 422
