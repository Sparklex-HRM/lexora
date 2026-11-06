"""Employee 360 profile (stream U1): the aggregated read, number-or-uuid lookup,
tenure, and who sees which section.
Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest test_profile.py -q
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import database
import masters_models  # noqa: F401
import models  # noqa: F401
import career_models  # noqa: F401
import comp_models  # noqa: F401
import exit_models  # noqa: F401
import letters_models  # noqa: F401
from career_common import EXTRA_COLUMNS
from models import Employee
from profile_routers import _is_uuid, router as profile_router, tenure
from routers import router
from shared.utils import identity

ORG = str(uuid.uuid4())


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        for col, ddl in EXTRA_COLUMNS:                    # SQLite has no ADD COLUMN IF NOT EXISTS
            if col not in Employee.__table__.columns:
                await conn.execute(text(f"ALTER TABLE employees ADD COLUMN {col} {ddl}"))
        await conn.execute(sa.insert(identity.organizations).values(
            id=uuid.UUID(ORG), name="Maple Logistics", code="MAPLE", country="CA", province="ON", is_active=True))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(router)
app.include_router(profile_router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(role="hr_admin", emp=None, email=None):
    h = {"X-Org-Id": ORG, "X-Role": role, "X-User-Id": str(uuid.uuid4())}
    if emp:
        h["X-Employee-Id"] = emp
    if email:
        h["X-User-Email"] = email
    return h


def _add(client, **over):
    body = {"first_name": "Test", "last_name": "Person", "annual_salary": 600000, "hire_date": "2025-04-01", **over}
    r = client.post("/employees", headers=H(), json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def team(client):
    """Manoj manages Asha; Hema is HR and reports to nobody."""
    hema = _add(client, employee_number="EMP-0001", first_name="Hema", last_name="Rao", job_title="HR Head")
    manoj = _add(client, employee_number="EMP-0003", first_name="Manoj", last_name="Iyer", job_title="Sales Manager")
    asha = _add(client, employee_number="EMP-0004", first_name="Asha", last_name="Rao", manager_id=manoj["id"],
                job_title="Sales Executive", department="Sales", email="asha@maple.example", sin="046454286")
    return {"hema": hema, "manoj": manoj, "asha": asha}


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_tenure_counts_whole_years_and_months():
    assert tenure(date(2025, 4, 1), date(2026, 10, 13)) == {"years": 1, "months": 6, "days": 560, "text": "1 y 6 m"}
    assert tenure(date(2026, 8, 1), date(2026, 10, 12))["text"] == "2 m"
    assert tenure(date(2026, 10, 1), date(2026, 10, 12))["text"] == "11 d"
    assert tenure(date(2026, 12, 1), date(2026, 10, 12))["text"].startswith("Joins ")
    assert tenure(None)["text"] is None


def test_uuid_detection():
    assert _is_uuid(str(uuid.uuid4())) and not _is_uuid("EMP-0004") and not _is_uuid("")


# ── the aggregated read ──────────────────────────────────────────────────────

def test_hr_reads_by_number_and_by_uuid_with_every_section(client, team):
    by_number = client.get("/employees/emp-0004/profile", headers=H())          # case-insensitive number
    by_uuid = client.get(f"/employees/{team['asha']['id']}/profile", headers=H())
    assert by_number.status_code == 200 and by_uuid.status_code == 200, by_number.text
    p = by_number.json()
    assert p["employee"]["id"] == by_uuid.json()["employee"]["id"] == team["asha"]["id"]
    assert p["employee"]["employee_number"] == "EMP-0004"
    assert p["manager"]["employee_number"] == "EMP-0003" and p["manager_chain"][0]["name"] == "Manoj Iyer"
    assert p["reports"] == []
    assert p["permissions"]["scope"] == "all" and p["permissions"]["can_hr_write"] and p["permissions"]["can_see_pay"]
    assert p["compensation"]["current"]["annual_amount"] == 600000 and p["compensation"]["current"]["currency"] == "CAD"
    assert p["career"]["probation_history"] == [] and p["career_changes"] == [] and p["letters"] == []
    assert p["letters_count"] == 0 and p["documents_count"] == 0 and p["assets"] == []
    assert p["tenure"]["years"] >= 1 and p["tenure"]["text"]
    # ordered by employee number: 0001 → 0003 → 0004
    assert p["prev"]["employee_number"] == "EMP-0003" and p["next"] is None
    first = client.get("/employees/EMP-0001/profile", headers=H()).json()
    assert first["prev"] is None and first["next"]["employee_number"] == "EMP-0003"


def test_identifiers_are_masked_in_the_profile(client, team):
    p = client.get("/employees/EMP-0004/profile", headers=H("org_admin")).json()
    e = p["employee"]
    assert e["masked"] is True
    assert e["sin_masked"] and "046454286" not in e["sin_masked"] and e["sin_masked"].endswith("286")
    assert "sin" not in e and "bank_account" not in e        # only the *_masked keys travel


def test_manager_sees_direct_report_without_pay_and_not_others(client, team):
    me = H("manager", emp=team["manoj"]["id"])
    r = client.get("/employees/EMP-0004/profile", headers=me)
    assert r.status_code == 200
    p = r.json()
    assert p["permissions"]["scope"] == "team" and not p["permissions"]["can_see_pay"]
    assert p["compensation"] is None and p["career_changes"] is None and p["letters"] is None
    assert p["assets"] == [] and p["documents_count"] is None and p["logins"] is None
    assert p["permissions"]["can_start_exit"] and not p["permissions"]["can_edit"]
    assert client.get("/employees/EMP-0001/profile", headers=me).status_code == 404       # not a report
    own = client.get("/employees/EMP-0003/profile", headers=me).json()                     # self
    assert own["permissions"]["is_self"] and own["reports"][0]["employee_number"] == "EMP-0004"
    assert own["prev"] is None and own["next"]["employee_number"] == "EMP-0004"           # neighbours inside the team only


def test_accountant_sees_a_work_card_only_on_another_profile(client, team):
    # go-live live matrix 2026-10-25 (High LEAK): $.employee carried annual_salary + bank details for the accountant
    p = client.get("/employees/EMP-0004/profile", headers=H("accountant", emp=team["hema"]["id"])).json()
    emp = p["employee"]
    assert emp.get("names_only") is True and emp.get("masked") is True
    for k in ("annual_salary", "bank_transit", "bank_institution", "sin_masked", "date_of_birth", "personal_email", "phone"):
        assert not emp.get(k), k
    assert p["compensation"] is None


def test_employee_sees_only_self(client, team):
    me = H("employee", emp=team["asha"]["id"], email="asha@maple.example")
    r = client.get("/employees/EMP-0004/profile", headers=me)
    assert r.status_code == 200
    p = r.json()
    assert p["permissions"] == {"scope": "self", "is_self": True, "role": "employee", "can_edit": False,
                                "can_hr_write": False, "can_see_pay": True, "can_see_assets": True,
                                "can_see_documents": True, "can_manage_logins": False, "can_start_exit": False}
    assert p["compensation"]["current"]["annual_amount"] == 600000      # own pay is visible
    assert p["career_changes"] == [] and p["letters"] == [] and p["assets"] == []
    assert p["prev"] is None and p["next"] is None
    assert client.get("/employees/EMP-0001/profile", headers=me).status_code == 404
    assert client.get(f"/employees/{team['hema']['id']}/profile", headers=me).status_code == 404


def test_unknown_number_or_id_is_404(client, team):
    assert client.get("/employees/EMP-9999/profile", headers=H()).status_code == 404
    assert client.get(f"/employees/{uuid.uuid4()}/profile", headers=H()).status_code == 404
    other_org = {**H(), "X-Org-Id": str(uuid.uuid4())}
    assert client.get("/employees/EMP-0004/profile", headers=other_org).status_code == 404
