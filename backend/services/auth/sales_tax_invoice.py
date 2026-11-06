"""Auth service — GST/HST invoice for the Lexora HRM subscription (Canada).

The SELLER is the Lexora HRM operator's own business. Its details are CONFIGURATION,
never written in code:

    LEXORA_SELLER_LEGAL_NAME     registered legal name
    LEXORA_SELLER_GST_HST_NUMBER GST/HST registration number: BN + RT + 4 digits (123456789RT0001)
    LEXORA_SELLER_ADDRESS        business address (one line)
    LEXORA_SELLER_PROVINCE       2-letter province of the seller
    LEXORA_SELLER_EMAIL          optional, printed on the invoice
    LEXORA_SALES_TAX_RATES       optional JSON override of the rate table, e.g. {"ON": 13, "NS": 14}

While the seller's legal name + GST/HST number + address are not all set, every document
is a PRO FORMA ("not a GST/HST invoice") — we never print a tax invoice with invented
seller data.

Rate table (default; an accountant confirms it before the first real invoice — the operator
can override it with LEXORA_SALES_TAX_RATES without a code change):
    GST 5 %  — AB, BC, MB, NT, NU, QC, SK, YT
    HST 13 % — ON
    HST 14 % — NS (from 1 April 2025)
    HST 15 % — NB, NL, PE
    Source: CRA, "GST/HST rates" (canada.ca, GST/HST for businesses).
The province used is the customer's billing province (place of supply = the recipient's
Canadian address on record). Provincial sales taxes (QST in Québec, PST in BC / MB / SK) are
NOT added: they depend on the seller's own provincial registrations, so the invoice says so.

Invoice number: "LX-2026-00001" — consecutive per calendar year.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/Toronto")

PROVINCE_NAMES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
}
HST_PROVINCES = ("ON", "NS", "NB", "NL", "PE")
DEFAULT_RATES = {"AB": 5.0, "BC": 5.0, "MB": 5.0, "NT": 5.0, "NU": 5.0, "QC": 5.0, "SK": 5.0, "YT": 5.0,
                 "ON": 13.0, "NS": 14.0, "NB": 15.0, "NL": 15.0, "PE": 15.0}
PST_NOTE = {"QC": "QST is not included on this invoice.", "BC": "BC PST is not included on this invoice.",
            "MB": "Manitoba RST is not included on this invoice.", "SK": "Saskatchewan PST is not included on this invoice."}


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def rates() -> dict:
    out = dict(DEFAULT_RATES)
    raw = _env("LEXORA_SALES_TAX_RATES")
    if raw:
        try:
            for k, v in (json.loads(raw) or {}).items():
                if str(k).upper() in PROVINCE_NAMES:
                    out[str(k).upper()] = float(v)
        except (ValueError, TypeError, AttributeError):
            pass                                   # a broken override keeps the default table
    return out


def rate_for(province: str | None) -> float | None:
    return rates().get((province or "").upper()) if province else None


def tax_label(province: str | None) -> str:
    p = (province or "").upper()
    r = rate_for(p)
    if r is None:
        return "GST/HST"
    return f"{'HST' if p in HST_PROVINCES else 'GST'} {r:g}%"


def seller() -> dict:
    """The seller block from configuration + whether it is complete."""
    num = _env("LEXORA_SELLER_GST_HST_NUMBER").upper().replace(" ", "")
    prov = _env("LEXORA_SELLER_PROVINCE").upper() or None
    s = {"legal_name": _env("LEXORA_SELLER_LEGAL_NAME") or None, "gst_hst_number": num or None,
         "address": _env("LEXORA_SELLER_ADDRESS") or None, "province": prov, "email": _env("LEXORA_SELLER_EMAIL") or None}
    missing = [k for k in ("legal_name", "gst_hst_number", "address") if not s[k]]
    if num and not (len(num) == 15 and num[:9].isdigit() and num[9:11] == "RT" and num[11:].isdigit()):
        missing.append("gst_hst_number (must look like 123456789RT0001)")
    if prov and prov not in PROVINCE_NAMES:
        missing.append("province (not a Canadian province/territory)")
    s["configured"] = not missing
    s["missing"] = missing
    return s


def _r2(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def tax_split(subtotal: float, province: str | None, rate_pct: float | None = None) -> dict:
    """One GST or HST line for the customer's province. Unknown province → 'undetermined':
    no tax is added and the document says why — never a silent guess."""
    p = (province or "").upper() or None
    rate = rate_pct if rate_pct is not None else rate_for(p)
    if rate is None:
        return {"tax_lines": [], "gst": 0.0, "total": _r2(subtotal), "supply": "undetermined", "rate_pct": None,
                "province": p}
    code = "hst" if p in HST_PROVINCES else "gst"
    amt = _r2(subtotal * rate / 100)
    lines = [{"code": code, "label": f"{code.upper()} {rate:g}%", "rate": rate, "amount": amt}]
    return {"tax_lines": lines, "gst": amt, "total": _r2(subtotal + amt), "supply": p, "rate_pct": rate, "province": p}


def invoice_year(d: date) -> str:
    return f"{d.year}"


def invoice_number(year: str, seq: int, prefix: str = "LX") -> str:
    """'LX-2026-00001'."""
    return f"{prefix}-{year}-{seq:05d}"


def build_document(*, number: str | None, issue_date: date, buyer: dict, lines: list[dict],
                   subtotal: float, period_label: str, currency: str = "CAD") -> dict:
    """The full invoice document (what the PDF prints and what is stored)."""
    s = seller()
    prov = (buyer.get("province") or "").upper() or None
    split = tax_split(subtotal, prov)
    kind = "tax_invoice" if s["configured"] and prov else "proforma"
    notes = []
    if not s["configured"]:
        notes.append("Seller GST/HST details are not configured (LEXORA_SELLER_LEGAL_NAME / _GST_HST_NUMBER / _ADDRESS)")
    if not prov:
        notes.append("Customer province unknown — add the billing province to calculate GST/HST")
    if prov in PST_NOTE:
        notes.append(PST_NOTE[prov])
    return {
        "kind": kind, "title": "INVOICE" if kind == "tax_invoice" else "PRO FORMA — NOT A GST/HST INVOICE",
        "number": number if kind == "tax_invoice" else None, "issue_date": issue_date.isoformat(),
        "seller": s, "buyer": buyer,
        "place_of_supply": {"province": prov, "province_name": PROVINCE_NAMES.get(prov or "")},
        "supply": split["supply"], "lines": lines, "subtotal": _r2(subtotal), "tax_lines": split["tax_lines"],
        "gst": split["gst"], "total": split["total"], "currency": currency, "period_label": period_label,
        "notes": notes,
    }


def render_pdf(doc: dict) -> bytes:
    try:
        from shared.utils.pdf_fonts import UnicodePDF as _PDF
    except Exception:                       # font helper not present → Latin-1 core font
        from fpdf import FPDF as _PDF
    pdf = _PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    uni = getattr(pdf, "unicode_ok", False)

    def money(v):
        v = float(v)
        return f"{'-' if v < 0 else ''}${abs(v):,.2f}"

    def t(s):
        s = "" if s is None else str(s)
        return s if uni else s.encode("latin-1", "replace").decode("latin-1")

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, t(doc["title"]), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 9)
    s, b = doc["seller"], doc["buyer"]
    pdf.multi_cell(0, 5, t(f"From: {s.get('legal_name') or '[seller legal name not configured]'}\n"
                           f"{s.get('address') or '[address not configured]'}\n"
                           f"GST/HST registration no.: {s.get('gst_hst_number') or '[not configured]'}"),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.multi_cell(0, 5, t(f"Invoice no.: {doc.get('number') or '(pro forma — no invoice number)'}    "
                           f"Date: {doc['issue_date']}    Period: {doc['period_label']}"), new_x="LMARGIN", new_y="NEXT")
    pos = doc["place_of_supply"]
    pdf.multi_cell(0, 5, t(f"Bill to: {b.get('name')}\n{b.get('address') or ''}\n"
                           f"Province: {pos.get('province_name') or 'not known'}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 7, t("Description"), border=1)
    pdf.cell(20, 7, "Qty", border=1, align="R")
    pdf.cell(0, 7, t("Amount (CAD)"), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for ln in doc["lines"]:
        pdf.cell(120, 7, t(str(ln.get("label"))[:72]), border=1)
        pdf.cell(20, 7, str(ln.get("qty", 1)), border=1, align="R")
        pdf.cell(0, 7, t(money(ln.get("amount", 0))), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(140, 7, t("Subtotal"), border=1)
    pdf.cell(0, 7, t(money(doc["subtotal"])), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for tl in doc["tax_lines"]:
        pdf.cell(140, 7, t(tl["label"]), border=1)
        pdf.cell(0, 7, t(money(tl["amount"])), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(140, 8, t("Total (CAD)"), border=1)
    pdf.cell(0, 8, t(money(doc["total"])), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.ln(2)
    for n in doc.get("notes") or []:
        pdf.multi_cell(0, 4, t("Note: " + n), new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 4, t(f"Generated {datetime.now(ET).strftime('%Y-%m-%d %H:%M')} ET by Lexora HRM. "
                           "This is a computer-generated document."), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
