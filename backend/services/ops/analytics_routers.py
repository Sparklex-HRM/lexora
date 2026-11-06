"""People analytics routes — Stream D7. Mounted under /engage/analytics (gateway `engage` → ops).

GET /engage/analytics/people?months=12          headcount trend, joiners / leavers, attrition by dept / tenure / manager,
                                                 gender mix (descriptive), span of control, overtime, leave liability
GET /engage/analytics/retention-actions          POST   PATCH /{id}      the retention actions log

Reads are plain SELECTs on the shared database (employee / leave / time tables belong to other services).
Money (leave liability $) only for payroll / finance / org admin; HR sees days.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402
from shared.utils.roles import FINANCE, HR_WRITE, OFFICE, has_role, require_roles  # noqa: E402

import analytics_logic as AL  # noqa: E402
import engage_common as C  # noqa: E402
from database import get_db  # noqa: E402
from positions_models import RetentionAction  # noqa: E402

router = APIRouter(prefix="/engage/analytics", tags=["people-analytics"])
ORG = "CAST(:org AS uuid)"


async def rows(sql: str, params: dict) -> list[dict]:
    """One SELECT; a missing table / column (module not installed) is an empty result, never a crash."""
    try:
        async with pm.session() as s:
            res = (await s.execute(text(sql), params)).mappings().all()
        return [{k: (str(v) if hasattr(v, "hex") and not isinstance(v, (str, bytes)) else v) for k, v in r.items()} for r in res]
    except Exception:                                   # noqa: BLE001
        return []


@router.get("/people")
async def people(request: Request, months: int = 12, db: AsyncSession = Depends(get_db)):
    require_roles(request, OFFICE + ("manager",), "see people analytics")
    org = C.org_of(request)
    today = await org_today(org)
    months = max(3, min(int(months or 12), 36))
    emps = await rows(f"SELECT id, first_name, last_name, department, job_title, grade, gender, hire_date, termination_date, status, "
                      f"manager_id, manager_name, work_location, annual_salary FROM employees WHERE org_id = {ORG}", {"org": org})
    for e in emps:
        e["name"] = f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip()
        if (e.get("status") or "") == "terminated" and not e.get("termination_date"):
            e["termination_date"] = None            # unknown date: counted as active until a date is set (reported below)
    if C.is_manager(request):
        me = C.emp_of(request) or ""
        emps = [e for e in emps if str(e.get("manager_id") or "") == me]
    series = AL.headcount_series(emps, today, months)
    last12 = series[-12:]
    leavers_12 = sum(p["leavers"] for p in last12)
    avg_hc = (sum(p["headcount"] for p in last12) / len(last12)) if last12 else 0
    money = has_role(C.role_of(request), FINANCE)
    out = {"as_of": today.isoformat(), "months": months, "headcount": sum(1 for e in emps if AL.active_on(e, today)),
           "series": series, "joiners_12m": sum(p["joiners"] for p in last12), "leavers_12m": leavers_12,
           "attrition_12m_pct": AL.attrition_rate(leavers_12, avg_hc),
           "attrition_by_department": AL.attrition_by(emps, today, "department"),
           "attrition_by_tenure": AL.attrition_by(emps, today, "tenure_band"),
           "attrition_by_manager": AL.attrition_by(emps, today, "manager_name"),
           "gender_by_department": AL.gender_mix(emps, "department", today),
           "span_of_control": AL.span_of_control(emps, today, int(await get_setting(org, "analytics.wide_span_at", 10) or 10)),
           "leavers_without_date": sum(1 for e in emps if (e.get("status") or "") == "terminated" and not e.get("termination_date")),
           "shows_money": money}
    if not C.is_manager(request):
        q_start = today - timedelta(days=365)
        sheets = await rows(f"SELECT t.employee_id, e.department, t.period_end, t.ot_hours, t.ot2_hours FROM ta_timesheets t "
                            f"LEFT JOIN employees e ON e.id = t.employee_id WHERE t.org_id = {ORG} AND t.period_end >= :s "
                            "AND t.status IN ('approved','locked')", {"org": org, "s": q_start})
        out["overtime_by_month"] = AL.overtime_by(sheets, "month")
        out["overtime_by_department"] = AL.overtime_by(sheets, "department")
        fy = today.year if today.month >= 4 else today.year - 1
        bals = await rows(f"SELECT b.employee_id, b.leave_type_code, b.leave_type_name, b.paid, b.allocated, b.carried_over, b.used "
                          f"FROM leave_balances b JOIN employees e ON e.id = b.employee_id WHERE b.org_id = {ORG} AND b.fiscal_year = :fy "
                          "AND e.is_active IS TRUE AND e.status = 'active'", {"org": org, "fy": fy})
        divisor = int(await get_setting(org, "leave.encashment_divisor", 30) or 30)
        sal = {str(e["id"]): float(e.get("annual_salary") or 0) for e in emps}
        name = {str(e["id"]): e["name"] for e in emps}
        dept = {str(e["id"]): e.get("department") for e in emps}
        ll = AL.leave_liability(bals, sal if money else {}, divisor)
        for r in ll["rows"]:
            r["employee_name"] = name.get(r["employee_id"])
            r["department"] = dept.get(r["employee_id"])
            if not money:
                r.pop("amount", None)
                for t in r["types"]:
                    t.pop("amount", None)
        if not money:
            ll.pop("total_amount", None)
        out["leave_liability"] = {**ll, "fiscal_year": fy, "balances_found": len(bals)}
    return out


# ══════════════════════════ retention actions ═════════════════════════════════

def _ra_out(r: RetentionAction) -> dict:
    return {"id": str(r.id), "employee_id": str(r.employee_id), "employee_name": r.employee_name, "department": r.department,
            "action": r.action, "kind": r.kind, "owner_name": r.owner_name, "owner_user_id": r.owner_user_id, "due_on": C.iso(r.due_on),
            "status": r.status, "outcome": r.outcome, "risk_score_at": r.risk_score_at, "risk_band_at": r.risk_band_at,
            "created_by": r.created_by, "created_at": C.iso(r.created_at), "closed_at": C.iso(r.closed_at)}


@router.get("/retention-actions")
async def list_actions(request: Request, status: str | None = None, employee_id: str | None = None, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE + ("manager",), "see retention actions")
    org = C.org_of(request)
    q = select(RetentionAction).where(RetentionAction.org_id == org)
    if status:
        q = q.where(RetentionAction.status == status)
    if employee_id:
        q = q.where(RetentionAction.employee_id == C.to_uuid(employee_id, "employee"))
    items = (await db.execute(q.order_by(RetentionAction.created_at.desc()))).scalars().all()
    if C.is_manager(request):
        me = C.emp_of(request) or ""
        team = {str(e["id"]) for e in await rows(f"SELECT id FROM employees WHERE org_id = {ORG} AND manager_id = CAST(:me AS uuid)", {"org": org, "me": me})}
        items = [r for r in items if str(r.employee_id) in team]
    return {"actions": [_ra_out(r) for r in items], "kinds": ["conversation", "pay_review", "role_change", "learning", "manager_change", "other"]}


class ActionIn(BaseModel):
    employee_id: str
    action: str
    kind: str = "conversation"
    owner_name: str | None = None
    due_on: str | None = None
    risk_score_at: int | None = None
    risk_band_at: str | None = None


@router.post("/retention-actions", status_code=201)
async def add_action(body: ActionIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE + ("manager",), "log retention actions")
    org = C.org_of(request)
    if not body.action.strip():
        raise HTTPException(422, "Say what will be done")
    e = await rows(f"SELECT id, first_name, last_name, department, manager_id FROM employees WHERE org_id = {ORG} AND id = CAST(:id AS uuid)",
                   {"org": org, "id": body.employee_id})
    if not e:
        raise HTTPException(404, "Employee not found")
    e = e[0]
    if C.is_manager(request) and str(e.get("manager_id") or "") != (C.emp_of(request) or ""):
        raise HTTPException(403, "You can log actions for your direct reports only")
    r = RetentionAction(org_id=uuid.UUID(org), employee_id=uuid.UUID(body.employee_id), employee_name=C.emp_name(e), department=e.get("department"),
                        action=body.action.strip()[:400], kind=body.kind[:30] if body.kind else "conversation",
                        owner_name=(body.owner_name or C.actor_of(request))[:200], owner_user_id=C.uid_of(request),
                        due_on=date.fromisoformat(body.due_on) if body.due_on else None, risk_score_at=body.risk_score_at,
                        risk_band_at=(body.risk_band_at or "")[:10] or None, created_by=C.actor_of(request))
    db.add(r)
    await db.commit()
    return _ra_out(r)


@router.patch("/retention-actions/{rid}")
async def update_action(rid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE + ("manager",), "update retention actions")
    org = C.org_of(request)
    r = (await db.execute(select(RetentionAction).where(RetentionAction.id == C.to_uuid(rid, "action"), RetentionAction.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Action not found")
    if "action" in body and str(body["action"]).strip():
        r.action = str(body["action"]).strip()[:400]
    if "kind" in body:
        r.kind = str(body["kind"] or "other")[:30]
    if "owner_name" in body:
        r.owner_name = str(body["owner_name"] or "")[:200] or None
    if "due_on" in body:
        r.due_on = date.fromisoformat(body["due_on"]) if body["due_on"] else None
    if "outcome" in body:
        r.outcome = str(body["outcome"] or "")[:4000] or None
    if "status" in body:
        if body["status"] not in ("open", "done", "dropped"):
            raise HTTPException(422, "Status must be open, done or dropped")
        r.status = body["status"]
        r.closed_at = C.now() if r.status != "open" else None
    await db.commit()
    return _ra_out(r)
