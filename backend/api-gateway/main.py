"""Lexora HRM — API Gateway.

The single ingress + identity authority. Validates the JWT once, scrubs any
client-supplied identity headers, stamps trusted ones from the validated token,
and reverse-proxies to the owning microservice. Downstream services trust the
stamped X-Org-Id and never re-verify the token.
"""
import os
import sys

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

sys.path.append("/app")
import jwt  # noqa: E402
import shared.middleware.tenant as _tenant  # noqa: E402
from shared.middleware.tenant import TenantMiddleware, SPOOFABLE  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.security import decode_token  # noqa: E402

import revocation  # noqa: E402
import rbac_gate  # noqa: E402  (2026-10-24: permission catalogue + custom roles)

# Token-authenticated public pages added by the login work: "set your password"
# (invite link), "forgot password" and "reset password". The secret link token
# in the path / the e-mail + org code is the credential, not a JWT.
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/auth/invite", "/auth/password/forgot", "/auth/password/reset")

# Stream D (hiring) — separate addition. ONE public prefix, no login:
#   /hiring/public/careers/{org_code}[/{job}[/apply]]   the org's careers page + apply form
#   /hiring/public/offer/{token}[/accept|/decline]      a candidate answering an offer link
# The org comes from the code / the offer token in the path, never from a header;
# the routes are rate-limited in the service and expose only the org name, its
# published jobs and its privacy text. The trailing slash keeps "/hiring/publicX" private.
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/hiring/public/",)

# Stream G3 (platform & integrations) — public paths whose credential is NOT a JWT:
#   /auth/sso/{public,start,callback,saml,exchange}  IdP round-trip (signed state / one-time code)
#   /auth/scim/v2/                                   per-org SCIM bearer token, checked in the service
#   /auth/esign/public/                              signing link token + one-time code
#   /notifications/chatops/*/command                 Slack / Teams request signature
#   /notifications/whatsapp/webhook                  Meta / Twilio receipt signature
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + (
    "/auth/sso/public/", "/auth/sso/start/", "/auth/sso/callback/", "/auth/sso/saml/", "/auth/sso/exchange",
    "/auth/scim/v2/", "/auth/esign/public/",
    "/notifications/chatops/slack/command", "/notifications/chatops/teams/command", "/notifications/whatsapp/webhook")
import apikey_auth  # noqa: E402  (G3: org API keys accepted instead of a JWT)
# Stream S4: public sign-up / MFA step / payment webhook; ended sessions + read-only subscription.
import saas_gate  # noqa: E402
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + saas_gate.PUBLIC_PATHS
# S3 readiness probe (k8s / compose / load balancer) must answer without a token.
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/ready",)
# Stream D3: the MCP server (POST /ai/mcp, /ai/mcp/tools|call|whoami). The credential is the
# org-scoped `lxai_` token checked inside the ai_platform service; client identity headers are
# still scrubbed above, so the token is the ONLY source of org / user / scopes.
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/ai/mcp",)
# WASA hardening 2026-10-21: our own proof-of-work CAPTCHA challenge (login / forgot / signup / MFA).
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/auth/challenge",)
# The login-page footer (org name + published Privacy Officer contact, PIPEDA) is public BY DESIGN. It was public
# before only by accident ("/auth/login" prefix); the exact-boundary match now needs it listed.
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/auth/login-info",)

# Gap G3 (2026-10-23): biometric terminals push punches without a login. ZKTeco / eSSL ADMS firmware calls the
# fixed root path /iclock/* (serial + per-device IP allow-list); token paths /time/device-push/{adms,cosec,http}/<token>
# for Matrix COSEC and generic HTTP push. The time service binds the org from the device registration only,
# rate-limits, caps bodies and answers "OK"/counts — never data. Client identity headers are scrubbed as always.
_tenant.PUBLIC = tuple(_tenant.PUBLIC) + ("/iclock/", "/time/device-push/")

# Headers only the gateway may set. X-Branch-Scope is stamped below too.
_GATEWAY_OWNED = tuple(SPOOFABLE) + ("x-branch-scope",) + saas_gate.OWNED_HEADERS


class ScrubIdentityHeaders:
    """Outermost layer: drop every identity header a CLIENT sent, before anything
    reads them. Without this, TenantMiddleware's service-to-service shortcut
    ("an X-Org-Id header means the gateway already vouched for you") would let
    anyone skip the JWT simply by sending X-Org-Id / X-Role to the gateway."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["headers"] = [(k, v) for k, v in scope.get("headers", [])
                                if k.decode("latin-1").lower() not in _GATEWAY_OWNED]
        await self.app(scope, receive, send)

app = FastAPI(title="Lexora HRM — API Gateway", version="0.1.0")

import hardening  # noqa: E402  (WASA 2026-10-21: host allow-list, forced password change, client IP, headers)

app.add_middleware(
    CORSMiddleware,
    # Explicit allow-list (no '*'; production refuses '*', http:// and localhost origins — ops_runtime).
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS if o.strip() and o.strip() != "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    max_age=600,
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
)
# Runs inside-out: TenantMiddleware validates the JWT and binds request.state.
app.add_middleware(TenantMiddleware)
# G3: runs after the scrub, before TenantMiddleware — an `lxk_…` API key becomes
# trusted identity headers (org + bounded role) or a 401/403; a JWT passes untouched.
app.add_middleware(apikey_auth.ApiKeyAuth)
# Added last = runs first: client identity headers never reach TenantMiddleware.
app.add_middleware(ScrubIdentityHeaders)
# S1: per-client-address flood guard, counted in Redis across gateway replicas (ratelimit_mw.py).
from ratelimit_mw import GatewayRateLimit  # noqa: E402
app.add_middleware(GatewayRateLimit)
# WASA #6 (host header): outermost — a request for any other Host never reaches a route.
hardening.install_host_allowlist(app)

# prefix -> upstream base URL
SERVICES = {
    "auth": settings.AUTH_URL,
    "employees": settings.EMPLOYEE_URL,
    "masters": settings.EMPLOYEE_URL,   # master data lives in the employee/people service
    "exit": settings.EMPLOYEE_URL,      # exit cases (resignation → clearance → settlement) live there too
    "letters": settings.EMPLOYEE_URL,   # the letter engine (templates, issued letters, PDFs) lives there too
    "hiring": settings.EMPLOYEE_URL,        # Stream D: requisitions, candidates, interviews, offers
    "performance": settings.EMPLOYEE_URL,   # Stream D: goals, appraisal cycles, increment proposals
    "onboarding": settings.ONBOARDING_URL,
    "payroll": settings.PAYROLL_URL,
    "time": settings.TIME_URL,
    "leave": settings.LEAVE_URL,
    "approvals": settings.APPROVALS_URL,
    "notifications": settings.NOTIFICATION_URL,
    "settings": settings.NOTIFICATION_URL,   # company settings live in the notification service
    "reports": settings.APPROVALS_URL,       # reports + dashboard live in the approvals service
    "ai": settings.AI_PLATFORM_URL,
    "ops": settings.OPS_URL,
    "compliance": settings.COMPLIANCE_URL,
    # Stream G4: engagement (helpdesk, surveys, kudos, LMS, prefs) lives in the ops service but is
    # core (not behind the "ops" module paywall); the asset register lives in the employee service.
    "engage": settings.OPS_URL,
    "assets": settings.EMPLOYEE_URL,
    "integrations": os.getenv("INTEGRATIONS_SERVICE_URL", "http://integrations-service:8000"),   # S5 live provider adapters
    "iclock": settings.TIME_URL,            # G3 2026-10-23: ZKTeco / eSSL ADMS push (fixed device path /iclock/*)
}

# prefix -> the subscription module that gates it. "core" is always on, so
# auth/employees/onboarding/approvals/notifications stay reachable on every plan.
# Anything not listed defaults to "core" (open). This is the SERVER-SIDE paywall
# that backs the frontend nav gating — an org that didn't buy a module can't
# reach its API even by calling the gateway directly.
SERVICE_MODULES = {
    "payroll": "payroll",
    "time": "time",
    "leave": "leave",
    "ops": "ops",
    "ai": "ai",
    "compliance": "compliance",
    "hiring": "talent",          # Stream D — same module the Talent menu group is gated on
    "performance": "talent",
}


# Routes inside a paid service that must still work on EVERY plan (none today).
ALWAYS_ON_PREFIXES: tuple[str, ...] = ()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "api-gateway"}


def _stamp_identity(request: Request) -> dict:
    """Trusted identity headers built from validated state (never from client)."""
    st = request.state
    return {
        "X-Org-Id": getattr(st, "org_id", "") or "",
        "X-User-Id": getattr(st, "user_id", "") or "",
        "X-User-Email": getattr(st, "user_email", "") or "",
        "X-Role": getattr(st, "role", "") or "",
        "X-Employee-Id": getattr(st, "employee_id", "") or "",
        # Resolved branch scope (Phase 2): branch users → their branch; org users →
        # the switched view branch, or empty = all branches. Empty means "no filter".
        "X-Branch-Id": getattr(st, "branch_id", "") or "",
        "X-Branch-Scope": getattr(st, "branch_scope", "") or "branch",
        # Subscribed modules, forwarded so a service can enforce a finer paywall
        # if it ever needs to (the gateway already gates at the service level).
        "X-Modules": ",".join(getattr(st, "modules", None) or []),
        # 2026-10-24: set only when a custom role acts on this request (rbac_gate.apply)
        "X-Acting-Role": getattr(st, "acting_role", "") or "",
        "X-Data-Scope": getattr(st, "data_scope", "") or "",
        # 2026-10-26: with X-Acting-Role — the data classes that custom role holds (shared/utils/roles.holds_data)
        "X-Acting-Data": getattr(st, "acting_data", "") or "",
    }


def _module_denied(service: str, request: Request):
    """Return a 403 JSONResponse if the caller's org hasn't subscribed to the
    module that owns `service`; else None. superadmin bypasses. A token minted
    before module-claims existed (modules is None) fails OPEN for a clean rollout
    — it'll be enforced on the user's next login."""
    required = SERVICE_MODULES.get(service)
    if not required:
        return None                                   # core / unmapped → always on
    if any(request.url.path.startswith(p) for p in ALWAYS_ON_PREFIXES):
        return None                                   # legal duties are never behind a paywall
    if (getattr(request.state, "role", "") or "") == "superadmin":
        return None
    modules = getattr(request.state, "modules", None)
    if modules is None:
        return None                                   # backward-compat: pre-claim token
    if required not in modules:
        return JSONResponse(
            {"detail": "module_not_subscribed", "module": required},
            status_code=403,
        )
    return None


async def _session_denied(request: Request):
    """Refuse a request whose login was disabled / changed after the token was
    issued, and refresh role + employee id from the login's live state."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return None                                   # public path — no login involved
    iat = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            iat = decode_token(auth.split(" ", 1)[1]).get("iat")
        except jwt.PyJWTError:
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
    reason, state = await revocation.check(uid, iat)
    request.state.login_state = state          # 2026-10-24: custom roles for rbac_gate
    if reason == "unavailable":
        return JSONResponse({"detail": revocation.MESSAGES[reason]}, status_code=503)
    if reason:
        return JSONResponse({"detail": revocation.MESSAGES[reason]}, status_code=401)
    if state:
        request.state.role = state.get("role") or request.state.role
        request.state.employee_id = state.get("employee_id")
    return None


def _client_ip(request: Request) -> str:
    """The address the services should rate-limit on. A client-sent
    X-Forwarded-For is only believed when a trusted proxy sits in front
    (TRUST_FORWARDED_FOR=1); otherwise it is replaced, so it cannot be used
    to dodge the sign-in lockout."""
    return hardening.client_ip(request)


async def _proxy(service: str, path: str, request: Request):
    base = SERVICES.get(service)
    if not base:
        return JSONResponse({"detail": f"Service '{service}' not found"}, status_code=404)

    # Disabled login / ended session: stop here, before any service is called.
    denied = await _session_denied(request)
    if denied is not None:
        return denied

    # WASA #7: a login whose password was chosen by someone else must change it first.
    denied = hardening.password_change_denied(request, f"/{service}" + (f"/{path}" if path else ""))
    if denied is not None:
        return denied

    # Subscription paywall: block a service whose module the org didn't buy.
    denied = _module_denied(service, request)
    if denied is not None:
        return denied

    # S4: ended session (sid revoked) → 401; read-only subscription → 402 on writes.
    denied = await saas_gate.check(request, service, path)
    if denied is not None:
        return denied

    # 2026-10-24 org Roles & Permissions: the permission catalogue (deny by default) + custom roles.
    rbac = rbac_gate.decide(request.method, f"/{service}" + (f"/{path}" if path else ""),
                            getattr(request.state, "role", None), getattr(request.state, "login_state", None))
    if rbac.deny:
        return JSONResponse({"detail": rbac_gate.DENY_MESSAGE, "code": rbac.code or "RBAC_DENIED"}, status_code=403)
    rbac_gate.apply(request, rbac)

    # Scrub any client-supplied identity, then stamp our validated ones.
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in SPOOFABLE and k.lower() not in ("host", "content-length")}
    headers = {k: v for k, v in headers.items() if k.lower() not in ("x-branch-scope", "x-forwarded-for")}
    headers.update(_stamp_identity(request))
    headers = {k: v for k, v in headers.items() if k.lower() != "x-session-id"}
    headers.update(saas_gate.stamp(request))
    headers["X-Forwarded-For"] = _client_ip(request)

    url = f"{base}/{service}" + (f"/{path}" if path else "")
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("LEXORA_UPSTREAM_TIMEOUT_SEC", "30"))) as client:
            r = await client.request(request.method, url, params=request.query_params,
                                     content=body, headers=headers)
    except httpx.ConnectError:
        return JSONResponse({"detail": f"'{service}' unavailable"}, status_code=503)
    except httpx.TimeoutException:
        return JSONResponse({"detail": f"'{service}' timed out"}, status_code=504)
    # Pass through the few response headers a browser needs for downloads, lock-outs and the
    # SSO round-trip (a 303 from /auth/sso/start or /callback is useless without its Location).
    extra = {h: r.headers[h] for h in hardening.PASS_THROUGH_HEADERS if h in r.headers}
    extra.setdefault("x-content-type-options", "nosniff")
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type"), headers=extra)


import search_fanout  # noqa: E402  (2026-10-20 global search)


@app.get("/search")
async def global_search(request: Request, q: str = "", types: str | None = None, limit: int = 8):
    """Federated record search (search_fanout.py): parallel fan-out to each owning service's
    /…/search with the trusted identity headers; each service enforces its own org + role scope."""
    if not getattr(request.state, "user_id", None):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    denied = await _session_denied(request)
    if denied is not None:
        return denied
    denied = hardening.password_change_denied(request, "/search")
    if denied is not None:
        return denied
    denied = await saas_gate.check(request, "search", "")
    if denied is not None:
        return denied
    headers = _stamp_identity(request)
    headers.update(saas_gate.stamp(request))
    headers["X-Forwarded-For"] = _client_ip(request)
    out = await search_fanout.federate(
        q, types, limit, headers=headers, services=SERVICES,
        modules=getattr(request.state, "modules", None), role=(getattr(request.state, "role", "") or ""))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.api_route("/{service}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_root(service: str, request: Request):
    return await _proxy(service, "", request)


@app.api_route("/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(service: str, path: str, request: Request):
    return await _proxy(service, path, request)

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "api-gateway")
