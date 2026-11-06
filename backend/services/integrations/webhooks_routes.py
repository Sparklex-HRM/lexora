"""Inbound provider webhook log.

    GET  /integrations/webhooks/log      admin: what arrived (masked)

Lexora HRM has no inbound provider webhooks today (the old payout / e-sign providers were
removed); the log stays so a future Canadian provider (payments, e-signature) writes to the same
`int_inbound_webhooks` register.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from shared.utils import platform_models as pm
from shared.utils.providers.models_s5 import InboundWebhook

from common import org_of, require_roles

router = APIRouter(prefix="/integrations/webhooks", tags=["provider-webhooks"])


@router.get("/log")
async def webhook_log(request: Request, limit: int = 300):
    org = org_of(request)
    require_roles(request, ("org_admin",), "see provider webhooks")
    async with pm.session() as s:
        rows = (await s.execute(select(InboundWebhook).where(InboundWebhook.org_id == org)
                                .order_by(InboundWebhook.received_at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return {"items": [{"id": r.id, "provider": r.provider, "event": r.event, "event_id": r.event_id, "verified": r.verified,
                       "outcome": r.outcome, "received_at": pm.iso(r.received_at)} for r in rows]}
