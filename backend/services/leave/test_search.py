"""Global search — leave (/leave/search): HR = org, manager = own + their reports, employee = own.
Run:  cd backend/services/leave && PYTHONPATH="../..;." python -m pytest -q test_search.py
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


import search_routes as SR  # noqa: E402
from database import get_db  # noqa: E402
from models import LeaveRequest  # noqa: E402

ORG, OTHER = uuid.uuid4(), uuid.uuid4()
PRIYA, RAVI, BOSS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
Session = async_sessionmaker(engine, expire_on_commit=False)


async def _db():
    async with Session() as s:
        yield s


async def _seed():
    async with engine.begin() as c:
        await c.run_sync(lambda conn: LeaveRequest.__table__.create(conn))
    async with Session() as s:
        for org, emp, name, mgr in ((ORG, PRIYA, "Priya Menon", BOSS), (ORG, RAVI, "Ravi Kumar", None),
                                    (OTHER, uuid.uuid4(), "Priya Outsider", None)):
            s.add(LeaveRequest(org_id=org, employee_id=emp, employee_name=name, employee_number="K1", manager_id=mgr,
                               fiscal_year=2026, leave_type_code="VAC", leave_type_name="Vacation leave",
                               start_date=date(2026, 10, 5), end_date=date(2026, 10, 6), days=2, status="pending"))
        await s.commit()


app = FastAPI()
app.include_router(SR.router)
app.dependency_overrides[get_db] = _db
asyncio.run(_seed())
client = TestClient(app)


def H(role, emp=None):
    return {"X-Org-Id": str(ORG), "X-Role": role, **({"X-Employee-Id": str(emp)} if emp else {})}


def names(q, h):
    r = client.get("/leave/search", params={"q": q}, headers=h)
    assert r.status_code == 200, r.text
    return sorted(x["title"].split(" — ")[-1] for x in r.json()["hits"])


def test_scope_by_role():
    assert names("vacation", H("hr_admin")) == ["Priya Menon", "Ravi Kumar"]            # never the other org
    assert names("vacation", H("manager", BOSS)) == ["Priya Menon"]                     # own team only
    assert names("vacation", H("employee", RAVI)) == ["Ravi Kumar"]
    assert names("priya", H("employee", RAVI)) == []
    assert names("vacation", H("employee")) == []                                       # no employee record
    assert names("vacation", H("payroll_admin", RAVI)) == ["Ravi Kumar"]                # not a leave approver
