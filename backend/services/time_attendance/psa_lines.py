"""Stream G2 — PSA view of timesheet lines (billable hours, rate snapshot, invoiced flag).

The existing self-entry timesheet line already carries `project_id` + `billable`. This
module ADDS (never changes) the columns project services need and serves them:

    ta_timesheet_lines.bill_rate / bill_unit / bill_currency / rate_card_id   the rate SNAPSHOT
    ta_timesheet_lines.invoice_line_id / invoiced_at                           which invoice took it

GET  /time/psa/lines?from=&to=&status=approved,locked&employee_id=&project_id=&billable=1&uninvoiced=1
GET  /time/psa/summary?from=&to=            hours per employee (billable / non-billable / total)
POST /time/psa/lines/snapshot {lines:[{id, bill_rate, bill_unit, bill_currency, rate_card_id}], overwrite}
POST /time/psa/lines/invoiced   {line_ids, invoice_line_id}
POST /time/psa/lines/uninvoiced {invoice_line_id}

Only APPROVED or LOCKED timesheets count as billable work; a draft line is shown but
never invoiced. A line that is already on an invoice keeps its snapshot for ever.
"""
import sys
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from database import get_db  # noqa: E402

router = APIRouter(prefix="/time/psa", tags=["time-psa"])

OFFICE_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}
BILLABLE_STATUSES = ("approved", "locked")

_COLUMNS = [
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS bill_rate NUMERIC(10,2)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS bill_unit VARCHAR(6)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS bill_currency VARCHAR(3)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS rate_card_id VARCHAR(64)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS invoice_line_id VARCHAR(64)",
    "ALTER TABLE ta_timesheet_lines ADD COLUMN IF NOT EXISTS invoiced_at TIMESTAMPTZ",
]
_ready = False


async def ensure_schema(db: AsyncSession) -> None:
    """Idempotent ADD COLUMN IF NOT EXISTS — runs once per process, on first use."""
    global _ready
    if _ready:
        return
    try:
        for ddl in _COLUMNS:
            await db.execute(text(ddl))
        await db.commit()
        _ready = True
    except Exception:  # noqa: BLE001 — table not created yet on a cold start
        await db.rollback()


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _me(request: Request) -> str | None:
    return request.headers.get("x-employee-id") or None


def _d(v: str | None, what: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise HTTPException(400, f"{what} must be YYYY-MM-DD")


def _row(r) -> dict:
    return {
        "id": str(r["id"]), "timesheet_id": str(r["timesheet_id"]), "employee_id": str(r["employee_id"]),
        "employee_name": r["employee_name"], "employee_number": r["employee_number"],
        "work_date": r["work_date"].isoformat() if r["work_date"] else None,
        "project_id": r["project_id"], "project_name": r["project_name"],
        "hours": float(r["hours"] or 0), "billable": bool(r["billable"]), "cost_center": r["cost_center"],
        "note": r["note"], "status": r["status"],
        "period_start": r["period_start"].isoformat() if r["period_start"] else None,
        "period_end": r["period_end"].isoformat() if r["period_end"] else None,
        "bill_rate": float(r["bill_rate"]) if r["bill_rate"] is not None else None,
        "bill_unit": r["bill_unit"], "bill_currency": r["bill_currency"], "rate_card_id": r["rate_card_id"],
        "invoice_line_id": r["invoice_line_id"],
        "invoiced_at": r["invoiced_at"].isoformat() if r["invoiced_at"] else None,
    }


_SELECT = """
SELECT l.id, l.timesheet_id, t.employee_id, t.employee_name, t.employee_number, l.work_date,
       l.project_id, l.project_name, l.hours, l.billable, l.cost_center, l.note, t.status,
       t.period_start, t.period_end, l.bill_rate, l.bill_unit, l.bill_currency, l.rate_card_id,
       l.invoice_line_id, l.invoiced_at, t.manager_id
FROM ta_timesheet_lines l JOIN ta_timesheets t ON t.id = l.timesheet_id
WHERE l.org_id = CAST(:org AS uuid) AND COALESCE(l.hours, 0) > 0
"""


@router.get("/lines")
async def lines(request: Request, db: AsyncSession = Depends(get_db)):
    await ensure_schema(db)
    org = _org(request)
    p = request.query_params
    d_from, d_to = _d(p.get("from"), "from"), _d(p.get("to"), "to")
    statuses = [s.strip() for s in (p.get("status") or ",".join(BILLABLE_STATUSES)).split(",") if s.strip()]
    sql, args = _SELECT, {"org": org}
    if d_from:
        sql += " AND l.work_date >= :d_from"; args["d_from"] = d_from
    if d_to:
        sql += " AND l.work_date <= :d_to"; args["d_to"] = d_to
    if statuses and "all" not in statuses:
        sql += " AND t.status = ANY(:statuses)"; args["statuses"] = statuses
    if p.get("employee_id"):
        sql += " AND t.employee_id = CAST(:emp AS uuid)"; args["emp"] = p["employee_id"]
    if p.get("project_id"):
        sql += " AND l.project_id = :proj"; args["proj"] = p["project_id"]
    if p.get("billable") in ("1", "true"):
        sql += " AND l.billable IS TRUE"
    elif p.get("billable") in ("0", "false"):
        sql += " AND l.billable IS NOT TRUE"
    if p.get("uninvoiced") in ("1", "true"):
        sql += " AND l.invoice_line_id IS NULL"
    role, me = _role(request), _me(request)
    if role not in OFFICE_ROLES:
        if role == "manager" and me:
            sql += " AND (t.manager_id = CAST(:me AS uuid) OR t.employee_id = CAST(:me AS uuid))"; args["me"] = me
        elif me:
            sql += " AND t.employee_id = CAST(:me AS uuid)"; args["me"] = me
        else:
            return []
    sql += " ORDER BY l.work_date, t.employee_name"
    rows = (await db.execute(text(sql), args)).mappings().all()
    return [_row(r) for r in rows]


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Approved/locked hours per employee in a date range: billable, non-billable, total."""
    await ensure_schema(db)
    org = _org(request)
    p = request.query_params
    d_from, d_to = _d(p.get("from"), "from"), _d(p.get("to"), "to")
    if not d_from or not d_to:
        raise HTTPException(400, "from and to are required")
    if _role(request) not in OFFICE_ROLES and _role(request) != "manager":
        raise HTTPException(403, "Office or manager role required")
    rows = (await db.execute(text(
        "SELECT t.employee_id, t.employee_name, t.employee_number, "
        "SUM(CASE WHEN l.billable IS TRUE THEN l.hours ELSE 0 END) AS billable, "
        "SUM(CASE WHEN l.billable IS TRUE THEN 0 ELSE l.hours END) AS non_billable, SUM(l.hours) AS total, "
        "SUM(CASE WHEN l.billable IS TRUE AND l.invoice_line_id IS NULL THEN l.hours ELSE 0 END) AS uninvoiced "
        "FROM ta_timesheet_lines l JOIN ta_timesheets t ON t.id = l.timesheet_id "
        "WHERE l.org_id = CAST(:org AS uuid) AND t.status = ANY(:st) AND l.work_date >= :a AND l.work_date <= :b "
        "AND COALESCE(l.hours, 0) > 0 GROUP BY t.employee_id, t.employee_name, t.employee_number"),
        {"org": org, "st": list(BILLABLE_STATUSES), "a": d_from, "b": d_to})).mappings().all()
    return [{"employee_id": str(r["employee_id"]), "employee_name": r["employee_name"],
             "employee_number": r["employee_number"], "billable": float(r["billable"] or 0),
             "non_billable": float(r["non_billable"] or 0), "total": float(r["total"] or 0),
             "uninvoiced": float(r["uninvoiced"] or 0)} for r in rows]


class SnapshotLine(BaseModel):
    id: str
    bill_rate: float
    bill_unit: str = "hour"
    bill_currency: str = ""
    rate_card_id: str | None = None


class SnapshotIn(BaseModel):
    lines: list[SnapshotLine]
    overwrite: bool = False


@router.post("/lines/snapshot")
async def snapshot(body: SnapshotIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Stamp the bill rate on lines. Never touches a line that is already invoiced; without
    `overwrite` it never changes a rate that was stamped earlier (that is the snapshot)."""
    await ensure_schema(db)
    org = _org(request)
    if _role(request) not in OFFICE_ROLES:
        raise HTTPException(403, "Office role required")
    n = 0
    for ln in body.lines:
        if ln.bill_unit not in ("hour", "day"):
            raise HTTPException(400, "bill_unit must be hour or day")
        sql = ("UPDATE ta_timesheet_lines SET bill_rate = :r, bill_unit = :u, bill_currency = :c, rate_card_id = :rc "
               "WHERE id = CAST(:id AS uuid) AND org_id = CAST(:org AS uuid) AND invoice_line_id IS NULL")
        if not body.overwrite:
            sql += " AND bill_rate IS NULL"
        res = await db.execute(text(sql), {"r": round(float(ln.bill_rate), 2), "u": ln.bill_unit,
                                           "c": (ln.bill_currency or "")[:3].upper() or None,
                                           "rc": ln.rate_card_id, "id": ln.id, "org": org})
        n += res.rowcount or 0
    await db.commit()
    return {"stamped": n}


class InvoicedIn(BaseModel):
    line_ids: list[str]
    invoice_line_id: str


@router.post("/lines/invoiced")
async def mark_invoiced(body: InvoicedIn, request: Request, db: AsyncSession = Depends(get_db)):
    await ensure_schema(db)
    org = _org(request)
    if _role(request) not in OFFICE_ROLES:
        raise HTTPException(403, "Office role required")
    if not body.line_ids:
        return {"marked": 0}
    try:
        ids = [uuid.UUID(str(x)) for x in body.line_ids]
    except ValueError:
        raise HTTPException(400, "line_ids must be ids")
    res = await db.execute(text(
        "UPDATE ta_timesheet_lines l SET invoice_line_id = :inv, invoiced_at = :now "
        "FROM ta_timesheets t WHERE t.id = l.timesheet_id AND l.org_id = CAST(:org AS uuid) "
        "AND l.id = ANY(CAST(:ids AS uuid[])) AND l.invoice_line_id IS NULL AND t.status = ANY(:st)"),
        {"inv": body.invoice_line_id, "now": datetime.now(timezone.utc), "org": org,
         "ids": ids, "st": list(BILLABLE_STATUSES)})
    await db.commit()
    return {"marked": res.rowcount or 0}


class UninvoicedIn(BaseModel):
    invoice_line_id: str


@router.post("/lines/uninvoiced")
async def mark_uninvoiced(body: UninvoicedIn, request: Request, db: AsyncSession = Depends(get_db)):
    """A DRAFT invoice was discarded — the hours go back to the uninvoiced pool."""
    await ensure_schema(db)
    org = _org(request)
    if _role(request) not in OFFICE_ROLES:
        raise HTTPException(403, "Office role required")
    res = await db.execute(text(
        "UPDATE ta_timesheet_lines SET invoice_line_id = NULL, invoiced_at = NULL "
        "WHERE org_id = CAST(:org AS uuid) AND invoice_line_id = :inv"), {"org": org, "inv": body.invoice_line_id})
    await db.commit()
    return {"released": res.rowcount or 0}
