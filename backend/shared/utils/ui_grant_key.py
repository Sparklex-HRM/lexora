"""Signing key of the signed UI grant (ES256) — one place for env names, loading and the production checks.

Go-live blocker closed 2026-10-26 (docs/ops/GO_LIVE_CHECKLIST.md "UI-grant signing key", docs/security/UI_GRANT.md).

Env (first one set wins; the LEXORA_* names are the production names, the short ones are the 2026-10-24 names the
local compose stack still uses):
    LEXORA_UI_GRANT_PRIVATE_KEY      | UI_GRANT_PRIVATE_KEY       PKCS#8 PEM of a P-256 key ("\\n" escapes accepted)
    LEXORA_UI_GRANT_PRIVATE_KEY_FILE | UI_GRANT_PRIVATE_KEY_FILE  path of a mounted PEM file (k8s / compose secret)
    LEXORA_UI_GRANT_KID              | UI_GRANT_KID               key id pinned in the web build (default "k1")

Production (LEXORA_ENV=production) refuses to boot auth-service when (production_problems):
    * no key is configured, the file is unreadable, or the PEM is not a P-256 private key;
    * the kid is "dev", or is not 1-16 of [A-Za-z0-9_-];
    * the key IS a known development key — whatever kid it is given (public half compared with DEV_PUBLIC_JWKS,
      the same keys frontend/src/config/uiGrantKeys.ts trusts in a dev build; a test keeps the two lists equal).
Messages name settings only — never a key or its value.

Make the production key with scripts/ops/gen_ui_grant_key.py (prints env lines; never writes the private key into
the repository).
"""
from __future__ import annotations

import base64
import os
import re
from typing import Any, Mapping, Optional

DEV_KID = "dev"
DEFAULT_KID = "k1"
KID_RE = re.compile(r"[A-Za-z0-9_-]{1,16}")

PEM_VARS = ("LEXORA_UI_GRANT_PRIVATE_KEY", "UI_GRANT_PRIVATE_KEY")
FILE_VARS = ("LEXORA_UI_GRANT_PRIVATE_KEY_FILE", "UI_GRANT_PRIVATE_KEY_FILE")
KID_VARS = ("LEXORA_UI_GRANT_KID", "UI_GRANT_KID")

# Public halves of every development key ever trusted by a dev build (frontend/src/config/uiGrantKeys.ts DEV_KEYS).
# A production auth-service refuses to sign with any of them. Add a line here AND there when a dev key is remade.
DEV_PUBLIC_JWKS: dict[str, dict[str, str]] = {
    "dev": {"kty": "EC", "crv": "P-256", "x": "2edrAG1YyFVT5_a1ZEuLYljK2VJqlIJmRfvDNp_X_uw",
            "y": "uDRPjmwYKLapQ2fJtievTh2Hnttnr5VNLMQx7kpxPuk"},
}
# Dev keys no longer trusted by any build (the dev key inherited from the Jethro HR fork) — still refused by a production signer
# (frontend RETIRED_DEV_UI_GRANT_KEYS).
RETIRED_DEV_PUBLIC_JWKS: dict[str, dict[str, str]] = {
    "jethro-dev": {"kty": "EC", "crv": "P-256", "x": "ibt5z23uOB_ipM9wQMGwRG1BuPrdSqYoLXsm9OM_Nz4",
                   "y": "AR4QDQO1xAJC54c0ghDGnzPQLU7mUDohYHiKb66cCrk"},
}


class KeyProblem(ValueError):
    """The configured key cannot be used. str() = a reason naming settings only."""


def _first(env: Mapping[str, Any], names: tuple[str, ...]) -> tuple[Optional[str], str]:
    for n in names:
        v = str(env.get(n) or "").strip()
        if v:
            return n, v
    return None, ""


def kid(env: Mapping[str, Any] | None = None) -> str:
    e = os.environ if env is None else env
    return _first(e, KID_VARS)[1] or DEFAULT_KID


def configured(env: Mapping[str, Any] | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(_first(e, PEM_VARS)[1] or _first(e, FILE_VARS)[1])


def read_pem(env: Mapping[str, Any] | None = None) -> str:
    """The PEM text ("" when nothing is configured). Raises KeyProblem when the key file cannot be read."""
    e = os.environ if env is None else env
    _, pem = _first(e, PEM_VARS)
    if not pem:
        name, path = _first(e, FILE_VARS)
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    pem = fh.read().strip()
            except OSError as ex:
                raise KeyProblem(f"{name} points at a file that cannot be read ({type(ex).__name__})") from None
    return pem.replace("\\n", "\n")


def load_private_key(pem: str):
    """P-256 private key object from PEM text, or KeyProblem."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        k = serialization.load_pem_private_key(pem.encode(), password=None)
    except Exception as ex:  # noqa: BLE001
        raise KeyProblem(f"the UI-grant private key is not a readable PKCS#8 PEM ({type(ex).__name__})") from None
    if not isinstance(k, ec.EllipticCurvePrivateKey) or k.curve.name != "secp256r1":
        raise KeyProblem("the UI-grant private key is not an EC P-256 (secp256r1) key")
    return k


def _b64u(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


def public_jwk(private_or_public_key) -> dict[str, str]:
    pub = private_or_public_key.public_key() if hasattr(private_or_public_key, "private_numbers") else private_or_public_key
    nums = pub.public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": _b64u(nums.x), "y": _b64u(nums.y)}


def is_dev_key(key) -> bool:
    j = public_jwk(key)
    return any(d["x"] == j["x"] and d["y"] == j["y"] for d in (*DEV_PUBLIC_JWKS.values(), *RETIRED_DEV_PUBLIC_JWKS.values()))


def production_problems(env: Mapping[str, Any] | None = None) -> list[str]:
    """Reasons (setting names only) why this env must not sign UI grants in production. [] = OK."""
    e = os.environ if env is None else env
    out: list[str] = []
    k_id = kid(e)
    if k_id.lower() == DEV_KID:
        out.append("LEXORA_UI_GRANT_KID / UI_GRANT_KID is the development key id ('dev') — set the production "
                   "signing key (scripts/ops/gen_ui_grant_key.py)")
    elif not KID_RE.fullmatch(k_id):
        out.append("LEXORA_UI_GRANT_KID / UI_GRANT_KID must be 1-16 letters, digits, '-' or '_'")
    if not configured(e):
        out.append("LEXORA_UI_GRANT_PRIVATE_KEY (or _FILE) is unset — no ui_grant can be signed and nobody can "
                   "use the web app (scripts/ops/gen_ui_grant_key.py)")
        return out
    try:
        key = load_private_key(read_pem(e))
    except KeyProblem as ex:
        out.append(str(ex))
        return out
    if is_dev_key(key):
        out.append("LEXORA_UI_GRANT_PRIVATE_KEY is a DEVELOPMENT key (its public half is trusted by dev builds and "
                   "kept in developer .env files) — generate the production key with scripts/ops/gen_ui_grant_key.py")
    return out
