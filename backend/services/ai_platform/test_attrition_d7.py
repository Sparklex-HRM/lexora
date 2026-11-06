"""D7 attrition additions: pay vs peers, survey signal, training overdue, why list, calibration.
Run:  cd backend/services/ai_platform && PYTHONPATH=../../ python -m pytest -q test_attrition_d7.py
"""
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import attrition as A  # noqa: E402

TODAY = date(2026, 10, 16)


def test_pay_ratios_use_department_grade_then_department():
    emps = [{"id": "a", "department": "Plant", "grade": "G2", "annual_salary": 300000},
            {"id": "b", "department": "Plant", "grade": "G2", "annual_salary": 400000},
            {"id": "c", "department": "Plant", "grade": "G2", "annual_salary": 500000},
            {"id": "d", "department": "Plant", "grade": "G3", "annual_salary": 450000},      # G3 group too small → department
            {"id": "e", "department": "Office", "grade": None, "annual_salary": 0},           # no pay → skipped
            {"id": "f", "department": "Office", "grade": None, "annual_salary": 100000}]
    pr = A.pay_ratios(emps)
    assert pr["a"]["ratio"] == 0.75 and pr["a"]["peers"] == 3 and "G2" in pr["a"]["group"]
    assert pr["d"]["peers"] == 4 and pr["d"]["group"] == "Plant"
    assert pr["f"]["ratio"] is None and pr["f"]["peers"] == 1
    assert "e" not in pr


def test_pay_below_peers_and_survey_and_training_factors_with_why():
    emp = {"id": "a", "name": "Asha", "department": "Plant", "hire_date": "2016-01-01", "manager_id": "m"}
    ctx = {"revisions": {"a": ["2026-06-01"]}, "pay_ratio": {"a": {"ratio": 0.7, "peers": 5, "group": "Plant / grade G2"}},
           "survey_signals": {"a": "90-day check-in on 2026-10-01: eNPS 3"}, "training_overdue": {"a": 2}}
    r = A.score_employee(emp, ctx, TODAY)
    names = {f["factor"]: f["points"] for f in r["factors"]}
    assert names["Pay below peers"] == 15 and names["Survey signal"] == 10 and names["Training overdue"] == 5
    assert "Tenure band" in names and names["Tenure band"] == 0
    assert r["score"] == 30 and r["band"] == "watch"
    assert r["why"][0].startswith("Pay below peers (+15)")
    # ratio just under watch, few peers → no factor
    ctx2 = {"pay_ratio": {"a": {"ratio": 0.8, "peers": 2, "group": "Plant"}}}
    assert "Pay below peers" not in {f["factor"] for f in A.score_employee(emp, ctx2, TODAY)["factors"]}
    ctx3 = {"pay_ratio": {"a": {"ratio": 0.8, "peers": 4, "group": "Plant"}}}
    assert {f["factor"]: f["points"] for f in A.score_employee(emp, ctx3, TODAY)["factors"]}["Pay below peers"] == 10


def test_survey_signal_latest_low_answer_within_window_named_only():
    qs = [{"key": "nps", "type": "nps", "text": "Recommend?"}, {"key": "s", "type": "scale", "text": "Manager supports me"}, {"key": "t", "type": "text", "text": "?"}]
    rows = [{"employee_id": "a", "submitted_at": "2026-09-01", "questions": qs, "answers": {"nps": 3, "s": 4}, "title": "90-day"},
            {"employee_id": "a", "submitted_at": "2026-10-01", "questions": qs, "answers": {"nps": 9, "s": 2, "t": "fine"}, "title": "Pulse"},
            {"employee_id": "b", "submitted_at": "2026-10-01", "questions": qs, "answers": {"nps": 9, "s": 5}, "title": "Pulse"},
            {"employee_id": "c", "submitted_at": "2025-01-01", "questions": qs, "answers": {"nps": 0}, "title": "Old"},
            {"employee_id": None, "submitted_at": "2026-10-01", "questions": qs, "answers": {"nps": 0}, "title": "Anonymous"}]
    sig = A.survey_signal(rows, TODAY)
    assert set(sig) == {"a"} and sig["a"].startswith("Pulse on 2026-10-01") and "= 2" in sig["a"]


def test_calibration_needs_50_exits_then_reports_lifts():
    few = [{"left": True, "factors": ["No increment"], "score": 20}] * 10 + [{"left": False, "factors": [], "score": 0}] * 40
    c = A.calibrate(few)
    assert c["available"] is False and c["exits"] == 10 and c["needed"] == 50 and "rules only" in c["model"]
    hist = ([{"left": True, "factors": ["No increment", "Tenure band"], "score": 35}] * 40
            + [{"left": True, "factors": ["Tenure band"], "score": 15}] * 20
            + [{"left": False, "factors": ["No increment", "Tenure band"], "score": 35}] * 20
            + [{"left": False, "factors": ["Tenure band"], "score": 15}] * 120)
    c = A.calibrate(hist)
    assert c["available"] and c["exits"] == 60 and c["people"] == 200 and c["overall_exit_rate"] == 30.0
    ni = next(r for r in c["factors"] if r["factor"] == "No increment")
    assert ni["flagged"] == 60 and ni["exit_rate_flagged"] == 66.7 and ni["exit_rate_not_flagged"] == 14.3 and ni["lift"] == 4.67
    bands = {b["band"]: b for b in c["bands"]}
    assert bands["watch"]["exit_rate"] == 66.7 and bands["low"]["exit_rate"] == 14.3
    assert isinstance(c["model"], (str, dict))        # dict only when scikit-learn is installed


def test_why_list_orders_by_points():
    f = [{"factor": "A", "points": 5, "evidence": "x"}, {"factor": "B", "points": 20, "evidence": "y"}]
    assert A.why_list(f) == ["B (+20): y", "A (+5): x"]
    assert A.why_list([]) == []
