"""Employee service (stream A): who can read whom, work country (always CA) + province,
number / id format checks (SIN, bank, postal code), auto employee numbers, reporting manager, documents.
Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest test_identity_access.py -q
"""
import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import access
import database
import masters_models  # noqa: F401
import models  # noqa: F401
from routers import router
from shared.utils import audit, docstore, identity  # noqa: F401
from shared.utils import id_validators as v
from shared.utils.employee_fixes import (FIX, OK, REVIEW, format_number, match_manager,
                                         next_free_number, suffix_of)

ORG_ON, ORG_BC, ORG_NONE = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%sample\n"


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        for oid, name, country, prov in ((ORG_ON, "Maple Logistics", "CA", "ON"), (ORG_BC, "Northwind", "CA", "BC")):
            await conn.execute(sa.insert(identity.organizations).values(
                id=uuid.UUID(oid), name=name, code=name[:6].upper(), country=country, province=prov, is_active=True))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(org=ORG_ON, role="hr_admin", emp=None, user=None, email=None):
    h = {"X-Org-Id": org, "X-Role": role, "X-User-Id": user or str(uuid.uuid4())}
    if emp:
        h["X-Employee-Id"] = emp
    if email:
        h["X-User-Email"] = email
    return h


def _add(client, org=ORG_ON, **over):
    body = {"first_name": "Test", "last_name": "Person", "annual_salary": 60000, **over}
    r = client.post("/employees", headers=H(org), json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def team(client):
    """Sarah manages Liam; Chloe is someone else's report."""
    boss = _add(client, first_name="Sarah", last_name="Fraser", email="sarah@maple.example")
    arjun = _add(client, first_name="Liam", last_name="Wong", manager_id=boss["id"], email="liam@maple.example")
    divya = _add(client, first_name="Chloe", last_name="Gagnon", email="chloe@maple.example")
    return {"boss": boss, "arjun": arjun, "divya": divya}


# ── format checks (pure) ─────────────────────────────────────────────────────

def test_luhn_known_values():
    assert v.luhn_ok("046454286") and not v.luhn_ok("046454287")    # the published sample SIN


def test_canada_formats():
    assert v.clean_sin("046 454 286") == "046454286"
    with pytest.raises(ValueError, match="not valid"):
        v.clean_sin("123456789")
    with pytest.raises(ValueError, match="9 digits"):
        v.clean_sin("12345")
    assert v.clean_institution("003") == "003" and v.clean_transit("00482") == "00482"
    with pytest.raises(ValueError):
        v.clean_transit("482")
    assert v.clean_bank_account_ca("1234567") == "1234567"
    assert v.clean_postal_code("m5v3l9") == "M5V 3L9"
    for bad in ("560025", "D1A 1A1", "W1A 1A1", "M5V3L"):
        with pytest.raises(ValueError):
            v.clean_postal_code(bad)


def test_fields_are_checked_for_the_work_country():
    cleaned, errors = v.clean_identity_fields("CA", {"bank_account": "123456789012", "sin": ""})
    assert cleaned == {"bank_account": "123456789012", "sin": None} and not errors
    _, errors = v.clean_identity_fields("CA", {"sin": "111111111", "bank_transit": "12"})
    assert set(errors) == {"sin", "bank_transit"}
    assert v.clean_country("ca") == "CA" and v.clean_country("", default="CA") == "CA"
    assert v.clean_country(None) is None
    for bad in ("US", "IND"):
        with pytest.raises(ValueError):
            v.clean_country(bad)


def test_file_type_comes_from_the_bytes_not_the_name():
    assert docstore.sniff(PNG) == ("image/png", "png") and docstore.sniff(PDF) == ("application/pdf", "pdf")
    assert docstore.sniff(b"\xff\xd8\xff\xe0rest") == ("image/jpeg", "jpg")
    assert docstore.sniff(b"MZ\x90\x00 an exe") is None and docstore.sniff(b"<html>") is None
    ok = docstore.check_upload("../../etc/pass wd.exe", PNG)
    assert ok["file_name"] == "pass wd.png" and ok["content_type"] == "image/png"   # real type wins
    with pytest.raises(docstore.DocError, match="JPG, PNG or PDF"):
        docstore.check_upload("resume.pdf", b"PK\x03\x04 really a zip")
    with pytest.raises(docstore.DocError, match="empty"):
        docstore.check_upload("a.pdf", b"")
    with pytest.raises(docstore.DocError, match="too large"):
        docstore.check_upload("a.pdf", b"%PDF-" + b"0" * docstore.MAX_BYTES)


# ── employee numbers (pure) ──────────────────────────────────────────────────

def test_number_format_and_next_free():
    assert format_number("EMP", 7, 4) == "EMP-0007" and format_number("ML/", 7, 3) == "ML/007"
    assert format_number("", 12, 5) == "00012"
    assert suffix_of("EMP", "EMP-0042") == 42 and suffix_of("EMP", "emp0042") == 42
    assert suffix_of("EMP", "ML-0042") is None and suffix_of("EMP", "EMP-A1") is None
    assert next_free_number("EMP", 4, None, set()) == ("EMP-0001", 2)
    # First use in an org that already has numbers: continue after the highest.
    assert next_free_number("EMP", 4, None, {"EMP-0006", "EMP-0002", "X-99"}) == ("EMP-0007", 8)
    # A hand-typed number sitting on the counter is skipped, never reused.
    assert next_free_number("EMP", 4, 8, {"EMP-0008", "emp-0009"}) == ("EMP-0010", 11)


# ── manager back-fill rules (pure) ────────────────────────────────────────────

def test_manager_backfill_needs_exactly_one_match():
    people = [{"id": "1", "first_name": "Sarah", "last_name": "Fraser", "employee_number": "EMP-0001"},
              {"id": "2", "first_name": "Liam", "last_name": "Wong"},
              {"id": "3", "first_name": "Liam", "last_name": "Wong"}]
    hit = match_manager("  sarah   FRASER ", "2", people)
    assert hit["action"] == FIX and hit["manager_id"] == "1" and hit["manager_name"] == "Sarah Fraser"
    assert match_manager("Fraser, Sarah", "2", people)["manager_id"] == "1"
    assert match_manager("EMP-0001", "2", people)["manager_id"] == "1"
    assert match_manager("Liam Wong", "1", people)["action"] == REVIEW        # two people → ambiguous
    assert match_manager("Sarah Fraser", "1", people)["action"] == REVIEW       # cannot report to self
    assert match_manager("Nobody Here", "1", people)["action"] == REVIEW
    assert match_manager("", "1", people)["action"] == OK


def test_scope_rule():
    for role in ("org_admin", "hr_admin", "hr_manager", "payroll_admin", "superadmin"):
        assert access.scope_for(role) == access.ALL
    assert access.scope_for("manager") == access.TEAM
    assert access.scope_for("employee") == access.SELF and access.scope_for("") == access.SELF
    assert access.scope_for("", system=True) == access.ALL


# ── work country ─────────────────────────────────────────────────────────────

def test_country_is_always_canada_and_the_province_defaults_to_the_org(client):
    e = _add(client, ORG_ON)                                  # no country, no province sent
    assert e["address_country"] == "CA" and e["country"] == "CA"
    assert e["province_of_employment"] == "ON" and e["pay_frequency"] == "biweekly"
    c = _add(client, ORG_BC)
    assert c["address_country"] == "CA" and c["province_of_employment"] == "BC"
    x = _add(client, ORG_ON, province_of_employment="QC")    # the form's choice wins over the org default
    assert x["province_of_employment"] == "QC"
    for bad in ({"address_country": "US"}, {"address_country": "IND"}):
        r = client.post("/employees", headers=H(ORG_ON), json={"first_name": "A", "last_name": "B", **bad})
        assert r.status_code == 422 and "Canada" in str(r.json()["detail"])
    r = client.post("/employees", headers=H(ORG_ON), json={"first_name": "A", "last_name": "B", "province_of_employment": "XX"})
    assert r.status_code == 422 and "province" in str(r.json()["detail"])
    r = client.post("/employees", headers=H(ORG_NONE), json={"first_name": "A", "last_name": "B"})
    assert r.status_code == 422                                 # no org province and none chosen → refused
    r = client.post("/employees", headers=H(ORG_ON), json={"first_name": "A", "last_name": "B", "postal_code": "560025"})
    assert r.status_code == 422 and "postal code" in str(r.json()["detail"]).lower()
    p = _add(client, ORG_ON, postal_code="k1a0b1")
    assert p["postal_code"] == "K1A 0B1"
    assert client.patch(f"/employees/{e['id']}", headers=H(ORG_ON), json={"address_country": "US"}).status_code == 422
    r = client.patch(f"/employees/{e['id']}", headers=H(ORG_ON), json={"province_of_employment": "AB"})
    assert r.status_code == 200 and r.json()["province_of_employment"] == "AB"


# ── identifiers on the form ──────────────────────────────────────────────────

def test_canada_sin_luhn_encrypted_and_masked(client):
    r = client.post("/employees", headers=H(ORG_BC), json={"first_name": "Bad", "last_name": "Sin", "sin": "123456789"})
    assert r.status_code == 422 and "SIN" in r.json()["detail"]
    e = _add(client, ORG_BC, first_name="Jordan", last_name="Lee", sin="046-454-286", bank_institution="003",
             bank_transit="00482", bank_account="1234567", td1_federal_claim=16452, td1_provincial_claim=12747)
    assert e["sin_masked"].endswith("286") and "046454286" not in str(e) and e["has_sin"] is True
    assert e["td1_federal_claim"] == 16452 and e["bank_account_masked"] == "XXXX4567"
    r = client.post("/employees", headers=H(ORG_BC), json={"first_name": "Bad", "last_name": "Transit", "bank_transit": "12"})
    assert r.status_code == 422

    async def stored():
        async with database.SessionLocal() as s:
            return (await s.execute(sa.text(
                "SELECT sin_enc, bank_account_enc FROM employees WHERE first_name = 'Jordan'"))).first()
    row = client.portal.call(stored)
    assert all(str(x).startswith("enc:v1:") for x in row)            # ciphertext on disk
    assert client.get(f"/employees/{e['id']}/sensitive", headers=H(ORG_BC, role="payroll_admin")).json()["sin"] == "046454286"
    assert client.get(f"/employees/{e['id']}/sensitive", headers=H(ORG_BC, role="hr_manager")).status_code == 403


# ── auto number ──────────────────────────────────────────────────────────────

def test_auto_number_runs_per_org_and_manual_override_is_unique(client):
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    kw = {"address_country": "CA", "province_of_employment": "ON"}
    a1, a2 = _add(client, org_a, **kw), _add(client, org_a, **kw)
    assert (a1["employee_number"], a2["employee_number"]) == ("EMP-0001", "EMP-0002")
    assert _add(client, org_b, **kw)["employee_number"] == "EMP-0001"        # another org starts at 1 too
    assert _add(client, org_a, employee_number="ML-77", **kw)["employee_number"] == "ML-77"
    r = client.post("/employees", headers=H(org_a), json={"first_name": "D", "last_name": "Up",
                                                           "employee_number": "ml-77", **kw})
    assert r.status_code == 409 and "already used" in r.json()["detail"]
    _add(client, org_a, employee_number="EMP-0003", **kw)                    # typed by hand, sits on the counter
    assert _add(client, org_a, **kw)["employee_number"] == "EMP-0004"        # auto skips it
    assert client.get("/employees/next-number", headers=H(org_a)).json()["next_number"] == "EMP-0005"
    opts = client.get("/employees/form-options", headers=H(org_a)).json()
    assert opts["next_number"] == "EMP-0005" and len(opts["managers"]) == 5
    r = client.patch(f"/employees/{a2['id']}", headers=H(org_a), json={"employee_number": "EMP-0001"})
    assert r.status_code == 409


# ── reporting manager ────────────────────────────────────────────────────────

def test_manager_is_saved_as_an_id(client, team):
    assert team["arjun"]["manager_id"] == team["boss"]["id"] and team["arjun"]["manager_name"] == "Sarah Fraser"
    # Typed text that names exactly one person is linked too.
    e = _add(client, first_name="Noah", last_name="Martin", manager_name="sarah fraser")
    assert e["manager_id"] == team["boss"]["id"]
    # Text that matches nobody stays as text, with no id.
    e2 = _add(client, first_name="Emma", last_name="Martin", manager_name="Head Office")
    assert e2["manager_id"] is None and e2["manager_name"] == "Head Office"
    hr = H()
    assert client.patch(f"/employees/{team['boss']['id']}", headers=hr,
                        json={"manager_id": team["boss"]["id"]}).status_code == 422          # self
    assert client.patch(f"/employees/{team['boss']['id']}", headers=hr,
                        json={"manager_id": team["arjun"]["id"]}).status_code == 422         # loop
    assert client.patch(f"/employees/{team['divya']['id']}", headers=hr,
                        json={"manager_id": str(uuid.uuid4())}).status_code == 422           # not an employee
    r = client.patch(f"/employees/{e['id']}", headers=hr, json={"manager_id": None})
    assert r.status_code == 200 and r.json()["manager_id"] is None


# ── who can read whom ────────────────────────────────────────────────────────

def test_employee_reads_only_self(client, team):
    me, other = team["arjun"]["id"], team["divya"]["id"]
    rows = client.get("/employees", headers=H(role="employee", emp=me)).json()
    assert [r["id"] for r in rows] == [me] and rows[0]["is_self"] is True
    assert client.get(f"/employees/{me}", headers=H(role="employee", emp=me)).status_code == 200
    assert client.get(f"/employees/{other}", headers=H(role="employee", emp=me)).status_code == 404
    assert client.get(f"/employees/{other}/sensitive", headers=H(role="employee", emp=me)).status_code == 404
    assert client.get(f"/employees/{other}/documents", headers=H(role="employee", emp=me)).status_code == 404
    # Own full identifiers are allowed (and audited); ?reveal=full on the list is not.
    assert client.get(f"/employees/{me}/sensitive", headers=H(role="employee", emp=me)).status_code == 200
    assert client.get("/employees", headers=H(role="employee", emp=me), params={"reveal": "full"}).status_code == 403
    # No write of any kind.
    assert client.patch(f"/employees/{me}", headers=H(role="employee", emp=me),
                        json={"annual_salary": 9999999}).status_code == 403
    assert client.post("/employees", headers=H(role="employee", emp=me),
                       json={"first_name": "X", "last_name": "Y"}).status_code == 403
    assert client.get("/employees/form-options", headers=H(role="employee", emp=me)).status_code == 403


def test_login_with_no_employee_link_sees_nothing(client, team):
    assert client.get("/employees", headers=H(role="employee")).json() == []
    assert client.get(f"/employees/{team['divya']['id']}", headers=H(role="employee")).status_code == 404
    # A login made before linking existed is matched by its e-mail — to one record only.
    rows = client.get("/employees", headers=H(role="employee", email="CHLOE@maple.example")).json()
    assert [r["id"] for r in rows] == [team["divya"]["id"]]


def test_manager_reads_self_and_direct_reports_only(client, team):
    boss = team["boss"]["id"]
    ids = {r["id"] for r in client.get("/employees", headers=H(role="manager", emp=boss)).json()}
    assert team["arjun"]["id"] in ids and boss in ids and team["divya"]["id"] not in ids
    assert client.get(f"/employees/{team['arjun']['id']}", headers=H(role="manager", emp=boss)).status_code == 200
    assert client.get(f"/employees/{team['divya']['id']}", headers=H(role="manager", emp=boss)).status_code == 404
    # A manager never gets a report's full ids or documents, and cannot edit.
    assert client.get(f"/employees/{team['arjun']['id']}/sensitive", headers=H(role="manager", emp=boss)).status_code == 403
    assert client.get(f"/employees/{team['arjun']['id']}/documents", headers=H(role="manager", emp=boss)).status_code == 404
    assert client.patch(f"/employees/{team['arjun']['id']}", headers=H(role="manager", emp=boss),
                        json={"job_title": "x"}).status_code == 403


def test_office_roles_read_all_and_orgs_are_isolated(client, team):
    n = len(client.get("/employees", headers=H(role="org_admin")).json())
    for role in ("hr_admin", "hr_manager", "payroll_admin"):
        assert len(client.get("/employees", headers=H(role=role)).json()) == n >= 3
    assert all(r["id"] != team["arjun"]["id"] for r in client.get("/employees", headers=H(ORG_BC)).json())
    assert client.get(f"/employees/{team['arjun']['id']}", headers=H(ORG_BC)).status_code == 404
    # Another service doing background work reads the org only with the internal token;
    # an org header with no user and no token reads nothing.
    from shared.utils.internal import internal_headers
    assert len(client.get("/employees", headers=internal_headers(ORG_ON, "test")).json()) == n
    assert client.get("/employees", headers={"X-Org-Id": ORG_ON}).json() == []
    assert client.get("/employees", headers={"X-Role": "org_admin"}).status_code == 401
    # payroll may edit pay fields but not add people.
    assert client.patch(f"/employees/{team['divya']['id']}", headers=H(role="payroll_admin"),
                        json={"annual_salary": 72000}).status_code == 200
    assert client.post("/employees", headers=H(role="payroll_admin"),
                       json={"first_name": "X", "last_name": "Y"}).status_code == 403


# ── documents ────────────────────────────────────────────────────────────────

def test_documents_are_stored_in_the_database(client, team):
    eid, hr = team["arjun"]["id"], H()
    r = client.post(f"/employees/{eid}/documents", headers=hr, data={"doc_type": "sin_proof"},
                    files={"file": ("sin.exe", PNG, "application/x-msdownload")})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["content_type"] == "image/png" and doc["file_name"] == "sin.png" and doc["size_bytes"] == len(PNG)
    r = client.post(f"/employees/{eid}/documents", headers=hr, files={"file": ("cv.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 422 and "JPG, PNG or PDF" in r.json()["detail"]

    lst = client.get(f"/employees/{eid}/documents", headers=hr).json()
    assert [d["id"] for d in lst] == [doc["id"]] and "data" not in lst[0]
    dl = client.get(f"/employees/{eid}/documents/{doc['id']}/download", headers=hr)
    assert dl.status_code == 200 and dl.content == PNG and dl.headers["content-type"] == "image/png"

    # The employee can see and add their own; nobody else's.
    me = H(role="employee", emp=eid)
    assert len(client.get(f"/employees/{eid}/documents", headers=me).json()) == 1
    assert client.post(f"/employees/{eid}/documents", headers=me, files={"file": ("a.pdf", PDF, "x")}).status_code == 201
    other = H(role="employee", emp=team["divya"]["id"])
    assert client.get(f"/employees/{eid}/documents/{doc['id']}/download", headers=other).status_code == 404
    assert client.delete(f"/employees/{eid}/documents/{doc['id']}", headers=me).status_code == 403

    assert client.post(f"/employees/{eid}/documents/{doc['id']}/verify", headers=hr, json={}).json()["verified"] is True
    assert client.delete(f"/employees/{eid}/documents/{doc['id']}", headers=hr).status_code == 200
    assert doc["id"] not in [d["id"] for d in client.get(f"/employees/{eid}/documents", headers=hr).json()]
    assert client.get(f"/employees/{eid}/documents/{doc['id']}/download", headers=hr).status_code == 404

    async def still_there():
        async with database.SessionLocal() as s:
            return (await s.execute(sa.text("SELECT count(*) FROM employee_documents WHERE deleted_at IS NOT NULL"))).scalar()
    assert client.portal.call(still_there) == 1                       # soft delete: the row is kept
