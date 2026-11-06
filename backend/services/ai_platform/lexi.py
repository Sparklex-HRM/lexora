"""Lexi — the HR & payroll copilot.

A real Claude tool-calling agent (Anthropic SDK, claude-opus-5 by default,
override with LEXI_MODEL) that grounds every answer in the org's live data via
tools. When no ANTHROPIC_API_KEY is configured it degrades gracefully to a
grounded intent responder, so the product works in every environment — and
either way we meter tokens + cost for the economics dashboard.
"""
import json
import os
import sys

import httpx

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.enums import periods_per_year  # noqa: E402

LEXI_MODEL = os.getenv("LEXI_MODEL", "claude-opus-5")

# $ per 1M tokens (input, output) — mirrors the Anthropic price list.
PRICES = {
    "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def price_for(model: str) -> tuple[float, float]:
    return PRICES.get(model, PRICES["claude-opus-5"])


def cost_usd(model: str, in_tok: int, out_tok: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """List price; prompt-cache reads 0.1x input, 5-minute cache writes 1.25x input."""
    pin, pout = price_for(model)
    return round(in_tok / 1e6 * pin + out_tok / 1e6 * pout
                 + (cache_read or 0) / 1e6 * pin * 0.1 + (cache_write or 0) / 1e6 * pin * 1.25, 6)


# The Canadian statutory cheat-sheet so Lexi speaks the right payroll system (the payroll engine's verified 2026 constants).
_FACTS = {
    "CA": ("a Canadian employer",
           "For Canadian payroll use these 2026 CRA figures: CPP 5.95% (YMPE $74,600, basic exemption $3,500, "
           "employee max $4,230.45); CPP2 4% on the YMPE→YAMPE $85,000 band (max $416.00); EI 1.63% (MIE $68,900, "
           "max $1,123.07, employer 1.4×); federal BPA $16,452. Québec employment uses QPP (6.3%) instead of CPP, "
           "EI at 1.30% and the QPIP premium; Québec income tax is withheld under the Revenu Québec formulas."),
}


def _system_static(country: str = "CA") -> str:
    """Cached part — one variant per country, the same for every org and every request."""
    desc, facts = _FACTS.get((country or "CA").upper(), _FACTS["CA"])
    return (
        f"You are Lexi, the AI HR & payroll copilot for {desc}, inside Lexora HRM (the organization is named at "
        "the end of these instructions). "
        "Answer concisely and professionally. Use the tools to ground every factual answer in the org's "
        f"live data — never invent employee names, counts, or dollar figures. {facts} "
        "Pay frequency drives tax annualization. If asked to do something you can't (write "
        "data, run payroll), explain how to do it in the app."
    )


def _system_context(org: str) -> str:
    """Per-request tail after the cache breakpoint."""
    return f"Organization: {org}."


def _system(org: str, country: str = "CA") -> str:
    """Whole prompt as one string (used for the token estimate of the fallback path)."""
    return _system_static(country) + " " + _system_context(org)


# Representative prompt for token-length estimation (fallback path).
SYSTEM = _system("your organization", "CA")

TOOLS = [
    {"name": "get_workforce_summary", "description": "Headcount, breakdowns by department/province/pay-frequency, and annual payroll for the org.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_employees", "description": "List employees with name, number, department, job title, province, pay frequency, pay type and salary.",
     "input_schema": {"type": "object", "properties": {"pay_frequency": {"type": "string", "description": "optional filter: weekly|biweekly|semi_monthly|monthly"}}}},
    {"name": "get_payroll_summary", "description": "Pay-run count, the most recent run, and year-to-date CPP/EI/tax totals for the org.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _fwd(headers: dict) -> dict:
    return {k: headers.get(k, "") for k in ("x-org-id", "x-user-id", "x-user-email", "x-role") if headers.get(k)}


async def _get(url: str, headers: dict) -> list | dict:
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(url, headers=_fwd(headers))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _employees(headers: dict) -> list[dict]:
    return await _get(f"{settings.EMPLOYEE_URL}/employees", headers) or []


def _summary(emps: list[dict]) -> dict:
    by = lambda f: {k: sum(1 for e in emps if (e.get(f) or "—") == k) for k in {(e.get(f) or "—") for e in emps}}
    annual = sum(e.get("annual_salary") or 0 for e in emps)
    return {"headcount": len(emps), "annual_payroll": annual, "monthly_payroll": round(annual / 12, 2),
            "by_department": by("department"), "by_province": by("province_of_employment"),
            "by_pay_frequency": by("pay_frequency")}


async def _tool(name: str, args: dict, headers: dict) -> dict:
    emps = await _employees(headers)
    if name == "get_workforce_summary":
        return _summary(emps)
    if name == "list_employees":
        freq = (args or {}).get("pay_frequency")
        rows = [e for e in emps if not freq or e.get("pay_frequency") == freq]
        return {"employees": [{"name": f"{e['first_name']} {e['last_name']}", "number": e["employee_number"],
                               "department": e.get("department"), "job_title": e.get("job_title"),
                               "province": e.get("province_of_employment"), "pay_frequency": e.get("pay_frequency"),
                               "pay_type": e.get("pay_type"), "annual_salary": e.get("annual_salary")} for e in rows]}
    if name == "get_payroll_summary":
        runs = await _get(f"{settings.PAYROLL_URL}/payroll/runs", headers) or []
        ytd = await _get(f"{settings.PAYROLL_URL}/payroll/ytd?year=2026", headers) or {}
        emp_ytd = ytd.get("employees", []) if isinstance(ytd, dict) else []
        return {"pay_runs": len(runs), "last_run": runs[0] if runs else None,
                "ytd_income_tax": round(sum(e["income_tax"] for e in emp_ytd), 2),
                "ytd_cpp": round(sum(e["cpp"] + e["cpp2"] for e in emp_ytd), 2),
                "ytd_ei": round(sum(e["ei"] for e in emp_ytd), 2)}
    return {"error": f"unknown tool {name}"}


# ── real Claude tool loop ────────────────────────────────────────────────────
async def _claude(message: str, history: list, headers: dict, org: str, country: str = "CA") -> dict | None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import AsyncAnthropic
    except Exception:
        return None
    try:
        import claude_client as _cc
        client = AsyncAnthropic()
        msgs = [{"role": m["role"], "content": m["content"]} for m in history if m.get("role") in ("user", "assistant")]
        msgs.append({"role": "user", "content": message})
        in_tok = out_tok = cache_r = cache_w = 0
        system = _cc.system_blocks(_system_static(country), _system_context(org))
        for _ in range(6):  # bound the tool loop
            # TOOLS + static instructions = cached prefix; automatic caching carries the loop.
            resp = await client.messages.create(
                model=LEXI_MODEL, max_tokens=1024, system=system, tools=TOOLS, messages=msgs,
                extra_body=_cc.with_auto_cache(None))
            u = _cc.usage_fields(resp.usage)
            in_tok += u["input_tokens"]
            out_tok += u["output_tokens"]
            cache_r += u["cache_read_tokens"]
            cache_w += u["cache_write_tokens"]
            if resp.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": resp.content})
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        out = await _tool(b.name, b.input, headers)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)})
                msgs.append({"role": "user", "content": results})
                continue
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return {"reply": text or "…", "model": LEXI_MODEL, "mode": "live",
                    "input_tokens": in_tok, "output_tokens": out_tok, "cache_read_tokens": cache_r, "cache_write_tokens": cache_w,
                    "cost_usd": cost_usd(LEXI_MODEL, in_tok, out_tok, cache_r, cache_w)}
        return {"reply": "I couldn't finish that — try rephrasing.", "model": LEXI_MODEL, "mode": "live",
                "input_tokens": in_tok, "output_tokens": out_tok, "cache_read_tokens": cache_r, "cache_write_tokens": cache_w,
                "cost_usd": cost_usd(LEXI_MODEL, in_tok, out_tok, cache_r, cache_w)}
    except Exception:
        return None


# ── grounded fallback (no LLM key) ───────────────────────────────────────────
async def _fallback(message: str, headers: dict, org: str, country: str = "CA") -> dict:
    msg = message.lower().strip()
    emps = await _employees(headers)
    s = _summary(emps)
    cur = "$"
    region_word = "province"

    def has(*w):
        return any(x in msg for x in w)

    reply = None
    if has("how many", "headcount", "number of employ", "total employ", "team size"):
        prov = ", ".join(f"{v} in {k}" for k, v in s["by_province"].items())
        reply = f"You have {s['headcount']} active employees ({prov})."
    elif has("payroll cost", "monthly payroll", "annual payroll", "cost", "how much", "salary bill"):
        extra = "employer CPP/EI"
        reply = (f"Total annual base payroll is {cur}{s['annual_payroll']:,.0f} (~{cur}{s['monthly_payroll']:,.0f}/month) "
                 f"across {s['headcount']} employees, before {extra}.")
    elif has("weekly", "biweekly", "bi-weekly", "semi", "monthly", "frequency", "paid"):
        for fk, label in (("weekly", "weekly"), ("semi_monthly", "semi-monthly"), ("biweekly", "bi-weekly"), ("monthly", "monthly")):
            if fk.replace("_", "") in msg.replace("-", "").replace(" ", "") or label in msg:
                who = [f"{e['first_name']} {e['last_name']}" for e in emps if e.get("pay_frequency") == fk]
                reply = (f"{len(who)} paid {label} ({periods_per_year(fk)} periods/yr): " + ", ".join(who) + "."
                         if who else f"No employees are currently paid {label}.")
                break
        if reply is None:
            reply = "Pay-frequency mix: " + ", ".join(f"{v} {k.replace('_', '-')}" for k, v in s["by_pay_frequency"].items()) + "."
    elif "cpp2" in msg.replace(" ", ""):
        reply = ("CPP2 (2026): a 4% contribution on pensionable earnings between the YMPE ($74,600) and YAMPE "
                 "($85,000) — max $416.00/yr, reported separately (T4 box 16A).")
    elif has("qpp", "qpip", "quebec", "québec"):
        reply = ("Québec (2026): QPP replaces CPP, EI is 1.30% (Québec rate) and the QPIP premium is withheld; income tax is "
                 "federal (with the 16.5% Québec abatement) plus Québec tax under the Revenu Québec formulas. RL-1 slips go with the T4.")
    elif has("cpp"):
        reply = "Base CPP (2026): 5.95% between the $3,500 exemption and YMPE $74,600 — employee max $4,230.45/yr, matched by the employer."
    elif has("ei", "employment insurance"):
        reply = "EI (2026): 1.63% up to the MIE $68,900 — employee max $1,123.07/yr; the employer pays 1.4× the employee premium."
    elif has("remittance", "cra", "remit"):
        reply = "Each run you remit income tax + CPP (ee+er) + EI (ee + 1.4× er) to the CRA. The Payroll page previews the exact remittance."
    else:
        reply = ("I'm Lexi — I answer questions about your workforce and Canadian payroll. Try: \"how many employees?\", "
                 "\"what's our monthly payroll cost?\", \"who is paid weekly?\", or \"explain CPP2\".")

    # Estimated tokens (chars/4) so the economics dashboard reflects real usage,
    # priced at the configured model's rate and clearly labelled 'estimated'.
    in_tok = max(1, (len(_system(org, country)) + len(message) + sum(len(str(e)) for e in emps)) // 4)
    out_tok = max(1, len(reply) // 4)
    return {"reply": reply, "model": LEXI_MODEL, "mode": "estimated",
            "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost_usd(LEXI_MODEL, in_tok, out_tok)}


async def answer(message: str, history: list, headers: dict, org: str = "your organization",
                 country: str = "CA") -> dict:
    return (await _claude(message, history, headers, org, country)
            or await _fallback(message, headers, org, country))
