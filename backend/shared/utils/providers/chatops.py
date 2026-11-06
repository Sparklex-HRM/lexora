"""Slack / Microsoft Teams — outbound notifications through an incoming-webhook
URL per org (opt-in per notification kind), and inbound slash commands with a
signature check. Everything pure lives here; the DB and HTTP routes are in the
notification service (``chatops_routes.py``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

from shared.utils.providers import http
from shared.utils.providers.base import Field, NotConfigured, Provider, register


class Slack(Provider):
    key, label, group = "slack", "Slack", "messaging"
    modes = {"": ""}
    fields = {"": [Field("webhook_url", "Incoming webhook URL", secret=True),
                   Field("signing_secret", "Signing secret (slash commands)", secret=True, required=False),
                   Field("kinds", "Notification types to mirror (comma list)", required=False,
                         help="e.g. approval,leave,payroll — empty = none"),
                   Field("user_links", "Linked chat users", required=False, kind="hidden")]}
    note = "Posts a short line (title + link, never amounts) to the webhook. Slash commands need the signing secret."

    async def test(self, mode, cfg):
        if not cfg.get("webhook_url"):
            return {"ok": False, "message": "Not configured: missing Incoming webhook URL"}
        r = await http.post_json(cfg["webhook_url"], slack_payload("Lexora HRM test message", "", "System"))
        return {"ok": r.ok, "message": "Slack accepted the test message." if r.ok
                else f"Slack refused: {r.status or ''} {r.excerpt(120)}".strip()}


class Teams(Provider):
    key, label, group = "teams", "Microsoft Teams", "messaging"
    modes = {"": ""}
    fields = {"": [Field("webhook_url", "Incoming webhook (Workflows) URL", secret=True),
                   Field("outgoing_secret", "Outgoing webhook security token (commands)", secret=True, required=False),
                   Field("kinds", "Notification types to mirror (comma list)", required=False),
                   Field("user_links", "Linked chat users", required=False, kind="hidden")]}
    note = "Posts an Adaptive Card (title + link, never amounts). Commands need the outgoing-webhook token."

    async def test(self, mode, cfg):
        if not cfg.get("webhook_url"):
            return {"ok": False, "message": "Not configured: missing Incoming webhook URL"}
        r = await http.post_json(cfg["webhook_url"], teams_payload("Lexora HRM test message", "", "System"))
        return {"ok": r.ok, "message": "Teams accepted the test message." if r.ok
                else f"Teams refused: {r.status or ''} {r.excerpt(120)}".strip()}


register(Slack())
register(Teams())


def kinds_opted_in(cfg: dict) -> set[str]:
    return {k.strip() for k in (cfg.get("kinds") or "").split(",") if k.strip()}


def slack_payload(title: str, link: str, kind_label: str) -> dict:
    text = f"*{kind_label}* — {title}" + (f"\n<{link}|Open in Lexora HRM>" if link else "")
    return {"text": f"{kind_label} — {title}", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}


def teams_payload(title: str, link: str, kind_label: str) -> dict:
    body = [{"type": "TextBlock", "text": kind_label, "weight": "Bolder", "size": "Small"},
            {"type": "TextBlock", "text": title, "wrap": True}]
    card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": body}
    if link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in Lexora HRM", "url": link}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


async def send(provider_key: str, cfg: dict, *, title: str, link: str, kind_label: str) -> dict:
    url = (cfg or {}).get("webhook_url")
    if not url:
        raise NotConfigured(f"{'Slack' if provider_key == 'slack' else 'Teams'} is not configured.")
    payload = slack_payload(title, link, kind_label) if provider_key == "slack" else teams_payload(title, link, kind_label)
    r = await http.post_json(url, payload)
    return {"ok": r.ok, "status": r.status, "message": None if r.ok else r.excerpt(200)}


# ── inbound command signatures ───────────────────────────────────────────────

def verify_slack_signature(signing_secret: str, timestamp: str | None, body: bytes, signature: str | None,
                           now: float | None = None, tolerance: int = 300) -> bool:
    """Slack: v0=HMAC-SHA256(secret, 'v0:<ts>:<raw body>'), timestamp within 5 minutes."""
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        return False
    base = b"v0:" + str(ts).encode() + b":" + (body or b"")
    want = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.strip())


def verify_teams_hmac(secret_b64: str, body: bytes, auth_header: str | None) -> bool:
    """Teams outgoing webhook: Authorization: HMAC <base64 HMAC-SHA256(base64-decoded secret, raw body)>."""
    if not secret_b64 or not auth_header or not auth_header.startswith("HMAC "):
        return False
    try:
        key = base64.b64decode(secret_b64)
    except Exception:  # noqa: BLE001
        return False
    want = base64.b64encode(hmac.new(key, body or b"", hashlib.sha256).digest()).decode()
    return hmac.compare_digest(want, auth_header[5:].strip())


# ── commands ─────────────────────────────────────────────────────────────────

COMMANDS = {"leave balance": "Your leave balances", "payslip": "A link to your latest payslip",
            "approve <id>": "Approve a waiting item by its number"}


def parse_command(text: str) -> tuple[str, str]:
    """('leave_balance' | 'payslip' | 'approve' | 'help' | 'unknown', argument)."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = re.sub(r"^/?lexora\b\s*", "", t)          # "/lexora leave balance" → "leave balance"
    t = re.sub(r"^/", "", t)
    if t in ("leave balance", "leave", "balance", "leave balances"):
        return "leave_balance", ""
    if t in ("payslip", "payslips", "my payslip"):
        return "payslip", ""
    m = re.match(r"^approve\s+([A-Za-z0-9\-]{1,80})$", t)
    if m:
        return "approve", m.group(1)
    if t in ("", "help", "?"):
        return "help", ""
    return "unknown", t[:40]


def help_text() -> str:
    return "Commands: " + " · ".join(f"`/{k}` — {v}" for k, v in COMMANDS.items())
