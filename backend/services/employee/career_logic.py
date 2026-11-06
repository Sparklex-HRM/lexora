"""Career changes + probation — the pure rules (no database).

DEFAULT_PROBATION_MONTHS is an HR policy default an org changes in settings. It is
not a statutory figure.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

DEFAULT_PROBATION_MONTHS = 6

# The employee fields that change ONLY through a career-change record.
GOVERNED_FIELDS = {
    "job_title": "Designation", "department": "Department", "grade": "Grade",
    "manager_id": "Manager", "work_location": "Location", "division": "Division",
    "cost_center": "Cost centre",
}
# manager_name travels with manager_id; grade lives in a column this stream added.
MODEL_FIELDS = ("job_title", "department", "manager_id", "manager_name", "work_location", "division", "cost_center")
EXTRA_FIELDS = ("grade",)

# type → (label, the fields of which at least ONE must change)
TYPES: dict[str, tuple[str, tuple]] = {
    "promotion": ("Promotion", ("job_title", "grade")),
    "transfer": ("Transfer", ("department", "work_location", "division", "cost_center")),
    "re_designation": ("Re-designation", ("job_title",)),
    "manager_change": ("Manager change", ("manager_id",)),
    "location_change": ("Location change", ("work_location",)),
    "department_change": ("Department change", ("department",)),
    "grade_change": ("Grade change", ("grade",)),
    # D4 writes this change type from /employees/{id}/entity-transfer; D1 gives it a letter (entity_transfer template)
    "entity_transfer": ("Legal entity transfer", ("entity_id",)),
}
LETTER_FOR_TYPE = {"promotion": "promotion", "transfer": "transfer", "location_change": "transfer",
                   "department_change": "transfer", "entity_transfer": "entity_transfer"}

OPEN_STATUSES = ("draft", "submitted", "approved")


def clean_new_values(change_type: str, new_values: dict | None, old: dict) -> tuple[dict, list[str]]:
    """Keep only governed fields that really change. Returns (values, errors)."""
    errs: list[str] = []
    if change_type not in TYPES:
        return {}, [f"Type must be one of: {', '.join(TYPES)}."]
    out: dict = {}
    for k, v in (new_values or {}).items():
        if k == "manager_name":
            continue
        if k not in GOVERNED_FIELDS:
            errs.append(f"'{k}' cannot be changed by a career change.")
            continue
        v = (str(v).strip() if v is not None else "") or None
        if (old.get(k) or None) != v:
            out[k] = v
    need = TYPES[change_type][1]
    if not errs and not any(k in out for k in need):
        labels = " or ".join(GOVERNED_FIELDS[k].lower() for k in need)
        errs.append(f"A {TYPES[change_type][0].lower()} must change the {labels}.")
    return out, errs


def capture_old(current: dict, new_values: dict) -> dict:
    """The employee's values BEFORE the change, for exactly the fields being changed
    (plus the manager's name when the manager changes)."""
    old = {k: current.get(k) for k in new_values if k in GOVERNED_FIELDS}
    if "manager_id" in new_values:
        old["manager_name"] = current.get("manager_name")
    return old


def governed_diff(before: dict | None, now: dict) -> dict:
    """Governed fields whose value differs between a stored snapshot and the record now."""
    before = before or {}
    return {k: now.get(k) for k in GOVERNED_FIELDS if (now.get(k) or None) != (before.get(k) or None)}


def infer_type(changed: dict) -> str:
    """The closest change type for a set of changed fields (used when a change was
    typed straight onto the employee record and has to be recorded after the fact)."""
    keys = {k for k in changed if k in GOVERNED_FIELDS}
    single = {"grade": "grade_change", "job_title": "re_designation", "manager_id": "manager_change",
              "work_location": "location_change", "department": "department_change"}
    if len(keys) == 1 and next(iter(keys)) in single:
        return single[next(iter(keys))]
    if keys <= {"job_title", "grade"}:
        return "re_designation"
    return "transfer"


def decision_error(status: str, action: str, *, is_maker: bool, role: str, approver_roles: set) -> str | None:
    """Why this action is refused (None = allowed). Maker can never approve their own."""
    if action == "submit":
        return None if status == "draft" else "Only a draft can be submitted."
    if action in ("approve", "reject"):
        if status != "submitted":
            return "Only a submitted record can be approved or rejected."
        if role not in approver_roles:
            return "Only the HR head or org admin can approve."
        if is_maker and action == "approve":
            return "You created this record. Someone else must approve it."
        return None
    if action == "cancel":
        if status == "applied":
            return "This change is already applied. Reverse it with a new change."
        return None if status in OPEN_STATUSES else "This record is already closed."
    return "That is not allowed."


def is_due(status: str, effective_date: date, today: date) -> bool:
    return status == "approved" and effective_date <= today


# ── probation ─────────────────────────────────────────────────────────────────
def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + int(months), 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def probation_end(hire_date: date, months: int) -> date:
    """LAST day of probation: joining 1 Jan + 6 months → 30 Jun."""
    return add_months(hire_date, months) - timedelta(days=1)


def probation_months_for(settings: dict | None, grade: str | None = None, employment_type: str | None = None) -> int:
    """Months for one employee: the most specific matching rule, else the org default."""
    s = settings or {}
    best, best_score = None, -1
    for r in s.get("probation_rules") or []:
        g, t = (r.get("grade") or "").strip().lower(), (r.get("employment_type") or "").strip().lower()
        if g and g != (grade or "").strip().lower():
            continue
        if t and t != (employment_type or "").strip().lower():
            continue
        score = (2 if g else 0) + (1 if t else 0)
        if score > best_score:
            best, best_score = r, score
    try:
        months = int(best["months"]) if best else int(s.get("probation_months") or DEFAULT_PROBATION_MONTHS)
    except (TypeError, ValueError, KeyError):
        months = DEFAULT_PROBATION_MONTHS
    return max(months, 0)


def probation_bucket(end_date: date, today: date) -> tuple[str, int]:
    """('overdue' | '7' | '15' | '30' | 'later', days left — negative when overdue)."""
    left = (end_date - today).days
    if left < 0:
        return "overdue", left
    for lim in (7, 15, 30):
        if left <= lim:
            return str(lim), left
    return "later", left
