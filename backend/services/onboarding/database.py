"""Onboarding service — DB wiring."""
import sys

sys.path.append("/app")
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    import models  # noqa: F401  (registers onboarding tables on Base)
    from shared.utils import docstore  # noqa: F401  (employee_documents — uploaded files)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Columns added after v0.2 (create_all never alters an existing table).
        from sqlalchemy import text
        for col, ddl in (("country", "VARCHAR(2)"), ("review_note", "VARCHAR(500)")):
            await conn.execute(text(f"ALTER TABLE onboarding_sessions ADD COLUMN IF NOT EXISTS {col} {ddl}"))
