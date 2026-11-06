"""G7 (2026-10-23) — decide approvals from WhatsApp (Approve / Reject buttons).

Flow
  1. dispatch(org): for every pending inbox item that can be decided in the inbox (callback set), every person the
     web inbox would show it to (same ``may_act``; the admin-override is NOT messaged) who has an ACTIVE WhatsApp
     opt-in that explicitly includes "approvals" on their CURRENT number gets ONE template message with two
     quick-reply buttons. Each button carries a signed, single-use token.
  2. The approver taps a button → Meta calls the existing public webhook (/notifications/whatsapp/webhook, signature
     checked there with the org's app secret) → the notification service forwards the verified messages here
     (POST /approvals/whatsapp/inbound, service token only).
  3. We check: HMAC signature, the row exists in THAT org, not used, not expired, the reply came from the same phone
     the message went to, the opt-in is still active, the item is still pending in the same cycle.
     Approve → the decision is executed by calling OUR OWN web-inbox route POST /approvals/items/{id}/decide as that
     approver (role / employee id read fresh from their active login) — the same permission checks
     (own request, named / role / manager / delegate, chain steps), the same owner callback, the same decision
     row. Reject → the web inbox requires a reason, so we ask for one; the next text from that phone within 15
     minutes becomes the comment ("cancel" = do nothing).
  4. Every outcome is written to the audit trail (approvals.whatsapp.decide) and answered on WhatsApp.

Token = "wa1.<row id>.<a|r>.<hmac>" — HMAC-SHA256 over (row id, decision, org, item, approver, cycle, expiry) with
LEXORA_WA_APPROVAL_KEY (or a key derived from JWT_SECRET). Single use = an atomic UPDATE … WHERE status='sent'.
Expiry: LEXORA_WA_APPROVAL_TTL_HOURS (default 48). Nothing works without Meta keys: no provider / no template →
the row says "not_configured" and nothing is claimed as sent.

Routes
  GET  /approvals/whatsapp/status      org admin / HR: provider + template state, opted-in approvers, counts
  GET  /approvals/whatsapp/requests    org admin / HR: the message log (phone masked)
  POST /approvals/whatsapp/dispatch    org admin: send now (the background loop does it every minute)
  POST /approvals/whatsapp/inbound     SERVICE ONLY (X-Internal-Auth) — verified webhook messages
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, Text, select, text, update
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.internal import is_internal  # noqa: E402
from shared.utils.platform_models import ApprovalItem, PlatformBase  # noqa: E402

log = logging.getLogger("lexora.approvals.whatsapp")

router = APIRouter(prefix="/approvals/whatsapp", tags=["whatsapp-approvals"])
TTL_HOURS = int(os.getenv("LEXORA_WA_APPROVAL_TTL_HOURS", "48") or 48)
REASON_MINUTES = 15
LOOP_SECONDS = int(os.getenv("LEXORA_WA_APPROVAL_LOOP_SECONDS", "60") or 60)
MAX_SENDS_PER_RUN = 200
NEW_ITEM_WINDOW = timedelta(days=3)      # first switch-on does not blast an old backlog
OFFICE_ROLES = {"org_admin", "superadmin", "hr_admin", "hr_manager"}


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


class WaApproval(PlatformBase):
    """One WhatsApp approval request to one approver for one inbox item (+ cycle)."""
    __tablename__ = "apr_wa_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=pm.new_id)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    item_id: Mapped[str] = mapped_column(String(36), index=True)
    cycle: Mapped[int] = mapped_column(Integer, default=1)
    approver_user_id: Mapped[str] = mapped_column(String(36), index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    phone_masked: Mapped[str] = mapped_column(String(30), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    # new | sent | not_configured | failed | processing | awaiting_reason | done | refused | expired | superseded | cancelled
    decision: Mapped[str | None] = mapped_column(String(12))
    comment: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(300))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    last_inbound_id: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=pm.now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_READY = {"v": False}


async def ensure_tables() -> None:
    if not _READY["v"]:
        await pm.create_tables([WaApproval.__table__], retries=3, delay=1.0)
        _READY["v"] = True


# ── signed tokens (pure, unit-tested) ─────────────────────────────────────────
def _key() -> bytes:
    k = (os.getenv("LEXORA_WA_APPROVAL_KEY") or "").strip()
    if k:
        return k.encode()
    secret = os.getenv("JWT_SECRET", "dev-only-insecure-key-change-me")
    return hmac.new(secret.encode(), b"lexora-wa-approval-token-v1", hashlib.sha256).digest()


def _ts(dt: datetime) -> int:
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())


def sign(row_id: str, decision: str, org: str, item: str, user: str, cycle: int, expires_at: datetime) -> str:
    msg = f"wa1|{row_id}|{decision}|{org}|{item}|{user}|{cycle}|{_ts(expires_at)}".encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()[:40]


def make_token(row, decision: str) -> str:
    d = "a" if decision == "approve" else "r"
    return f"wa1.{row.id}.{d}.{sign(row.id, d, row.org_id, row.item_id, row.approver_user_id, row.cycle, row.expires_at)}"


def parse_token(token: str) -> tuple[str, str, str] | None:
    """(row id, 'a'|'r', sig) or None — shape only; the signature is checked against the stored row."""
    parts = (token or "").split(".")
    if len(parts) != 4 or parts[0] != "wa1" or parts[2] not in ("a", "r") or not (8 <= len(parts[1]) <= 36) \
            or len(parts[3]) != 40:
        return None
    return parts[1], parts[2], parts[3]


def token_valid(row, d: str, sig: str) -> bool:
    want = sign(row.id, d, row.org_id, row.item_id, row.approver_user_id, row.cycle, row.expires_at)
    return hmac.compare_digest(want, sig)


def phone_hash(phone: str) -> str:
    from shared.utils.providers import whatsapp as WA
    return WA.phone_hash(phone)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── dispatch ──────────────────────────────────────────────────────────────────
async def _optins(org: str) -> dict[str, object]:
    """user_id → opt-in row, ONLY active opt-ins that explicitly list 'approvals'."""
    from shared.utils.providers.models_s5 import WhatsappOptin
    async with pm.session() as s:
        rows = (await s.execute(select(WhatsappOptin).where(WhatsappOptin.org_id == str(org),
                                                            WhatsappOptin.status == "opted_in"))).scalars().all()
    return {r.user_id: r for r in rows if r.user_id and "approvals" in (r.events or [])}


async def _provider(org: str) -> tuple[dict, str | None]:
    """(store.load result, reason it cannot send | None)."""
    from shared.utils.providers import store
    wa = await store.load(org, "whatsapp")
    if not wa["enabled"]:
        return wa, "WhatsApp is not switched on (Integrations → WhatsApp)."
    if wa["mode"] != "meta":
        return wa, "WhatsApp approvals need the Meta Cloud API (Twilio is not wired for buttons)."
    cfg = wa["config"] or {}
    if not cfg.get("phone_number_id") or not cfg.get("access_token"):
        return wa, "WhatsApp (Meta) is not configured."
    if not (cfg.get("template_approval") or "").strip():
        return wa, "No approval template is set (template_approval, with two quick-reply buttons)."
    return wa, None


async def _log_message(org, phone, template, status, error, message_id) -> None:
    from shared.utils.providers import whatsapp as WA
    from shared.utils.providers.models import WhatsappMessage
    try:
        async with pm.session() as s:
            s.add(WhatsappMessage(id=pm.new_id(), org_id=str(org), to_masked=WA.mask_phone(phone), template=template or "",
                                  kind="approval", provider="meta", status=status, error=(error or None) and error[:300],
                                  provider_message_id=message_id))
            await s.commit()
    except Exception as e:                                   # noqa: BLE001
        log.warning("whatsapp approval log write failed: %s", e)


async def dispatch(org: str, *, now: datetime | None = None) -> dict:
    """Send the approval message to every opted-in approver who has not had one for this item / cycle."""
    from shared.utils.notify import kind_label, scrub_sensitive  # noqa: F401
    from shared.utils.providers import mirror, whatsapp as WA
    from shared.utils.providers.base import NotConfigured, ProviderError
    await ensure_tables()
    now = now or pm.now_utc()
    wa, why = await _provider(org)
    out = {"sent": 0, "skipped": 0, "failed": 0, "not_configured": why}
    optins = await _optins(org)
    if not optins:
        return out
    users = await directory.org_users(org)
    by_id = {u["id"]: u for u in users}
    employees = {e["id"]: e for e in await directory.org_employees(org)}
    delegations = await ac.load_delegations(org)
    phones = await mirror.phones_for_users(org, list(optins))
    async with pm.session() as s:
        items = (await s.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == org, ApprovalItem.status == "pending", ApprovalItem.callback_path != "",
            ApprovalItem.opened_at >= now - NEW_ITEM_WINDOW))).scalars().all()
        done = {(r.item_id, r.cycle, r.approver_user_id) for r in (await s.execute(select(WaApproval).where(
            WaApproval.org_id == org, WaApproval.item_id.in_([i.id for i in items] or ["-"])))).scalars().all()}
    sends = 0
    for i in items:
        item = ac.item_dict(i)
        for uid, opt in optins.items():
            u = by_id.get(uid)
            if not u or (i.id, i.cycle, uid) in done:
                continue
            v = ac.may_act(item, {"user_id": uid, "role": u.get("role"), "employee_id": u.get("employee_id")},
                           employees=employees, users=by_id, delegations=delegations)
            if not v["allowed"] or v["reason"] == "admin":
                continue
            phone = phones.get(uid) or ""
            if not phone or opt.phone_hash != WA.phone_hash(phone):
                out["skipped"] += 1                          # number changed since the opt-in → fresh opt-in needed
                continue
            if sends >= MAX_SENDS_PER_RUN:
                return out
            sends += 1
            title = scrub_sensitive(i.title or "")[:200]
            row = WaApproval(id=pm.new_id(), org_id=org, item_id=i.id, cycle=i.cycle, approver_user_id=uid,
                             phone_hash=WA.phone_hash(phone), phone_masked=WA.mask_phone(phone), title=title,
                             status="new", expires_at=now + timedelta(hours=TTL_HOURS), created_at=now)
            if why:
                row.status, row.result = "not_configured", why
            else:
                try:
                    res = await WA.send_buttons_template(
                        wa["config"], to=phone, template=wa["config"]["template_approval"].strip(),
                        params=[title, f"{(i.requester_name or 'Someone')[:60]} · {ac.kind_label(i.kind)}"],
                        payloads=[make_token(row, "approve"), make_token(row, "reject")],
                        lang=(wa["config"].get("template_lang") or "en").strip() or "en")
                    row.status, row.provider_message_id = "sent", res.get("message_id")
                except NotConfigured as e:
                    row.status, row.result = "not_configured", e.message[:300]
                except ProviderError as e:
                    row.status, row.result = "failed", e.message[:300]
            async with pm.session() as s:
                s.add(row)
                await s.commit()
            await _log_message(org, phone, (wa["config"] or {}).get("template_approval"), row.status, row.result,
                               row.provider_message_id)
            out["sent" if row.status == "sent" else "failed" if row.status == "failed" else "skipped"] += 1
    return out


async def expire_due(org: str | None = None, now: datetime | None = None) -> int:
    await ensure_tables()
    now = now or pm.now_utc()
    q = update(WaApproval).where(WaApproval.status.in_(("sent", "awaiting_reason")), WaApproval.expires_at < now)
    if org:
        q = q.where(WaApproval.org_id == org)
    async with pm.session() as s:
        res = await s.execute(q.values(status="expired"))
        await s.commit()
    return res.rowcount or 0


# ── inbound ───────────────────────────────────────────────────────────────────
async def _reply(org: str, to: str, msg: str) -> None:
    from shared.utils.providers import store, whatsapp as WA
    from shared.utils.providers.base import NotConfigured, ProviderError
    wa = await store.load(org, "whatsapp")
    if not wa["enabled"] or wa["mode"] != "meta":
        return
    try:
        await WA.send_text(wa["config"], to=to, text=msg)
    except (NotConfigured, ProviderError) as e:
        log.info("whatsapp approval reply not sent: %s", getattr(e, "message", e))


async def _claim(row_id: str, org: str, from_status: str, to_status: str, **values) -> bool:
    async with pm.session() as s:
        res = await s.execute(update(WaApproval).where(WaApproval.id == row_id, WaApproval.org_id == org,
                                                       WaApproval.status == from_status)
                              .values(status=to_status, **values))
        await s.commit()
    return (res.rowcount or 0) == 1


async def _set(row_id: str, **values) -> None:
    async with pm.session() as s:
        await s.execute(update(WaApproval).where(WaApproval.id == row_id).values(**values))
        await s.commit()


async def execute_as_approver(org: str, row: WaApproval, decision: str, comment: str) -> tuple[bool, str]:
    """Run the decision through the web inbox's own route as the approver. (ok, message)."""
    users = {u["id"]: u for u in await directory.org_users(org)}       # active logins only
    u = users.get(row.approver_user_id)
    if not u:
        return False, "Your Lexora HRM login is not active, so nothing was recorded."
    headers = {"X-Org-Id": org, "X-User-Id": u["id"], "X-Role": u.get("role") or "",
               "X-Employee-Id": u.get("employee_id") or "", "X-User-Email": u.get("email") or ""}
    url = f"{settings.APPROVALS_URL}/approvals/items/{row.item_id}/decide"
    try:
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post(url, json={"decision": decision, "comment": comment}, headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException):
        return False, "Lexora HRM did not answer in time. Nothing was changed — open Lexora HRM to decide."
    if 200 <= r.status_code < 300:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return True, (body.get("message") if isinstance(body, dict) and body.get("message") else
                      ("Approved" if decision == "approve" else "Rejected"))
    try:
        d = r.json().get("detail")
    except Exception:                                        # noqa: BLE001
        d = None
    if isinstance(d, dict):
        d = d.get("message")
    return False, (d if isinstance(d, str) and d else f"Lexora HRM refused this ({r.status_code}). Nothing was changed.")


async def _audit(org: str, row: WaApproval, decision: str, ok: bool, msg: str) -> None:
    from shared.utils.audit import record_audit
    u = next((x for x in await directory.org_users(org) if x["id"] == row.approver_user_id), {}) or {}
    shim = SimpleNamespace(headers={"x-org-id": org, "x-user-id": row.approver_user_id, "x-user-email": u.get("email") or "",
                                    "x-role": u.get("role") or ""})
    await record_audit(shim, "approvals.whatsapp.decide", entity_type="approval_item", entity_id=row.item_id,
                       summary=f"WhatsApp {decision} by {directory.display_name(u) or 'approver'} ({row.phone_masked}): "
                               f"{'recorded' if ok else 'refused'} — {msg[:150]}",
                       meta={"via": "whatsapp", "wa_request": row.id, "decision": decision, "ok": ok})


async def _load(row_id: str, org: str) -> WaApproval | None:
    async with pm.session() as s:
        return (await s.execute(select(WaApproval).where(WaApproval.id == row_id, WaApproval.org_id == org))).scalar_one_or_none()


async def _still_pending(row: WaApproval) -> bool:
    async with pm.session() as s:
        i = (await s.execute(select(ApprovalItem).where(ApprovalItem.id == row.item_id,
                                                        ApprovalItem.org_id == row.org_id))).scalar_one_or_none()
    return bool(i and i.status == "pending" and i.cycle == row.cycle)


async def _optin_ok(org: str, row: WaApproval) -> bool:
    opt = (await _optins(org)).get(row.approver_user_id)
    return bool(opt and opt.phone_hash == row.phone_hash)


async def _finish(org: str, row: WaApproval, sender: str, decision: str, comment: str) -> str:
    ok, msg = await execute_as_approver(org, row, decision, comment)
    await _set(row.id, status="done" if ok else "refused", decision=decision, comment=comment or None,
               result=msg[:300], decided_at=pm.now_utc())
    await _audit(org, row, decision, ok, msg)
    reply = (f"✅ {msg}: {row.title}" if ok else f"⚠️ Not recorded: {msg}")
    await _reply(org, sender, reply)
    return "done" if ok else "refused"


async def handle_message(org: str, m: dict, now: datetime | None = None) -> str:
    """One verified inbound message → outcome word (for tests / the log)."""
    await ensure_tables()
    now = now or pm.now_utc()
    sender = m.get("from") or ""
    if m.get("payload"):
        parsed = parse_token(m["payload"])
        if not parsed:
            return "ignored"
        rid, d, sig = parsed
        row = await _load(rid, org)
        if not row or not token_valid(row, d, sig):
            return "bad_token"
        if phone_hash(sender) != row.phone_hash:
            return "wrong_phone"                             # a forwarded / copied payload is useless
        if row.last_inbound_id == m.get("id"):
            return "replay"
        if _aware(row.expires_at) < now:
            await _claim(row.id, org, "sent", "expired")
            await _reply(org, sender, "This approval request has expired. Open Lexora HRM to decide.")
            return "expired"
        if not await _claim(row.id, org, "sent", "processing", used_at=now, last_inbound_id=m.get("id")):
            await _reply(org, sender, "This button was already used. Open Lexora HRM to see where it stands.")
            return "used"
        if not await _optin_ok(org, row):
            await _set(row.id, status="refused", result="WhatsApp consent withdrawn")
            return "no_consent"
        if not await _still_pending(row):
            await _set(row.id, status="superseded", result="Already decided elsewhere")
            await _reply(org, sender, f"Already decided: {row.title}. Nothing changed.")
            return "superseded"
        if d == "a":
            return await _finish(org, row, sender, "approve", "Approved on WhatsApp")
        await _set(row.id, status="awaiting_reason", reason_until=now + timedelta(minutes=REASON_MINUTES))
        await _reply(org, sender, f"Reply with the reason for rejecting “{row.title}” within {REASON_MINUTES} minutes. "
                                  "Send CANCEL to leave it pending.")
        return "awaiting_reason"
    txt = (m.get("text") or "").strip()
    if not txt:
        return "ignored"
    async with pm.session() as s:
        row = (await s.execute(select(WaApproval).where(
            WaApproval.org_id == org, WaApproval.phone_hash == phone_hash(sender), WaApproval.status == "awaiting_reason")
            .order_by(WaApproval.used_at.desc()))).scalars().first()
    if not row:
        return "ignored"
    if row.last_inbound_id == m.get("id"):
        return "replay"
    if not row.reason_until or _aware(row.reason_until) < now:
        await _claim(row.id, org, "awaiting_reason", "expired")
        await _reply(org, sender, "The time to give a reason has passed. Open Lexora HRM to reject with a reason.")
        return "expired"
    if txt.lower() in ("cancel", "stop", "no"):
        await _claim(row.id, org, "awaiting_reason", "cancelled", last_inbound_id=m.get("id"))
        await _reply(org, sender, f"Not rejected. “{row.title}” is still waiting for you in Lexora HRM.")
        return "cancelled"
    if len(txt) < 3:
        await _reply(org, sender, "Please type a reason (a few words).")
        return "short_reason"
    if not await _claim(row.id, org, "awaiting_reason", "processing", last_inbound_id=m.get("id")):
        return "used"
    if not await _optin_ok(org, row) or not await _still_pending(row):
        await _set(row.id, status="superseded", result="Already decided elsewhere or consent withdrawn")
        await _reply(org, sender, f"Already decided or no longer allowed: {row.title}. Nothing changed.")
        return "superseded"
    return await _finish(org, row, sender, "reject", txt[:500])


# ── routes ────────────────────────────────────────────────────────────────────
class InboundIn(BaseModel):
    messages: list[dict] = []


@router.post("/inbound")
async def inbound(body: InboundIn, request: Request):
    """Service-only: the notification service forwards Meta webhook messages AFTER checking the signature."""
    if not is_internal(request.headers) or request.headers.get("x-user-id"):
        raise err(403, "WA_APPR_INTERNAL", "Not allowed")
    org = request.headers.get("x-org-id") or ""
    if not org:
        raise err(400, "WA_APPR_ORG", "Organisation missing")
    out = []
    for m in body.messages[:50]:
        try:
            out.append(await handle_message(org, m))
        except Exception as e:                               # noqa: BLE001
            log.warning("whatsapp approval message failed: %s", e)
            out.append("error")
    return {"handled": out}


def _office(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org or not request.headers.get("x-user-id"):
        raise HTTPException(401, "Not signed in")
    if (request.headers.get("x-role") or "") not in OFFICE_ROLES:
        raise err(403, "WA_APPR_FORBIDDEN", "Only an org admin or HR can see WhatsApp approvals")
    return org


@router.get("/me")
async def me(request: Request):
    """Any signed-in user: the consent text, whether the org can send, and my own recent WhatsApp approvals."""
    from shared.utils.providers import whatsapp as WA
    org, uid = request.headers.get("x-org-id"), request.headers.get("x-user-id")
    if not org or not uid:
        raise HTTPException(401, "Not signed in")
    await ensure_tables()
    _, why = await _provider(org)
    async with pm.session() as s:
        rows = (await s.execute(select(WaApproval).where(WaApproval.org_id == org, WaApproval.approver_user_id == uid)
                                .order_by(WaApproval.created_at.desc()).limit(50))).scalars().all()
    return {"consent_text": WA.APPROVAL_CONSENT, "org_ready": why is None,
            "org_reason": None if why is None else "Your organisation has not finished setting up WhatsApp approvals.",
            "ttl_hours": TTL_HOURS,
            "items": [{"title": r.title, "status": r.status, "decision": r.decision, "phone": r.phone_masked,
                       "created_at": pm.iso(r.created_at), "decided_at": pm.iso(r.decided_at)} for r in rows]}


@router.get("/status")
async def status(request: Request):
    org = _office(request)
    await ensure_tables()
    wa, why = await _provider(org)
    optins = await _optins(org)
    async with pm.session() as s:
        rows = (await s.execute(text("SELECT status, COUNT(*) FROM apr_wa_requests WHERE org_id = :o GROUP BY status"),
                                {"o": org})).all()
    return {"ready": why is None, "reason": why, "mode": wa["mode"], "enabled": bool(wa["enabled"]),
            "template_set": bool(((wa["config"] or {}).get("template_approval") or "").strip()),
            "opted_in_approvers": len(optins), "ttl_hours": TTL_HOURS, "reason_minutes": REASON_MINUTES,
            "counts": {r[0]: int(r[1]) for r in rows}}


@router.get("/requests")
async def requests_log(request: Request, limit: int = 300):
    org = _office(request)
    await ensure_tables()
    users = {u["id"]: u for u in await directory.org_users(org)}
    async with pm.session() as s:
        rows = (await s.execute(select(WaApproval).where(WaApproval.org_id == org)
                                .order_by(WaApproval.created_at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return {"items": [{"id": r.id, "item_id": r.item_id, "title": r.title, "approver": directory.display_name(users.get(r.approver_user_id)) or "Former user",
                       "phone": r.phone_masked, "status": r.status, "decision": r.decision, "result": r.result,
                       "created_at": pm.iso(r.created_at), "expires_at": pm.iso(r.expires_at), "used_at": pm.iso(r.used_at),
                       "decided_at": pm.iso(r.decided_at)} for r in rows]}


@router.post("/dispatch")
async def dispatch_now(request: Request):
    org = _office(request)
    if (request.headers.get("x-role") or "") not in ("org_admin", "superadmin"):
        raise err(403, "WA_APPR_ADMIN", "Only an org admin can send now")
    await expire_due(org)
    return await dispatch(org)


# ── background loop ───────────────────────────────────────────────────────────
async def _orgs_with_whatsapp() -> list[str]:
    try:
        async with pm.session() as s:
            rows = (await s.execute(text("SELECT org_id FROM int_providers WHERE provider = 'whatsapp' AND enabled IS TRUE"))).all()
        return [str(r[0]) for r in rows]
    except Exception:                                        # noqa: BLE001
        return []


async def loop() -> None:
    while True:
        await asyncio.sleep(LOOP_SECONDS)
        try:
            await expire_due()
            for org in await _orgs_with_whatsapp():
                await dispatch(org)
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.warning("whatsapp approvals loop failed: %s", e)
