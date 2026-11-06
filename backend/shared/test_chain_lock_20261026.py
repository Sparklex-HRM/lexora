"""chain_lock (wave 2, 2026-10-26): concurrent appenders to a per-org hash chain never take the same seq.

Needs Postgres (advisory locks): set CHAIN_LOCK_DB_URL (or CHECK_DB_URL), e.g.
    CHAIN_LOCK_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5452/lexora python -m pytest -q shared/test_chain_lock_20261026.py
Uses a throw-away table (dropped at the end). On SQLite the helper is a no-op (tested too).
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.utils.chain_lock import lock_chain

URL = os.getenv("CHAIN_LOCK_DB_URL") or os.getenv("CHECK_DB_URL") or ""


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_noop_on_sqlite():
    async def go():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with async_sessionmaker(eng)() as s:
            assert await lock_chain(s, "x", "org") is False
        await eng.dispose()
    _run(go())


@pytest.mark.skipif(not URL.startswith("postgresql"), reason="needs Postgres (CHAIN_LOCK_DB_URL)")
def test_concurrent_appenders_get_distinct_seq():
    table = f"zz_chain_lock_test_{uuid.uuid4().hex[:8]}"
    org = str(uuid.uuid4())

    async def go():
        eng = create_async_engine(URL, pool_size=10, max_overflow=20)
        sm = async_sessionmaker(eng, expire_on_commit=False)
        async with eng.begin() as c:
            await c.execute(text(f"CREATE TABLE {table} (org text, seq int, UNIQUE (org, seq))"))
        try:
            async def append(locked: bool):
                async with sm() as s:
                    if locked:
                        await lock_chain(s, table, org)
                    last = (await s.execute(text(f"SELECT max(seq) FROM {table} WHERE org = :o"), {"o": org})).scalar()
                    await asyncio.sleep(0.01)                     # widen the read → insert window
                    await s.execute(text(f"INSERT INTO {table} VALUES (:o, :q)"), {"o": org, "q": (last or 0) + 1})
                    await s.commit()

            res = await asyncio.gather(*(append(True) for _ in range(25)), return_exceptions=True)
            assert [r for r in res if isinstance(r, Exception)] == []
            async with eng.connect() as c:
                seqs = (await c.execute(text(f"SELECT seq FROM {table} WHERE org = :o ORDER BY seq"), {"o": org})).scalars().all()
            assert seqs == list(range(1, 26))
            # without the lock the same race fails (proves the test exercises the window)
            res = await asyncio.gather(*(append(False) for _ in range(10)), return_exceptions=True)
            assert any(isinstance(r, Exception) for r in res)
        finally:
            async with eng.begin() as c:
                await c.execute(text(f"DROP TABLE IF EXISTS {table}"))
            await eng.dispose()
    _run(go())
