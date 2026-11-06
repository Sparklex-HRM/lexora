"""Reports arithmetic + inbox sync / reminders — small hand-computed fixtures.

Run:  cd backend/services/approvals && python -m pytest -q test_reports_and_inbox.py
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import inbox as ib  # noqa: E402
import reports_calc as rc  # noqa: E402
import reports_routers as rr  # noqa: E402
import routers as ar  # noqa: E402
from shared.utils import directory, notify as nt  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

D = date


def emp(i, hire, term=None, dept="Ops", **kw):
    return {"id": i, "first_name": "Emp", "last_name": i, "employee_number": i.upper(), "department": dept,
            "hire_date": hire, "termination_date": term, "status": "terminated" if term else "active", **kw}


# 10 on the rolls on 1 Jan; 2 leave in the quarter; 4 join → 12 on 31 Mar
STAFF = ([emp(f"a{n}", D(2024, 1, 1)) for n in range(8)]
         + [emp("l1", D(2024, 1, 1), D(2026, 1, 20)), emp("l2", D(2024, 1, 1), D(2026, 3, 31), dept="Sales")]
         + [emp(f"j{n}", D(2026, 2, 10), dept="Sales") for n in range(4)])


# ══════════════════════════ headcount / attrition ══════════════════════════════

def test_headcount_and_last_day_still_counts():
    assert rc.headcount_on(STAFF, D(2026, 1, 1)) == 10
    assert rc.headcount_on(STAFF, D(2026, 1, 20)) == 10          # the last working day is still on the rolls
    assert rc.headcount_on(STAFF, D(2026, 1, 21)) == 9
    assert rc.headcount_on(STAFF, D(2026, 3, 31)) == 13          # l2's last day
    assert rc.headcount_on(STAFF, D(2026, 4, 1)) == 12
    assert rc.headcount_on([], D(2026, 1, 1)) == 0
    # still onboarding with no joining date → not counted; no joining date otherwise → from creation
    assert not rc.employed_on({"status": "onboarding", "created_at": "2026-01-01T00:00:00"}, D(2026, 6, 1))
    assert rc.employed_on({"status": "active", "created_at": "2026-01-01T00:00:00"}, D(2026, 6, 1))


def test_headcount_by_department_shares():
    out = rc.headcount_by(STAFF, "department", D(2026, 4, 1))
    assert out["rows"] == [{"group": "Ops", "headcount": 8, "share": 66.7}, {"group": "Sales", "headcount": 4, "share": 33.3}]
    assert out["totals"]["headcount"] == 12
    assert rc.headcount_by([], "department", D(2026, 4, 1))["rows"] == []


def test_attrition_formula():
    f = rc.attrition_figures(STAFF, D(2026, 1, 1), D(2026, 3, 31))
    # opening 10, closing 13 (l2 still there on the 31st), leavers 2 → 2 / 11.5 = 17.4 %
    assert (f["opening"], f["closing"], f["leavers"], f["average"]) == (10, 13, 2, 11.5)
    assert f["attrition_pct"] == 17.4
    rep = rc.attrition(STAFF, D(2026, 1, 1), D(2026, 3, 31))
    assert [r["leavers"] for r in rep["rows"]] == [1, 0, 1]
    assert rep["rows"][0]["attrition"] == round(1 / 9.5 * 100, 1)       # Jan: (10 + 9) / 2
    assert rep["totals"]["attrition"] == 17.4 and "average headcount" in rep["formula"]
    assert rc.attrition_figures([], D(2026, 1, 1), D(2026, 3, 31))["attrition_pct"] is None   # never a made-up 0 %


def test_joiners_leavers_and_trend():
    jl = rc.joiners_leavers(STAFF, D(2026, 1, 1), D(2026, 3, 31))
    assert [(r["joiners"], r["leavers"], r["net"]) for r in jl["rows"]] == [(0, 1, -1), (4, 0, 4), (0, 1, -1)]
    assert jl["totals"] == {"month": "Total", "joiners": 4, "leavers": 2, "net": 2}
    tr = rc.headcount_trend(STAFF, D(2026, 1, 1), D(2026, 3, 15))
    assert [r["headcount"] for r in tr["rows"]] == [9, 13, 13] and tr["rows"][2]["counted_on"] == "2026-03-15"


def test_probation_and_birthdays():
    people = [emp("p1", D(2026, 6, 1), probation_end_date=D(2026, 10, 20)),
              emp("p2", D(2026, 5, 1), probation_end_date=D(2026, 10, 2)),
              emp("p3", D(2026, 1, 1), probation_end_date=D(2027, 6, 1)),
              emp("p4", D(2020, 10, 15), date_of_birth=D(1990, 10, 14)),
              emp("p5", D(2024, 2, 29), date_of_birth=D(1992, 2, 29))]
    rows = rc.probation_due(people, D(2026, 10, 12))["rows"]
    assert [(r["employee_number"], r["days_left"], r["state"]) for r in rows] == \
        [("P2", -10, "Overdue by 10 day(s)"), ("P1", 8, "Due in 8 day(s)")]
    b = rc.birthdays_anniversaries(people, D(2026, 10, 12))["rows"]
    assert [(r["employee"], r["occasion"], r["on"], r["detail"]) for r in b] == \
        [("Emp p4", "Birthday", "2026-10-14", ""), ("Emp p4", "Work anniversary", "2026-10-15", "6 year(s)")]
    feb = rc.birthdays_anniversaries(people, D(2027, 2, 20))["rows"]       # 29 Feb in a non-leap year → 28 Feb
    assert ("Emp p5", "Birthday", "2027-02-28") in [(r["employee"], r["occasion"], r["on"]) for r in feb]
    assert all("1990" not in str(r) and "1992" not in str(r) for r in b + feb)   # birth year never shown


# ══════════════════════════ time and leave ═════════════════════════════════════

def test_attendance_summary():
    days = ([{"employee_id": "e1", "status": "present", "planned_hours": 8, "actual_hours": 8, "late_minutes": 0}] * 3
            + [{"employee_id": "e1", "status": "late", "planned_hours": 8, "actual_hours": 7.5, "late_minutes": 30},
               {"employee_id": "e1", "status": "absent", "planned_hours": 8, "actual_hours": 0, "late_minutes": 0},
               {"employee_id": "e1", "status": "leave", "planned_hours": 0, "actual_hours": 0, "late_minutes": 0},
               {"employee_id": "e1", "status": "rest_day", "planned_hours": 0, "actual_hours": 0, "late_minutes": 0}])
    r = rc.attendance_summary(days, {"e1": emp("e1", D(2024, 1, 1))})["rows"][0]
    assert (r["days_worked"], r["late_days"], r["absent_days"], r["leave_days"]) == (4, 1, 1, 1)
    assert (r["planned_hours"], r["actual_hours"], r["late_minutes"]) == (40.0, 31.5, 30)
    assert r["attendance"] == 80.0                                # 4 / (4 + 1)
    assert rc.attendance_summary([], {})["rows"] == []


def test_leave_balances_usage_overtime():
    b = rc.leave_balances([{"employee_id": "e1", "fiscal_year": 2026, "leave_type_name": "Earned", "allocated": 18,
                            "carried_over": 4, "used": 5.5, "pending": 2}], {"e1": emp("e1", D(2024, 1, 1))})["rows"][0]
    assert (b["entitled"], b["remaining"], b["used_share"]) == (22.0, 14.5, 25.0)
    reqs = [{"status": "approved", "leave_type_name": "Earned", "start_date": D(2026, 1, 30), "days": 3},
            {"status": "approved", "leave_type_name": "Earned", "start_date": D(2026, 2, 2), "days": 1.5},
            {"status": "approved", "leave_type_name": "Sick", "start_date": D(2026, 2, 9), "days": 1},
            {"status": "pending", "leave_type_name": "Sick", "start_date": D(2026, 2, 10), "days": 9},
            {"status": "approved", "leave_type_name": "Sick", "start_date": D(2025, 12, 31), "days": 9}]
    u = rc.leave_usage(reqs, D(2026, 1, 1), D(2026, 2, 28))
    assert u["rows"] == [{"leave_type": "Earned", "total": 4.5, "2026-01": 3.0, "2026-02": 1.5},
                         {"leave_type": "Sick", "total": 1.0, "2026-01": 0.0, "2026-02": 1.0}]
    assert u["totals"]["total"] == 5.5 and u["chart"]["data"][1] == {"month": "Feb 2026", "days": 2.5}
    ts = [{"employee_id": "e1", "status": "approved", "reg_hours": 80, "ot_hours": 6, "ot2_hours": 2},
          {"employee_id": "e1", "status": "locked", "reg_hours": 72, "ot_hours": 0, "ot2_hours": 0},
          {"employee_id": "e1", "status": "submitted", "reg_hours": 80, "ot_hours": 50, "ot2_hours": 0},
          {"employee_id": "e2", "status": "approved", "reg_hours": 80, "ot_hours": 0, "ot2_hours": 0}]
    o = rc.overtime(ts, {"e1": emp("e1", D(2024, 1, 1))})
    assert len(o["rows"]) == 1 and o["rows"][0]["total_overtime"] == 8.0 and o["rows"][0]["regular_hours"] == 152.0
    assert o["rows"][0]["overtime_share"] == 5.0                  # 8 / 160


# ══════════════════════════ payroll: finalised runs only ═══════════════════════

def stub(e, pay_date, status, gross, net, er_cpp=0.0, er_ei=0.0, cpp=0.0, ei=0.0, tax=0.0, run="r1"):
    return {"employee_id": e, "pay_run_id": run, "pay_date": pay_date, "run_status": status, "gross": gross,
            "net_pay": net, "cpp": cpp, "cpp2": 0, "ei": ei, "income_tax": tax,
            "detail": {"employer_cost": {"cpp": er_cpp, "ei": er_ei}, "deductions": {}}}


STUBS = [stub("e1", D(2026, 9, 30), "calculated", 5000, 3800, er_cpp=280, er_ei=0, cpp=280, tax=900, run="r9"),
         stub("e2", D(2026, 9, 30), "paid", 2000, 1700, er_cpp=110, er_ei=45.64, cpp=110, ei=32.6, run="r9"),
         stub("e1", D(2026, 10, 31), "approved", 5000, 3800, er_cpp=280, cpp=280, tax=900, run="r10"),
         stub("e1", D(2026, 10, 31), "draft", 99999, 99999, er_cpp=9999, cpp=9999, tax=9999, run="rX"),
         stub("e2", D(2026, 10, 31), "cancelled", 88888, 88888, run="rY")]
PEOPLE = {"e1": emp("e1", D(2024, 1, 1), dept="Ops"), "e2": emp("e2", D(2024, 1, 1), dept="Sales")}


def test_payroll_cost_uses_finalised_runs_only():
    out = rc.payroll_cost(STUBS, PEOPLE, "month")
    assert [(r["group"], r["gross"], r["employer_contributions"], r["total_cost"], r["employees_paid"]) for r in out["rows"]] == \
        [("Sep 2026", 7000.0, 435.64, 7435.64, 2), ("Oct 2026", 5000.0, 280.0, 5280.0, 1)]
    assert out["totals"]["total_cost"] == 12715.64 and out["totals"]["net_paid"] == 9300.0
    assert any("2 pay stub(s)" in n for n in out["notes"])           # the draft + cancelled ones, said out loud
    dept = rc.payroll_cost(STUBS, PEOPLE, "department")
    assert [(r["group"], r["total_cost"]) for r in dept["rows"]] == [("Ops", 10560.0), ("Sales", 2155.64)]
    only_draft = rc.payroll_cost([STUBS[3]], PEOPLE, "month")
    assert only_draft["rows"] == [] and only_draft["totals"] is None  # nothing finalised → nothing shown


def test_employer_cost_sums_cpp_ei_qpip_eht_wcb():
    assert rc.employer_cost_of({"employer_cost": {"cpp": 280, "cpp2": 16, "ei": 45.64, "qpip": 12.04, "eht": 9.75,
                                                  "wcb": 22.5}}) == 385.93
    assert rc.employer_cost_of(None) == 0 and rc.employer_cost_of({}) == 0


def test_statutory_liability_is_the_cra_remittance_per_month():
    out = rc.statutory_liability(STUBS)
    sep, octo = out["rows"]
    assert (sep["a_employee"], sep["a_employer"], sep["b_employee"], sep["b_employer"], sep["tax"]) == \
        (390.0, 390.0, 32.6, 45.64, 900.0)
    assert sep["total"] == 1758.24 and octo["total"] == 1460.0
    ca = rc.statutory_liability([{**STUBS[0], "cpp2": 100}])
    assert ca["rows"][0]["a_employee"] == 380.0
    assert [c["label"] for c in ca["columns"]][1] == "CPP — employee"


def test_state_counts_are_counts_only():
    out = rc.state_counts([("Offers", [{"state": "accepted"}, {"state": "accepted"}, {"state": "sent"}, {"state": None}]),
                           ("Requisitions", [])])
    assert out["rows"] == [{"what": "Offers", "state": "Accepted", "count": 2, "share": 50.0},
                           {"what": "Offers", "state": "Not set", "count": 1, "share": 25.0},
                           {"what": "Offers", "state": "Sent", "count": 1, "share": 25.0}]
    assert set(rr.GENERIC) == {"exit_cases", "hiring_pipeline", "performance_cycle", "career_changes", "revision_requests"}
    assert rr.GENERIC["revision_requests"][2] == rr.PAY


# ══════════════════════════ roles / scope ══════════════════════════════════════

def test_report_roles_and_scope():
    for role in ("org_admin", "payroll_admin", "payroll", "finance"):
        assert rr.can_see_pay(role)
    for role in ("hr_admin", "hr", "hr_manager", "manager", "employee", ""):
        assert not rr.can_see_pay(role)                              # payroll cost: payroll / admin / finance only
    assert rr.may_open(rr.PEOPLE, "manager") and rr.may_open(rr.PEOPLE, "hr_admin")
    assert not rr.may_open(rr.PEOPLE, "employee") and not rr.may_open(rr.OFFICE, "manager")
    assert not rr.may_open(rr.PAY, "hr_admin")
    rows = [{"id": "a", "manager_id": "m1"}, {"id": "b", "manager_id": "m2"}, {"id": "c", "manager_id": None}]
    assert rr.scope_employees(rows, "hr", None)[0] == rows
    assert [e["id"] for e in rr.scope_employees(rows, "manager", "m1")[0]] == ["a"]
    assert rr.scope_employees(rows, "manager", None)[0] == []        # a manager with no employee record sees nobody
    assert rr.scope_employees(rows, "employee", "m1")[0] == []
    assert rr.tile("x", "X", None)["empty"] == "No data yet" and rr.tile("x", "X", 0)["empty"] == ""


def test_callback_target_is_safe():
    base, path = ar.callback_target("/leave/requests/abc/{decision}", "approve")
    assert base == ar.SERVICE_URLS["leave"] and path == "/leave/requests/abc/approve"
    assert ar.callback_target("/hiring/offers/1/decide", "reject") == (None, "/hiring/offers/1/decide")   # → gateway
    for bad in ("http://evil.test/x", "leave/requests/1", "/leave/../auth/x", "//evil.test/x://", ""):
        with pytest.raises(ValueError):
            ar.callback_target(bad, "approve")


# ══════════════════════════ ageing / reminders ═════════════════════════════════

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def test_reminder_due_and_buckets():
    opened = NOW - timedelta(days=3, hours=1)
    assert ib.reminder_due(opened, 0, 3, NOW) and not ib.reminder_due(opened, 1, 3, NOW)
    assert ib.reminder_due(NOW - timedelta(days=6), 1, 3, NOW)
    assert not ib.reminder_due(NOW - timedelta(days=2, hours=23), 0, 3, NOW)
    assert not ib.reminder_due(NOW - timedelta(days=90), 0, 0, NOW)          # 0 = switched off
    assert [ib.age_bucket(d) for d in (0, 2, 3, 7, 8, 14, 15, 400)] == \
        ["0–2 days", "0–2 days", "3–7 days", "3–7 days", "8–14 days", "8–14 days", "15+ days", "15+ days"]
    items = [{"kind": "leave", "status": "pending", "opened_at": NOW - timedelta(days=1)},
             {"kind": "leave", "status": "pending", "opened_at": NOW - timedelta(days=9)},
             {"kind": "offer", "status": "pending", "opened_at": (NOW - timedelta(days=20)).replace(tzinfo=None)},
             {"kind": "offer", "status": "approved", "opened_at": NOW - timedelta(days=50)}]
    out = rc.approvals_ageing(items, NOW, ib.AGE_BUCKETS, ib.age_bucket, lambda k: k.title())
    assert out["rows"] == [{"kind": "Leave", "0–2 days": 1, "3–7 days": 0, "8–14 days": 1, "15+ days": 0, "total": 2, "oldest": 9},
                           {"kind": "Offer", "0–2 days": 0, "3–7 days": 0, "8–14 days": 0, "15+ days": 1, "total": 1, "oldest": 20}]
    assert out["totals"]["total"] == 3 and out["totals"]["oldest"] == 20


# ══════════════════════════ built-in sync + reminders (SQLite) ═════════════════

ORG = "11111111-1111-1111-1111-111111111111"
USERS = [{"id": "u-hr", "email": "hr@example.test", "role": "hr_admin", "employee_id": None, "first_name": "Hr", "last_name": "One"},
         {"id": "u-mgr", "email": "mgr@example.test", "role": "manager", "employee_id": "e-mgr", "first_name": "Team", "last_name": "Lead"},
         {"id": "u-emp", "email": "emp@example.test", "role": "employee", "employee_id": "e-emp", "first_name": "Staff", "last_name": "Member"}]
EMPS = [{"id": "e-emp", "manager_id": "e-mgr", "first_name": "Staff", "last_name": "Member", "email": "emp@example.test"},
        {"id": "e-mgr", "manager_id": None, "first_name": "Team", "last_name": "Lead", "email": "mgr@example.test"}]


class Quiet:
    def configured(self):
        return False


@pytest.fixture()
def db(monkeypatch, tmp_path):
    async def setup():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'apr.db'}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(pm.PlatformBase.metadata.create_all)
        pm.set_sessionmaker(async_sessionmaker(engine, expire_on_commit=False))

    async def users(org_id):
        return [dict(u) for u in USERS]

    async def emps(org_id):
        return [dict(e) for e in EMPS]

    monkeypatch.setattr(directory, "org_users", users)
    monkeypatch.setattr(directory, "org_employees", emps)
    nt.set_transport(Quiet())
    yield setup
    pm.set_sessionmaker(None)
    nt.set_transport(nt.SmtpTransport())


async def _all(model):
    async with pm.session() as s:
        return (await s.execute(select(model))).scalars().all()


def _leave(ref, opened):
    return {"kind": "leave", "ref_id": ref, "employee_id": "e-emp", "title": "Leave — Staff Member",
            "summary": ["Earned · 20 Oct 2026 · 1 day(s)"], "link": "/leave",
            "callback_path": f"/leave/requests/{ref}/{{decision}}", "opened_at": opened}


def test_sync_mirrors_then_closes_with_the_sources_outcome(db):
    async def go():
        await db()
        now = pm.now_utc()

        async def outcomes(org, kind, ref_ids):
            return {"L1": {"decision": "approved", "actor_user_id": "u-mgr", "note": "ok", "at": now}}

        pending = [_leave("L1", now - timedelta(hours=1)), _leave("L2", now - timedelta(days=30))]
        assert await ib.apply_sync(ORG, pending, outcomes) == {"opened": 2, "closed": 0}
        assert await ib.apply_sync(ORG, pending, outcomes) == {"opened": 0, "closed": 0}       # idempotent
        await nt.drain()
        items = {i.ref_id: i for i in await _all(pm.ApprovalItem)}
        assert items["L1"].requester_user_id == "u-emp" and items["L1"].approver_manager_of_employee_id == "e-emp"
        assert items["L1"].source == "sync" and items["L2"].status == "pending"
        told = [n for n in await _all(pm.Notification) if n.user_id in ("u-mgr", "u-hr")]
        assert len(told) == 2 and all("Leave — Staff Member" in n.title for n in told)   # L1 only; the old L2 is silent
        # L1 decided on the leave screen, L2 vanished (withdrawn)
        assert await ib.apply_sync(ORG, [], outcomes) == {"opened": 0, "closed": 2}
        await nt.drain()
        items = {i.ref_id: i for i in await _all(pm.ApprovalItem)}
        assert (items["L1"].status, items["L2"].status) == ("approved", "withdrawn")
        decs = {d.item_id: d for d in await _all(pm.ApprovalDecision)}
        d1 = decs[items["L1"].id]
        assert (d1.decision, d1.actor_name, d1.comment, d1.via) == ("approved", "Team Lead", "ok", "sync")
        mine = [n.title for n in await _all(pm.Notification) if n.user_id == "u-emp"]
        assert mine == ["Leave approved: Leave — Staff Member"]
        # an in-app only notice: the leave service sends its own e-mail
        assert not [d for d in await _all(pm.Delivery) if d.user_id == "u-emp" and d.channel == "email"]
    asyncio.run(go())


def test_reminders_go_to_approvers_then_hr(db):
    async def go():
        await db()
        now = pm.now_utc()
        await ib.apply_sync(ORG, [_leave("L9", now - timedelta(days=7))], None)       # old → no opening notice
        async with pm.session() as s:
            s.add(pm.OrgSetting(org_id=ORG, key="approvals", value={"escalation_days": 3}))
            await s.commit()
        assert await ib.send_due_reminders(ORG, now) == 1                               # day 7 ≥ 3
        assert await ib.send_due_reminders(ORG, now) == 1                               # day 7 ≥ 6 (second)
        assert await ib.send_due_reminders(ORG, now) == 0                               # third is due on day 9
        await nt.drain()
        item = (await _all(pm.ApprovalItem))[0]
        assert item.reminders_sent == 2 and item.last_reminder_at is not None
        titles = [n.title for n in await _all(pm.Notification)]
        assert len(titles) == 4 and all(t.startswith("Still waiting for you: ") for t in titles)
        assert not [n for n in await _all(pm.Notification) if n.user_id == "u-emp"]     # never the requester
    asyncio.run(go())


def test_inbox_close_keeps_a_step_the_owner_reopened(db):
    """Comp cycles / hiring offers run their own steps on ONE item: inside the approve callback the
    owner closes step 1 and re-opens the same item for step 2 (cycle 2). The inbox's own close
    afterwards must not close step 2."""
    from shared.utils import approvals_client as ac

    async def go():
        await db()
        kw = dict(kind="comp_cycle", ref_id="C1", title="Compensation cycle: X", summary=["s"], link="/x",
                  callback_path="/performance/comp/cycles/C1/approval/{decision}", requester_user_id="u-mgr")
        await ac._open_item(ORG, approver_roles=["hr_admin"], approver_user_ids=None,
                            approver_manager_of_employee_id=None, tell=False, **kw)
        # owner callback: close step 1, re-open for step 2
        await ac._close_item(ORG, kind="comp_cycle", ref_id="C1", decision="approved", actor_user_id="u-hr", tell=False)
        await ac._open_item(ORG, approver_roles=["org_admin"], approver_user_ids=None,
                            approver_manager_of_employee_id=None, tell=False, **kw)
        # the inbox then closes what it decided (cycle 1) → nothing to do
        assert await ac._close_item(ORG, kind="comp_cycle", ref_id="C1", decision="approved", actor_user_id="u-hr",
                                    via="inbox", tell=False, cycle=1) is None
        item = (await _all(pm.ApprovalItem))[0]
        assert (item.status, item.cycle, list(item.approver_roles)) == ("pending", 2, ["org_admin"])
        # without a re-open the inbox close still records the decision
        assert await ac._close_item(ORG, kind="comp_cycle", ref_id="C1", decision="approved", actor_user_id="u-hr",
                                    via="inbox", tell=False, cycle=2) == item.id
        assert (await _all(pm.ApprovalItem))[0].status == "approved"
    asyncio.run(go())
