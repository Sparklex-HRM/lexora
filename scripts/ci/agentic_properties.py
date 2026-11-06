"""Agentic property checks (CI gate, 2026-10-25, report §Trust "property checks as CI").

Deterministic invariants over the agentic tables — grades SYSTEM STATE, not transcripts:

  P1  no proposal is `executed` without an `approval.approved` log event naming it (tier 0 = decision
      auto_tier0, still an event) — and tier-2 executions carry two DIFFERENT approver ids;
  P2  no `shadow` proposal has an execution event, and no proposal of a shadow run executed;
  P3  the action log is intact: per-org hash chain verifies, sequences have no gaps (no row updated or
      removed) — and on Postgres the append-only trigger is installed;
  P4  no hard-deny tool ever reached a status other than `denied`;
  P5  every executed proposal's approver role was allowed for its tier at decision time is NOT checked
      here (settings may change later) — the approval event records the roles instead.
  P6  (2026-10-26, lifecycle agents) no work item is ever opened by a SHADOW run (shadow opens nothing, sends
      nothing); the Exit Agent has no tool that computes or pays money (final pay = the payroll engine; run_final_pay
      = tier 2); the Attendance Agent's only tool is the tier-0 chase; Onboarding's letter tool drafts only (tier 1).

Runs against DATABASE_URL / CHECK_DB_URL when set (the local stack), else against a fresh SQLite scenario
that exercises the rails end to end (so the gate is green on any machine and red on a regression).

    python scripts/ci/agentic_properties.py            # exit 0 = all invariants hold
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "services", "ai_platform"))
os.environ.setdefault("LEXORA_ENV", "test")
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "ANTHROPIC_API_KEY"):
    os.environ[k] = ""

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from shared.utils import platform_models as pm  # noqa: E402

import agentic_log as LOG  # noqa: E402
from agentic_models import AgentActionLog, AgentProposal, AgentRun, AgentTask  # noqa: E402
from agentic_registry import HARD_DENY  # noqa: E402

FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("  FAIL", msg)


async def check_db(is_pg: bool) -> None:
    async with pm.session() as s:
        props = (await s.execute(select(AgentProposal))).scalars().all()
        runs = {r.id: r for r in (await s.execute(select(AgentRun))).scalars().all()}
        events = (await s.execute(select(AgentActionLog))).scalars().all()
        tasks = (await s.execute(select(AgentTask))).scalars().all()
        orgs = sorted({p.org_id for p in props} | {e.org_id for e in events})
    by_pid: dict[str, list[AgentActionLog]] = {}
    for e in events:
        if e.approval_id:
            by_pid.setdefault(e.approval_id, []).append(e)
    # P1
    for p in props:
        evs = by_pid.get(p.id, [])
        if p.status == "executed":
            appr = [e for e in evs if e.event_type == "approval.approved"]
            if not appr:
                fail(f"P1 proposal {p.id} ({p.tool_name}) is executed without an approval.approved event")
            if p.risk_tier == 2 and (not p.approver_user_id or not p.second_approver_user_id or p.approver_user_id == p.second_approver_user_id):
                fail(f"P1 tier-2 proposal {p.id} executed without two different approvers")
            if p.risk_tier >= 1 and not p.approver_user_id:
                fail(f"P1 tier-{p.risk_tier} proposal {p.id} executed with no approver id")
        # P2
        if p.status == "shadow" or p.mode == "shadow" or (p.run_id and p.run_id in runs and runs[p.run_id].mode == "shadow"):
            if p.status in ("executed", "approved", "failed") or any(e.event_type.startswith("execution.") for e in evs):
                fail(f"P2 shadow proposal {p.id} ({p.tool_name}) shows execution")
        # P4
        if p.tool_name in HARD_DENY and p.status != "denied":
            fail(f"P4 hard-deny tool {p.tool_name} reached status {p.status} ({p.id})")
    # P6 — shadow opens nothing
    for t in tasks:
        if t.created_by_run and t.created_by_run in runs and runs[t.created_by_run].mode == "shadow":
            fail(f"P6 task {t.id} ({t.agent}/{t.kind}) was opened by shadow run {t.created_by_run}")
    # P3
    for org in orgs:
        v = await LOG.verify_chain(org)
        if not v["ok"]:
            fail(f"P3 action log chain broken for org {org}: seq {v['first_bad_seq']} — {v['reason']}")
    if is_pg:
        async with pm.session() as s:
            n = (await s.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('ai_agent_action_log_no_update','ai_agent_action_log_no_truncate') AND NOT tgisinternal"))).scalar()
        if int(n or 0) < 2:
            fail("P3 append-only triggers on ai_agent_action_log are not installed (run migrations: 0015_agentic)")
        else:
            try:
                async with pm.session() as s:
                    await s.execute(text("UPDATE ai_agent_action_log SET step_no = step_no WHERE false"))
                    await s.commit()
            except Exception:                                # noqa: BLE001  (WHERE false touches no row; a refusal here would be fine too)
                pass
    print(f"  checked {len(props)} proposal(s), {len(events)} log event(s), {len(orgs)} org(s)")


def check_registry() -> None:
    """P6 static half: the lifecycle agents' tool sets."""
    from agentic_registry import REGISTRY
    ex = REGISTRY["exit"].tier_map
    if ex.get("exit.run_final_pay") != 2:
        fail("P6 exit.run_final_pay must be tier 2 (two different approvers)")
    for t in ex:
        if any(w in t for w in ("pay_", "release", "bank", "compute_fnf", "calculate")):
            fail(f"P6 the Exit Agent must not have a money tool: {t}")
    if REGISTRY["attendance"].tier_map != {"attendance.chase": 0}:
        fail(f"P6 the Attendance Agent may only chase: {REGISTRY['attendance'].tier_map}")
    ob = REGISTRY["onboarding"].tier_map
    if ob.get("onboarding.draft_appointment_letter") != 1 or any("issue" in t for t in ob):
        fail(f"P6 the Onboarding Agent drafts letters at tier 1 and never issues: {ob}")


async def lifecycle_scenario(org: str) -> None:
    """Shadow then live runs of the three lifecycle agents on fixtures (no network)."""
    from datetime import date
    from shared.utils import org_settings
    import agentic_proposals as PR
    import agentic_state as ST
    from agents.onboarding import run as OB
    from agents.exit import run as EXR
    from agents.attendance import run as AT
    lg = {"by_employee": {"e1": "u-e1", "m1": "u-mgr"}, "hr": ["u-hr"], "payroll": ["u-pay"], "org_admin": ["u-ceo"]}
    joiner = {"case_key": "emp:e1", "kind": "employee", "status": "active", "employee": {"id": "e1", "name": "J", "manager_id": "m1"},
              "fields": {"sin": False, "sin_valid": None, "bank_account": True, "bank_institution": "004", "bank_transit": "12345", "address": True,
                         "td1_federal": True, "td1_provincial": False, "temporary_resident": False, "work_permit_expiry": None, "province": "ON",
                         "hire_date": date(2026, 10, 20), "annual_salary": 72000.0},
              "docs": {"types": ["education", "void_cheque"], "unverified": []}, "consents": [], "tasks": [], "letter": None, "assets": 1, "lms": 1}
    ex = {"id": "c1", "case_number": "EX-1", "employee_id": "e1", "country": "CA", "exit_type": "resignation", "status": "clearance",
          "submitted_on": date(2026, 10, 1), "notice_days": 14, "notice_source": "policy", "notice_served_days": 14, "notice_shortfall_days": 0,
          "notice_treatment": "none", "requested_lwd": "2026-10-30", "expected_lwd": date(2026, 10, 30), "lwd": date(2026, 10, 30),
          "province": "ON", "pay_period_end": date(2026, 10, 31), "next_pay_day": date(2026, 11, 6), "roe_reason_code": "E",
          "settlement_status": "none", "employee": {"id": "e1", "name": "J", "manager_id": "m1"},
          "clearance": [{"id": "c-it", "department": "IT", "item": "Laptop returned", "owner_role": "hr_admin", "owner_user_id": None, "status": "cleared"}],
          "letter_rows": [], "case_letters": []}
    att = {"today": date(2026, 10, 26), "window": {"start": date(2026, 10, 19), "end": date(2026, 10, 25)},
           "pay": {"period": "2026-10", "period_start": date(2026, 10, 1), "period_end": date(2026, 10, 31), "pay_date": date(2026, 10, 31), "cutoff": date(2026, 10, 26)},
           "employees": [{"id": "e1", "name": "J", "manager_id": "m1"}], "days": [{"employee_id": "e1", "work_date": date(2026, 10, 22), "first_in": "09:00", "last_out": None, "status": "present"}],
           "regularisations": [], "leave": [], "ot_breaches": [], "night_blocked": []}
    await ST.set_mode(org, "shadow", actor_id="u1", actor_email="ceo@x")
    org_settings.invalidate()
    for _ in range(2):
        await OB.run_onboarding(org, trigger="manual", cases=[joiner], today=date(2026, 10, 26), logins=lg)
        await EXR.run_exit(org, trigger="manual", cases=[ex], today=date(2026, 10, 30), logins=lg)
        await AT.run_attendance(org, trigger="daily", inputs=att, logins=lg)
        await ST.set_mode(org, "live", actor_id="u1", actor_email="ceo@x", reason="property-check scenario override")
        org_settings.invalidate()
    for p in await PR.list_proposals(org, agent="exit", status="inbox"):
        if p["tool_name"] == "exit.run_final_pay":
            await PR.approve(org, p["id"], PR.Approver("u-ceo", "ceo@x", "org_admin"))
            await PR.approve(org, p["id"], PR.Approver("u-pay", "pay@x", "payroll_admin"))


async def scenario() -> None:
    """Fresh SQLite: exercise the rails, then run the same checks on the result."""
    from shared.utils import org_settings
    import agentic_proposals as PR
    import agentic_registry as REG
    import agentic_runs as RUNS
    import agentic_state as ST
    import agents.lifecycle_common  # noqa: F401  (registers ai_lifecycle_items before create_all)
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with eng.begin() as c:
        await c.run_sync(lambda cc: pm.PlatformBase.metadata.create_all(cc))
    pm.set_sessionmaker(async_sessionmaker(eng, expire_on_commit=False))
    org_settings.invalidate()

    async def addons(org):
        return ["agentic"]
    ST.org_addons = addons

    @REG.register_executor("adjust_payroll_input")
    async def _x(org, proposal, args):
        return {"status": "done"}
    REG.EXECUTORS["change_bank_details"] = _x
    org = "33333333-3333-3333-3333-333333333333"
    r = await RUNS.start_run(org, agent="payroll_auditor", trigger="manual")
    await PR.propose(org, agent="payroll_auditor", tool_name="adjust_payroll_input", run_id=r["id"], tool_args={"v": 1}, plain_action="shadow one")
    await RUNS.finish_run(org, r["id"], summary={"headline": "shadow"})
    await ST.set_mode(org, "live", actor_id="u1", actor_email="ceo@x", reason="property-check scenario override")
    org_settings.invalidate()
    p1 = await PR.propose(org, agent="payroll_auditor", tool_name="adjust_payroll_input", tool_args={"v": 2}, plain_action="tier 1")
    await PR.approve(org, p1["id"], PR.Approver("u-hr", "hr@x", "hr_admin"))
    p2 = await PR.propose(org, agent="payroll_auditor", tool_name="change_bank_details", tool_args={"v": 3, "account_in_master": True}, plain_action="tier 2")
    await PR.approve(org, p2["id"], PR.Approver("u-ceo", "ceo@x", "org_admin"))
    await PR.approve(org, p2["id"], PR.Approver("u-pay", "pay@x", "payroll_admin"))
    await PR.propose(org, agent="payroll_auditor", tool_name="pay_unlisted_account", tool_args={}, plain_action="deny")
    await lifecycle_scenario("44444444-4444-4444-4444-444444444444")
    await check_db(is_pg=False)
    # a tampered chain must be caught
    async with pm.session() as s:
        await s.execute(text("UPDATE ai_agent_action_log SET risk_tier = 9 WHERE seq = 2"))
        await s.commit()
    v = await LOG.verify_chain(org)
    if v["ok"]:
        fail("P3 self-test: tampering was NOT detected")
    else:
        print("  self-test: tampering detected at seq", v["first_bad_seq"])


async def main() -> int:
    check_registry()
    url = os.getenv("CHECK_DB_URL") or os.getenv("AGENTIC_DB_URL") or ""
    if url:
        print(f"agentic properties against {url.split('@')[-1]}")
        eng = create_async_engine(url, pool_pre_ping=True)
        pm.set_sessionmaker(async_sessionmaker(eng, expire_on_commit=False))
        try:
            await check_db(is_pg=eng.dialect.name == "postgresql")
        finally:
            await eng.dispose()
    else:
        print("agentic properties: no CHECK_DB_URL — running the SQLite scenario")
        await scenario()
    print(f"{len(FAILS)} invariant(s) violated")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
