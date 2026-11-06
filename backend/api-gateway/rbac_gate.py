"""API gateway — permission-catalogue enforcement + custom roles (org Roles & Permissions, 2026-10-24).

Every authenticated request is matched to its route row in the compiled catalogue
(backend/shared/utils/rbac_routes.json: required permissions + the system roles the access contract opens it to).

* A login WITHOUT custom roles: LEXORA_RBAC_ENFORCE=all (default) refuses a KNOWN route the contract does not
  open to its system role (403 RBAC_DENIED) before any service is called; the service's own role check stays
  behind it. A route the table does not know yet (added after the last compile) passes to the service.
* A login WITH custom roles: candidates = its system role (if the contract opens the route to it) + each active
  custom role whose permissions cover the route's permissions and whose ACTING system role
  (rbac_catalogue.acting_role: own -> employee, team -> manager, else its base role) the contract opens the route
  to. The widest data reach wins (a tie keeps the system role). A custom role wins -> X-Role is its acting role
  (so the service's checks and data scoping run for that role), X-Acting-Role names the custom role, and a
  department / location / entity reach adds X-Data-Scope (the employee service narrows People by it) — such a
  role only acts on NARROW_REACH_ROUTES. No candidate -> 403 RBAC_DENIED. Deny by default: an unknown route
  never elevates through a custom role.
superadmin passes (platform operator; services check platform routes). LEXORA_RBAC_ENFORCE=custom limits the
gateway check to logins with custom roles; =off disables it (break-glass, logged at boot).
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.append("/app")
from shared.utils import rbac_catalogue as cat  # noqa: E402

log = logging.getLogger("gateway.rbac")

MODE = (os.getenv("LEXORA_RBAC_ENFORCE") or "all").strip().lower()
if MODE not in ("all", "custom", "off"):
    MODE = "all"
if MODE != "all":
    log.warning("LEXORA_RBAC_ENFORCE=%s — gateway catalogue check is %s", MODE,
                "limited to logins with custom roles" if MODE == "custom" else "OFF")

DENY_MESSAGE = "Your role does not allow this. Ask your administrator for access."


class Decision:
    __slots__ = ("deny", "acting_role", "acting_key", "data_scope", "reach", "code", "data_classes")

    def __init__(self, deny: bool = False, acting_role: str | None = None, acting_key: str | None = None,
                 data_scope: str | None = None, reach: str | None = None, code: str | None = None,
                 data_classes: tuple[str, ...] = ()):
        self.deny, self.acting_role, self.acting_key = deny, acting_role, acting_key
        self.data_scope, self.reach, self.code = data_scope, reach, code
        # 2026-10-26: sensitive data classes the ACTING custom role carries (data.pay -> "pay", …). Services hide
        # fields of the classes it lacks (X-Acting-Data) — the acting system role alone would show them.
        self.data_classes = data_classes

    def __repr__(self):  # pragma: no cover
        return (f"Decision(deny={self.deny}, acting={self.acting_role}, key={self.acting_key}, "
                f"scope={self.data_scope})")


PASS = Decision()


def _needed(row: dict, acting: str) -> set[str]:
    need = set(row.get("perms") or [])
    if row.get("scope") == "scoped" and acting in cat.SCOPED_NARROW:
        need = {p for p in need if not p.startswith("data.")}   # the code answers own / team rows only
    return need


def decide(method: str, path: str, role: str | None, state: dict | None, *, mode: str | None = None,
           index: cat.RouteIndex | None = None) -> Decision:
    mode = mode or MODE
    role = (role or "").strip().lower()
    if mode == "off" or not role or role == "superadmin":
        return PASS
    customs = [c for c in ((state or {}).get("custom_roles") or []) if (c.get("status") or "active") == "active"]
    if mode == "custom" and not customs:
        return PASS
    row = (index or cat.route_index()).find(method, path)
    if row is None or not row.get("perms"):
        return PASS                     # unknown to the compile (services decide) / self / public route
    allowed = set(row.get("roles") or [])
    sys_ok = role in allowed or role not in cat.SYSTEM_ROLE_KEYS   # unknown legacy role names: services decide
    if not customs:
        return PASS if sys_ok else Decision(deny=True, code="RBAC_DENIED")
    cands: list[tuple[int, int, Decision]] = []
    if sys_ok:
        cands.append((cat.REACH_RANK.get(cat.SYSTEM_REACH.get(role, "own"), 0), 1, PASS))
    scope_values = (state or {}).get("scope_values") or {}
    for c in customs:
        reach = c.get("reach") or "org"
        act = cat.acting_role(c.get("base_role") or "employee", reach)
        if act not in allowed:
            continue
        if not _needed(row, act) <= set(c.get("permissions") or []):
            continue
        data_scope = None
        if reach in cat.NARROW_REACH:
            if (method.upper(), row["p"]) not in cat.NARROW_REACH_ROUTES:
                continue
            val = scope_values.get(reach)
            if not val:
                continue                # cannot narrow -> this role cannot act (fail closed)
            data_scope = f"{reach}:{val}"
        data_classes = tuple(sorted(p.split(".", 1)[1] for p in (c.get("permissions") or []) if p.startswith("data.")))
        cands.append((cat.REACH_RANK.get(reach, 0), 0,
                      Decision(acting_role=act, acting_key=c.get("key"), data_scope=data_scope, reach=reach,
                               data_classes=data_classes)))
    if not cands:
        return Decision(deny=True, code="RBAC_DENIED")
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return cands[0][2]


def apply(request, d: Decision) -> None:
    """Re-stamp the request identity for a custom role that acts (read by _stamp_identity)."""
    if d.acting_role is None:
        return
    st = request.state
    st.system_role = getattr(st, "role", None)
    st.role = d.acting_role
    st.acting_role = d.acting_key or ""
    st.data_scope = d.data_scope or ""
    st.acting_data = ",".join(d.data_classes) or "-"      # "-" = acting with NO sensitive data class
    if d.reach == "org":
        # whole-organisation reach is not locked to the login's own branch (branch users would be)
        st.branch_id = request.headers.get("x-view-branch") or None
    elif d.reach == "location" and d.data_scope:
        st.branch_id = d.data_scope.split(":", 1)[1]
