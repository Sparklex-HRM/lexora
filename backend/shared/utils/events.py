"""Lexora HRM — outbound events → webhooks.

    from shared.utils.events import emit
    await emit(org_id, "employee.created", {"employee_id": "...", "employee_number": "EMP-0042"})

Best-effort and never raises: one delivery row per subscribed webhook, the POST
runs in the background with a 10 s timeout, an HMAC-SHA256 signature header,
retries with backoff (1 m, 5 m, 30 m, 2 h, 6 h — then ``dead``) driven by the
notification service tick (``retry_due``), and a replay from the delivery log.

Headers on every delivery:
    X-Lexora-Event:      employee.created
    X-Lexora-Delivery:   <delivery id>
    X-Lexora-Signature:  t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>
The receiver recomputes the HMAC over the raw body with its secret and compares.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta

from sqlalchemy import select

from shared.utils import crypto
from shared.utils import platform_models as pm
from shared.utils.providers import http
from shared.utils.providers.models import Webhook, WebhookDelivery

log = logging.getLogger("lexora.events")

EVENTS: dict[str, str] = {
    "employee.created": "Employee added",
    "employee.updated": "Employee record changed",
    "employee.exited": "Employee left (exit applied)",
    "leave.approved": "Leave approved",
    "leave.rejected": "Leave rejected",
    "payroll.run.completed": "Pay run completed",
    "timesheet.locked": "Timesheet locked",
    "candidate.offer.accepted": "Offer accepted by candidate",
    "candidate.offer.declined": "Offer declined by candidate",
    "esign.signed": "Document e-signed",
    "esign.declined": "E-sign declined",
    "webhook.test": "Test event",
}
BACKOFF_SECONDS = [60, 300, 1800, 7200, 21600]
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
SECRET_AAD = "int:webhook:secret"


def canonical_body(event_id: str, event: str, org_id: str, created_at: str, payload: dict) -> str:
    return json.dumps({"id": event_id, "event": event, "org_id": org_id, "created_at": created_at,
                       "data": payload}, sort_keys=True, separators=(",", ":"), default=str)


def sign(secret: str, body: str, ts: int | None = None) -> str:
    ts = int(ts if ts is not None else time.time())
    mac = hmac.new((secret or "").encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: str, header: str, now: float | None = None, tolerance: int = 300) -> bool:
    """For receivers (and tests): check a signature header against the raw body."""
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, ts), header.strip())


def subscribed(events: list | None, event: str) -> bool:
    ev = set(events or [])
    return "*" in ev or event in ev or (event.split(".")[0] + ".*") in ev


def backoff(attempt: int) -> timedelta | None:
    """Delay before the NEXT try after ``attempt`` failed tries; None = give up."""
    if attempt >= MAX_ATTEMPTS:
        return None
    return timedelta(seconds=BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])


_tasks: set = set()


def _spawn(coro) -> None:
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def drain() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def emit(org_id, event: str, payload: dict | None = None, *, deliver_now: bool = True) -> list[str]:
    """Queue the event for every subscribed webhook of the org. Returns delivery ids. Never raises."""
    try:
        org = str(org_id)
        async with pm.session() as s:
            hooks = (await s.execute(select(Webhook).where(
                Webhook.org_id == org, Webhook.enabled.is_(True)))).scalars().all()
            hooks = [h for h in hooks if subscribed(h.events, event)]
            if not hooks:
                return []
            eid = pm.new_id()
            ids = []
            for h in hooks:
                d = WebhookDelivery(id=pm.new_id(), org_id=org, webhook_id=h.id, event=event, event_id=eid,
                                    payload=payload or {}, status="pending", next_attempt_at=pm.now_utc())
                s.add(d)
                ids.append(d.id)
            await s.commit()
        if deliver_now:
            for did in ids:
                _spawn(deliver(did))
        return ids
    except Exception as e:                                   # noqa: BLE001
        log.warning("emit(%s, %s) failed: %s", org_id, event, e)
        return []


async def deliver(delivery_id: str) -> str:
    """One attempt. Returns the new status. The POST never logs the body or the secret."""
    try:
        async with pm.session() as s:
            d = (await s.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id))).scalar_one_or_none()
            if not d or d.status in ("delivered", "dead"):
                return d.status if d else "gone"
            h = (await s.execute(select(Webhook).where(Webhook.id == d.webhook_id))).scalar_one_or_none()
            if not h:
                d.status, d.response_excerpt = "dead", "The webhook was deleted."
                await s.commit()
                return d.status
            try:
                secret = crypto.decrypt(h.secret, aad=SECRET_AAD) or ""
            except crypto.CryptoError:
                secret = ""
            body = canonical_body(d.event_id, d.event, d.org_id, pm.iso(d.created_at) or "", d.payload or {})
            headers = {"Content-Type": "application/json", "X-Lexora-Event": d.event,
                       "X-Lexora-Delivery": d.id, "X-Lexora-Signature": sign(secret, body),
                       "User-Agent": "Lexora-Webhooks/1.0"}
            r = await http.request("POST", h.url, data=body.encode(), headers=headers, timeout=10.0)
            d.attempt = (d.attempt or 0) + 1
            d.http_status = r.status
            d.response_excerpt = r.excerpt(300)
            if r.ok:
                d.status, d.delivered_at, d.next_attempt_at = "delivered", pm.now_utc(), None
            else:
                wait = backoff(d.attempt)
                if wait is None:
                    d.status, d.next_attempt_at = "dead", None
                else:
                    d.status, d.next_attempt_at = "failed", pm.now_utc() + wait
            h.last_status, h.last_delivery_at = d.status, pm.now_utc()
            await s.commit()
            return d.status
    except Exception as e:                                   # noqa: BLE001
        log.warning("webhook delivery %s crashed: %s", delivery_id, e)
        return "error"


async def retry_due(limit: int = 50) -> int:
    """The service tick: send every failed delivery whose retry time has come."""
    n = 0
    try:
        async with pm.session() as s:
            ids = list((await s.execute(select(WebhookDelivery.id).where(
                WebhookDelivery.status.in_(("failed", "pending")),
                WebhookDelivery.next_attempt_at <= pm.now_utc())
                .order_by(WebhookDelivery.next_attempt_at).limit(limit))).scalars().all())
        for did in ids:
            await deliver(did)
            n += 1
    except Exception as e:                                   # noqa: BLE001
        log.warning("retry_due failed: %s", e)
    return n


async def replay(org_id, delivery_id: str) -> dict | None:
    """Queue a fresh delivery of the same event (keeps the old row). Returns the new row's id/status."""
    async with pm.session() as s:
        d = (await s.execute(select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id, WebhookDelivery.org_id == str(org_id)))).scalar_one_or_none()
        if not d:
            return None
        n = WebhookDelivery(id=pm.new_id(), org_id=d.org_id, webhook_id=d.webhook_id, event=d.event,
                            event_id=d.event_id, payload=d.payload, status="pending",
                            next_attempt_at=pm.now_utc(), replay_of=d.id)
        s.add(n)
        await s.commit()
        nid = n.id
    st = await deliver(nid)
    return {"id": nid, "status": st}
