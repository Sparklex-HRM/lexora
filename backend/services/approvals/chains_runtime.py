"""Approval chains — the parts that touch the database (approvals service only).

* runs beside pending items (for the inbox rows: current step, SLA, who is still waiting)
* moving a run forward when someone approves a step (re-targets the inbox item)
* escalation sweep (tick) and the auto-approval sweep (backstop for the client's task)
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from shared.utils import approval_chains as chains
from shared.utils import approvals_client as ac
from shared.utils import directory
from shared.utils import platform_models as pm
from shared.utils.approval_chains import ApprovalChain, ApprovalRun
from shared.utils.platform_models import ApprovalDecision, ApprovalItem

log = logging.getLogger("lexora.approvals.chains")


async def runs_for(db, org: str, item_ids: list[str]) -> dict[str, ApprovalRun]:
    """{item_id: its latest run} for the given items."""
    if not item_ids:
        return {}
    rows = (await db.execute(select(ApprovalRun).where(
        ApprovalRun.org_id == org, ApprovalRun.item_id.in_(item_ids)).order_by(ApprovalRun.cycle))).scalars().all()
    return {r.item_id: r for r in rows}       # a later cycle overwrites an earlier one


def run_summary(run: ApprovalRun | None, now: datetime | None = None) -> dict:
    """What the inbox row shows about the chain."""
    if not run or not run.steps:
        return {"step_label": "", "step_no": 0, "steps_total": 0, "waiting_for": [], "sla_due_at": None,
                "overdue": False, "chain_name": run.chain_name if run else "", "escalated": False}
    now = now or pm.now_utc()
    cur = min(run.current, len(run.steps) - 1)
    step = run.steps[cur]
    due = chains.sla_due(step, pm.aware(run.step_started_at))
    return {"step_label": chains.step_label(run.steps, run.current), "step_no": cur + 1, "steps_total": len(run.steps),
            "waiting_for": chains.waiting_labels(step), "sla_due_at": pm.iso(due), "overdue": bool(due and now > due),
            "chain_name": run.chain_name or "Default", "escalated": bool(step.get("escalated")),
            "mode": step.get("mode") or "any"}


def run_detail(run: ApprovalRun | None) -> dict | None:
    if not run:
        return None
    return {"id": run.id, "chain_name": run.chain_name, "chain_version": run.chain_version, "status": run.status,
            "current": run.current, "context": run.context or {}, "history": run.history or [],
            "started_at": pm.iso(run.started_at), "step_started_at": pm.iso(run.step_started_at),
            "steps": [{"name": s.get("name"), "mode": s.get("mode"), "pending": chains.waiting_labels(s),
                       "approvers": [a.get("label") for a in s.get("approvers") or []],
                       "decided_by": s.get("decided_by") or [], "escalated": bool(s.get("escalated")),
                       "done": i < run.current or (i == run.current and run.status in ("approved", "closed")),
                       "current": i == run.current and run.status in ("pending", "auto")}
                      for i, s in enumerate(run.steps or [])]}


async def advance(db, org: str, item: ApprovalItem, run: ApprovalRun, actor: dict, principal: dict | None,
                  comment: str, employees: dict, users_by_id: dict, now: datetime | None = None) -> dict:
    """Record one step approval. Returns {"final": bool, "step": n, "of": N}.
    When ``final`` is False the item was re-targeted to the next step's approvers (or the
    remaining parallel approvers) and the caller must NOT call the owner."""
    now = now or pm.now_utc()
    who = principal or actor
    step = run.steps[min(run.current, len(run.steps) - 1)]
    steps = [dict(s) for s in run.steps]                      # JSON column: assign a new list so SQLAlchemy sees it
    step = dict(step)
    done = chains.apply_approval(step, {"user_id": who["user_id"], "role": who.get("role"), "employee_id": who.get("employee_id")},
                                 employees, by_name=directory.display_name(users_by_id.get(actor["user_id"])) or actor.get("email") or "")
    steps[min(run.current, len(run.steps) - 1)] = step
    hist = list(run.history or [])
    hist.append({"at": now.isoformat(), "event": "approved_step", "step": run.current + 1,
                 "by": directory.display_name(users_by_id.get(actor["user_id"])) or actor.get("email") or "",
                 "on_behalf_of": directory.display_name(users_by_id.get(principal["user_id"])) if principal else "",
                 "comment": comment or ""})
    last = run.current >= len(run.steps) - 1
    if done and last:
        run.steps, run.history = steps, hist
        await db.commit()
        return {"final": True, "step": run.current + 1, "of": len(run.steps)}
    # a decision row for the step (append-only, like every other decision)
    db.add(ApprovalDecision(org_id=org, item_id=item.id, cycle=item.cycle, decision="approved", comment=comment or None,
                            actor_user_id=actor["user_id"],
                            actor_name=directory.display_name(users_by_id.get(actor["user_id"])) or actor.get("email") or None,
                            on_behalf_of_user_id=principal["user_id"] if principal else None,
                            on_behalf_of_name=directory.display_name(users_by_id.get(principal["user_id"])) if principal else None,
                            via="step", at=now))
    if done:
        run.current += 1
        run.step_started_at = now
        hist.append({"at": now.isoformat(), "event": "step_started", "step": run.current + 1,
                     "name": steps[run.current].get("name")})
    run.steps, run.history = steps, hist
    t = chains.targets(steps[min(run.current, len(steps) - 1)])
    item.approver_roles, item.approver_user_ids = t["approver_roles"], t["approver_user_ids"]
    item.approver_manager_of_employee_id = t["approver_manager_of_employee_id"]
    item.reminders_sent, item.last_reminder_at = 0, None
    await db.commit()
    if done:
        await ac._tell_approvers(org, ac.item_dict(item))
    return {"final": False, "step": run.current + 1, "of": len(run.steps)}


async def escalate_due(org: str, now: datetime | None = None) -> int:
    """Tick: every pending run whose current step waited longer than its escalation hours gets
    the escalation target added (and told). Returns how many were escalated."""
    now = now or pm.now_utc()
    n = 0
    async with pm.session() as s:
        runs = (await s.execute(select(ApprovalRun).where(ApprovalRun.org_id == org, ApprovalRun.status == "pending"))).scalars().all()
        if not runs:
            return 0
        employees = users = None
        for run in runs:
            if not run.steps:
                continue
            cur = min(run.current, len(run.steps) - 1)
            step = dict(run.steps[cur])
            if not chains.escalation_due(step, pm.aware(run.step_started_at), now):
                continue
            item = (await s.execute(select(ApprovalItem).where(ApprovalItem.id == run.item_id, ApprovalItem.org_id == org))).scalar_one_or_none()
            if not item or item.status != "pending":
                continue
            if employees is None:
                employees = {e["id"]: e for e in await directory.org_employees(org)}
                users = {u["id"]: u for u in await directory.org_users(org)}
            caller = {"approver_manager_of_employee_id": item.approver_manager_of_employee_id}
            added = chains.escalate(step, run.context or {}, caller=caller, employees=employees, users=users)
            steps = [dict(x) for x in run.steps]
            steps[cur] = step
            hist = list(run.history or [])
            hist.append({"at": now.isoformat(), "event": "escalated", "step": cur + 1,
                         "to": [a.get("label") for a in added]})
            run.steps, run.history, run.escalated_at = steps, hist, now
            t = chains.targets(step)
            item.approver_roles, item.approver_user_ids = t["approver_roles"], t["approver_user_ids"]
            item.approver_manager_of_employee_id = t["approver_manager_of_employee_id"]
            await s.commit()
            await ac._tell_approvers(org, ac.item_dict(item), reminder=True)
            n += 1
    return n


async def run_auto_due(org: str) -> int:
    """Backstop for the client's in-process task: finish any run still marked 'auto'."""
    n = 0
    async with pm.session() as s:
        runs = (await s.execute(select(ApprovalRun).where(ApprovalRun.org_id == org, ApprovalRun.status == "auto"))).scalars().all()
        items = {i.id: i for i in (await s.execute(select(ApprovalItem).where(
            ApprovalItem.id.in_([r.item_id for r in runs]) if runs else ApprovalItem.id.is_(None)))).scalars().all()}
    for run in runs:
        item = items.get(run.item_id)
        if not item or item.status != "pending":
            continue
        if await ac.execute_auto_approval(org, ac.item_dict(item)):
            n += 1
    return n


async def seed_defaults(db, org: str) -> int:
    """Make sure every registered request type has a (default) chain row the screen can edit."""
    have = {r[0] for r in (await db.execute(select(ApprovalChain.request_type).where(ApprovalChain.org_id == org))).all()}
    n = 0
    for key, meta in chains.REQUEST_TYPES.items():
        if key in have:
            continue
        db.add(ApprovalChain(org_id=org, request_type=key, name=f"{meta['label']} — default",
                             description="One step: whoever the owning screen names. Edit to add steps, conditions, escalation.",
                             definition=chains.default_definition(), version=1, is_default=True, active=True, updated_by="system"))
        n += 1
    if n:
        await db.commit()
    return n
