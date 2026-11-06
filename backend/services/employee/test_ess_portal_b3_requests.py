"""Employee portal batch 3 — "Raise a request" + "My tasks": pure rules and role checks on every route.
Run:  cd backend/services/employee && python -m pytest -q test_ess_portal_b3_requests.py
"""
import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ess_requests_logic as L
import ess_requests_routers as RT
from database import get_db
from ess_requests_models import EssRequest, EssRequestType, EssTodo
from models import Employee

ORG = str(uuid.uuid4())
PRIYA, VIKRAM, OTHER, HRP = (str(uuid.uuid4()) for _ in range(4))
PRIYA_UID = "user-priya"

LETTER = [{"label": "Purpose", "key": "purpose", "type": "select", "options": ["visa", "bank", "rent", "other"], "required": True},
          {"label": "Addressed to", "type": "text", "required": True}]
ADDRESS = [{"label": "New address", "type": "text", "required": True},
           {"label": "Effective date", "type": "date", "required": True},
           {"label": "Proof", "type": "file"}]


# ── pure rules ────────────────────────────────────────────────────────────────

def test_fields_use_the_d1_definition_shape_and_rules():
    f = L.normalise_fields(LETTER)
    assert [x["key"] for x in f] == ["purpose", "addressed_to"]
    assert f[0]["options"] == ["visa", "bank", "rent", "other"] and f[0]["required"] is True
    assert "entity" not in f[0] and "visible_to" not in f[0]
    with pytest.raises(L.RequestError, match="at least one option"):
        L.normalise_fields([{"label": "Reason", "type": "select", "options": []}])
    with pytest.raises(L.RequestError, match="Unknown type"):
        L.normalise_fields([{"label": "X", "type": "colour"}])
    with pytest.raises(L.RequestError, match="share the key"):
        L.normalise_fields([{"label": "Reason"}, {"label": "reason"}])


def test_type_key_is_slugged_and_never_changes():
    t = L.normalise_type({"name": "Employment letter", "fields": LETTER, "category": "Letters"})
    assert t["key"] == "employment_letter" and t["active"] is True and len(t["fields"]) == 2
    t2 = L.normalise_type({"name": "Renamed", "key": "zzz"}, existing_key="employment_letter")
    assert t2["key"] == "employment_letter"
    with pytest.raises(L.RequestError):
        L.normalise_type({"name": ""})


def test_values_are_coerced_by_d1_and_required_enforced():
    f = L.normalise_fields(ADDRESS)
    ok = L.clean_values(f, {"new_address": " 12 MG Road, Bengaluru ", "effective_date": "2026-11-01"})
    assert ok == {"new_address": "12 MG Road, Bengaluru", "effective_date": "2026-11-01"}
    with pytest.raises(L.RequestError, match="New address is required"):
        L.clean_values(f, {"effective_date": "2026-11-01"})
    with pytest.raises(L.RequestError, match="must be a date"):
        L.clean_values(f, {"new_address": "x", "effective_date": "next week"})
    with pytest.raises(L.RequestError, match="not a field"):
        L.clean_values(f, {"new_address": "x", "effective_date": "2026-11-01", "salary": 1})
    lf = L.normalise_fields(LETTER)
    with pytest.raises(L.RequestError, match="not one of the choices"):
        L.clean_values(lf, {"purpose": "loan", "addressed_to": "HDFC"})
    rows = L.display_rows(f, {"new_address": "x", "effective_date": "2026-11-01", "proof": "abc"})
    assert [r["label"] for r in rows] == ["New address", "Effective date", "Proof"]
    assert L.file_keys(f, {"proof": "abc"}) == ["proof"]
    assert "Proof: attached" in L.summary_lines(f, {"proof": "abc"})


def test_who_may_see_decide_withdraw():
    assert L.may_view("employee", PRIYA, PRIYA, VIKRAM) and not L.may_view("employee", OTHER, PRIYA, VIKRAM)
    assert L.may_view("manager", VIKRAM, PRIYA, VIKRAM) and not L.may_view("manager", OTHER, PRIYA, VIKRAM)
    assert L.may_view("hr_admin", HRP, PRIYA, VIKRAM)
    assert not L.may_decide("hr_admin", PRIYA, PRIYA, VIKRAM)                    # HR raising for themself
    assert not L.may_decide("hr_admin", None, PRIYA, VIKRAM, actor_user_id="u1", requester_user_id="u1")
    assert L.may_decide("hr_manager", HRP, PRIYA, VIKRAM)
    assert L.may_decide("manager", VIKRAM, PRIYA, VIKRAM) and not L.may_decide("manager", OTHER, PRIYA, VIKRAM)
    assert not L.may_decide("employee", OTHER, PRIYA, VIKRAM)
    assert L.may_withdraw(PRIYA, PRIYA, "pending")[0]
    assert not L.may_withdraw(PRIYA, PRIYA, "approved")[0] and not L.may_withdraw(OTHER, PRIYA, "pending")[0]
    assert [L.list_scope(r, PRIYA) for r in ("hr_admin", "manager", "employee")] == ["all", "team", "self"]
    assert L.list_scope("employee", None) == "none"


def test_todo_rules():
    assert L.clean_todo({"title": " Submit Form 12BB ", "due_date": "2026-10-31"}) == \
        {"title": "Submit Form 12BB", "due_date": date(2026, 10, 31), "note": None}
    assert L.clean_todo({"done": True}, partial=True) == {"done": True}
    with pytest.raises(L.RequestError):
        L.clean_todo({"title": ""})
    with pytest.raises(L.RequestError):
        L.clean_todo({"title": "x", "due_date": "soon"})


def test_exit_line_ownership():
    case = {"id": "c1", "employee_id": PRIYA, "manager_id": VIKRAM, "employee_name": "Priya"}
    mgr_line = {"owner_role": "manager", "owner_employee_id": VIKRAM}
    it_line = {"owner_role": "it_admin"}
    named = {"owner_role": "hr_admin", "owner_user_id": "u-raj"}
    assert L.exit_line_is_mine({"role": "manager", "employee_id": VIKRAM}, mgr_line, case)
    assert not L.exit_line_is_mine({"role": "manager", "employee_id": OTHER}, mgr_line, case)   # not her manager
    assert not L.exit_line_is_mine({"role": "employee", "employee_id": OTHER}, it_line, case)
    assert L.exit_line_is_mine({"role": "it_admin", "employee_id": OTHER}, it_line, case)
    assert L.exit_line_is_mine({"role": "employee", "user_id": "u-raj", "employee_id": OTHER}, named, case)
    assert not L.exit_line_is_mine({"role": "hr_admin", "employee_id": PRIYA}, named, case)     # own case
    own = L.own_exit_task({"id": "i1", "department": "IT", "item": "Return laptop", "status": "pending"},
                          {**case, "lwd": date(2026, 11, 30)})
    assert own["source"] == "exit_clearance" and own["due_date"] == "2026-11-30" and own["editable"] is False


def test_merge_orders_open_first_then_due():
    rows = L.merge_tasks(
        [L.todo_task({"id": "1", "title": "B", "due_date": "2026-10-20", "done": True}),
         L.todo_task({"id": "2", "title": "A", "due_date": None, "done": False})],
        [L.onboarding_task({"id": "o1", "title": "Sign NDA", "owner": "Employee", "due_date": "2026-10-18"},
                           {"first_name": "Priya"}, for_manager=False)])
    assert [r["title"] for r in rows] == ["Sign NDA", "A", "B"]
    assert rows[0]["yours"] is True and rows[0]["source"] == "onboarding"
    c = L.task_counts(rows, "2026-10-19")
    assert c == {"total": 3, "open": 2, "done": 1, "overdue": 1, "by_source": {"onboarding": 1, "todo": 1}}


# ── routes ────────────────────────────────────────────────────────────────────

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

    def scalar_one(self):
        return self.rows[0]


class FakeDB:
    """Returns the stored rows of whatever class the SELECT is about (no WHERE evaluation —
    the routes re-check ownership in Python, which is what these tests pin)."""
    def __init__(self, *rows):
        self.rows = list(rows)
        self.added, self.deleted = [], []

    async def execute(self, stmt, *a, **k):
        ent = stmt.column_descriptions[0].get("entity")
        return _R([r for r in self.rows if ent is not None and isinstance(r, ent)])

    def add(self, o):
        o.id = getattr(o, "id", None) or uuid.uuid4()
        self.added.append(o)
        self.rows.append(o)

    async def delete(self, o):
        self.deleted.append(o)

    async def commit(self):
        pass

    async def refresh(self, o):
        pass


@pytest.fixture
def calls(monkeypatch):
    log = {"open": [], "close": [], "notify": []}

    async def open_item(*a, **k):
        log["open"].append(k)
        return "item"

    async def close_item(*a, **k):
        log["close"].append(k)
        return ""

    async def notify(*a, **k):
        log["notify"].append(k)

    async def audit(*a, **k):
        return True
    monkeypatch.setattr(RT.approvals_client, "open_item", open_item)
    monkeypatch.setattr(RT.approvals_client, "close_item", close_item)
    monkeypatch.setattr(RT, "notify", notify)
    monkeypatch.setattr(RT, "record_audit", audit)
    return log


def _client(db):
    app = FastAPI()
    app.include_router(RT.router)

    async def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def H(role, emp, uid=None):
    return {"x-org-id": ORG, "x-user-id": uid or str(uuid.uuid4()), "x-role": role, "x-employee-id": emp or "",
            "x-user-email": f"{role}@maple.test"}


def _type(fields=LETTER, active=True, key="employment_letter"):
    t = EssRequestType(org_id=uuid.UUID(ORG), key=key, name="Employment letter", fields=L.normalise_fields(fields),
                       active=active, sort_order=10)
    t.id = uuid.uuid4()
    return t


def _priya():
    e = Employee(org_id=uuid.UUID(ORG), first_name="Priya", last_name="Menon", department="Engineering",
                 manager_id=uuid.UUID(VIKRAM))
    e.id = uuid.UUID(PRIYA)
    return e


def _req(status="pending"):
    r = EssRequest(org_id=uuid.UUID(ORG), type_id=uuid.uuid4(), type_key="employment_letter", type_name="Employment letter",
                   employee_id=uuid.UUID(PRIYA), employee_name="Priya Menon", manager_id=uuid.UUID(VIKRAM),
                   requester_user_id=PRIYA_UID, answers={"purpose": "visa", "addressed_to": "Consulate"},
                   fields_snapshot=L.normalise_fields(LETTER), status=status)
    r.id = uuid.uuid4()
    return r


def test_only_hr_defines_types(calls):
    c = _client(FakeDB())
    body = {"name": "Employment letter", "fields": LETTER}
    assert c.post("/employees/ess/request-types", json=body, headers=H("employee", PRIYA)).status_code == 403
    assert c.post("/employees/ess/request-types", json=body, headers=H("manager", VIKRAM)).status_code == 403
    ok = c.post("/employees/ess/request-types", json=body, headers=H("hr_admin", HRP))
    assert ok.status_code == 201 and ok.json()["key"] == "employment_letter"
    bad = c.post("/employees/ess/request-types", json={"name": "X", "fields": [{"label": "R", "type": "select"}]},
                 headers=H("hr_admin", HRP))
    assert bad.status_code == 422


def test_duplicate_key_refused_and_patch_hr_only(calls):
    t = _type()
    c = _client(FakeDB(t))
    assert c.post("/employees/ess/request-types", json={"name": "Employment letter"}, headers=H("hr_admin", HRP)).status_code == 409
    assert c.patch(f"/employees/ess/request-types/{t.id}", json={"active": False}, headers=H("employee", PRIYA)).status_code == 403
    r = c.patch(f"/employees/ess/request-types/{t.id}", json={"active": False, "key": "hack"}, headers=H("hr_manager", HRP))
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["key"] == "employment_letter"


def test_employees_see_only_active_types(calls):
    c = _client(FakeDB(_type(), _type(active=False, key="old")))
    assert len(c.get("/employees/ess/request-types?all=1", headers=H("employee", PRIYA)).json()) == 1
    assert len(c.get("/employees/ess/request-types?all=1", headers=H("hr_admin", HRP)).json()) == 2


def test_raise_validates_and_opens_the_chain(calls):
    t = _type()
    db = FakeDB(t, _priya())
    c = _client(db)
    bad = c.post("/employees/ess/requests", json={"type_id": str(t.id), "values": {"purpose": "loan", "addressed_to": "x"}},
                 headers=H("employee", PRIYA, PRIYA_UID))
    assert bad.status_code == 422 and "not one of the choices" in bad.json()["detail"]
    assert c.post("/employees/ess/requests", json={"type_id": str(t.id), "values": {}},
                  headers=H("employee", None)).status_code == 400
    ok = c.post("/employees/ess/requests", json={"type_key": "employment_letter",
                                                 "values": {"purpose": "visa", "addressed_to": "German Consulate"}},
                headers=H("employee", PRIYA, PRIYA_UID))
    assert ok.status_code == 201 and ok.json()["status"] == "pending" and ok.json()["can_withdraw"] is True
    o = calls["open"][-1]
    assert o["kind"] == "employee_request" and o["approver_manager_of_employee_id"] == PRIYA
    assert o["requester_user_id"] == PRIYA_UID and o["callback_path"].startswith("/employees/ess/requests/")
    assert o["callback_path"].endswith("/{decision}")
    assert o["context"] == {"request_type": "employment_letter", "department": "Engineering", "employee_id": PRIYA}


def test_inactive_type_cannot_be_raised(calls):
    t = _type(active=False)
    c = _client(FakeDB(t, _priya()))
    assert c.post("/employees/ess/requests", json={"type_id": str(t.id), "values": {}},
                  headers=H("employee", PRIYA)).status_code == 404


def test_file_answer_must_be_the_requesters_document(calls):
    from shared.utils.docstore import StoredDocument
    t = _type(ADDRESS, key="address_change")
    d = StoredDocument(org_id=uuid.UUID(ORG), employee_id=uuid.UUID(OTHER), doc_type="request_attachment")
    d.id = uuid.uuid4()
    c = _client(FakeDB(t, _priya(), d))
    body = {"type_id": str(t.id), "values": {"new_address": "1 Park St", "effective_date": "2026-11-01", "proof": str(d.id)}}
    r = c.post("/employees/ess/requests", json=body, headers=H("employee", PRIYA))
    assert r.status_code == 422 and "upload it again" in r.json()["detail"]


def test_list_visibility(calls):
    mine = _req()
    c = _client(FakeDB(mine))
    assert len(c.get("/employees/ess/requests", headers=H("employee", PRIYA)).json()) == 1
    assert c.get("/employees/ess/requests", headers=H("employee", OTHER)).json() == []
    assert len(c.get("/employees/ess/requests", headers=H("manager", VIKRAM)).json()) == 1
    assert c.get("/employees/ess/requests", headers=H("manager", OTHER)).json() == []
    assert len(c.get("/employees/ess/requests", headers=H("hr_admin", HRP)).json()) == 1
    assert c.get(f"/employees/ess/requests/{mine.id}", headers=H("employee", OTHER)).status_code == 404


def test_decision_rule_never_the_requester(calls):
    r = _req()
    c = _client(FakeDB(r))
    assert c.post(f"/employees/ess/requests/{r.id}/approve", headers=H("employee", PRIYA, PRIYA_UID)).status_code == 403
    assert c.post(f"/employees/ess/requests/{r.id}/approve", headers=H("hr_admin", PRIYA, PRIYA_UID)).status_code == 403
    assert c.post(f"/employees/ess/requests/{r.id}/approve", headers=H("manager", OTHER)).status_code == 404   # not visible
    ok = c.post(f"/employees/ess/requests/{r.id}/reject", json={"decision": "reject", "comment": "Use the bank template"},
                headers=H("manager", VIKRAM))
    assert ok.status_code == 200 and ok.json()["status"] == "rejected" and ok.json()["decision_note"] == "Use the bank template"
    assert calls["close"][-1]["decision"] == "rejected" and calls["notify"][-1]["employee_ids"] == [PRIYA]
    assert c.post(f"/employees/ess/requests/{r.id}/approve", headers=H("hr_admin", HRP)).status_code == 409


def test_hr_approves_via_callback(calls):
    r = _req()
    c = _client(FakeDB(r))
    ok = c.post(f"/employees/ess/requests/{r.id}/approve", json={"decision": "approve", "comment": "", "note": ""},
                headers=H("hr_admin", HRP))
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert calls["close"][-1] == {"kind": "employee_request", "ref_id": str(r.id), "decision": "approved",
                                  "actor_user_id": calls["close"][-1]["actor_user_id"]}


def test_withdraw_only_requester_while_pending(calls):
    r = _req()
    c = _client(FakeDB(r))
    assert c.post(f"/employees/ess/requests/{r.id}/withdraw", headers=H("hr_admin", HRP)).status_code == 403
    ok = c.post(f"/employees/ess/requests/{r.id}/withdraw", headers=H("employee", PRIYA, PRIYA_UID))
    assert ok.status_code == 200 and ok.json()["status"] == "withdrawn" and calls["close"][-1]["decision"] == "withdrawn"
    assert c.post(f"/employees/ess/requests/{r.id}/withdraw", headers=H("employee", PRIYA, PRIYA_UID)).status_code == 409


def test_todos_are_private(calls):
    t = EssTodo(org_id=uuid.UUID(ORG), user_id=PRIYA_UID, employee_id=uuid.UUID(PRIYA), title="Submit 12BB", done=False)
    t.id = uuid.uuid4()
    db = FakeDB(t)
    c = _client(db)
    assert c.patch(f"/employees/ess/todos/{t.id}", json={"done": True}, headers=H("employee", OTHER, "user-other")).status_code == 404
    assert c.delete(f"/employees/ess/todos/{t.id}", headers=H("hr_admin", HRP, "user-hr")).status_code == 404
    ok = c.patch(f"/employees/ess/todos/{t.id}", json={"done": True}, headers=H("employee", PRIYA, PRIYA_UID))
    assert ok.status_code == 200 and ok.json()["done"] is True and ok.json()["done_at"]
    new = c.post("/employees/ess/todos", json={"title": "Book health check", "due_date": "2026-10-30"},
                 headers=H("employee", PRIYA, PRIYA_UID))
    assert new.status_code == 201 and new.json()["due_date"] == "2026-10-30"
    assert c.post("/employees/ess/todos", json={"title": ""}, headers=H("employee", PRIYA, PRIYA_UID)).status_code == 422
    assert c.delete(f"/employees/ess/todos/{t.id}", headers=H("employee", PRIYA, PRIYA_UID)).status_code == 200
    assert db.deleted == [t]


def test_tasks_merge_sources(calls, monkeypatch):
    t = EssTodo(org_id=uuid.UUID(ORG), user_id=PRIYA_UID, title="Renew passport", done=False)
    t.id = uuid.uuid4()

    async def ex(db, org, actor):
        assert actor["employee_id"] == PRIYA and actor["user_id"] == PRIYA_UID
        return [L.own_exit_task({"id": "i1", "department": "IT", "item": "Return laptop", "status": "pending"},
                                {"id": "c1", "employee_id": PRIYA, "lwd": date(2026, 11, 30)})]

    async def onb(request, db, org, me, role):
        return [], "unavailable"
    monkeypatch.setattr(RT, "exit_tasks", ex)
    monkeypatch.setattr(RT, "onboarding_tasks", onb)
    c = _client(FakeDB(t))
    r = c.get("/employees/ess/tasks", headers=H("employee", PRIYA, PRIYA_UID)).json()
    assert {x["source"] for x in r["items"]} == {"todo", "exit_clearance"}
    assert [x["editable"] for x in r["items"] if x["source"] == "todo"] == [True]
    assert r["sources"] == {"todo": "ok", "exit": "ok", "onboarding": "unavailable"}
