"""Performance G4 — 1:1 conversations (/performance/one-on-ones).

  GET   /performance/one-on-ones                       my 1:1s (as manager and as report); HR: dates and counts only
  POST  /performance/one-on-ones                       manager: {employee_id, scheduled_at, repeat_days, agenda}
                                                       (an employee may ask for one with their own manager)
  GET   /performance/one-on-ones/{id}
  PATCH /performance/one-on-ones/{id}                  agenda, shared notes, MY private notes, action items, time
  POST  /performance/one-on-ones/{id}/complete         mark done; open actions carry to the next one (created if repeating)
  POST  /performance/one-on-ones/{id}/cancel

Private notes: the manager's are seen only by the manager, the employee's only by the employee.
HR sees that 1:1s happen (dates, counts) — never notes, agenda wording or actions.
"""
import sys
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")

import hiring_integrations as ext  # noqa: E402
import perf_g4_logic as G  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, emp_name, fail, get_actor, now, one, ser, to_datetime, uid  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from perf_g4_models import PerfOneOnOne  # noqa: E402

router = APIRouter(prefix="/performance/one-on-ones", tags=["performance-1on1"])


def _view(m: PerfOneOnOne) -> dict:
    return {"manager_id": str(m.manager_id), "employee_id": str(m.employee_id)}


def _out(m: PerfOneOnOne, actor: Actor) -> dict:
    sc = G.one_scope(actor, _view(m))
    return G.redact_one(ser(m), sc) if sc else {}


async def _meeting(db, actor: Actor, mid: str, write: bool = False) -> tuple[PerfOneOnOne, str]:
    m = await one(db, PerfOneOnOne, mid, actor.org, "1:1")
    sc = G.one_scope(actor, _view(m))
    if sc is None:
        raise HTTPException(404, "1:1 not found")
    if write and sc == "hr":
        raise HTTPException(403, "Only the two people in a 1:1 can change it.")
    return m, sc


@router.get("")
async def list_ones(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    q = select(PerfOneOnOne).where(PerfOneOnOne.org_id == actor.org)
    if not actor.is_hr:
        if not actor.employee_id:
            return {"items": [], "linked": False, "team": [], "is_hr": False}
        me = uid(actor.employee_id)
        q = q.where((PerfOneOnOne.manager_id == me) | (PerfOneOnOne.employee_id == me))
    rows = (await db.execute(q.order_by(PerfOneOnOne.scheduled_at.desc()).limit(500))).scalars().all()
    items = [_out(m, actor) for m in rows]
    team = []
    if actor.employee_id:
        reps = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.is_active.is_(True),
                                                        Employee.manager_id == uid(actor.employee_id)))).scalars().all()
        team = [{"id": str(e.id), "name": emp_name(e), "employee_number": e.employee_number} for e in reps]
    my_manager = None
    if actor.employee_id:
        me_row = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == uid(actor.employee_id)))).scalar_one_or_none()
        if me_row is not None and me_row.manager_id:
            mg = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == me_row.manager_id))).scalar_one_or_none()
            my_manager = {"id": str(mg.id), "name": emp_name(mg)} if mg else None
    return {"items": [i for i in items if i], "linked": bool(actor.employee_id), "team": team, "my_manager": my_manager, "is_hr": actor.is_hr}


@router.post("", status_code=201)
async def create_one(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.employee_id:
        raise HTTPException(409, "Your login is not linked to an employee record yet. Ask HR to link it.")
    other = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == uid(body.get("employee_id"), "Employee")))).scalar_one_or_none()
    me = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == uid(actor.employee_id)))).scalar_one_or_none()
    if other is None or me is None:
        raise HTTPException(404, "Employee not found")
    if str(other.manager_id or "") == actor.employee_id:
        mgr, rep, by = me, other, "manager"
    elif str(me.manager_id or "") == str(other.id):
        mgr, rep, by = other, me, "employee"            # an employee asking for a 1:1 with their own manager
    else:
        raise HTTPException(403, "A 1:1 is between a manager and their direct report.")
    when = to_datetime(body.get("scheduled_at"))
    if not when:
        raise HTTPException(422, "Pick the date and time.")
    rep_days = body.get("repeat_days")
    if rep_days not in (None, "", 0, "0"):
        try:
            rep_days = int(rep_days)
        except (TypeError, ValueError):
            raise HTTPException(422, "Repeat must be every 7, 14 or 30 days, or not at all.")
        if rep_days not in (7, 14, 30):
            raise HTTPException(422, "Repeat must be every 7, 14 or 30 days, or not at all.")
    else:
        rep_days = None
    # open actions from the last 1:1 between the two come along
    last = (await db.execute(select(PerfOneOnOne).where(
        PerfOneOnOne.org_id == actor.org, PerfOneOnOne.manager_id == mgr.id, PerfOneOnOne.employee_id == rep.id,
        PerfOneOnOne.status == "done").order_by(PerfOneOnOne.scheduled_at.desc()).limit(1))).scalar_one_or_none()
    try:
        agenda = G.clean_agenda(body.get("agenda") or [], by)
    except RuleError as e:
        fail(e)
    m = PerfOneOnOne(org_id=uid(actor.org), manager_id=mgr.id, manager_name=emp_name(mgr), employee_id=rep.id, employee_name=emp_name(rep),
                     scheduled_at=when, repeat_days=rep_days, status="scheduled", agenda=agenda,
                     action_items=G.carry_over(last.action_items, str(last.id)) if last else [], created_by=actor.user_id)
    db.add(m)
    await db.commit()
    other_emp = rep if by == "manager" else mgr
    await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [other_emp.id]),
                     title=f"1:1 with {actor.name} on {when.strftime('%d %b %Y %H:%M')} UTC", body="Add your agenda points before the meeting.",
                     link="/performance/one-on-ones", kind="info")
    return _out(m, actor)


@router.get("/{meeting_id}")
async def get_one(meeting_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    m, sc = await _meeting(db, actor, meeting_id)
    return G.redact_one(ser(m), sc)


@router.patch("/{meeting_id}")
async def update_one(meeting_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    m, sc = await _meeting(db, actor, meeting_id, write=True)
    if m.status == "cancelled":
        raise HTTPException(409, "This 1:1 was cancelled.")
    try:
        if "agenda" in body:
            m.agenda = G.clean_agenda(body["agenda"], sc, m.agenda or [])
        if "action_items" in body:
            m.action_items = G.clean_actions(body["action_items"], m.action_items or [])
    except RuleError as e:
        fail(e)
    if "shared_notes" in body:
        m.shared_notes = str(body.get("shared_notes") or "")[:8000] or None
    if "my_private" in body:
        txt = str(body.get("my_private") or "")[:8000] or None
        if sc == "manager":
            m.manager_private = txt
        else:
            m.employee_private = txt
    if "scheduled_at" in body and m.status == "scheduled":
        m.scheduled_at = to_datetime(body.get("scheduled_at")) or m.scheduled_at
    await db.commit()
    return G.redact_one(ser(m), sc)


@router.post("/{meeting_id}/complete")
async def complete_one(meeting_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    m, sc = await _meeting(db, actor, meeting_id, write=True)
    if m.status != "scheduled":
        raise HTTPException(409, f"This 1:1 is already {m.status}.")
    await cas(db, PerfOneOnOne, m, "status", "scheduled", status="done", closed_at=now())
    nxt = None
    if m.repeat_days:
        nxt = PerfOneOnOne(org_id=m.org_id, manager_id=m.manager_id, manager_name=m.manager_name, employee_id=m.employee_id,
                           employee_name=m.employee_name, scheduled_at=m.scheduled_at + timedelta(days=m.repeat_days),
                           repeat_days=m.repeat_days, status="scheduled", agenda=[],
                           action_items=G.carry_over(m.action_items, str(m.id)), created_by=actor.user_id)
        db.add(nxt)
    await db.commit()
    await db.refresh(m)
    return {"meeting": G.redact_one(ser(m), sc), "next": G.redact_one(ser(nxt), sc) if nxt else None}


@router.post("/{meeting_id}/cancel")
async def cancel_one(meeting_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    m, sc = await _meeting(db, actor, meeting_id, write=True)
    if m.status != "scheduled":
        raise HTTPException(409, f"This 1:1 is already {m.status}.")
    await cas(db, PerfOneOnOne, m, "status", "scheduled", status="cancelled", closed_at=now())
    await db.commit()
    await db.refresh(m)
    return G.redact_one(ser(m), sc)
