"""WhatsApp chase channel for the agentic add-on — provider-agnostic, consent-gated, template-only (2026-10-26).

    from shared.utils.whatsapp_channel import send_chase, channel_plan, load_config
    res = await send_chase(org, employee_id=eid, kind="agent_chase", params=["Payroll Auditor", "2"], reminder_no=2)
    # → {"channel": "whatsapp", "status": "sent" | "would_send" | "skipped", "reason": …, "to": "+91•••••1234"}

Config: company setting ``notify.whatsapp`` (group ``notify`` in ``org_settings.GROUPS``; read/written through
``GET/PUT /ai/agents/channels/whatsapp``, org admin):
    {enabled: false (default), provider: "meta_cloud", phone_number_id: "", api_version: "v21.0",
     token_env: "",            # NAME of the environment variable holding the access token — the token itself is
                               # never stored in settings, the DB or the repo
     language: "en", after_reminders: 2, templates: {"agent_chase": ""}}

Rules (all enforced here, tested with a fake transport):
* APPROVED TEMPLATES ONLY — a message is a pre-approved Meta template name from ``templates`` plus positional
  parameters (role name, reminder number). There is no free-text path in this module.
* CONSENT — per-employee opt-in in ``int_whatsapp_optins`` (status ``opted_in``, ``agent_chase`` among the events — a
  blanket opt-in does NOT cover it — and the stored phone hash equals the employee's current number). No consent → skipped.
* PREFERENCE — in-app → e-mail → WhatsApp: ``channel_plan`` adds WhatsApp only when the org enabled it, the employee
  consented and the reminder number reached ``after_reminders`` (the first nudges stay in-app + e-mail).
* NO REAL SENDS UNLESS CONFIGURED — enabled + phone_number_id + the token env var set + a template name → the Meta
  Cloud transport; anything missing → ``LogOnlyTransport`` ("would send", logged, nothing leaves the machine).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Protocol

log = logging.getLogger("lexora.whatsapp.channel")

GROUP = "notify"
DEFAULTS = {"enabled": False, "provider": "meta_cloud", "phone_number_id": "", "api_version": "v21.0", "token_env": "",
            "language": "en", "after_reminders": 2, "templates": {"agent_chase": ""}}
CONSENT_EVENT = "agent_chase"
PROVIDERS = ("meta_cloud",)
FORBIDDEN = ("token", "secret", "password", "key", "credential")           # never a credential VALUE in the settings
_TEMPLATE_RE = re.compile(r"^[a-z0-9_]{1,512}$")                          # Meta template names: lowercase, digits, underscore


class Transport(Protocol):
    name: str

    async def send(self, message: dict) -> dict: ...


def normalise_phone(v: str | None) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 10:
        d = "91" + d
    return d


def mask(v: str | None) -> str:
    d = normalise_phone(v)
    return f"+{d[:2]}•••••{d[-4:]}" if len(d) >= 6 else "•••"


def phone_hash(phone: str | None) -> str:
    try:
        from shared.utils.providers.whatsapp import phone_hash as _ph
        return _ph(phone or "")
    except Exception:                                            # noqa: BLE001
        return hashlib.sha256(normalise_phone(phone).encode()).hexdigest()


def template_message(cfg: dict, *, to: str, kind: str, params: list) -> dict:
    """Meta WhatsApp Cloud API shape (POST /{api_version}/{phone_number_id}/messages). Template-only."""
    name = ((cfg.get("templates") or {}).get(kind) or "").strip()
    if not name or not _TEMPLATE_RE.match(name):
        raise ValueError(f"no approved template configured for {kind!r}")
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": normalise_phone(to), "type": "template",
            "template": {"name": name, "language": {"code": cfg.get("language") or "en"},
                         "components": [{"type": "body", "parameters": [{"type": "text", "text": str(p)[:60]} for p in params]}]}}


class LogOnlyTransport:
    """Not configured / local: logs what WOULD be sent; nothing leaves the machine."""
    name = "log_only"

    def __init__(self, reason: str = "not configured"):
        self.reason = reason

    async def send(self, message: dict) -> dict:
        log.info("whatsapp would send template %s to %s (%s)", (message.get("template") or {}).get("name"), mask(message.get("to")), self.reason)
        return {"accepted": False, "status": "would_send", "reason": self.reason}


class MetaCloudTransport:
    name = "meta_cloud"

    def __init__(self, *, phone_number_id: str, token: str, api_version: str = "v21.0", timeout: float = 15.0):
        self.url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self._token, self.timeout = token, timeout

    async def send(self, message: dict) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(self.url, headers={"Authorization": f"Bearer {self._token}"}, json=message)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            return {"accepted": False, "status": "failed", "reason": type(e).__name__}
        if r.status_code in (200, 201):
            mid = ((r.json() or {}).get("messages") or [{}])[0].get("id") if r.content else None
            return {"accepted": True, "status": "sent", "provider_message_id": mid}
        return {"accepted": False, "status": "failed", "reason": f"HTTP {r.status_code}"}


class FakeTransport:
    """Tests: records every message, accepts it."""
    name = "fake"

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, message: dict) -> dict:
        self.sent.append(message)
        return {"accepted": True, "status": "sent", "provider_message_id": f"fake-{len(self.sent)}"}


def clean_config(v: dict, current: dict | None = None) -> dict:
    out = {**DEFAULTS, **(current or {})}
    out["templates"] = {**DEFAULTS["templates"], **((current or {}).get("templates") or {})}
    for k, val in (v or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"{k} is not a WhatsApp channel setting")
        if k == "token_env":
            name = str(val or "").strip()
            if name and not re.match(r"^[A-Z][A-Z0-9_]{2,63}$", name):
                raise ValueError("token_env is the NAME of an environment variable (e.g. LEXORA_WA_TOKEN) — never the token itself")
            out[k] = name
        elif k == "templates":
            if not isinstance(val, dict):
                raise ValueError("templates must be an object {kind: approved template name}")
            for kind, name in val.items():
                if kind != CONSENT_EVENT:
                    raise ValueError(f"unknown template kind {kind!r}")
                name = str(name or "").strip()
                if name and not _TEMPLATE_RE.match(name):
                    raise ValueError("template names are lowercase letters, digits and underscores (as approved in Meta)")
                out["templates"][kind] = name
        elif k == "enabled":
            out[k] = bool(val)
        elif k == "provider":
            if val not in PROVIDERS:
                raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
            out[k] = val
        elif k == "after_reminders":
            n = int(val)
            if n < 1 or n > 10:
                raise ValueError("after_reminders must be 1–10")
            out[k] = n
        else:
            s = str(val or "").strip()[:64]
            if k == "phone_number_id" and s and not s.isdigit():
                raise ValueError("phone_number_id is the numeric id Meta shows for the business number")
            out[k] = s
    for k in out:
        if k != "token_env" and any(f in k.lower() for f in FORBIDDEN):
            raise ValueError("credentials are never stored in settings")
    return out


async def load_config(org: str) -> dict:
    try:
        from shared.utils.org_settings import get_setting
        v = await get_setting(org, f"{GROUP}.whatsapp", {}) or {}
    except Exception:                                            # noqa: BLE001
        v = {}
    out = {**DEFAULTS, **(v if isinstance(v, dict) else {})}
    out["templates"] = {**DEFAULTS["templates"], **((v or {}).get("templates") or {} if isinstance(v, dict) else {})}
    return out


def readiness(cfg: dict) -> dict:
    """What is missing before a REAL send could happen (shown on the settings card)."""
    missing = []
    if not cfg.get("enabled"):
        missing.append("switched off")
    if not cfg.get("phone_number_id"):
        missing.append("phone number id")
    if not cfg.get("token_env"):
        missing.append("token environment variable name")
    elif not os.getenv(cfg["token_env"]):
        missing.append(f"environment variable {cfg['token_env']} is not set on the server")
    if not (cfg.get("templates") or {}).get(CONSENT_EVENT):
        missing.append("approved template name for agent reminders")
    return {"can_send": not missing, "missing": missing}


def transport_for(cfg: dict) -> Transport:
    rd = readiness(cfg)
    if not rd["can_send"]:
        return LogOnlyTransport("not configured: " + ", ".join(rd["missing"]))
    return MetaCloudTransport(phone_number_id=cfg["phone_number_id"], token=os.environ[cfg["token_env"]], api_version=cfg.get("api_version") or "v21.0")


async def consent_for(org: str, employee_id: str | None) -> dict:
    """{ok, phone, reason} — opted in for agent_chase on the employee's CURRENT number."""
    if not employee_id:
        return {"ok": False, "phone": None, "reason": "no employee for this owner"}
    from sqlalchemy import text
    from shared.utils import platform_models as pm
    try:
        async with pm.session() as s:
            e = (await s.execute(text("SELECT phone FROM employees WHERE org_id = CAST(:o AS uuid) AND id = CAST(:e AS uuid)"), {"o": str(org), "e": str(employee_id)})).first()
            o = (await s.execute(text("SELECT status, phone_hash, events FROM int_whatsapp_optins WHERE CAST(org_id AS text) = :o AND CAST(employee_id AS text) = :e "
                                      "ORDER BY at DESC LIMIT 1"), {"o": str(org), "e": str(employee_id)})).first()
    except Exception as ex:                                      # noqa: BLE001
        return {"ok": False, "phone": None, "reason": f"consent unreadable ({type(ex).__name__})"}
    return consent_decision(phone=(e[0] if e else None), optin=({"status": o[0], "phone_hash": o[1], "events": o[2]} if o else None))


def consent_decision(*, phone: str | None, optin: dict | None) -> dict:
    if not phone:
        return {"ok": False, "phone": None, "reason": "no mobile number on the employee record"}
    if not optin or optin.get("status") != "opted_in":
        return {"ok": False, "phone": phone, "reason": "employee has not opted in to WhatsApp"}
    if CONSENT_EVENT not in (optin.get("events") or []):
        return {"ok": False, "phone": phone, "reason": "opt-in does not cover agent reminders (explicit 'agent_chase' consent needed)"}
    if optin.get("phone_hash") and optin["phone_hash"] != phone_hash(phone):
        return {"ok": False, "phone": phone, "reason": "the number changed since the opt-in — ask again"}
    return {"ok": True, "phone": phone, "reason": None}


def channel_plan(cfg: dict, *, consent_ok: bool, reminder_no: int) -> list[str]:
    """Preference order in-app → e-mail → WhatsApp (only when enabled, consented and the reminder number is reached)."""
    plan = ["in_app", "email"]
    if cfg.get("enabled") and consent_ok and int(reminder_no or 0) >= int(cfg.get("after_reminders") or 2):
        plan.append("whatsapp")
    return plan


async def send_chase(org: str, *, employee_id: str | None, params: list, reminder_no: int, kind: str = CONSENT_EVENT,
                     cfg: dict | None = None, transport: Transport | None = None, consent: dict | None = None) -> dict:
    """One WhatsApp reminder if the plan includes it. Never raises."""
    try:
        cfg = cfg or await load_config(org)
        if not cfg.get("enabled"):
            return {"channel": "whatsapp", "status": "skipped", "reason": "WhatsApp channel switched off for the company"}
        consent = consent or await consent_for(org, employee_id)
        if "whatsapp" not in channel_plan(cfg, consent_ok=consent["ok"], reminder_no=reminder_no):
            return {"channel": "whatsapp", "status": "skipped", "reason": consent.get("reason") or f"in-app + e-mail first (WhatsApp from reminder {cfg.get('after_reminders')})"}
        msg = template_message(cfg, to=consent["phone"], kind=kind, params=params)
        tr = transport or transport_for(cfg)
        res = await tr.send(msg)
        return {"channel": "whatsapp", "status": res.get("status") or ("sent" if res.get("accepted") else "failed"), "reason": res.get("reason"),
                "transport": getattr(tr, "name", "?"), "template": msg["template"]["name"], "to": mask(consent["phone"]),
                "provider_message_id": res.get("provider_message_id")}
    except Exception as e:                                       # noqa: BLE001
        return {"channel": "whatsapp", "status": "skipped", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
