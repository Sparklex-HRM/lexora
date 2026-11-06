"""Mirror an in-app notification to Slack / Teams / WhatsApp when the org opted
that notification KIND in. Called by ``notify()`` after its rows are committed —
in the background, best-effort, never raising. The in-app notification is the
record; a mirror is a copy of the TITLE and the LINK only (never the body, never
amounts — the title is scrubbed like the e-mail subject).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from shared.utils import platform_models as pm
from shared.utils.providers import chatops, store, whatsapp
from shared.utils.providers.base import NotConfigured, ProviderError
from shared.utils.providers.models import WhatsappMessage

log = logging.getLogger("lexora.mirror")


async def mirror_notification(org_id: str, *, kind: str, title: str, link: str, user_ids: list[str]) -> None:
    try:
        from shared.utils.notify import absolute_link, kind_label, scrub_sensitive
        safe_title, full_link, label = scrub_sensitive(title), absolute_link(link), kind_label(kind)
        for key in ("slack", "teams"):
            cfg = await store.load(org_id, key)
            if cfg["enabled"] and kind in chatops.kinds_opted_in(cfg["config"]):
                try:
                    r = await chatops.send(key, cfg["config"], title=safe_title, link=full_link, kind_label=label)
                    if not r["ok"]:
                        await store.record_error(org_id, key, r["message"] or f"HTTP {r['status']}")
                except NotConfigured:
                    pass
        wa = await store.load(org_id, "whatsapp")
        if wa["enabled"] and kind in whatsapp.kinds_opted_in(wa["config"]) and user_ids:
            await _mirror_whatsapp(org_id, wa, kind=kind, title=safe_title, link=full_link, user_ids=user_ids)
    except Exception as e:                                   # noqa: BLE001
        log.warning("mirror_notification(%s) failed: %s", org_id, e)


async def phones_for_users(org_id: str, user_ids: list[str]) -> dict[str, str]:
    """user id → employee phone (users.employee_id → employees.phone). Empty when unreachable."""
    if not user_ids:
        return {}
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT u.id AS uid, e.phone FROM users u JOIN employees e ON e.id = u.employee_id "
                "WHERE u.org_id = CAST(:org AS uuid) AND e.phone IS NOT NULL AND e.phone <> ''"),
                {"org": str(org_id)})).mappings().all()
        want = {str(u) for u in user_ids}
        return {str(r["uid"]): r["phone"] for r in rows if str(r["uid"]) in want}
    except Exception as e:                                   # noqa: BLE001
        log.warning("phones_for_users failed: %s", e)
        return {}


async def opted_in_users(org_id: str, user_ids: list[str], phones: dict[str, str], kind: str) -> set[str]:
    """S5 — WhatsApp consent: the users (of ``phones``) with an ACTIVE opt-in for THIS number and this
    message type. Fails CLOSED: no table / no row / changed number → no WhatsApp."""
    if not phones:
        return set()
    try:
        from sqlalchemy import select
        from shared.utils.providers.models_s5 import WhatsappOptin
        async with pm.session() as s:
            rows = (await s.execute(select(WhatsappOptin).where(
                WhatsappOptin.org_id == str(org_id), WhatsappOptin.status == "opted_in",
                WhatsappOptin.user_id.in_([str(u) for u in phones])))).scalars().all()
    except Exception as e:                                   # noqa: BLE001
        log.warning("whatsapp opt-in lookup failed (sending nothing): %s", e)
        return set()
    ev = whatsapp.event_for_kind(kind)
    out = set()
    for r in rows:
        ph = phones.get(r.user_id or "")
        if ph and r.phone_hash == whatsapp.phone_hash(ph) and (not r.events or (ev and ev in r.events) or kind in r.events):
            out.add(r.user_id)
    return out


async def _mirror_whatsapp(org_id, wa: dict, *, kind, title, link, user_ids) -> None:
    phones = await phones_for_users(org_id, user_ids)
    if not phones:
        return
    allowed = await opted_in_users(org_id, user_ids, phones, kind)
    template = whatsapp.template_for(wa["config"], kind)
    for uid, phone in phones.items():
        if uid not in allowed:
            continue                                         # no opt-in on record → nothing sent, nothing logged as sent
        await send_whatsapp(org_id, wa, to=phone, template=template, params=[title[:200], link[:300]], kind=kind)


async def send_whatsapp(org_id, wa: dict, *, to: str, template: str, params: list[str], kind: str) -> dict:
    """Send + log one WhatsApp message with its TRUE status. Returns the log row's shape."""
    row = WhatsappMessage(id=pm.new_id(), org_id=str(org_id), to_masked=whatsapp.mask_phone(to),
                          template=template or "", kind=kind, provider=wa["mode"], status="not_configured")
    try:
        if not wa["enabled"]:
            raise NotConfigured("WhatsApp is switched off.")
        res = await whatsapp.send_template(wa["config"], wa["mode"], to=to, template=template, params=params,
                                           lang=(wa["config"].get("template_lang") or "en").strip() or "en")
        row.status, row.provider_message_id = res["status"], res.get("message_id")
    except NotConfigured as e:
        row.status, row.error = "not_configured", e.message
    except ProviderError as e:
        row.status, row.error = "failed", e.message[:300]
        await store.record_error(org_id, "whatsapp", e.message)
    try:
        async with pm.session() as s:
            s.add(row)
            await s.commit()
    except Exception as e:                                   # noqa: BLE001
        log.warning("whatsapp log write failed: %s", e)
    return {"id": row.id, "status": row.status, "error": row.error, "message_id": row.provider_message_id}
