"""Leave service — fiscal-year balances, requests, and manager-routed approval.

Manager identity: the gateway stamps X-Employee-Id (the employee record the login
maps to). A manager's queue = requests whose employee's manager_id == that id;
HR/admin see the whole org. Approving a request moves days pending → used on the
matching fiscal-year balance.
"""
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
import asyncio  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.email import brand_html, plain, send_email, strong  # noqa: E402
from shared.utils.org_time import local_today  # noqa: E402

from database import get_db  # noqa: E402
from models import LeaveBalance, LeaveRequest  # noqa: E402


async def _notify_leave_decision(request, r):
    """Best-effort email the employee their leave decision (never raises/blocks)."""
    try:
        emp = (await _employees(request)).get(str(r.employee_id), {})
        to = emp.get("work_email") or emp.get("email")
        if not to:
            return
        lines = [f"Hi {((r.employee_name or 'there').split() or ['there'])[0]},",
                 (f"Your {r.leave_type_name or r.leave_type_code} request for "
                  f"{r.start_date} to {r.end_date} ({float(r.days):g} day(s)) has been ",
                  strong(r.status), f" by {r.reviewed_by_name}." if r.reviewed_by_name else ".")]
        if r.review_note:
            lines.append(f"Note: {r.review_note}")
        html = brand_html(f"Leave {r.status}", lines)   # brand_html escapes names / note
        text = "\n".join(plain(ln) for ln in lines)
        asyncio.create_task(send_email(to, f"Your leave request was {r.status}", text, html=html))
    except Exception:
        pass

router = APIRouter(prefix="/leave", tags=["leave"])

ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
APPROVER_ROLES = ADMIN_ROLES | {"manager"}


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _uid(request: Request) -> str | None:
    return request.headers.get("x-user-id")


def _emp(request: Request) -> str | None:
    """The employee record the logged-in user maps to (for manager scoping)."""
    return request.headers.get("x-employee-id")


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def _name(request: Request) -> str | None:
    return request.headers.get("x-user-email")


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id")
            if request.headers.get(k)}


def _branch(request: Request) -> str | None:
    """Resolved branch scope (gateway-stamped). Non-empty → restrict to that branch."""
    return request.headers.get("x-branch-id") or None


async def _get(request: Request, path: str) -> list | dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}{path}", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _employees(request: Request) -> dict:
    return {e["id"]: e for e in await _get(request, "/employees")}


async def _leave_types(request: Request) -> list:
    rows = await _get(request, "/masters/leave-types")
    return rows if isinstance(rows, list) else []


async def _fiscal_years(request: Request) -> list:
    rows = await _get(request, "/masters/fiscal-years")
    return rows if isinstance(rows, list) else []


async def _stat_holidays(request: Request) -> list:
    rows = await _get(request, "/masters/stat-holidays")
    return rows if isinstance(rows, list) else []


def _resolve_fy(fiscal_years: list, d: date) -> int:
    """Fiscal year an leave date falls in — from the FiscalYear master's window,
    else the calendar year."""
    for fy in fiscal_years:
        sd, ed = fy.get("start_date"), fy.get("end_date")
        if sd and ed and sd[:10] <= d.isoformat() <= ed[:10]:
            try:
                return int(fy.get("code") or d.year)
            except (TypeError, ValueError):
                return d.year
    return d.year


def _current_fy(fiscal_years: list) -> int:
    cur = next((fy for fy in fiscal_years if fy.get("is_current")), None)
    if cur:
        try:
            return int(cur.get("code") or local_today().year)
        except (TypeError, ValueError):
            pass
    return 2026


def _working_days(start: date, end: date, half_day: bool, holidays: set[date]) -> float:
    if end < start:
        raise HTTPException(400, "end_date before start_date")
    if half_day and start == end:
        return 0.5
    days = 0
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:   # Mon–Fri, excluding stat holidays
            days += 1
        d += timedelta(days=1)
    return float(days)


async def _reports(request: Request, emps: dict) -> set:
    """Employee ids managed by the logged-in user (their direct reports)."""
    me = _emp(request)
    if not me:
        return set()
    return {eid for eid, e in emps.items() if str(e.get("manager_id")) == me}


async def _branch_emp_ids(request: Request) -> set | None:
    """Employee UUIDs in the caller's branch scope (own-table rows filter to these).
    None = org-wide. Employees are already branch-scoped by the gateway."""
    if not _branch(request):
        return None
    return {uuid.UUID(i) for i in (await _employees(request)).keys()}


# ══════════════════════════ Leave types + balances ═════════════════════════════

@router.get("/types")
async def leave_types(request: Request):
    _org(request)
    return await _leave_types(request)


# Leave types that are granted for a life event, not taken at will. HR can mark any type
# "event" in Master Data (master_leave_types.category); these codes are the fallback.
EVENT_CODES = {"MAT", "ML", "PAT", "PL_PAT", "ADOPT", "ADP", "BRV", "BEREAVEMENT", "MARRIAGE", "MAR", "MISCARRIAGE", "SURR",
               "PREG", "PAR", "PARENTAL", "CCL", "COMPASSIONATE"}   # Canada: pregnancy / parental / compassionate care


def leave_categories(types: list) -> dict:
    """{code: 'usable' | 'event'} from the leave-type master (only explicit values)."""
    return {t.get("code"): (t.get("category") or "").lower() for t in types or []
            if t.get("code") and (t.get("category") or "").lower() in ("usable", "event")}


def category_of(code: str, paid: bool, cats: dict) -> str:
    """usable | event | unpaid."""
    if not paid:
        return "unpaid"
    if code in cats:
        return cats[code]
    return "event" if (code or "").upper() in EVENT_CODES else "usable"


def balance_totals(rows: list[dict]) -> dict:
    usable = [r for r in rows if r.get("category") == "usable"]
    return {"usable_remaining": round(sum(r["remaining"] for r in usable), 2),
            "usable_entitled": round(sum(r["entitled"] for r in usable), 2),
            "usable_used": round(sum(r["used"] for r in usable), 2),
            "usable_pending": round(sum(r["pending"] for r in usable), 2),
            "event": [{"code": r["leave_type_code"], "name": r["leave_type_name"], "remaining": r["remaining"],
                       "entitled": r["entitled"]} for r in rows if r.get("category") == "event"]}


def _bal_out(b: LeaveBalance) -> dict:
    alloc = float(b.allocated) + float(b.carried_over)
    remaining = round(alloc - float(b.used) - float(b.pending), 2)
    return {"id": str(b.id), "employee_id": str(b.employee_id), "fiscal_year": b.fiscal_year,
            "leave_type_code": b.leave_type_code, "leave_type_name": b.leave_type_name,
            "paid": b.paid, "allocated": float(b.allocated), "carried_over": float(b.carried_over),
            "entitled": round(alloc, 2), "used": float(b.used), "pending": float(b.pending),
            "remaining": remaining}


async def _ensure_balance(db, org, emp: dict, fy: int, lt: dict) -> LeaveBalance:
    b = (await db.execute(select(LeaveBalance).where(
        LeaveBalance.org_id == org, LeaveBalance.employee_id == emp["id"],
        LeaveBalance.fiscal_year == fy, LeaveBalance.leave_type_code == lt["code"]))).scalar_one_or_none()
    if b:
        return b
    b = LeaveBalance(org_id=org, employee_id=uuid.UUID(emp["id"]), fiscal_year=fy,
                     leave_type_code=lt["code"], leave_type_name=lt.get("name"),
                     paid=bool(lt.get("paid", True)), allocated=float(lt.get("days_per_year") or 0),
                     carried_over=0, used=0, pending=0)
    db.add(b)
    return b


class AllocateIn(BaseModel):
    fiscal_year: int | None = None
    employee_ids: list[str] | None = None       # None = the whole org


@router.post("/balances/allocate", status_code=201)
async def allocate_balances(body: AllocateIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Grant each (employee × leave type) its fiscal-year entitlement. Idempotent:
    an employee who already has a balance for a type/year is left untouched. Carries
    over unused paid days from the prior year up to the type's carryover_max."""
    if _role(request) not in ADMIN_ROLES:
        raise HTTPException(403, "Only HR/admin can allocate leave")
    org = _org(request)
    fys = await _fiscal_years(request)
    fy = body.fiscal_year or _current_fy(fys)
    types = [t for t in await _leave_types(request) if t.get("code")]
    emps = await _employees(request)
    targets = [emps[i] for i in (body.employee_ids or list(emps)) if i in emps]

    created = 0
    for e in targets:
        if e.get("status") == "terminated":
            continue
        for lt in types:
            exists = (await db.execute(select(LeaveBalance).where(
                LeaveBalance.org_id == org, LeaveBalance.employee_id == e["id"],
                LeaveBalance.fiscal_year == fy,
                LeaveBalance.leave_type_code == lt["code"]))).scalar_one_or_none()
            if exists:
                continue
            # carry over remaining paid days from the prior year (capped)
            carry = 0.0
            cap = float(lt.get("carryover_max") or 0)
            if cap > 0:
                prior = (await db.execute(select(LeaveBalance).where(
                    LeaveBalance.org_id == org, LeaveBalance.employee_id == e["id"],
                    LeaveBalance.fiscal_year == fy - 1,
                    LeaveBalance.leave_type_code == lt["code"]))).scalar_one_or_none()
                if prior:
                    rem = float(prior.allocated) + float(prior.carried_over) - float(prior.used) - float(prior.pending)
                    carry = round(min(max(rem, 0.0), cap), 2)
            db.add(LeaveBalance(
                org_id=org, employee_id=uuid.UUID(e["id"]), fiscal_year=fy,
                leave_type_code=lt["code"], leave_type_name=lt.get("name"),
                paid=bool(lt.get("paid", True)), allocated=float(lt.get("days_per_year") or 0),
                carried_over=carry, used=0, pending=0))
            created += 1
    await db.commit()
    return {"fiscal_year": fy, "employees": len(targets), "balances_created": created}


@router.get("/balances")
async def list_balances(request: Request, employee_id: str | None = None,
                        fiscal_year: int | None = None, db: AsyncSession = Depends(get_db)):
    """Balances for one employee (defaults to the caller's own record). Managers
    and HR/admin may query anyone; an employee only sees their own."""
    org = _org(request)
    role = _role(request)
    target = employee_id or _emp(request)
    if not target:
        raise HTTPException(400, "No employee context — pass employee_id")
    if role not in APPROVER_ROLES and target != _emp(request):
        raise HTTPException(403, "You can only view your own balances")
    fys = await _fiscal_years(request)
    fy = fiscal_year or _current_fy(fys)
    rows = (await db.execute(select(LeaveBalance).where(
        LeaveBalance.org_id == org, LeaveBalance.employee_id == target,
        LeaveBalance.fiscal_year == fy).order_by(LeaveBalance.leave_type_name))).scalars().all()
    # 2026-10-16: one total for every screen — usable types only; event leaves (maternity,
    # paternity …) are listed separately and never summed into "days you can take".
    cats = leave_categories(await _leave_types(request))
    out = []
    for b in rows:
        o = _bal_out(b)
        o["category"] = category_of(b.leave_type_code, b.paid, cats)
        out.append(o)
    return {"employee_id": target, "fiscal_year": fy, "balances": out, "totals": balance_totals(out)}


# ══════════════════════════ Requests + approval ════════════════════════════════

def _req_out(r: LeaveRequest) -> dict:
    return {"id": str(r.id), "employee_id": str(r.employee_id), "employee_name": r.employee_name,
            "employee_number": r.employee_number,
            "manager_id": str(r.manager_id) if r.manager_id else None, "manager_name": r.manager_name,
            "fiscal_year": r.fiscal_year, "leave_type_code": r.leave_type_code,
            "leave_type_name": r.leave_type_name,
            "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(),
            "days": float(r.days), "half_day": r.half_day, "reason": r.reason, "status": r.status,
            "reviewed_by_name": r.reviewed_by_name,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_note": r.review_note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            # 2026-10-16 employee portal
            "half_session": r.half_session, "cc_employee_ids": r.cc_employee_ids or [],
            "contact_phone": r.contact_phone, "contact_note": r.contact_note,
            "attachment_id": str(r.attachment_id) if r.attachment_id else None,
            "cancel_reason": r.cancel_reason,
            "cancel_requested_at": r.cancel_requested_at.isoformat() if r.cancel_requested_at else None}


class RequestIn(BaseModel):
    employee_id: str | None = None       # defaults to the caller's own record
    leave_type_code: str
    start_date: str
    end_date: str
    half_day: bool = False
    reason: str | None = None
    # 2026-10-16 employee portal
    half_session: str | None = None              # first | second  (with half_day)
    cc_employee_ids: list[str] | None = None     # colleagues to tell
    contact_phone: str | None = None             # reachable during leave
    contact_note: str | None = None
    attachment_id: str | None = None             # from POST /leave/attachments


@router.post("/requests", status_code=201)
async def create_request(body: RequestIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emps = await _employees(request)
    eid = body.employee_id or _emp(request)
    if not eid or eid not in emps:
        raise HTTPException(400, "Unknown employee — no employee context")
    # employees may only file for themselves; managers/HR may file on behalf
    if _role(request) not in APPROVER_ROLES and eid != _emp(request):
        raise HTTPException(403, "You can only request leave for yourself")
    e = emps[eid]

    try:
        sd = date.fromisoformat(body.start_date)
        ed = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")

    lt = next((t for t in await _leave_types(request) if t.get("code") == body.leave_type_code), None)
    if not lt:
        raise HTTPException(404, f"Unknown leave type '{body.leave_type_code}'")

    prov = e.get("province_of_employment")
    holidays = {date.fromisoformat(h["holiday_date"][:10]) for h in await _stat_holidays(request)
                if h.get("holiday_date") and h.get("province") in (None, prov)}
    days = _working_days(sd, ed, body.half_day, holidays)
    if days <= 0:
        raise HTTPException(400, "No working days in that range (weekends/holidays only)")

    # 2026-10-16 employee portal: half session, CC, contact, attachment rule (per leave type)
    import ess_leave_logic as EL
    people = emps
    if body.cc_employee_ids:                      # an employee's /employees is just themself → use the directory
        d = await _get(request, "/employees/directory")
        people = {x["id"]: x for x in d} if isinstance(d, list) else emps
    problem = EL.validate_apply(lt, days=days, half_day=body.half_day, half_session=body.half_session,
                                attachment_id=body.attachment_id, cc=body.cc_employee_ids or [], emps=people, me=eid)
    if problem:
        raise HTTPException(422, problem)
    if body.attachment_id:
        import ess_leave
        await ess_leave.check_attachment(db, org, eid, body.attachment_id)

    fys = await _fiscal_years(request)
    fy = _resolve_fy(fys, sd)

    # reserve the days against the balance (create it on demand)
    bal = await _ensure_balance(db, org, e, fy, lt)
    if bal.paid:
        remaining = float(bal.allocated) + float(bal.carried_over) - float(bal.used) - float(bal.pending)
        if days > remaining + 1e-6:
            raise HTTPException(409, f"Insufficient {lt.get('name')} balance: {remaining} day(s) left, {days} requested")
        bal.pending = round(float(bal.pending) + days, 2)

    r = LeaveRequest(
        org_id=org, employee_id=uuid.UUID(eid),
        employee_name=f"{e['first_name']} {e['last_name']}", employee_number=e.get("employee_number"),
        manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None,
        manager_name=e.get("manager_name"), fiscal_year=fy,
        leave_type_code=lt["code"], leave_type_name=lt.get("name"),
        start_date=sd, end_date=ed, days=days, half_day=body.half_day, reason=body.reason,
        half_session=(body.half_session or "first") if body.half_day else None,
        cc_employee_ids=list(dict.fromkeys(body.cc_employee_ids or [])) or None,
        contact_phone=(body.contact_phone or "").strip()[:30] or None,
        contact_note=(body.contact_note or "").strip()[:300] or None,
        attachment_id=uuid.UUID(body.attachment_id) if body.attachment_id else None)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    if r.cc_employee_ids:
        from shared.utils.notify import notify
        await notify(org, employee_ids=[str(x) for x in r.cc_employee_ids], kind="leave", email=False,
                     title=f"FYI: {r.employee_name} is on {r.leave_type_name or 'leave'} {r.start_date:%d %b} – {r.end_date:%d %b}",
                     body=(f"Contact during leave: {r.contact_phone}" if r.contact_phone else "") or (r.reason or ""),
                     link="/absence-calendar")
    return _req_out(r)


@router.get("/requests")
async def list_requests(request: Request, status: str | None = None, employee_id: str | None = None,
                        scope: str | None = None, db: AsyncSession = Depends(get_db)):
    """scope: mine | team | org. Default = org for HR/admin, team for managers,
    mine for everyone else."""
    org = _org(request)
    role = _role(request)
    emps = await _employees(request)
    q = select(LeaveRequest).where(LeaveRequest.org_id == org)
    if status:
        q = q.where(LeaveRequest.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if employee_id:
        q = q.where(LeaveRequest.employee_id == employee_id)

    scope = scope or ("org" if role in ADMIN_ROLES else "team" if role == "manager" else "mine")
    if scope == "mine" and _emp(request):
        q = q.where(LeaveRequest.employee_id == _emp(request))
    elif scope == "team":
        reports = await _reports(request, emps)
        q = q.where(LeaveRequest.employee_id.in_(reports or {"00000000-0000-0000-0000-000000000000"}))
    elif scope == "org" and role not in ADMIN_ROLES:
        raise HTTPException(403, "Org-wide leave is HR/admin only")

    bids = await _branch_emp_ids(request)
    if bids is not None:
        q = q.where(LeaveRequest.employee_id.in_(bids))
    q = q.order_by(LeaveRequest.created_at.desc())
    return [_req_out(r) for r in (await db.execute(q)).scalars().all()]


async def _req_or_404(db, org, rid) -> LeaveRequest:
    r = (await db.execute(select(LeaveRequest).where(
        LeaveRequest.id == rid, LeaveRequest.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Leave request not found")
    return r


async def _balance_for(db, org, r: LeaveRequest) -> LeaveBalance | None:
    return (await db.execute(select(LeaveBalance).where(
        LeaveBalance.org_id == org, LeaveBalance.employee_id == r.employee_id,
        LeaveBalance.fiscal_year == r.fiscal_year,
        LeaveBalance.leave_type_code == r.leave_type_code))).scalar_one_or_none()


def _can_approve(request: Request, r: LeaveRequest) -> bool:
    role = _role(request)
    if role in ADMIN_ROLES:
        return True
    if role == "manager" and _emp(request) and str(r.manager_id) == _emp(request):
        return True
    return False


class ReviewIn(BaseModel):
    note: str | None = None


@router.post("/requests/{rid}/approve")
async def approve_request(rid: str, request: Request, body: ReviewIn = ReviewIn(),
                          db: AsyncSession = Depends(get_db)):
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    if not _can_approve(request, r):
        raise HTTPException(403, "Only this employee's manager or HR can approve")
    if r.status != "pending":
        raise HTTPException(409, f"Cannot approve a {r.status} request")
    bal = await _balance_for(db, org, r)
    if bal and bal.paid:
        bal.pending = round(max(0.0, float(bal.pending) - float(r.days)), 2)
        bal.used = round(float(bal.used) + float(r.days), 2)
    r.status = "approved"
    r.reviewed_by = _uid(request)
    r.reviewed_by_name = _name(request)
    r.reviewed_at = _now()
    r.review_note = body.note
    await db.commit()
    await db.refresh(r)
    await _notify_leave_decision(request, r)
    await _g3_emit_leave(org, "leave.approved", r)
    return _req_out(r)


async def _g3_emit_leave(org, event: str, r) -> None:
    """G3: outbound webhooks (best-effort, never raises)."""
    try:
        from shared.utils.events import emit
        await emit(org, event, {"request_id": str(r.id), "employee_id": str(r.employee_id),
                                "employee_number": r.employee_number, "leave_type": r.leave_type_code,
                                "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(),
                                "days": float(r.days or 0)})
    except Exception:  # noqa: BLE001
        pass


@router.post("/requests/{rid}/reject")
async def reject_request(rid: str, request: Request, body: ReviewIn = ReviewIn(),
                         db: AsyncSession = Depends(get_db)):
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    if not _can_approve(request, r):
        raise HTTPException(403, "Only this employee's manager or HR can reject")
    if r.status != "pending":
        raise HTTPException(409, f"Cannot reject a {r.status} request")
    bal = await _balance_for(db, org, r)
    if bal and bal.paid:
        bal.pending = round(max(0.0, float(bal.pending) - float(r.days)), 2)
    r.status = "rejected"
    r.reviewed_by = _uid(request)
    r.reviewed_by_name = _name(request)
    r.reviewed_at = _now()
    r.review_note = body.note
    await db.commit()
    await db.refresh(r)
    await _notify_leave_decision(request, r)
    await _g3_emit_leave(org, "leave.rejected", r)
    return _req_out(r)


@router.post("/requests/{rid}/cancel")
async def cancel_request(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Employee withdraws their own request (or HR cancels). Releases reserved days."""
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    is_owner = _emp(request) and str(r.employee_id) == _emp(request)
    if not is_owner and _role(request) not in ADMIN_ROLES:
        raise HTTPException(403, "Only the requester or HR can cancel")
    if r.status not in ("pending", "approved"):
        raise HTTPException(409, f"Cannot cancel a {r.status} request")
    if r.status == "approved" and _role(request) not in ADMIN_ROLES:
        # 2026-10-16: an APPROVED leave is not undone by the employee alone — it goes to the approver
        import ess_leave
        return await ess_leave.start_cancellation(request, db, r, reason=None)
    bal = await _balance_for(db, org, r)
    if bal and bal.paid:
        if r.status == "pending":
            bal.pending = round(max(0.0, float(bal.pending) - float(r.days)), 2)
        elif r.status == "approved":
            bal.used = round(max(0.0, float(bal.used) - float(r.days)), 2)
    r.status = "cancelled"
    r.reviewed_at = _now()
    await db.commit()
    await db.refresh(r)
    return _req_out(r)


@router.get("/approvals")
async def approvals_queue(request: Request, db: AsyncSession = Depends(get_db)):
    """A manager/HR's pending leave requests, scoped to their reports (managers)
    or the whole org (HR/admin)."""
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Approvals are for managers and HR")
    org = _org(request)
    emps = await _employees(request)
    q = select(LeaveRequest).where(LeaveRequest.org_id == org, LeaveRequest.status == "pending")
    if _role(request) == "manager":
        reports = await _reports(request, emps)
        q = q.where(LeaveRequest.employee_id.in_(reports or {"00000000-0000-0000-0000-000000000000"}))
    bids = await _branch_emp_ids(request)
    if bids is not None:
        q = q.where(LeaveRequest.employee_id.in_(bids))
    q = q.order_by(LeaveRequest.start_date)
    rows = [_req_out(r) for r in (await db.execute(q)).scalars().all()]
    return {"pending": rows, "pending_total": len(rows),
            "days_total": round(sum(x["days"] for x in rows), 2)}


@router.get("/summary")
async def summary(request: Request, fiscal_year: int | None = None, db: AsyncSession = Depends(get_db)):
    """Org-level leave picture for HR/admin dashboards."""
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Leave summary is for managers and HR")
    org = _org(request)
    fys = await _fiscal_years(request)
    fy = fiscal_year or _current_fy(fys)
    reqs = (await db.execute(select(LeaveRequest).where(
        LeaveRequest.org_id == org, LeaveRequest.fiscal_year == fy))).scalars().all()
    by_status: dict = {}
    by_type: dict = {}
    for r in reqs:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_type[r.leave_type_name or r.leave_type_code] = round(
            by_type.get(r.leave_type_name or r.leave_type_code, 0) + float(r.days), 2)
    bals = (await db.execute(select(LeaveBalance).where(
        LeaveBalance.org_id == org, LeaveBalance.fiscal_year == fy))).scalars().all()
    allocated = round(sum(float(b.allocated) + float(b.carried_over) for b in bals), 2)
    used = round(sum(float(b.used) for b in bals), 2)
    return {"fiscal_year": fy, "requests": len(reqs), "by_status": by_status,
            "days_by_type": by_type, "employees_with_balances": len({str(b.employee_id) for b in bals}),
            "total_allocated_days": allocated, "total_used_days": used,
            "pending": by_status.get("pending", 0)}


@router.get("/alerts")
async def alerts(request: Request, fiscal_year: int | None = None, db: AsyncSession = Depends(get_db)):
    """Balance & approval alerts — the data the leave_balance_guard watchdog polls:
    over-drawn (negative) or fully-depleted paid balances, plus stale pending
    approvals sitting too long."""
    org = _org(request)
    fys = await _fiscal_years(request)
    fy = fiscal_year or _current_fy(fys)
    names = await _employees(request)
    bals = (await db.execute(select(LeaveBalance).where(
        LeaveBalance.org_id == org, LeaveBalance.fiscal_year == fy))).scalars().all()
    negative, depleted = [], []
    for b in bals:
        if not b.paid:
            continue
        entitled = float(b.allocated) + float(b.carried_over)
        remaining = round(entitled - float(b.used) - float(b.pending), 2)
        e = names.get(str(b.employee_id), {})
        row = {"employee_id": str(b.employee_id),
               "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or "—",
               "leave_type": b.leave_type_name, "remaining": remaining, "entitled": entitled}
        if remaining < -1e-6:
            negative.append(row)
        elif entitled > 0 and remaining <= 0:
            depleted.append(row)
    # stale pending approvals (submitted > 7 days ago)
    cutoff = _now() - timedelta(days=7)
    stale = (await db.execute(select(LeaveRequest).where(
        LeaveRequest.org_id == org, LeaveRequest.status == "pending",
        LeaveRequest.created_at < cutoff))).scalars().all()
    return {"fiscal_year": fy, "count": len(negative) + len(depleted),
            "negative": negative, "depleted": depleted,
            "stale_approvals": len(stale)}
