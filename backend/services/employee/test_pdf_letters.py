"""Letters print $ amounts and accented French-Canadian names in the PDF text layer."""
import io

from pypdf import PdfReader

import letters_engine as E
from letters_defaults import DEFAULT_TEMPLATES


def _text(data: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def test_letter_pdf_has_dollars_and_accented_names():
    values = {"employee.full_name": "Amélie Côté-Bérubé", "salary.effective_from": "1 April 2026",
              "salary.old_annual": E.fmt_money(80000, "CAD"), "salary.new_annual": E.fmt_money(88000, "CAD"),
              "salary.increase_amount": E.fmt_money(8000, "CAD")}
    blocks, _ = E.build_blocks(DEFAULT_TEMPLATES[5]["body"], values,
                               {"salary.breakup_table": [["Component", "Per month", "Per year"],
                                                         ["Base salary", E.fmt_money(7333.33, "CAD"), E.fmt_money(88000, "CAD")]]})
    pdf = E.render_pdf(blocks, letterhead={"company_name": "Maple Logistics Inc.", "address": "Toronto, ON",
                                           "signatory_name": "Geneviève Lefèbvre", "signatory_designation": "Head – HR"},
                       number="LTR/2026/0001", date_text="15 October 2026")
    text = _text(pdf)
    assert "$88,000.00" in text and "$7,333.33" in text
    assert "Amélie Côté-Bérubé" in text
    assert "Geneviève Lefèbvre" in text and "Head – HR" in text
