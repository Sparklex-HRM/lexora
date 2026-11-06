"""Auth service — SCIM 2.0 endpoint + token admin + provisioning log (stream G3).

Admin (gateway JWT, org_admin):
    GET  /auth/scim/admin/tokens      POST /auth/scim/admin/tokens {label}   (token shown ONCE)
    POST /auth/scim/admin/tokens/{id}/revoke
    GET  /auth/scim/admin/log
Provisioning (the gateway lists /auth/scim/v2/ as public; the per-org BEARER TOKEN is the credential):
    GET  /auth/scim/v2/ServiceProviderConfig | /Schemas | /ResourceTypes
    GET/POST /auth/scim/v2/Users      GET/PUT/PATCH/DELETE /auth/scim/v2/Users/{id}
    GET /auth/scim/v2/Groups          GET/PATCH /auth/scim/v2/Groups/{role}
"""
import sys
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from http_errors import refusal as _refusal  # noqa: E402  (2026-10-24: no raw exception text)
from shared.utils import identity  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.providers.models import ScimLog, ScimToken  # noqa: E402
from shared.utils.ratelimit import RateLimiter, client_ip, make_limiter  # noqa: E402  (Redis-shared when REDIS_URL is set)

import scim as S  # noqa: E402
from database import get_db  # noqa: E402
from models import User  # noqa: E402

router = APIRouter(prefix="/auth/scim", tags=["scim"])
scim_limiter = make_limiter(600, 60, name="scim")
SCIM_CT = "application/scim+json"


def _admin(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an admin can manage provisioning")
    return org


# ── admin: tokens + log ──────────────────────────────────────────────────────

@router.get("/admin/tokens")
async def list_tokens(request: Request):
    org = _admin(request)
    async with pm.session() as s:
        rows = (await s.execute(select(ScimToken).where(ScimToken.org_id == org)
                                .order_by(ScimToken.created_at.desc()))).scalars().all()
    return {"items": [{"id": t.id, "label": t.label, "created_at": pm.iso(t.created_at), "created_by": t.created_by,
                       "last_used_at": pm.iso(t.last_used_at), "revoked_at": pm.iso(t.revoked_at),
                       "status": "revoked" if t.revoked_at else "active"} for t in rows],
            "base_url": _base(request)}


class TokenIn(BaseModel):
    label: str = ""


@router.post("/admin/tokens", status_code=201)
async def create_token(body: TokenIn, request: Request):
    org = _admin(request)
    raw, h = S.new_token()
    async with pm.session() as s:
        t = ScimToken(org_id=org, label=(body.label or "").strip()[:80] or "SCIM token", token_hash=h,
                      created_by=request.headers.get("x-user-email"))
        s.add(t)
        await s.commit()
        tid = t.id
    await record_audit(request, "integration.scim.token.create", entity_type="scim_token", entity_id=tid,
                       summary=f"SCIM token created ({body.label or 'SCIM token'})")
    return {"id": tid, "token": raw, "base_url": _base(request),
            "note": "Copy the token now. It is not shown again."}


@router.post("/admin/tokens/{tid}/revoke")
async def revoke_token(tid: str, request: Request):
    org = _admin(request)
    async with pm.session() as s:
        t = (await s.execute(select(ScimToken).where(ScimToken.id == tid, ScimToken.org_id == org))).scalar_one_or_none()
        if not t:
            raise HTTPException(404, "Token not found")
        if not t.revoked_at:
            t.revoked_at = pm.now_utc()
            await s.commit()
    await record_audit(request, "integration.scim.token.revoke", entity_type="scim_token", entity_id=tid,
                       summary=f"SCIM token revoked ({t.label})")
    return {"ok": True}


@router.get("/admin/log")
async def provisioning_log(request: Request, limit: int = 300):
    org = _admin(request)
    async with pm.session() as s:
        rows = (await s.execute(select(ScimLog).where(ScimLog.org_id == org)
                                .order_by(ScimLog.at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return {"items": [{"id": r.id, "at": pm.iso(r.at), "method": r.method, "path": r.path, "user_name": r.user_name,
                       "external_id": r.external_id, "outcome": r.outcome, "status_code": r.status_code,
                       "message": r.message} for r in rows]}


# ── provisioning: auth by bearer token ───────────────────────────────────────

def _base(request: Request) -> str:
    import sso
    return sso.api_base() + "/auth/scim/v2"


async def _org_from_token(request: Request) -> str:
    wait = scim_limiter.hit(f"scim|{client_ip(request)}")
    if wait:
        raise S.ScimError(429, "Too many requests")
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise S.ScimError(401, "A bearer token is required")
    raw = auth.split(" ", 1)[1].strip()
    async with pm.session() as s:
        t = (await s.execute(select(ScimToken).where(ScimToken.token_hash == S.hash_token(raw)))).scalar_one_or_none()
        if not t or t.revoked_at:
            raise S.ScimError(401, "Unknown or revoked token")
        t.last_used_at = pm.now_utc()
        await s.commit()
        return t.org_id


async def _log(org: str, request: Request, *, outcome: str, status: int, user_name=None, external_id=None, message=None):
    try:
        async with pm.session() as s:
            s.add(ScimLog(org_id=org, method=request.method, path=request.url.path[-200:], user_name=user_name,
                          external_id=external_id, outcome=outcome, status_code=status, message=(message or "")[:400]))
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


def _err(e: S.ScimError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status, media_type=SCIM_CT)


def _ok(body, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=SCIM_CT)


async def _users(db: AsyncSession, org: str) -> list[dict]:
    rows = (await db.execute(select(User).where(User.org_id == identity._uuid(org), User.role != "superadmin"))).scalars().all()
    emps = await identity.employees_brief(org)
    out = []
    for u in rows:
        r = identity.public_user({c.name: getattr(u, c.name, None) for c in identity.users.c})
        e = emps.get(r["employee_id"]) if r["employee_id"] else None
        r["employee_number"] = (e or {}).get("employee_number")
        r["_employee"] = e
        out.append(r)
    return out


@router.get("/v2/ServiceProviderConfig")
async def spc(request: Request):
    try:
        await _org_from_token(request)
    except S.ScimError as e:
        return _err(e)
    return _ok({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "patch": {"supported": True}, "bulk": {"supported": False}, "filter": {"supported": True, "maxResults": 200},
                "changePassword": {"supported": False}, "sort": {"supported": False}, "etag": {"supported": False},
                "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token",
                                           "description": "Per-organization token from Lexora HRM → Integrations → SCIM"}]})


@router.get("/v2/ResourceTypes")
async def resource_types(request: Request):
    try:
        await _org_from_token(request)
    except S.ScimError as e:
        return _err(e)
    return _ok({"schemas": [S.SCHEMA_LIST], "totalResults": 2, "Resources": [
        {"id": "User", "name": "User", "endpoint": "/Users", "schema": S.SCHEMA_USER},
        {"id": "Group", "name": "Group", "endpoint": "/Groups", "schema": S.SCHEMA_GROUP}]})


@router.get("/v2/Schemas")
async def schemas(request: Request):
    try:
        await _org_from_token(request)
    except S.ScimError as e:
        return _err(e)
    return _ok({"schemas": [S.SCHEMA_LIST], "totalResults": 3,
                "Resources": [{"id": S.SCHEMA_USER}, {"id": S.SCHEMA_ENTERPRISE}, {"id": S.SCHEMA_GROUP}]})


@router.get("/v2/Users")
async def scim_list_users(request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100,
                          db: AsyncSession = Depends(get_db)):
    try:
        org = await _org_from_token(request)
        flt = S.parse_filter(filter)
    except S.ScimError as e:
        return _err(e)
    rows = [r for r in await _users(db, org) if S.matches(r, flt)]
    start, n = max(1, startIndex), max(1, min(count, 200))
    page = rows[start - 1:start - 1 + n]
    base = _base(request)
    return _ok(S.list_response([S.to_scim_user(r, base=base, employee=r["_employee"]) for r in page], len(rows), start, n))


@router.get("/v2/Users/{uid}")
async def scim_get_user(uid: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        org = await _org_from_token(request)
    except S.ScimError as e:
        return _err(e)
    r = next((r for r in await _users(db, org) if r["id"] == uid), None)
    if not r:
        return _err(S.ScimError(404, "User not found"))
    return _ok(S.to_scim_user(r, base=_base(request), employee=r["_employee"]))


async def _create(org: str, request: Request, data: dict, db: AsyncSession) -> JSONResponse:
    if not data["email"]:
        raise S.ScimError(400, "userName (e-mail) is required", "invalidValue")
    try:
        email = identity.clean_email(data["email"])
    except identity.IdentityError as e:
        raise S.ScimError(400, _refusal(400, e, "SCIM_INVALID").detail, "invalidValue")
    existing = (await db.execute(select(User).where(
        User.org_id == identity._uuid(org), sa.func.lower(User.email) == email))).scalars().first()
    if existing:
        raise S.ScimError(409, "A user with that userName already exists", "uniqueness")
    emps = await identity.employees_brief(org)
    emp = None
    if data["employee_number"]:
        emp = next((e for e in emps.values() if (e.get("employee_number") or "").upper() == data["employee_number"].upper()), None)
    if not emp:
        emp = next((e for e in emps.values() if (e.get("work_email") or e.get("email") or "").lower() == email), None)
    actor = {"email": "scim", "role": "system"}
    outcome = "created"
    if emp:
        res = await identity.ensure_login_for_employee(org, emp["id"], email, "employee", send_invite=False,
                                                       first_name=data["first_name"] or emp.get("first_name"),
                                                       last_name=data["last_name"] or emp.get("last_name"), actor=actor)
        uid, outcome = res["user_id"], "linked" if res["linked"] else "created"
        msg = f"Login {outcome} and linked to employee {emp.get('employee_number')}"
    else:
        import users_routes
        res = await users_routes._create_unlinked(db, org, email, "employee", data["first_name"] or email.split("@")[0],
                                                  data["last_name"] or "", False, actor)
        uid = res["user_id"]
        msg = "Login created; no matching employee record (link it in Users later)"
    if data["active"] is False:
        await identity.set_user_active(org, uid, False, "Deactivated by SCIM", actor=actor)
    await _log(org, request, outcome=outcome, status=201, user_name=email, external_id=data["external_id"], message=msg)
    r = next(r for r in await _users(db, org) if r["id"] == uid)
    return _ok(S.to_scim_user(r, base=_base(request), employee=r["_employee"], external_id=data["external_id"]), 201)


@router.post("/v2/Users")
async def scim_create_user(request: Request, db: AsyncSession = Depends(get_db)):
    org = None
    try:
        org = await _org_from_token(request)
        body = await request.json()
        return await _create(org, request, S.from_scim_user(body), db)
    except S.ScimError as e:
        if org:
            await _log(org, request, outcome="rejected", status=e.status, message=e.detail)
        return _err(e)
    except identity.IdentityError as e:
        if org:
            await _log(org, request, outcome="rejected", status=e.status, message=_refusal(e.status, e, "SCIM_INVALID").detail)
        return _err(S.ScimError(e.status, _refusal(e.status, e, "SCIM_INVALID").detail))
    except ValueError:
        return _err(S.ScimError(400, "The body is not valid JSON", "invalidSyntax"))


async def _apply(org: str, request: Request, uid: str, new: dict, db: AsyncSession) -> JSONResponse:
    u = (await db.execute(select(User).where(User.id == identity._uuid(uid), User.org_id == identity._uuid(org)))).scalar_one_or_none()
    if not u or u.role in ("superadmin", "org_admin"):
        raise S.ScimError(404, "User not found")
    actor = {"email": "scim", "role": "system"}
    changes = []
    if new.get("email") and new["email"] != u.email.lower():
        email = identity.clean_email(new["email"])
        dup = (await db.execute(select(User).where(User.org_id == u.org_id, sa.func.lower(User.email) == email,
                                                   User.id != u.id))).scalars().first()
        if dup:
            raise S.ScimError(409, "That userName is already used", "uniqueness")
        u.email = email
        changes.append("e-mail")
    for k in ("first_name", "last_name"):
        if k in new and (new[k] or None) != getattr(u, k):
            setattr(u, k, new[k])
            changes.append(k.replace("_", " "))
    await db.commit()
    if "active" in new and bool(new["active"]) != bool(u.is_active):
        await identity.set_user_active(org, u.id, bool(new["active"]),
                                       None if new["active"] else "Deactivated by SCIM", actor=actor)
        changes.append("activated" if new["active"] else "deactivated")
        # set_user_active wrote through its own session: reload this session's copy of the
        # login so the response below reflects the new active flag (it used to echo the old one).
        await db.refresh(u)
    outcome = "deactivated" if "deactivated" in changes else "updated"
    await _log(org, request, outcome=outcome, status=200, user_name=u.email, external_id=new.get("external_id"),
               message=", ".join(changes) or "No change")
    r = next(r for r in await _users(db, org) if r["id"] == str(u.id))
    return _ok(S.to_scim_user(r, base=_base(request), employee=r["_employee"], external_id=new.get("external_id")))


@router.put("/v2/Users/{uid}")
async def scim_replace_user(uid: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        org = await _org_from_token(request)
        return await _apply(org, request, uid, S.from_scim_user(await request.json()), db)
    except S.ScimError as e:
        return _err(e)
    except identity.IdentityError as e:
        return _err(S.ScimError(e.status, _refusal(e.status, e, "SCIM_INVALID").detail))


@router.patch("/v2/Users/{uid}")
async def scim_patch_user(uid: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        org = await _org_from_token(request)
        cur = next((r for r in await _users(db, org) if r["id"] == uid), None)
        if not cur:
            raise S.ScimError(404, "User not found")
        base = {"email": cur["email"], "first_name": cur["first_name"], "last_name": cur["last_name"],
                "active": cur["is_active"], "employee_number": cur["employee_number"]}
        return await _apply(org, request, uid, S.apply_patch(base, await request.json()), db)
    except S.ScimError as e:
        return _err(e)
    except identity.IdentityError as e:
        return _err(S.ScimError(e.status, _refusal(e.status, e, "SCIM_INVALID").detail))


@router.delete("/v2/Users/{uid}", status_code=204)
async def scim_delete_user(uid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """SCIM DELETE = deactivate. Logins are never hard-deleted (audit trail)."""
    try:
        org = await _org_from_token(request)
        await _apply(org, request, uid, {"active": False}, db)
    except S.ScimError as e:
        return _err(e)
    return JSONResponse(None, status_code=204)


@router.get("/v2/Groups")
async def scim_groups(request: Request, filter: str | None = None, db: AsyncSession = Depends(get_db)):
    try:
        org = await _org_from_token(request)
    except S.ScimError as e:
        return _err(e)
    users = await _users(db, org)
    base = _base(request)
    groups = [S.to_scim_group(r, [u for u in users if u["role"] == r], base=base) for r in S.GROUP_ROLES]
    if filter:
        try:
            flt = S.parse_filter(filter)
        except S.ScimError as e:
            return _err(e)
        if flt and flt[0] == "name":
            groups = [g for g in groups if S.matches({"name": g["displayName"]}, flt)]
    return _ok(S.list_response(groups, len(groups), 1, len(groups)))


@router.get("/v2/Groups/{role}")
async def scim_group(role: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        org = await _org_from_token(request)
    except S.ScimError as e:
        return _err(e)
    if role not in S.GROUP_ROLES:
        return _err(S.ScimError(404, "Group not found"))
    users = await _users(db, org)
    return _ok(S.to_scim_group(role, [u for u in users if u["role"] == role], base=_base(request)))


@router.patch("/v2/Groups/{role}")
async def scim_patch_group(role: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Adding a member to a group = giving that role. Removing = back to employee."""
    try:
        org = await _org_from_token(request)
        if role not in S.GROUP_ROLES:
            raise S.ScimError(404, "Group not found")
        added, removed = S.group_patch_members(await request.json())
    except S.ScimError as e:
        return _err(e)
    for uid, new_role in [(u, role) for u in added] + [(u, "employee") for u in removed]:
        u = (await db.execute(select(User).where(User.id == identity._uuid(uid), User.org_id == identity._uuid(org)))).scalar_one_or_none()
        if not u or u.role in ("superadmin", "org_admin") or u.role == new_role:
            continue
        old = u.role
        u.role, u.branch_scope = new_role, "org" if new_role in identity.ORG_SCOPE_ROLES else "branch"
        u.tokens_valid_after = datetime.now(timezone.utc)
        await db.commit()
        await identity._audit(org, {"email": "scim", "role": "system"}, "auth.user.role", u.id,
                              f"Role of {u.email} changed by SCIM: {old} → {new_role}", {"from": old, "to": new_role})
        await _log(org, request, outcome="updated", status=200, user_name=u.email, message=f"role {old} → {new_role}")
    users = await _users(db, org)
    return _ok(S.to_scim_group(role, [u for u in users if u["role"] == role], base=_base(request)))
