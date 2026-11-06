"""Time & Attendance — capture, attendance, timesheets, approvals, payroll feed.

Audiences (all org-authenticated via the gateway's stamped X-Org-Id):
  • Employee   → punch, my timesheet, request a regularization
  • Supervisor → the attendance cockpit (who's in), generate timesheets
  • Manager    → approvals queue (their reports, routed by manager_id)

The point of the module is the FEED: approved timesheet hours → pay-run
`entries` (REG/OT/STAT), so payroll's readiness gate has real data behind it.
"""
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today, to_local_date  # noqa: E402
from shared.utils.roles import HR_WRITE, OFFICE, has_role  # noqa: E402

from constants import (  # noqa: E402
    DEFAULT_LATE_GRACE_MIN, DEFAULT_SCHEDULE,
)
from constants_ca_time import (  # noqa: E402
    break_rules, ot_rules, overtime_split, stat_eligibility, stat_holiday_pay,
    stat_rules, stat_worked_premium_multiplier,
)
from database import get_db  # noqa: E402
from models import (  # noqa: E402
    AttendanceDay, Punch, Regularization, RosterShift, ScheduleAssignment,
    Timesheet, TimesheetLine, WorkSchedule,
)


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance in metres between two lat/lng points."""
    from math import asin, cos, radians, sin, sqrt
    r = 6371000.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

router = APIRouter(prefix="/time", tags=["time"])

ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
APPROVER_ROLES = ADMIN_ROLES | {"manager"}


def _now():
    return datetime.now(timezone.utc)


async def _today(org=None):
    """The org's business date (org time zone, America/Toronto by default) — never the UTC date."""
    return await org_today(org)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _uid(request: Request) -> str | None:
    return request.headers.get("x-user-id")


def _emp(request: Request) -> str | None:
    """The employee record the logged-in user maps to — for manager scoping."""
    return request.headers.get("x-employee-id")


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id")
            if request.headers.get(k)}


def _branch(request: Request) -> str | None:
    """Resolved branch scope (gateway-stamped). Non-empty → restrict to that branch."""
    return request.headers.get("x-branch-id") or None


# ── access gates (WASA 2026-10-21) ────────────────────────────────────────────
# HR_WRITE = org_admin + HR family · OFFICE = HR_WRITE + payroll (the pay office).
def _is_hr(request: Request) -> bool:
    return has_role(_role(request), HR_WRITE)


def _is_office(request: Request) -> bool:
    """HR + pay office — every timesheet, with pay (hourly_rate / stat_pay)."""
    return has_role(_role(request), OFFICE)


def _require_hr(request: Request, what: str) -> None:
    if not _is_hr(request):
        raise HTTPException(403, f"Only HR can {what}")


def _ts_visible(request: Request, t) -> bool:
    """Timesheet scope: office = all · manager = own + direct reports · anyone else = own."""
    if _is_office(request):
        return True
    me = _emp(request) or ""
    if me and str(t.employee_id) == me:
        return True
    return _role(request) == "manager" and bool(me) and str(t.manager_id or "") == me


def _ts_can_edit(request: Request, t) -> bool:
    """Edit / submit a draft: the owner, their manager, HR."""
    if _is_hr(request):
        return True
    me = _emp(request) or ""
    if me and str(t.employee_id) == me:
        return True
    return _role(request) == "manager" and bool(me) and str(t.manager_id or "") == me


def _check_ts_reviewer(request: Request, t) -> None:
    """Approve / reject / lock: HR for any timesheet; a manager only their direct reports'."""
    if _is_hr(request):
        return
    me = _emp(request) or ""
    if _role(request) == "manager" and me and str(t.manager_id or "") == me:
        return
    raise HTTPException(404, "Timesheet not found")


def _ts_for(request: Request, t, lines=None) -> dict:
    """_ts_out, minus pay (hourly_rate / stat_pay) on someone else's row for non-office roles."""
    o = _ts_out(t, lines)
    if not _is_office(request) and str(t.employee_id) != (_emp(request) or ""):
        o["hourly_rate"] = None
        o["stat_pay"] = None
    return o


async def _fetch_employees(request: Request) -> list[dict]:
    """The org roster from the employee service (never raises — empty on failure)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _branch_emp_ids(request: Request) -> set | None:
    """Employee ids in the caller's branch scope (own-table rows are filtered to
    these). None = org-wide (no branch filter). Employees are already branch-scoped
    by the gateway, so this is just the fetched set."""
    if not _branch(request):
        return None
    return {uuid.UUID(e["id"]) for e in await _fetch_employees(request)}


async def _fetch_stat_holidays(request: Request) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/masters/stat-holidays", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


def _eff_hourly_rate(emp: dict) -> float:
    """Effective hourly rate for OT/STAT/REG math — hourly uses its rate, salary
    derives one from annual ÷ (standard weekly hours × 52)."""
    if emp.get("pay_type") == "hourly":
        return float(emp.get("hourly_rate") or 0)
    std = float(emp.get("standard_hours_per_week") or 40) or 40
    return round(float(emp.get("annual_salary") or 0) / (std * 52), 2)


def _local_time(dt: datetime, tzname: str | None) -> time:
    """The wall-clock time of a UTC instant in the schedule's timezone."""
    if not dt:
        return time(0, 0)
    if ZoneInfo and tzname:
        try:
            return dt.astimezone(ZoneInfo(tzname)).time()
        except Exception:
            pass
    return dt.time()


# ══════════════════════════ Schedules & assignments ════════════════════════════

def _sched_out(s: WorkSchedule) -> dict:
    return {"id": str(s.id), "name": s.name, "code": s.code,
            "days_of_week": s.days_of_week or [], "start_time": s.start_time,
            "end_time": s.end_time, "break_minutes": s.break_minutes,
            "daily_hours": float(s.daily_hours or 0), "timezone": s.timezone,
            "is_default": s.is_default, "is_active": s.is_active,
            "site_lat": float(s.site_lat) if s.site_lat is not None else None,
            "site_lng": float(s.site_lng) if s.site_lng is not None else None,
            "geofence_m": s.geofence_m}


class ScheduleIn(BaseModel):
    name: str
    code: str | None = None
    days_of_week: list[int] = [0, 1, 2, 3, 4]
    start_time: str = "09:00"
    end_time: str = "17:00"
    break_minutes: int = 30
    daily_hours: float = 8
    timezone: str = "America/Toronto"
    is_default: bool = False
    site_lat: float | None = None
    site_lng: float | None = None
    geofence_m: int | None = None


@router.get("/schedules")
async def list_schedules(request: Request, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(WorkSchedule).where(
        WorkSchedule.org_id == _org(request), WorkSchedule.is_active.is_(True)).order_by(WorkSchedule.name))
    return [_sched_out(s) for s in res.scalars().all()]


@router.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "add a work schedule")
    s = WorkSchedule(org_id=_org(request), **body.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _sched_out(s)


@router.patch("/schedules/{sid}")
async def update_schedule(sid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "change a work schedule")
    s = (await db.execute(select(WorkSchedule).where(
        WorkSchedule.id == sid, WorkSchedule.org_id == _org(request)))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Schedule not found")
    cols = {c.name for c in WorkSchedule.__table__.columns} - {"id", "org_id", "created_at"}
    for k, v in body.items():
        if k in cols:
            setattr(s, k, v)
    await db.commit()
    await db.refresh(s)
    return _sched_out(s)


@router.post("/schedules/seed-defaults", status_code=201)
async def seed_default_schedule(request: Request, db: AsyncSession = Depends(get_db)):
    """Seed a standard 9–5 pattern if the org has none yet (idempotent-ish)."""
    _require_hr(request, "add the standard work schedule")
    org = _org(request)
    existing = (await db.execute(select(WorkSchedule).where(WorkSchedule.org_id == org))).scalars().first()
    if existing:
        return {"seeded": 0}
    s = WorkSchedule(org_id=org, **DEFAULT_SCHEDULE)
    db.add(s)
    await db.commit()
    return {"seeded": 1}


@router.get("/assignments")
async def list_assignments(request: Request, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ScheduleAssignment).where(
        ScheduleAssignment.org_id == _org(request), ScheduleAssignment.is_active.is_(True)))
    return [{"employee_id": str(a.employee_id), "schedule_id": str(a.schedule_id),
             "effective_from": a.effective_from.isoformat() if a.effective_from else None}
            for a in res.scalars().all()]


class AssignIn(BaseModel):
    schedule_id: str
    effective_from: str | None = None


@router.put("/assignments/{employee_id}")
async def assign_schedule(employee_id: str, body: AssignIn, request: Request,
                          db: AsyncSession = Depends(get_db)):
    """Set an employee's working pattern (supersedes any prior active one).
    HR for anyone; a manager only for their own direct reports."""
    org = _org(request)
    if not _is_hr(request):
        if _role(request) != "manager":
            raise HTTPException(403, "Only HR or the employee's manager can set a work schedule")
        me = _emp(request) or ""
        emp = {str(x.get("id")): x for x in await _fetch_employees(request)}.get(employee_id)
        if not emp or not me or str(emp.get("manager_id") or "") != me:
            raise HTTPException(404, "Employee not found")
    prior = (await db.execute(select(ScheduleAssignment).where(
        ScheduleAssignment.org_id == org, ScheduleAssignment.employee_id == employee_id,
        ScheduleAssignment.is_active.is_(True)))).scalars().all()
    for p in prior:
        p.is_active = False
    eff = date.fromisoformat(body.effective_from) if body.effective_from else None
    a = ScheduleAssignment(org_id=org, employee_id=employee_id,
                           schedule_id=body.schedule_id, effective_from=eff)
    db.add(a)
    await db.commit()
    return {"employee_id": employee_id, "schedule_id": body.schedule_id}


async def _schedule_map(db, org) -> tuple[dict, dict]:
    """(schedule_id → schedule dict, employee_id → schedule dict)."""
    scheds = {str(s.id): _sched_out(s) for s in (await db.execute(
        select(WorkSchedule).where(WorkSchedule.org_id == org))).scalars().all()}
    by_emp = {}
    for a in (await db.execute(select(ScheduleAssignment).where(
            ScheduleAssignment.org_id == org, ScheduleAssignment.is_active.is_(True)))).scalars().all():
        s = scheds.get(str(a.schedule_id))
        if s:
            by_emp[str(a.employee_id)] = s
    default = next((s for s in scheds.values() if s["is_default"]), None)
    return by_emp, default


def _plan_for(emp: dict, sched: dict | None, default: dict | None, work_date: date):
    """(planned_hours, is_workday, planned_start_hhmm, break_minutes, timezone)."""
    s = sched or default
    if s:
        workday = work_date.weekday() in (s.get("days_of_week") or [])
        return (float(s["daily_hours"]) if workday else 0.0), workday, \
            s.get("start_time"), s.get("break_minutes") or 0, s.get("timezone")
    # fallback: Mon–Fri, standard hours ÷ 5
    workday = work_date.weekday() < 5
    hrs = round(float(emp.get("standard_hours_per_week") or 40) / 5, 2) if workday else 0.0
    return hrs, workday, "09:00", 30, "America/Toronto"


# ══════════════════════════ Punches & attendance ═══════════════════════════════

def _derive(punches: list[Punch], planned_hours: float, is_workday: bool,
            planned_start: str | None, break_min: int, tzname: str | None,
            is_holiday: bool, is_past: bool):
    """Fold a day's punches into (actual_hours, first_in, last_out, late_min, status)."""
    events = sorted(punches, key=lambda p: p.at)
    ins = [p for p in events if p.direction == "in"]
    outs = [p for p in events if p.direction == "out"]
    first_in = ins[0].at if ins else None
    last_out = outs[-1].at if outs else None

    worked = timedelta()
    open_in = None
    for p in events:
        if p.direction in ("in", "break_end"):
            open_in = p.at
        elif p.direction in ("out", "break_start") and open_in:
            worked += (p.at - open_in)
            open_in = None
    actual = round(worked.total_seconds() / 3600, 2)

    late_min = 0
    if first_in and planned_start:
        try:
            ph, pm = map(int, planned_start.split(":"))
            local = _local_time(first_in, tzname)
            delta = (local.hour * 60 + local.minute) - (ph * 60 + pm)
            late_min = max(0, delta)
        except Exception:
            late_min = 0

    if is_holiday:
        status = "holiday"
    elif not is_workday:
        status = "rest_day"
    elif not first_in:
        status = "absent" if is_past else "scheduled"
    elif first_in and not last_out:
        status = "late" if late_min > DEFAULT_LATE_GRACE_MIN else "on_shift"
    else:
        status = "late" if late_min > DEFAULT_LATE_GRACE_MIN else "present"
    return actual, first_in, last_out, late_min, status


async def _recompute_day(db, org, emp: dict, sched: dict | None, default: dict | None,
                         work_date: date, holidays: set[date]):
    """Rebuild the AttendanceDay for one employee+date from its punches."""
    punches = (await db.execute(select(Punch).where(
        Punch.org_id == org, Punch.employee_id == emp["id"],
        Punch.work_date == work_date))).scalars().all()
    planned, workday, p_start, brk, tz = _plan_for(emp, sched, default, work_date)
    # G2 2026-10-23: a PUBLISHED roster row (hand-made or generated from a rotation pattern) plans the day
    from rotation_routers import roster_overlay  # noqa: E402
    planned, workday, p_start, brk = await roster_overlay(db, org, emp["id"], work_date, planned, workday, p_start, brk)
    is_holiday = work_date in holidays
    is_past = work_date < await _today(org)
    actual, first_in, last_out, late_min, status = _derive(
        punches, planned, workday, p_start, brk, tz, is_holiday, is_past)

    row = (await db.execute(select(AttendanceDay).where(
        AttendanceDay.org_id == org, AttendanceDay.employee_id == emp["id"],
        AttendanceDay.work_date == work_date))).scalar_one_or_none()
    if not row:
        row = AttendanceDay(org_id=org, employee_id=uuid.UUID(emp["id"]), work_date=work_date)
        db.add(row)
    row.planned_hours = planned
    row.actual_hours = actual
    row.first_in = first_in
    row.last_out = last_out
    row.late_minutes = late_min
    row.status = status
    row.is_holiday = is_holiday
    return row


class PunchIn(BaseModel):
    employee_id: str
    direction: str                 # in | out | break_start | break_end
    source: str = "web"
    note: str | None = None
    at: str | None = None          # ISO datetime; defaults to now
    lat: float | None = None
    lng: float | None = None


@router.post("/punch", status_code=201)
async def punch(body: PunchIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    if body.direction not in ("in", "out", "break_start", "break_end"):
        raise HTTPException(422, "direction must be in | out | break_start | break_end")
    when = _now()
    if body.at:
        try:
            when = datetime.fromisoformat(body.at)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "at must be an ISO datetime")
    emp = {e["id"]: e for e in await _fetch_employees(request)}.get(body.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    wd = to_local_date(when)
    by_emp, default = await _schedule_map(db, org)
    sched = by_emp.get(body.employee_id) or default

    # Geofence validation (opportunistic — recorded, not blocked): if the schedule
    # defines a site + radius and the punch carries coordinates, flag on/off-site.
    geofence_ok = None
    geofence_m = None
    if body.lat is not None and body.lng is not None and sched \
            and sched.get("site_lat") is not None and sched.get("geofence_m"):
        dist = _haversine_m(body.lat, body.lng, sched["site_lat"], sched["site_lng"])
        geofence_ok = dist <= sched["geofence_m"]
        geofence_m = round(dist)

    p = Punch(org_id=org, employee_id=uuid.UUID(body.employee_id), work_date=wd, at=when,
              direction=body.direction, source=body.source, note=body.note,
              lat=body.lat, lng=body.lng, geofence_ok=geofence_ok, created_by=_uid(request))
    db.add(p)
    holidays = {date.fromisoformat(h["holiday_date"][:10]) for h in await _fetch_stat_holidays(request)
                if h.get("holiday_date") and (h.get("province") in (None, emp.get("province_of_employment")))}
    row = await _recompute_day(db, org, emp, by_emp.get(emp["id"]), default, wd, holidays)
    await db.commit()
    return {"id": str(p.id), "direction": p.direction, "at": when.isoformat(),
            "work_date": wd.isoformat(), "status": row.status,
            "actual_hours": float(row.actual_hours), "late_minutes": row.late_minutes,
            "geofence_ok": geofence_ok, "distance_m": geofence_m}


@router.get("/punches")
async def list_punches(request: Request, employee_id: str, day: str | None = None,
                       db: AsyncSession = Depends(get_db)):
    org = _org(request)
    if _role(request) not in APPROVER_ROLES and employee_id != (_emp(request) or ""):
        raise HTTPException(403, "You can only see your own punches")
    q = select(Punch).where(Punch.org_id == org, Punch.employee_id == employee_id)
    if day:
        q = q.where(Punch.work_date == date.fromisoformat(day))
    q = q.order_by(Punch.at)
    return [{"id": str(p.id), "direction": p.direction, "at": p.at.isoformat(),
             "work_date": p.work_date.isoformat(), "source": p.source, "note": p.note}
            for p in (await db.execute(q)).scalars().all()]


@router.get("/attendance")
async def attendance_board(request: Request, day: str | None = None,
                           db: AsyncSession = Depends(get_db)):
    """The cockpit 'who's in' board for a date — every employee, plan vs actual."""
    org = _org(request)
    wd = date.fromisoformat(day) if day else await _today(org)
    emps = await _fetch_employees(request)
    role, me = _role(request), _emp(request)
    if role == "manager" and me:                    # a manager sees only their reports
        emps = [e for e in emps if str(e.get("manager_id")) == me]
    elif role not in APPROVER_ROLES:                # an employee sees only their own row
        emps = [e for e in emps if str(e.get("id")) == (me or "")]
    by_emp, default = await _schedule_map(db, org)
    holidays_all = await _fetch_stat_holidays(request)
    punch_rows = (await db.execute(select(Punch).where(
        Punch.org_id == org, Punch.work_date == wd))).scalars().all()
    punches_by_emp = defaultdict(list)
    for p in punch_rows:
        punches_by_emp[str(p.employee_id)].append(p)

    is_past = wd < await _today(org)
    out = []
    for e in emps:
        if e.get("status") == "terminated":
            continue
        eid = e["id"]
        hol = {date.fromisoformat(h["holiday_date"][:10]) for h in holidays_all
               if h.get("holiday_date") and (h.get("province") in (None, e.get("province_of_employment")))}
        planned, workday, p_start, brk, tz = _plan_for(e, by_emp.get(eid), default, wd)
        actual, first_in, last_out, late_min, status = _derive(
            punches_by_emp.get(eid, []), planned, workday, p_start, brk, tz, wd in hol, is_past)
        out.append({
            "employee_id": eid, "name": f"{e['first_name']} {e['last_name']}",
            "employee_number": e.get("employee_number"), "province": e.get("province_of_employment"),
            "department": e.get("department"), "pay_type": e.get("pay_type"),
            "planned_hours": planned, "actual_hours": actual, "status": status,
            "late_minutes": late_min,
            "first_in": first_in.isoformat() if first_in else None,
            "last_out": last_out.isoformat() if last_out else None,
        })
    out.sort(key=lambda r: r["name"])
    counts = defaultdict(int)
    for r in out:
        counts[r["status"]] += 1
    summary = {
        "date": wd.isoformat(), "total": len(out),
        "present": counts["present"] + counts["on_shift"] + counts["late"],
        "on_shift": counts["on_shift"], "late": counts["late"],
        "absent": counts["absent"], "on_leave": counts["leave"],
        "rest_day": counts["rest_day"], "holiday": counts["holiday"],
        "scheduled": counts["scheduled"],
    }
    return {"summary": summary, "rows": out}


@router.get("/attendance/{employee_id}")
async def employee_attendance(employee_id: str, request: Request,
                              start: str | None = None, end: str | None = None,
                              db: AsyncSession = Depends(get_db)):
    org = _org(request)
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    me = _emp(request) or ""
    if not (_is_office(request) or (me and employee_id == me)
            or (_role(request) == "manager" and me and str(e.get("manager_id") or "") == me)):
        raise HTTPException(404, "Employee not found")       # own · manager = reports · office = all
    d_end = date.fromisoformat(end) if end else await _today(org)
    d_start = date.fromisoformat(start) if start else (d_end - timedelta(days=13))
    rows = (await db.execute(select(AttendanceDay).where(
        AttendanceDay.org_id == org, AttendanceDay.employee_id == employee_id,
        AttendanceDay.work_date >= d_start, AttendanceDay.work_date <= d_end)
        .order_by(AttendanceDay.work_date))).scalars().all()
    days = [{"work_date": r.work_date.isoformat(), "planned_hours": float(r.planned_hours),
             "actual_hours": float(r.actual_hours), "status": r.status,
             "late_minutes": r.late_minutes, "is_holiday": r.is_holiday,
             "first_in": r.first_in.isoformat() if r.first_in else None,
             "last_out": r.last_out.isoformat() if r.last_out else None} for r in rows]
    return {"employee": {"id": employee_id, "name": f"{e['first_name']} {e['last_name']}",
                         "employee_number": e.get("employee_number"),
                         "province": e.get("province_of_employment"), "pay_type": e.get("pay_type")},
            "start": d_start.isoformat(), "end": d_end.isoformat(),
            "actual_total": round(sum(d["actual_hours"] for d in days), 2),
            "planned_total": round(sum(d["planned_hours"] for d in days), 2),
            "days": days}


# ══════════════════════════ Timesheets & approvals ═════════════════════════════

def _ts_out(t: Timesheet, lines: list[TimesheetLine] | None = None) -> dict:
    o = {"id": str(t.id), "employee_id": str(t.employee_id), "employee_name": t.employee_name,
         "employee_number": t.employee_number,
         "manager_id": str(t.manager_id) if t.manager_id else None, "manager_name": t.manager_name,
         "province": t.province, "pay_type": t.pay_type,
         "hourly_rate": float(t.hourly_rate or 0), "pay_frequency": t.pay_frequency,
         "period_start": t.period_start.isoformat(), "period_end": t.period_end.isoformat(),
         "reg_hours": float(t.reg_hours), "ot_hours": float(t.ot_hours),
         "ot2_hours": float(t.ot2_hours or 0), "stat_hours": float(t.stat_hours),
         "stat_pay": float(t.stat_pay or 0), "total_hours": float(t.total_hours),
         "status": t.status,
         "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
         "reviewed_at": t.reviewed_at.isoformat() if t.reviewed_at else None,
         "review_note": t.review_note,
         "locked_at": t.locked_at.isoformat() if t.locked_at else None}
    if lines is not None:
        o["lines"] = [{"id": str(l.id), "work_date": l.work_date.isoformat(),
                       "reg_hours": float(l.reg_hours), "ot_hours": float(l.ot_hours),
                       "ot2_hours": float(l.ot2_hours or 0), "stat_hours": float(l.stat_hours),
                       "hours": float(l.hours or 0), "project_id": l.project_id,
                       "project_name": l.project_name, "billable": bool(l.billable),
                       "cost_center": l.cost_center,
                       "note": l.note} for l in sorted(lines, key=lambda x: x.work_date)]
    return o


class GenerateIn(BaseModel):
    period_start: str
    period_end: str
    pay_frequency: str | None = None
    employee_ids: list[str] | None = None      # None = everyone on the frequency


@router.post("/timesheets/generate", status_code=201)
async def generate_timesheets(body: GenerateIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Build draft timesheets from attendance for a period. REG/OT/OT2 split by the
    provincial ESA engine (daily + weekly + double-time), grouped by ISO week; STAT
    from stat-holiday eligibility + average-daily-wage. Skips employees who already
    have a timesheet for the period. HR for everyone; a manager for their own team."""
    org = _org(request)
    if not _is_hr(request) and _role(request) != "manager":
        raise HTTPException(403, "Only HR or a manager can generate timesheets")
    try:
        p_start = date.fromisoformat(body.period_start)
        p_end = date.fromisoformat(body.period_end)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if p_end < p_start:
        raise HTTPException(400, "period_end before period_start")
    year = p_end.year

    emps = await _fetch_employees(request)
    if not _is_hr(request):                       # manager → own direct reports only
        me = _emp(request) or ""
        emps = [e for e in emps if me and str(e.get("manager_id") or "") == me]
    if body.pay_frequency:
        emps = [e for e in emps if e.get("pay_frequency") == body.pay_frequency]
    if body.employee_ids:
        sel = set(body.employee_ids)
        emps = [e for e in emps if e["id"] in sel]
    emps = [e for e in emps if e.get("status") != "terminated"]

    holidays_all = await _fetch_stat_holidays(request)
    created, skipped = [], 0
    for e in emps:
        eid = e["id"]
        dup = (await db.execute(select(Timesheet).where(
            Timesheet.org_id == org, Timesheet.employee_id == eid,
            Timesheet.period_start == p_start, Timesheet.period_end == p_end))).scalar_one_or_none()
        if dup:
            skipped += 1
            continue
        hol = {date.fromisoformat(h["holiday_date"][:10]) for h in holidays_all
               if h.get("holiday_date") and (h.get("province") in (None, e.get("province_of_employment")))}
        days = (await db.execute(select(AttendanceDay).where(
            AttendanceDay.org_id == org, AttendanceDay.employee_id == eid,
            AttendanceDay.work_date >= p_start, AttendanceDay.work_date <= p_end)
            .order_by(AttendanceDay.work_date))).scalars().all()

        prov = e.get("province_of_employment", "ON")
        rate = _eff_hourly_rate(e)
        std_daily = round(float(e.get("standard_hours_per_week") or 40) / 5, 2)
        ot_eligible = e.get("overtime_eligible", True)   # exempt/ineligible → all hours are regular
        day_map = {d.work_date: d for d in days}

        # ── Overtime: real provincial engine, per ISO week → reg / 1.5× / 2× ──
        by_week: dict = defaultdict(list)
        for d in days:
            by_week[d.work_date.isocalendar()[:2]].append((d.work_date, float(d.actual_hours)))
        reg_by_date, ot_by_date, ot2_by_date = {}, {}, {}
        for _wk, wk in by_week.items():
            wk.sort(key=lambda x: x[0])
            ot15_w, ot20_w = (0.0, 0.0) if not ot_eligible else overtime_split(prov, [h for _, h in wk], year)[1:]
            rem20, rem15 = ot20_w, ot15_w
            for d0, h in sorted(wk, key=lambda x: -x[1]):      # 2× comes off the longest days
                if rem20 <= 0:
                    break
                take = min(h, rem20); ot2_by_date[d0] = round(take, 2); rem20 = round(rem20 - take, 2)
            for d0, h in reversed(wk):                          # 1.5× off the later days
                if rem15 <= 0:
                    break
                avail = round(h - ot2_by_date.get(d0, 0.0), 2)
                take = min(avail, rem15); ot_by_date[d0] = round(take, 2); rem15 = round(rem15 - take, 2)
            for d0, h in wk:
                reg_by_date[d0] = round(h - ot_by_date.get(d0, 0.0) - ot2_by_date.get(d0, 0.0), 2)

        # ── Stat pay: average-daily-wage over the province's lookback window ──
        stat_lb = stat_rules(prov, year)["lookback_days"]
        wages_lb, days_worked_lb = 0.0, 0
        if hol:
            lb_rows = (await db.execute(select(AttendanceDay).where(
                AttendanceDay.org_id == org, AttendanceDay.employee_id == eid,
                AttendanceDay.work_date >= p_start - timedelta(days=stat_lb),
                AttendanceDay.work_date < p_start))).scalars().all()
            for d in lb_rows:
                if float(d.actual_hours) > 0:
                    wages_lb += float(d.actual_hours) * rate
                    days_worked_lb += 1

        def _stat_for(wd, worked):
            before = day_map.get(wd - timedelta(days=1))
            after = day_map.get(wd + timedelta(days=1))
            wb = bool(before and float(before.actual_hours) > 0) or days_worked_lb > 0
            wa = bool(after and float(after.actual_hours) > 0) or worked
            if not stat_eligibility(prov, wb, wa, days_worked_lb, year):
                return 0.0, 0.0
            pay = stat_holiday_pay(prov, wages_lb, days_worked_lb, rate, std_daily, year)
            return (round(pay / rate, 2) if rate else std_daily), round(pay, 2)

        lines, reg_sum, ot_sum, ot2_sum, stat_hsum, stat_paysum = [], 0.0, 0.0, 0.0, 0.0, 0.0
        seen = set()
        for d in days:
            wd = d.work_date
            seen.add(wd)
            reg = reg_by_date.get(wd, float(d.actual_hours))
            ot = ot_by_date.get(wd, 0.0)
            ot2 = ot2_by_date.get(wd, 0.0)
            stat_h = stat_p = 0.0
            if wd in hol:
                worked = float(d.actual_hours) > 0
                stat_h, stat_p = _stat_for(wd, worked)
                if not worked:
                    reg = ot = ot2 = 0.0     # a day off: no worked hours, just holiday pay
            reg_sum += reg; ot_sum += ot; ot2_sum += ot2; stat_hsum += stat_h; stat_paysum += stat_p
            lines.append(TimesheetLine(org_id=org, timesheet_id=None, work_date=wd,
                                       reg_hours=round(reg, 2), ot_hours=round(ot, 2), ot2_hours=round(ot2, 2),
                                       stat_hours=round(stat_h, 2), cost_center=e.get("cost_center"),
                                       note="Stat holiday" if wd in hol else None))
        # Stat holidays in the period with no attendance row → still owed holiday pay.
        for hd in sorted(hol):
            if p_start <= hd <= p_end and hd not in seen:
                stat_h, stat_p = _stat_for(hd, False)
                stat_hsum += stat_h; stat_paysum += stat_p
                lines.append(TimesheetLine(org_id=org, timesheet_id=None, work_date=hd,
                                           reg_hours=0, ot_hours=0, ot2_hours=0,
                                           stat_hours=round(stat_h, 2),
                                           cost_center=e.get("cost_center"), note="Stat holiday"))

        t = Timesheet(
            org_id=org, employee_id=uuid.UUID(eid),
            employee_name=f"{e['first_name']} {e['last_name']}",
            employee_number=e.get("employee_number"),
            manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None,
            province=prov, pay_type=e.get("pay_type"), hourly_rate=rate,
            pay_frequency=e.get("pay_frequency"), period_start=p_start, period_end=p_end,
            reg_hours=round(reg_sum, 2), ot_hours=round(ot_sum, 2), ot2_hours=round(ot2_sum, 2),
            stat_hours=round(stat_hsum, 2), stat_pay=round(stat_paysum, 2),
            total_hours=round(reg_sum + ot_sum + ot2_sum + stat_hsum, 2), status="draft")
        db.add(t)
        await db.flush()
        for l in lines:
            l.timesheet_id = t.id
            db.add(l)
        created.append(_ts_for(request, t))
    await db.commit()
    return {"created": len(created), "skipped": skipped, "timesheets": created}


@router.get("/timesheets")
async def list_timesheets(request: Request, status: str | None = None,
                          employee_id: str | None = None, period_end: str | None = None,
                          db: AsyncSession = Depends(get_db)):
    org = _org(request)
    q = select(Timesheet).where(Timesheet.org_id == org)
    if status:
        codes = [s.strip() for s in status.split(",") if s.strip()]
        q = q.where(Timesheet.status.in_(codes))
    if employee_id:
        q = q.where(Timesheet.employee_id == employee_id)
    if period_end:
        q = q.where(Timesheet.period_end == date.fromisoformat(period_end))
    role, me = _role(request), _emp(request)
    if not _is_office(request):
        # employee / accountant = own · manager = own + direct reports
        if not me:
            return []
        if role == "manager":
            q = q.where(or_(Timesheet.manager_id == me, Timesheet.employee_id == me))
        else:
            q = q.where(Timesheet.employee_id == me)
    bids = await _branch_emp_ids(request)
    if bids is not None:
        q = q.where(Timesheet.employee_id.in_(bids))
    q = q.order_by(Timesheet.period_end.desc(), Timesheet.employee_name)
    return [_ts_for(request, t) for t in (await db.execute(q)).scalars().all()]


async def _ts_or_404(db, org, tid) -> Timesheet:
    t = (await db.execute(select(Timesheet).where(
        Timesheet.id == tid, Timesheet.org_id == org))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Timesheet not found")
    return t


async def _lines(db, org, tid):
    return (await db.execute(select(TimesheetLine).where(
        TimesheetLine.org_id == org, TimesheetLine.timesheet_id == tid))).scalars().all()


def _roll_totals(t: Timesheet, lines):
    t.reg_hours = round(sum(float(l.reg_hours) for l in lines), 2)
    t.ot_hours = round(sum(float(l.ot_hours) for l in lines), 2)
    t.ot2_hours = round(sum(float(l.ot2_hours or 0) for l in lines), 2)
    t.stat_hours = round(sum(float(l.stat_hours) for l in lines), 2)
    # After a manual edit, keep the stat $ consistent with the (possibly edited) hours.
    t.stat_pay = round(t.stat_hours * float(t.hourly_rate or 0), 2)
    t.total_hours = round(t.reg_hours + t.ot_hours + t.ot2_hours + t.stat_hours, 2)


@router.get("/timesheets/{tid}")
async def timesheet_detail(tid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    t = await _ts_or_404(db, org, tid)
    if not _ts_visible(request, t):
        raise HTTPException(404, "Timesheet not found")
    return _ts_for(request, t, await _lines(db, org, tid))


class LineEdit(BaseModel):
    lines: list[dict]     # [{id?, work_date, reg_hours, ot_hours, stat_hours, note?}]


@router.patch("/timesheets/{tid}/lines")
async def edit_lines(tid: str, body: LineEdit, request: Request, db: AsyncSession = Depends(get_db)):
    """Adjust hours before submitting. Only editable while draft/rejected.
    Owner, their manager or HR only."""
    org = _org(request)
    t = await _ts_or_404(db, org, tid)
    if not _ts_visible(request, t):
        raise HTTPException(404, "Timesheet not found")
    if not _ts_can_edit(request, t):
        raise HTTPException(403, "Only the employee, their manager or HR can edit this timesheet")
    if t.status not in ("draft", "rejected"):
        raise HTTPException(409, f"Cannot edit a {t.status} timesheet")
    existing = {str(l.id): l for l in await _lines(db, org, tid)}
    for row in body.lines:
        lid = row.get("id")
        if lid and lid in existing:
            l = existing[lid]
            for k in ("reg_hours", "ot_hours", "ot2_hours", "stat_hours", "note", "cost_center"):
                if k in row:
                    setattr(l, k, row[k])
        else:
            db.add(TimesheetLine(org_id=org, timesheet_id=t.id,
                                 work_date=date.fromisoformat(row["work_date"]),
                                 reg_hours=row.get("reg_hours", 0), ot_hours=row.get("ot_hours", 0),
                                 stat_hours=row.get("stat_hours", 0), note=row.get("note"),
                                 cost_center=row.get("cost_center")))
    await db.flush()
    _roll_totals(t, await _lines(db, org, tid))
    await db.commit()
    return _ts_for(request, t, await _lines(db, org, tid))


@router.post("/timesheets/{tid}/submit")
async def submit_timesheet(tid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    t = await _ts_or_404(db, org, tid)
    if not _ts_visible(request, t):
        raise HTTPException(404, "Timesheet not found")
    if not _ts_can_edit(request, t):
        raise HTTPException(403, "Only the employee, their manager or HR can submit this timesheet")
    if t.status not in ("draft", "rejected"):
        raise HTTPException(409, f"Cannot submit a {t.status} timesheet")
    _roll_totals(t, await _lines(db, org, tid))
    t.status = "submitted"
    t.submitted_at = _now()
    await db.commit()
    return _ts_for(request, t)


class ReviewIn(BaseModel):
    note: str | None = None


@router.post("/timesheets/{tid}/approve")
async def approve_timesheet(tid: str, request: Request, body: ReviewIn = ReviewIn(),
                            db: AsyncSession = Depends(get_db)):
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only a manager or HR can approve timesheets")
    org = _org(request)
    t = await _ts_or_404(db, org, tid)
    _check_ts_reviewer(request, t)
    if t.status not in ("submitted", "draft"):
        raise HTTPException(409, f"Cannot approve a {t.status} timesheet")
    t.status = "approved"
    t.reviewed_by = _uid(request)
    t.reviewed_at = _now()
    t.review_note = body.note
    await db.commit()
    return _ts_for(request, t)


@router.post("/timesheets/{tid}/reject")
async def reject_timesheet(tid: str, request: Request, body: ReviewIn = ReviewIn(),
                           db: AsyncSession = Depends(get_db)):
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only a manager or HR can reject timesheets")
    org = _org(request)
    t = await _ts_or_404(db, org, tid)
    _check_ts_reviewer(request, t)
    t.status = "rejected"
    t.reviewed_by = _uid(request)
    t.reviewed_at = _now()
    t.review_note = body.note
    await db.commit()
    return _ts_for(request, t)


@router.post("/timesheets/{tid}/lock")
async def lock_timesheet(tid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Lock an approved timesheet — hours are frozen once fed to a pay run."""
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only a manager or HR can lock timesheets")
    org = _org(request)
    t = await _ts_or_404(db, org, tid)
    _check_ts_reviewer(request, t)
    if t.status != "approved":
        raise HTTPException(409, "Only an approved timesheet can be locked")
    t.status = "locked"
    t.locked_at = _now()
    await db.commit()
    try:                                                   # G3: outbound webhooks (best-effort)
        from shared.utils.events import emit as _g3_emit
        await _g3_emit(org, "timesheet.locked", {"timesheet_id": str(t.id),
                                                 "employee_id": str(getattr(t, "employee_id", "") or ""),
                                                 "period_start": str(getattr(t, "period_start", "") or ""),
                                                 "period_end": str(getattr(t, "period_end", "") or "")})
    except Exception:  # noqa: BLE001
        pass
    await _nudge_payroll_auditor(org, t)
    return _ts_for(request, t)


async def _nudge_payroll_auditor(org: str, t) -> None:
    """Tell the Payroll Auditor a timesheet was locked. The agent decides whether the pay PERIOD (of that pay
    frequency) is now fully locked and runs once per period; the 15-min poll stays as the safety net.
    Best-effort, internal identity, short timeout — a lock never fails because of it."""
    try:
        from shared.utils.internal import internal_headers
        async with httpx.AsyncClient(timeout=3.0) as c:
            await c.post(f"{settings.AI_PLATFORM_URL}/ai/internal/agents/attendance-locked",
                         headers=internal_headers(org, "time-attendance", role="payroll_admin"),
                         json={"org_id": str(org), "period_start": str(getattr(t, "period_start", "") or "")[:10],
                               "period_end": str(getattr(t, "period_end", "") or "")[:10],
                               "pay_frequency": getattr(t, "pay_frequency", None)})
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════ Regularizations ════════════════════════════════════

class RegIn(BaseModel):
    employee_id: str
    work_date: str
    reason: str
    requested_in: str | None = None
    requested_out: str | None = None
    note: str | None = None


def _valid_hhmm(v: str) -> bool:
    try:
        hh, mm = (int(x) for x in v.split(":"))
        return 0 <= hh < 24 and 0 <= mm < 60
    except (ValueError, AttributeError):
        return False


def _check_decider(request: Request, r) -> None:
    """HR / admin, or the employee's own manager — never the requester (2026-10-16)."""
    me = _emp(request) or ""
    if me and str(r.employee_id) == me:
        raise HTTPException(403, "You cannot decide your own correction")
    if _role(request) == "manager" and str(r.manager_id or "") != me:
        raise HTTPException(403, "Only this employee's manager or HR can decide")


@router.post("/regularizations", status_code=201)
async def request_regularization(body: RegIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    # 2026-10-16: an employee raises a correction for themselves only (HR may raise on behalf).
    if _role(request) not in ADMIN_ROLES and body.employee_id != (_emp(request) or ""):
        raise HTTPException(403, "You can only request a correction for your own attendance")
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(body.employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    try:
        wd = date.fromisoformat(body.work_date)
    except ValueError:
        raise HTTPException(422, "work_date must be YYYY-MM-DD")
    if wd > await _today(org):
        raise HTTPException(422, "A correction is for a day that has already started")
    for hhmm in (body.requested_in, body.requested_out):
        if hhmm and not _valid_hhmm(hhmm):
            raise HTTPException(422, "Times must be HH:MM")
    dup = (await db.execute(select(Regularization).where(
        Regularization.org_id == org, Regularization.employee_id == uuid.UUID(body.employee_id),
        Regularization.work_date == wd, Regularization.status == "pending"))).scalars().first()
    if dup:
        raise HTTPException(409, "A correction for this day is already waiting for a decision")
    r = Regularization(
        org_id=org, employee_id=uuid.UUID(body.employee_id),
        employee_name=f"{e['first_name']} {e['last_name']}",
        manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None,
        work_date=date.fromisoformat(body.work_date), reason=body.reason,
        requested_in=body.requested_in, requested_out=body.requested_out, note=body.note)
    db.add(r)
    await db.commit()
    return {"id": str(r.id), "status": r.status}


def _reg_out(r: Regularization) -> dict:
    return {"id": str(r.id), "employee_id": str(r.employee_id), "employee_name": r.employee_name,
            "work_date": r.work_date.isoformat(), "reason": r.reason,
            "requested_in": r.requested_in, "requested_out": r.requested_out,
            "note": r.note, "status": r.status,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None}


@router.get("/regularizations")
async def list_regularizations(request: Request, status: str | None = None,
                               db: AsyncSession = Depends(get_db)):
    org = _org(request)
    q = select(Regularization).where(Regularization.org_id == org)
    if status:
        q = q.where(Regularization.status == status)
    role, me = _role(request), _emp(request)
    if role == "manager" and me:
        q = q.where(Regularization.manager_id == me)
    elif role not in ADMIN_ROLES:                    # an employee sees only their own
        if not me:
            return []
        q = q.where(Regularization.employee_id == me)
    q = q.order_by(Regularization.created_at.desc())
    return [_reg_out(r) for r in (await db.execute(q)).scalars().all()]


@router.post("/regularizations/{rid}/approve")
async def approve_regularization(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only a manager or HR can approve")
    org = _org(request)
    r = (await db.execute(select(Regularization).where(
        Regularization.id == rid, Regularization.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Regularization not found")
    _check_decider(request, r)
    if r.status != "pending":
        raise HTTPException(409, f"This correction is already {r.status}")
    # Apply the correction: write in/out punches for the day, then recompute.
    emp = {e["id"]: e for e in await _fetch_employees(request)}.get(str(r.employee_id))
    if emp:
        by_emp, default = await _schedule_map(db, org)
        tzname = (by_emp.get(str(r.employee_id)) or default or {}).get("timezone") or "America/Toronto"
        for hhmm, direction in ((r.requested_in, "in"), (r.requested_out, "out")):
            if hhmm:
                try:
                    hh, mm = map(int, hhmm.split(":"))
                    # the employee typed WALL-CLOCK time in their shift's timezone (was stored as UTC)
                    tz = ZoneInfo(tzname) if ZoneInfo else timezone.utc
                    at = datetime.combine(r.work_date, time(hh, mm), tzinfo=tz).astimezone(timezone.utc)
                    db.add(Punch(org_id=org, employee_id=r.employee_id, work_date=r.work_date,
                                 at=at, direction=direction, source="admin",
                                 note="regularization", created_by=_uid(request)))
                except Exception:
                    pass
        await db.flush()
        holidays = {date.fromisoformat(h["holiday_date"][:10]) for h in await _fetch_stat_holidays(request)
                    if h.get("holiday_date") and (h.get("province") in (None, emp.get("province_of_employment")))}
        await _recompute_day(db, org, emp, by_emp.get(emp["id"]), default, r.work_date, holidays)
    r.status = "approved"
    r.reviewed_by = _uid(request)
    r.reviewed_at = _now()
    await db.commit()
    return _reg_out(r)


@router.post("/regularizations/{rid}/reject")
async def reject_regularization(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only a manager or HR can reject")
    org = _org(request)
    r = (await db.execute(select(Regularization).where(
        Regularization.id == rid, Regularization.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Regularization not found")
    _check_decider(request, r)
    if r.status != "pending":
        raise HTTPException(409, f"This correction is already {r.status}")
    r.status = "rejected"
    r.reviewed_by = _uid(request)
    r.reviewed_at = _now()
    await db.commit()
    return _reg_out(r)


@router.get("/approvals")
async def approvals_queue(request: Request, db: AsyncSession = Depends(get_db)):
    """A manager/HR's pending work: submitted timesheets + pending regularizations,
    scoped to their reports (managers) or the whole org (HR/admin)."""
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Approvals are for managers and HR")
    org = _org(request)
    role, me = _role(request), _emp(request)
    tq = select(Timesheet).where(Timesheet.org_id == org, Timesheet.status == "submitted")
    rq = select(Regularization).where(Regularization.org_id == org, Regularization.status == "pending")
    if role == "manager" and me:
        tq = tq.where(Timesheet.manager_id == me)
        rq = rq.where(Regularization.manager_id == me)
    bids = await _branch_emp_ids(request)
    if bids is not None:
        tq = tq.where(Timesheet.employee_id.in_(bids))
        rq = rq.where(Regularization.employee_id.in_(bids))
    ts = [_ts_for(request, t) for t in (await db.execute(tq)).scalars().all()]
    regs = [_reg_out(r) for r in (await db.execute(rq)).scalars().all()]
    return {"timesheets": ts, "regularizations": regs,
            "pending_total": len(ts) + len(regs)}


# ══════════════════════════ Payroll feed (the killer) ══════════════════════════

def _feed_entries(t: Timesheet) -> dict:
    """One timesheet → pay-run earnings (REG / OT 1.5× / OT2 2× / STAT). Hourly REG
    carries actual pay (payroll zeroes the synthetic base); salary omits REG (base
    salary stands). STAT is the computed average-daily-wage amount, not hours×rate."""
    rate = float(t.hourly_rate or 0)
    earnings = []
    if t.pay_type == "hourly" and float(t.reg_hours) > 0:
        earnings.append({"code": "REG", "amount": round(float(t.reg_hours) * rate, 2), "hours": float(t.reg_hours)})
    if float(t.ot_hours) > 0:
        earnings.append({"code": "OT", "amount": round(float(t.ot_hours) * rate * 1.5, 2), "hours": float(t.ot_hours)})
    if float(t.ot2_hours or 0) > 0:
        earnings.append({"code": "OT2", "amount": round(float(t.ot2_hours) * rate * 2.0, 2), "hours": float(t.ot2_hours)})
    stat_amt = float(t.stat_pay or 0) or round(float(t.stat_hours) * rate, 2)
    if stat_amt > 0:
        earnings.append({"code": "STAT", "amount": round(stat_amt, 2), "hours": float(t.stat_hours)})
    return {"earnings": earnings}


@router.get("/payroll-feed")
async def payroll_feed(request: Request, pay_frequency: str, period_start: str, period_end: str,
                       db: AsyncSession = Depends(get_db)):
    """APPROVED/LOCKED timesheet hours → pay-run `entries`. This is what the
    payroll cockpit posts into POST /payroll/runs so worked hours become pay."""
    if not _is_office(request):
        raise HTTPException(403, "Only the pay office or HR can read the payroll hours feed")
    org = _org(request)
    try:
        p_start = date.fromisoformat(period_start)
        p_end = date.fromisoformat(period_end)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    # Match every approved/locked timesheet whose period falls WITHIN the pay-run
    # window — exact-match biweekly runs AND weekly (My Time) timesheets both qualify,
    # and multiple per employee are aggregated.
    rows = (await db.execute(select(Timesheet).where(
        Timesheet.org_id == org, Timesheet.pay_frequency == pay_frequency,
        Timesheet.period_start >= p_start, Timesheet.period_end <= p_end,
        Timesheet.status.in_(["approved", "locked"])))).scalars().all()
    agg: dict = {}
    for t in rows:
        a = agg.setdefault(str(t.employee_id), {"reg": 0.0, "ot": 0.0, "ot2": 0.0, "stat": 0.0,
                                                "stat_pay": 0.0, "total": 0.0, "rate": float(t.hourly_rate or 0),
                                                "pay_type": t.pay_type, "timesheets": 0})
        a["reg"] += float(t.reg_hours); a["ot"] += float(t.ot_hours); a["ot2"] += float(t.ot2_hours or 0)
        a["stat"] += float(t.stat_hours); a["stat_pay"] += float(t.stat_pay or 0)
        a["total"] += float(t.total_hours); a["timesheets"] += 1

    entries, hours = {}, {}
    for eid, a in agg.items():
        rate = a["rate"]
        earnings = []
        if a["pay_type"] == "hourly" and a["reg"] > 0:
            earnings.append({"code": "REG", "amount": round(a["reg"] * rate, 2), "hours": round(a["reg"], 2)})
        if a["ot"] > 0:
            earnings.append({"code": "OT", "amount": round(a["ot"] * rate * 1.5, 2), "hours": round(a["ot"], 2)})
        if a["ot2"] > 0:
            earnings.append({"code": "OT2", "amount": round(a["ot2"] * rate * 2.0, 2), "hours": round(a["ot2"], 2)})
        stat_amt = a["stat_pay"] or round(a["stat"] * rate, 2)
        if stat_amt > 0:
            earnings.append({"code": "STAT", "amount": round(stat_amt, 2), "hours": round(a["stat"], 2)})
        entries[eid] = {"earnings": earnings}
        hours[eid] = {"reg": round(a["reg"], 2), "ot": round(a["ot"], 2), "ot2": round(a["ot2"], 2),
                      "stat": round(a["stat"], 2), "stat_pay": round(a["stat_pay"], 2),
                      "total": round(a["total"], 2), "timesheets": a["timesheets"]}
    return {"pay_frequency": pay_frequency, "period_start": period_start, "period_end": period_end,
            "employee_count": len(entries), "entries": entries, "hours": hours,
            "employee_ids": list(entries.keys())}


@router.get("/readiness")
async def payroll_readiness(request: Request, pay_frequency: str, period_start: str, period_end: str,
                            db: AsyncSession = Depends(get_db)):
    """The payroll cockpit's 'Attendance & timesheets' gate: how many employees on
    this frequency have an approved/locked timesheet for the period, and who's missing."""
    if not _is_office(request):
        raise HTTPException(403, "Only the pay office or HR can read payroll readiness")
    org = _org(request)
    try:
        p_start = date.fromisoformat(period_start)
        p_end = date.fromisoformat(period_end)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    emps = [e for e in await _fetch_employees(request)
            if e.get("pay_frequency") == pay_frequency and e.get("status") != "terminated"]
    # Any timesheet whose period falls within the pay-run window counts (weekly
    # My Time timesheets + biweekly runs alike). An employee is ready once they
    # have an approved/locked timesheet in the window.
    rows = (await db.execute(select(Timesheet).where(
        Timesheet.org_id == org, Timesheet.pay_frequency == pay_frequency,
        Timesheet.period_start >= p_start, Timesheet.period_end <= p_end))).scalars().all()
    by_emp: dict = {}
    for t in rows:
        eid = str(t.employee_id)
        rank = {"approved": 3, "locked": 3, "submitted": 2, "draft": 1, "rejected": 0}
        if rank.get(t.status, 0) >= rank.get(by_emp.get(eid, ""), -1):
            by_emp[eid] = t.status          # keep the most-advanced status per employee
    ready_ids = {eid for eid, st in by_emp.items() if st in ("approved", "locked")}
    submitted = {eid for eid, st in by_emp.items() if st == "submitted"}
    missing = [{"employee_id": e["id"], "name": f"{e['first_name']} {e['last_name']}",
                "status": by_emp.get(e["id"], "none")}
               for e in emps if e["id"] not in ready_ids]
    total = len(emps)
    return {"pay_frequency": pay_frequency, "period_start": period_start, "period_end": period_end,
            "total_employees": total, "ready": len(ready_ids), "submitted": len(submitted),
            "missing_count": len(missing), "missing": missing,
            "all_ready": total > 0 and len(ready_ids) == total,
            "coverage_pct": round(100 * len(ready_ids) / total) if total else 0}


# ══════════════════════════ Statutory rules reference ══════════════════════════

@router.get("/rules")
async def rules(request: Request, province: str = "ON", year: int = 2026):
    """The effective ESA rules for a province/year — powers the UI's rules panel.
    DESIGN REFERENCES (TODO-VERIFY): confirm against the province's ESA before
    real payroll. See constants_ca_time.py."""
    _org(request)
    prov = (province or "ON").upper()
    labels = {
        "ON": "Ontario", "BC": "British Columbia", "AB": "Alberta", "QC": "Quebec",
        "MB": "Manitoba", "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick",
        "NL": "Newfoundland and Labrador", "PE": "Prince Edward Island", "NT": "Northwest Territories",
        "NU": "Nunavut", "YT": "Yukon",
    }
    if prov not in labels:
        raise HTTPException(422, "province must be one of the 13 Canadian province / territory codes")
    ot = ot_rules(prov, year)
    st = stat_rules(prov, year)
    br = break_rules(prov, year)
    label = labels[prov]
    # human summary of the OT trigger
    if ot["combine"] == "weekly":
        ot_summary = f">{ot['weekly_ot_after']} h/week → 1.5×"
    elif ot["combine"] == "greater_of":
        ot_summary = f">{ot['daily_ot_after']} h/day OR >{ot['weekly_ot_after']} h/week (greater of) → 1.5×"
    else:
        dbl = f", >{ot['daily_double_after']} h/day → 2×" if ot.get("daily_double_after") else ""
        ot_summary = f">{ot['daily_ot_after']} h/day AND >{ot['weekly_ot_after']} h/week → 1.5×{dbl}"
    return {
        "province": prov, "province_label": label, "year": year,
        "disclaimer": "Design references — verify against the province's ESA before real payroll.",
        "overtime": {**ot, "summary": ot_summary},
        "stat_holiday": st,
        "breaks": br,
    }


# ══════════════════════════ Roster / scheduling (Phase 4) ══════════════════════

def _shift_out(s: RosterShift) -> dict:
    return {"id": str(s.id), "employee_id": str(s.employee_id), "work_date": s.work_date.isoformat(),
            "start_time": s.start_time, "end_time": s.end_time, "planned_hours": float(s.planned_hours or 0),
            "location": s.location, "status": s.status, "note": s.note,
            # G2: generated rows are ordinary rows; kind=off is a weekly off / holiday from a rotation
            "kind": s.kind or "work", "source": s.source or "manual", "shift_code": s.shift_code,
            "pattern_id": str(s.pattern_id) if s.pattern_id else None}


@router.get("/roster")
async def list_roster(request: Request, start: str, end: str, db: AsyncSession = Depends(get_db)):
    """Published + planned shifts in a date window, plus the employee roster the
    grid is drawn over (managers see their reports, HR/admin the whole org)."""
    org = _org(request)
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    emps = await _fetch_employees(request)
    role, me = _role(request), _emp(request)
    if role == "manager" and me:
        emps = [e for e in emps if str(e.get("manager_id")) == me]
    emps = [e for e in emps if e.get("status") != "terminated"]
    shifts = (await db.execute(select(RosterShift).where(
        RosterShift.org_id == org, RosterShift.work_date >= d0, RosterShift.work_date <= d1))).scalars().all()
    return {"start": start, "end": end,
            "employees": [{"employee_id": e["id"], "name": f"{e['first_name']} {e['last_name']}",
                           "employee_number": e.get("employee_number"), "department": e.get("department")}
                          for e in sorted(emps, key=lambda x: f"{x['first_name']} {x['last_name']}")],
            "shifts": [_shift_out(s) for s in shifts]}


class ShiftIn(BaseModel):
    employee_id: str
    work_date: str
    start_time: str = "09:00"
    end_time: str = "17:00"
    planned_hours: float = Field(8, ge=0, le=24)
    location: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=200)
    # 2026-10-24 forms: a hand-set day off (ta_roster_shifts.kind already exists) — "work" | "off"
    kind: str = "work"


class RosterAssignIn(BaseModel):
    shifts: list[ShiftIn]


@router.post("/roster/assign", status_code=201)
async def assign_roster(body: RosterAssignIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Upsert planned shifts (one per employee+date). Managers/HR only."""
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only managers/HR can build the roster")
    org = _org(request)
    n = 0
    for sh in body.shifts:
        wd = date.fromisoformat(sh.work_date)
        kind = (sh.kind or "work").lower()
        if kind not in ("work", "off"):
            raise HTTPException(422, "Day type must be 'work' or 'off'")
        if kind == "off":   # same shape as a rotation's day off (rotation_logic): no hours, OFF code
            sh.start_time, sh.end_time, sh.planned_hours = "00:00", "00:00", 0
        elif not (_valid_hhmm(sh.start_time) and _valid_hhmm(sh.end_time)):
            raise HTTPException(422, "Times must be HH:MM")
        code = "OFF" if kind == "off" else None
        existing = (await db.execute(select(RosterShift).where(
            RosterShift.org_id == org, RosterShift.employee_id == sh.employee_id,
            RosterShift.work_date == wd))).scalar_one_or_none()
        if existing:
            existing.start_time, existing.end_time = sh.start_time, sh.end_time
            existing.planned_hours, existing.location, existing.note = sh.planned_hours, sh.location, sh.note
            existing.status = "planned"
            existing.source, existing.kind, existing.shift_code = "manual", kind, code   # G2: a hand edit wins over the pattern
        else:
            db.add(RosterShift(org_id=org, employee_id=uuid.UUID(sh.employee_id), work_date=wd,
                               start_time=sh.start_time, end_time=sh.end_time, planned_hours=sh.planned_hours,
                               location=sh.location, note=sh.note, status="planned", source="manual",
                               kind=kind, shift_code=code))
        n += 1
    await db.commit()
    return {"upserted": n}


class PublishIn(BaseModel):
    start: str
    end: str


@router.post("/roster/publish")
async def publish_roster(body: PublishIn, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only managers/HR can publish the roster")
    org = _org(request)
    d0, d1 = date.fromisoformat(body.start), date.fromisoformat(body.end)
    rows = (await db.execute(select(RosterShift).where(
        RosterShift.org_id == org, RosterShift.work_date >= d0, RosterShift.work_date <= d1,
        RosterShift.status == "planned"))).scalars().all()
    for s in rows:
        s.status = "published"
    await db.commit()
    return {"published": len(rows)}


@router.delete("/roster/{sid}")
async def delete_shift(sid: str, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in APPROVER_ROLES:
        raise HTTPException(403, "Only managers/HR can edit the roster")
    org = _org(request)
    s = (await db.execute(select(RosterShift).where(
        RosterShift.id == sid, RosterShift.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Shift not found")
    await db.delete(s)
    await db.commit()
    return {"ok": True}


# ══════════════════════════ Analytics (Phase 4) ════════════════════════════════

@router.get("/analytics")
async def analytics(request: Request, start: str | None = None, end: str | None = None,
                    db: AsyncSession = Depends(get_db)):
    """Absenteeism, punctuality and overtime analytics over a window — also the
    data the AI watchdogs (overtime_anomaly, stat_holiday) poll.
    HR / pay office = whole org · manager = own team (self + direct reports) · others 403."""
    team_only = not _is_office(request)
    if team_only and _role(request) != "manager":
        raise HTTPException(403, "Attendance analytics are for managers, HR and the pay office")
    org = _org(request)
    d1 = date.fromisoformat(end) if end else await _today(org)
    d0 = date.fromisoformat(start) if start else (d1 - timedelta(days=27))
    emps = {e["id"]: e for e in await _fetch_employees(request) if e.get("status") != "terminated"}
    if team_only:
        me = _emp(request) or ""
        emps = {k: e for k, e in emps.items()
                if me and (str(k) == me or str(e.get("manager_id") or "") == me)}
    by_emp, default = await _schedule_map(db, org)
    holidays_all = await _fetch_stat_holidays(request)

    days = (await db.execute(select(AttendanceDay).where(
        AttendanceDay.org_id == org, AttendanceDay.work_date >= d0,
        AttendanceDay.work_date <= d1))).scalars().all()
    ot_rows = (await db.execute(
        select(TimesheetLine.work_date, TimesheetLine.ot_hours, TimesheetLine.ot2_hours, Timesheet.employee_id)
        .join(Timesheet, TimesheetLine.timesheet_id == Timesheet.id)
        .where(Timesheet.org_id == org, TimesheetLine.work_date >= d0, TimesheetLine.work_date <= d1))).all()
    if team_only:                                   # a manager's aggregates cover their own team only
        days = [d for d in days if str(d.employee_id) in emps]
        ot_rows = [r for r in ot_rows if str(r[3]) in emps]

    active_ids = {str(d.employee_id) for d in days}
    attended = defaultdict(set)
    late_days = late_min_total = present_rows = 0
    total_hours = 0.0
    daily = defaultdict(lambda: {"present": 0, "late": 0, "ot": 0.0})
    for d in days:
        eid = str(d.employee_id)
        wd = d.work_date
        if float(d.actual_hours) > 0:
            attended[eid].add(wd); present_rows += 1
            total_hours += float(d.actual_hours); daily[wd.isoformat()]["present"] += 1
        if d.status == "late":
            late_days += 1; late_min_total += int(d.late_minutes or 0); daily[wd.isoformat()]["late"] += 1

    def _scheduled_days(emp, s):
        cnt = 0
        prov = emp.get("province_of_employment")
        hol = {date.fromisoformat(h["holiday_date"][:10]) for h in holidays_all
               if h.get("holiday_date") and h.get("province") in (None, prov)}
        dd = d0
        dow = (s or {}).get("days_of_week") if s else None
        while dd <= d1:
            workday = (dd.weekday() in dow) if dow else (dd.weekday() < 5)
            if workday and dd not in hol:
                cnt += 1
            dd += timedelta(days=1)
        return cnt

    scheduled_total = absent_total = 0
    for eid in active_ids:
        e = emps.get(eid)
        if not e:
            continue
        sd = _scheduled_days(e, by_emp.get(eid) or default)
        scheduled_total += sd
        absent_total += max(0, sd - len(attended.get(eid, set())))

    ot_by_emp = defaultdict(lambda: {"ot": 0.0, "ot2": 0.0})
    for wd, ot, ot2, eid in ot_rows:
        ot_by_emp[str(eid)]["ot"] += float(ot or 0)
        ot_by_emp[str(eid)]["ot2"] += float(ot2 or 0)
        daily[wd.isoformat()]["ot"] += float(ot or 0) + float(ot2 or 0)
    ot_total = round(sum(v["ot"] for v in ot_by_emp.values()), 2)
    ot2_total = round(sum(v["ot2"] for v in ot_by_emp.values()), 2)
    ot_people = [(eid, v["ot"] + v["ot2"]) for eid, v in ot_by_emp.items() if (v["ot"] + v["ot2"]) > 0]
    ot_people.sort(key=lambda x: -x[1])
    avg_ot = (sum(h for _, h in ot_people) / len(ot_people)) if ot_people else 0

    def _row(eid, hrs):
        e = emps.get(eid, {})
        return {"employee_id": eid, "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or "—",
                "department": e.get("department"), "ot_hours": round(hrs, 2),
                "spike": hrs >= max(6.0, 1.5 * avg_ot)}
    top_ot = [_row(eid, hrs) for eid, hrs in ot_people[:10]]
    anomalies = [r for r in top_ot if r["spike"]]

    dept = defaultdict(lambda: {"present": 0, "hours": 0.0, "ot": 0.0, "late": 0})
    for d in days:
        dp = emps.get(str(d.employee_id), {}).get("department") or "—"
        if float(d.actual_hours) > 0:
            dept[dp]["present"] += 1; dept[dp]["hours"] += float(d.actual_hours)
        if d.status == "late":
            dept[dp]["late"] += 1
    for eid, v in ot_by_emp.items():
        dp = emps.get(eid, {}).get("department") or "—"
        dept[dp]["ot"] += v["ot"] + v["ot2"]
    by_department = [{"department": k, "present_days": v["present"], "hours": round(v["hours"], 1),
                      "ot_hours": round(v["ot"], 1), "late": v["late"]}
                     for k, v in sorted(dept.items(), key=lambda x: -x[1]["hours"])]

    today = await _today(org)
    upcoming = sorted({(h["holiday_date"][:10], h["name"]) for h in holidays_all
                       if h.get("holiday_date") and today.isoformat() <= h["holiday_date"][:10] <= (today + timedelta(days=21)).isoformat()})

    daily_series = [{"date": k, "present": v["present"], "late": v["late"], "ot": round(v["ot"], 2)}
                    for k, v in sorted(daily.items())]
    return {
        "start": d0.isoformat(), "end": d1.isoformat(), "period_days": (d1 - d0).days + 1,
        "headline": {"employees_tracked": len(active_ids), "attendance_days": present_rows,
                     "avg_daily_hours": round(total_hours / present_rows, 2) if present_rows else 0,
                     "total_hours": round(total_hours, 1)},
        "absenteeism": {"scheduled_days": scheduled_total, "absent_days": absent_total,
                        "rate_pct": round(100 * absent_total / scheduled_total, 1) if scheduled_total else 0,
                        "late_days": late_days, "late_rate_pct": round(100 * late_days / present_rows, 1) if present_rows else 0,
                        "avg_late_min": round(late_min_total / late_days) if late_days else 0},
        "overtime": {"ot_hours": ot_total, "ot2_hours": ot2_total, "employees_with_ot": len(ot_people),
                     "avg_ot_hours": round(avg_ot, 2), "top": top_ot},
        "anomalies": {"overtime": anomalies, "count": len(anomalies)},
        "by_department": by_department,
        "daily": daily_series,
        "upcoming_stat": {"count": len(upcoming), "active_employees": len(emps),
                          "holidays": [{"date": d, "name": n} for d, n in upcoming]},
    }


# ══════════════════════════ Employee self-entry (My Time) ══════════════════════

class EntryLine(BaseModel):
    work_date: str
    project_id: str
    project_name: str | None = None
    billable: bool = False
    hours: float
    note: str | None = None


class EntryIn(BaseModel):
    period_start: str
    period_end: str
    lines: list[EntryLine]
    submit: bool = False          # True = go straight to submitted (skip draft)


@router.get("/my-timesheet")
async def my_timesheet(request: Request, period_start: str, period_end: str,
                       db: AsyncSession = Depends(get_db)):
    """The caller's OWN timesheet for a period (project lines), for the My Time page.
    Leave + holidays are fetched separately by the client and shown read-only."""
    org = _org(request)
    me = _emp(request)
    if not me:
        raise HTTPException(400, "Your login isn't linked to an employee record")
    try:
        p_start, p_end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    t = (await db.execute(select(Timesheet).where(
        Timesheet.org_id == org, Timesheet.employee_id == me,
        Timesheet.period_start == p_start, Timesheet.period_end == p_end))).scalar_one_or_none()
    return {"employee_id": me, "period_start": period_start, "period_end": period_end,
            "timesheet": _ts_out(t, await _lines(db, org, t.id)) if t else None}


@router.post("/timesheets/entry", status_code=201)
async def submit_entry(body: EntryIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Employee self-service: book worked hours to projects for a period. Splits
    REG / OT (1.5×) / OT2 (2×) via the provincial ESA engine across each day's
    total, distributes the split across that day's project lines, and adds stat-
    holiday pay. Replaces the caller's draft for the period (409 once submitted)."""
    org = _org(request)
    me = _emp(request)
    if not me:
        raise HTTPException(400, "Your login isn't linked to an employee record")
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(me)
    if not e:
        raise HTTPException(404, "Employee not found")
    try:
        p_start, p_end = date.fromisoformat(body.period_start), date.fromisoformat(body.period_end)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")

    # validate the entered lines
    clean = []
    for ln in body.lines:
        if float(ln.hours) <= 0:
            continue
        if not ln.project_id:
            raise HTTPException(400, "Every entry must be booked to a project")
        wd = date.fromisoformat(ln.work_date)
        if not (p_start <= wd <= p_end):
            raise HTTPException(400, f"{ln.work_date} is outside the pay period")
        clean.append((wd, ln))
    if not clean:
        raise HTTPException(400, "Add at least one line with hours")

    # existing timesheet for the period?
    t = (await db.execute(select(Timesheet).where(
        Timesheet.org_id == org, Timesheet.employee_id == me,
        Timesheet.period_start == p_start, Timesheet.period_end == p_end))).scalar_one_or_none()
    if t and t.status not in ("draft", "rejected"):
        raise HTTPException(409, f"This period's timesheet is already {t.status}")

    prov = e.get("province_of_employment", "ON")
    year = p_end.year
    rate = _eff_hourly_rate(e)
    std_daily = round(float(e.get("standard_hours_per_week") or 40) / 5, 2)
    ot_eligible = e.get("overtime_eligible", True)

    # daily worked totals → ESA split per ISO week → attribute to dates
    day_worked = defaultdict(float)
    for wd, ln in clean:
        day_worked[wd] += float(ln.hours)
    by_week = defaultdict(list)
    for wd, hrs in day_worked.items():
        by_week[wd.isocalendar()[:2]].append((wd, hrs))
    reg_by_date, ot_by_date, ot2_by_date = {}, {}, {}
    for _wk, wk in by_week.items():
        wk.sort(key=lambda x: x[0])
        ot15_w, ot20_w = (0.0, 0.0) if not ot_eligible else overtime_split(prov, [h for _, h in wk], year)[1:]
        rem20, rem15 = ot20_w, ot15_w
        for d0, h in sorted(wk, key=lambda x: -x[1]):
            if rem20 <= 0:
                break
            take = min(h, rem20); ot2_by_date[d0] = round(take, 2); rem20 = round(rem20 - take, 2)
        for d0, h in reversed(wk):
            if rem15 <= 0:
                break
            avail = round(h - ot2_by_date.get(d0, 0.0), 2)
            take = min(avail, rem15); ot_by_date[d0] = round(take, 2); rem15 = round(rem15 - take, 2)
        for d0, h in wk:
            reg_by_date[d0] = round(h - ot_by_date.get(d0, 0.0) - ot2_by_date.get(d0, 0.0), 2)

    # stat-holiday pay for holidays in the period (auto — employee never enters it)
    holidays_all = await _fetch_stat_holidays(request)
    hol = {date.fromisoformat(h["holiday_date"][:10]) for h in holidays_all
           if h.get("holiday_date") and h.get("province") in (None, prov)}
    stat_lb = stat_rules(prov, year)["lookback_days"]
    wages_lb, days_worked_lb = 0.0, 0
    if hol:
        lb_rows = (await db.execute(select(AttendanceDay).where(
            AttendanceDay.org_id == org, AttendanceDay.employee_id == me,
            AttendanceDay.work_date >= p_start - timedelta(days=stat_lb),
            AttendanceDay.work_date < p_start))).scalars().all()
        for d in lb_rows:
            if float(d.actual_hours) > 0:
                wages_lb += float(d.actual_hours) * rate
                days_worked_lb += 1

    # rebuild the timesheet + lines
    if not t:
        t = Timesheet(org_id=org, employee_id=uuid.UUID(me), period_start=p_start, period_end=p_end)
        db.add(t)
        await db.flush()
    else:
        for old in await _lines(db, org, t.id):
            await db.delete(old)
        await db.flush()

    t.employee_name = f"{e['first_name']} {e['last_name']}"
    t.employee_number = e.get("employee_number")
    t.manager_id = uuid.UUID(e["manager_id"]) if e.get("manager_id") else None
    t.manager_name = e.get("manager_name")
    t.province = prov
    t.pay_type = e.get("pay_type")
    t.hourly_rate = rate
    t.pay_frequency = e.get("pay_frequency")

    reg_sum = ot_sum = ot2_sum = stat_hsum = stat_paysum = 0.0
    # worked project lines — distribute the day's ESA split across the day's projects
    for wd, ln in clean:
        dw = day_worked[wd] or 1
        share = float(ln.hours) / dw
        l_reg = round(reg_by_date.get(wd, float(ln.hours)) * share, 2)
        l_ot = round(ot_by_date.get(wd, 0.0) * share, 2)
        l_ot2 = round(ot2_by_date.get(wd, 0.0) * share, 2)
        reg_sum += l_reg; ot_sum += l_ot; ot2_sum += l_ot2
        db.add(TimesheetLine(org_id=org, timesheet_id=t.id, work_date=wd,
                             reg_hours=l_reg, ot_hours=l_ot, ot2_hours=l_ot2, stat_hours=0,
                             hours=round(float(ln.hours), 2), project_id=ln.project_id,
                             project_name=ln.project_name, billable=bool(ln.billable),
                             cost_center=e.get("cost_center"), note=ln.note))
    # stat lines (no project) for holidays observed in the period
    for hd in sorted(hol):
        if not (p_start <= hd <= p_end):
            continue
        worked = day_worked.get(hd, 0) > 0
        before = day_worked.get(hd - timedelta(days=1), 0) > 0 or days_worked_lb > 0
        after = day_worked.get(hd + timedelta(days=1), 0) > 0 or worked
        if not stat_eligibility(prov, before, after, days_worked_lb, year):
            continue
        pay = stat_holiday_pay(prov, wages_lb, days_worked_lb, rate, std_daily, year)
        stat_h = round(pay / rate, 2) if rate else std_daily
        stat_hsum += stat_h; stat_paysum += pay
        db.add(TimesheetLine(org_id=org, timesheet_id=t.id, work_date=hd,
                             reg_hours=0, ot_hours=0, ot2_hours=0, stat_hours=round(stat_h, 2),
                             hours=0, project_name="Statutory holiday", note="Stat holiday"))

    t.reg_hours = round(reg_sum, 2)
    t.ot_hours = round(ot_sum, 2)
    t.ot2_hours = round(ot2_sum, 2)
    t.stat_hours = round(stat_hsum, 2)
    t.stat_pay = round(stat_paysum, 2)
    t.total_hours = round(reg_sum + ot_sum + ot2_sum + stat_hsum, 2)
    if body.submit:
        t.status = "submitted"
        t.submitted_at = _now()
    else:
        t.status = "draft"
    await db.commit()
    await db.refresh(t)
    return _ts_out(t, await _lines(db, org, t.id))
