"""Stream D7 boot: tables + one best-effort tick (every ENGAGE_TICK_SECONDS, default 10 min) that

  * opens surveys whose schedule is due              (engage2_routers.run_due_schedules)
  * sends 30 / 60 / 90-day onboarding check-ins       (engage2_routers.run_onboarding_surveys)
  * runs active LMS auto-assignment rules            (lms_routers.run_rule, per org)
  * sends training due-soon / overdue reminders       (lms_routers.run_reminders)

Every step is wrapped: a missing table on an installation is logged and skipped.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select, text

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import engage2_models  # noqa: E402,F401
import lms_models  # noqa: E402,F401
import positions_models  # noqa: E402,F401
from database import Base, SessionLocal, engine  # noqa: E402

_TASK = None


async def init_d7_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in lms_models.LMS_DDL + engage2_models.ENGAGE2_DDL + positions_models.POSITIONS_DDL:
            await conn.execute(text(ddl))


async def _rules_tick(db) -> int:
    from lms_models import LmsRule
    from lms_routers import run_rule
    n = 0
    rules = (await db.execute(select(LmsRule).where(LmsRule.active.is_(True)))).scalars().all()
    by_org: dict[str, list] = {}
    for r in rules:
        by_org.setdefault(str(r.org_id), []).append(r)
    for org, rs in by_org.items():
        emps = {e["id"]: {**e, "role": None} for e in await directory.org_employees(org)}
        roles = {u.get("employee_id"): u.get("role") for u in await directory.org_users(org) if u.get("employee_id")}
        try:
            async with SessionLocal() as s2:
                hire = (await s2.execute(text("SELECT id, hire_date, work_location, status FROM employees WHERE org_id = CAST(:org AS uuid) AND is_active IS TRUE"),
                                         {"org": org})).mappings().all()
            for h in hire:
                if str(h["id"]) in emps:
                    emps[str(h["id"])].update({"hire_date": h["hire_date"], "work_location": h["work_location"], "status": h["status"]})
        except Exception:                               # noqa: BLE001
            pass
        for k, e in emps.items():
            e["role"] = roles.get(k)
            e["id"] = k
        today = await org_today(org)
        for r in rs:
            n += await run_rule(db, org, r, emps, today, "system@lms-rules")
    return n


async def _tick_once() -> dict:
    out = {}
    from engage2_routers import run_due_schedules, run_onboarding_surveys
    from lms_routers import run_reminders
    for name, fn in (("surveys", run_due_schedules), ("onboarding", run_onboarding_surveys), ("rules", _rules_tick), ("reminders", run_reminders)):
        try:
            async with SessionLocal() as db:
                out[name] = await fn(db)
        except Exception as ex:                         # noqa: BLE001
            print(f"[ops] d7 {name} skipped: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
            out[name] = None
    return out


async def _loop():
    every = int(os.getenv("ENGAGE_TICK_SECONDS", "600"))
    await asyncio.sleep(40)
    while True:
        res = await _tick_once()
        if any(res.values()):
            print(f"[ops] d7 tick: {res}", flush=True)
        await asyncio.sleep(max(every, 60))


async def d7_init() -> None:
    global _TASK
    await init_d7_schema()
    if _TASK is None and os.getenv("ENGAGE_TICK_DISABLED", "").lower() not in ("1", "true", "yes"):
        _TASK = asyncio.create_task(_loop())
