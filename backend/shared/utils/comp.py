"""Lexora HRM — effective-dated compensation helpers (Stream B, shared).

The ONE function other streams code against:

    effective_salary_for_period(employee, period_start, period_end)
        -> [(from_date, to_date, annual_amount, components), ...]

It answers "what salary was in force on each day of this pay period". The list has
one slice when nothing changed inside the period and one slice per rate when a
revision took effect inside it. Slices are in date order, never overlap, and cover
the whole period.

Where the history comes from (first match wins):
  1. the `history=` argument,
  2. `employee["comp_history"]` (rows from GET /employees/compensation/history),
  3. the history registered for this request with `register_history()` — the
     payroll hook does this at the start of preview / run,
  4. nothing known → ONE slice built from the employee's current `annual_salary`
     (or hourly rate), i.e. "no revision".

A history row is a dict: effective_from, effective_to (optional), annual_amount,
hourly_rate (optional), pay_type (optional), components (optional {CODE: monthly}).

Everything here is pure date arithmetic. There is NO statutory figure, rate or
rounding in this module: amounts are returned as given and blended values are
left unrounded for the engine to round where it already does.
"""
from __future__ import annotations

import calendar
import contextvars
from datetime import date, datetime, timedelta

from shared.utils.org_time import local_today

_HISTORY: contextvars.ContextVar[dict | None] = contextvars.ContextVar("lexora_comp_history", default=None)

WEEKS_PER_YEAR = 52     # the hourly → annual convention the Canada run already uses

# Pay currency (Canada-only product).
CURRENCY_BY_COUNTRY = {"CA": "CAD"}


def currency_for(country: str | None) -> str:
    return CURRENCY_BY_COUNTRY.get((country or "CA").upper(), "CAD")


# ── small date helpers ────────────────────────────────────────────────────────
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


def days_between(a: date, b: date) -> int:
    """Calendar days from a to b, both included (0 when b is before a)."""
    return max((b - a).days + 1, 0)


def month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(d: date) -> tuple[date, date]:
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def next_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y + (mo // 12):04d}-{(mo % 12) + 1:02d}"


# ── history ───────────────────────────────────────────────────────────────────
def register_history(history_by_employee: dict | None) -> None:
    """Make {employee_id: [rows]} visible to effective_salary_for_period() for the
    rest of this request (context-local, so two requests never see each other's)."""
    _HISTORY.set({str(k): list(v or []) for k, v in (history_by_employee or {}).items()})


def annual_of(row: dict, employee: dict | None = None) -> float:
    """Annual amount of a history row. Hourly rows are annualised with the employee's
    standard weekly hours × 52 — the same convention the run uses for hourly pay."""
    amt = row.get("annual_amount")
    if amt is not None and float(amt or 0) > 0:
        return float(amt)
    rate = float(row.get("hourly_rate") or 0)
    if rate > 0:
        hrs = float((employee or {}).get("standard_hours_per_week") or row.get("standard_hours_per_week") or 40)
        return rate * hrs * WEEKS_PER_YEAR
    return float(amt or 0)


def normalise_history(rows: list | None, employee: dict | None = None) -> list[dict]:
    """Sort rows by effective date and close each one the day before the next starts.
    Two rows on the same day: the later-created one wins (a correction)."""
    clean = []
    for i, r in enumerate(rows or []):
        d = to_date(r.get("effective_from"))
        if not d:
            continue
        clean.append({**r, "effective_from": d, "_i": i,
                      "_created": str(r.get("created_at") or ""), "annual_amount": annual_of(r, employee)})
    clean.sort(key=lambda r: (r["effective_from"], r["_created"], r["_i"]))
    out: list[dict] = []
    for r in clean:
        if out and out[-1]["effective_from"] == r["effective_from"]:
            out[-1] = r
        else:
            out.append(r)
    for i, r in enumerate(out):
        nxt = out[i + 1]["effective_from"] - timedelta(days=1) if i + 1 < len(out) else None
        own = to_date(r.get("effective_to"))
        r["effective_to"] = nxt if nxt is not None else own
    return out


def _fallback_row(employee: dict) -> dict:
    return {"effective_from": date.min, "effective_to": None,
            "annual_amount": float(employee.get("annual_salary") or 0),
            "hourly_rate": float(employee.get("hourly_rate") or 0),
            "pay_type": employee.get("pay_type") or "salary", "components": {}, "source": "current"}


def _history_for(employee: dict, history: list | None) -> list:
    if history is not None:
        return history
    if employee.get("comp_history") is not None:
        return employee["comp_history"]
    reg = _HISTORY.get()
    if reg is not None:
        return reg.get(str(employee.get("id")), [])
    return []


def effective_salary_slices(employee: dict, period_start, period_end, history: list | None = None) -> list[dict]:
    """Rich form of effective_salary_for_period(): a list of dicts with from_date,
    to_date, days, annual_amount, hourly_rate, pay_type, components, revision_id."""
    ps, pe = to_date(period_start), to_date(period_end)
    if not ps or not pe or pe < ps:
        raise ValueError("period_start and period_end must be dates, start on or before end")
    employee = employee or {}
    rows = normalise_history(_history_for(employee, history), employee)
    if not rows or not any(r["annual_amount"] > 0 for r in rows):
        rows = normalise_history([_fallback_row(employee)], employee)
    # Days before the first known row use that first row (the opening salary).
    rows[0]["effective_from"] = min(rows[0]["effective_from"], ps)
    out = []
    for r in rows:
        a = max(r["effective_from"], ps)
        b = min(r["effective_to"] or pe, pe)
        if b < a:
            continue
        out.append({"from_date": a, "to_date": b, "days": days_between(a, b),
                    "annual_amount": r["annual_amount"], "hourly_rate": float(r.get("hourly_rate") or 0),
                    "pay_type": r.get("pay_type") or employee.get("pay_type") or "salary",
                    "components": dict(r.get("components") or {}),
                    "revision_id": r.get("source_revision_id"), "reason": r.get("reason")})
    return out


def effective_salary_for_period(employee: dict, period_start, period_end, history: list | None = None) -> list[tuple]:
    """Salary in force on each day of [period_start, period_end].

    Returns [(from_date, to_date, annual_amount, components), ...] in date order.
    `components` is the monthly break-up {CODE: amount} recorded with that salary
    ({} when none was recorded). One slice = no change inside the period."""
    return [(s["from_date"], s["to_date"], s["annual_amount"], s["components"])
            for s in effective_salary_slices(employee, period_start, period_end, history)]


# ── blending (day-weighted; nothing is rounded here) ──────────────────────────
def _as_parts(slices) -> list[tuple[int, float, dict]]:
    parts = []
    for s in slices or []:
        if isinstance(s, dict):
            a, b, amt, comps = s["from_date"], s["to_date"], s["annual_amount"], s.get("components") or {}
        else:
            a, b, amt, comps = s[0], s[1], s[2], (s[3] if len(s) > 3 else {}) or {}
        parts.append((days_between(to_date(a), to_date(b)), float(amt or 0), comps))
    return parts


def blended_annual(slices) -> float:
    """Day-weighted annual rate for the period: Σ annual × days ÷ Σ days."""
    parts = _as_parts(slices)
    total = sum(d for d, _, _ in parts)
    return sum(d * amt for d, amt, _ in parts) / total if total else 0.0


def period_amount(slices, periods_per_year: int) -> float:
    """Pay for ONE period across its slices: old rate × days before + new rate × days from.
    = Σ (annual ÷ periods_per_year) × (slice days ÷ period days). Unrounded."""
    return blended_annual(slices) / periods_per_year if periods_per_year else 0.0


def blended_components(slices) -> dict:
    """Day-weighted monthly break-up across the slices ({} if any slice has none)."""
    parts = _as_parts(slices)
    total = sum(d for d, _, _ in parts)
    if not total or any(not comps for _, _, comps in parts):
        return {}
    out: dict = {}
    for d, _, comps in parts:
        for code, amt in comps.items():
            out[code] = out.get(code, 0.0) + float(amt or 0) * d / total
    return out


def split_days(period_start, period_end, effective_date) -> tuple[int, int]:
    """(days at the old rate, days at the new rate) for a change on effective_date."""
    ps, pe, ed = to_date(period_start), to_date(period_end), to_date(effective_date)
    total = days_between(ps, pe)
    if ed <= ps:
        return 0, total
    if ed > pe:
        return total, 0
    return (ed - ps).days, days_between(ed, pe)


# ── timing of a revision against what has already been paid ───────────────────
def revision_timing(effective_from, last_paid_end=None, today=None) -> str:
    """'backdated' — the date falls inside a period that is already paid;
    'future' — the date has not arrived yet; 'current' — arrived, not yet paid."""
    ed, lp, td = to_date(effective_from), to_date(last_paid_end), to_date(today) or local_today()
    if lp and ed <= lp:
        return "backdated"
    return "future" if ed > td else "current"


def retro_lines(effective_from, paid_periods: list, old_annual: float, new_annual: float,
                periods_per_year: int) -> list[dict]:
    """Canada: the pay difference for each already-paid period on or after the
    effective date. `paid_periods` = [(period_start, period_end), ...]. A period the
    date falls inside counts only for its days from that date. Amounts are plain
    earnings differences — NO deduction or tax is worked out here."""
    ed = to_date(effective_from)
    per_period = (float(new_annual) - float(old_annual)) / periods_per_year if periods_per_year else 0.0
    out = []
    for ps, pe in paid_periods or []:
        ps, pe = to_date(ps), to_date(pe)
        if not ps or not pe or pe < ed:
            continue
        total = days_between(ps, pe)
        days = days_between(max(ps, ed), pe)
        out.append({"period_start": ps.isoformat(), "period_end": pe.isoformat(), "days": days,
                    "period_days": total, "amount": per_period * days / total if total else 0.0})
    return out


# ── fetch (payroll → employee service) ────────────────────────────────────────
async def fetch_history(employee_url: str, headers: dict, employee_ids: list | None = None) -> dict:
    """{employee_id: [rows]} from the employee service. {} on any failure — a missing
    history only means "no revision known", it never stops payroll."""
    import httpx
    params = {"employee_ids": ",".join(str(x) for x in employee_ids)} if employee_ids else None
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{employee_url}/employees/compensation/history", params=params, headers=headers)
            if r.status_code == 200:
                return r.json() or {}
    except (httpx.HTTPError, ValueError):
        pass
    return {}
