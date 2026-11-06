"""Privacy centre (PIPEDA) pure-logic tests: 30-day access SLA, consent coverage incl. withdrawal,
breach record (24 months), notice / service-provider terms wording, retention defaults.
Run:  cd backend/services/compliance && PYTHONPATH=../../ python -m pytest test_privacy.py -q
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import privacy


def _c(emp, purpose, status, t):
    return SimpleNamespace(employee_id=emp, purpose=purpose, status=status, given_at=t)


def test_sla_due_is_30_days():
    t0 = datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert privacy.SLA_DAYS == 30
    assert privacy.compute_due(t0) == datetime(2026, 11, 2, tzinfo=timezone.utc)


def test_sla_bands():
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert privacy.sla_state(now + timedelta(days=60), "open", now)["band"] == "ok"
    assert privacy.sla_state(now + timedelta(days=20), "open", now)["band"] == "soon"
    s = privacy.sla_state(now + timedelta(days=5), "in_progress", now)
    assert s["band"] == "urgent" and s["days_left"] == 5 and not s["overdue"]
    s = privacy.sla_state(now - timedelta(days=2), "open", now)
    assert s["overdue"] is True and s["band"] == "overdue"
    assert privacy.sla_state(now - timedelta(days=2), "completed", now)["band"] == "closed"
    assert privacy.sla_state(None, "open", now)["band"] == "none"


def test_consent_coverage_and_withdrawal():
    t = datetime(2026, 9, 1, tzinfo=timezone.utc)
    P = list(privacy.PURPOSES)
    assert "government_reporting" in P
    rows = [_c("e1", p, "given", t) for p in P]                  # e1 fully covered
    rows += [_c("e2", p, "given", t) for p in P[:2]]             # e2 partial
    rows += [_c("e3", p, "given", t) for p in P]                 # e3 given then withdraws payroll
    rows += [_c("e3", "payroll_processing", "withdrawn", t + timedelta(days=1))]
    cov = privacy.consent_coverage(["e1", "e2", "e3"], rows)
    assert cov["employees"] == 3 and cov["fully_covered"] == 1
    assert cov["coverage_pct"] == 33.3
    assert cov["per_purpose"]["payroll_processing"] == 2
    assert cov["per_purpose"]["benefits"] == 2
    assert set(cov["missing"]) == {"e2", "e3"}
    rows += [_c("e3", "payroll_processing", "given", t + timedelta(days=2))]
    assert privacy.consent_coverage(["e1", "e3"], rows)["coverage_pct"] == 100.0
    assert privacy.consent_coverage([], rows)["coverage_pct"] == 0.0


def test_breach_record_is_kept_24_months_and_report_flag():
    det = privacy._now() - timedelta(hours=10)
    b = SimpleNamespace(id="x", ref="BR-1", detected_at=det, occurred_at=None,
                        description="d", data_categories=["sin"], individuals_affected=1, severity="high", status="open",
                        commissioner_notified_at=None, individuals_notified_at=None, checklist=[], reported_by=None)
    out = privacy._breach_out(b)
    assert 9.5 < out["hours_since_detection"] < 10.5 and out["reported"] is False
    assert out["keep_record_until"] == (det + timedelta(days=730)).date().isoformat()
    b.commissioner_notified_at = det + timedelta(hours=30)
    assert privacy._breach_out(b)["reported"] is True
    keys = [i["key"] for i in privacy.BREACH_CHECKLIST]
    assert "report_commissioner" in keys and "notify_individuals" in keys and "record" in keys
    assert any("real risk of significant harm" in i["label"] for i in privacy.BREACH_CHECKLIST)


def test_pdf_text_and_export_pdf():
    assert "Côté — $1,000" in privacy.pdf_text("Côté — $1,000")
    assert privacy.pdf_text(None) == ""
    out = privacy._export_pdf({"organization": "Maple Logistics Inc.", "generated_at": "2026-10-03T10:00:00",
                               "law": privacy.LAW, "employee": {"first_name": "Chloé", "sin_masked": "XXX-XXX-286"},
                               "consents": [{"purpose_label": "Payroll", "status": "given", "notice_version": 1, "given_at": "2026-10-01T00:00:00"}],
                               "data_requests": [], "payslips": [], "tax_declaration": None})
    assert out[:4] == b"%PDF" and len(out) > 800
    pdf = privacy._pdf(); pdf.add_page(); pdf.set_font("Helvetica", "", 10)
    for line in privacy.PROCESSOR_CONTRACT.format(org="Acme", version="1.0").splitlines():
        pdf.multi_cell(0, 5.5, privacy.pdf_text(line) or " ", new_x="LMARGIN", new_y="NEXT")
    assert bytes(pdf.output())[:4] == b"%PDF"


def test_default_notice_and_contract_render():
    txt = privacy.DEFAULT_NOTICE.format(org="Acme", dpo="privacy@acme.example")
    assert "Acme" in txt and "30 days" in txt and "privacy@acme.example" in txt
    assert "Privacy Commissioner of Canada" in txt and "Social Insurance Number" in txt
    c = privacy.PROCESSOR_CONTRACT.format(org="Acme", version="1.0")
    assert "PIPEDA" in c and "Principle 4.1.3" in c and "30 days" in c and "24 months" in c
    assert {r[0] for r in privacy.DEFAULT_RETENTION} >= {"payroll_records", "employee_personal_data", "breach_register"}
    assert privacy.DEFAULT_RETENTION[0][1] == 6                   # Income Tax Act s.230(4): six years
    assert dict((r[0], r[1]) for r in privacy.DEFAULT_RETENTION)["breach_register"] == 2


def test_retention_default_needs_confirmation():
    from privacy_models import RetentionPolicy
    p = RetentionPolicy(record_type="payroll_records", retain_years=6, legal_basis="x", action="review", enabled=True)
    assert privacy._policy_out(p)["needs_confirmation"] is True
    p.confirmed_at, p.confirmed_by = datetime.now(timezone.utc), "cpa@maple.example"
    out = privacy._policy_out(p)
    assert out["needs_confirmation"] is False and out["confirmed_by"] == "cpa@maple.example"
