"""Lexora HRM — Time & Attendance Service.

Capture (punches) → attendance (plan vs actual) → timesheets (approve/lock) →
the payroll feed (approved hours become pay-run REG/OT/STAT entries).
"""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

sys.path.append("/app")
from database import init_db  # noqa: E402
import models  # noqa: E402,F401  (registers T&A tables on Base)
from routers import router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await mobile_init()   # Stream G4: ta_mobile_settings / ta_geo_fences / ta_devices / ta_punch_mobile
    # Integration 2026-10-23: device webhook secrets are stored hashed; hash any plain one left from before.
    from database import SessionLocal
    from biometric_import import hash_legacy_secrets
    async with SessionLocal() as s:
        await hash_legacy_secrets(s)
    import asyncio
    from device_push import watch_offline  # G3 2026-10-23: device offline alerts
    watcher = asyncio.create_task(watch_offline())
    rotation_routers.start_tick()   # G2 2026-10-23: nightly roster top-up for orgs with auto-generate on
    yield
    watcher.cancel()


app = FastAPI(title="Lexora HRM — Time & Attendance Service", version="0.2.0", lifespan=lifespan)
# 2026-10-16 employee portal: /time/me/* (my attendance month, corrections, permission) — static paths
from ess_time import router as ess_time_router, perm_router as ess_perm_router  # noqa: E402
app.include_router(ess_time_router)
app.include_router(ess_perm_router)
from search_routes import router as search_router  # noqa: E402  (2026-10-20 global search)
app.include_router(search_router)
# ── Gap G2 (2026-10-23): shift rotation patterns, auto-generated roster, shift swaps — /time/rotations/* ──
import rotation_routers  # noqa: E402  (registers ta_rotation_* / ta_shift_swaps on Base before init_db)
app.include_router(rotation_routers.router)
app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "time_attendance"}


@app.get("/time/ping")
async def ping():
    return {"service": "time_attendance", "prefix": "/time", "phase": 2, "status": "live"}


# ── Stream F: biometric punch import (eSSL/ZKTeco/Matrix CSV + push-device webhook)
from biometric_import import router as biometric_router  # noqa: E402
app.include_router(biometric_router)
# ── Gap G3 (2026-10-23): native device push (ZKTeco/eSSL ADMS /iclock, Matrix COSEC, HTTP) + device health ──
import device_push  # noqa: E402  (registers ta_device_push on Base before init_db)
app.include_router(device_push.iclock)
app.include_router(device_push.push)
app.include_router(device_push.admin)


# ── Stream G4: mobile punch (geo-fence, selfie, device binding, offline sync) — /time/mobile/* ──
from mobile_punch import router as mobile_router, init_mobile_schema as _mobile_schema  # noqa: E402
app.include_router(mobile_router)


async def mobile_init():
    from database import engine
    await _mobile_schema(engine)


# ── Stream G2: PSA view of timesheet lines (billable hours, rate snapshot, invoiced flag) — /time/psa/*
from psa_lines import router as psa_lines_router  # noqa: E402
app.include_router(psa_lines_router)

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "time_attendance")
