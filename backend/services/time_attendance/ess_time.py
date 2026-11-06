"""Employee self-service attendance (2026-10-16 employee portal).

GET /time/me/attendance?month=YYYY-MM[&employee_id=]
    One month as a calendar: P / A / L / HL / H / WO per day, shift, first in / last out (local
    time), hours, late minutes, exceptions, pending corrections; KPIs avg hours, late days,
    exception days. An employee sees only their own month; a manager their reports; HR anyone.

The pure parts (who may see whom, folding one day) live in ``ess_time_logic`` so they are
unit-tested without a database.
"""
import sys
import uuid
from datetime import date, timedelta

import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402

import ess_time_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from models import Punch, Regularization  # noqa: E402
from models_ess import Permission, TimeEssPolicy  # noqa: E402  (registers the portal tables before init_db)
from shared.utils import approvals_client  # noqa: E402
from routers import (  # noqa: E402
    _derive, _emp, _fetch_employees, _fetch_stat_holidays, _fwd, _local_time, _org, _plan_for, _role,
    _schedule_map, _today,
)

router = APIRouter(prefix="/time/me", tags=["time-self-service"])
perm_router = APIRouter(prefix="/time/permissions", tags=["time-permissions"])
HR = L.HR_ROLES


async def _leave_rows(request: Request, employee_id: str) -> list[dict]:
    """Approved + pending leave of one employee (the leave service is the system of record)."""
    params = {"employee_id": employee_id, "status": "approved,pending,cancel_requested"}
    if employee_id == _emp(request):
        params["scope"] = "mine"            # a manager's own leave is not in their "team" scope
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.LEAVE_URL}/leave/requests", headers=_fwd(request), params=params)
            if r.status_code == 200 and isinstance(r.json(), list):
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def resolve_target(request: Request, employee_id: str | None) -> dict:
    """The employee whose month is asked for, after the role check (404 for someone else's)."""
    role, me = _role(request), _emp(request)
    target = employee_id or me
    if not target:
        raise HTTPException(400, "Your login is not linked to an employee record")
    emps = {e["id"]: e for e in await _fetch_employees(request)}
    e = emps.get(target)
    if not e or not L.may_view(role, me, e):
        raise HTTPException(404, "Employee not found")
    return e


@router.get("/requests")
async def my_time_requests(request: Request, db: AsyncSession = Depends(get_db)):
    """The caller's own attendance corrections (and permissions, batch 2), newest first."""
    org, me = _org(request), _emp(request)
    if not me:
        return {"corrections": [], "permissions": []}
    regs = (await db.execute(select(Regularization).where(
        Regularization.org_id == org, Regularization.employee_id == uuid.UUID(me))
        .order_by(Regularization.created_at.desc()))).scalars().all()
    return {"corrections": [{"id": str(r.id), "work_date": r.work_date.isoformat(), "reason": r.reason,
                             "requested_in": r.requested_in, "requested_out": r.requested_out, "note": r.note,
                             "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None,
                             "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None} for r in regs],
            "permissions": [_perm_out(p) for p in (await db.execute(select(Permission).where(
                Permission.org_id == org, Permission.employee_id == uuid.UUID(me))
                .order_by(Permission.created_at.desc()))).scalars().all()]}


@router.get("/attendance")
async def my_attendance(request: Request, month: str | None = None, employee_id: str | None = None,
                        db: AsyncSession = Depends(get_db)):
    org = _org(request)
    e = await resolve_target(request, employee_id)
    today = await _today(org)
    start, end = L.month_bounds(month or today.strftime("%Y-%m"))
    by_emp, default = await _schedule_map(db, org)
    sched = by_emp.get(e["id"]) or default
    prov = e.get("province_of_employment")
    holidays = {}
    for h in await _fetch_stat_holidays(request):
        if h.get("holiday_date") and h.get("province") in (None, "", prov) and not h.get("is_optional"):
            holidays[date.fromisoformat(h["holiday_date"][:10])] = h.get("name") or "Holiday"
    punches = (await db.execute(select(Punch).where(
        Punch.org_id == org, Punch.employee_id == uuid.UUID(e["id"]),
        Punch.work_date >= start, Punch.work_date <= end))).scalars().all()
    by_day: dict = {}
    for p in punches:
        by_day.setdefault(p.work_date, []).append(p)
    regs = (await db.execute(select(Regularization).where(
        Regularization.org_id == org, Regularization.employee_id == uuid.UUID(e["id"]),
        Regularization.work_date >= start, Regularization.work_date <= end))).scalars().all()
    reg_by_day = {r.work_date: r for r in sorted(regs, key=lambda r: r.created_at)}
    perms = (await db.execute(select(Permission).where(
        Permission.org_id == org, Permission.employee_id == uuid.UUID(e["id"]),
        Permission.work_date >= start, Permission.work_date <= end,
        Permission.status.in_(["pending", "approved"])))).scalars().all()
    perm_by_day = {p.work_date: p for p in sorted(perms, key=lambda p: p.created_at)}
    for h in await _optional_taken(request, e["id"], start, end):
        holidays[date.fromisoformat(h["holiday_date"][:10])] = f"{h.get('name') or 'Optional holiday'} (optional)"
    leave_by_day = L.leave_days(await _leave_rows(request, e["id"]), start, end)

    days = []
    d = start
    while d <= end:
        planned, workday, p_start, brk, tz = _plan_for(e, sched, default, d)
        actual, first_in, last_out, late_min, status = _derive(
            by_day.get(d, []), planned, workday, p_start, brk, tz, d in holidays, d < today)
        reg = reg_by_day.get(d)
        days.append(L.fold_day(
            d, today=today, planned=planned, workday=workday, actual=actual, status=status, late_min=late_min,
            first_in=_local_time(first_in, tz).strftime("%H:%M") if first_in else None,
            last_out=_local_time(last_out, tz).strftime("%H:%M") if last_out else None,
            holiday=holidays.get(d), leave=leave_by_day.get(d),
            shift={"name": (sched or {}).get("name") or "Standard", "start": p_start,
                   "end": (sched or {}).get("end_time") or ""} if workday else None,
            correction={"id": str(reg.id), "status": reg.status, "reason": reg.reason} if reg else None,
            permission=_perm_cell(perm_by_day.get(d))))
        d += timedelta(days=1)
    return {"employee": {"id": e["id"], "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                         "employee_number": e.get("employee_number")},
            "month": start.strftime("%Y-%m"), "start": start.isoformat(), "end": end.isoformat(),
            "today": today.isoformat(), "shift": {"name": (sched or {}).get("name") or "Standard",
                                                  "start": (sched or {}).get("start_time"), "end": (sched or {}).get("end_time")},
            "kpis": L.kpis(days), "days": days}


async def _optional_taken(request: Request, employee_id: str, start: date, end: date) -> list[dict]:
    """Optional holidays this employee picked and got approved (leave service). Never raises."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.LEAVE_URL}/leave/optional-holidays/taken", headers=_fwd(request),
                            params={"employee_id": employee_id, "start": start.isoformat(), "end": end.isoformat()})
            if r.status_code == 200 and isinstance(r.json(), list):
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


# ══════════════════════════ Permission (short hours) ═══════════════════════════

def _perm_out(p: Permission) -> dict:
    return {"id": str(p.id), "employee_id": str(p.employee_id), "employee_name": p.employee_name,
            "work_date": p.work_date.isoformat(), "from_time": p.from_time, "to_time": p.to_time,
            "hours": float(p.hours or 0), "reason": p.reason, "status": p.status, "review_note": p.review_note,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None}


def _perm_cell(p: Permission | None) -> dict | None:
    return None if not p else {"id": str(p.id), "status": p.status, "from_time": p.from_time, "to_time": p.to_time,
                               "hours": float(p.hours or 0)}


async def _policy(db, org) -> dict:
    row = (await db.execute(select(TimeEssPolicy).where(TimeEssPolicy.org_id == org))).scalar_one_or_none()
    if not row:
        return {**L.DEFAULT_PERMISSION_POLICY, "source": "default"}
    return {"max_per_month": int(row.permission_max_per_month or 0), "max_hours": float(row.permission_max_hours or 0),
            "source": "company", "updated_by": row.updated_by}


async def _used_in_month(db, org, employee_id: str, d: date) -> int:
    start, end = L.month_bounds(d.strftime("%Y-%m"))
    rows = (await db.execute(select(Permission).where(
        Permission.org_id == org, Permission.employee_id == uuid.UUID(employee_id),
        Permission.work_date >= start, Permission.work_date <= end,
        Permission.status.in_(["pending", "approved"])))).scalars().all()
    return len(rows)


@perm_router.get("/policy")
async def permission_policy(request: Request, month: str | None = None, db: AsyncSession = Depends(get_db)):
    """Company policy + how many the CALLER used in ``month`` (default this month)."""
    org, me = _org(request), _emp(request)
    pol = await _policy(db, org)
    d = L.month_bounds(month)[0] if month else await _today(org)
    pol["used_this_month"] = await _used_in_month(db, org, me, d) if me else 0
    return pol


class PolicyIn(BaseModel):
    max_per_month: int = Field(ge=0, le=31)
    max_hours: float = Field(ge=0, le=8)


@perm_router.put("/policy")
async def set_permission_policy(body: PolicyIn, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in HR:
        raise HTTPException(403, "Only HR can change the permission policy")
    org = _org(request)
    row = (await db.execute(select(TimeEssPolicy).where(TimeEssPolicy.org_id == org))).scalar_one_or_none()
    if not row:
        row = TimeEssPolicy(org_id=uuid.UUID(org))
        db.add(row)
    row.permission_max_per_month, row.permission_max_hours = body.max_per_month, body.max_hours
    row.updated_by = request.headers.get("x-user-email") or ""
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return await _policy(db, org)


class PermissionIn(BaseModel):
    work_date: str
    from_time: str
    to_time: str
    reason: str = Field(min_length=3, max_length=500)
    employee_id: str | None = None          # HR on behalf; an employee always files for themselves


@perm_router.post("", status_code=201)
async def request_permission(body: PermissionIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, role, me = _org(request), _role(request), _emp(request)
    target = body.employee_id or me
    if not target or not L.may_request_for(role, me, target):
        raise HTTPException(403, "You can only ask for permission for yourself")
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(target)
    if not e:
        raise HTTPException(404, "Employee not found")
    try:
        wd = date.fromisoformat(body.work_date)
    except ValueError:
        raise HTTPException(422, "work_date must be YYYY-MM-DD")
    try:
        hours = L.hours_between(body.from_time, body.to_time)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    pol = await _policy(db, org)
    why = L.permission_check(pol, hours=hours, used_this_month=await _used_in_month(db, org, target, wd))
    if why:
        raise HTTPException(409, why)
    p = Permission(org_id=uuid.UUID(org), employee_id=uuid.UUID(target),
                   employee_name=f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                   manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None,
                   work_date=wd, from_time=body.from_time, to_time=body.to_time, hours=hours, reason=body.reason,
                   created_by=uuid.UUID(request.headers["x-user-id"]) if request.headers.get("x-user-id") else None,
                   status="pending", created_at=datetime.now(timezone.utc))
    db.add(p)
    await db.commit()
    await approvals_client.open_item(
        org, kind="permission", ref_id=str(p.id), title=f"Permission — {p.employee_name}",
        summary=[f"{wd.strftime('%d %b %Y')} · {p.from_time}–{p.to_time} ({hours:g} h)", f"Reason: {body.reason}"],
        requester_user_id=request.headers.get("x-user-id"), approver_roles=["org_admin", "hr_admin", "hr_manager", "hr"],
        approver_manager_of_employee_id=target, link="/attendance",
        callback_path=f"/time/permissions/{p.id}/{{decision}}",
        context={"hours": hours, "department": e.get("department") or "", "employee_id": target})
    return _perm_out(p)


@perm_router.get("")
async def list_permissions(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    """HR: the org · manager: their reports · employee: their own."""
    org, role, me = _org(request), _role(request), _emp(request)
    q = select(Permission).where(Permission.org_id == org)
    if status:
        q = q.where(Permission.status == status)
    if role == "manager" and me:
        q = q.where(Permission.manager_id == uuid.UUID(me))
    elif role not in HR:
        if not me:
            return []
        q = q.where(Permission.employee_id == uuid.UUID(me))
    return [_perm_out(p) for p in (await db.execute(q.order_by(Permission.created_at.desc()))).scalars().all()]


async def _perm_or_404(db, org, pid) -> Permission:
    try:
        pid = uuid.UUID(str(pid))
    except ValueError:
        raise HTTPException(404, "Permission not found")
    p = (await db.execute(select(Permission).where(Permission.id == pid, Permission.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Permission not found")
    return p


class DecideIn(BaseModel):
    comment: str | None = None
    note: str | None = None
    decision: str | None = None


async def _decide(pid: str, verdict: str, request: Request, body: DecideIn | None, db) -> dict:
    org, role, me = _org(request), _role(request), _emp(request)
    p = await _perm_or_404(db, org, pid)
    if not L.may_decide(role, me, str(p.employee_id), str(p.manager_id) if p.manager_id else None):
        raise HTTPException(403, "Only this employee's manager or HR can decide a permission")
    if p.status != "pending":
        raise HTTPException(409, f"This permission is already {p.status}")
    uid = request.headers.get("x-user-id")
    p.status, p.reviewed_at = verdict, datetime.now(timezone.utc)
    p.reviewed_by = uuid.UUID(uid) if uid else None
    p.review_note = (body.comment or body.note) if body else None
    await db.commit()
    await approvals_client.close_item(org, kind="permission", ref_id=str(p.id), decision=verdict, actor_user_id=uid)
    return _perm_out(p)


@perm_router.post("/{pid}/approve")
async def approve_permission(pid: str, request: Request, body: DecideIn | None = None, db: AsyncSession = Depends(get_db)):
    return await _decide(pid, "approved", request, body, db)


@perm_router.post("/{pid}/reject")
async def reject_permission(pid: str, request: Request, body: DecideIn | None = None, db: AsyncSession = Depends(get_db)):
    return await _decide(pid, "rejected", request, body, db)


@perm_router.post("/{pid}/cancel")
async def cancel_permission(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The employee withdraws a pending permission (HR may cancel any)."""
    org, role, me = _org(request), _role(request), _emp(request)
    p = await _perm_or_404(db, org, pid)
    if str(p.employee_id) != (me or "") and role not in HR:
        raise HTTPException(403, "Only the requester or HR can cancel")
    if p.status != "pending":
        raise HTTPException(409, f"Only a pending permission can be withdrawn (this one is {p.status})")
    p.status = "cancelled"
    await db.commit()
    await approvals_client.close_item(org, kind="permission", ref_id=str(p.id), decision="withdrawn",
                                      actor_user_id=request.headers.get("x-user-id"))
    return _perm_out(p)
