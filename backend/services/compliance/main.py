"""Lexora HRM — Compliance Service.

OHS safety incident register, tamper-evident audit trail (shared hash chain),
WCB/WSIB per-org rate config + premium assessment, and the privacy centre (PIPEDA).
"""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

sys.path.append("/app")
from database import init_db  # noqa: E402
import models  # noqa: E402,F401  (registers compliance tables on Base)
import privacy_models  # noqa: E402,F401  (registers privacy-centre tables on Base)
from routers import router  # noqa: E402
from privacy import router as privacy_router, start_scheduler  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = start_scheduler()          # daily retention DRY-RUN per org (never purges by itself)
    yield
    if task:
        task.cancel()


app = FastAPI(title="Lexora HRM — Compliance Service", version="0.2.0", lifespan=lifespan)
app.include_router(router)
app.include_router(privacy_router)    # privacy centre (PIPEDA; Québec Law 25 notes)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "compliance"}


@app.get("/compliance/ping")
async def ping(request: Request):
    from shared.utils.roles import HR_WRITE, require_roles
    require_roles(request, HR_WRITE, "check the compliance service")   # contract: /compliance = HR (WASA 2026-10-21)
    return {"service": "compliance", "prefix": "/compliance", "phase": 4, "status": "live"}

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "compliance")
