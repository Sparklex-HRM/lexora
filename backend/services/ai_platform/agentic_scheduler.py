"""Agentic housekeeping loop — every 15 minutes, for every active org with the add-on ON:
chase the work items that are due (in-app + e-mail, escalation after N chases) and expire stale
proposals. Phase-2 agents add their own triggers (pay-date minus N, attendance lock, filing due)
through ``tick_hooks``.

Started from the service lifespan like ``wd_runner.scheduler``; ``migrations.worker`` stubs
``asyncio.create_task`` so a migration never chases anyone.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from sqlalchemy import text

from shared.utils import platform_models as pm

from agentic_proposals import expire_stale
from agentic_state import agentic_state
from agentic_tasks import chase_due_tasks

log = logging.getLogger("lexora.agentic")
TICK_SECONDS = int(os.getenv("AGENTIC_TICK_SECONDS", "900") or 900)

# phase-2 agents register `async def hook(org: str, state: dict) -> None` here (run after the chase on every tick)
tick_hooks: list[Callable[[str, dict], Awaitable[None]]] = []


async def _active_orgs() -> list[str]:
    try:
        async with pm.session() as s:
            r = (await s.execute(text("SELECT id FROM organizations WHERE is_active IS TRUE LIMIT 500"))).all()
        return [str(x[0]) for x in r]
    except Exception:                                        # noqa: BLE001
        return []


async def tick_org(org: str) -> dict | None:
    st = await agentic_state(org)
    if not st["enabled"]:
        return None
    out = {"org": org, "mode": st["mode"]}
    try:
        out["chase"] = await chase_due_tasks(org)
    except Exception as e:                                   # noqa: BLE001
        log.warning("agentic chase failed for %s: %s", org, e)
    try:
        out["expired"] = await expire_stale(org)
    except Exception as e:                                   # noqa: BLE001
        log.warning("agentic expiry failed for %s: %s", org, e)
    for hook in list(tick_hooks):
        try:
            await hook(org, st)
        except Exception as e:                               # noqa: BLE001
            log.warning("agentic hook %s failed for %s: %s", getattr(hook, "__name__", hook), org, e)
    return out


async def tick() -> list[dict]:
    out = []
    for org in await _active_orgs():
        r = await tick_org(org)
        if r:
            out.append(r)
    return out


async def scheduler():
    await asyncio.sleep(30)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.warning("agentic tick failed: %s", e)
        await asyncio.sleep(TICK_SECONDS)
