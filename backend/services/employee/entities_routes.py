"""Employee service — legal entity of an employee (stream D4, multi-entity).

Mounted under /employees (entities_sub is included BEFORE the /{employee_id} routes):

    GET  /employees/entities                     entities of the org + headcount per entity
    GET  /employees/entities/transfers           entity transfers (scheduled / applied), newest first
    POST /employees/entities/apply-due           apply scheduled transfers whose date has arrived
    GET  /employees/{id}/entity-history          the person's spells per entity
    POST /employees/{id}/entity-transfer         HR: move to another entity from an effective date

A transfer is a career-change record (change_type "entity_transfer") with history kept in
employee_entity_history: the old spell is closed the day before the effective date (each
entity is its own CRA employer — payroll issues T4 / ROE per entity). `entity_id` on
the employee record cannot be PATCHed directly — only this path changes it.

Also exported for routers.py: `request_entity_filter()` (the ?entity_id= / X-View-Entity view
filter on the employee list) and `resolve_entity_for_create()` (default entity on create).
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared import legal_entities as LE  # noqa: E402
from shared.utils.org_time import local_today, org_today  # noqa: E402

import access  # noqa: E402
from career_models import CareerChange  # noqa: E402
from database import get_db  # noqa: E402
from entities_models import EmployeeEntityHistory  # noqa: E402
from models import Employee  # noqa: E402

entities_sub = APIRouter()


def _org(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or ""


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


async def _today(org) -> date:
    try:
        return await org_today(org)
    except Exception:  # noqa: BLE001
        return local_today()


# ── helpers used by routers.py ───────────────────────────────────────────────
async def request_entity_filter(request: Request, q, org):
    """Narrow an Employee query to the entity view the caller asked for. Rows whose entity_id
    is NULL (not yet assigned) count as the org's DEFAULT entity."""
    eid = LE.request_entity_id(request)
    if not eid:
        return q
    d = await LE.default_entity(org)
    if d and str(d["id"]) == eid:
        return q.where(or_(Employee.entity_id == uuid.UUID(eid), Employee.entity_id.is_(None)))
    return q.where(Employee.entity_id == uuid.UUID(eid))


async def resolve_entity_for_create(org, entity_id) -> uuid.UUID | None:
    """The entity a new employee is created under: the chosen one (must be an active entity
    of this org) or the default. None only when the org has no entity table yet."""
    if entity_id:
        ent = await LE.entity_by_id(org, entity_id)
        if not ent or ent.get("is_active") is False:
            raise HTTPException(422, "Choose a legal entity of this organization")
        return ent["id"]
    d = await LE.default_entity(org)
    return d["id"] if d else None


# ── history ──────────────────────────────────────────────────────────────────
def _hist_out(h: EmployeeEntityHistory) -> dict:
    return {"id": str(h.id), "employee_id": str(h.employee_id), "entity_id": str(h.entity_id),
            "entity_name": h.entity_name, "from_date": h.from_date.isoformat() if h.from_date else None,
            "to_date": h.to_date.isoformat() if h.to_date else None, "status": h.status, "reason": h.reason,
            "career_change_id": str(h.career_change_id) if h.career_change_id else None,
            "created_by": h.created_by, "created_at": h.created_at.isoformat() if h.created_at else None}


async def _current_spell(db, org: uuid.UUID, e: Employee, ents: dict) -> EmployeeEntityHistory | None:
    """The open history row for the employee's current entity; created on the fly for a
    person who predates the history table (from their hire date)."""
    row = (await db.execute(select(EmployeeEntityHistory).where(
        EmployeeEntityHistory.org_id == org, EmployeeEntityHistory.employee_id == e.id,
        EmployeeEntityHistory.status == "current"))).scalars().first()
    if row:
        return row
    cur = e.entity_id
    if not cur:
        d = await LE.default_entity(org)
        cur = d["id"] if d else None
    if not cur:
        return None
    row = EmployeeEntityHistory(org_id=org, employee_id=e.id, entity_id=cur, entity_name=LE.entity_label(ents.get(str(cur))),
                                from_date=e.hire_date, status="current", created_by="system:backfill")
    db.add(row)
    await db.flush()
    return row


async def _apply_scheduled(db, org: uuid.UUID, h: EmployeeEntityHistory, ents: dict) -> bool:
    """Make a scheduled spell current: close the open one the day before, set employees.entity_id."""
    e = (await db.execute(select(Employee).where(Employee.id == h.employee_id, Employee.org_id == org))).scalar_one_or_none()
    if not e:
        return False
    open_row = await _current_spell(db, org, e, ents)
    if open_row and open_row.id != h.id:
        open_row.to_date = (h.from_date - timedelta(days=1)) if h.from_date else None
        open_row.status = "closed"
    e.entity_id = h.entity_id
    h.status = "current"
    if h.career_change_id:
        c = (await db.execute(select(CareerChange).where(CareerChange.id == h.career_change_id))).scalar_one_or_none()
        if c and c.status in ("approved", "submitted", "draft"):
            from career_common import now as _now
            c.status, c.applied_at = "applied", _now()
    return True


async def apply_due_transfers(db, org: uuid.UUID, today: date | None = None) -> int:
    """Apply every scheduled transfer whose effective date has arrived (oldest first)."""
    today = today or await _today(org)
    ents = await LE.entity_map(org)
    rows = (await db.execute(select(EmployeeEntityHistory).where(
        EmployeeEntityHistory.org_id == org, EmployeeEntityHistory.status == "scheduled",
        EmployeeEntityHistory.from_date <= today).order_by(EmployeeEntityHistory.from_date))).scalars().all()
    n = 0
    for h in rows:
        if await _apply_scheduled(db, org, h, ents):
            n += 1
    if n:
        await db.commit()
    return n


# ── routes ───────────────────────────────────────────────────────────────────
@entities_sub.get("/entities")
async def list_entities(request: Request, db: AsyncSession = Depends(get_db)):
    """Entities of the org with headcount — the picker + the entity column everywhere."""
    org = _org(request)
    await apply_due_transfers(db, org)
    ents = await LE.list_entities(org, include_inactive=True, fresh=True)
    counts = {str(eid): n for eid, n in (await db.execute(
        select(Employee.entity_id, func.count(Employee.id)).where(Employee.org_id == org, Employee.is_active.is_(True))
        .group_by(Employee.entity_id))).all()}
    d = next((x for x in ents if x.get("is_default")), ents[0] if ents else None)
    out = []
    for x in ents:
        n = counts.get(str(x["id"]), 0)
        if d and x["id"] == d["id"]:
            n += counts.get("None", 0)            # unassigned rows belong to the default entity
        out.append({"id": str(x["id"]), "name": x["name"], "short_code": x["short_code"],
                    "is_default": bool(x.get("is_default")), "is_active": x.get("is_active") is not False,
                    "province": x.get("province") or x.get("state_code"), "country": "CA", "employee_count": n})
    return {"entities": out, "default_id": str(d["id"]) if d else None,
            "unassigned": counts.get("None", 0), "view": LE.request_entity_id(request) or "all"}


@entities_sub.get("/entities/transfers")
async def list_transfers(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    access.require_edit(request)
    await apply_due_transfers(db, org)
    q = select(EmployeeEntityHistory, Employee).join(Employee, Employee.id == EmployeeEntityHistory.employee_id).where(
        EmployeeEntityHistory.org_id == org, EmployeeEntityHistory.career_change_id.is_not(None))
    if status:
        q = q.where(EmployeeEntityHistory.status == status)
    rows = (await db.execute(q.order_by(EmployeeEntityHistory.created_at.desc()))).all()
    return {"transfers": [{**_hist_out(h), "employee": f"{e.first_name} {e.last_name}", "employee_number": e.employee_number}
                          for h, e in rows]}


@entities_sub.post("/entities/apply-due")
async def apply_due_now(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    access.require_hr_write(request, "apply due entity transfers")   # HR / admin / internal tick
    return {"applied": await apply_due_transfers(db, org)}


@entities_sub.get("/{employee_id}/entity-history")
async def entity_history(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    eid = _uuid(employee_id)
    e = (await db.execute(select(Employee).where(Employee.id == eid, Employee.org_id == org))).scalar_one_or_none() if eid else None
    if not e:
        raise HTTPException(404, "Employee not found")
    v = await access.viewer(request, db)
    if not access.can_see(v, e):
        raise HTTPException(404, "Employee not found")
    await apply_due_transfers(db, org)
    ents = await LE.entity_map(org)
    cur = await _current_spell(db, org, e, ents)
    if cur:
        await db.commit()
    rows = (await db.execute(select(EmployeeEntityHistory).where(
        EmployeeEntityHistory.org_id == org, EmployeeEntityHistory.employee_id == e.id)
        .order_by(EmployeeEntityHistory.from_date.desc().nulls_last(), EmployeeEntityHistory.created_at.desc()))).scalars().all()
    cur_ent = ents.get(str(e.entity_id)) if e.entity_id else None
    return {"employee_id": str(e.id), "entity_id": str(e.entity_id) if e.entity_id else None,
            "entity_name": LE.entity_label(cur_ent), "history": [_hist_out(h) for h in rows]}


class TransferBody(BaseModel):
    entity_id: str
    effective_date: str
    reason: str | None = None
    note: str | None = None
    # 2026-10-24 forms wrap-up: transfer terms, recorded on the career-change record (new_values["terms"])
    # for audit / final-pay decisions. Recorded only — nothing here changes payroll or leave by itself.
    transfer_type: Literal["permanent", "secondment", "business_transfer"] = "permanent"
    service_continuity: bool = True   # earlier service counts for vacation / notice under the new entity
    carry_leave: bool = True          # leave balances move with the person (else pay out / reset)
    final_pay_under_old_entity: bool = False   # old entity issues final pay + ROE


@entities_sub.post("/{employee_id}/entity-transfer", status_code=201)
async def entity_transfer(employee_id: str, body: TransferBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Move an employee to another legal entity from an effective date (HR / admin)."""
    access.require_hr_write(request, "transfer employees between legal entities")
    org = _org(request)
    eid = _uuid(employee_id)
    e = (await db.execute(select(Employee).where(Employee.id == eid, Employee.org_id == org))).scalar_one_or_none() if eid else None
    if not e:
        raise HTTPException(404, "Employee not found")
    target = await LE.entity_by_id(org, body.entity_id)
    if not target or target.get("is_active") is False:
        raise HTTPException(422, "Choose an active legal entity of this organization")
    try:
        eff = date.fromisoformat(body.effective_date)
    except ValueError:
        raise HTTPException(400, "effective_date must be YYYY-MM-DD")
    ents = await LE.entity_map(org)
    cur = await _current_spell(db, org, e, ents)
    cur_id = e.entity_id or (cur.entity_id if cur else None)
    if cur_id and cur_id == target["id"]:
        raise HTTPException(400, f"{e.first_name} already belongs to {target['name']}")
    if cur and cur.from_date and eff <= cur.from_date:
        raise HTTPException(400, f"Effective date must be after the current spell started ({cur.from_date.isoformat()})")
    pending = (await db.execute(select(EmployeeEntityHistory).where(
        EmployeeEntityHistory.org_id == org, EmployeeEntityHistory.employee_id == e.id,
        EmployeeEntityHistory.status == "scheduled"))).scalars().first()
    if pending:
        raise HTTPException(409, f"A transfer to {pending.entity_name} is already scheduled for {pending.from_date}")
    today = await _today(org)
    old_ent = ents.get(str(cur_id)) if cur_id else None
    change = CareerChange(org_id=org, employee_id=e.id, change_type="entity_transfer", effective_date=eff,
                          old_values={"entity_id": str(cur_id) if cur_id else None, "entity_name": LE.entity_label(old_ent)},
                          new_values={"entity_id": str(target["id"]), "entity_name": target["name"],
                                      "terms": {"transfer_type": body.transfer_type,
                                                "service_continuity": body.service_continuity,
                                                "carry_leave": body.carry_leave,
                                                "final_pay_under_old_entity": body.final_pay_under_old_entity}},
                          reason=(body.reason or "")[:200] or None, note=body.note,
                          status="approved", created_by=_actor(request), created_by_id=request.headers.get("x-user-id"),
                          decided_by=_actor(request))
    db.add(change)
    await db.flush()
    h = EmployeeEntityHistory(org_id=org, employee_id=e.id, entity_id=target["id"], entity_name=target["name"],
                              from_date=eff, status="scheduled", reason=change.reason, career_change_id=change.id,
                              created_by=_actor(request))
    db.add(h)
    await db.flush()
    applied = False
    if eff <= today:
        applied = await _apply_scheduled(db, org, h, ents)
    await db.commit()
    await db.refresh(e)
    from shared.audit_trail import audit_change
    await audit_change(request, "employee.entity_transfer", entity_type="employee", entity_id=str(e.id),
                       before={"entity_id": str(cur_id) if cur_id else None, "entity_name": LE.entity_label(old_ent)},
                       after={"entity_id": str(target["id"]), "entity_name": target["name"], "effective_date": eff.isoformat(),
                              "applied": applied},
                       summary=f"{e.first_name} {e.last_name}: {LE.entity_label(old_ent) or 'unassigned'} → {target['name']} "
                               f"from {eff.isoformat()}{'' if applied else ' (scheduled)'}",
                       extra={"transfer_type": body.transfer_type})
    return {"ok": True, "applied": applied, "career_change_id": str(change.id), "entity_id": str(e.entity_id) if e.entity_id else None,
            "history": _hist_out(h)}
