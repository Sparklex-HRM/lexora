"""Leave service — DB wiring (shared Postgres, row-level org_id)."""
import sys

sys.path.append("/app")
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


# 2026-10-16 employee portal (migration 0007): leave apply extras + cancellation request.
_ALTERS = [
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS half_session VARCHAR(10)",
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS cc_employee_ids JSONB",
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(30)",
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS contact_note VARCHAR(300)",
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS attachment_id UUID",
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS cancel_reason TEXT",
    "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ",
]


async def init_db():
    import models  # noqa: F401  (registers leave tables on Base)
    import models_ess  # noqa: F401  (employee portal tables)
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in _ALTERS:
            await conn.execute(text(ddl))
