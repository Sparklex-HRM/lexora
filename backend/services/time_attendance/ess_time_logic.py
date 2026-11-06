"""Pure rules behind the employee self-service time screens (no database, unit-tested).

* who may see / act on whose attendance
* folding one calendar day into a P / A / L / HL / H / WO code with exceptions
* month KPIs
* permission (short hours) policy checks
"""
from calendar import monthrange
from datetime import date, datetime, timedelta

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
APPROVER_ROLES = HR_ROLES | {"manager"}
LATE_GRACE_MIN = 7           # same grace the attendance board uses (constants.DEFAULT_LATE_GRACE_MIN)


def may_view(role: str, me: str | None, emp: dict) -> bool:
    """Employee: own record only. Manager: own + direct reports. HR / admin: anyone."""
    if role in HR_ROLES:
        return True
    if me and str(emp.get("id")) == str(me):
        return True
    return role == "manager" and bool(me) and str(emp.get("manager_id") or "") == str(me)


def may_request_for(role: str, me: str | None, employee_id: str) -> bool:
    """Who may RAISE a correction / permission for ``employee_id``: the employee, or HR on behalf."""
    return role in HR_ROLES or (bool(me) and str(employee_id) == str(me))


def may_decide(role: str, me: str | None, row_employee_id: str, row_manager_id: str | None) -> bool:
    """HR / admin, or the employee's own manager — never the requester themselves."""
    if me and str(row_employee_id) == str(me):
        return False
    if role in HR_ROLES:
        return True
    return role == "manager" and bool(me) and str(row_manager_id or "") == str(me)


def month_bounds(month: str) -> tuple[date, date]:
    try:
        y, m = (int(x) for x in month.split("-")[:2])
        start = date(y, m, 1)
    except (ValueError, TypeError):
        raise ValueError("month must be YYYY-MM")
    return start, date(y, m, monthrange(y, m)[1])


def leave_days(rows: list[dict], start: date, end: date) -> dict:
    """{date: {type, code, status, half}} for leave rows overlapping [start, end]."""
    out: dict = {}
    for r in rows or []:
        try:
            sd, ed = date.fromisoformat(r["start_date"][:10]), date.fromisoformat(r["end_date"][:10])
        except (KeyError, TypeError, ValueError):
            continue
        d = max(sd, start)
        while d <= min(ed, end):
            half = bool(r.get("half_day")) and sd == ed
            out[d] = {"type": r.get("leave_type_name") or r.get("leave_type_code"), "code": r.get("leave_type_code"),
                      "status": r.get("status"), "half": half, "session": r.get("half_session") if half else None}
            d += timedelta(days=1)
    return out


def fold_day(d: date, *, today: date, planned: float, workday: bool, actual: float, status: str, late_min: int,
             first_in: str | None, last_out: str | None, holiday: str | None, leave: dict | None,
             shift: dict | None, correction: dict | None, permission: dict | None = None) -> dict:
    """One calendar cell. ``status`` is the attendance board's status for the day."""
    exceptions: list[str] = []
    approved_leave = leave if leave and leave.get("status") == "approved" else None
    if holiday:
        code = "H"
    elif not workday:
        code = "WO"
    elif approved_leave and not approved_leave.get("half"):
        code = "L"
    elif approved_leave and approved_leave.get("half"):
        code = "HL"
    elif first_in:
        code = "P"
    elif d < today:
        code = "A"
    else:
        code = ""                              # a future working day
    is_past = d < today
    if workday and not holiday and code in ("P", "HL"):
        if late_min > LATE_GRACE_MIN and not (permission and permission.get("status") == "approved"):
            exceptions.append("late")
        if first_in and not last_out and is_past:
            exceptions.append("missing_out")
    if code == "A":
        exceptions.append("absent")
    if correction and correction.get("status") == "approved" and "absent" in exceptions and first_in:
        exceptions.remove("absent")
    return {"date": d.isoformat(), "weekday": d.strftime("%a"), "code": code, "status": status,
            "planned_hours": round(float(planned or 0), 2), "hours": round(float(actual or 0), 2),
            "first_in": first_in, "last_out": last_out, "late_minutes": int(late_min or 0) if first_in else 0,
            "holiday": holiday, "leave": leave, "shift": shift, "exceptions": exceptions,
            "correction": correction, "permission": permission, "future": d > today}


def kpis(days: list[dict]) -> dict:
    worked = [x for x in days if x.get("first_in") and x["hours"] > 0]
    return {"avg_hours": round(sum(x["hours"] for x in worked) / len(worked), 2) if worked else 0.0,
            "days_present": sum(1 for x in days if x["code"] in ("P", "HL")),
            "late_days": sum(1 for x in days if "late" in x["exceptions"]),
            "exception_days": sum(1 for x in days if x["exceptions"]),
            "leave_days": sum(1 for x in days if x["code"] in ("L", "HL")),
            "absent_days": sum(1 for x in days if x["code"] == "A"),
            "holidays": sum(1 for x in days if x["code"] == "H")}


# ── permission (short hours) ─────────────────────────────────────────────────

DEFAULT_PERMISSION_POLICY = {"max_per_month": 2, "max_hours": 2.0}   # company policy, editable by HR


def hours_between(start_hhmm: str, end_hhmm: str) -> float:
    try:
        a = datetime.strptime(start_hhmm, "%H:%M")
        b = datetime.strptime(end_hhmm, "%H:%M")
    except (TypeError, ValueError):
        raise ValueError("Times must be HH:MM")
    if b <= a:
        raise ValueError("The end time must be after the start time")
    return round((b - a).seconds / 3600, 2)


def permission_check(policy: dict, *, hours: float, used_this_month: int) -> str | None:
    """None when allowed, else the reason in plain words."""
    mx_h = float(policy.get("max_hours") or 0)
    mx_n = int(policy.get("max_per_month") or 0)
    if mx_h and hours > mx_h + 1e-9:
        return f"A permission can be at most {mx_h:g} hour(s) under your company policy"
    if mx_n and used_this_month >= mx_n:
        return f"You have used all {mx_n} permission(s) allowed this month"
    return None
