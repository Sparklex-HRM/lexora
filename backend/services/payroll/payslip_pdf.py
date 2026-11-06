"""Payslip → PDF (pure-Python fpdf2, no system deps → Docker-safe).

Renders the same figures the on-screen payslip shows, from the PayStub.detail
dict, into a one-page A4 PDF the frontend embeds in its in-app viewer.
"""
from shared.utils.pdf_fonts import UnicodePDF, pdf_text  # accented names via bundled Noto Sans (stream S1)

BRAND = (79, 70, 229)    # Lexora primary-500 #4f46e5 (indigo)
DARK = (31, 41, 55)
GREY = (107, 114, 128)
RULE = (226, 232, 240)

FREQ = {
    "weekly": "Weekly", "biweekly": "Bi-weekly", "bi-weekly": "Bi-weekly",
    "semimonthly": "Semi-monthly", "semi-monthly": "Semi-monthly", "monthly": "Monthly",
}


def _money(v) -> str:
    try:
        return f"${float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def render_payslip_pdf(detail: dict, org_name: str) -> bytes:
    emp = detail.get("employee") or {}
    per = detail.get("period") or {}
    ded = detail.get("deductions") or {}
    ec = detail.get("employer_cost") or {}
    ytd = detail.get("ytd") or {}
    earn_extra = [e for e in (detail.get("entries") or {}).get("earnings", [])
                  if (e.get("amount") or 0) > 0]
    gross = float(detail.get("gross") or 0)
    base = gross - sum(float(e.get("amount") or 0) for e in earn_extra)
    net = float(detail.get("net_pay") or 0)
    total_ded = sum(float(ded.get(k) or 0) for k in
                    ("cpp", "cpp2", "ei", "qpip", "income_tax", "pre_tax", "after_tax"))
    qc = (emp.get("province") or "").upper() == "QC"
    pen, pen2 = ("QPP", "QPP2") if qc else ("CPP", "CPP2")

    pdf = UnicodePDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_margins(14, 12, 14)
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    def row(label, value, bold=False, muted=False, indent=0):
        pdf.set_font("Helvetica", "B" if bold else "", 9)
        pdf.set_text_color(*(GREY if muted else DARK))
        pdf.cell(W - 45, 6, ("   " * indent) + str(label), border=0)
        pdf.cell(45, 6, str(value), border=0, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*DARK)

    def rule():
        y = pdf.get_y() + 1
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(2)

    def section(title):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*GREY)
        pdf.cell(0, 5, title.upper(), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*DARK)

    # ── Header band ──────────────────────────────────────────────────────────
    pdf.set_fill_color(*BRAND)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 12, "  PAYSLIP", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*DARK)
    pdf.ln(4)

    # ── Employer / employee ──────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(W / 2, 6, org_name or "Lexora HRM", border=0)
    pdf.cell(W / 2, 6, emp.get("name") or "", border=0, align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GREY)
    sub = emp.get("employee_number") or ""
    if emp.get("job_title"):
        sub += (" - " if sub else "") + str(emp.get("job_title"))
    pdf.cell(W / 2, 5, f"{emp.get('province') or ''} - Canada", border=0)
    pdf.cell(W / 2, 5, sub, border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*DARK)
    pdf.ln(3)

    # ── Period ───────────────────────────────────────────────────────────────
    row("Pay period", f"{per.get('start') or '-'}  to  {per.get('end') or '-'}")
    row("Pay date", per.get("pay_date") or "-")
    _pb = detail.get("payable") or {}          # date rules (Stream C): payable days + basis + why
    if _pb.get("payable_days") is not None and _pb.get("reason_code") != "full":
        row("Paid days", f"{float(_pb['payable_days']):g} of {float(_pb.get('days_in_period') or 0):g} "
                         f"({_pb.get('basis_label') or 'calendar days'})")
        row("Reason", pdf_text(_pb.get("reason") or ""), muted=True)
    row("Frequency", FREQ.get((emp.get("pay_frequency") or "").lower(),
                              emp.get("pay_frequency") or "-"))
    rule()

    # ── Earnings ─────────────────────────────────────────────────────────────
    section("Earnings")
    row(f"Base pay ({emp.get('pay_type') or 'salary'})", _money(base))
    for e in earn_extra:
        row(e.get("code") or "Earning", _money(e.get("amount")))
    row("Gross", _money(gross), bold=True)

    # ── Deductions ───────────────────────────────────────────────────────────
    section("Deductions")
    row(pen, "- " + _money(ded.get("cpp")))
    row(pen2, "- " + _money(ded.get("cpp2")), muted=(ded.get("cpp2") or 0) == 0)
    row("EI", "- " + _money(ded.get("ei")))
    if qc or (ded.get("qpip") or 0) > 0:
        row("QPIP", "- " + _money(ded.get("qpip")))
    row("Income tax (federal + Québec)" if qc else "Income tax (federal + provincial)", "- " + _money(ded.get("income_tax")))
    if (ded.get("pre_tax") or 0) > 0:
        row("Pre-tax (RRSP etc.)", "- " + _money(ded.get("pre_tax")))
    if (ded.get("after_tax") or 0) > 0:
        row("Other deductions", "- " + _money(ded.get("after_tax")))
    row("Total deductions", "- " + _money(total_ded), bold=True)

    # ── Net pay band ─────────────────────────────────────────────────────────
    pdf.ln(3)
    pdf.set_fill_color(*BRAND)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(W - 45, 11, "  Net pay", fill=True, border=0)
    pdf.cell(45, 11, _money(net) + "  ", fill=True, border=0, align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*DARK)

    # ── Employer contributions + YTD ─────────────────────────────────────────
    section("Employer contributions")
    row(pen, _money(ec.get("cpp")))
    row(pen2, _money(ec.get("cpp2")), muted=(ec.get("cpp2") or 0) == 0)
    row("EI (1.4x)", _money(ec.get("ei")))
    if qc or (ec.get("qpip") or 0) > 0:
        row("QPIP", _money(ec.get("qpip")))

    section("Year-to-date")
    row(pen, _money(ytd.get("cpp")))
    row(pen2, _money(ytd.get("cpp2")))
    row("EI", _money(ytd.get("ei")))
    row("Pensionable", _money(ytd.get("pensionable")))
    row("Insurable", _money(ytd.get("insurable")))
    _vac = detail.get("vacation") or {}
    if _vac.get("percent"):
        section("Vacation pay")
        row(f"Accrued this pay ({float(_vac.get('percent') or 0):g}%)", _money(_vac.get("accrued")))
        row("Paid out this pay", _money(_vac.get("paid")))

    # ── Footer note ──────────────────────────────────────────────────────────
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(0, 4,
        ("Québec: QPP, QPIP and Québec income tax per Revenu Québec (TP-1015.F); federal tax per CRA T4127 "
         "with the 16.5% abatement. " if qc else "Figures per the CRA T4127 payroll formula. ")
        + "Per-period income tax = annual tax / pay periods. Statutory constants carry a verification "
          "status in Payroll > Year-end > Compliance; a TEST-mode stub is not for remittance.")

    out = pdf.output()
    return bytes(out)
