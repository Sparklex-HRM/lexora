"""Auth service — org Roles & Permissions (go-live wrap-up 2026-10-24).

    GET    /auth/rbac/catalogue                       modules x actions, data scopes, reaches, system roles + perms
    GET    /auth/rbac/roles                           system roles (read-only) + this org's custom roles
    POST   /auth/rbac/roles                           create a custom role {name, description, base_role, reach,
                                                      permissions, cloned_from?}
    GET    /auth/rbac/roles/{role_id}                 one custom role + its members
    PUT    /auth/rbac/roles/{role_id}                 edit (version + 1)
    POST   /auth/rbac/roles/{role_id}/retire          stop it working everywhere (assignments kept, ignored)
    POST   /auth/rbac/roles/{role_id}/restore
    DELETE /auth/rbac/roles/{role_id}                 only a role that was never assigned (else retire)
    POST   /auth/rbac/roles/{role_id}/members         {user_ids: [...]} give the role to logins
    DELETE /auth/rbac/roles/{role_id}/members/{user_id}
    GET    /auth/rbac/users                           logins with their system role + custom roles (picker / grid)
    GET    /auth/rbac/users/{user_id}                 effective-permission preview of one login
    PUT    /auth/rbac/users/{user_id}/roles           {role_ids: [...]} the login's full custom-role list
    GET    /auth/rbac/history?role_id=&user_id=       change log (rbac_changes)
    GET    /auth/rbac/me                              the caller's own effective permissions

Reads: org_admin + hr_admin (+ a custom role carrying roles.view). Writes: org_admin + hr_admin only (roles.*
writes are never on a custom role). Every rule is in shared/utils/rbac_grant.py; every change writes a
rbac_changes row in the SAME transaction plus a copy in the tamper-evident audit_events chain.
Errors are {"detail": sentence, "code": "RBAC_…"}.
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import identity  # noqa: E402
from shared.utils import rbac_catalogue as cat  # noqa: E402
from shared.utils import rbac_grant as G  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.rbac_models import (RbacChange, RbacRole, RbacUserRole, access_from,  # noqa: E402
                                      custom_roles_for, role_dict)

from database import get_db  # noqa: E402
from http_errors import PublicError  # noqa: E402
from models import User  # noqa: E402

router = APIRouter(prefix="/auth/rbac", tags=["rbac"])

READ_ROLES = {"superadmin", "org_admin", "hr_admin"}
MAX_ROLES_PER_ORG = 200


def _err(status: int, detail: str, code: str) -> PublicError:
    return PublicError(status, detail, code=code)


#: refusals that are about WHO asks (403); every other refusal is about WHAT was sent (422)
_FORBIDDEN = {"RBAC_ESCALATION", "RBAC_REACH_ESCALATION", "RBAC_MANAGE_DENIED", "RBAC_SELF_ASSIGN",
              "RBAC_ORG_ADMIN_PLATFORM_ONLY", "RBAC_BASE_PLATFORM_ONLY"}


def _refuse(r: G.Refusal | None) -> None:
    if r is not None:
        code = getattr(r, "code", "RBAC_REFUSED")
        raise _err(403 if code in _FORBIDDEN else 422, str(r), code)


def _uid(v, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        raise _err(422, f"That {what} is not valid.", "RBAC_BAD_ID")


def _org(request: Request) -> uuid.UUID:
    org = request.headers.get("x-org-id")
    if not org:
        raise _err(401, "Sign in again.", "AUTH_NO_SESSION")
    return _uid(org, "organisation")


def _role_hdr(request: Request) -> str:
    return (request.headers.get("x-role") or "").strip().lower()


def _can_read(request: Request) -> None:
    """System readers, or the gateway let a custom role carrying roles.view act as hr_admin (X-Acting-Role)."""
    if _role_hdr(request) not in READ_ROLES:
        raise PublicError(403, "Only an Org Admin or HR Admin can see roles.", code="RBAC_READ_DENIED")


async def _granter(request: Request, db: AsyncSession) -> dict:
    """The caller as a granter: system role from the DATABASE (not the header), its custom roles, perms, reach."""
    uid = _uid(request.headers.get("x-user-id"), "login")
    u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if not u:
        raise _err(401, "Sign in again.", "AUTH_NO_SESSION")
    customs = await custom_roles_for(db, u.id)
    acc = access_from(u.role, customs)
    return {"id": str(u.id), "email": u.email, "role": u.role, "perms": set(acc["perms"]), "reach": acc["reach"]}


async def _writer(request: Request, db: AsyncSession) -> dict:
    g = await _granter(request, db)
    refusal = G.manage_refusal(g["role"])
    if request.headers.get("x-acting-role") or refusal is not None:   # a custom role never writes here
        raise PublicError(403, "Only an Org Admin or HR Admin can manage roles.", code="RBAC_MANAGE_DENIED")
    return g


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return (s or "role")[:48]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _log(db: AsyncSession, request: Request, g: dict, action: str, *, role: dict | None = None,
               user_id: str | None = None, summary: str, before=None, after=None) -> None:
    db.add(RbacChange(org_id=_org(request), actor_id=g["id"], actor_email=g["email"], actor_role=g["role"],
                      action=action, role_id=(role or {}).get("id"), role_key=(role or {}).get("key"),
                      user_id=user_id, summary=summary[:2000], before=before, after=after))


async def _audit(request: Request, action: str, entity_id, summary: str, meta: dict) -> None:
    await record_audit(request, f"rbac.{action}", entity_type="rbac", entity_id=entity_id, summary=summary, meta=meta)


def _snap(r: dict) -> dict:
    return {k: r.get(k) for k in ("name", "description", "base_role", "reach", "permissions", "status", "version")}


async def _load_role(db: AsyncSession, org: uuid.UUID, role_id: str) -> RbacRole:
    r = (await db.execute(select(RbacRole).where(RbacRole.id == _uid(role_id, "role"),
                                                 RbacRole.org_id == org))).scalar_one_or_none()
    if not r:
        raise _err(404, "Role not found.", "RBAC_ROLE_NOT_FOUND")
    return r


async def _member_count(db: AsyncSession, role_id) -> int:
    return int((await db.execute(select(func.count()).select_from(RbacUserRole).where(
        RbacUserRole.role_id == role_id))).scalar() or 0)


def _system_rows() -> list[dict]:
    out = []
    for r in cat.SYSTEM_ROLES:
        out.append({**r, "system": True, "permissions": sorted(cat.system_permissions(r["key"])),
                    "reach_label": cat.REACH_LABELS[r["reach"]],
                    "can_clone": r["key"] in cat.CUSTOM_BASES,
                    "platform_only": r["key"] in cat.PLATFORM_ONLY_ROLES})
    return out


# ── read ──────────────────────────────────────────────────────────────────────

@router.get("/catalogue")
async def catalogue(request: Request):
    _can_read(request)
    return {**cat.catalogue_view(), "system_roles": _system_rows(),
            "reach_by_base": {b: (["own"] if b == "employee" else ["own", "team"] if b == "manager"
                                  else list(cat.DATA_REACH)) for b in cat.CUSTOM_BASES}}


@router.get("/roles")
async def list_roles(request: Request, db: AsyncSession = Depends(get_db)):
    _can_read(request)
    org = _org(request)
    rows = (await db.execute(select(RbacRole).where(RbacRole.org_id == org).order_by(RbacRole.name))).scalars().all()
    counts = dict((await db.execute(select(RbacUserRole.role_id, func.count()).where(
        RbacUserRole.org_id == org).group_by(RbacUserRole.role_id))).all())
    sys_counts = dict((await db.execute(select(User.role, func.count()).where(
        User.org_id == org, User.is_active.is_(True)).group_by(User.role))).all())
    custom = []
    for r in rows:
        d = role_dict(r)
        d.update(system=False, members=int(counts.get(r.id, 0)), reach_label=cat.REACH_LABELS.get(r.reach, r.reach),
                 acting_role=cat.acting_role(r.base_role, r.reach),
                 updated_at=r.updated_at.isoformat() if r.updated_at else None, updated_by=r.updated_by,
                 created_by=r.created_by)
        custom.append(d)
    system = [{**s, "members": int(sys_counts.get(s["key"], 0))} for s in _system_rows()]
    return {"system": system, "custom": custom}


@router.get("/roles/{role_id}")
async def get_role(role_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _can_read(request)
    org = _org(request)
    r = await _load_role(db, org, role_id)
    members = (await db.execute(select(User, RbacUserRole.assigned_at, RbacUserRole.assigned_by)
                                .join(RbacUserRole, RbacUserRole.user_id == User.id)
                                .where(RbacUserRole.role_id == r.id).order_by(User.email))).all()
    return {**role_dict(r), "reach_label": cat.REACH_LABELS.get(r.reach, r.reach),
            "acting_role": cat.acting_role(r.base_role, r.reach),
            "members": [{"user_id": str(u.id), "email": u.email, "name": _name(u), "system_role": u.role,
                         "is_active": bool(u.is_active), "assigned_at": at.isoformat() if at else None,
                         "assigned_by": by} for (u, at, by) in members]}


def _name(u: User) -> str:
    return " ".join(x for x in (u.first_name, u.last_name) if x) or u.email


@router.get("/users")
async def list_role_users(request: Request, q: str | None = None, db: AsyncSession = Depends(get_db)):
    _can_read(request)
    org = _org(request)
    users = (await db.execute(select(User).where(User.org_id == org, User.role != "superadmin")
                              .order_by(User.email))).scalars().all()
    links = (await db.execute(select(RbacUserRole.user_id, RbacRole.id, RbacRole.name, RbacRole.status)
                              .join(RbacRole, RbacRole.id == RbacUserRole.role_id)
                              .where(RbacUserRole.org_id == org))).all()
    by_user: dict[str, list] = {}
    for uid, rid, name, status in links:
        by_user.setdefault(str(uid), []).append({"id": str(rid), "name": name, "status": status})
    emps = await identity.employees_brief(str(org))
    out = []
    for u in users:
        e = emps.get(str(u.employee_id)) if u.employee_id else None
        row = {"user_id": str(u.id), "email": u.email, "name": _name(u), "system_role": u.role,
               "system_role_label": identity.ROLE_LABELS.get(u.role, u.role), "is_active": bool(u.is_active),
               "employee_number": (e or {}).get("employee_number"), "department": (e or {}).get("department"),
               "custom_roles": by_user.get(str(u.id), [])}
        out.append(row)
    if q:
        s = q.strip().lower()
        out = [r for r in out if s in f"{r['name']} {r['email']} {r['employee_number'] or ''} {r['department'] or ''}".lower()]
    return {"items": out, "total": len(out)}


def _preview(system_role: str, customs: list[dict]) -> dict:
    acc = access_from(system_role, customs)
    sys_p = cat.system_permissions(system_role)
    src: dict[str, list[str]] = {}
    for p in sys_p:
        src.setdefault(p, []).append(f"system:{system_role}")
    for c in customs:
        if (c.get("status") or "active") != "active":
            continue
        for p in c.get("permissions") or []:
            src.setdefault(p, []).append(f"custom:{c['key']}")
    return {**acc, "sources": src, "reach_label": cat.REACH_LABELS.get(acc["reach"], acc["reach"])}


@router.get("/users/{user_id}")
async def user_preview(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _can_read(request)
    org = _org(request)
    u = (await db.execute(select(User).where(User.id == _uid(user_id, "login"), User.org_id == org))).scalar_one_or_none()
    if not u:
        raise _err(404, "Login not found.", "RBAC_USER_NOT_FOUND")
    assigned = await custom_roles_for(db, u.id, active_only=False)
    return {"user_id": str(u.id), "email": u.email, "name": _name(u), "system_role": u.role,
            "system_role_label": identity.ROLE_LABELS.get(u.role, u.role),
            "custom_roles": assigned, **_preview(u.role, assigned)}


@router.get("/me")
async def my_access(request: Request, db: AsyncSession = Depends(get_db)):
    g_uid = _uid(request.headers.get("x-user-id"), "login")
    u = (await db.execute(select(User).where(User.id == g_uid))).scalar_one_or_none()
    if not u:
        raise _err(401, "Sign in again.", "AUTH_NO_SESSION")
    return _preview(u.role, await custom_roles_for(db, u.id))


@router.get("/history")
async def history(request: Request, role_id: str | None = None, user_id: str | None = None, limit: int = 200,
                  db: AsyncSession = Depends(get_db)):
    _can_read(request)
    org = _org(request)
    q = select(RbacChange).where(RbacChange.org_id == org)
    if role_id:
        q = q.where(RbacChange.role_id == str(_uid(role_id, "role")))
    if user_id:
        q = q.where(RbacChange.user_id == str(_uid(user_id, "login")))
    rows = (await db.execute(q.order_by(RbacChange.at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return [{"id": str(c.id), "at": c.at.isoformat() if c.at else None, "actor_email": c.actor_email,
             "actor_role": c.actor_role, "action": c.action, "role_id": c.role_id, "role_key": c.role_key,
             "user_id": c.user_id, "summary": c.summary, "before": c.before, "after": c.after} for c in rows]


# ── write ─────────────────────────────────────────────────────────────────────

class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    base_role: str = Field(min_length=2, max_length=30)
    reach: str = Field(default="org", max_length=20)
    permissions: list[str] = Field(default_factory=list, max_length=600)
    cloned_from: str | None = Field(default=None, max_length=64)


def _clean(body: RoleIn) -> dict:
    name = " ".join(body.name.split())
    if len(name) < 2:
        raise _err(422, "Give the role a name.", "RBAC_NAME_REQUIRED")
    perms = sorted({p.strip() for p in body.permissions if isinstance(p, str) and p.strip()})
    return {"name": name, "description": (body.description or "").strip() or None,
            "base_role": body.base_role.strip().lower(), "reach": (body.reach or "org").strip().lower(),
            "permissions": perms, "cloned_from": (body.cloned_from or "").strip().lower() or None}


@router.post("/roles", status_code=201)
async def create_role(body: RoleIn, request: Request, db: AsyncSession = Depends(get_db)):
    g = await _writer(request, db)
    org = _org(request)
    v = _clean(body)
    if v["cloned_from"] and v["cloned_from"] in cat.PLATFORM_ONLY_ROLES:
        raise _err(403, "Org Admin cannot be cloned. It is managed by the Lexora HRM platform operator.",
                   "RBAC_BASE_PLATFORM_ONLY")
    _refuse(G.definition_refusal(v["base_role"], v["reach"], v["permissions"], granter_role=g["role"],
                                 granter_perms=g["perms"], granter_reach=g["reach"]))
    n = int((await db.execute(select(func.count()).select_from(RbacRole).where(RbacRole.org_id == org))).scalar() or 0)
    if n >= MAX_ROLES_PER_ORG:
        raise _err(409, f"An organisation can have at most {MAX_ROLES_PER_ORG} custom roles. Retire or delete some.",
                   "RBAC_TOO_MANY_ROLES")
    if (await db.execute(select(RbacRole.id).where(RbacRole.org_id == org,
                                                   func.lower(RbacRole.name) == v["name"].lower()))).first():
        raise _err(409, "A role with this name already exists.", "RBAC_NAME_TAKEN")
    key, i = _slug(v["name"]), 2
    base_key = key
    while key in cat.SYSTEM_ROLE_KEYS or key == "superadmin" or (await db.execute(
            select(RbacRole.id).where(RbacRole.org_id == org, RbacRole.key == key))).first():
        key, i = f"{base_key[:44]}_{i}", i + 1
    r = RbacRole(org_id=org, key=key, created_by=g["email"], updated_by=g["email"], **v)
    db.add(r)
    await db.flush()
    d = role_dict(r)
    await _log(db, request, g, "role.create", role=d, summary=f"Custom role '{r.name}' created "
               f"(built on {r.base_role}, {cat.REACH_LABELS.get(r.reach, r.reach)}, {len(r.permissions)} permissions)",
               after=_snap(d))
    await db.commit()
    await _audit(request, "role.create", d["id"], f"Custom role '{r.name}' created", {"after": _snap(d)})
    return d


@router.put("/roles/{role_id}")
async def edit_role(role_id: str, body: RoleIn, request: Request, db: AsyncSession = Depends(get_db)):
    g = await _writer(request, db)
    org = _org(request)
    r = await _load_role(db, org, role_id)
    before = role_dict(r)
    _refuse(G.edit_refusal(granter_role=g["role"], granter_perms=g["perms"], granter_reach=g["reach"], role=before))
    v = _clean(body)
    _refuse(G.definition_refusal(v["base_role"], v["reach"], v["permissions"], granter_role=g["role"],
                                 granter_perms=g["perms"], granter_reach=g["reach"]))
    if v["name"].lower() != r.name.lower() and (await db.execute(select(RbacRole.id).where(
            RbacRole.org_id == org, func.lower(RbacRole.name) == v["name"].lower(), RbacRole.id != r.id))).first():
        raise _err(409, "A role with this name already exists.", "RBAC_NAME_TAKEN")
    r.name, r.description, r.base_role, r.reach, r.permissions = (v["name"], v["description"], v["base_role"],
                                                                    v["reach"], v["permissions"])
    r.version = (r.version or 1) + 1
    r.updated_by, r.updated_at = g["email"], _now()
    after = role_dict(r)
    added = sorted(set(after["permissions"]) - set(before["permissions"]))
    removed = sorted(set(before["permissions"]) - set(after["permissions"]))
    await _log(db, request, g, "role.edit", role=after, before=_snap(before), after=_snap(after),
               summary=f"Role '{r.name}' edited (v{r.version}): +{len(added)} / -{len(removed)} permissions"
                       + (f"; reach {before['reach']} -> {after['reach']}" if before["reach"] != after["reach"] else "")
                       + (f"; base {before['base_role']} -> {after['base_role']}" if before["base_role"] != after["base_role"] else ""))
    await db.commit()
    await _audit(request, "role.edit", after["id"], f"Role '{r.name}' edited",
                 {"added": added, "removed": removed, "before": _snap(before), "after": _snap(after)})
    return after


async def _set_status(role_id: str, status: str, request: Request, db: AsyncSession) -> dict:
    g = await _writer(request, db)
    org = _org(request)
    r = await _load_role(db, org, role_id)
    before = role_dict(r)
    _refuse(G.edit_refusal(granter_role=g["role"], granter_perms=g["perms"], granter_reach=g["reach"], role=before))
    if r.status == status:
        return before
    r.status, r.updated_by, r.updated_at = status, g["email"], _now()
    word = "retired" if status == "retired" else "restored"
    after = role_dict(r)
    await _log(db, request, g, f"role.{'retire' if status == 'retired' else 'restore'}", role=after,
               before=_snap(before), after=_snap(after), summary=f"Role '{r.name}' {word}")
    await db.commit()
    await _audit(request, f"role.{word}", after["id"], f"Role '{r.name}' {word}", {"members": await _member_count(db, r.id)})
    return after


@router.post("/roles/{role_id}/retire")
async def retire_role(role_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _set_status(role_id, "retired", request, db)


@router.post("/roles/{role_id}/restore")
async def restore_role(role_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _set_status(role_id, "active", request, db)


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    g = await _writer(request, db)
    org = _org(request)
    r = await _load_role(db, org, role_id)
    before = role_dict(r)
    _refuse(G.edit_refusal(granter_role=g["role"], granter_perms=g["perms"], granter_reach=g["reach"], role=before))
    ever = (await db.execute(select(RbacChange.id).where(RbacChange.org_id == org, RbacChange.role_id == before["id"],
                                                         RbacChange.action == "user.assign").limit(1))).first()
    if await _member_count(db, r.id) or ever:
        raise _err(409, "This role has been given to people, so it cannot be deleted (its history must stay). "
                        "Retire it instead.", "RBAC_ROLE_IN_USE")
    await db.delete(r)
    await _log(db, request, g, "role.delete", role=before, before=_snap(before), summary=f"Role '{before['name']}' deleted")
    await db.commit()
    await _audit(request, "role.delete", before["id"], f"Role '{before['name']}' deleted", {"before": _snap(before)})
    return {"deleted": True, "id": before["id"]}


class MembersIn(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=500)


async def _target_user(db: AsyncSession, org: uuid.UUID, user_id: str) -> User:
    u = (await db.execute(select(User).where(User.id == _uid(user_id, "login"), User.org_id == org))).scalar_one_or_none()
    if not u or u.role == "superadmin":
        raise _err(404, "Login not found.", "RBAC_USER_NOT_FOUND")
    return u


async def _assign(db, request, g, org, u: User, r: RbacRole, add: bool) -> bool:
    d = role_dict(r)
    _refuse(G.assign_refusal(granter_role=g["role"], granter_id=g["id"], granter_perms=g["perms"],
                             granter_reach=g["reach"], target_id=str(u.id), target_system_role=u.role,
                             role=d if add else {**d, "status": "active"}))
    link = (await db.execute(select(RbacUserRole).where(RbacUserRole.user_id == u.id,
                                                        RbacUserRole.role_id == r.id))).scalar_one_or_none()
    if add and not link:
        db.add(RbacUserRole(org_id=org, user_id=u.id, role_id=r.id, assigned_by=g["email"]))
        await _log(db, request, g, "user.assign", role=d, user_id=str(u.id),
                   summary=f"Role '{r.name}' given to {u.email}")
        return True
    if not add and link:
        await db.delete(link)
        await _log(db, request, g, "user.unassign", role=d, user_id=str(u.id),
                   summary=f"Role '{r.name}' taken from {u.email}")
        return True
    return False


@router.post("/roles/{role_id}/members")
async def add_members(role_id: str, body: MembersIn, request: Request, db: AsyncSession = Depends(get_db)):
    g = await _writer(request, db)
    org = _org(request)
    r = await _load_role(db, org, role_id)
    changed = []
    for uid in dict.fromkeys(body.user_ids):
        u = await _target_user(db, org, uid)
        if await _assign(db, request, g, org, u, r, True):
            changed.append(u.email)
    await db.commit()
    if changed:
        await _audit(request, "user.assign", str(r.id), f"Role '{r.name}' given to {len(changed)} login(s)",
                     {"emails": changed})
    return {"added": len(changed), "members": await _member_count(db, r.id)}


@router.delete("/roles/{role_id}/members/{user_id}")
async def remove_member(role_id: str, user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    g = await _writer(request, db)
    org = _org(request)
    r = await _load_role(db, org, role_id)
    u = await _target_user(db, org, user_id)
    ok = await _assign(db, request, g, org, u, r, False)
    await db.commit()
    if ok:
        await _audit(request, "user.unassign", str(r.id), f"Role '{r.name}' taken from {u.email}", {"user_id": str(u.id)})
    return {"removed": ok, "members": await _member_count(db, r.id)}


class UserRolesIn(BaseModel):
    role_ids: list[str] = Field(default_factory=list, max_length=50)


@router.put("/users/{user_id}/roles")
async def set_user_roles(user_id: str, body: UserRolesIn, request: Request, db: AsyncSession = Depends(get_db)):
    g = await _writer(request, db)
    org = _org(request)
    u = await _target_user(db, org, user_id)
    want = {str(_uid(x, "role")) for x in body.role_ids}
    current = {str(rid) for (rid,) in (await db.execute(select(RbacUserRole.role_id).where(
        RbacUserRole.user_id == u.id))).all()}
    roles = {str(r.id): r for r in (await db.execute(select(RbacRole).where(
        RbacRole.org_id == org, RbacRole.id.in_([uuid.UUID(x) for x in (want | current)] or [uuid.uuid4()])))).scalars().all()}
    missing = want - set(roles)
    if missing:
        raise _err(404, "Role not found.", "RBAC_ROLE_NOT_FOUND")
    n = 0
    for rid in sorted(want - current):
        n += await _assign(db, request, g, org, u, roles[rid], True)
    for rid in sorted(current - want):
        n += await _assign(db, request, g, org, u, roles[rid], False)
    await db.commit()
    if n:
        await _audit(request, "user.roles", str(u.id), f"Custom roles of {u.email} changed",
                     {"roles": sorted(roles[x].key for x in want)})
    assigned = await custom_roles_for(db, u.id, active_only=False)
    return {"user_id": str(u.id), "changed": n, "custom_roles": assigned, **_preview(u.role, assigned)}

