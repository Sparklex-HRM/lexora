"""`/ai/copilot` — chat, confirm/cancel a proposed write, status (budget), audit."""
import sys
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import copilot as CP  # noqa: E402
import llm  # noqa: E402
from g5_models import CopilotAudit, CopilotPending  # noqa: E402
from shield import Shield  # noqa: E402

router = APIRouter(prefix="/ai/copilot", tags=["copilot"])


def _ids(request: Request) -> tuple[str, str]:
    org, uid = request.headers.get("x-org-id"), request.headers.get("x-user-id")
    if not org or not uid:
        raise HTTPException(401, "Missing user context")
    return org, uid


async def _budget(org: str, uid: str) -> dict:
    cfg = await get_setting(org, "ai_copilot", {}) or {}
    org_limit, user_limit = int(cfg.get("org_tokens_per_day") or 0), int(cfg.get("user_tokens_per_day") or 0)
    start = pm.now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    async with pm.session() as s:
        tot = (await s.execute(select(func.coalesce(func.sum(CopilotAudit.input_tokens + CopilotAudit.output_tokens), 0))
                               .where(CopilotAudit.org_id == org, CopilotAudit.created_at >= start))).scalar() or 0
        mine = (await s.execute(select(func.coalesce(func.sum(CopilotAudit.input_tokens + CopilotAudit.output_tokens), 0))
                                .where(CopilotAudit.org_id == org, CopilotAudit.user_id == uid, CopilotAudit.created_at >= start))).scalar() or 0
    over = (org_limit and tot >= org_limit) or (user_limit and mine >= user_limit)
    return {"enabled": bool(cfg.get("enabled", True)), "org_used": int(tot), "org_limit": org_limit, "user_used": int(mine),
            "user_limit": user_limit, "over": bool(over), "resets": (start + timedelta(days=1)).isoformat()}


@router.get("/status")
async def status(request: Request):
    org, uid = _ids(request)
    b = await _budget(org, uid)
    return {"configured": llm.configured(), "model": llm.model_name() if llm.configured() else None,
            "message": ("AI assistant is live." if llm.configured() else llm.NOT_CONFIGURED), "budget": b,
            "shield": "Identifiers and money are replaced by placeholders before any model call; the model never sees raw values.",
            "linked_employee": bool(request.headers.get("x-employee-id"))}


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []


@router.post("/chat")
async def chat(body: ChatIn, request: Request):
    org, uid = _ids(request)
    headers = dict(request.headers)
    role = headers.get("x-role", "") or ""
    msg = (body.message or "").strip()[:2000]
    if not msg:
        raise HTTPException(422, "Say something first")
    today = await org_today(org)
    pending_sink: list[dict] = []
    tools = CP.Tools(headers, pending_sink)
    budget = await _budget(org, uid)
    note, result = "", None
    if not budget["enabled"]:
        note = "The copilot is switched off for your company."
        result = {"reply": note, "mode": "refused", "tool_calls": []}
    elif llm.configured() and not budget["over"]:
        country = (await get_setting(org, "locale.country", "") or "CA").upper()
        try:
            result = await CP.model_answer(msg, body.history or [], headers, tools, role, country, today, await directory.org_name(org))
        except llm.LlmError as e:
            note = f"The model call failed ({e}); answered by rules instead."
    else:
        note = (llm.NOT_CONFIGURED if not llm.configured()
                else f"Daily AI budget reached ({budget['user_used'] if budget['user_limit'] and budget['user_used'] >= budget['user_limit'] else budget['org_used']} tokens). Rules-based answers still work.")
    if result is None:
        reply, calls = await CP.rules_answer(msg, tools, today)
        sh = Shield()
        result = {"reply": reply, "reply_shielded": sh.protect(reply), "prompt_shielded": sh.protect(msg), "tool_calls": [
            {**c, "args": sh.protect_value(c.get("args") or {})} for c in calls], "masked": sh.masked, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": 0.0, "model": None, "mode": "rules"}
    pend_out = None
    async with pm.session() as s:
        if pending_sink:
            p = pending_sink[-1]
            row = CopilotPending(org_id=org, user_id=uid, action=p["action"], payload=p["payload"], summary=p["summary"],
                                 expires_at=pm.now_utc() + timedelta(minutes=30))
            s.add(row)
            await s.flush()
            pend_out = {"id": row.id, "action": row.action, "summary": row.summary, "expires_at": pm.iso(row.expires_at)}
        s.add(CopilotAudit(org_id=org, user_id=uid, user_email=headers.get("x-user-email"), role=role, mode=result["mode"],
                           model=result.get("model"), prompt_shielded=result.get("prompt_shielded", ""),
                           reply_shielded=result.get("reply_shielded", ""), tool_calls=result.get("tool_calls", []),
                           masked_count=result.get("masked", 0), input_tokens=result.get("input_tokens", 0),
                           output_tokens=result.get("output_tokens", 0), cache_read_tokens=result.get("cache_read_tokens", 0),
                           cache_write_tokens=result.get("cache_write_tokens", 0),
                           cost_usd=result.get("cost_usd", 0.0), error=note[:300] or None))
        await s.commit()
    return {"reply": result["reply"], "mode": result["mode"], "note": note, "pending": pend_out,
            "tools_used": [c["tool"] for c in result.get("tool_calls", [])], "masked": result.get("masked", 0),
            "usage": {"input_tokens": result.get("input_tokens", 0), "output_tokens": result.get("output_tokens", 0),
                      "cache_read_tokens": result.get("cache_read_tokens", 0), "cache_write_tokens": result.get("cache_write_tokens", 0),
                      "cost_usd": result.get("cost_usd", 0.0), "model": result.get("model")},
            "budget": await _budget(org, uid)}


class ConfirmIn(BaseModel):
    pending_id: str


async def _pending(s, org, uid, pid) -> CopilotPending:
    row = (await s.execute(select(CopilotPending).where(CopilotPending.id == pid, CopilotPending.org_id == org,
                                                        CopilotPending.user_id == uid))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "That proposed action was not found")
    if row.status != "pending":
        raise HTTPException(409, f"That action is already {row.status}")
    if row.expires_at and pm.aware(row.expires_at) < pm.now_utc():
        row.status = "expired"
        await s.commit()
        raise HTTPException(409, "That proposal expired — ask again")
    return row


@router.post("/confirm")
async def confirm(body: ConfirmIn, request: Request):
    org, uid = _ids(request)
    headers = dict(request.headers)
    async with pm.session() as s:
        row = await _pending(s, org, uid, body.pending_id)
        if row.action == "apply_leave":
            data, err = await CP._post(f"{settings.LEAVE_URL}/leave/requests", headers, row.payload)
        else:
            data, err = None, "Unknown action"
        row.decided_at = pm.now_utc()
        row.status = "failed" if err else "done"
        row.result = {"error": err} if err else {"id": (data or {}).get("id"), "status": (data or {}).get("status")}
        s.add(CopilotAudit(org_id=org, user_id=uid, user_email=headers.get("x-user-email"), role=headers.get("x-role"),
                           mode="rules", prompt_shielded=f"[confirm {row.action}]", reply_shielded=("failed: " + err) if err else "submitted",
                           tool_calls=[{"tool": row.action, "args": Shield().protect_value(row.payload), "ok": not err}]))
        await s.commit()
    if err:
        return {"ok": False, "message": f"Not submitted: {err}", "status": "failed"}
    return {"ok": True, "message": f"Submitted: {row.summary}. It now waits for your manager.", "status": "done",
            "link": "/leave", "request": row.result}


@router.post("/cancel")
async def cancel(body: ConfirmIn, request: Request):
    org, uid = _ids(request)
    async with pm.session() as s:
        row = await _pending(s, org, uid, body.pending_id)
        row.status, row.decided_at = "cancelled", pm.now_utc()
        await s.commit()
    return {"ok": True, "message": "Cancelled — nothing was submitted."}


@router.get("/audit")
async def audit(request: Request, limit: int = 100, all_users: bool = False):
    """A person sees their own exchanges; org admin / HR may see the company's (still shielded)."""
    org, uid = _ids(request)
    role = request.headers.get("x-role", "") or ""
    q = select(CopilotAudit).where(CopilotAudit.org_id == org)
    if all_users:
        if role not in ("org_admin", "superadmin") and not directory.role_in(role, ["hr"]):
            raise HTTPException(403, "Only an org admin or HR can see everyone's copilot audit")
    else:
        q = q.where(CopilotAudit.user_id == uid)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(CopilotAudit.created_at.desc()).limit(min(limit, 500)))).scalars().all()
    return {"items": [{"id": r.id, "user": r.user_email, "role": r.role, "mode": r.mode, "model": r.model, "prompt": r.prompt_shielded,
                       "reply": r.reply_shielded, "tools": r.tool_calls or [], "masked": r.masked_count,
                       "tokens": r.input_tokens + r.output_tokens, "cost_usd": float(r.cost_usd or 0), "note": r.error,
                       "at": pm.iso(r.created_at)} for r in rows],
            "note": "Prompts and replies are stored as the model saw them — with placeholders, never the real identifiers or amounts."}
