"""API gateway — WASA hardening (2026-10-21), mirrors the HMS round-1/2 fixes.

1. Host allow-list (WASA "Host header"): LEXORA_ALLOWED_HOSTS = comma list of host names
   (no port; "*.example.com" wildcards allowed). A request for any other Host gets
   400 before any route runs. Unset = every host (local development only — production
   compose / Helm set it from the public domain).
2. Forced password change (WASA "no forced password change"): a token carrying
   "pwchg": true (issued while users.must_change_password is on — the operator or an
   admin chose the password) may only reach the few routes needed to change it.
3. Client IP: with TRUST_FORWARDED_FOR=1 the address the services rate-limit on is the
   RIGHT-most X-Forwarded-For entry (the one OUR edge proxy appended), never the
   left-most one, which the client writes itself and could use to dodge lock-outs.
4. Response headers passed through from the services: downloads, lock-outs, redirects
   and the per-file safety headers (CSP sandbox on uploaded files, cache rules).
"""
from __future__ import annotations

import fnmatch
import os

import jwt
from fastapi.responses import JSONResponse

from shared.utils.security import decode_token

# ── 1. host allow-list ───────────────────────────────────────────────────────


def allowed_hosts() -> list[str]:
    raw = (os.getenv("LEXORA_ALLOWED_HOSTS") or "").strip()
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def host_ok(host_header: str | None, allowed: list[str]) -> bool:
    if not allowed or "*" in allowed:
        return True
    host = (host_header or "").strip().lower()
    if host.startswith("["):                       # [::1]:8200
        host = host[1:].split("]", 1)[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return any(fnmatch.fnmatchcase(host, pat) for pat in allowed)


class HostAllowList:
    """Pure ASGI (outermost): 400 for a Host outside LEXORA_ALLOWED_HOSTS."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            allowed = allowed_hosts()
            if allowed:
                host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")
                path = scope.get("path", "")
                # in-cluster probes call by pod / service name
                if path not in ("/health", "/ready") and not host_ok(host, allowed):
                    resp = JSONResponse({"detail": "Unknown host"}, status_code=400)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def install_host_allowlist(app) -> None:
    app.add_middleware(HostAllowList)


# ── 2. forced password change ────────────────────────────────────────────────
PWCHG_ALLOWED = ("/auth/me", "/auth/change-password", "/auth/logout", "/auth/password-policy",
                 "/auth/login-info", "/auth/challenge")
PWCHG_DETAIL = "password_change_required"


def _claims(request) -> dict:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return {}
    try:
        return decode_token(auth.split(" ", 1)[1])
    except jwt.PyJWTError:
        return {}


def password_change_denied(request, full_path: str):
    """403 password_change_required for a pwchg token outside the change-password routes."""
    if not getattr(request.state, "user_id", None):
        return None
    if not _claims(request).get("pwchg"):
        return None
    p = full_path.rstrip("/") or "/"
    if any(p == a or p.startswith(a + "/") for a in PWCHG_ALLOWED):
        return None
    return JSONResponse({"detail": PWCHG_DETAIL,
                         "message": "Choose a new password before you continue."}, status_code=403)


# ── 3. client IP ─────────────────────────────────────────────────────────────


def client_ip(request) -> str:
    """The address the services rate-limit on (one value, sent as X-Forwarded-For)."""
    peer = request.client.host if request.client else ""
    if os.getenv("TRUST_FORWARDED_FOR", "").lower() in ("1", "true", "yes"):
        fwd = request.headers.get("x-forwarded-for") or ""
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            return hops[-1][:64]                  # appended by our edge proxy; the rest is client-written
    return peer


# ── 4. response headers passed through ───────────────────────────────────────
PASS_THROUGH_HEADERS = ("content-disposition", "x-content-type-options", "retry-after", "location",
                        "content-security-policy", "cache-control", "x-frame-options",
                        "cross-origin-resource-policy",
                        "x-content-sha256")          # agentic phase 3: integrity of a downloaded filing artefact (Compliance Clerk)
