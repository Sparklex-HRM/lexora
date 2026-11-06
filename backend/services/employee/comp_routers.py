"""Effective-dated compensation history — API (employee service).

GET  /employees/compensation/history?employee_ids=a,b   HR · {employee_id: [rows oldest first]}
GET  /employees/compensation/mine                       the logged-in employee (read-only)
GET  /employees/{employee_id}/compensation              HR or the employee · current + timeline
POST /employees/{employee_id}/compensation/history      HR / payroll · append ONE row and set the
                                                        employee's current salary in the same
                                                        transaction (idempotent per revision id)

The employee's `annual_salary` / `hourly_rate` stay what payroll reads today. This
table is the history behind them. Rows are append-only.
"""
import sys
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.comp import currency_for  # noqa: E402
from shared.utils.org_time import local_today, org_today, to_local_date  # noqa: E402

from career_common import (actor_of, full_name, is_hr, is_self, load_employee, my_employee, org_of,  # noqa: E402
                           parse_date, require_hr, to_uuid)
from comp_models import CompHistory  # noqa: E402
from database import get_db  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/employees", tags=["compensation"])

REASONS = ("opening", "annual_increment", "promotion", "market_correction", "correction", "direct_edit", "other")


def _f(v) -> float | None:
    return float(v) if v is not None else None


def row_out(r: CompHistory) -> dict:
    return {"id": str(r.id), "employee_id": str(r.employee_id),
            "effective_from": r.effective_from.isoformat(),
            "effective_to": r.effective_to.isoformat() if r.effective_to else None,
            "pay_type": r.pay_type, "annual_amount": _f(r.annual_amount), "hourly_rate": _f(r.hourly_rate),
            "currency": r.currency, "components": r.components or {}, "reason": r.reason, "note": r.note,
            "source": r.source, "source_revision_id": str(r.source_revision_id) if r.source_revision_id else None,
            "created_by": r.created_by, "approved_by": r.approved_by,
            "created_at": r.created_at.isoformat() if r.created_at else None}


def _current_amounts(e: Employee) -> tuple[float, float]:
    return float(e.annual_salary or 0), float(e.hourly_rate or 0)


def _opening_row(e: Employee) -> CompHistory:
    annual, hourly = _current_amounts(e)
    start = e.hire_date or (to_local_date(e.created_at) if e.created_at else local_today())
    return CompHistory(org_id=e.org_id, employee_id=e.id, effective_from=start,
                       pay_type=e.pay_type or "salary", annual_amount=annual or None, hourly_rate=hourly or None,
                       currency=currency_for(e.address_country), components={}, reason="opening",
                       source="opening", created_by="system",
                       note="Opening salary — the value on the employee record when history began.")


async def backfill(db: AsyncSession, org: str | None = None) -> int:
    """ONE opening row for every employee that has a salary but no history. Idempotent:
    a second run finds nothing to add. Returns rows added."""
    q = select(Employee)
    if org:
        q = q.where(Employee.org_id == to_uuid(org, "org"))
    have = select(CompHistory.employee_id)
    if org:
        have = have.where(CompHistory.org_id == to_uuid(org, "org"))
    done = {r[0] for r in (await db.execute(have.distinct())).all()}
    n = 0
    for e in (await db.execute(q)).scalars().all():
        if e.id in done or not any(_current_amounts(e)):
            continue
        db.add(_opening_row(e))
        n += 1
    if n:
        await db.commit()
    return n


async def reconcile(db: AsyncSession, org: str) -> int:
    """A salary typed straight onto the employee record (not through a revision) still
    gets a history row, dated today and marked as a direct edit — so the timeline never
    silently disagrees with what payroll reads."""
    await backfill(db, org)
    emps = {e.id: e for e in (await db.execute(select(Employee).where(
        Employee.org_id == to_uuid(org, "org")))).scalars().all()}
    rows = (await db.execute(select(CompHistory).where(CompHistory.org_id == to_uuid(org, "org"))
                             .order_by(CompHistory.effective_from, CompHistory.created_at))).scalars().all()
    latest: dict = {}
    for r in rows:
        latest[r.employee_id] = r
    n = 0
    today = await org_today(org)
    for eid, e in emps.items():
        last = latest.get(eid)
        if not last:
            continue
        annual, hourly = _current_amounts(e)
        is_hourly = (e.pay_type or "salary") == "hourly"
        cur, was = (hourly, float(last.hourly_rate or 0)) if is_hourly else (annual, float(last.annual_amount or 0))
        if cur <= 0 or abs(cur - was) < 0.005:
            continue
        if last.effective_from >= today:
            eff = last.effective_from + timedelta(days=1)
        else:
            eff = today
        last.effective_to = eff - timedelta(days=1)
        db.add(CompHistory(org_id=e.org_id, employee_id=e.id, effective_from=eff, pay_type=e.pay_type or "salary",
                           annual_amount=annual or None, hourly_rate=hourly or None,
                           currency=currency_for(e.address_country), components={}, reason="direct_edit",
                           source="direct_edit", created_by="system",
                           note="Salary was changed on the employee record, not through a salary revision."))
        n += 1
    if n:
        await db.commit()
    return n


async def _history(db, org: str, employee_id=None) -> list[CompHistory]:
    q = select(CompHistory).where(CompHistory.org_id == to_uuid(org, "org"))
    if employee_id:
        q = q.where(CompHistory.employee_id == to_uuid(employee_id, "employee"))
    return list((await db.execute(q.order_by(CompHistory.effective_from, CompHistory.created_at))).scalars().all())


def _timeline(rows: list[CompHistory]) -> list[dict]:
    """Newest first, each row with the change from the one before it."""
    out, prev = [], None
    for r in rows:
        d = row_out(r)
        key = "hourly_rate" if r.pay_type == "hourly" else "annual_amount"
        if prev is not None and prev.get(key) and d.get(key) is not None:
            d["change_amount"] = round(d[key] - prev[key], 2)
            d["change_percent"] = round((d[key] - prev[key]) * 100 / prev[key], 2) if prev[key] else None
        out.append(d)
        prev = d
    return list(reversed(out))


async def _employee_view(db, org: str, e: Employee) -> dict:
    await reconcile(db, org)
    rows = await _history(db, org, str(e.id))
    annual, hourly = _current_amounts(e)
    tl = _timeline(rows)
    return {"employee": {"id": str(e.id), "name": full_name(e), "employee_number": e.employee_number,
                         "job_title": e.job_title, "department": e.department,
                         "country": (e.address_country or "CA").upper(), "pay_frequency": e.pay_frequency},
            "current": {"pay_type": e.pay_type or "salary", "annual_amount": annual, "hourly_rate": hourly,
                        "currency": currency_for(e.address_country),
                        "components": (tl[0]["components"] if tl else {}),
                        "effective_from": tl[0]["effective_from"] if tl else None},
            "history": tl}


# ── static routes first ───────────────────────────────────────────────────────
@router.get("/compensation/history")
async def history_map(request: Request, employee_ids: str | None = None, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    await reconcile(db, org)
    wanted = {x.strip() for x in (employee_ids or "").split(",") if x.strip()}
    out: dict = {}
    for r in await _history(db, org):
        if wanted and str(r.employee_id) not in wanted:
            continue
        out.setdefault(str(r.employee_id), []).append(row_out(r))
    return out


@router.get("/compensation/mine")
async def my_compensation(request: Request, db: AsyncSession = Depends(get_db)):
    e = await my_employee(request, db)
    return await _employee_view(db, org_of(request), e)


@router.get("/{employee_id}/compensation")
async def employee_compensation(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    if not (is_hr(request) or is_self(request, e)):
        raise HTTPException(403, "You can only see your own compensation")
    return await _employee_view(db, org, e)


class HistoryIn(BaseModel):
    effective_from: str
    annual_amount: float | None = None
    hourly_rate: float | None = None
    components: dict | None = None
    reason: str | None = None
    note: str | None = None
    source: str = "revision"
    source_revision_id: str | None = None
    created_by: str | None = None
    approved_by: str | None = None
    apply_to_employee: bool = True


@router.post("/{employee_id}/compensation/history", status_code=201)
async def add_history(employee_id: str, body: HistoryIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Append one history row. With apply_to_employee the employee's current salary is
    set in the SAME transaction, so the record and its history cannot drift apart.
    Calling it again with the same source_revision_id returns the row already written."""
    require_hr(request)
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    eff = parse_date(body.effective_from, "effective_from")
    rid = to_uuid(body.source_revision_id, "source_revision_id") if body.source_revision_id else None
    if rid:
        dup = (await db.execute(select(CompHistory).where(
            CompHistory.org_id == e.org_id, CompHistory.employee_id == e.id,
            CompHistory.source_revision_id == rid))).scalars().first()
        if dup:
            return {**row_out(dup), "already_recorded": True}
    if not (body.annual_amount and body.annual_amount > 0) and not (body.hourly_rate and body.hourly_rate > 0):
        raise HTTPException(400, "Give the new annual salary or the new hourly rate")
    if not (await _history(db, org, employee_id)) and any(_current_amounts(e)):
        db.add(_opening_row(e))                       # keep the salary this row replaces
        await db.flush()
    rows = await _history(db, org, employee_id)
    before = [r for r in rows if r.effective_from < eff]
    if before:
        before[-1].effective_to = eff - timedelta(days=1)
    after = [r for r in rows if r.effective_from > eff]
    is_hourly = bool(body.hourly_rate and body.hourly_rate > 0 and not body.annual_amount)
    row = CompHistory(id=uuid.uuid4(), org_id=e.org_id, employee_id=e.id, effective_from=eff,
                      effective_to=(after[0].effective_from - timedelta(days=1)) if after else None,
                      pay_type="hourly" if is_hourly else (e.pay_type or "salary"),
                      annual_amount=body.annual_amount or None, hourly_rate=body.hourly_rate or None,
                      currency=currency_for(e.address_country), components=body.components or {},
                      reason=(body.reason or "other")[:60], note=body.note, source=(body.source or "revision")[:20],
                      source_revision_id=rid, created_by=body.created_by or actor_of(request),
                      approved_by=body.approved_by)
    db.add(row)
    applied = False
    if body.apply_to_employee and not after:          # only the LATEST row drives the live record
        if body.annual_amount and body.annual_amount > 0:
            e.annual_salary = round(float(body.annual_amount), 2)
        if body.hourly_rate and body.hourly_rate > 0:
            e.hourly_rate = round(float(body.hourly_rate), 2)
        applied = True
    await db.commit()
    await record_audit(request, "compensation.history.add", entity_type="employee", entity_id=employee_id,
                       summary=f"Salary history row from {eff.isoformat()} ({row.reason})",
                       meta={"source": row.source, "revision": body.source_revision_id, "applied": applied})
    return {**row_out(row), "applied_to_employee": applied}
