"""Time & Attendance service — DB wiring (shared Postgres, row-level org_id)."""
import sys

sys.path.append("/app")
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


# Idempotent column adds for tables that predate a field (no Alembic — same
# ADD COLUMN IF NOT EXISTS pattern the other Lexora HRM services use on startup).
_MIGRATIONS = [
    "ALTER TABLE ta_timesheets ADD COLUMN IF NOT EXISTS ot2_hours NUMERIC(7,2) DEFAULT 0",
    "ALTER TABLE ta_timesheets ADD COLUMN IF NOT EXISTS stat_pay NUMERIC(9,2) DEFAULT 0",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS ot2_hours NUMERIC(6,2) DEFAULT 0",
    "ALTER TABLE ta_work_schedules ADD COLUMN IF NOT EXISTS site_lat NUMERIC(9,6)",
    "ALTER TABLE ta_work_schedules ADD COLUMN IF NOT EXISTS site_lng NUMERIC(9,6)",
    "ALTER TABLE ta_work_schedules ADD COLUMN IF NOT EXISTS geofence_m INTEGER",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS hours NUMERIC(6,2) DEFAULT 0",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS project_name VARCHAR(180)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS billable BOOLEAN DEFAULT false",
    "ALTER TABLE ta_timesheets ADD COLUMN IF NOT EXISTS manager_name VARCHAR(200)",
    # 2026-10-23 G2 rotation patterns: generated roster rows are ordinary rows + where they came from
    "ALTER TABLE ta_roster_shifts ADD COLUMN IF NOT EXISTS source VARCHAR(12) DEFAULT 'manual'",
    "ALTER TABLE ta_roster_shifts ADD COLUMN IF NOT EXISTS kind VARCHAR(8) DEFAULT 'work'",
    "ALTER TABLE ta_roster_shifts ADD COLUMN IF NOT EXISTS shift_code VARCHAR(8)",
    "ALTER TABLE ta_roster_shifts ADD COLUMN IF NOT EXISTS pattern_id UUID",
    "ALTER TABLE ta_roster_shifts ADD COLUMN IF NOT EXISTS assignment_id UUID",
    "CREATE INDEX IF NOT EXISTS ix_ta_roster_shifts_emp_date ON ta_roster_shifts (org_id, employee_id, work_date)",
]


async def init_db():
    import models  # noqa: F401  (registers T&A tables on Base)
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _MIGRATIONS:
            await conn.execute(text(stmt))
