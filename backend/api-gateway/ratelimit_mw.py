"""Gateway-wide request budget per client address, counted in Redis (shared by every
gateway replica) — a flood guard in front of every service, not a business rule.

    LEXORA_GATEWAY_RPM         requests per minute per client address (default 3000; 0 = off)
    LEXORA_GATEWAY_HEAVY_RPM   uploads (multipart / file bodies) and exports / downloads (PDF, Excel,
                               CSV, ZIP, bulk) per minute per client address (default 300; 0 = off)
                               — added 2026-10-24 (go-live): these are the expensive answers
                               (PDF rendering, workbook builds, big bodies) and the bulk-data ones.

Sign-in, invite, sign-up, OTP / MFA, forgot-password and e-sign pages keep their own, much
tighter limits inside the services. Redis down → the in-process twin limits per replica and
the outage is logged (shared/utils/ratelimit.py). /health, /ready and CORS preflights are
never counted.

Client address: with TRUST_FORWARDED_FOR=1 the RIGHT-most X-Forwarded-For entry (the one our
own edge appended) — never the left-most one, which the client writes itself (2026-10-24 fix:
the flood guard used the left-most entry, so a client could pick a fresh "address" per request;
the services' lock-outs already used the right-most one via hardening.client_ip).
"""
import os
import re

from starlette.responses import JSONResponse

from shared.utils.ratelimit import make_limiter

EXEMPT = ("/health", "/ready")

# Exports / downloads / bulk endpoints (path words) and file answers (extensions).
_HEAVY_PATH = re.compile(
    r"/(?:export|exports|download|downloads|bulk|bulk-[a-z-]+|zip|xlsx|csv|pdf|print|backup|archive)(?:/|$)"
    r"|\.(?:pdf|xlsx|xls|csv|zip|txt|xml|json)$|[-_](?:export|pdf|xlsx|csv|zip|download)(?:/|$)", re.I)


def _peer(scope) -> str:
    client = scope.get("client") or ("", 0)
    peer = client[0] or ""
    if os.getenv("TRUST_FORWARDED_FOR", "").lower() in ("1", "true", "yes"):
        for k, v in scope.get("headers") or []:
            if k == b"x-forwarded-for" and v:
                hops = [h.strip() for h in v.decode("latin-1").split(",") if h.strip()]
                if hops:
                    return hops[-1][:64]
    return peer[:64]


def is_heavy(scope) -> bool:
    """An upload (multipart / octet-stream body) or an export / download / bulk request."""
    method = scope.get("method", "GET")
    if method in ("POST", "PUT", "PATCH"):
        for k, v in scope.get("headers") or []:
            if k == b"content-type":
                ct = v.decode("latin-1").lower()
                if ct.startswith(("multipart/form-data", "application/octet-stream")):
                    return True
                break
    return bool(_HEAVY_PATH.search(scope.get("path", "")))


class GatewayRateLimit:
    def __init__(self, app, rpm: int | None = None, limiter=None, heavy_rpm: int | None = None, heavy_limiter=None):
        self.app = app
        self.rpm = int(os.getenv("LEXORA_GATEWAY_RPM", "3000")) if rpm is None else int(rpm)
        self.limiter = limiter or (make_limiter(self.rpm, 60, name="gateway_ip") if self.rpm > 0 else None)
        self.heavy_rpm = int(os.getenv("LEXORA_GATEWAY_HEAVY_RPM", "300")) if heavy_rpm is None else int(heavy_rpm)
        self.heavy = heavy_limiter or (make_limiter(self.heavy_rpm, 60, name="gateway_heavy")
                                       if self.heavy_rpm > 0 else None)

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") == "OPTIONS"
                or scope.get("path", "") in EXEMPT):
            return await self.app(scope, receive, send)
        peer = _peer(scope)
        wait = await self.limiter.ahit(f"ip|{peer}") if self.limiter is not None else 0
        if not wait and self.heavy is not None and is_heavy(scope):
            wait = await self.heavy.ahit(f"heavy|{peer}")
            if wait:
                resp = JSONResponse({"detail": f"Too many uploads / downloads. Wait {wait} second(s) and try again."},
                                    status_code=429, headers={"Retry-After": str(wait)})
                return await resp(scope, receive, send)
        if wait:
            resp = JSONResponse({"detail": f"Too many requests. Wait {wait} second(s) and try again."},
                                status_code=429, headers={"Retry-After": str(wait)})
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)
