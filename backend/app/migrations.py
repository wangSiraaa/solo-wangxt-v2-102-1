"""轻量启动迁移（无 Alembic 依赖）：给旧库补列并回填“始终激活”默认语义。

幂等：每次启动检查 information_schema（SQLite/PostgreSQL 通用写法用 inspector），
缺什么补什么。旧场景/旧载波的激活区间统一回填为空列表 —— 在模型语义里
空列表 ⇔ 始终激活，因此历史分析结论完全不变。
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .db import Base


def _existing_columns(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def run_migrations(engine: Engine) -> None:
    # 新表（含 plan_records）直接创建；已存在的表不动
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        dialect = engine.dialect.name

        # ---- scenarios：revision / updated_at ----
        cols = _existing_columns(engine, "scenarios")
        if "revision" not in cols:
            conn.execute(text("ALTER TABLE scenarios ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"))
        if "updated_at" not in cols:
            if dialect == "sqlite":
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN updated_at DATETIME"))
            else:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE"))

        # ---- carriers：windows（旧数据回填 '[]' = 始终激活）----
        ccols = _existing_columns(engine, "carriers")
        if "windows" not in ccols:
            conn.execute(text("ALTER TABLE carriers ADD COLUMN windows JSON"))
            # 不同驱动 JSON 列的字面量写法不同，直接按行回填最稳妥
            rows = conn.execute(text("SELECT id FROM carriers")).fetchall()
            for (cid,) in rows:
                conn.execute(text("UPDATE carriers SET windows = :w WHERE id = :id"),
                             {"w": "[]", "id": cid})
            if dialect != "sqlite":
                conn.execute(text("UPDATE carriers SET windows = '[]' WHERE windows IS NULL"))
        else:
            # 兼容手工建库/历史 NULL
            conn.execute(text("UPDATE carriers SET windows = '[]' WHERE windows IS NULL"))
