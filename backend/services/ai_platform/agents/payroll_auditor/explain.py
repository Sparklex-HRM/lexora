"""Variance explanation — deterministic linking first, the model may only REPHRASE.

``link_all(findings, inputs)`` walks every PA-05 / PA-06 / PA-16 variance and attaches a ``linked_event`` + a templated
plain-words ``explanation`` when one of these explains it, in this order: joiner · leaver · salary revision effective in
the period · retro pay · unpaid days × day rate · hours worked · one-time earning · TD1 change · bonus (CRA bonus
method) · a CPP / QPP, EI or QPIP maximum reached · the new calendar year (YTD restarted) · gross moved (income tax
follows the income). Anything left is ``unexplained — needs a reason`` and becomes a chase task to payroll (run.py).

``rephrase(client, model, …)`` sends ONE batch of linked explanations to the model with a frozen, cached prefix
(``STATIC_PREFIX`` ≥ 1,024 tokens; the batch is the uncached message) and asks for friendlier sentences as JSON
``{finding_id: sentence}``. ``validate_sentence`` checks every dollar figure / number and every employee reference
in the model's sentence against the item; one foreign figure → that finding keeps the template.
No key / no SDK / refusal → templates.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

sys.path.append("/app")

import claude_client as LLM  # noqa: E402

from . import statutory as SX  # noqa: E402
from .checks import _d, _f, _pct, components, day_rate, money  # noqa: E402

PROMPT_TEMPLATE_ID = "payroll_auditor.rephrase.ca.v1"
MONEY_RE = re.compile(r"(?:\$\s?)?(\d[\d,]*(?:\.\d+)?)")
EMP_RE = re.compile(r"\b([A-Z]{1,6}\d{2,6})\b")
LINKABLE = ("PA-05", "PA-06", "PA-16")


def _in_period(d, start, end) -> bool:
    d = _d(d)
    return bool(d and start <= d <= end)


def _amt(v: float) -> str:
    return money(abs(_f(v)))


def _more_less(delta: float) -> str:
    return "more" if delta > 0 else "less"


def _cap_reached(line: dict, comp: str, stat: dict, province: str | None) -> tuple[bool, float | None]:
    plan = SX.plan_for(province, stat)
    if not plan or not line.get("ytd"):
        return False, None
    y = line["ytd"]
    key, mx = {"CPP": ("cpp", plan["max"]), "CPP2": ("cpp2", plan["max2"]), "EI": ("ei", plan["ei_max"]),
               "QPIP": ("qpip", plan.get("qpip_max"))}.get(comp, (None, None))
    if not key or not mx:
        return False, None
    return _f(y.get(key)) >= mx - 0.01, mx


def link_one(f: dict, inputs: dict) -> tuple[dict | None, str | None]:
    """→ (linked_event, templated explanation) or (None, None)."""
    eid = f.get("employee_id")
    comp = (f.get("component") or "").upper()
    delta = _f(f.get("delta_cad"))
    start, end = inputs["period_start"], inputs["period_end"]
    prev_end = inputs.get("prev_period_end")
    emps = {str(e["id"]): e for e in inputs.get("employees") or []}
    e = emps.get(str(eid)) if eid else None
    cur, prev = (inputs.get("current") or {}).get(str(eid)) or {}, (inputs.get("previous") or {}).get(str(eid)) or {}
    if f.get("code") == "PA-06":
        ev = f.get("linked_event")
        if ev and ev.get("type") == "headcount_change":
            n = int(ev.get("delta") or 0)
            return ev, (f"{f.get('cost_centre')}: headcount moved by {n:+d} since the previous run, which moves the cost-centre gross by "
                        f"{_amt(delta)} ({_f(f.get('delta_pct')):+.1f} %).")
        return None, None
    if not e:
        return None, None
    who = e.get("employee_number") or "the employee"
    label = "net pay" if comp == "NET" else "gross" if comp == "GROSS" else comp
    # 1 joiner
    if _in_period(e.get("hire_date"), start, end):
        return ({"type": "joiner", "id": str(eid), "hire_date": str(_d(e.get("hire_date")))},
                f"{who} started on {_d(e.get('hire_date')).isoformat()}, so this is a part-period first pay ({_amt(delta)} {_more_less(delta)} than the previous run).")
    # 2 leaver
    t = _d(e.get("termination_date"))
    if t and start <= t <= end:
        return ({"type": "exit", "id": str(eid), "lwd": t.isoformat()},
                f"{who} leaves on {t.isoformat()}; this pay runs to the last day only ({_amt(delta)} {_more_less(delta)} than the previous run).")
    # 3 salary revision effective in the period
    for r in inputs.get("revisions") or []:
        if str(r.get("employee_id")) != str(eid) or (r.get("status") or "") not in ("approved", "applied"):
            continue
        eff = _d(r.get("effective_from"))
        if eff and eff <= end and (prev_end is None or eff > prev_end):
            old, new = _f(r.get("old_amount")), _f(r.get("new_amount"))
            pct = f" ({(new - old) / old * 100:+.1f} %)" if old else ""
            return ({"type": "revision", "id": str(r.get("id")), "effective_from": eff.isoformat(), "letter_id": r.get("letter_id")},
                    f"Salary revision effective {eff.isoformat()}: {_amt(old)} → {_amt(new)}{pct}; {label} moves by {_amt(delta)}.")
    ce, pe_ = components(cur), components(prev)
    # 4 retro pay
    retro = sum(v for k, v in ce.items() if k in ("RETRO", "RETROPAY", "ARREARS", "BACKPAY"))
    if delta > 0 and retro > 0 and comp in ("GROSS", "NET", "RETRO", "TAX", "CPP", "EI", "QPIP"):
        return ({"type": "retro", "id": str(eid), "amount": round(retro, 2)},
                f"Retro pay of {_amt(retro)} for a back-dated salary revision is paid this period, lifting {label} by {_amt(delta)}.")
    # 5 unpaid days
    u_now, u_prev = _f(cur.get("unpaid_days")), _f(prev.get("unpaid_days"))
    if abs(u_now - u_prev) >= 0.5 and comp in ("GROSS", "NET", "REG", "CPP", "EI", "QPIP", "TAX"):
        rate = day_rate(cur)
        return ({"type": "unpaid_days", "id": str(eid), "unpaid_days": u_now, "unpaid_days_prev": u_prev, "day_rate": rate},
                f"{u_now:g} unpaid day(s) this period against {u_prev:g} in the previous run, at about {_amt(rate)} a day — about {_amt((u_now - u_prev) * rate)} "
                f"{'less' if u_now > u_prev else 'more'} gross.")
    # 6 hours worked (hourly pay)
    h_now = sum(_f(v) for v in (cur.get("hours") or {}).values())
    h_prev = sum(_f(v) for v in (prev.get("hours") or {}).values())
    if h_now and h_prev and abs(h_now - h_prev) >= 1 and comp in ("GROSS", "NET", "REG", "OT", "CPP", "EI", "QPIP", "TAX"):
        return ({"type": "hours", "id": str(eid), "hours": h_now, "hours_prev": h_prev},
                f"{h_now:g} hours paid this period against {h_prev:g} in the previous run, so {label} moves by {_amt(delta)}.")
    # 7 one-time earning
    for code, amt in ce.items():
        if code in ("GROSS", "NET", "CPP", "CPP2", "EI", "QPIP", "TAX", "PAYROLL_TAX", "PRE_TAX", "AFTER_TAX") or amt <= 0 or pe_.get(code):
            continue
        if code == comp or (comp in ("GROSS", "NET") and abs(amt - abs(delta)) <= max(1.0, abs(delta) * 0.25)):
            return ({"type": "one_time", "id": str(eid), "code": code, "amount": round(amt, 2)},
                    f"A one-time earning {code} of {_amt(amt)} is included this period and was not in the previous run.")
    # 8 TD1 change
    if comp in ("TAX", "NET") and cur.get("td1") and prev.get("td1") and cur["td1"] != prev["td1"]:
        return ({"type": "td1", "id": str(eid), "from": prev["td1"], "to": cur["td1"]},
                f"The TD1 claim amounts changed since the previous run, so income tax moved by {_amt(delta)}.")
    # 9 bonus (CRA bonus method)
    if comp in ("TAX", "NET", "GROSS") and _f(cur.get("bonus_taxable")) > 0:
        return ({"type": "bonus", "id": str(eid), "amount": round(_f(cur.get("bonus_taxable")), 2)},
                f"A bonus of {_amt(cur.get('bonus_taxable'))} is taxed this period by the CRA bonus method, so {label} moves by {_amt(delta)}.")
    # 10 a maximum reached
    if comp in ("CPP", "CPP2", "EI", "QPIP", "NET") and delta != 0:
        for c in ((comp,) if comp != "NET" else ("CPP", "EI", "QPIP")):
            hit, mx = _cap_reached(cur, c, inputs.get("statutory") or {}, cur.get("province") or e.get("province"))
            if hit and (comp != "NET" or delta > 0):
                return ({"type": "maximum_reached", "id": c, "maximum": mx},
                        f"The annual {c} maximum of {_amt(mx)} is reached this period, so the deduction stops and {label} moves by {_amt(delta)}.")
    # 11 new calendar year — YTD restarted
    cp, pp = _d(cur.get("pay_date")), _d(prev.get("pay_date"))
    if cp and pp and cp.year != pp.year and comp in ("CPP", "CPP2", "EI", "QPIP", "NET"):
        return ({"type": "new_year", "id": str(cp.year), "from": pp.year, "to": cp.year},
                f"This is the first pay of {cp.year}: the YTD restarted, so deductions that stopped at last year's maximum start again ({label} moves by {_amt(delta)}).")
    # 12 income tax follows the income
    if comp == "TAX":
        g_now, g_prev = _f(cur.get("gross")), _f(prev.get("gross"))
        dg = _pct(g_prev, g_now)
        if dg is not None and abs(dg) >= 5:
            return ({"type": "income_change", "id": str(eid), "gross": g_now, "gross_prev": g_prev},
                    f"Gross moved {dg:+.1f} % ({_amt(g_prev)} → {_amt(g_now)}); income tax is progressive, so it moves by {_amt(delta)}.")
    return None, None


def link_all(findings: list[dict], inputs: dict) -> list[dict]:
    """Mutates and returns ``findings``: linked variances get {linked_event, explanation, explained_by: template, status: explained};
    unlinked variances get status unexplained. Gaps (missing SIN, open regularisation, duplicate account …) stay OPEN until
    a person fixes them — their linked event is the cause, not a resolution."""
    for f in findings:
        if f["code"] not in LINKABLE:
            f.setdefault("status", "info" if f["severity"] == "info" else "unexplained")
            if not f.get("explanation"):
                f["explanation"], f["explained_by"] = f.get("title"), "template"
            continue
        ev, text = link_one(f, inputs)
        if ev and text:
            f["linked_event"], f["explanation"], f["explained_by"], f["status"] = ev, text, "template", "explained"
        else:
            f["linked_event"] = f.get("linked_event")
            f["explanation"], f["explained_by"], f["status"] = "unexplained — needs a reason", None, "unexplained"
    return findings


# ══════════════════════════ model rephrase (validated) ══════════════════════════

_RULES = """You are the Payroll Auditor's writer for Lexora HRM, a Canadian HR and payroll product. You receive a JSON
batch of pay-variance explanations that a deterministic engine has ALREADY linked to a cause (a joiner, a leaver, a
salary revision, retro pay, unpaid days, hours worked, a one-time earning, a TD1 change, a bonus, an annual CPP / QPP,
EI or QPIP maximum being reached, the first pay of a new calendar year, or a change in gross pay). Your only job is to
rewrite each explanation as one or two friendly, plain-English sentences a payroll checker can read in a hurry.

Hard rules — breaking any one of them makes the sentence unusable and it will be discarded:
1. Never invent, round differently, convert or add any number. Every dollar figure, percentage, day count, hour
   count and date you write must appear character-for-character (digits) in the input item you are rewriting.
2. Never name a person. Refer to the employee only by the employee code given in the item (for example E0017) or as
   "the employee". Never guess a department, a manager, a reason, or a policy.
3. Never add advice, judgement or a recommendation. Do not say whether the change is right or wrong, do not suggest
   an action, do not mention tax law, do not mention other employees.
4. Keep the cause exactly as given. If the item says the cause is a revision effective on a date, the sentence says
   that; it does not become a promotion, a bonus or anything else.
5. Output ONLY a JSON object mapping each item's "id" to its rewritten sentence string. No markdown, no code fences,
   no commentary before or after the JSON, no extra keys.
6. If an item cannot be rewritten without breaking rules 1–4, copy its "template" text unchanged as the value.

Style guide: Canadian English; "$" before amounts exactly as given (Canadian dollars); one idea per sentence; present
tense; no exclamation marks; no emojis; avoid "due to" (prefer "because"); never start with "This"; never use the
words "AI", "model" or "algorithm"; keep each value under 240 characters.

Glossary of the causes you will see (for understanding only — do not add these words unless the item has them):
- joiner: the employee started during the pay period, so the first pay covers part of the period.
- exit: the employee left during the pay period, so pay runs to the last day only.
- revision: a salary revision with an effective date; the new salary replaces the old one.
- retro: retro pay — back pay for earlier periods because a revision was effective in the past.
- unpaid_days: days not paid (unpaid leave or absence), at a day rate.
- hours: an hourly employee was paid for more or fewer hours.
- one_time: a one-off earning line for this period (a bonus, a commission, a vacation payout…).
- td1: the employee changed a TD1 form (federal or provincial claim amount), moving income tax.
- bonus: a bonus taxed by the CRA bonus method this period.
- maximum_reached: the employee reached the annual CPP / QPP, EI or QPIP maximum, so that deduction stops.
- new_year: the first pay of a new calendar year, so the year-to-date totals restarted.
- income_change: gross pay moved, and income tax is progressive.
- headcount_change: a cost centre gained or lost people, moving its total.

Component names you may see: Gross, Net pay, CPP / QPP, CPP2 / QPP2, EI, QPIP, Income tax, Pre-tax deductions,
After-tax deductions, Regular hours, Overtime, Statutory holiday pay, Vacation payout, Retro pay. Use the exact
component label from the item.

Worked example (input item → acceptable output):
  {"id": "f1", "employee": "E0017", "component": "Gross", "template": "E0017 started on 2026-10-12, so this is a part-period first pay ($1,840.00 less than the previous run)."}
  → "E0017 started on 2026-10-12, so the first pay covers part of the period and gross is $1,840.00 lower."
Unacceptable: "E0017 joined mid-October and earned about $1.8k less." (changed figures and date).
Unacceptable: "Marie joined on 2026-10-12 …" (a name).
Unacceptable: "… which looks correct." (judgement).
"""

# The prefix must clear the model's minimum cacheable size (1,024 tokens on the larger models; 4,096 on haiku).
_REFERENCE = "\n".join(
    f"Reference line {i:02d}: pay variance causes are one of joiner, exit, revision, retro, unpaid_days, hours, one_time, td1, "
    f"bonus, maximum_reached, new_year, income_change or headcount_change; a rewritten sentence keeps every digit of the template, "
    f"names no person, adds no judgement, and is returned as the JSON value for the item's id." for i in range(1, 25))
STATIC_PREFIX = _RULES + "\n\n" + _REFERENCE + "\n"


def prefix_hash() -> str:
    return hashlib.sha256(STATIC_PREFIX.encode("utf-8")).hexdigest()


def rough_tokens(text: str) -> int:
    return len(text) // 4


def numbers_in(text: str) -> set[str]:
    """Normalised digit strings (commas / $ stripped, trailing .0 dropped) found in a text."""
    out = set()
    for m in MONEY_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.add(f"{v:g}")
        if "." not in raw:
            out.add(raw)
    for m in re.finditer(r"\d{4}-\d{2}(?:-\d{2})?", text or ""):
        out.add(m.group(0))
    return out


def validate_sentence(sentence: str, item: dict) -> tuple[bool, list[str]]:
    """Every number and every employee code in the sentence must exist in the item's template / structure."""
    allowed_text = " ".join(str(x) for x in (item.get("template"), item.get("employee"), json.dumps(item.get("event") or {})))
    allowed = numbers_in(allowed_text)
    for d in re.findall(r"\d{4}-\d{2}(?:-\d{2})?", allowed_text):
        allowed |= set(d.split("-"))
    bad = [n for n in numbers_in(sentence) if n not in allowed]
    for code in EMP_RE.findall(sentence or ""):
        if code != (item.get("employee") or "") and code not in allowed_text:
            bad.append(code)
    if not sentence or len(sentence) > 400:
        bad.append("length")
    return (not bad), bad


def items_for(findings: list[dict]) -> list[dict]:
    return [{"id": f["_id"] if "_id" in f else f.get("id") or f"{f['code']}:{f.get('employee_id')}:{f.get('component')}",
             "employee": (f.get("employee_ref") or "").split(" · ")[0] or "the employee", "component": f.get("component"),
             "template": f.get("explanation"),
             "event": {k: v for k, v in (f.get("linked_event") or {}).items() if k in ("type", "effective_from", "lwd", "hire_date", "unpaid_days", "amount", "maximum", "from", "to", "hours")}}
            for f in findings if f.get("explained_by") == "template" and f.get("explanation") and f["code"] in LINKABLE]


async def rephrase(client, *, model: str, effort: str, findings: list[dict], org_label: str = "", today: str = "") -> dict:
    """Rewrites the templated explanations in place (explained_by → model) when the model's sentence validates.
    Returns usage + counts; never raises; without a client it is a no-op."""
    items = items_for(findings)
    out = {"attempted": len(items), "accepted": 0, "rejected": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
           "model": None, "fallback_reason": None, "prompt_template_id": PROMPT_TEMPLATE_ID, "prompt_hash": prefix_hash()}
    if not items or client is None:
        out["fallback_reason"] = "no_client" if client is None else "nothing_to_rephrase"
        return out
    dynamic = f"Company: {org_label or 'this company'}. Today: {today or 'n/a'}. Items: {len(items)}."
    try:
        res = await LLM.complete(client, model=model, system=STATIC_PREFIX, system_dynamic=dynamic, effort="low", max_tokens=min(8000, 120 * len(items) + 200),
                                 messages=[{"role": "user", "content": "Rewrite these items. Reply with the JSON object only.\n\n" + json.dumps(items, ensure_ascii=False)}])
    except LLM.LlmError as e:
        out["fallback_reason"] = f"llm:{e.kind}"
        return out
    out["usage"] = {k: int(res.get(k) or 0) for k in out["usage"]}
    out["model"] = res.get("model") or model
    if res.get("refusal"):
        out["fallback_reason"] = "refusal"
        return out
    text = LLM.text_of(res["content"]).strip()
    text = text[text.find("{"): text.rfind("}") + 1] if "{" in text and "}" in text else text
    try:
        mapping = json.loads(text)
    except ValueError:
        out["fallback_reason"] = "not_json"
        return out
    if not isinstance(mapping, dict):
        out["fallback_reason"] = "not_object"
        return out
    by_id = {it["id"]: it for it in items}
    for f in findings:
        fid = f["_id"] if "_id" in f else f.get("id") or f"{f['code']}:{f.get('employee_id')}:{f.get('component')}"
        if fid not in by_id or fid not in mapping:
            continue
        sentence = str(mapping[fid] or "").strip()
        ok, _bad = validate_sentence(sentence, by_id[fid])
        if ok and sentence != by_id[fid]["template"]:
            f["explanation"], f["explained_by"] = sentence, "model"
            out["accepted"] += 1
        else:
            out["rejected"] += 1 if sentence != by_id[fid]["template"] else 0
    return out
