"""Document centre — pure rules (no DB, no FastAPI). Tested in test_ess_portal_b3_docs.py."""
import csv
import io
from datetime import date

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
STATUSES = ("published", "archived")
MAX_TITLE, MAX_CATEGORY, MAX_LABEL, MAX_BODY, MAX_UA = 200, 60, 40, 100_000, 400


def is_hr(role: str | None) -> bool:
    return (role or "").lower() in HR_ROLES


def clean(s, n: int) -> str | None:
    s = (s or "").strip() if isinstance(s, str) else (str(s).strip() if s is not None else "")
    return s[:n] or None


def validate_version(version_label, effective_date, body, document_id) -> str | None:
    """What a version needs: a label, and something to read (text and/or a PDF)."""
    if not clean(version_label, MAX_LABEL):
        return "Give the version a label, for example v1 or 2026-27"
    if not clean(body, MAX_BODY) and not document_id:
        return "Add the policy text or attach a PDF"
    if body and len(str(body)) > MAX_BODY:
        return "The policy text is too long — attach it as a PDF instead"
    if effective_date is not None and not isinstance(effective_date, date):
        return "The effective date is not a valid date"
    return None


def validate_policy(title, category, version_label, effective_date, body, document_id) -> str | None:
    if not clean(title, MAX_TITLE):
        return "Give the policy a title"
    if category is not None and len(str(category)) > MAX_CATEGORY:
        return "The category is too long"
    return validate_version(version_label, effective_date, body, document_id)


def history_entry(p) -> dict:
    """The current version of a policy, as it goes into `history` when a new one replaces it."""
    return {"version_no": p.version_no, "version_label": p.version_label,
            "effective_date": p.effective_date.isoformat() if p.effective_date else None,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "published_by": p.published_by, "document_id": str(p.document_id) if p.document_id else None,
            "file_name": p.file_name, "ack_required": bool(p.ack_required)}


def label_taken(p, label: str) -> bool:
    """A new version must not reuse a label already used on this policy."""
    want = (label or "").strip().lower()
    used = {(p.version_label or "").strip().lower()} | {
        (h.get("version_label") or "").strip().lower() for h in (p.history or [])}
    return want in used


def my_status(p, acked: bool) -> str:
    """acknowledged | pending | not_required | archived — for one employee, on the CURRENT version."""
    if p.status == "archived":
        return "archived"
    if not p.ack_required:
        return "not_required"
    return "acknowledged" if acked else "pending"


def ack_check(p, *, self_id: str | None, body_employee_id: str | None, version_no) -> tuple[int, str] | None:
    """(status, message) when this acknowledgement must be refused, else None.
    Only the person themselves acknowledges, only the current version, only a published policy
    that asks for it."""
    if not self_id:
        return 400, "Your login is not linked to an employee record"
    if body_employee_id and str(body_employee_id) != str(self_id):
        return 403, "You can only acknowledge a policy for yourself"
    if p.status != "published":
        return 404, "Policy not found"
    if not p.ack_required:
        return 409, "This policy does not need an acknowledgement"
    if version_no is not None and int(version_no) != int(p.version_no):
        return 409, "A newer version of this policy has been published — read it and acknowledge again"
    return None


def emp_name(e) -> str:
    return f"{getattr(e, 'first_name', '') or ''} {getattr(e, 'last_name', '') or ''}".strip()


def register(p, employees: list, acks: list) -> dict:
    """Who has / has not acknowledged the CURRENT version. `acks` = rows of this policy (any version)."""
    cur = {str(a.employee_id): a for a in acks if int(a.version_no) == int(p.version_no)}
    rows = []
    for e in employees:
        a = cur.get(str(e.id))
        rows.append({"employee_id": str(e.id), "employee_number": e.employee_number, "name": emp_name(e),
                     "department": e.department, "job_title": getattr(e, "job_title", None),
                     "status": "acknowledged" if a else "pending",
                     "acknowledged_at": a.acknowledged_at.isoformat() if a and a.acknowledged_at else None,
                     "version_label": a.version_label if a else None,
                     "user_agent": a.user_agent if a else None})
    rows.sort(key=lambda r: (r["status"] != "pending", (r["name"] or "").lower()))
    return {"rows": rows, **counts(len(employees), sum(1 for r in rows if r["status"] == "acknowledged"))}


def counts(total: int, acknowledged: int) -> dict:
    acknowledged = min(acknowledged, total)
    return {"total": total, "acknowledged": acknowledged, "pending": total - acknowledged,
            "percent": round(100 * acknowledged / total) if total else 0}


def register_csv(p, rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Policy", "Version", "Employee number", "Name", "Department", "Status", "Acknowledged at", "Browser"])
    for r in rows:
        w.writerow([p.title, p.version_label, r["employee_number"] or "", _safe(r["name"]), _safe(r["department"] or ""),
                    r["status"], r["acknowledged_at"] or "", _safe(r["user_agent"] or "")])
    return buf.getvalue()


def _safe(v: str) -> str:
    """No spreadsheet formulas from a CSV cell."""
    v = str(v or "")
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def remind_text(p) -> tuple[str, str]:
    return (f"Please read and accept: {p.title}",
            f"{p.title} ({p.version_label}) needs your acknowledgement. Open the Document centre → Policies.")
