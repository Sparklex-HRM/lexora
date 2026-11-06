"""Lexora HRM — Approvals Service: the REGISTRY + INBOX (not a workflow engine).

Owning services keep their own state machines and register "this is waiting for
someone" through ``shared.utils.approvals_client.open_item`` / ``close_item``. This
service serves the one inbox (`/approvals`), mirrors the three older queues (leave,
timesheets, attendance fixes), reminds approvers about items that waited too long —
and serves the reports and the executive dashboard (`/reports`).
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from sqlalchemy import text

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

import chains_routers  # noqa: E402  (D1 — no-code approval chains)
import chains_runtime as crt  # noqa: E402
import inbox as ib  # noqa: E402
import report_builder_routers as rbr  # noqa: E402  (G5 — custom report builder)
from database import init_db  # noqa: E402
from report_builder_models import RB_TABLES  # noqa: E402
from reports_routers import router as reports_router  # noqa: E402
from routers import router  # noqa: E402

log = logging.getLogger("lexora.approvals")
TICK_SECONDS = int(os.getenv("APPROVALS_TICK_SECONDS", "600") or 600)


async def _active_orgs() -> list[str]:
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT id FROM organizations WHERE is_active IS TRUE LIMIT 500"))).all()
        return [str(r[0]) for r in rows]
    except Exception:                                        # noqa: BLE001
        return []


async def _tick():
    """Every ten minutes: mirror the built-in queues, then send reminders that are due."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            for org in await _active_orgs():
                await ib.sync_builtin(org)
                await ib.send_due_reminders(org)
                await crt.escalate_due(org)                  # D1 — chain step escalation
                await crt.run_auto_due(org)                  # D1 — auto-approve backstop
            await rbr.run_due_schedules()                    # G5 — scheduled custom reports
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.warning("approvals tick failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        await pm.create_tables(RB_TABLES, retries=5)        # G5 — saved reports + schedules
    except Exception as e:                                   # noqa: BLE001
        log.warning("report builder tables not created: %s", e)
    try:                                                     # go-live 2026-10-24: create apr_wa_requests at boot,
        await wa_approvals.ensure_tables()                  # so a fresh `migrate check` sees it (was lazy)
    except Exception as e:                                   # noqa: BLE001
        log.warning("whatsapp approval table not created: %s", e)
    task = asyncio.create_task(_tick())
    wa_task = asyncio.create_task(wa_approvals.loop())      # G7 2026-10-23: WhatsApp approval requests
    yield
    task.cancel()
    wa_task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Lexora HRM — Approvals Service", version="1.0.0", lifespan=lifespan)
from search_routes import router as search_router  # noqa: E402  (2026-10-20 global search: /approvals/search)
app.include_router(search_router)
import wa_approvals  # noqa: E402  (G7 2026-10-23: /approvals/whatsapp/* — before /approvals/{...})
app.include_router(wa_approvals.router)
app.include_router(chains_routers.router)                    # D1: /approvals/workflows/* before /approvals/{...}
app.include_router(chains_routers.public)                    # D1: /approvals/chains/resolve (any signed-in user)
app.include_router(router)
app.include_router(rbr.router)                               # G5: /reports/builder/* must register before /reports/{key}
app.include_router(reports_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "approvals"}


@app.get("/approvals/ping")
async def ping():
    return {"service": "approvals", "prefix": "/approvals", "status": "live", "also_serves": ["/reports"]}

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "approvals")
