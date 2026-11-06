"""`/ai/agents/*` — the Agentic operations screen: overview, approvals inbox, tasks, runs / shadow
reports, action log + chain verify, value, settings.

Who may look / act: org admin, HR admin, payroll admin (the office that owns payroll and filings;
the CEO is the org admin). Employees, managers and the accountant get 403 — nothing of theirs lives
here. Settings and the shadow → live switch: org admin only. The approver on every decision is the
signed-in person from the gateway headers — never a body field.

(``GET /ai/agents`` itself belongs to the legacy watchdog fleet in main.py; this router only owns the
sub-paths.)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_proposals as PR  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
import agentic_tasks as TK  # noqa: E402
import agentic_value as VAL  # noqa: E402
from agentic_models import AGENTS  # noqa: E402
from agentic_registry import REGISTRY, registry_dict  # noqa: E402

router = APIRouter(prefix="/ai/agents", tags=["agentic"])
OFFICE_ROLES = ("org_admin", "hr_admin", "payroll_admin")


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return (request.headers.get("x-role", "") or "").lower()


def _office(request: Request) -> str:
    """Org admin, HR admin, payroll admin (superadmin passes). Managers, employees, accountant: 403."""
    role = _role(request)
    if role != "superadmin" and role not in OFFICE_ROLES:
        raise HTTPException(403, "Agentic operations are for the org admin, HR admin and payroll admin")
    return role


def _admin(request: Request) -> str:
    role = _role(request)
    if role not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an org admin can change agent settings")
    return role


def _approver(request: Request) -> PR.Approver:
    try:
        return PR.Approver.from_headers(request.headers)
    except PR.ProposalError as e:
        raise HTTPException(e.status, e.message)


def _raise(e: Exception):
    status = getattr(e, "status", None)
    if isinstance(status, int):
        raise HTTPException(status, getattr(e, "message", str(e)))
    if isinstance(e, PermissionError):
        raise HTTPException(409, str(e))
    if isinstance(e, ValueError):
        raise HTTPException(422, str(e))
    raise e


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Bad date {v!r} — use YYYY-MM-DD or ISO-8601")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ══════════════════════════ overview / registry ═════════════════════════════════

@router.get("/overview")
async def overview(request: Request):
    org = _org(request)
    role = _office(request)
    st = await ST.agentic_state(org)
    latest = await RUNS.latest_runs(org)
    open_tasks = await TK.open_counts(org)
    inbox = await PR.inbox_counts(org)
    value = await VAL.month_value_by_agent(org) if st["enabled"] else {}
    agents = []
    for spec in registry_dict():
        k = spec["key"]
        agents.append({**spec, "mode": st["mode"], "last_run": latest.get(k), "open_tasks": open_tasks.get(k, 0), "inbox": inbox.get(k, 0),
                       "value_this_month": value.get(k, {"proxy_minutes": 0, "avoided_dollars": 0, "runs": 0})})
    return {"state": {k: st[k] for k in ("enabled", "addon_purchased", "paused", "mode", "shadow_cycles_done", "can_go_live", "live_gate_note", "live_after_shadow_cycles")},
            "agents": agents, "inbox_total": sum(inbox.values()), "open_tasks_total": sum(open_tasks.values()),
            "can_edit": role in ("org_admin", "superadmin"), "as_of": pm.iso(pm.now_utc())}


@router.get("/registry")
async def registry(request: Request):
    _org(request)
    _office(request)
    return {"agents": registry_dict()}


# ══════════════════════════ proposals (approval inbox) ══════════════════════════

@router.get("/proposals")
async def proposals(request: Request, status: str | None = "inbox", agent: str | None = None, run_id: str | None = None, limit: int = 200):
    org = _org(request)
    _office(request)
    items = await PR.list_proposals(org, status=status or None, agent=agent, run_id=run_id, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/proposals/{pid}")
async def proposal(pid: str, request: Request):
    org = _org(request)
    _office(request)
    try:
        return {**(await PR.get_proposal(org, pid)), "log": await LOG.recent(org, approval_id=pid, limit=50)}
    except PR.ProposalError as e:
        _raise(e)


class ApproveIn(BaseModel):
    for_session: bool = False
    note: str = Field("", max_length=400)


class EditApproveIn(BaseModel):
    tool_args: dict
    note: str = Field("", max_length=400)


class DenyIn(BaseModel):
    message: str = Field(..., min_length=3, max_length=600)


@router.post("/proposals/{pid}/approve")
async def approve(pid: str, body: ApproveIn, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await PR.approve(org, pid, _approver(request), for_session=body.for_session, note=body.note)
    except PR.ProposalError as e:
        _raise(e)


@router.post("/proposals/{pid}/edit-approve")
async def edit_approve(pid: str, body: EditApproveIn, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await PR.approve(org, pid, _approver(request), edited_args=body.tool_args, note=body.note)
    except PR.ProposalError as e:
        _raise(e)


@router.post("/proposals/{pid}/deny")
async def deny(pid: str, body: DenyIn, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await PR.deny(org, pid, _approver(request), message=body.message)
    except PR.ProposalError as e:
        _raise(e)


@router.get("/grants")
async def grants(request: Request, active_only: bool = True):
    org = _org(request)
    _office(request)
    return {"items": await PR.list_grants(org, active_only=active_only)}


@router.post("/grants/{gid}/revoke")
async def revoke_grant(gid: str, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await PR.revoke_grant(org, gid, _approver(request))
    except PR.ProposalError as e:
        _raise(e)


# ══════════════════════════ tasks ═══════════════════════════════════════════════

@router.get("/tasks")
async def tasks(request: Request, status: str | None = "open,waiting,escalated", agent: str | None = None, limit: int = 500):
    org = _org(request)
    _office(request)
    items = await TK.list_tasks(org, status=status or None, agent=agent, limit=limit)
    return {"items": items, "total": len(items)}


class CompleteIn(BaseModel):
    result: dict = Field(default_factory=dict)
    status: str = "done"


class EscalateIn(BaseModel):
    reason: str = Field("", max_length=400)


@router.post("/tasks/{tid}/complete")
async def complete(tid: str, body: CompleteIn, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await TK.complete_task(org, tid, actor_user_id=request.headers.get("x-user-id"), result=body.result, status=body.status)
    except TK.TaskError as e:
        _raise(e)


@router.post("/tasks/{tid}/escalate")
async def escalate(tid: str, body: EscalateIn, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await TK.escalate_task(org, tid, actor_user_id=request.headers.get("x-user-id"), reason=body.reason)
    except TK.TaskError as e:
        _raise(e)


@router.post("/tasks/{tid}/chase")
async def chase(tid: str, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await TK.chase_task(org, tid, actor_user_id=request.headers.get("x-user-id"))
    except TK.TaskError as e:
        _raise(e)


# ══════════════════════════ runs / shadow reports ═══════════════════════════════

@router.get("/runs")
async def runs(request: Request, agent: str | None = None, mode: str | None = None, limit: int = 100):
    org = _org(request)
    _office(request)
    return {"items": await RUNS.list_runs(org, agent=agent, mode=mode, limit=limit)}


@router.get("/runs/{rid}")
async def run_report(rid: str, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await RUNS.shadow_report(org, rid)
    except RUNS.RunError as e:
        _raise(e)


# ══════════════════════════ action log ══════════════════════════════════════════

@router.get("/log")
async def log_rows(request: Request, limit: int = 200, run_id: str | None = None, event_type: str | None = None):
    org = _org(request)
    _office(request)
    return {"items": await LOG.recent(org, limit=limit, run_id=run_id, event_type=event_type)}


@router.get("/log/verify")
async def log_verify(request: Request):
    org = _org(request)
    _office(request)
    return await LOG.verify_chain(org)


# ══════════════════════════ value ═══════════════════════════════════════════════

@router.get("/value")
async def value(request: Request):
    """``?from=YYYY-MM-DD&to=YYYY-MM-DD`` (UTC instants or dates); default = this month so far."""
    org = _org(request)
    _office(request)
    q = request.query_params
    return await VAL.value_summary(org, _dt(q.get("from")), _dt(q.get("to")))


@router.post("/value/{eid}/confirm")
async def confirm_value(eid: str, request: Request):
    org = _org(request)
    _office(request)
    try:
        return await VAL.confirm_outcome(org, eid, actor_user_id=_approver(request).user_id)
    except VAL.ValueError_ as e:
        _raise(e)


# ══════════════════════════ settings ════════════════════════════════════════════

@router.get("/settings")
async def get_settings(request: Request):
    org = _org(request)
    role = _office(request)
    st = await ST.agentic_state(org)
    return {"value": st, "defaults": ST.defaults(), "can_edit": role in ("org_admin", "superadmin"), "agents": sorted(AGENTS),
            "roles": ["org_admin", "hr_admin", "hr_manager", "payroll_admin", "accountant", "manager"]}


class SettingsIn(BaseModel):
    value: dict


class ModeIn(BaseModel):
    mode: str = Field(..., pattern=r"^(shadow|live)$")
    reason: str = Field("", max_length=400)


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request):
    org = _org(request)
    _admin(request)
    try:
        saved = await ST.save_settings(org, body.value or {}, actor_id=request.headers.get("x-user-id"), actor_email=request.headers.get("x-user-email"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    await LOG.append_event(org, event_type="settings.saved", tool_name="save_settings", approver_user_id=request.headers.get("x-user-id"),
                           roles_at_time_of_action=[_role(request)], tool_input={k: saved.get(k) for k in ("enabled", "approvers", "minutes_table", "thresholds", "escalation_roles")},
                           risk_tier=0, policy_decision="allow")
    return {"saved": True, "value": await ST.agentic_state(org)}


@router.post("/settings/mode")
async def set_mode(body: ModeIn, request: Request):
    """shadow → live needs 2 completed shadow cycles OR an org-admin reason (recorded as the override)."""
    org = _org(request)
    _admin(request)
    try:
        await ST.set_mode(org, body.mode, actor_id=request.headers.get("x-user-id"), actor_email=request.headers.get("x-user-email"), reason=body.reason)
    except (ValueError, PermissionError) as e:
        _raise(e)
    await LOG.append_event(org, event_type="settings.mode", tool_name="set_mode", approver_user_id=request.headers.get("x-user-id"),
                           roles_at_time_of_action=[_role(request)], decision=body.mode, decision_ts=pm.now_utc(), deny_message=None,
                           agent_reason_text=body.reason or None, risk_tier=2 if body.mode == "live" else 0, policy_decision="allow")
    return {"saved": True, "value": await ST.agentic_state(org), "agents": [REGISTRY[a].role_name for a in AGENTS]}
