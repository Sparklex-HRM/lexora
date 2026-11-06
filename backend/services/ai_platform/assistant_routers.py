"""D3 routes — `/ai/assistant` (chat with citations + gated actions), `/ai/settings`,
`/ai/tokens` (AI access tokens for MCP), `/ai/metering` (usage + caps)."""
from __future__ import annotations

import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import actions as ACT  # noqa: E402
import ai_config as CFG  # noqa: E402
import assistant as AS  # noqa: E402
import claude_client as LLM  # noqa: E402
import copilot as CP  # noqa: E402
import metering as MT  # noqa: E402
import tokens as TK  # noqa: E402
import tools_registry as TR  # noqa: E402
from ai_models import AiToolCall  # noqa: E402
from g5_models import CopilotAudit  # noqa: E402
from shield import Shield  # noqa: E402

router = APIRouter(prefix="/ai", tags=["ai-assistant"])
ADMIN = ("org_admin", "superadmin")


def _ids(request: Request) -> tuple[str, str]:
    org, uid = request.headers.get("x-org-id"), request.headers.get("x-user-id")
    if not org or not uid:
        raise HTTPException(401, "Missing user context")
    return org, uid


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _admin(request: Request) -> str:
    r = _role(request)
    if r not in ADMIN:
        raise HTTPException(403, "Only an org admin can change AI settings")
    return r


def _office(request: Request) -> bool:
    r = _role(request)
    return r in ADMIN or directory.role_in(r, ["hr"])


def _ctx(request: Request) -> TR.Ctx:
    return TR.Ctx(headers={k.lower(): v for k, v in request.headers.items()}, channel="chat")


async def _cap(org: str, cfg: dict) -> dict:
    tot = await MT.month_totals(org)
    return MT.cap_status(tot["tokens"], tot["cost_cad"], cfg["monthly_cap_tokens"], cfg["monthly_cap_cad"], cfg["cap_mode"])


# ══════════════════════════ assistant ════════════════════════════════════════════

@router.get("/assistant/status")
async def assistant_status(request: Request):
    org, uid = _ids(request)
    cfg = await CFG.load(org)
    st = CFG.status(cfg)
    cap = await _cap(org, cfg)
    specs = TR.visible_tools(_role(request), {"read", "act"} if cfg["features"].get("actions", True) else {"read"})
    return {**{k: st[k] for k in ("configured", "key_source", "model", "effort", "features", "message")}, "cap": cap,
            "tools": {"read": [t.name for t in specs if t.scope == "read"], "act": [t.name for t in specs if t.scope == "act"]},
            "shield": "Identifiers and money are replaced by placeholders before any model call; the model never sees raw values.",
            "linked_employee": bool(request.headers.get("x-employee-id")),
            "note": None if cfg["features"].get("qa", True) else "AI Q&A is switched off for your company (AI settings)."}


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []
    session_id: str | None = None                            # HR Desk session (phase 2): the UI sends it back so turns stay in one session


@router.post("/assistant/chat")
async def assistant_chat(body: ChatIn, request: Request):
    org, uid = _ids(request)
    ctx = _ctx(request)
    role = ctx.role
    msg = (body.message or "").strip()[:2000]
    if not msg:
        raise HTTPException(422, "Say something first")
    cfg = await CFG.load(org)
    today = await org_today(org)
    note, result, cap = "", None, await _cap(org, cfg)
    key, key_src = CFG.resolve_key(cfg)
    actions_on = bool(cfg["features"].get("actions", True))
    # ── HR Desk first (phase 2, P2-C): hard router → pay change → policy (cited) → else the tool assistant below ──
    from desk import dispatch as DK
    desk_client = LLM.client_for(key) if (key and CFG.sdk_available() and cfg["features"].get("qa", True) and not cap["blocked"]) else None
    try:
        desk_res = await DK.dispatch(ctx, msg, body.history or [], session_id=body.session_id, cfg=cfg, client=desk_client, today=today,
                                     org_name=await directory.org_name(org))
    except Exception as e:                                   # noqa: BLE001 — the desk never blocks the chat; the tool assistant answers
        desk_res = None
        note = f"HR Desk unavailable ({type(e).__name__}); answered by the assistant."
    if desk_res is not None:
        cost = await DK.meter(org, uid, ctx, desk_res, cfg, key_src)
        try:
            sh = Shield()
            async with pm.session() as s:
                s.add(CopilotAudit(org_id=org, user_id=uid, user_email=ctx.user_email or None, role=role, mode=f"desk:{desk_res.get('mode')}", model=desk_res.get("model"),
                                   prompt_shielded="" if desk_res.get("kind") == "hard_route" else sh.protect(msg),      # a routed turn stores its class only
                                   reply_shielded="" if desk_res.get("kind") == "hard_route" else sh.protect(desk_res.get("reply") or ""),
                                   tool_calls=[{"tool": "hr_desk", "lane": desk_res.get("lane"), "kind": desk_res.get("kind")}], masked_count=sh.masked,
                                   input_tokens=(desk_res.get("usage") or {}).get("input_tokens", 0), output_tokens=(desk_res.get("usage") or {}).get("output_tokens", 0),
                                   cache_read_tokens=(desk_res.get("usage") or {}).get("cache_read_tokens", 0), cache_write_tokens=(desk_res.get("usage") or {}).get("cache_write_tokens", 0),
                                   cost_usd=(cost or {}).get("cost_usd") or 0.0, error=None))
                await s.commit()
        except Exception:                                    # noqa: BLE001
            pass
        return DK.reply_payload(desk_res, cost=cost, cap=await _cap(org, cfg), note=note)
    if not cfg["features"].get("qa", True):
        note = "AI Q&A is switched off for your company; answered by rules instead."
    elif not (key and CFG.sdk_available()):
        note = CFG.NOT_CONFIGURED
    elif cap["blocked"]:
        note = f"Monthly AI cap reached ({cap['used_tokens']:,} tokens); rules-based answers only until next month."
    else:
        country = "CA"                                            # Lexora HRM is Canada-only
        try:
            result = await AS.answer(ctx, msg, body.history or [], client=LLM.client_for(key), model=cfg["model"], effort=cfg["effort"], role=role,
                                     country=country, today=today, org_name=await directory.org_name(org), allow_actions=actions_on)
            cr, cw = result.get("cache_read_tokens", 0), result.get("cache_write_tokens", 0)
            cost = MT.cost_of(result["model"], result["input_tokens"], result["output_tokens"], cfg["prices"], cfg["usd_cad_rate"],
                              cache_read_tokens=cr, cache_write_tokens=cw)
            await MT.record(org, user_id=uid, user_email=ctx.user_email or None, channel="chat", feature="assistant_chat", model=result["model"],
                            key_source=key_src, input_tokens=result["input_tokens"], output_tokens=result["output_tokens"], cost=cost,
                            cache_read_tokens=cr, cache_write_tokens=cw)
            result["cost"] = cost
            if cap["warn"]:
                note = f"Heads-up: {cap['pct']}% of this month's AI cap is used."
        except LLM.LlmError as e:
            note = f"The model call failed ({e.kind}: {e.detail}); answered by rules instead."
    if result is None:
        tools = CP.Tools(ctx.headers, [])
        reply, calls = await CP.rules_answer(msg, tools, today)
        sh = Shield()
        for p in tools.pending:                               # the rules path proposes through the same card flow
            ctx.proposals.append(ACT._card("apply_leave", "Apply " + p["summary"], ctx.user_email or "you",
                                           "A leave request goes to your manager for approval.", p["payload"], "/leave"))
        result = {"reply": reply, "reply_shielded": sh.protect(reply), "prompt_shielded": sh.protect(msg), "citations": [], "cited_ids": [],
                  "tool_calls": [{**c, "args": sh.protect_value(c.get("args") or {})} for c in calls], "masked": sh.masked, "input_tokens": 0,
                  "output_tokens": 0, "model": None, "mode": "rules", "cost": {"cost_usd": 0.0, "cost_cad": 0.0, "note": ""}}
    cards = await ACT.persist(ctx, ctx.proposals)
    try:
        async with pm.session() as s:
            s.add(CopilotAudit(org_id=org, user_id=uid, user_email=ctx.user_email or None, role=role, mode=result["mode"], model=result.get("model"),
                               prompt_shielded=result.get("prompt_shielded", ""), reply_shielded=result.get("reply_shielded", ""),
                               tool_calls=result.get("tool_calls", []), masked_count=result.get("masked", 0), input_tokens=result.get("input_tokens", 0),
                               output_tokens=result.get("output_tokens", 0), cache_read_tokens=result.get("cache_read_tokens", 0),
                               cache_write_tokens=result.get("cache_write_tokens", 0),
                               cost_usd=(result.get("cost") or {}).get("cost_usd") or 0.0, error=note[:300] or None))
            await s.commit()
    except Exception:                                        # noqa: BLE001 — the audit row must not break the answer
        pass
    # the tool assistant's turn still lives on the desk session (metrics, run, value ledger); thumbs + Ask-a-person work the same
    ext = await DK.record_tools_turn(ctx, msg, result, session_id=body.session_id)
    return {"reply": result["reply"], "mode": result["mode"], "note": note, "citations": result.get("citations", []), "cited_ids": result.get("cited_ids", []),
            "actions": cards, "tools_used": [c["tool"] for c in result.get("tool_calls", [])], "masked": result.get("masked", 0),
            "refusal": result.get("refusal"), "usage": {"input_tokens": result.get("input_tokens", 0), "output_tokens": result.get("output_tokens", 0),
                                                        "cache_read_tokens": result.get("cache_read_tokens", 0),
                                                        "cache_write_tokens": result.get("cache_write_tokens", 0),
                                                        "model": result.get("model"), **(result.get("cost") or {})},
            "cap": await _cap(org, cfg),
            "desk": ({"session_id": ext.get("session_id"), "turn_id": ext.get("turn_id"), "run_id": ext.get("run_id"), "kind": "tools", "lane": "tools",
                      "mode": result["mode"], "citations": [], "ask_human": True, "ask_hr": None, "addon": ext.get("addon"), "disclosure": ext.get("disclosure"),
                      "csat": ext.get("csat"), "ticket": None, "ic_referral": False, "hard_class": None, "structure": None} if ext else None)}


@router.get("/assistant/tools")
async def assistant_tools(request: Request):
    _ids(request)
    cfg = await CFG.load(request.headers.get("x-org-id"))
    scopes = {"read", "act"} if cfg["features"].get("actions", True) else {"read"}
    return {"tools": TR.mcp_tool_dicts(TR.visible_tools(_role(request), scopes)),
            "confirmations": ACT.CONFIRMATIONS}


@router.get("/assistant/actions")
async def assistant_actions(request: Request, limit: int = 50):
    _ids(request)
    return {"items": await ACT.mine(_ctx(request), min(limit, 200))}


@router.post("/assistant/actions/{action_id}/confirm")
async def assistant_confirm(action_id: str, request: Request):
    _ids(request)
    try:
        return await ACT.confirm(_ctx(request), action_id)
    except ACT.ActionError as e:
        raise HTTPException(e.status, e.message)


@router.post("/assistant/actions/{action_id}/cancel")
async def assistant_cancel(action_id: str, request: Request):
    _ids(request)
    try:
        return await ACT.cancel(_ctx(request), action_id)
    except ACT.ActionError as e:
        raise HTTPException(e.status, e.message)


# ══════════════════════════ settings ═════════════════════════════════════════════

@router.get("/settings")
async def settings_get(request: Request):
    org, _ = _ids(request)
    _admin(request)
    cfg = await CFG.load(org)
    cfg.pop("_key_enc", None)
    return {**cfg, "status": CFG.status(await CFG.load(org)), "platform_key": bool(CFG.platform_key()), "cap": await _cap(org, cfg)}


@router.put("/settings")
async def settings_put(body: dict, request: Request):
    org, _ = _ids(request)
    _admin(request)
    if body.get("model") and body["model"] not in MT.MODELS and not str(body["model"]).startswith("claude-"):
        raise HTTPException(422, "Model must be a Claude model id")
    out = await CFG.save(org, body or {}, request.headers.get("x-user-email"))
    await ACT._audit(ACT._Req(dict(request.headers)), "ai.settings.update", entity_type="ai_settings", entity_id=org,
                     summary="AI settings changed" + (" (API key set)" if body.get("api_key") else " (API key removed)" if body.get("clear_key") else ""),
                     meta={"fields": sorted(k for k in body if k != "api_key"), "via": "ui"})
    return out


@router.post("/settings/test-key")
async def settings_test_key(body: dict, request: Request):
    """Try ONE tiny call with the given key (or the saved one). Metered like any other call."""
    org, uid = _ids(request)
    _admin(request)
    cfg = await CFG.load(org)
    key, src = ((body or {}).get("api_key") or "").strip(), "typed"
    if not key:
        key, src = CFG.resolve_key(cfg)
    if not key:
        return {"ok": False, "message": "No key to test — paste one or set a platform key."}
    if not CFG.sdk_available():
        return {"ok": False, "message": "The anthropic SDK is not installed on this server."}
    model = (body or {}).get("model") or cfg["model"]
    try:
        r = await LLM.ping(LLM.client_for(key), model)
    except LLM.LlmError as e:
        return {"ok": False, "message": f"Key test failed — {e.kind}: {e.detail}", "key_source": src}
    cost = MT.cost_of(r["model"], r["input_tokens"], r["output_tokens"], cfg["prices"], cfg["usd_cad_rate"])
    await MT.record(org, user_id=uid, user_email=request.headers.get("x-user-email"), channel="chat", feature="key_test", model=r["model"],
                    key_source=src if src != "typed" else "org", input_tokens=r["input_tokens"], output_tokens=r["output_tokens"], cost=cost)
    return {"ok": True, "message": f"Key works — {r['model']} answered.", "key_source": src, **r, **cost}


# ══════════════════════════ AI access tokens (MCP) ══════════════════════════════

class TokenIn(BaseModel):
    name: str
    user_id: str
    user_email: str | None = None
    role: str = "employee"
    employee_id: str | None = None
    scopes: list[str] = ["read"]
    days: int | None = None
    rate_per_min: int | None = None


@router.get("/tokens")
async def tokens_list(request: Request):
    org, _ = _ids(request)
    _admin(request)
    return {"items": await TK.list_for_org(org), "scopes": list(TK.SCOPES), "default_days": TK.DEFAULT_DAYS}


@router.post("/tokens", status_code=201)
async def tokens_create(body: TokenIn, request: Request):
    org, _ = _ids(request)
    _admin(request)
    if not body.user_id.strip():
        raise HTTPException(422, "Pick the user this token acts as")
    role = (body.role or "employee").lower()
    if role == "superadmin":
        raise HTTPException(422, "A token cannot act as superadmin")
    raw, row = await TK.create(org, name=body.name, user_id=body.user_id.strip(), user_email=body.user_email, role=role, employee_id=body.employee_id,
                               scopes=body.scopes, days=body.days, rate_per_min=body.rate_per_min, created_by=request.headers.get("x-user-email"))
    await ACT._audit(ACT._Req(dict(request.headers)), "ai.token.create", entity_type="ai_access_token", entity_id=row["id"],
                     summary=f"AI access token '{row['name']}' issued for {row.get('user_email') or row['user_id']} ({', '.join(row['scopes'])})",
                     meta={"scopes": row["scopes"], "role": row["role"], "expires_at": pm.iso(row["expires_at"]), "via": "ui"})
    return {"token": raw, "item": TK.public(row), "note": "Copy the token now — it is not shown again."}


@router.delete("/tokens/{token_id}")
async def tokens_revoke(token_id: str, request: Request):
    org, _ = _ids(request)
    _admin(request)
    if not await TK.revoke(org, token_id):
        raise HTTPException(404, "Token not found")
    await ACT._audit(ACT._Req(dict(request.headers)), "ai.token.revoke", entity_type="ai_access_token", entity_id=token_id, summary="AI access token revoked",
                     meta={"via": "ui"})
    return {"ok": True}


@router.get("/tokens/{token_id}/calls")
async def tokens_calls(token_id: str, request: Request, limit: int = 100):
    org, _ = _ids(request)
    _admin(request)
    async with pm.session() as s:
        rows = (await s.execute(select(AiToolCall).where(AiToolCall.org_id == org, AiToolCall.token_id == token_id)
                                .order_by(AiToolCall.created_at.desc()).limit(min(limit, 500)))).scalars().all()
    return {"items": [{"id": r.id, "tool": r.tool, "args": r.args, "ok": r.ok, "error": r.error, "duration_ms": r.duration_ms, "at": pm.iso(r.created_at)} for r in rows],
            "note": "Arguments are stored shielded (placeholders instead of identifiers and amounts)."}


# ══════════════════════════ metering ═════════════════════════════════════════════

@router.get("/metering")
async def metering_summary(request: Request):
    org, uid = _ids(request)
    cfg = await CFG.load(org)
    cap = await _cap(org, cfg)
    mine = await MT.month_totals(org, uid)
    if not _office(request):
        return {"scope": "me", "me": mine, "cap": cap, "price_source": MT.PRICE_SOURCE, "model": cfg["model"]}
    s = await MT.summary(org)
    return {"scope": "org", "me": mine, "org": await MT.month_totals(org), "cap": cap, "price_source": MT.PRICE_SOURCE, "model": cfg["model"],
            "usd_cad_rate": cfg["usd_cad_rate"], "prices": {m: MT.price_for(m, cfg["prices"]) for m in MT.MODELS}, **s}
