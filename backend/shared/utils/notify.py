"""Lexora HRM — notify(): one call for an in-app notification plus an optional e-mail.

    from shared.utils.notify import notify, notify_later, cancel_later

    await notify(org_id, roles=["hr"], employee_ids=[emp_id],
                 title="Confirmation due for review", body="Probation ends on 30 Oct.",
                 link="/probation", kind="probation")

Rules this module keeps:

* BEST-EFFORT. ``notify`` never raises into the caller and never blocks on SMTP: the
  in-app rows are written straight away, the e-mail goes out in a background task
  (and the notification service retries anything still queued).
* HONEST STATUS. An e-mail is ``sent`` only after the SMTP server accepted it. With
  no SMTP configured the status is ``not_configured`` and the in-app notification
  still goes. A failure keeps its reason. Every recipient/channel is one row in the
  delivery log.
* NOTHING SENSITIVE BY E-MAIL. The e-mail carries the title and a link — never the
  ``body``. The title is also scrubbed of anything that looks like an amount or a
  statutory id. Details stay on the screen behind the login.
* Templates use ``{{name}}`` placeholders from a fixed allow-list. Substitution is a
  plain text replace — nothing is evaluated.
"""
import asyncio
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from sqlalchemy import select, update

from shared.utils import directory
from shared.utils import platform_models as pm
from shared.utils.platform_models import Delivery, Notification, NotifyPreference, NotifyTemplate, Reminder

log = logging.getLogger("lexora.notify")

# Kinds callers may use. Any other string still works; it just shows under its own name.
KINDS: dict[str, str] = {
    "general": "General",
    "approval": "Approvals",
    "leave": "Leave",
    "attendance": "Time and attendance",
    "payroll": "Payroll",
    "onboarding": "Onboarding",
    "compensation": "Salary revisions",
    "career": "Career changes",
    "probation": "Probation and confirmation",
    "letter": "Letters",
    "exit": "Exit",
    "hiring": "Hiring",
    "performance": "Performance",
    "reminder": "Reminders",
    "system": "System",
}


def kind_label(kind: str) -> str:
    return KINDS.get(kind) or (kind or "general").replace("_", " ").capitalize()


# ══════════════════════════ templates (allow-list, no code) ════════════════════

PLACEHOLDERS: dict[str, str] = {
    "title": "The notification title (amounts and id numbers are hidden)",
    "recipient_name": "The name of the person receiving the e-mail",
    "org_name": "Your company name",
    "kind": "The notification type, for example Leave",
    "link": "The full link to the screen in Lexora HRM",
    "date": "Today's date",
}
DEFAULT_SUBJECT = "{{title}}"
DEFAULT_BODY = ("Hello {{recipient_name}},\n\n"
                "{{title}}\n\n"
                "Sign in to see the details: {{link}}\n\n"
                "{{org_name}}")

_PH = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def unknown_placeholders(text: str) -> list[str]:
    return sorted({m.group(1) for m in _PH.finditer(text or "") if m.group(1) not in PLACEHOLDERS})


def render_template(text: str, values: dict) -> str:
    """Replace allow-listed ``{{name}}`` placeholders. Anything else is left as typed."""
    def sub(m):
        name = m.group(1)
        return str(values.get(name, "")) if name in PLACEHOLDERS else m.group(0)
    return _PH.sub(sub, text or "")


_SENSITIVE = [
    re.compile(r"(?:\$|€|£|\b(?:CA\$|C\$|CAD|USD)\s?)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE),  # money
    re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"),             # 120,000 / 120,000.00
    re.compile(r"\b\d{3}[- ]?\d{3}[- ]?\d{3}\b"),               # SIN (9 digits)
    re.compile(r"\b\d{6,}\b"),                                  # any long number
]


def scrub_sensitive(text: str) -> str:
    """Hide anything in ``text`` that looks like an amount or an id number."""
    out = text or ""
    for rx in _SENSITIVE:
        out = rx.sub("[hidden]", out)
    return out


def absolute_link(link: str) -> str:
    base = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    if not link:
        return base
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"{base}/{link.lstrip('/')}"


# ══════════════════════════ SMTP transport ═════════════════════════════════════

class SmtpTransport:
    """Server-level SMTP from the environment (SMTP_HOST / PORT / USER / PASSWORD / FROM / TLS)."""

    def configured(self) -> bool:
        return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER"))

    def send(self, *, to: str, subject: str, text: str, sender_name: str = "", reply_to: str = "") -> None:
        """Send one message. Returns only if the server accepted it; raises otherwise."""
        host = os.getenv("SMTP_HOST", "")
        port = int(os.getenv("SMTP_PORT", "587") or 587)
        user = os.getenv("SMTP_USER", "")
        password = os.getenv("SMTP_PASSWORD", "")
        tls = os.getenv("SMTP_TLS", "true").lower() in ("1", "true", "yes")
        env_name, env_addr = parseaddr(os.getenv("SMTP_FROM", "") or user)
        msg = EmailMessage()
        msg["From"] = formataddr((sender_name or env_name or "Lexora HRM", env_addr or user))
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(text)
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, 465, context=ctx, timeout=20) as s:
                s.login(user, password)
                refused = s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if tls:
                    s.starttls(context=ctx)
                    s.ehlo()
                s.login(user, password)
                refused = s.send_message(msg)
        if refused:
            raise RuntimeError(f"Recipient refused by the mail server: {refused}")


_transport = SmtpTransport()


def set_transport(t) -> None:
    """Tests swap in a fake transport."""
    global _transport
    _transport = t


def smtp_status() -> dict:
    ok = False
    try:
        ok = bool(_transport.configured())
    except Exception:                                        # noqa: BLE001
        ok = False
    return {"configured": ok,
            "message": ("E-mail is switched on for this installation." if ok else
                        "No SMTP server is set up on this installation. In-app notifications work; "
                        "e-mails are logged as \"not configured\" and are not sent.")}


# ══════════════════════════ recipients ═════════════════════════════════════════

async def resolve_recipients(org_id: str, *, user_ids=None, roles=None, employee_ids=None) -> list[dict]:
    """Turn user ids / roles / employee ids into a de-duplicated recipient list.

    Each recipient: {user_id | None, name, email}. An employee without a login has
    ``user_id`` None (they can still get the e-mail, never the in-app notification).
    """
    user_ids = [str(u) for u in (user_ids or []) if u]
    roles = [r for r in (roles or []) if r]
    employee_ids = [str(e) for e in (employee_ids or []) if e]
    if not (user_ids or roles or employee_ids):
        return []
    users = await directory.org_users(org_id)
    by_id = {u["id"]: u for u in users}
    by_emp = {u["employee_id"]: u for u in users if u.get("employee_id")}

    out: dict[str, dict] = {}

    def add_user(u: dict):
        out.setdefault("u:" + u["id"], {"user_id": u["id"], "name": directory.display_name(u),
                                         "email": u.get("email")})

    for uid in user_ids:
        if uid in by_id:
            add_user(by_id[uid])
    if roles:
        for u in users:
            if directory.role_in(u.get("role"), roles):
                add_user(u)
    missing = [e for e in employee_ids if e not in by_emp]
    for eid in employee_ids:
        if eid in by_emp:
            add_user(by_emp[eid])
    if missing:
        emps = {e["id"]: e for e in await directory.org_employees(org_id)}
        for eid in missing:
            e = emps.get(eid)
            if e:
                out.setdefault("e:" + eid, {"user_id": None, "name": directory.display_name(e),
                                            "email": e.get("email")})
    return list(out.values())


# ══════════════════════════ notify ═════════════════════════════════════════════

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
    """Wait for background e-mail tasks (tests, and a clean service shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def notify(org_id: str, *, user_ids: list[str] | None = None, roles: list[str] | None = None,
                 employee_ids: list[str] | None = None, title: str, body: str = "", link: str = "",
                 kind: str = "general", email: bool = True) -> None:
    """Notify people in-app and (unless ``email=False`` or they switched it off) by e-mail.

    Recipients are the union of ``user_ids``, everyone holding one of ``roles`` and the
    logins of ``employee_ids`` — each person once. Best-effort: never raises.
    """
    try:
        await _notify(str(org_id), user_ids=user_ids, roles=roles, employee_ids=employee_ids,
                      title=title, body=body, link=link, kind=kind or "general", email=email)
    except Exception as e:                                   # noqa: BLE001
        log.warning("notify(%s, %r) failed: %s", org_id, title, e)


async def _notify(org_id, *, user_ids, roles, employee_ids, title, body, link, kind, email) -> list[str]:
    recipients = await resolve_recipients(org_id, user_ids=user_ids, roles=roles, employee_ids=employee_ids)
    if not recipients:
        return []
    title = (title or "").strip()[:300] or "Notification"
    link = (link or "")[:400]
    queued: list[str] = []
    async with pm.session() as s:
        off: set[str] = set()
        uids = [r["user_id"] for r in recipients if r["user_id"]]
        if email and uids:
            rows = (await s.execute(select(NotifyPreference).where(
                NotifyPreference.org_id == org_id, NotifyPreference.user_id.in_(uids),
                NotifyPreference.kind == kind, NotifyPreference.email_on.is_(False)))).scalars().all()
            off = {p.user_id for p in rows}
        for r in recipients:
            common = dict(org_id=org_id, user_id=r["user_id"], recipient_name=r["name"],
                          recipient_email=r["email"], kind=kind, title=title, link=link)
            nid = None
            if r["user_id"]:
                n = Notification(id=pm.new_id(), org_id=org_id, user_id=r["user_id"], title=title,
                                 body=body or "", link=link, kind=kind)
                s.add(n)
                nid = n.id
                s.add(Delivery(channel="in_app", status="delivered", notification_id=nid, **common))
            else:
                s.add(Delivery(channel="in_app", status="no_login",
                               reason="This person has no login yet.", **common))
            if not email:
                continue
            if r["user_id"] in off:
                s.add(Delivery(channel="email", status="off_by_user", notification_id=nid,
                               reason="The person switched e-mail off for this type.", **common))
            elif not r["email"]:
                s.add(Delivery(channel="email", status="no_address", notification_id=nid,
                               reason="No e-mail address on record.", **common))
            else:
                d = Delivery(id=pm.new_id(), channel="email", status="queued", notification_id=nid, **common)
                s.add(d)
                queued.append(d.id)
        await s.commit()
    if queued:
        _spawn(dispatch_email(delivery_ids=queued))
    _spawn(_mirror(org_id, kind, title, link, uids))      # G3: Slack / Teams / WhatsApp mirror (opt-in per kind)
    return queued


async def _mirror(org_id, kind, title, link, user_ids) -> None:
    try:
        from shared.utils.providers.mirror import mirror_notification
        await mirror_notification(org_id, kind=kind, title=title, link=link, user_ids=user_ids)
    except Exception as e:                                   # noqa: BLE001
        log.warning("notification mirror failed: %s", e)


# ══════════════════════════ e-mail dispatch ════════════════════════════════════

async def _template_for(s, org_id: str, kind: str) -> tuple[str, str]:
    t = (await s.execute(select(NotifyTemplate).where(
        NotifyTemplate.org_id == org_id, NotifyTemplate.kind == kind))).scalar_one_or_none()
    if t:
        return t.subject or DEFAULT_SUBJECT, t.body or DEFAULT_BODY
    return DEFAULT_SUBJECT, DEFAULT_BODY


async def _send_one(delivery_id: str) -> str:
    """Claim one queued delivery, try to send it, write the true outcome. Returns the status."""
    from shared.utils.org_settings import get_setting
    now = pm.now_utc()
    async with pm.session() as s:
        claimed = await s.execute(update(Delivery).where(
            Delivery.id == delivery_id, Delivery.status == "queued")
            .values(status="sending", attempts=Delivery.attempts + 1, updated_at=now))
        await s.commit()
        if claimed.rowcount != 1:
            return "skipped"                                  # someone else has it
        d = (await s.execute(select(Delivery).where(Delivery.id == delivery_id))).scalar_one()

        if not _transport.configured():
            d.status, d.reason = "not_configured", "No SMTP server is set up on this installation."
            d.updated_at = pm.now_utc()
            await s.commit()
            return d.status

        subject_t, body_t = await _template_for(s, d.org_id, d.kind)
        sender = await get_setting(d.org_id, "notification_sender", {}) or {}
        values = {"title": scrub_sensitive(d.title), "recipient_name": d.recipient_name or "",
                  "org_name": (sender.get("sender_name") or await directory.org_name(d.org_id) or "Lexora HRM"),
                  "kind": kind_label(d.kind), "link": absolute_link(d.link),
                  "date": pm.now_utc().date().isoformat()}
        subject = render_template(subject_t, values).replace("\n", " ").strip()[:300]
        text = render_template(body_t, values)
        try:
            await asyncio.to_thread(_transport.send, to=d.recipient_email, subject=subject, text=text,
                                    sender_name=sender.get("sender_name") or "",
                                    reply_to=sender.get("reply_to") or "")
            d.status, d.reason, d.sent_at = "sent", None, pm.now_utc()
        except Exception as e:                               # noqa: BLE001
            d.status = "failed"
            d.reason = (f"{type(e).__name__}: {e}")[:400]
        d.updated_at = pm.now_utc()
        await s.commit()
        return d.status


async def dispatch_email(delivery_ids: list[str] | None = None, limit: int = 50) -> dict:
    """Send queued e-mails. With no ids: the oldest ``limit`` queued rows (the service tick)."""
    counts: dict[str, int] = {}
    try:
        if delivery_ids is None:
            stale = pm.now_utc() - timedelta(minutes=10)
            async with pm.session() as s:
                # a row stuck in "sending" (the process died mid-send) goes back to the queue
                await s.execute(update(Delivery).where(
                    Delivery.status == "sending", Delivery.updated_at < stale).values(status="queued"))
                await s.commit()
                delivery_ids = list((await s.execute(
                    select(Delivery.id).where(Delivery.channel == "email", Delivery.status == "queued")
                    .order_by(Delivery.created_at).limit(limit))).scalars().all())
        for did in delivery_ids:
            try:
                st = await _send_one(did)
            except Exception as e:                           # noqa: BLE001
                log.warning("e-mail delivery %s crashed: %s", did, e)
                st = "error"
            counts[st] = counts.get(st, 0) + 1
    except Exception as e:                                   # noqa: BLE001
        log.warning("dispatch_email failed: %s", e)
    return counts


async def retry_delivery(org_id: str, delivery_id: str) -> str:
    """Admin retry of a failed / not-configured e-mail. Returns the new status."""
    async with pm.session() as s:
        res = await s.execute(update(Delivery).where(
            Delivery.id == delivery_id, Delivery.org_id == str(org_id), Delivery.channel == "email",
            Delivery.status.in_(("failed", "not_configured"))).values(status="queued", updated_at=pm.now_utc()))
        await s.commit()
        if res.rowcount != 1:
            return "not_retryable"
    return await _send_one(delivery_id)


# ══════════════════════════ reminders (notify_later) ═══════════════════════════

async def notify_later(org_id: str, *, due_at: datetime, key: str | None = None, **notify_kwargs) -> None:
    """Fire ``notify(org_id, **notify_kwargs)`` at ``due_at`` (the notification service
    checks once a minute). ``key`` makes it idempotent and cancellable: registering the
    same key again replaces the pending reminder. Best-effort, never raises."""
    try:
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        allowed = {"user_ids", "roles", "employee_ids", "title", "body", "link", "kind", "email"}
        payload = {k: v for k, v in notify_kwargs.items() if k in allowed}
        async with pm.session() as s:
            if key:
                await s.execute(update(Reminder).where(
                    Reminder.org_id == str(org_id), Reminder.key == key, Reminder.status == "pending")
                    .values(status="cancelled"))
            s.add(Reminder(org_id=str(org_id), key=key, due_at=due_at, payload=payload))
            await s.commit()
    except Exception as e:                                   # noqa: BLE001
        log.warning("notify_later(%s, %s) failed: %s", org_id, key, e)


async def cancel_later(org_id: str, key: str) -> None:
    """Cancel a pending reminder registered with ``key``. Never raises."""
    try:
        async with pm.session() as s:
            await s.execute(update(Reminder).where(
                Reminder.org_id == str(org_id), Reminder.key == key, Reminder.status == "pending")
                .values(status="cancelled"))
            await s.commit()
    except Exception as e:                                   # noqa: BLE001
        log.warning("cancel_later(%s, %s) failed: %s", org_id, key, e)


async def fire_due_reminders(now: datetime | None = None, limit: int = 100) -> int:
    """Fire every pending reminder that is due. Returns how many fired."""
    now = now or pm.now_utc()
    fired = 0
    try:
        async with pm.session() as s:
            due = (await s.execute(select(Reminder).where(
                Reminder.status == "pending", Reminder.due_at <= now)
                .order_by(Reminder.due_at).limit(limit))).scalars().all()
        for r in due:
            async with pm.session() as s:
                res = await s.execute(update(Reminder).where(
                    Reminder.id == r.id, Reminder.status == "pending")
                    .values(status="fired", fired_at=pm.now_utc()))
                await s.commit()
            if res.rowcount != 1:
                continue
            p = dict(r.payload or {})
            if not p.get("title"):
                continue
            await notify(r.org_id, **p)
            fired += 1
    except Exception as e:                                   # noqa: BLE001
        log.warning("fire_due_reminders failed: %s", e)
    return fired
