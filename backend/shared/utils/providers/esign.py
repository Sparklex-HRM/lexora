"""E-sign providers.

``internal``      — built and real: a one-time code to the signer, the signer's
                    typed name + IP + time, SHA-256 of the file before and after,
                    a visible signature block stamped on the last page and an
                    audit-certificate page appended. This is a simple
                    ELECTRONIC SIGNATURE record, NOT a certificate-based
                    digital signature — the screen says so.
``docusign``      — interface + config only. Not built.

The pure PDF work (``stamp_and_certify``) uses fpdf2 (overlay page) + pypdf
(merge/append); both are already in the service image.
"""
from __future__ import annotations

import hashlib
import io
import secrets
from datetime import datetime

from shared.utils.providers.base import Field, NotConfigured, Provider, register

LEGAL_NOTE = ("Internal e-sign records an electronic signature: who signed (name, e-mail or phone verified by a "
              "one-time code), from where (IP address), when, and the SHA-256 fingerprint of the exact file. "
              "It is an electronic record, not a qualified / certificate-based digital signature. "
              "Use a licensed provider where the law or your policy requires one.")


class InternalEsign(Provider):
    key, label, group = "esign_internal", "Internal e-sign (one-time code)", "documents"
    modes = {"": ""}
    fields = {"": [Field("otp_channel", "Send the one-time code by", required=False, kind="select",
                         help="email (default) or whatsapp (needs the WhatsApp provider)"),
                   Field("link_hours", "Signing link works for (hours)", required=False, kind="number")]}
    note = LEGAL_NOTE

    async def status(self, mode, cfg, enabled):
        return {"state": "configured", "message": "Built in. One-time code by e-mail (needs SMTP) or WhatsApp."}

    async def test(self, mode, cfg):
        from shared.utils.notify import smtp_status
        st = smtp_status()
        return {"ok": st["configured"], "message": st["message"]}


class DocuSign(Provider):
    key, label, group, stub = "esign_docusign", "DocuSign", "documents", True
    modes = {"": ""}
    fields = {"": [Field("base_url", "Account base URL"), Field("account_id", "Account id"),
                   Field("integration_key", "Integration key (client id)"),
                   Field("secret_key", "Secret key", secret=True)]}
    note = "Interface only — envelopes are not sent until the live connector is built."


register(InternalEsign())
register(DocuSign())


# ══════════════════════════ internal e-sign: pure pieces ═══════════════════════

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{(code or '').strip()}".encode()).hexdigest()


def new_link_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def is_pdf(data: bytes) -> bool:
    return bytes(data[:5]) == b"%PDF-"


def _latin(s) -> str:
    """Text for the stamp/certificate. Name kept for callers; the PDFs now use the bundled
    Unicode font (shared/utils/pdf_fonts.py), so signer names print as typed."""
    from shared.utils.pdf_fonts import pdf_text
    return pdf_text(s)


def signature_lines(*, signer_name: str, typed_name: str, verified_via: str, ip: str, signed_at: datetime,
                    doc_sha256: str, envelope_id: str) -> list[str]:
    return [_latin(f"Signed electronically by {typed_name or signer_name}"),
            _latin(f"Identity: {verified_via}"), f"Time: {signed_at.strftime('%d %b %Y %H:%M:%S UTC')}   IP: {_latin(ip or '-')}",
            f"Document fingerprint (SHA-256): {doc_sha256[:32]}...", f"Lexora HRM e-sign record {envelope_id}"]


def stamp_and_certify(pdf: bytes, *, block_lines: list[str], certificate: dict) -> bytes:
    """Stamp a visible signature block on the LAST page and append a certificate page.

    certificate = {title, signer_name, signer_contact, verified_via, typed_name, ip, user_agent,
                   signed_at, original_sha256, events: [{at, what, ip}], envelope_id, note}
    """
    from pypdf import PdfReader, PdfWriter

    from shared.utils.pdf_fonts import UnicodePDF as FPDF

    reader = PdfReader(io.BytesIO(pdf))
    writer = PdfWriter()
    last = len(reader.pages) - 1
    for i, page in enumerate(reader.pages):
        if i == last:
            w = float(page.mediabox.width) * 25.4 / 72
            h = float(page.mediabox.height) * 25.4 / 72
            ov = FPDF(unit="mm", format=(w, h))
            ov.set_auto_page_break(False)
            ov.add_page()
            bw, bh = min(110.0, w - 20), 6 + 5 * len(block_lines)
            x, y = w - bw - 10, h - bh - 12
            ov.set_fill_color(246, 242, 254)
            ov.set_draw_color(101, 34, 214)
            ov.rect(x, y, bw, bh, style="DF")
            ov.set_xy(x + 2, y + 2)
            ov.set_font("Helvetica", "B", 8)
            ov.cell(bw - 4, 4, _latin(block_lines[0][:80]), new_x="LMARGIN", new_y="NEXT")
            ov.set_font("Helvetica", "", 7)
            for ln in block_lines[1:]:
                ov.set_x(x + 2)
                ov.cell(bw - 4, 4, _latin(ln[:95]), new_x="LMARGIN", new_y="NEXT")
            ov_page = PdfReader(io.BytesIO(bytes(ov.output()))).pages[0]
            page.merge_page(ov_page)
        writer.add_page(page)

    c = certificate
    cert = FPDF()
    cert.set_auto_page_break(True, margin=15)
    cert.add_page()
    cert.set_font("Helvetica", "B", 15)
    cert.cell(0, 10, "Electronic signature certificate", new_x="LMARGIN", new_y="NEXT")
    cert.set_font("Helvetica", "", 9)
    rows = [("Document", c.get("title", "")), ("Record", c.get("envelope_id", "")),
            ("Signer", c.get("signer_name", "")), ("Contact", c.get("signer_contact", "")),
            ("Verified by", c.get("verified_via", "")), ("Typed name", c.get("typed_name", "")),
            ("Signed at (UTC)", c.get("signed_at", "")), ("IP address", c.get("ip", "")),
            ("Browser", (c.get("user_agent") or "")[:110]),
            ("Original file SHA-256", c.get("original_sha256", ""))]
    for k, v in rows:
        cert.set_font("Helvetica", "B", 9)
        cert.cell(45, 6, k, new_x="RIGHT", new_y="TOP")
        cert.set_font("Helvetica", "", 9)
        cert.multi_cell(0, 6, _latin(v or "-"), new_x="LMARGIN", new_y="NEXT")
    cert.ln(3)
    cert.set_font("Helvetica", "B", 10)
    cert.cell(0, 7, "Events", new_x="LMARGIN", new_y="NEXT")
    cert.set_font("Helvetica", "", 8)
    for ev in c.get("events") or []:
        cert.multi_cell(0, 5, _latin(f"{ev.get('at', '')}  {ev.get('what', '')}  {('from ' + ev['ip']) if ev.get('ip') else ''}"),
                        new_x="LMARGIN", new_y="NEXT")
    cert.ln(3)
    cert.set_font("Helvetica", "I", 8)
    cert.multi_cell(0, 4.5, _latin(c.get("note") or LEGAL_NOTE), new_x="LMARGIN", new_y="NEXT")
    for p in PdfReader(io.BytesIO(bytes(cert.output()))).pages:
        writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def provider_can_sign(provider_key: str) -> None:
    """Raise NotConfigured unless the provider has a built signing path."""
    if provider_key != "esign_internal":
        raise NotConfigured("This e-sign provider is not configured. Only the internal e-sign is built.")
