"""Reports — READ-ONLY queries over the tables other services own.

All services share one Postgres, so a report reads the handful of columns it needs
straight from the owning table. Rules:

* every query is filtered by the caller's ``org_id`` (from the gateway header);
* nothing here writes;
* each read runs in its own session and returns ``(rows, problem)`` — if a table is
  not there (module not installed yet) the report says so instead of failing;
* identifiers that must never leave their service (SIN, bank …) are not selected.
"""
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text

from shared.utils import platform_models as pm
from shared.utils.platform_models import ApprovalItem
from shared.utils.org_time import org_today

log = logging.getLogger("lexora.reports")


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


async def _read(sql: str, params: dict) -> tuple[list[dict], str | None]:
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(sql), params)).mappings().all()
        return [{k: _plain(v) for k, v in r.items()} for r in rows], None
    except Exception as e:                                   # noqa: BLE001
        msg = str(e).splitlines()[0][:200]
        log.info("report source not readable: %s", msg)
        return [], "This information is not available on this installation yet."


async def table_exists(name: str) -> bool:
    try:
        async with pm.session() as s:
            r = (await s.execute(text("SELECT to_regclass(CAST(:n AS text))"), {"n": name})).first()
        return bool(r and r[0])
    except Exception:                                        # noqa: BLE001
        return False


async def employees(org_id: str, branch_id: str | None = None) -> tuple[list[dict], str | None]:
    """Everyone the org ever had on record (leavers included — the reports need them)."""
    sql = ("SELECT id, employee_number, first_name, last_name, department, work_location, job_title, "
           "employment_type, status, hire_date, termination_date, probation_end_date, date_of_birth, "
           "manager_id, manager_name, branch_id, created_at FROM employees "
           "WHERE org_id = CAST(:org AS uuid)")
    params = {"org": org_id}
    if branch_id:
        sql += " AND CAST(branch_id AS text) = :branch"
        params["branch"] = branch_id
    return await _read(sql, params)


async def attendance_days(org_id: str, start: date, end: date) -> tuple[list[dict], str | None]:
    return await _read(
        "SELECT employee_id, work_date, planned_hours, actual_hours, late_minutes, status FROM ta_attendance_days "
        "WHERE org_id = CAST(:org AS uuid) AND work_date BETWEEN :s AND :e", {"org": org_id, "s": start, "e": end})


async def leave_balances(org_id: str, fiscal_year: int | None) -> tuple[list[dict], str | None]:
    if fiscal_year is None:
        yr, _ = await _read("SELECT MAX(fiscal_year) AS y FROM leave_balances WHERE org_id = CAST(:org AS uuid)",
                            {"org": org_id})
        fiscal_year = yr[0]["y"] if yr and yr[0]["y"] is not None else (await org_today(org_id)).year
    return await _read(
        "SELECT employee_id, fiscal_year, leave_type_code, leave_type_name, allocated, carried_over, used, pending "
        "FROM leave_balances WHERE org_id = CAST(:org AS uuid) AND fiscal_year = :fy", {"org": org_id, "fy": fiscal_year})


async def leave_requests(org_id: str, start: date, end: date) -> tuple[list[dict], str | None]:
    """Requests that overlap the window."""
    return await _read(
        "SELECT employee_id, employee_name, leave_type_code, leave_type_name, start_date, end_date, days, status "
        "FROM leave_requests WHERE org_id = CAST(:org AS uuid) AND start_date <= :e AND end_date >= :s",
        {"org": org_id, "s": start, "e": end})


async def timesheets(org_id: str, start: date, end: date) -> tuple[list[dict], str | None]:
    return await _read(
        "SELECT employee_id, period_start, period_end, reg_hours, ot_hours, ot2_hours, status FROM ta_timesheets "
        "WHERE org_id = CAST(:org AS uuid) AND period_end BETWEEN :s AND :e", {"org": org_id, "s": start, "e": end})


async def pay_stubs(org_id: str, start: date, end: date) -> tuple[list[dict], str | None]:
    """Pay stubs with their run's pay date and status (the calc decides which runs count)."""
    return await _read(
        "SELECT s.employee_id, s.pay_run_id, s.gross, s.cpp, s.cpp2, s.ei, s.income_tax, s.net_pay, s.detail, "
        "r.pay_date, r.status AS run_status FROM pay_stubs s JOIN pay_runs r ON r.id = s.pay_run_id "
        "WHERE s.org_id = CAST(:org AS uuid) AND r.org_id = CAST(:org AS uuid) AND r.pay_date BETWEEN :s AND :e",
        {"org": org_id, "s": start, "e": end})


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


async def state_rows(table: str, state_col: str, date_col: str | None, org_id: str,
                     start: date, end: date) -> tuple[list[dict], str | None]:
    """The state of every row of one lifecycle table for the org (created inside the window
    when the table has a creation date). Identifiers come from a fixed list in the router."""
    if not (_IDENT.match(table) and _IDENT.match(state_col) and (date_col is None or _IDENT.match(date_col))):
        return [], "This information is not available on this installation yet."
    sql = f"SELECT CAST({state_col} AS text) AS state FROM {table} WHERE CAST(org_id AS text) = :org"
    params: dict = {"org": org_id}
    if date_col:
        sql += f" AND CAST({date_col} AS date) BETWEEN :s AND :e"
        params.update({"s": start, "e": end})
    return await _read(sql, params)


async def approval_items(org_id: str) -> list[dict]:
    async with pm.session() as s:
        rows = (await s.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == org_id, ApprovalItem.status == "pending"))).scalars().all()
    return [{"kind": i.kind, "status": i.status, "opened_at": pm.aware(i.opened_at)} for i in rows]


def now() -> datetime:
    return pm.now_utc()
