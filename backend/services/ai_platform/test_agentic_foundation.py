"""Agentic foundation tests (2026-10-25) — tiers, two-person rule, shadow mode, hash chain, mode gate,
tasks + chasing, session grants, value ledger. SQLite in memory, no network, no SMTP, no model calls.

Run:  cd backend/services/ai_platform && python -m pytest -q test_agentic_foundation.py
"""
import asyncio
import os
import sys
from datetime import timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)
os.environ.pop("ANTHROPIC_API_KEY", None)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
    os.environ[k] = ""

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from shared.utils import org_settings  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_proposals as PR  # noqa: E402
import agentic_registry as REG  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_scheduler as SCH  # noqa: E402
import agentic_state as ST  # noqa: E402
import agentic_tasks as TK  # noqa: E402
import agentic_value as VAL  # noqa: E402
from agentic_models import AGENTIC_TABLES  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
A1 = PR.Approver("u-hr", "hr@x.ca", "hr_admin")
A2 = PR.Approver("u-pay", "pay@x.ca", "payroll_admin")
CEO = PR.Approver("u-ceo", "ceo@x.ca", "org_admin")
EMP = PR.Approver("u-emp", "emp@x.ca", "employee")


_LOOP: dict = {}


def run(coro):
    """One event loop per test (the fixture owns it) — aiosqlite's worker thread must outlive every call."""
    return _LOOP["loop"].run_until_complete(coro)


@pytest.fixture(autouse=True)
def db(monkeypatch):
    _LOOP["loop"] = asyncio.new_event_loop()
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    sm = async_sessionmaker(eng, expire_on_commit=False)

    async def mk():
        async with eng.begin() as c:
            await c.run_sync(lambda cc: pm.PlatformBase.metadata.create_all(cc))
    run(mk())
    pm.set_sessionmaker(sm)
    org_settings.invalidate()
    addons = {"v": ["agentic"]}

    async def fake_addons(org):
        return list(addons["v"])
    monkeypatch.setattr(ST, "org_addons", fake_addons)
    saved = dict(REG.EXECUTORS)
    yield {"sm": sm, "addons": addons}
    REG.EXECUTORS.clear()
    REG.EXECUTORS.update(saved)
    pm.set_sessionmaker(None)
    org_settings.invalidate()
    run(eng.dispose())
    _LOOP["loop"].close()


def go_live(reason="Pilot approved by the payroll head on 25 Oct"):
    run(ST.set_mode(ORG, "live", actor_id="u-ceo", actor_email="ceo@x.ca", reason=reason))
    org_settings.invalidate()


def calls_executor(name="exit.draft_exit_letters"):
    calls = []

    @REG.register_executor(name)
    async def _x(org, proposal, args):
        calls.append((org, proposal["id"], args))
        return {"id": "row-1", "status": "done"}
    return calls


def propose(tool="exit.draft_exit_letters", agent="exit", **kw):
    base = dict(tool_args={"employee_id": "e-17", "component": "LETTER", "value": 0, "sin": "046454286", "amount": 3200},
                plain_action="Draft the separation letter for MAP017", reason="Last working day reached", before={"drafts": 0}, after={"drafts": 1},
                aggregate_impact={"headcount": 1, "dollars": 3200}, evidence_refs=[{"label": "R-123", "link": "/attendance"}], confidence="high")
    base.update(kw)
    return run(PR.propose(ORG, agent=agent, tool_name=tool, **base))


# ══════════════════════════ tiers ══════════════════════════════════════════════

def test_tiers_untrusted_adds_one_and_bulk_lifts_to_two():
    th = {"bulk_employees": 25, "net_delta_floor_cad": 1000}
    assert REG.tier_for("exit", "exit.chase", thresholds=th)["tier"] == 0
    assert REG.tier_for("exit", "exit.draft_exit_letters", thresholds=th)["tier"] == 1
    assert REG.tier_for("exit", "exit.draft_exit_letters", untrusted_input=True, thresholds=th)["tier"] == 2
    assert REG.tier_for("exit", "exit.chase", untrusted_input=True, thresholds=th)["tier"] == 1
    assert REG.tier_for("exit", "exit.run_final_pay", untrusted_input=True, thresholds=th)["tier"] == 2      # capped
    assert REG.tier_for("exit", "exit.draft_exit_letters", aggregate_impact={"headcount": 30}, thresholds=th)["tier"] == 2
    assert REG.tier_for("exit", "exit.draft_exit_letters", aggregate_impact={"dollars": 50000}, thresholds=th)["tier"] == 2
    assert REG.tier_for("hr_desk", "never_heard_of", thresholds=th)["tier"] == 1                                         # unknown → never auto
    assert REG.tier_for("hr_desk", "disable_audit", thresholds=th)["tier"] == "deny"


# ══════════════════════════ shadow mode ════════════════════════════════════════

def test_shadow_mode_records_and_never_executes():
    calls = calls_executor()
    st = run(ST.agentic_state(ORG))
    assert st["enabled"] and st["mode"] == "shadow" and st["addon_purchased"]
    p = propose()
    assert p["status"] == "shadow" and p["policy_decision"] == "shadow" and p["mode"] == "shadow"
    assert calls == []
    with pytest.raises(PR.ProposalError) as e:
        run(PR.approve(ORG, p["id"], A1))
    assert e.value.status == 409
    assert calls == []
    # tier 0 in shadow is ALSO only recorded
    p0 = propose(tool="exit.chase")
    assert p0["status"] == "shadow" and p0["risk_tier"] == 0
    ev = run(LOG.recent(ORG, event_type="proposal.shadow"))
    assert len(ev) == 2 and all(e["policy_decision"] == "shadow" for e in ev)
    assert "046454286" not in str(p["tool_args"]) and "046454286" not in str(ev)          # PII tokenised on the card and in the log


def test_addon_not_purchased_means_off(db):
    db["addons"]["v"] = []
    st = run(ST.agentic_state(ORG))
    assert st["enabled"] is False and st["addon_purchased"] is False
    with pytest.raises(PR.ProposalError) as e:
        propose()
    assert e.value.status == 409
    with pytest.raises(RUNS.RunError):
        run(RUNS.start_run(ORG, agent="exit", trigger="manual"))
    assert run(SCH.tick_org(ORG)) is None                                                    # scheduler skips the org
    run(ST.save_settings(ORG, {"enabled": True}, actor_id="u-ceo", actor_email="ceo@x.ca"))  # explicit admin switch wins
    org_settings.invalidate()
    assert run(ST.agentic_state(ORG))["enabled"] is True
    db["addons"]["v"] = ["agentic"]
    run(ST.save_settings(ORG, {"enabled": False}, actor_id="u-ceo", actor_email="ceo@x.ca"))
    org_settings.invalidate()
    st = run(ST.agentic_state(ORG))
    assert st["enabled"] is False and st["paused"] is True


# ══════════════════════════ mode gate ══════════════════════════════════════════

def test_mode_switch_gate_two_shadow_cycles_or_override():
    with pytest.raises(PermissionError):
        run(ST.set_mode(ORG, "live", actor_id="u-ceo", actor_email="ceo@x.ca"))
    with pytest.raises(PermissionError):
        run(ST.set_mode(ORG, "live", actor_id="u-ceo", actor_email="ceo@x.ca", reason="ok"))      # too short to count as a reason
    assert run(ST.agentic_state(ORG))["mode"] == "shadow"
    go_live()
    st = run(ST.agentic_state(ORG))
    assert st["mode"] == "live" and st["live_override"]["by"] == "ceo@x.ca" and st["can_go_live"]
    run(ST.set_mode(ORG, "shadow", actor_id="u-ceo", actor_email="ceo@x.ca"))
    org_settings.invalidate()
    assert run(ST.agentic_state(ORG))["mode"] == "shadow"
    # a fresh org with two shadow cycles done needs no reason
    org_settings.invalidate()
    run(ST.save_settings(ORG, {}, actor_id=None, actor_email=None))
    run(ST.bump_shadow_cycles(ORG)); run(ST.bump_shadow_cycles(ORG))
    org_settings.invalidate()
    assert run(ST.agentic_state(ORG))["shadow_cycles_done"] == 2
    run(ST.set_mode(ORG, "live", actor_id="u-ceo", actor_email="ceo@x.ca"))
    org_settings.invalidate()
    assert run(ST.agentic_state(ORG))["mode"] == "live"


# ══════════════════════════ live: tier 1 / tier 2 / deny / expiry ══════════════

def test_tier1_one_approver_executes_with_server_identity():
    calls = calls_executor()
    go_live()
    r = run(RUNS.start_run(ORG, agent="exit", trigger="manual", requested_by_user_id="u-hr"))
    assert r["mode"] == "live"
    p = propose(run_id=r["id"])
    assert p["status"] == "proposed" and p["risk_tier"] == 1 and p["needs_second_approval"] is False
    assert calls == []
    with pytest.raises(PR.ProposalError) as e:
        run(PR.approve(ORG, p["id"], EMP))                                                   # employee may not approve
    assert e.value.status == 403
    out = run(PR.approve(ORG, p["id"], A1))
    assert out["status"] == "executed" and out["approver_user_id"] == "u-hr" and out["result"]["executed"] is True
    assert calls and calls[0][2]["sin"] == "046454286" and calls[0][2]["value"] == 0         # executor gets the RAW args
    with pytest.raises(PR.ProposalError):
        run(PR.approve(ORG, p["id"], A2))                                                    # consumed once — no replay
    ev = run(LOG.recent(ORG))
    types = [e["event_type"] for e in ev]
    assert "approval.approved" in types and "execution.done" in types and "proposal.created" in types
    appr = next(e for e in ev if e["event_type"] == "approval.approved")
    assert appr["approver_user_id"] == "u-hr" and appr["decision_latency_ms"] is not None and appr["approval_id"] == p["id"]
    fin = run(RUNS.finish_run(ORG, r["id"], summary={"headline": "1 ready"}, checks_run=14, findings=1, input_tokens=100, output_tokens=20, cost_usd=0.01))
    assert fin["status"] == "done" and fin["proposals"] == 1
    assert run(LOG.verify_chain(ORG))["ok"]


def test_tier2_needs_two_different_approvers():
    calls = calls_executor("exit.run_final_pay")
    go_live()
    p = propose(tool="exit.run_final_pay", tool_args={"case_id": "c-1", "employee_id": "e-17", "last_day": "2026-10-30", "account_in_master": True})
    assert p["risk_tier"] == 2 and p["status"] == "proposed" and p["needs_second_approval"]
    with pytest.raises(PR.ProposalError) as e:
        run(PR.approve(ORG, p["id"], A1))                                                    # hr_admin is not in tier2_roles
    assert e.value.status == 403
    first = run(PR.approve(ORG, p["id"], CEO))
    assert first["status"] == "second_approval" and first["approver_user_id"] == "u-ceo" and calls == []
    with pytest.raises(PR.ProposalError) as e:
        run(PR.approve(ORG, p["id"], CEO))                                                   # same person again
    assert e.value.status == 409 and "different" in e.value.message
    assert calls == []
    with pytest.raises(PR.ProposalError):
        run(PR.approve(ORG, p["id"], CEO, for_session=True))                                 # never for tier 2
    second = run(PR.approve(ORG, p["id"], A2))
    assert second["status"] == "executed" and second["second_approver_user_id"] == "u-pay" and len(calls) == 1
    ev = run(LOG.recent(ORG, approval_id=p["id"]))
    assert [e["event_type"] for e in reversed(ev)] == ["proposal.created", "approval.first", "approval.approved", "execution.done"]


def test_hard_deny_never_becomes_a_proposal():
    calls = calls_executor("pay_unlisted_account")
    go_live()
    p = propose(tool="pay_unlisted_account")
    assert p["status"] == "denied" and p["policy_decision"] == "deny" and "Hard deny" in p["deny_message"]
    p2 = propose(tool="off_cycle_payment", tool_args={"employee_id": "e-1", "amount": 5000, "account_in_master": False})
    assert p2["status"] == "denied" and "not in the employee master" in p2["deny_message"]
    with pytest.raises(PR.ProposalError):
        run(PR.approve(ORG, p["id"], CEO))
    assert calls == []
    assert run(LOG.recent(ORG, event_type="proposal.denied_by_policy"))


def test_deny_edit_and_expiry():
    calls = calls_executor()
    go_live()
    p = propose()
    with pytest.raises(PR.ProposalError):
        run(PR.deny(ORG, p["id"], A1, message=""))
    d = run(PR.deny(ORG, p["id"], A1, message="Leave was not approved yet"))
    assert d["status"] == "denied" and d["deny_message"].startswith("Leave")
    p2 = propose()
    e = run(PR.approve(ORG, p2["id"], A2, edited_args={"employee_id": "e-17", "component": "LETTER", "value": 1, "sin": "046454286", "amount": 1600}))
    assert e["status"] == "executed" and "value" in e["edited_input_diff"] and calls[-1][2]["value"] == 1
    p3 = propose()
    past = pm.now_utc() + timedelta(days=4)
    assert run(PR.expire_stale(ORG, now=past)) == 1
    with pytest.raises(PR.ProposalError) as ex:
        run(PR.approve(ORG, p3["id"], A1))
    assert ex.value.status == 409
    # no executor registered → approved, recorded, nothing changed
    p4 = propose(tool="hold_payroll_line")
    a = run(PR.approve(ORG, p4["id"], A1))
    assert a["status"] == "approved" and a["result"]["executed"] is False
    v = run(VAL.value_summary(ORG))
    assert v["measured"]["proposals_decided"] == 3 and v["measured"]["denied"] == 1
    assert v["measured"]["accept_without_edit_pct"] == 50.0                                  # one edited, one clean


def test_tier0_auto_logged_and_session_grant():
    calls = calls_executor("draft_reply")
    calls1 = calls_executor("close_ticket")
    go_live()
    p0 = propose(tool="draft_reply", agent="hr_desk", aggregate_impact={})
    assert p0["status"] == "executed" and p0["risk_tier"] == 0 and p0["approver_user_id"] is None and len(calls) == 1
    p1 = propose(tool="close_ticket", agent="hr_desk", aggregate_impact={})
    g = run(PR.approve(ORG, p1["id"], A1, for_session=True))
    assert g["status"] == "executed" and g["session_grant"]["active"] and len(calls1) == 1
    p2 = propose(tool="close_ticket", agent="hr_desk", aggregate_impact={})
    assert p2["status"] == "executed" and p2["approver_user_id"] == "u-hr" and p2["approved_for_session"] and len(calls1) == 2
    p3 = propose(tool="close_ticket", agent="hr_desk", untrusted_input=True, aggregate_impact={})       # +1 tier → grant does not cover it
    assert p3["status"] == "proposed" and p3["risk_tier"] == 2
    run(PR.revoke_grant(ORG, g["session_grant"]["id"], A1))
    p4 = propose(tool="close_ticket", agent="hr_desk", aggregate_impact={})
    assert p4["status"] == "proposed"


# ══════════════════════════ hash chain ═════════════════════════════════════════

def test_hash_chain_verify_detects_tampering(db):
    for i in range(5):
        run(LOG.append_event(ORG, event_type="run.started", agent_name="exit", tool_name="t", tool_input={"i": i, "sin": "046454286"}, risk_tier=0))
    v = run(LOG.verify_chain(ORG))
    assert v["ok"] and v["checked"] == 5
    rows = run(LOG.recent(ORG))
    assert all("046454286" not in str(r["tool_input"]) for r in rows)

    async def tamper(sql, **p):
        async with pm.session() as s:
            await s.execute(text(sql), p)
            await s.commit()
    run(tamper("UPDATE ai_agent_action_log SET risk_tier = 2 WHERE seq = 3 AND org_id = :o", o=ORG))        # SQLite has no trigger
    v = run(LOG.verify_chain(ORG))
    assert not v["ok"] and v["first_bad_seq"] == 3 and "altered" in v["reason"]
    run(tamper("UPDATE ai_agent_action_log SET risk_tier = 0 WHERE seq = 3 AND org_id = :o", o=ORG))
    assert run(LOG.verify_chain(ORG))["ok"]
    run(tamper("DELETE FROM ai_agent_action_log WHERE seq = 2 AND org_id = :o", o=ORG))
    v = run(LOG.verify_chain(ORG))
    assert not v["ok"] and v["first_bad_seq"] == 3 and "gap" in v["reason"]
    # other org untouched
    run(LOG.append_event("22222222-2222-2222-2222-222222222222", event_type="x"))
    assert run(LOG.verify_chain("22222222-2222-2222-2222-222222222222"))["ok"]


def test_append_only_ddl_is_a_trigger_not_a_revoke():
    from agentic_models import APPEND_ONLY_DDL
    joined = "\n".join(APPEND_ONLY_DDL)
    assert "BEFORE UPDATE OR DELETE" in joined and "TRUNCATE" in joined and "RAISE EXCEPTION" in joined
    assert "GRANT" not in joined and "REVOKE" not in joined


# ══════════════════════════ tasks ══════════════════════════════════════════════

def test_tasks_dedupe_chase_escalate_complete():
    run(ST.save_settings(ORG, {"thresholds": {"escalate_after_chases": 2, "chase_every_hours": 24}}, actor_id="u-ceo", actor_email="ceo@x.ca"))
    org_settings.invalidate()
    t = run(TK.open_task(ORG, agent="exit", kind="bank_missing", title="Add bank details for MAP017", detail={"what": "No transit number", "how_to_fix": "Employee → Pay"},
                         owner_user_id="u-hr", employee_id="e-17", due_at=pm.now_utc() + timedelta(days=2), dedupe_key="bank_missing:e-17"))
    assert t["status"] == "open" and t["chase_count"] == 0
    again = run(TK.open_task(ORG, agent="exit", kind="bank_missing", title="dup", owner_user_id="u-hr", dedupe_key="bank_missing:e-17"))
    assert again["id"] == t["id"] and again["deduplicated"]
    assert run(TK.chase_due_tasks(ORG)) == {"chased": 0, "escalated": 0, "due": 0}            # not due yet
    r1 = run(TK.chase_due_tasks(ORG, now=pm.now_utc() + timedelta(hours=25)))
    assert r1["chased"] == 1 and r1["escalated"] == 0
    r2 = run(TK.chase_due_tasks(ORG, now=pm.now_utc() + timedelta(hours=50)))
    assert r2["chased"] == 1 and r2["escalated"] == 1
    rows = run(TK.list_tasks(ORG))
    assert rows[0]["status"] == "escalated" and rows[0]["chase_count"] == 2 and rows[0]["escalation_to"]["roles"]
    done = run(TK.complete_task(ORG, t["id"], actor_user_id="u-hr", result={"fixed": True}))
    assert done["status"] == "done"
    with pytest.raises(TK.TaskError):
        run(TK.complete_task(ORG, t["id"], actor_user_id="u-hr"))
    types = [e["event_type"] for e in run(LOG.recent(ORG))]
    for want in ("task.opened", "task.chased", "task.escalated", "task.done"):
        assert want in types
    assert run(SCH.tick_org(ORG))["mode"] == "shadow"


# ══════════════════════════ value ledger ═══════════════════════════════════════

def test_value_row_strings_fit_their_columns(db):
    # e2e 2026-10-25: an itemised basis > 300 chars was refused by Postgres (varchar(300)) and failed the evidence step
    e = run(VAL.record_outcome(ORG, agent="onboarding", kind="filing_prepared", dollars_avoided=10214.0, basis="x" * 900, note="n" * 900))
    assert len(e["basis"]) == 300 and e["basis"].endswith("…") and len(e["note"]) == 400


def test_value_three_layers_and_cost():
    run(VAL.record_outcome(ORG, agent="hr_desk", kind="query_resolved", units=4))
    run(VAL.record_outcome(ORG, agent="onboarding", kind="filing_prepared", units=1))
    e = run(VAL.record_outcome(ORG, agent="exit", kind="exception_confirmed", dollars_avoided=3200, basis="2 unpaid days wrongly deducted × $160"))
    run(VAL.record_outcome(ORG, agent="onboarding", kind="interest_avoided", dollars_avoided=9000, basis="late-remittance penalty avoided", confirmed_by_user_id="u-pay"))
    v = run(VAL.value_summary(ORG))
    assert v["proxy"]["minutes"] == 4 * 10 + 60 + 15 and v["proxy"]["hours"] == round((40 + 60 + 15) / 60, 2)
    assert v["avoided"]["dollars"] == 9000 and v["avoided"]["unconfirmed_dollars"] == 3200       # unconfirmed is NOT value
    run(VAL.confirm_outcome(ORG, e["id"], actor_user_id="u-hr"))
    v = run(VAL.value_summary(ORG))
    assert v["avoided"]["dollars"] == 12200 and v["avoided"]["items"] == 2
    assert v["cost"]["events"] >= 5 and v["cost"]["reviewer_minutes"] == 0
    run(ST.save_settings(ORG, {"minutes_table": {"query_resolved": 20}}, actor_id="u-ceo", actor_email="ceo@x.ca"))
    org_settings.invalidate()
    assert run(VAL.value_summary(ORG))["proxy"]["minutes"] == 4 * 20 + 60 + 15                  # editable table re-prices history
    with pytest.raises(ValueError):
        run(ST.save_settings(ORG, {"minutes_table": {"query_resolved": 9999}}, actor_id="u-ceo", actor_email="ceo@x.ca"))


def test_registry_and_shadow_report():
    reg = REG.registry_dict()
    assert [a["role_name"] for a in reg] == ["Payroll Auditor", "HR Desk", "Onboarding Agent", "Exit Agent", "Attendance Agent"]
    assert all("AI" not in a["role_name"] and "Copilot" not in a["role_name"] for a in reg)
    r = run(RUNS.start_run(ORG, agent="exit", trigger="manual"))
    propose(run_id=r["id"])
    run(TK.open_task(ORG, agent="exit", kind="missing_punch", title="3 punches missing", owner_user_id="u-hr", created_by_run=r["id"]))
    run(RUNS.finish_run(ORG, r["id"], summary={"headline": "411 ready, 1 would need a look"}, checks_run=14, findings=1))
    rep = run(RUNS.shadow_report(ORG, r["id"]))
    assert rep["run"]["status"] == "done" and rep["run"]["proposals"] == 1 and rep["run"]["tasks_opened"] == 1
    assert rep["counts"]["shadow"] == 1 and rep["counts"]["dollars"] == 3200 and rep["would_have_done"][0]["status"] == "shadow"
