"""People analytics maths (Stream D7) — pure, no database.
Run:  cd backend/services/ops && PYTHONPATH=../../ python -m pytest -q test_analytics_logic.py
"""
from datetime import date

import analytics_logic as AL

TODAY = date(2026, 10, 16)
EMPS = [{"id": "a", "name": "A", "department": "Plant", "gender": "Female", "hire_date": "2024-01-10", "termination_date": None, "manager_id": "m1", "manager_name": "M1"},
        {"id": "b", "name": "B", "department": "Plant", "gender": "Male", "hire_date": "2026-09-05", "termination_date": None, "manager_id": "m1", "manager_name": "M1"},
        {"id": "c", "name": "C", "department": "Office", "gender": None, "hire_date": "2022-03-01", "termination_date": "2026-08-20", "manager_id": "m2", "manager_name": "M2"},
        {"id": "d", "name": "D", "department": "Office", "gender": None, "hire_date": "2025-11-01", "termination_date": None, "manager_id": "m2", "manager_name": "M2"},
        {"id": "e", "name": "E", "department": "Plant", "gender": "Female", "hire_date": "2026-11-01", "termination_date": None, "manager_id": "m1", "manager_name": "M1"},   # future joiner
        {"id": "m1", "name": "M1", "department": "Plant", "gender": "Male", "hire_date": "2019-01-01", "termination_date": None, "manager_id": None},
        {"id": "m2", "name": "M2", "department": "Office", "gender": "Female", "hire_date": "2019-01-01", "termination_date": None, "manager_id": None}]


def test_month_ends_and_headcount_series():
    ends = AL.month_ends(TODAY, 3)
    assert ends == [date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31)]
    s = AL.headcount_series(EMPS, TODAY, 3)
    assert [p["month"] for p in s] == ["2026-08", "2026-09", "2026-10"]
    aug, sep, octo = s
    assert aug["headcount"] == 4 and aug["leavers"] == 1 and aug["joiners"] == 0     # c left on 20 Aug; e has not joined
    assert sep["headcount"] == 5 and sep["joiners"] == 1                            # b joined 5 Sep
    assert octo["headcount"] == 5 and octo["joiners"] == 0 and octo["leavers"] == 0


def test_attrition_by_department_tenure_and_manager():
    by_dept = {r["group"]: r for r in AL.attrition_by(EMPS, TODAY, "department")}
    assert by_dept["Office"]["leavers"] == 1 and by_dept["Office"]["headcount"] == 2 and by_dept["Office"]["attrition_pct"] == 40.0
    assert by_dept["Plant"]["leavers"] == 0 and by_dept["Plant"]["attrition_pct"] == 0.0
    assert AL.attrition_rate(3, 0) is None and AL.attrition_rate(3, 30) == 10.0
    assert AL.tenure_band(0.5) == "< 1 y" and AL.tenure_band(1.9) == "1–2 y" and AL.tenure_band(4) == "2–5 y" and AL.tenure_band(9) == "5+ y" and AL.tenure_band(None) == "—"
    by_ten = {r["group"]: r for r in AL.attrition_by(EMPS, TODAY, "tenure_band")}
    assert by_ten["2–5 y"]["leavers"] == 1                     # c: 2022-03 → 2026-08 = 4.5 y at exit
    by_mgr = {r["group"]: r for r in AL.attrition_by(EMPS, TODAY, "manager_name")}
    assert by_mgr["M2"]["leavers"] == 1 and by_mgr["M1"]["leavers"] == 0


def test_gender_mix_is_descriptive_counts_only():
    g = {r["group"]: r for r in AL.gender_mix(EMPS, "department", TODAY)}
    assert g["Plant"]["total"] == 3 and g["Plant"]["by_gender"] == {"female": 1, "male": 2}        # e has not joined yet
    assert g["Office"]["by_gender"] == {"not recorded": 1, "female": 1}                              # c has left; d not recorded


def test_span_of_control():
    s = AL.span_of_control(EMPS, TODAY, wide_at=1)
    assert s["managers"] == 2 and s["rows"][0]["manager_id"] == "m1" and s["rows"][0]["reports"] == 2     # a, b (e not joined yet)
    assert next(r for r in s["rows"] if r["manager_id"] == "m2")["reports"] == 1                         # c has left
    assert s["average"] == 1.5 and s["median"] == 1.5 and s["wide"] == 1


def test_overtime_by_month_and_department():
    sheets = [{"employee_id": "a", "department": "Plant", "period_end": "2026-09-30", "ot_hours": 10, "ot2_hours": 2},
              {"employee_id": "b", "department": "Plant", "period_end": "2026-09-30", "ot_hours": 4, "ot2_hours": 0},
              {"employee_id": "a", "department": "Plant", "period_end": "2026-10-15", "ot_hours": 6, "ot2_hours": 0}]
    m = {r["group"]: r for r in AL.overtime_by(sheets, "month")}
    assert m["2026-09"]["ot_hours"] == 16.0 and m["2026-09"]["people"] == 2 and m["2026-09"]["avg_per_person"] == 8.0
    d = AL.overtime_by(sheets, "department")
    assert d[0]["group"] == "Plant" and d[0]["ot_hours"] == 22.0 and d[0]["sheets"] == 3


def test_leave_liability_estimate_is_labelled_and_money_optional():
    bals = [{"employee_id": "a", "leave_type_name": "Earned", "paid": True, "allocated": 20, "carried_over": 5, "used": 10},
            {"employee_id": "a", "leave_type_name": "Casual", "paid": True, "allocated": 8, "carried_over": 0, "used": 9},    # negative → skipped
            {"employee_id": "b", "leave_type_name": "LWP", "paid": False, "allocated": 10, "carried_over": 0, "used": 0},
            {"employee_id": "b", "leave_type_name": "Earned", "paid": True, "allocated": 15, "carried_over": 0, "used": 0}]
    ll = AL.leave_liability(bals, {"a": 360000, "b": 0}, 30)
    assert ll["total_days"] == 30.0 and ll["rows"][0]["employee_id"] == "a" and ll["rows"][0]["amount"] == 15000.0   # 15 d × (360000/12/30)
    assert ll["rows"][1]["amount"] == 0.0 and "estimate" in ll["method"]
    assert AL.leave_liability(bals, {}, 30)["total_amount"] == 0.0
