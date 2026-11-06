"""Stream G2 — Project Services (PSA) routes under /ops/psa/*.

Builds ON the existing ops resources: clients and projects are read from `ops_clients` /
`ops_projects`; an invoice draft is written into `ops_invoices` (the same row the generic
/ops/invoices screen lists) with its lines in `psa_invoice_lines`. Billable hours come from
the time service's PSA view (/time/psa/*), which owns the timesheet tables.

GET/POST/PUT/DELETE /ops/psa/rate-cards              client rate cards
GET/POST/PUT/DELETE /ops/psa/assignments             project assignments (% allocation, dates)
GET  /ops/psa/bench?as_of=                           employees allocated under 100 %
GET  /ops/psa/billable-lines?from&to&client&project_id&employee_id&uninvoiced   timesheet lines + the rate that applies
POST /ops/psa/billable-lines/apply-rates {from, to}  stamp rate snapshots on lines that have none
GET  /ops/psa/utilisation?month=YYYY-MM&department=  billable ÷ available per employee + team roll-up
POST /ops/psa/invoices/draft                         T&M from approved+locked hours, fixed lines for milestone/retainer
GET  /ops/psa/invoices · GET /ops/psa/invoices/{id} · POST /{id}/status · POST /{id}/payment · DELETE /{id} (draft)
GET  /ops/psa/leakage?from&to                        approved billable hours not invoiced; invoiced not paid
GET  /ops/psa/revenue?from&to&group=client|project|month
GET/POST/DELETE /ops/psa/fx-rates                    manual rates to the home currency
GET  /ops/psa/settings                               home currency, hours per day, working days
"""
import sys
import uuid
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

from database import get_db  # noqa: E402
from models import Client, Invoice, Project  # noqa: E402
from psa_models import INVOICE_COLUMNS, Assignment, FxRate, InvoiceLine, RateCard  # noqa: E402
from psa_logic import (bench_rows, due_date, fx_convert, group_tm_lines, invoice_totals, leakage_summary,  # noqa: E402
                       month_bounds, next_invoice_number, price_lines, r2, revenue_rows, team_rollup, to_date,
                       utilisation_rows)

router = APIRouter(prefix="/ops/psa", tags=["ops-psa"])

OFFICE_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}
WRITE_ROLES = OFFICE_ROLES
FINANCE_ROLES = {"superadmin", "org_admin", "payroll_admin"}

_schema_ready = False


async def ensure_schema(db: AsyncSession) -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        for ddl in INVOICE_COLUMNS:
            await db.execute(text(ddl))
        await db.commit()
        _schema_ready = True
    except Exception:  # noqa: BLE001
        await db.rollback()


# ── request helpers ───────────────────────────────────────────────────────────
def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or "system"


def _require(request: Request, roles: set, msg: str):
    if _role(request) not in roles:
        raise HTTPException(403, msg)


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id")
            if request.headers.get(k)}


def _uuid(v, what="id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, f"{what} is not a valid id")


def _date(v, what: str, required: bool = True) -> date | None:
    d = to_date(v)
    if d is None and required:
        raise HTTPException(400, f"{what} must be a date (YYYY-MM-DD)")
    return d


def _ser(row) -> dict:
    out = {}
    for col in row.__table__.columns:
        v = getattr(row, col.name)
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, bool):
            v = float(v)
        out[col.name] = v
    return out


async def _employees(request: Request) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _time_get(request: Request, path: str, params: dict) -> list | dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{settings.TIME_URL}/time/psa/{path}", params=params, headers=_fwd(request))
    except (httpx.ConnectError, httpx.TimeoutException):
        raise HTTPException(503, "The time & attendance service could not be reached")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"Time service: {r.text[:200]}")
    return r.json()


async def _time_post(request: Request, path: str, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{settings.TIME_URL}/time/psa/{path}", json=body, headers=_fwd(request))
    except (httpx.ConnectError, httpx.TimeoutException):
        raise HTTPException(503, "The time & attendance service could not be reached")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"Time service: {r.text[:200]}")
    return r.json()


async def _psa_settings(org: str) -> dict:
    ww = await get_setting(org, "work_week", {}) or {}
    wh = await get_setting(org, "work_hours", {}) or {}
    loc = await get_setting(org, "locale", {}) or {}
    return {"working_days": ww.get("working_days") or [0, 1, 2, 3, 4],
            "hours_per_day": float(wh.get("hours_per_day") or 8),
            "home_currency": (loc.get("currency") or "").upper(),
            "country": loc.get("country") or ""}


async def _projects(db, org) -> dict:
    rows = (await db.execute(select(Project).where(Project.org_id == org, Project.is_active.is_(True)))).scalars().all()
    return {str(p.id): p for p in rows}


async def _cards(db, org) -> list[dict]:
    rows = (await db.execute(select(RateCard).where(RateCard.org_id == org, RateCard.is_active.is_(True)))).scalars().all()
    return [_ser(r) for r in rows]


async def _fx(db, org) -> dict:
    rows = (await db.execute(select(FxRate).where(FxRate.org_id == org).order_by(FxRate.as_of.desc()))).scalars().all()
    out: dict = {}
    for r in rows:                     # newest first → first seen wins
        out.setdefault(r.currency.upper(), float(r.rate_to_home))
    return out


@router.get("/settings")
async def psa_settings(request: Request):
    return await _psa_settings(_org(request))


# ── rate cards ────────────────────────────────────────────────────────────────
class RateCardIn(BaseModel):
    client: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    role: str | None = None
    employee_id: str | None = None
    employee_name: str | None = None
    rate: float
    unit: str = "hour"
    currency: str = ""
    effective_from: str
    effective_to: str | None = None
    note: str | None = None


def _card_fields(body: RateCardIn, home: str) -> dict:
    if body.unit not in ("hour", "day"):
        raise HTTPException(400, "Unit must be hour or day")
    if float(body.rate) <= 0:
        raise HTTPException(400, "Rate must be more than 0")
    if not (body.client or body.project_id or body.role or body.employee_id):
        raise HTTPException(400, "Give the card at least one of: client, project, role, employee")
    ef = _date(body.effective_from, "Effective from")
    et = _date(body.effective_to, "Effective to", required=False)
    if et and et < ef:
        raise HTTPException(400, "Effective to must be on or after effective from")
    return dict(client=(body.client or "").strip() or None, project_id=body.project_id or None,
                project_name=body.project_name or None, role=(body.role or "").strip() or None,
                employee_id=_uuid(body.employee_id, "employee") if body.employee_id else None,
                employee_name=body.employee_name or None, rate=round(float(body.rate), 2), unit=body.unit,
                currency=(body.currency or home or "")[:3].upper(), effective_from=ef, effective_to=et,
                note=(body.note or "")[:300] or None)


@router.get("/rate-cards")
async def list_rate_cards(request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    org = _org(request)
    rows = (await db.execute(select(RateCard).where(RateCard.org_id == org, RateCard.is_active.is_(True))
                             .order_by(RateCard.client, RateCard.effective_from.desc()))).scalars().all()
    return [_ser(r) for r in rows]


@router.post("/rate-cards", status_code=201)
async def create_rate_card(body: RateCardIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, WRITE_ROLES, "HR, payroll or admin role required")
    org = _org(request)
    st = await _psa_settings(org)
    row = RateCard(org_id=org, created_by=_actor(request), **_card_fields(body, st["home_currency"]))
    db.add(row); await db.commit(); await db.refresh(row)
    return _ser(row)


@router.put("/rate-cards/{cid}")
async def update_rate_card(cid: str, body: RateCardIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, WRITE_ROLES, "HR, payroll or admin role required")
    org = _org(request)
    row = (await db.execute(select(RateCard).where(RateCard.id == _uuid(cid), RateCard.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Rate card not found")
    st = await _psa_settings(org)
    for k, v in _card_fields(body, st["home_currency"]).items():
        setattr(row, k, v)
    await db.commit(); await db.refresh(row)
    return _ser(row)


@router.delete("/rate-cards/{cid}")
async def delete_rate_card(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, WRITE_ROLES, "HR, payroll or admin role required")
    org = _org(request)
    row = (await db.execute(select(RateCard).where(RateCard.id == _uuid(cid), RateCard.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Rate card not found")
    row.is_active = False
    await db.commit()
    return {"ok": True}


# ── assignments & bench ───────────────────────────────────────────────────────
class AssignmentIn(BaseModel):
    employee_id: str
    project_id: str
    role: str | None = None
    allocation_pct: float = 100
    start_date: str
    end_date: str | None = None
    billable: bool = True
    note: str | None = None


@router.get("/assignments")
async def list_assignments(request: Request, employee_id: str | None = None, project_id: str | None = None,
                           db: AsyncSession = Depends(get_db)):
    org = _org(request)
    q = select(Assignment).where(Assignment.org_id == org, Assignment.is_active.is_(True))
    if employee_id:
        q = q.where(Assignment.employee_id == _uuid(employee_id))
    if project_id:
        q = q.where(Assignment.project_id == project_id)
    role, me = _role(request), request.headers.get("x-employee-id")
    if role not in OFFICE_ROLES and role != "manager" and me:
        q = q.where(Assignment.employee_id == _uuid(me))
    rows = (await db.execute(q.order_by(Assignment.start_date.desc()))).scalars().all()
    return [_ser(r) for r in rows]


@router.post("/assignments", status_code=201)
async def create_assignment(body: AssignmentIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, WRITE_ROLES | {"manager"}, "HR, manager or admin role required")
    org = _org(request)
    if not (0 < float(body.allocation_pct) <= 100):
        raise HTTPException(400, "Allocation must be between 1 and 100 %")
    sd = _date(body.start_date, "Start date"); ed = _date(body.end_date, "End date", required=False)
    if ed and ed < sd:
        raise HTTPException(400, "End date must be on or after the start date")
    projects = await _projects(db, org)
    p = projects.get(body.project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    emp = next((e for e in await _employees(request) if e["id"] == body.employee_id), None)
    if not emp:
        raise HTTPException(404, "Employee not found")
    # the same person cannot be over 100 % across assignments that overlap the new range
    others = (await db.execute(select(Assignment).where(Assignment.org_id == org, Assignment.is_active.is_(True),
                                                        Assignment.employee_id == _uuid(body.employee_id)))).scalars().all()
    overlap_total = sum(float(a.allocation_pct) for a in others
                        if (not a.end_date or a.end_date >= sd) and (not ed or a.start_date <= ed))
    if overlap_total + float(body.allocation_pct) > 100:
        raise HTTPException(409, f"This person is already {overlap_total:.0f} % allocated in that period. "
                                 f"Only {max(100 - overlap_total, 0):.0f} % is free.")
    row = Assignment(org_id=org, employee_id=_uuid(body.employee_id),
                     employee_name=f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip(),
                     employee_number=emp.get("employee_number"), department=emp.get("department"),
                     project_id=body.project_id, project_name=p.name, client=p.client, role=body.role,
                     allocation_pct=round(float(body.allocation_pct), 2), start_date=sd, end_date=ed,
                     billable=body.billable, note=(body.note or "")[:300] or None)
    db.add(row); await db.commit(); await db.refresh(row)
    return _ser(row)


@router.put("/assignments/{aid}")
async def update_assignment(aid: str, body: AssignmentIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, WRITE_ROLES | {"manager"}, "HR, manager or admin role required")
    org = _org(request)
    row = (await db.execute(select(Assignment).where(Assignment.id == _uuid(aid), Assignment.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Assignment not found")
    if not (0 < float(body.allocation_pct) <= 100):
        raise HTTPException(400, "Allocation must be between 1 and 100 %")
    sd = _date(body.start_date, "Start date"); ed = _date(body.end_date, "End date", required=False)
    if ed and ed < sd:
        raise HTTPException(400, "End date must be on or after the start date")
    row.role, row.allocation_pct, row.start_date, row.end_date = body.role, round(float(body.allocation_pct), 2), sd, ed
    row.billable, row.note = body.billable, (body.note or "")[:300] or None
    await db.commit(); await db.refresh(row)
    return _ser(row)


@router.delete("/assignments/{aid}")
async def end_assignment(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, WRITE_ROLES | {"manager"}, "HR, manager or admin role required")
    org = _org(request)
    row = (await db.execute(select(Assignment).where(Assignment.id == _uuid(aid), Assignment.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Assignment not found")
    row.is_active = False
    await db.commit()
    return {"ok": True}


@router.get("/bench")
async def bench(request: Request, as_of: str | None = None, db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    org = _org(request)
    on = _date(as_of, "as_of", required=False) or await org_today(org)
    emps = [e for e in await _employees(request) if e.get("status") not in ("terminated",)]
    rows = (await db.execute(select(Assignment).where(Assignment.org_id == org, Assignment.is_active.is_(True)))).scalars().all()
    return {"as_of": on.isoformat(), "rows": bench_rows(emps, [_ser(a) for a in rows], on),
            "headcount": len(emps)}


# ── billable hours ────────────────────────────────────────────────────────────
async def _priced_lines(request, db, org, params: dict) -> list[dict]:
    st = await _psa_settings(org)
    raw = await _time_get(request, "lines", params)
    projects = await _projects(db, org)
    project_client = {pid: (p.client or "") for pid, p in projects.items()}
    emps = {e["id"]: e for e in await _employees(request)}
    for l in raw:
        l["client"] = project_client.get(str(l.get("project_id")) or "") or None
        l["role"] = (emps.get(l["employee_id"]) or {}).get("job_title")
        l["department"] = (emps.get(l["employee_id"]) or {}).get("department")
    cards = await _cards(db, org)
    return price_lines(raw, cards, hours_per_day=st["hours_per_day"], client=None,
                       default_currency=st["home_currency"], project_client=project_client)


@router.get("/billable-lines")
async def billable_lines(request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    org = _org(request)
    p = request.query_params
    params = {k: p[k] for k in ("from", "to", "employee_id", "project_id", "status", "uninvoiced", "billable") if p.get(k)}
    lines = await _priced_lines(request, db, org, params)
    if p.get("client"):
        lines = [l for l in lines if (l.get("client") or "").lower() == p["client"].lower()]
    return lines


class RangeIn(BaseModel):
    from_date: str | None = None
    to_date: str | None = None
    overwrite: bool = False


@router.post("/billable-lines/apply-rates")
async def apply_rates(body: RangeIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Stamp the rate card in force on each approved/locked billable line that has no snapshot yet."""
    _require(request, WRITE_ROLES, "HR, payroll or admin role required")
    org = _org(request)
    params = {"billable": "1", "uninvoiced": "1"}
    if body.from_date:
        params["from"] = body.from_date
    if body.to_date:
        params["to"] = body.to_date
    lines = await _priced_lines(request, db, org, params)
    todo = [{"id": l["id"], "bill_rate": l["rate"], "bill_unit": l["unit"], "bill_currency": l["currency"],
             "rate_card_id": l.get("rate_card_id")} for l in lines
            if l["rate_source"] == "card" or (body.overwrite and not l["missing_rate"])]
    if not todo:
        return {"stamped": 0, "missing_rate": sum(1 for l in lines if l["missing_rate"])}
    res = await _time_post(request, "lines/snapshot", {"lines": todo, "overwrite": body.overwrite})
    return {**res, "missing_rate": sum(1 for l in lines if l["missing_rate"])}


# ── utilisation ───────────────────────────────────────────────────────────────
@router.get("/utilisation")
async def utilisation(request: Request, month: str | None = None, department: str | None = None,
                      db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    org = _org(request)
    month = month or (await org_today(org)).strftime("%Y-%m")
    try:
        a, b = month_bounds(month)
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")
    st = await _psa_settings(org)
    emps = [e for e in await _employees(request) if e.get("status") != "terminated"]
    if department:
        emps = [e for e in emps if (e.get("department") or "").lower() == department.lower()]
    summ = await _time_get(request, "summary", {"from": a.isoformat(), "to": b.isoformat()})
    by = {s["employee_id"]: s for s in summ}
    rows = utilisation_rows(emps, by, a, b, st["working_days"], st["hours_per_day"])
    tot_avail = r2(sum(r["available"] for r in rows)); tot_bill = r2(sum(r["billable"] for r in rows))
    return {"month": month, "from": a.isoformat(), "to": b.isoformat(), "hours_per_day": st["hours_per_day"],
            "working_days": st["working_days"], "rows": rows, "teams": team_rollup(rows),
            "totals": {"available": tot_avail, "billable": tot_bill,
                       "non_billable": r2(sum(r["non_billable"] for r in rows)),
                       "utilisation_pct": r2(tot_bill * 100 / tot_avail) if tot_avail else 0.0}}


# ── invoices ──────────────────────────────────────────────────────────────────
class FixedLine(BaseModel):
    kind: str = "milestone"          # milestone | retainer | other
    description: str
    amount: float
    project_id: str | None = None


class DraftIn(BaseModel):
    client: str
    period_start: str
    period_end: str
    invoice_type: str = "tm"         # tm | milestone | retainer | mixed
    currency: str | None = None
    tax_pct: float = 0
    include_hours: bool = True
    fixed_lines: list[FixedLine] = []
    issue_date: str | None = None
    note: str | None = None


async def _inv_out(db, org, inv: Invoice) -> dict:
    extra = (await db.execute(text(
        "SELECT currency, paid_amount, paid_on, period_start, period_end, invoice_type, note, created_by "
        "FROM ops_invoices WHERE id = CAST(:id AS uuid)"), {"id": str(inv.id)})).mappings().first() or {}
    lines = (await db.execute(select(InvoiceLine).where(InvoiceLine.org_id == org, InvoiceLine.invoice_id == inv.id)
                              .order_by(InvoiceLine.created_at))).scalars().all()
    out = _ser(inv)
    out.update({k: (v.isoformat() if isinstance(v, (date, datetime)) else (float(v) if hasattr(v, "__float__") and v is not None and not isinstance(v, bool) else v))
                for k, v in dict(extra).items()})
    out["lines"] = [_ser(l) for l in lines]
    out["hours"] = r2(sum(float(l.hours or 0) for l in lines))
    out["outstanding"] = r2(float(inv.total or 0) - float(extra.get("paid_amount") or 0))
    return out


@router.post("/invoices/draft", status_code=201)
async def draft_invoice(body: DraftIn, request: Request, db: AsyncSession = Depends(get_db)):
    """One draft invoice for a client and a period. T&M lines = approved+locked billable
    hours of that client's projects not yet invoiced, hours × the rate in force (a stamped
    snapshot is kept). Milestone / retainer amounts are typed in as fixed lines."""
    _require(request, FINANCE_ROLES | {"hr_admin"}, "Finance, payroll or admin role required")
    await ensure_schema(db)
    org = _org(request)
    ps, pe = _date(body.period_start, "Period start"), _date(body.period_end, "Period end")
    if pe < ps:
        raise HTTPException(400, "Period end must be on or after the start")
    client = (await db.execute(select(Client).where(Client.org_id == org, Client.is_active.is_(True),
                                                    Client.name == body.client.strip()))).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found — add it under Staffing & Ops → Clients first")
    st = await _psa_settings(org)
    currency = (body.currency or st["home_currency"] or "")[:3].upper()
    tm_lines: list[dict] = []
    priced: list[dict] = []
    if body.include_hours and body.invoice_type in ("tm", "mixed"):
        priced = await _priced_lines(request, db, org, {"from": ps.isoformat(), "to": pe.isoformat(),
                                                        "billable": "1", "uninvoiced": "1"})
        priced = [l for l in priced if (l.get("client") or "").lower() == client.name.lower()]
        tm_lines = group_tm_lines(priced)
    fixed = []
    for f in body.fixed_lines:
        if float(f.amount) <= 0 or not f.description.strip():
            raise HTTPException(400, "Every fixed line needs a description and an amount above 0")
        if f.kind not in ("milestone", "retainer", "other"):
            raise HTTPException(400, "Fixed line kind must be milestone, retainer or other")
        fixed.append({"kind": f.kind, "description": f.description.strip()[:300], "amount": r2(f.amount),
                      "project_id": f.project_id, "hours": 0.0, "rate": 0.0, "unit": None, "currency": currency,
                      "line_ids": [], "missing_rate": False, "employee_id": None, "employee_name": None,
                      "project_name": None, "period_start": ps, "period_end": pe})
    all_lines = tm_lines + fixed
    if not all_lines:
        raise HTTPException(409, "Nothing to invoice: no uninvoiced approved billable hours for this client in the "
                                 "period and no fixed lines")
    if any(l["currency"] != currency for l in tm_lines):
        raise HTTPException(409, "The rate cards for these hours are in a different currency than the invoice. "
                                 "Pick that currency for the invoice or fix the cards.")
    tot = invoice_totals(all_lines, body.tax_pct)
    issue = _date(body.issue_date, "Issue date", required=False) or await org_today(org)
    existing = [r[0] for r in (await db.execute(select(Invoice.name).where(Invoice.org_id == org))).all()]
    number = next_invoice_number(existing, issue.year)
    inv = Invoice(org_id=org, name=number, client=client.name, issue_date=issue,
                  due_date=due_date(issue, client.payment_terms), amount=tot["amount"], tax=tot["tax"],
                  total=tot["total"], status="draft")
    db.add(inv); await db.flush()
    await db.execute(text("UPDATE ops_invoices SET currency=:c, period_start=:ps, period_end=:pe, invoice_type=:t, "
                          "note=:n, created_by=:by WHERE id = CAST(:id AS uuid)"),
                     {"c": currency, "ps": ps, "pe": pe, "t": body.invoice_type, "n": (body.note or "")[:400] or None,
                      "by": _actor(request), "id": str(inv.id)})
    marked = 0
    for l in all_lines:
        row = InvoiceLine(org_id=org, invoice_id=inv.id, kind=l["kind"], description=l["description"],
                          employee_id=_uuid(l["employee_id"]) if l.get("employee_id") else None,
                          employee_name=l.get("employee_name"), project_id=l.get("project_id"),
                          project_name=l.get("project_name"), hours=l.get("hours") or 0, unit=l.get("unit"),
                          rate=l.get("rate") or 0, amount=l["amount"], currency=currency,
                          period_start=l.get("period_start") or ps, period_end=l.get("period_end") or pe,
                          line_ids=list(l.get("line_ids") or []), missing_rate=bool(l.get("missing_rate")))
        db.add(row); await db.flush()
        if row.line_ids:
            # snapshot the rate on the hours first (keeps the price even if the card changes), then mark them invoiced
            snap = [{"id": lid, "bill_rate": l["rate"], "bill_unit": l["unit"] or "hour", "bill_currency": currency,
                     "rate_card_id": None} for lid in row.line_ids]
            await _time_post(request, "lines/snapshot", {"lines": snap, "overwrite": False})
            res = await _time_post(request, "lines/invoiced", {"line_ids": row.line_ids, "invoice_line_id": str(row.id)})
            marked += int(res.get("marked") or 0)
    await db.commit(); await db.refresh(inv)
    out = await _inv_out(db, org, inv)
    out["timesheet_lines_marked"] = marked
    out["missing_rate_lines"] = sum(1 for l in priced if l.get("missing_rate"))
    return out


@router.get("/invoices")
async def list_invoices(request: Request, client: str | None = None, status: str | None = None,
                        db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    await ensure_schema(db)
    org = _org(request)
    q = select(Invoice).where(Invoice.org_id == org, Invoice.is_active.is_(True))
    if client:
        q = q.where(Invoice.client == client)
    if status:
        q = q.where(Invoice.status.in_([s.strip() for s in status.split(",")]))
    rows = (await db.execute(q.order_by(Invoice.issue_date.desc(), Invoice.name.desc()))).scalars().all()
    return [await _inv_out(db, org, r) for r in rows]


@router.get("/invoices/{iid}")
async def invoice_detail(iid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    await ensure_schema(db)
    org = _org(request)
    inv = (await db.execute(select(Invoice).where(Invoice.id == _uuid(iid), Invoice.org_id == org))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return await _inv_out(db, org, inv)


class StatusIn(BaseModel):
    status: str


@router.post("/invoices/{iid}/status")
async def set_status(iid: str, body: StatusIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, FINANCE_ROLES | {"hr_admin"}, "Finance, payroll or admin role required")
    org = _org(request)
    inv = (await db.execute(select(Invoice).where(Invoice.id == _uuid(iid), Invoice.org_id == org))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    allowed = {"draft": {"sent"}, "sent": {"overdue", "paid"}, "overdue": {"paid"}, "paid": set()}
    if body.status not in allowed.get(inv.status or "draft", set()):
        raise HTTPException(409, f"An invoice that is {inv.status} cannot become {body.status}")
    inv.status = body.status
    if body.status == "paid":
        await db.execute(text("UPDATE ops_invoices SET paid_amount = total, paid_on = COALESCE(paid_on, :d) "
                              "WHERE id = CAST(:id AS uuid)"), {"d": await org_today(org), "id": str(inv.id)})
    await db.commit(); await db.refresh(inv)
    return await _inv_out(db, org, inv)


class PaymentIn(BaseModel):
    amount: float
    paid_on: str | None = None


@router.post("/invoices/{iid}/payment")
async def record_payment(iid: str, body: PaymentIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, FINANCE_ROLES | {"hr_admin"}, "Finance, payroll or admin role required")
    await ensure_schema(db)
    org = _org(request)
    inv = (await db.execute(select(Invoice).where(Invoice.id == _uuid(iid), Invoice.org_id == org))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status == "draft":
        raise HTTPException(409, "Send the invoice before recording a payment")
    if float(body.amount) <= 0:
        raise HTTPException(400, "Amount must be more than 0")
    paid_on = _date(body.paid_on, "Paid on", required=False) or await org_today(org)
    cur = (await db.execute(text("SELECT COALESCE(paid_amount, 0) FROM ops_invoices WHERE id = CAST(:id AS uuid)"),
                            {"id": str(inv.id)})).scalar() or 0
    new_paid = r2(float(cur) + float(body.amount))
    if new_paid > float(inv.total or 0) + 0.005:
        raise HTTPException(409, f"That is more than the invoice total. Outstanding is {r2(float(inv.total or 0) - float(cur)):,.2f}")
    await db.execute(text("UPDATE ops_invoices SET paid_amount = :p, paid_on = :d WHERE id = CAST(:id AS uuid)"),
                     {"p": new_paid, "d": paid_on, "id": str(inv.id)})
    if new_paid >= float(inv.total or 0) - 0.005:
        inv.status = "paid"
    await db.commit(); await db.refresh(inv)
    return await _inv_out(db, org, inv)


@router.delete("/invoices/{iid}")
async def discard_draft(iid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Discard a DRAFT: its hours go back to the uninvoiced pool."""
    _require(request, FINANCE_ROLES | {"hr_admin"}, "Finance, payroll or admin role required")
    org = _org(request)
    inv = (await db.execute(select(Invoice).where(Invoice.id == _uuid(iid), Invoice.org_id == org))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status != "draft":
        raise HTTPException(409, "Only a draft can be discarded")
    lines = (await db.execute(select(InvoiceLine).where(InvoiceLine.org_id == org, InvoiceLine.invoice_id == inv.id))).scalars().all()
    released = 0
    for l in lines:
        if l.line_ids:
            res = await _time_post(request, "lines/uninvoiced", {"invoice_line_id": str(l.id)})
            released += int(res.get("released") or 0)
        await db.delete(l)
    inv.is_active = False
    await db.commit()
    return {"ok": True, "hours_released_lines": released}


# ── leakage & revenue ─────────────────────────────────────────────────────────
@router.get("/leakage")
async def leakage(request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    await ensure_schema(db)
    org = _org(request)
    p = request.query_params
    params = {"billable": "1", "uninvoiced": "1"}
    for k in ("from", "to"):
        if p.get(k):
            params[k] = p[k]
    lines = await _priced_lines(request, db, org, params)
    invs = (await db.execute(select(Invoice).where(Invoice.org_id == org, Invoice.is_active.is_(True)))).scalars().all()
    inv_rows = [await _inv_out(db, org, i) for i in invs]
    out = leakage_summary(lines, inv_rows)
    out["lines"] = lines
    return out


@router.get("/revenue")
async def revenue(request: Request, group: str = "client", db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    await ensure_schema(db)
    org = _org(request)
    if group not in ("client", "project", "month"):
        raise HTTPException(400, "group must be client, project or month")
    p = request.query_params
    d_from, d_to = _date(p.get("from"), "from", required=False), _date(p.get("to"), "to", required=False)
    st = await _psa_settings(org)
    rates = await _fx(db, org)
    invs = (await db.execute(select(Invoice).where(Invoice.org_id == org, Invoice.is_active.is_(True)))).scalars().all()
    inv_map = {}
    for i in invs:
        if d_from and i.issue_date and i.issue_date < d_from:
            continue
        if d_to and i.issue_date and i.issue_date > d_to:
            continue
        inv_map[str(i.id)] = await _inv_out(db, org, i)
    lines = (await db.execute(select(InvoiceLine).where(InvoiceLine.org_id == org))).scalars().all()
    rows = revenue_rows([_ser(l) for l in lines if str(l.invoice_id) in inv_map], inv_map, st["home_currency"], rates, group)
    return {"group": group, "home_currency": st["home_currency"], "rows": rows,
            "total": r2(sum(r["amount"] for r in rows)), "paid": r2(sum(r["paid"] for r in rows)),
            "unconverted_lines": sum(r["unconverted_lines"] for r in rows)}


# ── FX ────────────────────────────────────────────────────────────────────────
class FxIn(BaseModel):
    currency: str
    rate_to_home: float
    as_of: str
    source: str | None = None
    note: str | None = None


@router.get("/fx-rates")
async def list_fx(request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, OFFICE_ROLES | {"manager"}, "Office or manager role required")
    org = _org(request)
    rows = (await db.execute(select(FxRate).where(FxRate.org_id == org).order_by(FxRate.currency, FxRate.as_of.desc()))).scalars().all()
    st = await _psa_settings(org)
    return {"home_currency": st["home_currency"], "rows": [_ser(r) for r in rows]}


@router.post("/fx-rates", status_code=201)
async def add_fx(body: FxIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, FINANCE_ROLES | {"hr_admin"}, "Finance, payroll or admin role required")
    org = _org(request)
    cur = (body.currency or "").strip().upper()
    if len(cur) != 3:
        raise HTTPException(400, "Currency must be a 3-letter code, for example USD")
    if float(body.rate_to_home) <= 0:
        raise HTTPException(400, "Rate must be more than 0")
    row = FxRate(org_id=org, currency=cur, rate_to_home=round(float(body.rate_to_home), 6),
                 as_of=_date(body.as_of, "As of"), source=(body.source or "")[:200] or None, note=body.note)
    db.add(row); await db.commit(); await db.refresh(row)
    return _ser(row)


@router.delete("/fx-rates/{fid}")
async def delete_fx(fid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, FINANCE_ROLES | {"hr_admin"}, "Finance, payroll or admin role required")
    org = _org(request)
    row = (await db.execute(select(FxRate).where(FxRate.id == _uuid(fid), FxRate.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Rate not found")
    await db.delete(row); await db.commit()
    return {"ok": True}


_ = fx_convert   # re-exported for tests / callers
