"""Gap G2 (2026-10-23) — shift rotation patterns, generated roster, swaps.
Run:  cd backend/services/time_attendance && python -m pytest -q test_rotation.py
"""
import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

import rotation_logic as RL  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_t, _c, **_k):
    return "JSON"


MON = date(2026, 11, 2)          # a Monday


# ══════════════════════════ pure ═══════════════════════════════════════════════

def test_templates_are_valid_and_shaped():
    for k in RL.TEMPLATES:
        t = RL.template_out(k)
        cy, sh = RL.validate_pattern(t["cycle"], t["shifts"])
        assert cy and sh
    abc = RL.template_out("three_shift_weekly")
    assert abc["cycle_days"] == 21 and abc["cycle"][:7] == ["A"] * 6 + ["OFF"] and abc["cycle"][14:21] == ["C"] * 6 + ["OFF"]
    assert abc["shifts"]["C"]["hours"] == 7.5                       # 22:00–06:00 overnight − 30 min
    assert RL.template_out("four_on_two_off")["cycle"] == ["D"] * 4 + ["OFF"] * 2
    rot = RL.template_out("rotating_weekly_off")["cycle"]
    offs = [i for i, c in enumerate(rot) if c == "OFF"]
    assert len(rot) == 49 and offs == [6, 7, 15, 23, 31, 39, 47]    # Sunday, then Monday, Tuesday … one per week


def test_validation_messages():
    with pytest.raises(RL.PatternError, match="not defined"):
        RL.validate_pattern(["A", "X"], {"A": {"start": "09:00", "end": "17:00"}})
    with pytest.raises(RL.PatternError, match="HH:MM"):
        RL.validate_pattern(["A"], {"A": {"start": "9", "end": "17:00"}})
    with pytest.raises(RL.PatternError, match="no working day"):
        RL.validate_pattern(["OFF"], {"A": {"start": "09:00", "end": "17:00"}})
    with pytest.raises(RL.PatternError, match="at most"):
        RL.validate_pattern(["A"] * 85, {"A": {"start": "09:00", "end": "17:00"}})


def test_custom_n_day_cycle_and_offset_preview():
    cy, sh = RL.validate_pattern(["n", "n", "off", "d", "d", "off", "off"], {"N": {"start": "20:00", "end": "08:00", "break_minutes": 60},
                                                                             "D": {"start": "08:00", "end": "18:00", "break_minutes": 60}})
    assert cy == ["N", "N", "OFF", "D", "D", "OFF", "OFF"] and sh["N"]["hours"] == 11
    p = RL.preview(cy, sh, MON, 9, offset=3)
    assert [x["code"] for x in p] == ["D", "D", "OFF", "OFF", "N", "N", "OFF", "D", "D"]
    assert p[4]["night"] is True and p[0]["night"] is False


def test_three_crews_cover_every_shift():
    cov = RL.coverage(RL.TEMPLATES["three_shift_weekly"]["cycle"], 3, 7)
    for day in cov["per_day"]:
        assert set(day) <= {"A", "B", "C", "OFF"}
    # crews 7 days apart: Monday–Saturday each of A, B, C is worked by exactly one crew; Sunday the plant rests
    for i, day in enumerate(cov["per_day"]):
        assert day == ({"OFF": 3} if i % 7 == 6 else {"A": 1, "B": 1, "C": 1}), (i, day)


EMP = {"e1": {"first_name": "Ravi", "last_name": "K", "gender": "male"},
       "e2": {"first_name": "Neha", "last_name": "G", "gender": "female"}}
ABC = {"p": {"cycle": RL.TEMPLATES["three_shift_weekly"]["cycle"], "shifts": RL.TEMPLATES["three_shift_weekly"]["shifts"], "name": "ABC"}}


def _plan(**kw):
    base = dict(assignments=[{"id": "a1", "pattern_id": "p", "employee_id": "e1", "start_date": MON, "offset_days": 0}],
                patterns=ABC, employees=EMP, start=MON, end=MON + timedelta(days=20))
    base.update(kw)
    return RL.plan(**base)


def test_plan_generates_the_cycle_with_offs():
    r = _plan()
    work = [x for x in r["rows"] if x["kind"] == "work"]
    off = [x for x in r["rows"] if x["kind"] == "off"]
    assert len(work) == 18 and len(off) == 3 and not r["conflicts"]
    assert work[0]["start_time"] == "06:00" and work[0]["shift_code"] == "A" and work[0]["planned_hours"] == 7.5
    assert off[0]["work_date"] == (MON + timedelta(days=6)).isoformat() and off[0]["note"].startswith("Weekly off")


def test_plan_respects_leave_holiday_and_manual_rows():
    lv = {"e1": {MON + timedelta(days=1)}}
    hol = {"e1": {MON + timedelta(days=2): "Civic Holiday"}}
    ex = {("e1", (MON + timedelta(days=3)).isoformat()): {"id": "x1", "source": "manual", "status": "planned", "kind": "work",
                                                          "start_time": "10:00", "end_time": "18:00"},
          ("e1", (MON + timedelta(days=4)).isoformat()): {"id": "x2", "source": "pattern", "status": "planned", "kind": "work",
                                                          "start_time": "06:00", "end_time": "14:00"}}
    r = _plan(leave=lv, holidays=hol, existing=ex, end=MON + timedelta(days=6))
    dates = {x["work_date"]: x for x in r["rows"]}
    assert (MON + timedelta(days=1)).isoformat() not in dates and r["skipped"][0]["reason"] == "on approved leave"
    assert dates[(MON + timedelta(days=2)).isoformat()]["kind"] == "off" and "Civic Holiday" in dates[(MON + timedelta(days=2)).isoformat()]["note"]
    assert (MON + timedelta(days=3)).isoformat() not in dates and r["kept"][0]["reason"].startswith("kept — set by hand")
    assert dates[(MON + timedelta(days=4)).isoformat()]["existing_id"] == "x2"          # a generated row is refreshed in place
    r2 = _plan(existing=ex, end=MON + timedelta(days=6), overwrite_manual=True)
    assert any(x.get("existing_id") == "x1" for x in r2["rows"])


def test_published_rows_are_kept_unless_asked():
    ex = {("e1", MON.isoformat()): {"id": "x1", "source": "pattern", "status": "published", "kind": "work", "start_time": "06:00", "end_time": "14:00"}}
    assert _plan(existing=ex, end=MON)["kept"][0]["reason"] == "kept — already published"
    assert _plan(existing=ex, end=MON, overwrite_published=True)["rows"][0]["existing_id"] == "x1"


def test_night_shifts_plan_like_any_other_shift():
    a = [{"id": "a2", "pattern_id": "p", "employee_id": "e2", "start_date": MON, "offset_days": 14}]     # starts on nights (C)
    r = _plan(assignments=a, end=MON + timedelta(days=1))
    assert not r["conflicts"] and len([x for x in r["rows"] if x["kind"] == "work"]) == 2


def test_minimum_rest_between_shifts():
    # yesterday (existing, hand-made) a night 22:00–06:00; today the rotation says morning 06:00 → 0 h rest
    ex = {("e1", (MON - timedelta(days=1)).isoformat()): {"id": "n", "source": "manual", "status": "published", "kind": "work",
                                                          "start_time": "22:00", "end_time": "06:00"}}
    r = _plan(existing=ex, end=MON, min_rest_hours=11)
    assert r["conflicts"][0]["code"] == "rest" and "0 h of rest" in r["conflicts"][0]["reason"]
    assert not _plan(existing=ex, end=MON, min_rest_hours=0)["conflicts"]
    # within the rotation: C week ends Sunday OFF then A Monday — the OFF day gives the rest
    full = _plan(assignments=[{"id": "a", "pattern_id": "p", "employee_id": "e1", "start_date": MON, "offset_days": 0}],
                 end=MON + timedelta(days=27), min_rest_hours=11)
    assert not full["conflicts"]


def test_swap_problems_rest_and_night():
    a = {"id": "s1", "employee_id": "e1", "work_date": MON.isoformat(), "start_time": "06:00", "end_time": "14:00", "kind": "work"}
    b = {"id": "s2", "employee_id": "e2", "work_date": MON.isoformat(), "start_time": "22:00", "end_time": "06:00", "kind": "work"}
    e1_next = {"id": "s3", "employee_id": "e1", "work_date": (MON + timedelta(days=1)).isoformat(), "start_time": "06:00", "end_time": "14:00"}
    p = RL.swap_problems(a_emp={"id": "e1", "first_name": "Ravi"}, b_emp={"id": "e2", "first_name": "Neha", "gender": "female"},
                         a_shift=a, b_shift=b, a_neighbours=[a, e1_next], b_neighbours=[b], min_rest_hours=11)
    assert any("Ravi would get only 0 h of rest" in x for x in p)          # Ravi takes the night, then 06:00 next day
    assert not any("Neha" in x for x in p)                                 # Neha takes a morning — fine
    b_morning = {**b, "start_time": "14:00", "end_time": "22:00"}
    a_night = {**a, "start_time": "22:00", "end_time": "06:00"}
    p2 = RL.swap_problems(a_emp={"id": "e1", "first_name": "Ravi"}, b_emp={"id": "e2", "first_name": "Neha", "gender": "female"},
                          a_shift=a_night, b_shift=b_morning, a_neighbours=[a_night], b_neighbours=[b_morning], min_rest_hours=0)
    assert p2 == []                                                        # no rest rule, no problem


# ══════════════════════════ routes (sqlite) ════════════════════════════════════

ORG = str(uuid.uuid4())
E_RAVI, E_ASHA, E_NEHA, E_MGR = (str(uuid.uuid4()) for _ in range(4))
EMPS = {E_RAVI: {"id": E_RAVI, "first_name": "Ravi", "last_name": "K", "employee_number": "LX030", "gender": "male",
                 "department": "Plant", "manager_id": E_MGR, "status": "active", "province_of_employment": "ON"},
        E_ASHA: {"id": E_ASHA, "first_name": "Asha", "last_name": "R", "employee_number": "LX031", "gender": "female",
                 "department": "Plant", "manager_id": E_MGR, "status": "active", "province_of_employment": "ON"},
        E_NEHA: {"id": E_NEHA, "first_name": "Neha", "last_name": "G", "employee_number": "LX032", "gender": "female",
                 "department": "Plant", "manager_id": E_MGR, "status": "active", "province_of_employment": "ON"},
        E_MGR: {"id": E_MGR, "first_name": "Vikram", "last_name": "Rao", "employee_number": "LX008", "gender": "male",
                "department": "Plant", "manager_id": None, "status": "active", "province_of_employment": "ON"}}


class Req:
    def __init__(self, role="hr_admin", emp="", uid="u-hr"):
        self.headers = {"x-org-id": ORG, "x-role": role, "x-user-id": uid, "x-user-email": f"{uid}@k",
                        **({"x-employee-id": emp} if emp else {})}


@pytest.fixture
def api(monkeypatch):
    import rotation_routers as RR
    from models import RosterShift
    from rotation_models import RotationAssignment, RotationPattern, RotationSettings, ShiftSwap
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def make():
        async with eng.begin() as c:
            await c.run_sync(lambda conn: RosterShift.metadata.create_all(conn, tables=[
                RosterShift.__table__, RotationPattern.__table__, RotationAssignment.__table__, ShiftSwap.__table__,
                RotationSettings.__table__]))
    asyncio.run(make())

    async def emps(db, org):
        return EMPS

    async def leave(db, org, d0, d1):
        return {E_RAVI: {MON + timedelta(days=2)}}

    async def hols(db, org, d0, d1, e):
        return {}

    async def today(org=None):
        return MON - timedelta(days=1)
    opened, closed = [], []

    async def open_item(org, **kw):
        opened.append(kw)
        return "item-1"

    async def close_item(org, **kw):
        closed.append(kw)
    import shared.utils.approvals_client as AC
    monkeypatch.setattr(AC, "open_item", open_item)
    monkeypatch.setattr(AC, "close_item", close_item)
    monkeypatch.setattr(RR, "employees_sql", emps)
    monkeypatch.setattr(RR, "leave_sql", leave)
    monkeypatch.setattr(RR, "holidays_sql", hols)
    monkeypatch.setattr(RR, "org_today", today)

    async def no_tell(*a, **k):
        return None
    monkeypatch.setattr(RR, "_tell", no_tell)
    import shared.utils.audit as AU

    async def no_audit(*a, **k):
        return True
    monkeypatch.setattr(AU, "record_audit", no_audit)
    return RR, async_sessionmaker(eng, expire_on_commit=False), {"opened": opened, "closed": closed}


def test_pattern_assign_generate_publish_swap_flow(api):
    RR, factory, box = api
    from models import RosterShift

    async def go():
        async with factory() as db:
            t = RL.template_out("three_shift_weekly")
            with pytest.raises(HTTPException):           # employees / managers cannot define patterns
                await RR.create_pattern(RR.PatternIn(name="ABC", cycle=t["cycle"], shifts=t["shifts"]), Req("manager", E_MGR, "u-m"), db)
            p = await RR.create_pattern(RR.PatternIn(name="Plant ABC", template="three_shift_weekly", cycle=t["cycle"], shifts=t["shifts"]), Req(), db)
            assert p["cycle_days"] == 21 and p["night"] is True
            # the manager puts the 3 reports on it as crews A/B/C (stagger 7 days)
            a = await RR.create_assignment(RR.AssignIn(pattern_id=p["id"], employee_ids=[E_RAVI, E_ASHA, E_NEHA], start_date=MON.isoformat(),
                                                       stagger_days=7), Req("manager", E_MGR, "u-m"), db)
            assert [x["offset_days"] for x in a["assignments"]] == [0, 7, 14]
            with pytest.raises(HTTPException):          # not the manager's report
                await RR.create_assignment(RR.AssignIn(pattern_id=p["id"], employee_ids=[E_MGR], start_date=MON.isoformat()),
                                           Req("manager", E_RAVI, "u-r"), db)
            dry = await RR.generate(RR.GenerateIn(start=MON.isoformat(), weeks=1, dry_run=True), Req("manager", E_MGR, "u-m"), db)
            assert dry["created"] == 0 and dry["employees"] == 3
            # Ravi's leave day skipped; a dry run writes nothing
            assert any(s["reason"] == "on approved leave" for s in dry["skipped"])
            assert not (await db.execute(select(RosterShift))).scalars().all()
            done = await RR.generate(RR.GenerateIn(start=MON.isoformat(), weeks=1, dry_run=False), Req(), db)
            assert done["created"] == 20 and not done["conflicts"]           # 21 − Ravi's leave day
            again = await RR.generate(RR.GenerateIn(start=MON.isoformat(), weeks=1, dry_run=False), Req(), db)
            assert again["created"] == 0 and again["updated"] == 20          # idempotent
            rows = (await db.execute(select(RosterShift))).scalars().all()
            assert all(r.source == "pattern" and r.status == "planned" for r in rows)
            # publish (what /time/roster/publish does) — then attendance reads the generated row like a manual one
            for r in rows:
                r.status = "published"
            await db.commit()
            off = next(r for r in rows if r.kind == "off")
            assert await RR.roster_overlay(db, ORG, off.employee_id, off.work_date, 8.0, True, "09:00", 30) == (0.0, False, None, 30)
            w = next(r for r in rows if r.kind == "work" and str(r.employee_id) == E_ASHA)
            assert await RR.roster_overlay(db, ORG, w.employee_id, w.work_date, 8.0, True, "09:00", 30) == (7.5, True, "14:00", 30)
            # Ravi (mornings) asks Asha (afternoons) to swap the same day
            ra = next(r for r in rows if str(r.employee_id) == E_RAVI and r.work_date == MON)
            sa = next(r for r in rows if str(r.employee_id) == E_ASHA and r.work_date == MON)
            opts = await RR.swap_options(str(ra.id), Req("employee", E_RAVI, "u-r"), db)
            # with the default 11 h company rest, Ravi finishing at 22:00 and back at 06:00 Tuesday is flagged …
            assert any(o["id"] == str(sa.id) and any("8 h of rest" in p for p in o["problems"]) for o in opts["options"])
            await RR.put_settings(RR.SettingsIn(min_rest_hours=8), Req(), db)       # … a company that allows 8 h
            opts = await RR.swap_options(str(ra.id), Req("employee", E_RAVI, "u-r"), db)
            assert any(o["id"] == str(sa.id) and not o["problems"] for o in opts["options"])
            sw = await RR.request_swap(RR.SwapIn(my_shift_id=str(ra.id), partner_shift_id=str(sa.id), reason="Doctor visit"),
                                       Req("employee", E_RAVI, "u-r"), db)
            assert sw["status"] == "pending_partner"
            with pytest.raises(HTTPException):          # someone else's shift cannot be offered
                await RR.request_swap(RR.SwapIn(my_shift_id=str(sa.id), partner_shift_id=str(ra.id)), Req("employee", E_RAVI, "u-r"), db)
            with pytest.raises(HTTPException):          # only the colleague accepts
                await RR.accept_swap(sw["id"], Req("employee", E_RAVI, "u-r"), None, db)
            sw = await RR.accept_swap(sw["id"], Req("employee", E_ASHA, "u-a"), None, db)
            assert sw["status"] == "pending_manager" and box["opened"][-1]["kind"] == "shift_swap"
            assert box["opened"][-1]["approver_manager_of_employee_id"] == E_RAVI
            with pytest.raises(HTTPException):          # a party to the swap cannot approve it
                await RR.approve_swap(sw["id"], Req("employee", E_ASHA, "u-a"), None, db)
            sw = await RR.approve_swap(sw["id"], Req("manager", E_MGR, "u-m"), RR.NoteIn(comment="ok"), db)
            assert sw["status"] == "approved" and box["closed"][-1]["decision"] == "approved"
            await db.refresh(ra)
            await db.refresh(sa)
            assert str(ra.employee_id) == E_ASHA and str(sa.employee_id) == E_RAVI and ra.source == "swap"
            # a regenerate never undoes the swap (source = swap is kept like a manual row)
            again = await RR.generate(RR.GenerateIn(start=MON.isoformat(), weeks=1, dry_run=False, overwrite_published=True), Req(), db)
            await db.refresh(ra)
            assert str(ra.employee_id) == E_ASHA and again["counts"]["kept"] >= 2
    asyncio.run(go())


def test_swap_breaking_rest_is_refused(api):
    RR, factory, box = api
    from models import RosterShift

    async def go():
        async with factory() as db:
            oid = uuid.UUID(ORG)
            n = RosterShift(org_id=oid, employee_id=uuid.UUID(E_ASHA), work_date=MON, start_time="22:00", end_time="06:00",
                            planned_hours=7.5, status="published", kind="work", source="manual")
            m = RosterShift(org_id=oid, employee_id=uuid.UUID(E_RAVI), work_date=MON, start_time="06:00", end_time="14:00",
                            planned_hours=7.5, status="published", kind="work", source="manual")
            m2 = RosterShift(org_id=oid, employee_id=uuid.UUID(E_RAVI), work_date=MON + timedelta(days=1), start_time="06:00",
                             end_time="14:00", planned_hours=7.5, status="published", kind="work", source="manual")
            db.add_all([n, m, m2])
            await db.commit()
            with pytest.raises(HTTPException) as ex:
                await RR.request_swap(RR.SwapIn(my_shift_id=str(m.id), partner_shift_id=str(n.id)), Req("employee", E_RAVI, "u-r"), db)
            assert ex.value.status_code == 409 and ex.value.detail["code"] == "TA_ROTA_SWAP_RULES"
            assert "rest" in ex.value.detail["message"]
    asyncio.run(go())


def test_settings_and_auto_generate(api):
    RR, factory, box = api

    async def go():
        async with factory() as db:
            s = await RR.get_settings(Req(), db)
            assert s["min_rest_hours"] == 11 and "Company rule" in s["rest_note"] and s["auto_generate"] is False
            with pytest.raises(HTTPException):
                await RR.put_settings(RR.SettingsIn(min_rest_hours=30), Req(), db)
            with pytest.raises(HTTPException):
                await RR.put_settings(RR.SettingsIn(), Req("manager", E_MGR, "u-m"), db)
            s = await RR.put_settings(RR.SettingsIn(min_rest_hours=10, weeks_ahead=2, auto_generate=True), Req(), db)
            assert s["weeks_ahead"] == 2 and s["auto_generate"] is True
    asyncio.run(go())
