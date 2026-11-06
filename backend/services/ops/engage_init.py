"""Engagement boot: tables + the exit-survey hook (Stream G4).

Exit survey hook — best-effort, every 10 minutes: for each exit case that reached
`accepted` (or later) and has no survey yet, make an Exit survey targeted at that one
employee, tell them, and log the case so it is never sent twice. The exit tables belong to
the employee service; they are read here with a plain SELECT on the shared database and
any failure (table missing, column renamed) is logged and ignored.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta

from sqlalchemy import select, text

sys.path.append("/app")
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import engage_logic as L  # noqa: E402
from database import SessionLocal, engine  # noqa: E402
from engage_models import ExitSurveyLog, Survey, init_engage_schema  # noqa: E402

AFTER_ACCEPTANCE = ("accepted", "clearance", "settlement", "settled", "closed")


async def send_exit_surveys(db) -> int:
    statuses = ", ".join(f"'{s}'" for s in AFTER_ACCEPTANCE)          # constants, not user input
    rows = (await db.execute(text(
        f"SELECT id, org_id, employee_id, employee_name, lwd FROM exit_cases WHERE status IN ({statuses}) "
        "ORDER BY created_at DESC LIMIT 500"))).mappings().all()
    if not rows:
        return 0
    done = {(str(r.org_id), r.exit_case_id) for r in (await db.execute(select(ExitSurveyLog))).scalars().all()}
    sent = 0
    tpl = L.SURVEY_TEMPLATES["exit"]
    for r in rows:
        key = (str(r["org_id"]), str(r["id"]))
        if key in done:
            continue
        today = await org_today(r["org_id"])
        closes = (r["lwd"] or today) + timedelta(days=30)
        s = Survey(org_id=r["org_id"], title=f"Exit survey — {r['employee_name'] or 'leaver'}", kind="exit",
                   questions=tpl["questions"], anonymous=False, status="open", opens_on=today, closes_on=closes,
                   target={"employee_ids": [str(r["employee_id"])]}, threshold=1, exit_case_id=str(r["id"]),
                   created_by="system@exit-hook")
        db.add(s)
        await db.flush()
        db.add(ExitSurveyLog(org_id=r["org_id"], exit_case_id=str(r["id"]), survey_id=s.id))
        await db.commit()
        await notify(str(r["org_id"]), employee_ids=[str(r["employee_id"])], kind="exit",
                     title="Please fill in your exit survey", body="It takes about 3 minutes. Your answers help the team you leave behind.",
                     link=f"/engage/surveys?id={s.id}")
        sent += 1
    return sent


async def _loop():
    every = int(os.getenv("ENGAGE_TICK_SECONDS", "600"))
    await asyncio.sleep(25)
    while True:
        try:
            async with SessionLocal() as db:
                n = await send_exit_surveys(db)
                if n:
                    print(f"[ops] exit surveys sent: {n}", flush=True)
        except Exception as ex:  # noqa: BLE001 — the exit tables may not exist on this installation
            print(f"[ops] exit-survey hook skipped: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
        await asyncio.sleep(max(every, 60))


_TASK = None


async def engage_init() -> None:
    global _TASK
    await init_engage_schema(engine)
    if _TASK is None and os.getenv("ENGAGE_TICK_DISABLED", "").lower() not in ("1", "true", "yes"):
        _TASK = asyncio.create_task(_loop())
