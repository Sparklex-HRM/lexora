"""Lexora HRM — shared email sender (SMTP, e.g. Zoho Mail).

Reads SMTP config from env (SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS — set in Q-HRM/.env,
never committed). `send_email` is BEST-EFFORT: it runs the blocking smtplib call off
the event loop and NEVER raises into the caller, so a notification failure can never
break the business action (invite, approval, pay run). Returns True only on a real send.

Port 587 → STARTTLS (SMTP_TLS=true); port 465 → implicit SSL. Zoho custom-domain org
accounts use smtppro.zoho.<region> (e.g. smtppro.zoho.in) and require an APP PASSWORD.
"""
import asyncio
import html
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER"))


def _cfg() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", ""),
        "port": int(os.getenv("SMTP_PORT", "587") or 587),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "sender": os.getenv("SMTP_FROM", "Lexora HRM <no-reply@lexora.local>"),
        "tls": os.getenv("SMTP_TLS", "true").lower() in ("1", "true", "yes"),
    }


def _send_sync(to, subject: str, body: str, html: str | None) -> bool:
    c = _cfg()
    if not c["host"] or not c["user"]:
        return False                       # not configured (e.g. local dev) — no-op
    msg = EmailMessage()
    msg["From"] = c["sender"]
    msg["To"] = to if isinstance(to, str) else ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)                  # plain-text part (always)
    if html:
        msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if c["port"] == 465:
        with smtplib.SMTP_SSL(c["host"], 465, context=ctx, timeout=20) as s:
            s.login(c["user"], c["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
            s.ehlo()
            if c["tls"]:
                s.starttls(context=ctx)
                s.ehlo()
            s.login(c["user"], c["password"])
            s.send_message(msg)
    return True


async def send_email(to, subject: str, body: str, html: str | None = None) -> bool:
    """Send an email via the configured SMTP. Best-effort — returns False (never
    raises) if SMTP is unconfigured or the send fails, so callers can fire-and-forget."""
    try:
        return await asyncio.to_thread(_send_sync, to, subject, body, html)
    except Exception:
        return False


class Safe(str):
    """A fragment of TRUSTED markup that brand_html must not escape (e.g. Safe("<b>")).
    Never wrap user data in Safe — use strong(text) to bold a user value safely."""


def strong(text) -> "Safe":
    """Bold a value for an e-mail line: the value itself is HTML-escaped."""
    return Safe(f"<b>{html.escape(str(text if text is not None else ''))}</b>")


def _part_html(p) -> str:
    if isinstance(p, Safe):
        return str(p)
    return html.escape(str(p if p is not None else ""))


def _line_html(line) -> str:
    """A line is a str (escaped), a Safe (verbatim) or a list/tuple of such parts."""
    if isinstance(line, (list, tuple)):
        return "".join(_part_html(p) for p in line)
    return _part_html(line)


def plain(line) -> str:
    """The plain-text form of a brand_html line (markup parts dropped, tags stripped)."""
    if isinstance(line, (list, tuple)):
        return "".join(plain(p) for p in line)
    if isinstance(line, Safe):
        return html.unescape(re.sub(r"<[^>]*>", "", str(line)))
    return str(line if line is not None else "")


def safe_link(url) -> str | None:
    """Only absolute http(s) URLs may become a button; anything else (javascript:, data:, …) → None."""
    u = str(url or "").strip()
    if not u or any(ch in u for ch in "\r\n\t\x00"):
        return None
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return u


_DANGEROUS_SCHEMES = {"javascript", "data", "vbscript", "file", "blob"}
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")


def dangerous_url(value) -> bool:
    """True when a free-text link / place / website would run code if used as an href:
    javascript:, data:, vbscript:, file:, blob: — also when hidden with tabs, newlines or
    leading control characters the way browsers forgive them ("java\\tscript:")."""
    s = str(value or "")
    s = re.sub(r"[\t\n\r]", "", s).lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f\x10\x11\x12"
                                          "\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f  ")
    m = _SCHEME.match(s)
    return bool(m and m.group(1).lower() in _DANGEROUS_SCHEMES)


def brand_html(title: str, lines: list, cta_label: str | None = None,
               cta_url: str | None = None) -> str:
    """Minimal branded HTML wrapper (Lexora HRM purple) for notification emails.

    Every title / line / CTA label is HTML-escaped (user data — names, notes, org names — can
    never inject markup); pass Safe(...) / strong(...) parts for intentional formatting. The CTA
    button is only rendered for an http(s) URL, and the URL is attribute-escaped.

    Header = the logo's colours as live text (mail clients block remote images by default):
    ink #1a073c band, "Jet" white, "hro" lavender #c4a2f6, tagline in the logo's grey-violet tint.
    CTA = primary-600 #6522d6 (white text 7.6:1)."""
    title = _line_html(title)
    body = "".join(f"<p style='margin:0 0 12px;color:#334155;font-size:14px'>{_line_html(ln)}</p>"
                   for ln in lines)
    cta = ""
    url = safe_link(cta_url)
    if cta_label and url:
        cta = (f"<a href=\"{html.escape(url, quote=True)}\" style='display:inline-block;margin-top:8px;background:#6522d6;"
               f"color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;"
               f"font-size:14px;font-weight:600'>{_line_html(cta_label)}</a>")
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;"
        "border:1px solid #e4d8f6;border-radius:12px;overflow:hidden'>"
        "<div style='background:#1a073c;padding:14px 20px'>"
        "<span style='font-size:22px;font-weight:800;letter-spacing:-0.5px'>"
        "<span style='color:#ffffff'>Jet</span><span style='color:#c4a2f6'>hro</span></span>"
        "<div style='color:#cdbef0;font-size:9px;letter-spacing:2.5px;margin-top:2px'>AUTONOMOUS HR OPERATIONS</div></div>"
        f"<div style='padding:20px'><h2 style='margin:0 0 14px;color:#1a073c;font-size:18px'>{title}</h2>"
        f"{body}{cta}</div>"
        "<div style='padding:12px 20px;background:#f6f2fe;color:#45396b;font-size:11px'>"
        "Sent by Lexora HRM · you're receiving this because of activity on your account.</div></div>")
