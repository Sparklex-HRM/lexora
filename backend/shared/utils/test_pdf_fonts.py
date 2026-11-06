"""Unicode PDFs: accented (Québec French) names and CAD amounts survive into the PDF text layer."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pypdf import PdfReader  # noqa: E402

from shared.utils import pdf_fonts as P  # noqa: E402


def pdf_text_of(data: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def test_font_and_licence_ship_together():
    assert P.font_available()
    lic = open(os.path.join(P.FONT_DIR, "OFL.txt"), encoding="utf-8").read()
    assert "SIL Open Font License" in lic and "Noto" in lic


def test_cad_uses_dollar_and_thousands_grouping():
    assert P.cad(123456.5) == "$123,456.50"
    assert P.cad(12345678) == "$12,345,678.00"
    assert P.cad(-999) == "-$999.00"
    assert P.cad(None) == "$0.00"
    assert P.cad(1000, 0) == "$1,000"


def test_unicode_pdf_prints_accented_names():
    pdf = P.UnicodePDF(format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)                 # core name → redirected to Noto Sans
    pdf.cell(0, 8, f"Net pay {P.cad(54321)} — Hélène Bélanger-Côté, Œuvre José", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, "Gross ≥ $65,700 → CPP2 applies")  # glyphs Noto lacks are mapped, not crashed
    text = pdf_text_of(bytes(pdf.output()))
    assert "$54,321.00" in text
    assert "Hélène Bélanger-Côté" in text and "Œuvre José" in text
    assert ">=" in text and "->" in text


def test_esign_stamp_and_certificate_keep_signer_name():
    from datetime import datetime, timezone
    from shared.utils.providers import esign
    base = P.UnicodePDF()
    base.add_page()
    base.set_font("Helvetica", "", 10)
    base.cell(0, 8, "Offer letter")
    lines = esign.signature_lines(signer_name="Hélène Côté", typed_name="Hélène Côté", verified_via="code",
                                  ip="1.2.3.4", signed_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
                                  doc_sha256="ab" * 32, envelope_id="ENV1")
    out = esign.stamp_and_certify(bytes(base.output()), block_lines=lines, certificate={
        "title": "Offer – $82,000", "signer_name": "Hélène Côté", "signed_at": "2026-10-15", "envelope_id": "ENV1"})
    text = pdf_text_of(out)
    assert text.count("Hélène Côté") >= 2 and "$82,000" in text


def test_latin1_fallback_when_font_missing(monkeypatch):
    monkeypatch.setattr(P, "font_available", lambda: False)
    pdf = P.UnicodePDF()
    assert not pdf.unicode_ok
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, "Net $100 – Œuvre")                   # still produces a PDF
    assert "Net $100 - OEuvre" in pdf_text_of(bytes(pdf.output()))
    assert P.pdf_text("$5 – x", unicode_ok=False) == "$5 - x"
