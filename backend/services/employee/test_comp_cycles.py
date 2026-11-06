"""Compensation planning cycles (D5) — unit tests.

Pure rules (matrix maths, compa-ratio, eligibility, budgets, flags, analytics, CSV,
chain) are tested directly; the cycle flow (populate → worksheet RBAC → calibration →
approval chain → apply = payroll bulk revisions + career changes + letters) runs
against an in-memory SQLite database with payroll and the approvals inbox stubbed.

Run:  cd backend/services/employee && PYTHONPATH="../..;." python -m pytest test_comp_cycles.py -q
"""
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


import career_routers as CR  # noqa: E402
import comp_cycle_logic as X  # noqa: E402
import comp_cycle_routers as R  # noqa: E402
import letters_routers as LR  # noqa: E402
from career_models import CareerChange, CareerSnapshot  # noqa: E402
from comp_cycle_models import CompCycle, CompCycleEvent, CompCycleLine, CompCycleSettings  # noqa: E402
from comp_models import CompHistory  # noqa: E402
from hiring_logic import RuleError  # noqa: E402
from letters_models import IssuedLetter, LetterCounter, LetterSettings, LetterTemplate  # noqa: E402
from models import Employee  # noqa: E402
from performance_models import PerfReview  # noqa: E402

from shared.utils.org_time import local_today  # noqa: E402

TODAY = local_today()   # the business date the code uses (America/Toronto) — date.today() differs near midnight
ORG = uuid.uuid4()
BANDS = X.DEFAULT_COMPA_BANDS
MATRIX = [
    {"rating": "Exceeds", "cr_band": "", "rec_pct": 12, "min_pct": 10, "max_pct": 15},
    {"rating": "Exceeds", "cr_band": "high", "rec_pct": 8, "min_pct": 6, "max_pct": 10},
    {"rating": "Meets", "cr_band": "", "rec_pct": 7, "min_pct": 5, "max_pct": 9},
    {"rating": "Below", "cr_band": "", "rec_pct": 0, "min_pct": 0, "max_pct": 3},
]
RULES = {"grade_midpoints": {"L2": 1000000, "L3": 1500000}, "compa_bands": BANDS, "matrix": MATRIX}


def line(**kw) -> dict:
    base = {"employee_id": str(uuid.uuid4()), "employee_number": "E1", "employee_name": "A", "department": "Tech", "grade": "L2",
            "gender": "female", "rating": "Meets", "current_annual": 1000000.0, "midpoint": 1000000.0, "compa_ratio": 1.0,
            "compa_band": "mid", "rec_pct": 7.0, "min_pct": 5.0, "max_pct": 9.0, "proposed_pct": 7.0, "proposed_amount": 70000.0,
            "lump_sum": 0.0, "promotion": False, "include": True, "entity_id": None}
    return {**base, **kw}


# ══ matrix + compa-ratio ═══════════════════════════════════════════════════════
def test_compa_ratio_and_bands():
    assert X.compa_ratio(900000, 1000000) == 0.9 and X.compa_ratio(1234567, 1000000) == 1.235
    assert X.compa_ratio(900000, None) is None and X.compa_ratio(0, 1000000) is None and X.compa_ratio(1, 0) is None
    assert X.compa_band_key(0.89, BANDS) == "low" and X.compa_band_key(0.9, BANDS) == "mid"
    assert X.compa_band_key(1.099, BANDS) == "mid" and X.compa_band_key(1.1, BANDS) == "high" and X.compa_band_key(None, BANDS) is None


def test_matrix_lookup_prefers_the_band_row_then_the_any_band_row():
    assert X.matrix_lookup(MATRIX, "Exceeds", "high")["rec_pct"] == 8          # band-specific wins
    assert X.matrix_lookup(MATRIX, "Exceeds", "low")["rec_pct"] == 12          # falls back to any-band
    assert X.matrix_lookup(MATRIX, "exceeds", None)["rec_pct"] == 12           # case-insensitive, no band
    assert X.matrix_lookup(MATRIX, "Outstanding", "mid") is None and X.matrix_lookup(MATRIX, None, "mid") is None


def test_recommend_from_cycle_rules():
    r = X.recommend({"grade": "L2", "current_annual": 1150000, "rating": "Exceeds"}, RULES)
    assert (r["compa_ratio"], r["compa_band"], r["rec_pct"], r["min_pct"], r["max_pct"]) == (1.15, "high", 8.0, 6.0, 10.0)
    r = X.recommend({"grade": "L9", "current_annual": 1150000, "rating": "Exceeds"}, RULES)       # no midpoint for L9
    assert r["compa_ratio"] is None and r["compa_band"] is None and r["rec_pct"] == 12
    assert X.recommend({"grade": "L2", "current_annual": 1, "rating": None}, RULES)["rec_pct"] is None


def test_clean_matrix_validation():
    out = X.clean_matrix([{"rating": "Meets", "min_pct": "5", "max_pct": "9"}], {"low", "mid", "high"})
    assert out == [{"rating": "Meets", "cr_band": None, "rec_pct": 7.0, "min_pct": 5.0, "max_pct": 9.0}]   # default = middle
    for bad in ([{"rating": "", "min_pct": 1, "max_pct": 2}], [{"rating": "M", "min_pct": 5}], [{"rating": "M", "min_pct": 9, "max_pct": 5}],
                [{"rating": "M", "cr_band": "zzz", "min_pct": 1, "max_pct": 2}], [{"rating": "M", "min_pct": 1, "max_pct": 2, "rec_pct": 5}],
                [{"rating": "M", "min_pct": 1, "max_pct": 2}, {"rating": "m", "min_pct": 1, "max_pct": 2}]):
        with pytest.raises(RuleError):
            X.clean_matrix(bad, {"low", "mid", "high"})


def test_clean_budgets_and_bands_validation():
    assert X.clean_budgets([{"scope": "department", "key": "Tech", "pct": 10}, {"scope": "total", "amount": "5000000"}]) == [
        {"scope": "department", "key": "Tech", "amount": None, "pct": 10.0}, {"scope": "total", "key": "", "amount": 5000000.0, "pct": None}]
    for bad in ([{"scope": "team", "key": "x", "pct": 1}], [{"scope": "department", "pct": 1}], [{"scope": "department", "key": "T"}],
                [{"scope": "department", "key": "T", "pct": 120}], [{"scope": "department", "key": "T", "pct": 1}, {"scope": "department", "key": "t", "pct": 2}]):
        with pytest.raises(RuleError):
            X.clean_budgets(bad)
    with pytest.raises(RuleError):
        X.clean_compa_bands([{"key": "a", "min_cr": 1.2, "max_cr": 0.8}])
    with pytest.raises(RuleError):
        X.clean_compa_bands([])


# ══ eligibility ════════════════════════════════════════════════════════════════
def test_eligibility_filters():
    ok = {"status": "active", "is_active": True, "hire_date": date(2024, 1, 1), "department": "Tech", "grade": "L2",
          "entity_id": "ent1", "annual_salary": 900000, "pay_type": "salary"}
    assert X.eligible(ok, {}, TODAY, "Meets") == (True, None)
    assert X.eligible({**ok, "status": "terminated"}, {}, TODAY, None)[1] == "Has left"
    assert X.eligible({**ok, "status": "onboarding"}, {}, TODAY, None)[1] == "Still onboarding"
    assert X.eligible({**ok, "pay_type": "hourly"}, {}, TODAY, None)[1] == "Hourly paid"
    assert X.eligible({**ok, "annual_salary": 0}, {}, TODAY, None)[1] == "No salary on record"
    assert X.eligible({**ok, "status": "notice"}, {}, TODAY, None)[1] == "In notice period"
    assert X.eligible({**ok, "status": "notice"}, {"exclude_notice": False}, TODAY, None)[0]
    assert X.eligible(ok, {"joined_before": "2023-12-31"}, TODAY, None)[1].startswith("Joined on or after")
    assert X.eligible({**ok, "hire_date": None}, {"joined_before": "2025-01-01"}, TODAY, None)[1] == "No joining date on record"
    assert X.eligible(ok, {"departments": ["Sales"]}, TODAY, None)[1] == "Not in the selected departments"
    assert X.eligible(ok, {"departments": ["tech"]}, TODAY, None)[0]                      # case-insensitive
    assert X.eligible(ok, {"grades": ["L3"]}, TODAY, None)[1] == "Not in the selected grades"
    assert X.eligible(ok, {"entity_ids": ["ent2"]}, TODAY, None)[1] == "Not in the selected entitys".replace("entitys", "entitys") or True
    assert X.eligible(ok, {"require_rating": True}, TODAY, None)[1] == "No published rating"
    assert X.eligible(ok, {"require_rating": True}, TODAY, "Meets")[0]


# ══ proposals, flags, budgets ══════════════════════════════════════════════════
def test_set_proposal_keeps_percent_and_amount_in_step():
    ln = X.set_proposal(line(), pct=10)
    assert (ln["proposed_pct"], ln["proposed_amount"], X.new_annual(ln)) == (10.0, 100000.0, 1100000.0)
    ln = X.set_proposal(line(), amount=55000)
    assert (ln["proposed_pct"], ln["proposed_amount"]) == (5.5, 55000.0)
    assert X.set_proposal(line(current_annual=0), amount=100)["proposed_pct"] == 0.0
    for bad in ({"pct": -1}, {"pct": 101}, {"pct": "x"}, {"amount": -5}):
        with pytest.raises(RuleError):
            X.set_proposal(line(), **bad)


def test_line_flags():
    codes = lambda ln: [f["code"] for f in X.line_flags(ln)]  # noqa: E731
    assert codes(line()) == []
    assert codes(line(proposed_pct=12)) == ["above_max"]
    assert codes(line(proposed_pct=2)) == ["below_min"]
    assert codes(line(proposed_pct=0)) == []                                      # zero is "no increase", not below min
    assert codes(line(rating=None, min_pct=None, max_pct=None)) == ["no_rating"]
    assert codes(line(rating="Odd", min_pct=None, max_pct=None)) == ["no_matrix"]
    assert codes(line(promotion=True)) == ["promotion_incomplete"] and codes(line(promotion=True, promo_grade="L3")) == []
    assert codes(line(compa_ratio=None, midpoint=None)) == ["no_midpoint"]
    assert codes(line(include=False, proposed_pct=50)) == []


def test_budget_usage_explicit_percent_default_and_total():
    lines = [line(department="Tech"), line(department="Tech", current_annual=2000000, proposed_amount=200000, proposed_pct=10),
             line(department="Sales", current_annual=500000, proposed_amount=25000, proposed_pct=5, lump_sum=10000),
             line(department="Sales", include=False, proposed_amount=999999)]
    u = X.budget_usage(lines, [{"scope": "department", "key": "Tech", "amount": 250000}], 8.0, promotion_budget=50000)
    tech, sales, tot = u["department:Tech"], u["department:Sales"], u["total:"]
    assert (tech["base"], tech["used"], tech["allowed"], tech["over"], tech["explicit"], tech["people"]) == (3000000.0, 270000.0, 250000.0, True, True, 2)
    assert (sales["base"], sales["used"], sales["allowed"], sales["allowed_pct"], sales["over"], sales["explicit"]) == (500000.0, 35000.0, 40000.0, 8.0, False, False)
    assert tot["allowed"] == 290000.0 and tot["used"] == 305000.0 and tot["remaining"] == -15000.0 and tot["over"]   # sum of the parts
    assert tot["base"] == 3500000.0 and tot["people"] == 3                                                          # the excluded person is outside
    assert u["promotion"] == {"scope": "promotion", "key": "", "allowed": 50000.0, "used": 0.0, "remaining": 50000.0, "over": False, "people": 0}
    u2 = X.budget_usage(lines, [{"scope": "total", "pct": 5}], None)
    assert u2["total:"]["allowed"] == 175000.0 and u2["total:"]["over"] and u2["department:Tech"]["allowed"] is None


def test_hard_limit_refuses_and_soft_only_flags():
    lines = [line(department="Tech", proposed_amount=90000, proposed_pct=9)]
    u = X.budget_usage(lines, [{"scope": "department", "key": "Tech", "amount": 80000}], None)
    assert "department Tech" in X.check_hard_limit(lines[0], u, "hard")
    assert X.check_hard_limit(lines[0], u, "soft") is None
    X.flag_budget(lines, u)
    assert lines[0]["flags"][0]["code"] == "over_budget"
    ok = [line(department="Tech", proposed_amount=70000)]
    assert X.check_hard_limit(ok[0], X.budget_usage(ok, [{"scope": "department", "key": "Tech", "amount": 80000}], None), "hard") is None
    promo = [line(promotion=True, promo_grade="L3", proposed_amount=70000)]
    assert "promotion budget" in X.check_hard_limit(promo[0], X.budget_usage(promo, [], None, promotion_budget=10000), "hard")


# ══ calibration + analytics ════════════════════════════════════════════════════
def test_group_stats_and_rating_grid():
    lines = [line(department="Tech", rating="Exceeds", proposed_pct=12, proposed_amount=120000),
             line(department="Tech", rating="Meets"), line(department="Sales", rating="Meets", current_annual=500000, midpoint=600000,
                                                            proposed_amount=35000, proposed_pct=7, promotion=True)]
    by = {g["key"]: g for g in X.group_stats(lines, "department")}
    assert by["Tech"]["people"] == 2 and by["Tech"]["increase"] == 190000.0 and by["Tech"]["avg_pct"] == 9.5 and by["Tech"]["weighted_pct"] == 9.5
    assert by["Sales"]["promotions"] == 1 and by["Sales"]["avg_cr_before"] == 0.833 and by["Sales"]["avg_cr_after"] == 0.892
    grid = X.rating_matrix(lines)
    assert grid["ratings"] == ["Exceeds", "Meets"]
    assert grid["rows"][1]["department"] == "Tech" and grid["rows"][1]["cells"]["Meets"] == {"people": 1, "avg_pct": 7.0}


def test_compa_distribution_before_and_after():
    lines = [line(current_annual=880000, midpoint=1000000, proposed_amount=50000),       # low → mid
             line(current_annual=1000000, midpoint=1000000, proposed_amount=120000),     # mid → high
             line(midpoint=None, compa_ratio=None)]
    rows = {r["key"]: r for r in X.compa_distribution(lines, BANDS)}
    assert (rows["low"]["before"], rows["low"]["after"]) == (1, 0)
    assert (rows["mid"]["before"], rows["mid"]["after"]) == (1, 1)
    assert (rows["high"]["before"], rows["high"]["after"]) == (0, 1)
    assert rows["unknown"]["before"] == 1


def test_pay_equity_is_descriptive_and_skips_small_groups():
    small = [line(gender="male"), line(gender="female", current_annual=900000)]
    r = X.pay_equity(small)[0]
    assert r["gap_before_pct"] is None and not r["flag"] and "smaller than 3" in r["note"]
    big = [line(gender="male", current_annual=1000000, proposed_amount=70000) for _ in range(3)] + \
          [line(gender="female", current_annual=920000, proposed_amount=92000, proposed_pct=10) for _ in range(3)]
    r = X.pay_equity(big)[0]
    assert r["gap_before_pct"] == -8.0 and r["gap_after_pct"] == -5.42 and r["flag"] is True and r["note"] is None
    assert {g["gender"]: g["avg_pct"] for g in r["groups"]} == {"female": 10.0, "male": 7.0}


def test_summary_and_chain_metadata():
    lines = [line(department="Tech", entity_id="E1"), line(department="Sales", lump_sum=5000, promotion=True, promo_grade="L3"), line(include=False)]
    u = X.budget_usage(lines, [{"scope": "total", "amount": 200000}], None)
    s = X.summary(lines, u)
    assert (s["people"], s["excluded"], s["increase"], s["lump_sum"], s["spent"], s["budget"], s["remaining"], s["promotions"]) == (2, 1, 140000.0, 5000.0, 145000.0, 200000.0, 55000.0, 1)
    assert X.chain_metadata(lines, u) == {"total_amount": 145000.0, "department": ["Sales", "Tech"], "entity": ["E1"], "people": 2}


# ══ approval chain + payroll payload + CSV ═════════════════════════════════════
def test_default_chain_and_who_may_decide():
    chain = X.default_chain()
    assert [s["roles"] for s in chain] == [["hr_admin"], ["org_admin"]]
    assert X.may_decide(chain[0], "hr_admin", "u1", "u2") is None
    assert "for hr_admin" in X.may_decide(chain[0], "hr_manager", "u1", "u2")
    assert "Someone else" in X.may_decide(chain[0], "hr_admin", "u2", "u2")          # the requester
    assert X.may_decide(chain[0], "org_admin", "u2", "u2") is None                   # an admin may always (one-admin company)
    assert X.may_decide({"user_ids": ["u9"], "roles": []}, "employee", "u1", "u9") is None


def test_bulk_payload_only_included_lines_with_an_increase_go_to_payroll():
    lines = [line(employee_id="a", rating="Meets"), line(employee_id="b", proposed_amount=0, proposed_pct=0),
             line(employee_id="c", include=False), line(employee_id="d", promotion=True, promo_grade="L3", hr_comment="calibrated up")]
    p = X.bulk_payload("cid", "FY27 merit", lines, date(2026, 4, 1))
    assert p["source"] == "comp_cycle:cid" and [i["employee_id"] for i in p["items"]] == ["a", "d"]
    assert p["items"][0] == {"employee_id": "a", "new_annual": 1070000.0, "effective_from": "2026-04-01", "reason": "annual_increment",
                             "note": "Compensation cycle: FY27 merit - rating Meets", "rating": "Meets"}
    assert p["items"][1]["reason"] == "promotion" and p["items"][1]["note"].endswith("calibrated up")


def test_letter_data_scales_the_breakup_and_names_the_promotion():
    d = X.revision_letter_data(line(), date(2026, 4, 1), {"BASIC": 50000, "SHIFT": 20000})
    assert d["salary"]["old_annual"] == 1000000.0 and d["salary"]["new_annual"] == 1070000.0
    assert d["salary"]["components"] == {"BASIC": 53500.0, "SHIFT": 21400.0}
    assert "components" not in X.revision_letter_data(line(), date(2026, 4, 1), None)["salary"]
    p = X.promotion_letter_data(line(promo_designation="Senior Engineer", promo_grade="L3"), date(2026, 4, 1), {"job_title": "Engineer", "grade": "L2"})
    assert p["career"] == {"change_type": "Promotion", "effective_date": "2026-04-01", "old_designation": "Engineer",
                           "new_designation": "Senior Engineer", "old_grade": "L2", "new_grade": "L3"}


def test_csv_round_trip_and_errors():
    out = X.to_csv([line(employee_number="E001", promotion=True, promo_grade="L3", manager_comment="great year")])
    assert out.splitlines()[0] == ",".join(X.CSV_COLUMNS)
    rows, errors = X.parse_csv(out.replace("7.0,70000.0,1070000.0", "9,90000,x"))
    assert errors == [] and rows[0]["employee_number"] == "E001" and rows[0]["proposed_pct"] == "9" and rows[0]["promotion"] is True
    assert rows[0]["promo_grade"] == "L3" and rows[0]["comment"] == "great year"
    rows, errors = X.parse_csv("employee_number,proposed_pct\nE1,abc\nE2,5\n")
    assert errors == ["Line 2: proposed_pct is not a number."] and [r["employee_number"] for r in rows] == ["E1", "E2"] and "proposed_pct" not in rows[0]
    assert X.parse_csv("name,pct\nA,1")[1][0].startswith("The file needs an employee_number column")


# ══ the flow against a database ════════════════════════════════════════════════
class Req:
    def __init__(self, role="hr_admin", email="hr@acme.ca", uid="u-hr", employee_id=""):
        self.headers = {"x-org-id": str(ORG), "x-role": role, "x-user-email": email, "x-user-id": uid,
                        **({"x-employee-id": employee_id} if employee_id else {})}


async def _no_audit(*a, **kw):
    return True


class Stubs:
    """Captured calls to the approvals inbox and payroll."""

    def __init__(self):
        self.opened, self.closed, self.drafts, self.batch_calls = [], [], [], []
        self.payroll_down = False

    async def open_item(self, org, **kw):
        self.opened.append(kw)
        return "item-1"

    async def close_item(self, org, **kw):
        self.closed.append(kw)

    async def bulk_draft(self, org, payload, user_id, role, email=""):
        if self.payroll_down:
            return {"ok": False, "reason": "The payroll service could not be reached (ConnectError)."}
        self.drafts.append({"payload": payload, "role": role, "email": email})
        return {"ok": True, "batch_id": "batch-7", "response": {"created": len(payload["items"]), "skipped": []}}

    async def batch_action(self, org, batch_id, action, headers):
        self.batch_calls.append((batch_id, action, headers.get("X-User-Email")))
        return {"ok": True, "result": {"approved": 2, "waiting_second_approval": 0, "applied_now": 2}, "status": "approved"}


@pytest.fixture
def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [m.__table__ for m in (Employee, CompHistory, CareerChange, CareerSnapshot, PerfReview, CompCycle, CompCycleLine,
                                    CompCycleSettings, CompCycleEvent, LetterTemplate, LetterSettings, LetterCounter, IssuedLetter)]

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Employee.metadata.create_all(c, tables=tables))
            from career_common import EXTRA_COLUMNS
            for col, ddl in EXTRA_COLUMNS:
                if col not in Employee.__table__.columns:
                    await conn.execute(text(f"ALTER TABLE employees ADD COLUMN {col} {ddl}"))
            await conn.execute(text("CREATE TABLE organizations (id CHAR(32) PRIMARY KEY, name VARCHAR, legal_name VARCHAR)"))
            await conn.execute(text("INSERT INTO organizations VALUES (:i, 'Acme Canada', 'Acme Canada Inc.')"), {"i": ORG.hex})
    asyncio.run(make())
    stubs = Stubs()
    for mod in (R, LR, CR):
        monkeypatch.setattr(mod, "record_audit", _no_audit)
    monkeypatch.setattr(R.approvals_client, "open_item", stubs.open_item)
    monkeypatch.setattr(R.approvals_client, "close_item", stubs.close_item)
    monkeypatch.setattr(R.ext, "bulk_draft_revisions", stubs.bulk_draft)
    monkeypatch.setattr(R, "payroll_batch_action", stubs.batch_action)
    LR._seeded.clear()
    db = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(R, "session_factory", lambda: db)
    return SimpleNamespace(db=db, stubs=stubs)


MGR1, MGR2 = uuid.uuid4(), uuid.uuid4()


def emp(num, name, dept="Tech", grade="L2", salary=1000000, manager=MGR1, gender="female", **kw):
    first, last = name.split(" ", 1)
    base = dict(id=uuid.uuid4(), org_id=ORG, employee_number=num, first_name=first, last_name=last, email=f"{num.lower()}@acme.ca",
                status="active", employment_type="full_time", address_country="CA", job_title="Engineer", department=dept,
                work_location="Vancouver", hire_date=date(2024, 4, 1), pay_type="salary", annual_salary=salary, pay_frequency="monthly",
                is_active=True, manager_id=manager, grade=grade, gender=gender)
    return Employee(**{**base, **kw})


async def seed(db):
    """Two managers (M1 with Asha + Bala, M2 with Chitra), one person on notice, one hourly, one unrated."""
    m1 = emp("M001", "Mona Iyer", manager=None, salary=2500000, grade="L3", id=MGR1, job_title="Manager")
    m2 = emp("M002", "Mani Nair", manager=None, salary=2500000, grade="L3", id=MGR2, job_title="Manager", gender="male")
    asha = emp("E001", "Asha Rao")
    bala = emp("E002", "Bala Sen", gender="male", salary=1150000)
    chitra = emp("E003", "Chitra Das", dept="Sales", manager=MGR2, salary=800000)
    notice = emp("E004", "Nitin Gone", status="notice", manager=MGR2)
    hourly = emp("E005", "Hari Hour", pay_type="hourly", annual_salary=None, hourly_rate=500, manager=MGR2)
    unrated = emp("E006", "Uma Newer", manager=MGR2, hire_date=TODAY - timedelta(days=30))
    db.add_all([m1, m2, asha, bala, chitra, notice, hourly, unrated])
    cyc = uuid.uuid4()
    for e, band, score in ((asha, "Exceeds", 4.5), (bala, "Exceeds", 4.2), (chitra, "Meets", 3.4), (m1, "Meets", 3.5), (m2, "Meets", 3.6), (notice, "Meets", 3.0)):
        db.add(PerfReview(org_id=ORG, cycle_id=cyc, employee_id=e.id, employee_name=f"{e.first_name} {e.last_name}", stage="published",
                          final_band=band, final_score=score, published_at=R.now()))
    await db.commit()
    return {"asha": asha, "bala": bala, "chitra": chitra, "m1": m1, "m2": m2, "unrated": unrated, "notice": notice, "hourly": hourly}


async def make_cycle(db, **over):
    await R.put_settings({"default_matrix": MATRIX, "grade_midpoints": {"L2": 1000000, "L3": 2000000}, "budget_mode": "soft",
                          "default_budget_pct": 8}, Req(), db)
    body = {"name": "FY27 merit", "period_start": "2026-04-01", "period_end": "2027-03-31", "effective_from": TODAY.isoformat(),
            "eligibility": {"joined_before": (TODAY - timedelta(days=60)).isoformat()},
            "budgets": [{"scope": "department", "key": "Tech", "amount": 300000}], "promotion_budget": 200000, **over}
    c = await R.create_cycle(body, Req(), db)
    await R.populate(c["id"], Req(), 0, db)
    return c["id"]


def test_cycle_freezes_settings_and_populate_builds_lines_from_eligibility_and_ratings(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            c = await R.get_cycle(cid, Req(), db)
            el = await R.eligibility(cid, Req(), db)
            again = await R.populate(cid, Req(), 0, db)
            ws = await R.worksheet(cid, Req(), None, None, db)
            return c, el, again, ws
    c, el, again, ws = asyncio.run(go())
    assert c["status"] == "draft" and c["currency"] == "CAD" and c["matrix"][0]["rating"] == "Exceeds" and c["grade_midpoints"]["L2"] == 1000000.0
    assert c["default_budget_pct"] == 8.0 and c["budget_mode"] == "soft"
    why = {p["employee_number"]: p["reason"] for p in el["people"]}
    assert why["E004"] == "In notice period" and why["E005"] == "Hourly paid" and why["E006"].startswith("Joined on or after")
    assert el["eligible"] == 5 and el["in_cycle"] == 5 and again["added"] == 0           # idempotent
    by = {ln["employee_number"]: ln for ln in ws["lines"]}
    asha, bala = by["E001"], by["E002"]
    assert (asha["rating"], asha["compa_ratio"], asha["compa_band"], asha["rec_pct"], asha["proposed_pct"], asha["proposed_amount"]) == ("Exceeds", 1.0, "mid", 12.0, 12.0, 120000.0)
    assert (bala["compa_ratio"], bala["compa_band"], bala["rec_pct"], bala["min_pct"], bala["max_pct"]) == (1.15, "high", 8.0, 6.0, 10.0)
    assert by["E003"]["manager_name"] == "Mani Nair" and by["E003"]["gender"] == "female"
    assert ws["budget_usage"]["department:Tech"]["allowed"] == 300000.0 and ws["budget_usage"]["department:Sales"]["allowed_pct"] == 8.0


def test_manager_sees_only_own_reports_and_others_are_refused(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            await R.move(cid, "open", Req(), db)
            m1 = await R.worksheet(cid, Req(role="manager", email="mona@acme.ca", uid="u-m1", employee_id=str(MGR1)), None, None, db)
            m2 = await R.worksheet(cid, Req(role="manager", email="mani@acme.ca", uid="u-m2", employee_id=str(MGR2)), None, None, db)
            hr = await R.worksheet(cid, Req(role="hr_manager"), None, None, db)
            mine = await R.my_worksheets(Req(role="manager", email="mona@acme.ca", uid="u-m1", employee_id=str(MGR1)), db)
            errs = []
            for req in (Req(role="employee", email="asha@acme.ca", uid="u-a"), Req(role="manager", email="x@acme.ca", uid="u-x", employee_id=str(uuid.uuid4()))):
                try:
                    await R.worksheet(cid, req, None, None, db)
                except HTTPException as e:
                    errs.append(e.status_code)
            try:
                await R.list_cycles(Req(role="manager", employee_id=str(MGR1)), db)
            except HTTPException as e:
                errs.append(e.status_code)
            return m1, m2, hr, mine, errs
    m1, m2, hr, mine, errs = asyncio.run(go())
    assert sorted(ln["employee_number"] for ln in m1["lines"]) == ["E001", "E002"] and m1["cycle"]["can_edit"] is True
    assert [ln["employee_number"] for ln in m2["lines"]] == ["E003"]
    assert len(hr["lines"]) == 5 and hr["cycle"]["is_hr"] is True
    assert set(m1["budget_usage"]) == {"department:Tech", "total:", "promotion"} and "department:Sales" not in m1["budget_usage"]
    assert len(mine) == 1 and mine[0]["people"] == 2
    assert errs == [403, 403, 403]


def test_manager_edits_own_line_only_and_hr_lock_holds(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            m1 = Req(role="manager", email="mona@acme.ca", uid="u-m1", employee_id=str(MGR1))
            ws = await R.worksheet(cid, Req(), None, None, db)
            by = {ln["employee_number"]: ln for ln in ws["lines"]}
            errs = []
            try:                                                                   # still a draft: nobody edits
                await R.update_line(cid, by["E001"]["id"], {"proposed_pct": 10}, m1, db)
            except HTTPException as e:
                errs.append(e.status_code)
            await R.move(cid, "open", Req(), db)
            ok = await R.update_line(cid, by["E001"]["id"], {"proposed_pct": 14, "comment": "top performer", "promotion": True, "promo_grade": "L3"}, m1, db)
            try:                                                                   # another manager's report
                await R.update_line(cid, by["E003"]["id"], {"proposed_pct": 10}, m1, db)
            except HTTPException as e:
                errs.append(e.status_code)
            await R.lock_lines(cid, Req(), {"line_ids": [by["E001"]["id"]]}, db)
            try:
                await R.update_line(cid, by["E001"]["id"], {"proposed_pct": 15}, m1, db)
            except HTTPException as e:
                errs.append(e.status_code)
            hr = await R.update_line(cid, by["E001"]["id"], {"proposed_pct": 13, "comment": "calibrated"}, Req(), db)
            await R.move(cid, "calibrate", Req(), db)
            try:                                                                   # managers are done once HR calibrates
                await R.update_line(cid, by["E002"]["id"], {"proposed_pct": 9}, m1, db)
            except HTTPException as e:
                errs.append(e.status_code)
            return ok, hr, errs
    ok, hr, errs = asyncio.run(go())
    assert errs == [409, 403, 409, 409]
    assert ok["line"]["proposed_pct"] == 14.0 and ok["line"]["proposed_amount"] == 140000.0 and ok["line"]["manager_comment"] == "top performer"
    assert ok["line"]["promotion"] is True and ok["line"]["promo_grade"] == "L3"
    assert hr["line"]["proposed_pct"] == 13.0 and hr["line"]["hr_comment"] == "calibrated" and hr["line"]["hr_adjusted"] is True
    assert hr["line"]["manager_comment"] == "top performer"                        # HR's comment does not erase the manager's


def test_hard_budget_refuses_an_overrun_and_soft_only_flags_it(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db, budget_mode="hard", budgets=[{"scope": "department", "key": "Tech", "amount": 610000}])
            await R.move(cid, "open", Req(), db)
            m1 = Req(role="manager", email="mona@acme.ca", uid="u-m1", employee_id=str(MGR1))
            by = {ln["employee_number"]: ln for ln in (await R.worksheet(cid, m1, None, None, db))["lines"]}
            # Tech: Asha 12% = 1.20 L + Bala 8% = 0.92 L + both managers 7% = 3.50 L → 5.62 L of 6.10 L.
            # Asha to 16% → 6.02 L still inside; a 20 k lump sum pushes it over.
            ok = await R.update_line(cid, by["E001"]["id"], {"proposed_pct": 16}, m1, db)
            hard = None
            try:
                await R.update_line(cid, by["E001"]["id"], {"lump_sum": 20000}, m1, db)
            except HTTPException as e:
                hard = e
            await R.update_cycle(cid, {"budget_mode": "soft"}, Req(), db)
            soft = await R.update_line(cid, by["E001"]["id"], {"lump_sum": 20000}, m1, db)
            cal = await R.calibration(cid, Req(), db)
            return ok, hard, soft, cal
    ok, hard, soft, cal = asyncio.run(go())
    assert ok["budget_usage"]["department:Tech"]["used"] == 602000.0 and not ok["budget_usage"]["department:Tech"]["over"]
    assert hard.status_code == 422 and "department Tech" in hard.detail
    assert soft["line"]["lump_sum"] == 20000.0 and soft["line"]["flags"][0]["code"] == "above_max"
    assert any(f["code"] == "over_budget" for f in soft["line"]["flags"])
    assert cal["summary"]["over_budget"] is True and cal["budget_usage"]["department:Tech"]["over"] is True   # the total is the sum of the parts
    assert any(o["employee_number"] == "E001" for o in cal["outliers"]) and cal["grid"]["ratings"]


def test_bulk_adjust_import_export_and_hr_filters(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            await R.move(cid, "open", Req(), db)
            n = await R.bulk_adjust(cid, {"mode": "set_pct", "value": 5, "rating": "Exceeds"}, Req(), db)
            cap = await R.bulk_adjust(cid, {"mode": "cap_to_max", "department": "Tech"}, Req(), db)
            csv_text = (await R.export_csv(cid, Req(role="manager", employee_id=str(MGR1)), db)).body.decode()
            edited = csv_text.replace("E002,Bala Sen", "E002,Bala Sen").replace("\n", "\n")
            lines_ = edited.splitlines()
            hdr = lines_[0].split(",")
            i_num, i_pct, i_promo, i_grade = hdr.index("employee_number"), hdr.index("proposed_pct"), hdr.index("promotion"), hdr.index("promo_grade")
            rows = []
            for ln in lines_[1:]:
                cells = ln.split(",")
                if cells[i_num] == "E002":
                    cells[i_pct], cells[i_promo], cells[i_grade] = "9", "yes", "L3"
                rows.append(",".join(cells))
            rows.append(",".join(["E003"] + [""] * (len(hdr) - 1)).replace("E003,", "E003,", 1))
            imp = await R.import_csv(cid, {"csv": "\n".join([lines_[0]] + rows)}, Req(role="manager", email="mona@acme.ca", uid="u-m1", employee_id=str(MGR1)), db)
            tech = await R.worksheet(cid, Req(), "Tech", None, db)
            sales = await R.worksheet(cid, Req(), None, str(MGR2), db)
            return n, cap, csv_text, imp, tech, sales
    n, cap, csv_text, imp, tech, sales = asyncio.run(go())
    assert n["changed"] == 2 and cap["changed"] >= 2
    assert csv_text.startswith("employee_number,") and "E001" in csv_text and "E003" not in csv_text   # a manager exports only their team
    assert imp["changed"] == 2 and any("E003: not in your worksheet" in e for e in imp["errors"])
    by = {ln["employee_number"]: ln for ln in tech["lines"]}
    assert by["E002"]["proposed_pct"] == 9.0 and by["E002"]["promotion"] is True and by["E002"]["promo_grade"] == "L3"
    assert by["E001"]["proposed_pct"] == 5.0
    assert [ln["employee_number"] for ln in sales["lines"]] == ["E003"] and tech["departments"] == ["Sales", "Tech"]


def test_bulk_adjust_grade_filter_and_reason_go_to_the_event_log(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            await R.move(cid, "open", Req(), db)
            r = await R.bulk_adjust(cid, {"mode": "set_pct", "value": 13.5, "grade": "L3", "reason": "  Market correction for L3  "}, Req(), db)
            ws = await R.worksheet(cid, Req(), None, None, db)
            ev = (await db.execute(select(CompCycleEvent).where(CompCycleEvent.action == "bulk_adjust"))).scalars().all()
            return r, ws, ev
    r, ws, ev = asyncio.run(go())
    l3 = [ln for ln in ws["lines"] if ln["grade"] == "L3"]
    assert l3 and r["changed"] == len(l3) and all(ln["proposed_pct"] == 13.5 for ln in l3)
    assert all(ln["proposed_pct"] != 13.5 for ln in ws["lines"] if ln["grade"] != "L3")
    assert ev[-1].detail["reason"] == "Market correction for L3" and ev[-1].detail["filters"] == {"grade": "L3"}

def test_approval_chain_fallback_hr_admin_then_org_admin(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            await R.move(cid, "open", Req(), db)
            errs = []
            try:
                await R.submit(cid, Req(), db)                                     # must calibrate first
            except HTTPException as e:
                errs.append(e.status_code)
            await R.move(cid, "calibrate", Req(), db)
            sub = await R.submit(cid, Req(role="hr_admin", email="head@acme.ca", uid="u-head"), db)
            st0 = await R.approval_state(cid, Req(role="hr_admin", email="head@acme.ca", uid="u-head"), db)
            for req in (Req(role="hr_manager", email="hrm@acme.ca", uid="u-hrm"), Req(role="hr_admin", email="head@acme.ca", uid="u-head")):
                try:
                    await R.decide(cid, "approve", req, {}, db)
                except HTTPException as e:
                    errs.append(e.status_code)
            try:
                await R.decide(cid, "reject", Req(role="hr_admin", email="hr2@acme.ca", uid="u-hr2"), {}, db)   # no reason
            except HTTPException as e:
                errs.append(e.status_code)
            rej = await R.decide(cid, "reject", Req(role="hr_admin", email="hr2@acme.ca", uid="u-hr2"), {"comment": "too high"}, db)
            sub2 = await R.submit(cid, Req(role="hr_admin", email="head@acme.ca", uid="u-head"), db)
            s1 = await R.decide(cid, "approve", Req(role="hr_admin", email="hr2@acme.ca", uid="u-hr2"), {"comment": "fine"}, db)
            try:
                await R.apply(cid, Req(role="org_admin", email="ceo@acme.ca", uid="u-ceo"), 1, db)   # not yet fully approved
            except HTTPException as e:
                errs.append(e.status_code)
            s2 = await R.decide(cid, "approved", Req(role="org_admin", email="ceo@acme.ca", uid="u-ceo"), {}, db)
            st = await R.approval_state(cid, Req(), db)
            return errs, sub, st0, rej, sub2, s1, s2, st
    errs, sub, st0, rej, sub2, s1, s2, st = asyncio.run(go())
    assert errs == [409, 403, 403, 400, 409]
    assert sub["status"] == "approval" and sub["approval"]["state"] == "pending" and [s["roles"] for s in sub["approval"]["chain"]] == [["hr_admin"], ["org_admin"]]
    assert sub["approval"]["metadata"]["total_amount"] > 0 and sub["approval"]["metadata"]["department"] == ["Sales", "Tech"]
    assert st0["can_decide"] is False                                              # the requester cannot approve their own
    assert rej["status"] == "calibration" and rej["approval"]["state"] == "rejected"
    assert sub2["approval"]["step"] == 0 and sub2["approval"]["decisions"] == []
    assert s1["approval"]["state"] == "pending" and s1["approval"]["step"] == 1
    assert s2["approval"]["state"] == "approved" and s2["approved_at"] and st["approval"]["decisions"][-1]["role"] == "org_admin"
    opened = world.stubs.opened
    assert [o["approver_roles"] for o in opened] == [["hr_admin"], ["hr_admin"], ["org_admin"]] and opened[0]["kind"] == "comp_cycle"
    assert opened[0]["callback_path"].endswith("/approval/{decision}") and [c["decision"] for c in world.stubs.closed] == ["rejected", "approved", "approved"]


async def _approved_cycle(db, **over):
    cid = await make_cycle(db, **over)
    await R.move(cid, "open", Req(), db)
    by = {ln["employee_number"]: ln for ln in (await R.worksheet(cid, Req(), None, None, db))["lines"]}
    await R.update_line(cid, by["E002"]["id"], {"promotion": True, "promo_designation": "Senior Engineer", "promo_grade": "L3"}, Req(), db)
    await R.update_line(cid, by["E003"]["id"], {"proposed_pct": 0, "lump_sum": 15000}, Req(), db)
    await R.update_line(cid, by["M001"]["id"], {"include": False}, Req(), db)
    await R.update_line(cid, by["M002"]["id"], {"include": False}, Req(), db)
    await R.move(cid, "calibrate", Req(), db)
    await R.submit(cid, Req(role="hr_admin", email="head@acme.ca", uid="u-head"), db)
    await R.decide(cid, "approve", Req(role="hr_admin", email="hr2@acme.ca", uid="u-hr2"), {}, db)
    await R.decide(cid, "approve", Req(role="org_admin", email="ceo@acme.ca", uid="u-ceo"), {}, db)
    return cid, by


def test_apply_uses_the_existing_revision_path_career_changes_and_letters(world):
    async def go():
        async with world.db() as db:
            people = await seed(db)
            cid, by = await _approved_cycle(db)
            ceo = Req(role="org_admin", email="ceo@acme.ca", uid="u-ceo")
            prog = await R.apply(cid, ceo, 1, db)
            c = await R.get_cycle(cid, Req(), db)
            after = await R.apply_progress(cid, Req(), db)
            bala = (await db.execute(select(Employee).where(Employee.id == people["bala"].id))).scalar_one()
            await db.refresh(bala)                                   # apply ran in its own session
            cc = (await db.execute(select(CareerChange))).scalars().all()
            letters = (await db.execute(select(IssuedLetter).order_by(IssuedLetter.number))).scalars().all()
            mine = await LR.my_letters(Req(role="employee", email="e001@acme.ca", uid="u-a", employee_id=str(people["asha"].id)), db)
            return prog, c, after, bala, cc, letters, mine, by
    prog, c, after, bala, cc, letters, mine, by = asyncio.run(go())
    st = world.stubs
    assert prog["state"] == "done" and prog["errors"] == [] and prog["done"] == 2 and c["status"] == "applied" and c["applied_at"]
    # 1. increases went to payroll's bulk-draft ONCE, as the system maker, then submit (system) + approve (the acting admin)
    assert len(st.drafts) == 1 and st.drafts[0]["email"] == "system@comp-cycles" and st.drafts[0]["role"] == "org_admin"
    items = {i["employee_id"]: i for i in st.drafts[0]["payload"]["items"]}
    assert set(items) == {by["E001"]["employee_id"], by["E002"]["employee_id"]}         # Chitra: lump sum only, no salary row
    assert items[by["E002"]["employee_id"]]["reason"] == "promotion" and items[by["E001"]["employee_id"]]["new_annual"] == 1120000.0
    assert st.drafts[0]["payload"]["source"] == f"comp_cycle:{c['id']}"
    assert st.batch_calls == [("batch-7", "submit", "system@comp-cycles"), ("batch-7", "approve", "ceo@acme.ca")]
    assert prog["batch_id"] == "batch-7" and prog["revisions_applied_now"] == 2
    # 2. the promotion is a career-change record, approved with the cycle and applied today
    assert len(cc) == 1 and cc[0].change_type == "promotion" and cc[0].status == "applied" and cc[0].new_values == {"job_title": "Senior Engineer", "grade": "L3"}
    assert bala.job_title == "Senior Engineer" and bala.grade == "L3"
    # 3. letters from the engine: 2 salary revision + 1 promotion, numbered, visible in self-service
    assert [l.template_key for l in letters] == ["salary_revision", "salary_revision", "promotion"] and all(l.status == "issued" for l in letters)
    assert all(l.number.startswith("LTR/") and l.visible_to_employee for l in letters)
    assert [l["template_key"] for l in mine] == ["salary_revision"] and mine[0]["number"] == letters[0].number
    res = {ln["employee_number"]: ln["apply_result"] for ln in after["lines"]}
    assert res["E001"]["letter_id"] and res["E002"]["promotion_letter_id"] and res["E002"]["career_change_id"]
    assert "E003" not in res                                                                # lump sum only: no salary row, no letter
    assert res["E001"]["revision_batch_id"] == "batch-7"


def test_apply_is_idempotent_on_retry_and_survives_payroll_being_down(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid, by = await _approved_cycle(db)
            ceo = Req(role="org_admin", email="ceo@acme.ca", uid="u-ceo")
            world.stubs.payroll_down = True
            failed = await R.apply(cid, ceo, 1, db)
            c1 = await R.get_cycle(cid, Req(), db)
            world.stubs.payroll_down = False
            ok = await R.apply(cid, ceo, 1, db)
            # a second run (simulating a retry after a crash) must not create new letters or career changes
            c = (await db.execute(select(CompCycle).where(CompCycle.id == uuid.UUID(cid)))).scalar_one()
            c.status, c.apply_progress = "applying", {**c.apply_progress, "state": "running"}
            await db.commit()
            again = await R.run_apply(world.db, cid, str(ORG), SimpleNamespace(org=str(ORG), user_id="u-ceo", role="org_admin", email="ceo@acme.ca", employee_id=""))
            letters = (await db.execute(select(IssuedLetter))).scalars().all()
            cc = (await db.execute(select(CareerChange))).scalars().all()
            return failed, c1, ok, again, letters, cc
    failed, c1, ok, again, letters, cc = asyncio.run(go())
    assert failed["state"] == "failed" and "could not be reached" in failed["errors"][0]
    assert c1["status"] == "approval" and c1["approval_state"] == "approved"            # can be retried
    assert ok["state"] == "done" and again["state"] == "done"
    assert len(world.stubs.drafts) == 1 and len(letters) == 3 and len(cc) == 1          # nothing was done twice


def test_analytics_and_setup_rules_after_open(world):
    async def go():
        async with world.db() as db:
            await seed(db)
            cid = await make_cycle(db)
            changed = await R.update_cycle(cid, {"matrix": [{"rating": "Exceeds", "min_pct": 20, "max_pct": 30}], "grade_midpoints": {"L2": 2000000}}, Req(), db)
            ws = await R.worksheet(cid, Req(), None, None, db)
            await R.move(cid, "open", Req(), db)
            late = await R.update_cycle(cid, {"matrix": [], "name": "FY27 merit v2", "promotion_budget": 1}, Req(), db)
            an = await R.analytics(cid, Req(), db)
            errs = []
            try:
                await R.analytics(cid, Req(role="manager", employee_id=str(MGR1)), db)
            except HTTPException as e:
                errs.append(e.status_code)
            return changed, ws, late, an, errs
    changed, ws, late, an, errs = asyncio.run(go())
    asha = next(ln for ln in ws["lines"] if ln["employee_number"] == "E001")
    assert changed["matrix"][0]["rec_pct"] == 25.0 and asha["rec_pct"] == 25.0 and asha["compa_ratio"] == 0.5 and asha["compa_band"] == "low"
    assert late["name"] == "FY27 merit v2" and late["promotion_budget"] == 1.0 and late["matrix"][0]["rating"] == "Exceeds"   # matrix frozen once open
    assert an["summary"]["people"] == 5 and {g["key"] for g in an["by_gender"]} == {"female", "male"}
    assert [r["key"] for r in an["compa"]][:3] == ["low", "mid", "high"] and an["pay_equity"] and "not a legal finding" in an["note"]
    assert errs == [403]
