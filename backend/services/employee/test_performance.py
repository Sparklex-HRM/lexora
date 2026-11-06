"""Performance rules — unit tests. Run: cd backend/services/employee && python -m pytest -q test_performance.py"""
import os
import sys
from datetime import date

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import performance_logic as P  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402

HR = Actor(org="o", user_id="u-hr", role="hr_manager", employee_id="e-hr")
HEAD = Actor(org="o", user_id="u-head", role="hr_admin", employee_id="e-head")
MGR = Actor(org="o", user_id="u-mgr", role="manager", employee_id="e-mgr")
SKIP = Actor(org="o", user_id="u-skip", role="manager", employee_id="e-skip")
EMP = Actor(org="o", user_id="u-emp", role="employee", employee_id="e-emp")
PEER = Actor(org="o", user_id="u-peer", role="employee", employee_id="e-peer")
OTHER_MGR = Actor(org="o", user_id="u-m2", role="manager", employee_id="e-m2")
SCALE = P.DEFAULT_SETTINGS["rating_scale"]
TEMPLATE = {"scale": SCALE, "competencies": ["Quality", "Teamwork"], "goal_weight": 60, "competency_weight": 40}
GOALS = [{"id": "g1", "title": "Reduce claim rejections", "weight": 50}, {"id": "g2", "title": "Close audits", "weight": 30},
         {"id": "g3", "title": "Train two juniors", "weight": 20}]
REVIEW = {"employee_id": "e-emp", "manager_id": "e-mgr", "reviewer_id": "e-skip", "stage": "manager",
          "self_review": {"comments": "mine"}, "manager_review": {"comments": "good year", "promotion_recommended": True,
                                                                    "promotion_note": "ready"},
          "reviewer_review": {"comments": "agree"}, "scores": {"overall": {"score": 4.1}}, "overall_score": 4.1,
          "final_score": 4.0, "final_band": "Exceeds expectations", "calibration_note": "moved down to fit the curve",
          "promotion_recommended": True, "promotion_note": "ready"}


# ── goal weights ────────────────────────────────────────────────────────────
def test_goal_weights_must_sum_to_100():
    assert P.weights_check([50, 30, 20]) == (True, 100.0)
    assert P.weights_check([33.33, 33.33, 33.34]) == (True, 100.0)
    assert P.weights_check([50, 30]) == (False, 80.0)
    assert P.weights_check([60, 50]) == (False, 110.0)
    assert P.weights_check([100, 0])[0] is False                     # a goal with no weight is not a goal
    assert P.weights_check([])[0] is False


def test_goal_status_and_who_may_act():
    assert P.goal_next("draft", "submit") == "proposed" and P.goal_next("returned", "submit") == "proposed"
    assert P.goal_next("proposed", "approve") == "approved" and P.goal_next("approved", "return") == "returned"
    for bad in (("draft", "approve"), ("approved", "submit"), ("draft", "return")):
        with pytest.raises(RuleError):
            P.goal_next(*bad)
    assert P.goal_can("edit", EMP, "e-emp", "e-mgr") and P.goal_can("checkin", EMP, "e-emp", "e-mgr")
    assert not P.goal_can("approve", EMP, "e-emp", "e-mgr")          # nobody approves their own goals
    assert P.goal_can("approve", MGR, "e-emp", "e-mgr")              # the manager does
    assert not P.goal_can("approve", OTHER_MGR, "e-emp", "e-mgr")    # another manager does not
    assert not P.goal_can("view", PEER, "e-emp", "e-mgr")            # a colleague cannot even see them
    assert P.goal_can("approve", HR, "e-emp", "e-mgr")               # HR may step in
    assert P.goal_can("approve", HEAD, "e-head", None) and not P.goal_can("approve", HEAD, "e-head", "e-mgr")
    assert P.clean_progress("45.5") == 45.5
    with pytest.raises(RuleError):
        P.clean_progress(120)


# ── score arithmetic ────────────────────────────────────────────────────────
def test_score_arithmetic_is_shown_and_right():
    g = P.goal_score(GOALS, {"g1": 5, "g2": 4, "g3": 3})
    # 50x5 + 30x4 + 20x3 = 250 + 120 + 60 = 430 ; / 100 = 4.3
    assert g["points_total"] == 430 and g["weight_total"] == 100 and g["score"] == 4.3
    assert [ln["points"] for ln in g["lines"]] == [250, 120, 60] and g["text"] == "430 points / 100 weight = 4.3"
    c = P.competency_score(["Quality", "Teamwork"], {"Quality": 4, "Teamwork": 3})
    assert c["score"] == 3.5 and c["text"] == "7 / 2 competencies = 3.5"
    o = P.overall_score(4.3, 3.5, 60, 40)
    # 4.3x60 + 3.5x40 = 258 + 140 = 398 ; / 100 = 3.98
    assert o["score"] == 3.98 and "= 398; / 100 = 3.98" in o["text"]
    assert P.overall_score(4.3, 3.5, 70, 30)["score"] == 4.06        # the org's weights drive it
    assert P.overall_score(None, 3.5, 60, 40)["score"] == 3.5        # no goals: competencies carry it
    assert P.overall_score(4.3, None, 60, 40)["score"] == 4.3
    assert P.overall_score(None, None, 60, 40)["score"] is None
    full = P.compute_scores(GOALS, {"goal_ratings": {"g1": 5, "g2": 4, "g3": 3},
                                    "competency_ratings": {"Quality": 4, "Teamwork": 3}}, TEMPLATE)
    assert full["overall"]["score"] == 3.98 and full["band"] == "Exceeds expectations"
    assert P.goal_score([], {})["score"] is None


def test_band_is_nearest_level_half_goes_up():
    assert P.band_for(3.98, SCALE) == "Exceeds expectations" and P.band_for(3.49, SCALE) == "Meets expectations"
    assert P.band_for(3.5, SCALE) == "Exceeds expectations" and P.band_for(1.0, SCALE) == "Unsatisfactory"
    assert P.band_for(None, SCALE) is None
    own = [{"label": "A", "value": 3}, {"label": "B", "value": 2}, {"label": "C", "value": 1}]   # an org's own scale
    assert P.band_for(2.6, own) == "A" and P.band_for(2.4, own) == "B"


def test_rating_scale_is_the_orgs_own():
    s = P.clean_scale([{"label": "C", "value": 1}, {"label": "A", "value": 3}, {"label": "B", "value": 2}])
    assert [x["label"] for x in s] == ["A", "B", "C"]
    for bad in ([{"label": "A", "value": 1}], [{"label": "A", "value": 1}, {"label": "a", "value": 2}],
                [{"label": "A", "value": 1}, {"label": "B", "value": 1}], [{"label": "", "value": 1}, {"label": "B", "value": 2}]):
        with pytest.raises(RuleError):
            P.clean_scale(bad)
    cur = P.merge_settings(None)
    with pytest.raises(RuleError):
        P.clean_settings({"guideline": [{"label": "Outstanding", "pct": 10}, {"label": "Meets expectations", "pct": 60}]}, cur)
    with pytest.raises(RuleError):
        P.clean_settings({"goal_weight": 0, "competency_weight": 0}, cur)
    assert P.clean_settings({"goal_weight": 70, "competency_weight": 30}, cur) == {"goal_weight": 70.0, "competency_weight": 30.0}


# ── stages ──────────────────────────────────────────────────────────────────
def test_stage_machine_and_whose_turn():
    assert P.next_stage("self", True, True) == "manager"
    assert P.next_stage("manager", True, True) == "reviewer"
    assert P.next_stage("manager", True, False) == "calibration"     # no skip-level on record
    assert P.next_stage("manager", False, True) == "calibration"     # stage switched off
    assert P.next_stage("reviewer", True, True) == "calibration" and P.next_stage("calibration", True, True) == "published"
    with pytest.raises(RuleError):
        P.next_stage("published", True, True)
    assert P.can_submit("self", EMP, REVIEW) and not P.can_submit("self", MGR, REVIEW)
    assert P.can_submit("manager", MGR, REVIEW) and not P.can_submit("manager", EMP, REVIEW)
    assert not P.can_submit("manager", HEAD, REVIEW)                 # HR does not write someone's review
    assert P.can_submit("reviewer", SKIP, REVIEW) and not P.can_submit("reviewer", MGR, REVIEW)
    assert not P.can_submit("calibration", HEAD, REVIEW)
    with pytest.raises(RuleError):                                   # reopen needs a reason
        P.hr_move_ok("manager", "self", "")
    with pytest.raises(RuleError):                                   # publishing is never a side effect of a move
        P.hr_move_ok("calibration", "published", "x")
    P.hr_move_ok("manager", "self", "Employee asked to correct a rating")


def test_submission_validation():
    ok = P.validate_stage_payload("manager", {"goal_ratings": {"g1": 5, "g2": 4, "g3": 3},
                                              "competency_ratings": {"Quality": 4, "Teamwork": 3}, "comments": "Solid"},
                                  GOALS, TEMPLATE)
    assert ok["goal_ratings"] == {"g1": 5.0, "g2": 4.0, "g3": 3.0} and ok["promotion_recommended"] is False
    base = {"goal_ratings": {"g1": 5, "g2": 4, "g3": 3}, "competency_ratings": {"Quality": 4, "Teamwork": 3}, "comments": "x"}
    for bad in ({**base, "goal_ratings": {"g1": 5}}, {**base, "competency_ratings": {"Quality": 4}},
                {**base, "goal_ratings": {"g1": 9, "g2": 4, "g3": 3}}, {**base, "comments": ""},
                {**base, "promotion_recommended": True}):
        with pytest.raises(RuleError):
            P.validate_stage_payload("manager", bad, GOALS, TEMPLATE)
    assert P.validate_stage_payload("self", {**base, "comments": ""}, GOALS, TEMPLATE)["comments"] == ""
    with pytest.raises(RuleError):
        P.validate_stage_payload("reviewer", {"comments": "ok", "suggested_score": 9}, GOALS, TEMPLATE)
    assert P.validate_stage_payload("reviewer", {"comments": "ok", "suggested_score": 3.5}, GOALS, TEMPLATE)["suggested_score"] == 3.5


# ── confidentiality ─────────────────────────────────────────────────────────
def test_who_may_read_a_review():
    assert P.view_scope(EMP, REVIEW) == "self"
    assert P.view_scope(MGR, REVIEW) == "manager"
    assert P.view_scope(SKIP, REVIEW) == "reviewer"
    assert P.view_scope(HR, REVIEW) == "hr"
    assert P.view_scope(PEER, REVIEW) is None                        # another employee: no access at all
    assert P.view_scope(OTHER_MGR, REVIEW) is None                   # a manager of a different team: none
    assert P.view_scope(Actor(role="employee", employee_id=""), REVIEW) is None
    own = {**REVIEW, "employee_id": "e-head"}
    assert P.view_scope(HEAD, own) == "self"                         # HR reading their OWN review gets the employee view


def test_employee_view_is_redacted():
    before = P.redact(REVIEW, "self")
    for hidden in ("manager_review", "reviewer_review", "scores", "overall_score", "final_score", "final_band",
                   "calibration_note", "promotion_recommended", "promotion_note"):
        assert hidden not in before, hidden
    assert before["self_review"] == {"comments": "mine"}
    after = P.redact({**REVIEW, "stage": "published"}, "self")
    assert after["final_band"] == "Exceeds expectations" and after["final_score"] == 4.0
    assert after["manager_review"] == {"comments": "good year"}      # comments yes, promotion note no
    assert "calibration_note" not in after and "reviewer_review" not in after and "promotion_note" not in after


def test_manager_view_is_redacted():
    drafting = P.redact({**REVIEW, "stage": "self"}, "manager")
    assert "self_review" not in drafting                             # the employee's draft is theirs until submitted
    m = P.redact(REVIEW, "manager")
    assert m["self_review"] and "calibration_note" not in m and "final_band" not in m and "reviewer_review" not in m
    pub = P.redact({**REVIEW, "stage": "published"}, "manager")
    assert pub["final_band"] == "Exceeds expectations" and "calibration_note" not in pub
    assert P.redact(REVIEW, "hr")["calibration_note"] == "moved down to fit the curve"


# ── calibration ─────────────────────────────────────────────────────────────
def test_calibration_change_needs_reason_and_is_audited():
    rv = {"stage": "calibration", "final_score": 3.98, "final_band": "Exceeds expectations", "calibration_note": None}
    with pytest.raises(RuleError) as e:
        P.calibrate(rv, 3.0, "", SCALE)
    assert e.value.status == 422
    values, audit = P.calibrate(rv, 3.0, "Peer group comparison", SCALE)
    assert values == {"final_score": 3.0, "final_band": "Meets expectations", "calibration_note": "Peer group comparison"}
    assert [(a["field"], a["old_value"], a["new_value"], a["reason"]) for a in audit] == [
        ("final_score", "3.98", "3", "Peer group comparison"),
        ("final_band", "Exceeds expectations", "Meets expectations", "Peer group comparison")]
    values, audit = P.calibrate(rv, 3.98, "", SCALE)                 # no change: nothing to audit, no reason needed
    assert audit == [] and values["final_score"] == 3.98
    _, audit = P.calibrate(rv, 4.2, "Stretch project", SCALE)        # score moved, band did not: one row
    assert [a["field"] for a in audit] == ["final_score"]
    with pytest.raises(RuleError):
        P.calibrate(rv, 7, "x", SCALE)
    with pytest.raises(RuleError):                                   # only while at HR calibration
        P.calibrate({**rv, "stage": "published"}, 3, "x", SCALE)


def test_distribution_vs_guideline():
    bands = ["Outstanding", "Exceeds expectations", "Exceeds expectations", "Meets expectations", None]
    d = {r["label"]: r for r in P.distribution(bands, SCALE, [{"label": "Outstanding", "pct": 10},
                                                              {"label": "Exceeds expectations", "pct": 20},
                                                              {"label": "Meets expectations", "pct": 60},
                                                              {"label": "Needs improvement", "pct": 10}])}
    assert d["Outstanding"]["count"] == 1 and d["Outstanding"]["pct"] == 25.0 and d["Outstanding"]["delta_pct"] == 15.0
    assert d["Exceeds expectations"]["pct"] == 50.0 and d["Meets expectations"]["delta_pct"] == -35.0
    assert d["Unsatisfactory"]["guideline_pct"] == 0.0
    assert "guideline_pct" not in P.distribution(bands, SCALE)[0]    # the guideline is optional


def test_eligibility():
    today = date(2026, 10, 12)
    cut = date(2026, 4, 1)
    emp = {"status": "active", "is_active": True, "hire_date": date(2025, 6, 1), "termination_date": None}
    assert P.eligible(emp, cut, True, today) == (True, None)
    assert P.eligible({**emp, "hire_date": date(2026, 4, 1)}, cut, True, today)[0] is False        # joined ON the date
    assert P.eligible({**emp, "hire_date": None}, cut, True, today)[0] is False
    assert P.eligible({**emp, "termination_date": date(2026, 11, 30)}, cut, True, today) == (False, "In notice period")
    assert P.eligible({**emp, "termination_date": date(2026, 11, 30)}, cut, False, today)[0] is True
    assert P.eligible({**emp, "status": "terminated"}, cut, True, today)[0] is False
    assert P.eligible(emp, None, True, today)[0] is True


# ── rating -> increment ─────────────────────────────────────────────────────
LABELS = {s["label"] for s in SCALE}
MATRIX = P.clean_matrix([
    {"band": "Outstanding", "min_pct": 12, "max_pct": 18},
    {"band": "Outstanding", "grade": "G5", "min_pct": 10, "max_pct": 14, "default_pct": 12},
    {"band": "Meets expectations", "min_pct": 4, "max_pct": 8, "default_pct": 5},
], LABELS)
REVIEWS = [
    {"employee_id": "e1", "employee_name": "Emp One", "stage": "published", "final_band": "Outstanding", "grade": "G3", "final_score": 4.8},
    {"employee_id": "e2", "employee_name": "Emp Two", "stage": "published", "final_band": "Outstanding", "grade": "g5", "final_score": 4.6},
    {"employee_id": "e3", "employee_name": "Emp Three", "stage": "published", "final_band": "Meets expectations", "grade": None, "final_score": 3.0},
    {"employee_id": "e4", "employee_name": "Emp Four", "stage": "published", "final_band": "Needs improvement", "grade": "G3", "final_score": 2.0},
    {"employee_id": "e5", "employee_name": "Emp Five", "stage": "calibration", "final_band": "Outstanding", "grade": "G3", "final_score": 4.9},
]
ANNUAL = {"e1": 1000000, "e2": 2000000, "e3": 600000, "e4": 500000, "e5": 900000}


def test_matrix_validation_and_lookup():
    assert MATRIX[0]["default_pct"] == 15.0                          # no default typed = the middle of the range
    assert P.matrix_lookup(MATRIX, "Outstanding", "G5")["max_pct"] == 14.0     # the grade row wins
    assert P.matrix_lookup(MATRIX, "Outstanding", "G3")["max_pct"] == 18.0     # else the any-grade row
    assert P.matrix_lookup(MATRIX, "Needs improvement", "G3") is None
    for bad in ([{"band": "Great", "min_pct": 1, "max_pct": 2}], [{"band": "Outstanding", "min_pct": 9, "max_pct": 5}],
                [{"band": "Outstanding", "min_pct": 5, "max_pct": 9, "default_pct": 20}],
                [{"band": "Outstanding", "min_pct": 1, "max_pct": 2}, {"band": "Outstanding", "min_pct": 3, "max_pct": 4}]):
        with pytest.raises(RuleError):
            P.clean_matrix(bad, LABELS)


def test_matrix_to_proposal():
    items = {i["employee_id"]: i for i in P.build_items(REVIEWS, ANNUAL, MATRIX)}
    assert set(items) == {"e1", "e2", "e3", "e4"}                    # only PUBLISHED ratings
    assert (items["e1"]["min_pct"], items["e1"]["max_pct"], items["e1"]["proposed_pct"]) == (12.0, 18.0, 15.0)
    assert items["e2"]["proposed_pct"] == 12.0 and items["e3"]["proposed_pct"] == 5.0
    assert items["e4"]["proposed_pct"] == 0.0 and items["e4"]["include"] is False and items["e4"]["min_pct"] is None
    assert P.check_item(items["e1"]) is None
    assert "Outside the 12% to 18% range" in P.check_item({**items["e1"], "proposed_pct": 20})
    assert P.check_item({**items["e1"], "proposed_pct": 20, "reason": "Retention case"}) is None
    assert P.check_item({**items["e1"], "proposed_pct": 11.99}) is not None
    assert P.check_item({**items["e4"], "proposed_pct": 3}) is not None and P.check_item({**items["e4"], "proposed_pct": 3, "reason": "PIP done"}) is None
    assert P.check_item({**items["e1"], "proposed_pct": 101, "reason": "x"}) is not None


def test_budget_totals():
    items = P.build_items(REVIEWS, ANNUAL, MATRIX)
    # 1,000,000 x 15% = 150,000 ; 2,000,000 x 12% = 240,000 ; 600,000 x 5% = 30,000  -> 420,000 (e4 not included)
    t = P.totals(items, 400000)
    assert t["increase_total"] == 420000.0 and t["people"] == 3 and t["current_annual_total"] == 3600000.0
    assert t["over_budget"] is True and t["remaining"] == -20000.0 and t["average_pct"] == 11.67
    assert P.totals(items, 500000)["over_budget"] is False and P.totals(items, None)["remaining"] is None
    assert P.item_cost(items[0]) == 150000.0


def test_bulk_draft_payload_matches_the_payroll_contract():
    items = P.build_items(REVIEWS, ANNUAL, MATRIX)
    items[0] = {**items[0], "proposed_pct": 20, "reason": "Retention case"}
    payload = P.bulk_draft_payload("cyc-1", "FY 2025-26", items, date(2026, 4, 1))
    assert payload["source"] == "performance:cyc-1"
    assert [i["employee_id"] for i in payload["items"]] == ["e1", "e2", "e3"]      # e4: not included, 0%
    assert payload["items"][0] == {"employee_id": "e1", "percent": 20.0, "effective_from": "2026-04-01",
                                   "reason": "Appraisal: FY 2025-26 - rating Outstanding", "note": "Retention case"}
    assert set(payload["items"][1]) == {"employee_id", "percent", "effective_from", "reason", "note"}
    assert payload["items"][1]["note"] is None
    assert all("new_annual" not in i and "amount" not in i for i in payload["items"])   # we never send a salary figure


def test_routes_exist():
    import performance_routers
    paths = {(m, r.path) for r in performance_routers.router.routes for m in r.methods}
    for want in [("POST", "/performance/goals/{action}"), ("POST", "/performance/reviews/{review_id}/submit"),
                 ("POST", "/performance/reviews/{review_id}/calibrate"), ("GET", "/performance/cycles/{cycle_id}/calibration"),
                 ("POST", "/performance/increment-proposals/{proposal_id}/send"), ("POST", "/performance/reviews/{review_id}/ack")]:
        assert want in paths, want


# Forms A (2026-10-24): manager summary notes, HR-only recommendations, promotion flag at calibration
def test_manager_summary_and_hr_only_recommendations():
    base = {"goal_ratings": {"g1": 5, "g2": 4, "g3": 3}, "competency_ratings": {"Quality": 4, "Teamwork": 3}, "comments": "x"}
    ok = P.validate_stage_payload("manager", {**base, "strengths_note": " Led the audit ", "improvement_note": "Delegate more",
                                              "pip_recommended": True, "pip_note": "Missed 4 of 6 deadlines",
                                              "recommended_hike_pct": "8.256"}, GOALS, TEMPLATE)
    assert ok["strengths_note"] == "Led the audit" and ok["improvement_note"] == "Delegate more"
    assert ok["pip_recommended"] is True and ok["pip_note"] == "Missed 4 of 6 deadlines" and ok["recommended_hike_pct"] == 8.26
    plain = P.validate_stage_payload("manager", base, GOALS, TEMPLATE)
    assert plain["pip_recommended"] is False and plain["recommended_hike_pct"] is None and plain["strengths_note"] is None
    for bad in ({**base, "pip_recommended": True}, {**base, "recommended_hike_pct": 120}, {**base, "recommended_hike_pct": "abc"}):
        with pytest.raises(RuleError):
            P.validate_stage_payload("manager", bad, GOALS, TEMPLATE)
    rv = {**REVIEW, "stage": "published", "manager_review": {**REVIEW["manager_review"], **ok}}
    seen = P.redact(rv, "self")["manager_review"]
    assert seen["strengths_note"] == "Led the audit" and seen["improvement_note"] == "Delegate more"
    for hidden in P.HR_ONLY_MANAGER_KEYS:
        assert hidden not in seen, hidden
    assert P.redact(rv, "hr")["manager_review"]["pip_note"] == "Missed 4 of 6 deadlines"


def test_promotion_flag_at_calibration_needs_reason():
    rv = {"stage": "calibration", "promotion_recommended": False, "promotion_note": None}
    assert P.calibrate_promotion(rv, False, "") == ({}, [])            # no change: nothing to do
    with pytest.raises(RuleError):
        P.calibrate_promotion(rv, True, "  ")
    values, audit = P.calibrate_promotion(rv, True, "Calibration panel agreed")
    assert values == {"promotion_recommended": True, "promotion_note": "Calibration panel agreed"}
    assert [(a["field"], a["old_value"], a["new_value"]) for a in audit] == [("promotion_recommended", "no", "yes")]
    values, _ = P.calibrate_promotion({**rv, "promotion_recommended": True, "promotion_note": "ready"}, False, "Not this year")
    assert values == {"promotion_recommended": False}
    with pytest.raises(RuleError):
        P.calibrate_promotion({**rv, "stage": "published"}, True, "x")
