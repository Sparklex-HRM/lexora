"""Hiring D2 (recruiting depth) — unit tests: job-board feeds, slots + pick link, offer
approval chain, scorecards + bias guard, referral bonus, candidate messages, captcha hook,
signed OAuth state, the calendar adapters against MOCKED HTTP, and route wiring.

Run: cd backend/services/employee && PYTHONPATH=../.. python -m pytest -q test_hiring_d2.py
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import hiring_d2_logic as D  # noqa: E402
import hiring_logic as L  # noqa: E402
from hiring_logic import RuleError  # noqa: E402
from shared.utils.providers import resilience  # noqa: E402
from shared.utils.providers.calendar_base import Event, calendar_adapter, expires_in_to_at, token_is_fresh  # noqa: E402
from shared.utils.providers import calendar_google as G, calendar_microsoft as M  # noqa: E402

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
JOB = {"id": "p1", "title": "Staff Nurse", "description": "Care for patients.\n\nNight shifts.", "requirements": "RN licence (CNO)",
       "reference": "REQ-2026-0007", "department": "Nursing", "location": "Hamilton, ON", "city": "Hamilton",
       "state": "ON", "country": "CA", "employment_type": "full_time", "published_at": NOW,
       "url": "https://app.example/careers/ACME/p1", "salary_min": 78000, "salary_max": 96000}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── job boards ──────────────────────────────────────────────────────────────
def test_indeed_feed_follows_the_documented_elements():
    xml = D.indeed_xml("Acme Hospital", "https://app.example/careers/ACME", [JOB], now=NOW)
    assert xml.startswith('<?xml version="1.0" encoding="utf-8"?>\n<source>')
    for tag in ("publisher", "publisherurl", "lastBuildDate", "title", "date", "referencenumber", "url", "company",
                "city", "state", "country", "description", "jobtype", "salary"):
        assert f"<{tag}>" in xml, tag
    assert "<![CDATA[Staff Nurse]]>" in xml and "<![CDATA[fulltime]]>" in xml and "<![CDATA[REQ-2026-0007]]>" in xml
    assert "Fri, 16 Oct 2026 09:00:00 +0000" in xml and "<salary><![CDATA[CAD 78000 - 96000 per year]]>" in xml
    assert D.indeed_xml("A", "u", [], now=NOW).count("<job>") == 0


def test_indeed_cdata_cannot_be_broken_out_of():
    xml = D.indeed_xml("A", "u", [{**JOB, "description": "x ]]> <script>"}], now=NOW)
    assert "]]]]><![CDATA[>" in xml and "<script>" not in xml.replace("<![CDATA[", "").split("]]>")[0]


def test_google_jobs_jsonld_uses_the_documented_enum_and_shape():
    j = D.google_jobs_jsonld(JOB, {"name": "Acme Hospital", "website": "https://acme.example", "logo_url": "https://api/logo"},
                             JOB["url"], valid_days=30)
    assert j["@type"] == "JobPosting" and j["employmentType"] == "FULL_TIME"
    assert j["datePosted"] == "2026-10-16" and j["validThrough"] == "2026-11-15"
    assert j["hiringOrganization"] == {"@type": "Organization", "name": "Acme Hospital", "sameAs": "https://acme.example", "logo": "https://api/logo"}
    assert j["jobLocation"]["address"] == {"@type": "PostalAddress", "addressCountry": "CA", "addressLocality": "Hamilton", "addressRegion": "ON"}
    assert j["identifier"]["value"] == "REQ-2026-0007" and j["baseSalary"]["value"]["minValue"] == 78000
    assert j["description"] == "<p>Care for patients.</p><p>Night shifts.</p>"
    assert D.google_jobs_jsonld({**JOB, "employment_type": "intern", "salary_min": None}, {"name": "A"}, "u")["employmentType"] == "INTERN"
    assert "baseSalary" not in D.google_jobs_jsonld({**JOB, "salary_min": None}, {"name": "A"}, "u")
    json.dumps(j)                                                      # serialisable as-is


def test_public_job_never_carries_the_internal_requisition_budget():
    """The requisition budget drives the above-budget offer approval; it is not a published salary."""
    from types import SimpleNamespace
    import hiring_boards
    p = SimpleNamespace(id="p1", title="Staff Nurse", description="Care", requirements=None, published_at=NOW)
    r = SimpleNamespace(req_number="REQ-2026-0007", department="Nursing", location="Hamilton, ON",
                        employment_type="full_time", budget_min=78000, budget_max=96000)
    j = hiring_boards.job_dict(p, r, "ACME", src="indeed")
    assert j["salary_min"] is None and j["salary_max"] is None
    assert "baseSalary" not in D.google_jobs_jsonld(j, {"name": "Acme"}, j["url"])
    assert "<salary>" not in D.indeed_xml("Acme", "https://c", [j]) and "96000" not in D.indeed_xml("Acme", "https://c", [j])


def test_partner_only_boards_are_export_only_and_honest():
    assert D.PARTNER_ONLY == ("linkedin",)
    st = D.board_state("linkedin", True, True)              # G6: no LinkedIn company id / poster e-mail yet
    assert st["state"] == "needs_setup" and "partner" in st["message"].lower() and st["docs"].startswith("https://")
    assert D.board_state("indeed", False, True)["state"] == "off"
    assert D.board_state("indeed", True, False)["state"] == "hidden"
    assert D.board_state("google_jobs", True, True)["state"] == "live"
    txt = D.export_text("linkedin", JOB, "Acme Hospital", JOB["url"] + "?src=linkedin")
    assert txt.startswith("Staff Nurse\nAcme Hospital") and "Apply here: " in txt and "?src=linkedin" in txt
    csv_ = D.export_csv([JOB], "Acme Hospital")
    assert csv_.splitlines()[0].startswith("title,company,reference") and "Staff Nurse,Acme Hospital,REQ-2026-0007" in csv_
    assert D.split_location("Hamilton, ON") == ("Hamilton", "ON") and D.split_location("") == (None, None)


# ── interview slots + candidate pick ────────────────────────────────────────
def test_slots_are_cleaned_ordered_and_checked():
    raw = [(NOW + timedelta(days=2, hours=2)).isoformat(), {"start": (NOW + timedelta(days=1)).isoformat()}]
    out = D.clean_slots(raw, 45, now=NOW)
    assert [s["start"] for s in out] == [(NOW + timedelta(days=1)).isoformat(), (NOW + timedelta(days=2, hours=2)).isoformat()]
    assert out[0]["end"] == (NOW + timedelta(days=1, minutes=45)).isoformat()
    for bad in ([], [(NOW - timedelta(hours=1)).isoformat()], [{"start": NOW.isoformat(), "end": NOW.isoformat()}], ["garbage"]):
        with pytest.raises(RuleError):
            D.clean_slots(bad, 45, now=NOW)
    with pytest.raises(RuleError):                                     # overlap
        D.clean_slots([(NOW + timedelta(days=1)).isoformat(), (NOW + timedelta(days=1, minutes=20)).isoformat()], 45, now=NOW)
    with pytest.raises(RuleError):
        D.clean_slots([(NOW + timedelta(days=1)).isoformat()], 5, now=NOW)
    with pytest.raises(RuleError):
        D.clean_slots([(NOW + timedelta(days=i + 1)).isoformat() for i in range(11)], 45, now=NOW)


def test_pick_link_is_single_use_and_expires():
    exp = NOW + timedelta(days=5)
    assert D.slot_link_state("proposed", exp, NOW) == "ok"
    assert D.slot_link_state("proposed", exp, exp + timedelta(seconds=1)) == "expired"
    assert D.slot_link_state("picked", exp, NOW) == "picked"
    assert D.slot_link_state("cancelled", exp, NOW) == "closed"
    slots = D.clean_slots([(NOW + timedelta(days=1)).isoformat(), (NOW + timedelta(days=2)).isoformat()], 30, now=NOW)
    assert D.pick_slot(slots, "1", now=NOW) == slots[1]
    for bad in ("x", 2, -1):
        with pytest.raises(RuleError):
            D.pick_slot(slots, bad, now=NOW)
    with pytest.raises(RuleError) as e:                                # the slot passed meanwhile
        D.pick_slot(slots, 0, now=NOW + timedelta(days=1, minutes=1))
    assert e.value.status == 409


# ── offer approval chain ────────────────────────────────────────────────────
def test_default_chain_is_manager_then_hr_admin_and_skips_a_missing_manager():
    c = D.default_offer_chain("e-mgr", "R Menon")
    assert [s["kind"] for s in c] == ["manager", "role"] and c[0]["employee_id"] == "e-mgr" and c[1]["role"] == "hr_admin"
    assert "R Menon" in c[0]["label"]
    assert D.default_offer_chain(None) == [{"kind": "role", "role": "hr_admin", "label": "HR admin"}]


def test_registry_chain_is_validated_or_refused():
    ok = D.clean_chain([{"kind": "role", "role": "finance", "label": "Finance"}, {"approver_kind": "user", "approver_user_id": "u9"},
                        {"kind": "manager", "employee_id": "e1"}])
    assert [s["kind"] for s in ok] == ["role", "user", "manager"] and ok[1]["user_id"] == "u9"
    for bad in ([], [{"kind": "role"}], [{"kind": "alien", "x": 1}], ["str"]):
        with pytest.raises(RuleError):
            D.clean_chain(bad)


def test_who_may_decide_a_chain_step():
    mgr = {"kind": "manager", "employee_id": "e-mgr"}
    role = {"kind": "role", "role": "hr_admin"}
    user = {"kind": "user", "user_id": "u9"}
    assert D.step_may_decide(mgr, "manager", "u-m", "e-mgr") and not D.step_may_decide(mgr, "manager", "u-x", "e-other")
    assert D.step_may_decide(role, "hr_admin", "u", "e") and not D.step_may_decide(role, "hr_manager", "u", "e")
    assert D.step_may_decide({"kind": "role", "role": "hr"}, "hr_manager", "u", "e")     # role family
    assert D.step_may_decide(user, "employee", "u9", "") and not D.step_may_decide(user, "employee", "u8", "")
    assert D.step_may_decide(mgr, "org_admin", "u", "")                                   # admin never stuck


def test_chain_walks_forward_on_approve_and_stops_on_reject():
    steps = [{"status": "pending"}, {"status": "waiting"}, {"status": "waiting"}]
    s1, st = D.chain_after_decision(steps, "approve")
    assert st == "pending" and [s["status"] for s in s1] == ["approved", "pending", "waiting"]
    s2, st = D.chain_after_decision(s1, "approve")
    assert st == "pending" and s2[2]["status"] == "pending"
    s3, st = D.chain_after_decision(s2, "approve")
    assert st == "approved" and all(s["status"] == "approved" for s in s3)
    r, st = D.chain_after_decision(s1, "reject")
    assert st == "draft" and [s["status"] for s in r] == ["approved", "rejected", "skipped"]
    with pytest.raises(RuleError):
        D.chain_after_decision(s3, "approve")


# ── scorecards ──────────────────────────────────────────────────────────────
def test_competencies_are_validated():
    comps = D.clean_competencies([{"label": "Role skills", "weight": 2}, {"label": "Ownership!", "description": "x"}])
    assert comps[0]["key"] == "role_skills" and comps[1]["key"] == "ownership" and comps[1]["weight"] == 1.0
    for bad in ([], [{"label": ""}], [{"label": "A", "weight": 0}], [{"label": "A"}, {"label": "a"}], [{"label": "A", "weight": "x"}]):
        with pytest.raises(RuleError):
            D.clean_competencies(bad)
    assert len(D.DEFAULT_COMPETENCIES) == 5


def test_scores_cover_every_competency_and_weight_the_total():
    comps = D.clean_competencies([{"label": "Skills", "weight": 3}, {"label": "Communication", "weight": 1}])
    sc = D.clean_scores([{"key": "skills", "rating": 5, "note": " strong "}, {"key": "communication", "rating": "1"}], comps)
    assert sc[0]["note"] == "strong" and sc[1]["rating"] == 1
    assert D.weighted_score(sc) == 4.0                                  # (3*5 + 1*1) / 4
    with pytest.raises(RuleError):
        D.clean_scores([{"key": "skills", "rating": 5}], comps)         # communication missing
    with pytest.raises(RuleError):
        D.clean_scores([{"key": "skills", "rating": 6}, {"key": "communication", "rating": 1}], comps)


def test_aggregated_candidate_view():
    fb = [{"id": "f1", "interview_id": "i1", "interviewer_name": "A", "rating": 4, "recommendation": "hire"},
          {"id": "f2", "interview_id": "i1", "interviewer_name": "B", "rating": 2, "recommendation": "no_hire"}]
    ans = [{"feedback_id": "f1", "competency_key": "skills", "label": "Skills", "weight": 2, "rating": 5},
           {"feedback_id": "f1", "competency_key": "comm", "label": "Communication", "weight": 1, "rating": 3},
           {"feedback_id": "f2", "competency_key": "skills", "label": "Skills", "weight": 2, "rating": 3},
           {"feedback_id": "f2", "competency_key": "comm", "label": "Communication", "weight": 1, "rating": 1}]
    agg = D.aggregate_scorecard(fb, ans)
    comps = {c["key"]: c for c in agg["competencies"]}
    assert comps["skills"]["avg"] == 4.0 and comps["comm"]["avg"] == 2.0 and comps["skills"]["n"] == 2
    assert agg["overall_weighted"] == round((2 * 4.0 + 1 * 2.0) / 3, 2)
    assert agg["recommendations"] == {"hire": 1, "no_hire": 1, "hold": 0} and agg["hire_share_pct"] == 50.0
    by = {i["feedback_id"]: i for i in agg["interviewers"]}
    assert by["f1"]["weighted_score"] == round((2 * 5 + 3) / 3, 2) and by["f2"]["weighted_score"] == round((2 * 3 + 1) / 3, 2)
    assert D.aggregate_scorecard([], [])["overall_weighted"] is None


def test_bias_guard_label_is_stable_and_name_free():
    a, b = D.blind_label("app-1"), D.blind_label("app-1")
    assert a == b and a.startswith("Candidate ") and len(a) == len("Candidate XXXX")
    assert D.blind_label("app-2") != a


# ── referrals + bonus ───────────────────────────────────────────────────────
def test_referral_form_and_status_mapping():
    c = D.clean_referral({"first_name": " Asha ", "email": "asha@example.com", "link_url": "https://linkedin.com/in/asha"})
    assert c["first_name"] == "Asha" and c["phone"] is None and c["link_url"].startswith("https://")
    for bad in ({"first_name": ""}, {"first_name": "A"}, {"first_name": "A", "email": "nope"},
                {"first_name": "A", "phone": "9876543210", "link_url": "ftp://x"}):
        with pytest.raises(RuleError):
            D.clean_referral(bad)
    assert D.referral_status_for_stage("joined") == "hired" and D.referral_status_for_stage("rejected") == "closed"
    assert D.referral_status_for_stage("interview") == "in_process" and D.referral_status_for_stage(None) == "submitted"
    x = D.clean_referral_candidate({"current_employer": "  Apollo  ", "city": "Pune" * 40})
    assert x["current_employer"] == "Apollo" and len(x["city"]) == 100
    assert D.clean_referral_candidate({}) == {"current_employer": None, "city": None}


def test_bonus_due_after_n_days_of_joining():
    joined = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
    due = D.bonus_due_on(joined, 90)
    assert due == date(2026, 10, 30) and D.pay_month_for(due) == "2026-10"
    assert D.bonus_state(False, None, date(2026, 10, 16), 10000) == "waiting_join"
    assert D.bonus_state(True, due, date(2026, 10, 16), 10000) == "waiting_days"
    assert D.bonus_state(True, due, date(2026, 10, 30), 10000) == "due"
    assert D.bonus_state(True, due, date(2026, 11, 1), 0) == "none"


def test_bonus_due_date_is_the_eastern_date_of_joining_not_the_utc_date():
    # joined 1 Nov 2026 02:30 UTC = 31 Oct 2026 22:30 EDT: the bonus belongs to October, not November
    joined = datetime(2026, 11, 1, 2, 30, tzinfo=timezone.utc)
    due = D.bonus_due_on(joined, 0)
    assert due == date(2026, 10, 31) and D.pay_month_for(due) == "2026-10"
    assert D.bonus_due_on(date(2026, 11, 1), 30) == date(2026, 12, 1)


# ── candidate messages ──────────────────────────────────────────────────────
def test_templates_render_only_allow_listed_placeholders():
    out = D.render("Hi {{first_name}}, {{job_title}} at {{org_name}} {{ secret }}", {"first_name": "Asha", "job_title": "Nurse", "org_name": "Acme"})
    assert out == "Hi Asha, Nurse at Acme {{ secret }}"
    assert D.unknown_placeholders("{{first_name}} {{password}} {{x}}") == ["password", "x"]
    assert set(D.DEFAULT_COMMS) >= {"applied", "rejected", "interview_scheduled", "interview_reminder", "slot_pick"}
    for ev, (s, b) in D.DEFAULT_COMMS.items():
        assert not D.unknown_placeholders(s) and not D.unknown_placeholders(b), ev


def test_comms_decision_is_honest_before_sending():
    assert D.comms_decision("email", True, "a@b.c", True, True) == ("ok", None)
    assert D.comms_decision("email", True, "a@b.c", True, False)[0] == "disabled"
    assert D.comms_decision("whatsapp", False, "+919876543210", True, True)[0] == "opted_out"
    assert D.comms_decision("whatsapp", True, None, True, True)[0] == "no_address"
    assert D.comms_decision("email", True, "a@b.c", False, True)[0] == "not_configured"
    assert D.mask_contact("asha@example.com") == "as••@example.com" and D.mask_contact("+91 98765 43210") == "••••••••3210"


# ── captcha hook ────────────────────────────────────────────────────────────
def test_captcha_hook_is_honest(monkeypatch):
    monkeypatch.delenv("LEXORA_CAPTCHA_PROVIDER", raising=False)
    assert D.captcha_info()["configured"] is False and run(D.verify_captcha(None)) is True
    monkeypatch.setenv("LEXORA_CAPTCHA_PROVIDER", "turnstile")
    monkeypatch.setenv("LEXORA_CAPTCHA_SECRET", "s")
    monkeypatch.setenv("LEXORA_CAPTCHA_SITE_KEY", "site")
    assert D.captcha_info() == {"configured": True, "provider": "turnstile", "site_key": "site"}
    assert run(D.verify_captcha(None, "1.2.3.4")) is False            # configured + no token = fail closed
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"success": True}))
    bad = httpx.MockTransport(lambda r: httpx.Response(200, json={"success": False}))
    assert run(D.verify_captcha("tok", "1.2.3.4", transport=ok)) is True
    assert run(D.verify_captcha("tok", "1.2.3.4", transport=bad)) is False


# ── signed OAuth state ──────────────────────────────────────────────────────
def test_oauth_state_is_signed_and_expires():
    t = D.sign_state({"org": "o1", "user": "u1", "provider": "google"}, ttl_sec=60, now=1000.0)
    assert D.verify_state(t, now=1030.0)["org"] == "o1"
    assert D.verify_state(t, now=1061.0) is None                        # expired
    raw, _, sig = t.rpartition(".")
    assert D.verify_state(raw + ".deadbeef", now=1030.0) is None        # tampered
    assert D.verify_state(None) is None and D.verify_state("nodot") is None


# ── settings extension ──────────────────────────────────────────────────────
def test_d2_settings_validate():
    s = L.merge_settings(None)
    assert s["careers_primary_color"] == "#6522d6" and s["interview_slot_link_days"] == 5 and s["offer_approval_chain"] is True
    ok = L.clean_settings({"careers_primary_color": "#ABCDEF", "referral_default_bonus": "5000", "interview_reminder_hours": 48,
                           "scorecard_hide_names": 1, "careers_tagline": " Care first "})
    assert ok == {"careers_primary_color": "#abcdef", "referral_default_bonus": 5000.0, "interview_reminder_hours": 48,
                  "scorecard_hide_names": True, "careers_tagline": "Care first"}
    for bad in ({"careers_primary_color": "teal"}, {"referral_default_bonus": -1}, {"interview_slot_link_days": 0}):
        with pytest.raises(RuleError):
            L.clean_settings(bad)


def test_files_dir_path_has_no_user_input():
    p = D.resume_path("org1", "cand1", "doc1", "../pdf")
    assert p.replace(os.sep, "/") == "org1/hiring/cand1/doc1.pdf"


# ── calendar adapters against MOCKED HTTP ───────────────────────────────────
@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(resilience, "BACKOFF_BASE", 0)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(resilience, "_audit", _noop)
    yield
    resilience.TRANSPORT = None


def mock(handler):
    calls = []

    def h(req: httpx.Request):
        calls.append(req)
        return handler(req, len(calls))
    resilience.TRANSPORT = httpx.MockTransport(h)
    return calls


EV = Event(title="Interview: Nurse — Technical (Asha)", start=NOW + timedelta(days=1), end=NOW + timedelta(days=1, minutes=45),
           description="Round 1", attendees=[{"email": "panel@acme.example", "name": "P"}, {"email": "asha@example.com"}],
           timezone="America/Toronto", online_meeting=True, request_id="lexora-iv-1")


def test_calendar_adapters_are_honest_without_an_oauth_app(monkeypatch):
    for v in ("LEXORA_GOOGLE_CLIENT_ID", "LEXORA_GOOGLE_CLIENT_SECRET", "LEXORA_MS_CLIENT_ID", "LEXORA_MS_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    for key in ("google", "microsoft"):
        a = calendar_adapter(key)
        assert not a.configured() and a.status()["state"] == "not_configured"
        d = a.describe()
        assert d["docs_url"].startswith("https://") and d["oauth_docs_url"].startswith("https://") and "not configured" in d["message"]
    assert calendar_adapter("zoom") is None


def test_google_auth_url_and_event_bodies(monkeypatch):
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_SECRET", "gsec")
    a = calendar_adapter("google")
    url = a.auth_url("https://api.example/hiring/public/calendar/google/callback", "st")
    assert url.startswith(G.AUTH_URL + "?") and "client_id=gid" in url and "access_type=offline" in url and "state=st" in url
    assert "calendar.events" in url
    body = G.event_body(EV)
    assert body["summary"] == EV.title and body["start"] == {"dateTime": EV.start.isoformat(), "timeZone": "America/Toronto"}
    assert body["attendees"][0] == {"email": "panel@acme.example", "displayName": "P"}
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert "conferenceData" not in G.event_body(Event(title="t", start=EV.start, end=EV.end, online_meeting=False))


def test_google_token_exchange_create_update_cancel_mocked(monkeypatch):
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_SECRET", "gsec")

    def handler(req: httpx.Request, n: int):
        p = req.url.path
        if p == "/token":
            assert req.method == "POST" and b"grant_type=authorization_code" in req.content
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3599, "scope": "s"})
        if p == "/v1/userinfo":
            return httpx.Response(200, json={"email": "hr@acme.example"})
        if p == "/calendar/v3/calendars/primary/events" and req.method == "POST":
            assert req.url.params["conferenceDataVersion"] == "1" and req.url.params["sendUpdates"] == "all"
            assert req.headers["Authorization"] == "Bearer at"
            assert json.loads(req.content)["conferenceData"]["createRequest"]["requestId"] == "lexora-iv-1"
            return httpx.Response(200, json={"id": "evt1", "htmlLink": "https://cal/evt1", "hangoutLink": "https://meet.google.com/abc-defg-hij"})
        if p == "/calendar/v3/calendars/primary/events/evt1" and req.method == "PATCH":
            assert "conferenceData" not in json.loads(req.content)
            return httpx.Response(200, json={"id": "evt1", "htmlLink": "https://cal/evt1", "hangoutLink": "https://meet.google.com/abc-defg-hij"})
        if p == "/calendar/v3/calendars/primary/events/evt1" and req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500, json={"error": {"message": "boom"}})
    calls = mock(handler)
    a = calendar_adapter("google")
    t = run(a.exchange_code("code", "https://api.example/cb", org_id="o1"))
    assert t.ok and t.access_token == "at" and t.refresh_token == "rt" and t.account_email == "hr@acme.example"
    assert token_is_fresh(t.expires_at)
    r = run(a.create_event("at", EV, org_id="o1"))
    assert r.ok and r.event_id == "evt1" and r.join_url == "https://meet.google.com/abc-defg-hij" and r.html_link == "https://cal/evt1"
    assert run(a.update_event("at", "evt1", EV, org_id="o1")).ok
    assert run(a.cancel_event("at", "evt1", org_id="o1")).ok
    assert len(calls) == 5


def test_google_join_link_is_only_reported_when_returned_and_errors_surface(monkeypatch):
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_SECRET", "gsec")
    mock(lambda req, n: httpx.Response(200, json={"id": "evt2", "htmlLink": "https://cal/evt2"}))
    r = run(calendar_adapter("google").create_event("at", EV))
    assert r.ok and r.join_url is None                                  # no Meet in the answer → no link claimed
    mock(lambda req, n: httpx.Response(403, json={"error": {"message": "Insufficient Permission"}}))
    r = run(calendar_adapter("google").create_event("at", EV))
    assert not r.ok and "403" in r.error
    mock(lambda req, n: httpx.Response(401, json={"error": "invalid_grant", "error_description": "Token has been expired"}))
    t = run(calendar_adapter("google").refresh("rt"))
    assert not t.ok and "401" in t.error
    assert G.parse_event({"id": "x", "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet/x"}]}}).join_url == "https://meet/x"


def test_google_refresh_keeps_the_refresh_token_and_retries_on_429(monkeypatch):
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("LEXORA_GOOGLE_CLIENT_SECRET", "gsec")

    def handler(req, n):
        if n == 1:
            return httpx.Response(429, json={"error": "rate_limited"})
        return httpx.Response(200, json={"access_token": "at2", "expires_in": 3600})
    calls = mock(handler)
    t = run(calendar_adapter("google").refresh("rt-old", org_id="o1"))
    assert t.ok and t.access_token == "at2" and t.refresh_token == "rt-old" and len(calls) == 2


def test_microsoft_auth_url_and_event_bodies(monkeypatch):
    monkeypatch.setenv("LEXORA_MS_CLIENT_ID", "mid")
    monkeypatch.setenv("LEXORA_MS_CLIENT_SECRET", "msec")
    monkeypatch.setenv("LEXORA_MS_TENANT", "acme.onmicrosoft.com")
    a = calendar_adapter("microsoft")
    assert a.configured()
    url = a.auth_url("https://api.example/cb", "st")
    assert url.startswith("https://login.microsoftonline.com/acme.onmicrosoft.com/oauth2/v2.0/authorize?")
    assert "Calendars.ReadWrite" in url and "offline_access" in url and "response_mode=query" in url
    body = M.event_body(EV)
    assert body["subject"] == EV.title and body["isOnlineMeeting"] is True and body["onlineMeetingProvider"] == "teamsForBusiness"
    assert body["start"] == {"dateTime": EV.start.replace(tzinfo=None).isoformat(timespec="seconds"), "timeZone": "America/Toronto"}
    assert body["attendees"][0] == {"emailAddress": {"address": "panel@acme.example", "name": "P"}, "type": "required"}
    assert body["body"] == {"contentType": "text", "content": "Round 1"}


def test_microsoft_graph_create_update_cancel_mocked(monkeypatch):
    monkeypatch.setenv("LEXORA_MS_CLIENT_ID", "mid")
    monkeypatch.setenv("LEXORA_MS_CLIENT_SECRET", "msec")
    monkeypatch.delenv("LEXORA_MS_TENANT", raising=False)

    def handler(req: httpx.Request, n: int):
        p = req.url.path
        if p == "/common/oauth2/v2.0/token":
            return httpx.Response(200, json={"access_token": "mat", "refresh_token": "mrt", "expires_in": 3600, "scope": "Calendars.ReadWrite"})
        if p == "/v1.0/me":
            return httpx.Response(200, json={"mail": "hr@acme.example", "userPrincipalName": "hr@acme.example"})
        if p == "/v1.0/me/events" and req.method == "POST":
            b = json.loads(req.content)
            assert b["isOnlineMeeting"] and req.headers["Authorization"] == "Bearer mat"
            return httpx.Response(201, json={"id": "AAMk1", "webLink": "https://outlook/AAMk1",
                                             "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/xyz"}})
        if p == "/v1.0/me/events/AAMk1" and req.method == "PATCH":
            assert "isOnlineMeeting" not in json.loads(req.content)
            return httpx.Response(200, json={"id": "AAMk1", "webLink": "https://outlook/AAMk1", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/xyz"}})
        if p == "/v1.0/me/events/AAMk1" and req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500, json={"error": {"code": "x", "message": "boom"}})
    calls = mock(handler)
    a = calendar_adapter("microsoft")
    t = run(a.exchange_code("code", "https://api.example/cb", org_id="o1"))
    assert t.ok and t.account_email == "hr@acme.example" and t.refresh_token == "mrt"
    r = run(a.create_event("mat", EV, org_id="o1"))
    assert r.ok and r.event_id == "AAMk1" and r.join_url.startswith("https://teams.microsoft.com/")
    assert run(a.update_event("mat", "AAMk1", EV)).ok and run(a.cancel_event("mat", "AAMk1")).ok
    assert len(calls) == 5
    mock(lambda req, n: httpx.Response(201, json={"id": "AAMk2", "webLink": "https://outlook/2"}))
    assert run(a.create_event("mat", EV)).join_url is None             # no Teams meeting returned → none claimed


def test_token_helpers():
    at = expires_in_to_at(3600, now=NOW)
    assert at == NOW + timedelta(seconds=3540) and token_is_fresh(at, now=NOW) and not token_is_fresh(at, now=NOW + timedelta(hours=1))
    assert not token_is_fresh(None) and expires_in_to_at("junk", now=NOW) == NOW + timedelta(seconds=3540)


# ── wiring ──────────────────────────────────────────────────────────────────
def test_d2_routes_exist_and_public_ones_stay_under_the_public_prefix():
    import hiring_approvals, hiring_boards, hiring_careers, hiring_comms, hiring_referrals, hiring_scheduling, hiring_scorecards
    paths = {(m, r.path) for rt in (hiring_approvals.router, hiring_boards.router, hiring_careers.router, hiring_comms.router,
                                    hiring_referrals.router, hiring_scheduling.router, hiring_scorecards.router)
             for r in rt.routes for m in r.methods}
    for want in [("GET", "/hiring/public/careers/{org_code}/feed/indeed.xml"), ("GET", "/hiring/public/careers/{org_code}/logo"),
                 ("GET", "/hiring/public/interview/{token}"), ("POST", "/hiring/public/interview/{token}/pick"),
                 ("GET", "/hiring/public/calendar/{provider}/callback"),
                 ("PUT", "/hiring/postings/{posting_id}/boards/{board}"), ("GET", "/hiring/postings/{posting_id}/export/{board}"),
                 ("POST", "/hiring/calendar/{provider}/connect"), ("POST", "/hiring/interviews/{iv_id}/slots"),
                 ("POST", "/hiring/interviews/{iv_id}/reschedule"), ("POST", "/hiring/interviews/{iv_id}/cancel"),
                 ("POST", "/hiring/interviews/{iv_id}/scorecard-feedback"), ("GET", "/hiring/applications/{app_id}/scorecard"),
                 ("POST", "/hiring/offers/{offer_id}/submit-approval"), ("POST", "/hiring/offers/{offer_id}/approval/{decision}"),
                 ("POST", "/hiring/referrals"), ("GET", "/hiring/referrals/mine"), ("PUT", "/hiring/requisitions/{req_id}/referral-rule"),
                 ("GET", "/hiring/referral-bonuses/for-payroll"), ("PUT", "/hiring/comms/templates"),
                 ("PUT", "/hiring/candidates/{cand_id}/comms")]:
        assert want in paths, want
    public = {p for _, p in paths if p.startswith("/hiring/public/")}
    assert public == {r.path for r in hiring_careers.router.routes}      # only hiring_careers is public
    assert not any(p.startswith("/hiring/public") for _, p in paths if p not in public)


def test_careers_source_tracking():
    from hiring_public import source_of
    assert source_of("indeed") == ("job_board", "Indeed") and source_of("referral") == ("referral", "careers link")
    assert source_of("agency:ABC Staffing") == ("agency", "abc staffing") and source_of("board:Shine") == ("job_board", "shine")
    assert source_of("") == ("careers_page", None) and source_of("<script>") == ("careers_page", None)


def test_new_tables_are_additive_and_prefixed():
    import hiring_d2_models as MM
    from database import Base
    names = {t.name for t in Base.metadata.sorted_tables}
    for t in ("hire_posting_boards", "hire_scorecard_templates", "hire_scorecard_answers", "hire_interview_slots",
              "hire_calendar_connections", "hire_calendar_events", "hire_offer_approval_steps", "hire_referral_rules",
              "hire_referrals", "hire_referral_bonuses_due", "hire_comms_templates", "hire_candidate_comms", "hire_comms_log"):
        assert t in names, t
    assert MM.BOARDS == ("indeed", "google_jobs", "linkedin")
