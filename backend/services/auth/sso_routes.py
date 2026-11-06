"""Auth service — SSO routes (stream G3).

Admin (gateway-authenticated, org_admin):
    GET / PUT /auth/sso/config          POST /auth/sso/test
Public (no JWT — the gateway lists these prefixes):
    GET  /auth/sso/public/{org_code}    does this org have SSO? (button text, password allowed?)
    GET  /auth/sso/start/{org_code}     redirect to the IdP
    GET  /auth/sso/callback/oidc        POST /auth/sso/callback/saml
    GET  /auth/sso/saml/{org_code}/metadata
    POST /auth/sso/exchange {code}      one-time code → the normal login tokens
"""
import secrets
import sys
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from http_errors import refusal as _refusal  # noqa: E402  (2026-10-24: no raw exception text)
from shared.utils import identity  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.providers.models import SsoConfig  # noqa: E402
from shared.utils.ratelimit import RateLimiter, client_ip, make_limiter  # noqa: E402  (Redis-shared when REDIS_URL is set)

import sso  # noqa: E402
import sso_saml  # noqa: E402
from database import get_db  # noqa: E402
from models import Organization, User  # noqa: E402
from routers import _log_session, _tokens  # noqa: E402

router = APIRouter(prefix="/auth/sso", tags=["sso"])
start_limiter = make_limiter(60, 900, name="sso_start")


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    return org


def _admin(request: Request) -> str:
    org = _org(request)
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an admin can change single sign-on")
    return org


async def _cfg(org_id: str) -> SsoConfig | None:
    async with pm.session() as s:
        return (await s.execute(select(SsoConfig).where(SsoConfig.org_id == str(org_id)))).scalar_one_or_none()


async def _org_by_code(db: AsyncSession, code: str) -> Organization | None:
    code = (code or "").strip().upper()
    if not code:
        return None
    return (await db.execute(select(Organization).where(func.upper(Organization.code) == code))).scalar_one_or_none()


def _fail(message: str) -> RedirectResponse:
    return RedirectResponse(f"{sso.app_base()}/login?sso_error={quote(message[:200])}", status_code=303)


# ── admin ────────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config(request: Request, db: AsyncSession = Depends(get_db)):
    org = _admin(request)
    o = (await db.execute(select(Organization).where(Organization.id == org))).scalar_one_or_none()
    c = await _cfg(org)
    out = sso.public_config(c, o.code if o else "")
    out["missing"] = sso.missing_for(c) if c else ["Not set up"]
    out["protocols"] = sso.PROTOCOLS
    out["roles"] = [{"key": k, "label": identity.ROLE_LABELS[k]} for k in identity.ASSIGNABLE_ROLES if k != "org_admin"]
    return out


class SsoIn(BaseModel):
    protocol: str | None = None
    enabled: bool | None = None
    required: bool | None = None
    display_name: str | None = None
    discovery_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None          # omitted / "" = keep
    idp_entity_id: str | None = None
    idp_sso_url: str | None = None
    idp_cert_pem: str | None = None           # omitted / "" = keep
    attr_email: str | None = None
    attr_employee_number: str | None = None
    jit_enabled: bool | None = None
    jit_default_role: str | None = None


@router.put("/config")
async def put_config(body: SsoIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _admin(request)
    patch = body.model_dump(exclude_none=True)
    if "protocol" in patch and patch["protocol"] not in sso.PROTOCOLS:
        raise HTTPException(422, "Protocol must be oidc or saml")
    if "jit_default_role" in patch:
        try:
            patch["jit_default_role"] = identity.normalise_role(patch["jit_default_role"])
        except identity.IdentityError as e:
            raise _refusal(422, e, "SSO_ROLE_INVALID", fallback="Pick a valid default role.")
        if patch["jit_default_role"] == "org_admin":
            raise HTTPException(422, "A sign-on cannot create admins. Pick another default role.")
    for k in ("discovery_url", "idp_sso_url"):
        if patch.get(k) and not patch[k].startswith("https://"):
            raise HTTPException(422, "The identity provider URLs must start with https://")
    if patch.get("idp_cert_pem") and not sso_saml.cert_fingerprint(patch["idp_cert_pem"]):
        raise HTTPException(422, "The IdP certificate could not be read. Paste the PEM or the base64 body from the metadata.")
    async with pm.session() as s:
        c = (await s.execute(select(SsoConfig).where(SsoConfig.org_id == org))).scalar_one_or_none()
        if not c:
            c = SsoConfig(org_id=org)
            s.add(c)
        for k, v in patch.items():
            if k == "client_secret":
                if v:
                    c.client_secret = sso.encrypt_secret(v)
            elif k == "idp_cert_pem":
                if v:
                    c.idp_cert_pem = v.strip()
            else:
                setattr(c, k, v.strip() if isinstance(v, str) else v)
        if c.enabled and sso.missing_for(c):
            c.enabled = False
            patch["enabled"] = False
            patch["_note"] = "Switched off: missing " + ", ".join(sso.missing_for(c))
        c.updated_by, c.updated_at = request.headers.get("x-user-email"), pm.now_utc()
        await s.commit()
    changed = {k: ("(set)" if k in ("client_secret", "idp_cert_pem") else v) for k, v in patch.items()}
    await record_audit(request, "integration.sso.update", entity_type="sso", entity_id=org,
                       summary="Single sign-on settings changed", meta=changed)
    out = await get_config(request, db)
    if patch.get("_note"):
        out["note"] = patch["_note"]
    return out


@router.post("/test")
async def test_config(request: Request):
    """OIDC: read the discovery document. SAML: read the certificate. No sign-in happens."""
    org = _admin(request)
    c = await _cfg(org)
    if not c:
        return {"ok": False, "message": "Not configured."}
    miss = sso.missing_for(c)
    if miss:
        return {"ok": False, "message": "Not configured: missing " + ", ".join(miss)}
    try:
        if c.protocol == "oidc":
            d = await sso.discover(c.discovery_url)
            msg = f"Discovery OK. Issuer {d['issuer']}."
        else:
            fp = sso_saml.cert_fingerprint(c.idp_cert_pem or "")
            if not fp:
                return {"ok": False, "message": "The IdP certificate could not be read."}
            msg = f"Certificate OK (SHA-256 {fp[:16]}…). Sign-in URL {c.idp_sso_url}."
    except sso.SsoError as e:
        await record_audit(request, "integration.sso.test", entity_type="sso", entity_id=org, summary=e.message)
        return {"ok": False, "message": e.message}
    await record_audit(request, "integration.sso.test", entity_type="sso", entity_id=org, summary=msg)
    return {"ok": True, "message": msg}


# ── public ───────────────────────────────────────────────────────────────────

@router.get("/public/{org_code}")
async def public_info(org_code: str, request: Request, db: AsyncSession = Depends(get_db)):
    o = await _org_by_code(db, org_code)
    if not o or not o.is_active:
        return {"sso": False, "required": False, "display_name": "", "org_name": None}
    return sso.login_page_info(await _cfg(str(o.id)), o.name)


@router.get("/start/{org_code}")
async def start(org_code: str, request: Request, db: AsyncSession = Depends(get_db)):
    wait = start_limiter.hit(f"sso|{client_ip(request)}")
    if wait:
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute.", headers={"Retry-After": str(wait)})
    o = await _org_by_code(db, org_code)
    c = await _cfg(str(o.id)) if o else None
    if not o or not o.is_active or not c or not c.enabled or sso.missing_for(c):
        return _fail("Single sign-on is not set up for this organization code.")
    try:
        if c.protocol == "oidc":
            d = await sso.discover(c.discovery_url)
            nonce = secrets.token_urlsafe(16)
            verifier, challenge = sso.pkce_pair()
            st = sso.make_state(str(o.id), "oidc", {"nonce": nonce, "cv": verifier})
            return RedirectResponse(sso.oidc_authorize_url(d, client_id=c.client_id, state=st, nonce=nonce,
                                                           challenge=challenge), status_code=303)
        rid = sso_saml.new_request_id()
        st = sso.make_state(str(o.id), "saml", {"rid": rid})
        xml = sso_saml.build_authn_request(request_id=rid, sp_entity_id=sso.saml_sp_entity_id(o.code),
                                           acs_url=sso.saml_acs_url(), idp_sso_url=c.idp_sso_url)
        return RedirectResponse(sso_saml.redirect_url(c.idp_sso_url, xml, st), status_code=303)
    except sso.SsoError as e:
        return _fail(e.message)


async def _finish(request: Request, db: AsyncSession, org_id: str, c: SsoConfig, attrs: dict, how: str):
    try:
        who = await sso.resolve_user(org_id, c, attrs)
    except sso.SsoError as e:
        await _log_session(db, request, email=sso.claim(attrs, c.attr_email or "email") or "", user=None,
                           success=False, reason=f"sso_{how}_refused")
        return _fail(e.message)
    except identity.IdentityError as e:
        return _fail(_refusal(400, e, "SSO_USER_REFUSED", fallback="The sign-in could not be completed.").detail)
    u = (await db.execute(select(User).where(User.id == identity._uuid(who["user_id"])))).scalar_one_or_none()
    if not u or not u.is_active:
        return _fail("This login has been disabled. Contact your HR team.")
    await _log_session(db, request, email=u.email, user=u, success=True, reason=f"sso_{how}")
    await identity._audit(org_id, {"user_id": u.id, "email": u.email, "role": u.role}, "auth.sso.login", u.id,
                          f"Signed in with SSO ({how.upper()})", {"jit_created": who["created"]})
    return RedirectResponse(f"{sso.app_base()}/sso/done?code={quote(sso.make_code(str(u.id)))}", status_code=303)


@router.get("/callback/oidc")
async def callback_oidc(request: Request, db: AsyncSession = Depends(get_db)):
    q = request.query_params
    if q.get("error"):
        return _fail(f"The identity provider refused the sign-in ({q.get('error_description') or q['error']}).")
    try:
        st = sso.read_state(q.get("state", ""), "oidc")
        c = await _cfg(st["org_id"])
        if not c or not c.enabled or c.protocol != "oidc":
            return _fail("Single sign-on is switched off for this organization.")
        d = await sso.discover(c.discovery_url)
        tok = await sso.oidc_exchange(d, code=q.get("code", ""), client_id=c.client_id,
                                      client_secret=sso.decrypt_secret(c.client_secret), verifier=st.get("cv", ""))
        claims = sso.oidc_verify_id_token(tok["id_token"], disc=d, client_id=c.client_id, nonce=st.get("nonce", ""))
    except sso.SsoError as e:
        return _fail(e.message)
    return await _finish(request, db, st["org_id"], c, claims, "oidc")


@router.post("/callback/saml")
async def callback_saml(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    try:
        st = sso.read_state(str(form.get("RelayState") or ""), "saml")
        c = await _cfg(st["org_id"])
        if not c or not c.enabled or c.protocol != "saml":
            return _fail("Single sign-on is switched off for this organization.")
        o = (await db.execute(select(Organization).where(Organization.id == st["org_id"]))).scalar_one_or_none()
        key = sso_saml.load_public_key(c.idp_cert_pem or "")
        res = sso_saml.parse_response(str(form.get("SAMLResponse") or ""), public_key=key,
                                      sp_entity_id=sso.saml_sp_entity_id(o.code if o else ""),
                                      acs_url=sso.saml_acs_url(), expected_request_id=st.get("rid"),
                                      idp_entity_id=c.idp_entity_id)
    except (sso.SsoError, sso_saml.SamlError) as e:
        return _fail(_refusal(400, e, "SSO_SAML_REFUSED", fallback="The SAML response could not be accepted.").detail)
    attrs = dict(res["attributes"])
    attrs.setdefault("name_id", res["name_id"])
    return await _finish(request, db, st["org_id"], c, attrs, "saml")


@router.get("/saml/{org_code}/metadata")
async def saml_metadata(org_code: str):
    xml = sso_saml.sp_metadata_xml(sp_entity_id=sso.saml_sp_entity_id(org_code), acs_url=sso.saml_acs_url())
    return Response(content=xml, media_type="application/xml")


class ExchangeIn(BaseModel):
    code: str


@router.post("/exchange")
async def exchange(body: ExchangeIn, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        uid = sso.consume_code(body.code)
    except sso.SsoError as e:
        raise HTTPException(e.status, e.message)
    u = (await db.execute(select(User).where(User.id == identity._uuid(uid)))).scalar_one_or_none()
    if not u or not u.is_active:
        raise HTTPException(403, "This login has been disabled. Contact your HR team.")
    u.last_login = pm.now_utc()
    await db.commit()
    return await _tokens(u, db)
