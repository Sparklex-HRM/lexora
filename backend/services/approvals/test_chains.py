"""No-code approval chains (D1) — resolution scenarios, step advancing, escalation, auto-approve.

Run:  cd backend/services/approvals && PYTHONPATH=../../ python -m pytest -q test_chains.py
"""
import asyncio
import os
import sys
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import chains_runtime as rt  # noqa: E402
from shared.utils import approval_chains as ch  # noqa: E402
from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import directory, notify as nt  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

ORG = "o1"
USERS = [{"id": "u-adm", "email": "adm@x.test", "role": "org_admin", "employee_id": None, "first_name": "Org", "last_name": "Admin"},
         {"id": "u-hr", "email": "hr@x.test", "role": "hr_admin", "employee_id": "e-hr", "first_name": "Head", "last_name": "HR"},
         {"id": "u-fin", "email": "fin@x.test", "role": "finance", "employee_id": None, "first_name": "Fin", "last_name": "Ance"},
         {"id": "u-mgr", "email": "mgr@x.test", "role": "manager", "employee_id": "e-mgr", "first_name": "Team", "last_name": "Lead"},
         {"id": "u-dir", "email": "dir@x.test", "role": "manager", "employee_id": "e-dir", "first_name": "Sales", "last_name": "Director"},
         {"id": "u-emp", "email": "emp@x.test", "role": "employee", "employee_id": "e-emp", "first_name": "Staff", "last_name": "Member"}]
EMPS = {"e-emp": {"id": "e-emp", "manager_id": "e-mgr", "first_name": "Staff", "last_name": "Member", "department": "Sales"},
        "e-mgr": {"id": "e-mgr", "manager_id": "e-dir", "first_name": "Team", "last_name": "Lead"},
        "e-dir": {"id": "e-dir", "manager_id": None, "first_name": "Sales", "last_name": "Director"},
        "e-hr": {"id": "e-hr", "manager_id": None, "first_name": "Head", "last_name": "HR"}}
BY_ID = {u["id"]: u for u in USERS}

EXPENSE = {"steps": [
    {"id": "s1", "name": "Manager", "mode": "any", "approvers": [{"type": "manager", "level": 1}], "when": [],
     "escalate_after_hours": 24, "escalate_to": {"type": "role", "role": "hr"}, "sla_hours": 48},
    {"id": "s2", "name": "Finance + HR head", "mode": "all",
     "approvers": [{"type": "role", "role": "finance"}, {"type": "user", "user_id": "u-hr"}],
     "when": [{"field": "amount", "op": "gte", "value": 10000}]},
    {"id": "s3", "name": "Org admin", "mode": "any", "approvers": [{"type": "role", "role": "org_admin"}],
     "when": [{"field": "amount", "op": "gte", "value": 50000}, {"field": "department", "op": "in", "value": ["Sales", "Marketing"]}]}],
    "auto_approve": [{"name": "Petty cash", "when": [{"field": "amount", "op": "lt", "value": 500}]}], "sla_hours": 0}


def R(amount, dept="Sales", d=EXPENSE):
    return ch.resolve(d, {"amount": amount, "department": dept, "employee_id": "e-emp"}, employees=EMPS, users=BY_ID)


# ══════════════════════════ 1. thresholds + department conditions ══════════════

def test_threshold_picks_steps():
    r = R(2500)
    assert [s["name"] for s in r["steps"]] == ["Manager"] and r["skipped"] == ["Finance + HR head", "Org admin"]
    r = R(25000)
    assert [s["name"] for s in r["steps"]] == ["Manager", "Finance + HR head"]
    r = R(60000)
    assert [s["name"] for s in r["steps"]] == ["Manager", "Finance + HR head", "Org admin"]
    r = R(60000, dept="Ops")                                       # department condition fails → no org admin step
    assert [s["name"] for s in r["steps"]] == ["Manager", "Finance + HR head"]
    lines = ch.explain(R(25000))
    assert lines[0] == "Step 1 — Manager: Team Lead (manager of Staff Member) (escalates after 24 h; SLA 48 h)"
    assert lines[1] == "Step 2 — Finance + HR head: Finance AND Head HR"


# ══════════════════════════ 2. auto-approve ════════════════════════════════════

def test_auto_approve_rule():
    r = R(300)
    assert r["auto_approved"] and r["auto_rule"] == "Petty cash" and r["steps"] == []
    assert ch.explain(r) == ["Approved automatically by the rule 'Petty cash' — nobody has to decide."]
    with pytest.raises(ch.ChainError):                             # a rule without conditions would approve everything
        ch.validate_definition({"steps": EXPENSE["steps"], "auto_approve": [{"name": "x", "when": []}]})


# ══════════════════════════ 3. parallel (all) + any-of group ══════════════════

def test_parallel_step_needs_everyone_and_targets_shrink():
    step = R(25000)["steps"][1]
    assert ch.targets(step) == {"approver_roles": ["finance"], "approver_user_ids": ["u-hr"], "approver_manager_of_employee_id": None}
    done = ch.apply_approval(step, {"user_id": "u-fin", "role": "finance", "employee_id": None}, EMPS, by_name="Fin Ance")
    assert not done and ch.targets(step)["approver_roles"] == [] and ch.targets(step)["approver_user_ids"] == ["u-hr"]
    assert ch.waiting_labels(step) == ["Head HR"]
    done = ch.apply_approval(step, {"user_id": "u-hr", "role": "hr_admin", "employee_id": "e-hr"}, EMPS)
    assert done and step["pending"] == [] and [d["user_id"] for d in step["decided_by"]] == ["u-fin", "u-hr"]


def test_any_of_group_inside_a_parallel_step():
    d = {"steps": [{"name": "Two signatures", "mode": "all", "approvers": [
        {"type": "any_of", "approvers": [{"type": "role", "role": "finance"}, {"type": "role", "role": "payroll"}]},
        {"type": "role", "role": "org_admin"}]}]}
    step = ch.resolve(ch.validate_definition(d), {}, employees=EMPS, users=BY_ID)["steps"][0]
    assert step["approvers"][0]["label"] == "Finance or Payroll"
    assert set(ch.targets(step)["approver_roles"]) == {"finance", "payroll", "org_admin"}
    assert not ch.apply_approval(step, {"user_id": "u-fin", "role": "finance"}, EMPS)   # group satisfied, admin still pending
    assert ch.targets(step)["approver_roles"] == ["org_admin"]
    assert ch.apply_approval(step, {"user_id": "u-adm", "role": "org_admin"}, EMPS)


# ══════════════════════════ 4. manager level N + fallbacks ═════════════════════

def test_manager_levels_and_fallbacks():
    d = {"steps": [{"name": "L1", "approvers": [{"type": "manager", "level": 1}]},
                   {"name": "L2", "approvers": [{"type": "manager", "level": 2}]},
                   {"name": "L3", "approvers": [{"type": "manager", "level": 3}]}]}
    r = ch.resolve(ch.validate_definition(d), {"employee_id": "e-emp"}, employees=EMPS, users=BY_ID)
    s1, s2, s3 = r["steps"]
    assert s1["approvers"][0] == {"type": "manager", "employee_id": "e-emp", "level": 1, "label": "Team Lead (manager of Staff Member)"}
    assert s2["approvers"][0]["type"] == "user" and s2["approvers"][0]["user_id"] == "u-dir"    # resolved at open time
    assert s3["approvers"][0] == {"type": "role", "role": "hr", "label": "HR (no manager at level 3)", "fallback": True}
    # the manager spec is dynamic: whoever is the employee's manager when they act
    assert ch.spec_matches(s1["approvers"][0], {"user_id": "u-mgr", "role": "manager", "employee_id": "e-mgr"}, EMPS)
    assert not ch.spec_matches(s1["approvers"][0], {"user_id": "u-dir", "role": "manager", "employee_id": "e-dir"}, EMPS)
    with pytest.raises(ch.ChainError):
        ch.validate_definition({"steps": [{"approvers": [{"type": "manager", "level": 9}]}]})


# ══════════════════════════ 5. the seeded default = the caller's approvers ═════

def test_default_chain_keeps_the_screens_approvers():
    caller = {"approver_roles": ["org_admin", "hr"], "approver_user_ids": ["u-fin"], "approver_manager_of_employee_id": "e-emp"}
    r = ch.resolve(None, {}, caller=caller, employees=EMPS, users=BY_ID)
    assert len(r["steps"]) == 1 and ch.targets(r["steps"][0]) == caller
    # every step skipped by conditions → fall back to the caller's approvers (nobody is left waiting on nothing)
    d = {"steps": [{"name": "Only big", "approvers": [{"type": "role", "role": "finance"}], "when": [{"field": "amount", "op": "gte", "value": 1e9}]}]}
    r = ch.resolve(ch.validate_definition(d), {"amount": 5}, caller=caller, employees=EMPS, users=BY_ID)
    assert r["steps"][0]["id"] == "fallback" and ch.targets(r["steps"][0]) == caller


def test_conditions_cover_every_operator():
    c = {"amount": 1200, "department": "Sales", "tags": "", "grade": "L3"}
    assert ch.condition_holds({"field": "amount", "op": "gt", "value": "1000"}, c)
    assert ch.condition_holds({"field": "amount", "op": "lte", "value": 1200}, c)
    assert not ch.condition_holds({"field": "amount", "op": "ne", "value": 1200}, c)
    assert ch.condition_holds({"field": "department", "op": "in", "value": "sales, ops"}, c)
    assert ch.condition_holds({"field": "department", "op": "not_in", "value": ["Ops"]}, c)
    assert ch.condition_holds({"field": "department", "op": "contains", "value": "ale"}, c)
    assert ch.condition_holds({"field": "tags", "op": "is_empty"}, c) and ch.condition_holds({"field": "grade", "op": "not_empty"}, c)
    assert not ch.condition_holds({"field": "missing", "op": "eq", "value": 1}, c)
    assert not ch.condition_holds({"field": "amount", "op": "nope", "value": 1}, c)


# ══════════════════════════ DB flow: open → step 1 → step 2 → owner ═══════════

class Quiet:
    def configured(self):
        return False


@pytest.fixture()
def db(monkeypatch, tmp_path):
    async def setup():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chains.db'}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(pm.PlatformBase.metadata.create_all)
        pm.set_sessionmaker(async_sessionmaker(engine, expire_on_commit=False))
        async with pm.session() as s:
            s.add(ch.ApprovalChain(org_id=ORG, request_type="expense", name="Expenses", definition=ch.validate_definition(EXPENSE),
                                   version=2, is_default=False, active=True))
            await s.commit()

    async def users(org_id):
        return [dict(u) for u in USERS]

    async def emps(org_id):
        return [dict(e) for e in EMPS.values()]

    monkeypatch.setattr(directory, "org_users", users)
    monkeypatch.setattr(directory, "org_employees", emps)
    nt.set_transport(Quiet())
    yield setup
    pm.set_sessionmaker(None)
    nt.set_transport(nt.SmtpTransport())


async def _item(ref):
    async with pm.session() as s:
        return (await s.execute(select(pm.ApprovalItem).where(pm.ApprovalItem.ref_id == ref))).scalar_one()


async def _run(item_id):
    async with pm.session() as s:
        return (await s.execute(select(ch.ApprovalRun).where(ch.ApprovalRun.item_id == item_id))).scalar_one()


def test_open_item_uses_the_chain_then_steps_advance(db):
    async def go():
        await db()
        item_id = await ac.open_item(ORG, kind="expense", ref_id="X1", title="Expense — Staff Member", summary=["$2,500 travel"],
                                     requester_user_id="u-emp", approver_roles=["hr"], link="/hiring/offers",
                                     callback_path="/hiring/offers/X1/{decision}",
                                     context={"amount": 25000, "department": "Sales", "employee_id": "e-emp"})
        item = await _item("X1")
        # the chain replaced the screen's "hr" with the manager of the requester
        assert item.id == item_id and item.approver_roles == [] and item.approver_manager_of_employee_id == "e-emp"
        run = await _run(item.id)
        assert run.chain_name == "Expenses" and run.chain_version == 2 and run.current == 0 and len(run.steps) == 2
        assert rt.run_summary(run)["step_label"] == "Step 1 of 2: Manager" and rt.run_summary(run)["sla_due_at"]
        # the manager approves → not final; item re-targeted to finance + HR head
        async with pm.session() as s:
            i = (await s.execute(select(pm.ApprovalItem).where(pm.ApprovalItem.id == item.id))).scalar_one()
            r = (await s.execute(select(ch.ApprovalRun).where(ch.ApprovalRun.item_id == item.id))).scalar_one()
            out = await rt.advance(s, ORG, i, r, {"user_id": "u-mgr", "role": "manager", "employee_id": "e-mgr", "email": "mgr@x.test"},
                                   None, "fine by me", EMPS, BY_ID)
        assert out == {"final": False, "step": 2, "of": 2}
        item = await _item("X1")
        assert item.status == "pending" and item.approver_roles == ["finance"] and item.approver_user_ids == ["u-hr"]
        assert item.approver_manager_of_employee_id is None
        # finance approves → parallel step still waits for the HR head
        async with pm.session() as s:
            i = (await s.execute(select(pm.ApprovalItem).where(pm.ApprovalItem.id == item.id))).scalar_one()
            r = (await s.execute(select(ch.ApprovalRun).where(ch.ApprovalRun.item_id == item.id))).scalar_one()
            out = await rt.advance(s, ORG, i, r, {"user_id": "u-fin", "role": "finance", "employee_id": None, "email": ""}, None, "", EMPS, BY_ID)
        assert out["final"] is False
        item = await _item("X1")
        assert item.approver_roles == [] and item.approver_user_ids == ["u-hr"]
        # HR head approves → final: the caller now calls the owner and closes the item
        async with pm.session() as s:
            i = (await s.execute(select(pm.ApprovalItem).where(pm.ApprovalItem.id == item.id))).scalar_one()
            r = (await s.execute(select(ch.ApprovalRun).where(ch.ApprovalRun.item_id == item.id))).scalar_one()
            out = await rt.advance(s, ORG, i, r, {"user_id": "u-hr", "role": "hr_admin", "employee_id": "e-hr", "email": ""}, None, "", EMPS, BY_ID)
        assert out["final"] is True
        await ac.close_item(ORG, kind="expense", ref_id="X1", decision="approved", actor_user_id="u-hr")
        run = await _run(item.id)
        assert run.status == "approved" and [h["event"] for h in run.history][:3] == ["resolved", "approved_step", "step_started"]
        async with pm.session() as s:
            decs = (await s.execute(select(pm.ApprovalDecision))).scalars().all()
        assert sorted(d.via for d in decs) == ["owner", "step", "step"]        # two step rows + the final one
        await nt.drain()
    asyncio.run(go())


def test_escalation_adds_hr_after_24h(db):
    async def go():
        await db()
        await ac.open_item(ORG, kind="expense", ref_id="X2", title="Expense", summary=[], requester_user_id="u-emp",
                           link="/hiring/offers", callback_path="/payroll/x/X2/{decision}",
                           context={"amount": 2000, "department": "Sales", "employee_id": "e-emp"})
        item = await _item("X2")
        assert item.approver_roles == []
        now = pm.now_utc()
        assert await rt.escalate_due(ORG, now + timedelta(hours=23)) == 0
        assert await rt.escalate_due(ORG, now + timedelta(hours=25)) == 1
        assert await rt.escalate_due(ORG, now + timedelta(hours=30)) == 0            # once per step
        item = await _item("X2")
        assert item.approver_roles == ["hr"] and item.approver_manager_of_employee_id == "e-emp"   # manager can still act
        run = await _run(item.id)
        assert run.escalated_at is not None and run.history[-1]["event"] == "escalated" and run.history[-1]["to"] == ["HR (any HR role)"]
        await nt.drain()
    asyncio.run(go())


def test_auto_approve_calls_the_owner_and_closes(db, monkeypatch):
    calls = []

    async def fake_post(org, path, verb, **kw):
        calls.append((path, verb, kw["actor"]["role"]))
        return True, ""

    async def go():
        await db()
        monkeypatch.setattr(ac, "post_callback", fake_post)
        monkeypatch.setattr(ac, "AUTO_APPROVE_DELAY_SECONDS", 0.01)
        await ac.open_item(ORG, kind="expense", ref_id="X3", title="Tea", summary=[], requester_user_id="u-emp",
                           link="/hiring/offers", callback_path="/hiring/offers/X3/{decision}",
                           context={"amount": 120, "department": "Sales", "employee_id": "e-emp"})
        await asyncio.sleep(0.2)
        item = await _item("X3")
        assert item.status == "approved" and calls == [("/hiring/offers/X3/{decision}", "approve", "org_admin")]
        run = await _run(item.id)
        assert run.status == "approved" and run.history[0]["note"] == "auto-approve rule 'Petty cash'"
        async with pm.session() as s:
            d = (await s.execute(select(pm.ApprovalDecision))).scalars().one()
        assert d.via == "auto" and d.actor_name == "Auto-approval rule"
        await nt.drain()
    asyncio.run(go())


def test_workflow_routes_save_simulate_and_public_resolve(db):
    """The settings routes (validated save, audit-free here) and the read-only resolve D3 calls."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import chains_routers as cr

    async def no_audit(*a, **kw):
        return True
    cr.audit_change = no_audit
    app = FastAPI()
    app.include_router(cr.router)
    app.include_router(cr.public)
    H = lambda role, emp=None: {"X-Org-Id": ORG, "X-Role": role, "X-User-Id": "u-x", **({"X-Employee-Id": emp} if emp else {})}

    async def go():
        await db()
    asyncio.run(go())
    with TestClient(app) as c:
        lst = c.get("/approvals/workflows", headers=H("hr_admin")).json()
        assert {i["request_type"] for i in lst["items"]} >= set(ch.REQUEST_TYPES) and lst["can_edit"]
        exp = next(i for i in lst["items"] if i["request_type"] == "expense")
        assert exp["version"] == 2 and exp["steps"] == 3 and exp["auto_rules"] == 1
        assert c.get("/approvals/workflows/meta", headers=H("employee")).status_code == 403
        bad = c.put("/approvals/workflows/leave", json={"definition": {"steps": []}, "active": True}, headers=H("hr_admin"))
        assert bad.status_code == 422 and "at least one step" in bad.json()["detail"]
        assert c.put("/approvals/workflows/leave", json={"definition": {"steps": [{"approvers": [{"type": "role", "role": "hr"}]}]}, "active": True}, headers=H("hr_manager")).status_code == 403
        ok = c.put("/approvals/workflows/leave", json={"name": "Leave two-step", "definition": {"steps": [
            {"name": "Manager", "approvers": [{"type": "manager", "level": 1}]},
            {"name": "HR for long leave", "approvers": [{"type": "role", "role": "hr"}], "when": [{"field": "leave_days", "op": "gte", "value": 5}]}]}, "active": True}, headers=H("hr_admin"))
        assert ok.status_code == 200 and ok.json()["version"] == 2 and ok.json()["is_default"] is False
        sim = c.post("/approvals/workflows/expense/simulate", json={"context": {"amount": 25000, "department": "Sales"}, "requester_employee_id": "e-emp"}, headers=H("hr_manager")).json()
        assert [s["name"] for s in sim["steps"]] == ["Manager", "Finance + HR head"] and sim["steps"][0]["approvers"] == ["Team Lead (manager of Staff Member)"]
        # D3: any signed-in user, their own context (employee id + department from their record)
        r = c.get("/approvals/chains/resolve", params={"request_type": "expense", "amount": 60000}, headers=H("employee", "e-emp")).json()
        assert r["context"]["department"] == "Sales" and [s["name"] for s in r["steps"]] == ["Manager", "Finance + HR head", "Org admin"]
        assert r["lines"][0].startswith("Step 1 — Manager: Team Lead (manager of Staff Member)")
        r = c.get("/approvals/chains/resolve", params={"request_type": "leave", "days": 7}, headers=H("employee", "e-emp")).json()
        assert [s["name"] for s in r["steps"]] == ["Manager", "HR for long leave"] and r["context"]["leave_days"] == "7"
        r = c.get("/approvals/chains/leave", params={"days": 2}, headers=H("employee", "e-emp")).json()
        assert [s["name"] for s in r["steps"]] == ["Manager"] and r["skipped"] == ["HR for long leave"]
        assert c.get("/approvals/chains", params={"request_type": "expense", "amount": 100}, headers=H("employee", "e-emp")).json()["auto_approved"]
        assert "request_types" in c.get("/approvals/chains", headers=H("employee")).json()
        assert c.post("/approvals/workflows/leave/reset", headers=H("org_admin")).json()["is_default"] is True


def test_unregistered_kind_falls_back_to_the_screens_approvers(db):
    async def go():
        await db()
        await ac.open_item(ORG, kind="something_new", ref_id="N1", title="New", summary=[], requester_user_id="u-emp",
                           approver_roles=["org_admin"], approver_user_ids=["u-fin"], link="/x", callback_path="", context={})
        item = await _item("N1")
        assert item.approver_roles == ["org_admin"] and item.approver_user_ids == ["u-fin"]
        run = await _run(item.id)
        assert run.chain_name == "Default" and rt.run_summary(run)["step_label"] == "Step 1 of 1: Approver named by the screen"
        # NO context (a caller that drives its own steps, e.g. comp cycles / hiring offers): plain registry, no run
        await ac.open_item(ORG, kind="expense", ref_id="N2", title="Own steps", summary=[], requester_user_id="u-emp",
                           approver_roles=["hr_admin"], link="/x", callback_path="")
        item = await _item("N2")
        assert item.approver_roles == ["hr_admin"] and item.approver_manager_of_employee_id is None
        async with pm.session() as s:
            assert (await s.execute(select(ch.ApprovalRun).where(ch.ApprovalRun.item_id == item.id))).scalar_one_or_none() is None
        await nt.drain()
    asyncio.run(go())


def test_resolve_chain_entry_point_for_other_streams(db):
    """D2 (hiring_offer) and D5 (comp_cycle) call ONE async resolver, from either module."""
    async def go():
        await db()
        assert ac.resolve_chain is ch.resolve_chain
        # a customised chain → ordered steps carrying both shapes (roles[]/user_ids[] and kind/role/user_id/employee_id)
        steps = await ch.resolve_chain(ORG, request_type="expense", metadata={"ctc": 25000, "department": ["Sales"], "entity": "E1",
                                                                            "requester_employee_id": "e-emp"})
        assert [s["step"] for s in steps] == [1, 2]
        assert steps[0]["kind"] == "manager" and steps[0]["employee_id"] == "e-mgr" and steps[0]["manager_of_employee_id"] == "e-emp"
        assert steps[0]["label"] == "Team Lead (manager of Staff Member)" and steps[0]["chain"] == "Expenses" and steps[0]["is_default"] is False
        assert steps[1] == {**steps[1], "mode": "all", "roles": ["finance"], "user_ids": ["u-hr"], "kind": "role", "role": "finance"}
        # metadata aliases: ctc → amount; a list-valued department matches "in"
        big = await ac.resolve_chain(ORG, "expense", {"ctc": 60000, "department": ["Ops", "Sales"], "requester_employee_id": "e-emp"})
        assert [s["name"] for s in big] == ["Manager", "Finance + HR head", "Org admin"]
        # auto-approve → [] and the rule name is written back into the metadata
        meta = {"total_amount": 100, "requester_employee_id": "e-emp"}
        assert await ac.resolve_chain(ORG, "expense", meta) == [] and meta["_auto_rule"] == "Petty cash"
        # the seeded default (not customised) → [] so the caller keeps its own fallback chain …
        assert await ch.resolve_chain(ORG, request_type="hiring_offer", metadata={"ctc": 950000}) == []
        # … unless the caller hands over its fallback approvers, which the default step then returns
        steps = await ch.resolve_chain(ORG, "comp_cycle", {"total_amount": 2.5e6}, approver_roles=["hr_admin"])
        assert len(steps) == 1 and steps[0]["roles"] == ["hr_admin"] and steps[0]["is_default"] is True and steps[0]["kind"] == "role"
        await nt.drain()
    asyncio.run(go())
