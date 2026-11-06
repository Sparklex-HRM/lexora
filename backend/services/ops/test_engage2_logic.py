"""Engagement depth rules (Stream D7) — pure, no database.
Run:  cd backend/services/ops && PYTHONPATH=../../ python -m pytest -q test_engage2_logic.py
"""
from datetime import date, datetime, timezone

import engage2_logic as E2

TODAY = date(2026, 10, 16)       # a Friday


def test_next_run_weekly_monthly_quarterly():
    assert E2.next_run_on("weekly", 0, 1, TODAY) == date(2026, 10, 19)             # next Monday
    assert E2.next_run_on("weekly", 4, 1, TODAY) == date(2026, 10, 23)             # today is Friday → next Friday
    assert E2.next_run_on("monthly", 0, 1, TODAY) == date(2026, 11, 1)
    assert E2.next_run_on("monthly", 0, 16, TODAY) == date(2026, 11, 16)           # strictly after
    assert E2.next_run_on("monthly", 0, 31, date(2026, 12, 20)) == date(2026, 12, 28)   # day capped at 28
    assert E2.next_run_on("monthly", 0, 31, date(2026, 12, 29)) == date(2027, 1, 28)    # year rolls
    assert E2.next_run_on("quarterly", 0, 1, TODAY) == date(2027, 1, 1)
    assert E2.survey_title("Pulse", date(2026, 11, 1), "monthly") == "Pulse — Nov 2026"
    assert E2.survey_title("eNPS", date(2027, 1, 1), "quarterly") == "eNPS — Q1 2027"
    due = E2.schedules_due([{"id": 1, "active": True, "next_run_on": TODAY}, {"id": 2, "active": False, "next_run_on": TODAY},
                            {"id": 3, "active": True, "next_run_on": date(2026, 10, 17)}, {"id": 4, "active": True, "next_run_on": None}], TODAY)
    assert [s["id"] for s in due] == [1]


QS = [{"key": "nps", "type": "nps", "text": "?"}, {"key": "s", "type": "scale", "text": "?"}, {"key": "t", "type": "text", "text": "?"}]


def test_segment_results_hide_small_segments_but_keep_participation():
    rs = [{"answers": {"nps": 10, "s": 5}, "segment": "Plant"}] * 5 + [{"answers": {"nps": 2, "s": 1}, "segment": "Office"}] * 2
    rows = E2.segment_results(QS, rs, 5, anonymous=True)
    overall, plant, office = rows[0], next(r for r in rows if r["segment"] == "Plant"), next(r for r in rows if r["segment"] == "Office")
    assert overall["segment"] == "All" and overall["responses"] == 7 and not overall["hidden"]
    assert plant["scores"] == {"nps": 100, "s": 5.0} and not plant["hidden"]
    assert office["hidden"] and office["scores"] == {} and office["responses"] == 2       # below threshold: count only
    assert overall["scores"]["nps"] == round((5 - 2) * 100 / 7)
    # named survey: nothing hidden
    assert not any(r["hidden"] for r in E2.segment_results(QS, rs, 5, anonymous=False))
    # everything hidden when the whole survey is below the threshold
    assert E2.segment_results(QS, rs[:3], 5, anonymous=True)[0]["hidden"]


def test_enps_trend_deltas_skip_hidden_points():
    pts = [{"date": "2026-01", "score": 10, "visible": True}, {"date": "2026-02", "score": None, "visible": False},
           {"date": "2026-03", "score": 25, "visible": True}, {"date": "2026-04", "score": 20, "visible": True}]
    t = E2.trend_with_delta(pts)
    assert [p["delta"] for p in t] == [None, None, 15, -5]
    assert E2.participation(7, 20) == 35.0 and E2.participation(1, 0) is None


def test_manager_insights_team_threshold_and_recognition():
    team = {"a", "b", "c", "d", "e", "f"}
    surveys = [{"id": "s1", "title": "Pulse", "kind": "pulse", "date": "2026-10-01", "questions": [{"key": "s", "type": "scale", "text": "?"}],
                "responses": [{"answers": {"s": 4}, "manager_id": "m"}] * 5 + [{"answers": {"s": 1}, "manager_id": "other"}] * 5, "company_score": 2.5},
               {"id": "s2", "title": "Pulse 2", "kind": "pulse", "date": "2026-10-08", "questions": [{"key": "s", "type": "scale", "text": "?"}],
                "responses": [{"answers": {"s": 2}, "manager_id": "m"}] * 2, "company_score": None}]
    kudos = [{"from_employee_id": "m", "to_employee_id": "a", "points": 10, "status": "approved"},
             {"from_employee_id": "x", "to_employee_id": "b", "points": 0, "status": "approved"},
             {"from_employee_id": "m", "to_employee_id": "c", "points": 5, "status": "rejected"}]
    r = E2.manager_insights(team, surveys, kudos, 5, "m")
    s1, s2 = r["surveys"]
    assert s1["team_responses"] == 5 and not s1["hidden"] and s1["score"] == 4.0 and s1["participation_pct"] == 83.3 and s1["company_score"] == 2.5
    assert s2["hidden"] and s2["score"] is None and s2["team_responses"] == 2
    assert r["recognition"] == {"given": 1, "given_points": 10, "received_by_team": 2, "team_members_recognised": 2, "team_members_not_recognised": 4}


def test_budget_and_leaderboard():
    assert E2.budget_remaining(100, 30) == 70 and E2.budget_remaining(10, 30) == 0
    assert E2.can_spend(100, 90, 10) and not E2.can_spend(100, 95, 10) and not E2.can_spend(100, 0, -1) and E2.can_spend(100, 100, 0)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    k = [{"to_employee_id": "a", "to_name": "A", "points": 10, "created_at": now, "status": "approved"},
         {"to_employee_id": "a", "to_name": "A", "points": 5, "created_at": now, "status": "approved"},
         {"to_employee_id": "b", "to_name": "B", "points": 50, "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "status": "approved"},
         {"to_employee_id": "c", "to_name": "C", "points": 50, "created_at": now, "status": "pending"}]
    top = E2.points_leaderboard(k, "2026-10")
    assert top == [{"employee_id": "a", "name": "A", "points": 15, "count": 2}]


def test_onboarding_due_30_60_90_once_with_grace():
    emps = [{"id": "a", "hire_date": "2026-09-16", "status": "active"},      # 30 days today
            {"id": "b", "hire_date": "2026-07-18", "status": "active"},      # 90 days today
            {"id": "c", "hire_date": "2026-08-15", "status": "active"},      # 60 days was 2026-10-14 (within grace)
            {"id": "d", "hire_date": "2026-08-01", "status": "active"},      # 60 days was 2026-09-30 (past grace)
            {"id": "e", "hire_date": "2026-09-16", "status": "terminated"},
            {"id": "f", "hire_date": None, "status": "active"}]
    due = E2.onboarding_due(emps, {("b", 90)}, TODAY)
    assert sorted((d["employee_id"], d["stage"]) for d in due) == [("a", 30), ("c", 60)]
    assert E2.ONBOARDING_QUESTIONS[90][0]["type"] == "nps"
