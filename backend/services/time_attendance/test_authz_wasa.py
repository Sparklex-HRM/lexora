"""WASA 2026-10-21 — access control on schedules, assignments, timesheets, analytics and the payroll
feed. Contract: employee = own rows (no one else's hourly_rate), manager = own + direct reports,
HR = org, pay office = read pay. Run:
  cd backend/services/time_attendance && PYTHONPATH="../..;." python -m pytest -q test_authz_wasa.py
"""
import asyncio
import os
import sys
import uuid
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@compiles(JSONB, "sqlite")
def _j(element, compiler, **kw):
    return "JSON"


# Postgres accepts uuid strings in comparisons (the routers pass header strings); sqlite's
# generic Uuid processor wants uuid objects — coerce, test-only.
from sqlalchemy.sql import sqltypes  # noqa: E402

_orig_bp = sqltypes.Uuid.bind_processor


def _lenient_bp(self, dialect):
    p = _orig_bp(self, dialect)
    if p is None:
        return None

    def proc(v):
        if isinstance(v, str):
            v = uuid.UUID(v)
        return p(v)
    return proc


sqltypes.Uuid.bind_processor = _lenient_bp


import routers as R  # noqa: E402
from database import get_db  # noqa: E402
from models import (  # noqa: E402
    AttendanceDay, Punch, Regularization, RosterShift, ScheduleAssignment, Timesheet, TimesheetLine,
    WorkSchedule,
)

ORG = uuid.uuid4()
BOSS, PRIYA, RAVI, ACC = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
EMPS = [
    {"id": str(BOSS), "first_name": "Boss", "last_name": "Lead", "manager_id": None, "status": "active",
     "pay_frequency": "biweekly", "department": "Eng"},
    {"id": str(PRIYA), "first_name": "Priya", "last_name": "Menon", "manager_id": str(BOSS), "status": "active",
     "pay_frequency": "biweekly", "department": "Eng"},
    {"id": str(RAVI), "first_name": "Ravi", "last_name": "Kumar", "manager_id": None, "status": "active",
     "pay_frequency": "biweekly", "department": "Ops"},
    {"id": str(ACC), "first_name": "Anu", "last_name": "Accounts", "manager_id": None, "status": "active",
     "pay_frequency": "biweekly", "department": "Finance"},
]
TS = {}
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
Session = async_sessionmaker(engine, expire_on_commit=False)


async def _db():
    async with Session() as s:
        yield s


async def _seed():
    async with engine.begin() as c:
        for m in (WorkSchedule, ScheduleAssignment, AttendanceDay, Timesheet, TimesheetLine, Punch,
                  Regularization, RosterShift):
            await c.run_sync(lambda conn, m=m: m.__table__.create(conn))
    async with Session() as s:
        for emp, name, mgr, rate in ((BOSS, "Boss Lead", None, 90), (PRIYA, "Priya Menon", BOSS, 40),
                                     (RAVI, "Ravi Kumar", None, 55)):
            t = Timesheet(org_id=ORG, employee_id=emp, employee_name=name, manager_id=mgr, province="ON",
                          pay_type="hourly", hourly_rate=rate, pay_frequency="biweekly",
                          period_start=date(2026, 10, 1), period_end=date(2026, 10, 14), reg_hours=8,
                          ot_hours=0, ot2_hours=0, stat_hours=0, stat_pay=0, total_hours=8, status="draft")
            s.add(t)
            await s.flush()
            s.add(TimesheetLine(org_id=ORG, timesheet_id=t.id, work_date=date(2026, 10, 1), reg_hours=8,
                                ot_hours=0, ot2_hours=0, stat_hours=0))
            s.add(AttendanceDay(org_id=ORG, employee_id=emp, work_date=date(2026, 10, 1), planned_hours=8,
                                actual_hours=8, status="present", late_minutes=0, is_holiday=False))
            TS[name.split()[0].lower()] = str(t.id)
        await s.commit()


async def _emps(_request):
    return [dict(e) for e in EMPS]


async def _none(_request):
    return []


async def _today(_org=None):
    return date(2026, 10, 14)


app = FastAPI()
app.include_router(R.router)
app.dependency_overrides[get_db] = _db
asyncio.run(_seed())
R._fetch_employees = _emps
R._fetch_stat_holidays = _none
R._today = _today
client = TestClient(app)


def H(role, emp=None):
    return {"X-Org-Id": str(ORG), "X-Role": role, **({"X-Employee-Id": str(emp)} if emp else {})}


EMP, MGR, ACCT = H("employee", PRIYA), H("manager", BOSS), H("accountant", ACC)
HR, PAY, ADMIN = H("hr_manager"), H("payroll_admin"), H("org_admin")


# ── schedules / assignments ───────────────────────────────────────────────────
def test_schedule_writes_hr_only():
    for h in (EMP, MGR, ACCT, PAY):
        assert client.post("/time/schedules", json={"name": "X"}, headers=h).status_code == 403
        assert client.post("/time/schedules/seed-defaults", headers=h).status_code == 403
        assert client.patch(f"/time/schedules/{uuid.uuid4()}", json={"name": "Y"}, headers=h).status_code == 403
    r = client.post("/time/schedules", json={"name": "Day"}, headers=HR)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert client.patch(f"/time/schedules/{sid}", json={"name": "Day 2"}, headers=ADMIN).json()["name"] == "Day 2"
    assert client.get("/time/schedules", headers=EMP).status_code == 200      # read stays ALL


def test_assignment_hr_or_own_manager():
    sid = client.post("/time/schedules", json={"name": "Night"}, headers=HR).json()["id"]
    body = {"schedule_id": sid}
    assert client.put(f"/time/assignments/{PRIYA}", json=body, headers=EMP).status_code == 403
    assert client.put(f"/time/assignments/{PRIYA}", json=body, headers=PAY).status_code == 403
    assert client.put(f"/time/assignments/{RAVI}", json=body, headers=MGR).status_code == 404   # not a report
    assert client.put(f"/time/assignments/{PRIYA}", json=body, headers=MGR).status_code == 200
    assert client.put(f"/time/assignments/{RAVI}", json=body, headers=HR).status_code == 200


# ── timesheets ────────────────────────────────────────────────────────────────
def _names(h):
    r = client.get("/time/timesheets", headers=h)
    assert r.status_code == 200, r.text
    return sorted(t["employee_name"] for t in r.json()), r.json()


def test_list_scope_and_rate():
    names, rows = _names(EMP)
    assert names == ["Priya Menon"] and rows[0]["hourly_rate"] == 40.0          # own rate kept
    names, rows = _names(MGR)
    assert names == ["Boss Lead", "Priya Menon"]
    by = {t["employee_name"]: t for t in rows}
    assert by["Boss Lead"]["hourly_rate"] == 90.0 and by["Priya Menon"]["hourly_rate"] is None
    assert _names(ACCT)[0] == []                                              # no own timesheet
    for h in (HR, PAY, ADMIN):
        names, rows = _names(h)
        assert names == ["Boss Lead", "Priya Menon", "Ravi Kumar"]
        assert all(t["hourly_rate"] for t in rows)


def test_detail_scope():
    assert client.get(f"/time/timesheets/{TS['ravi']}", headers=EMP).status_code == 404
    assert client.get(f"/time/timesheets/{TS['ravi']}", headers=MGR).status_code == 404
    assert client.get(f"/time/timesheets/{TS['ravi']}", headers=ACCT).status_code == 404
    r = client.get(f"/time/timesheets/{TS['priya']}", headers=MGR)
    assert r.status_code == 200 and r.json()["hourly_rate"] is None and r.json()["lines"]
    assert client.get(f"/time/timesheets/{TS['priya']}", headers=EMP).json()["hourly_rate"] == 40.0
    assert client.get(f"/time/timesheets/{TS['ravi']}", headers=PAY).json()["hourly_rate"] == 55.0


def test_edit_and_submit_owner_manager_hr():
    body = {"lines": [{"work_date": "2026-10-02", "reg_hours": 4}]}
    assert client.patch(f"/time/timesheets/{TS['ravi']}/lines", json=body, headers=EMP).status_code == 404
    assert client.post(f"/time/timesheets/{TS['ravi']}/submit", headers=EMP).status_code == 404
    assert client.patch(f"/time/timesheets/{TS['ravi']}/lines", json=body, headers=MGR).status_code == 404
    assert client.patch(f"/time/timesheets/{TS['ravi']}/lines", json=body, headers=PAY).status_code == 403
    assert client.patch(f"/time/timesheets/{TS['priya']}/lines", json=body, headers=EMP).status_code == 200
    assert client.patch(f"/time/timesheets/{TS['priya']}/lines", json=body, headers=MGR).status_code == 200
    assert client.patch(f"/time/timesheets/{TS['ravi']}/lines", json=body, headers=HR).status_code == 200
    assert client.post(f"/time/timesheets/{TS['boss']}/submit", headers=MGR).status_code == 200   # own
    assert client.post(f"/time/timesheets/{TS['priya']}/submit", headers=EMP).status_code == 200


def test_review_manager_only_own_reports():
    assert client.post(f"/time/timesheets/{TS['ravi']}/reject", json={}, headers=MGR).status_code == 404
    assert client.post(f"/time/timesheets/{TS['ravi']}/reject", json={}, headers=EMP).status_code == 403


def test_generate_hr_or_manager_team():
    body = {"period_start": "2026-09-01", "period_end": "2026-09-14"}
    assert client.post("/time/timesheets/generate", json=body, headers=EMP).status_code == 403
    assert client.post("/time/timesheets/generate", json=body, headers=ACCT).status_code == 403
    r = client.post("/time/timesheets/generate", json=body, headers=MGR)
    assert r.status_code == 201
    assert [t["employee_name"] for t in r.json()["timesheets"]] == ["Priya Menon"]
    assert r.json()["timesheets"][0]["hourly_rate"] is None


# ── payroll feed / readiness / analytics ──────────────────────────────────────
def test_feed_and_readiness_pay_office():
    q = {"pay_frequency": "biweekly", "period_start": "2026-10-01", "period_end": "2026-10-14"}
    for path in ("/time/payroll-feed", "/time/readiness"):
        for h in (EMP, MGR, ACCT):
            assert client.get(path, params=q, headers=h).status_code == 403
        for h in (HR, PAY, ADMIN):
            assert client.get(path, params=q, headers=h).status_code == 200


def test_analytics_scope():
    q = {"start": "2026-10-01", "end": "2026-10-14"}
    assert client.get("/time/analytics", params=q, headers=EMP).status_code == 403
    assert client.get("/time/analytics", params=q, headers=ACCT).status_code == 403
    m = client.get("/time/analytics", params=q, headers=MGR)
    assert m.status_code == 200 and m.json()["headline"]["employees_tracked"] == 2   # self + Priya
    for h in (HR, PAY, ADMIN):
        assert client.get("/time/analytics", params=q, headers=h).json()["headline"]["employees_tracked"] == 3


def test_employee_attendance_scope():
    assert client.get(f"/time/attendance/{RAVI}", headers=EMP).status_code == 404
    assert client.get(f"/time/attendance/{RAVI}", headers=MGR).status_code == 404
    assert client.get(f"/time/attendance/{PRIYA}", headers=EMP).status_code == 200
    assert client.get(f"/time/attendance/{PRIYA}", headers=MGR).status_code == 200
    assert client.get(f"/time/attendance/{RAVI}", headers=HR).status_code == 200
