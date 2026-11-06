"""HR copilot — a tool-based assistant over the product's own APIs, as the caller.

Rules this module keeps
* IDENTITY. Every tool calls a sibling service with the caller's gateway-stamped
  headers, so the person can never see more through the copilot than on the screens.
* SHIELD. The model only ever sees shielded text: the prompt, the history and every
  tool result go through ``shield.Shield`` first; the final reply is restored.
* CONFIRM. A tool that would write (apply leave) only records a PENDING action and
  returns its summary; nothing is written until the person confirms on screen.
* HONEST. Without a key the copilot says so and answers through the rules-based
  intent router using the same tools; the reply is never made up.
* BUDGET. Daily token budgets per company and per person (company settings
  ``ai_copilot``) gate the model path only — rules answers cost nothing.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta

import httpx

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402

import llm  # noqa: E402
from shield import Shield  # noqa: E402

FWD = ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id")
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                             "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


def fwd(headers: dict) -> dict:
    return {k: headers.get(k, "") for k in FWD if headers.get(k)}


async def _get(url: str, headers: dict, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url, headers=fwd(headers), params=params)
            if r.status_code == 200:
                return r.json(), None
            try:
                return None, (r.json().get("detail") or f"HTTP {r.status_code}")
            except Exception:                                # noqa: BLE001
                return None, f"HTTP {r.status_code}"
    except (httpx.ConnectError, httpx.TimeoutException):
        return None, "That service did not answer."


async def _post(url: str, headers: dict, body: dict):
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, headers=fwd(headers), json=body)
            try:
                data = r.json()
            except Exception:                                # noqa: BLE001
                data = {}
            if r.status_code in (200, 201):
                return data, None
            return None, (data.get("detail") if isinstance(data, dict) else None) or f"HTTP {r.status_code}"
    except (httpx.ConnectError, httpx.TimeoutException):
        return None, "That service did not answer."


# ══════════════════════════ tools ══════════════════════════════════════════════

TOOLS = [
    {"name": "get_leave_balance", "description": "The caller's own leave balances for the current year, per leave type.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "apply_leave", "description": "Prepare a leave request for the caller. It is NOT submitted — the person must confirm on screen.",
     "input_schema": {"type": "object", "properties": {
         "leave_type": {"type": "string", "description": "leave type code or name, e.g. VAC or Vacation"},
         "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"},
         "half_day": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["leave_type", "start_date", "end_date"]}},
    {"name": "list_payslips", "description": "The caller's own payslips (month, pay date, link). Amounts are masked.",
     "input_schema": {"type": "object", "properties": {"months": {"type": "integer"}}}},
    {"name": "get_payslip", "description": "Key figures of the caller's payslip for one month (YYYY-MM) with a link to open it.",
     "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}, "required": ["month"]}},
    {"name": "explain_payslip_line", "description": "Explain one line of the caller's pay stub (e.g. CPP, CPP2, QPP, EI, QPIP, income tax, vacation pay) from the stored components.",
     "input_schema": {"type": "object", "properties": {"month": {"type": "string"}, "line": {"type": "string"}}, "required": ["line"]}},
    {"name": "my_pending_approvals", "description": "Approvals waiting for the caller to decide, and the caller's own requests still pending.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "policy_lookup", "description": "Company policy text: working week and hours, approvals, leave types, published policy documents (titles only), country.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}}},
]

LINE_HELP = {
    "regular": "Regular pay is your salary (or hours × rate) for the pay period.",
    "overtime": "Overtime is paid at the rate your province's employment standards set (usually 1.5 times) above the overtime threshold.",
    "vacation": "Vacation pay is a percentage of gross wages (at least 4% in most provinces — 6% after five years in some) paid out or accrued.",
    "stat": "Statutory holiday pay follows your province's rule for the public holidays you are entitled to.",
    "income_tax": "Income tax withheld for the period: federal and provincial tax from the CRA payroll formulas (T4127), using your TD1 claim amounts.",
    "tax": "Income tax withheld for the period: federal and provincial tax from the CRA payroll formulas (T4127), using your TD1 claim amounts.",
    "federal": "Federal income tax from the CRA formula, after your federal TD1 claim amount.",
    "provincial": "Provincial income tax from your province's formula, after your provincial TD1 claim amount (Québec: Revenu Québec).",
    "cpp": "Canada Pension Plan — 5.95% of pensionable earnings between the $3,500 basic exemption and the YMPE, up to the yearly maximum; the employer matches it.",
    "cpp2": "CPP2 — 4% on earnings between the YMPE and the YAMPE, up to the yearly maximum; the employer matches it.",
    "qpp": "Québec Pension Plan — replaces CPP for Québec employment, at the QPP rate up to the yearly maximum.",
    "qpip": "Québec Parental Insurance Plan premium — Québec employees only.",
    "ei": "Employment Insurance — 1.63% of insurable earnings up to the yearly maximum (1.30% in Québec); the employer pays 1.4 times.",
    "rrsp": "Group RRSP contribution deducted at source — it reduces the income tax withheld.",
    "union": "Union dues — deducted under the collective agreement; deductible on your return.",
    "gross": "Gross pay is every earning before deductions.",
    "net": "Net pay is gross pay minus every deduction — the amount deposited to your bank.",
}


class Tools:
    def __init__(self, headers: dict, pending_sink: list):
        self.h = headers
        self.pending = pending_sink                          # the router persists these after the chat

    @property
    def employee_id(self) -> str:
        return self.h.get("x-employee-id") or ""

    async def get_leave_balance(self, **_):
        if not self.employee_id:
            return {"error": "Your login is not linked to an employee record, so there are no leave balances to show."}
        data, err = await _get(f"{settings.LEAVE_URL}/leave/balances", self.h)
        if err:
            return {"error": err}
        rows = []
        for b in data.get("balances") or []:
            rows.append({"leave_type": b.get("leave_type_name") or b.get("leave_type_code"), "code": b.get("leave_type_code"),
                         "allocated": b.get("allocated"), "carried_over": b.get("carried_over"), "used": b.get("used"),
                         "pending": b.get("pending"), "remaining": b.get("remaining", b.get("available"))})
        return {"fiscal_year": data.get("fiscal_year"), "balances": rows, "link": "/leave"}

    async def apply_leave(self, leave_type: str = "", start_date: str = "", end_date: str = "", half_day: bool = False, reason: str = "", **_):
        if not self.employee_id:
            return {"error": "Your login is not linked to an employee record, so leave cannot be requested here."}
        try:
            sd, ed = date.fromisoformat(str(start_date)[:10]), date.fromisoformat(str(end_date or start_date)[:10])
        except ValueError:
            return {"error": "Dates must look like 2026-10-20."}
        if ed < sd:
            return {"error": "The end date is before the start date."}
        bal, err = await _get(f"{settings.LEAVE_URL}/leave/balances", self.h)
        types = [(b.get("leave_type_code"), b.get("leave_type_name")) for b in (bal or {}).get("balances") or []]
        want = (leave_type or "").strip().lower()
        code = next((c for c, n in types if c and (c.lower() == want or (n or "").lower() == want)), None) \
            or next((c for c, n in types if c and (want in (n or "").lower() or want in c.lower())), None)
        if not code:
            return {"error": f"I could not match the leave type '{leave_type}'.", "available_types": [f"{c} — {n}" for c, n in types]}
        name = next((n for c, n in types if c == code), code)
        summary = f"{name} from {sd.isoformat()} to {ed.isoformat()}" + (" (half day)" if half_day else "") + (f" — reason: {reason}" if reason else "")
        payload = {"leave_type_code": code, "start_date": sd.isoformat(), "end_date": ed.isoformat(), "half_day": bool(half_day), "reason": reason or ""}
        self.pending.append({"action": "apply_leave", "payload": payload, "summary": summary})
        return {"needs_confirm": True, "summary": summary, "note": "Nothing has been submitted. The person must press Confirm on screen."}

    async def list_payslips(self, months: int = 6, **_):
        data, err = await _get(f"{settings.PAYROLL_URL}/payroll/payslips", self.h)
        if err:
            return {"error": err}
        rows = sorted((data or []), key=lambda x: x.get("pay_date") or "", reverse=True)[: max(1, min(int(months or 6), 24))]
        return {"payslips": [{"month": (r.get("pay_date") or "")[:7], "pay_date": r.get("pay_date"), "period_end": r.get("period_end"),
                              "gross": r.get("gross"), "net_pay": r.get("net_pay"), "link": f"/my-pay?stub={r.get('stub_id')}"} for r in rows],
                "link": "/my-pay"}

    async def _stub_for(self, month: str | None):
        data, err = await _get(f"{settings.PAYROLL_URL}/payroll/payslips", self.h)
        if err:
            return None, err
        rows = sorted((data or []), key=lambda x: x.get("pay_date") or "", reverse=True)
        if month:
            rows = [r for r in rows if (r.get("pay_date") or "").startswith(month)]
        if not rows:
            return None, f"No payslip for {month}." if month else "No payslip yet."
        d, err = await _get(f"{settings.PAYROLL_URL}/payroll/payslips/{rows[0]['stub_id']}", self.h)
        if err:
            return None, err
        d["_summary"] = rows[0]
        return d, None

    async def get_payslip(self, month: str = "", **_):
        d, err = await self._stub_for(month or None)
        if err:
            return {"error": err}
        s = d["_summary"]
        ded = d.get("deductions") or {}
        out = {"month": (s.get("pay_date") or "")[:7], "pay_date": s.get("pay_date"), "gross": s.get("gross"), "net_pay": s.get("net_pay"),
               "deductions": {k: v for k, v in ded.items() if isinstance(v, (int, float))},
               "link": f"/my-pay?stub={s.get('stub_id')}"}
        struct = (d.get("bases") or {}).get("structure") or {}
        if struct:
            out["components"] = {k: v for k, v in struct.items() if isinstance(v, (int, float))}
        return out

    async def explain_payslip_line(self, line: str = "", month: str = "", **_):
        d, err = await self._stub_for(month or None)
        if err:
            return {"error": err}
        want = (line or "").strip().lower().replace(" ", "_")
        flat = _flatten(d)
        hits = [(k, v) for k, v in flat if want and (want in k.lower() or k.lower().endswith(want))]
        key = next((k for k in LINE_HELP if k in want or want in k), None)
        return {"month": (d["_summary"].get("pay_date") or "")[:7], "line": line,
                "values": [{"where": k, "amount": v} for k, v in hits[:8]],
                "how_it_is_worked_out": LINE_HELP.get(key or "", "No stored rule text for this line — the amount is as recorded on the payslip."),
                "link": f"/my-pay?stub={d['_summary'].get('stub_id')}"}

    async def my_pending_approvals(self, **_):
        inbox, e1 = await _get(f"{settings.APPROVALS_URL}/approvals/inbox", self.h)
        mine, e2 = await _get(f"{settings.APPROVALS_URL}/approvals/mine", self.h)
        out = {"waiting_for_me": [], "my_requests_pending": [], "link": "/approvals"}
        for i in (inbox or {}).get("items") or []:
            out["waiting_for_me"].append({"type": i.get("kind_label"), "title": i.get("title"), "from": i.get("requester_name"), "days_waiting": i.get("age_days")})
        for i in (mine or {}).get("items") or []:
            if i.get("status") == "pending":
                out["my_requests_pending"].append({"type": i.get("kind_label"), "title": i.get("title"), "waiting_on": i.get("waiting_on"), "days_waiting": i.get("age_days")})
        if e1 and e2:
            out["error"] = e1
        return out

    async def policy_lookup(self, question: str = "", **_):
        org = self.h.get("x-org-id", "")
        text = await policy_text(org, self.h)
        q = (question or "").lower()
        words = [w for w in re.findall(r"[a-z]{3,}", q) if w not in ("what", "the", "our", "policy", "how", "many", "does", "are", "is")]
        lines = [ln for ln in text.splitlines() if ln.strip()]
        hits = [ln for ln in lines if any(w in ln.lower() for w in words)] if words else lines
        return {"matching_lines": hits[:25] or ["Nothing in the company settings matches that question."],
                "source": "Company settings and the policy list on this installation. Policy documents are stored by title only — the full text is not on this system.",
                "link": "/company-settings"}

    async def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, name, None)
        if not fn or name.startswith("_") or name not in {t["name"] for t in TOOLS}:
            return {"error": f"Unknown tool {name}"}
        try:
            return await fn(**(args or {}))
        except TypeError as e:
            return {"error": f"Bad arguments: {e}"}


def _flatten(d, prefix="") -> list[tuple[str, float]]:
    out = []
    if isinstance(d, dict):
        for k, v in d.items():
            if k.startswith("_"):
                continue
            out += _flatten(v, f"{prefix}{k}.")
    elif isinstance(d, list):
        for i, v in enumerate(d):
            if isinstance(v, dict) and ("code" in v or "name" in v) and "amount" in v:
                out.append((f"{prefix}{v.get('code') or v.get('name')}", v["amount"]))
            else:
                out += _flatten(v, f"{prefix}{i}.")
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out.append((prefix.rstrip("."), d))
    return out


async def policy_text(org: str, headers: dict) -> str:
    """Plain-text view of the settings the copilot may quote (no personal data)."""
    parts = []
    for grp in ("company", "locale", "work_week", "work_hours", "approvals"):
        v = await get_setting(org, grp, {}) or {}
        if grp == "company":
            parts.append(f"Company: {v.get('legal_name') or v.get('trade_name') or 'not set'}.")
        elif grp == "locale":
            parts.append(f"Country {v.get('country') or '?'}, currency {v.get('currency') or '?'}, financial year starts month {v.get('fy_start_month')}.")
        elif grp == "work_week":
            names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            parts.append("Working days: " + ", ".join(names[i] for i in v.get("working_days", []) if 0 <= i < 7) + ". Weekly off: " +
                         ", ".join(names[i] for i in v.get("weekly_offs", []) if 0 <= i < 7) + ".")
        elif grp == "work_hours":
            parts.append(f"Work hours {v.get('start')} to {v.get('end')}, {v.get('hours_per_day')} hours a day, break {v.get('break_minutes')} minutes.")
        elif grp == "approvals":
            parts.append(f"Approvals are reminded after {v.get('escalation_days')} day(s) waiting.")
    try:
        from watchdogs import rows, ORG
        for p in await rows(f"SELECT name, category, version, effective_date, status FROM ops_policies WHERE org_id = {ORG} AND is_active IS TRUE", {"org": org}):
            parts.append(f"Policy document: {p['name']} ({p.get('category') or 'general'}, version {p.get('version') or '-'}, effective {p.get('effective_date') or '-'}, {p.get('status')}).")
        for t in await rows(f"SELECT name, code FROM master_leave_types WHERE org_id = {ORG}", {"org": org}):
            parts.append(f"Leave type: {t['name']} ({t['code']}).")
    except Exception:                                        # noqa: BLE001
        pass
    return "\n".join(parts)


# ══════════════════════════ rules-based intent router (no key needed) ══════════

def parse_month(msg: str, today: date) -> str | None:
    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", msg)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    low = msg.lower()
    if "last month" in low or "previous month" in low:
        d = (today.replace(day=1) - timedelta(days=1))
        return d.isoformat()[:7]
    if "this month" in low:
        return today.isoformat()[:7]
    for name, num in MONTHS.items():
        if re.search(rf"\b{name}\b", low):
            yr = re.search(r"\b(20\d{2})\b", low)
            y = int(yr.group(1)) if yr else (today.year if num <= today.month else today.year - 1)
            return f"{y}-{num:02d}"
    return None


def parse_dates(msg: str, today: date) -> tuple[str | None, str | None]:
    ds = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", msg)
    if ds:
        return ds[0], (ds[1] if len(ds) > 1 else ds[0])
    low = msg.lower()
    if "day after tomorrow" in low:
        d = (today + timedelta(days=2)).isoformat()
        return d, d
    if "tomorrow" in low:
        d = (today + timedelta(days=1)).isoformat()
        return d, d
    if "today" in low:
        return today.isoformat(), today.isoformat()
    return None, None


def intent_of(msg: str) -> str:
    low = msg.lower()
    if re.search(r"\b(apply|request|book|take|file)\b.*\bleave\b|\bleave\b.*\b(apply|request|book)\b", low):
        return "apply_leave"
    if re.search(r"\bbalance|leave(s)? left|how many .*leave|remaining leave|leave summary", low):
        return "balance"
    if re.search(r"\b(explain|why|what is|what's|understand)\b.*\b(cpp2|cpp|qpp|qpip|ei|tax|vacation|overtime|rrsp|union|deduction|line)\b", low):
        return "explain"
    if re.search(r"payslip|pay slip|pay stub|paystub|my pay\b|net pay|take[- ]home|gross", low):
        return "payslip"
    if re.search(r"pending|approval|waiting|approve", low):
        return "approvals"
    if re.search(r"policy|rule|working (day|hour|week)|work week|office hours|holiday|notice period|probation|leave type", low):
        return "policy"
    return "help"


_GENERIC_WORDS = {"leave", "paid", "unpaid", "without", "pay", "day", "days", "annual", "earned", "special"}

HELP = ("I can show your leave balance, prepare a leave request (you confirm), list or explain your pay stubs, "
        "show what is waiting for your approval, and quote company policy settings. Try: \"my leave balance\", "
        "\"apply vacation 2026-10-20 to 2026-10-21\", \"explain CPP on my September pay stub\", \"what is pending for me?\".")


async def rules_answer(msg: str, tools: Tools, today: date) -> tuple[str, list[dict]]:
    """Rules-based path: pick an intent, call the tool, write the answer in plain words."""
    intent = intent_of(msg)
    calls: list[dict] = []

    async def use(name, **args):
        res = await tools.call(name, args)
        calls.append({"tool": name, "args": args, "ok": "error" not in res})
        return res

    if intent == "balance":
        r = await use("get_leave_balance")
        if r.get("error"):
            return r["error"], calls
        if not r["balances"]:
            return "No leave balances are recorded for you this year.", calls
        lines = [f"- {b['leave_type']}: {b.get('remaining') if b.get('remaining') is not None else _rem(b)} left "
                 f"(allocated {b.get('allocated')}, used {b.get('used')}, pending {b.get('pending')})" for b in r["balances"]]
        return f"Your leave balances for {r.get('fiscal_year')}:\n" + "\n".join(lines), calls
    if intent == "apply_leave":
        sd, ed = parse_dates(msg, today)
        if not sd:
            return "Tell me the dates, for example: apply casual leave 2026-10-20 to 2026-10-21.", calls
        bal = await use("get_leave_balance")
        types = [(b["code"], b["leave_type"]) for b in (bal.get("balances") or [])]
        low = msg.lower()

        def _type_said(code: str, name: str | None) -> bool:
            # the code as a word ("CL"), the full name ("casual leave"), or a DISTINCTIVE first word
            # ("casual", "sick"). A generic first word ("Leave Without Pay" → "leave") would match
            # every sentence and the copilot used to pick that type instead of asking.
            if re.search(rf"\b{re.escape(code.lower())}\b", low):
                return True
            nm = (name or "").lower().strip()
            if nm and nm in low:
                return True
            first = nm.split(" ")[0] if nm else ""
            return bool(first) and first not in _GENERIC_WORDS and re.search(rf"\b{re.escape(first)}\b", low) is not None

        lt = next((c for c, n in types if c and _type_said(c, n)), None)
        if not lt and len(types) == 1:
            lt = types[0][0]
        if not lt:
            return "Which leave type? " + (", ".join(f"{c} ({n})" for c, n in types) or "No leave types are set up for you."), calls
        half = "half" in low
        reason_m = re.search(r"(?:reason|because|for)\s*[:\-]?\s*(.+)$", msg, re.IGNORECASE)
        r = await use("apply_leave", leave_type=lt, start_date=sd, end_date=ed, half_day=half, reason=(reason_m.group(1).strip() if reason_m else ""))
        if r.get("error"):
            return r["error"] + (" Types: " + ", ".join(r.get("available_types", [])) if r.get("available_types") else ""), calls
        return f"Ready to submit: {r['summary']}. Press Confirm to send it to your manager, or Cancel.", calls
    if intent == "payslip":
        month = parse_month(msg, today)
        r = await use("get_payslip", month=month or "")
        if r.get("error"):
            return r["error"], calls
        ded = ", ".join(f"{k.replace('_', ' ')} {v:,.2f}" for k, v in (r.get("deductions") or {}).items() if isinstance(v, (int, float)) and v)
        return (f"Payslip {r['month']} (paid {r['pay_date']}): gross {_m(r['gross'])}, net {_m(r['net_pay'])}." +
                (f" Deductions: {ded}." if ded else "") +
                f" Open it: {r['link']}"), calls
    if intent == "explain":
        month = parse_month(msg, today)
        m = re.search(r"\b(cpp2|cpp|qpp|qpip|ei|income tax|federal|provincial|tax|vacation|overtime|rrsp|union|regular|gross|net)\b", msg.lower())
        line = m.group(1) if m else msg
        r = await use("explain_payslip_line", month=month or "", line=line)
        if r.get("error"):
            return r["error"], calls
        vals = "; ".join(f"{v['where']}: {_m(v['amount'])}" for v in r.get("values") or []) or "no line with that name on the payslip"
        return f"{line.upper()} on your {r['month']} pay stub — {vals}. {r['how_it_is_worked_out']} Open it: {r['link']}", calls
    if intent == "approvals":
        r = await use("my_pending_approvals")
        if r.get("error"):
            return r["error"], calls
        a, b = r["waiting_for_me"], r["my_requests_pending"]
        txt = (f"{len(a)} item(s) waiting for your decision" + (": " + "; ".join(f"{i['type']} — {i['title']} ({i['days_waiting']} d)" for i in a[:6]) if a else "") + ". "
               f"{len(b)} of your own request(s) still pending" + (": " + "; ".join(f"{i['type']} — {i['title']} (with {i['waiting_on']})" for i in b[:6]) if b else "") + ".")
        return txt + " Open: /approvals", calls
    if intent == "policy":
        r = await use("policy_lookup", question=msg)
        return "From the company settings:\n" + "\n".join(f"- {ln}" for ln in r["matching_lines"]) + f"\n({r['source']})", calls
    return HELP, calls


def _rem(b: dict) -> str:
    try:
        return f"{float(b.get('allocated') or 0) + float(b.get('carried_over') or 0) - float(b.get('used') or 0) - float(b.get('pending') or 0):g}"
    except (TypeError, ValueError):
        return "?"


def _m(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


# ══════════════════════════ model path (shielded) ══════════════════════════════

def system_context(role: str, country: str, today: date, org_name: str) -> str:
    """Per-request tail (company, date, role) — sent after the cache breakpoint."""
    return (f"Company: {org_name or 'this company'} ({country}). Today is {today.isoformat()}. "
            f"The person you help has the role '{role}'.")


def system_prompt(role: str, country: str, today: date, org_name: str) -> str:
    """Whole prompt as one string (instructions, then the context) — what the model sees."""
    return SYSTEM_STATIC + "\n" + system_context(role, country, today, org_name)


SYSTEM_STATIC = ("You are the HR copilot inside Lexora HRM. The company, today's date and the role of the person you help are "
            "given at the end of these instructions. Use the tools for every fact; never invent balances, amounts or dates. "
            "Values like [[MONEY_1]] or [[ID_2]] are placeholders for real figures — repeat them exactly as given, the system "
            "fills them in for the person. Keep answers short and plain. For apply_leave, tell the person the request is ready "
            "and they must press Confirm; it is not submitted by you. If a tool returns an error, say so plainly.")


async def model_answer(msg: str, history: list[dict], headers: dict, tools: Tools, role: str, country: str, today: date,
                       org_name: str) -> dict:
    sh = Shield()
    msgs = [{"role": m["role"], "content": sh.protect(m.get("content", ""))} for m in history if m.get("role") in ("user", "assistant")][-12:]
    msgs.append({"role": "user", "content": sh.protect(msg)})
    calls, in_tok, out_tok, cost, model = [], 0, 0, 0.0, llm.model_name()
    cache_r = cache_w = 0
    context = system_context(role, country, today, org_name)
    reply = ""
    for _ in range(6):
        res = await llm.complete(system=SYSTEM_STATIC, system_dynamic=context, messages=msgs, tools=TOOLS)
        in_tok += res["input_tokens"]; out_tok += res["output_tokens"]; cost += res["cost_usd"]; model = res["model"]
        cache_r += int(res.get("cache_read_tokens") or 0); cache_w += int(res.get("cache_write_tokens") or 0)
        uses = llm.tool_uses(res["content"])
        if res["stop_reason"] == "tool_use" and uses:
            msgs.append({"role": "assistant", "content": res["content"]})
            results = []
            for u in uses:
                args = sh.restore_value(dict(u.input or {}))
                out = await tools.call(u.name, args)
                calls.append({"tool": u.name, "args": sh.protect_value(args), "ok": "error" not in out})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(sh.protect_value(out))})
            msgs.append({"role": "user", "content": results})
            continue
        reply = llm.text_of(res["content"])
        break
    shielded_reply = reply or "I could not finish that — try rephrasing."
    return {"reply": sh.restore(shielded_reply), "reply_shielded": shielded_reply, "prompt_shielded": sh.protect(msg),
            "tool_calls": calls, "masked": sh.masked, "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": round(cost, 6),
            "cache_read_tokens": cache_r, "cache_write_tokens": cache_w,
            "model": model, "mode": "live"}
