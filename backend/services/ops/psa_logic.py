"""Stream G2 — Project Services arithmetic. Pure functions, no DB, no HTTP.

rate resolution · line amounts · available hours · utilisation · bench · invoice draft
grouping · leakage · FX conversion · invoice numbering · due dates.
Nothing here is a statutory figure; every number comes from the org's own cards and settings.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

UNITS = ("hour", "day")


def to_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def r2(x) -> float:
    return round(float(x or 0) + 1e-9, 2)


# ── rate cards ────────────────────────────────────────────────────────────────
def card_specificity(card: dict) -> int:
    """Higher = more specific. employee+project 7, employee 6, project+role 5, project 4,
    client+role 3, client 2, role 1, nothing 0."""
    emp, proj, cli, role = card.get("employee_id"), card.get("project_id"), card.get("client"), card.get("role")
    if emp and proj:
        return 7
    if emp:
        return 6
    if proj and role:
        return 5
    if proj:
        return 4
    if cli and role:
        return 3
    if cli:
        return 2
    if role:
        return 1
    return 0


def card_matches(card: dict, *, employee_id=None, project_id=None, client=None, role=None, on: date) -> bool:
    if not card.get("is_active", True):
        return False
    ef, et = to_date(card.get("effective_from")), to_date(card.get("effective_to"))
    if ef and on < ef:
        return False
    if et and on > et:
        return False
    if card.get("employee_id") and str(card["employee_id"]) != str(employee_id or ""):
        return False
    if card.get("project_id") and str(card["project_id"]) != str(project_id or ""):
        return False
    if card.get("client") and (card["client"] or "").strip().lower() != (client or "").strip().lower():
        return False
    if card.get("role") and (card["role"] or "").strip().lower() != (role or "").strip().lower():
        return False
    return True


def resolve_rate(cards: list[dict], *, employee_id=None, project_id=None, client=None, role=None, on) -> dict | None:
    """The card in force on `on` for this work: most specific wins; among equals the
    latest effective_from wins. None when no card applies."""
    d = to_date(on)
    best, best_key = None, None
    for c in cards or []:
        if not card_matches(c, employee_id=employee_id, project_id=project_id, client=client, role=role, on=d):
            continue
        key = (card_specificity(c), to_date(c.get("effective_from")) or date.min)
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


def line_amount(hours: float, rate: float, unit: str = "hour", hours_per_day: float = 8) -> float:
    """hour: hours × rate. day: (hours ÷ hours per day) × rate."""
    h, r = float(hours or 0), float(rate or 0)
    if unit == "day":
        hpd = float(hours_per_day or 8) or 8
        return r2(h / hpd * r)
    return r2(h * r)


# ── available hours & utilisation ─────────────────────────────────────────────
def month_bounds(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def working_days(start: date, end: date, working_weekdays, holidays=()) -> int:
    """Calendar days in [start, end] whose weekday (Mon=0) is a working day and that are not holidays."""
    wd = set(int(x) for x in (working_weekdays or [0, 1, 2, 3, 4]))
    hol = {to_date(h) for h in (holidays or [])}
    n, d = 0, start
    while d <= end:
        if d.weekday() in wd and d not in hol:
            n += 1
        d += timedelta(days=1)
    return n


def available_hours(start: date, end: date, working_weekdays, hours_per_day: float,
                    join_date=None, exit_date=None, holidays=()) -> float:
    """Working days × hours per day, counting only days the person was employed."""
    a, b = start, end
    jd, xd = to_date(join_date), to_date(exit_date)
    if jd and jd > a:
        a = jd
    if xd and xd < b:
        b = xd
    if b < a:
        return 0.0
    return r2(working_days(a, b, working_weekdays, holidays) * float(hours_per_day or 8))


def utilisation_rows(employees: list[dict], hours_by_emp: dict, start: date, end: date,
                     working_weekdays, hours_per_day: float, holidays=()) -> list[dict]:
    """One row per employee: available, billable, non-billable, utilisation %.
    hours_by_emp: {employee_id: {billable, non_billable, total}}."""
    out = []
    for e in employees:
        h = hours_by_emp.get(str(e["id"])) or {}
        avail = available_hours(start, end, working_weekdays, hours_per_day, e.get("hire_date"),
                                e.get("termination_date"), holidays)
        bill = float(h.get("billable") or 0)
        nonb = float(h.get("non_billable") or 0)
        out.append({"employee_id": str(e["id"]),
                    "employee_name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                    "employee_number": e.get("employee_number"), "department": e.get("department"),
                    "available": avail, "billable": r2(bill), "non_billable": r2(nonb), "total": r2(bill + nonb),
                    "utilisation_pct": r2(bill * 100 / avail) if avail else 0.0,
                    "productive_pct": r2((bill + nonb) * 100 / avail) if avail else 0.0})
    return out


def team_rollup(rows: list[dict], key: str = "department") -> list[dict]:
    g: dict = defaultdict(lambda: {"available": 0.0, "billable": 0.0, "non_billable": 0.0, "people": 0})
    for r in rows:
        k = r.get(key) or "—"
        g[k]["available"] += r["available"]; g[k]["billable"] += r["billable"]
        g[k]["non_billable"] += r["non_billable"]; g[k]["people"] += 1
    out = []
    for k, v in sorted(g.items()):
        out.append({key: k, "people": v["people"], "available": r2(v["available"]), "billable": r2(v["billable"]),
                    "non_billable": r2(v["non_billable"]),
                    "utilisation_pct": r2(v["billable"] * 100 / v["available"]) if v["available"] else 0.0})
    return out


# ── bench ─────────────────────────────────────────────────────────────────────
def allocation_on(assignments: list[dict], employee_id, on: date) -> float:
    tot = 0.0
    for a in assignments:
        if str(a.get("employee_id")) != str(employee_id) or not a.get("is_active", True):
            continue
        s, e = to_date(a.get("start_date")), to_date(a.get("end_date"))
        if s and on < s:
            continue
        if e and on > e:
            continue
        tot += float(a.get("allocation_pct") or 0)
    return r2(tot)


def bench_rows(employees: list[dict], assignments: list[dict], on: date) -> list[dict]:
    """Everyone allocated below 100 % on `on` (no project = 0 %)."""
    out = []
    for e in employees:
        if e.get("status") == "terminated":
            continue
        alloc = allocation_on(assignments, e["id"], on)
        if alloc >= 100:
            continue
        projects = sorted({a.get("project_name") or a.get("project_id") for a in assignments
                           if str(a.get("employee_id")) == str(e["id"]) and a.get("is_active", True)
                           and (not to_date(a.get("end_date")) or to_date(a["end_date"]) >= on)
                           and (not to_date(a.get("start_date")) or to_date(a["start_date"]) <= on)})
        out.append({"employee_id": str(e["id"]),
                    "employee_name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                    "employee_number": e.get("employee_number"), "department": e.get("department"),
                    "designation": e.get("job_title"), "allocated_pct": alloc, "free_pct": r2(100 - alloc),
                    "projects": ", ".join(p for p in projects if p), "fully_on_bench": alloc == 0})
    out.sort(key=lambda r: (-r["free_pct"], r["employee_name"]))
    return out


# ── invoice draft ─────────────────────────────────────────────────────────────
def price_lines(lines: list[dict], cards: list[dict], *, hours_per_day: float, client: str | None,
                default_currency: str, project_client: dict | None = None) -> list[dict]:
    """Attach a rate to every timesheet line. A stamped snapshot (bill_rate) is kept;
    otherwise the rate card in force on the work date is resolved. Returns the lines with
    rate, unit, currency, amount, rate_source and missing_rate."""
    out = []
    for ln in lines:
        l = dict(ln)
        proj_client = (project_client or {}).get(str(l.get("project_id")) or "") or client
        if l.get("bill_rate") is not None:
            rate, unit, cur, src, cid = float(l["bill_rate"]), l.get("bill_unit") or "hour", l.get("bill_currency") or default_currency, "snapshot", l.get("rate_card_id")
        else:
            c = resolve_rate(cards, employee_id=l.get("employee_id"), project_id=l.get("project_id"),
                             client=proj_client, role=l.get("role"), on=l.get("work_date"))
            if c:
                rate, unit, cur, src, cid = float(c["rate"]), c.get("unit") or "hour", c.get("currency") or default_currency, "card", str(c.get("id") or "")
            else:
                rate, unit, cur, src, cid = 0.0, "hour", default_currency, "none", None
        l.update({"rate": rate, "unit": unit, "currency": cur, "rate_source": src, "rate_card_id": cid,
                  "missing_rate": src == "none",
                  "amount": line_amount(l.get("hours") or 0, rate, unit, hours_per_day)})
        out.append(l)
    return out


def group_tm_lines(priced: list[dict]) -> list[dict]:
    """T&M invoice lines: one per (employee, project, rate, unit, currency)."""
    g: dict = {}
    for l in priced:
        if not l.get("billable", True):
            continue
        k = (str(l.get("employee_id")), str(l.get("project_id")), float(l["rate"]), l["unit"], l["currency"])
        row = g.setdefault(k, {"kind": "tm", "employee_id": l.get("employee_id"), "employee_name": l.get("employee_name"),
                               "project_id": l.get("project_id"), "project_name": l.get("project_name"),
                               "rate": float(l["rate"]), "unit": l["unit"], "currency": l["currency"],
                               "hours": 0.0, "amount": 0.0, "line_ids": [], "missing_rate": False,
                               "period_start": None, "period_end": None})
        row["hours"] = r2(row["hours"] + float(l.get("hours") or 0))
        row["amount"] = r2(row["amount"] + float(l.get("amount") or 0))
        row["line_ids"].append(str(l["id"]))
        row["missing_rate"] = row["missing_rate"] or bool(l.get("missing_rate"))
        wd = to_date(l.get("work_date"))
        if wd:
            row["period_start"] = wd if not row["period_start"] or wd < row["period_start"] else row["period_start"]
            row["period_end"] = wd if not row["period_end"] or wd > row["period_end"] else row["period_end"]
    out = []
    for row in g.values():
        unit_word = "day" if row["unit"] == "day" else "hour"
        qty = f"{row['hours']:g}"
        row["description"] = f"{row['employee_name'] or 'Work'} — {row['project_name'] or row['project_id'] or 'project'}: {qty} h at {row['currency']} {row['rate']:,.2f}/{unit_word}"
        out.append(row)
    out.sort(key=lambda r: (r["project_name"] or "", r["employee_name"] or ""))
    return out


def invoice_totals(lines: list[dict], tax_pct: float = 0.0) -> dict:
    amount = r2(sum(float(l.get("amount") or 0) for l in lines))
    tax = r2(amount * float(tax_pct or 0) / 100)
    return {"amount": amount, "tax": tax, "total": r2(amount + tax)}


def next_invoice_number(existing: list[str], year: int, prefix: str = "INV") -> str:
    """INV-2026-0001, 0002 … — the first number not already used this year."""
    used = set(existing or [])
    n = 1
    while True:
        cand = f"{prefix}-{year}-{n:04d}"
        if cand not in used:
            return cand
        n += 1


def due_date(issue: date, payment_terms: str | None) -> date:
    t = (payment_terms or "").strip().lower()
    if "receipt" in t:
        return issue
    digits = "".join(ch for ch in t if ch.isdigit())
    return issue + timedelta(days=int(digits) if digits else 30)


# ── leakage & revenue ─────────────────────────────────────────────────────────
def leakage_summary(priced_uninvoiced: list[dict], invoices: list[dict]) -> dict:
    by_client: dict = defaultdict(lambda: {"hours": 0.0, "amount": 0.0, "lines": 0, "missing_rate": 0})
    by_project: dict = defaultdict(lambda: {"hours": 0.0, "amount": 0.0, "lines": 0, "missing_rate": 0})
    by_emp: dict = defaultdict(lambda: {"hours": 0.0, "amount": 0.0, "lines": 0, "missing_rate": 0})
    for l in priced_uninvoiced:
        for bucket, key in ((by_client, l.get("client") or "—"), (by_project, l.get("project_name") or l.get("project_id") or "—"),
                            (by_emp, l.get("employee_name") or str(l.get("employee_id")))):
            b = bucket[key]
            b["hours"] = r2(b["hours"] + float(l.get("hours") or 0)); b["amount"] = r2(b["amount"] + float(l.get("amount") or 0))
            b["lines"] += 1; b["missing_rate"] += 1 if l.get("missing_rate") else 0
    unpaid = []
    for inv in invoices:
        total, paid = float(inv.get("total") or 0), float(inv.get("paid_amount") or 0)
        if inv.get("status") in ("sent", "overdue") or (inv.get("status") != "paid" and total > paid and inv.get("status") != "draft"):
            unpaid.append({**inv, "outstanding": r2(total - paid)})
    return {"uninvoiced": {"hours": r2(sum(float(l.get("hours") or 0) for l in priced_uninvoiced)),
                           "amount": r2(sum(float(l.get("amount") or 0) for l in priced_uninvoiced)),
                           "lines": len(priced_uninvoiced),
                           "missing_rate": sum(1 for l in priced_uninvoiced if l.get("missing_rate"))},
            "by_client": [{"client": k, **v} for k, v in sorted(by_client.items())],
            "by_project": [{"project": k, **v} for k, v in sorted(by_project.items())],
            "by_employee": [{"employee": k, **v} for k, v in sorted(by_emp.items())],
            "invoiced_unpaid": unpaid,
            "outstanding_total": r2(sum(u["outstanding"] for u in unpaid))}


def fx_convert(amount: float, currency: str, home: str, rates: dict) -> tuple[float, bool]:
    """(amount in home currency, converted?). Same currency → unchanged. No rate → (amount, False)."""
    cur = (currency or home or "").upper()
    if not cur or cur == (home or "").upper():
        return r2(amount), True
    r = rates.get(cur)
    if r is None:
        return r2(amount), False
    return r2(float(amount) * float(r)), True


def revenue_rows(lines: list[dict], invoices_by_id: dict, home: str, rates: dict, group: str = "client") -> list[dict]:
    """Invoice lines → revenue by client | project | month (month of the invoice issue date).
    Amounts in the home currency where a manual FX rate exists; rows without one are flagged."""
    g: dict = defaultdict(lambda: {"amount_home": 0.0, "hours": 0.0, "lines": 0, "unconverted": 0, "paid_home": 0.0,
                                   "invoices": set()})
    for l in lines:
        inv = invoices_by_id.get(str(l.get("invoice_id"))) or {}
        if inv.get("is_active") is False:
            continue
        key = {"client": inv.get("client") or "—", "project": l.get("project_name") or l.get("project_id") or "—",
               "month": (inv.get("issue_date") or "")[:7] or "—"}.get(group, inv.get("client") or "—")
        amt, ok = fx_convert(float(l.get("amount") or 0), l.get("currency") or inv.get("currency") or home, home, rates)
        b = g[key]
        b["amount_home"] = r2(b["amount_home"] + amt); b["hours"] = r2(b["hours"] + float(l.get("hours") or 0))
        b["lines"] += 1; b["unconverted"] += 0 if ok else 1; b["invoices"].add(str(l.get("invoice_id")))
        if inv.get("status") == "paid":
            b["paid_home"] = r2(b["paid_home"] + amt)
    out = []
    for k, v in sorted(g.items()):
        out.append({group: k, "amount": v["amount_home"], "paid": v["paid_home"], "hours": v["hours"],
                    "lines": v["lines"], "invoices": len(v["invoices"]), "unconverted_lines": v["unconverted"]})
    return out
