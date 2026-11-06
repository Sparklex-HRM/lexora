"""The Claude-backed assistant: question → shield → Claude + READ tools (as the caller)
→ proposals for ACT tools → re-identify → answer with citations.

What the model sees: ONLY shielded text (identifiers / money → placeholders, see
shield.py). What the person sees: the restored reply, the citations (built here from the
tools' ``source`` fields — never by the model), and the confirmation cards.
"""
from __future__ import annotations

import json
from datetime import date

import claude_client as LLM
import tools_registry as TR
from shield import Shield

MAX_ITERATIONS = 8
MAX_HISTORY = 12


def system_context(role: str, country: str, today: date, org_name: str) -> str:
    """The per-request part of the system prompt (company, date, role). Sent AFTER the cache
    breakpoint so a new day / another company / another role never invalidates the cached
    tools + rules prefix."""
    return (f"Company: {org_name or 'this company'} ({country or 'CA'}). Today is {today.isoformat()}. "
            f"The person you help has the role '{role}'.")


def system_prompt(role: str, country: str, today: date, org_name: str, action_ok: bool) -> str:
    """Whole prompt as one string (rules first, then the context) — what the model sees."""
    return system_static(action_ok) + "\n" + system_context(role, country, today, org_name)


def system_static(action_ok: bool) -> str:
    """The cached part: identical for every request with the same action switch."""
    return (
        "You are the HR assistant inside Lexora HRM. The company, today's date and the role of the person you help "
        "are given at the end of these instructions.\n"
        "Rules:\n"
        "1. Use the tools for every fact. Never invent balances, amounts, dates, names or rules. If a tool returns an error, say so plainly.\n"
        "2. Placeholders like [[MONEY_1]] or [[ID_2]] stand for real figures — repeat them exactly; the system fills them in.\n"
        "3. Cite: every tool result has a 'source_id' like S1. After each fact taken from a result, add its tag in square brackets, e.g. "
        "'You have 7 casual leaves left [S1].' Use only tags you were given.\n"
        "4. Payslip explanations go line by line: the amount, the base it is worked out from, the rule. "
        "Statutory rules marked needs_confirmation must be flagged as not yet verified.\n"
        + ("5. Action tools only PROPOSE. After one, tell the person exactly what the confirmation card says and that nothing happens until they "
           "press Confirm (twice for high-risk). Never claim an action was done.\n" if action_ok else
           "5. You cannot perform actions in this conversation (actions are switched off); explain how to do it in the app.\n")
        + "6. Keep answers short, plain and in the person's language. Do not include internal or system XML tags."
    )


async def answer(ctx: TR.Ctx, message: str, history: list[dict], *, client, model: str, effort: str, role: str, country: str, today: date,
                 org_name: str, allow_actions: bool = True) -> dict:
    sh = Shield()
    scopes = set(ctx.scopes) if allow_actions else (set(ctx.scopes) - {"act"})
    specs = TR.visible_tools(role, scopes)
    tools = TR.claude_tools(specs)
    msgs = [{"role": m["role"], "content": sh.protect(str(m.get("content") or ""))} for m in (history or [])
            if m.get("role") in ("user", "assistant") and m.get("content")][-MAX_HISTORY:]
    if msgs and msgs[0]["role"] != "user":
        msgs = msgs[1:]
    msgs.append({"role": "user", "content": sh.protect(message)})
    system = system_static(allow_actions and "act" in scopes)
    context = system_context(role, country, today, org_name)

    calls, citations, in_tok, out_tok, used_model = [], [], 0, 0, model
    usage: dict = {}
    reply, refusal, stop = "", None, None
    for _ in range(MAX_ITERATIONS):
        # tools + static rules = cached prefix; context after it; automatic caching moves a
        # second breakpoint to the end of the conversation so each tool hop reads the last one.
        res = await LLM.complete(client, model=model, system=system, system_dynamic=context, messages=msgs, tools=tools,
                                 effort=effort, auto_cache=True)
        LLM.add_usage(usage, res)
        in_tok += res["input_tokens"]; out_tok += res["output_tokens"]; used_model = res["model"]; stop = res["stop_reason"]
        if res["refusal"]:
            refusal = res["refusal"]
            reply = "I can't help with that request."
            break
        uses = LLM.tool_uses(res["content"])
        if stop == "tool_use" and uses:
            msgs.append({"role": "assistant", "content": res["content"]})
            results = []
            for u in uses:
                args = sh.restore_value(dict(getattr(u, "input", None) or {}))
                out = await TR.call(ctx, u.name, args)
                ok = "error" not in out
                calls.append({"tool": u.name, "args": sh.protect_value(args), "ok": ok})
                source = out.pop("source", None) if isinstance(out, dict) else None
                if ok and source:
                    citations.append({"id": f"S{len(citations) + 1}", "tool": u.name, **source})
                    out = {"source_id": citations[-1]["id"], **out}
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(sh.protect_value(out), default=str), "is_error": not ok})
            msgs.append({"role": "user", "content": results})
            continue
        reply = LLM.text_of(res["content"])
        if stop == "max_tokens" and reply:
            reply += "\n\n(The answer was cut short — ask a narrower question.)"
        break
    shielded = reply or "I could not finish that — try rephrasing."
    used_tags = {t for t in __import__("re").findall(r"\[(S\d+)\]", shielded)}
    return {"reply": sh.restore(shielded), "reply_shielded": shielded, "prompt_shielded": sh.protect(message), "citations": citations,
            "cited_ids": sorted(used_tags), "tool_calls": calls, "masked": sh.masked, "input_tokens": in_tok, "output_tokens": out_tok,
            "cache_read_tokens": usage.get("cache_read_tokens", 0), "cache_write_tokens": usage.get("cache_write_tokens", 0),
            "model": used_model, "mode": "live", "refusal": refusal, "stop_reason": stop}
