"""Payroll service — DB wiring."""
import sys

sys.path.append("/app")
from sqlalchemy import text  # noqa: E402

from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


# Columns added after v0.1 — create_all won't ALTER an existing table.
_NEW_COLUMNS = [
    ("employee_ytd", "bonus", "NUMERIC(14,2) DEFAULT 0"),
    ("employee_ytd", "vac_accrued", "NUMERIC(14,2) DEFAULT 0"),
    ("employee_ytd", "vac_paid", "NUMERIC(14,2) DEFAULT 0"),
    ("employee_ytd", "qpip", "NUMERIC(12,2) DEFAULT 0"),
    ("pay_runs", "meta", "JSONB"),
]
# Idempotent index DDL: the statutory sign-off is one row per (scope, rule).
_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_statutory_rule_verification_scope ON statutory_rule_verification "
    "(COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), rule_key)",
]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for tbl, col, ddl in _NEW_COLUMNS:
            await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        for ddl in _INDEXES:
            await conn.execute(text(ddl))
        # D4 multi-entity: entity_id on pay_runs / pay_stubs (+ indexes).
        from entity_scope import ensure_columns
        await ensure_columns(conn)
