"""Fixed-term / contract employees (2026-10-22): buckets, alerts, term actions, role checks.

Run:  cd backend/services/employee && PYTHONPATH="../..;." python -m pytest test_fixed_term.py -q
"""
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta

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


import contracts_logic as L  # noqa: E402
import contracts_routers as CT  # noqa: E402
from contracts_models import ContractTerm  # noqa: E402
from models import Employee  # noqa: E402

TODAY = date(2026, 10, 22)
ORG = uuid.uuid4()
MGR = uuid.uuid4()


class Req:
    def __init__(self, role="hr_admin", email="hr@acme.ca", uid="u-hr", employee_id=""):
        self.headers = {"x-org-id": str(ORG), "x-role": role, "x-user-email": email, "x-user-id": uid,
                        **({"x-employee-id": employee_id} if employee_id else {})}


# ── pure ─────────────────────────────────────────────────────────────────────
def test_buckets_cover_every_range():
    d = TODAY
    assert L.bucket(None, d) == ("no_end_date", None)
    assert L.bucket(d - timedelta(days=1), d) == ("expired", -1)
    assert L.bucket(d, d) == ("7", 0) and L.bucket(d + timedelta(days=7), d)[0] == "7"
    assert L.bucket(d + timedelta(days=8), d)[0] == "30" and L.bucket(d + timedelta(days=30), d)[0] == "30"
    assert L.bucket(d + timedelta(days=60), d)[0] == "60" and L.bucket(d + timedelta(days=90), d)[0] == "90"
    assert L.bucket(d + timedelta(days=91), d)[0] == "later"


def test_due_alert_picks_tightest_unsent_threshold_once():
    end = TODAY + timedelta(days=25)
    assert L.due_alert(end, TODAY, set()) == 30
    assert L.due_alert(end, TODAY, {30}) is None
    assert L.due_alert(end, TODAY, {60}) == 30
    assert L.due_alert(TODAY + timedelta(days=100), TODAY, set()) is None
    assert L.due_alert(TODAY - timedelta(days=3), TODAY, {60, 30, 7}) == 0


def test_term_validation_and_renewal_window():
    assert L.validate_term(date(2026, 1, 1), None, None) == ["Enter the contract end date"]
    assert "before its start" in L.validate_term(date(2026, 5, 1), date(2026, 4, 1), None)[0]
    assert "joining date" in L.validate_term(date(2026, 1, 1), date(2026, 12, 31), date(2026, 2, 1))[0]
    assert L.renewal_window(date(2026, 10, 31), date(2027, 10, 31)) == (date(2026, 11, 1), date(2027, 10, 31))
    with pytest.raises(ValueError):
        L.renewal_window(date(2026, 10, 31), date(2026, 10, 31))
    assert L.notice_date(date(2026, 12, 31), 30) == date(2026, 12, 1) and L.notice_date(date(2026, 12, 31), None) is None
    assert L.is_term_bound("fixed_term") and L.is_term_bound("Contract") and not L.is_term_bound("full_time")


# ── database-backed ──────────────────────────────────────────────────────────
@pytest.fixture
def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Employee.metadata.create_all(c, tables=[Employee.__table__, ContractTerm.__table__]))
    asyncio.run(make())

    async def _today(org):
        return TODAY

    async def _no_audit(*a, **kw):
        return True

    sent = []

    async def _notify(org, **kw):
        sent.append(kw)

    async def _exit(request, e, reason, lwd):
        return True, "Exit case started (EX-TEST)"

    monkeypatch.setattr(CT, "org_today", _today)
    monkeypatch.setattr(CT, "record_audit", _no_audit)
    monkeypatch.setattr(CT, "_start_exit", _exit)
    import shared.utils.notify as N
    monkeypatch.setattr(N, "notify", _notify)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    sm.sent = sent
    return sm


def _emp(**kw):
    base = dict(id=uuid.uuid4(), org_id=ORG, employee_number="FT01", first_name="Kiran", last_name="Shetty",
                email="kiran@acme.example", status="active", employment_type="fixed_term", address_country="CA",
                job_title="Analyst", department="Ops", hire_date=date(2026, 1, 1), pay_type="salary",
                annual_salary=600000, pay_frequency="monthly", is_active=True, province_of_employment="ON",
                standard_hours_per_week=40, manager_id=MGR, manager_name="Mona Lead")
    return Employee(**{**base, **kw})


def run(c):
    return asyncio.run(c)


def test_set_list_renew_convert_flow(world):
    async def go():
        async with world() as db:
            e = _emp()
            db.add(e)
            db.add(_emp(id=uuid.uuid4(), employee_number="P01", employment_type="full_time"))
            await db.commit()
            r = await CT.set_term(str(e.id), CT.TermIn(start_date="2026-01-01", end_date="2026-11-10", notice_days=15), Req(), db)
            assert r["bucket"] == "30" and r["days_left"] == 19 and r["notice_by"] == "2026-10-26"
            lst = await CT.list_fixed_term(Req(), db=db)
            assert [x["employee_number"] for x in lst["rows"]] == ["FT01"] and lst["counts"]["30"] == 1
            # manager sees own report, a stranger manager sees nothing, employee is refused
            mine = await CT.list_fixed_term(Req(role="manager", employee_id=str(MGR)), db=db)
            assert len(mine["rows"]) == 1 and mine["can_act"] is False
            other = await CT.list_fixed_term(Req(role="manager", employee_id=str(uuid.uuid4())), db=db)
            assert other["rows"] == []
            with pytest.raises(HTTPException) as ex:
                await CT.list_fixed_term(Req(role="employee"), db=db)
            assert ex.value.status_code == 403
            with pytest.raises(HTTPException) as ex:
                await CT.list_fixed_term(Req(role="payroll_admin"), db=db)
            assert ex.value.status_code == 403
            # manager recommends; cannot renew
            await CT.recommend(str(e.id), CT.RecommendIn(recommendation="renew", reason="Project extended", new_end_date="2027-05-10"),
                               Req(role="manager", employee_id=str(MGR)), db)
            with pytest.raises(HTTPException) as ex:
                await CT.renew(str(e.id), CT.RenewIn(new_end_date="2027-05-10", reason="x", issue_letter=False), Req(role="manager", employee_id=str(MGR)), db)
            assert ex.value.status_code == 403
            # HR renews: next term starts the day after the old end
            r = await CT.renew(str(e.id), CT.RenewIn(new_end_date="2027-05-10", reason="Project extended", issue_letter=False), Req(), db)
            assert r["term_no"] == 2 and r["contract_start_date"] == "2026-11-11"
            with pytest.raises(HTTPException):
                await CT.renew(str(e.id), CT.RenewIn(new_end_date="2027-01-01", reason="back", issue_letter=False), Req(), db)
            lst = await CT.list_fixed_term(Req(), db=db)
            assert lst["rows"][0]["renewals"] == 1 and lst["rows"][0]["recommendation"] == "renew"
            # convert to permanent
            r = await CT.convert(str(e.id), CT.ConvertIn(effective_date="2026-12-01", reason="Strong performer", issue_letter=False), Req(), db)
            assert r["employment_type"] == "full_time"
            await db.refresh(e)
            assert e.contract_end_date is None and e.employment_type == "full_time"
            assert (await CT.list_fixed_term(Req(), db=db))["rows"] == []
            h = await CT.history(str(e.id), Req(), db)
            assert [x["action"] for x in h["rows"]] == ["start", "recommend", "renew", "convert"]
    run(go())


def test_end_hands_over_to_exit_and_alerts_once(world):
    async def go():
        async with world() as db:
            e = _emp(contract_start_date=date(2026, 1, 1), contract_end_date=TODAY + timedelta(days=5))
            db.add(e)
            await db.commit()
            assert await CT.scan_org(db, str(ORG)) == 1
            assert await CT.scan_org(db, str(ORG)) == 0          # same threshold never twice
            assert len(world.sent) == 2                          # HR roles + the manager
            r = await CT.end_contract(str(e.id), CT.EndIn(reason="Project closed"), Req(), db)
            assert r["exit_started"] and r["last_working_date"] == (TODAY + timedelta(days=5)).isoformat()
            hist = (await db.execute(select(ContractTerm).where(ContractTerm.employee_id == e.id))).scalars().all()
            assert {t.action for t in hist} == {"start", "alert", "end"}        # start = term from the employee record
            # employee may read own history but not alert rows; a stranger may not
            own = await CT.history(str(e.id), Req(role="employee", email="kiran@acme.example", employee_id=str(e.id)), db)
            assert [x["action"] for x in own["rows"]] == ["start", "end"]
            with pytest.raises(HTTPException):
                await CT.history(str(e.id), Req(role="employee", email="x@acme.ca", employee_id=str(uuid.uuid4())), db)
    run(go())


def test_set_term_refuses_permanent_and_bad_dates(world):
    async def go():
        async with world() as db:
            e = _emp(employment_type="full_time")
            db.add(e)
            await db.commit()
            with pytest.raises(HTTPException) as ex:
                await CT.set_term(str(e.id), CT.TermIn(end_date="2026-12-31"), Req(), db)
            assert ex.value.status_code == 422
            with pytest.raises(HTTPException) as ex:
                await CT.set_term(str(e.id), CT.TermIn(start_date="2026-06-01", end_date="2026-05-01", employment_type="fixed_term"), Req(), db)
            assert ex.value.status_code == 422
            r = await CT.set_term(str(e.id), CT.TermIn(end_date="2026-12-31", employment_type="contract"), Req(), db)
            assert r["employment_type"] == "contract" and r["contract_start_date"] == "2026-01-01"
            with pytest.raises(HTTPException) as ex:
                await CT.set_term(str(e.id), CT.TermIn(end_date="2026-12-31"), Req(role="payroll_admin"), db)
            assert ex.value.status_code == 403
    run(go())
