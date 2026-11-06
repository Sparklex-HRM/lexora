"""Engagement rules (Stream G4) — pure, no database.
Run:  cd backend/services/ops && python -m pytest -q test_engage_logic.py
"""
from datetime import date, datetime, timedelta, timezone

import engage_logic as L

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


# ── helpdesk ──────────────────────────────────────────────────────────────────

def test_category_lookup_falls_back_to_other():
    assert L.category_for(L.DEFAULT_CATEGORIES, "payroll")["sla_hours"] == 48
    assert L.category_for(L.DEFAULT_CATEGORIES, "nope")["key"] == "nope"


def test_sla_due_and_states():
    created = NOW - timedelta(hours=10)
    due = L.sla_due(created, 24)
    assert due == created + timedelta(hours=24)
    assert L.sla_state(NOW, due, "open") == "ok"
    assert L.sla_state(NOW + timedelta(hours=12), due, "open") == "at_risk"
    assert L.sla_state(NOW + timedelta(hours=20), due, "in_progress") == "breached"
    assert L.sla_state(NOW + timedelta(hours=40), due, "resolved", resolved_at=NOW) == "met"
    assert L.sla_state(NOW + timedelta(hours=40), due, "resolved", resolved_at=NOW + timedelta(hours=30)) == "breached"
    assert L.sla_state(NOW, None, "open") == "n/a"


def test_age_and_buckets():
    assert L.age_hours(NOW, NOW - timedelta(hours=5, minutes=30)) == 5.5
    assert L.age_bucket(3) == "0-1 day" and L.age_bucket(30) == "1-3 days"
    assert L.age_bucket(100) == "3-7 days" and L.age_bucket(400) == "over 7 days"


def test_ageing_report_counts_open_breaches_and_averages():
    t = [
        {"status": "open", "created_at": NOW - timedelta(hours=2), "due_at": NOW + timedelta(hours=22), "category": "leave"},
        {"status": "open", "created_at": NOW - timedelta(hours=100), "due_at": NOW - timedelta(hours=28), "category": "leave"},
        {"status": "resolved", "created_at": NOW - timedelta(hours=10), "due_at": NOW + timedelta(hours=14),
         "resolved_at": NOW - timedelta(hours=4), "category": "payroll"},
        {"status": "closed", "created_at": NOW - timedelta(hours=80), "due_at": NOW - timedelta(hours=32),
         "resolved_at": NOW - timedelta(hours=2), "category": "payroll"},
    ]
    r = L.ageing_report(t, NOW)
    assert r["open"] == 2 and r["resolved"] == 2
    assert r["breached_open"] == 1 and r["breached_total"] == 2
    assert r["buckets"] == {"0-1 day": 1, "1-3 days": 0, "3-7 days": 1, "over 7 days": 0}
    assert r["by_category"]["payroll"]["avg_hours"] == 42.0
    assert r["by_category"]["leave"]["breached"] == 1


def test_next_number():
    assert L.next_number("HD", 7) == "HD-0007"


# ── surveys ───────────────────────────────────────────────────────────────────

def test_enps_maths():
    r = L.enps([10, 9, 8, 7, 6, 0, 10, 11, None])
    assert r["n"] == 7 and r["promoters"] == 3 and r["passives"] == 2 and r["detractors"] == 2
    assert r["enps"] == round((3 - 2) * 100 / 7)
    assert L.enps([])["enps"] is None


def test_anonymity_threshold_hides_results():
    qs = [{"key": "nps", "type": "nps", "text": "?"}]
    rs = [{"answers": {"nps": 9}}] * 4
    r = L.survey_results(qs, rs, 5, anonymous=True)
    assert r["visible"] is False and "at least 5" in r["message"]
    r = L.survey_results(qs, rs + [{"answers": {"nps": 2}}], 5, anonymous=True)
    assert r["visible"] and r["questions"][0]["enps"] == 60
    assert L.survey_results(qs, rs, 5, anonymous=False)["visible"]      # named surveys show at once


def test_distribution_and_scale_average():
    qs = [{"key": "s", "type": "scale", "text": "?"}, {"key": "c", "type": "choice", "text": "?", "options": ["A", "B"]},
          {"key": "t", "type": "text", "text": "?"}]
    rs = [{"answers": {"s": 4, "c": "A", "t": "fine"}}, {"answers": {"s": 2, "c": "A"}}, {"answers": {"s": 3}}]
    r = L.survey_results(qs, rs, 1, anonymous=False)
    s, c, t = r["questions"]
    assert s["average"] == 3.0 and s["distribution"] == {"1": 0, "2": 1, "3": 1, "4": 1, "5": 0}
    assert c["distribution"] == {"A": 2, "B": 0} and t["texts"] == ["fine"]


def test_targeting_and_open_window():
    e = {"id": "e1", "department": "Plant", "work_location": "Pune"}
    assert L.in_target({}, e) and L.in_target(None, e)
    assert L.in_target({"departments": ["Plant"]}, e) and not L.in_target({"departments": ["Office"]}, e)
    assert L.in_target({"employee_ids": ["e1"]}, e) and L.in_target({"locations": ["Pune"]}, e)
    d = date(2026, 10, 13)
    assert L.survey_open("open", None, None, d)
    assert not L.survey_open("open", date(2026, 10, 14), None, d)
    assert not L.survey_open("open", None, date(2026, 10, 12), d)
    assert not L.survey_open("draft", None, None, d)


# ── recognition ───────────────────────────────────────────────────────────────

def test_monthly_summary():
    k = [{"created_at": NOW, "to_employee_id": "a", "to_name": "A", "from_name": "B", "value_tag": "Teamwork", "status": "approved"},
         {"created_at": NOW, "to_employee_id": "a", "to_name": "A", "from_name": "C", "value_tag": "Safety", "status": "approved"},
         {"created_at": NOW, "to_employee_id": "b", "to_name": "B", "from_name": "C", "value_tag": "Safety", "status": "pending"},
         {"created_at": NOW - timedelta(days=40), "to_employee_id": "b", "to_name": "B", "from_name": "C", "value_tag": "Safety", "status": "approved"}]
    r = L.monthly_summary(k, "2026-10")
    assert r["total"] == 2 and r["people_recognised"] == 1 and r["top"][0]["count"] == 2
    assert r["by_value"] == {"Teamwork": 1, "Safety": 1} and r["top_givers"][0] == ("C", 1) or r["top_givers"][0] == ("B", 1)


# ── LMS ───────────────────────────────────────────────────────────────────────

QUIZ = [{"q": "1", "options": ["a", "b"], "answer": 1}, {"q": "2", "options": ["a", "b", "c"], "answer": 0},
        {"q": "3", "options": ["a", "b"], "answer": 0}]


def test_quiz_scoring_dict_and_list():
    assert L.quiz_score(QUIZ, {0: 1, 1: 0, 2: 1}) == {"score": 67, "correct": 2, "total": 3}
    assert L.quiz_score(QUIZ, [1, 0, 0])["score"] == 100
    assert L.quiz_score(QUIZ, {"0": "1", "1": "x"})["correct"] == 1
    assert L.quiz_score([], {})["score"] == 100
    assert L.passed(70, 70) and not L.passed(69, 70)


def test_progress_and_states():
    mods = [{"key": "m1"}, {"key": "m2"}]
    assert L.progress_pct(mods, ["m1"], False, True) == 33
    assert L.progress_pct(mods, ["m1", "m2"], True, True) == 100
    assert L.progress_pct(mods, ["m1"], False, False) == 50
    assert L.progress_pct([], [], False, False) == 100
    d = date(2026, 10, 13)
    assert L.assignment_state("assigned", date(2026, 10, 1), d) == "overdue"
    assert L.assignment_state("completed", date(2026, 10, 1), d) == "completed"
    assert L.assignment_state("in_progress", date(2026, 11, 1), d) == "in_progress"


def test_compliance_report_only_mandatory():
    courses = [{"id": "c1", "title": "Safety", "mandatory": True}, {"id": "c2", "title": "Excel", "mandatory": False}]
    asg = [{"course_id": "c1", "status": "completed", "due_on": None}, {"course_id": "c1", "status": "assigned", "due_on": date(2026, 1, 1)},
           {"course_id": "c1", "status": "in_progress", "due_on": date(2027, 1, 1)}, {"course_id": "c2", "status": "assigned", "due_on": None}]
    r = L.compliance_report(courses, asg, date(2026, 10, 13))
    assert len(r["courses"]) == 1
    c = r["courses"][0]
    assert c["assigned"] == 3 and c["completed"] == 1 and c["overdue"] == 1 and c["compliance_pct"] == 33
    assert r["compliance_pct"] == 33


def test_certificate_number():
    assert L.certificate_number("CERT", 12, 2026) == "CERT-2026-0012"


def test_certificate_pdf_renders():
    from engage_certificate import render_certificate
    data = render_certificate(person="Asha Rao", course="Fire safety – basics", number="CERT-2026-0001",
                              completed_on="2026-10-13", org_name="Lexora HRM Demo Pvt Ltd", score=90)
    assert data[:5] == b"%PDF-" and len(data) > 800
