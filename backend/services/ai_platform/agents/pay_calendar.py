"""Shared READ-ONLY helpers for the lifecycle agents: who signs in for which employee (chase owners) and the
open pay period + cut-off (from the payroll service's own pay runs — Canada: weekly / bi-weekly / semi-monthly /
monthly, whatever the last committed run used)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from watchdogs import ORG, rows

COMMITTED = ("calculated", "approved", "paid")


def _d(v) -> date | None:
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


async def logins(org: str) -> dict:
    """{employee_id: user_id} + the HR / payroll office user ids (who the agents chase for master-data gaps)."""
    out = {"by_employee": {}, "hr": [], "payroll": [], "org_admin": []}
    for u in await rows(f"SELECT id, employee_id, role, email FROM users WHERE org_id = {ORG} AND is_active IS TRUE", {"org": org}):
        if u.get("employee_id"):
            out["by_employee"][str(u["employee_id"])] = str(u["id"])
        role = (u.get("role") or "").lower()
        if role in ("hr_admin", "hr_manager", "hr"):
            out["hr"].append(str(u["id"]))
        elif role in ("payroll_admin", "payroll"):
            out["payroll"].append(str(u["id"]))
        elif role == "org_admin":
            out["org_admin"].append(str(u["id"]))
    return out


async def open_period(org: str, today: date, lead_days: int = 3) -> dict:
    """The pay period after the last committed regular run, its pay date and the input cut-off (pay date − lead days).
    No run yet → a bi-weekly period that contains today, paid six days after it ends."""
    r = await rows(f"SELECT period_start, period_end, pay_date FROM pay_runs WHERE org_id = {ORG} AND status IN ('calculated','approved','paid') "
                   "AND COALESCE(CAST(meta->>'off_cycle' AS text), 'false') <> 'true' ORDER BY period_end DESC LIMIT 1", {"org": org})
    if r and _d(r[0].get("period_end")) and _d(r[0].get("period_start")):
        ls, le = _d(r[0]["period_start"]), _d(r[0]["period_end"])
        length = (le - ls).days + 1
        lag = ((_d(r[0].get("pay_date")) or le) - le).days
        ps = le + timedelta(days=1)
        pe = ps + timedelta(days=length - 1)
        pay = pe + timedelta(days=max(lag, 0))
    else:
        ps = today - timedelta(days=today.weekday() + 1 if today.weekday() != 6 else 0)   # the Sunday that starts this week
        pe = ps + timedelta(days=13)
        pay = pe + timedelta(days=6)
    return {"period": f"{ps.isoformat()}..{pe.isoformat()}", "period_start": ps, "period_end": pe, "pay_date": pay,
            "cutoff": pay - timedelta(days=int(lead_days)), "lead_days": int(lead_days)}


async def schedule(org: str) -> dict | None:
    """{anchor_start, length_days, pay_lag_days} from the last committed regular run, or None before the first run.
    Exact for weekly / bi-weekly pay; for semi-monthly / monthly pay the fixed length is an approximation."""
    r = await rows(f"SELECT period_start, period_end, pay_date FROM pay_runs WHERE org_id = {ORG} AND status IN ('calculated','approved','paid') "
                   "AND COALESCE(CAST(meta->>'off_cycle' AS text), 'false') <> 'true' ORDER BY period_end DESC LIMIT 1", {"org": org})
    if not r or not _d(r[0].get("period_start")) or not _d(r[0].get("period_end")):
        return None
    ls, le = _d(r[0]["period_start"]), _d(r[0]["period_end"])
    return {"anchor_start": ls, "length_days": (le - ls).days + 1, "pay_lag_days": max(((_d(r[0].get("pay_date")) or le) - le).days, 0)}


def period_containing(sched: dict | None, d: date | None) -> tuple[date | None, date | None]:
    """(period_end, pay_date) of the pay period that contains ``d`` (None, None without a schedule)."""
    if not sched or not d:
        return None, None
    n = sched["length_days"]
    k = (d - sched["anchor_start"]).days // n
    ps = sched["anchor_start"] + timedelta(days=k * n)
    pe = ps + timedelta(days=n - 1)
    return pe, pe + timedelta(days=sched["pay_lag_days"])
