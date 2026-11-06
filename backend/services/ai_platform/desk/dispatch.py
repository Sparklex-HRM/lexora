"""The employee chat path (phase 2, P2-C): EVERY Ask Lexi question goes through the HR Desk first.

Dispatch order (decided 2026-10-25; the first match wins):
  1. hard router        desk.router.classify — sexual harassment / harassment / medical / mental health / grievance / disciplinary /
                        whistle-blowing / legal / salary dispute / another person's data → fixed message, no model, every plan
  2. pay-change intent  "why is my salary less" → deterministic payslip diff; model narrates only with the add-on
  3. policy intent      approved, in-force, ACL-admitted clauses retrieved above the org threshold → grounded cited answer
                        (model with validation, or extractive without a key); an explicit "talk to a person" also stays here
  4. existing tools     anything else (leave balance, holidays, who approves, payslip lines, approvals, actions) → the
                        tool-calling assistant (assistant.answer / rules fallback); its turn is still recorded on the desk
                        session (desk.engine.record_external_turn) so metrics, the run and the value ledger see it
  5. abstain            the desk's abstain template (policy matched but the answer failed validation) or the rules
                        fallback's help text — with "Ask a person" / "Ask HR" always offered

``dispatch`` returns the desk result when steps 1–3 answered, else None (the caller runs step 4 and then calls
``record_tools_turn``). ``reply_payload`` shapes a desk result into the /ai/assistant/chat response.
"""
from __future__ import annotations

import sys

sys.path.append("/app")

import metering as MT  # noqa: E402

from desk import agent as AG  # noqa: E402
from desk import engine as EN  # noqa: E402
from desk import router as RT  # noqa: E402

TOOLS_FIRST_INTENTS = ("pay",)                 # "show my payslip" — a record question, not a policy or a pay change


def plan(question: str) -> dict:
    """Pure: which lane a question takes before any I/O (unit-tested dispatch order)."""
    dec = RT.classify(question)
    intent = EN.intent_of(question)
    if dec.hard and dec.klass:
        return {"lane": "hard_route", "klass": dec.klass, "intent": intent, "wants_human": dec.wants_human}
    if intent == "pay_change":
        return {"lane": "pay_change", "klass": None, "intent": intent, "wants_human": dec.wants_human}
    if dec.wants_human:
        return {"lane": "policy", "klass": None, "intent": intent, "wants_human": True}
    if intent in TOOLS_FIRST_INTENTS:
        return {"lane": "tools", "klass": None, "intent": intent, "wants_human": False}
    return {"lane": "policy", "klass": None, "intent": intent, "wants_human": False}      # policy if a clause matches, else tools


async def dispatch(ctx, question: str, history: list[dict], *, session_id: str | None, cfg: dict, client, today, org_name: str) -> dict | None:
    st = await AG.state(ctx.org_id)
    p = plan(question)
    if p["lane"] == "tools":
        return None
    res = await EN.handle(ctx, question, session_id=session_id, history=history, client=client, model=cfg.get("model") or "", effort=cfg.get("effort") or "low",
                          today=today, org_name=org_name, defer_unmatched=(p["lane"] == "policy" and not p["wants_human"]), agentic=st)
    if res.get("kind") == "defer":
        return None
    res["lane"] = p["lane"]
    res["agentic_state"] = {k: st.get(k) for k in ("enabled", "mode", "addon_purchased", "paused")}
    return res


async def record_tools_turn(ctx, question: str, result: dict, *, session_id: str | None, intent: str | None = None) -> dict | None:
    """After the tool-calling assistant answered: one desk turn on the same session (best effort)."""
    try:
        tools = [c.get("tool") for c in (result.get("tool_calls") or []) if c.get("tool")]
        cited = bool(result.get("citations")) or bool(tools)
        usage = {k: result.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")}
        return await EN.record_external_turn(ctx, question, result.get("reply") or "", session_id=session_id, intent=intent or (tools[0] if tools else "help"),
                                             cited=cited, tools_used=tools, usage=usage, model=result.get("model"))
    except Exception:                                        # noqa: BLE001
        return None


async def meter(org: str, uid: str, ctx, res: dict, cfg: dict, key_src: str | None) -> dict:
    """Metering of a live desk turn (tokens + C$) — same ledger as the assistant (feature hr_desk)."""
    u = res.get("usage") or {}
    cost = {"cost_usd": 0.0, "cost_cad": 0.0, "note": ""}
    if res.get("mode") == "live" and u:
        cost = MT.cost_of(res.get("model"), u.get("input_tokens", 0), u.get("output_tokens", 0), cfg["prices"], cfg["usd_cad_rate"],
                          cache_read_tokens=u.get("cache_read_tokens", 0), cache_write_tokens=u.get("cache_write_tokens", 0))
        try:
            await MT.record(org, user_id=uid, user_email=ctx.user_email or None, channel="chat", feature="hr_desk", model=res.get("model"), key_source=key_src,
                            input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0), cost=cost,
                            cache_read_tokens=u.get("cache_read_tokens", 0), cache_write_tokens=u.get("cache_write_tokens", 0))
        except Exception:                                    # noqa: BLE001
            pass
    return cost


def desk_block(res: dict) -> dict:
    """The ``desk`` object the chat UI renders (citation chips, disclosure, Ask-a-person, thumbs, pay table, fixed style)."""
    return {"session_id": res.get("session_id"), "turn_id": res.get("turn_id"), "run_id": res.get("run_id"), "kind": res.get("kind"),
            "lane": res.get("lane"), "intent": res.get("intent"), "mode": res.get("mode"), "hard_class": res.get("hard_class"),
            "citations": res.get("citations") or [], "confidence": res.get("confidence"), "abstained": bool(res.get("abstained")),
            "abstain_reason": res.get("abstain_reason"), "ticket": res.get("ticket"), "ic_referral": bool(res.get("ic_referral")),
            "handoff_reason": res.get("handoff_reason"), "ask_human": True, "ask_hr": res.get("ask_hr"), "addon": bool(res.get("addon")),
            "disclosure": res.get("disclosure"), "csat": res.get("csat"),
            "structure": res.get("structure") if res.get("kind") == "pay_change" else None, "narrated": res.get("narrated"),
            "fallback_reason": res.get("fallback_reason"), "links": res.get("links") or []}


def reply_payload(res: dict, *, cost: dict, cap: dict, note: str = "") -> dict:
    """A desk result in the /ai/assistant/chat response shape (+ the ``desk`` block)."""
    u = res.get("usage") or {}
    mode = res.get("mode") or "rules"
    return {"reply": res.get("reply") or "", "mode": mode, "note": note, "citations": [], "cited_ids": [], "actions": [], "tools_used": [],
            "masked": 0, "refusal": None,
            "usage": {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0), "cache_read_tokens": u.get("cache_read_tokens", 0),
                      "cache_write_tokens": u.get("cache_write_tokens", 0), "model": res.get("model"), **(cost or {})},
            "cap": cap, "desk": desk_block(res)}
