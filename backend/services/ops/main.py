"""Lexora HRM — Operations / Staffing Service (branches + PSA)."""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

sys.path.append("/app")
from database import init_db  # noqa: E402
from routers import router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await engage_init()   # Stream G4: eng_* tables + the exit-survey hook
    await d7_init()       # Stream D7: LMS depth / engagement depth / positions tables + the schedules-reminders tick
    yield


app = FastAPI(title="Lexora HRM — Operations Service", version="0.1.0", lifespan=lifespan)
app.include_router(router)

# ── Stream G2: Project Services (rate cards, assignments, utilisation, invoice drafts) — /ops/psa/*
# Imported before the lifespan runs so its tables are registered for create_all.
from psa_routers import router as psa_router  # noqa: E402
app.include_router(psa_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ops"}


# ── Stream G4: engagement — HR helpdesk · surveys · recognition · LMS · user prefs — /engage/* ──
import engage_models  # noqa: E402,F401  (new eng_* tables on Base)
from engage_init import engage_init  # noqa: E402
from engage_routers import router as engage_router  # noqa: E402
from engage_lms import router as engage_lms_router  # noqa: E402
from search_routes import router as search_router  # noqa: E402  (2026-10-20 global search: GET /engage/search)
app.include_router(search_router)
app.include_router(engage_router)
app.include_router(engage_lms_router)

# ── Stream D7: LMS depth · engagement depth · headcount planning · people analytics — /engage/* ──
from d7_init import d7_init  # noqa: E402
from analytics_routers import router as analytics_router  # noqa: E402
from engage2_routers import router as engage2_router  # noqa: E402
from lms_routers import router as lms_depth_router  # noqa: E402
from positions_routers import router as positions_router  # noqa: E402
app.include_router(lms_depth_router)
app.include_router(engage2_router)
app.include_router(positions_router)
app.include_router(analytics_router)

# ── Employee portal batch 3 (item 12): company feed — /engage/feed/* (eng_feed_* tables via create_all at boot) ──
import feed_models  # noqa: E402,F401
from feed_routers import router as feed_router  # noqa: E402
app.include_router(feed_router)

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "ops")
