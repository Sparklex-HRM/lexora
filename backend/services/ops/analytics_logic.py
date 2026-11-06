"""People analytics — pure maths (no database). Stream D7. Tested in test_analytics_logic.py.

Headcount trend, joiners / leavers, attrition by department / tenure band / manager, gender mix
(descriptive only), span of control, overtime hours, leave liability (an estimate, labelled as such).
"""
from __future__ import annotations

from datetime import date, timedelta
from shared.utils.org_time import local_today  # org-TZ business date (UTC store, local today)

TENURE_BANDS = (("< 1 y", 0, 1), ("1–2 y", 1, 2), ("2–5 y", 2, 5), ("5+ y", 5, 1000))


def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def month_ends(today: date, months: int) -> list[date]:
    """Last day of each of the past `months` months, oldest first, ending with the current month's last day."""
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        nxt = date(y + (m // 12), m % 12 + 1, 1)
        out.append(nxt - timedelta(days=1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def active_on(emp: dict, day: date) -> bool:
    hd, td = _d(emp.get("hire_date")), _d(emp.get("termination_date"))
    if hd and hd > day:
        return False
    if td and td <= day:
        return False
    return True


def headcount_series(emps: list[dict], today: date, months: int = 12) -> list[dict]:
    """[{month: 'YYYY-MM', headcount, joiners, leavers}] — people are counted by hire / termination dates."""
    out = []
    for end in month_ends(today, months):
        start = end.replace(day=1)
        hc = sum(1 for e in emps if active_on(e, end))
        joined = sum(1 for e in emps if (_d(e.get("hire_date")) or date.min) >= start and (_d(e.get("hire_date")) or date.max) <= end)
        left = sum(1 for e in emps if e.get("termination_date") and start <= _d(e["termination_date"]) <= end)
        out.append({"month": end.strftime("%Y-%m"), "headcount": hc, "joiners": joined, "leavers": left})
    return out


def attrition_rate(leavers: int, avg_headcount: float) -> float | None:
    return round(leavers * 100 / avg_headcount, 1) if avg_headcount else None


def tenure_years(hire_date, as_of: date) -> float | None:
    hd = _d(hire_date)
    return round((as_of - hd).days / 365.25, 2) if hd else None


def tenure_band(years: float | None) -> str:
    if years is None:
        return "—"
    for name, lo, hi in TENURE_BANDS:
        if lo <= years < hi:
            return name
    return "5+ y"


def attrition_by(emps: list[dict], today: date, key: str, window_days: int = 365) -> list[dict]:
    """Leavers in the window / average headcount of the group, per `key` (department | manager_name | tenure_band)."""
    since = today - timedelta(days=window_days)
    g: dict[str, dict] = {}
    for e in emps:
        td = _d(e.get("termination_date"))
        left = bool(td and since < td <= today)
        active = active_on(e, today)
        if not (left or active):
            continue
        if key == "tenure_band":
            k = tenure_band(tenure_years(e.get("hire_date"), td if left and td else today))
        else:
            k = str(e.get(key) or "—")
        r = g.setdefault(k, {"group": k, "headcount": 0, "leavers": 0})
        if active:
            r["headcount"] += 1
        if left:
            r["leavers"] += 1
    rows = []
    for r in g.values():
        avg = r["headcount"] + r["leavers"] / 2        # leavers were employed for part of the window
        rows.append({**r, "attrition_pct": attrition_rate(r["leavers"], avg)})
    return sorted(rows, key=lambda r: (-(r["attrition_pct"] or 0), r["group"]))


def gender_mix(emps: list[dict], by: str = "department", today: date | None = None) -> list[dict]:
    """Descriptive only: counts per group per gender value as recorded (no inference, no targets). Active people on `today`."""
    g: dict[str, dict] = {}
    today = today or local_today()
    for e in emps:
        if not active_on(e, today):
            continue
        k = str(e.get(by) or "—")
        gen = str(e.get("gender") or "not recorded").strip().lower()
        r = g.setdefault(k, {"group": k, "total": 0, "by_gender": {}})
        r["total"] += 1
        r["by_gender"][gen] = r["by_gender"].get(gen, 0) + 1
    return sorted(g.values(), key=lambda r: r["group"])


def span_of_control(emps: list[dict], today: date, wide_at: int = 10) -> dict:
    """Direct reports per manager (active people only). `wide_at` flags managers above that count."""
    reports: dict[str, int] = {}
    name: dict[str, str] = {}
    for e in emps:
        if not active_on(e, today):
            continue
        name[str(e["id"])] = e.get("name") or f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip()
        m = e.get("manager_id")
        if m:
            reports[str(m)] = reports.get(str(m), 0) + 1
    rows = sorted([{"manager_id": m, "manager_name": name.get(m) or next((e.get("manager_name") for e in emps if str(e.get("manager_id") or "") == m), None) or "—",
                    "reports": n, "wide": n > wide_at} for m, n in reports.items()], key=lambda r: -r["reports"])
    counts = [r["reports"] for r in rows]
    med = None
    if counts:
        s = sorted(counts)
        med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    return {"managers": len(rows), "average": round(sum(counts) / len(counts), 1) if counts else None, "median": med,
            "wide_at": wide_at, "wide": sum(1 for r in rows if r["wide"]), "rows": rows}


def overtime_by(sheets: list[dict], key: str = "month") -> list[dict]:
    """sheets: [{employee_id, department, period_end, ot_hours, ot2_hours}] → OT hours per month or department."""
    g: dict[str, dict] = {}
    for s in sheets:
        k = (_d(s.get("period_end")) or date.min).strftime("%Y-%m") if key == "month" else str(s.get(key) or "—")
        r = g.setdefault(k, {"group": k, "ot_hours": 0.0, "sheets": 0, "people": set()})
        r["ot_hours"] += _f(s.get("ot_hours")) + _f(s.get("ot2_hours"))
        r["sheets"] += 1
        r["people"].add(str(s.get("employee_id")))
    rows = []
    for r in sorted(g.values(), key=lambda r: r["group"]):
        n = len(r["people"])
        rows.append({"group": r["group"], "ot_hours": round(r["ot_hours"], 1), "sheets": r["sheets"], "people": n,
                     "avg_per_person": round(r["ot_hours"] / n, 1) if n else 0.0})
    return rows


def leave_liability(balances: list[dict], salary_by_emp: dict[str, float], divisor: int = 30) -> dict:
    """balances: [{employee_id, leave_type_name, paid, allocated, carried_over, used}] →
    balance days × (annual salary / 12 / divisor). An ESTIMATE for planning — the org's encashment rule decides the real figure."""
    rows: dict[str, dict] = {}
    for b in balances:
        if not b.get("paid", True):
            continue
        eid = str(b.get("employee_id"))
        days = _f(b.get("allocated")) + _f(b.get("carried_over")) - _f(b.get("used"))
        if days <= 0:
            continue
        daily = (_f(salary_by_emp.get(eid)) / 12 / max(int(divisor or 30), 1)) if salary_by_emp.get(eid) else 0.0
        r = rows.setdefault(eid, {"employee_id": eid, "days": 0.0, "amount": 0.0, "types": []})
        r["days"] += days
        r["amount"] += days * daily
        r["types"].append({"leave_type": b.get("leave_type_name") or b.get("leave_type_code"), "days": round(days, 2), "amount": round(days * daily, 2)})
    out = sorted(rows.values(), key=lambda r: -r["amount"])
    for r in out:
        r["days"], r["amount"] = round(r["days"], 2), round(r["amount"], 2)
    return {"rows": out, "total_days": round(sum(r["days"] for r in out), 2), "total_amount": round(sum(r["amount"] for r in out), 2),
            "divisor": divisor, "method": f"balance days × (annual salary ÷ 12 ÷ {divisor}) — estimate; the organisation's encashment rule decides the real figure"}
