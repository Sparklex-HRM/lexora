"""Global search — attendance corrections + permissions (/time/search): HR = org, manager = own +
reports, employee = own. Run:  cd backend/services/time_attendance && PYTHONPATH="../..;." python -m pytest -q test_search.py
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
from models import Regularization  # noqa: E402
from models_ess import Permission  # noqa: E402

ORG = uuid.uuid4()
PRIYA, RAVI, BOSS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
Session = async_sessionmaker(engine, expire_on_commit=False)


async def _db():
    async with Session() as s:
        yield s


async def _seed():
    async with engine.begin() as c:
        await c.run_sync(lambda conn: Regularization.__table__.create(conn))
        await c.run_sync(lambda conn: Permission.__table__.create(conn))
    async with Session() as s:
        for emp, name, mgr in ((PRIYA, "Priya Menon", BOSS), (RAVI, "Ravi Kumar", None)):
            s.add(Regularization(org_id=ORG, employee_id=emp, employee_name=name, manager_id=mgr, work_date=date(2026, 10, 1),
                                 reason="missed_punch", status="pending"))
            s.add(Permission(org_id=ORG, employee_id=emp, employee_name=name, manager_id=mgr, work_date=date(2026, 10, 2),
                             from_time="15:00", to_time="17:00", hours=2, status="pending"))
        await s.commit()


app = FastAPI()
app.include_router(SR.router)
app.dependency_overrides[get_db] = _db
asyncio.run(_seed())
client = TestClient(app)


def H(role, emp=None):
    return {"X-Org-Id": str(ORG), "X-Role": role, **({"X-Employee-Id": str(emp)} if emp else {})}


def got(q, h, types):
    r = client.get("/time/search", params={"q": q, "types": types}, headers=h)
    assert r.status_code == 200, r.text
    return sorted(x["title"].split(" — ")[-1] for x in r.json()["hits"])


def test_corrections_and_permissions_scope():
    assert got("correction", H("hr_admin"), "attendance") == ["Priya Menon", "Ravi Kumar"]
    assert got("correction", H("manager", BOSS), "attendance") == ["Priya Menon"]
    assert got("correction", H("employee", RAVI), "attendance") == ["Ravi Kumar"]
    assert got("priya", H("employee", RAVI), "attendance") == []
    assert got("permission", H("employee", PRIYA), "permission") == ["Priya Menon"]
    assert got("permission", H("employee"), "permission") == []
