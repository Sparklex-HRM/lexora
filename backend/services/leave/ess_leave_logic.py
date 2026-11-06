"""Pure rules behind the employee-portal leave features (no HTTP; unit-tested).

* leave apply extras: half session, CC colleagues, attachment required by the leave type's policy
* who may decide a cancellation / optional-holiday pick / comp-off
* comp-off: which days qualify, how much a day of punches earns, when the credit expires
* optional holidays: the yearly quota
All numbers here are COMPANY policy the org sets (Leave settings), never statutory figures.
"""
import uuid
from datetime import date, timedelta

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
DEFAULT_POLICY = {"optional_holidays_per_year": 2, "comp_off_full_day_hours": 8.0,
                  "comp_off_half_day_hours": 4.0, "comp_off_expiry_days": 90}
MAX_CC = 10


def validate_apply(lt: dict, *, days: float, half_day: bool, half_session: str | None, attachment_id: str | None,
                   cc: list, emps: dict, me: str) -> str | None:
    """None when the application is acceptable, else the reason in plain words."""
    if half_session not in (None, "", "first", "second"):
        return "Half day must be the first half or the second half"
    after = lt.get("attachment_after_days")
    try:
        after = int(after) if after not in (None, "") else None
    except (TypeError, ValueError):
        after = None
    if after is not None and days > after and not attachment_id:
        return (f"{lt.get('name') or 'This leave'} longer than {after} day(s) needs a supporting document "
                f"(e.g. a medical certificate) — attach it and send again")
    if len(cc or []) > MAX_CC:
        return f"You can copy at most {MAX_CC} colleagues"
    for c in cc or []:
        if c == me:
            return "You do not need to copy yourself"
        if c not in emps:
            return "One of the colleagues to copy was not found"
    if attachment_id:
        try:
            uuid.UUID(str(attachment_id))
        except ValueError:
            return "The attachment reference is not valid"
    return None


def may_decide(role: str, me: str | None, row_employee_id: str, row_manager_id: str | None) -> bool:
    """HR / admin, or the employee's own manager — never the requester."""
    if me and str(row_employee_id) == str(me):
        return False
    if role in HR_ROLES:
        return True
    return role == "manager" and bool(me) and str(row_manager_id or "") == str(me)


def may_view(role: str, me: str | None, row_employee_id: str, row_manager_id: str | None) -> bool:
    if role in HR_ROLES or (me and str(row_employee_id) == str(me)):
        return True
    return role == "manager" and bool(me) and str(row_manager_id or "") == str(me)


# ── comp-off ─────────────────────────────────────────────────────────────────

def comp_off_day_kind(d: date, holidays: dict, weekly_offs: list[int]) -> tuple[str, str] | None:
    """('holiday', name) | ('weekly_off', 'Saturday') | None (an ordinary working day)."""
    if d in holidays:
        return "holiday", holidays[d]
    if d.weekday() in (weekly_offs or [5, 6]):
        return "weekly_off", d.strftime("%A")
    return None


def worked_hours(punches: list[dict]) -> tuple[float, str | None, str | None]:
    """(hours, first_in ISO, last_out ISO) from raw punch rows (in / out / break_*)."""
    from datetime import datetime
    ev = sorted(punches or [], key=lambda p: p.get("at") or "")
    total, open_in, first_in, last_out = 0.0, None, None, None
    for p in ev:
        try:
            t = datetime.fromisoformat(str(p["at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if p.get("direction") in ("in", "break_end"):
            open_in = t
            if p.get("direction") == "in" and first_in is None:
                first_in = p["at"]
        elif p.get("direction") in ("out", "break_start") and open_in:
            total += (t - open_in).total_seconds() / 3600
            open_in = None
            if p.get("direction") == "out":
                last_out = p["at"]
    return round(total, 2), first_in, last_out


def comp_off_days(hours: float, policy: dict) -> float:
    full = float(policy.get("comp_off_full_day_hours") or 0)
    half = float(policy.get("comp_off_half_day_hours") or 0)
    if full and hours + 1e-9 >= full:
        return 1.0
    if half and hours + 1e-9 >= half:
        return 0.5
    return 0.0


def comp_off_expiry(worked: date, policy: dict) -> date | None:
    n = int(policy.get("comp_off_expiry_days") or 0)
    return worked + timedelta(days=n) if n > 0 else None


def comp_off_window_ok(worked: date, today: date, policy: dict) -> bool:
    """A comp-off is claimed for a day already worked and still inside the expiry window."""
    if worked > today:
        return False
    exp = comp_off_expiry(worked, policy)
    return exp is None or today <= exp


# ── optional holidays ────────────────────────────────────────────────────────

def optional_quota(policy: dict, picks: list[dict], year: int) -> dict:
    used = sum(1 for p in picks if p.get("year") == year and p.get("status") in ("pending", "approved"))
    mx = int(policy.get("optional_holidays_per_year") or 0)
    return {"allowed": mx, "used": used, "left": max(0, mx - used)}
