"""Shift rotation patterns · auto-generated roster · shift swaps (gap G2, 2026-10-23) — /time/rotations/*.

    GET  /time/rotations/templates                         ready-made patterns (3-shift A/B/C, 4-on-2-off, weekly offs …)
    GET  /time/rotations/patterns · POST · PUT /{id} · DELETE /{id}       HR defines patterns
    GET  /time/rotations/patterns/{id}/preview?start=&days=&offset=&crews=&crew_offset=
    POST /time/rotations/preview                            preview an unsaved pattern (editor)
    GET  /time/rotations/assignments · POST · DELETE /{id}  HR (anyone) / manager (own reports)
    POST /time/rotations/generate {start?, weeks?, employee_ids?, dry_run, overwrite_manual?, overwrite_published?}
    GET/PUT /time/rotations/settings                        min rest (company rule), weeks ahead, nightly auto-generate
    GET  /time/rotations/my-shifts?start=&end=              the caller's published shifts
    GET  /time/rotations/swap-options?shift_id=             colleagues' shifts the caller could swap with (+ problems)
    GET  /time/rotations/swaps?scope=mine|team|all
    POST /time/rotations/swaps {my_shift_id, partner_shift_id, reason}
    POST /time/rotations/swaps/{id}/accept|decline          the colleague
    POST /time/rotations/swaps/{id}/approve|reject          the manager (also the one-inbox callback)
    POST /time/rotations/swaps/{id}/cancel                  the requester / HR

Generated shifts are ordinary ta_roster_shifts rows (source = pattern, status = planned): the Shifts &
roster grid shows them, Publish makes them live, and once published the
attendance engine plans the day from them (routers._recompute_day → roster_overlay) like any manual row.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import rotation_logic as RL
from database import SessionLocal, get_db
from models import RosterShift
from rotation_models import RotationAssignment, RotationPattern, RotationSettings, ShiftSwap
from shared.utils.org_time import org_today
from shared.utils.roles import HR_WRITE, has_role

log = logging.getLogger("lexora.time.rotations")
router = APIRouter(prefix="/time/rotations", tags=["time-rotations"])

TEAM_ROLES = {"manager"}
SWAP_OPEN = ("pending_partner", "pending_manager")


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _me(request: Request) -> str:
    return (request.headers.get("x-employee-id") or "").strip()


def _uid(request: Request) -> str:
    return request.headers.get("x-user-id") or ""


def _who(request: Request) -> str:
    return request.headers.get("x-user-email") or _uid(request) or "system"


def _is_hr(request: Request) -> bool:
    return has_role(_role(request), HR_WRITE)


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **extra})


def _require_hr(request: Request, what: str) -> None:
    if not _is_hr(request):
        raise HTTPException(403, {"code": "TA_ROTA_HR_ONLY", "message": f"Only HR can {what}."})


def _require_team(request: Request, what: str) -> None:
    if not (_is_hr(request) or _role(request) in TEAM_ROLES):
        raise HTTPException(403, {"code": "TA_ROTA_TEAM_ONLY", "message": f"Only HR or a manager can {what}."})


def _uuid(v, what="id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise _err(404, "TA_ROTA_NOT_FOUND", f"That {what} was not found.")


def _day(v, what="date") -> date:
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        raise _err(422, "TA_ROTA_BAD_DATE", f"The {what} must be a date (YYYY-MM-DD).")


# ══════════════════════════ shared loaders (SQL on the shared DB: work with or without a request) ═══

async def employees_sql(db, org: str) -> dict[str, dict]:
    rows = (await db.execute(text(
        "SELECT id, first_name, last_name, employee_number, gender, department, manager_id, status, province_of_employment "
        "FROM employees WHERE org_id = CAST(:org AS uuid)"), {"org": str(org)})).mappings().all()
    return {str(r["id"]): {**dict(r), "id": str(r["id"]), "manager_id": str(r["manager_id"]) if r["manager_id"] else None}
            for r in rows}


async def leave_sql(db, org: str, d0: date, d1: date) -> dict[str, set]:
    out: dict[str, set] = {}
    try:
        rows = (await db.execute(text(
            "SELECT employee_id, start_date, end_date, half_day FROM leave_requests WHERE org_id = CAST(:org AS uuid) "
            "AND status = 'approved' AND end_date >= :d0 AND start_date <= :d1"), {"org": str(org), "d0": d0, "d1": d1})).mappings().all()
    except Exception:  # noqa: BLE001 — leave module not installed
        await db.rollback()
        return out
    for r in rows:
        if r["half_day"]:
            continue                      # a half day still works the shift's other half — planned as usual
        d = max(r["start_date"], d0)
        while d <= min(r["end_date"], d1):
            out.setdefault(str(r["employee_id"]), set()).add(d)
            d += timedelta(days=1)
    return out


async def holidays_sql(db, org: str, d0: date, d1: date, employees: dict[str, dict]) -> dict[str, dict]:
    try:
        rows = (await db.execute(text(
            "SELECT holiday_date, province, name, COALESCE(is_optional, false) AS is_optional, COALESCE(is_active, true) AS is_active "
            "FROM master_stat_holidays WHERE org_id = CAST(:org AS uuid) AND holiday_date BETWEEN :d0 AND :d1"),
            {"org": str(org), "d0": d0, "d1": d1})).mappings().all()
    except Exception:  # noqa: BLE001
        await db.rollback()
        return {}
    hols = [r for r in rows if r["is_active"] and not r["is_optional"] and r["holiday_date"]]
    out: dict[str, dict] = {}
    for eid, e in employees.items():
        st = (e.get("province_of_employment") or "").upper()
        for h in hols:
            if not h["province"] or (h["province"] or "").upper() == st:
                out.setdefault(eid, {})[h["holiday_date"]] = h["name"] or "Holiday"
    return out


async def load_settings(db, org: str) -> dict:
    row = (await db.execute(select(RotationSettings).where(RotationSettings.org_id == _uuid(org, "org")))).scalar_one_or_none()
    return {"min_rest_hours": float(row.min_rest_hours) if row and row.min_rest_hours is not None else 11.0,
            "weeks_ahead": int(row.weeks_ahead) if row and row.weeks_ahead else 4,
            "auto_generate": bool(row.auto_generate) if row else False,
            "swaps_enabled": True if row is None else bool(row.swaps_enabled),
            "last_auto_run": row.last_auto_run.isoformat() if row and row.last_auto_run else None,
            "updated_by": row.updated_by if row else None,
            "rest_note": "Company rule. Provincial employment standards set daily/weekly rest (for example Ontario ESA: "
                         "11 consecutive hours off each day, 8 hours between shifts) — set the gap your company follows."}


def _shift_dict(s: RosterShift) -> dict:
    return {"id": str(s.id), "employee_id": str(s.employee_id), "work_date": s.work_date.isoformat(), "start_time": s.start_time,
            "end_time": s.end_time, "planned_hours": float(s.planned_hours or 0), "status": s.status, "kind": s.kind or "work",
            "source": s.source or "manual", "shift_code": s.shift_code, "note": s.note, "location": s.location}


def _name(e: dict | None) -> str:
    return f"{(e or {}).get('first_name') or ''} {(e or {}).get('last_name') or ''}".strip() or "—"


def _in_scope(request: Request, emp: dict | None) -> bool:
    if _is_hr(request):
        return True
    return _role(request) == "manager" and bool(emp) and str(emp.get("manager_id") or "") == _me(request) and bool(_me(request))


# ══════════════════════════ attendance + roster overlay (used by routers._recompute_day) ══════════

async def roster_overlay(db, org, employee_id, work_date: date, planned, workday, p_start, brk):
    """A PUBLISHED roster row (hand-made or generated) decides the day's plan: OFF → rest day; a shift → its hours/start."""
    try:
        s = (await db.execute(select(RosterShift).where(
            RosterShift.org_id == uuid.UUID(str(org)), RosterShift.employee_id == uuid.UUID(str(employee_id)),
            RosterShift.work_date == work_date,
            RosterShift.status == "published"))).scalars().first()
    except Exception:  # noqa: BLE001
        return planned, workday, p_start, brk
    if not s:
        return planned, workday, p_start, brk
    if (s.kind or "work") == "off":
        return 0.0, False, None, brk
    return float(s.planned_hours or 0), True, s.start_time, brk


# ══════════════════════════ templates · patterns · preview ═══════════════════════════════════════

@router.get("/templates")
async def templates(request: Request):
    _org(request)
    return [RL.template_out(k) for k in RL.TEMPLATES]


def _pattern_out(p: RotationPattern) -> dict:
    return {"id": str(p.id), "name": p.name, "code": p.code, "template": p.template, "cycle": list(p.cycle or []),
            "cycle_days": len(p.cycle or []), "shifts": dict(p.shifts or {}), "description": p.description, "active": p.active,
            "work_days": sum(1 for c in (p.cycle or []) if c != RL.OFF),
            "night": any(RL.is_night(v) for v in (p.shifts or {}).values()),
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


@router.get("/patterns")
async def list_patterns(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    rows = (await db.execute(select(RotationPattern).where(RotationPattern.org_id == _uuid(org, "org"))
                             .order_by(RotationPattern.name))).scalars().all()
    return [_pattern_out(p) for p in rows if p.active]


class PatternIn(BaseModel):
    name: str
    code: str | None = None
    template: str | None = None
    cycle: list[str] = []
    shifts: dict = {}
    description: str | None = None


def _validated(body: PatternIn) -> tuple[list, dict]:
    if not (body.name or "").strip():
        raise _err(422, "TA_ROTA_NAME", "Give the pattern a name.")
    try:
        return RL.validate_pattern(body.cycle, body.shifts)
    except RL.PatternError as e:
        raise _err(422, "TA_ROTA_PATTERN_INVALID", str(e))


@router.post("/patterns", status_code=201)
async def create_pattern(body: PatternIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "define rotation patterns")
    org = _org(request)
    cycle, shifts = _validated(body)
    p = RotationPattern(org_id=_uuid(org, "org"), name=body.name.strip()[:120], code=(body.code or "").strip()[:20] or None,
                        template=body.template if body.template in RL.TEMPLATES else None, cycle=cycle, shifts=shifts,
                        description=body.description, created_by=_who(request))
    db.add(p)
    await db.commit()
    return _pattern_out(p)


@router.put("/patterns/{pid}")
async def update_pattern(pid: str, body: PatternIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "change rotation patterns")
    org = _org(request)
    p = (await db.execute(select(RotationPattern).where(RotationPattern.id == _uuid(pid, "pattern"),
                                                        RotationPattern.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not p or not p.active:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That pattern was not found.")
    cycle, shifts = _validated(body)
    p.name, p.code, p.cycle, p.shifts, p.description = body.name.strip()[:120], (body.code or "").strip()[:20] or None, cycle, shifts, body.description
    await db.commit()
    return _pattern_out(p)


@router.delete("/patterns/{pid}")
async def delete_pattern(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "remove rotation patterns")
    org = _org(request)
    p = (await db.execute(select(RotationPattern).where(RotationPattern.id == _uuid(pid, "pattern"),
                                                        RotationPattern.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not p:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That pattern was not found.")
    live = (await db.execute(select(RotationAssignment).where(RotationAssignment.org_id == p.org_id, RotationAssignment.pattern_id == p.id,
                                                              RotationAssignment.active.is_(True)))).scalars().first()
    if live:
        raise _err(409, "TA_ROTA_PATTERN_IN_USE", "People are still assigned to this pattern — end those assignments first.")
    p.active = False
    await db.commit()
    return {"ok": True}


@router.get("/patterns/{pid}/preview")
async def preview_pattern(pid: str, request: Request, start: str | None = None, days: int = 56, offset: int = 0,
                          crews: int = 1, crew_offset: int = 0, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    p = (await db.execute(select(RotationPattern).where(RotationPattern.id == _uuid(pid, "pattern"),
                                                        RotationPattern.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not p:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That pattern was not found.")
    d0 = _day(start) if start else await org_today(org)
    return {"pattern": _pattern_out(p), "days": RL.preview(p.cycle, p.shifts, d0, days, offset),
            "coverage": RL.coverage(p.cycle, crews, crew_offset) if crews > 1 else None}


class PreviewIn(BaseModel):
    cycle: list[str]
    shifts: dict
    start: str | None = None
    days: int = 56
    offset: int = 0
    crews: int = 1
    crew_offset: int = 0


@router.post("/preview")
async def preview_unsaved(body: PreviewIn, request: Request):
    org = _org(request)
    try:
        cycle, shifts = RL.validate_pattern(body.cycle, body.shifts)
    except RL.PatternError as e:
        raise _err(422, "TA_ROTA_PATTERN_INVALID", str(e))
    d0 = _day(body.start) if body.start else await org_today(org)
    return {"days": RL.preview(cycle, shifts, d0, body.days, body.offset),
            "coverage": RL.coverage(cycle, body.crews, body.crew_offset) if body.crews > 1 else None}


# ══════════════════════════ assignments ═════════════════════════════════════════════════════════

def _assign_out(a: RotationAssignment, pats: dict, emps: dict) -> dict:
    p = pats.get(str(a.pattern_id))
    e = emps.get(str(a.employee_id)) if a.employee_id else None
    return {"id": str(a.id), "pattern_id": str(a.pattern_id), "pattern": p.name if p else "—",
            "employee_id": str(a.employee_id) if a.employee_id else None, "employee": _name(e) if e else None,
            "employee_number": (e or {}).get("employee_number"), "department": a.department or (e or {}).get("department"),
            "team": bool(a.department and not a.employee_id), "crew": a.crew, "start_date": a.start_date.isoformat(),
            "end_date": a.end_date.isoformat() if a.end_date else None, "offset_days": a.offset_days or 0, "active": a.active}


@router.get("/assignments")
async def list_assignments(request: Request, db: AsyncSession = Depends(get_db)):
    _require_team(request, "see rotation assignments")
    org = _org(request)
    emps = await employees_sql(db, org)
    pats = {str(p.id): p for p in (await db.execute(select(RotationPattern).where(RotationPattern.org_id == _uuid(org, "org")))).scalars().all()}
    rows = (await db.execute(select(RotationAssignment).where(RotationAssignment.org_id == _uuid(org, "org"),
                                                              RotationAssignment.active.is_(True))
                             .order_by(RotationAssignment.start_date.desc()))).scalars().all()
    out = []
    my_depts = {e.get("department") for e in emps.values() if str(e.get("manager_id") or "") == _me(request)}
    for a in rows:
        if not _is_hr(request):
            if a.employee_id and not _in_scope(request, emps.get(str(a.employee_id))):
                continue
            if not a.employee_id and a.department not in my_depts:
                continue
        out.append(_assign_out(a, pats, emps))
    return out


class AssignIn(BaseModel):
    pattern_id: str
    employee_ids: list[str] = []
    department: str | None = None          # a whole team (crew) instead of named people
    crew: str | None = None
    start_date: str
    end_date: str | None = None
    offset_days: int = 0
    stagger_days: int = 0                  # named people: person i starts i × stagger days into the cycle


@router.post("/assignments", status_code=201)
async def create_assignment(body: AssignIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_team(request, "assign rotation patterns")
    org = _org(request)
    p = (await db.execute(select(RotationPattern).where(RotationPattern.id == _uuid(body.pattern_id, "pattern"),
                                                        RotationPattern.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not p or not p.active:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That pattern was not found.")
    d0 = _day(body.start_date, "start date")
    d1 = _day(body.end_date, "end date") if body.end_date else None
    if d1 and d1 < d0:
        raise _err(422, "TA_ROTA_DATES", "The end date is before the start date.")
    emps = await employees_sql(db, org)
    made = []
    if body.department and not body.employee_ids:
        if not _is_hr(request):
            raise HTTPException(403, {"code": "TA_ROTA_HR_ONLY", "message": "Only HR can put a whole department on a pattern — pick your team members instead."})
        if not any((e.get("department") or "") == body.department for e in emps.values()):
            raise _err(422, "TA_ROTA_DEPT", "Nobody works in that department.")
        a = RotationAssignment(org_id=p.org_id, pattern_id=p.id, department=body.department[:120], crew=(body.crew or None),
                               start_date=d0, end_date=d1, offset_days=max(0, int(body.offset_days or 0)) % len(p.cycle),
                               created_by=_who(request))
        db.add(a)
        made.append(a)
    else:
        if not body.employee_ids:
            raise _err(422, "TA_ROTA_WHO", "Pick the employees (or a department) to put on the pattern.")
        for i, eid in enumerate(dict.fromkeys(body.employee_ids)):
            e = emps.get(str(eid))
            if not e or not _in_scope(request, e):
                raise _err(404, "TA_ROTA_NOT_FOUND", "One of those employees was not found in your team.")
            # a new personal assignment ends the person's earlier one the day before
            for old in (await db.execute(select(RotationAssignment).where(
                    RotationAssignment.org_id == p.org_id, RotationAssignment.employee_id == _uuid(eid),
                    RotationAssignment.active.is_(True)))).scalars().all():
                if old.start_date >= d0:
                    old.active = False
                elif not old.end_date or old.end_date >= d0:
                    old.end_date = d0 - timedelta(days=1)
            off = (max(0, int(body.offset_days or 0)) + i * max(0, int(body.stagger_days or 0))) % len(p.cycle)
            a = RotationAssignment(org_id=p.org_id, pattern_id=p.id, employee_id=_uuid(eid), crew=(body.crew or None),
                                   start_date=d0, end_date=d1, offset_days=off, created_by=_who(request))
            db.add(a)
            made.append(a)
    await db.commit()
    pats = {str(p.id): p}
    return {"created": len(made), "assignments": [_assign_out(a, pats, emps) for a in made]}


@router.delete("/assignments/{aid}")
async def end_assignment(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """End an assignment; its future shifts that are still only PLANNED are removed (published ones stay)."""
    _require_team(request, "end rotation assignments")
    org = _org(request)
    a = (await db.execute(select(RotationAssignment).where(RotationAssignment.id == _uuid(aid, "assignment"),
                                                           RotationAssignment.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not a or not a.active:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That assignment was not found.")
    emps = await employees_sql(db, org)
    if a.employee_id and not _in_scope(request, emps.get(str(a.employee_id))):
        raise _err(404, "TA_ROTA_NOT_FOUND", "That assignment was not found.")
    if not a.employee_id and not _is_hr(request):
        raise HTTPException(403, {"code": "TA_ROTA_HR_ONLY", "message": "Only HR can end a whole department's pattern."})
    a.active = False
    today = await org_today(org)
    rows = (await db.execute(select(RosterShift).where(RosterShift.org_id == a.org_id, RosterShift.assignment_id == a.id,
                                                       RosterShift.work_date >= today, RosterShift.status == "planned"))).scalars().all()
    for r in rows:
        await db.delete(r)
    await db.commit()
    return {"ok": True, "removed_planned_shifts": len(rows)}


# ══════════════════════════ settings ════════════════════════════════════════════════════════════

@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    return await load_settings(db, _org(request))


class SettingsIn(BaseModel):
    min_rest_hours: float = 11
    weeks_ahead: int = 4
    auto_generate: bool = False
    swaps_enabled: bool = True


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "change the rotation rules")
    org = _org(request)
    if not 0 <= body.min_rest_hours <= 24:
        raise _err(422, "TA_ROTA_REST", "Minimum rest must be between 0 and 24 hours (0 = no check).")
    if not 1 <= body.weeks_ahead <= 12:
        raise _err(422, "TA_ROTA_WEEKS", "Generate between 1 and 12 weeks ahead.")
    row = (await db.execute(select(RotationSettings).where(RotationSettings.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not row:
        row = RotationSettings(org_id=_uuid(org, "org"))
        db.add(row)
    row.min_rest_hours, row.weeks_ahead = body.min_rest_hours, body.weeks_ahead
    row.auto_generate, row.swaps_enabled, row.updated_by = body.auto_generate, body.swaps_enabled, _who(request)
    await db.commit()
    return await load_settings(db, org)


# ══════════════════════════ generate ════════════════════════════════════════════════════════════

def resolve_assignments(rows: list[RotationAssignment], emps: dict[str, dict], d0: date, d1: date,
                        scope: set[str] | None) -> list[dict]:
    """ONE assignment per employee for the window: a personal one beats a team one; the latest start wins. Pure-ish."""
    personal: dict[str, RotationAssignment] = {}
    team: dict[str, RotationAssignment] = {}
    for a in rows:
        if not a.active or a.start_date > d1 or (a.end_date and a.end_date < d0):
            continue
        if a.employee_id:
            k = str(a.employee_id)
            if k not in personal or a.start_date > personal[k].start_date:
                personal[k] = a
        elif a.department:
            if a.department not in team or a.start_date > team[a.department].start_date:
                team[a.department] = a
    out = []
    for eid, e in emps.items():
        if scope is not None and eid not in scope:
            continue
        if (e.get("status") or "") in ("terminated", "inactive"):
            continue
        a = personal.get(eid) or team.get(e.get("department") or "")
        if a:
            out.append({"id": str(a.id), "pattern_id": str(a.pattern_id), "employee_id": eid, "start_date": a.start_date,
                        "end_date": a.end_date, "offset_days": a.offset_days or 0})
    return out


async def generate_core(db, org: str, d0: date, d1: date, *, scope: set[str] | None, dry_run: bool,
                        overwrite_manual: bool = False, overwrite_published: bool = False) -> dict:
    s = await load_settings(db, org)
    oid = _uuid(org, "org")
    pats = {str(p.id): {"cycle": p.cycle, "shifts": p.shifts, "name": p.name}
            for p in (await db.execute(select(RotationPattern).where(RotationPattern.org_id == oid,
                                                                     RotationPattern.active.is_(True)))).scalars().all()}
    arows = (await db.execute(select(RotationAssignment).where(RotationAssignment.org_id == oid,
                                                               RotationAssignment.active.is_(True)))).scalars().all()
    emps = await employees_sql(db, org)
    assigns = [a for a in resolve_assignments(arows, emps, d0, d1, scope) if a["pattern_id"] in pats]
    ids = [uuid.UUID(a["employee_id"]) for a in assigns]
    existing = {}
    if ids:
        for r in (await db.execute(select(RosterShift).where(RosterShift.org_id == oid, RosterShift.employee_id.in_(ids),
                                                             RosterShift.work_date >= d0 - timedelta(days=1),
                                                             RosterShift.work_date <= d1))).scalars().all():
            existing[(str(r.employee_id), r.work_date.isoformat())] = {"id": str(r.id), "source": r.source or "manual", "status": r.status,
                                                                       "kind": r.kind or "work", "start_time": r.start_time,
                                                                       "end_time": r.end_time}
    res = RL.plan(assignments=assigns, patterns=pats, employees=emps, start=d0, end=d1,
                  leave=await leave_sql(db, org, d0, d1), holidays=await holidays_sql(db, org, d0, d1, emps), existing=existing,
                  min_rest_hours=s["min_rest_hours"], overwrite_manual=overwrite_manual, overwrite_published=overwrite_published)
    created = updated = deleted = 0
    if not dry_run:
        by_id = {}
        ex_ids = [uuid.UUID(r["existing_id"]) for r in res["rows"] if r.get("existing_id")] + [uuid.UUID(x) for x in res["deletes"]]
        if ex_ids:
            by_id = {str(r.id): r for r in (await db.execute(select(RosterShift).where(RosterShift.id.in_(ex_ids)))).scalars().all()}
        for r in res["rows"]:
            fields = dict(start_time=r["start_time"], end_time=r["end_time"], planned_hours=r["planned_hours"], note=r["note"],
                          kind=r["kind"], shift_code=r["shift_code"], source="pattern", status="planned",
                          pattern_id=uuid.UUID(r["pattern_id"]), assignment_id=uuid.UUID(r["assignment_id"]) if r["assignment_id"] else None)
            row = by_id.get(str(r.get("existing_id") or ""))
            if row:
                for k, v in fields.items():
                    setattr(row, k, v)
                updated += 1
            else:
                db.add(RosterShift(org_id=oid, employee_id=uuid.UUID(r["employee_id"]), work_date=_day(r["work_date"]), **fields))
                created += 1
        for x in res["deletes"]:
            row = by_id.get(x)
            if row and (row.source or "manual") == "pattern" and row.status == "planned":
                await db.delete(row)
                deleted += 1
        await db.commit()
    return {"start": d0.isoformat(), "end": d1.isoformat(), "dry_run": dry_run, "employees": len(assigns),
            "work_shifts": sum(1 for r in res["rows"] if r["kind"] == "work"), "off_days": sum(1 for r in res["rows"] if r["kind"] == "off"),
            "created": created, "updated": updated, "deleted": deleted, "min_rest_hours": s["min_rest_hours"],
            "rows": res["rows"][:3000], "kept": res["kept"][:500], "skipped": res["skipped"][:500], "conflicts": res["conflicts"][:500],
            "counts": {"kept": len(res["kept"]), "skipped": len(res["skipped"]), "conflicts": len(res["conflicts"])}}


class GenerateIn(BaseModel):
    start: str | None = None
    weeks: int | None = None
    employee_ids: list[str] | None = None
    dry_run: bool = True
    overwrite_manual: bool = False
    overwrite_published: bool = False


@router.post("/generate")
async def generate(body: GenerateIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Build the roster from the assignments for the next N weeks. dry_run (default) = preview only."""
    _require_team(request, "generate the roster")
    org = _org(request)
    s = await load_settings(db, org)
    d0 = _day(body.start, "start") if body.start else await org_today(org)
    weeks = int(body.weeks or s["weeks_ahead"])
    if not 1 <= weeks <= 12:
        raise _err(422, "TA_ROTA_WEEKS", "Generate between 1 and 12 weeks at a time.")
    d1 = d0 + timedelta(days=weeks * 7 - 1)
    scope: set[str] | None = None
    if not _is_hr(request):
        emps = await employees_sql(db, org)
        scope = {k for k, e in emps.items() if _in_scope(request, e)}
        if body.overwrite_published:
            raise HTTPException(403, {"code": "TA_ROTA_HR_ONLY", "message": "Only HR can regenerate shifts that are already published."})
    if body.employee_ids:
        want = {str(x) for x in body.employee_ids}
        scope = want if scope is None else (scope & want)
    out = await generate_core(db, org, d0, d1, scope=scope, dry_run=body.dry_run, overwrite_manual=body.overwrite_manual,
                              overwrite_published=body.overwrite_published)
    if not body.dry_run:
        try:
            from shared.utils.audit import record_audit
            await record_audit(request, "time.rotation.generate", entity_type="roster", entity_id=f"{out['start']}..{out['end']}",
                               summary=f"Roster generated from rotation patterns: {out['created']} new, {out['updated']} updated, "
                                       f"{out['deleted']} removed, {out['counts']['conflicts']} not planned",
                               meta={k: out[k] for k in ("start", "end", "created", "updated", "deleted", "employees")})
        except Exception:  # noqa: BLE001
            pass
    return out


# ── nightly auto-generate (org setting) ──────────────────────────────────────────────────────────

async def auto_generate_once(now: datetime | None = None) -> int:
    """Every org with auto_generate on gets its roster topped up to `weeks_ahead` (once a day). Returns orgs done."""
    now = now or _now()
    n = 0
    async with SessionLocal() as db:
        rows = (await db.execute(select(RotationSettings).where(RotationSettings.auto_generate.is_(True)))).scalars().all()
        for r in rows:
            if r.last_auto_run and (now - r.last_auto_run) < timedelta(hours=20):
                continue
            org = str(r.org_id)
            try:
                d0 = await org_today(org)
                await generate_core(db, org, d0, d0 + timedelta(days=int(r.weeks_ahead or 4) * 7 - 1), scope=None, dry_run=False)
                r2 = (await db.execute(select(RotationSettings).where(RotationSettings.org_id == r.org_id))).scalar_one()
                r2.last_auto_run = now
                await db.commit()
                n += 1
            except Exception as e:  # noqa: BLE001
                await db.rollback()
                log.warning("auto roster generation failed for %s: %s", org, e)
    return n


_TICK: asyncio.Task | None = None


async def _tick_loop():
    await asyncio.sleep(60)
    while True:
        try:
            await auto_generate_once()
        except Exception as e:  # noqa: BLE001
            log.warning("rotation tick: %s", e)
        await asyncio.sleep(6 * 3600)


def start_tick() -> None:
    """Called from the service lifespan."""
    global _TICK
    try:
        if _TICK is None or _TICK.done():
            _TICK = asyncio.get_running_loop().create_task(_tick_loop())
    except RuntimeError:
        pass


# ══════════════════════════ my shifts + swaps ═══════════════════════════════════════════════════

@router.get("/my-shifts")
async def my_shifts(request: Request, start: str | None = None, end: str | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    me = _me(request)
    if not me:
        return {"shifts": [], "swaps_enabled": (await load_settings(db, org))["swaps_enabled"]}
    d0 = _day(start) if start else await org_today(org)
    d1 = _day(end) if end else d0 + timedelta(days=27)
    rows = (await db.execute(select(RosterShift).where(RosterShift.org_id == _uuid(org, "org"), RosterShift.employee_id == _uuid(me),
                                                       RosterShift.work_date >= d0, RosterShift.work_date <= d1,
                                                       RosterShift.status == "published").order_by(RosterShift.work_date))).scalars().all()
    pending = {str(x.requester_shift_id) for x in (await db.execute(select(ShiftSwap).where(
        ShiftSwap.org_id == _uuid(org, "org"), ShiftSwap.status.in_(SWAP_OPEN)))).scalars().all()}
    return {"start": d0.isoformat(), "end": d1.isoformat(), "swaps_enabled": (await load_settings(db, org))["swaps_enabled"],
            "shifts": [{**_shift_dict(r), "swap_pending": str(r.id) in pending} for r in rows]}


async def _shift(db, org: str, sid) -> RosterShift:
    s = (await db.execute(select(RosterShift).where(RosterShift.id == _uuid(sid, "shift"),
                                                    RosterShift.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not s:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That shift was not found.")
    return s


async def _neighbours(db, org: str, employee_id, around: list[date]) -> list[dict]:
    lo, hi = min(around) - timedelta(days=2), max(around) + timedelta(days=2)
    rows = (await db.execute(select(RosterShift).where(RosterShift.org_id == _uuid(org, "org"), RosterShift.employee_id == employee_id,
                                                       RosterShift.work_date >= lo, RosterShift.work_date <= hi))).scalars().all()
    return [_shift_dict(r) for r in rows]


async def _swap_problems(db, org: str, a: RosterShift, b: RosterShift, emps: dict) -> list[str]:
    s = await load_settings(db, org)
    dates = [a.work_date, b.work_date]
    return RL.swap_problems(a_emp=emps.get(str(a.employee_id)) or {}, b_emp=emps.get(str(b.employee_id)) or {},
                            a_shift=_shift_dict(a), b_shift=_shift_dict(b),
                            a_neighbours=await _neighbours(db, org, a.employee_id, dates),
                            b_neighbours=await _neighbours(db, org, b.employee_id, dates),
                            min_rest_hours=s["min_rest_hours"])


@router.get("/swap-options")
async def swap_options(shift_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Colleagues' published work shifts (same department, ±14 days, not in the past) the caller could swap with."""
    org = _org(request)
    mine = await _shift(db, org, shift_id)
    if str(mine.employee_id) != _me(request) and not _is_hr(request):
        raise _err(404, "TA_ROTA_NOT_FOUND", "That shift was not found.")
    emps = await employees_sql(db, org)
    me = emps.get(str(mine.employee_id)) or {}
    today = await org_today(org)
    lo, hi = max(today, mine.work_date - timedelta(days=14)), mine.work_date + timedelta(days=14)
    rows = (await db.execute(select(RosterShift).where(RosterShift.org_id == mine.org_id, RosterShift.employee_id != mine.employee_id,
                                                       RosterShift.work_date >= lo, RosterShift.work_date <= hi,
                                                       RosterShift.status == "published").order_by(RosterShift.work_date))).scalars().all()
    out = []
    for r in rows:
        e = emps.get(str(r.employee_id)) or {}
        if (r.kind or "work") != "work" or (e.get("department") or "") != (me.get("department") or "") or (e.get("status") or "") == "terminated":
            continue
        if r.work_date == mine.work_date and r.start_time == mine.start_time and r.end_time == mine.end_time:
            continue                                  # the same shift — nothing to swap
        out.append({**_shift_dict(r), "employee": _name(e), "employee_number": e.get("employee_number"),
                    "problems": await _swap_problems(db, org, mine, r, emps) if len(out) < 60 else []})
        if len(out) >= 80:
            break
    return {"shift": _shift_dict(mine), "options": out}


def _swap_out(x: ShiftSwap, emps: dict) -> dict:
    snap = x.snapshot or {}
    return {"id": str(x.id), "status": x.status, "reason": x.reason, "requester_id": str(x.requester_id),
            "requester": _name(emps.get(str(x.requester_id))), "partner_id": str(x.partner_id), "partner": _name(emps.get(str(x.partner_id))),
            "requester_shift": snap.get("a"), "partner_shift": snap.get("b"), "partner_note": x.partner_note,
            "decided_by": x.decided_by, "decided_at": x.decided_at.isoformat() if x.decided_at else None,
            "decision_note": x.decision_note, "created_at": x.created_at.isoformat() if x.created_at else None}


@router.get("/swaps")
async def list_swaps(request: Request, scope: str = "mine", db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emps = await employees_sql(db, org)
    rows = (await db.execute(select(ShiftSwap).where(ShiftSwap.org_id == _uuid(org, "org")).order_by(ShiftSwap.created_at.desc())
                             .limit(500))).scalars().all()
    me = _me(request)
    out = []
    for x in rows:
        mine = me and me in (str(x.requester_id), str(x.partner_id))
        if scope == "mine" and not mine:
            continue
        if scope in ("team", "all") and not mine:
            if not (_is_hr(request) or _in_scope(request, emps.get(str(x.requester_id))) or _in_scope(request, emps.get(str(x.partner_id)))):
                continue
        o = _swap_out(x, emps)
        o["for_me"] = (x.status == "pending_partner" and me == str(x.partner_id))
        out.append(o)
    return out


class SwapIn(BaseModel):
    my_shift_id: str
    partner_shift_id: str
    reason: str | None = None


async def _users_of(org: str, employee_ids: list[str]) -> list[str]:
    try:
        from shared.utils.directory import org_users
        want = {str(e) for e in employee_ids}
        return [u["id"] for u in await org_users(org) if str(u.get("employee_id") or "") in want]
    except Exception:  # noqa: BLE001
        return []


async def _tell(org: str, employee_ids: list[str], title: str, body: str = "") -> None:
    try:
        from shared.utils.notify import notify
        ids = await _users_of(org, employee_ids)
        if ids:
            await notify(org, user_ids=ids, title=title, body=body, link="/my-shifts", kind="roster")
    except Exception:  # noqa: BLE001
        pass


def _label(s: dict | None) -> str:
    if not s:
        return "—"
    return f"{s['work_date']} {s['start_time']}–{s['end_time']}"


@router.post("/swaps", status_code=201)
async def request_swap(body: SwapIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    if not (await load_settings(db, org))["swaps_enabled"]:
        raise _err(409, "TA_ROTA_SWAPS_OFF", "Shift swaps are switched off in your company.")
    a = await _shift(db, org, body.my_shift_id)
    b = await _shift(db, org, body.partner_shift_id)
    if str(a.employee_id) != _me(request) and not _is_hr(request):
        raise HTTPException(403, {"code": "TA_ROTA_NOT_YOUR_SHIFT", "message": "You can only offer your own shift for a swap."})
    if a.employee_id == b.employee_id:
        raise _err(422, "TA_ROTA_SWAP_SELF", "Pick a colleague's shift, not one of your own.")
    today = await org_today(org)
    for s in (a, b):
        if s.work_date < today:
            raise _err(409, "TA_ROTA_SWAP_PAST", "Shifts in the past cannot be swapped.")
        if (s.kind or "work") != "work" or s.status != "published":
            raise _err(409, "TA_ROTA_SWAP_NOT_WORK", "Only published work shifts can be swapped (not a day off or a draft).")
    busy = (await db.execute(select(ShiftSwap).where(ShiftSwap.org_id == a.org_id, ShiftSwap.status.in_(SWAP_OPEN)))).scalars().all()
    if any({str(x.requester_shift_id), str(x.partner_shift_id)} & {str(a.id), str(b.id)} for x in busy):
        raise _err(409, "TA_ROTA_SWAP_BUSY", "One of these shifts already has a swap request waiting.")
    emps = await employees_sql(db, org)
    probs = await _swap_problems(db, org, a, b, emps)
    if probs:
        raise _err(409, "TA_ROTA_SWAP_RULES", "This swap would break a rostering rule: " + "; ".join(probs) + ".", problems=probs)
    x = ShiftSwap(org_id=a.org_id, requester_id=a.employee_id, requester_shift_id=a.id, partner_id=b.employee_id, partner_shift_id=b.id,
                  snapshot={"a": _shift_dict(a), "b": _shift_dict(b)}, reason=(body.reason or "")[:400] or None,
                  requested_by_uid=_uid(request))
    db.add(x)
    await db.commit()
    await _tell(org, [str(b.employee_id)], f"Shift swap asked by {_name(emps.get(str(a.employee_id)))}",
                f"Your {_label(_shift_dict(b))} for their {_label(_shift_dict(a))}. Accept or decline on My shifts.")
    return _swap_out(x, emps)


async def _swap(db, org: str, sid) -> ShiftSwap:
    x = (await db.execute(select(ShiftSwap).where(ShiftSwap.id == _uuid(sid, "swap"), ShiftSwap.org_id == _uuid(org, "org")))).scalar_one_or_none()
    if not x:
        raise _err(404, "TA_ROTA_NOT_FOUND", "That swap request was not found.")
    return x


class NoteIn(BaseModel):
    note: str | None = None
    comment: str | None = None
    decision: str | None = None


@router.post("/swaps/{sid}/accept")
async def accept_swap(sid: str, request: Request, body: NoteIn | None = None, db: AsyncSession = Depends(get_db)):
    """The colleague agrees → the manager is asked through the one inbox (org chain "Shift swap")."""
    org = _org(request)
    x = await _swap(db, org, sid)
    if _me(request) != str(x.partner_id):
        raise HTTPException(403, {"code": "TA_ROTA_NOT_PARTNER", "message": "Only the colleague who was asked can accept."})
    if x.status != "pending_partner":
        raise _err(409, "TA_ROTA_SWAP_STATE", f"This swap is already {x.status.replace('_', ' ')}.")
    a, b = await _shift(db, org, x.requester_shift_id), await _shift(db, org, x.partner_shift_id)
    emps = await employees_sql(db, org)
    probs = await _swap_problems(db, org, a, b, emps)
    if probs:
        raise _err(409, "TA_ROTA_SWAP_RULES", "The roster changed and this swap now breaks a rule: " + "; ".join(probs) + ".", problems=probs)
    x.status, x.partner_decided_at = "pending_manager", _now()
    x.partner_note = ((body.note or body.comment) if body else None)
    await db.commit()
    req_users = await _users_of(org, [str(x.requester_id)])
    try:
        from shared.utils.approvals_client import open_item
        from rotation_logic import is_night
        sa, sb = x.snapshot.get("a"), x.snapshot.get("b")
        night = any(is_night({"start": s["start_time"], "end": s["end_time"]}) for s in (sa, sb))
        await open_item(org, kind="shift_swap", ref_id=str(x.id),
                        title=f"Shift swap — {_name(emps.get(str(x.requester_id)))} ⇄ {_name(emps.get(str(x.partner_id)))}",
                        summary=[f"{_name(emps.get(str(x.requester_id)))} gives {_label(sa)} and takes {_label(sb)}",
                                 f"{_name(emps.get(str(x.partner_id)))} agreed" + (f": {x.partner_note}" if x.partner_note else ""),
                                 *( [f"Reason: {x.reason}"] if x.reason else [])],
                        requester_user_id=req_users[0] if req_users else (x.requested_by_uid or None),
                        approver_roles=["hr"], approver_manager_of_employee_id=str(x.requester_id),
                        link="/scheduling/rotations?tab=swaps", callback_path=f"/time/rotations/swaps/{x.id}/{{decision}}",
                        context={"department": (emps.get(str(x.requester_id)) or {}).get("department") or "",
                                 "employee_id": str(x.requester_id), "night_shift": night,
                                 "days_apart": abs((date.fromisoformat(sa["work_date"]) - date.fromisoformat(sb["work_date"])).days)})
    except Exception as e:  # noqa: BLE001 — the manager can still decide on the Rotations screen
        log.info("swap %s not put in the inbox: %s", x.id, e)
    await _tell(org, [str(x.requester_id)], "Your colleague accepted the shift swap", "It now waits for your manager.")
    return _swap_out(x, emps)


@router.post("/swaps/{sid}/decline")
async def decline_swap(sid: str, request: Request, body: NoteIn | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    x = await _swap(db, org, sid)
    if _me(request) != str(x.partner_id):
        raise HTTPException(403, {"code": "TA_ROTA_NOT_PARTNER", "message": "Only the colleague who was asked can decline."})
    if x.status != "pending_partner":
        raise _err(409, "TA_ROTA_SWAP_STATE", f"This swap is already {x.status.replace('_', ' ')}.")
    x.status, x.partner_decided_at, x.partner_note = "declined", _now(), ((body.note or body.comment) if body else None)
    await db.commit()
    emps = await employees_sql(db, org)
    await _tell(org, [str(x.requester_id)], "Your shift swap was declined", x.partner_note or "")
    return _swap_out(x, emps)


async def _may_decide(request: Request, org: str, x: ShiftSwap, emps: dict) -> None:
    """HR, the requester's or the colleague's manager — or whoever the org's chain / a delegation says (one inbox)."""
    me = _me(request)
    if me and me in (str(x.requester_id), str(x.partner_id)) and _role(request) != "org_admin":
        raise HTTPException(403, {"code": "TA_ROTA_OWN_SWAP", "message": "You are part of this swap, so someone else has to decide it."})
    if _is_hr(request) or _in_scope(request, emps.get(str(x.requester_id))) or _in_scope(request, emps.get(str(x.partner_id))):
        return
    try:
        from shared.utils import approvals_client as ac
        from shared.utils import directory
        from shared.utils import platform_models as pm
        from shared.utils.platform_models import ApprovalItem
        async with pm.session() as s:
            item = (await s.execute(select(ApprovalItem).where(ApprovalItem.org_id == str(org), ApprovalItem.kind == "shift_swap",
                                                               ApprovalItem.ref_id == str(x.id)))).scalar_one_or_none()
        if item and item.status == "pending":
            users = {u["id"]: u for u in await directory.org_users(org)}
            v = ac.may_act(ac.item_dict(item), {"user_id": _uid(request), "role": _role(request), "employee_id": me},
                           employees={e["id"]: e for e in await directory.org_employees(org)}, users=users,
                           delegations=await ac.load_delegations(org))
            if v["allowed"]:
                return
    except Exception:  # noqa: BLE001
        pass
    raise HTTPException(403, {"code": "TA_ROTA_NOT_APPROVER", "message": "Only the manager or HR can decide this swap."})


@router.post("/swaps/{sid}/approve")
async def approve_swap(sid: str, request: Request, body: NoteIn | None = None, db: AsyncSession = Depends(get_db)):
    """Manager approves (directly or from the inbox) → the two roster rows change hands (re-checked first)."""
    org = _org(request)
    x = await _swap(db, org, sid)
    if x.status != "pending_manager":
        raise _err(409, "TA_ROTA_SWAP_STATE", "Only a swap the colleague has accepted can be approved"
                                              + (f" (this one is {x.status.replace('_', ' ')})." if x.status else "."))
    emps = await employees_sql(db, org)
    await _may_decide(request, org, x, emps)
    a, b = await _shift(db, org, x.requester_shift_id), await _shift(db, org, x.partner_shift_id)
    snap = x.snapshot or {}
    if (str(a.employee_id), a.work_date.isoformat(), a.start_time) != (snap["a"]["employee_id"], snap["a"]["work_date"], snap["a"]["start_time"]) \
            or (str(b.employee_id), b.work_date.isoformat(), b.start_time) != (snap["b"]["employee_id"], snap["b"]["work_date"], snap["b"]["start_time"]):
        raise _err(409, "TA_ROTA_ROSTER_CHANGED", "The roster changed since the swap was asked — reject it and ask again.")
    probs = await _swap_problems(db, org, a, b, emps)
    if probs:
        raise _err(409, "TA_ROTA_SWAP_RULES", "This swap now breaks a rostering rule: " + "; ".join(probs) + ".", problems=probs)
    ra, rb = a.employee_id, b.employee_id
    a.employee_id, b.employee_id = rb, ra
    note = ((body.note or body.comment) if body else None)
    for s, other in ((a, ra), (b, rb)):
        s.source = "swap"
        s.note = f"Swapped with {_name(emps.get(str(other)))} — approved by {_who(request)}"[:200]
    x.status, x.decided_by, x.decided_at, x.decision_note = "approved", _who(request), _now(), (note or "")[:400] or None
    await db.commit()
    try:
        from shared.utils.approvals_client import close_item
        await close_item(org, kind="shift_swap", ref_id=str(x.id), decision="approved", actor_user_id=_uid(request))
    except Exception:  # noqa: BLE001
        pass
    # the two days are re-planned by attendance from the swapped rows
    await _tell(org, [str(ra), str(rb)], "Shift swap approved", f"{_label(snap['a'])} ⇄ {_label(snap['b'])}")
    return _swap_out(x, emps)


@router.post("/swaps/{sid}/reject")
async def reject_swap(sid: str, request: Request, body: NoteIn | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    x = await _swap(db, org, sid)
    if x.status != "pending_manager":
        raise _err(409, "TA_ROTA_SWAP_STATE", f"This swap is {x.status.replace('_', ' ')} — nothing to reject.")
    emps = await employees_sql(db, org)
    await _may_decide(request, org, x, emps)
    x.status, x.decided_by, x.decided_at = "rejected", _who(request), _now()
    x.decision_note = (((body.note or body.comment) if body else None) or "")[:400] or None
    await db.commit()
    try:
        from shared.utils.approvals_client import close_item
        await close_item(org, kind="shift_swap", ref_id=str(x.id), decision="rejected", actor_user_id=_uid(request))
    except Exception:  # noqa: BLE001
        pass
    await _tell(org, [str(x.requester_id), str(x.partner_id)], "Shift swap rejected", x.decision_note or "")
    return _swap_out(x, emps)


@router.post("/swaps/{sid}/cancel")
async def cancel_swap(sid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    x = await _swap(db, org, sid)
    if _me(request) != str(x.requester_id) and not _is_hr(request):
        raise HTTPException(403, {"code": "TA_ROTA_NOT_REQUESTER", "message": "Only the person who asked (or HR) can withdraw a swap."})
    if x.status not in SWAP_OPEN:
        raise _err(409, "TA_ROTA_SWAP_STATE", f"This swap is already {x.status.replace('_', ' ')}.")
    was = x.status
    x.status, x.decided_at, x.decided_by = "cancelled", _now(), _who(request)
    await db.commit()
    if was == "pending_manager":
        try:
            from shared.utils.approvals_client import close_item
            await close_item(org, kind="shift_swap", ref_id=str(x.id), decision="cancelled", actor_user_id=_uid(request))
        except Exception:  # noqa: BLE001
            pass
    return _swap_out(x, await employees_sql(db, org))
