"""Course certificate PDF keeps accented names and typographic marks (bundled Unicode font)."""
import io

from pypdf import PdfReader

from engage_certificate import render_certificate


def test_certificate_prints_unicode_name():
    data = render_certificate(person="Amélie Côté-Bérubé", course="Workplace harassment awareness – $ gifts policy",
                              number="CERT-0001", completed_on="15 Oct 2026", org_name="Maple Logistics", score=90)
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    assert "Amélie Côté-Bérubé" in text
    assert "$" in text and "–" in text
