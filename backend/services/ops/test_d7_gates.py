"""D7 route gates: positions / analytics / LMS-depth / engagement-depth routes answer 403 to the wrong role
and 200/201 to the right one — no database (get_db replaced by a fake; directory reads stubbed)."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import analytics_routers
import engage2_routers
import lms_routers
import positions_routers
from database import get_db

ORG = str(uuid.uuid4())


class _Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def first(self):
        return self.rows[0] if self.rows else None

    def mappings(self):
        return self


class FakeDB:
    async def execute(self, *a, **k):
        return _Result()

    def add(self, obj):
        obj.id = uuid.uuid4()

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        pass

    def run_sync(self, fn):
        return fn(None)


def _client() -> TestClient:
    app = FastAPI()
    for r in (positions_routers.router, analytics_routers.router, lms_routers.router, engage2_routers.router):
        app.include_router(r)

    async def fake_db():
        yield FakeDB()
    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


def H(role: str) -> dict:
    return {"x-org-id": ORG, "x-role": role, "x-user-id": str(uuid.uuid4()), "x-employee-id": str(uuid.uuid4())}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    async def no_rows(*a, **k):
        return []

    async def today(org):
        from datetime import date
        return date(2026, 10, 16)

    async def no_emps(*a, **k):
        return {}
    for mod in (positions_routers, analytics_routers, lms_routers, engage2_routers):
        d = getattr(mod, "directory", None)
        if d is not None:
            monkeypatch.setattr(d, "org_employees", no_rows, raising=False)
            monkeypatch.setattr(d, "org_users", no_rows, raising=False)
        monkeypatch.setattr(mod, "org_today", today, raising=False)
    monkeypatch.setattr(analytics_routers, "rows", no_rows)
    monkeypatch.setattr(positions_routers, "_emp_rows", no_emps)

    async def no_setting(org, key, default=None):
        return default
    monkeypatch.setattr(analytics_routers, "get_setting", no_setting)
    monkeypatch.setattr(lms_routers.C, "employees", no_emps)


def test_positions_list_and_money_visibility():
    c = _client()
    for role in ("org_admin", "hr_admin", "payroll_admin", "manager", "superadmin"):
        r = c.get("/engage/positions", headers=H(role))
        assert r.status_code == 200, role
    assert c.get("/engage/positions", headers=H("employee")).status_code == 403
    assert c.get("/engage/positions", headers=H("manager")).json()["shows_money"] is False
    assert c.get("/engage/positions", headers=H("payroll_admin")).json()["shows_money"] is True
    assert c.get("/engage/positions", headers=H("hr_manager")).json()["can_edit"] is True
    assert c.get("/engage/positions", headers=H("payroll_admin")).json()["can_edit"] is False


def test_positions_write_gates_and_summary():
    c = _client()
    body = {"title": "Fitter", "department": "Plant", "budgeted_ctc": 300000}
    assert c.post("/engage/positions", json=body, headers=H("hr_admin")).status_code == 201
    assert c.post("/engage/positions", json=body, headers=H("payroll_admin")).status_code == 403
    assert c.post("/engage/positions", json=body, headers=H("manager")).status_code == 403
    assert c.post("/engage/positions", json={"title": " ", "budgeted_ctc": -5}, headers=H("hr_admin")).status_code == 422
    assert c.get("/engage/positions/summary", headers=H("manager")).status_code == 403
    r = c.get("/engage/positions/summary", headers=H("hr_admin"))
    assert r.status_code == 200 and r.json()["currency"] == "CAD"


def test_analytics_and_retention_gates():
    c = _client()
    assert c.get("/engage/analytics/people", headers=H("employee")).status_code == 403
    r = c.get("/engage/analytics/people", headers=H("hr_admin"))
    assert r.status_code == 200 and "series" in r.json() and "leave_liability" in r.json()
    rm = c.get("/engage/analytics/people", headers=H("manager"))
    assert rm.status_code == 200 and "leave_liability" not in rm.json()          # a manager sees their team only, no liability
    assert c.get("/engage/analytics/retention-actions", headers=H("payroll_admin")).status_code == 403
    assert c.get("/engage/analytics/retention-actions", headers=H("hr_manager")).status_code == 200


def test_lms_depth_gates():
    c = _client()
    assert c.get("/engage/lms/rules", headers=H("employee")).status_code == 403
    assert c.get("/engage/lms/rules", headers=H("hr_admin")).status_code == 200
    assert c.get("/engage/lms/questions", headers=H("manager")).status_code == 403
    assert c.get("/engage/lms/report", headers=H("manager")).status_code == 403
    assert c.get("/engage/lms/team", headers=H("employee")).status_code == 403
    assert c.get("/engage/lms/team", headers=H("manager")).status_code == 200
    assert c.get("/engage/lms/my", headers=H("employee")).status_code == 200
    assert c.get("/engage/lms/paths", headers=H("employee")).status_code == 200
    assert c.post("/engage/lms/rules", json={"name": "Harassment awareness", "course_id": str(uuid.uuid4()), "path_id": str(uuid.uuid4())}, headers=H("hr_admin")).status_code == 422


def test_engagement_depth_gates():
    c = _client()
    assert c.get("/engage/survey-schedules", headers=H("manager")).status_code == 403
    assert c.get("/engage/survey-schedules", headers=H("hr_admin")).status_code == 200
    assert c.get("/engage/survey-bank", headers=H("employee")).status_code == 403
    assert c.get("/engage/insights/manager", headers=H("employee")).status_code == 403
    assert c.get("/engage/insights/enps", headers=H("manager")).status_code == 403      # WASA 2026-10-21: HR only
    assert c.get("/engage/insights/enps", headers=H("hr_manager")).status_code == 200
    assert c.get("/engage/kudos-budgets", headers=H("employee")).status_code == 403
    assert c.get("/engage/kudos-leaderboard", headers=H("employee")).status_code == 200
    assert c.post("/engage/survey-schedules", json={"title": "Pulse", "cadence": "fortnightly"}, headers=H("hr_admin")).status_code == 422


def test_wasa_requisitions_picker_office_only(monkeypatch):
    """WASA 2026-10-21: the requisition picker is on the HR position editor — a manager gets 403."""
    async def reqs(org):
        return [{"id": "R1", "req_number": "REQ-1"}]
    monkeypatch.setattr(positions_routers, "_requisitions", reqs)
    c = _client()
    for role in ("manager", "employee", "accountant"):
        assert c.get("/engage/positions/requisitions", headers=H(role)).status_code == 403, role
    for role in ("hr_admin", "org_admin", "payroll_admin"):
        assert c.get("/engage/positions/requisitions", headers=H(role)).json()["requisitions"][0]["id"] == "R1"


def test_wasa_manager_team_views_stay_narrowed():
    """Manager screens keep working, narrowed to the manager (insights / budgets / positions)."""
    c = _client()
    h = H("manager")
    r = c.get("/engage/kudos-budgets", headers=h)
    assert r.status_code == 200 and len(r.json()["budgets"]) == 1 and r.json()["can_edit"] is False
    assert c.get("/engage/insights/manager", headers=h).json()["manager_id"] == h["x-employee-id"]
