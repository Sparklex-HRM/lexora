"""Proof-of-work CAPTCHA helper for our own scripts (WASA hardening 2026-10-21).

The auth service refuses POST /auth/login, /auth/register-org, /auth/password/forgot,
/auth/signup and the MFA sign-in steps without a solved ``captcha_token``
(CAPTCHA_MODE=pow, the default). This asks GET /auth/challenge, solves it with
the server's own reference solver (backend/services/auth/pow_captcha.py, loaded by
path — stdlib only) and returns the token. With CAPTCHA_MODE=off the challenge
says ``enabled: false`` and nothing is added.

    import _captcha
    body = _captcha.attach(GW, "/auth/login", {"email": ..., "password": ...})

Honest limit: a script can of course solve it — that is the point of a work cost,
not a proof of a human. Rate limits / lockout remain the real guard.
"""
from __future__ import annotations

import importlib.util
import json
import urllib.parse
import urllib.request
from pathlib import Path

_POW = None
PURPOSE_BY_PATH = {
    "/auth/login": "login",
    "/auth/register-org": "signup",
    "/auth/signup": "signup",
    "/auth/password/forgot": "forgot",
    "/auth/mfa/challenge/verify": "mfa",
    "/auth/mfa/challenge/enroll/finish": "mfa",
}


def _pow():
    global _POW
    if _POW is None:
        path = Path(__file__).resolve().parents[1] / "backend" / "services" / "auth" / "pow_captcha.py"
        spec = importlib.util.spec_from_file_location("jethro_pow_captcha", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _POW = mod
    return _POW


def token(gw: str, purpose: str = "login", account: str | None = None) -> str | None:
    """A solved captcha_token, or None when the check is off (or the service is
    older than the CAPTCHA and has no /auth/challenge)."""
    q = {"purpose": purpose}
    if account:
        q["account"] = account
    url = gw.rstrip("/") + "/auth/challenge?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            ch = json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None
    if not ch.get("enabled", True) or ch.get("mode") == "off" or "salt" not in ch:
        return None
    pc = _pow()
    return pc.encode_token(ch, pc.solve(ch), interactive=bool(ch.get("interactive")))


def attach(gw: str, path: str, body):
    """Add a fresh captcha_token to `body` (a dict, changed in place and returned)
    when `path` is one of the protected pre-login routes and none is set yet."""
    p = (path or "").split("?", 1)[0].rstrip("/")
    purpose = PURPOSE_BY_PATH.get(p)
    if purpose is None or not isinstance(body, dict) or body.get("captcha_token"):
        return body
    account = body.get("email") or body.get("admin_email")
    tok = token(gw, purpose, account)
    if tok:
        body["captcha_token"] = tok
    return body
