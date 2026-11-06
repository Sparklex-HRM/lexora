"""Headcount planning routes — Stream D7. Mounted under /engage/positions (gateway `engage` → ops).

GET  /engage/positions?status=&department=        list (HR / org admin / payroll-finance; money needs OFFICE)
POST /engage/positions                             create     PATCH /engage/positions/{id}     DELETE (open/frozen only)
POST /engage/positions/{id}/fill {employee_id}     snapshot the holder's annual CTC
POST /engage/positions/{id}/vacate
POST /engage/positions/{id}/link-requisition {requisition_id|null}   read-only reference to hire_requisitions
GET  /engage/positions/requisitions                open requisitions for the picker (SELECT only)
GET  /engage/positions/summary?by=department|entity|grade|fiscal_year   budget vs actual
POST /engage/positions/seed-from-employees         one filled position per active employee without one
"""
from __future__ import annotations

import sys
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402
from shared.utils.roles import FINANCE, HR_WRITE, OFFICE, has_role, require_roles  # noqa: E402

import engage_common as C  # noqa: E402
import positions_logic as PL  # noqa: E402
from database import get_db  # noqa: E402
from positions_models import Position  # noqa: E402

router = APIRouter(prefix="/engage/positions", tags=["positions"])


def _sees_money(request: Request) -> bool:
    return has_role(C.role_of(request), FINANCE) or has_role(C.role_of(request), ("org_admin", "hr"))


def _out(p: Position, money: bool) -> dict:
    d = {"id": str(p.id), "code": p.code, "title": p.title, "grade": p.grade, "department": p.department, "entity": p.entity,
         "location": p.location, "fiscal_year": p.fiscal_year, "status": p.status, "employee_id": str(p.employee_id) if p.employee_id else None,
         "employee_name": p.employee_name, "filled_on": C.iso(p.filled_on), "requisition_id": str(p.requisition_id) if p.requisition_id else None,
         "requisition_number": p.requisition_number, "manager_id": str(p.manager_id) if p.manager_id else None, "manager_name": p.manager_name,
         "notes": p.notes, "created_by": p.created_by, "created_at": C.iso(p.created_at), "updated_at": C.iso(p.updated_at)}
    if money:
        d["budgeted_ctc"] = p.budgeted_ctc
        d["actual_ctc"] = p.actual_ctc
        d["over_budget"] = PL.over_budget(p.budgeted_ctc, p.actual_ctc or 0) if p.status == "filled" else False
    return d


async def _emp_rows(org: str) -> dict[str, dict]:
    """Active employees with annual_salary (SELECT on the shared DB; a missing column is an empty map)."""
    try:
        async with pm.session() as s:
            rows = (await s.execute(text("SELECT id, first_name, last_name, department, job_title, grade, work_location, manager_id, manager_name, "
                                         "annual_salary, hire_date, status FROM employees WHERE org_id = CAST(:org AS uuid) AND is_active IS TRUE"),
                                    {"org": org})).mappings().all()
        return {str(r["id"]): dict(r) for r in rows}
    except Exception:                                   # noqa: BLE001
        return {}


async def _next_code(db, org: str) -> str:
    rows = (await db.execute(select(Position.code).where(Position.org_id == org))).scalars().all()
    n = 0
    for c in rows:
        try:
            n = max(n, int(str(c).split("-")[-1]))
        except ValueError:
            pass
    return PL.position_code(n + 1)


@router.get("")
async def list_positions(request: Request, status: str | None = None, department: str | None = None, db: AsyncSession = Depends(get_db)):
    require_roles(request, OFFICE + ("manager",), "see positions")
    org = C.org_of(request)
    q = select(Position).where(Position.org_id == org)
    if status:
        q = q.where(Position.status == status)
    if department:
        q = q.where(Position.department == department)
    rows = (await db.execute(q.order_by(Position.code))).scalars().all()
    if C.is_manager(request):
        me = C.emp_of(request) or ""
        rows = [p for p in rows if str(p.manager_id or "") == me]
    money = _sees_money(request)
    counts = {"open": 0, "filled": 0, "frozen": 0}
    for p in rows:
        counts[p.status] = counts.get(p.status, 0) + 1
    return {"positions": [_out(p, money) for p in rows], "counts": counts, "shows_money": money,
            "can_edit": has_role(C.role_of(request), HR_WRITE)}


class PositionIn(BaseModel):
    title: str
    grade: str | None = None
    department: str | None = None
    entity: str | None = None
    location: str | None = None
    fiscal_year: str | None = None
    budgeted_ctc: float = 0.0
    status: str = "open"
    manager_id: str | None = None
    notes: str | None = None


@router.post("", status_code=201)
async def create_position(body: PositionIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "create positions")
    org = C.org_of(request)
    errs = PL.validate(body.model_dump())
    if errs:
        raise HTTPException(422, "; ".join(errs))
    emps = await _emp_rows(org)
    mgr = emps.get(body.manager_id or "")
    p = Position(org_id=uuid.UUID(org), code=await _next_code(db, org), title=body.title.strip()[:200], grade=(body.grade or "")[:40] or None,
                 department=(body.department or "")[:120] or None, entity=(body.entity or "")[:200] or None, location=(body.location or "")[:120] or None,
                 fiscal_year=(body.fiscal_year or "")[:9] or None, budgeted_ctc=float(body.budgeted_ctc or 0),
                 status=body.status if body.status in ("open", "frozen") else "open",
                 manager_id=uuid.UUID(body.manager_id) if mgr else None, manager_name=C.emp_name(mgr) if mgr else None,
                 notes=(body.notes or "")[:4000] or None, created_by=C.actor_of(request))
    db.add(p)
    await db.commit()
    return _out(p, True)


@router.patch("/{pid}")
async def update_position(pid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "change positions")
    org = C.org_of(request)
    p = (await db.execute(select(Position).where(Position.id == C.to_uuid(pid, "position"), Position.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Position not found")
    errs = PL.validate({**{"title": p.title, "budgeted_ctc": p.budgeted_ctc}, **body})
    if errs:
        raise HTTPException(422, "; ".join(errs))
    for k, n in (("title", 200), ("grade", 40), ("department", 120), ("entity", 200), ("location", 120), ("fiscal_year", 9), ("notes", 4000)):
        if k in body:
            setattr(p, k, str(body[k] or "").strip()[:n] or None if k != "title" else str(body[k]).strip()[:n])
    if "budgeted_ctc" in body:
        p.budgeted_ctc = float(body["budgeted_ctc"] or 0)
    if "status" in body and body["status"] != p.status:
        if body["status"] == "filled":
            raise HTTPException(422, "Use 'fill' with an employee to mark a position filled")
        if not PL.can_move(p.status, body["status"]):
            raise HTTPException(409, f"A {p.status} position cannot become {body['status']}")
        p.status = body["status"]
        if p.status != "filled":
            p.employee_id, p.employee_name, p.actual_ctc, p.filled_on = None, None, None, None
    if "manager_id" in body:
        emps = await _emp_rows(org)
        m = emps.get(body["manager_id"] or "")
        p.manager_id, p.manager_name = (uuid.UUID(body["manager_id"]) if m else None), (C.emp_name(m) if m else None)
    await db.commit()
    return _out(p, True)


@router.delete("/{pid}")
async def delete_position(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "delete positions")
    org = C.org_of(request)
    p = (await db.execute(select(Position).where(Position.id == C.to_uuid(pid, "position"), Position.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Position not found")
    if p.status == "filled":
        raise HTTPException(409, "Vacate the position before deleting it")
    await db.delete(p)
    await db.commit()
    return {"ok": True}


class FillIn(BaseModel):
    employee_id: str
    actual_ctc: float | None = None
    filled_on: str | None = None


@router.post("/{pid}/fill")
async def fill(pid: str, body: FillIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "fill positions")
    org = C.org_of(request)
    p = (await db.execute(select(Position).where(Position.id == C.to_uuid(pid, "position"), Position.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Position not found")
    if not PL.can_move(p.status, "filled"):
        raise HTTPException(409, f"A {p.status} position cannot be filled")
    emps = await _emp_rows(org)
    e = emps.get(body.employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    taken = (await db.execute(select(Position).where(Position.org_id == org, Position.employee_id == uuid.UUID(body.employee_id),
                                                     Position.status == "filled"))).scalars().first()
    if taken and taken.id != p.id:
        raise HTTPException(409, f"{C.emp_name(e)} already holds {taken.code}")
    p.status, p.employee_id, p.employee_name = "filled", uuid.UUID(body.employee_id), C.emp_name(e)
    p.actual_ctc = float(body.actual_ctc) if body.actual_ctc is not None else (float(e.get("annual_salary") or 0) or None)
    p.filled_on = date.fromisoformat(body.filled_on) if body.filled_on else await org_today(org)
    await db.commit()
    return _out(p, True)


@router.post("/{pid}/vacate")
async def vacate(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "vacate positions")
    org = C.org_of(request)
    p = (await db.execute(select(Position).where(Position.id == C.to_uuid(pid, "position"), Position.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Position not found")
    p.status, p.employee_id, p.employee_name, p.actual_ctc, p.filled_on = "open", None, None, None, None
    await db.commit()
    return _out(p, True)


async def _requisitions(org: str) -> list[dict]:
    try:
        async with pm.session() as s:
            rows = (await s.execute(text("SELECT id, req_number, designation, department, grade, location, openings, status, hiring_manager_name, "
                                         "target_date FROM hire_requisitions WHERE org_id = CAST(:org AS uuid) ORDER BY created_at DESC LIMIT 500"),
                                    {"org": org})).mappings().all()
        return [{**dict(r), "id": str(r["id"]), "target_date": C.iso(r["target_date"])} for r in rows]
    except Exception:                                   # noqa: BLE001
        return []


@router.get("/requisitions")
async def requisitions(request: Request):
    # The picker on the HR position editor; no manager screen calls it (WASA 2026-10-21: manager removed).
    require_roles(request, OFFICE, "see requisitions")
    return {"requisitions": await _requisitions(C.org_of(request)), "note": "Read-only reference; hiring is managed on the Hiring screens."}


class LinkIn(BaseModel):
    requisition_id: str | None = None


@router.post("/{pid}/link-requisition")
async def link_req(pid: str, body: LinkIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "link requisitions")
    org = C.org_of(request)
    p = (await db.execute(select(Position).where(Position.id == C.to_uuid(pid, "position"), Position.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Position not found")
    if not body.requisition_id:
        p.requisition_id, p.requisition_number = None, None
    else:
        reqs = {r["id"]: r for r in await _requisitions(org)}
        r = reqs.get(body.requisition_id)
        if not r:
            raise HTTPException(404, "Requisition not found")
        p.requisition_id, p.requisition_number = uuid.UUID(body.requisition_id), r.get("req_number")
    await db.commit()
    return _out(p, True)


@router.get("/summary")
async def summary(request: Request, by: str = "department", db: AsyncSession = Depends(get_db)):
    require_roles(request, OFFICE, "see the headcount budget")
    org = C.org_of(request)
    if by not in ("department", "entity", "grade", "fiscal_year", "location"):
        by = "department"
    rows = (await db.execute(select(Position).where(Position.org_id == org))).scalars().all()
    money = _sees_money(request)
    res = PL.budget_summary([_out(p, True) for p in rows], by)
    if not money:
        for r in res["rows"] + [res["total"]]:
            for k in ("budgeted", "actual", "open_budget", "filled_budget", "variance"):
                r.pop(k, None)
    return {**res, "shows_money": money, "currency": "CAD"}


@router.post("/seed-from-employees", status_code=201)
async def seed(request: Request, db: AsyncSession = Depends(get_db)):
    """Create one filled position per active employee who holds none (budget = current annual CTC)."""
    require_roles(request, HR_WRITE, "seed positions")
    org = C.org_of(request)
    emps = await _emp_rows(org)
    have = {str(p.employee_id) for p in (await db.execute(select(Position).where(Position.org_id == org, Position.employee_id.is_not(None)))).scalars().all()}
    today = await org_today(org)
    made = 0
    code_n = int((await _next_code(db, org)).split("-")[-1])
    for eid, e in emps.items():
        if eid in have or (e.get("status") or "active") != "active":
            continue
        ctc = float(e.get("annual_salary") or 0)
        db.add(Position(org_id=uuid.UUID(org), code=PL.position_code(code_n), title=e.get("job_title") or "Position", grade=e.get("grade"),
                        department=e.get("department"), location=e.get("work_location"), budgeted_ctc=ctc, status="filled",
                        employee_id=uuid.UUID(eid), employee_name=C.emp_name(e), actual_ctc=ctc or None, filled_on=e.get("hire_date") or today,
                        manager_id=e.get("manager_id"), manager_name=e.get("manager_name"), created_by=C.actor_of(request)))
        code_n += 1
        made += 1
    await db.commit()
    return {"created": made}
