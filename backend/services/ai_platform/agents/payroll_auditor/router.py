"""``/ai/agents/payroll-auditor/*`` — the Payroll Auditor workspace (office roles via ``lifecycle_common.guard``: permission
``ai.view`` / ``ai.create``; thresholds = ``ai.admin``, org admin). The approver / checker identity is the signed-in
person from the gateway headers — never a body field.

  POST /run {pay_period?, pay_frequency?}       manual run (trigger "manual")
  GET  /status?period&pay_frequency             period resolution, pay date, lock state, thresholds, checks
  GET  /periods                                 pay periods the agent has run for
  GET  /pack?period= | ?run_id=                 the review pack (summary · grid · waiting-on · would-have-done · statutory · tie-out)
  GET  /findings?run_id&severity&status&code    the exception grid only
  POST /findings/{id}/confirm {note?}           value outcome exception_confirmed (dollars at risk)
  POST /findings/{id}/dismiss {reason}
  POST /findings/{id}/note {text}
  POST /findings/{id}/explain {text}            a person writes the reason (closes the chase task)
  GET  /thresholds · PUT /thresholds {value}    presets tight / normal / loose / custom + the agent's own keys
"""
from __future__ import annotations

import sys
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

sys.path.append("/app")
from shared.utils.org_time import org_today  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402

from agents.lifecycle_common import guard, org_of, user_of  # noqa: E402  (role-family + permission guard; named import so the authz AST sees it)

from . import inputs as IN  # noqa: E402
from . import pack as PK  # noqa: E402
from . import run as RN  # noqa: E402
from . import thresholds as TH  # noqa: E402
from .checks import CHECKS  # noqa: E402

router = APIRouter(prefix="/ai/agents/payroll-auditor", tags=["agentic-payroll-auditor"])
WHO = "The Payroll Auditor is for the org admin, HR admin and payroll admin"
PERIOD_RE = r"^(weekly|biweekly|semi_monthly|monthly):\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$"
FREQ_RE = r"^(weekly|biweekly|semi_monthly|monthly)$"


def _raise(e: Exception):
    status = getattr(e, "status", None)
    if isinstance(status, int):
        raise HTTPException(status, getattr(e, "message", str(e)))
    if isinstance(e, ValueError):
        raise HTTPException(422, str(e))
    raise e


class RunIn(BaseModel):
    pay_period: str | None = Field(None, pattern=PERIOD_RE)
    pay_frequency: str | None = Field(None, pattern=FREQ_RE)


@router.post("/run")
async def run_now(body: RunIn, request: Request):
    org = org_of(request)
    guard(request, "ai.create", WHO)
    try:
        return await RN.run_payroll_audit(org, trigger="manual", pay_period=body.pay_period, pay_frequency=body.pay_frequency, requested_by=user_of(request))
    except RUNS.RunError as e:
        _raise(e)


def _iso_row(r: dict | None) -> dict | None:
    if not r:
        return None
    return {k: (v.isoformat() if hasattr(v, "isoformat") else (str(v) if k == "id" else v)) for k, v in r.items() if k in ("id", "period_start", "period_end", "pay_date", "status")}


@router.get("/status")
async def status(request: Request, period: str | None = None, pay_frequency: str | None = None):
    org = org_of(request)
    role = guard(request, "ai.view", WHO)
    st = await ST.agentic_state(org)
    th = TH.effective(st)
    today = await org_today(org)
    P = await IN.resolve_period(org, today=today, period=period, pay_frequency=pay_frequency)
    emps = await IN.employees(org)
    freq = P["pay_frequency"]
    active = {e["id"] for e in emps if e.get("is_active", True) and e.get("status") in ("active", "on_leave") and (e.get("pay_frequency") or freq) == freq}
    _, _, _, att = await IN.attendance(org, P["period_start"], P["period_end"], active)
    latest = await PK.latest_run_for_period(org, P["period"])
    freqs = sorted({e.get("pay_frequency") for e in emps if e.get("is_active", True) and e.get("pay_frequency") in IN.PAY_FREQUENCIES}) or [freq]
    return {"state": {k: st.get(k) for k in ("enabled", "mode", "shadow_cycles_done", "can_go_live", "live_gate_note", "addon_purchased", "paused")},
            "pay_frequency": freq, "pay_frequencies": freqs,
            "period": P["period"], "period_start": P["period_start"].isoformat(), "period_end": P["period_end"].isoformat(), "pay_date": P["pay_date"].isoformat(),
            "prev_period": P["prev_period"], "today": today.isoformat(), "freeze_by": (P["pay_date"] - timedelta(days=th["lead_days"])).isoformat(),
            "approve_by": (P["pay_date"] - timedelta(days=th["approval_lead_days"])).isoformat(),
            "attendance": {"tracked": att["tracked"], "locked": att["locked"], "ready": att["ready"], "total": att["total"], "missing": att["missing"][:50]},
            "previous_run": _iso_row(P["previous_run"]), "engine_run": _iso_row(P["current_run"]),
            "latest_run": latest, "thresholds": th, "checks": [{"code": c, "name": n} for c, n in CHECKS], "can_edit": role in ("org_admin", "superadmin"),
            "headcount": len(active)}


@router.get("/periods")
async def periods(request: Request):
    org = org_of(request)
    guard(request, "ai.view", WHO)
    return {"items": await PK.periods(org)}


@router.get("/pack")
async def pack(request: Request, period: str | None = None, run_id: str | None = None):
    org = org_of(request)
    guard(request, "ai.view", WHO)
    rid = run_id
    if not rid:
        latest = await PK.latest_run_for_period(org, period)
        if not latest:                     # a normal state, not an error: the screen shows "press Run now"
            return {"empty": True, "pay_period": period, "message": "No Payroll Auditor run for that pay period yet — press Run now."}
        rid = latest["id"]
    try:
        return await PK.review_pack(org, rid)
    except PK.PackError as e:
        _raise(e)


@router.get("/findings")
async def findings(request: Request, run_id: str, severity: str | None = None, status: str | None = None, code: str | None = None):
    org = org_of(request)
    guard(request, "ai.view", WHO)
    items = await PK.findings_for_run(org, run_id, severity=severity, status=status, code=code)
    return {"items": items, "total": len(items)}


class NoteIn(BaseModel):
    note: str = Field("", max_length=600)


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=600)


class TextIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=600)


@router.post("/findings/{fid}/confirm")
async def confirm(fid: str, body: NoteIn, request: Request):
    org = org_of(request)
    guard(request, "ai.create", WHO)
    try:
        return await PK.confirm(org, fid, actor_user_id=user_of(request), note=body.note)
    except PK.PackError as e:
        _raise(e)


@router.post("/findings/{fid}/dismiss")
async def dismiss(fid: str, body: ReasonIn, request: Request):
    org = org_of(request)
    guard(request, "ai.create", WHO)
    try:
        return await PK.dismiss(org, fid, actor_user_id=user_of(request), reason=body.reason)
    except PK.PackError as e:
        _raise(e)


@router.post("/findings/{fid}/note")
async def note(fid: str, body: TextIn, request: Request):
    org = org_of(request)
    guard(request, "ai.create", WHO)
    try:
        return await PK.note(org, fid, actor_user_id=user_of(request), text=body.text)
    except PK.PackError as e:
        _raise(e)


@router.post("/findings/{fid}/explain")
async def explain(fid: str, body: TextIn, request: Request):
    org = org_of(request)
    guard(request, "ai.create", WHO)
    try:
        return await PK.explain_manually(org, fid, actor_user_id=user_of(request), text=body.text)
    except PK.PackError as e:
        _raise(e)


@router.get("/thresholds")
async def get_thresholds(request: Request):
    org = org_of(request)
    role = guard(request, "ai.view", WHO)
    st = await ST.agentic_state(org)
    return {"value": TH.effective(st), "own": st.get("payroll_auditor") or {}, "defaults": TH.DEFAULTS, "presets": TH.PRESETS, "can_edit": role in ("org_admin", "superadmin")}


class ThresholdsIn(BaseModel):
    value: dict


@router.put("/thresholds")
async def put_thresholds(body: ThresholdsIn, request: Request):
    org = org_of(request)
    role = guard(request, "ai.admin", "Only an org admin can change the Payroll Auditor thresholds")
    try:
        saved = await TH.save_payroll_thresholds(org, body.value or {}, actor_id=request.headers.get("x-user-id"), actor_email=request.headers.get("x-user-email"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    await LOG.append_event(org, event_type="settings.saved", agent_name="payroll_auditor", tool_name="save_payroll_thresholds", approver_user_id=request.headers.get("x-user-id"),
                           roles_at_time_of_action=[role], tool_input=saved, risk_tier=0, policy_decision="allow")
    st = await ST.agentic_state(org)
    return {"saved": True, "value": TH.effective(st), "own": saved}
