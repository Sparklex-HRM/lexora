"""WASA 2026-10-21 — employee-service access fixes: master-data writes = HR,
entity apply-due = HR / internal, the accountant gets a names-only directory, a manager's asset rights
cover their own team, offer approvals are HR + named approvers, comp-cycle import = HR / a manager
with reports. Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest -q test_authz_wasa.py
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import access
import assets_routers as AR
import comp_cycle_routers as CC
import database
import hiring_approvals as HA
import masters_models  # noqa: F401
import models  # noqa: F401
from hiring_logic import Actor
from masters_routers import router as masters_router
from routers import router
from shared.utils import identity

# Postgres takes uuid strings in comparisons (the routers pass header strings); sqlite's generic
# Uuid processor wants uuid objects — coerce, test-only.
from sqlalchemy.sql import sqltypes  # noqa: E402

_orig_bp = sqltypes.Uuid.bind_processor


def _lenient_bp(self, dialect):
    p = _orig_bp(self, dialect)
    if p is None:
        return None

    def proc(v):
        return p(uuid.UUID(v) if isinstance(v, str) else v)
    return proc


sqltypes.Uuid.bind_processor = _lenient_bp

ORG = str(uuid.uuid4())
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for t in (identity.organizations, identity.users):
            await conn.run_sync(lambda c, t=t: t.create(c, checkfirst=True))
        await conn.execute(sa.insert(identity.organizations).values(
            id=uuid.UUID(ORG), name="Wasa Logistics", code="WASAEM", country="CA", province="ON", is_active=True))
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(router)
app.include_router(masters_router)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(role, emp=None):
    h = {"X-Org-Id": ORG, "X-Role": role, "X-User-Id": str(uuid.uuid4())}
    if emp:
        h["X-Employee-Id"] = emp
    return h


@pytest.fixture(scope="module")
def people(client):
    def add(**over):
        body = {"first_name": "T", "last_name": "P", "annual_salary": 90000, "address_country": "CA",
                "province_of_employment": "ON", **over}
        r = client.post("/employees", headers=H("hr_admin"), json=body)
        assert r.status_code == 201, r.text
        return r.json()
    boss = add(first_name="Boss", last_name="Lead")
    priya = add(first_name="Priya", last_name="Menon", manager_id=boss["id"], sin="046454286",
                work_email="priya@wasa.example")
    anu = add(first_name="Anu", last_name="Accounts")
    return {"boss": boss, "priya": priya, "anu": anu}


# ── master data ───────────────────────────────────────────────────────────────
def test_master_writes_hr_only(client):
    for role in ("employee", "manager", "accountant", "payroll_admin"):
        h = H(role)
        assert client.post("/masters/departments", headers=h, json={"name": "X"}).status_code == 403
        assert client.patch(f"/masters/departments/{uuid.uuid4()}", headers=h, json={"name": "Y"}).status_code == 403
        assert client.delete(f"/masters/departments/{uuid.uuid4()}", headers=h).status_code == 403
        assert client.post("/masters/seed-defaults", headers=h).status_code == 403
    r = client.post("/masters/departments", headers=H("hr_manager"), json={"name": "Weaving"})
    assert r.status_code == 201
    did = r.json()["id"]
    assert client.patch(f"/masters/departments/{did}", headers=H("org_admin"), json={"name": "Weave"}).json()["name"] == "Weave"
    assert client.get("/masters/departments", headers=H("employee")).status_code == 200      # read stays ALL
    assert client.delete(f"/masters/departments/{did}", headers=H("hr_admin")).status_code == 200


def test_entity_apply_due_hr_only(client):
    for role in ("employee", "manager", "accountant", "payroll_admin"):
        assert client.post("/employees/entities/apply-due", headers=H(role)).status_code == 403
    assert client.post("/employees/entities/apply-due", headers=H("hr_manager")).status_code == 200


# ── accountant = names-only directory ─────────────────────────────────────────
PAY_KEYS = ("annual_salary", "hourly_rate", "sin_masked", "bank_account_masked", "date_of_birth", "phone",
            "address_line1", "bank_transit", "bank_institution")


def test_accountant_list_is_names_only(client, people):
    rows = client.get("/employees", headers=H("accountant", people["anu"]["id"])).json()
    by = {r["id"]: r for r in rows}
    p = by[people["priya"]["id"]]
    assert p["first_name"] == "Priya" and p["work_email"] == "priya@wasa.example" and p["names_only"]
    assert not any(k in p for k in PAY_KEYS)
    assert by[people["anu"]["id"]]["annual_salary"] == 90000            # own record in full
    # HR / payroll unchanged
    hr = {r["id"]: r for r in client.get("/employees", headers=H("hr_admin")).json()}
    assert hr[people["priya"]["id"]]["annual_salary"] == 90000
    pay = {r["id"]: r for r in client.get("/employees", headers=H("payroll_admin")).json()}
    assert pay[people["priya"]["id"]]["annual_salary"] == 90000


def test_accountant_one_record_and_documents(client, people):
    acc = H("accountant", people["anu"]["id"])
    pid = people["priya"]["id"]
    r = client.get(f"/employees/{pid}", headers=acc)
    assert r.status_code == 200 and r.json()["names_only"] and "annual_salary" not in r.json()
    assert client.get(f"/employees/{pid}?reveal=full", headers=acc).status_code == 403
    assert client.get(f"/employees/{pid}/sensitive", headers=acc).status_code == 403
    assert client.get(f"/employees/{pid}/documents", headers=acc).status_code == 404
    assert client.post(f"/employees/{pid}/documents", headers=acc, data={"doc_type": "other"},
                       files={"file": ("a.png", PNG, "image/png")}).status_code == 404
    own = client.get(f"/employees/{people['anu']['id']}", headers=acc).json()
    assert own["annual_salary"] == 90000
    assert client.get(f"/employees/{pid}/documents", headers=H("hr_admin")).status_code == 200


def test_employee_update_and_upload_scope(client, people):
    """PATCH needs HR / payroll (access.require_edit); an employee uploads only to their own record."""
    pid, bid = people["priya"]["id"], people["boss"]["id"]
    me = H("employee", pid)
    assert client.patch(f"/employees/{pid}", headers=me, json={"job_title": "CEO"}).status_code == 403
    assert client.patch(f"/employees/{bid}", headers=H("manager", bid), json={"job_title": "x"}).status_code == 403
    assert client.post(f"/employees/{bid}/documents", headers=me, data={"doc_type": "other"},
                       files={"file": ("a.png", PNG, "image/png")}).status_code == 404
    assert client.post(f"/employees/{pid}/documents", headers=me, data={"doc_type": "other"},
                       files={"file": ("a.png", PNG, "image/png")}).status_code == 201


# ── assets: a manager manages their own team only ─────────────────────────────
def test_asset_team_scope(client, people):
    boss, priya, anu = people["boss"]["id"], people["priya"]["id"], people["anu"]["id"]
    mgr = {"role": "manager", "employee_id": boss, "user_id": "u", "email": "b@x"}

    async def go():
        async with database.SessionLocal() as db:
            return (await AR._team(db, ORG, mgr),
                    await AR._may_manage_emp(db, ORG, mgr, priya), await AR._may_manage_emp(db, ORG, mgr, anu),
                    await AR._may_manage_emp(db, ORG, mgr, None),
                    await AR._may_manage_emp(db, ORG, {**mgr, "role": "hr_admin"}, anu),
                    await AR._may_manage_emp(db, ORG, {**mgr, "role": "payroll_admin"}, anu),
                    await AR._may_manage_emp(db, ORG, {**mgr, "role": "employee"}, priya))
    team, own_report, other, unassigned, hr, pay, emp = asyncio.run(go())
    assert team == {boss, priya}
    assert own_report and not other and not unassigned
    assert hr and pay and not emp


# ── offer approval: HR + named approvers only ─────────────────────────────────
def _patch_offer(monkeypatch, role, emp="", user="", steps=None):
    actor = Actor(org=ORG, user_id=user, role=role, employee_id=emp)

    async def get_actor(_r, _db):
        return actor

    async def one(*_a, **_k):
        return SimpleNamespace(id="O1", status="draft", created_by="someone")

    async def steps_for(*_a, **_k):
        return steps or []
    monkeypatch.setattr(HA, "get_actor", get_actor)
    monkeypatch.setattr(HA, "one", one)
    monkeypatch.setattr(HA, "steps_for", steps_for)


def _step(kind, **kw):
    return SimpleNamespace(approver_kind=kind, approver_employee_id=kw.get("emp"), approver_role=kw.get("role"),
                           approver_user_id=kw.get("user"), status="pending", step_no=1, chain_source="default",
                           __table__=SimpleNamespace(columns=[]))


def _call(fn, *a):
    return asyncio.run(fn(*a))


def test_offer_approval_scope(monkeypatch):
    steps = [_step("manager", emp="E-BOSS"), _step("role", role="hr_admin")]
    for role, emp in (("employee", "E-PRIYA"), ("accountant", "E-ANU"), ("payroll_admin", ""), ("manager", "E-OTHER")):
        _patch_offer(monkeypatch, role, emp=emp, steps=steps)
        with pytest.raises(HTTPException) as ex:
            _call(HA.get_approval, "O1", None, None)
        assert ex.value.status_code == 404
    _patch_offer(monkeypatch, "manager", emp="E-BOSS", steps=steps)
    assert _call(HA._named_on_chain, None, Actor(org=ORG, role="manager", employee_id="E-BOSS"),
                 SimpleNamespace(id="O1")) is True
    assert _call(HA._named_on_chain, None, Actor(org=ORG, role="manager", employee_id="E-X"),
                 SimpleNamespace(id="O1")) is False
    _patch_offer(monkeypatch, "manager", steps=[_step("user", user="U9")])
    assert _call(HA._named_on_chain, None, Actor(org=ORG, role="manager", user_id="U9"), SimpleNamespace(id="O1")) is True


# ── comp-cycle import: HR or a manager with reports ───────────────────────────
def test_comp_import_needs_reports(monkeypatch):
    actor = Actor(org=ORG, role="employee", employee_id="E1")

    async def get_actor(_r, _db):
        return actor

    async def cycle(*_a):
        return SimpleNamespace(status="open")

    async def lines(*_a):
        return [SimpleNamespace(manager_id="BOSS", employee_number="K1", locked=False, id="L1")]
    monkeypatch.setattr(CC, "get_actor", get_actor)
    monkeypatch.setattr(CC, "_cycle", cycle)
    monkeypatch.setattr(CC, "_lines", lines)
    with pytest.raises(HTTPException) as ex:
        asyncio.run(CC.import_csv("C1", {"csv": ""}, None, None))
    assert ex.value.status_code == 403
    actor.role, actor.employee_id = "manager", "SOMEONE"
    with pytest.raises(HTTPException) as ex:
        asyncio.run(CC.import_csv("C1", {"csv": ""}, None, None))
    assert ex.value.status_code == 403


# ── org RBAC 2026-10-26: a custom role acting as an office role sees only the data classes it carries ──
def test_custom_role_without_data_pay_gets_no_pay_fields(client, people):
    pid = people["priya"]["id"]
    desk = {**H("hr_manager"), "X-Acting-Role": "people_desk", "X-Acting-Data": "personal"}
    row = {r["id"]: r for r in client.get("/employees", headers=desk).json()}[pid]
    assert row["annual_salary"] is None and row.get("sin_masked") is None and row["pay_hidden"]
    one = client.get(f"/employees/{pid}", headers=desk).json()
    assert one["annual_salary"] is None and one["pay_hidden"]
    assert client.get(f"/employees/{pid}?reveal=full", headers=desk).status_code == 403   # no data.identity
    # data.pay but no data.identity: salary yes, identifiers no
    pay = {**H("hr_manager"), "X-Acting-Role": "people_pay", "X-Acting-Data": "pay,personal"}
    row = {r["id"]: r for r in client.get("/employees", headers=pay).json()}[pid]
    assert row["annual_salary"] == 90000 and row.get("sin_masked") is None
    # both classes: the full record, like the system role
    full = {**H("hr_manager"), "X-Acting-Role": "people_all", "X-Acting-Data": "identity,pay,personal"}
    row = {r["id"]: r for r in client.get("/employees", headers=full).json()}[pid]
    assert row["annual_salary"] == 90000 and not row.get("pay_hidden")
    # the system role itself: unchanged
    row = {r["id"]: r for r in client.get("/employees", headers=H("hr_manager")).json()}[pid]
    assert row["annual_salary"] == 90000 and not row.get("pay_hidden")
