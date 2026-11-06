"""Approvals service — DB wiring.

This service OWNS the ``apr_*`` tables (inbox items, decisions, delegations). The
models live in ``shared/utils/platform_models.py`` because ``open_item`` /
``close_item`` write them from every service. Reports only READ other services'
tables (see ``reports_data.py``).
"""
import sys

sys.path.append("/app")
from sqlalchemy import text  # noqa: E402

from shared.utils import platform_models as pm  # noqa: E402

# `create_all` never ALTERs an existing table. A column added after the first release
# goes here as ("table", "column", "SQL TYPE [DEFAULT …]") and is added on boot.
_NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("rb_schedules", "local_time", "VARCHAR(5)"),     # 2026-10-25: report schedule send time on the org's clock
    ("rb_schedules", "tz", "VARCHAR(64)"),
]


async def get_db():
    async with pm.session() as session:
        yield session


async def init_db():
    await pm.create_tables(pm.APPROVAL_TABLES)
    from shared.utils.approval_chains import CHAIN_TABLES   # D1: apr_chains + apr_chain_runs
    await pm.create_tables(CHAIN_TABLES)
    from report_builder_models import RB_TABLES             # G5 — created here too so the ALTERs below find the table
    await pm.create_tables(RB_TABLES)
    if _NEW_COLUMNS:
        async with pm.get_engine().begin() as conn:
            for table, column, ddl in _NEW_COLUMNS:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
