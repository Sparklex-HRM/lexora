"""Salary revisions — the pure rules (no database, no HTTP).

  resolve_new_amount   new salary | % raise | amount raise → the new figure
  approval_decision    maker ≠ approver · optional two-step above a % or amount
  bulk_review          per-row old / new / difference / warnings + totals + cost impact
  parse_sheet          CSV / XLSX upload → items

Nothing statutory lives here. The only rounding is the final new salary to 2
decimals, because that is the figure stored on the record.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from shared.utils.org_time import local_today  # org-TZ business date (stream S1 helper)

REASONS = {"annual_increment": "Annual increment", "promotion": "Promotion",
           "market_correction": "Market correction", "correction": "Correction", "other": "Other"}
_REASON_WORDS = {v.lower(): k for k, v in REASONS.items()} | {k: k for k in REASONS}
MODES = ("new_annual", "percent", "amount")
MAKER_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}
APPROVER_ROLES = {"superadmin", "org_admin", "hr_admin"}         # HR head / org admin
FINAL_APPROVER_ROLES = {"superadmin", "org_admin"}               # second step of a two-step approval
OPEN_STATUSES = ("draft", "submitted", "approved")
WEEKS_PER_YEAR = 52


def resolve_new_amount(old: float, mode: str, value: float) -> float:
    """The new salary (or hourly rate) from the way HR typed the change."""
    o, v = Decimal(str(old or 0)), Decimal(str(value or 0))
    if mode == "percent":
        new = o * (Decimal(1) + v / Decimal(100))
    elif mode == "amount":
        new = o + v
    elif mode == "new_annual":
        new = v
    else:
        raise ValueError(f"mode must be one of {MODES}")
    # exact decimal maths, rounded ONCE (half-up) to the 2 decimals the record stores
    return float(new.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def change_percent(old: float, new: float) -> float | None:
    return (float(new) - float(old)) * 100.0 / float(old) if float(old or 0) else None


def annual_equivalent(amount: float, pay_type: str, std_hours: float | None) -> float:
    """Annual cost of a rate. Hourly: rate × standard weekly hours × 52 — the same
    convention the Canada run uses for an hourly base."""
    if pay_type == "hourly":
        return float(amount or 0) * float(std_hours or 40) * WEEKS_PER_YEAR
    return float(amount or 0)


def needs_two_step(old: float, new: float, settings: dict | None, pay_type: str = "salary",
                   std_hours: float | None = None) -> bool:
    s = settings or {}
    if not s.get("two_step_enabled"):
        return False
    pct = change_percent(old, new)
    inc = annual_equivalent(new, pay_type, std_hours) - annual_equivalent(old, pay_type, std_hours)
    lim_pct, lim_amt = s.get("two_step_percent"), s.get("two_step_amount")
    if lim_pct is not None and pct is not None and pct >= float(lim_pct):
        return True
    if lim_amt is not None and inc >= float(lim_amt):
        return True
    return lim_pct is None and lim_amt is None       # switched on with no threshold = every revision


def _same(actor: dict, by_id: str | None, by: str | None) -> bool:
    a_id, a_mail = str(actor.get("id") or ""), str(actor.get("email") or "").lower()
    return bool((a_id and by_id and a_id == str(by_id)) or (a_mail and by and a_mail == str(by).lower()))


def approval_decision(rev: dict, actor: dict) -> dict:
    """Can `actor` {id, email, role} approve `rev` {status, created_by, created_by_id,
    needs_two_step, approvals}? → {allowed, error, final, step}.
    `final` False = first of two approvals: the revision stays 'submitted'."""
    no = lambda msg: {"allowed": False, "error": msg, "final": False, "step": 0}  # noqa: E731
    if rev.get("status") != "submitted":
        return no("Only a submitted revision can be approved.")
    role = str(actor.get("role") or "").lower()
    if role not in APPROVER_ROLES:
        return no("Only the HR head or org admin can approve a salary revision.")
    if _same(actor, rev.get("created_by_id"), rev.get("created_by")):
        return no("You created this revision. Someone else must approve it.")
    done = list(rev.get("approvals") or [])
    if not rev.get("needs_two_step"):
        return {"allowed": True, "error": None, "final": True, "step": 1}
    if not done:
        return {"allowed": True, "error": None, "final": False, "step": 1}
    if any(_same(actor, a.get("by_id"), a.get("by")) for a in done):
        return no("You already approved this revision. A second person must give the final approval.")
    if role not in FINAL_APPROVER_ROLES:
        return no("The final approval of this revision must come from the org admin.")
    return {"allowed": True, "error": None, "final": True, "step": 2}


def can_cancel(status: str) -> str | None:
    if status == "applied":
        return "This revision is already applied. Reverse it with a new correcting revision."
    if status not in OPEN_STATUSES:
        return "This revision is already closed."
    return None


# ── bulk review ───────────────────────────────────────────────────────────────
def to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)      # dd-mm-yyyy
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def item_mode(item: dict) -> tuple[str | None, float | None]:
    """Which of new_annual / percent / amount an item carries (in that order)."""
    for key, mode in (("new_annual", "new_annual"), ("percent", "percent"), ("amount", "amount")):
        v = _num(item.get(key))
        if v is not None:
            return mode, v
    return None, None


def normalise_items(items: list[dict], source: str | None = None) -> list[dict]:
    """Make callers' items safe for review. `reason` must be one of REASONS; another
    service may send its own words there (e.g. "Appraisal: FY26 - rating A"). Those
    words are kept in the note, and the reason becomes annual_increment for an
    appraisal / performance source, else other."""
    src = (source or "").lower()
    fallback = "annual_increment" if src.startswith(("apprais", "performance")) else "other"
    out = []
    for raw in items or []:
        it = dict(raw)
        r = str(it.get("reason") or "").strip()
        key = _REASON_WORDS.get(r.lower())
        if r and not key:
            it["note"] = " — ".join(x for x in [r, str(it.get("note") or "").strip()] if x)
            it["reason"] = fallback
        elif key:
            it["reason"] = key
        out.append(it)
    return out


def emp_label(e: dict) -> str:
    return f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or str(e.get("employee_number") or "")


def review_item(item: dict, emp: dict | None, *, today: date, pending: set | None = None,
                seen: set | None = None, default_effective: date | None = None,
                min_wage_fn=None) -> dict:
    """ONE row of the review screen. `errors` stop the row from being saved;
    `warnings` are shown but do not stop it."""
    errors: list[str] = []
    warnings: list[str] = []
    row = {"employee_id": (emp or {}).get("id") or item.get("employee_id"),
           "employee_number": (emp or {}).get("employee_number") or item.get("employee_number"),
           "employee": emp_label(emp) if emp else None, "department": (emp or {}).get("department"),
           "reason": item.get("reason") or "annual_increment", "note": item.get("note"),
           "rating": item.get("rating"), "errors": errors, "warnings": warnings}
    if not emp:
        errors.append("Employee not found.")
        return {**row, "ok": False}
    country = (emp.get("address_country") or "CA").upper()
    pay_type = emp.get("pay_type") or "salary"
    old = float(emp.get("hourly_rate") or 0) if pay_type == "hourly" else float(emp.get("annual_salary") or 0)
    row.update({"country": country, "pay_type": pay_type, "old": old,
                "grade": emp.get("grade"), "job_title": emp.get("job_title")})
    if emp.get("status") == "terminated":
        errors.append("This employee has left.")
    if seen is not None:
        if row["employee_id"] in seen:
            errors.append("This employee is in the list more than once.")
        seen.add(row["employee_id"])
    if row["reason"] not in REASONS:
        errors.append(f"Reason must be one of: {', '.join(REASONS)}.")
    eff = to_date(item.get("effective_from")) or default_effective
    row["effective_from"] = eff.isoformat() if eff else None
    if not eff:
        errors.append("Effective date is missing or not a date.")
    mode, value = item_mode(item)
    row.update({"mode": mode, "value": value})
    if mode is None:
        errors.append("Give a new salary, a % or an amount.")
    if old <= 0:
        errors.append("This employee has no current salary on record.")
    if errors:
        return {**row, "ok": False}
    new = resolve_new_amount(old, mode, value)
    if new <= 0:
        errors.append("The new salary must be more than 0.")
        return {**row, "ok": False}
    std = emp.get("standard_hours_per_week")
    old_a, new_a = annual_equivalent(old, pay_type, std), annual_equivalent(new, pay_type, std)
    row.update({"new": new, "old_annual": old_a, "new_annual": new_a, "diff_annual": new_a - old_a,
                "diff_monthly": (new_a - old_a) / 12.0, "percent": change_percent(old, new)})
    if abs(new - old) < 0.005:
        warnings.append("No change in salary.")
    elif new < old:
        warnings.append("This is a decrease.")
    if eff < today:
        warnings.append("The date is in the past. Any period already paid will be settled as retro pay.")
    if pending and row["employee_id"] in pending:
        warnings.append("Another revision is already waiting for this employee.")
    return {**row, "ok": True}


def review_totals(rows: list[dict]) -> dict:
    """Head count and cost impact (CAD)."""
    cur_of = lambda r: "CAD"  # noqa: E731
    by: dict = {}
    for r in rows:
        if not r.get("ok"):
            continue
        t = by.setdefault(cur_of(r), {"employees": 0, "old_annual": 0.0, "new_annual": 0.0})
        t["employees"] += 1
        t["old_annual"] += r["old_annual"]
        t["new_annual"] += r["new_annual"]
    for t in by.values():
        t["cost_per_year"] = t["new_annual"] - t["old_annual"]
        t["cost_per_month"] = t["cost_per_year"] / 12.0
        t["average_percent"] = (t["cost_per_year"] * 100.0 / t["old_annual"]) if t["old_annual"] else None
        for k in ("old_annual", "new_annual", "cost_per_year", "cost_per_month"):
            t[k] = round(t[k], 2)
        if t["average_percent"] is not None:
            t["average_percent"] = round(t["average_percent"], 2)
    return {"rows": len(rows), "ok": sum(1 for r in rows if r.get("ok")),
            "with_errors": sum(1 for r in rows if r.get("errors")),
            "with_warnings": sum(1 for r in rows if r.get("ok") and r.get("warnings")), "by_currency": by}


def bulk_review(items: list[dict], employees: list[dict], *, today: date | None = None, pending: set | None = None,
                default_effective: date | None = None, min_wage_fn=None) -> dict:
    """Review a whole batch. Employees are matched by id, else by employee number."""
    today = today or local_today()
    by_id = {str(e.get("id")): e for e in employees}
    by_no = {str(e.get("employee_number") or "").strip().lower(): e for e in employees if e.get("employee_number")}
    seen: set = set()
    rows = []
    for it in items or []:
        emp = by_id.get(str(it.get("employee_id") or "")) or by_no.get(str(it.get("employee_number") or "").strip().lower())
        rows.append(review_item(it, emp, today=today, pending=pending, seen=seen,
                                default_effective=default_effective, min_wage_fn=min_wage_fn))
    return {"rows": rows, "totals": review_totals(rows)}


# ── sheet upload ──────────────────────────────────────────────────────────────
_HEADERS = {
    "employee_number": ("employee no", "employee number", "emp no", "emp code", "employee code", "employee id", "employee_number"),
    "new_annual": ("new ctc", "new annual", "new salary", "new annual salary", "new annual ctc", "new_annual"),
    "percent": ("%", "percent", "hike %", "increase %", "hike percent", "percentage"),
    "amount": ("amount", "hike amount", "increase amount", "increase"),
    "effective_from": ("effective date", "effective from", "effective", "effective_from"),
    "reason": ("reason",), "note": ("note", "remarks", "comment"), "rating": ("rating", "rating band"),
}
SHEET_COLUMNS = ["Employee no", "New salary", "%", "Effective date", "Reason", "Note"]


def _header_key(h) -> str | None:
    s = re.sub(r"\s+", " ", str(h or "").strip().lower().replace("_", " "))
    for key, names in _HEADERS.items():
        if s in names or s.replace(" ", "_") == key:
            return key
    return None


def _rows_from(filename: str, data: bytes) -> list[list]:
    if (filename or "").lower().endswith((".xlsx", ".xlsm")):
        from xlsx_lite import read_workbook     # the product's own reader
        book = read_workbook(data)
        return next((rows for rows in book.values() if rows), [])
    text = data.decode("utf-8-sig", errors="replace")
    return [r for r in csv.reader(io.StringIO(text))]


def parse_sheet(filename: str, data: bytes) -> dict:
    """→ {items, errors}. One item per data row: employee_number + (new_annual | percent |
    amount) + effective_from (+ reason, note, rating). Empty rows are skipped."""
    try:
        rows = _rows_from(filename, data)
    except Exception as ex:  # noqa: BLE001
        return {"items": [], "errors": [f"The file could not be read ({type(ex).__name__}). Use CSV or XLSX."]}
    # keep the row number of the FILE, so "Row 6" in a message is row 6 in the sheet
    rows = [(n, r) for n, r in enumerate(rows, start=1) if any(str(c or "").strip() for c in r)]
    if not rows:
        return {"items": [], "errors": ["The file is empty."]}
    keys = [_header_key(h) for h in rows[0][1]]
    if "employee_number" not in keys:
        return {"items": [], "errors": ["The first row must have an 'Employee no' column."]}
    if not ({"new_annual", "percent", "amount"} & set(k for k in keys if k)):
        return {"items": [], "errors": ["The sheet needs a 'New salary', '%' or 'Amount' column."]}
    items, errors = [], []
    for n, r in rows[1:]:
        it: dict = {"row": n}
        for k, cell in zip(keys, r):
            if k is None or cell is None or str(cell).strip() == "":
                continue
            if k in ("new_annual", "percent", "amount"):
                v = _num(cell)
                if v is None:
                    errors.append(f"Row {n}: '{cell}' is not a number.")
                    continue
                it[k] = v
            elif k == "effective_from":
                d = to_date(cell)
                if not d:
                    errors.append(f"Row {n}: '{cell}' is not a date. Use YYYY-MM-DD or DD-MM-YYYY.")
                    continue
                it[k] = d.isoformat()
            elif k == "reason":
                it[k] = _REASON_WORDS.get(str(cell).strip().lower(), str(cell).strip().lower().replace(" ", "_"))
            elif k == "employee_number":
                s = str(cell).strip()
                it[k] = s[:-2] if re.match(r"^\d+\.0$", s) else s      # 1001.0 from a numeric cell
            else:
                it[k] = str(cell).strip()
        if it.get("employee_number"):
            items.append(it)
    return {"items": items, "errors": errors}
