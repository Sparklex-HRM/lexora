"""Employee ↔ legal entity (stream D4): default entity on create, entity view filter,
transfer with effective date (immediate + scheduled), history + CRA payroll-account change flags,
entity_id not PATCHable, RBAC.
Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest test_entities.py -q
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import entities_models  # noqa: F401
import career_models  # noqa: F401
import masters_models  # noqa: F401
import models  # noqa: F401
from routers import router
from shared import legal_entities as LE
from shared.utils import audit, identity  # noqa: F401

ORG = str(uuid.uuid4())
ENT_A, ENT_B = uuid.uuid4(), uuid.uuid4()
OTHER_ORG = str(uuid.uuid4())


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users, LE.legal_entities):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        await conn.execute(sa.insert(identity.organizations).values(
            id=uuid.UUID(ORG), name="Maple Logistics", code="MAPLE", country="CA", province="ON", is_active=True))
        await conn.execute(sa.insert(LE.legal_entities).values(
            id=ENT_A, org_id=uuid.UUID(ORG), name="Maple Logistics Inc.", short_code="MLI", is_default=True, is_active=True,
            business_number="123456782", payroll_account="123456782RP0001", province="ON"))
        await conn.execute(sa.insert(LE.legal_entities).values(
            id=ENT_B, org_id=uuid.UUID(ORG), name="Maple Freight Ltd.", short_code="MFL", is_default=False, is_active=True,
            business_number="987654324", payroll_account="987654324RP0001", province="BC",
            numbering_prefix="MF-"))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(role="hr_admin", org=ORG, **extra):
    h = {"X-Org-Id": org, "X-Role": role, "X-User-Id": str(uuid.uuid4()), "X-User-Email": f"{role}@maple.example"}
    h.update(extra)
    return h


def _emp(client, first, number, entity_id=None):
    body = {"first_name": first, "last_name": "Kumar", "employee_number": number, "address_country": "CA",
            "province_of_employment": "ON", "hire_date": "2025-04-01", "status": "active", "annual_salary": 600000}
    if entity_id:
        body["entity_id"] = entity_id
    r = client.post("/employees", json=body, headers=H())
    assert r.status_code == 201, r.text
    return r.json()


def test_request_entity_id_parsing():
    class R:
        def __init__(self, q=None, h=None):
            self.query_params, self.headers = q or {}, h or {}
    assert LE.request_entity_id(R()) == ""
    assert LE.request_entity_id(R(h={"x-view-entity": "all"})) == ""
    assert LE.request_entity_id(R(h={"x-view-entity": "garbage"})) == ""
    assert LE.request_entity_id(R(h={"x-view-entity": str(ENT_B)})) == str(ENT_B)
    assert LE.request_entity_id(R(q={"entity_id": str(ENT_A)}, h={"x-view-entity": str(ENT_B)})) == str(ENT_A)


def test_create_defaults_to_default_entity_and_accepts_a_choice(client):
    LE.invalidate(ORG)
    a = _emp(client, "Ravi", "KT-0001")
    assert a["entity_id"] == str(ENT_A)
    b = _emp(client, "Meena", "KT-0002", entity_id=str(ENT_B))
    assert b["entity_id"] == str(ENT_B)
    r = client.post("/employees", json={"first_name": "X", "last_name": "Y", "address_country": "CA",
                                        "province_of_employment": "ON", "entity_id": str(uuid.uuid4())}, headers=H())
    assert r.status_code == 422
    # entity_id is not PATCHable — only the transfer path changes it
    r = client.patch(f"/employees/{a['id']}", json={"entity_id": str(ENT_B), "city": "Bengaluru"}, headers=H())
    assert r.status_code == 200 and r.json()["entity_id"] == str(ENT_A) and r.json()["city"] == "Bengaluru"


def test_entity_view_filter_on_list(client):
    all_rows = client.get("/employees", headers=H()).json()
    assert {e["employee_number"] for e in all_rows} >= {"KT-0001", "KT-0002"}
    only_b = client.get("/employees", headers=H(**{"X-View-Entity": str(ENT_B)})).json()
    assert [e["employee_number"] for e in only_b] == ["KT-0002"]
    only_a = client.get("/employees", params={"entity_id": str(ENT_A)}, headers=H()).json()
    assert {e["employee_number"] for e in only_a} == {"KT-0001"}
    r = client.get("/employees/entities", headers=H())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["default_id"] == str(ENT_A)
    counts = {x["short_code"]: x["employee_count"] for x in d["entities"]}
    assert counts == {"MLI": 1, "MFL": 1}


def test_immediate_transfer_closes_old_spell(client):
    a = next(e for e in client.get("/employees", headers=H()).json() if e["employee_number"] == "KT-0001")
    assert client.post(f"/employees/{a['id']}/entity-transfer", headers=H("manager"),
                       json={"entity_id": str(ENT_B), "effective_date": "2026-10-01"}).status_code == 403
    assert client.post(f"/employees/{a['id']}/entity-transfer", headers=H(),
                       json={"entity_id": str(ENT_A), "effective_date": "2026-10-01"}).status_code == 400   # same entity
    r = client.post(f"/employees/{a['id']}/entity-transfer", headers=H(),
                    json={"entity_id": str(ENT_B), "effective_date": "2026-10-01", "reason": "Group restructuring",
                          "transfer_type": "business_transfer", "carry_leave": False})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["applied"] and d["entity_id"] == str(ENT_B)
    e = client.get(f"/employees/{a['id']}", headers=H()).json()
    assert e["entity_id"] == str(ENT_B)
    h = client.get(f"/employees/{a['id']}/entity-history", headers=H()).json()
    assert h["entity_name"] == "Maple Freight Ltd."
    spells = h["history"]
    assert [s["status"] for s in spells] == ["current", "closed"]
    old = spells[1]
    assert old["entity_id"] == str(ENT_A) and old["to_date"] == "2026-09-30"
    assert old["status"] == "closed" and old["from_date"] == "2025-04-01"
    assert spells[0]["from_date"] == "2026-10-01" and spells[0]["career_change_id"]

    # the career timeline (career_changes) carries the record as applied
    async def change():
        async with database.SessionLocal() as s:
            return (await s.execute(sa.select(career_models.CareerChange).where(
                career_models.CareerChange.id == uuid.UUID(spells[0]["career_change_id"])))).scalar_one()
    c = client.portal.call(change)
    assert c.change_type == "entity_transfer" and c.status == "applied" and c.applied_at is not None
    assert c.new_values["entity_name"] == "Maple Freight Ltd." and c.old_values["entity_name"] == "Maple Logistics Inc."
    # 2026-10-24: transfer terms recorded on the career change (defaults fill what was not sent)
    assert c.new_values["terms"] == {"transfer_type": "business_transfer", "service_continuity": True,
                                     "carry_leave": False, "final_pay_under_old_entity": False}
    counts = {x["short_code"]: x["employee_count"] for x in client.get("/employees/entities", headers=H()).json()["entities"]}
    assert counts == {"MLI": 0, "MFL": 2}


def test_scheduled_transfer_applies_on_its_date(client):
    b = next(e for e in client.get("/employees", headers=H()).json() if e["employee_number"] == "KT-0002")
    future = (date.today() + timedelta(days=40)).isoformat()
    r = client.post(f"/employees/{b['id']}/entity-transfer", headers=H(), json={"entity_id": str(ENT_A), "effective_date": future})
    assert r.status_code == 201 and r.json()["applied"] is False and r.json()["entity_id"] == str(ENT_B)
    # second scheduled → 409
    assert client.post(f"/employees/{b['id']}/entity-transfer", headers=H(),
                       json={"entity_id": str(ENT_A), "effective_date": future}).status_code == 409
    t = client.get("/employees/entities/transfers", headers=H()).json()["transfers"]
    assert any(x["employee_number"] == "KT-0002" and x["status"] == "scheduled" for x in t)
    assert client.get("/employees/entities/transfers", headers=H("employee")).status_code == 403

    # time travels: the date arrives → apply-due moves the person
    async def backdate():
        async with database.SessionLocal() as s:
            await s.execute(sa.update(entities_models.EmployeeEntityHistory)
                            .where(entities_models.EmployeeEntityHistory.status == "scheduled")
                            .values(from_date=date.today() - timedelta(days=1)))
            await s.commit()
    client.portal.call(backdate)
    assert client.post("/employees/entities/apply-due", headers=H()).json()["applied"] == 1
    assert client.get(f"/employees/{b['id']}", headers=H()).json()["entity_id"] == str(ENT_A)
    h = client.get(f"/employees/{b['id']}/entity-history", headers=H()).json()["history"]
    assert h[0]["status"] == "current" and h[0]["entity_id"] == str(ENT_A) and h[1]["status"] == "closed"


def test_other_org_cannot_see_or_transfer(client):
    a = next(e for e in client.get("/employees", headers=H()).json() if e["employee_number"] == "KT-0001")
    assert client.get(f"/employees/{a['id']}/entity-history", headers=H(org=OTHER_ORG)).status_code == 404
    assert client.post(f"/employees/{a['id']}/entity-transfer", headers=H(org=OTHER_ORG),
                       json={"entity_id": str(ENT_A), "effective_date": "2026-10-01"}).status_code == 404


def test_auto_number_uses_entity_prefix_when_set(client):
    """D4 integration: an entity with a numbering prefix gives its new joiners that prefix; an entity
    without one keeps the org prefix (unchanged behaviour)."""
    LE.invalidate(ORG)
    base = {"last_name": "Rao", "address_country": "CA", "province_of_employment": "ON", "hire_date": "2025-04-01"}
    a = client.post("/employees", json={**base, "first_name": "Org"}, headers=H())
    b = client.post("/employees", json={**base, "first_name": "Gar", "entity_id": str(ENT_B)}, headers=H())
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert not a.json()["employee_number"].startswith("MF-")
    assert b.json()["employee_number"].startswith("MF-")
    nx = client.get(f"/employees/next-number?entity_id={ENT_B}", headers=H()).json()
    assert nx["prefix"] == "MF-" and nx["next_number"].startswith("MF-")
