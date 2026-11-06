"""Auth service — single sign-on (stream G3): OIDC + the pieces SAML shares.

Libraries: PyJWT 2.10 (already in the image, with ``cryptography`` → RS256/ES256
id-token verification through ``jwt.PyJWKClient``) and httpx. No new dependency.

Flow (both protocols):
  1. /auth/sso/start/{org_code}   builds a SIGNED state (HS256 JWT, 10 min: org,
     nonce, PKCE verifier / SAML request id) and redirects to the IdP.
  2. the IdP sends the browser back to the public callback; the state is verified,
     the IdP's answer is verified (id_token signature + nonce / SAML signature +
     conditions), the person is resolved (or JIT-created) and a ONE-TIME exchange
     code (HS256 JWT, 60 s, single use) is put on the frontend URL /sso/done.
  3. the frontend posts the code to /auth/sso/exchange and gets the SAME tokens
     ``/auth/login`` issues (``routers._tokens``). Nothing else changes.

Secrets: the OIDC client secret is encrypted with shared/utils/crypto (the same
scheme as the SIN). The IdP certificate is public and stored as PEM.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import uuid

import jwt
import sqlalchemy as sa

from shared.utils import crypto, identity
from shared.utils.config import settings
from shared.utils.providers import http
from shared.utils.providers.models import SsoConfig

SECRET_AAD = "int:sso:client_secret"
STATE_TTL = 600
CODE_TTL = 60
PROTOCOLS = {"oidc": "OpenID Connect (Azure AD / Entra, Okta, Google)", "saml": "SAML 2.0"}


class SsoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# ── URLs ─────────────────────────────────────────────────────────────────────

def app_base() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def api_base() -> str:
    """Where the IdP can reach the gateway from the browser (the frontend proxies /api)."""
    return os.getenv("SSO_PUBLIC_API_BASE", app_base() + "/api").rstrip("/")


def oidc_redirect_uri() -> str:
    return api_base() + "/auth/sso/callback/oidc"


def saml_acs_url() -> str:
    return api_base() + "/auth/sso/callback/saml"


def saml_sp_entity_id(org_code: str) -> str:
    return f"{api_base()}/auth/sso/saml/{(org_code or '').upper()}/metadata"


# ── signed state / exchange code (pure) ──────────────────────────────────────

def make_state(org_id: str, protocol: str, extra: dict | None = None) -> str:
    now = int(time.time())
    payload = {"type": "sso_state", "org_id": str(org_id), "protocol": protocol, "iat": now,
               "exp": now + STATE_TTL, "jti": uuid.uuid4().hex, **(extra or {})}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def read_state(token: str, protocol: str) -> dict:
    try:
        st = jwt.decode(token or "", settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise SsoError("This sign-in attempt has expired. Start again.", 400)
    if st.get("type") != "sso_state" or st.get("protocol") != protocol:
        raise SsoError("This sign-in attempt is not valid. Start again.", 400)
    return st


_used_codes: dict[str, float] = {}


def make_code(user_id: str) -> str:
    now = int(time.time())
    return jwt.encode({"type": "sso_code", "sub": str(user_id), "iat": now, "exp": now + CODE_TTL,
                       "jti": uuid.uuid4().hex}, settings.JWT_SECRET, algorithm="HS256")


def consume_code(code: str, now: float | None = None) -> str:
    """Returns the user id; a code works ONCE (in-process) and for 60 s."""
    t = now if now is not None else time.time()
    try:
        c = jwt.decode(code or "", settings.JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    except jwt.PyJWTError:
        raise SsoError("This sign-in code is not valid.", 401)
    if c.get("type") != "sso_code" or not isinstance(c.get("exp"), int):
        raise SsoError("This sign-in code is not valid.", 401)
    if c["exp"] < t:
        raise SsoError("This sign-in code has expired. Sign in again.", 401)
    for k in [k for k, exp in _used_codes.items() if exp < t]:
        _used_codes.pop(k, None)
    if c["jti"] in _used_codes:
        raise SsoError("This sign-in code was already used. Sign in again.", 401)
    _used_codes[c["jti"]] = c["exp"]
    return c["sub"]


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# ── config helpers ───────────────────────────────────────────────────────────

def public_config(c: SsoConfig | None, org_code: str) -> dict:
    if not c:
        return {"protocol": "oidc", "enabled": False, "required": False, "display_name": "", "discovery_url": "",
                "client_id": "", "client_secret_set": False, "idp_entity_id": "", "idp_sso_url": "",
                "idp_cert_set": False, "attr_email": "email", "attr_employee_number": "", "jit_enabled": False,
                "jit_default_role": "employee", "updated_at": None, "updated_by": None,
                **sp_details(org_code)}
    return {"protocol": c.protocol, "enabled": c.enabled, "required": c.required, "display_name": c.display_name,
            "discovery_url": c.discovery_url, "client_id": c.client_id, "client_secret_set": bool(c.client_secret),
            "idp_entity_id": c.idp_entity_id, "idp_sso_url": c.idp_sso_url, "idp_cert_set": bool(c.idp_cert_pem),
            "attr_email": c.attr_email, "attr_employee_number": c.attr_employee_number,
            "jit_enabled": c.jit_enabled, "jit_default_role": c.jit_default_role,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None, "updated_by": c.updated_by,
            **sp_details(org_code)}


def sp_details(org_code: str) -> dict:
    return {"oidc_redirect_uri": oidc_redirect_uri(), "saml_acs_url": saml_acs_url(),
            "saml_sp_entity_id": saml_sp_entity_id(org_code),
            "saml_metadata_url": f"{api_base()}/auth/sso/saml/{(org_code or '').upper()}/metadata"}


def login_page_info(c: SsoConfig | None, org_name: str | None) -> dict:
    """What the login page may know for an org code: is there a button, and is a password allowed."""
    if not c or not c.enabled:
        return {"sso": False, "required": False, "display_name": "", "org_name": org_name}
    return {"sso": True, "required": bool(c.required), "display_name": c.display_name or "your company sign-in",
            "org_name": org_name}


def password_login_allowed(c: SsoConfig | None, role: str) -> bool:
    """'SSO required' leaves ONE door for admins (break-glass); everyone else must use SSO."""
    if not c or not c.enabled or not c.required:
        return True
    return role in ("org_admin", "superadmin")


def missing_for(c: SsoConfig) -> list[str]:
    if c.protocol == "oidc":
        return [n for n, v in (("Discovery URL", c.discovery_url), ("Client id", c.client_id),
                               ("Client secret", c.client_secret)) if not v]
    return [n for n, v in (("IdP entity id", c.idp_entity_id), ("IdP sign-in URL", c.idp_sso_url),
                           ("IdP certificate", c.idp_cert_pem)) if not v]


# ── OIDC ─────────────────────────────────────────────────────────────────────

_discovery: dict[str, tuple[float, dict]] = {}


async def discover(url: str) -> dict:
    u = (url or "").strip().rstrip("/")
    if not u.endswith("/.well-known/openid-configuration"):
        u = u + "/.well-known/openid-configuration"
    hit = _discovery.get(u)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    r = await http.get_json(u, timeout=8.0)
    if not r.ok or not isinstance(r.json, dict):
        raise SsoError(f"Could not read the identity provider's discovery document ({r.status or r.error}).", 502)
    d = r.json
    for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not d.get(k):
            raise SsoError(f"The discovery document has no {k}.", 502)
    _discovery[u] = (time.monotonic() + 3600, d)
    return d


def oidc_authorize_url(disc: dict, *, client_id: str, state: str, nonce: str, challenge: str) -> str:
    from urllib.parse import urlencode
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": oidc_redirect_uri(),
         "scope": "openid email profile", "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return disc["authorization_endpoint"] + ("&" if "?" in disc["authorization_endpoint"] else "?") + urlencode(q)


async def oidc_exchange(disc: dict, *, code: str, client_id: str, client_secret: str, verifier: str) -> dict:
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": oidc_redirect_uri(),
            "client_id": client_id, "client_secret": client_secret, "code_verifier": verifier}
    r = await http.request("POST", disc["token_endpoint"], data=data, timeout=10.0,
                           headers={"Accept": "application/json"})
    if not r.ok or not isinstance(r.json, dict) or not r.json.get("id_token"):
        raise SsoError("The identity provider did not accept the sign-in. Try again.", 401)
    return r.json


def oidc_verify_id_token(id_token: str, *, disc: dict, client_id: str, nonce: str) -> dict:
    """Signature (JWKS), issuer, audience, expiry and nonce — via PyJWT."""
    try:
        key = jwt.PyJWKClient(disc["jwks_uri"], cache_keys=True, timeout=8).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(id_token, key.key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                            audience=client_id, issuer=disc["issuer"], options={"require": ["exp", "iat", "sub"]})
    except jwt.PyJWTError as e:
        raise SsoError(f"The identity token could not be verified ({type(e).__name__}).", 401)
    if not nonce or claims.get("nonce") != nonce:
        raise SsoError("The identity token does not belong to this sign-in attempt.", 401)
    return claims


def claim(claims: dict, name: str) -> str | None:
    """Read a claim / attribute by name — nested names use dots."""
    v: object = claims
    for part in (name or "").split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(part)
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).strip() if v not in (None, "") else None


# ── user resolution + JIT (shared by both protocols) ─────────────────────────

async def resolve_user(org_id: str, cfg: SsoConfig, attrs: dict) -> dict:
    """Find the login for the IdP's attributes; create it (JIT) when allowed.
    Returns {user_id, email, created}. Raises SsoError with a screen-ready message."""
    email = claim(attrs, cfg.attr_email or "email") or claim(attrs, "preferred_username") or claim(attrs, "upn")
    emp_no = claim(attrs, cfg.attr_employee_number) if cfg.attr_employee_number else None
    if email:
        try:
            email = identity.clean_email(email)
        except identity.IdentityError:
            email = None
    org = identity._uuid(org_id)
    async with identity._sm()() as s:
        u = None
        if emp_no:
            e = (await s.execute(sa.select(identity.employees_ref).where(
                identity.employees_ref.c.org_id == org,
                sa.func.upper(identity.employees_ref.c.employee_number) == emp_no.upper()))).mappings().first()
            if e:
                u = (await s.execute(sa.select(identity.users).where(
                    identity.users.c.org_id == org, identity.users.c.employee_id == e["id"]))).mappings().first()
                if not u and not email:
                    email = e.get("work_email") or e.get("email")
        if not u and email:
            u = (await s.execute(sa.select(identity.users).where(
                identity.users.c.org_id == org, sa.func.lower(identity.users.c.email) == email))).mappings().first()
    if u:
        if not u["is_active"]:
            raise SsoError("This login has been disabled. Contact your HR team.", 403)
        return {"user_id": str(u["id"]), "email": u["email"], "created": False}
    if not cfg.jit_enabled:
        raise SsoError("Your company sign-in worked, but there is no Lexora HRM login for "
                       f"{email or 'this account'} yet. Ask your HR team to create one.", 403)
    if not email:
        raise SsoError("The identity provider did not send an e-mail address, so a login cannot be created.", 403)
    role = identity.normalise_role(cfg.jit_default_role or "employee")
    emp = None
    async with identity._sm()() as s:
        emp = (await s.execute(sa.select(identity.employees_ref).where(
            identity.employees_ref.c.org_id == org,
            sa.func.lower(sa.func.coalesce(identity.employees_ref.c.work_email, identity.employees_ref.c.email)) == email
        ))).mappings().first()
    actor = {"email": "sso", "role": "system"}
    if emp:
        res = await identity.ensure_login_for_employee(org, emp["id"], email, role, send_invite=False,
                                                       first_name=emp.get("first_name"), last_name=emp.get("last_name"),
                                                       actor=actor)
        return {"user_id": res["user_id"], "email": email, "created": res["created"]}
    first = claim(attrs, "given_name") or claim(attrs, "firstName") or (claim(attrs, "name") or email).split(" ")[0]
    last = claim(attrs, "family_name") or claim(attrs, "lastName") or ""
    from shared.utils.security import hash_password
    uid = uuid.uuid4()
    async with identity._sm()() as s:
        await s.execute(sa.insert(identity.users).values(
            id=uid, org_id=org, email=email, hashed_password=hash_password(secrets.token_urlsafe(48)),
            first_name=first[:100], last_name=last[:100], role=role,
            branch_scope="org" if role in identity.ORG_SCOPE_ROLES else "branch",
            is_active=True, password_set=False, created_at=identity.now()))
        await s.commit()
    await identity._audit(org, actor, "auth.user.create", uid, f"Login created by SSO (JIT) for {email}",
                          {"role": role, "jit": True})
    return {"user_id": str(uid), "email": email, "created": True}


def encrypt_secret(v: str | None) -> str | None:
    return crypto.encrypt(v, aad=SECRET_AAD) if v else None


def decrypt_secret(v: str | None) -> str:
    try:
        return crypto.decrypt(v, aad=SECRET_AAD) or ""
    except crypto.CryptoError:
        return ""
