"""Hiring rules — unit tests. Run: cd backend/services/employee && python -m pytest -q test_hiring.py"""
import io
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import hiring_logic as L  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402

HR = Actor(org="o", user_id="u-hr", role="hr_manager", employee_id="e-hr")
HEAD = Actor(org="o", user_id="u-head", role="hr_admin", employee_id="e-head")
ADMIN = Actor(org="o", user_id="u-adm", role="org_admin", employee_id="e-adm")
MGR = Actor(org="o", user_id="u-mgr", role="manager", employee_id="e-mgr")
OTHER_MGR = Actor(org="o", user_id="u-m2", role="manager", employee_id="e-m2")
EMP = Actor(org="o", user_id="u-emp", role="employee", employee_id="e-emp")
REQ = {"created_by": "u-hr", "hiring_manager_id": "e-mgr", "approval_step": "manager", "status": "pending_approval"}


# ── requisition state machine + who may act ─────────────────────────────────
def test_requisition_transitions():
    assert L.req_next("draft", "submit") == "pending_approval"
    assert L.req_next("pending_approval", "approve") == "open"
    assert L.req_next("pending_approval", "reject") == "draft"
    assert L.req_next("open", "hold") == "on_hold"
    assert L.req_next("on_hold", "resume") == "open"
    assert L.req_next("open", "fill") == "filled"
    for s in ("draft", "pending_approval", "open", "on_hold"):
        assert L.req_next(s, "cancel") == "cancelled"
    for bad in (("open", "approve"), ("draft", "hold"), ("cancelled", "submit"), ("filled", "cancel"), ("draft", "fill")):
        with pytest.raises(RuleError):
            L.req_next(*bad)


def test_requisition_approval_chain_follows_the_setting():
    assert L.req_steps("manager_then_hr") == ["manager", "hr"]
    assert L.req_submit("manager_then_hr", HR, REQ) == ("pending_approval", "manager")
    assert L.req_approve("manager_then_hr", "manager") == ("pending_approval", "hr")
    assert L.req_approve("manager_then_hr", "hr") == ("open", None)
    # the hiring manager submitting their own requisition has already agreed
    assert L.req_submit("manager_then_hr", MGR, REQ) == ("pending_approval", "hr")
    assert L.req_submit("hr_only", HR, REQ) == ("pending_approval", "hr")
    assert L.req_submit("none", HR, REQ) == ("open", None)


def test_requisition_who_may_act():
    assert L.req_can_act("approve", MGR, REQ)                       # the hiring manager, at the manager step
    assert not L.req_can_act("approve", OTHER_MGR, REQ)             # another manager cannot
    assert not L.req_can_act("approve", HR, REQ)                    # hr_manager is not an HR head
    assert L.req_can_act("approve", HEAD, REQ)                      # HR head may act for the manager
    hr_step = {**REQ, "approval_step": "hr"}
    assert not L.req_can_act("approve", MGR, hr_step)               # manager cannot do the HR step
    assert L.req_can_act("approve", ADMIN, hr_step) and L.req_can_act("reject", HEAD, hr_step)
    assert not L.req_can_act("approve", EMP, REQ) and not L.req_can_act("edit", EMP, REQ)
    assert L.req_can_act("hold", HR, REQ) and not L.req_can_act("hold", MGR, REQ)
    assert L.req_can_act("cancel", MGR, REQ) and not L.req_can_act("cancel", MGR, {**REQ, "status": "open"})
    assert not L.req_can_act("fill", ADMIN, REQ)                    # system only


# ── application pipeline ────────────────────────────────────────────────────
def test_application_moves():
    assert L.app_move("applied", "screening")["stage"] == "screening"
    assert L.app_move("interview", "screening")["stage"] == "screening"       # back is allowed
    with pytest.raises(RuleError):
        L.app_move("screening", "screening")
    with pytest.raises(RuleError):                                             # rounds not done
        L.app_move("interview", "selected", rounds_done=0, rounds_required=2)
    assert L.app_move("interview", "selected", rounds_done=2, rounds_required=2)["stage"] == "selected"
    for hand in ("offer", "joined"):                                           # never by hand
        with pytest.raises(RuleError):
            L.app_move("selected", hand)
    assert L.app_move("selected", "offer", system=True)["stage"] == "offer"
    assert L.app_move("offer", "joined", system=True)["stage"] == "joined"
    with pytest.raises(RuleError):                                             # live offer blocks manual moves
        L.app_move("offer", "rejected", reason="x")
    with pytest.raises(RuleError):
        L.app_move("joined", "rejected", reason="x")


def test_rejection_needs_reason_and_hold_returns_where_it_was():
    with pytest.raises(RuleError) as e:
        L.app_move("screening", "rejected")
    assert e.value.status == 422
    r = L.app_move("screening", "rejected", reason="Not enough experience")
    assert r == {"stage": "rejected", "prev_stage": "screening"}
    h = L.app_move("interview", "on_hold")
    assert h == {"stage": "on_hold", "prev_stage": "interview"}
    assert L.app_move("on_hold", "interview", prev="interview") == {"stage": "interview", "prev_stage": None}
    with pytest.raises(RuleError):
        L.app_move("on_hold", "selected", prev="interview")
    assert L.app_move("rejected", "screening", prev="screening")["stage"] == "screening"   # reopen
    with pytest.raises(RuleError):
        L.app_move("rejected", "withdrawn", prev="screening")
    assert L.return_stage("offer") == "selected" and L.return_stage(None) == "applied"


def test_rounds_done_needs_every_panel_members_feedback():
    ivs = [{"id": "i1", "application_id": "a1", "status": "scheduled", "panel": [{"employee_id": "e1"}, {"employee_id": "e2"}]},
           {"id": "i2", "application_id": "a1", "status": "cancelled", "panel": [{"employee_id": "e1"}]},
           {"id": "i3", "application_id": "a1", "status": "scheduled", "panel": [{"employee_id": "e3"}]}]
    fb = [{"interview_id": "i1", "interviewer_employee_id": "e1"}, {"interview_id": "i2", "interviewer_employee_id": "e1"}]
    assert L.rounds_done(ivs, fb) == 0
    fb.append({"interview_id": "i1", "interviewer_employee_id": "e2"})
    assert L.rounds_done(ivs, fb) == 1
    fb.append({"interview_id": "i3", "interviewer_employee_id": "e3"})
    assert L.rounds_done(ivs, fb) == 2


def test_interviewer_sees_only_assigned_candidates():
    ivs = [{"id": "i1", "application_id": "a1", "status": "scheduled", "panel": [{"employee_id": "e1"}]},
           {"id": "i2", "application_id": "a2", "status": "scheduled", "panel": [{"employee_id": "e2"}]},
           {"id": "i3", "application_id": "a3", "status": "cancelled", "panel": [{"employee_id": "e1"}]}]
    assert L.interviewer_application_ids(ivs, "e1") == {"a1"}       # not a2 (other panel), not a3 (cancelled)
    assert L.interviewer_application_ids(ivs, "e9") == set()
    assert L.interviewer_application_ids(ivs, "") == set()
    assert not L.is_panel_member(ivs[1], "e1")


def test_feedback_form():
    ok = L.clean_feedback({"rating": "4", "recommendation": "hire", "strengths": " Clear ", "concerns": ""})
    assert ok == {"rating": 4, "recommendation": "hire", "strengths": "Clear", "concerns": None}
    for bad in ({"rating": 6, "recommendation": "hire"}, {"rating": 3, "recommendation": "maybe"}, {"recommendation": "hold"}):
        with pytest.raises(RuleError):
            L.clean_feedback(bad)


# ── duplicate candidates ────────────────────────────────────────────────────
def test_duplicate_detection_reports_and_never_merges():
    assert L.norm_email("  Asha@Example.COM ") == "asha@example.com"
    assert L.norm_phone("+91 98765-43210") == L.norm_phone("09876543210") == "9876543210"
    assert L.norm_phone("12") is None
    others = [{"id": "c1", "email_norm": "asha@example.com", "phone_norm": "9876543210", "name": "A"},
              {"id": "c2", "email_norm": "other@example.com", "phone_norm": "9876543210", "name": "B"},
              {"id": "c3", "email_norm": "x@example.com", "phone_norm": "1112223334", "name": "C"}]
    d = L.find_duplicates("ASHA@example.com", "98765 43210", others)
    assert [(x["id"], x["match"]) for x in d] == [("c1", ["email", "phone"]), ("c2", ["phone"])]
    assert L.find_duplicates("new@example.com", "5550001111", others) == []
    assert L.find_duplicates(None, None, others) == []
    assert [x["id"] for x in L.find_duplicates("asha@example.com", None, others, exclude_id="c1")] == []
    assert len(others) == 3                                          # the list is untouched: report only


# ── offer state machine, approvals, token ───────────────────────────────────
def test_offer_transitions():
    assert L.offer_next("draft", "approve") == "approved"
    assert L.offer_next("approved", "send") == "sent"
    assert L.offer_next("sent", "accept") == "accepted" and L.offer_next("sent", "decline") == "declined"
    assert L.offer_next("sent", "expire") == "expired"
    for s in ("draft", "approved", "sent"):
        assert L.offer_next(s, "withdraw") == "withdrawn"
    for bad in (("draft", "send"), ("approved", "accept"), ("accepted", "decline"), ("declined", "accept"),
                ("expired", "accept"), ("withdrawn", "send"), ("accepted", "withdraw")):
        with pytest.raises(RuleError):
            L.offer_next(*bad)


def test_offer_approval_and_second_approver_above_budget():
    s = L.merge_settings(None)
    assert not L.offer_above_budget(900000, 1000000) and L.offer_above_budget(1000001, 1000000)
    assert not L.offer_above_budget(5000000, None)                   # no budget typed = nothing to exceed
    assert L.offer_approvals_required(False, s) == 1 and L.offer_approvals_required(True, s) == 2
    assert L.offer_approvals_required(True, {**s, "offer_second_approver_above_budget": False}) == 1
    with pytest.raises(RuleError) as e:                              # hr_manager / manager / employee cannot approve
        L.offer_approve(HR, [], 1)
    assert e.value.status == 403
    assert not L.offer_can_act("approve", MGR) and not L.offer_can_act("send", EMP) and L.offer_can_act("send", HR)
    a1, st = L.offer_approve(HEAD, [], 2)
    assert st == "draft" and len(a1) == 1                            # one of two: still a draft
    with pytest.raises(RuleError):                                   # the same person cannot be the second approver
        L.offer_approve(HEAD, a1, 2)
    a2, st = L.offer_approve(ADMIN, a1, 2)
    assert st == "approved" and [x["by"] for x in a2] == ["u-head", "u-adm"]
    assert L.offer_approve(HEAD, [], 1)[1] == "approved"


def test_offer_token_single_use_and_expiry():
    raw, hashed = L.new_token()
    assert len(raw) >= 40 and hashed == L.hash_token(raw) and raw not in hashed and len(hashed) == 64
    assert L.new_token()[0] != raw
    now = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    exp = L.token_expiry(now, 7)
    assert exp == now + timedelta(days=7)
    assert L.token_state("sent", exp, None, now) == "ok"
    assert L.token_state("sent", exp, None, exp + timedelta(seconds=1)) == "expired"
    assert L.token_state("sent", exp, now, now) == "used"            # used once = used
    assert L.token_state("accepted", exp, now, now) == "used" and L.token_state("declined", exp, now, now) == "used"
    assert L.token_state("expired", exp, None, now) == "expired"
    assert L.token_state("withdrawn", exp, None, now) == "closed" and L.token_state("draft", None, None, now) == "closed"
    assert L.hash_token("guess") != hashed


# ── uploads ─────────────────────────────────────────────────────────────────
def _docx(extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        for n in (extra or []):
            z.writestr(n, "x")
    return buf.getvalue()


def test_upload_sniffing_by_magic_bytes():
    assert L.sniff_upload(b"%PDF-1.7 ...")[0] == "pdf"
    assert L.sniff_upload(b"\x89PNG\r\n\x1a\n....")[0] == "png"
    assert L.sniff_upload(b"\xff\xd8\xff\xe0....")[0] == "jpg"
    assert L.sniff_upload(_docx())[0] == "docx"
    assert L.sniff_upload(_docx(["word/vbaProject.bin"])) is None    # macro document
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("evil.exe", "MZ")
    assert L.sniff_upload(buf.getvalue()) is None                    # a zip that is not a DOCX
    assert L.sniff_upload(b"MZ\x90\x00 this is an exe") is None
    assert L.sniff_upload(b"<html><script>") is None and L.sniff_upload(b"") is None
    with pytest.raises(RuleError) as e:                              # the NAME says pdf, the bytes do not
        L.check_upload(b"MZ renamed.pdf", 5)
    assert e.value.status == 415
    with pytest.raises(RuleError) as e:
        L.check_upload(b"%PDF-" + b"0" * (1024 * 1024 + 1), 1)
    assert e.value.status == 413
    assert L.check_upload(b"%PDF-1.4", 1) == ("pdf", "application/pdf")
    assert L.safe_file_name("../../etc/passwd.exe", "pdf") == "passwd.pdf"
    assert L.safe_file_name(None, "docx") == "file.docx"


def test_rate_limiter():
    rl = L.RateLimiter(3, 60)
    assert [rl.allow("ip", t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert rl.allow("other", 3)                                      # another caller is not affected
    assert rl.allow("ip", 61)                                        # the window slid


def test_settings_defaults_and_validation():
    s = L.merge_settings({"required_interview_rounds": 2, "junk": 1})
    assert s["required_interview_rounds"] == 2 and "junk" not in s and s["careers_page_enabled"] is False
    assert L.clean_settings({"requisition_approval": "hr_only", "zzz": 1}) == {"requisition_approval": "hr_only"}
    for bad in ({"requisition_approval": "anyone"}, {"offer_valid_days": 0}, {"resume_max_mb": 99}, {"privacy_text": " "}):
        with pytest.raises(RuleError):
            L.clean_settings(bad)


# ── reports ─────────────────────────────────────────────────────────────────
def test_reports_from_rows():
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    reqs = [{"id": "r1", "req_number": "REQ-1", "designation": "Analyst", "status": "filled", "openings": 2,
             "opened_at": t0, "filled_at": t0 + timedelta(days=30)},
            {"id": "r2", "req_number": "REQ-2", "designation": "Nurse", "status": "open", "openings": 3, "opened_at": t0}]
    apps = [{"id": "a1", "requisition_id": "r1", "stage": "joined", "source_type": "referral"},
            {"id": "a2", "requisition_id": "r1", "stage": "joined", "source_type": "careers_page"},
            {"id": "a3", "requisition_id": "r2", "stage": "rejected", "source_type": "careers_page"},
            {"id": "a4", "requisition_id": "r2", "stage": "offer", "source_type": "agency"}]
    offers = [{"requisition_id": "r1", "status": "accepted"}, {"requisition_id": "r1", "status": "accepted"},
              {"requisition_id": "r2", "status": "declined"}, {"requisition_id": "r2", "status": "sent"},
              {"requisition_id": "r2", "status": "expired"}]
    hist = [{"application_id": "a3", "to_stage": "applied", "at": t0},
            {"application_id": "a3", "to_stage": "screening", "at": t0 + timedelta(days=4)},
            {"application_id": "a3", "to_stage": "rejected", "at": t0 + timedelta(days=10)}]
    rep = L.report(reqs, apps, offers, hist, now=t0 + timedelta(days=40))
    r1 = rep["openings"][0]
    assert (r1["openings"], r1["accepted"], r1["joined"], r1["remaining"]) == (2, 2, 2, 0)
    assert rep["openings"][1]["remaining"] == 3 and rep["openings"][1]["pipeline"]["offer"] == 1
    assert rep["time_to_fill"] == {"avg_days": 30.0, "filled_requisitions": 1}
    stages = {s["stage"]: s["avg_days"] for s in rep["time_in_stage"]}
    assert stages["applied"] == 4.0 and stages["screening"] == 6.0
    mix = {m["source"]: m for m in rep["source_mix"]}
    assert mix["careers_page"]["applications"] == 2 and mix["careers_page"]["share_pct"] == 50.0 and mix["referral"]["joined"] == 1
    assert rep["offer_acceptance"]["rate_pct"] == 50.0 and rep["offer_acceptance"]["pending"] == 1   # 2 of 4 decided
    assert L.filled_counts(apps, offers)["r1"] == {"accepted": 2, "joined": 2}
    assert L.report([], [], [], [])["offer_acceptance"]["rate_pct"] is None


# ── wiring: the modules import, the routes exist, the public prefix is public ──
def test_routes_and_public_prefix():
    import hiring_offers
    import hiring_public
    import hiring_routers
    paths = {(m, r.path) for rt in (hiring_routers.router, hiring_offers.router, hiring_public.router)
             for r in rt.routes for m in r.methods}
    for want in [("POST", "/hiring/requisitions/{req_id}/{action}"), ("POST", "/hiring/applications/move"),
                 ("GET", "/hiring/interviews/mine"), ("POST", "/hiring/offers/{offer_id}/send"),
                 ("POST", "/hiring/public/careers/{org_code}/{posting_id}/apply"),
                 ("POST", "/hiring/public/offer/{token}/accept"), ("GET", "/hiring/public/offer/{token}")]:
        assert want in paths, want
    public = [p for _, p in paths if p.startswith("/hiring/public/")]
    assert len({p for p in public}) == 6
    assert all(p.startswith("/hiring/public/") for _, p in paths if p in {r.path for r in hiring_public.router.routes})
    # every signed-in hiring route stays OUTSIDE the public prefix
    assert not any(p.startswith("/hiring/public") for r in (hiring_routers.router, hiring_offers.router) for p in [x.path for x in r.routes])


def test_gateway_public_matcher_only_opens_the_public_prefix():
    import shared.middleware.tenant as tenant
    saved = tenant.PUBLIC
    try:
        tenant.PUBLIC = tuple(saved) + ("/hiring/public/",)          # what api-gateway/main.py adds
        assert tenant._is_public("/hiring/public/careers/ACME")
        assert tenant._is_public("/hiring/public/offer/abc/accept")
        for private in ("/hiring/candidates", "/hiring/offers", "/hiring/publicX", "/hiring/documents/1", "/performance/reviews"):
            assert not tenant._is_public(private), private
    finally:
        tenant.PUBLIC = saved


def test_onboarding_payload_is_prefilled_from_the_offer():
    from datetime import date
    from types import SimpleNamespace as NS
    import hiring_offers
    o = NS(id="of1", application_id="ap1", requisition_id="rq1", designation="Staff Nurse", department="Nursing",
           location="Toronto", employment_type="full_time", manager_id="m1", manager_name="R Menon", grade="G4",
           joining_date=date(2026, 11, 3), annual_ctc=78000, breakup={"lines": [{"code": "BASE", "annual": 78000}]})
    c = NS(first_name="Asha", last_name=None, email="asha@example.com", phone="4165550199", city="Toronto")
    payload, extra = hiring_offers.onboarding_payload(o, c, "CA")
    assert payload["first_name"] == "Asha" and payload["last_name"] == "" and payload["job_title"] == "Staff Nurse"
    assert payload["hire_date"] == "2026-11-03" and payload["annual_salary"] == 78000.0 and payload["address_country"] == "CA"
    assert payload["department"] == "Nursing" and payload["work_location"] == "Toronto" and payload["manager_name"] == "R Menon"
    assert extra["grade"] == "G4" and extra["manager_id"] == "m1" and extra["hiring_offer_id"] == "of1"
    assert extra["offered_ctc_breakup"] == [{"code": "BASE", "annual": 78000}] and extra["address_country"] == "CA"
    assert hiring_offers.letter_data(NS(designation="Staff Nurse", location="Toronto", manager_name="R Menon",
                                        joining_date=date(2026, 11, 3), token_expires_at=None, annual_ctc=78000)) == {
        "offer": {"position": "Staff Nurse", "location": "Toronto", "reporting_to": "R Menon",
                  "joining_date": "2026-11-03", "annual_ctc": 78000.0}}

def test_forms_a_document_kinds_and_hr_recorded_consent():
    """Forms A (2026-10-24): HR tags candidate documents; HR can record the person's consent once."""
    assert L.doc_kind("id_proof") == "id_proof" and L.doc_kind(" Education ") == "education"
    assert L.doc_kind("resume") == "resume" and L.doc_kind("bogus") == "document" and L.doc_kind(None) == "document"
    assert all(len(k) <= 20 for k in L.DOC_KINDS)            # hire_documents.kind is String(20)
    import hiring_routers as R
    from hiring_models import HireCandidate
    c = HireCandidate(first_name="", tags=[])
    R._fill_candidate(c, {"first_name": "Asha", "phone": "9876543210"})
    assert c.consent_at is None and c.consent_text is None
    R._fill_candidate(c, {"consent_given": True})
    first = c.consent_at
    assert first is not None and c.consent_text == R.CONSENT_BY_HR
    R._fill_candidate(c, {"consent_given": True, "consent_text": "other"})
    assert c.consent_at == first and c.consent_text == R.CONSENT_BY_HR   # recorded once, never overwritten
    R._fill_candidate(c, {"consent_given": False})
    assert c.consent_at == first

