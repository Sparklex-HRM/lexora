"""Lexora HRM — tenant middleware (multi-tenant identity resolution).

Same trust model as the Medfidelity gateway: the API gateway is the ONLY thing
that validates the JWT. It scrubs client-supplied identity headers, then stamps
trusted ones (X-Org-Id, X-User-Id, X-Role, ...) from the validated token before
forwarding. Downstream services trust X-Org-Id and never re-verify the JWT.
"""
import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from shared.utils.security import decode_token

# Paths reachable without a token.
PUBLIC = (
    "/health", "/docs", "/openapi.json", "/redoc",
    "/auth/login", "/auth/refresh", "/auth/register-org",
    # New-hire self-service is authenticated by the secret invite token in the
    # path, not a JWT — the onboarding service derives org_id from the token.
    "/onboarding/invite",
    # Biometric devices (eSSL/ZKTeco/Matrix push) cannot carry a JWT: the time service
    # authenticates them by X-Device-Secret and derives org_id from the device record.
    # The gateway still scrubs every client identity header (SPOOFABLE) before proxying.
    "/time/biometric/webhook/",
)

# Identity headers a client must never set — the gateway owns these.
SPOOFABLE = (
    "x-org-id", "x-user-id", "x-user-email", "x-user-name",
    "x-role", "x-employee-id", "x-branch-id", "x-internal-auth",
    "x-modules",
    # 2026-10-24 org Roles & Permissions: the custom role acting on a request + its data narrowing
    "x-acting-role", "x-data-scope",
    # 2026-10-26: the data classes (pay / identity / personal / audit) the acting custom role holds
    "x-acting-data",
)


def _is_public(path: str) -> bool:
    """Exact path, or a sub-path on a "/" boundary. (Until 2026-10-21 a bare startswith made
    "/auth/login-info" public because "/auth/login" is; an entry ending in "/" is a folder.)"""
    for p in PUBLIC:
        if path == p or path.startswith(p.rstrip("/") + "/"):
            return True
    return False


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # let CORS preflight through untouched
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if _is_public(path):
            return await call_next(request)

        # Service-to-service trusted path: gateway already stamped X-Org-Id.
        org_id = request.headers.get("x-org-id")
        if org_id:
            request.state.org_id = org_id
            request.state.user_id = request.headers.get("x-user-id")
            request.state.user_email = request.headers.get("x-user-email")
            request.state.role = request.headers.get("x-role")
            request.state.employee_id = request.headers.get("x-employee-id")
            request.state.branch_id = request.headers.get("x-branch-id")
            request.state.branch_scope = request.headers.get("x-branch-scope")
            mods = request.headers.get("x-modules")
            request.state.modules = mods.split(",") if mods else None
            return await call_next(request)

        # Edge path: validate the bearer token ourselves.
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"detail": "Missing bearer token"}, status_code=401)
        try:
            claims = decode_token(auth.split(" ", 1)[1])
        except jwt.PyJWTError:
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)

        request.state.org_id = claims.get("org_id")
        request.state.user_id = claims.get("sub")
        request.state.user_email = claims.get("email")
        request.state.role = claims.get("role")
        request.state.employee_id = claims.get("employee_id")
        request.state.branch_scope = claims.get("branch_scope") or "branch"
        request.state.branch_id = claims.get("branch_id")
        # An org-scope user may request a specific branch view via X-View-Branch;
        # a branch-scope user is locked to their own branch (can't widen).
        if request.state.branch_scope == "org":
            request.state.branch_id = request.headers.get("x-view-branch") or None
        request.state.permissions = claims.get("permissions", [])
        # Subscribed modules (Phase 4 paywall). None = token predates the claim →
        # the gateway fails OPEN for backward-compat; a present list is enforced.
        request.state.modules = claims.get("modules")
        return await call_next(request)
