"""Lexora HRM — Auth Service (identity + tenancy root)."""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

sys.path.append("/app")
from database import init_db  # noqa: E402
from routers import router  # noqa: E402
from billing import router as billing_router  # noqa: E402
from security_routes import router as security_router  # noqa: E402
from users_routes import router as users_router  # noqa: E402
# Stream G3 — platform & integrations: SSO, SCIM, API keys, webhooks, e-sign, providers (KYC / BGV)
from sso_routes import router as sso_router  # noqa: E402
from scim_routes import router as scim_router  # noqa: E402
from apikeys_routes import router as apikeys_router  # noqa: E402
from webhooks_routes import router as webhooks_router  # noqa: E402
from esign_routes import router as esign_router  # noqa: E402
from integrations_routes import router as integrations_router  # noqa: E402
from shared.utils.providers.models import ensure_tables as g3_ensure_tables  # noqa: E402
# Stream S4 — self-serve sign-up, subscription billing (invoice; no card processor), MFA + sessions
from saas_db import init_saas  # noqa: E402
from signup_routes import router as signup_router  # noqa: E402
from billing_saas_routes import router as billing_saas_router  # noqa: E402
from account_routes import router as account_router  # noqa: E402
# Stream D4 — legal entities (group companies under one org); importing registers the table on Base
from entities_routes import router as entities_router, init_entities  # noqa: E402
# Go-live 2026-10-24 — org Roles & Permissions (custom roles; importing registers rbac_* on the shared Base)
from rbac_routes import router as rbac_router  # noqa: E402
import ui_grant as _ui_grant  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await g3_ensure_tables()
    await init_saas()
    await init_entities()        # D4: columns + default entity per org (idempotent)
    _ui_grant.signer_ready()     # 2026-10-24: log at boot (CRITICAL) when no UI-grant signing key is configured
    yield


app = FastAPI(title="Lexora HRM — Auth Service", version="0.2.0", lifespan=lifespan)
# WASA 2026-10-21: refusals with a second field — {"detail", "code"} (CAPTCHA) / {"detail", "ref"}.
from http_errors import install as _install_public_errors  # noqa: E402
_install_public_errors(app)
app.include_router(router)
# S4 routes BEFORE billing_router so /auth/billing/invoices/{id}/pdf etc. are not shadowed.
for _r in (signup_router, billing_saas_router, account_router):
    app.include_router(_r)
app.include_router(billing_router)      # PEPM billing (CAD) + GST/HST invoices
app.include_router(security_router)     # password policy, sessions, org export, login-info
app.include_router(users_router)        # logins for employees: create / invite / reset / disable / role
app.include_router(entities_router)     # D4: legal entities (group companies) CRUD
app.include_router(rbac_router)         # 2026-10-24: Roles & Permissions (custom roles, assignments, preview)
for _r in (sso_router, scim_router, apikeys_router, webhooks_router, esign_router, integrations_router):
    app.include_router(_r)              # G3 platform & integrations


@app.get("/health")
async def health():
    return {"status": "ok", "service": "auth"}

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "auth")
