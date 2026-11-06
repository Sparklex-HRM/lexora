"""Global search — employee service (/employees/search): role scope, directory-only fields,
phone last-4 only inside the full-record scope, org isolation, HR-only hiring, own letters / exits.
Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest test_search.py -q
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import ess_docs_models  # noqa: F401
import ess_requests_models  # noqa: F401
import exit_models  # noqa: F401
import hiring_models  # noqa: F401
import letters_models  # noqa: F401
import masters_models  # noqa: F401
import models  # noqa: F401
from routers import router
from search_routes import router as search_router
from shared.utils import identity

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        for oid, name in ((ORG, "Maple Search"), (OTHER, "Other Search")):
            await conn.execute(sa.insert(identity.organizations).values(
                id=uuid.UUID(oid), name=name, code=name[:6].upper() + oid[:4], country="CA", province="ON", is_active=True))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(search_router)
app.include_router(router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(role="hr_admin", emp=None, org=ORG, modules=None):
    h = {"X-Org-Id": org, "X-Role": role, "X-User-Id": str(uuid.uuid4())}
    if emp:
        h["X-Employee-Id"] = emp
    if modules is not None:
        h["X-Modules"] = ",".join(modules)
    return h


def _add(client, org=ORG, **over):
    body = {"first_name": "Test", "last_name": "Person", "annual_salary": 90000, "phone": "4165550000",
            **over}
    r = client.post("/employees", headers=H(org=org), json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def people(client):
    boss = _add(client, first_name="Lakshmi", last_name="Rao", job_title="Engineering Manager", department="Engineering",
                work_email="lakshmi@maple.example", phone="9000011111")
    priya = _add(client, first_name="Priya", last_name="Menon", job_title="Engineer", department="Engineering",
                 work_email="priya@maple.example", manager_id=boss["id"], phone="9000022222", work_location="Vancouver")
    ravi = _add(client, first_name="Ravi", last_name="Kumar", job_title="Accountant", department="Finance",
                work_email="ravi@maple.example", phone="9000033333")
    _add(client, org=OTHER, first_name="Priya", last_name="Outsider", phone="9000044444")
    return {"boss": boss, "priya": priya, "ravi": ravi}


def _hits(client, q, h, types=None):
    url = f"/employees/search?q={q}" + (f"&types={types}" if types else "")
    r = client.get(url, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["hits"]


def test_every_role_finds_colleagues_by_name_number_department(client, people):
    for role, emp in (("employee", people["ravi"]["id"]), ("manager", people["boss"]["id"]), ("hr_admin", None)):
        hits = _hits(client, "priya", H(role, emp), "employee")
        assert [h["title"] for h in hits] == ["Priya Menon"], role            # never the other org's Priya
    num = people["priya"]["employee_number"]
    assert _hits(client, num, H("employee", people["ravi"]["id"]), "employee")[0]["score"] == 100
    assert {h["title"] for h in _hits(client, "engineering", H("employee", people["ravi"]["id"]), "employee")} == \
        {"Priya Menon", "Lakshmi Rao"}


def test_hits_carry_no_salary_or_personal_data(client, people):
    blob = str(_hits(client, "priya", H("employee", people["ravi"]["id"]), "employee"))
    for secret in ("90000", "9000022222", "2222"):
        assert secret not in blob, secret


def test_phone_last4_only_inside_full_record_scope(client, people):
    # HR: anyone's phone; employee: only their own; manager: self + reports
    assert [h["title"] for h in _hits(client, "2222", H("hr_admin"), "employee")] == ["Priya Menon"]
    assert _hits(client, "2222", H("employee", people["ravi"]["id"]), "employee") == []
    assert [h["title"] for h in _hits(client, "3333", H("employee", people["ravi"]["id"]), "employee")] == ["Ravi Kumar"]
    assert [h["title"] for h in _hits(client, "2222", H("manager", people["boss"]["id"]), "employee")] == ["Priya Menon"]
    assert _hits(client, "3333", H("manager", people["boss"]["id"]), "employee") == []


def test_profile_link_only_in_scope(client, people):
    h = _hits(client, "priya", H("employee", people["ravi"]["id"]), "employee")[0]
    assert h["url"].startswith("/employees?hl=")                                  # directory, not the profile
    h = _hits(client, "priya", H("manager", people["boss"]["id"]), "employee")[0]
    assert h["url"] == f"/employees/{people['priya']['id']}"
    # accountant reads org-wide server-side but has no profile screen → directory card
    assert _hits(client, "priya", H("accountant"), "employee")[0]["url"].startswith("/employees?hl=")


def test_hiring_is_hr_only_and_needs_the_talent_module(client, people):
    import asyncio
    from database import SessionLocal
    from hiring_models import HireCandidate

    async def seed():
        async with SessionLocal() as s:
            s.add(HireCandidate(org_id=uuid.UUID(ORG), first_name="Kiran", last_name="Desai", email="kiran@cand.example",
                                current_title="Data Analyst", is_active=True))
            await s.commit()
    asyncio.run(seed())
    assert [h["title"] for h in _hits(client, "kiran", H("hr_admin"), "candidate")] == ["Kiran Desai"]
    assert _hits(client, "kiran", H("manager", people["boss"]["id"]), "candidate") == []
    assert _hits(client, "kiran", H("employee", people["ravi"]["id"]), "candidate") == []
    assert _hits(client, "kiran", H("hr_admin", modules=["core", "payroll"]), "candidate") == []   # no talent SKU


def test_letters_and_exits_own_only_for_employees(client, people):
    import asyncio
    from database import SessionLocal
    from exit_models import ExitCase
    from letters_models import IssuedLetter

    async def seed():
        async with SessionLocal() as s:
            for who in ("priya", "ravi"):
                e = people[who]
                s.add(IssuedLetter(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(e["id"]), template_key="experience",
                                   template_name="Experience letter", number=f"LTR-{who.upper()}", status="issued",
                                   visible_to_employee=True, letter_date=date(2026, 10, 1),
                                   body_snapshot="x", pdf=b"%PDF", sha256="0" * 64))
                s.add(ExitCase(org_id=uuid.UUID(ORG), case_number=f"EX-{who.upper()}", employee_id=uuid.UUID(e["id"]),
                               employee_name=f"{e['first_name']} {e['last_name']}", employee_number=e["employee_number"],
                               manager_id=uuid.UUID(e["manager_id"]) if e.get("manager_id") else None,
                               exit_type="resignation", initiated_by="employee", submitted_on=date(2026, 10, 1), status="submitted"))
            await s.commit()
    asyncio.run(seed())
    mine = _hits(client, "experience", H("employee", people["ravi"]["id"]), "letter")
    assert [h["subtitle"].split(" · ")[0] for h in mine] == ["LTR-RAVI"]
    assert len(_hits(client, "experience", H("hr_admin"), "letter")) == 2
    assert [h["title"] for h in _hits(client, "EX-", H("employee", people["ravi"]["id"]), "exit")] == ["Exit EX-RAVI — Ravi Kumar"]
    assert {h["title"] for h in _hits(client, "EX-", H("manager", people["boss"]["id"]), "exit")} == {"Exit EX-PRIYA — Priya Menon"}
    assert len(_hits(client, "EX-", H("hr_admin"), "exit")) == 2


def test_no_role_or_superadmin_gets_nothing(client, people):
    assert client.get("/employees/search?q=priya", headers={"X-Org-Id": ORG}).status_code == 401
    assert _hits(client, "priya", H("superadmin")) == []
    assert _hits(client, "", H("hr_admin")) == []


def test_assets_own_team_office(client, people):
    import asyncio
    from database import SessionLocal
    from exit_models import EmployeeAsset

    async def seed():
        async with SessionLocal() as s:
            s.add(EmployeeAsset(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(people["priya"]["id"]), item="Laptop", tag="LT-0099"))
            await s.commit()
    asyncio.run(seed())
    assert [h["title"] for h in _hits(client, "LT-0099", H("employee", people["priya"]["id"]), "asset")] == ["Laptop (LT-0099)"]
    assert _hits(client, "LT-0099", H("employee", people["ravi"]["id"]), "asset") == []
    assert len(_hits(client, "LT-0099", H("manager", people["boss"]["id"]), "asset")) == 1
    assert len(_hits(client, "laptop", H("payroll_admin"), "asset")) == 1
