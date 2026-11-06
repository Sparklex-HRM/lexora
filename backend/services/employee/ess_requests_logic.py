"""Employee portal batch 3 — pure rules for "Raise a request" and "My tasks" (no DB, no HTTP).

* a request type's form is a list of field definitions in the SAME shape as D1 custom fields and
  is validated by the SAME code (custom_fields_logic.validate_definition / coerce_value /
  missing_required / display_value) — nothing is reimplemented here
* who may see / decide / withdraw a request
* to-do validation
* which exit-clearance lines belong to whom, and the merge into one "My tasks" list
"""
from __future__ import annotations

import re
from datetime import date

import custom_fields_logic as CF

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
STATUSES = ("pending", "approved", "rejected", "withdrawn")
MAX_FIELDS = 30
JOINER_OWNERS = {"employee", "new hire", "new joiner", "joiner", "self", "you"}


class RequestError(ValueError):
    pass


def is_hr(role: str | None) -> bool:
    return (role or "").lower() in HR_ROLES


# ── request types ─────────────────────────────────────────────────────────────

def normalise_fields(raw) -> list[dict]:
    """The form of a request type: each field goes through D1's validate_definition (entity is
    fixed to "employee" only because the D1 shape needs one; it is dropped afterwards)."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise RequestError("fields must be a list")
    if len(raw) > MAX_FIELDS:
        raise RequestError(f"A request form can have at most {MAX_FIELDS} fields")
    out, keys = [], set()
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            raise RequestError("Each field must be an object")
        try:
            d = CF.validate_definition({**f, "entity": "employee", "visible_to": [], "editable_by": [],
                                        "sort_order": f.get("sort_order", (i + 1) * 10)}, is_new=True)
        except CF.FieldError as e:
            raise RequestError(f"Field {i + 1}: {e}")
        if d["key"] in keys:
            raise RequestError(f"Two fields share the key '{d['key']}' — give them different labels or keys")
        keys.add(d["key"])
        for k in ("entity", "visible_to", "editable_by", "show_in_grid", "section"):
            d.pop(k, None)
        out.append(d)
    return out


def normalise_type(body: dict, *, existing_key: str | None = None) -> dict:
    """Validate a request type from the admin screen. The key never changes after creation."""
    name = str(body.get("name") or "").strip()
    if not name:
        raise RequestError("Give the request type a name")
    if existing_key:
        key = existing_key
    else:
        key = str(body.get("key") or "").strip().lower() or CF.slug(name)
        if not re.match(r"^[a-z][a-z0-9_]{0,39}$", key or ""):
            raise RequestError("The key must start with a letter and use only lowercase letters, digits and underscores")
    try:
        sort = int(body.get("sort_order") if body.get("sort_order") not in (None, "") else 100)
    except (TypeError, ValueError):
        raise RequestError("Order must be a whole number")
    return {"key": key, "name": name[:120],
            "description": (str(body.get("description") or "").strip()[:600]) or None,
            "category": (str(body.get("category") or "").strip()[:60]) or None,
            "handler_note": (str(body.get("handler_note") or "").strip()[:300]) or None,
            "active": bool(body.get("active", True)), "sort_order": sort,
            "fields": normalise_fields(body.get("fields"))}


# ── values ────────────────────────────────────────────────────────────────────

def clean_values(fields: list[dict], values: dict | None) -> dict:
    """Coerce each answer with D1's coerce_value; unknown keys and missing required answers are refused."""
    values = values or {}
    if not isinstance(values, dict):
        raise RequestError("values must be an object of {field key: answer}")
    active = [f for f in fields if f.get("active", True)]
    by_key = {f["key"]: f for f in active}
    unknown = [k for k in values if k not in by_key]
    if unknown:
        raise RequestError(f"'{unknown[0]}' is not a field of this request")
    out = {}
    for f in active:
        try:
            v = CF.coerce_value(f, values.get(f["key"]))
        except CF.FieldError as e:
            raise RequestError(str(e))
        if v is not None:
            out[f["key"]] = v
    missing = CF.missing_required(active, out, None)
    if missing:
        raise RequestError(f"{by_key[missing[0]].get('label') or missing[0]} is required")
    return out


def file_keys(fields: list[dict], values: dict) -> list[str]:
    return [f["key"] for f in fields if f.get("type") == "file" and values.get(f["key"])]


def display_rows(fields: list[dict], values: dict | None) -> list[dict]:
    values = values or {}
    return [{"key": f["key"], "label": f.get("label") or f["key"], "type": f.get("type"),
             "value": values.get(f["key"]), "display": CF.display_value(f, values.get(f["key"]))}
            for f in fields if f["key"] in values]


def summary_lines(fields: list[dict], values: dict | None, limit: int = 6) -> list[str]:
    out = []
    for r in display_rows(fields, values):
        shown = "attached" if r["type"] == "file" else r["display"]
        out.append(f"{r['label']}: {shown[:160]}")
    return out[:limit]


# ── who may do what ───────────────────────────────────────────────────────────

def may_view(role: str | None, me: str | None, row_employee_id: str, row_manager_id: str | None) -> bool:
    if is_hr(role) or (me and str(row_employee_id) == str(me)):
        return True
    return (role or "") == "manager" and bool(me) and str(row_manager_id or "") == str(me)


def may_decide(role: str | None, me: str | None, row_employee_id: str, row_manager_id: str | None,
               *, actor_user_id: str | None = None, requester_user_id: str | None = None) -> bool:
    """HR, or the employee's own manager — never the person who raised it."""
    if me and str(row_employee_id) == str(me):
        return False
    if actor_user_id and requester_user_id and str(actor_user_id) == str(requester_user_id):
        return False
    if is_hr(role):
        return True
    return (role or "") == "manager" and bool(me) and str(row_manager_id or "") == str(me)


def may_withdraw(me: str | None, row_employee_id: str, status: str) -> tuple[bool, str]:
    if not me or str(row_employee_id) != str(me):
        return False, "Only the person who raised this request can withdraw it"
    if status != "pending":
        return False, f"Only a pending request can be withdrawn (this one is {status})"
    return True, ""


def list_scope(role: str | None, me: str | None) -> str:
    """all (HR) · team (manager: own + direct reports) · self · none (no employee record)."""
    if is_hr(role):
        return "all"
    if not me:
        return "none"
    return "team" if (role or "") == "manager" else "self"


# ── to-dos ────────────────────────────────────────────────────────────────────

def clean_todo(body: dict, *, partial: bool = False) -> dict:
    out = {}
    if not partial or "title" in body:
        t = str(body.get("title") or "").strip()
        if not t:
            raise RequestError("Give the to-do a title")
        out["title"] = t[:200]
    if not partial or "due_date" in body:
        d = body.get("due_date")
        if d in (None, ""):
            out["due_date"] = None
        else:
            try:
                out["due_date"] = date.fromisoformat(str(d)[:10])
            except ValueError:
                raise RequestError("Due date must be a date (YYYY-MM-DD)")
    if not partial or "note" in body:
        out["note"] = (str(body.get("note") or "").strip()[:500]) or None
    if "done" in body:
        out["done"] = bool(body.get("done"))
    return out


# ── exit-clearance lines that are someone's job ──────────────────────────────

def exit_line_is_mine(actor: dict, line: dict, case: dict) -> bool:
    """Lines the actor must clear on SOMEONE ELSE's open exit case: named user, the manager line of
    their direct report, or a line owned by their role (never the generic 'manager' / 'employee')."""
    me, uid, role = str(actor.get("employee_id") or ""), str(actor.get("user_id") or ""), (actor.get("role") or "").lower()
    if me and str(case.get("employee_id")) == me:
        return False                                   # your own case → shown as "My exit clearance"
    if line.get("owner_user_id") and uid and str(line["owner_user_id"]) == uid:
        return True
    if line.get("owner_role") == "manager":
        owner_emp = str(line.get("owner_employee_id") or case.get("manager_id") or "")
        return bool(me) and owner_emp == me
    return bool(role) and role not in ("employee", "manager") and role == (line.get("owner_role") or "").lower()


def _due(v) -> str | None:
    if not v:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)[:10]


def todo_task(t: dict) -> dict:
    return {"id": f"todo:{t['id']}", "ref_id": t["id"], "source": "todo", "source_label": "My to-do",
            "title": t["title"], "detail": t.get("note"), "due_date": _due(t.get("due_date")),
            "done": bool(t.get("done")), "status": "done" if t.get("done") else "open", "editable": True, "link": None}


def own_exit_task(line: dict, case: dict) -> dict:
    done = line.get("status") != "pending"
    return {"id": f"exit-own:{line['id']}", "ref_id": line["id"], "source": "exit_clearance",
            "source_label": "My exit clearance", "title": f"{line.get('department') or 'Clearance'}: {line.get('item')}",
            "detail": f"Cleared by {line.get('owner_name') or line.get('owner_role') or 'HR'}",
            "due_date": _due(case.get("lwd")), "done": done, "status": line.get("status"), "editable": False,
            "link": "/my-resignation"}


def owner_exit_task(line: dict, case: dict) -> dict:
    done = line.get("status") != "pending"
    return {"id": f"exit-owner:{line['id']}", "ref_id": line["id"], "source": "exit_owner",
            "source_label": "Exit clearance to sign off",
            "title": f"{line.get('item')} — {case.get('employee_name') or 'leaving employee'}",
            "detail": f"{line.get('department') or ''} · case {case.get('case_number') or ''}".strip(" ·"),
            "due_date": _due(case.get("lwd")), "done": done, "status": line.get("status"), "editable": False,
            "link": f"/exits/{case.get('id')}"}


def onboarding_task(t: dict, session: dict, *, for_manager: bool) -> dict:
    who = f"{session.get('first_name') or ''} {session.get('last_name') or ''}".strip()
    owner = (t.get("owner") or "").strip()
    return {"id": f"onb:{t['id']}", "ref_id": t["id"], "source": "onboarding",
            "source_label": "Onboarding — new team member" if for_manager else "My onboarding",
            "title": f"{t.get('title')} — {who}" if for_manager else t.get("title"),
            "detail": (f"Done by {owner}" if owner else None) if not for_manager else "Manager task",
            "due_date": _due(t.get("due_date")), "done": bool(t.get("done")),
            "status": "done" if t.get("done") else ("overdue" if t.get("overdue") else "open"),
            "editable": False, "link": "/onboarding" if for_manager else None,
            "yours": for_manager or owner.lower() in JOINER_OWNERS or not owner}


def merge_tasks(*lists: list[dict]) -> list[dict]:
    """Open first, then by due date (no date last), then title."""
    rows = [r for lst in lists for r in (lst or [])]
    return sorted(rows, key=lambda r: (bool(r.get("done")), r.get("due_date") or "9999-12-31", (r.get("title") or "").lower()))


def task_counts(rows: list[dict], today: str) -> dict:
    open_ = [r for r in rows if not r.get("done")]
    return {"total": len(rows), "open": len(open_), "done": len(rows) - len(open_),
            "overdue": sum(1 for r in open_ if r.get("due_date") and r["due_date"] < today),
            "by_source": {s: sum(1 for r in open_ if r["source"] == s) for s in sorted({r["source"] for r in rows})}}
