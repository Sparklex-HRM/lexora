"""The watchdog fleet — READ-ONLY SQL agents.

Each agent: what it checks in plain words, its default thresholds, who is told, its
cadence, and a ``run(org, ctx)`` that reads rows (SELECT only, org-scoped, through the
tiny shared pool) and hands them to the pure rules in ``wd_rules``. Nothing here
writes to any business table; the run log and findings are written by ``wd_runner``.

Thresholds come from company settings (``ai_watchdogs.thresholds[agent_key]``), falling
back to the defaults below. The OT agent also reads a G1 ``overtime`` settings group if
that group exists (guarded) and otherwise the time-attendance constants.
"""
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Awaitable, Callable

from sqlalchemy import text

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import wd_rules as R  # noqa: E402

log = logging.getLogger("lexora.watchdogs")


# ══════════════════════════ read helpers (SELECT only) ═════════════════════════

def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "hex") and not isinstance(v, (str, bytes)):   # uuid
        return str(v)
    return v


async def rows(sql: str, params: dict) -> list[dict]:
    """Run one SELECT. A missing table (module not installed) is an empty result, not a crash."""
    if not sql.lstrip().lower().startswith("select"):
        raise ValueError("watchdogs may only SELECT")
    try:
        async with pm.session() as s:
            res = (await s.execute(text(sql), params)).mappings().all()
        return [{k: _plain(v) for k, v in r.items()} for r in res]
    except Exception as e:                                   # noqa: BLE001
        log.info("watchdog read skipped: %s", str(e).splitlines()[0][:160])
        return []


async def table_exists(name: str) -> bool:
    try:
        async with pm.session() as s:
            r = (await s.execute(text("SELECT to_regclass(CAST(:n AS text))"), {"n": name})).first()
        return bool(r and r[0])
    except Exception:                                        # noqa: BLE001
        return False


async def org_country(org: str) -> str:
    c = await get_setting(org, "locale.country", "")
    return (c or "CA").upper()


def _nm(r: dict) -> str:
    return f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or (r.get("employee_number") or "")


ORG = "CAST(:org AS uuid)"


async def active_employees(org: str, extra_cols: str = "") -> list[dict]:
    cols = "id, employee_number, first_name, last_name, department, status, hire_date, termination_date, probation_end_date, manager_id, address_country, province_of_employment"
    if extra_cols:
        cols += ", " + extra_cols
    out = await rows(f"SELECT {cols} FROM employees WHERE org_id = {ORG} AND is_active IS TRUE", {"org": org})
    for e in out:
        e["name"] = _nm(e)
    return out


# ══════════════════════════ agent registry ═════════════════════════════════════

@dataclass
class Agent:
    key: str
    name: str
    domain: str
    checks: str                      # plain words: what it looks for
    cadence_minutes: int
    notify_roles: list[str]
    thresholds: dict = field(default_factory=dict)
    threshold_help: dict = field(default_factory=dict)
    countries: tuple = ()            # () = every country
    run: Callable[[str, dict], Awaitable[tuple[list[dict], int, str | None]]] | None = None


AGENTS: dict[str, Agent] = {}


def agent(**kw):
    def deco(fn):
        a = Agent(run=fn, **kw)
        AGENTS[a.key] = a
        return fn
    return deco


async def effective_thresholds(org: str, key: str, country: str) -> dict:
    a = AGENTS[key]
    th = dict(a.thresholds)
    if key == "ot_breach":
        th.update(_ot_defaults(country))
        g1 = await get_setting(org, "overtime", None)        # G1's group, if it exists on this install
        if isinstance(g1, dict):
            for src, dst in (("daily_max_hours", "daily_hours_max"), ("weekly_max_hours", "weekly_hours_max"),
                             ("quarter_ot_cap", "quarter_ot_hours_max"), ("daily_hours_max", "daily_hours_max"),
                             ("weekly_hours_max", "weekly_hours_max"), ("quarter_ot_hours_max", "quarter_ot_hours_max")):
                if g1.get(src) not in (None, ""):
                    th[dst] = g1[src]
    stored = await get_setting(org, "ai_watchdogs.thresholds", {}) or {}
    for k, v in (stored.get(key) or {}).items():
        if k in th or k in a.threshold_help:
            th[k] = v
    return th


def _ot_defaults(country: str) -> dict:
    """Default limits (the company's overtime settings win): 13 hours a day and 48 hours a week, the common Canadian
    employment-standards ceilings without an excess-hours agreement; no quarterly cap."""
    return {"daily_hours_max": 13, "weekly_hours_max": 48, "quarter_ot_hours_max": None}


# ── 1 payroll variance ───────────────────────────────────────────────────────

@agent(key="payroll_variance", name="Payroll variance", domain="Payroll",
       checks="Each employee's gross pay on the latest regular pay date against the previous one. Flags a change beyond the limit "
              "that no salary revision, approved leave, joining or leaving explains.",
       cadence_minutes=24 * 60, notify_roles=["payroll", "org_admin"],
       thresholds={"variance_pct": 15, "high_pct": 40},
       threshold_help={"variance_pct": "Flag when gross moves by more than this %", "high_pct": "Mark as high above this %"})
async def _payroll_variance(org: str, ctx: dict):
    dates = await rows(f"SELECT DISTINCT pay_date FROM pay_runs WHERE org_id = {ORG} AND status <> 'draft' "
                       "AND COALESCE(CAST(meta->>'off_cycle' AS text), 'false') <> 'true' ORDER BY pay_date DESC LIMIT 2", {"org": org})
    if len(dates) < 2:
        return [], len(dates), "Fewer than two finalised regular pay dates — nothing to compare yet."
    d1, d0 = dates[0]["pay_date"], dates[1]["pay_date"]
    sql = (f"SELECT s.employee_id, e.employee_number, e.first_name, e.last_name, MAX(r.pay_date) AS pay_date, "
           "SUM(s.gross) AS gross, SUM(s.net_pay) AS net "
           f"FROM pay_stubs s JOIN pay_runs r ON r.id = s.pay_run_id LEFT JOIN employees e ON e.id = s.employee_id "
           f"WHERE s.org_id = {ORG} AND r.org_id = {ORG} AND r.status <> 'draft' AND r.pay_date = :d "
           "AND COALESCE(CAST(r.meta->>'off_cycle' AS text), 'false') <> 'true' "
           "GROUP BY s.employee_id, e.employee_number, e.first_name, e.last_name")
    this, last = await rows(sql, {"org": org, "d": d1}), await rows(sql, {"org": org, "d": d0})
    for r in this + last:
        r["name"] = _nm(r)
    ids = [str(r["employee_id"]) for r in this]
    ex: dict[str, dict] = {}
    for e in await rows(f"SELECT id, hire_date, termination_date FROM employees WHERE org_id = {ORG}", {"org": org}):
        ex[str(e["id"])] = {"hire_date": e["hire_date"], "termination_date": e["termination_date"]}
    start, end = d0 - timedelta(days=31), d1
    for r in await rows(f"SELECT employee_id, effective_from FROM comp_revisions WHERE org_id = {ORG} AND status IN ('approved','applied') "
                        "AND effective_from BETWEEN :s AND :e", {"org": org, "s": start, "e": end}):
        ex.setdefault(str(r["employee_id"]), {})["revision"] = str(r["effective_from"])
    for r in await rows(f"SELECT employee_id, start_date, end_date FROM leave_requests WHERE org_id = {ORG} AND status = 'approved' "
                        "AND start_date <= :e AND end_date >= :s", {"org": org, "s": start, "e": end}):
        sd, ed = max(r["start_date"], start), min(r["end_date"], end)
        ex.setdefault(str(r["employee_id"]), {})["leave_days"] = ex.get(str(r["employee_id"]), {}).get("leave_days", 0) + max((ed - sd).days + 1, 0)
    th = ctx["thresholds"]
    return R.payroll_variance(this, last, {k: v for k, v in ex.items() if k in ids}, th), len(this) + len(last), None


# ── 2 CPP / EI (Canada) ──────────────────────────────────────────────────────

@agent(key="cpp_ei_check", name="CPP / EI check", domain="Compliance", countries=("CA",),
       checks="For the latest regular pay date: CPP / EI deducted from an employee marked exempt, and year-to-date CPP, CPP2 and "
              "EI above the year's employee maximum (Québec: EI at the Québec rate; QPP is not compared).",
       cadence_minutes=24 * 60, notify_roles=["payroll", "org_admin"],
       thresholds={"tolerance": 1, "cpp_max": 4230.45, "cpp2_max": 416.00, "ei_max": 1123.07, "ei_max_qc": 895.70},
       threshold_help={"tolerance": "Allowed difference in dollars before a line is flagged",
                       "cpp_max": "Maximum employee CPP for the year", "cpp2_max": "Maximum employee CPP2 for the year",
                       "ei_max": "Maximum employee EI premium (outside Québec)", "ei_max_qc": "Maximum employee EI premium (Québec)"})
async def _cpp_ei(org: str, ctx: dict):
    dates = await rows(f"SELECT DISTINCT pay_date FROM pay_runs WHERE org_id = {ORG} AND status <> 'draft' ORDER BY pay_date DESC LIMIT 1", {"org": org})
    if not dates:
        return [], 0, "No finalised pay run yet."
    d = dates[0]["pay_date"]
    stubs = await rows(
        f"SELECT s.employee_id, e.employee_number, e.first_name, e.last_name, r.pay_date, s.cpp, s.cpp2, s.ei, "
        "e.province_of_employment AS province, e.cpp_exempt, e.ei_exempt "
        f"FROM pay_stubs s JOIN pay_runs r ON r.id = s.pay_run_id LEFT JOIN employees e ON e.id = s.employee_id "
        f"WHERE s.org_id = {ORG} AND r.org_id = {ORG} AND r.status <> 'draft' AND r.pay_date = :d", {"org": org, "d": d})
    ytd = {str(y["employee_id"]): y for y in await rows(
        f"SELECT s.employee_id, SUM(s.cpp) AS ytd_cpp, SUM(s.cpp2) AS ytd_cpp2, SUM(s.ei) AS ytd_ei FROM pay_stubs s "
        f"JOIN pay_runs r ON r.id = s.pay_run_id WHERE s.org_id = {ORG} AND r.status <> 'draft' "
        "AND r.pay_date >= :y0 AND r.pay_date <= :d GROUP BY s.employee_id", {"org": org, "y0": date(d.year, 1, 1), "d": d})}
    for s in stubs:
        s["name"] = _nm(s)
        s.update({k: v for k, v in (ytd.get(str(s["employee_id"])) or {}).items() if k.startswith("ytd_")})
    return R.cpp_ei_check(stubs, ctx["thresholds"]), len(stubs), None


# ── 3 OT breach ──────────────────────────────────────────────────────────────

@agent(key="ot_breach", name="Overtime breach", domain="Time",
       checks="Worked hours per day and per week over the limits (and overtime in the quarter over a cap, when the company sets "
              "one). Limits come from the company's overtime settings when set, else 13 h a day / 48 h a week.",
       cadence_minutes=24 * 60, notify_roles=["hr", "org_admin"],
       thresholds={"window_days": 90},
       threshold_help={"daily_hours_max": "Hours in one day above which it is a breach", "weekly_hours_max": "Hours in one week above which it is a breach",
                       "quarter_ot_hours_max": "Overtime hours allowed per quarter (blank = not checked)", "window_days": "How many days back to look"})
async def _ot(org: str, ctx: dict):
    th = ctx["thresholds"]
    today = await org_today(org)
    since = today - timedelta(days=int(th.get("window_days", 90)))
    days = await rows(f"SELECT d.employee_id, e.first_name, e.last_name, e.employee_number, d.work_date, d.actual_hours FROM ta_attendance_days d "
                      f"LEFT JOIN employees e ON e.id = d.employee_id WHERE d.org_id = {ORG} AND d.work_date >= :s AND d.actual_hours > 0",
                      {"org": org, "s": since})
    q_start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    sheets = await rows(f"SELECT t.employee_id, t.employee_name AS name, t.period_start, t.period_end, t.ot_hours, t.ot2_hours FROM ta_timesheets t "
                        f"WHERE t.org_id = {ORG} AND t.period_end >= :q AND t.status IN ('approved','locked','submitted')", {"org": org, "q": q_start})
    for d in days:
        d["name"] = _nm(d)
    return R.ot_breach(days, sheets, th), len(days) + len(sheets), None


# ── 4 missing punches ────────────────────────────────────────────────────────

@agent(key="missing_punches", name="Missing / single punches", domain="Time",
       checks="Worked days in the last N days that have only one punch, or a clock-in without a clock-out.",
       cadence_minutes=12 * 60, notify_roles=["hr"],
       thresholds={"window_days": 14, "warn_after_days": 2},
       threshold_help={"window_days": "Days back to look", "warn_after_days": "Days with a problem before severity becomes warn"})
async def _punches(org: str, ctx: dict):
    th = ctx["thresholds"]
    today = await org_today(org)
    since = today - timedelta(days=int(th.get("window_days", 14)))
    days = await rows(f"SELECT d.employee_id, e.first_name, e.last_name, e.employee_number, d.work_date, d.status, d.first_in, d.last_out "
                      f"FROM ta_attendance_days d LEFT JOIN employees e ON e.id = d.employee_id WHERE d.org_id = {ORG} AND d.work_date >= :s",
                      {"org": org, "s": since})
    for d in days:
        d["name"] = _nm(d)
        for k in ("first_in", "last_out"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
    counts = {(str(p["employee_id"]), str(p["work_date"])): int(p["n"]) for p in await rows(
        f"SELECT employee_id, work_date, COUNT(*) AS n FROM ta_punches WHERE org_id = {ORG} AND work_date >= :s GROUP BY employee_id, work_date",
        {"org": org, "s": since})}
    return R.missing_punches(days, counts, th, today), len(days), None


# ── 5 leave without approval ─────────────────────────────────────────────────

@agent(key="leave_without_approval", name="Leave without approval", domain="Leave",
       checks="Leave requests whose first day has arrived but are still pending, and days marked absent with no approved leave.",
       cadence_minutes=12 * 60, notify_roles=["hr"],
       thresholds={"window_days": 14, "absent_days_min": 1},
       threshold_help={"window_days": "Days back to look for absences", "absent_days_min": "Absent days before a person is listed"})
async def _leave_unapproved(org: str, ctx: dict):
    th = ctx["thresholds"]
    today = await org_today(org)
    since = today - timedelta(days=int(th.get("window_days", 14)))
    pending = await rows(f"SELECT id, employee_id, employee_name, leave_type_name, start_date, end_date, days, manager_name FROM leave_requests "
                         f"WHERE org_id = {ORG} AND status = 'pending' AND start_date <= :t", {"org": org, "t": today})
    approved = await rows(f"SELECT employee_id, start_date, end_date FROM leave_requests WHERE org_id = {ORG} AND status = 'approved' AND end_date >= :s",
                          {"org": org, "s": since})
    absent = await rows(f"SELECT d.employee_id, e.first_name, e.last_name, e.employee_number, d.work_date FROM ta_attendance_days d "
                        f"LEFT JOIN employees e ON e.id = d.employee_id WHERE d.org_id = {ORG} AND d.status = 'absent' AND d.work_date BETWEEN :s AND :t",
                        {"org": org, "s": since, "t": today})
    for d in absent:
        d["name"] = _nm(d)
    return R.leave_without_approval(pending, absent, approved, today, th), len(pending) + len(absent), None


# ── 7 ROE required (Canada) ──────────────────────────────────────────────────

@agent(key="roe_required", name="ROE required", domain="Compliance", countries=("CA",),
       checks="Employees whose last day has passed in the window with no Record of Employment reason code on their exit case.",
       cadence_minutes=24 * 60, notify_roles=["payroll", "hr"],
       thresholds={"window_days": 45, "deadline_days": 5},
       threshold_help={"window_days": "Days back to look for leavers", "deadline_days": "Days after the last day before it is high"})
async def _roe(org: str, ctx: dict):
    th = ctx["thresholds"]
    today = await org_today(org)
    leavers = await rows(f"SELECT id, first_name, last_name, employee_number, termination_date FROM employees WHERE org_id = {ORG} "
                         "AND termination_date BETWEEN :s AND :t",
                         {"org": org, "s": today - timedelta(days=int(th.get("window_days", 45))), "t": today})
    for e in leavers:
        e["name"] = _nm(e)
    cases = {str(c["employee_id"]): c for c in await rows(
        f"SELECT employee_id, case_number, roe_reason_code FROM exit_cases WHERE org_id = {ORG}", {"org": org})}
    return R.roe_required(leavers, cases, today, th), len(leavers), None


# ── 8 document / permit / TD1 expiry ─────────────────────────────────────────

@agent(key="document_expiry", name="Document and permit expiry", domain="People",
       checks="Work permits / visas expiring soon or expired; Canadian employees with no TD1 claim. Licences are not stored on this "
              "installation and are not checked.",
       cadence_minutes=24 * 60, notify_roles=["hr"],
       thresholds={"days_ahead": 60}, threshold_help={"days_ahead": "Warn this many days before expiry"})
async def _docs(org: str, ctx: dict):
    emps = await active_employees(org, "work_permit_expiry, work_eligibility, td1_federal_claim")
    return R.document_expiry(emps, await org_today(org), ctx["thresholds"], ctx["country"]), len(emps), None


# ── 9 onboarding stuck ───────────────────────────────────────────────────────

@agent(key="onboarding_stuck", name="Onboarding stuck", domain="People",
       checks="Onboarding invitations still 'invited' or 'submitted' after N days.",
       cadence_minutes=24 * 60, notify_roles=["hr"],
       thresholds={"days": 7}, threshold_help={"days": "Days before an onboarding counts as stuck"})
async def _onb(org: str, ctx: dict):
    sessions = await rows(f"SELECT id, status, first_name, last_name, email, current_step, created_at FROM onboarding_sessions WHERE org_id = {ORG} "
                          "AND status IN ('invited','submitted')", {"org": org})
    return R.onboarding_stuck(sessions, pm.now_utc(), ctx["thresholds"]), len(sessions), None


# ── 10 probation ending with no action ──────────────────────────────────────

@agent(key="probation_no_action", name="Probation with no decision", domain="People",
       checks="Active employees whose probation ends within N days (or has ended) with no confirm / extend / do-not-confirm action.",
       cadence_minutes=24 * 60, notify_roles=["hr"],
       thresholds={"days_ahead": 7}, threshold_help={"days_ahead": "Days before the end date to start flagging"})
async def _prob(org: str, ctx: dict):
    emps = await active_employees(org)
    last: dict[str, dict] = {}
    for a in await rows(f"SELECT employee_id, action, created_at FROM probation_actions WHERE org_id = {ORG} ORDER BY created_at", {"org": org}):
        last[str(a["employee_id"])] = a
    return R.probation_no_action(emps, last, await org_today(org), ctx["thresholds"]), len(emps), None


# ── 11 pending approvals ageing ─────────────────────────────────────────────

@agent(key="approvals_ageing", name="Approvals ageing", domain="Workflow",
       checks="Approvals that have waited N days or more.",
       cadence_minutes=6 * 60, notify_roles=["org_admin", "hr"],
       thresholds={"days": 5}, threshold_help={"days": "Days waiting before it is flagged"})
async def _apr(org: str, ctx: dict):
    items = await rows("SELECT id, kind, title, requester_name, opened_at, reminders_sent FROM apr_items WHERE org_id = :org AND status = 'pending'", {"org": org})
    for i in items:
        i["kind_label"] = (i.get("kind") or "").replace("_", " ").capitalize()
        i["opened_at"] = pm.aware(i["opened_at"]) if isinstance(i.get("opened_at"), datetime) else i.get("opened_at")
    return R.approvals_ageing(items, pm.now_utc(), ctx["thresholds"]), len(items), None


# ── 12 exit past LWD ────────────────────────────────────────────────────────

@agent(key="exit_past_lwd", name="Exit past last working day", domain="People",
       checks="Exit cases still open after the last working day, and employees still active after their recorded termination date.",
       cadence_minutes=24 * 60, notify_roles=["hr", "payroll"],
       thresholds={}, threshold_help={})
async def _exit(org: str, ctx: dict):
    today = await org_today(org)
    cases = await rows(f"SELECT id, case_number, employee_id, employee_name, status, lwd, settlement_status FROM exit_cases WHERE org_id = {ORG} "
                       "AND lwd < :t AND status NOT IN ('closed','withdrawn','rejected')", {"org": org, "t": today})
    leavers = await rows(f"SELECT id, first_name, last_name, employee_number, status, termination_date FROM employees WHERE org_id = {ORG} "
                         "AND status = 'active' AND termination_date < :t", {"org": org, "t": today})
    for e in leavers:
        e["name"] = _nm(e)
    return R.exit_past_lwd(cases, leavers, today, ctx["thresholds"]), len(cases) + len(leavers), None


# ── 14 headcount vs budget (guarded) ────────────────────────────────────────

BUDGET_TABLES = ("hr_headcount_budget", "psa_headcount_budget", "psa_budgets", "headcount_budgets", "budget_headcount")


@agent(key="headcount_vs_budget", name="Headcount vs budget", domain="Cost",
       checks="Active headcount per department against a headcount budget table, when one exists on this installation.",
       cadence_minutes=24 * 60, notify_roles=["org_admin"], thresholds={}, threshold_help={})
async def _budget(org: str, ctx: dict):
    for t in BUDGET_TABLES:
        if await table_exists(t):
            budget = await rows(f"SELECT department, budget AS headcount_budget FROM {t} WHERE CAST(org_id AS text) = :org", {"org": org})
            if not budget:
                return [], 0, f"Budget table {t} has no rows for this company."
            heads = await rows(f"SELECT department, COUNT(*) AS n FROM employees WHERE org_id = {ORG} AND is_active IS TRUE AND status = 'active' GROUP BY department", {"org": org})
            by = {h["department"]: int(h["n"]) for h in heads}
            out = []
            for b in budget:
                n, lim = by.get(b["department"], 0), float(b["headcount_budget"] or 0)
                if lim and n > lim:
                    out.append(R.finding(severity="warn", title=f"{b['department']}: {n} on the rolls against a budget of {lim:g}",
                                         explanation=f"Rule: active headcount above the budgeted headcount for the department (source table {t}).",
                                         evidence=[R._ev(department=b["department"], headcount=n, budget=lim, over_by=n - lim)],
                                         fingerprint=f"budget:{b['department']}"))
            return out, len(heads) + len(budget), None
    return [], 0, "No headcount budget table on this installation — this check is skipped, not passed."


def agents_for(country: str) -> list[Agent]:
    return [a for a in AGENTS.values() if not a.countries or country in a.countries]
