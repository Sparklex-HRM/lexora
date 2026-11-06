"""Reports + executive dashboard — `/reports` (served by the approvals service).

* `GET /reports/catalog`      what exists, what the caller may open, what is still to add
* `GET /reports/dashboard`    KPI tiles + chart series, each with an "as of" time
* `GET /reports/{key}`        one report: columns, rows, totals, formula, notes

Scope: org admin / HR / payroll see the whole company (or the branch they are viewing);
a manager sees their direct reports; nobody else can open reports. Payroll cost,
statutory liability (CRA remittance) is for org admin / payroll / finance only.
A report with nothing to show returns empty rows — never a made-up number.
"""
import sys
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request

sys.path.append("/app")
from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import directory  # noqa: E402
from shared.utils.roles import request_role, role_holds  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_time import to_local_date  # noqa: E402

import inbox as ib  # noqa: E402
import reports_calc as rc  # noqa: E402
import reports_data as rd  # noqa: E402

router = APIRouter(prefix="/reports", tags=["reports"])

PEOPLE, PAY, OFFICE = "people", "pay", "office"

CATALOG = [
    # key, title, group, access, description, params
    ("headcount", "Headcount", "People", PEOPLE, "People on the rolls by department, location, grade or type.", ["as_of", "by"]),
    ("headcount_trend", "Headcount trend", "People", PEOPLE, "Headcount at each month end.", ["from", "to"]),
    ("joiners_leavers", "Joiners and leavers", "People", PEOPLE, "Who joined and who left, month by month.", ["from", "to"]),
    ("attrition", "Attrition", "People", PEOPLE, "Attrition % with the formula shown.", ["from", "to"]),
    ("probation_due", "Probation due", "People", PEOPLE, "Probation periods ending soon or overdue.", []),
    ("birthdays_anniversaries", "Birthdays and anniversaries", "People", PEOPLE, "The next 30 days.", []),
    ("attendance_summary", "Attendance summary", "Time and leave", PEOPLE, "Days worked, late, absent and hours per person.", ["from", "to"]),
    ("overtime", "Overtime", "Time and leave", PEOPLE, "Overtime hours from approved timesheets.", ["from", "to"]),
    ("leave_balances", "Leave balances", "Time and leave", PEOPLE, "Entitled, used and remaining days per person.", ["year"]),
    ("leave_usage", "Leave usage", "Time and leave", PEOPLE, "Approved leave days by type and month.", ["from", "to"]),
    ("payroll_cost", "Payroll cost", "Payroll", PAY, "Gross pay and employer cost by month or department, finalised runs only.", ["from", "to", "by"]),
    ("statutory_liability", "Statutory liability", "Payroll", PAY, "CPP, EI and income tax to remit to the CRA per pay month.", ["from", "to"]),
    ("approvals_ageing", "Pending approvals ageing", "Workflow", OFFICE, "How long requests have been waiting, by type.", []),
]
# Reports over the lifecycle modules (exit, hiring, performance, career, revisions).
# Each is a plain count by state. One appears in the catalog only when its first table
# exists in the database; until then it is listed under "To add" — never faked.
# key: (title, group, access, description, [(what, table, state column, date column | None)])
GENERIC = {
    "exit_cases": ("Exit cases", "People", OFFICE, "Exit cases by state and type, and clearance lines.",
                   [("Exit cases by state", "exit_cases", "status", "created_at"),
                    ("Exit cases by type", "exit_cases", "exit_type", "created_at"),
                    ("Clearance lines (all dates)", "exit_clearance_items", "status", None)]),
    "hiring_pipeline": ("Hiring pipeline", "Talent", OFFICE, "Requisitions, applications and offers by state.",
                        [("Requisitions", "hire_requisitions", "status", "created_at"),
                         ("Applications by stage", "hire_applications", "stage", "created_at"),
                         ("Offers", "hire_offers", "status", "created_at")]),
    "performance_cycle": ("Performance and goals", "Talent", OFFICE, "Reviews by stage and goals by state.",
                          [("Reviews by stage", "perf_reviews", "stage", "created_at"),
                           ("Goals", "perf_goals", "status", "created_at")]),
    "career_changes": ("Career changes and confirmations", "People", OFFICE,
                       "Promotions, transfers and probation actions by type and state.",
                       [("Career changes by type", "career_changes", "change_type", "created_at"),
                        ("Career changes by state", "career_changes", "status", "created_at"),
                        ("Probation actions", "probation_actions", "action", "created_at")]),
    "revision_requests": ("Salary revision requests", "Payroll", PAY, "Revision requests by state. Counts only.",
                          [("Revision requests", "comp_revisions", "status", "created_at")]),
}
PAY_ROLES = ["org_admin", "payroll", "finance"]
OFFICE_ROLES = ["org_admin", "hr", "payroll"]


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    # org RBAC 2026-10-26: carries the acting custom role's data classes (can_see_pay asks for data.pay)
    return request_role(request)


def can_see_pay(role: str) -> bool:
    # The accountant is in the "finance" family but has no Reports screen of their own (pay data is
    # per-person). WASA 2026-10-21.
    if (role or "").strip().lower() == "accountant":
        return False
    if not role_holds(role, "pay"):       # a custom role acting as payroll / org office without data.pay
        return False
    return role == "superadmin" or directory.role_in(role, PAY_ROLES)


def is_office(role: str) -> bool:
    return role == "superadmin" or directory.role_in(role, OFFICE_ROLES)


def may_open(access: str, role: str) -> bool:
    if access == PAY:
        return can_see_pay(role)
    if access == OFFICE:
        return is_office(role)
    return is_office(role) or role == "manager"


def scope_employees(rows: list[dict], role: str, my_employee_id: str | None) -> tuple[list[dict], str]:
    """Whole company for the office roles; direct reports only for a manager."""
    if is_office(role):
        return rows, "Whole company"
    if role == "manager":
        mine = [e for e in rows if my_employee_id and str(e.get("manager_id") or "") == my_employee_id]
        return mine, "Your direct reports"
    return [], "Nothing"


def _date(v: str | None, default: date) -> date:
    if not v:
        return default
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise HTTPException(422, "Dates must look like 2026-10-12")


def _window(frm: str | None, to: str | None, today: date) -> tuple[date, date]:
    """Default window: the last 12 months, ending today."""
    end = _date(to, today)
    start = _date(frm, rc.month_start(rc.add_months(end, -11)))
    if start > end:
        raise HTTPException(422, "The from date is after the to date")
    if (end - start).days > 366 * 3:
        raise HTTPException(422, "Pick a period of at most three years")
    return start, end


async def _people(request: Request):
    org, role = _org(request), _role(request)
    rows, problem = await rd.employees(org, request.headers.get("x-branch-id") or None)
    scoped, scope = scope_employees(rows, role, request.headers.get("x-employee-id") or None)
    if request.headers.get("x-branch-id") and is_office(role):
        scope = "The branch you are viewing"
    return scoped, scope, problem


@router.get("/catalog")
async def catalog(request: Request):
    _org(request)
    role = _role(request)
    reports = [{"key": k, "title": t, "group": g, "description": d, "params": p}
               for k, t, g, access, d, p in CATALOG if may_open(access, role)]
    to_add = []
    for k, (t, g, access, d, sets) in GENERIC.items():
        if await rd.table_exists(sets[0][1]):
            if may_open(access, role):
                reports.append({"key": k, "title": t, "group": g, "description": d, "params": ["from", "to"]})
        else:
            to_add.append({"key": k, "title": t, "group": g,
                           "reason": "That module's records are not in this database yet."})
    return {"reports": reports, "to_add": to_add,
            "scope": "Whole company" if is_office(role) else "Your direct reports" if role == "manager" else "None"}


# ══════════════════════════ dashboard ══════════════════════════════════════════

def tile(key, label, value, unit="number", sub="", empty="No data yet"):
    return {"key": key, "label": label, "value": value, "unit": unit, "sub": sub,
            "empty": empty if value is None else ""}


@router.get("/dashboard")
async def dashboard(request: Request):
    org, role = _org(request), _role(request)
    if not may_open(PEOPLE, role):
        raise HTTPException(403, "The dashboard is for managers, HR, payroll and org admins")
    now = rd.now()
    today = to_local_date(now)
    emps, scope, problem = await _people(request)
    ids = {str(e["id"]) for e in emps}
    year_start, month_start = rc.month_start(rc.add_months(today, -11)), rc.month_start(today)
    tiles, charts = [], []

    has_people = bool(emps)
    head = rc.headcount_on(emps, today) if has_people else None
    tiles.append(tile("headcount", "Headcount", head, sub="On the rolls today",
                      empty=problem or "No employees yet"))
    tiles.append(tile("joiners", "Joiners this month",
                      sum(1 for e in emps if rc.joined_in(e, month_start, today)) if has_people else None))
    tiles.append(tile("leavers", "Leavers this month",
                      sum(1 for e in emps if rc.left_in(e, month_start, today)) if has_people else None))
    att = rc.attrition_figures(emps, year_start, today) if has_people else None
    tiles.append(tile("attrition", "Attrition, last 12 months", att["attrition_pct"] if att else None, "percent",
                      sub=f"{att['leavers']} leaver(s) ÷ average headcount {att['average']:g}" if att and att["attrition_pct"] is not None else "",
                      empty="No data yet"))
    soon = rc.probation_due(emps, today, ahead_days=30, behind_days=30)["rows"] if has_people else []
    tiles.append(tile("probation", "Probation ending within 30 days", len([r for r in soon if r["days_left"] >= 0]) if has_people else None,
                      sub=f"{len([r for r in soon if r['days_left'] < 0])} overdue" if soon else ""))

    reqs, _ = await rd.leave_requests(org, today, today)
    on_leave = [r for r in reqs if r["status"] == "approved" and str(r["employee_id"]) in ids]
    tiles.append(tile("on_leave", "On approved leave today", len(on_leave) if has_people else None))

    days, _ = await rd.attendance_days(org, today, today)
    days = [d for d in days if str(d["employee_id"]) in ids]
    worked = sum(1 for d in days if d["status"] in ("present", "late", "on_shift"))
    absent = sum(1 for d in days if d["status"] == "absent")
    tiles.append(tile("present", "Present today", worked if days else None,
                      sub=f"{absent} marked absent" if days else "", empty="No attendance recorded today"))

    if is_office(role):
        await ib.sync_builtin(org)
        pending = await rd.approval_items(org)
        oldest = max((ib.age_days(i["opened_at"], now) for i in pending), default=0)
        tiles.append(tile("approvals", "Approvals waiting, whole company", len(pending),
                          sub=f"Oldest has waited {oldest} day(s)" if pending else "Nothing is waiting"))

    if has_people:
        charts.append({"key": "headcount_trend", "title": "Headcount, last 12 months",
                       **_chart(rc.headcount_trend(emps, year_start, today))})
        charts.append({"key": "joiners_leavers", "title": "Joiners and leavers, last 12 months",
                       **_chart(rc.joiners_leavers(emps, year_start, today))})
    lreqs, _ = await rd.leave_requests(org, year_start, today)
    usage = rc.leave_usage([r for r in lreqs if str(r["employee_id"]) in ids], year_start, today)
    if usage["rows"]:
        charts.append({"key": "leave_usage", "title": "Approved leave days, last 12 months",
                       "type": "bar", "x": "month", "series": usage["chart"]["series"], "data": usage["chart"]["data"]})

    if can_see_pay(role):
        stubs, pay_problem = await rd.pay_stubs(org, year_start, today)
        by_emp = {str(e["id"]): e for e in emps}
        cost = rc.payroll_cost(stubs, by_emp, "month")
        last = cost["rows"][-1] if cost["rows"] else None
        tiles.append(tile("payroll_cost", "Payroll cost, latest finalised month", last["total_cost"] if last else None,
                          "money", sub=f"{last['group']} · {last['employees_paid']} paid" if last else "",
                          empty=pay_problem or "No finalised pay run yet"))
        if cost["rows"]:
            charts.append({"key": "payroll_cost", "title": "Payroll cost by month (finalised runs)", **_chart(cost)})

    return {"as_of": pm.iso(now), "scope": scope, "tiles": tiles, "charts": charts,
            "shows_pay": can_see_pay(role)}


async def _generic_report(key: str, org: str, role: str, q) -> dict:
    title, group, access, description, sets = GENERIC[key]
    if not may_open(access, role):
        raise HTTPException(403, "Your role cannot open this report")
    now = rd.now()
    start, end = _window(q.get("from"), q.get("to"), to_local_date(now))
    problems, data = [], []
    for what, table, state_col, date_col in sets:
        rows, problem = await rd.state_rows(table, state_col, date_col, org, start, end)
        if problem:
            problems.append(f"{what}: {problem}")
        data.append((what, rows))
    out = rc.state_counts(data)
    return {"key": key, "title": title, "group": group, "description": description, "as_of": pm.iso(now),
            "scope": "Whole company", "period": f"Created {start.isoformat()} to {end.isoformat()}", **out,
            "problems": problems,
            "empty": "" if out["rows"] else (problems[0] if problems else "No data yet for this period.")}


def _chart(report: dict) -> dict:
    c = report.get("chart") or {}
    return {"type": c.get("type", "bar"), "x": c.get("x"), "series": c.get("series", []),
            "data": c.get("data") or report["rows"]}


# ══════════════════════════ one report ═════════════════════════════════════════

@router.get("/{key}")
async def run_report(key: str, request: Request, as_of: str | None = None, by: str | None = None,
                     year: int | None = None):
    org, role = _org(request), _role(request)
    q = request.query_params
    if key in GENERIC:
        return await _generic_report(key, org, role, q)
    entry = next((c for c in CATALOG if c[0] == key), None)
    if not entry:
        raise HTTPException(404, "Unknown report")
    _, title, group, access, description, params = entry
    if not may_open(access, role):
        raise HTTPException(403, "Your role cannot open this report")
    now = rd.now()
    today = to_local_date(now)
    meta = {"key": key, "title": title, "group": group, "description": description, "as_of": pm.iso(now)}
    problems: list[str] = []

    async def people():
        rows, scope, problem = await _people(request)
        if problem:
            problems.append(problem)
        meta["scope"] = scope
        return rows

    if key == "headcount":
        d = _date(as_of, today)
        out = rc.headcount_by(await people(), by or "department", d)
        meta["period"] = f"As of {d.isoformat()}"
        meta["options"] = {"by": [{"value": k, "label": v} for k, v in rc.HEADCOUNT_DIMS.items()]}
    elif key in ("headcount_trend", "joiners_leavers", "attrition"):
        start, end = _window(q.get("from"), q.get("to"), today)
        fn = {"headcount_trend": rc.headcount_trend, "joiners_leavers": rc.joiners_leavers, "attrition": rc.attrition}[key]
        out = fn(await people(), start, end)
        meta["period"] = f"{start.isoformat()} to {end.isoformat()}"
    elif key == "probation_due":
        out = rc.probation_due(await people(), today)
    elif key == "birthdays_anniversaries":
        out = rc.birthdays_anniversaries(await people(), today)
    elif key in ("attendance_summary", "overtime", "leave_usage"):
        default_start = rc.month_start(today) if key == "attendance_summary" else None
        start, end = _window(q.get("from") or (default_start.isoformat() if default_start else None), q.get("to"), today)
        emps = await people()
        by_id = {str(e["id"]): e for e in emps}
        fetch = {"attendance_summary": rd.attendance_days, "overtime": rd.timesheets, "leave_usage": rd.leave_requests}[key]
        rows, problem = await fetch(org, start, end)
        if problem:
            problems.append(problem)
        rows = [r for r in rows if str(r["employee_id"]) in by_id]
        out = (rc.attendance_summary(rows, by_id) if key == "attendance_summary"
               else rc.overtime(rows, by_id) if key == "overtime" else rc.leave_usage(rows, start, end))
        meta["period"] = f"{start.isoformat()} to {end.isoformat()}"
    elif key == "leave_balances":
        emps = await people()
        by_id = {str(e["id"]): e for e in emps if rc.employed_on(e, today)}
        rows, problem = await rd.leave_balances(org, year)
        if problem:
            problems.append(problem)
        out = rc.leave_balances([r for r in rows if str(r["employee_id"]) in by_id], by_id)
    elif key in ("payroll_cost", "statutory_liability"):
        start, end = _window(q.get("from"), q.get("to"), today)
        emps, _problem = await rd.employees(org, request.headers.get("x-branch-id") or None)
        by_id = {str(e["id"]): e for e in emps}
        stubs, problem = await rd.pay_stubs(org, start, end)
        if problem:
            problems.append(problem)
        if request.headers.get("x-branch-id"):
            stubs = [s for s in stubs if str(s["employee_id"]) in by_id]
        if key == "payroll_cost":
            out = rc.payroll_cost(stubs, by_id, by or "month")
            meta["options"] = {"by": [{"value": "month", "label": "Month"}, {"value": "department", "label": "Department"}]}
        else:
            out = rc.statutory_liability(stubs)
        meta["period"] = f"{start.isoformat()} to {end.isoformat()}"
        meta["scope"] = "The branch you are viewing" if request.headers.get("x-branch-id") else "Whole company"
    else:   # approvals_ageing
        await ib.sync_builtin(org)
        out = rc.approvals_ageing(await rd.approval_items(org), now, ib.AGE_BUCKETS, ib.age_bucket, ac.kind_label)
        meta["scope"] = "Whole company"

    if not out["rows"]:
        out.pop("chart", None)
    return {**meta, **out, "problems": problems,
            "empty": "" if out["rows"] else (problems[0] if problems else "No data yet for this period.")}
