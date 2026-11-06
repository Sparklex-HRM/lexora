"""Attendance Agent — READ-ONLY collection (SELECT only)."""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta

sys.path.append("/app")

from watchdogs import ORG, rows  # noqa: E402

log = logging.getLogger("lexora.agentic.attendance")


def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


async def cutoff(org: str, today: date) -> dict:
    """The open pay period + the input cut-off (pay date − the org's lead days, default 3)."""
    try:
        import agentic_state as ST
        from agents import pay_calendar as PC
        lead = int((await ST.agentic_state(org)).get("lead_days") or 3)
        return await PC.open_period(org, today, lead)
    except Exception as e:                                       # noqa: BLE001
        log.info("pay period unavailable for %s: %s", org, e)
        ps = today - timedelta(days=(today.weekday() + 1) % 7)
        pe = ps + timedelta(days=13)
        return {"period": f"{ps.isoformat()}..{pe.isoformat()}", "period_start": ps, "period_end": pe, "pay_date": pe + timedelta(days=6),
                "cutoff": pe + timedelta(days=3), "lead_days": 3}


async def collect(org: str, *, today: date, window_days: int = 7) -> dict:
    start, end = today - timedelta(days=int(window_days)), today - timedelta(days=1)
    month_start = date(today.year, today.month, 1)
    P = await cutoff(org, today)
    emps = await rows(f"SELECT id, employee_number, first_name, last_name, manager_id, gender, status FROM employees WHERE org_id = {ORG} AND is_active IS TRUE "
                      "AND status IN ('active','on_leave')", {"org": org})
    for e in emps:
        e["id"] = str(e["id"])
        e["name"] = f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip()
        e["manager_id"] = str(e["manager_id"]) if e.get("manager_id") else None
    days = await rows(f"SELECT employee_id, work_date, first_in, last_out, status, late_minutes, is_holiday FROM ta_attendance_days WHERE org_id = {ORG} "
                      "AND work_date >= :s AND work_date <= :e", {"org": org, "s": min(start, month_start), "e": end})
    regs = await rows(f"SELECT id, employee_id, manager_id, work_date, reason, status, created_at FROM ta_regularizations WHERE org_id = {ORG} "
                      "AND (status = 'pending' OR work_date >= :s)", {"org": org, "s": start})
    leave = await rows(f"SELECT id, employee_id, manager_id, leave_type_code, start_date, end_date, status, created_at FROM leave_requests WHERE org_id = {ORG} "
                       "AND status IN ('pending','approved') AND end_date >= :s", {"org": org, "s": min(start, P["period_start"])})
    return {"today": today, "window": {"start": start, "end": end}, "pay": P, "employees": emps, "days": days, "regularisations": regs, "leave": leave,
            "sources": {"employees": f"{len(emps)} active", "attendance_days": f"{len(days)} rows", "regularisations": f"{len(regs)} rows",
                        "leave": f"{len(leave)} rows"}}
