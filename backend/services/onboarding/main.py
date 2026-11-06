"""Lexora HRM — Onboarding Service."""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

sys.path.append("/app")
from database import init_db  # noqa: E402
import models  # noqa: E402,F401  (registers onboarding tables on Base)
from routers import router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Lexora HRM — Onboarding Service", version="0.2.0", lifespan=lifespan)
# 2026-10-20 global search: GET /onboarding/search (before /onboarding/{...} routes)
from search_routes import router as search_router  # noqa: E402
app.include_router(search_router)
app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "onboarding"}

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "onboarding")
