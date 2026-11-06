"""G5 pure-logic tests: shield, watchdog rules, attrition score, copilot intent parsing.

Run:  cd backend/services/ai_platform && python -m pytest -q test_g5_logic.py
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import attrition as A  # noqa: E402
import copilot as CP  # noqa: E402
import wd_rules as R  # noqa: E402
from shield import Shield, leaks  # noqa: E402

D = date
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
TODAY = D(2026, 10, 13)


# ══════════════════════════ shield ═════════════════════════════════════════════

def test_shield_masks_identifiers_and_money_and_restores():
    sh = Shield()
    raw = ("My SIN is 046 454 286 (or 123-456-789), pay me CAD 120,000 or $85,000.50, postal code K1A 0B1, "
           "mail a.b@x.com, phone (416) 555-0142 or 6045550100, account 123456789012345. 12 days, 8.5 hours, 2026-10-13.")
    out = sh.protect(raw)
    for v in ("046 454 286", "123-456-789", "CAD 120,000", "$85,000.50", "K1A 0B1", "a.b@x.com", "(416) 555-0142", "6045550100",
              "123456789012345"):
        assert v not in out, v
    assert "12 days" in out and "8.5 hours" in out and "2026-10-13" in out      # small numbers and dates stay
    assert leaks(out) == []
    assert sh.restore(out) == raw                                                # exact round trip
    assert sh.protect("046 454 286 again").startswith("[[SIN_1]]")              # stable token for the same value


def test_shield_masks_tool_results_by_key_and_size():
    sh = Shield()
    res = {"gross": 85000.0, "net_pay": 71234.5, "days": 22, "hours": 8.5, "balances": [{"leave_type": "Casual", "remaining": 7.5}],
           "sin": "046454286", "note": "paid $500 bonus", "big_count": 12345, "pay_date": "2026-09-30", "cpp": 212.4}
    out = sh.protect_value(res)
    assert out["gross"].startswith("[[MONEY_") and out["net_pay"].startswith("[[MONEY_")
    assert out["days"] == 22 and out["hours"] == 8.5 and out["balances"][0]["remaining"] == 7.5
    assert out["sin"].startswith("[[ID_") and out["cpp"].startswith("[[MONEY_")
    assert "$500" not in out["note"]
    assert out["big_count"].startswith("[[MONEY_")                               # any number ≥ 1000 is masked
    assert out["pay_date"] == "2026-09-30"
    assert sh.restore(out["gross"]) == "85000"
    assert sh.restore_value({"x": out["sin"]}) == {"x": "046454286"}


# ══════════════════════════ watchdog rules ═════════════════════════════════════

def _pay(eid, gross, pay_date):
    return {"employee_id": eid, "name": "E " + eid, "employee_number": eid.upper(), "gross": gross, "net": gross * 0.8,
            "pay_date": pay_date}


def test_payroll_variance_flags_only_unexplained_moves():
    last = [_pay("a", 2000, D(2026, 9, 25)), _pay("b", 2000, D(2026, 9, 25)), _pay("c", 2000, D(2026, 9, 25)), _pay("d", 2000, D(2026, 9, 25))]
    this = [_pay("a", 3000, D(2026, 10, 9)), _pay("b", 2400, D(2026, 10, 9)), _pay("c", 1200, D(2026, 10, 9)),
            _pay("d", 2080, D(2026, 10, 9)), _pay("new", 1600, D(2026, 10, 9))]
    ex = {"b": {"revision": "2026-10-01"}, "c": {"leave_days": 5}}
    out = R.payroll_variance(this, last, ex, {"variance_pct": 15, "high_pct": 40})
    assert [f["subject_employee_id"] for f in out] == ["a"]                       # b revision, c approved leave, d under 15%, new has no previous
    assert out[0]["severity"] == "high" and out[0]["evidence"][1]["change_pct"] == 50.0
    assert "Previous pay gross 2,000.00" in out[0]["explanation"]


def test_cpp_ei_rules():
    th = {"tolerance": 1, "cpp_max": 4230.45, "cpp2_max": 416.0, "ei_max": 1123.07, "ei_max_qc": 895.70}
    rows = [{"employee_id": "a", "name": "A", "pay_date": D(2026, 10, 9), "province": "ON", "cpp": 200, "cpp2": 0, "ei": 50,
             "ytd_cpp": 4230.45, "ytd_cpp2": 0, "ytd_ei": 1123.07},
            {"employee_id": "b", "name": "B", "pay_date": D(2026, 10, 9), "province": "BC", "cpp": 150, "cpp2": 0, "ei": 40,
             "ytd_cpp": 4300, "ytd_cpp2": 0, "ytd_ei": 1200, "cpp_exempt": True},
            {"employee_id": "c", "name": "C", "pay_date": D(2026, 10, 9), "province": "QC", "cpp": 0, "cpp2": 0, "ei": 30,
             "ytd_cpp": 5200, "ytd_cpp2": 0, "ytd_ei": 900, "ei_exempt": False}]
    out = R.cpp_ei_check(rows, th)
    fps = sorted(f["fingerprint"].split(":")[0] for f in out)
    assert "a" not in {f["subject_employee_id"] for f in out}                    # exactly at the maximum is fine
    assert fps == ["cpp_exempt", "cpp_max", "ei_max", "ei_max"]                    # b: exempt + CPP over + EI over; c: QC EI over 895.70
    assert all(f["subject_employee_id"] != "c" or "QC" in str(f["evidence"]) for f in out)
    assert not [f for f in out if f["subject_employee_id"] == "c" and f["fingerprint"].startswith("cpp_max")]   # QPP not compared


def test_ot_breach_daily_weekly_quarter():
    days = [{"employee_id": "a", "name": "A", "work_date": D(2026, 10, 5 + i), "actual_hours": h} for i, h in enumerate([13, 9, 9, 9, 9, 9, 9])]
    sheets = [{"employee_id": "a", "name": "A", "period_start": D(2026, 10, 1), "period_end": D(2026, 10, 7), "ot_hours": 100, "ot2_hours": 50}]
    out = R.ot_breach(days, sheets, {"daily_hours_max": 12, "weekly_hours_max": 60, "quarter_ot_hours_max": 144})
    kinds = sorted(f["fingerprint"].split(":")[0] for f in out)
    assert kinds == ["ot_day", "ot_quarter", "ot_week"]
    assert R.ot_breach(days[1:], [], {"daily_hours_max": 12, "weekly_hours_max": 60, "quarter_ot_hours_max": None}) == []


def test_missing_punches():
    days = [{"employee_id": "a", "name": "A", "work_date": D(2026, 10, 10), "status": "present", "first_in": "x", "last_out": None},
            {"employee_id": "a", "name": "A", "work_date": D(2026, 10, 9), "status": "present", "first_in": "x", "last_out": "y"},
            {"employee_id": "a", "name": "A", "work_date": D(2026, 10, 13), "status": "on_shift", "first_in": "x", "last_out": None}]   # today: ignored
    out = R.missing_punches(days, {("a", "2026-10-10"): 1, ("a", "2026-10-09"): 2}, {"warn_after_days": 2}, TODAY)
    assert len(out) == 1 and out[0]["severity"] == "info" and out[0]["evidence"][0]["problem"] == "only one punch"


def test_lifecycle_rules():
    emps = [{"id": "p1", "name": "P", "status": "active", "probation_end_date": D(2026, 10, 15)},
            {"id": "p2", "name": "Q", "status": "active", "probation_end_date": D(2026, 12, 1)}]
    out = R.probation_no_action(emps, {}, TODAY, {"days_ahead": 7})
    assert [f["subject_employee_id"] for f in out] == ["p1"]
    done = {"p1": {"action": "confirm", "created_at": D(2026, 10, 1)}}
    assert R.probation_no_action(emps, done, TODAY, {"days_ahead": 7}) == []

    items = [{"id": "i1", "kind": "leave", "kind_label": "Leave", "title": "Leave — X", "requester_name": "X", "opened_at": NOW - timedelta(days=6)},
             {"id": "i2", "kind": "leave", "kind_label": "Leave", "title": "Leave — Y", "requester_name": "Y", "opened_at": NOW - timedelta(days=1)}]
    aged = R.approvals_ageing(items, NOW, {"days": 5})
    assert len(aged) == 1 and aged[0]["evidence"][0]["days_waiting"] == 6

    cases = [{"id": "c1", "case_number": "EXT-1", "employee_id": "e1", "employee_name": "Z", "status": "clearance", "lwd": D(2026, 10, 1)}]
    leavers = [{"id": "e2", "name": "W", "status": "active", "termination_date": D(2026, 9, 30)}]
    ex = R.exit_past_lwd(cases, leavers, TODAY, {})
    assert {f["severity"] for f in ex} == {"warn", "high"}

    roe = R.roe_required([{"id": "e3", "name": "V", "termination_date": D(2026, 10, 1)}], {"e3": {"case_number": "EXT-2", "roe_reason_code": None}}, TODAY, {"deadline_days": 5})
    assert roe and roe[0]["severity"] == "high" and roe[0]["evidence"][0]["days_since"] == 12
    assert R.roe_required([{"id": "e3", "name": "V", "termination_date": D(2026, 10, 1)}], {"e3": {"roe_reason_code": "E"}}, TODAY, {}) == []

    sessions = [{"id": "s1", "status": "invited", "first_name": "N", "last_name": "H", "created_at": NOW - timedelta(days=10), "current_step": "identity"},
                {"id": "s2", "status": "invited", "first_name": "M", "last_name": "H", "created_at": NOW - timedelta(days=2), "current_step": "identity"}]
    assert len(R.onboarding_stuck(sessions, NOW, {"days": 7})) == 1

    docs = R.document_expiry([{"id": "d1", "name": "D", "status": "active", "work_permit_expiry": D(2026, 11, 1), "td1_federal_claim": None}], TODAY, {"days_ahead": 60}, "CA")
    assert {f["fingerprint"].split(":")[0] for f in docs} == {"permit", "td1"}
    assert R.max_severity(docs) == "warn"


# ══════════════════════════ attrition ══════════════════════════════════════════

def test_attrition_score_is_explainable_and_capped():
    emp = {"id": "e", "name": "E", "department": "Sales", "hire_date": D(2025, 6, 1), "manager_id": "m"}
    ctx = {"revisions": {}, "dept_revised_12m": {"Sales"}, "ratings": {"e": [4.0, 3.2]}, "leave_days": {"e": (6, 1)},
           "ot": {"e": (60, 5)}, "manager_changes": {"e": D(2026, 8, 1)}, "exits_by_manager": {"m": 2}}
    r = A.score_employee(emp, ctx, TODAY)
    names = [f["factor"] for f in r["factors"]]
    assert names == ["Tenure band", "Rating drop", "Leave pattern change", "Overtime load", "Manager changed", "Teammates leaving"]
    assert r["score"] == 15 + 15 + 10 + 10 + 10 + 15 and r["band"] == "high"
    assert "not a prediction" in r["label"]
    quiet = A.score_employee({"id": "q", "name": "Q", "hire_date": D(2015, 1, 1)}, {"revisions": {"q": [D(2026, 4, 1)]}}, TODAY)
    assert quiet["score"] == 0 and quiet["band"] == "low"
    stale = A.score_employee({"id": "s", "name": "S", "department": "Ops", "hire_date": D(2020, 1, 1)}, {"revisions": {"s": [D(2024, 1, 1)]}, "dept_revised_12m": {"Ops"}}, TODAY)
    assert stale["score"] == 0 + 30 and "others in Ops" in stale["factors"][1]["evidence"]   # 6.8 years tenure = 0 points
    assert A.band(49) == "watch" and A.band(50) == "high" and A.band(24) == "low"


# ══════════════════════════ copilot parsing ════════════════════════════════════

def test_copilot_intents_and_parsing():
    assert CP.intent_of("what is my leave balance") == "balance"
    assert CP.intent_of("apply vacation leave 2026-10-20 to 2026-10-21") == "apply_leave"
    assert CP.intent_of("explain CPP on my September pay stub") == "explain"
    assert CP.intent_of("show my payslip for 2026-09") == "payslip"
    assert CP.intent_of("what is pending for me?") == "approvals"
    assert CP.intent_of("what are the working hours policy") == "policy"
    assert CP.intent_of("hello") == "help"
    assert CP.parse_month("payslip for 2026-09", TODAY) == "2026-09"
    assert CP.parse_month("september payslip", TODAY) == "2026-09"
    assert CP.parse_month("last month", TODAY) == "2026-09"
    assert CP.parse_dates("leave 2026-10-20 to 2026-10-21", TODAY) == ("2026-10-20", "2026-10-21")
    assert CP.parse_dates("leave tomorrow", TODAY) == ("2026-10-14", "2026-10-14")
    assert CP.parse_dates("leave sometime", TODAY) == (None, None)
