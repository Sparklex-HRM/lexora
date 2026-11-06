"""Alembic environment — async engine on DATABASE_URL, version table lexora_schema_version.

Revisions are written by hand with ``op.*`` (no autogenerate across services — model
modules of different services cannot share one interpreter). Use
``python -m migrations.cli check`` to see what the models need that the DB lacks.
"""
import asyncio
import os
import sys

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from migrations.cli import VERSION_TABLE  # noqa: E402

config = context.config


def _url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    return url


def _do_run(connection):
    context.configure(connection=connection, target_metadata=None, version_table=VERSION_TABLE,
                      transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def _online():
    engine = create_async_engine(_url(), pool_pre_ping=True)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url().replace("+asyncpg", ""), literal_binds=True, version_table=VERSION_TABLE)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_online())
