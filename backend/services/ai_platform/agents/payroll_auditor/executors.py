"""Payroll Auditor executors — ONE deterministic executor per tool name, reached only through the foundation's
approval rails (``agentic_proposals._execute``): tier 0 at once (logged), tier 1 one approver, tier 2 two DIFFERENT
approvers; in shadow mode none of these ever runs.

The agent never changes pay: every executor only opens a work item for a person or records a decision on the
review pack. The payroll engine and the people on the Payroll screens commit runs, lock timesheets and pay.

  payroll.chase_input              tier 0  open the work item for the person who can fix the gap (in-app + e-mail nudge)
  payroll.mark_variance_explained  tier 0  record that N variances were explained (value ledger + log)
  payroll.lock_inputs              tier 1  record the input freeze on the pack and ask HR to lock the approved timesheets
                                           (Time → Timesheets) — the agent does not lock them itself
  payroll.send_for_approval        tier 2  record that two different approvers accepted the draft and ask the payroll admin to
                                           commit the run in Payroll (the engine's own gates still apply there)
  payroll.ready_to_release         tier 2  record "ready to release" — pay stubs and deposits stay in Payroll
"""
from __future__ import annotations

import logging
import sys
from datetime import timedelta

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_tasks as TK  # noqa: E402
import agentic_value as VAL  # noqa: E402
from agentic_registry import register_executor  # noqa: E402

from . import pack as PK  # noqa: E402

log = logging.getLogger("lexora.agentic.payroll")
AGENT = "payroll_auditor"
TOOLS = {"payroll.chase_input": 0, "payroll.mark_variance_explained": 0, "payroll.lock_inputs": 1, "payroll.send_for_approval": 2, "payroll.ready_to_release": 2}
LINK = "/ai/agents/payroll-auditor"


async def _patch_run_summary(org: str, run_id: str | None, patch: dict) -> None:
    if not run_id:
        return
    from sqlalchemy import select
    from agentic_models import AgentRun
    async with pm.session() as s:
        r = (await s.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == str(org)))).scalar_one_or_none()
        if r:
            r.summary = {**(r.summary or {}), **patch}
            await s.commit()


async def _office_task(org: str, proposal: dict, *, kind: str, title: str, detail: dict, owner: str | None, period: str | None, hours: float = 24) -> dict:
    if not owner:                                                  # no office login to ask: the decision is still recorded
        return {"id": None, "status": "no_owner"}
    return await TK.open_task(org, agent=AGENT, kind=kind, title=title[:300], detail=detail, owner_user_id=owner,
                              due_at=pm.now_utc() + timedelta(hours=hours), created_by_run=proposal.get("run_id"),
                              dedupe_key=f"{kind}:{period or proposal.get('run_id')}"[:160])


@register_executor("payroll.chase_input")
async def chase_input(org: str, proposal: dict, args: dict) -> dict:
    due_h = float(args.get("due_hours") or 48)
    t = await TK.open_task(org, agent=AGENT, kind=str(args.get("kind") or "payroll_input")[:60], title=str(args.get("title") or "Payroll input needed")[:300],
                           detail=dict(args.get("detail") or {}), owner_user_id=args.get("owner_user_id"), owner_employee_id=args.get("owner_employee_id"),
                           due_at=pm.now_utc() + timedelta(hours=due_h), employee_id=args.get("employee_id"), run_ref=args.get("run_ref"),
                           created_by_run=proposal.get("run_id"), dedupe_key=args.get("dedupe_key"), first_chase_in_hours=args.get("first_chase_in_hours"))
    if args.get("finding_id"):
        await PK.set_task_ids(org, {str(args["finding_id"]): t["id"]})
    return {"task_id": t["id"], "deduplicated": bool(t.get("deduplicated")), "status": t["status"]}


@register_executor("payroll.mark_variance_explained")
async def mark_variance_explained(org: str, proposal: dict, args: dict) -> dict:
    ids = [str(x) for x in (args.get("finding_ids") or [])]
    n = await PK.mark_explained(org, ids, args.get("explanations") or None, by=str(args.get("by") or "template")) if ids else 0
    if n:
        await VAL.record_outcome(org, agent=AGENT, kind="variance_explained", units=n, run_id=proposal.get("run_id"), proposal_id=proposal.get("id"),
                                 note=f"{n} variance(s) linked to an event ({args.get('by') or 'template'})")
    return {"explained": n, "status": "done"}


@register_executor("payroll.lock_inputs")
async def lock_inputs(org: str, proposal: dict, args: dict) -> dict:
    now = pm.now_utc()
    period = args.get("period")
    t = await _office_task(org, proposal, kind="lock_timesheets", owner=args.get("hr_user_id"), period=period,
                           title=f"Lock the approved timesheets for {period} (Time → Timesheets)",
                           detail={"what": "Every timesheet of the period is approved and no blocking finding is open; the inputs were frozen by an approver.",
                                   "how_to_fix": "Open Time → Timesheets, filter the period and lock them so late edits cannot change the pay.",
                                   "link": "/time?tab=timesheets", "period": period})
    await _patch_run_summary(org, proposal.get("run_id"), {"inputs_locked_at": pm.iso(now), "inputs_locked_by": proposal.get("approver_user_id"), "lock_task_id": t["id"]})
    await LOG.append_event(org, event_type="inputs.frozen", agent_name=AGENT, run_id=proposal.get("run_id"), approval_id=proposal.get("id"), tool_name="payroll.lock_inputs",
                           approver_user_id=proposal.get("approver_user_id"), tool_input={"period": period, "task_id": t["id"]}, risk_tier=1, policy_decision="allow")
    return {"frozen_at": pm.iso(now), "task_id": t["id"], "status": "recorded"}


@register_executor("payroll.send_for_approval")
async def send_for_approval(org: str, proposal: dict, args: dict) -> dict:
    """Two different approvers accepted the draft. The agent records it and asks the payroll admin to commit the run in
    Payroll — the agent never calls the run endpoint, so it never creates or changes pay."""
    now = pm.now_utc()
    period = args.get("period")
    t = await _office_task(org, proposal, kind="commit_run", owner=args.get("payroll_user_id"), period=period,
                           title=f"Commit the {period} pay run in Payroll — the review pack was approved by two people",
                           detail={"what": f"Draft for {period}: {args.get('employees')} employees, net {args.get('net_label')}.",
                                   "how_to_fix": "Open Payroll → Run payroll, choose the same pay frequency and period, and commit it (the engine's own checks apply).",
                                   "link": "/payroll", "period": period, "pack": f"{LINK}?period={period}"})
    await _patch_run_summary(org, proposal.get("run_id"), {"sent_for_approval_at": pm.iso(now), "commit_task_id": t["id"],
                                                            "approvers": [proposal.get("approver_user_id"), proposal.get("second_approver_user_id")]})
    return {"task_id": t["id"], "status": "recorded", "next": "Payroll → Run payroll (a person commits the run)"}


@register_executor("payroll.ready_to_release")
async def ready_to_release(org: str, proposal: dict, args: dict) -> dict:
    now = pm.now_utc()
    await _patch_run_summary(org, proposal.get("run_id"), {"release_ready_at": pm.iso(now), "release_ready_by": [proposal.get("approver_user_id"), proposal.get("second_approver_user_id")]})
    await LOG.append_event(org, event_type="release.ready", agent_name=AGENT, run_id=proposal.get("run_id"), approval_id=proposal.get("id"), tool_name="payroll.ready_to_release",
                           approver_user_id=proposal.get("approver_user_id"), second_approver_user_id=proposal.get("second_approver_user_id"),
                           tool_input={"engine_run_id": args.get("engine_run_id"), "period": args.get("period")}, risk_tier=2, policy_decision="allow",
                           agent_reason_text="Two approvers confirmed the pack; pay stubs and deposits are released from Payroll (the agent moves no money).")
    return {"status": "recorded", "next": "Payroll → pay stubs and deposits (release stays in the payroll flow)", "engine_run_id": args.get("engine_run_id")}
