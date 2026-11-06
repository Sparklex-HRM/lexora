"""Work items the agents open for PEOPLE, and the chasing that gets them closed.

    from agentic_tasks import open_task, chase_due_tasks, complete_task, escalate_task
    t = await open_task(org, agent="exit", kind="bank_missing", title="Add bank details for MAP017",
                        detail={"what": ..., "why": ..., "how_to_fix": ..., "link": "/employees/…"},
                        owner_user_id=uid, due_at=pm.now_utc() + timedelta(days=2), employee_id=eid, created_by_run=run_id,
                        dedupe_key="bank_missing:MAP017")
    await chase_due_tasks(org)          # scheduler, every 15 min: in-app + e-mail nudge, escalate after N chases
    await complete_task(org, t["id"], actor_user_id=uid, result={"fixed": True})

* ``dedupe_key``: the same open item is never opened twice (the existing row is returned);
* chasing uses ``shared.utils.notify`` — in-app row always, e-mail honest (``not_configured`` when SMTP is
  blank; never raises, never blocks); the title carries no amount, id or name;
* escalation after ``thresholds.escalate_after_chases`` chases → the task's ``escalation_to`` roles /
  users (default: the org's ``escalation_roles`` setting, i.e. HR + org admin);
* every open / chase / escalate / complete is one row in the action log.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.notify import notify  # noqa: E402

from agentic_log import append_event  # noqa: E402
from agentic_models import AGENTS, AgentTask, TASK_STATUSES  # noqa: E402
from agentic_registry import REGISTRY  # noqa: E402
from agentic_state import agentic_state  # noqa: E402

LINK = "/ai/agents?tab=tasks"


class TaskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def task_dict(t: AgentTask) -> dict:
    return {"id": t.id, "agent": t.agent, "agent_role": REGISTRY[t.agent].role_name if t.agent in REGISTRY else t.agent, "kind": t.kind,
            "employee_id": t.employee_id, "run_ref": t.run_ref, "filing_id": t.filing_id, "title": t.title, "detail": t.detail or {},
            "owner_user_id": t.owner_user_id, "owner_employee_id": t.owner_employee_id, "due_at": pm.iso(t.due_at), "status": t.status,
            "chase_count": int(t.chase_count or 0), "last_chased_at": pm.iso(t.last_chased_at), "next_chase_at": pm.iso(t.next_chase_at),
            "escalation_to": t.escalation_to, "escalated_at": pm.iso(t.escalated_at), "created_by_run": t.created_by_run,
            "result": t.result, "completed_by": t.completed_by, "completed_at": pm.iso(t.completed_at),
            "created_at": pm.iso(t.created_at), "overdue": bool(t.due_at and t.status in ("open", "waiting", "escalated") and pm.aware(t.due_at) < pm.now_utc())}


async def open_task(org: str, *, agent: str, kind: str, title: str, detail: dict | None = None, owner_user_id: str | None = None,
                    owner_employee_id: str | None = None, due_at: datetime | None = None, employee_id: str | None = None,
                    run_ref: str | None = None, filing_id: str | None = None, created_by_run: str | None = None,
                    escalation_to: dict | None = None, dedupe_key: str | None = None, first_chase_in_hours: float | None = None,
                    notify_now: bool = True) -> dict:
    if agent not in AGENTS:
        raise TaskError(422, f"Unknown agent {agent!r}")
    if not (owner_user_id or owner_employee_id):
        raise TaskError(422, "A task needs an owner (login or employee)")
    st = await agentic_state(org)
    now = pm.now_utc()
    async with pm.session() as s:
        if dedupe_key:
            dup = (await s.execute(select(AgentTask).where(AgentTask.org_id == str(org), AgentTask.dedupe_key == dedupe_key,
                                                           AgentTask.status.in_(("open", "waiting", "escalated"))))).scalars().first()
            if dup:
                return {**task_dict(dup), "deduplicated": True}
        hours = float(first_chase_in_hours if first_chase_in_hours is not None else (st["thresholds"].get("chase_every_hours") or 24))
        t = AgentTask(org_id=str(org), agent=agent, kind=kind[:60], title=title[:300], detail=detail or {}, owner_user_id=owner_user_id,
                      owner_employee_id=owner_employee_id, due_at=due_at, employee_id=employee_id, run_ref=run_ref, filing_id=filing_id,
                      created_by_run=created_by_run, escalation_to=escalation_to, dedupe_key=dedupe_key,
                      next_chase_at=now + timedelta(hours=hours), status="open")
        s.add(t)
        await s.commit()
        out = task_dict(t)
    await append_event(org, event_type="task.opened", agent_name=agent, run_id=created_by_run, tool_name="open_task",
                       tool_input={"kind": kind, "title": title, "owner_user_id": owner_user_id, "due_at": pm.iso(due_at)},
                       risk_tier=0, policy_decision="allow", approval_id=None, agent_reason_text=(detail or {}).get("why"),
                       aggregate_impact={"headcount": 1 if employee_id else 0})
    if notify_now and st["enabled"]:
        await notify(org, user_ids=[owner_user_id] if owner_user_id else None, employee_ids=[owner_employee_id] if owner_employee_id else None,
                     kind="agent_task", title=f"{REGISTRY[agent].role_name} needs something from you",
                     body=f"{title}\n\n{(detail or {}).get('what') or ''}\n{(detail or {}).get('how_to_fix') or ''}".strip(),
                     link=(detail or {}).get("link") or LINK)
    return out


async def _load(s, org: str, task_id: str) -> AgentTask:
    t = (await s.execute(select(AgentTask).where(AgentTask.id == task_id, AgentTask.org_id == str(org)))).scalar_one_or_none()
    if not t:
        raise TaskError(404, "That task was not found")
    return t


async def complete_task(org: str, task_id: str, *, actor_user_id: str | None, result: dict | None = None, status: str = "done") -> dict:
    if status not in ("done", "cancelled"):
        raise TaskError(422, "status must be done or cancelled")
    async with pm.session() as s:
        t = await _load(s, org, task_id)
        if t.status in ("done", "cancelled"):
            raise TaskError(409, f"That task is already {t.status}")
        t.status, t.result, t.completed_by, t.completed_at, t.updated_at = status, result or {}, actor_user_id, pm.now_utc(), pm.now_utc()
        t.next_chase_at = None
        await s.commit()
        out = task_dict(t)
    await append_event(org, event_type=f"task.{status}", agent_name=t.agent, run_id=t.created_by_run, tool_name="complete_task",
                       approval_id=None, approver_user_id=actor_user_id, decision=status, decision_ts=pm.now_utc(),
                       tool_input={"task_id": task_id, "result": result or {}}, risk_tier=0, policy_decision="allow")
    return out


async def escalate_task(org: str, task_id: str, *, actor_user_id: str | None = None, reason: str = "") -> dict:
    st = await agentic_state(org)
    async with pm.session() as s:
        t = await _load(s, org, task_id)
        if t.status in ("done", "cancelled"):
            raise TaskError(409, f"That task is already {t.status}")
        t.status, t.escalated_at, t.updated_at = "escalated", pm.now_utc(), pm.now_utc()
        to = t.escalation_to or {"roles": st.get("escalation_roles") or ["hr_admin", "org_admin"]}
        t.escalation_to = to
        await s.commit()
        out = task_dict(t)
    await notify(org, roles=to.get("roles"), user_ids=to.get("user_ids"), kind="agent_task",
                 title=f"{REGISTRY[t.agent].role_name}: an item is still open after {out['chase_count']} reminder(s)",
                 body=f"{t.title}\n{reason or ''}".strip(), link=LINK)
    await append_event(org, event_type="task.escalated", agent_name=t.agent, run_id=t.created_by_run, tool_name="escalate_task",
                       approver_user_id=actor_user_id, tool_input={"task_id": task_id, "to": to, "reason": reason, "chase_count": out["chase_count"]},
                       risk_tier=0, policy_decision="allow", agent_reason_text=reason or f"{out['chase_count']} chase(s) without a fix")
    return out


async def _whatsapp_nudge(org: str, t: AgentTask, reminder_no: int) -> dict | None:
    """WhatsApp leg of a reminder (shared/utils/whatsapp_channel). Never raises; None when the channel is off."""
    try:
        from shared.utils import whatsapp_channel as WA
        cfg = await WA.load_config(org)
        if not cfg.get("enabled"):
            return None
        eid = t.owner_employee_id
        if not eid and t.owner_user_id:
            from sqlalchemy import text as _text
            async with pm.session() as s:
                r = (await s.execute(_text("SELECT employee_id FROM users WHERE CAST(id AS text) = :u"), {"u": str(t.owner_user_id)})).first()
            eid = str(r[0]) if r and r[0] else None
        role = REGISTRY[t.agent].role_name if t.agent in REGISTRY else "Lexora HRM"
        return await WA.send_chase(org, employee_id=eid, params=[role, str(reminder_no)], reminder_no=reminder_no, cfg=cfg)
    except Exception as e:                                   # noqa: BLE001
        return {"channel": "whatsapp", "status": "skipped", "reason": f"{type(e).__name__}"}


async def chase_task(org: str, task_id: str, *, now: datetime | None = None, actor_user_id: str | None = None) -> dict:
    """One nudge to the owner. Escalates when the chase count reaches the org's limit."""
    st = await agentic_state(org)
    now = now or pm.now_utc()
    every = float(st["thresholds"].get("chase_every_hours") or 24)
    limit = int(st["thresholds"].get("escalate_after_chases") or 3)
    async with pm.session() as s:
        t = await _load(s, org, task_id)
        if t.status not in ("open", "waiting", "escalated"):
            raise TaskError(409, f"That task is {t.status}")
        t.chase_count = int(t.chase_count or 0) + 1
        t.last_chased_at, t.next_chase_at, t.updated_at = now, now + timedelta(hours=every), now
        if t.status == "open":
            t.status = "waiting"
        await s.commit()
        out = task_dict(t)
    await notify(org, user_ids=[t.owner_user_id] if t.owner_user_id else None, employee_ids=[t.owner_employee_id] if t.owner_employee_id else None,
                 kind="agent_task", title=f"Reminder {out['chase_count']}: {REGISTRY[t.agent].role_name} is waiting on you",
                 body=f"{t.title}\n{(t.detail or {}).get('how_to_fix') or ''}".strip(), link=(t.detail or {}).get("link") or LINK)
    await append_event(org, event_type="task.chased", agent_name=t.agent, run_id=t.created_by_run, tool_name="chase_task",
                       approver_user_id=actor_user_id, tool_input={"task_id": task_id, "chase_count": out["chase_count"], "owner_user_id": t.owner_user_id},
                       risk_tier=0, policy_decision="allow")
    # 2026-10-26 — channel preference in-app → e-mail → WhatsApp: WhatsApp only when the company switched it on, the
    # owner opted in to agent reminders and the reminder number reached `after_reminders` (approved template, no free text)
    out["whatsapp"] = await _whatsapp_nudge(org, t, out["chase_count"])
    return_wa = out["whatsapp"]
    if return_wa and return_wa.get("status") in ("sent", "would_send", "failed"):
        await append_event(org, event_type="task.chased.whatsapp", agent_name=t.agent, run_id=t.created_by_run, tool_name="whatsapp_chase",
                           tool_input={"task_id": task_id, "status": return_wa.get("status"), "to": return_wa.get("to"), "template": return_wa.get("template"),
                                       "transport": return_wa.get("transport"), "reason": return_wa.get("reason")}, risk_tier=0, policy_decision="allow")
    if out["chase_count"] >= limit and t.status != "escalated":
        # keep this reminder's WhatsApp leg on the answer (the escalation result used to replace it)
        out = {**await escalate_task(org, task_id, reason=f"{out['chase_count']} reminders sent, still open"), "whatsapp": return_wa}
    return out


async def chase_due_tasks(org: str, *, now: datetime | None = None, limit: int = 200) -> dict:
    """Scheduler entry: every open / waiting task whose ``next_chase_at`` has passed. Guarded by the add-on
    switch (the scheduler checks ``agentic_state`` first; this also checks, cheaply)."""
    now = now or pm.now_utc()
    async with pm.session() as s:
        ids = (await s.execute(select(AgentTask.id).where(AgentTask.org_id == str(org), AgentTask.status.in_(("open", "waiting")),
                                                          AgentTask.next_chase_at.is_not(None), AgentTask.next_chase_at <= now)
                               .order_by(AgentTask.next_chase_at).limit(limit))).scalars().all()
    chased, escalated = 0, 0
    for tid in ids:
        try:
            r = await chase_task(org, tid, now=now)
            chased += 1
            escalated += 1 if r["status"] == "escalated" else 0
        except TaskError:
            continue
    return {"chased": chased, "escalated": escalated, "due": len(ids)}


async def list_tasks(org: str, *, status: str | None = None, agent: str | None = None, owner_user_id: str | None = None, limit: int = 500) -> list[dict]:
    q = select(AgentTask).where(AgentTask.org_id == str(org))
    if status:
        q = q.where(AgentTask.status.in_([x for x in status.split(",") if x in TASK_STATUSES] or TASK_STATUSES))
    if agent:
        q = q.where(AgentTask.agent == agent)
    if owner_user_id:
        q = q.where(AgentTask.owner_user_id == owner_user_id)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(AgentTask.status, AgentTask.due_at.asc().nulls_last(), AgentTask.created_at.desc()).limit(min(limit, 2000)))).scalars().all()
    return [task_dict(t) for t in rows]


async def open_counts(org: str) -> dict[str, int]:
    async with pm.session() as s:
        rows = (await s.execute(select(AgentTask.agent, func.count()).where(AgentTask.org_id == str(org), AgentTask.status.in_(("open", "waiting", "escalated")))
                                .group_by(AgentTask.agent))).all()
    return {a: int(n) for a, n in rows}
