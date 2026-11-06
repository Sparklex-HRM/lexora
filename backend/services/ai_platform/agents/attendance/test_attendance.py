"""Attendance Agent tests (2026-10-26): punch gaps → employee, pending regularisation / leave → manager (72 h window,
then blocking), late marks → manager, cut-off makes in-period items blocking, shadow opens nothing, live dedupes and
resolves, no tool edits anything.

Run:  cd backend/services/ai_platform && python -m pytest -q agents/attendance
"""
from datetime import date, datetime, timedelta, timezone

from agents.lifecycle_testkit import assert_fits_columns, E1, E2, LOGINS, MGR, ORG, db, go_live, run, tasks  # noqa: F401

import agentic_registry as REG  # noqa: E402
from agents.attendance import checks as CK  # noqa: E402
from agents.attendance import executors as EX  # noqa: E402
from agents.attendance import run as RN  # noqa: E402

TODAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)


def inputs(**over):
    d = {"today": TODAY, "window": {"start": TODAY - timedelta(days=7), "end": TODAY - timedelta(days=1)},
         "pay": {"period": "2026-10-18..2026-10-31", "period_start": date(2026, 10, 18), "period_end": date(2026, 10, 31),
                 "pay_date": date(2026, 11, 6), "cutoff": date(2026, 10, 26) + timedelta(days=10), "lead_days": 3},
         "employees": [{"id": E1, "employee_number": "MAP001", "name": "Liam Wong", "manager_id": MGR, "gender": "M"},
                       {"id": E2, "employee_number": "MAP002", "name": "Chloe Tremblay", "manager_id": MGR, "gender": "F"},
                       {"id": MGR, "employee_number": "MAP099", "name": "Lead", "manager_id": None, "gender": "M"}],
         "days": [{"employee_id": E1, "work_date": date(2026, 10, 22), "first_in": "09:05", "last_out": None, "status": "present"},
                  {"employee_id": E2, "work_date": date(2026, 10, 23), "first_in": None, "last_out": None, "status": "absent"},
                  {"employee_id": E2, "work_date": date(2026, 10, 21), "first_in": None, "last_out": None, "status": "absent"}],
         "regularisations": [], "leave": [{"id": "l-ok", "employee_id": E2, "status": "approved", "start_date": date(2026, 10, 21), "end_date": date(2026, 10, 21)}],
         "sources": {"inputs": "fixture"}}
    d.update(over)
    return d


def codes(items, sev=None):
    return [x["code"] for x in items if sev is None or x["severity"] == sev]


def test_punch_gaps_go_to_the_employee_and_leave_covers():
    r = CK.run_checks(inputs(), now=NOW)
    at1 = [x for x in r["items"] if x["code"] == "AT-01"]
    assert sorted(x["ref"] for x in at1) == ["2026-10-22", "2026-10-23"]          # 21 Oct covered by approved leave
    assert all(x["owner_role"] == "employee" and x["severity"] == "warning" for x in at1)
    assert "Single punch" in next(x for x in at1 if x["employee_id"] == E1)["title"]
    # a regularisation already raised for the day removes the punch item
    r2 = CK.run_checks(inputs(regularisations=[{"id": "r1", "employee_id": E1, "work_date": date(2026, 10, 22), "status": "pending",
                                                "created_at": NOW - timedelta(hours=10), "manager_id": MGR}]), now=NOW)
    assert [x["ref"] for x in r2["items"] if x["code"] == "AT-01"] == ["2026-10-23"]
    at5 = next(x for x in r2["items"] if x["code"] == "AT-05")
    assert at5["owner_role"] == "manager" and at5["severity"] == "warning" and at5["due_on"] == "2026-10-28"   # created (25 Oct 23:00 UTC) + 72 h


def test_regularisation_past_the_window_and_cutoff_blocking():
    old = [{"id": "r1", "employee_id": E1, "work_date": date(2026, 10, 20), "status": "pending", "created_at": NOW - timedelta(hours=80), "manager_id": MGR}]
    assert codes(CK.run_checks(inputs(regularisations=old), now=NOW)["items"], "blocking") == ["AT-05"]
    near = inputs(pay={**inputs()["pay"], "cutoff": TODAY + timedelta(days=1)})
    r = CK.run_checks(near, now=NOW)
    assert set(codes(r["items"], "blocking")) == {"AT-01"} and r["cutoff"]["days_to_cutoff"] == 1
    assert "Payroll cut-off 2026-10-27" in next(x for x in r["items"] if x["code"] == "AT-01")["what"]


def test_late_marks_and_pending_leave():
    days = [{"employee_id": E1, "work_date": date(2026, 10, d), "first_in": "09:30", "last_out": "18:00", "status": "late"} for d in (5, 6, 7)]
    r = CK.run_checks(inputs(days=days,
                             leave=[{"id": "l1", "employee_id": E2, "status": "pending", "start_date": date(2026, 10, 29), "end_date": date(2026, 10, 30)}]), now=NOW)
    it = {x["code"] + ":" + x["ref"]: x for x in r["items"]}
    assert it["AT-02:late:2026-10"]["owner_role"] == "manager" and it["AT-02:late:2026-10"]["detail"]["count"] == 3
    assert it["AT-06:leave:l1"]["owner_role"] == "manager" and "unpaid time" in it["AT-06:leave:l1"]["what"]
    assert {c["code"] for c in r["checks"]} == {"AT-01", "AT-02", "AT-05", "AT-06"}


def test_shadow_opens_nothing_live_dedupes_and_resolves():
    out = run(RN.run_attendance(ORG, trigger="daily", inputs=inputs(), logins=LOGINS, now=NOW))
    assert out["mode"] == "shadow" and out["chase"]["opened"] == 0 and len(out["chase"]["would_open"]) == 2 and tasks("attendance") == []
    assert out["summary"]["pay_inputs_open"] == 2 and out["summary"]["cutoff"]["cutoff"] == "2026-11-05"
    go_live()
    run(RN.run_attendance(ORG, trigger="daily", inputs=inputs(), logins=LOGINS, now=NOW))
    owners = sorted(((t["owner_user_id"], t["owner_employee_id"]) for t in tasks("attendance")), key=str)
    assert sorted(owners, key=str) == sorted([(None, E2), ("u-e1", None)], key=str)                                  # E2 has no login → the employee record is the owner
    run(RN.run_attendance(ORG, trigger="manual", inputs=inputs(), logins=LOGINS, now=NOW))
    assert len(tasks("attendance")) == 2
    fixed = inputs(days=[d for d in inputs()["days"] if d["employee_id"] != E1])
    run(RN.run_attendance(ORG, trigger="manual", inputs=fixed, logins=LOGINS, now=NOW))
    st = {t["owner_user_id"] or t["owner_employee_id"]: t["status"] for t in tasks("attendance")}
    assert st == {"u-e1": "done", E2: "open"}
    assert_fits_columns()                                                       # Postgres VARCHAR limits


def test_manager_task_goes_to_the_managers_login():
    go_live()
    regs = [{"id": "r9", "employee_id": E1, "work_date": date(2026, 10, 22), "status": "pending", "created_at": NOW - timedelta(hours=5), "manager_id": MGR}]
    run(RN.run_attendance(ORG, trigger="daily", inputs=inputs(regularisations=regs, days=[]), logins=LOGINS, now=NOW))
    assert [(t["kind"], t["owner_user_id"]) for t in tasks("attendance")] == [("at_05", "u-mgr")]


def test_only_chase_tool_no_editing_tool():
    spec = REG.REGISTRY["attendance"]
    assert spec.tier_map == {"attendance.chase": 0} and set(EX.TOOLS) == {"attendance.chase"} and "attendance.chase" in REG.EXECUTORS
    for word in ("approve", "edit", "acknowledge", "publish", "punch"):
        assert not any(word in t for t in spec.tier_map)
