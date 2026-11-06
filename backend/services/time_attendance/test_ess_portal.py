"""Employee portal (2026-10-16) — time self-service: role checks + the pure day/month rules.
Run:  cd backend/services/time_attendance && python -m pytest -q test_ess_portal.py
"""
import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ess_time
import ess_time_logic as L
import routers
from database import get_db

ORG = str(uuid.uuid4())
PRIYA, VIKRAM, OTHER = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
EMPS = [
    {"id": PRIYA, "first_name": "Priya", "last_name": "Menon", "manager_id": VIKRAM, "province_of_employment": "ON"},
    {"id": VIKRAM, "first_name": "Vikram", "last_name": "Rao", "manager_id": None, "province_of_employment": "ON"},
    {"id": OTHER, "first_name": "Ravi", "last_name": "K", "manager_id": None, "province_of_employment": "MH"},
]


# ── pure rules ────────────────────────────────────────────────────────────────

def test_may_view_scopes():
    p, v, o = EMPS
    assert L.may_view("employee", PRIYA, p) and not L.may_view("employee", PRIYA, o)
    assert L.may_view("manager", VIKRAM, p) and not L.may_view("manager", VIKRAM, o)
    assert L.may_view("hr_admin", None, o)


def test_may_decide_never_own_and_only_own_manager():
    assert not L.may_decide("hr_admin", PRIYA, PRIYA, VIKRAM)          # HR cannot approve their own
    assert L.may_decide("manager", VIKRAM, PRIYA, VIKRAM)
    assert not L.may_decide("manager", OTHER, PRIYA, VIKRAM)
    assert not L.may_decide("employee", OTHER, PRIYA, VIKRAM)
    assert L.may_decide("hr_manager", OTHER, PRIYA, VIKRAM)


def test_fold_day_codes_and_exceptions():
    t = date(2026, 10, 16)
    kw = dict(today=t, planned=8, actual=0, status="x", late_min=0, first_in=None, last_out=None,
              holiday=None, leave=None, shift=None, correction=None)
    assert L.fold_day(date(2026, 10, 2), workday=True, **{**kw, "holiday": "Gandhi Jayanti"})["code"] == "H"
    assert L.fold_day(date(2026, 10, 3), workday=False, **kw)["code"] == "WO"
    a = L.fold_day(date(2026, 10, 14), workday=True, **kw)
    assert a["code"] == "A" and a["exceptions"] == ["absent"]
    assert L.fold_day(date(2026, 10, 20), workday=True, **kw)["code"] == ""      # future working day
    lv = {"status": "approved", "half": False, "type": "Casual Leave"}
    assert L.fold_day(date(2026, 10, 14), workday=True, **{**kw, "leave": lv})["code"] == "L"
    assert L.fold_day(date(2026, 10, 14), workday=True, **{**kw, "leave": {**lv, "half": True}, "first_in": "13:30", "last_out": "18:00"})["code"] == "HL"
    late = L.fold_day(date(2026, 10, 15), workday=True, **{**kw, "first_in": "09:40", "last_out": None, "late_min": 40, "actual": 0})
    assert late["code"] == "P" and set(late["exceptions"]) == {"late", "missing_out"}
    # a pending leave does not turn an absence into leave
    assert L.fold_day(date(2026, 10, 14), workday=True, **{**kw, "leave": {**lv, "status": "pending"}})["code"] == "A"


def test_kpis():
    t = date(2026, 10, 16)
    base = dict(today=t, planned=8, status="x", holiday=None, leave=None, shift=None, correction=None, last_out="18:00")
    days = [L.fold_day(date(2026, 10, 12), workday=True, actual=9, late_min=0, first_in="09:00", **base),
            L.fold_day(date(2026, 10, 13), workday=True, actual=7, late_min=30, first_in="09:30", **base),
            L.fold_day(date(2026, 10, 14), workday=True, actual=0, late_min=0, first_in=None, **{**base, "last_out": None})]
    k = L.kpis(days)
    assert k["avg_hours"] == 8.0 and k["late_days"] == 1 and k["exception_days"] == 2 and k["absent_days"] == 1


def test_leave_days_and_month_bounds():
    s, e = L.month_bounds("2026-02")
    assert (s, e) == (date(2026, 2, 1), date(2026, 2, 28))
    with pytest.raises(ValueError):
        L.month_bounds("Feb")
    rows = [{"start_date": "2026-01-30", "end_date": "2026-02-02", "status": "approved", "leave_type_code": "CL"}]
    got = L.leave_days(rows, s, e)
    assert sorted(got) == [date(2026, 2, 1), date(2026, 2, 2)]


def test_permission_policy():
    assert L.hours_between("10:00", "11:30") == 1.5
    with pytest.raises(ValueError):
        L.hours_between("11:00", "10:00")
    pol = {"max_per_month": 2, "max_hours": 2}
    assert L.permission_check(pol, hours=1.5, used_this_month=1) is None
    assert "at most 2" in L.permission_check(pol, hours=2.5, used_this_month=0)
    assert "used all 2" in L.permission_check(pol, hours=1, used_this_month=2)


# ── endpoints: an employee only sees / acts on their own records ─────────────

class _R:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.added = []

    async def execute(self, *a, **k):
        return _R(self.rows)

    def add(self, o):
        o.id = uuid.uuid4()
        self.added.append(o)

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def refresh(self, o):
        pass


def _client(db, monkeypatch):
    async def emps(_req):
        return EMPS

    async def today(_org=None):
        return date(2026, 10, 16)

    async def no_hol(_req):
        return []

    for mod in (routers, ess_time):
        monkeypatch.setattr(mod, "_fetch_employees", emps)
        monkeypatch.setattr(mod, "_today", today)
        monkeypatch.setattr(mod, "_fetch_stat_holidays", no_hol)
    app = FastAPI()
    app.include_router(ess_time.router)
    app.include_router(routers.router)

    async def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def H(role, emp):
    return {"x-org-id": ORG, "x-user-id": str(uuid.uuid4()), "x-role": role, "x-employee-id": emp or ""}


def test_employee_cannot_raise_correction_for_someone_else(monkeypatch):
    c = _client(FakeDB(), monkeypatch)
    body = {"employee_id": OTHER, "work_date": "2026-10-14", "reason": "missed_punch", "requested_in": "09:00"}
    assert c.post("/time/regularizations", json=body, headers=H("employee", PRIYA)).status_code == 403
    ok = c.post("/time/regularizations", json={**body, "employee_id": PRIYA}, headers=H("employee", PRIYA))
    assert ok.status_code == 201
    future = c.post("/time/regularizations", json={**body, "employee_id": PRIYA, "work_date": "2026-10-20"}, headers=H("employee", PRIYA))
    assert future.status_code == 422


def test_employee_cannot_open_someone_elses_month(monkeypatch):
    c = _client(FakeDB(), monkeypatch)
    assert c.get(f"/time/me/attendance?month=2026-10&employee_id={OTHER}", headers=H("employee", PRIYA)).status_code == 404
    assert c.get(f"/time/me/attendance?month=2026-10&employee_id={OTHER}", headers=H("manager", VIKRAM)).status_code == 404


def test_own_month_renders(monkeypatch):
    async def no_leave(_req, _eid):
        return []
    monkeypatch.setattr(ess_time, "_leave_rows", no_leave)
    c = _client(FakeDB(), monkeypatch)
    r = c.get("/time/me/attendance?month=2026-10", headers=H("employee", PRIYA))
    assert r.status_code == 200
    j = r.json()
    assert len(j["days"]) == 31 and j["employee"]["id"] == PRIYA
    assert {d["code"] for d in j["days"] if d["weekday"] in ("Sat", "Sun")} == {"WO"}


def test_requester_cannot_approve_own_correction(monkeypatch):
    from models import Regularization
    r = Regularization(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(PRIYA), manager_id=uuid.UUID(VIKRAM),
                       work_date=date(2026, 10, 14), reason="missed_punch", status="pending")
    r.id = uuid.uuid4()
    c = _client(FakeDB([r]), monkeypatch)
    assert c.post(f"/time/regularizations/{r.id}/approve", headers=H("hr_admin", PRIYA)).status_code == 403
    assert c.post(f"/time/regularizations/{r.id}/approve", headers=H("manager", OTHER)).status_code == 403
    assert c.post(f"/time/regularizations/{r.id}/reject", headers=H("employee", OTHER)).status_code == 403


def test_employee_cannot_list_someone_elses_punches(monkeypatch):
    c = _client(FakeDB(), monkeypatch)
    assert c.get(f"/time/punches?employee_id={OTHER}", headers=H("employee", PRIYA)).status_code == 403
    assert c.get(f"/time/punches?employee_id={PRIYA}", headers=H("employee", PRIYA)).status_code == 200


# ── batch 2: permission (short hours) ─────────────────────────────────────────

def _perm_client(db, monkeypatch):
    c = _client(db, monkeypatch)
    calls = []

    async def fake_open(*a, **k):
        calls.append(("open", k.get("kind"), k.get("context")))
        return "item"

    async def fake_close(*a, **k):
        calls.append(("close", k.get("kind"), k.get("decision")))
        return True
    monkeypatch.setattr(ess_time.approvals_client, "open_item", fake_open)
    monkeypatch.setattr(ess_time.approvals_client, "close_item", fake_close)
    app = c.app
    app.include_router(ess_time.perm_router)
    return TestClient(app), calls


def test_permission_only_for_self_and_goes_to_the_chain(monkeypatch):
    c, calls = _perm_client(FakeDB(), monkeypatch)
    body = {"work_date": "2026-10-20", "from_time": "16:00", "to_time": "17:30", "reason": "Bank visit"}
    assert c.post("/time/permissions", json={**body, "employee_id": OTHER}, headers=H("employee", PRIYA)).status_code == 403
    r = c.post("/time/permissions", json=body, headers=H("employee", PRIYA))
    assert r.status_code == 201 and r.json()["hours"] == 1.5
    assert calls and calls[0][1] == "permission" and calls[0][2]["hours"] == 1.5
    assert c.post("/time/permissions", json={**body, "employee_id": OTHER}, headers=H("hr_admin", VIKRAM)).status_code == 201


def test_permission_policy_caps(monkeypatch):
    c, _ = _perm_client(FakeDB(), monkeypatch)
    body = {"work_date": "2026-10-20", "from_time": "13:00", "to_time": "16:00", "reason": "Too long"}
    r = c.post("/time/permissions", json=body, headers=H("employee", PRIYA))
    assert r.status_code == 409 and "at most 2" in r.json()["detail"]
    bad = c.post("/time/permissions", json={**body, "to_time": "12:00"}, headers=H("employee", PRIYA))
    assert bad.status_code == 422


def test_permission_policy_edit_is_hr_only(monkeypatch):
    c, _ = _perm_client(FakeDB(), monkeypatch)
    assert c.put("/time/permissions/policy", json={"max_per_month": 3, "max_hours": 2}, headers=H("employee", PRIYA)).status_code == 403
    assert c.put("/time/permissions/policy", json={"max_per_month": 3, "max_hours": 2}, headers=H("manager", VIKRAM)).status_code == 403


def test_permission_decisions(monkeypatch):
    from models_ess import Permission
    p = Permission(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(PRIYA), manager_id=uuid.UUID(VIKRAM),
                   work_date=date(2026, 10, 20), from_time="16:00", to_time="17:00", hours=1, status="pending")
    p.id = uuid.uuid4()
    c, calls = _perm_client(FakeDB([p]), monkeypatch)
    assert c.post(f"/time/permissions/{p.id}/approve", headers=H("employee", PRIYA)).status_code == 403    # own
    assert c.post(f"/time/permissions/{p.id}/approve", headers=H("manager", OTHER)).status_code == 403     # not her manager
    assert c.post(f"/time/permissions/{p.id}/cancel", headers=H("employee", OTHER)).status_code == 403     # not hers
    ok = c.post(f"/time/permissions/{p.id}/approve", json={"comment": "ok"}, headers=H("manager", VIKRAM))
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert ("close", "permission", "approved") in calls
    assert c.post(f"/time/permissions/{p.id}/reject", headers=H("hr_admin", OTHER)).status_code == 409
