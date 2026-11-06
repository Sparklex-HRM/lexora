"""Exit process rules (Stream C) — state machine, who may act, notice maths, clearance →
settlement, letters gate, last-working-day tick, rehire spell, attrition.

Run:  cd backend/services/employee && PYTHONPATH="../..;." python -m pytest test_exit_logic.py -q
"""
import asyncio
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import exit_logic as L  # noqa: E402

EMP, MGR, HRU, OTHER = "e-1", "m-1", "h-1", "x-9"
HR = {"role": "hr_admin", "employee_id": HRU, "user_id": "u-hr"}
MANAGER = {"role": "manager", "employee_id": MGR, "user_id": "u-m"}
SELF = {"role": "employee", "employee_id": EMP, "user_id": "u-e"}
STRANGER = {"role": "employee", "employee_id": OTHER, "user_id": "u-x"}
OTHER_MANAGER = {"role": "manager", "employee_id": OTHER, "user_id": "u-om"}
PAYROLL = {"role": "payroll_admin", "employee_id": "p-1", "user_id": "u-p"}


def case(**kw):
    return {"id": "c-1", "case_number": "EX-0001", "employee_id": EMP, "manager_id": MGR, "status": "submitted",
            "exit_type": "resignation", "initiated_by": "employee", "withdraw_requested": False, "separated_at": None,
            "letters": [], **kw}


# ── who is who ───────────────────────────────────────────────────────────────
def test_relation():
    c = case()
    assert L.relation(HR, c) == {"hr"} and L.relation(MANAGER, c) == {"manager"} and L.relation(SELF, c) == {"self"}
    assert L.relation(STRANGER, c) == set() and L.relation(OTHER_MANAGER, c) == set()
    assert L.relation(PAYROLL, c) == {"payroll"}
    assert not L.may_view(STRANGER, c) and L.may_view(MANAGER, c)
    # an HR user resigning: both HR and self
    assert L.relation({"role": "hr_admin", "employee_id": EMP}, c) == {"hr", "self"}


# ── create ───────────────────────────────────────────────────────────────────
def test_who_may_create_what():
    emp = {"id": EMP, "manager_id": MGR}
    assert L.may_create("resignation", SELF, emp, None) == (True, "", "employee")
    ok, why, _ = L.may_create("termination", SELF, emp, "x")
    assert not ok and "own resignation" in why
    assert L.may_create("termination", HR, emp, "Misconduct")[::2] == (True, "hr")
    ok, why, _ = L.may_create("termination", HR, emp, "  ")
    assert not ok and "reason" in why                               # employer-initiated needs a reason
    assert L.may_create("absconding", MANAGER, emp, "No show for 10 days")[::2] == (True, "manager")
    assert not L.may_create("termination", MANAGER, emp, "x")[0]    # HR starts a termination
    assert not L.may_create("resignation", STRANGER, emp, None)[0]
    assert not L.may_create("resignation", OTHER_MANAGER, emp, "x")[0]
    assert not L.may_create("sabbatical", HR, emp, "x")[0]


def test_initial_status():
    assert L.initial_status("resignation", {"self"}) == "submitted"
    assert L.initial_status("resignation", {"manager"}) == "hr_review"
    assert L.initial_status("termination", {"hr"}) == "hr_review"


# ── the state machine + who may act ──────────────────────────────────────────
def test_happy_path_walks_every_state():
    c = case()
    path = [("manager_review", MANAGER, "manager_review"), ("manager_approve", MANAGER, "hr_review"),
            ("accept", HR, "accepted"), ("start_clearance", HR, "clearance"), ("start_settlement", PAYROLL, "settlement"),
            ("settlement_paid", PAYROLL, "settled"), ("close", HR, "closed")]
    for action, who, expect in path:
        ok, why = L.can(action, c, who)
        assert ok, (action, why)
        c["status"] = L.next_status(action, c)
        assert c["status"] == expect
    assert L.available_actions(c, HR) == []                          # closed: nothing left


def test_wrong_person_or_wrong_state_is_refused():
    c = case()
    assert not L.can("accept", c, HR)[0]                             # not in HR review yet
    assert not L.can("manager_approve", c, SELF)[0]                  # the employee cannot approve own resignation
    assert not L.can("manager_approve", c, OTHER_MANAGER)[0]         # another manager cannot
    assert not L.can("manager_approve", c, STRANGER)[0]
    c["status"] = "hr_review"
    assert not L.can("accept", c, MANAGER)[0] and not L.can("accept", c, SELF)[0] and L.can("accept", c, HR)[0]
    assert not L.can("reject", c, MANAGER)[0] and L.can("reject", c, HR)[0]
    c["status"] = "clearance"
    assert not L.can("start_settlement", c, MANAGER)[0]
    assert not L.can("close", c, HR)[0]                              # must be settled first
    assert not L.can("fly", c, HR)[0]


def test_withdraw_rules():
    c = case()
    for s in ("submitted", "manager_review", "hr_review"):
        c["status"] = s
        assert L.can("withdraw", c, SELF)[0] and L.next_status("withdraw", c) == "withdrawn"
    for s in ("accepted", "clearance"):
        c["status"] = s
        assert not L.can("withdraw", c, SELF)[0]                     # after acceptance: only with HR approval
        assert L.can("request_withdraw", c, SELF)[0] and L.next_status("request_withdraw", c) is None
        assert not L.can("approve_withdraw", c, SELF)[0]
        assert L.can("approve_withdraw", c, HR)[0]
    c["withdraw_requested"] = True
    assert not L.can("request_withdraw", c, SELF)[0]                 # already asked
    assert L.can("decline_withdraw", c, HR)[0]
    c["status"], c["withdraw_requested"] = "settlement", False
    assert not L.can("request_withdraw", c, SELF)[0]
    # after the last working day there is nothing to withdraw
    gone = case(status="accepted", separated_at=datetime.now(timezone.utc))
    assert not L.can("request_withdraw", gone, SELF)[0] and not L.can("approve_withdraw", gone, HR)[0]
    # a termination cannot be "withdrawn" by the employee
    term = case(exit_type="termination", initiated_by="hr", status="hr_review")
    assert not L.can("withdraw", term, SELF)[0] and L.can("withdraw", term, HR)[0]


def test_hold_and_resume_returns_to_where_it_was():
    c = case(status="clearance")
    assert L.can("hold", c, HR)[0] and not L.can("hold", c, MANAGER)[0]
    c.update(status="on_hold", held_from_status="clearance")
    assert L.next_status("resume", c) == "clearance"
    assert [a["action"] for a in L.available_actions(c, HR)] == ["resume"]
    assert L.pending_with(c) == "HR"


def test_actions_listed_per_actor():
    c = case()
    assert {a["action"] for a in L.available_actions(c, SELF)} == {"withdraw"}
    assert {a["action"] for a in L.available_actions(c, MANAGER)} == {"manager_review", "manager_approve", "manager_reject"}
    assert "send_to_hr" in {a["action"] for a in L.available_actions(c, HR)}
    assert L.available_actions(c, STRANGER) == []
    assert all(a["needs_reason"] for a in L.available_actions(c, MANAGER) if a["action"] == "manager_reject")


# ── notice maths ─────────────────────────────────────────────────────────────
E_CONF = {"job_title": "Engineer", "employment_type": "full_time", "probation_end_date": date(2025, 6, 30)}
E_PROB = {"job_title": "Engineer", "employment_type": "full_time", "probation_end_date": date(2026, 12, 31)}
ON = date(2026, 10, 14)


def test_notice_defaults_30_and_probation_15_are_settings():
    assert L.notice_days_for(E_CONF, [], None, ON) == {"days": 30, "stage": "confirmed", "source": "default", "policy": None}
    assert L.notice_days_for(E_PROB, [], None, ON)["days"] == 15
    assert L.notice_days_for(E_CONF, [], {"default_notice_days": 60}, ON)["days"] == 60
    assert L.notice_days_for(E_PROB, [], {"probation_notice_days": 7}, ON)["days"] == 7


def test_notice_policy_most_specific_row_wins():
    pol = [{"id": "1", "grade": None, "employment_type": None, "stage": "any", "notice_days": 45},
           {"id": "2", "grade": None, "employment_type": "full_time", "stage": "confirmed", "notice_days": 60},
           {"id": "3", "grade": "Engineer", "employment_type": None, "stage": "any", "notice_days": 90},
           {"id": "4", "grade": "Director", "employment_type": None, "stage": "any", "notice_days": 120},
           {"id": "5", "grade": "Engineer", "employment_type": "full_time", "stage": "probation", "notice_days": 10},
           {"id": "6", "grade": "Engineer", "employment_type": "full_time", "stage": "confirmed", "notice_days": 75, "active": False}]
    assert L.notice_days_for(E_CONF, pol, None, ON)["days"] == 90          # grade row beats type+stage row
    assert L.notice_days_for(E_PROB, pol, None, ON)["days"] == 10          # grade+type+stage
    assert L.notice_days_for({**E_CONF, "job_title": "Clerk"}, pol, None, ON)["days"] == 60
    assert L.notice_days_for({**E_CONF, "job_title": "Clerk", "employment_type": "contract"}, pol, None, ON)["days"] == 45
    assert L.notice_days_for(E_CONF, pol, None, ON)["source"] == "policy"


def test_expected_last_working_day_from_the_acceptance_date():
    assert L.expected_lwd(ON, 30) == date(2026, 11, 12)                    # 14 Oct is day 1 → day 30 = 12 Nov
    assert L.expected_lwd(ON, 15) == date(2026, 10, 28)
    assert L.expected_lwd(ON, 0) == ON and L.expected_lwd(ON, 1) == ON
    assert L.expected_lwd(ON, 30, leave_days=3, leave_extends=True) == date(2026, 11, 15)
    assert L.expected_lwd(ON, 30, leave_days=3, leave_extends=False) == date(2026, 11, 12)


def test_notice_served_and_shortfall():
    full = L.notice_position(accepted_on=ON, notice_days=30, lwd=date(2026, 11, 12))
    assert full["served_days"] == 30 and full["shortfall_days"] == 0 and not full["early"]
    early = L.notice_position(accepted_on=ON, notice_days=30, lwd=date(2026, 10, 31))
    assert early["served_raw"] == 18 and early["shortfall_days"] == 12 and early["early"]
    later = L.notice_position(accepted_on=ON, notice_days=30, lwd=date(2026, 11, 30))
    assert later["shortfall_days"] == 0 and later["served_days"] == 30
    same_day = L.notice_position(accepted_on=ON, notice_days=30, lwd=ON)
    assert same_day["served_raw"] == 1 and same_day["shortfall_days"] == 29
    past = L.notice_position(accepted_on=ON, notice_days=30, lwd=date(2026, 10, 1))    # absconded before acceptance
    assert past["served_raw"] == 0 and past["shortfall_days"] == 30
    # leave during notice: extends the date → those days are not "served"
    lv = L.notice_position(accepted_on=ON, notice_days=30, lwd=date(2026, 11, 12), leave_days=3, leave_extends=True)
    assert lv["shortfall_days"] == 3 and lv["expected_lwd"] == "2026-11-15"


def test_shortfall_must_be_settled_one_of_three_ways():
    assert L.notice_treatment_ok(0, None, None, "resignation")[0]
    assert not L.notice_treatment_ok(12, None, None, "resignation")[0]
    assert L.notice_treatment_ok(12, "buyout", None, "resignation")[0]
    assert not L.notice_treatment_ok(12, "waiver", "", "resignation")[0]           # waiver needs a reason
    assert L.notice_treatment_ok(12, "waiver", "Project closed early", "resignation")[0]
    assert not L.notice_treatment_ok(12, "pay_in_lieu", None, "resignation")[0]    # employer pays only when it started the exit
    assert L.notice_treatment_ok(12, "pay_in_lieu", None, "termination")[0]


# ── clearance → settlement ───────────────────────────────────────────────────
def _items():
    lines = L.clearance_lines(None, case())
    for i, l in enumerate(lines):
        l["id"] = f"i{i}"
    return lines


def test_clearance_checklist_from_the_template():
    items = _items()
    assert len(items) == 7 and {i["department"] for i in items} >= {"Reporting manager", "IT", "Admin", "Finance", "HR"}
    mgr_line = [i for i in items if i["owner_role"] == "manager"][0]
    assert mgr_line["owner_employee_id"] == MGR and all(i["status"] == "pending" for i in items)
    custom = L.clearance_lines([{"department": "Stores", "item": "Tools", "owner_role": "hr_admin", "owner_user_id": "u-9"}], case())
    assert custom == [{"department": "Stores", "item": "Tools", "owner_role": "hr_admin", "owner_user_id": "u-9",
                       "owner_employee_id": None, "owner_name": None, "status": "pending", "recovery_amount": 0.0, "sort": 0}]


def test_who_may_clear_a_line():
    c = case(status="clearance")
    mgr_line = {"owner_role": "manager"}
    fin_line = {"owner_role": "payroll_admin"}
    named = {"owner_role": "hr_admin", "owner_user_id": "u-x"}
    assert L.may_clear(MANAGER, c, mgr_line)[0] and not L.may_clear(OTHER_MANAGER, c, mgr_line)[0]
    assert L.may_clear(PAYROLL, c, fin_line)[0] and not L.may_clear(PAYROLL, c, mgr_line)[0]
    assert L.may_clear(STRANGER, c, named)[0]                       # the named user
    assert L.may_clear(HR, c, mgr_line)[0] and L.may_clear(HR, c, fin_line)[0]
    assert not L.may_clear(SELF, c, mgr_line)[0] and not L.may_clear(SELF, c, named)[0]   # never your own exit


def test_clearance_update_rules():
    assert L.clearance_update_ok("cleared", None, None)[0]
    assert not L.clearance_update_ok("cleared_with_recovery", 0, "x")[0]
    assert not L.clearance_update_ok("cleared_with_recovery", 500, " ")[0]
    assert L.clearance_update_ok("cleared_with_recovery", 500, "Laptop charger missing")[0]
    assert not L.clearance_update_ok("done", None, None)[0]


def test_recovery_flows_into_the_final_pay_prefill():
    items = _items()
    for i in items:
        i["status"] = "cleared"
    items[1].update(status="cleared_with_recovery", recovery_amount=150.0, comment="Laptop charger")
    items[4].update(status="cleared_with_recovery", recovery_amount=800.0, comment="Travel advance")
    s = L.clearance_summary(items)
    assert s["complete"] and s["recovery_total"] == 950.0 and s["pending"] == 0
    c = case(status="clearance", lwd=date(2026, 10, 31), accepted_on=ON, notice_days=14, notice_served_days=9,
             notice_shortfall_days=5, notice_treatment="buyout")
    assert L.settlement_ready(c, items) == (True, "")
    p = L.settlement_prefill(c, items, "CA")
    b = p["body"]
    assert p["kind"] == "canada_final" and p["calculator"] == "/offboarding"
    assert b["last_day"] == "2026-10-31" and b["case_id"] == "c-1"
    assert b["extra_deductions"] == [{"code": "RECOVERY", "amount": 950.0}]
    assert p["notice"] == {"days": 14, "served": 9, "shortfall": 5, "treatment": "buyout"}
    assert any("buy-out" in m for m in p["manual"])
    assert [r["amount"] for r in p["clearance_recoveries"]] == [150.0, 800.0]


def test_canada_prefill_uses_the_real_last_day_and_the_chosen_roe_code():
    items = [{"status": "cleared_with_recovery", "recovery_amount": 200.0, "department": "IT", "item": "Badge", "comment": "lost"}]
    c = case(lwd=date(2026, 10, 14), roe_reason_code="E", notice_shortfall_days=0, notice_treatment="none")
    p = L.settlement_prefill(c, items, "CA")
    assert p["kind"] == "canada_final" and p["body"]["last_day"] == "2026-10-14" and p["body"]["reason"] == "E"
    assert p["body"]["extra_deductions"] == [{"code": "RECOVERY", "amount": 200.0}]
    # no code chosen → the suggestion for the exit type, always one of payroll's existing codes
    for t in L.EXIT_TYPES:
        assert L.settlement_prefill(case(exit_type=t, lwd=date(2026, 10, 14)), [], "CA")["body"]["reason"] in L.ROE_CODES
    lieu = case(exit_type="termination", lwd=date(2026, 10, 14), notice_shortfall_days=10, notice_treatment="pay_in_lieu")
    assert any("NOT worked out" in m for m in L.settlement_prefill(lieu, [], "CA")["manual"])


def test_settlement_blocked_until_clearance_or_override():
    items = _items()
    c = case(status="clearance", lwd=date(2026, 10, 31), notice_shortfall_days=0, notice_treatment="none")
    ok, why = L.settlement_ready(c, items)
    assert not ok and "7 line(s) pending" in why
    assert L.settlement_ready(c, items, "Employee abroad, laptop couriered") == (True, "")
    assert not L.settlement_ready(case(status="clearance"), items, "x")[0]                 # no last working day
    undecided = case(status="clearance", lwd=date(2026, 10, 31), notice_shortfall_days=5, notice_treatment=None)
    assert "choose buy-out" in L.settlement_ready(undecided, items, "override")[1]
    assert not L.settlement_ready(c, [])[0]                                                 # no checklist yet


# ── letters ──────────────────────────────────────────────────────────────────
def test_letters_are_held_until_clearance_and_settlement():
    done = [{"status": "cleared", "recovery_amount": 0}]
    pend = [{"status": "pending", "recovery_amount": 0, "department": "IT"}]
    acc = case(status="accepted")
    assert L.letter_allowed("resignation_acceptance", acc, pend, None)[0]
    assert not L.letter_allowed("resignation_acceptance", case(status="hr_review"), pend, None)[0]
    assert not L.letter_allowed("resignation_acceptance", case(status="accepted", exit_type="termination"), pend, None)[0]
    assert not L.letter_allowed("relieving", acc, pend, None)[0]
    assert not L.letter_allowed("relieving", case(status="settlement"), done, None)[0]     # cleared but not paid
    assert L.letter_allowed("relieving", case(status="settled"), done, None)[0]
    assert L.letter_allowed("experience", case(status="clearance"), pend, None, "Visa deadline")[0]   # HR override
    assert L.letter_allowed("relieving", acc, pend, {"hold_letters_until_settled": False})[0]
    assert not L.letter_allowed("bonus_letter", acc, done, None)[0]
    assert not L.letters_done(case(letters=[{"key": "relieving"}]))
    assert L.letters_done(case(letters=[{"key": "relieving"}, {"key": "experience"}, {"key": "fnf_cover"}]))


# ── last working day tick ────────────────────────────────────────────────────
def test_separation_due_only_once_and_only_when_accepted():
    today = date(2026, 10, 31)
    c = case(status="clearance", lwd=date(2026, 10, 31))
    assert L.separation_due(c, today) and L.separation_due(c, date(2026, 11, 5))
    assert not L.separation_due(c, date(2026, 10, 30))                       # not yet
    assert not L.separation_due({**c, "separated_at": datetime.now(timezone.utc)}, today)   # idempotent
    for s in ("submitted", "manager_review", "hr_review", "withdrawn", "rejected", "on_hold"):
        assert not L.separation_due({**c, "status": s}, today)
    assert not L.separation_due(case(status="accepted", lwd=None), today)
    assert L.separation_patch(c) == {"status": "terminated", "termination_date": date(2026, 10, 31)}   # the REAL date


class _Res:
    def __init__(self, rowcount=1, rows=None, one=None):
        self.rowcount, self._rows, self._one = rowcount, rows or [], one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows, first=lambda: (self._rows[0] if self._rows else None))

    def scalar_one_or_none(self):
        return self._one

    def all(self):
        return self._rows


class _TickDB:
    """Just enough of a session for separate_case(): the claim UPDATE succeeds once."""

    def __init__(self, case_row, emp_row):
        self.case_row, self.emp_row, self.claims, self.emp_updates, self.added = case_row, emp_row, 0, [], []

    async def execute(self, stmt, *a, **k):
        sql = str(stmt)
        if sql.startswith("UPDATE exit_cases") and "separated_at" in sql and "separation_result" not in sql:
            if self.case_row.separated_at is None:
                self.case_row.separated_at = datetime.now(timezone.utc)
                self.claims += 1
                return _Res(1)
            return _Res(0)
        if sql.startswith("UPDATE employees"):
            p = stmt.compile().params
            self.emp_row.status, self.emp_row.termination_date = p["status"], p["termination_date"]
            self.emp_updates.append(p)
            return _Res(1)
        if sql.startswith("UPDATE exit_cases"):
            self.case_row.separation_result = stmt.compile().params.get("separation_result")
            return _Res(1)
        if "FROM exit_cases" in sql:
            return _Res(one=self.case_row, rows=[self.case_row])
        if "FROM employment_spells" in sql:
            return _Res(rows=[])
        if "FROM employees" in sql:
            return _Res(one=self.emp_row, rows=[])
        return _Res()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


def test_lwd_tick_is_idempotent_and_stamps_the_real_exit_date(monkeypatch):
    import uuid
    import exit_common as C
    import exit_people as P

    org, eid = uuid.uuid4(), uuid.uuid4()
    lwd = date(2026, 10, 20)
    c = SimpleNamespace(id=uuid.uuid4(), org_id=org, case_number="EX-0007", employee_id=eid, employee_name="Asha Rao",
                        manager_id=None, manager_name=None, status="clearance", held_from_status=None, exit_type="resignation",
                        initiated_by="employee", lwd=lwd, accepted_on=date(2026, 9, 21), notice_days=30, notice_served_days=30,
                        notice_shortfall_days=0, notice_treatment="none", notice_treatment_reason=None, separated_at=None,
                        withdraw_requested=False, letters=[], roe_reason_code=None, department="Ops", reason_category="Better offer",
                        reason=None, rehire_eligible=True, rehire_note=None, separation_result=None)
    e = SimpleNamespace(id=eid, org_id=org, status="active", termination_date=None, hire_date=date(2024, 1, 1),
                        employee_number="E7", first_name="Asha", last_name="Rao")
    calls = {"logins": 0}

    async def fake_logins(o, emp, reason):
        calls["logins"] += 1
        return {"ok": True, "disabled": 1}

    async def fake_load_case(db, o, cid):
        return c

    async def noop(*a, **k):
        return {"cancelled": 0, "removed": 0, "ok": True}

    async def no_notify(*a, **k):
        return None

    monkeypatch.setattr(C, "today", lambda: date(2026, 10, 25))          # the tick runs 5 days late
    monkeypatch.setattr(C, "disable_logins", fake_logins)
    monkeypatch.setattr(C, "load_case", fake_load_case)
    monkeypatch.setattr(C, "notify", no_notify)
    monkeypatch.setattr(P, "_close_future_leave", noop)
    monkeypatch.setattr(P, "_close_future_shifts", noop)
    db = _TickDB(c, e)
    first = asyncio.run(P.separate_case(db, c, {}))
    assert first["done"] and first["exit_date"] == "2026-10-20"
    assert e.status == "terminated" and e.termination_date == lwd       # the REAL date, not the day the tick ran
    assert db.claims == 1 and calls["logins"] == 1
    spell = [o for o in db.added if o.__class__.__name__ == "EmploymentSpell"][0]
    assert spell.exit_date == lwd and spell.exit_type == "resignation" and spell.rehire_eligible is True
    second = asyncio.run(P.separate_case(db, c, {}))                     # the next tick: nothing happens
    assert second == {"done": False, "reason": "Not due"}
    assert db.claims == 1 and calls["logins"] == 1 and len(db.emp_updates) == 1
    # two ticks racing: the second loses the claim
    c2 = SimpleNamespace(**{**c.__dict__, "separated_at": None})
    db2 = _TickDB(SimpleNamespace(**{**c.__dict__}), e)                  # the row is already claimed in the database
    raced = asyncio.run(P.separate_case(db2, c2, {}))
    assert raced == {"done": False, "reason": "Already separated"} and calls["logins"] == 1


# ── rehire ───────────────────────────────────────────────────────────────────
FORMER = {"id": EMP, "status": "terminated", "employee_number": "E7", "hire_date": date(2022, 4, 1),
          "termination_date": date(2026, 10, 20)}


def test_rehire_creates_a_new_spell_and_keeps_the_old_one():
    plan, err = L.rehire_plan(FORMER, [{"spell_no": 1, "rehire_eligible": True}], "2027-01-05", "keep")
    assert err == ""
    assert plan["closing_spell"] == {"spell_no": 1, "employee_number": "E7", "hire_date": date(2022, 4, 1),
                                     "exit_date": date(2026, 10, 20)}
    assert plan["new_spell"] == {"spell_no": 2, "employee_number": "E7", "hire_date": date(2027, 1, 5), "exit_date": None,
                                 "previous_spell_no": 1}
    assert plan["employee_patch"]["status"] == "active" and plan["employee_patch"]["termination_date"] is None
    assert plan["employee_patch"]["hire_date"] == date(2027, 1, 5)
    # identifiers are NOT in the patch: the SIN stays on the same person record
    assert not {"sin", "sin_enc", "bank_account_enc"} & set(plan["employee_patch"])


def test_rehire_terms_cleaned_and_checked():
    types, regions = ("full_time", "contract"), ("ON", "BC")
    t, err = L.rehire_terms({"job_title": " Weaver ", "annual_salary": "360000", "employment_type": "contract",
                             "province_of_employment": "on", "probation_end_date": "2027-07-04", "grade": "",
                             "manager_id": "8d3f2b7e-4c4b-4a7e-9d0e-1c2b3a4d5e6f"}, "2027-01-05", types, regions)
    assert not err and t["job_title"] == "Weaver" and t["annual_salary"] == 360000.0
    assert t["province_of_employment"] == "ON" and t["probation_end_date"] == date(2027, 7, 4)
    assert "grade" not in t and str(t["manager_id"]) == "8d3f2b7e-4c4b-4a7e-9d0e-1c2b3a4d5e6f"
    assert "employment_type" in L.rehire_terms({"employment_type": "intern"}, "2027-01-05", types, regions)[1]
    assert "state" in L.rehire_terms({"province_of_employment": "ZZ"}, "2027-01-05", types, regions)[1]
    assert "before the new joining date" in L.rehire_terms({"probation_end_date": "2026-12-31"}, "2027-01-05", types, regions)[1]
    assert "negative" in L.rehire_terms({"annual_salary": -1}, "2027-01-05", types, regions)[1]
    assert "manager" in L.rehire_terms({"manager_id": "x"}, "2027-01-05", types, regions)[1]


def test_rehire_number_policy_and_refusals():
    plan, _ = L.rehire_plan(FORMER, [{"spell_no": 2}], "2027-01-05", "new", "E901")
    assert plan["new_spell"]["employee_number"] == "E901" and plan["new_spell"]["spell_no"] == 3
    assert "new employee number" in L.rehire_plan(FORMER, [], "2027-01-05", "new", " ")[1]
    assert "not eligible" in L.rehire_plan(FORMER, [{"spell_no": 1, "rehire_eligible": False}], "2027-01-05")[1]
    assert "after the exit date" in L.rehire_plan(FORMER, [], "2026-10-20")[1]
    assert "former employee" in L.rehire_plan({**FORMER, "status": "active"}, [], "2027-01-05")[1]
    assert "joining date" in L.rehire_plan(FORMER, [], "")[1]
    first, _ = L.rehire_plan(FORMER, [], "2027-01-05")                    # a leaver from before spells existed
    assert first["closing_spell"]["spell_no"] == 1 and first["new_spell"]["spell_no"] == 2


# ── attrition ────────────────────────────────────────────────────────────────
def test_attrition_summary_counts_real_exits_only():
    cases = [
        case(status="closed", lwd=date(2026, 10, 20), department="Ops", reason_category="Better offer", notice_days=30, notice_served_days=30),
        case(status="settled", lwd=date(2026, 10, 31), department="Ops", reason_category="Better offer", notice_days=30, notice_served_days=18, notice_shortfall_days=12),
        case(status="accepted", lwd=date(2026, 11, 15), department="Sales", exit_type="termination", notice_days=0, notice_served_days=0),
        case(status="withdrawn", lwd=date(2026, 10, 5), department="Sales"),
        case(status="hr_review", department="Sales"),
    ]
    a = L.attrition_summary(cases)
    assert a["total_exits"] == 3 and a["withdrawn"] == 1 and a["open_cases"] == 3
    assert a["by_month"] == [{"label": "2026-10", "count": 2}, {"label": "2026-11", "count": 1}]
    assert a["by_department"][0] == {"label": "Ops", "count": 2}
    assert {r["label"]: r["count"] for r in a["by_type"]} == {"Resignation": 2, "Termination": 1}
    assert {r["label"]: r["count"] for r in a["by_reason"]} == {"Better offer": 2, "Not given": 1}
    assert a["average_notice_served_days"] == 24.0 and a["notice_cases"] == 2 and a["short_notice_cases"] == 1
    assert L.attrition_summary([])["average_notice_served_days"] is None
