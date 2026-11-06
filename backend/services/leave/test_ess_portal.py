"""Employee portal (2026-10-16) — leave: usable vs event totals, role checks on the new routes.
Run:  cd backend/services/leave && python -m pytest -q test_ess_portal.py
"""
import routers as R


def _row(code, remaining, entitled, paid=True):
    return {"leave_type_code": code, "leave_type_name": code, "remaining": remaining, "entitled": entitled,
            "used": entitled - remaining, "pending": 0, "paid": paid}


def test_category_fallback_and_master_override():
    assert R.category_of("MAT", True, {}) == "event"
    assert R.category_of("PAT", True, {}) == "event"
    assert R.category_of("VAC", True, {}) == "usable"
    assert R.category_of("LWP", False, {}) == "unpaid"
    # HR marked a custom type as event, and MAT as usable — the master wins
    cats = R.leave_categories([{"code": "CHILD", "category": "Event"}, {"code": "MAT", "category": "usable"}, {"code": "X", "category": "?"}])
    assert cats == {"CHILD": "event", "MAT": "usable"}
    assert R.category_of("CHILD", True, cats) == "event" and R.category_of("MAT", True, cats) == "usable"


def test_totals_never_sum_event_leave():
    """Priya's balances: VAC 12, PEL 16, SICK 12, COMP 0 usable; MAT 182 + PAT 15 are event leave.
    The old screens summed everything paid (237); the usable total is 40."""
    rows = [_row("VAC", 12, 12), _row("PEL", 16, 18), _row("SICK", 12, 12), _row("COMP", 0, 0),
            _row("MAT", 182, 182), _row("PAT", 15, 15), _row("LWP", 0, 0, paid=False)]
    for r in rows:
        r["category"] = R.category_of(r["leave_type_code"], r["paid"], {})
    t = R.balance_totals(rows)
    assert t["usable_remaining"] == 40
    assert t["usable_entitled"] == 42
    assert [e["code"] for e in t["event"]] == ["MAT", "PAT"]


# ── batch 2: leave apply extras, comp-off, optional holidays, cancellation ───
import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ess_leave
import ess_leave_logic as EL
from database import get_db

ORG = str(uuid.uuid4())
PRIYA, VIKRAM, OTHER = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def test_attachment_required_by_leave_type_policy():
    sl = {"name": "Sick Leave", "attachment_after_days": 2}
    emps = {PRIYA: {}, OTHER: {}}
    assert "needs a supporting document" in EL.validate_apply(sl, days=3, half_day=False, half_session=None, attachment_id=None, cc=[], emps=emps, me=PRIYA)
    assert EL.validate_apply(sl, days=2, half_day=False, half_session=None, attachment_id=None, cc=[], emps=emps, me=PRIYA) is None
    assert EL.validate_apply(sl, days=3, half_day=False, half_session=None, attachment_id=str(uuid.uuid4()), cc=[], emps=emps, me=PRIYA) is None
    assert EL.validate_apply({"name": "VAC"}, days=9, half_day=False, half_session=None, attachment_id=None, cc=[], emps=emps, me=PRIYA) is None


def test_half_session_and_cc_rules():
    emps = {PRIYA: {}, OTHER: {}}
    assert "first half" in EL.validate_apply({}, days=0.5, half_day=True, half_session="evening", attachment_id=None, cc=[], emps=emps, me=PRIYA)
    assert EL.validate_apply({}, days=0.5, half_day=True, half_session="second", attachment_id=None, cc=[OTHER], emps=emps, me=PRIYA) is None
    assert "yourself" in EL.validate_apply({}, days=1, half_day=False, half_session=None, attachment_id=None, cc=[PRIYA], emps=emps, me=PRIYA)
    assert "not found" in EL.validate_apply({}, days=1, half_day=False, half_session=None, attachment_id=None, cc=[str(uuid.uuid4())], emps=emps, me=PRIYA)


def test_comp_off_rules():
    hol = {date(2026, 10, 12): "Thanksgiving"}
    assert EL.comp_off_day_kind(date(2026, 10, 12), hol, [5, 6]) == ("holiday", "Thanksgiving")
    assert EL.comp_off_day_kind(date(2026, 10, 4), hol, [5, 6]) == ("weekly_off", "Sunday")
    assert EL.comp_off_day_kind(date(2026, 10, 5), hol, [5, 6]) is None
    pol = {"comp_off_full_day_hours": 8, "comp_off_half_day_hours": 4, "comp_off_expiry_days": 90}
    assert EL.comp_off_days(8.5, pol) == 1.0 and EL.comp_off_days(5, pol) == 0.5 and EL.comp_off_days(3.9, pol) == 0
    assert EL.comp_off_expiry(date(2026, 10, 4), pol) == date(2027, 1, 2)
    assert not EL.comp_off_window_ok(date(2026, 10, 20), date(2026, 10, 16), pol)        # not worked yet
    assert not EL.comp_off_window_ok(date(2026, 6, 1), date(2026, 10, 16), pol)          # outside the window
    h, fin, lout = EL.worked_hours([{"direction": "in", "at": "2026-10-04T04:00:00Z"}, {"direction": "out", "at": "2026-10-04T13:00:00Z"}])
    assert h == 9.0 and fin.startswith("2026-10-04T04") and lout.startswith("2026-10-04T13")


def test_optional_quota():
    pol = {"optional_holidays_per_year": 2}
    picks = [{"year": 2026, "status": "approved"}, {"year": 2026, "status": "rejected"}, {"year": 2025, "status": "approved"}]
    assert EL.optional_quota(pol, picks, 2026) == {"allowed": 2, "used": 1, "left": 1}


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

    async def execute(self, *a, **k):
        return _R(self.rows)

    def add(self, o):
        o.id = uuid.uuid4()

    async def commit(self):
        pass

    async def refresh(self, o):
        pass


def _client(db, monkeypatch):
    async def none(*a, **k):
        return ""
    monkeypatch.setattr(ess_leave.approvals_client, "open_item", none)
    monkeypatch.setattr(ess_leave.approvals_client, "close_item", none)
    app = FastAPI()
    app.include_router(ess_leave.router)

    async def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def H(role, emp):
    return {"x-org-id": ORG, "x-user-id": str(uuid.uuid4()), "x-role": role, "x-employee-id": emp or ""}


def _leave(status):
    from models import LeaveRequest
    r = LeaveRequest(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(PRIYA), manager_id=uuid.UUID(VIKRAM), fiscal_year=2026,
                     leave_type_code="VAC", leave_type_name="Vacation", start_date=date(2026, 10, 26),
                     end_date=date(2026, 10, 27), days=2, half_day=False, status=status)
    r.id = uuid.uuid4()
    return r


def test_only_the_person_on_leave_asks_to_cancel(monkeypatch):
    r = _leave("approved")
    c = _client(FakeDB([r]), monkeypatch)
    assert c.post(f"/leave/requests/{r.id}/request-cancel", json={"reason": "x"}, headers=H("employee", OTHER)).status_code == 403
    ok = c.post(f"/leave/requests/{r.id}/request-cancel", json={"reason": "Trip postponed"}, headers=H("employee", PRIYA))
    assert ok.status_code == 200 and ok.json()["status"] == "cancel_requested"


def test_cancellation_decided_by_manager_not_requester(monkeypatch):
    r = _leave("cancel_requested")
    c = _client(FakeDB([r]), monkeypatch)
    assert c.post(f"/leave/requests/{r.id}/cancel-decision/approve", headers=H("employee", PRIYA)).status_code == 403
    assert c.post(f"/leave/requests/{r.id}/cancel-decision/approve", headers=H("manager", OTHER)).status_code == 403
    rej = c.post(f"/leave/requests/{r.id}/cancel-decision/reject", headers=H("manager", VIKRAM))
    assert rej.status_code == 200 and rej.json()["status"] == "approved"      # the leave stands


def test_policy_edit_hr_only(monkeypatch):
    c = _client(FakeDB(), monkeypatch)
    body = {"optional_holidays_per_year": 3, "comp_off_full_day_hours": 8, "comp_off_half_day_hours": 4, "comp_off_expiry_days": 60}
    assert c.put("/leave/ess-policy", json=body, headers=H("employee", PRIYA)).status_code == 403
    assert c.put("/leave/ess-policy", json=body, headers=H("manager", VIKRAM)).status_code == 403


def test_comp_off_cannot_be_self_approved(monkeypatch):
    from models_ess import CompOff
    co = CompOff(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(PRIYA), manager_id=uuid.UUID(VIKRAM), worked_date=date(2026, 10, 4),
                 days=1, status="pending")
    co.id = uuid.uuid4()
    c = _client(FakeDB([co]), monkeypatch)
    assert c.post(f"/leave/comp-off/{co.id}/approve", headers=H("hr_admin", PRIYA)).status_code == 403
    assert c.post(f"/leave/comp-off/{co.id}/cancel", headers=H("employee", OTHER)).status_code == 403
    assert c.post(f"/leave/comp-off/{co.id}/reject", headers=H("manager", VIKRAM)).json()["status"] == "rejected"
