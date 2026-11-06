"""Custom fields — the pure rules (no database, no HTTP).

* which entities / types exist and what a definition must look like
* value validation + coercion per type (regex, min/max, options, dates)
* who may SEE and who may EDIT a field (role families from shared.utils.roles)
"""
from __future__ import annotations

import re
from datetime import date

from shared.utils.roles import has_role

ENTITIES: dict[str, dict] = {
    "employee": {"label": "Employee", "table": "employees", "owner_col": "id"},
    "candidate": {"label": "Candidate", "table": "hire_candidates", "owner_col": None},
    "asset": {"label": "Asset", "table": "employee_assets", "owner_col": "employee_id"},
}
TYPES = ("text", "number", "date", "select", "multiselect", "boolean", "file", "lookup")
TYPE_LABELS = {"text": "Text", "number": "Number", "date": "Date", "select": "Choice (one)", "multiselect": "Choices (many)",
               "boolean": "Yes / no", "file": "File reference", "lookup": "Lookup (master list)"}
LOOKUPS = ("employees", "departments", "designations", "locations", "grades", "branches", "cost_centers")
ROLE_CHOICES = [("org_admin", "Org admin"), ("hr", "HR (all HR roles)"), ("hr_admin", "HR head"), ("hr_manager", "HR manager"),
                ("hr_staff", "HR staff"), ("payroll", "Payroll"), ("finance", "Finance"), ("manager", "Manager"), ("employee", "Employee (self)")]
DEFAULT_EDITORS = ("org_admin", "hr")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_TEXT = 2000


class FieldError(ValueError):
    pass


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")[:40]
    return s if _KEY_RE.match(s or "") else ("f_" + s)[:40] if s else ""


def validate_definition(d: dict, *, is_new: bool) -> dict:
    """Normalise a definition from the screen. Raises FieldError with a plain sentence."""
    out = {}
    entity = str(d.get("entity") or "").strip().lower()
    if entity not in ENTITIES:
        raise FieldError("Pick an entity: employee, candidate, asset or expense")
    out["entity"] = entity
    label = str(d.get("label") or "").strip()
    if not label:
        raise FieldError("Give the field a label")
    out["label"] = label[:120]
    key = str(d.get("key") or "").strip().lower() or slug(label)
    if not _KEY_RE.match(key):
        raise FieldError("The key must start with a letter and use only lowercase letters, digits and underscores (max 40)")
    out["key"] = key
    t = str(d.get("type") or "text").strip().lower()
    if t not in TYPES:
        raise FieldError(f"Unknown type '{t}'")
    out["type"] = t
    opts = d.get("options")
    if t in ("select", "multiselect"):
        if isinstance(opts, str):
            opts = [x.strip() for x in opts.split(",")]
        opts = [str(o).strip() for o in (opts or []) if str(o).strip()]
        if not opts:
            raise FieldError("A choice field needs at least one option")
        if len(opts) != len(set(opts)):
            raise FieldError("Options must be different from each other")
        out["options"] = opts[:200]
    elif t == "lookup":
        src = (opts[0] if isinstance(opts, list) and opts else opts if isinstance(opts, str) else "") or ""
        src = str(src).strip().lower()
        if src not in LOOKUPS:
            raise FieldError("A lookup field needs a master list: " + ", ".join(LOOKUPS))
        out["options"] = [src]
    else:
        out["options"] = None
    v = d.get("validation") if isinstance(d.get("validation"), dict) else {}
    val: dict = {}
    if v.get("regex"):
        try:
            re.compile(str(v["regex"]))
        except re.error:
            raise FieldError("The pattern is not a valid regular expression")
        val["regex"] = str(v["regex"])[:200]
    for k in ("min", "max"):
        if v.get(k) not in (None, ""):
            if t == "date":
                try:
                    date.fromisoformat(str(v[k])[:10])
                except ValueError:
                    raise FieldError(f"{k} must be a date (YYYY-MM-DD)")
                val[k] = str(v[k])[:10]
            else:
                try:
                    val[k] = float(v[k])
                except (TypeError, ValueError):
                    raise FieldError(f"{k} must be a number")
    if "min" in val and "max" in val and val["min"] > val["max"]:
        raise FieldError("min cannot be more than max")
    if v.get("help"):
        val["help"] = str(v["help"])[:300]
    out["validation"] = val or None
    for k in ("visible_to", "editable_by"):
        roles = d.get(k) or []
        if isinstance(roles, str):
            roles = [x.strip() for x in roles.split(",")]
        known = {r for r, _ in ROLE_CHOICES}
        roles = [str(r).strip() for r in roles if str(r).strip()]
        bad = [r for r in roles if r not in known]
        if bad:
            raise FieldError(f"Unknown role(s): {', '.join(bad)}")
        out[k] = roles
    out["required"] = bool(d.get("required"))
    out["section"] = (str(d.get("section") or "").strip()[:60]) or None
    try:
        out["sort_order"] = int(d.get("sort_order") if d.get("sort_order") not in (None, "") else 100)
    except (TypeError, ValueError):
        raise FieldError("Order must be a whole number")
    out["show_in_grid"] = bool(d.get("show_in_grid", True))
    out["active"] = bool(d.get("active", True))
    return out


# ── values ────────────────────────────────────────────────────────────────────

def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def coerce_value(defn: dict, v):
    """Return the stored form of ``v`` for this definition, or raise FieldError."""
    t, label = defn["type"], defn.get("label") or defn["key"]
    val = defn.get("validation") or {}
    if _empty(v):
        if defn.get("required"):
            raise FieldError(f"{label} is required")
        return None
    if t == "text" or t == "file":
        s = str(v).strip()
        if len(s) > MAX_TEXT:
            raise FieldError(f"{label} is too long (max {MAX_TEXT} characters)")
        if val.get("regex") and not re.fullmatch(val["regex"], s):
            raise FieldError(f"{label} does not match the required pattern" + (f" ({val['help']})" if val.get("help") else ""))
        if val.get("min") is not None and len(s) < val["min"]:
            raise FieldError(f"{label} needs at least {int(val['min'])} characters")
        if val.get("max") is not None and len(s) > val["max"]:
            raise FieldError(f"{label} can have at most {int(val['max'])} characters")
        return s
    if t == "number":
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise FieldError(f"{label} must be a number")
        if val.get("min") is not None and n < val["min"]:
            raise FieldError(f"{label} must be at least {val['min']:g}")
        if val.get("max") is not None and n > val["max"]:
            raise FieldError(f"{label} must be at most {val['max']:g}")
        return int(n) if n == int(n) else n
    if t == "date":
        try:
            d = date.fromisoformat(str(v)[:10])
        except ValueError:
            raise FieldError(f"{label} must be a date (YYYY-MM-DD)")
        if val.get("min") and d < date.fromisoformat(val["min"]):
            raise FieldError(f"{label} cannot be before {val['min']}")
        if val.get("max") and d > date.fromisoformat(val["max"]):
            raise FieldError(f"{label} cannot be after {val['max']}")
        return d.isoformat()
    if t == "select":
        s = str(v).strip()
        if s not in (defn.get("options") or []):
            raise FieldError(f"{label}: '{s}' is not one of the choices")
        return s
    if t == "multiselect":
        items = v if isinstance(v, list) else [x.strip() for x in str(v).split(",")]
        items = [str(x).strip() for x in items if str(x).strip()]
        bad = [x for x in items if x not in (defn.get("options") or [])]
        if bad:
            raise FieldError(f"{label}: {', '.join(bad)} is not one of the choices")
        return list(dict.fromkeys(items))
    if t == "boolean":
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "yes", "1", "y"):
            return True
        if s in ("false", "no", "0", "n"):
            return False
        raise FieldError(f"{label} must be yes or no")
    if t == "lookup":
        s = str(v).strip()
        if len(s) > 200:
            raise FieldError(f"{label} is too long")
        return s
    raise FieldError(f"{label}: unknown type")


def can_see(defn: dict, role: str | None) -> bool:
    vis = defn.get("visible_to") or []
    return (not vis) or has_role(role, vis)


def can_edit(defn: dict, role: str | None) -> bool:
    ed = defn.get("editable_by") or list(DEFAULT_EDITORS)
    return has_role(role, ed)


def visible_defs(defs: list[dict], role: str | None, *, include_inactive=False) -> list[dict]:
    return [d for d in defs if (include_inactive or d.get("active", True)) and can_see(d, role)]


def apply_values(defs: list[dict], current: dict | None, patch: dict, role: str | None) -> tuple[dict, list[str]]:
    """Merge ``patch`` ({key: value}) into ``current`` under this role's rights.
    Returns (new custom_data, changed keys). Raises FieldError on the first problem."""
    by_key = {d["key"]: d for d in defs if d.get("active", True)}
    out = dict(current or {})
    changed = []
    for k, v in (patch or {}).items():
        d = by_key.get(k)
        if not d:
            raise FieldError(f"'{k}' is not a custom field here")
        if not can_see(d, role) or not can_edit(d, role):
            raise FieldError(f"Your role cannot change {d.get('label') or k}")
        new = coerce_value(d, v)
        if out.get(k) != new:
            changed.append(k)
        if new is None:
            out.pop(k, None)
        else:
            out[k] = new
    return out, changed


def visible_values(defs: list[dict], data: dict | None, role: str | None) -> dict:
    """Only the keys this role may see (and that are still defined)."""
    data = data or {}
    return {d["key"]: data.get(d["key"]) for d in visible_defs(defs, role) if d["key"] in data}


def missing_required(defs: list[dict], data: dict | None, role: str | None) -> list[str]:
    data = data or {}
    return [d["key"] for d in visible_defs(defs, role) if d.get("required") and _empty(data.get(d["key"]))]


def display_value(defn: dict, v) -> str:
    if _empty(v):
        return ""
    if defn["type"] == "boolean":
        return "Yes" if v else "No"
    if defn["type"] == "multiselect" and isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)
