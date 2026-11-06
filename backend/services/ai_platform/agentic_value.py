"""Value ledger — the three honest layers the Value tab shows (report §ROI), always next to cost.

    from agentic_value import record_outcome, confirm_outcome, value_summary
    await record_outcome(org, agent="exit", kind="exception_confirmed", run_id=run_id, proposal_id=pid,
                         dollars_avoided=3200, basis="2 unpaid days wrongly deducted — $160/day", confirmed_by_user_id=uid)
    await value_summary(org, frm, to)

* MEASURED  — from the records: payroll-run close time (run duration), approval latency, share of proposals
              accepted without an edit, decisions, tasks closed;
* PROXY     — Σ outcomes × the org's editable minutes table (``ai_agentic.minutes_table``, conservative
              defaults: resolved query 10 min, exception confirmed 15 min, filing prepared 60 min …);
* AVOIDED   — ONLY human-confirmed rows: exceptions a person confirmed ($ at risk caught before pay) and
              interest / fees avoided computed by the agents from the ORG's own amounts and the statutory tables;
* COST      — agent tokens / C$ from the action log, and reviewer minutes (decisions × minutes per decision).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from agentic_log import append_event, cost_between  # noqa: E402
from agentic_models import AGENTS, AgentProposal, AgentRun, AgentTask, AgentValueEvent  # noqa: E402
from agentic_registry import REGISTRY  # noqa: E402
from agentic_state import agentic_state  # noqa: E402


class ValueError_(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def event_dict(e: AgentValueEvent) -> dict:
    return {"id": e.id, "agent": e.agent, "run_id": e.run_id, "proposal_id": e.proposal_id, "task_id": e.task_id, "kind": e.kind, "units": float(e.units or 0),
            "dollars_avoided": e.dollars_avoided, "basis": e.basis, "confirmed_by_user_id": e.confirmed_by_user_id, "confirmed_at": pm.iso(e.confirmed_at),
            "note": e.note, "at": pm.iso(e.at), "confirmed": bool(e.confirmed_by_user_id)}


def _clip(v: str | None, n: int) -> str | None:
    """Fit the column (Postgres refuses an over-long varchar; SQLite in the unit tests does not — phase-3 fix: the
    Compliance Clerk's itemised 'avoided if it had slipped a month' basis broke the evidence step on the real DB)."""
    if not v:
        return None
    v = str(v)
    return v if len(v) <= n else v[: n - 1] + "…"


async def record_outcome(org: str, *, agent: str, kind: str, units: float = 1.0, dollars_avoided: float | None = None, basis: str | None = None,
                         confirmed_by_user_id: str | None = None, run_id: str | None = None, proposal_id: str | None = None, task_id: str | None = None,
                         note: str | None = None) -> dict:
    if agent not in AGENTS:
        raise ValueError_(422, f"Unknown agent {agent!r}")
    e = AgentValueEvent(org_id=str(org), agent=agent, kind=kind[:60], units=float(units or 0), dollars_avoided=(float(dollars_avoided) if dollars_avoided is not None else None),
                        basis=_clip(basis, 300), confirmed_by_user_id=confirmed_by_user_id, confirmed_at=pm.now_utc() if confirmed_by_user_id else None,
                        run_id=run_id, proposal_id=proposal_id, task_id=task_id, note=_clip(note, 400))
    async with pm.session() as s:
        s.add(e)
        await s.commit()
        out = event_dict(e)
    await append_event(org, event_type="value.recorded", agent_name=agent, run_id=run_id, approval_id=proposal_id, tool_name="record_outcome",
                       tool_input={"kind": kind, "units": units, "dollars_avoided": dollars_avoided, "basis": basis, "confirmed": bool(confirmed_by_user_id)},
                       approver_user_id=confirmed_by_user_id, risk_tier=0, policy_decision="allow")
    return out


async def confirm_outcome(org: str, event_id: str, *, actor_user_id: str) -> dict:
    """A person confirms an avoided-$ row (until then it is an attention signal, not value)."""
    async with pm.session() as s:
        e = (await s.execute(select(AgentValueEvent).where(AgentValueEvent.id == event_id, AgentValueEvent.org_id == str(org)))).scalar_one_or_none()
        if not e:
            raise ValueError_(404, "Value row not found")
        if e.confirmed_by_user_id:
            raise ValueError_(409, "Already confirmed")
        e.confirmed_by_user_id, e.confirmed_at = actor_user_id, pm.now_utc()
        await s.commit()
        out = event_dict(e)
    await append_event(org, event_type="value.confirmed", agent_name=e.agent, run_id=e.run_id, approval_id=e.proposal_id, tool_name="confirm_outcome",
                       approver_user_id=actor_user_id, decision="confirm", decision_ts=pm.now_utc(), tool_input={"event_id": event_id, "dollars_avoided": e.dollars_avoided},
                       risk_tier=0, policy_decision="allow")
    return out


def _window(frm: datetime | None, to: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    to = to or now
    frm = frm or to.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if frm.tzinfo is None:
        frm = frm.replace(tzinfo=timezone.utc)
    if to.tzinfo is None:
        to = to.replace(tzinfo=timezone.utc)
    if to <= frm:
        to = frm + timedelta(days=1)
    return frm, to


async def value_summary(org: str, frm: datetime | None = None, to: datetime | None = None) -> dict:
    frm, to = _window(frm, to)
    st = await agentic_state(org)
    minutes = st["minutes_table"]
    per_agent = {a: {"agent": a, "agent_role": REGISTRY[a].role_name, "runs": 0, "proposals_decided": 0, "accepted_without_edit": 0, "denied": 0,
                     "proxy_minutes": 0.0, "avoided_dollars": 0.0, "unconfirmed_dollars": 0.0, "tasks_closed": 0, "cost_usd": 0.0} for a in AGENTS}
    async with pm.session() as s:
        runs = (await s.execute(select(AgentRun).where(AgentRun.org_id == str(org), AgentRun.started_at >= frm, AgentRun.started_at < to))).scalars().all()
        props = (await s.execute(select(AgentProposal).where(AgentProposal.org_id == str(org), AgentProposal.decided_at.is_not(None),
                                                             AgentProposal.decided_at >= frm, AgentProposal.decided_at < to))).scalars().all()
        tasks_closed = (await s.execute(select(AgentTask.agent, func.count()).where(AgentTask.org_id == str(org), AgentTask.status == "done",
                                                                                   AgentTask.completed_at >= frm, AgentTask.completed_at < to)
                                        .group_by(AgentTask.agent))).all()
        events = (await s.execute(select(AgentValueEvent).where(AgentValueEvent.org_id == str(org), AgentValueEvent.at >= frm, AgentValueEvent.at < to))).scalars().all()
    # measured
    done_runs = [r for r in runs if r.status == "done"]
    close_ms = [int(r.duration_ms or 0) for r in done_runs if r.duration_ms]
    decided = [p for p in props if p.status in ("approved", "executed", "denied", "failed")]
    latencies = [(pm.aware(p.decided_at) - pm.aware(p.created_at)).total_seconds() for p in decided if p.created_at and p.decided_at]
    accepted = [p for p in decided if p.status in ("approved", "executed")]
    no_edit = [p for p in accepted if not p.edited_input_diff]
    for r in runs:
        per_agent[r.agent]["runs"] += 1
        per_agent[r.agent]["cost_usd"] += float(r.cost_usd or 0)
    for p in decided:
        per_agent[p.agent]["proposals_decided"] += 1
        per_agent[p.agent]["accepted_without_edit"] += 1 if (p in no_edit) else 0
        per_agent[p.agent]["denied"] += 1 if p.status == "denied" else 0
    measured = {"runs": len(runs), "runs_done": len(done_runs), "shadow_runs": sum(1 for r in runs if r.mode == "shadow"),
                "avg_run_close_minutes": round(sum(close_ms) / len(close_ms) / 60000, 2) if close_ms else None,
                "proposals_decided": len(decided), "accepted": len(accepted), "denied": sum(1 for p in decided if p.status == "denied"),
                "accept_without_edit_pct": round(len(no_edit) / len(accepted) * 100, 1) if accepted else None,
                "avg_approval_latency_minutes": round(sum(latencies) / len(latencies) / 60, 1) if latencies else None,
                "median_approval_latency_minutes": round(sorted(latencies)[len(latencies) // 2] / 60, 1) if latencies else None,
                "tasks_closed": int(sum(n for _, n in tasks_closed))}
    # proxy
    proxy_rows: dict[str, dict] = {}
    for e in events:
        m = float(minutes.get(e.kind) or 0)
        row = proxy_rows.setdefault(e.kind, {"kind": e.kind, "units": 0.0, "minutes_each": m, "minutes": 0.0})
        row["units"] += float(e.units or 0)
        row["minutes"] += float(e.units or 0) * m
        per_agent[e.agent]["proxy_minutes"] += float(e.units or 0) * m
    for agent, n in tasks_closed:
        per_agent[agent]["tasks_closed"] += int(n)
        m = float(minutes.get("input_chased") or 0)
        row = proxy_rows.setdefault("input_chased", {"kind": "input_chased", "units": 0.0, "minutes_each": m, "minutes": 0.0})
        row["units"] += int(n)
        row["minutes"] += int(n) * m
        per_agent[agent]["proxy_minutes"] += int(n) * m
    proxy_minutes = sum(r["minutes"] for r in proxy_rows.values())
    # avoided (human-confirmed only)
    confirmed = [e for e in events if e.confirmed_by_user_id and (e.dollars_avoided or 0) > 0]
    unconfirmed = [e for e in events if not e.confirmed_by_user_id and (e.dollars_avoided or 0) > 0]
    for e in confirmed:
        per_agent[e.agent]["avoided_dollars"] += float(e.dollars_avoided or 0)
    for e in unconfirmed:
        per_agent[e.agent]["unconfirmed_dollars"] += float(e.dollars_avoided or 0)
    avoided = {"dollars": round(sum(float(e.dollars_avoided or 0) for e in confirmed), 2), "items": len(confirmed),
               "unconfirmed_dollars": round(sum(float(e.dollars_avoided or 0) for e in unconfirmed), 2), "unconfirmed_items": len(unconfirmed),
               "rows": [event_dict(e) for e in sorted(events, key=lambda x: pm.aware(x.at), reverse=True) if (e.dollars_avoided or 0) > 0][:200]}
    # cost
    cost = await cost_between(org, frm, to)
    review_each = float(minutes.get("review_minutes_per_decision") or 0)
    reviewer_minutes = round(len(decided) * review_each, 1)
    for a in per_agent.values():
        a["proxy_minutes"] = round(a["proxy_minutes"], 1)
        a["avoided_dollars"] = round(a["avoided_dollars"], 2)
        a["unconfirmed_dollars"] = round(a["unconfirmed_dollars"], 2)
        a["cost_usd"] = round(a["cost_usd"], 6)
    return {"from": frm.isoformat(), "to": to.isoformat(), "mode": st["mode"], "enabled": st["enabled"],
            "measured": measured,
            "proxy": {"minutes": round(proxy_minutes, 1), "hours": round(proxy_minutes / 60, 2), "rows": sorted(proxy_rows.values(), key=lambda r: -r["minutes"]),
                      "minutes_table": minutes, "note": "Hours = outcomes × the minutes table above (editable by an org admin). An estimate, labelled as one."},
            "avoided": {**avoided, "note": "Only rows a person confirmed count. Unconfirmed $ is an attention signal, not value."},
            "cost": {**cost, "reviewer_minutes": reviewer_minutes, "reviewer_minutes_per_decision": review_each, "decisions": len(decided),
                     "note": "Agent cost comes from the action log's token fields; C$ needs the USD→CAD rate on AI settings."},
            "per_agent": list(per_agent.values())}


async def month_value_by_agent(org: str) -> dict[str, dict]:
    v = await value_summary(org)
    return {a["agent"]: {"proxy_minutes": a["proxy_minutes"], "avoided_dollars": a["avoided_dollars"], "runs": a["runs"]} for a in v["per_agent"]}
