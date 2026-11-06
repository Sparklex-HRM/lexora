"""Lexora HRM — Notification Service.

In-app notifications (bell + page), the e-mail outbox with an honest delivery log,
per-user preferences, e-mail templates, reminders registered through
``shared.utils.notify.notify_later`` — and the company settings API (`/settings`).

Other services do not call this service over HTTP: they call
``shared.utils.notify.notify(...)``, which writes the rows in the shared database and
sends the e-mail in the background. This service serves the screens and runs the tick
that retries anything still queued and fires due reminders.
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

sys.path.append("/app")
from shared.utils import notify as nt  # noqa: E402

from database import init_db  # noqa: E402
from routers import router  # noqa: E402
from settings_routers import router as settings_router  # noqa: E402
from chatops_routes import router as chatops_router  # noqa: E402  (G3: Slack / Teams commands)
from whatsapp_routes import router as whatsapp_router  # noqa: E402  (G3: WhatsApp receipts + log)
from shared.utils import events as g3_events  # noqa: E402
from shared.utils.providers.models import ensure_tables as g3_ensure_tables  # noqa: E402

log = logging.getLogger("lexora.notification")
TICK_SECONDS = int(os.getenv("NOTIFY_TICK_SECONDS", "60") or 60)


async def _tick():
    """Once a minute: fire due reminders, then send whatever is still queued."""
    while True:
        try:
            await nt.fire_due_reminders()
            await nt.dispatch_email()
            await g3_events.retry_due()          # G3: webhook retries with backoff
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.warning("notification tick failed: %s", e)
        await asyncio.sleep(TICK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await g3_ensure_tables()
    task = asyncio.create_task(_tick())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await nt.drain()


app = FastAPI(title="Lexora HRM — Notification Service", version="1.0.0", lifespan=lifespan)
app.include_router(router)
app.include_router(settings_router)
app.include_router(chatops_router)
app.include_router(whatsapp_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "notification"}


@app.get("/notifications/ping")
async def ping():
    return {"service": "notification", "prefix": "/notifications", "status": "live",
            "also_serves": ["/settings"], "smtp": nt.smtp_status()}

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "notification")
