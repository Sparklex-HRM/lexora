"""Signed UI grant (HMS Entersoft finding "UI role tampering via login response", HIGH 7.1 — Lexora HRM fix 2026-10-24).

The access token is HS256 (JWT_SECRET): the browser cannot check it, so a role / permission list in a login or
/me answer was trusted as-is and editing it in a proxy painted another role's menus (every API call was still
refused server-side). `ui_grant` is a compact JWS, ES256 (ECDSA P-256 / SHA-256):

    header  {"alg": "ES256", "kid": <UI_GRANT_KID>, "typ": "jethro-ui-grant"}
    payload {"typ": "jethro-ui-grant", "sub", "org_id", "sid", "role" (system role), "roles" (system + custom role
             keys), "ui_roles" (system roles the UI may draw: the system role + each custom role's acting role),
             "perms" (effective permission keys), "reach", "modules", "iat", "exp"}
             iat / exp / sub / sid copied from the access token it is issued with.

The web app pins the PUBLIC key(s) in its bundle (frontend/src/config/uiGrantKeys.ts — never fetched) and takes
role, roles, permissions and modules ONLY from a grant that verifies and names the same sub + sid as its access
token. Honest scope: whoever rewrites the JavaScript in their own browser can still draw any menu on their own
screen; the server (gateway + services, security/authz) refuses the calls.

Keys (env only — never in code, repo, DB or logs; docs/security/UI_GRANT.md; loader shared/utils/ui_grant_key.py):
    LEXORA_UI_GRANT_PRIVATE_KEY       PKCS#8 PEM of a P-256 key ("\\n" escapes accepted), or
    LEXORA_UI_GRANT_PRIVATE_KEY_FILE  path to that PEM file (a mounted secret)
    LEXORA_UI_GRANT_KID               its key id, pinned in the web build (default "k1"; "dev" = the local dev key)
    (the 2026-10-24 short names UI_GRANT_PRIVATE_KEY / _FILE / UI_GRANT_KID still work when the LEXORA_* one is unset)
No usable key -> NO grant is signed (the web app then refuses to sign in) and a CRITICAL line is logged.
The local dev key (kid "dev", made by scripts/security/make_ui_grant_key.py, kept in the git-ignored .env) is
refused when LEXORA_ENV=production — under ANY kid (its public half is compared) — and the production boot guard
(shared/ops_runtime.py) refuses to start auth-service at all; only a vite dev build (or VITE_UI_GRANT_TRUST_DEV=1)
trusts it. Production key: scripts/ops/gen_ui_grant_key.py.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

import jwt

log = logging.getLogger("auth.ui_grant")

GRANT_TYP = "jethro-ui-grant"
ALG = "ES256"
DEV_KID = "dev"

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _is_production() -> bool:
    return (os.getenv("LEXORA_ENV") or "").strip().lower() in ("production", "prod")


def _load_key() -> Optional[tuple[str, Any]]:
    """(kid, private key object) or None. Read once per process (shared/utils/ui_grant_key.py)."""
    from shared.utils import ui_grant_key as K
    with _lock:
        if "key" in _cache:
            return _cache["key"]
        key = None
        kid = K.kid()
        try:
            pem = K.read_pem()
            if not pem:
                log.critical("LEXORA_UI_GRANT_PRIVATE_KEY not set — no ui_grant is signed; the web app refuses "
                             "sessions without one (docs/security/UI_GRANT.md)")
            elif kid == DEV_KID and _is_production():
                log.critical("UI grant: the DEV key (kid 'dev') is refused in production — set the production key")
            else:
                k = K.load_private_key(pem)
                if _is_production() and K.is_dev_key(k):
                    log.critical("UI grant: a DEVELOPMENT key is refused in production (kid %r) — set the "
                                 "production key (scripts/ops/gen_ui_grant_key.py)", kid)
                else:
                    key = (kid, k)
                    if kid == DEV_KID:
                        log.warning("UI grant: signing with the local DEV key (kid 'dev') — development only")
        except K.KeyProblem as e:
            log.critical("UI grant key unusable (%s) — the web app cannot sign in", e)
        _cache["key"] = key
        return key


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()


def signer_ready() -> bool:
    return _load_key() is not None


def grant_payload(claims: dict, access: dict) -> dict:
    """`claims` = the access token's (decoded) claims; `access` = the live answer of rbac_access.user_access()."""
    return {
        "typ": GRANT_TYP,
        "sub": str(claims.get("sub") or ""),
        "org_id": str(claims["org_id"]) if claims.get("org_id") else None,
        "sid": claims.get("sid"),
        "role": str(access.get("role") or ""),
        "roles": list(access.get("roles") or []),
        "ui_roles": list(access.get("ui_roles") or []),
        "perms": sorted(access.get("perms") or []),
        "custom_perms": sorted(access.get("custom_perms") or []),
        "reach": access.get("reach") or "own",
        "modules": claims.get("modules"),
        "iat": int(claims["iat"]) if claims.get("iat") is not None else None,
        "exp": int(claims["exp"]),
    }


def sign(claims: dict, access: dict) -> Optional[str]:
    """Signed grant for verified access-token claims + the login's live access. None when no key or unusable."""
    key = _load_key()
    if key is None:
        return None
    kid, k = key
    try:
        if not claims.get("sub") or not access.get("role") or claims.get("exp") is None:
            return None
        return jwt.encode(grant_payload(claims, access), k, algorithm=ALG, headers={"kid": kid, "typ": GRANT_TYP})
    except Exception as e:  # noqa: BLE001
        log.error("ui_grant signing failed: %s", type(e).__name__)
        return None


def claims_of_bearer(authorization: str | None) -> Optional[dict]:
    """The verified ACCESS token claims of an `Authorization: Bearer …` header, or None."""
    h = authorization or ""
    if not h.lower().startswith("bearer "):
        return None
    try:
        from shared.utils.security import decode_token
        c = decode_token(h[7:].strip())
    except Exception:  # noqa: BLE001
        return None
    return c if c.get("type") == "access" else None


def verify(token: str, public_keys: dict[str, Any]) -> dict:
    """Verify a grant against {kid: public key}. Raises ValueError on any failure (tests / services)."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise ValueError("malformed grant") from e
    if header.get("alg") != ALG:
        raise ValueError("wrong alg")
    kid = header.get("kid")
    if kid not in public_keys:
        raise ValueError("unknown kid")
    try:
        payload = jwt.decode(token, public_keys[kid], algorithms=[ALG], options={"require": ["exp"]})
    except jwt.ExpiredSignatureError as e:
        raise ValueError("expired") from e
    except jwt.PyJWTError as e:
        raise ValueError("bad signature") from e
    if payload.get("typ") != GRANT_TYP:
        raise ValueError("wrong typ")
    return payload
