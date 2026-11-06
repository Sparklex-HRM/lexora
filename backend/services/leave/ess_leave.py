"""Employee portal (2026-10-16) — leave self-service routes.

  POST /leave/attachments                                   upload a supporting document (own) → id
  GET  /leave/requests/{rid}/attachment                     download (requester, their manager, HR)
  POST /leave/requests/{rid}/request-cancel                 approved leave → cancellation request → approver
  POST /leave/requests/{rid}/cancel-decision/{approve|reject}   (inbox callback)
  GET|PUT /leave/ess-policy                                 company policy: optional holidays / comp-off
  GET  /leave/optional-holidays                             my province's optional holidays + my picks + quota
  POST /leave/optional-holidays/{holiday_id}/pick           → approval chain (kind optional_holiday)
  GET  /leave/optional-holidays/picks                       HR: all · manager: team · employee: own
  POST /leave/optional-holidays/picks/{pid}/{approve|reject|cancel}
  GET  /leave/optional-holidays/taken                       approved picks of one employee (attendance calendar)
  POST /leave/comp-off                                      claim for a worked holiday / weekly off (punches checked)
  GET  /leave/comp-off                                      HR: all · manager: team · employee: own
  POST /leave/comp-off/{cid}/{approve|reject|cancel}        approve credits COMP with an expiry date

Every decision goes through the org's D1 approval chain (``open_item(context=…)``); the owning
callbacks below are what the inbox calls on the LAST step.
"""
import sys
import uuid
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import approvals_client  # noqa: E402
from shared.utils import docstore  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.docstore import StoredDocument  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import local_today  # noqa: E402

import ess_leave_logic as EL  # noqa: E402
from database import get_db  # noqa: E402
from models import LeaveBalance, LeaveRequest  # noqa: E402
from models_ess import CompOff, LeaveEssPolicy, OptionalHolidayPick  # noqa: E402
from routers import (  # noqa: E402
    _balance_for, _current_fy, _emp, _employees, _fiscal_years, _fwd, _leave_types, _org, _req_or_404,
    _req_out, _resolve_fy, _role, _stat_holidays,
)

router = APIRouter(prefix="/leave", tags=["leave-self-service"])
HR_INBOX = ["org_admin", "hr_admin", "hr_manager", "hr"]


def _now():
    return datetime.now(timezone.utc)


def _uid(request: Request) -> str | None:
    return request.headers.get("x-user-id")


def _name(e: dict) -> str:
    return f"{e.get('first_name', '')} {e.get('last_name', '')}".strip()


class DecideIn(BaseModel):
    comment: str | None = None
    note: str | None = None
    decision: str | None = None


def _comment(body: DecideIn | None) -> str | None:
    return (body.comment or body.note) if body else None


# ══════════════════════════ attachments ════════════════════════════════════════

async def check_attachment(db, org: str, employee_id: str, attachment_id: str) -> None:
    d = (await db.execute(select(StoredDocument).where(
        StoredDocument.id == uuid.UUID(str(attachment_id)), StoredDocument.org_id == uuid.UUID(org),
        StoredDocument.deleted_at.is_(None)))).scalar_one_or_none()
    if not d or str(d.employee_id) != str(employee_id):
        raise HTTPException(422, "The attachment was not found — upload it again")


@router.post("/attachments", status_code=201)
async def upload_attachment(request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    org, me = _org(request), _emp(request)
    if not me:
        raise HTTPException(400, "Your login is not linked to an employee record")
    data = await file.read(docstore.MAX_BYTES + 1)
    try:
        cols = docstore.check_upload(file.filename, data)
    except docstore.DocError as exc:
        raise HTTPException(exc.status, str(exc))
    d = StoredDocument(org_id=uuid.UUID(org), employee_id=uuid.UUID(me), doc_type="leave_attachment",
                       uploaded_by=_uid(request), uploaded_by_name=request.headers.get("x-user-email"), **cols)
    db.add(d)
    await db.commit()
    return docstore.doc_out(d)


@router.get("/requests/{rid}/attachment")
async def download_attachment(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    if not EL.may_view(_role(request), _emp(request), str(r.employee_id), str(r.manager_id) if r.manager_id else None):
        raise HTTPException(404, "Leave request not found")
    if not r.attachment_id:
        raise HTTPException(404, "This request has no attachment")
    d = (await db.execute(select(StoredDocument).where(StoredDocument.id == r.attachment_id,
                                                       StoredDocument.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "The attachment is no longer available")
    data = (await db.execute(select(StoredDocument.data).where(StoredDocument.id == d.id))).scalar_one()
    return Response(content=bytes(data), media_type=d.content_type, headers={
        "Content-Disposition": f'attachment; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


# ══════════════════════════ cancellation of approved leave ════════════════════

class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


async def start_cancellation(request: Request, db, r: LeaveRequest, reason: str | None) -> dict:
    """Approved leave → 'cancel_requested' + an inbox item for the approver. Days stay used until approved."""
    org = _org(request)
    if r.status == "cancel_requested":
        raise HTTPException(409, "A cancellation is already waiting for a decision")
    if r.status != "approved":
        raise HTTPException(409, f"Only approved leave needs a cancellation request (this one is {r.status})")
    r.status, r.cancel_reason, r.cancel_requested_at = "cancel_requested", (reason or "").strip() or None, _now()
    await db.commit()
    await db.refresh(r)
    span = f"{r.start_date:%d %b %Y}" + ("" if r.start_date == r.end_date else f" to {r.end_date:%d %b %Y}")
    await approvals_client.open_item(
        org, kind="leave_cancel", ref_id=str(r.id), title=f"Cancel leave — {r.employee_name}",
        summary=[f"{r.leave_type_name or r.leave_type_code} · {span} · {float(r.days):g} day(s)",
                 f"Reason: {r.cancel_reason}" if r.cancel_reason else "No reason given"],
        requester_user_id=_uid(request), approver_roles=HR_INBOX, approver_manager_of_employee_id=str(r.employee_id),
        link="/leave", callback_path=f"/leave/requests/{r.id}/cancel-decision/{{decision}}",
        context={"employee_id": str(r.employee_id), "leave_days": float(r.days or 0), "leave_type": r.leave_type_code})
    return _req_out(r)


@router.post("/requests/{rid}/request-cancel")
async def request_cancel(rid: str, body: CancelIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    if str(r.employee_id) != (_emp(request) or ""):
        raise HTTPException(403, "Only the person on leave can ask to cancel it")
    return await start_cancellation(request, db, r, body.reason)


@router.post("/requests/{rid}/cancel-decision/{verdict}")
async def cancel_decision(rid: str, verdict: str, request: Request, body: DecideIn | None = None,
                          db: AsyncSession = Depends(get_db)):
    if verdict not in ("approve", "reject"):
        raise HTTPException(404, "Unknown decision")
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    if not EL.may_decide(_role(request), _emp(request), str(r.employee_id), str(r.manager_id) if r.manager_id else None):
        raise HTTPException(403, "Only this employee's manager or HR can decide a cancellation")
    if r.status != "cancel_requested":
        raise HTTPException(409, f"No cancellation is waiting on this leave (status {r.status})")
    if verdict == "approve":
        bal = await _balance_for(db, org, r)
        if bal and bal.paid:
            bal.used = round(max(0.0, float(bal.used) - float(r.days)), 2)
        r.status = "cancelled"
    else:
        r.status = "approved"                       # the leave stands
    r.reviewed_at, r.review_note = _now(), _comment(body) or r.review_note
    await db.commit()
    await db.refresh(r)
    await approvals_client.close_item(org, kind="leave_cancel", ref_id=str(r.id),
                                      decision="approved" if verdict == "approve" else "rejected", actor_user_id=_uid(request))
    return _req_out(r)


# ══════════════════════════ policy ══════════════════════════════════════════════

async def policy_of(db, org) -> dict:
    row = (await db.execute(select(LeaveEssPolicy).where(LeaveEssPolicy.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not row:
        return {**EL.DEFAULT_POLICY, "source": "default"}
    return {"optional_holidays_per_year": int(row.optional_holidays_per_year or 0),
            "comp_off_full_day_hours": float(row.comp_off_full_day_hours or 0),
            "comp_off_half_day_hours": float(row.comp_off_half_day_hours or 0),
            "comp_off_expiry_days": int(row.comp_off_expiry_days or 0), "source": "company", "updated_by": row.updated_by}


class PolicyIn(BaseModel):
    optional_holidays_per_year: int = Field(ge=0, le=20)
    comp_off_full_day_hours: float = Field(ge=0, le=24)
    comp_off_half_day_hours: float = Field(ge=0, le=24)
    comp_off_expiry_days: int = Field(ge=0, le=730)


@router.get("/ess-policy")
async def get_policy(request: Request, db: AsyncSession = Depends(get_db)):
    return await policy_of(db, _org(request))


@router.put("/ess-policy")
async def put_policy(body: PolicyIn, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in EL.HR_ROLES:
        raise HTTPException(403, "Only HR can change the leave policy")
    org = _org(request)
    row = (await db.execute(select(LeaveEssPolicy).where(LeaveEssPolicy.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not row:
        row = LeaveEssPolicy(org_id=uuid.UUID(org))
        db.add(row)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    row.updated_by, row.updated_at = request.headers.get("x-user-email") or "", _now()
    await db.commit()
    return await policy_of(db, org)


# ══════════════════════════ optional holidays ══════════════════════════════════

def _pick_out(p: OptionalHolidayPick) -> dict:
    return {"id": str(p.id), "employee_id": str(p.employee_id), "employee_name": p.employee_name,
            "holiday_id": str(p.holiday_id), "holiday_date": p.holiday_date.isoformat(), "holiday_name": p.holiday_name,
            "year": p.year, "status": p.status, "review_note": p.review_note,
            "created_at": p.created_at.isoformat() if p.created_at else None}


async def _optional_for(request: Request, state: str | None) -> list[dict]:
    return [h for h in await _stat_holidays(request)
            if h.get("is_optional") and h.get("holiday_date") and h.get("province") in (None, "", state)]


@router.get("/optional-holidays")
async def my_optional_holidays(request: Request, year: int | None = None, db: AsyncSession = Depends(get_db)):
    org, me = _org(request), _emp(request)
    emps = await _employees(request)
    e = emps.get(me or "") or {}
    y = year or local_today().year
    pol = await policy_of(db, org)
    picks = [_pick_out(p) for p in (await db.execute(select(OptionalHolidayPick).where(
        OptionalHolidayPick.org_id == uuid.UUID(org), OptionalHolidayPick.employee_id == uuid.UUID(me))
        .order_by(OptionalHolidayPick.holiday_date))).scalars().all()] if me else []
    by_h = {p["holiday_id"]: p for p in picks if p["status"] in ("pending", "approved")}
    hols = sorted([{**h, "pick": by_h.get(h["id"])} for h in await _optional_for(request, e.get("province_of_employment"))
                   if str(h["holiday_date"])[:4] == str(y)], key=lambda h: h["holiday_date"])
    return {"year": y, "quota": EL.optional_quota(pol, picks, y), "holidays": hols, "picks": picks}


@router.post("/optional-holidays/{holiday_id}/pick", status_code=201)
async def pick_optional(holiday_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, me = _org(request), _emp(request)
    emps = await _employees(request)
    e = emps.get(me or "")
    if not e:
        raise HTTPException(400, "Your login is not linked to an employee record")
    h = next((x for x in await _optional_for(request, e.get("province_of_employment")) if x["id"] == holiday_id), None)
    if not h:
        raise HTTPException(404, "That optional holiday is not on your list")
    hd = date.fromisoformat(str(h["holiday_date"])[:10])
    if hd < local_today():
        raise HTTPException(409, "That holiday has already passed")
    mine = (await db.execute(select(OptionalHolidayPick).where(
        OptionalHolidayPick.org_id == uuid.UUID(org), OptionalHolidayPick.employee_id == uuid.UUID(me)))).scalars().all()
    if any(str(p.holiday_id) == holiday_id and p.status in ("pending", "approved") for p in mine):
        raise HTTPException(409, "You already picked this holiday")
    q = EL.optional_quota(await policy_of(db, org), [_pick_out(p) for p in mine], hd.year)
    if q["left"] <= 0:
        raise HTTPException(409, f"You have used all {q['allowed']} optional holiday(s) for {hd.year}")
    p = OptionalHolidayPick(org_id=uuid.UUID(org), employee_id=uuid.UUID(me), employee_name=_name(e),
                            manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None,
                            holiday_id=uuid.UUID(holiday_id), holiday_date=hd, holiday_name=h.get("name"), year=hd.year,
                            status="pending", created_at=_now())
    db.add(p)
    await db.commit()
    await approvals_client.open_item(
        org, kind="optional_holiday", ref_id=str(p.id), title=f"Optional holiday — {p.employee_name}",
        summary=[f"{p.holiday_name} · {hd:%a %d %b %Y}", f"{q['used'] + 1} of {q['allowed']} for {hd.year}"],
        requester_user_id=_uid(request), approver_roles=HR_INBOX, approver_manager_of_employee_id=me,
        link="/stat-holidays", callback_path=f"/leave/optional-holidays/picks/{p.id}/{{decision}}",
        context={"department": e.get("department") or "", "employee_id": me})
    return _pick_out(p)


@router.get("/optional-holidays/picks")
async def list_picks(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    org, role, me = _org(request), _role(request), _emp(request)
    q = select(OptionalHolidayPick).where(OptionalHolidayPick.org_id == uuid.UUID(org))
    if status:
        q = q.where(OptionalHolidayPick.status == status)
    if role == "manager" and me:
        q = q.where(OptionalHolidayPick.manager_id == uuid.UUID(me))
    elif role not in EL.HR_ROLES:
        if not me:
            return []
        q = q.where(OptionalHolidayPick.employee_id == uuid.UUID(me))
    return [_pick_out(p) for p in (await db.execute(q.order_by(OptionalHolidayPick.holiday_date))).scalars().all()]


@router.get("/optional-holidays/taken")
async def taken(request: Request, employee_id: str, start: str | None = None, end: str | None = None,
                db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emps = await _employees(request)
    e = emps.get(employee_id)
    if not e or not EL.may_view(_role(request), _emp(request), employee_id, e.get("manager_id")):
        raise HTTPException(404, "Employee not found")
    q = select(OptionalHolidayPick).where(OptionalHolidayPick.org_id == uuid.UUID(org),
                                          OptionalHolidayPick.employee_id == uuid.UUID(employee_id),
                                          OptionalHolidayPick.status == "approved")
    if start:
        q = q.where(OptionalHolidayPick.holiday_date >= date.fromisoformat(start))
    if end:
        q = q.where(OptionalHolidayPick.holiday_date <= date.fromisoformat(end))
    return [{"holiday_date": p.holiday_date.isoformat(), "name": p.holiday_name}
            for p in (await db.execute(q)).scalars().all()]


async def _pick_or_404(db, org, pid) -> OptionalHolidayPick:
    try:
        pid = uuid.UUID(str(pid))
    except ValueError:
        raise HTTPException(404, "Pick not found")
    p = (await db.execute(select(OptionalHolidayPick).where(OptionalHolidayPick.id == pid,
                                                            OptionalHolidayPick.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Pick not found")
    return p


@router.post("/optional-holidays/picks/{pid}/{verdict}")
async def decide_pick(pid: str, verdict: str, request: Request, body: DecideIn | None = None,
                      db: AsyncSession = Depends(get_db)):
    org, role, me = _org(request), _role(request), _emp(request)
    p = await _pick_or_404(db, org, pid)
    if verdict == "cancel":
        if str(p.employee_id) != (me or "") and role not in EL.HR_ROLES:
            raise HTTPException(403, "Only the employee or HR can withdraw a pick")
        if p.status not in ("pending", "approved") or p.holiday_date < local_today():
            raise HTTPException(409, "This pick can no longer be withdrawn")
        p.status = "cancelled"
        decision = "withdrawn"
    elif verdict in ("approve", "reject"):
        if not EL.may_decide(role, me, str(p.employee_id), str(p.manager_id) if p.manager_id else None):
            raise HTTPException(403, "Only this employee's manager or HR can decide")
        if p.status != "pending":
            raise HTTPException(409, f"This pick is already {p.status}")
        p.status = "approved" if verdict == "approve" else "rejected"
        decision = p.status
    else:
        raise HTTPException(404, "Unknown action")
    p.reviewed_by, p.reviewed_at, p.review_note = _uid(request), _now(), _comment(body)
    await db.commit()
    await approvals_client.close_item(org, kind="optional_holiday", ref_id=str(p.id), decision=decision,
                                      actor_user_id=_uid(request))
    return _pick_out(p)


# ══════════════════════════ comp-off ═══════════════════════════════════════════

def _co_out(c: CompOff) -> dict:
    return {"id": str(c.id), "employee_id": str(c.employee_id), "employee_name": c.employee_name,
            "worked_date": c.worked_date.isoformat(), "day_kind": c.day_kind, "day_label": c.day_label,
            "hours_worked": float(c.hours_worked or 0), "first_in": c.first_in, "last_out": c.last_out,
            "days": float(c.days or 0), "reason": c.reason, "status": c.status,
            "expires_on": c.expires_on.isoformat() if c.expires_on else None, "credited": bool(c.credited),
            "expired_days": float(c.expired_days or 0), "review_note": c.review_note,
            "created_at": c.created_at.isoformat() if c.created_at else None}


async def _punches(request: Request, employee_id: str, d: date) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.TIME_URL}/time/punches", headers=_fwd(request),
                            params={"employee_id": employee_id, "day": d.isoformat()})
            if r.status_code == 200 and isinstance(r.json(), list):
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


def _hhmm(iso: str | None) -> str | None:
    if not iso:
        return None
    from zoneinfo import ZoneInfo
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone(ZoneInfo("America/Toronto")).strftime("%H:%M")
    except ValueError:
        return None


class CompOffIn(BaseModel):
    worked_date: str
    reason: str = Field(min_length=3, max_length=500)


@router.post("/comp-off", status_code=201)
async def claim_comp_off(body: CompOffIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, me = _org(request), _emp(request)
    emps = await _employees(request)
    e = emps.get(me or "")
    if not e:
        raise HTTPException(400, "Your login is not linked to an employee record")
    try:
        wd = date.fromisoformat(body.worked_date)
    except ValueError:
        raise HTTPException(422, "worked_date must be YYYY-MM-DD")
    pol = await policy_of(db, org)
    if not EL.comp_off_window_ok(wd, local_today(), pol):
        raise HTTPException(409, "A comp-off is claimed for a day you already worked, within the company's claim window")
    state = e.get("province_of_employment")
    hols = {date.fromisoformat(str(h["holiday_date"])[:10]): h.get("name") or "Holiday" for h in await _stat_holidays(request)
            if h.get("holiday_date") and not h.get("is_optional") and h.get("province") in (None, "", state)}
    weekly_offs = await get_setting(org, "work_week.weekly_offs", [5, 6])
    kind = EL.comp_off_day_kind(wd, hols, weekly_offs if isinstance(weekly_offs, list) else [5, 6])
    if not kind:
        raise HTTPException(409, "That was a normal working day — a comp-off is for work on a holiday or weekly off")
    dup = (await db.execute(select(CompOff).where(CompOff.org_id == uuid.UUID(org), CompOff.employee_id == uuid.UUID(me),
                                                  CompOff.worked_date == wd, CompOff.status.in_(["pending", "approved"])))).scalars().first()
    if dup:
        raise HTTPException(409, "You already claimed a comp-off for that day")
    hours, fin, lout = EL.worked_hours(await _punches(request, me, wd))
    days = EL.comp_off_days(hours, pol)
    if days <= 0:
        raise HTTPException(409, f"Your punches show {hours:g} hour(s) on {wd:%d %b} — the company policy needs at least "
                                 f"{pol['comp_off_half_day_hours']:g} h for half a day")
    c = CompOff(org_id=uuid.UUID(org), employee_id=uuid.UUID(me), employee_name=_name(e),
                manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None, worked_date=wd,
                day_kind=kind[0], day_label=kind[1], hours_worked=hours, first_in=_hhmm(fin), last_out=_hhmm(lout),
                days=days, reason=body.reason, status="pending", expires_on=EL.comp_off_expiry(wd, pol), created_at=_now())
    db.add(c)
    await db.commit()
    await approvals_client.open_item(
        org, kind="comp_off", ref_id=str(c.id), title=f"Comp-off — {c.employee_name}",
        summary=[f"Worked {wd:%a %d %b %Y} ({kind[1]}) · {hours:g} h ({c.first_in or '—'}–{c.last_out or '—'})",
                 f"{days:g} day comp-off" + (f", use by {c.expires_on:%d %b %Y}" if c.expires_on else ""), f"Reason: {body.reason}"],
        requester_user_id=_uid(request), approver_roles=HR_INBOX, approver_manager_of_employee_id=me, link="/leave",
        callback_path=f"/leave/comp-off/{c.id}/{{decision}}",
        context={"leave_days": days, "hours": hours, "department": e.get("department") or "", "employee_id": me})
    return _co_out(c)


@router.get("/comp-off")
async def list_comp_off(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    org, role, me = _org(request), _role(request), _emp(request)
    await expire_comp_offs(db, org)
    q = select(CompOff).where(CompOff.org_id == uuid.UUID(org))
    if status:
        q = q.where(CompOff.status == status)
    if role == "manager" and me:
        q = q.where((CompOff.manager_id == uuid.UUID(me)) | (CompOff.employee_id == uuid.UUID(me)))
    elif role not in EL.HR_ROLES:
        if not me:
            return []
        q = q.where(CompOff.employee_id == uuid.UUID(me))
    return [_co_out(c) for c in (await db.execute(q.order_by(CompOff.worked_date.desc()))).scalars().all()]


async def _comp_balance(db, org: str, request: Request, employee_id: str, fy: int) -> LeaveBalance:
    b = (await db.execute(select(LeaveBalance).where(
        LeaveBalance.org_id == uuid.UUID(org), LeaveBalance.employee_id == uuid.UUID(employee_id),
        LeaveBalance.fiscal_year == fy, LeaveBalance.leave_type_code == "COMP"))).scalar_one_or_none()
    if b:
        return b
    lt = next((t for t in await _leave_types(request) if t.get("code") == "COMP"), {"name": "Compensatory Off"})
    b = LeaveBalance(org_id=uuid.UUID(org), employee_id=uuid.UUID(employee_id), fiscal_year=fy, leave_type_code="COMP",
                     leave_type_name=lt.get("name") or "Compensatory Off", paid=True, allocated=0, carried_over=0, used=0, pending=0)
    db.add(b)
    return b


async def expire_comp_offs(db, org: str) -> int:
    """Credits past their expiry date give back what is still unused on the COMP balance. Idempotent."""
    today = local_today()
    rows = (await db.execute(select(CompOff).where(CompOff.org_id == uuid.UUID(org), CompOff.status == "approved",
                                                   CompOff.credited.is_(True), CompOff.expires_on < today))).scalars().all()
    n = 0
    for c in rows:
        b = (await db.execute(select(LeaveBalance).where(
            LeaveBalance.org_id == uuid.UUID(org), LeaveBalance.employee_id == c.employee_id,
            LeaveBalance.fiscal_year == (c.fiscal_year or today.year), LeaveBalance.leave_type_code == "COMP"))).scalar_one_or_none()
        if b:
            left = float(b.allocated) + float(b.carried_over) - float(b.used) - float(b.pending)
            take = round(min(float(c.days or 0), max(0.0, left)), 2)
            b.allocated = round(float(b.allocated) - take, 2)
            c.expired_days = take
        c.status = "expired"
        n += 1
    if n:
        await db.commit()
    return n


async def _co_or_404(db, org, cid) -> CompOff:
    try:
        cid = uuid.UUID(str(cid))
    except ValueError:
        raise HTTPException(404, "Comp-off not found")
    c = (await db.execute(select(CompOff).where(CompOff.id == cid, CompOff.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Comp-off not found")
    return c


@router.post("/comp-off/{cid}/{verdict}")
async def decide_comp_off(cid: str, verdict: str, request: Request, body: DecideIn | None = None,
                          db: AsyncSession = Depends(get_db)):
    org, role, me = _org(request), _role(request), _emp(request)
    c = await _co_or_404(db, org, cid)
    if verdict == "cancel":
        if str(c.employee_id) != (me or "") and role not in EL.HR_ROLES:
            raise HTTPException(403, "Only the employee or HR can withdraw a comp-off claim")
        if c.status != "pending":
            raise HTTPException(409, f"Only a pending claim can be withdrawn (this one is {c.status})")
        c.status, decision = "cancelled", "withdrawn"
    elif verdict in ("approve", "reject"):
        if not EL.may_decide(role, me, str(c.employee_id), str(c.manager_id) if c.manager_id else None):
            raise HTTPException(403, "Only this employee's manager or HR can decide a comp-off")
        if c.status != "pending":
            raise HTTPException(409, f"This claim is already {c.status}")
        if verdict == "approve":
            fy = _resolve_fy(await _fiscal_years(request), c.worked_date) or _current_fy(await _fiscal_years(request))
            b = await _comp_balance(db, org, request, str(c.employee_id), fy)
            b.allocated = round(float(b.allocated or 0) + float(c.days), 2)
            c.status, c.credited, c.fiscal_year = "approved", True, fy
        else:
            c.status = "rejected"
        decision = c.status
    else:
        raise HTTPException(404, "Unknown action")
    c.reviewed_by, c.reviewed_at, c.review_note = _uid(request), _now(), _comment(body)
    await db.commit()
    await approvals_client.close_item(org, kind="comp_off", ref_id=str(c.id), decision=decision, actor_user_id=_uid(request))
    return _co_out(c)
