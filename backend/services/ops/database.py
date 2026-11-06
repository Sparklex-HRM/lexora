"""Operations service — DB wiring."""
import sys

sys.path.append("/app")
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db():
    import models  # noqa: F401  (register tables on Base)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
