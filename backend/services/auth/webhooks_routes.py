"""Auth service — outbound webhooks (stream G3). org_admin only.

    GET  /auth/webhooks/events                       the event catalogue
    GET  /auth/webhooks        POST /auth/webhooks {url, events, description}   → secret shown ONCE
    PUT  /auth/webhooks/{id}   DELETE /auth/webhooks/{id}
    POST /auth/webhooks/{id}/test                     sends webhook.test now
    POST /auth/webhooks/{id}/rotate-secret            new signing secret, shown ONCE
    GET  /auth/webhooks/deliveries?webhook_id=&status=
    POST /auth/webhooks/deliveries/{id}/replay
"""
import secrets
import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import crypto, events  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.providers import http  # noqa: E402
from shared.utils.providers.models import Webhook, WebhookDelivery  # noqa: E402

router = APIRouter(prefix="/auth/webhooks", tags=["webhooks"])


def _admin(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an admin can manage webhooks")
    return org


def _out(h: Webhook) -> dict:
    return {"id": h.id, "url": h.url, "description": h.description, "events": list(h.events or []),
            "enabled": bool(h.enabled), "created_at": pm.iso(h.created_at), "created_by": h.created_by,
            "last_status": h.last_status, "last_delivery_at": pm.iso(h.last_delivery_at)}


def _d_out(d: WebhookDelivery) -> dict:
    return {"id": d.id, "webhook_id": d.webhook_id, "event": d.event, "event_id": d.event_id, "attempt": d.attempt,
            "status": d.status, "http_status": d.http_status, "response_excerpt": d.response_excerpt,
            "next_attempt_at": pm.iso(d.next_attempt_at), "created_at": pm.iso(d.created_at),
            "delivered_at": pm.iso(d.delivered_at), "replay_of": d.replay_of, "payload": d.payload}


@router.get("/events")
async def event_catalogue(request: Request):
    _admin(request)
    return {"events": [{"key": k, "label": v} for k, v in events.EVENTS.items()],
            "signature": "X-Lexora-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, '<t>.<raw body>')>",
            "retries": [f"{s // 60} min" if s < 3600 else f"{s // 3600} h" for s in events.BACKOFF_SECONDS]}


@router.get("")
async def list_hooks(request: Request):
    org = _admin(request)
    async with pm.session() as s:
        rows = (await s.execute(select(Webhook).where(Webhook.org_id == org).order_by(Webhook.created_at.desc()))).scalars().all()
    return {"items": [_out(h) for h in rows]}


class HookIn(BaseModel):
    url: str
    description: str = ""
    events: list[str] = []
    enabled: bool = True


def _clean(body: HookIn) -> dict:
    bad = http.check_https(body.url)
    if bad:
        raise HTTPException(422, bad)
    evs = sorted({e for e in body.events if e in events.EVENTS or e == "*" or e.endswith(".*")})
    if not evs:
        raise HTTPException(422, "Pick at least one event")
    return {"url": body.url.strip(), "description": (body.description or "").strip()[:200], "events": evs,
            "enabled": bool(body.enabled)}


@router.post("", status_code=201)
async def create_hook(body: HookIn, request: Request):
    org = _admin(request)
    data = _clean(body)
    secret = "whsec_" + secrets.token_urlsafe(32)
    async with pm.session() as s:
        h = Webhook(org_id=org, secret=crypto.encrypt(secret, aad=events.SECRET_AAD),
                    created_by=request.headers.get("x-user-email"), **data)
        s.add(h)
        await s.commit()
        out = _out(h)
    await record_audit(request, "integration.webhook.create", entity_type="webhook", entity_id=out["id"],
                       summary=f"Webhook added: {data['url']}", meta={"events": data["events"]})
    return {**out, "secret": secret, "note": "Copy the signing secret now. It is not shown again."}


@router.put("/{hid}")
async def update_hook(hid: str, body: HookIn, request: Request):
    org = _admin(request)
    data = _clean(body)
    async with pm.session() as s:
        h = (await s.execute(select(Webhook).where(Webhook.id == hid, Webhook.org_id == org))).scalar_one_or_none()
        if not h:
            raise HTTPException(404, "Webhook not found")
        for k, v in data.items():
            setattr(h, k, v)
        await s.commit()
        out = _out(h)
    await record_audit(request, "integration.webhook.update", entity_type="webhook", entity_id=hid,
                       summary=f"Webhook changed: {data['url']}", meta=data)
    return out


@router.delete("/{hid}")
async def delete_hook(hid: str, request: Request):
    org = _admin(request)
    async with pm.session() as s:
        h = (await s.execute(select(Webhook).where(Webhook.id == hid, Webhook.org_id == org))).scalar_one_or_none()
        if not h:
            raise HTTPException(404, "Webhook not found")
        url = h.url
        await s.delete(h)
        await s.commit()
    await record_audit(request, "integration.webhook.delete", entity_type="webhook", entity_id=hid,
                       summary=f"Webhook removed: {url}")
    return {"ok": True}


@router.post("/{hid}/rotate-secret")
async def rotate_secret(hid: str, request: Request):
    org = _admin(request)
    secret = "whsec_" + secrets.token_urlsafe(32)
    async with pm.session() as s:
        h = (await s.execute(select(Webhook).where(Webhook.id == hid, Webhook.org_id == org))).scalar_one_or_none()
        if not h:
            raise HTTPException(404, "Webhook not found")
        h.secret = crypto.encrypt(secret, aad=events.SECRET_AAD)
        await s.commit()
    await record_audit(request, "integration.webhook.rotate", entity_type="webhook", entity_id=hid,
                       summary="Webhook signing secret rotated")
    return {"secret": secret, "note": "Copy the new secret now. It is not shown again."}


@router.post("/{hid}/test")
async def test_hook(hid: str, request: Request):
    org = _admin(request)
    async with pm.session() as s:
        h = (await s.execute(select(Webhook).where(Webhook.id == hid, Webhook.org_id == org))).scalar_one_or_none()
        if not h:
            raise HTTPException(404, "Webhook not found")
        d = WebhookDelivery(org_id=org, webhook_id=h.id, event="webhook.test", event_id=pm.new_id(),
                            payload={"message": "Test from Lexora HRM", "by": request.headers.get("x-user-email")},
                            status="pending", next_attempt_at=pm.now_utc())
        s.add(d)
        await s.commit()
        did = d.id
    st = await events.deliver(did)
    async with pm.session() as s:
        d = (await s.execute(select(WebhookDelivery).where(WebhookDelivery.id == did))).scalar_one()
        out = _d_out(d)
    await record_audit(request, "integration.webhook.test", entity_type="webhook", entity_id=hid,
                       summary=f"Webhook test → {st}" + (f" (HTTP {out['http_status']})" if out["http_status"] else ""))
    return out


@router.get("/deliveries")
async def deliveries(request: Request, webhook_id: str | None = None, status: str | None = None, limit: int = 300):
    org = _admin(request)
    q = select(WebhookDelivery).where(WebhookDelivery.org_id == org)
    if webhook_id:
        q = q.where(WebhookDelivery.webhook_id == webhook_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(WebhookDelivery.created_at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
        hooks = {h.id: h.url for h in (await s.execute(select(Webhook).where(Webhook.org_id == org))).scalars().all()}
    return {"items": [{**_d_out(d), "url": hooks.get(d.webhook_id)} for d in rows]}


@router.post("/deliveries/{did}/replay")
async def replay_delivery(did: str, request: Request):
    org = _admin(request)
    res = await events.replay(org, did)
    if not res:
        raise HTTPException(404, "Delivery not found")
    await record_audit(request, "integration.webhook.replay", entity_type="webhook_delivery", entity_id=did,
                       summary=f"Delivery replayed → {res['status']}")
    return res
