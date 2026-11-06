"""G6 (2026-10-23) — one internal completion endpoint other Lexora HRM services use for small,
advisory AI jobs (resume skills extraction, job-description drafting, candidate-to-job fit).

  POST /ai/internal/complete      X-Internal-Auth only (the gateway strips it from browsers)
       {org_id, feature, system, prompt, max_tokens}
       → {ok: true, text, masked, usage} | {ok: false, reason, message}

The same guard rails as the assistant:
  * the org's key → the platform key → none ("not configured" is an honest answer, the
    caller falls back to its deterministic rules);
  * the org's AI switch (features.qa) and its monthly token / C$ cap (hard cap blocks);
  * the de-identify Shield masks e-mails, phones, SINs, postal codes, money and long numbers in the
    prompt BEFORE the call, and puts real values back in the answer;
  * output capped at 1,500 tokens (callers ask for less), low effort;
  * every call metered (feature = "<caller>.<feature>").
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, HTTPException, Request

sys.path.append("/app")
from shared.utils.internal import is_internal  # noqa: E402

import ai_config as CFG  # noqa: E402
import claude_client as LLM  # noqa: E402
import metering as MT  # noqa: E402
from shield import Shield  # noqa: E402

router = APIRouter(prefix="/ai/internal", tags=["ai-internal"])
MAX_OUT = 1500
MAX_PROMPT_CHARS = 24000
FEATURES = {"hiring.resume_parse", "hiring.jd_draft", "hiring.match"}


@router.post("/complete")
async def internal_complete(body: dict, request: Request):
    if not is_internal(request.headers):
        raise HTTPException(404, "Not found")
    org = str(body.get("org_id") or "").strip()
    feature = str(body.get("feature") or "").strip()
    if not org or feature not in FEATURES:
        raise HTTPException(422, "org_id and a known feature are required")
    cfg = await CFG.load(org)
    if not cfg["features"].get("qa", True):
        return {"ok": False, "reason": "switched_off", "message": "AI is switched off for this company (AI settings)."}
    key, key_src = CFG.resolve_key(cfg)
    if not (key and CFG.sdk_available()):
        return {"ok": False, "reason": "not_configured", "message": CFG.NOT_CONFIGURED}
    tot = await MT.month_totals(org)
    cap = MT.cap_status(tot["tokens"], tot["cost_cad"], cfg["monthly_cap_tokens"], cfg["monthly_cap_cad"], cfg["cap_mode"])
    if cap["blocked"]:
        return {"ok": False, "reason": "cap_reached", "message": "This month's AI cap is reached."}
    sh = Shield()
    prompt = sh.protect(str(body.get("prompt") or "")[:MAX_PROMPT_CHARS])
    system = str(body.get("system") or "")[:4000]
    try:
        max_tokens = max(64, min(int(body.get("max_tokens") or 800), MAX_OUT))
    except (TypeError, ValueError):
        max_tokens = 800
    try:
        r = await LLM.complete(LLM.client_for(key), model=cfg["model"], system=system,
                               messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens, effort="low")
    except LLM.LlmError as e:
        return {"ok": False, "reason": e.kind, "message": "The AI call failed; deterministic rules were used instead."}
    cr, cw = r.get("cache_read_tokens", 0), r.get("cache_write_tokens", 0)
    cost = MT.cost_of(r["model"], r["input_tokens"], r["output_tokens"], cfg["prices"], cfg["usd_cad_rate"],
                      cache_read_tokens=cr, cache_write_tokens=cw)
    try:
        await MT.record(org, user_id=request.headers.get("x-user-id"), user_email=None, channel="internal", feature=feature,
                        model=r["model"], key_source=key_src, input_tokens=r["input_tokens"], output_tokens=r["output_tokens"], cost=cost,
                        cache_read_tokens=cr, cache_write_tokens=cw)
    except Exception:                                        # noqa: BLE001 — metering must not break the answer
        pass
    if r.get("refusal"):
        return {"ok": False, "reason": "refusal", "message": "The model declined; deterministic rules were used instead."}
    return {"ok": True, "text": sh.restore(LLM.text_of(r["content"])), "masked": sh.masked,
            "usage": {"input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"], "cache_read_tokens": cr,
                      "cache_write_tokens": cw, "model": r["model"]}}


# ── Payroll Auditor (Canada, restored 2026-10-26): the time service's timesheet lock → Payroll Auditor ─────────
@router.post("/agents/attendance-locked")
async def attendance_locked(body: dict, request: Request):
    """X-Internal-Auth only. A timesheet was locked; when EVERY active employee's timesheet of the pay frequency for the
    open pay period is now locked, the Payroll Auditor runs once for the period (trigger attendance_lock, idempotent).
    Runs in the background so the lock call returns at once; the 15-min scheduler poll stays as the net."""
    if not is_internal(request.headers):
        raise HTTPException(404, "Not found")
    org = str(body.get("org_id") or request.headers.get("x-org-id") or "").strip()
    if not org:
        raise HTTPException(422, "org_id is required")
    import asyncio
    import agentic_state as ST
    from shared.utils.org_time import org_today
    from agents.payroll_auditor import inputs as PIN
    from agents.payroll_auditor import run as PRUN
    st = await ST.agentic_state(org)
    if not st.get("enabled"):
        return {"ok": True, "triggered": False, "reason": "agentic add-on off"}
    freq = body.get("pay_frequency") if body.get("pay_frequency") in PIN.PAY_FREQUENCIES else None
    P = await PIN.resolve_period(org, today=await org_today(org), pay_frequency=freq)
    freq = P["pay_frequency"]
    emps = await PIN.employees(org)
    active = {e["id"] for e in emps if e.get("is_active", True) and e.get("status") in ("active", "on_leave") and (e.get("pay_frequency") or freq) == freq}
    _, _, _, att = await PIN.attendance(org, P["period_start"], P["period_end"], active)
    if not (att["tracked"] and att["locked"]):
        return {"ok": True, "triggered": False, "reason": f"{att['locked_count']}/{att['total']} timesheets locked", "period": P["period"]}

    async def _go():
        try:
            await PRUN.on_attendance_locked(org, P["period"])
        except Exception:                                    # noqa: BLE001 — the run row records the failure
            pass
    asyncio.get_running_loop().create_task(_go())
    return {"ok": True, "triggered": True, "period": P["period"]}

