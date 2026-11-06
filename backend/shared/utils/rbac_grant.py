"""Who may define, assign and change which role (org Roles & Permissions, 2026-10-24).

Pure functions: each returns a Refusal (a sentence that carries an error code) or None. The auth service raises
the HTTP error with that code, so the refusal and its code come from one place.

Rules (least privilege — the HMS design of shared/utils/role_grant.py, not its code):
  * A custom role is built on a system role below Org Admin (CUSTOM_BASES) and may carry only permissions that
    (a) the catalogue knows, (b) are not platform / login / role management (NON_DELEGABLE), (c) its acting
    system role holds (the services still check that role), and (d) its GRANTER holds — a granter can never
    create or hand out more than they have, nor a wider data reach than their own.
  * Department / location / entity reach is enforced only on the People list + profile, so such a role may carry
    only NARROW_REACH_PERMS.
  * Org Admin (and superadmin) logins are created, re-roled, edited, reset, disabled and given custom roles ONLY
    by the Lexora HRM platform operator (Entersoft 2026-10-24 Medium, "Unauthorized Organization Admin Creation").
  * Nobody changes their own roles.
"""
from __future__ import annotations

from typing import Iterable, Optional

from shared.utils import rbac_catalogue as cat

SUPERADMIN = "superadmin"
ORG_ADMIN = "org_admin"
#: system roles allowed to open Roles & Permissions for writing (custom roles never: roles.* writes are NON_DELEGABLE)
ROLE_MANAGERS = frozenset({SUPERADMIN, ORG_ADMIN, "hr_admin"})


class Refusal(str):
    """A refusal sentence that also carries its error code."""
    code: str

    def __new__(cls, message: str, code: str):
        o = str.__new__(cls, message)
        o.code = code
        return o


def _n(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def reach_of(system_role: str, custom_reaches: Iterable[str] = ()) -> str:
    """The widest data reach a login has (its system role's, or a wider custom role's)."""
    best = cat.SYSTEM_REACH.get(_n(system_role), "org" if _n(system_role) == SUPERADMIN else "own")
    for r in custom_reaches or ():
        if cat.REACH_RANK.get(r, -1) > cat.REACH_RANK.get(best, -1):
            best = r
    return best


def effective_permissions(system_role: str, custom_roles: Iterable[dict] = ()) -> set[str]:
    """Union of the system role's and the ACTIVE custom roles' permissions."""
    out = set(cat.system_permissions(_n(system_role)))
    for c in custom_roles or ():
        if (c.get("status") or "active") == "active":
            out |= set(c.get("permissions") or [])
    return out


def definition_refusal(base_role: str, reach: str, permissions: Iterable[str], *,
                       granter_role: str, granter_perms: Iterable[str], granter_reach: str) -> Optional[Refusal]:
    """None when this granter may save a custom role with this base / reach / permission list."""
    base, reach = _n(base_role), _n(reach)
    perms = {str(p).strip() for p in (permissions or []) if str(p).strip()}
    if base in cat.PLATFORM_ONLY_ROLES:
        return Refusal("Custom roles cannot be built on Org Admin. Org Admin logins are managed by the Lexora HRM "
                       "platform operator.", "RBAC_BASE_PLATFORM_ONLY")
    if base not in cat.CUSTOM_BASES:
        return Refusal(f"'{(base_role or '')[:40]}' is not a system role a custom role can be built on.",
                       "RBAC_BASE_INVALID")
    if reach not in cat.REACH_RANK:
        return Refusal(f"Unknown data reach '{(reach or '')[:40]}'.", "RBAC_REACH_INVALID")
    if base == "employee" and reach != "own":
        return Refusal("A role built on Employee can only reach the person's own records.", "RBAC_REACH_BASE")
    if base == "manager" and reach not in ("own", "team"):
        return Refusal("A role built on Manager can reach own records or the direct team only.", "RBAC_REACH_BASE")
    if not perms:
        return Refusal("Pick at least one permission.", "RBAC_NO_PERMISSIONS")
    unknown = sorted(perms - cat.ALL_PERMISSIONS)
    if unknown:
        return Refusal("Unknown permission(s): " + ", ".join(unknown[:8]), "RBAC_UNKNOWN_PERMISSION")
    nd = sorted(perms & cat.NON_DELEGABLE)
    if nd:
        return Refusal("These permissions cannot be put on a custom role (logins, roles and the platform stay with "
                       "the system roles): " + ", ".join(nd[:8]), "RBAC_NON_DELEGABLE")
    if reach in cat.NARROW_REACH and not perms <= cat.NARROW_REACH_PERMS:
        extra = sorted(perms - cat.NARROW_REACH_PERMS)
        return Refusal(f"'{cat.REACH_LABELS[reach]}' reach applies to the People directory and profiles only. Use "
                       "'Whole organisation' or 'Own team' for: " + ", ".join(extra[:8]), "RBAC_REACH_NARROW_MODULE")
    act = cat.acting_role(base, reach)
    over = sorted(perms - cat.system_permissions(act))
    if over:
        return Refusal(f"The {act} system role these permissions act as does not have: " + ", ".join(over[:8])
                       + ". Build the role on a system role that has them.", "RBAC_OVER_BASE")
    if _n(granter_role) == SUPERADMIN:
        return None
    missing = sorted(perms - set(granter_perms or []))
    if missing:
        return Refusal("You cannot give permissions you do not have yourself: " + ", ".join(missing[:8]),
                       "RBAC_ESCALATION")
    if cat.REACH_RANK[reach] > cat.REACH_RANK.get(granter_reach, -1):
        return Refusal(f"You cannot give a wider data reach ({cat.REACH_LABELS[reach]}) than your own.",
                       "RBAC_REACH_ESCALATION")
    return None


def manage_refusal(granter_role: str) -> Optional[Refusal]:
    """None when this system role may write in Roles & Permissions."""
    if _n(granter_role) in ROLE_MANAGERS:
        return None
    return Refusal("Only an Org Admin or HR Admin can manage roles.", "RBAC_MANAGE_DENIED")


def assign_refusal(*, granter_role: str, granter_id: str, granter_perms: Iterable[str], granter_reach: str,
                   target_id: str, target_system_role: str, role: dict) -> Optional[Refusal]:
    """None when the granter may give (or take away) custom `role` to / from the target login."""
    if str(granter_id) == str(target_id):
        return Refusal("You cannot change your own roles. Ask another administrator.", "RBAC_SELF_ASSIGN")
    if _n(target_system_role) in cat.PLATFORM_ONLY_ROLES and _n(granter_role) != SUPERADMIN:
        return Refusal("Only the Lexora HRM platform operator may change an Org Admin login.",
                       "RBAC_ORG_ADMIN_PLATFORM_ONLY")
    if (role.get("status") or "active") != "active":
        return Refusal("This role is retired. Restore it before giving it to anyone.", "RBAC_ROLE_RETIRED")
    if _n(granter_role) == SUPERADMIN:
        return None
    missing = sorted(set(role.get("permissions") or []) - set(granter_perms or []))
    if missing:
        return Refusal("This role carries permissions you do not have yourself: " + ", ".join(missing[:8]),
                       "RBAC_ESCALATION")
    if cat.REACH_RANK.get(role.get("reach") or "org", 99) > cat.REACH_RANK.get(granter_reach, -1):
        return Refusal("This role reaches wider data than your own.", "RBAC_REACH_ESCALATION")
    return None


def edit_refusal(*, granter_role: str, granter_perms: Iterable[str], granter_reach: str,
                 role: dict) -> Optional[Refusal]:
    """None when the granter may edit / retire / restore / delete an EXISTING custom role (they must hold everything
    it carries now — else an HR Admin could retire or re-shape a role an Org Admin made)."""
    if _n(granter_role) == SUPERADMIN:
        return None
    missing = sorted(set(role.get("permissions") or []) - set(granter_perms or []))
    if missing:
        return Refusal("This role carries permissions you do not have, so you cannot change it: "
                       + ", ".join(missing[:8]), "RBAC_ESCALATION")
    if cat.REACH_RANK.get(role.get("reach") or "org", 99) > cat.REACH_RANK.get(granter_reach, -1):
        return Refusal("This role reaches wider data than your own, so you cannot change it.", "RBAC_REACH_ESCALATION")
    return None


def system_role_refusal(granter_role: str, target_role: str) -> Optional[Refusal]:
    """None when the granter may give system role `target_role` to a login (create or re-role)."""
    g, t = _n(granter_role), _n(target_role)
    if t == SUPERADMIN:
        return Refusal("The platform role cannot be given to an organisation login.", "AUTH_ROLE_SUPERADMIN")
    if t == ORG_ADMIN and g != SUPERADMIN:
        return Refusal("Only the Lexora HRM platform operator may make an Org Admin.", "AUTH_ROLE_PLATFORM_ONLY")
    if g in (SUPERADMIN, ORG_ADMIN, "hr_admin"):
        return None
    return Refusal("Only an Org Admin or HR Admin can give roles.", "AUTH_ROLE_DENIED")


def system_manage_refusal(granter_role: str, existing_role: str) -> Optional[Refusal]:
    """None when the granter may edit / reset / disable / re-role a login whose CURRENT role is `existing_role`."""
    if _n(existing_role) in cat.PLATFORM_ONLY_ROLES and _n(granter_role) != SUPERADMIN:
        return Refusal("Only the Lexora HRM platform operator may change an Org Admin login.",
                       "AUTH_ORG_ADMIN_PLATFORM_ONLY")
    return None
