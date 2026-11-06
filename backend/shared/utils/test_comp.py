"""Effective-dated salary slices (Stream B) — shared/utils/comp.py.

Run:  cd backend && python -m pytest shared/utils/test_comp.py -q
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import comp as C  # noqa: E402

EMP = {"id": "e1", "annual_salary": 1200000, "pay_type": "salary", "standard_hours_per_week": 40}
OPENING = {"effective_from": "2025-04-01", "annual_amount": 1200000, "components": {"BASIC": 50000, "SHIFT": 25000, "SPECIAL": 25000}}


def test_no_history_gives_one_slice_from_the_record():
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31))
    assert s == [(date(2026, 10, 1), date(2026, 10, 31), 1200000.0, {})]


def test_one_row_covers_the_whole_period_with_its_components():
    s = C.effective_salary_for_period(EMP, "2026-10-01", "2026-10-31", history=[OPENING])
    assert len(s) == 1 and s[0][2] == 1200000.0 and s[0][3]["BASIC"] == 50000


def test_mid_month_revision_splits_the_period_by_days():
    hist = [OPENING, {"effective_from": "2026-10-16", "annual_amount": 1440000, "components": {"BASIC": 60000}}]
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31), history=hist)
    assert [(a.day, b.day, amt) for a, b, amt, _ in s] == [(1, 15, 1200000.0), (16, 31, 1440000.0)]
    assert C.split_days(date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 16)) == (15, 16)
    # old rate × days before + new rate × days from, never rounded on the way
    want = 100000 * 15 / 31 + 120000 * 16 / 31
    assert abs(C.period_amount(s, 12) - want) < 1e-9
    assert abs(C.blended_annual(s) - want * 12) < 1e-6


def test_several_revisions_inside_one_period():
    hist = [OPENING,
            {"effective_from": "2026-10-11", "annual_amount": 1320000},
            {"effective_from": "2026-10-21", "annual_amount": 1500000}]
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31), history=hist)
    assert [(a.day, b.day, amt) for a, b, amt, _ in s] == [(1, 10, 1200000.0), (11, 20, 1320000.0), (21, 31, 1500000.0)]
    assert sum(C.days_between(a, b) for a, b, _, _ in s) == 31            # no gap, no overlap
    want = (1200000 * 10 + 1320000 * 10 + 1500000 * 11) / 31
    assert abs(C.blended_annual(s) - want) < 1e-6


def test_future_dated_revision_does_not_touch_an_earlier_period():
    hist = [OPENING, {"effective_from": "2027-04-01", "annual_amount": 1500000}]
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31), history=hist)
    assert len(s) == 1 and s[0][2] == 1200000.0
    later = C.effective_salary_for_period(EMP, date(2027, 4, 1), date(2027, 4, 30), history=hist)
    assert len(later) == 1 and later[0][2] == 1500000.0
    assert C.revision_timing(date(2027, 4, 1), last_paid_end=date(2026, 9, 30), today=date(2026, 10, 12)) == "future"


def test_revision_on_the_first_day_gives_one_slice_at_the_new_rate():
    hist = [OPENING, {"effective_from": "2026-10-01", "annual_amount": 1440000}]
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31), history=hist)
    assert len(s) == 1 and s[0][2] == 1440000.0


def test_period_before_the_first_row_uses_the_opening_salary():
    s = C.effective_salary_for_period(EMP, date(2025, 1, 1), date(2025, 1, 31), history=[OPENING])
    assert len(s) == 1 and s[0][2] == 1200000.0


def test_same_day_correction_the_later_row_wins():
    hist = [OPENING, {"effective_from": "2026-10-01", "annual_amount": 1300000, "created_at": "2026-10-02T10:00"},
            {"effective_from": "2026-10-01", "annual_amount": 1350000, "created_at": "2026-10-05T10:00"}]
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31), history=hist)
    assert len(s) == 1 and s[0][2] == 1350000.0


def test_history_from_the_employee_dict_and_from_the_request_registry():
    hist = [OPENING, {"effective_from": "2026-10-16", "annual_amount": 1440000}]
    assert len(C.effective_salary_for_period({**EMP, "comp_history": hist}, date(2026, 10, 1), date(2026, 10, 31))) == 2
    C.register_history({"e1": hist})
    try:
        assert len(C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31))) == 2
        assert len(C.effective_salary_for_period({**EMP, "id": "other"}, date(2026, 10, 1), date(2026, 10, 31))) == 1
    finally:
        C.register_history(None)
        C._HISTORY.set(None)


def test_hourly_rows_are_annualised_with_standard_hours():
    e = {"id": "h1", "pay_type": "hourly", "hourly_rate": 30, "standard_hours_per_week": 40}
    hist = [{"effective_from": "2026-01-01", "hourly_rate": 30, "pay_type": "hourly"},
            {"effective_from": "2026-10-11", "hourly_rate": 33, "pay_type": "hourly"}]
    s = C.effective_salary_slices(e, date(2026, 10, 4), date(2026, 10, 17), history=hist)
    assert [x["hourly_rate"] for x in s] == [30.0, 33.0]
    assert s[0]["annual_amount"] == 30 * 40 * 52 and s[1]["annual_amount"] == 33 * 40 * 52
    assert [x["days"] for x in s] == [7, 7]


def test_blended_components_are_day_weighted():
    hist = [{"effective_from": "2026-01-01", "annual_amount": 1200000, "components": {"BASIC": 50000, "SHIFT": 25000}},
            {"effective_from": "2026-10-16", "annual_amount": 1440000, "components": {"BASIC": 60000, "SHIFT": 30000}}]
    s = C.effective_salary_for_period(EMP, date(2026, 10, 1), date(2026, 10, 31), history=hist)
    b = C.blended_components(s)
    assert abs(b["BASIC"] - (50000 * 15 + 60000 * 16) / 31) < 1e-9
    assert C.blended_components([(date(2026, 10, 1), date(2026, 10, 31), 1.0, {})]) == {}


def test_revision_timing_backdated_current_future():
    # September and October are paid; the raise is dated 16 September.
    assert C.revision_timing(date(2026, 9, 16), last_paid_end=date(2026, 10, 31), today=date(2026, 11, 5)) == "backdated"
    assert C.revision_timing(date(2026, 11, 3), last_paid_end=date(2026, 10, 31), today=date(2026, 11, 5)) == "current"
    assert C.revision_timing(date(2026, 12, 1), last_paid_end=date(2026, 10, 31), today=date(2026, 11, 5)) == "future"


def test_canada_retro_lines_are_plain_earning_differences():
    paid = [(date(2026, 9, 6), date(2026, 9, 19)), (date(2026, 9, 20), date(2026, 10, 3))]
    lines = C.retro_lines(date(2026, 9, 13), paid, 78000, 83200, 26)
    assert [ln["days"] for ln in lines] == [7, 14]
    assert abs(lines[0]["amount"] - (5200 / 26) * 7 / 14) < 1e-9          # half of the first period
    assert abs(lines[1]["amount"] - 5200 / 26) < 1e-9
    assert C.retro_lines(date(2026, 11, 1), paid, 78000, 83200, 26) == []  # nothing paid after the date
    assert set(lines[0]) == {"period_start", "period_end", "days", "period_days", "amount"}   # no tax, no deductions


def test_bad_period_is_refused():
    try:
        C.effective_salary_for_period(EMP, date(2026, 10, 31), date(2026, 10, 1))
    except ValueError:
        return
    raise AssertionError("an inverted period must be refused")


def test_currency_follows_the_payroll_country():
    assert C.currency_for("CA") == "CAD" and C.currency_for("ca") == "CAD" and C.currency_for(None) == "CAD"
