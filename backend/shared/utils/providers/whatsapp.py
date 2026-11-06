"""WhatsApp provider — Meta Cloud API or Twilio. REAL send (behind a timeout) when
credentials exist; honest ``not_configured`` otherwise. Delivery receipts arrive
on a signed webhook (``verify_meta_signature`` / ``verify_twilio_signature``).

``sent`` is written only when the API answered 2xx with a message id. Delivery /
read come from the receipt webhook, never assumed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from shared.utils.providers import http
from shared.utils.providers.base import Field, NotConfigured, Provider, ProviderError, register


# S5: one approved template per business event. Every template takes TWO body variables:
# {{1}} = the notification title (amounts and id numbers hidden), {{2}} = the link into Lexora HRM.
# The notification KIND that triggers each event (shared/utils/notify.py KINDS).
EVENTS = {"payslip_ready": ("Payslip ready", "payroll"), "leave_approved": ("Leave approved", "leave"),
          "attendance_missing": ("Attendance missing", "attendance"), "invite": ("Invitation to join", "onboarding")}
KIND_TO_EVENT = {kind: ev for ev, (_, kind) in EVENTS.items()}
# G7 (2026-10-23): consent keys that are NOT notification mirrors. "approvals" = approval requests with
# Approve / Reject buttons (approvals/wa_approvals.py) — needs its OWN explicit opt-in (a blanket opt-in with
# no event list does NOT cover it) and the Meta template `template_approval`.
OPTIN_EXTRA = {"approvals": "Approval requests with Approve / Reject buttons",
               # 2026-10-26: agentic chase channel (shared/utils/whatsapp_channel.py) — its OWN explicit opt-in, like approvals
               "agent_chase": "Reminders from Lexora HRM's agents about a task waiting on you (no amounts, no other person's data)"}
APPROVAL_CONSENT = ("I agree to receive approval requests from my employer on WhatsApp at this number, with Approve / "
                    "Reject buttons. A button acts as me in Lexora HRM, once, and only while the request is open. I can stop "
                    "this at any time in Lexora HRM.")


def _event_fields(prefix: str) -> list[Field]:
    return [Field(f"template_{ev}", f"{prefix}: {label.lower()}", required=False,
                  help="Two body variables: {{1}} title, {{2}} link") for ev, (label, _) in EVENTS.items()]


class WhatsApp(Provider):
    key, label, group = "whatsapp", "WhatsApp messages", "messaging"
    modes = {"meta": "Meta WhatsApp Cloud API", "twilio": "Twilio WhatsApp"}
    fields = {"meta": [Field("phone_number_id", "Phone number id"),
                       Field("access_token", "Permanent access token", secret=True),
                       Field("app_secret", "App secret (webhook signature)", secret=True),
                       Field("verify_token", "Webhook verify token", secret=True),
                       Field("template_otp", "Template name: one-time code", required=False),
                       Field("template_notify", "Template name: notification", required=False),
                       *_event_fields("Template name"),
                       Field("template_approval", "Template name: approval request (2 quick-reply buttons)", required=False,
                             help="Body {{1}} what is waiting, {{2}} who asked; buttons 0 = Approve, 1 = Reject (quick reply)"),
                       Field("template_lang", "Template language code", required=False, help="en (default), hi, en_US …"),
                       Field("kinds", "Notification types to mirror (comma list)", required=False)],
              "twilio": [Field("account_sid", "Account SID"), Field("auth_token", "Auth token", secret=True),
                         Field("from_number", "From number (whatsapp:+14155238886)"),
                         Field("template_otp", "Content SID: one-time code", required=False),
                         Field("template_notify", "Content SID: notification", required=False),
                         *_event_fields("Content SID"),
                         Field("kinds", "Notification types to mirror (comma list)", required=False)]}
    docs_url = "https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates"
    note = ("Sends real template messages once the credentials are saved and switched on — and only to employees who "
            "opted in to WhatsApp. Receipts update the status.")

    async def test(self, mode, cfg):
        miss = self.missing(mode, cfg)
        if miss:
            return {"ok": False, "message": "Not configured: missing " + ", ".join(miss)}
        if mode == "meta":
            r = await http.get_json(f"https://graph.facebook.com/v20.0/{cfg['phone_number_id']}",
                                    headers={"Authorization": f"Bearer {cfg['access_token']}"})
        else:
            r = await http.get_json(f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}.json",
                                    auth=(cfg["account_sid"], cfg["auth_token"]))
        if r.ok:
            return {"ok": True, "message": "The provider accepted the credentials."}
        return {"ok": False, "message": f"The provider refused: {r.status or ''} {r.excerpt(120)}".strip()}


register(WhatsApp())


def normalise_phone(v: str) -> str:
    """E.164. A 10-digit North American number (416 555 0142) gets the +1 country code."""
    d = re.sub(r"[^\d+]", "", v or "")
    if d and not d.startswith("+"):
        d = ("+1" + d) if len(d) == 10 else ("+" + d)
    return d


def mask_phone(v: str) -> str:
    d = normalise_phone(v)
    return ("•" * max(len(d) - 4, 0)) + d[-4:] if d else ""


def kinds_opted_in(cfg: dict) -> set[str]:
    return {k.strip() for k in (cfg.get("kinds") or "").split(",") if k.strip()}


def event_for_kind(kind: str) -> str | None:
    return KIND_TO_EVENT.get(kind or "")


def template_for(cfg: dict, kind: str) -> str:
    """The event's own template when set (payslip ready, leave approved, …), else the generic one."""
    ev = event_for_kind(kind)
    return ((cfg.get(f"template_{ev}") if ev else "") or cfg.get("template_notify") or "").strip()


def phone_hash(phone: str) -> str:
    return hashlib.sha256(normalise_phone(phone).encode()).hexdigest()


async def send_template(cfg: dict, mode: str, *, to: str, template: str, params: list[str],
                        lang: str = "en") -> dict:
    """Send one template message. Returns {status: 'sent', message_id} ONLY on provider acceptance."""
    to = normalise_phone(to)
    if not to:
        raise ProviderError("No phone number on record.")
    if mode == "meta":
        for k in ("phone_number_id", "access_token"):
            if not cfg.get(k):
                raise NotConfigured("WhatsApp (Meta) is not configured.")
        if not template:
            raise NotConfigured("No template name is set for this message type.")
        payload = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "template",
                   "template": {"name": template, "language": {"code": lang},
                                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]}}
        r = await http.post_json(f"https://graph.facebook.com/v20.0/{cfg['phone_number_id']}/messages", payload,
                                 headers={"Authorization": f"Bearer {cfg['access_token']}"})
        if r.ok and isinstance(r.json, dict) and r.json.get("messages"):
            return {"status": "sent", "message_id": r.json["messages"][0].get("id")}
        raise ProviderError(f"Meta refused the message: {r.status or ''} {r.excerpt(160)}".strip())
    if mode == "twilio":
        for k in ("account_sid", "auth_token", "from_number"):
            if not cfg.get(k):
                raise NotConfigured("WhatsApp (Twilio) is not configured.")
        if not template:
            raise NotConfigured("No content SID is set for this message type.")
        import json as _json
        data = {"From": cfg["from_number"], "To": f"whatsapp:{to}", "ContentSid": template,
                "ContentVariables": _json.dumps({str(i + 1): p for i, p in enumerate(params)})}
        r = await http.request("POST", f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json",
                               data=data, auth=(cfg["account_sid"], cfg["auth_token"]))
        if r.ok and isinstance(r.json, dict) and r.json.get("sid"):
            return {"status": "sent", "message_id": r.json["sid"]}
        raise ProviderError(f"Twilio refused the message: {r.status or ''} {r.excerpt(160)}".strip())
    raise NotConfigured("WhatsApp is not configured.")


# ── receipt webhooks ─────────────────────────────────────────────────────────

def verify_meta_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: 'sha256=<hex hmac of the raw body>'."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:].strip())


def verify_twilio_signature(auth_token: str, url: str, form: dict, header: str | None) -> bool:
    """X-Twilio-Signature: base64(HMAC-SHA1(token, url + sorted(k+v)))."""
    if not auth_token or not header:
        return False
    s = url + "".join(k + str(form[k]) for k in sorted(form))
    want = base64.b64encode(hmac.new(auth_token.encode(), s.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(want, header.strip())


META_STATUS = {"sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed"}
TWILIO_STATUS = {"queued": "sent", "sent": "sent", "delivered": "delivered", "read": "read",
                 "failed": "failed", "undelivered": "failed"}


def parse_meta_statuses(body: dict) -> list[tuple[str, str, str | None]]:
    """[(message_id, status, error)] from a Cloud API webhook body."""
    out = []
    for entry in (body or {}).get("entry") or []:
        for ch in entry.get("changes") or []:
            for st in (ch.get("value") or {}).get("statuses") or []:
                err = None
                if st.get("errors"):
                    e0 = st["errors"][0]
                    err = f"{e0.get('code', '')} {e0.get('title', '')}".strip()
                mapped = META_STATUS.get(st.get("status"))
                if st.get("id") and mapped:
                    out.append((st["id"], mapped, err))
    return out


def parse_twilio_status(form: dict) -> tuple[str, str, str | None] | None:
    sid, st = form.get("MessageSid"), TWILIO_STATUS.get((form.get("MessageStatus") or "").lower())
    if not sid or not st:
        return None
    err = form.get("ErrorCode")
    return sid, st, (f"Error {err}" if err else None)


# ── G7 (2026-10-23): approvals over WhatsApp — template with quick-reply buttons, inbound replies ──
# Meta Cloud API: a business-initiated message must be an approved TEMPLATE; quick-reply button payloads are
# set per send and come back on the webhook as messages[].button.payload (template) or
# messages[].interactive.button_reply.id (interactive). Free-form text is allowed only inside the 24-hour
# window the person's own reply opens. Sources:
#   https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates (button components)
#   https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components (messages: button / interactive / text)

async def send_buttons_template(cfg: dict, *, to: str, template: str, params: list[str], payloads: list[str],
                                lang: str = "en") -> dict:
    """Meta only (Twilio's content API is not wired for quick replies). {status:'sent', message_id} on acceptance."""
    to = normalise_phone(to)
    if not to:
        raise ProviderError("No phone number on record.")
    for k in ("phone_number_id", "access_token"):
        if not cfg.get(k):
            raise NotConfigured("WhatsApp (Meta) is not configured.")
    if not template:
        raise NotConfigured("No approval template name is set (Integrations → WhatsApp → template_approval).")
    comps = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
    comps += [{"type": "button", "sub_type": "quick_reply", "index": str(i),
               "parameters": [{"type": "payload", "payload": pl}]} for i, pl in enumerate(payloads)]
    payload = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "template",
               "template": {"name": template, "language": {"code": lang}, "components": comps}}
    r = await http.post_json(f"https://graph.facebook.com/v20.0/{cfg['phone_number_id']}/messages", payload,
                             headers={"Authorization": f"Bearer {cfg['access_token']}"})
    if r.ok and isinstance(r.json, dict) and r.json.get("messages"):
        return {"status": "sent", "message_id": r.json["messages"][0].get("id")}
    raise ProviderError(f"Meta refused the message: {r.status or ''} {r.excerpt(160)}".strip())


async def send_text(cfg: dict, *, to: str, text: str) -> dict:
    """A plain reply inside the 24-hour window (our answer to the approver's button press)."""
    to = normalise_phone(to)
    for k in ("phone_number_id", "access_token"):
        if not cfg.get(k):
            raise NotConfigured("WhatsApp (Meta) is not configured.")
    payload = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text", "text": {"body": text[:1000]}}
    r = await http.post_json(f"https://graph.facebook.com/v20.0/{cfg['phone_number_id']}/messages", payload,
                             headers={"Authorization": f"Bearer {cfg['access_token']}"})
    if r.ok and isinstance(r.json, dict) and r.json.get("messages"):
        return {"status": "sent", "message_id": r.json["messages"][0].get("id")}
    raise ProviderError(f"Meta refused the message: {r.status or ''} {r.excerpt(160)}".strip())


def parse_meta_inbound(body: dict) -> list[dict]:
    """[{id, from, type, payload, text, timestamp}] — button / interactive replies and text messages only."""
    out = []
    for entry in (body or {}).get("entry") or []:
        for ch in entry.get("changes") or []:
            for m in (ch.get("value") or {}).get("messages") or []:
                t, payload, text = m.get("type"), None, None
                if t == "button":
                    payload, text = (m.get("button") or {}).get("payload"), (m.get("button") or {}).get("text")
                elif t == "interactive":
                    it = m.get("interactive") or {}
                    payload = (it.get("button_reply") or {}).get("id")
                    text = (it.get("button_reply") or {}).get("title")
                elif t == "text":
                    text = (m.get("text") or {}).get("body")
                else:
                    continue
                if not m.get("from") or not m.get("id"):
                    continue
                out.append({"id": str(m["id"])[:120], "from": normalise_phone(str(m["from"])), "type": t,
                            "payload": (str(payload)[:256] if payload else None),
                            "text": (str(text)[:1000] if text else None), "timestamp": str(m.get("timestamp") or "")[:20]})
    return out
