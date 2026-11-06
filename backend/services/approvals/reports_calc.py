"""Reports — the arithmetic, as pure functions over plain rows (no database here).

Every function takes lists of dicts (as read by ``reports_data.py``) and returns a
report: {"columns": [...], "rows": [...], "notes": [...], "formula": str | None,
"chart": {...} | None}. Nothing in this file invents a number: an empty input gives
empty rows, and a ratio with a zero denominator gives ``None`` (shown as "No data yet").
"""
from calendar import monthrange
from datetime import date, datetime, timedelta

# A pay run counts as FINALISED for cost reports when its status is one of these.
# In this product a run row is only written when payroll is actually run (previews
# are never stored) and it is written with status "calculated"; "approved" / "paid"
# are the later states the model allows. Draft / cancelled / reverted runs never count.
FINAL_RUN_STATUSES = {"calculated", "approved", "paid", "finalised", "finalized", "locked", "posted"}

ATTRITION_FORMULA = ("Attrition % = leavers in the period ÷ average headcount × 100. "
                     "Average headcount = (headcount on the first day + headcount on the last day) ÷ 2. "
                     "A leaver is anyone whose last working day falls inside the period.")


# ── small helpers ─────────────────────────────────────────────────────────────

def to_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def r2(v: float) -> float:
    return round(v + 0.0, 2)


def pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{date(int(y), int(m), 1).strftime('%b')} {y}"


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, monthrange(y, m + 1)[1]))


def months_between(start: date, end: date) -> list[str]:
    """Every YYYY-MM from ``start``'s month to ``end``'s month, inclusive."""
    out, cur = [], month_start(start)
    while cur <= end:
        out.append(month_key(cur))
        cur = add_months(cur, 1)
    return out


def emp_name(e: dict | None) -> str:
    if not e:
        return "Former employee"
    return f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() or (e.get("employee_number") or "Employee")


def col(key, label, type_="text"):
    return {"key": key, "label": label, "type": type_}


# ── who is on the rolls on a day ──────────────────────────────────────────────

def start_date(e: dict) -> date | None:
    """Joining date; a record without one counts from the day it was created."""
    return to_date(e.get("hire_date")) or to_date(e.get("created_at"))


def employed_on(e: dict, d: date) -> bool:
    """On the rolls on day ``d``: joined on or before it and not yet past the last day.
    Someone still being onboarded with no joining date is not counted."""
    if e.get("status") == "onboarding" and not to_date(e.get("hire_date")):
        return False
    s = start_date(e)
    if not s or s > d:
        return False
    t = to_date(e.get("termination_date"))
    return t is None or t >= d


def headcount_on(employees: list[dict], d: date) -> int:
    return sum(1 for e in employees if employed_on(e, d))


def joined_in(e: dict, start: date, end: date) -> bool:
    h = to_date(e.get("hire_date"))
    return bool(h and start <= h <= end)


def left_in(e: dict, start: date, end: date) -> bool:
    t = to_date(e.get("termination_date"))
    return bool(t and start <= t <= end)


# ══════════════════════════ people reports ═════════════════════════════════════

HEADCOUNT_DIMS = {"department": "Department", "work_location": "Location",
                  "job_title": "Grade / designation", "employment_type": "Employment type"}


def headcount_by(employees: list[dict], dim: str, as_of: date) -> dict:
    dim = dim if dim in HEADCOUNT_DIMS else "department"
    groups: dict[str, int] = {}
    for e in employees:
        if employed_on(e, as_of):
            label = str(e.get(dim) or "Not set").replace("_", " ")
            groups[label] = groups.get(label, 0) + 1
    total = sum(groups.values())
    rows = [{"group": k, "headcount": v, "share": pct(v, total)}
            for k, v in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
    return {"columns": [col("group", HEADCOUNT_DIMS[dim]), col("headcount", "Headcount", "number"),
                        col("share", "Share of total", "percent")],
            "rows": rows, "totals": {"group": "Total", "headcount": total, "share": 100.0 if total else None},
            "notes": ["Headcount = people on the rolls on the as-of date (joined on or before it, last "
                      "working day not yet passed).",
                      "A record with no joining date is counted from the day it was created."],
            "chart": {"type": "bar", "x": "group", "series": [{"key": "headcount", "label": "Headcount"}]}}


def headcount_trend(employees: list[dict], start: date, end: date) -> dict:
    rows = []
    for mk in months_between(start, end):
        y, m = map(int, mk.split("-"))
        last = min(month_end(date(y, m, 1)), end)
        rows.append({"month": month_label(mk), "headcount": headcount_on(employees, last), "counted_on": last.isoformat()})
    return {"columns": [col("month", "Month"), col("headcount", "Headcount at month end", "number"),
                        col("counted_on", "Counted on", "date")],
            "rows": rows, "notes": ["The current month is counted on the as-of date, not the month end."],
            "chart": {"type": "line", "x": "month", "series": [{"key": "headcount", "label": "Headcount"}]}}


def joiners_leavers(employees: list[dict], start: date, end: date) -> dict:
    rows, tj, tl = [], 0, 0
    for mk in months_between(start, end):
        y, m = map(int, mk.split("-"))
        ms, me = max(date(y, m, 1), start), min(month_end(date(y, m, 1)), end)
        j = sum(1 for e in employees if joined_in(e, ms, me))
        lv = sum(1 for e in employees if left_in(e, ms, me))
        tj, tl = tj + j, tl + lv
        rows.append({"month": month_label(mk), "joiners": j, "leavers": lv, "net": j - lv})
    return {"columns": [col("month", "Month"), col("joiners", "Joiners", "number"),
                        col("leavers", "Leavers", "number"), col("net", "Net change", "number")],
            "rows": rows, "totals": {"month": "Total", "joiners": tj, "leavers": tl, "net": tj - tl},
            "notes": ["Joiner = joining date in the month. Leaver = last working day in the month."],
            "chart": {"type": "bar", "x": "month", "series": [{"key": "joiners", "label": "Joiners"},
                                                              {"key": "leavers", "label": "Leavers"}]}}


def attrition_figures(employees: list[dict], start: date, end: date) -> dict:
    opening, closing = headcount_on(employees, start), headcount_on(employees, end)
    leavers = sum(1 for e in employees if left_in(e, start, end))
    average = (opening + closing) / 2
    return {"opening": opening, "closing": closing, "average": average, "leavers": leavers,
            "attrition_pct": pct(leavers, average)}


def attrition(employees: list[dict], start: date, end: date) -> dict:
    rows = []
    for mk in months_between(start, end):
        y, m = map(int, mk.split("-"))
        ms, me = max(date(y, m, 1), start), min(month_end(date(y, m, 1)), end)
        f = attrition_figures(employees, ms, me)
        rows.append({"period": month_label(mk), "opening": f["opening"], "closing": f["closing"],
                     "average": r2(f["average"]), "leavers": f["leavers"], "attrition": f["attrition_pct"]})
    f = attrition_figures(employees, start, end)
    return {"columns": [col("period", "Period"), col("opening", "Headcount, first day", "number"),
                        col("closing", "Headcount, last day", "number"),
                        col("average", "Average headcount", "number"), col("leavers", "Leavers", "number"),
                        col("attrition", "Attrition %", "percent")],
            "rows": rows,
            "totals": {"period": "Whole period", "opening": f["opening"], "closing": f["closing"],
                       "average": r2(f["average"]), "leavers": f["leavers"], "attrition": f["attrition_pct"]},
            "formula": ATTRITION_FORMULA,
            "notes": ["The whole-period line is NOT the sum of the months: it uses the first and last day of "
                      "the whole period. It is not annualised."],
            "chart": {"type": "line", "x": "period", "series": [{"key": "attrition", "label": "Attrition %"}]}}


def probation_due(employees: list[dict], today: date, ahead_days: int = 60, behind_days: int = 30) -> dict:
    rows = []
    for e in employees:
        p = to_date(e.get("probation_end_date"))
        if not p or not employed_on(e, today):
            continue
        left = (p - today).days
        h = to_date(e.get("hire_date"))
        if -behind_days <= left <= ahead_days:
            rows.append({"employee": emp_name(e), "employee_number": e.get("employee_number") or "",
                         "department": e.get("department") or "", "manager": e.get("manager_name") or "",
                         "joined": h.isoformat() if h else "", "probation_ends": p.isoformat(),
                         "days_left": left,
                         "state": "Ends today" if left == 0 else f"Overdue by {-left} day(s)" if left < 0 else f"Due in {left} day(s)"})
    rows.sort(key=lambda r: r["days_left"])
    return {"columns": [col("employee", "Employee"), col("employee_number", "Employee no."),
                        col("department", "Department"), col("manager", "Manager"), col("joined", "Joined", "date"),
                        col("probation_ends", "Probation ends", "date"), col("days_left", "Days left", "number"),
                        col("state", "State")],
            "rows": rows,
            "notes": [f"Shows probation end dates from {behind_days} days ago to {ahead_days} days ahead.",
                      "This list reads the probation end date on the employee record. It does not know "
                      "whether a confirmation was already recorded elsewhere."]}


def _next_occurrence(d: date, today: date) -> date:
    """The next time the day-and-month of ``d`` comes round, on or after ``today`` (29 Feb → 28 Feb)."""
    for year in (today.year, today.year + 1):
        try:
            occ = d.replace(year=year)
        except ValueError:
            occ = date(year, 2, 28)
        if occ >= today:
            return occ
    return today


def birthdays_anniversaries(employees: list[dict], today: date, ahead_days: int = 30) -> dict:
    rows = []
    limit = today + timedelta(days=ahead_days)
    for e in employees:
        if not employed_on(e, today):
            continue
        b = to_date(e.get("date_of_birth"))
        if b:
            occ = _next_occurrence(b, today)
            if occ <= limit:
                rows.append({"employee": emp_name(e), "department": e.get("department") or "",
                             "occasion": "Birthday", "on": occ.isoformat(), "in_days": (occ - today).days,
                             "detail": ""})                   # the birth year is never shown
        h = to_date(e.get("hire_date"))
        if h:
            occ = _next_occurrence(h, today)
            years = occ.year - h.year
            if occ <= limit and years >= 1:
                rows.append({"employee": emp_name(e), "department": e.get("department") or "",
                             "occasion": "Work anniversary", "on": occ.isoformat(),
                             "in_days": (occ - today).days, "detail": f"{years} year(s)"})
    rows.sort(key=lambda r: (r["in_days"], r["employee"].lower()))
    return {"columns": [col("employee", "Employee"), col("department", "Department"), col("occasion", "Occasion"),
                        col("on", "Date", "date"), col("in_days", "In days", "number"), col("detail", "Detail")],
            "rows": rows, "notes": [f"The next {ahead_days} days. Birth years are not shown."]}


# ══════════════════════════ time and leave ═════════════════════════════════════

_ATT_STATUS = {"present": "present", "late": "late", "on_shift": "present", "absent": "absent",
               "leave": "leave", "holiday": "holiday", "rest_day": "rest"}


def attendance_summary(days: list[dict], employees: dict[str, dict]) -> dict:
    agg: dict[str, dict] = {}
    for d in days:
        a = agg.setdefault(str(d["employee_id"]), {"present": 0, "late": 0, "absent": 0, "leave": 0, "holiday": 0,
                                                   "rest": 0, "planned": 0.0, "actual": 0.0, "late_min": 0})
        k = _ATT_STATUS.get(d.get("status") or "")
        if k:
            a[k] += 1
        if d.get("status") == "late":
            a["present"] += 1                               # a late day is still a day worked
        a["planned"] += num(d.get("planned_hours"))
        a["actual"] += num(d.get("actual_hours"))
        a["late_min"] += int(num(d.get("late_minutes")))
    rows = []
    for eid, a in agg.items():
        e = employees.get(eid)
        worked, due = a["present"], a["present"] + a["absent"]
        rows.append({"employee": emp_name(e), "employee_number": (e or {}).get("employee_number") or "",
                     "department": (e or {}).get("department") or "", "days_worked": worked,
                     "late_days": a["late"], "absent_days": a["absent"], "leave_days": a["leave"],
                     "holidays": a["holiday"], "planned_hours": r2(a["planned"]), "actual_hours": r2(a["actual"]),
                     "late_minutes": a["late_min"], "attendance": pct(worked, due)})
    rows.sort(key=lambda r: r["employee"].lower())
    return {"columns": [col("employee", "Employee"), col("employee_number", "Employee no."),
                        col("department", "Department"), col("days_worked", "Days worked", "number"),
                        col("late_days", "Late days", "number"), col("absent_days", "Absent days", "number"),
                        col("leave_days", "Leave days", "number"), col("holidays", "Holidays", "number"),
                        col("planned_hours", "Planned hours", "hours"), col("actual_hours", "Actual hours", "hours"),
                        col("late_minutes", "Late minutes", "number"), col("attendance", "Attendance %", "percent")],
            "rows": rows,
            "formula": "Attendance % = days worked ÷ (days worked + absent days) × 100. Leave, holidays and "
                       "rest days are left out of both sides. A late day counts as a day worked.",
            "notes": ["Built from the daily attendance records. A day with no record is not counted at all."]}


def leave_balances(balances: list[dict], employees: dict[str, dict]) -> dict:
    rows = []
    for b in balances:
        e = employees.get(str(b["employee_id"]))
        entitled = num(b.get("allocated")) + num(b.get("carried_over"))
        used, pending = num(b.get("used")), num(b.get("pending"))
        rows.append({"employee": emp_name(e), "employee_number": (e or {}).get("employee_number") or "",
                     "department": (e or {}).get("department") or "",
                     "leave_type": b.get("leave_type_name") or b.get("leave_type_code") or "",
                     "year": b.get("fiscal_year"), "granted": r2(num(b.get("allocated"))),
                     "carried_over": r2(num(b.get("carried_over"))), "entitled": r2(entitled), "used": r2(used),
                     "pending": r2(pending), "remaining": r2(entitled - used - pending),
                     "used_share": pct(used, entitled)})
    rows.sort(key=lambda r: (r["employee"].lower(), r["leave_type"]))
    return {"columns": [col("employee", "Employee"), col("employee_number", "Employee no."),
                        col("department", "Department"), col("leave_type", "Leave type"), col("year", "Year"),
                        col("granted", "Granted", "number"), col("carried_over", "Carried over", "number"),
                        col("entitled", "Entitled", "number"), col("used", "Used", "number"),
                        col("pending", "Awaiting approval", "number"), col("remaining", "Remaining", "number"),
                        col("used_share", "Used %", "percent")],
            "rows": rows,
            "formula": "Entitled = granted + carried over. Remaining = entitled − used − awaiting approval."}


def leave_usage(requests: list[dict], start: date, end: date) -> dict:
    """Approved leave days by type and month. A request is counted in the month it STARTS."""
    months = months_between(start, end)
    table: dict[str, dict[str, float]] = {}
    for r in requests:
        if r.get("status") != "approved":
            continue
        sd = to_date(r.get("start_date"))
        if not sd or not (start <= sd <= end):
            continue
        t = r.get("leave_type_name") or r.get("leave_type_code") or "Leave"
        table.setdefault(t, {})
        table[t][month_key(sd)] = table[t].get(month_key(sd), 0.0) + num(r.get("days"))
    rows = []
    for t in sorted(table):
        row = {"leave_type": t, "total": r2(sum(table[t].values()))}
        for mk in months:
            row[mk] = r2(table[t].get(mk, 0.0))
        rows.append(row)
    totals = {"leave_type": "Total", "total": r2(sum(r["total"] for r in rows))}
    for mk in months:
        totals[mk] = r2(sum(r[mk] for r in rows))
    by_month = [{"month": month_label(mk), "days": totals[mk]} for mk in months]
    return {"columns": [col("leave_type", "Leave type")] + [col(mk, month_label(mk), "number") for mk in months]
                       + [col("total", "Total days", "number")],
            "rows": rows, "totals": totals if rows else None,
            "notes": ["Approved requests only. A request that runs across two months is counted in the month it starts."],
            "chart": {"type": "bar", "x": "month", "series": [{"key": "days", "label": "Leave days"}], "data": by_month}}


def overtime(timesheets: list[dict], employees: dict[str, dict]) -> dict:
    agg: dict[str, dict] = {}
    for t in timesheets:
        if t.get("status") not in ("approved", "locked"):
            continue
        a = agg.setdefault(str(t["employee_id"]), {"reg": 0.0, "ot": 0.0, "ot2": 0.0, "sheets": 0})
        a["reg"] += num(t.get("reg_hours"))
        a["ot"] += num(t.get("ot_hours"))
        a["ot2"] += num(t.get("ot2_hours"))
        a["sheets"] += 1
    rows = []
    for eid, a in agg.items():
        total_ot = a["ot"] + a["ot2"]
        if total_ot <= 0:
            continue
        e = employees.get(eid)
        rows.append({"employee": emp_name(e), "employee_number": (e or {}).get("employee_number") or "",
                     "department": (e or {}).get("department") or "", "timesheets": a["sheets"],
                     "regular_hours": r2(a["reg"]), "overtime_hours": r2(a["ot"]), "double_time_hours": r2(a["ot2"]),
                     "total_overtime": r2(total_ot), "overtime_share": pct(total_ot, a["reg"] + total_ot)})
    rows.sort(key=lambda r: -r["total_overtime"])
    return {"columns": [col("employee", "Employee"), col("employee_number", "Employee no."),
                        col("department", "Department"), col("timesheets", "Timesheets", "number"),
                        col("regular_hours", "Regular hours", "hours"), col("overtime_hours", "Overtime hours", "hours"),
                        col("double_time_hours", "Double-time hours", "hours"),
                        col("total_overtime", "Total overtime", "hours"), col("overtime_share", "Overtime % of hours", "percent")],
            "rows": rows,
            "totals": {"employee": "Total", "regular_hours": r2(sum(r["regular_hours"] for r in rows)),
                       "overtime_hours": r2(sum(r["overtime_hours"] for r in rows)),
                       "double_time_hours": r2(sum(r["double_time_hours"] for r in rows)),
                       "total_overtime": r2(sum(r["total_overtime"] for r in rows))} if rows else None,
            "notes": ["Approved and locked timesheets only, by the period the timesheet ends in. "
                      "Hours only — no pay amounts."]}


# ══════════════════════════ payroll (finalised runs only) ══════════════════════

_EMPLOYER_KEYS = ("cpp", "cpp2", "ei", "qpip", "eht", "wcb")


def employer_cost_of(detail: dict | None) -> float:
    """Employer-side contributions on one pay stub (CPP/CPP2, EI, QPIP, EHT, WCB)."""
    ec = (detail or {}).get("employer_cost") or {}
    return sum(num(ec.get(k)) for k in _EMPLOYER_KEYS)


def final_stubs(stubs: list[dict]) -> list[dict]:
    return [s for s in stubs if (s.get("run_status") or "").lower() in FINAL_RUN_STATUSES]


def payroll_cost(stubs: list[dict], employees: dict[str, dict], by: str = "month") -> dict:
    by = by if by in ("month", "department") else "month"
    used = final_stubs(stubs)
    skipped = len(stubs) - len(used)
    groups: dict[str, dict] = {}
    for s in used:
        pd = to_date(s.get("pay_date"))
        if by == "month":
            if not pd:
                continue
            key = month_key(pd)
        else:
            key = (employees.get(str(s["employee_id"])) or {}).get("department") or "Not set"
        g = groups.setdefault(key, {"gross": 0.0, "employer": 0.0, "net": 0.0, "emps": set(), "runs": set()})
        g["gross"] += num(s.get("gross"))
        g["employer"] += employer_cost_of(s.get("detail"))
        g["net"] += num(s.get("net_pay"))
        g["emps"].add(str(s["employee_id"]))
        g["runs"].add(str(s.get("pay_run_id")))
    keys = sorted(groups) if by == "month" else sorted(groups, key=lambda k: -groups[k]["gross"])
    rows = []
    for k in keys:
        g = groups[k]
        rows.append({"group": month_label(k) if by == "month" else k, "employees_paid": len(g["emps"]),
                     "pay_runs": len(g["runs"]), "gross": r2(g["gross"]), "employer_contributions": r2(g["employer"]),
                     "total_cost": r2(g["gross"] + g["employer"]), "net_paid": r2(g["net"])})
    totals = None
    if rows:
        totals = {"group": "Total", "gross": r2(sum(r["gross"] for r in rows)),
                  "employer_contributions": r2(sum(r["employer_contributions"] for r in rows)),
                  "total_cost": r2(sum(r["total_cost"] for r in rows)), "net_paid": r2(sum(r["net_paid"] for r in rows))}
    notes = ["Finalised pay runs only, by pay date. Draft, cancelled and reverted runs are left out."]
    if skipped:
        notes.append(f"{skipped} pay stub(s) from runs that are not finalised were left out.")
    if by == "department":
        notes.append("Department is the employee's CURRENT department, not the one at the time of the run.")
    return {"columns": [col("group", "Month" if by == "month" else "Department"),
                        col("employees_paid", "Employees paid", "number"), col("pay_runs", "Pay runs", "number"),
                        col("gross", "Gross pay", "money"),
                        col("employer_contributions", "Employer contributions", "money"),
                        col("total_cost", "Total cost", "money"), col("net_paid", "Net paid", "money")],
            "rows": rows, "totals": totals,
            "formula": "Total cost = gross pay + employer contributions.",
            "notes": notes,
            "chart": {"type": "bar", "x": "group", "series": [{"key": "total_cost", "label": "Total cost"}]}}


def statutory_liability(stubs: list[dict]) -> dict:
    """Per month, from finalised pay stubs: the CRA source deductions withheld and the employer's share
    (CPP/QPP incl. CPP2, EI, income tax) — what the remittance for that month must cover."""
    months: dict[str, dict] = {}
    for s in final_stubs(stubs):
        pd = to_date(s.get("pay_date"))
        if not pd:
            continue
        d = s.get("detail") or {}
        ec = d.get("employer_cost") or {}
        m = months.setdefault(month_key(pd), {"a_ee": 0.0, "a_er": 0.0, "b_ee": 0.0, "b_er": 0.0, "tax": 0.0})
        m["a_ee"] += num(s.get("cpp")) + num(s.get("cpp2"))
        m["a_er"] += num(ec.get("cpp")) + num(ec.get("cpp2"))
        m["b_ee"] += num(s.get("ei"))
        m["b_er"] += num(ec.get("ei"))
        m["tax"] += num(s.get("income_tax"))
    rows = []
    for mk in sorted(months):
        m = months[mk]
        total = m["a_ee"] + m["a_er"] + m["b_ee"] + m["b_er"] + m["tax"]
        rows.append({"month": month_label(mk), "a_employee": r2(m["a_ee"]), "a_employer": r2(m["a_er"]),
                     "b_employee": r2(m["b_ee"]), "b_employer": r2(m["b_er"]), "tax": r2(m["tax"]), "total": r2(total)})
    columns = [col("month", "Pay month"), col("a_employee", "CPP — employee", "money"),
               col("a_employer", "CPP — employer", "money"), col("b_employee", "EI — employee", "money"),
               col("b_employer", "EI — employer", "money"), col("tax", "Income tax", "money"),
               col("total", "Total remittance", "money")]
    notes = ["Worked out from finalised pay stubs, grouped by pay date. It is a summary to reconcile against "
             "the CRA remittance (PD7A) screens — it is not itself a remittance voucher.",
             "CPP includes CPP2 (and QPP for Québec employees). Amounts in CAD."]
    return {"columns": columns, "rows": rows, "notes": notes,
            "formula": "Total remittance = employee CPP + EI + income tax withheld + employer CPP + EI for the month."}


# ══════════════════════════ simple counts (lifecycle modules) ══════════════════

def state_counts(sets: list[tuple[str, list[dict]]]) -> dict:
    """Counts by state for records owned by other modules: [(what, [{state}, …]), …].
    Counts only — no names, no amounts, no review text."""
    rows = []
    for what, items in sets:
        by: dict[str, int] = {}
        for i in items:
            label = str(i.get("state") or "Not set").replace("_", " ").capitalize()
            by[label] = by.get(label, 0) + 1
        total = sum(by.values())
        for label, n in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append({"what": what, "state": label, "count": n, "share": pct(n, total)})
    return {"columns": [col("what", "Record"), col("state", "State"), col("count", "Count", "number"),
                        col("share", "Share of that record", "percent")],
            "rows": rows, "notes": ["Counts only. Open the owning screen for the records themselves."]}


# ══════════════════════════ approvals ageing ═══════════════════════════════════

def approvals_ageing(items: list[dict], now: datetime, buckets: list[str], bucket_of, label_of) -> dict:
    table: dict[str, dict] = {}
    for i in items:
        if i.get("status") != "pending":
            continue
        opened = i.get("opened_at")
        if isinstance(opened, str):
            opened = datetime.fromisoformat(opened)
        if opened is not None and opened.tzinfo is None and now.tzinfo is not None:
            opened = opened.replace(tzinfo=now.tzinfo)
        days = max(0, (now - opened).days) if opened else 0
        t = table.setdefault(i["kind"], {b: 0 for b in buckets} | {"oldest": 0, "total": 0})
        t[bucket_of(days)] += 1
        t["total"] += 1
        t["oldest"] = max(t["oldest"], days)
    rows = []
    for kind in sorted(table, key=lambda k: -table[k]["total"]):
        t = table[kind]
        rows.append({"kind": label_of(kind), **{b: t[b] for b in buckets}, "total": t["total"], "oldest": t["oldest"]})
    totals = None
    if rows:
        totals = {"kind": "Total", **{b: sum(r[b] for r in rows) for b in buckets},
                  "total": sum(r["total"] for r in rows), "oldest": max(r["oldest"] for r in rows)}
    return {"columns": [col("kind", "Type")] + [col(b, b, "number") for b in buckets]
                       + [col("total", "Waiting", "number"), col("oldest", "Oldest (days)", "number")],
            "rows": rows, "totals": totals,
            "notes": ["Age is counted in whole days from the moment the request was raised."]}
