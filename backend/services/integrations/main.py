"""Lexora HRM — Integrations Service (stream S5): live provider adapters.

WhatsApp consent + event templates (Meta Cloud API) · the inbound-webhook log · the masked
provider-call log.

Provider CONFIG (credentials, sandbox / live, test button) stays where G3 put it:
/auth/integrations/{key} — this service reads the same encrypted ``int_providers`` rows.
"""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.providers import models as g3_models  # noqa: E402
from shared.utils.providers import models_s5  # noqa: E402
from shared.utils.providers.models_s5 import ProviderCall  # noqa: E402

import webhooks_routes  # noqa: E402
import whatsapp_routes  # noqa: E402
from common import org_of, require_roles  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await g3_models.ensure_tables()          # idempotent — the G3 tables this service writes to
    await models_s5.ensure_tables()
    yield


app = FastAPI(title="Lexora HRM — Integrations Service", version="1.0.0", lifespan=lifespan)
for r in (webhooks_routes, whatsapp_routes):
    app.include_router(r.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "integrations"}


@app.get("/integrations/calls")
async def provider_calls(request: Request, provider: str | None = None, limit: int = 300):
    """Every outbound provider call for this org: path, status, attempts, masked bodies. No keys, no headers."""
    org = org_of(request)
    require_roles(request, ("org_admin",), "see provider calls")
    q = select(ProviderCall).where(ProviderCall.org_id == org)
    if provider:
        q = q.where(ProviderCall.provider == provider)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(ProviderCall.at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return {"items": [{"id": c.id, "provider": c.provider, "operation": c.operation, "method": c.method, "host": c.host,
                       "path": c.path, "http_status": c.http_status, "ok": c.ok, "outcome": c.outcome, "attempts": c.attempts,
                       "duration_ms": c.duration_ms, "idempotency_key": c.idempotency_key, "request": c.request_masked,
                       "response": c.response_masked, "error": c.error, "at": pm.iso(c.at)} for c in rows]}


# S3 production runtime (health/ready, JSON logs, PII log filter, prod secret gate) — when present.
try:
    from shared.ops_runtime import install as _ops_install  # noqa: E402
    _ops_install(app, "integrations")
except ImportError:  # pragma: no cover
    pass
