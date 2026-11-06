"""Auth service — SCIM 2.0 pure pieces (RFC 7643 / 7644 subset).

Resources:  /Users  (list with ``filter``, get, create, replace, patch, delete=deactivate)
            /Groups (one group per assignable role; members patch = role change)
Everything that touches the database goes through ``shared/utils/identity.py``
(the same functions the Users screen and the onboarding approval use).
"""
from __future__ import annotations

import hashlib
import re
import secrets

from shared.utils import identity

SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCHEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

# Groups the IdP may push people into (never an admin through SCIM).
GROUP_ROLES = ("employee", "manager", "hr_admin", "hr_manager", "payroll_admin")


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type

    def body(self) -> dict:
        out = {"schemas": [SCHEMA_ERROR], "status": str(self.status), "detail": self.detail}
        if self.scim_type:
            out["scimType"] = self.scim_type
        return out


def new_token() -> tuple[str, str]:
    raw = "scim_" + secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


# ── filter ───────────────────────────────────────────────────────────────────

_FILTER = re.compile(r'^\s*([A-Za-z][A-Za-z0-9_.:$-]*)\s+(eq|co|sw)\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE)
_ATTR_ALIAS = {"username": "email", "emails.value": "email", "emails": "email", "externalid": "external_id",
               "displayname": "name", "name.givenname": "first_name", "name.familyname": "last_name",
               "active": "active", "id": "id",
               f"{SCHEMA_ENTERPRISE}:employeenumber".lower(): "employee_number", "employeenumber": "employee_number"}


def parse_filter(expr: str | None) -> tuple[str, str, str] | None:
    """'userName eq "a@b.c"' → ('email', 'eq', 'a@b.c'). None when no filter. Raises on anything else."""
    if not expr or not expr.strip():
        return None
    m = _FILTER.match(expr)
    if not m:
        raise ScimError(400, "Only filters of the form  attribute eq \"value\"  are supported", "invalidFilter")
    attr = _ATTR_ALIAS.get(m.group(1).lower())
    if not attr:
        raise ScimError(400, f"Cannot filter on {m.group(1)}", "invalidFilter")
    return attr, m.group(2).lower(), m.group(3).replace('\\"', '"')


def matches(row: dict, flt: tuple[str, str, str] | None) -> bool:
    if not flt:
        return True
    attr, op, val = flt
    have = row.get(attr)
    if attr == "active":
        return str(bool(have)).lower() == val.lower()
    have, val = str(have or "").lower(), val.lower()
    return {"eq": have == val, "co": val in have, "sw": have.startswith(val)}[op]


# ── mapping ──────────────────────────────────────────────────────────────────

def to_scim_user(u: dict, *, base: str, employee: dict | None = None, external_id: str | None = None) -> dict:
    out = {"schemas": [SCHEMA_USER, SCHEMA_ENTERPRISE], "id": u["id"], "userName": u["email"],
           "name": {"givenName": u.get("first_name") or "", "familyName": u.get("last_name") or "",
                    "formatted": u.get("name") or u["email"]},
           "displayName": u.get("name") or u["email"],
           "emails": [{"value": u["email"], "type": "work", "primary": True}],
           "active": bool(u.get("is_active")),
           "groups": [{"value": u.get("role"), "display": identity.ROLE_LABELS.get(u.get("role"), u.get("role"))}],
           SCHEMA_ENTERPRISE: {"employeeNumber": (employee or {}).get("employee_number") or "",
                               "department": (employee or {}).get("department") or ""},
           "meta": {"resourceType": "User", "location": f"{base}/Users/{u['id']}",
                    "created": u.get("created_at"), "lastModified": u.get("created_at")}}
    if external_id:
        out["externalId"] = external_id
    return out


def to_scim_group(role: str, members: list[dict], *, base: str) -> dict:
    return {"schemas": [SCHEMA_GROUP], "id": role, "displayName": identity.ROLE_LABELS.get(role, role),
            "members": [{"value": m["id"], "display": m.get("name") or m.get("email")} for m in members],
            "meta": {"resourceType": "Group", "location": f"{base}/Groups/{role}"}}


def list_response(items: list, total: int, start: int, count: int) -> dict:
    return {"schemas": [SCHEMA_LIST], "totalResults": total, "startIndex": start, "itemsPerPage": count,
            "Resources": items}


def from_scim_user(body: dict) -> dict:
    """SCIM User → {email, first_name, last_name, employee_number, active, external_id}."""
    body = body or {}
    emails = body.get("emails") or []
    primary = next((e for e in emails if isinstance(e, dict) and e.get("primary")), None) or \
        (emails[0] if emails and isinstance(emails[0], dict) else None)
    email = body.get("userName") or (primary or {}).get("value")
    name = body.get("name") or {}
    ent = body.get(SCHEMA_ENTERPRISE) or {}
    return {"email": (email or "").strip().lower(), "first_name": (name.get("givenName") or "").strip() or None,
            "last_name": (name.get("familyName") or "").strip() or None,
            "employee_number": (ent.get("employeeNumber") or "").strip() or None,
            "active": body.get("active", True) is not False,
            "external_id": body.get("externalId"),
            "display_name": body.get("displayName")}


def apply_patch(current: dict, body: dict) -> dict:
    """Return the patched {email, first_name, last_name, active, employee_number} (RFC 7644 §3.5.2 subset)."""
    out = dict(current)
    for op in (body or {}).get("Operations") or []:
        kind = (op.get("op") or "").lower()
        path = (op.get("path") or "").strip()
        value = op.get("value")
        if kind not in ("replace", "add", "remove"):
            raise ScimError(400, f"Unsupported op {op.get('op')}", "invalidValue")
        if not path:
            if not isinstance(value, dict):
                raise ScimError(400, "A value object is needed when no path is given", "invalidValue")
            merged = from_scim_user({**{"active": out.get("active")}, **value})
            for k in ("first_name", "last_name"):
                if value.get("name") and value["name"].get("givenName" if k == "first_name" else "familyName") is not None:
                    out[k] = merged[k]
            if "active" in value:
                out["active"] = value["active"] is not False
            if value.get("userName") or value.get("emails"):
                out["email"] = merged["email"]
            if value.get(SCHEMA_ENTERPRISE, {}).get("employeeNumber"):
                out["employee_number"] = merged["employee_number"]
            continue
        p = path.lower().replace(f"{SCHEMA_ENTERPRISE.lower()}:", "")
        if p == "active":
            out["active"] = (value is True or str(value).lower() == "true") if kind != "remove" else False
        elif p in ("name.givenname",):
            out["first_name"] = None if kind == "remove" else (value or "").strip()
        elif p in ("name.familyname",):
            out["last_name"] = None if kind == "remove" else (value or "").strip()
        elif p in ("username",) or p.startswith("emails"):
            if kind != "remove":
                v = value[0]["value"] if isinstance(value, list) and value and isinstance(value[0], dict) else value
                out["email"] = str(v or "").strip().lower()
        elif p == "employeenumber":
            out["employee_number"] = None if kind == "remove" else (value or "").strip()
        else:
            pass                                       # attributes Lexora HRM does not keep are ignored, not refused
    return out


def group_patch_members(body: dict) -> tuple[list[str], list[str]]:
    """(user ids added, user ids removed) from a Group PatchOp."""
    added, removed = [], []
    for op in (body or {}).get("Operations") or []:
        kind = (op.get("op") or "").lower()
        path = (op.get("path") or "").strip().lower()
        vals = op.get("value")
        ids = []
        if isinstance(vals, dict) and vals.get("members"):
            vals = vals["members"]
        if isinstance(vals, list):
            ids = [str(v.get("value")) for v in vals if isinstance(v, dict) and v.get("value")]
        m = re.match(r'^members\[value eq "([^"]+)"\]$', path)
        if m:
            ids = [m.group(1)]
        if kind in ("add", "replace") and (path in ("members", "") or path.startswith("members")):
            added += ids
        elif kind == "remove" and path.startswith("members"):
            removed += ids
    return added, removed
