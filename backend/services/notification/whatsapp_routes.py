"""Notification service — WhatsApp connector routes (stream G3).

Public (the gateway lists /notifications/whatsapp/webhook; signature is the credential):
    GET  /notifications/whatsapp/webhook      Meta verification handshake (hub.verify_token)
    POST /notifications/whatsapp/webhook      delivery receipts — Meta (X-Hub-Signature-256) or Twilio (X-Twilio-Signature)
Admin:
    GET  /notifications/whatsapp/messages     the honest message log
    POST /notifications/whatsapp/test {to}    sends the notification template once (real call)
"""
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.providers import mirror, store, whatsapp as WA  # noqa: E402
from shared.utils.providers.models import IntegrationProvider, WhatsappMessage  # noqa: E402

router = APIRouter(prefix="/notifications/whatsapp", tags=["whatsapp"])


def _admin(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Not authenticated")
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an admin can open this")
    return org


async def _all_configs() -> list[dict]:
    """Every org's decrypted WhatsApp config (small: one row per org)."""
    async with pm.session() as s:
        rows = (await s.execute(select(IntegrationProvider).where(IntegrationProvider.provider == "whatsapp"))).scalars().all()
    out = []
    for r in rows:
        p = store.base.get("whatsapp")
        out.append({"org_id": r.org_id, "mode": r.mode, "enabled": r.enabled,
                    "config": store.decrypt_config(p, r.mode, r.config or {})})
    return out


@router.get("/webhook")
async def meta_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token"):
        for c in await _all_configs():
            if c["mode"] == "meta" and c["config"].get("verify_token") == q["hub.verify_token"]:
                return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


async def _apply(updates: list[tuple[str, str, str | None]], org_filter: str | None = None) -> int:
    n = 0
    async with pm.session() as s:
        for mid, st, err in updates:
            m = (await s.execute(select(WhatsappMessage).where(WhatsappMessage.provider_message_id == mid))).scalar_one_or_none()
            if not m or (org_filter and m.org_id != org_filter):
                continue
            order = ["not_configured", "failed", "sent", "delivered", "read"]
            if st == "failed" or order.index(st) > order.index(m.status if m.status in order else "sent"):
                m.status, m.error, m.updated_at = st, err or m.error, pm.now_utc()
                n += 1
        await s.commit()
    return n


async def _forward_inbound(org_id: str, messages: list[dict]) -> None:
    """G7 (2026-10-23): hand verified replies to the approvals service (service token; org from the matched config).
    Best-effort — Meta must get its 200 regardless, and a lost reply only means the button can be pressed again."""
    import httpx
    from shared.utils.config import settings
    from shared.utils.internal import internal_headers
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            await c.post(f"{settings.APPROVALS_URL}/approvals/whatsapp/inbound", json={"messages": messages[:50]},
                         headers=internal_headers(org_id, "notification"))
    except Exception as e:                                   # noqa: BLE001
        import logging
        logging.getLogger("lexora.whatsapp").warning("forward to approvals failed: %s", e)


@router.post("/webhook")
async def receipt(request: Request):
    body = await request.body()
    ctype = request.headers.get("content-type", "")
    if "form" in ctype:                                  # Twilio
        form = dict(await request.form())
        parsed = WA.parse_twilio_status({k: str(v) for k, v in form.items()})
        if not parsed:
            return {"ok": True, "ignored": True}
        async with pm.session() as s:
            m = (await s.execute(select(WhatsappMessage).where(WhatsappMessage.provider_message_id == parsed[0]))).scalar_one_or_none()
        if not m:
            return {"ok": True, "ignored": True}
        cfg = await store.load(m.org_id, "whatsapp")
        if not WA.verify_twilio_signature(cfg["config"].get("auth_token") or "", str(request.url),
                                          {k: str(v) for k, v in form.items()}, request.headers.get("x-twilio-signature")):
            raise HTTPException(403, "Bad signature")
        return {"ok": True, "updated": await _apply([parsed], m.org_id)}
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Not JSON")
    phone_id = None
    try:
        phone_id = data["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
    except (KeyError, IndexError, TypeError):
        pass
    sig = request.headers.get("x-hub-signature-256")
    for c in await _all_configs():
        if c["mode"] == "meta" and (not phone_id or c["config"].get("phone_number_id") == phone_id):
            if WA.verify_meta_signature(c["config"].get("app_secret") or "", body, sig):
                updated = await _apply(WA.parse_meta_statuses(data), c["org_id"])
                inbound = WA.parse_meta_inbound(data)
                if inbound:                     # G7: button replies / texts → approvals (signature already checked)
                    await _forward_inbound(c["org_id"], inbound)
                return {"ok": True, "updated": updated, "inbound": len(inbound)}
    raise HTTPException(403, "Bad signature")


@router.get("/messages")
async def messages(request: Request, limit: int = 300):
    org = _admin(request)
    async with pm.session() as s:
        rows = (await s.execute(select(WhatsappMessage).where(WhatsappMessage.org_id == org)
                                .order_by(WhatsappMessage.created_at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return {"items": [{"id": m.id, "to": m.to_masked, "template": m.template, "kind": m.kind, "provider": m.provider,
                       "status": m.status, "error": m.error, "message_id": m.provider_message_id,
                       "created_at": pm.iso(m.created_at), "updated_at": pm.iso(m.updated_at)} for m in rows]}


class TestIn(BaseModel):
    to: str


@router.post("/test")
async def test_send(body: TestIn, request: Request):
    org = _admin(request)
    wa = await store.load(org, "whatsapp")
    res = await mirror.send_whatsapp(org, wa, to=body.to, template=(wa["config"].get("template_notify") or ""),
                                     params=["Test message from Lexora HRM", "https://lexora.local"], kind="test")
    await record_audit(request, "integration.whatsapp.test", entity_type="provider", entity_id="whatsapp",
                       summary=f"WhatsApp test to {WA.mask_phone(body.to)}: {res['status']}" + (f" — {res['error']}" if res.get("error") else ""))
    return res
