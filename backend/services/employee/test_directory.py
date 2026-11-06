"""People directory: an employee can look colleagues up — and sees only work facts.
Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest test_directory.py -q
"""
import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import masters_models  # noqa: F401
import models  # noqa: F401
from directory_routes import DIRECTORY_FIELDS
from routers import router
from shared.utils import identity

ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())

FORBIDDEN = {"annual_salary", "hourly_rate", "pay_type", "pay_frequency", "passport", "passport_masked",
             "bank_account", "bank_account_masked", "bank_account_last4", "bank_transit", "bank_institution",
             "sin", "sin_masked", "td1_federal_claim", "phone", "home_phone", "email", "address_line1", "address_line2",
             "city", "postal_code", "date_of_birth", "emergency_name", "emergency_phone", "td1_provincial_claim",
             "nominee_name", "nominee_phone", "father_name", "spouse_name", "marital_status", "gender"}


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        for oid, name in ((ORG, "Maple Directory"), (OTHER_ORG, "Other Co")):
            await conn.execute(sa.insert(identity.organizations).values(
                id=uuid.UUID(oid), name=name, code=name[:6].upper() + oid[:4], country="CA", province="ON",
                is_active=True))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(role="hr_admin", emp=None, org=ORG):
    h = {"X-Org-Id": org, "X-Role": role, "X-User-Id": str(uuid.uuid4())}
    if emp:
        h["X-Employee-Id"] = emp
    return h


def _add(client, org=ORG, **over):
    body = {"first_name": "Test", "last_name": "Person", "annual_salary": 900000, "phone": "9876543210",
            "email": "personal@gmail.example", "sin": "046454286", "bank_account": "1234567",
            "bank_transit": "12345", "bank_institution": "004", "address_line1": "12 Home Street", "city": "Toronto", **over}
    r = client.post("/employees", headers=H(org=org), json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def people(client):
    boss = _add(client, first_name="Lakshmi", last_name="Rao", job_title="Engineering Manager", department="Engineering",
                work_email="lakshmi@maple.example", work_location="Toronto")
    me = _add(client, first_name="Arjun", last_name="Nair", job_title="Engineer", department="Engineering",
              work_email="arjun@maple.example", manager_id=boss["id"], work_location="Vancouver")
    hr = _add(client, first_name="Divya", last_name="Menon", job_title="HR Partner", department="People",
              work_email="divya@maple.example")
    left = _add(client, first_name="Gone", last_name="Away")
    client.patch(f"/employees/{left['id']}", headers=H(), json={"status": "terminated"})
    _add(client, org=OTHER_ORG, first_name="Outsider", last_name="Person")
    return {"boss": boss, "me": me, "hr": hr, "left": left}


def test_employee_sees_colleagues_work_facts(client, people):
    r = client.get("/employees/directory", headers=H("employee", people["me"]["id"]))
    assert r.status_code == 200, r.text
    rows = {x["display_name"]: x for x in r.json()}
    assert {"Lakshmi Rao", "Arjun Nair", "Divya Menon"} <= set(rows)
    lak = rows["Lakshmi Rao"]
    assert lak["job_title"] == "Engineering Manager" and lak["department"] == "Engineering"
    assert lak["work_email"] == "lakshmi@maple.example" and lak["work_location"] == "Toronto"
    assert lak["initials"] == "LR"
    assert rows["Arjun Nair"]["manager_name"] == "Lakshmi Rao" and rows["Arjun Nair"]["is_self"] is True
    assert lak["is_self"] is False


def test_directory_never_carries_pay_ids_or_personal_data(client, people):
    rows = client.get("/employees/directory", headers=H("employee", people["me"]["id"])).json()
    for row in rows:
        assert set(row) == set(DIRECTORY_FIELDS)
        assert not (set(row) & FORBIDDEN)
    blob = str(rows)
    for secret in ("900000", "9876543210", "personal@gmail.example", "ABCP", "50100123456789", "HDFC0001234",
                   "12 Home Street", "Mysuru", "6789"):
        assert secret not in blob, secret


def test_leavers_and_other_orgs_are_not_listed(client, people):
    names = {x["display_name"] for x in client.get("/employees/directory", headers=H("employee", people["me"]["id"])).json()}
    assert "Gone Away" not in names and "Outsider Person" not in names


def test_search_and_department_filter(client, people):
    h = H("employee", people["me"]["id"])
    assert [x["display_name"] for x in client.get("/employees/directory?q=hr partner", headers=h).json()] == ["Divya Menon"]
    eng = {x["display_name"] for x in client.get("/employees/directory?department=Engineering", headers=h).json()}
    assert eng == {"Lakshmi Rao", "Arjun Nair"}


def test_every_role_may_read_it_but_not_anonymous(client, people):
    for role in ("employee", "manager", "hr_admin", "payroll_admin", "org_admin"):
        assert client.get("/employees/directory", headers=H(role)).status_code == 200, role
    assert client.get("/employees/directory", headers={"X-Org-Id": ORG}).status_code == 401
    assert client.get("/employees/directory", headers={"X-Role": "employee"}).status_code == 401


def test_full_list_stays_scoped_for_employee(client, people):
    """The directory does not widen GET /employees: an employee still gets only their own full record."""
    rows = client.get("/employees", headers=H("employee", people["me"]["id"])).json()
    assert [r["id"] for r in rows] == [people["me"]["id"]]
    assert client.get(f"/employees/{people['boss']['id']}", headers=H("employee", people["me"]["id"])).status_code == 404
