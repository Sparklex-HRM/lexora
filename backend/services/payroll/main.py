"""Lexora HRM — Payroll Service (Canadian net-pay engine: CRA T4127 for every province and
territory + the Québec engine, T4 / RL-1 / ROE / remittances / EHT / GL journal)."""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

sys.path.append("/app")
from database import init_db  # noqa: E402
from routers import router  # noqa: E402
from exit_pay import router as exit_pay_router  # noqa: E402  (Stream C: pay-day rules + exit pay status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await revisions_init()   # Stream B: daily tick that applies approved salary revisions when due
    yield


app = FastAPI(title="Lexora HRM — Payroll Service", version="0.1.0", lifespan=lifespan)
from search_routes import router as search_router  # noqa: E402  (2026-10-20 global search, before /payroll/{...})
app.include_router(search_router)
app.include_router(router)
app.include_router(exit_pay_router)

# ── Stream B (lifecycle): salary revisions (raises) — /payroll/revisions/* ──
from revisions import router as revisions_router  # noqa: E402
from revisions_hook import revisions_init  # noqa: E402
app.include_router(revisions_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "payroll"}


# ── Stream G6: Canada completeness (/payroll/ca/*) + migration kit (/payroll/migration/*) ──
import ca_models  # noqa: E402,F401  (registers the ca_* / migration_* tables on Base before init_db runs)
from ca_routers import router as ca_router  # noqa: E402
from migration_routers import router as migration_kit_router  # noqa: E402
app.include_router(ca_router)
app.include_router(migration_kit_router)


# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "payroll")
