"""Notification service — DB wiring.

This service OWNS the ``ntf_*`` (notifications, delivery log, preferences, templates,
reminders) and ``org_*`` (company settings, settings audit, logo) tables. The models
live in ``shared/utils/platform_models.py`` because ``notify()`` and ``get_setting()``
write / read them from every service.
"""
import sys

sys.path.append("/app")
from sqlalchemy import text  # noqa: E402

from shared.utils import platform_models as pm  # noqa: E402

# `create_all` never ALTERs an existing table. A column added after the first release
# goes here as ("table", "column", "SQL TYPE [DEFAULT …]") and is added on boot.
_NEW_COLUMNS: list[tuple[str, str, str]] = []


async def get_db():
    async with pm.session() as session:
        yield session


async def init_db():
    await pm.create_tables(pm.NOTIFICATION_TABLES)
    if _NEW_COLUMNS:
        async with pm.get_engine().begin() as conn:
            for table, column, ddl in _NEW_COLUMNS:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
