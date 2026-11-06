"""Global search — approvals (/approvals/search): a pending item only for someone who may act on it
(approver role / manager of the employee / admin), any item for the person who raised it, nothing
else. Run:  cd backend/services/approvals && PYTHONPATH="../..;." python -m pytest -q test_search.py
"""
import asyncio
import os
import sys
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers as R  # noqa: E402
import search_routes as SR  # noqa: E402
from database import get_db  # noqa: E402
from shared.utils.platform_models import ApprovalItem  # noqa: E402

ORG = str(uuid.uuid4())
PRIYA_U, RAVI_U, BOSS_U, HR_U = "u-priya", "u-ravi", "u-boss", "u-hr"
PRIYA_E, BOSS_E = str(uuid.uuid4()), str(uuid.uuid4())
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
Session = async_sessionmaker(engine, expire_on_commit=False)


async def _db():
    async with Session() as s:
        yield s


async def _ctx(org):
    employees = {PRIYA_E: {"id": PRIYA_E, "manager_id": BOSS_E}, BOSS_E: {"id": BOSS_E, "manager_id": None}}
    return [], {}, employees, []


async def _seed():
    async with engine.begin() as c:
        await c.run_sync(lambda conn: ApprovalItem.__table__.create(conn))
    async with Session() as s:
        s.add(ApprovalItem(org_id=ORG, kind="leave", ref_id="L1", title="Casual leave — Priya Menon",
                           requester_user_id=PRIYA_U, requester_name="Priya Menon", approver_roles=["hr_admin"],
                           approver_manager_of_employee_id=PRIYA_E, status="pending", summary={}))
        await s.commit()


app = FastAPI()
app.include_router(SR.router)
app.dependency_overrides[get_db] = _db
asyncio.run(_seed())
client = TestClient(app)


def titles(q, uid, role, emp=None, monkeypatch=None):
    h = {"X-Org-Id": ORG, "X-User-Id": uid, "X-Role": role, **({"X-Employee-Id": emp} if emp else {})}
    r = client.get("/approvals/search", params={"q": q}, headers=h)
    assert r.status_code == 200, r.text
    return [(x["title"], x["badge"]) for x in r.json()["hits"]]


def test_only_people_who_may_act_or_raised_it(monkeypatch):
    monkeypatch.setattr(R, "_context", _ctx)
    assert titles("priya", HR_U, "hr_admin") == [("Casual leave — Priya Menon", "waiting for you")]
    assert titles("casual", BOSS_U, "manager", BOSS_E) == [("Casual leave — Priya Menon", "waiting for you")]
    assert titles("priya", PRIYA_U, "employee", PRIYA_E) == [("Casual leave — Priya Menon", "pending")]   # own request
    assert titles("priya", RAVI_U, "employee") == []                                  # unrelated colleague
    assert titles("priya", RAVI_U, "manager", str(uuid.uuid4())) == []                # someone else's manager
