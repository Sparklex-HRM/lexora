"""Compliance service — DB wiring (shared Postgres, row-level org_id)."""
import sys

sys.path.append("/app")
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    import models  # noqa: F401  (registers compliance tables on Base)
    import privacy_models  # noqa: F401  (registers the privacy-centre tables on Base)
    import shared.utils.audit  # noqa: F401  (registers the shared audit_events table)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
