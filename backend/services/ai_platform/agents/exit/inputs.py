"""Exit Agent — READ-ONLY collection (SELECT only). One CASE = one open exit case."""
from __future__ import annotations

import sys
from datetime import date, datetime

sys.path.append("/app")

from watchdogs import ORG, rows  # noqa: E402

OPEN_STATUSES = ("submitted", "manager_review", "hr_review", "accepted", "clearance", "settlement", "settled", "on_hold")


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


async def collect(org: str, *, today: date) -> dict:
    sts = ",".join(f"'{s}'" for s in OPEN_STATUSES)
    cases = await rows(
        "SELECT id, case_number, employee_id, employee_name, department, manager_id, country, exit_type, status, submitted_on, requested_lwd, "
        "accepted_on, notice_days, notice_source, expected_lwd, lwd, notice_served_days, notice_shortfall_days, notice_treatment, "
        f"settlement_kind, settlement_ref, settlement_status, settlement_paid_at, roe_reason_code, letters, created_at, updated_at FROM exit_cases "
        f"WHERE org_id = {ORG} AND status IN ({sts})", {"org": org})
    items = await rows(f"SELECT id, case_id, department, item, owner_role, owner_user_id, owner_employee_id, owner_name, status, acted_at, sort "
                       f"FROM exit_clearance_items WHERE org_id = {ORG}", {"org": org})
    letters = await rows(f"SELECT employee_id, template_key, status, id FROM issued_letters WHERE org_id = {ORG} "
                         "AND template_key IN ('relieving','experience_certificate') AND status IN ('draft','issued')", {"org": org})
    emps = await rows(f"SELECT id, employee_number, first_name, last_name, manager_id, province_of_employment FROM employees WHERE org_id = {ORG}", {"org": org})
    from agents import pay_calendar as PC
    sched = await PC.schedule(org)
    emp_by = {str(e["id"]): e for e in emps}
    items_by: dict = {}
    for i in items:
        items_by.setdefault(str(i["case_id"]), []).append(i)
    let_by: dict = {}
    for l in letters:
        let_by.setdefault(str(l["employee_id"]), []).append(l)
    out = []
    for c in cases:
        cid, eid = str(c["id"]), str(c["employee_id"])
        e = emp_by.get(eid) or {}
        lwd = _d(c.get("lwd"))
        period_end, next_pay = PC.period_containing(sched, lwd)
        out.append({**c, "id": cid, "employee_id": eid, "lwd": lwd, "expected_lwd": _d(c.get("expected_lwd")), "submitted_on": _d(c.get("submitted_on")),
                    "province": e.get("province_of_employment"), "pay_period_end": period_end, "next_pay_day": next_pay,
                    "employee": {"id": eid, "employee_number": e.get("employee_number"), "name": c.get("employee_name") or f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
                                 "manager_id": str(c.get("manager_id") or e.get("manager_id") or "") or None},
                    "clearance": items_by.get(cid, []), "letter_rows": let_by.get(eid, []),
                    "case_letters": c.get("letters") if isinstance(c.get("letters"), list) else []})
    return {"today": today, "cases": out, "sources": {"exit_cases": f"{len(cases)} open", "clearance": f"{len(items)} items",
                                                     "pay_schedule": "from the last pay run" if sched else "no pay run yet (ROE / final-pay clocks estimated)"}}
