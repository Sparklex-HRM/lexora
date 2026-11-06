"""ops-service server-side role checks: per route group, an allowed role gets 200 and a
disallowed role gets 403 — without touching a database (get_db is replaced by a fake)."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import psa_routers
import routers
from database import get_db
from models import Branch

ORG = str(uuid.uuid4())


class _Result:
    def __init__(self, row=None):
        self.row = row

    def scalars(self):
        return self

    def all(self):
        return [self.row] if self.row is not None else []

    def scalar_one_or_none(self):
        return self.row

    def first(self):
        return self.row


class FakeDB:
    """Just enough AsyncSession for the CRUD routes."""

    def __init__(self, row=None):
        self.row = row

    async def execute(self, *a, **k):
        return _Result(self.row)

    def add(self, obj):
        obj.id = uuid.uuid4()

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


def _client(row=None) -> TestClient:
    app = FastAPI()
    app.include_router(routers.router)
    app.include_router(psa_routers.router)

    async def fake_db():
        yield FakeDB(row)
    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


def H(role: str) -> dict:
    return {"x-org-id": ORG, "x-role": role, "x-user-id": str(uuid.uuid4()), "x-employee-id": str(uuid.uuid4())}


@pytest.fixture(autouse=True)
def _no_settings_db(monkeypatch):
    async def fake_setting(org, key, default=None):
        return default
    monkeypatch.setattr(psa_routers, "get_setting", fake_setting)


# ── summary ───────────────────────────────────────────────────────────────────
def test_summary_office_only():
    c = _client()
    for role in ("org_admin", "hr_admin", "hr_manager", "payroll_admin", "superadmin"):
        assert c.get("/ops/summary", headers=H(role)).status_code == 200, role
    for role in ("employee", "manager", "accountant"):                 # WASA 2026-10-21: no manager screen calls it
        assert c.get("/ops/summary", headers=H(role)).status_code == 403, role


def test_no_role_is_401_and_unknown_role_is_403():
    c = _client()
    h = H("employee")
    h.pop("x-role")
    assert c.get("/ops/branches", headers=h).status_code == 401
    assert c.get("/ops/vendors", headers=H("intern")).status_code == 403


# ── list (read) ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("resource,allowed,denied", [
    ("branches", ["employee", "manager", "hr_admin"], []),
    ("projects", ["employee", "manager", "payroll_admin"], []),
    ("policies", ["employee", "org_admin"], []),
    ("clients", ["manager", "hr_manager", "payroll_admin"], ["employee"]),
    ("roster", ["manager", "hr_admin", "org_admin"], ["employee"]),
    ("tasks", ["manager", "payroll_admin", "hr_admin"], ["employee"]),
    ("vendors", ["hr_admin", "payroll_admin", "org_admin"], ["employee", "manager"]),
    ("documents", ["hr_manager", "payroll_admin"], ["employee", "manager"]),
    ("invoices", ["org_admin", "payroll_admin", "hr_admin", "superadmin"], ["employee", "manager", "hr_manager"]),
])
def test_list_gates(resource, allowed, denied):
    c = _client()
    for role in allowed:
        assert c.get(f"/ops/{resource}", headers=H(role)).status_code == 200, (resource, role)
    for role in denied:
        assert c.get(f"/ops/{resource}", headers=H(role)).status_code == 403, (resource, role)


# ── create / delete (write) ───────────────────────────────────────────────────
@pytest.mark.parametrize("resource,allowed,denied", [
    ("branches", ["org_admin", "hr_admin", "hr_manager"], ["employee", "manager", "payroll_admin"]),
    ("vendors", ["hr_admin"], ["employee", "manager", "payroll_admin"]),
    ("documents", ["org_admin"], ["employee", "manager"]),
    ("policies", ["hr_manager"], ["employee", "manager", "payroll_admin"]),
    ("clients", ["manager", "hr_admin"], ["employee", "payroll_admin"]),
    ("projects", ["manager", "org_admin"], ["employee"]),
    ("roster", ["manager", "hr_admin"], ["employee", "payroll_admin"]),
    ("tasks", ["manager", "payroll_admin", "hr_admin"], ["employee"]),
    ("invoices", ["org_admin", "payroll_admin", "hr_admin"], ["employee", "manager", "hr_manager"]),
])
def test_create_and_delete_gates(resource, allowed, denied):
    c = _client(row=Branch(org_id=ORG, name="x", is_active=True))
    for role in allowed:
        assert c.post(f"/ops/{resource}", json={"name": "Pune"}, headers=H(role)).status_code == 201, (resource, role)
        assert c.delete(f"/ops/{resource}/{uuid.uuid4()}", headers=H(role)).status_code == 200, (resource, role)
    for role in denied:
        assert c.post(f"/ops/{resource}", json={"name": "Pune"}, headers=H(role)).status_code == 403, (resource, role)
        assert c.delete(f"/ops/{resource}/{uuid.uuid4()}", headers=H(role)).status_code == 403, (resource, role)


# ── PSA reads that had no gate ────────────────────────────────────────────────
def test_psa_rate_cards_and_fx_reads():
    c = _client()
    for path in ("/ops/psa/rate-cards", "/ops/psa/fx-rates"):
        for role in ("org_admin", "hr_admin", "payroll_admin", "manager"):
            assert c.get(path, headers=H(role)).status_code == 200, (path, role)
        assert c.get(path, headers=H("employee")).status_code == 403, path
    assert c.get("/ops/psa/billable-lines", headers=H("employee")).status_code == 403
