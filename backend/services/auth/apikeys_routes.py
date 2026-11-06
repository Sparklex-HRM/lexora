"""Auth service — org API keys (stream G3). org_admin only.

    GET  /auth/api-keys                      list (prefix, scopes, role, expiry, last used)
    POST /auth/api-keys {name, scopes, role, expires_days}   → the key, shown ONCE
    POST /auth/api-keys/{id}/revoke
    GET  /auth/api-keys/scopes               the scope catalogue for the screen
"""
import sys
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

sys.path.append("/app")
from http_errors import refusal as _refusal  # noqa: E402  (2026-10-24: no raw exception text)
from shared.utils import apikeys as K  # noqa: E402
from shared.utils import identity  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.providers.models import ApiKey  # noqa: E402

router = APIRouter(prefix="/auth/api-keys", tags=["api-keys"])


def _admin(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an admin can manage API keys")
    return org


def _out(k: ApiKey) -> dict:
    v = K.verdict({"revoked_at": k.revoked_at, "expires_at": k.expires_at})
    return {"id": k.id, "name": k.name, "prefix": k.prefix + "_…", "scopes": list(k.scopes or []), "role": k.role,
            "role_label": identity.ROLE_LABELS.get(k.role, k.role), "expires_at": pm.iso(k.expires_at),
            "last_used_at": pm.iso(k.last_used_at), "created_at": pm.iso(k.created_at), "created_by": k.created_by,
            "revoked_at": pm.iso(k.revoked_at), "status": v or "active"}


@router.get("/scopes")
async def scopes(request: Request):
    _admin(request)
    return {"services": list(K.SCOPED_SERVICES), "levels": ["read", "write"],
            "roles": [{"key": r, "label": identity.ROLE_LABELS[r]} for r in K.ROLES_FOR_KEYS],
            "how": "Send the key as  Authorization: Bearer <key>  (or X-Api-Key: <key>) to the same API the app uses."}


@router.get("")
async def list_keys(request: Request):
    org = _admin(request)
    async with pm.session() as s:
        rows = (await s.execute(select(ApiKey).where(ApiKey.org_id == org).order_by(ApiKey.created_at.desc()))).scalars().all()
    return {"items": [_out(k) for k in rows]}


class KeyIn(BaseModel):
    name: str
    scopes: list[str] = []
    role: str = "employee"
    expires_days: int | None = 365


@router.post("", status_code=201)
async def create_key(body: KeyIn, request: Request):
    org = _admin(request)
    name = (body.name or "").strip()[:80]
    if not name:
        raise HTTPException(422, "Give the key a name (what will use it)")
    scopes = K.clean_scopes(body.scopes)
    if not scopes:
        raise HTTPException(422, "Pick at least one scope")
    try:
        role = identity.normalise_role(body.role)
    except identity.IdentityError as e:
        raise _refusal(422, e, "AUTH_APIKEY_ROLE_INVALID", fallback="Pick a valid role for the key.")
    if role not in K.ROLES_FOR_KEYS:
        raise HTTPException(422, "An API key cannot carry the admin role")
    if body.expires_days is not None and not (1 <= body.expires_days <= 3650):
        raise HTTPException(422, "Expiry must be between 1 and 3650 days")
    raw, prefix, h = K.generate()
    async with pm.session() as s:
        k = ApiKey(org_id=org, name=name, prefix=prefix, key_hash=h, scopes=scopes, role=role,
                   expires_at=(pm.now_utc() + timedelta(days=body.expires_days)) if body.expires_days else None,
                   created_by=request.headers.get("x-user-email"))
        s.add(k)
        await s.commit()
        out = _out(k)
    await record_audit(request, "integration.apikey.create", entity_type="api_key", entity_id=out["id"],
                       summary=f"API key '{name}' created ({', '.join(scopes)}; role {role})",
                       meta={"scopes": scopes, "role": role, "expires_at": out["expires_at"]})
    return {**out, "key": raw, "note": "Copy the key now. It is not shown again."}


@router.post("/{kid}/revoke")
async def revoke_key(kid: str, request: Request):
    org = _admin(request)
    async with pm.session() as s:
        k = (await s.execute(select(ApiKey).where(ApiKey.id == kid, ApiKey.org_id == org))).scalar_one_or_none()
        if not k:
            raise HTTPException(404, "Key not found")
        if not k.revoked_at:
            k.revoked_at = pm.now_utc()
            await s.commit()
        out = _out(k)
    await record_audit(request, "integration.apikey.revoke", entity_type="api_key", entity_id=kid,
                       summary=f"API key '{k.name}' revoked")
    return out
