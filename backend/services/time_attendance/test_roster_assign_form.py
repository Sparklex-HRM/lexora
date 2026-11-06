"""2026-10-24 forms wave — roster shift editor: a hand-set day off and field limits on /roster/assign.
Run:  cd backend/services/time_attendance && python -m pytest -q test_roster_assign_form.py
"""
import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE.parent.parent), str(_HERE)):   # backend/ (for shared.*) and this service
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import routers as R  # noqa: E402
from models import RosterShift  # noqa: E402

ORG = uuid.uuid4()
EMP = str(uuid.uuid4())
DAY = date(2026, 11, 2)


class Req:
    def __init__(self, role="hr_admin"):
        self.headers = {"x-org-id": str(ORG), "x-role": role, "x-user-id": "u-hr"}


@pytest.fixture
def factory():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def make():
        async with eng.begin() as c:
            await c.run_sync(lambda conn: RosterShift.metadata.create_all(conn, tables=[RosterShift.__table__]))
    asyncio.run(make())
    return async_sessionmaker(eng, expire_on_commit=False)


def test_shift_in_limits():
    with pytest.raises(ValidationError):
        R.ShiftIn(employee_id=EMP, work_date="2026-11-02", planned_hours=25)
    with pytest.raises(ValidationError):
        R.ShiftIn(employee_id=EMP, work_date="2026-11-02", note="x" * 201)
    assert R.ShiftIn(employee_id=EMP, work_date="2026-11-02").kind == "work"


def test_day_off_then_back_to_work(factory, monkeypatch):
    # Postgres accepts uuid strings (the route passes header strings); sqlite's Uuid processor wants
    # uuid objects — coerce, test-only (same shim as test_authz_wasa).
    from sqlalchemy.sql import sqltypes
    orig = sqltypes.Uuid.bind_processor

    def lenient(self, dialect):
        p = orig(self, dialect)
        return None if p is None else (lambda v: p(uuid.UUID(v) if isinstance(v, str) else v))
    monkeypatch.setattr(sqltypes.Uuid, "bind_processor", lenient)

    async def go():
        async with factory() as db:
            body = R.RosterAssignIn(shifts=[R.ShiftIn(employee_id=EMP, work_date=DAY.isoformat(), kind="off",
                                                      start_time="09:00", end_time="17:00", planned_hours=8, note="Weekly off")])
            assert (await R.assign_roster(body, Req(), db))["upserted"] == 1
            s = (await db.execute(select(RosterShift))).scalar_one()
            assert (s.kind, s.shift_code, float(s.planned_hours), s.start_time, s.note) == ("off", "OFF", 0.0, "00:00", "Weekly off")
            body = R.RosterAssignIn(shifts=[R.ShiftIn(employee_id=EMP, work_date=DAY.isoformat(), start_time="10:00",
                                                      end_time="18:30", planned_hours=7.5, location="Plant 2")])
            await R.assign_roster(body, Req(), db)
            s = (await db.execute(select(RosterShift))).scalar_one()
            assert (s.kind, s.shift_code, s.start_time, s.location) == ("work", None, "10:00", "Plant 2")
            with pytest.raises(HTTPException) as e:
                await R.assign_roster(R.RosterAssignIn(shifts=[R.ShiftIn(employee_id=EMP, work_date=DAY.isoformat(), kind="holiday")]), Req(), db)
            assert e.value.status_code == 422
            with pytest.raises(HTTPException) as e:
                await R.assign_roster(R.RosterAssignIn(shifts=[R.ShiftIn(employee_id=EMP, work_date=DAY.isoformat(), start_time="25:00")]), Req(), db)
            assert e.value.status_code == 422
    asyncio.run(go())
