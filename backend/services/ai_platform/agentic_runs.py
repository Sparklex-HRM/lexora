"""Agent runs — one record per execution of one agent for one org.

    from agentic_runs import start_run, finish_run, shadow_report
    run = await start_run(org, agent="exit", trigger="manual", requested_by_user_id=uid)   # mode from agentic_state
    ... agent opens tasks (created_by_run=run["id"]) and files proposals (run_id=run["id"]) ...
    await finish_run(org, run["id"], summary={"headline": "412 ready, 3 need a look", "waiting_on": [...]},
                     checks_run=14, findings=3, proposals=2, tasks_opened=5, input_tokens=..., output_tokens=..., cost_usd=...)

``shadow_report`` = a run plus its ``shadow`` proposals and tasks: "what I would have done".
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import desc, func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from agentic_log import append_event  # noqa: E402
from agentic_models import AGENTS, AgentProposal, AgentRun, AgentTask  # noqa: E402
from agentic_registry import AGENT_VERSION, REGISTRY  # noqa: E402
from agentic_state import agentic_state  # noqa: E402

_T0: dict[str, float] = {}


class RunError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def run_dict(r: AgentRun) -> dict:
    return {"id": r.id, "agent": r.agent, "agent_role": REGISTRY[r.agent].role_name if r.agent in REGISTRY else r.agent, "agent_version": r.agent_version,
            "trigger": r.trigger, "mode": r.mode, "status": r.status, "started_at": pm.iso(r.started_at), "finished_at": pm.iso(r.finished_at),
            "duration_ms": int(r.duration_ms or 0), "summary": r.summary or {}, "checks_run": r.checks_run, "findings": r.findings,
            "proposals": r.proposals, "tasks_opened": r.tasks_opened, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "cost_usd": float(r.cost_usd or 0), "cost_cad": r.cost_cad, "error": r.error, "requested_by_user_id": r.requested_by_user_id}


async def start_run(org: str, *, agent: str, trigger: str = "schedule", requested_by_user_id: str | None = None, mode: str | None = None) -> dict:
    if agent not in AGENTS:
        raise RunError(422, f"Unknown agent {agent!r}")
    st = await agentic_state(org)
    if not st["enabled"]:
        raise RunError(409, "Agentic operations are not switched on for this company (add-on not active).")
    mode = mode or st["mode"]
    r = AgentRun(org_id=str(org), agent=agent, agent_version=AGENT_VERSION, trigger=trigger[:24], mode=mode, requested_by_user_id=requested_by_user_id)
    async with pm.session() as s:
        s.add(r)
        await s.commit()
        out = run_dict(r)
    _T0[r.id] = time.monotonic()
    await append_event(org, event_type="run.started", agent_name=agent, agent_version=AGENT_VERSION, run_id=r.id, step_no=0,
                       requested_by_user_id=requested_by_user_id, run_as_identity=f"agent:{agent}", tool_name="start_run",
                       tool_input={"trigger": trigger, "mode": mode}, risk_tier=0, policy_decision="allow")
    return out


async def finish_run(org: str, run_id: str, *, summary: dict | None = None, checks_run: int = 0, findings: int = 0, proposals: int | None = None,
                     tasks_opened: int | None = None, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0,
                     cache_write_tokens: int = 0, cost_usd: float = 0.0, cost_cad: float | None = None, error: str | None = None) -> dict:
    async with pm.session() as s:
        r = (await s.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == str(org)))).scalar_one_or_none()
        if not r:
            raise RunError(404, "Run not found")
        if proposals is None:
            proposals = int((await s.execute(select(func.count()).select_from(AgentProposal).where(AgentProposal.run_id == run_id))).scalar() or 0)
        if tasks_opened is None:
            tasks_opened = int((await s.execute(select(func.count()).select_from(AgentTask).where(AgentTask.created_by_run == run_id))).scalar() or 0)
        r.finished_at = pm.now_utc()
        t0 = _T0.pop(run_id, None)
        r.duration_ms = int((time.monotonic() - t0) * 1000) if t0 else int((pm.aware(r.finished_at) - pm.aware(r.started_at)).total_seconds() * 1000)
        r.summary, r.checks_run, r.findings, r.proposals, r.tasks_opened = summary or {}, checks_run, findings, proposals, tasks_opened
        r.input_tokens, r.output_tokens, r.cost_usd, r.cost_cad = input_tokens, output_tokens, float(cost_usd or 0), cost_cad
        r.status, r.error = ("error" if error else "done"), (error[:400] if error else None)
        await s.commit()
        out = run_dict(r)
    await append_event(org, event_type="run.finished" if not error else "run.error", agent_name=r.agent, agent_version=r.agent_version, run_id=run_id,
                       run_as_identity=f"agent:{r.agent}", tool_name="finish_run", input_tokens=input_tokens, output_tokens=output_tokens,
                       cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens, cost_usd=cost_usd, cost_cad=cost_cad,
                       tool_input={"checks_run": checks_run, "findings": findings, "proposals": proposals, "tasks_opened": tasks_opened, "error": error},
                       tool_output=summary, risk_tier=0, policy_decision="allow", agent_reason_text=(summary or {}).get("headline"))
    return out


async def list_runs(org: str, *, agent: str | None = None, mode: str | None = None, limit: int = 100) -> list[dict]:
    q = select(AgentRun).where(AgentRun.org_id == str(org))
    if agent:
        q = q.where(AgentRun.agent == agent)
    if mode:
        q = q.where(AgentRun.mode == mode)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(desc(AgentRun.started_at)).limit(min(limit, 500)))).scalars().all()
    return [run_dict(r) for r in rows]


async def latest_runs(org: str) -> dict[str, dict]:
    async with pm.session() as s:
        rows = (await s.execute(select(AgentRun).where(AgentRun.org_id == str(org)).order_by(desc(AgentRun.started_at)).limit(60))).scalars().all()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r.agent, run_dict(r))
    return out


async def shadow_report(org: str, run_id: str) -> dict:
    """The run + everything it WOULD have done (shadow proposals) + the tasks it opened."""
    from agentic_proposals import proposal_dict
    from agentic_tasks import task_dict
    async with pm.session() as s:
        r = (await s.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == str(org)))).scalar_one_or_none()
        if not r:
            raise RunError(404, "Run not found")
        props = (await s.execute(select(AgentProposal).where(AgentProposal.run_id == run_id).order_by(AgentProposal.created_at))).scalars().all()
        tasks = (await s.execute(select(AgentTask).where(AgentTask.created_by_run == run_id).order_by(AgentTask.created_at))).scalars().all()
    would = [proposal_dict(p) for p in props]
    return {"run": run_dict(r), "would_have_done": would, "tasks": [task_dict(t) for t in tasks],
            "counts": {"proposals": len(would), "shadow": sum(1 for p in would if p["status"] == "shadow"),
                       "by_tier": {str(t): sum(1 for p in would if p["risk_tier"] == t) for t in (0, 1, 2)},
                       "dollars": round(sum(float((p["aggregate_impact"] or {}).get("dollars") or 0) for p in would), 2),
                       "headcount": sum(int((p["aggregate_impact"] or {}).get("headcount") or 0) for p in would)}}
