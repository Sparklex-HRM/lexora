"""Lexora HRM — server-side role gates (one vocabulary for every service).

Built on the shared role families in ``shared.utils.directory`` ("hr" covers hr_admin /
hr_manager / hr_staff, "payroll" covers payroll_admin, …) and the stored keys in
``shared.utils.identity``. The role comes from the gateway-stamped ``X-Role`` header
(clients cannot set it — the gateway scrubs it). ``superadmin`` passes every gate.

    from shared.utils.roles import require_roles, HR_WRITE, OFFICE, ALL_STAFF

    require_roles(request, HR_WRITE, "add a branch")        # 403 otherwise

Groups mirror the frontend (frontend/src/store/authStore.ts):
    HR_WRITE   org_admin + HR                      manage people / masters
    OFFICE     org_admin + HR + payroll            back-office readers
    FINANCE    org_admin + payroll (+ finance)     money out / invoices
    APPROVERS  OFFICE + manager
    ALL_STAFF  any signed-in role, incl. employee
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from shared.utils.directory import role_in

SUPERADMIN = "superadmin"

HR_WRITE: tuple[str, ...] = ("org_admin", "hr")
OFFICE: tuple[str, ...] = ("org_admin", "hr", "payroll")
FINANCE: tuple[str, ...] = ("org_admin", "payroll", "finance")
APPROVERS: tuple[str, ...] = OFFICE + ("manager",)
ALL_STAFF: tuple[str, ...] = ("*",)


def role_of(request: Request) -> str:
    return (request.headers.get("x-role") or "").strip().lower()


def has_role(role: str | None, allowed) -> bool:
    role = (role or "").strip().lower()
    if not role:
        return False
    if role == SUPERADMIN or "*" in (allowed or ()):
        return True
    return role_in(role, list(allowed or ()))


DATA_CLASSES = ("pay", "identity", "personal", "audit")


def acting_custom_role(request: Request) -> str:
    """The custom role key acting on this request ('' = the login's own system role). Gateway-owned header."""
    return (request.headers.get("x-acting-role") or "").strip()


def holds_data(request: Request, data_class: str) -> bool:
    """Org RBAC (2026-10-26): may this caller see FIELDS of a sensitive data class ('pay', 'identity', 'personal',
    'audit') that its role would otherwise show?

    * the login's own system role acts -> True: the role checks decide exactly as before;
    * a CUSTOM role acts (gateway rbac_gate stamped X-Acting-Role + X-Acting-Data) -> only when that custom role
      carries `data.<class>`. The custom role inherits its acting system role's screens, never its data classes.
    Fail closed: an acting custom role without X-Acting-Data holds no data class."""
    if not acting_custom_role(request):
        return True
    held = {c.strip() for c in (request.headers.get("x-acting-data") or "").split(",") if c.strip()}
    return data_class in held


class RequestRole(str):
    """The X-Role string, carrying which sensitive data classes the caller holds (`holds("pay")`). Compares and
    hashes like the plain role, so existing role checks are unchanged; field-level checks (`can_see_pay`) ask it."""
    held: frozenset | None = None              # None = the login's own system role (role checks decide)

    def holds(self, data_class: str) -> bool:
        return self.held is None or data_class in self.held


def request_role(request: Request, role: str | None = None) -> RequestRole:
    r = RequestRole(role if role is not None else (request.headers.get("x-role") or ""))
    if acting_custom_role(request):
        r.held = frozenset(c for c in DATA_CLASSES if holds_data(request, c))
    return r


def role_holds(role, data_class: str) -> bool:
    """False only for a RequestRole of an acting custom role that lacks `data.<class>`."""
    h = getattr(role, "holds", None)
    return True if h is None else bool(h(data_class))


def require_roles(request: Request, allowed, what: str = "do this") -> str:
    """403 unless the caller's role is in ``allowed`` (group names / role keys). Returns the role."""
    role = role_of(request)
    if not role:
        raise HTTPException(401, "Sign in again — no role on this request")
    if not has_role(role, allowed):
        raise HTTPException(403, f"Your role cannot {what}")
    return role
