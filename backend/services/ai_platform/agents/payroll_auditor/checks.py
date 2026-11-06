"""The deterministic Payroll Auditor checks (Canada): PA-01 … PA-17.

Pure functions: ``run_checks(inputs, th)`` → ``{"findings": [...], "checks": [...]}``. No DB, no network, no model.
``inputs`` is the dict ``inputs.collect`` builds (shape below) — tests build it by hand. Every statutory figure a
check compares against comes from ``inputs["statutory"]`` (the payroll engine's own constants, see statutory.py);
nothing here types a rate, a maximum or a threshold of law.

inputs = {
  "pay_frequency": "biweekly", "period": "biweekly:2026-10-04..2026-10-17", "period_start", "period_end", "pay_date",
  "today", "prev_period", "prev_period_start", "prev_period_end", "prev_pay_date",
  "employees": [{id, employee_number, name, department, cost_center, status, hire_date, termination_date, manager_id,
                 province, pay_frequency, is_active, annual_salary, sin, sin_key, bank_institution, bank_transit,
                 bank_account, bank_last4, td1_federal, td1_provincial, td1_federal_claim, td1_provincial_claim,
                 vacation_percent, cpp_exempt, ei_exempt}],
  "current":  {employee_id: LINE},   # the DRAFT (engine dry run) or the committed run of the period
  "previous": {employee_id: LINE},   # the previous REGULAR run of the same pay frequency
  "excluded": {employee_id: "reason"},
  "unpaid_leave": {employee_id: days},                      # approved UNPAID leave days in the period (leave service)
  "pending_regularisations": [...], "pending_leave": [...], "absent_days": {employee_id: [date, …]},
  "attendance": {"tracked", "locked", "ready", "total", "locked_at"},
  "revisions": [{id, employee_id, effective_from, status, letter_id, old_amount, new_amount}],
  "retro": [{id, employee_id, revision_id, code, amount, status, paid_period_end}],
  "exits": [{employee_id, case_number, lwd, status, settlement_status}],
  "vacation_balance": {employee_id: {accrued, paid, balance}},
  "ytd_before": {employee_id: {cpp, cpp2, ei, qpip, pensionable, insurable}},   # employee_ytd before this pay
  "statutory": statutory.py block, "tieout": {"register": {...}, "gl": {...}}, "timing": {...},
}
LINE = {employee_id, gross, net, province, pay_frequency, pay_date, earnings {CODE: amount}, hours {CODE: hours},
        deductions {cpp, cpp2, ei, qpip, income_tax, payroll_tax, pre_tax, after_tax},
        bases {pensionable, insurable} | None, ytd {cpp, cpp2, ei, qpip, pensionable, insurable} | None,
        td1 {federal, provincial} | None, vacation {percent, vacationable, accrued, paid} | None,
        payable {...} | None, unpaid_days, bonus_taxable, bank_last4, source "run" | "preview"}

Each finding: {code, check, severity critical|warning|info, employee_id, employee_ref, department, cost_centre, component,
prev, curr, delta_cad, delta_pct, rule, threshold, linked_event, evidence_refs [{label, link}], fix_hint, owner_role
employee|manager|hr|payroll, dollars_at_risk, verified (statutory only), title}.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from shared.utils.enums import PAY_FREQUENCY_PERIODS

from agents import statutory_clocks as CLK

from . import statutory as SX

CHECKS = [
    ("PA-01", "Headcount tie-out"), ("PA-02", "Joiner completeness"), ("PA-03", "Leavers · final pay · ROE"),
    ("PA-04", "Unpaid leave vs attendance"), ("PA-05", "Component variance"), ("PA-06", "Cost-centre roll-up"),
    ("PA-07", "Zero / negative net pay"), ("PA-08", "Provincial minimum wage"), ("PA-09", "Duplicate bank account / SIN"),
    ("PA-10", "Retro pay approval"), ("PA-11", "Register = GL journal tie-out"), ("PA-12", "Timing KPIs"),
    ("PA-13", "CPP / QPP · EI · QPIP rates"), ("PA-14", "YTD maximums"), ("PA-15", "YMPE / YAMPE in force"),
    ("PA-16", "Income-tax movement · TD1"), ("PA-17", "Vacation pay accrual"),
]
CHECK_NAME = dict(CHECKS)
RANK = {"critical": 0, "warning": 1, "info": 2}
# a CRITICAL finding of these stops the lock / send-for-approval / ready-to-release gates
BLOCKING_CODES = ("PA-01", "PA-02", "PA-03", "PA-04", "PA-07", "PA-09", "PA-10", "PA-11", "PA-13", "PA-14", "PA-15", "PA-17")
PROVINCES = SX.PROVINCES
RETRO_CODES = ("RETRO", "RETROPAY", "ARREARS", "BACKPAY")
DEDUCTION_COMPONENTS = ("CPP", "CPP2", "EI", "QPIP", "TAX", "PAYROLL_TAX", "PRE_TAX", "AFTER_TAX")
TOTAL_KEYS = ("GROSS", "NET", "CPP", "CPP2", "EI", "QPIP", "TAX")


# ══════════════════════════ helpers ═════════════════════════════════════════════

def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(prev: float, curr: float) -> float | None:
    if abs(prev) < 1e-9:
        return None if abs(curr) < 1e-9 else 100.0
    return round((curr - prev) / abs(prev) * 100, 2)


def money(v) -> str:
    """CAD, en-CA: $1,234.56 (−$1,234.56 for a negative)."""
    v = _f(v)
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def _ref(e: dict | None) -> str | None:
    if not e:
        return None
    return " · ".join(x for x in (e.get("employee_number"), e.get("name")) if x) or e.get("id")


def finding(code: str, severity: str, *, rule: str, employee: dict | None = None, component: str | None = None, prev=None, curr=None,
            threshold: dict | None = None, linked_event: dict | None = None, evidence: list | None = None, fix_hint: str | None = None,
            owner_role: str | None = None, dollars_at_risk: float | None = None, verified: bool | None = None, title: str | None = None,
            department: str | None = None, cost_centre: str | None = None) -> dict:
    p, c = (None if prev is None else round(_f(prev), 2)), (None if curr is None else round(_f(curr), 2))
    delta = round(c - p, 2) if (p is not None and c is not None) else None
    return {"code": code, "check": CHECK_NAME[code], "severity": severity if severity in RANK else "info",
            "employee_id": (employee or {}).get("id"), "employee_ref": _ref(employee),
            "department": department if department is not None else (employee or {}).get("department"),
            "cost_centre": cost_centre if cost_centre is not None else (employee or {}).get("cost_center"),
            "component": component, "prev": p, "curr": c, "delta_cad": delta,
            "delta_pct": _pct(p, c) if (p is not None and c is not None) else None, "rule": rule, "threshold": threshold or {},
            "linked_event": linked_event, "evidence_refs": evidence or [], "fix_hint": fix_hint, "owner_role": owner_role,
            "dollars_at_risk": (round(float(dollars_at_risk), 2) if dollars_at_risk is not None else (abs(delta) if delta is not None else None)),
            "verified": verified, "title": title or rule}


def _emp_link(e: dict | None) -> list[dict]:
    return [{"label": f"Employee {e.get('employee_number') or ''}".strip(), "link": f"/employees/{e.get('id')}"}] if e and e.get("id") else []


def _emps(inputs: dict) -> dict[str, dict]:
    return {str(e["id"]): e for e in inputs.get("employees") or []}


def _freq(inputs: dict) -> str:
    return inputs.get("pay_frequency") or "biweekly"


def _on_frequency(e: dict, inputs: dict) -> bool:
    return (e.get("pay_frequency") or _freq(inputs)) == _freq(inputs)


def _active_in_period(e: dict, start: date, end: date) -> bool:
    """On the rolls for at least one day of the period (the engine's date rules decide the pay)."""
    if e.get("status") == "onboarding" and not _d(e.get("hire_date")):
        return False
    h, t = _d(e.get("hire_date")), _d(e.get("termination_date"))
    if h and h > end:
        return False
    if t and t < start:
        return False
    if e.get("status") == "terminated" and not t:
        return False
    return e.get("status") in ("active", "on_leave", "terminated") or (e.get("status") == "onboarding" and h is not None and h <= end)


def day_rate(line: dict) -> float:
    """The line's gross per payable day (what one more unpaid day costs), from the engine's payable-days block."""
    p = line.get("payable") or {}
    days = _f(p.get("payable_days"))
    return round(_f(line.get("gross")) / days, 2) if days > 0 else 0.0


def components(line: dict) -> dict[str, float]:
    """Per-component amounts of a line: each earning code + gross / net + the deductions."""
    out = {str(k).upper(): _f(v) for k, v in (line.get("earnings") or {}).items()}
    d = line.get("deductions") or {}
    out.update({"GROSS": _f(line.get("gross")), "NET": _f(line.get("net")), "CPP": _f(d.get("cpp")), "CPP2": _f(d.get("cpp2")),
                "EI": _f(d.get("ei")), "QPIP": _f(d.get("qpip")), "TAX": _f(d.get("income_tax")), "PAYROLL_TAX": _f(d.get("payroll_tax")),
                "PRE_TAX": _f(d.get("pre_tax")), "AFTER_TAX": _f(d.get("after_tax"))})
    return out


COMPONENT_LABEL = {"GROSS": "Gross", "NET": "Net pay", "CPP": "CPP / QPP", "CPP2": "CPP2 / QPP2", "EI": "EI", "QPIP": "QPIP",
                   "TAX": "Income tax", "PAYROLL_TAX": "Territorial payroll tax", "PRE_TAX": "Pre-tax deductions",
                   "AFTER_TAX": "After-tax deductions", "REG": "Regular hours", "OT": "Overtime", "STAT": "Statutory holiday pay",
                   "VACP": "Vacation payout", "RETRO": "Retro pay"}


def _years_between(a: date | None, b: date | None) -> float:
    if not a or not b or b < a:
        return 0.0
    yrs = b.year - a.year - ((b.month, b.day) < (a.month, a.day))
    return float(max(yrs, 0))


def _periods(line: dict, inputs: dict) -> int:
    return int(PAY_FREQUENCY_PERIODS.get(line.get("pay_frequency") or _freq(inputs)) or 26)


# ══════════════════════════ PA-01 headcount ═════════════════════════════════════

def check_headcount(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, cur, excl = _emps(inputs), inputs.get("current") or {}, inputs.get("excluded") or {}
    start, end = inputs["period_start"], inputs["period_end"]
    expected = {eid for eid, e in emps.items() if _on_frequency(e, inputs) and _active_in_period(e, start, end)}
    in_run = set(cur)
    for eid in sorted(expected - in_run):
        e = emps[eid]
        reason = excl.get(eid)
        if reason:
            out.append(finding("PA-01", "info", employee=e, rule="An active employee is left out of this run by the engine's date rules",
                               title=f"Not paid this period: {reason}", linked_event={"type": "date_rule", "id": eid, "reason": reason},
                               evidence=_emp_link(e), owner_role="payroll", dollars_at_risk=0))
        else:
            out.append(finding("PA-01", "critical", employee=e, rule="HR headcount must equal payroll headcount — an active employee on this pay frequency is missing from the draft",
                               title="On the rolls but missing from payroll", evidence=_emp_link(e), owner_role="payroll",
                               fix_hint="Check the employee's pay frequency, status and hire date; add them to the run or record why they are left out.",
                               dollars_at_risk=(_f(e.get("annual_salary")) / _periods({}, inputs)) if e.get("annual_salary") else None))
    for eid in sorted(in_run - set(emps)):
        out.append(finding("PA-01", "critical", rule="A pay line with no employee record in HR", title="Paid but not on the rolls (possible ghost employee)",
                           employee={"id": eid}, owner_role="hr", curr=_f((cur.get(eid) or {}).get("net")), prev=0,
                           fix_hint="Find who this line belongs to; a pay stub without an HR record must not be paid."))
    for eid in sorted(in_run & set(emps)):
        e = emps[eid]
        t = _d(e.get("termination_date"))
        if e.get("status") == "terminated" and t and t < start:
            out.append(finding("PA-01", "critical", employee=e, rule="The last day is before the period but the employee is still in the run",
                               title=f"Leaver still being paid (last day {t.isoformat()})", curr=_f(cur[eid].get("net")), prev=0,
                               evidence=_emp_link(e), owner_role="payroll",
                               fix_hint="Remove them from the regular run; pay anything still owed through the final pay (Exit Agent → Run final pay)."))
    return out


# ══════════════════════════ PA-02 joiners ═══════════════════════════════════════

def _bank_gaps(e: dict) -> list[str]:
    gaps = []
    inst, tr = str(e.get("bank_institution") or "").strip(), str(e.get("bank_transit") or "").strip()
    if not (len(inst) == 3 and inst.isdigit()):
        gaps.append("institution number (3 digits)")
    if not (len(tr) == 5 and tr.isdigit()):
        gaps.append("transit number (5 digits)")
    if not e.get("bank_account"):
        gaps.append("account number")
    return gaps


def check_joiners(inputs: dict, th: dict) -> list[dict]:
    out = []
    start, end, today = inputs["period_start"], inputs["period_end"], inputs.get("today") or inputs["period_end"]
    cur = inputs.get("current") or {}
    for e in inputs.get("employees") or []:
        if not _on_frequency(e, inputs):
            continue
        eid = str(e["id"])
        h = _d(e.get("hire_date"))
        is_joiner = (h is not None and start <= h <= end) or (e.get("status") == "onboarding" and h is not None and h <= end)
        prov = (e.get("province") or "").upper()
        if not is_joiner and eid in cur:
            bank = _bank_gaps(e)
            if bank:
                out.append(finding("PA-02", "critical", employee=e, component="BANK", rule="Direct deposit needs the institution, transit and account number",
                                   title=f"Missing {', '.join(bank)}", evidence=_emp_link(e), owner_role="hr", curr=_f(cur[eid].get("net")), prev=None,
                                   fix_hint="Add the direct-deposit details on the employee record (void cheque or bank form) before the pay date."))
            if prov not in PROVINCES:
                out.append(finding("PA-02", "critical", employee=e, component="PROVINCE", rule="The province of employment decides the tax tables and CPP or QPP",
                                   title="Province of employment missing or not a Canadian province / territory", evidence=_emp_link(e), owner_role="hr",
                                   fix_hint="Set the province of employment on the employee record (one of the 13 provinces and territories)."))
            continue
        if not is_joiner:
            continue
        gaps, sev = [], "warning"
        bank = _bank_gaps(e)
        if bank:
            gaps.append("direct deposit (" + ", ".join(bank) + ")")
            sev = "critical"
        if prov not in PROVINCES:
            gaps.append("province of employment")
            sev = "critical"
        clock = None
        if not e.get("sin"):
            clock = CLK.sin_request(h or start, today)
            gaps.append(f"SIN (due {clock['due']})")
            if clock["status"] == "overdue":
                sev = "critical"
        if not e.get("td1_federal"):
            gaps.append("TD1 federal form")
        if not e.get("td1_provincial"):
            gaps.append(f"TD1{prov} provincial form" if prov and prov != "QC" else ("TP-1015.3 (Quebec)" if prov == "QC" else "TD1 provincial form"))
        if gaps:
            out.append(finding("PA-02", sev, employee=e, component="JOINER",
                               rule="Joiner completeness: SIN, direct deposit (institution, transit, account), TD1 federal + provincial, province of employment",
                               title=f"Joiner missing {'; '.join(gaps)}", threshold={"required": ["SIN", "institution", "transit", "account", "TD1 federal", "TD1 provincial", "province"]},
                               linked_event={"type": "joiner", "id": eid, "hire_date": h.isoformat() if h else None, "sin_clock": clock},
                               evidence=_emp_link(e), owner_role="hr",
                               fix_hint=("Complete the employee record before the run. Without TD1 forms the engine uses the basic personal amount "
                                         "(claim code 1); without direct-deposit details the pay cannot be deposited; the SIN is needed for the T4 / RL-1 and the ROE.")))
    return out


# ══════════════════════════ PA-03 leavers · final pay · ROE ═════════════════════

def _period_end_of(day: date, inputs: dict) -> date | None:
    for s, e in ((inputs.get("period_start"), inputs.get("period_end")), (inputs.get("prev_period_start"), inputs.get("prev_period_end"))):
        if s and e and s <= day <= e:
            return e
    return None


def check_leavers(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps = _emps(inputs)
    start, end, today = inputs["period_start"], inputs["period_end"], inputs.get("today") or inputs["period_end"]
    window_start = inputs.get("prev_period_start") or (start - timedelta(days=31))
    vac = inputs.get("vacation_balance") or {}
    cases = {str(x.get("employee_id")): x for x in inputs.get("exits") or []}
    for e in emps.values():                                          # terminated with a last day but no exit case
        t = _d(e.get("termination_date"))
        if t and window_start <= t <= end and str(e["id"]) not in cases and _on_frequency(e, inputs):
            cases[str(e["id"])] = {"employee_id": str(e["id"]), "lwd": t, "case_number": None, "settlement_status": None}
    for eid, x in sorted(cases.items()):
        e = emps.get(eid, {"id": eid})
        lwd = _d(x.get("lwd"))
        if not lwd or lwd > end or lwd < window_start:
            continue
        settled = (x.get("settlement_status") or "").lower() in ("paid", "settled", "closed", "done")
        bal = _f((vac.get(eid) or {}).get("balance"))
        prov = (e.get("province") or "").upper()
        next_pay = inputs.get("pay_date") if lwd >= start else (inputs.get("prev_pay_date") or inputs.get("pay_date"))
        ev = [{"label": f"Exit case {x.get('case_number') or ''}".strip(), "link": "/ai/agents/exit"}] + _emp_link(e)
        if not settled:
            clk = CLK.final_pay(prov, lwd, next_pay, today)
            sev = "critical" if clk["status"] == "overdue" else "warning"
            owed = f" Vacation pay owed: {money(bal)}." if bal > 0.01 else ""
            out.append(finding("PA-03", sev, employee=e, component="FINAL_PAY", rule=clk["label"],
                               title=("Final pay overdue" if clk["status"] == "overdue" else "Final pay due") + f" — last day {lwd.isoformat()}, due by {clk['due']}." + owed,
                               threshold={"final_pay_due": clk["due"], "rule_key": clk["rule_key"], "vacation_owed": round(bal, 2)},
                               linked_event={"type": "exit", "id": x.get("case_number") or eid, "lwd": lwd.isoformat(), "clock": clk},
                               evidence=ev, owner_role="payroll", dollars_at_risk=bal if bal > 0.01 else None, verified=clk["verified"],
                               fix_hint=("Run the final pay (Exit Agent → Run final pay, or Payroll → Final pay): it pays the vacation balance and any "
                                         "extras and returns the ROE draft." + ("" if clk["verified"] else " The provincial final-pay deadline is an estimate — confirm it."))))
        elif bal > 0.01:
            out.append(finding("PA-03", "warning", employee=e, component="VACATION", rule="Vacation pay owed must be paid out with the final pay",
                               title=f"Final pay recorded but {money(bal)} vacation pay is still on the books", prev=0, curr=bal,
                               linked_event={"type": "exit", "id": x.get("case_number") or eid, "lwd": lwd.isoformat()}, evidence=ev,
                               owner_role="payroll", fix_hint="Pay the remaining vacation balance with an off-cycle cheque (VACP) or explain why it is not owed."))
        pe = _period_end_of(lwd, inputs)
        roe = CLK.roe_electronic(pe, today)
        sev = "critical" if roe["status"] == "overdue" else ("warning" if roe["status"] == "due_soon" else "info")
        out.append(finding("PA-03", sev, employee=e, component="ROE", rule=roe["label"],
                           title=f"ROE due by {roe['due'] or 'the 5th day after the pay period ends'} — last day {lwd.isoformat()}",
                           threshold={"roe_due": roe["due"], "rule_key": roe["rule_key"]},
                           linked_event={"type": "roe", "id": eid, "lwd": lwd.isoformat(), "clock": roe},
                           evidence=[{"label": "ROE draft", "link": f"/payroll/roe?employee={eid}"}] + _emp_link(e), owner_role="payroll",
                           dollars_at_risk=0, verified=roe["verified"],
                           fix_hint="Issue the ROE in ROE Web from the draft (Payroll → ROE); Lexora HRM keeps the draft, not the ROE Web submission — confirm it was issued."))
    return out


# ══════════════════════════ PA-04 unpaid leave vs attendance ════════════════════

def check_unpaid_leave(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, cur = _emps(inputs), inputs.get("current") or {}
    start, end = inputs["period_start"], inputs["period_end"]
    leave = {str(k): _f(v) for k, v in (inputs.get("unpaid_leave") or {}).items()}
    reg = defaultdict(list)
    for r in inputs.get("pending_regularisations") or []:
        d = _d(r.get("work_date"))
        if d and start <= d <= end:
            reg[str(r["employee_id"])].append(r)
    pend = defaultdict(list)
    for lv in inputs.get("pending_leave") or []:
        s, e_ = _d(lv.get("start_date")), _d(lv.get("end_date"))
        if s and e_ and s <= end and e_ >= start:
            pend[str(lv["employee_id"])].append(lv)
    absent = {str(k): [_d(x) for x in v if _d(x)] for k, v in (inputs.get("absent_days") or {}).items()}
    for eid, line in sorted(cur.items()):
        e = emps.get(eid, {"id": eid})
        unpaid = _f(line.get("unpaid_days"))
        rate = day_rate(line)
        open_regs, open_leave = reg.get(eid) or [], pend.get(eid) or []
        if unpaid > 0 and open_regs:
            n = min(unpaid, len(open_regs))
            out.append(finding("PA-04", "critical", employee=e, component="UNPAID", prev=0, curr=unpaid,
                               rule="Every unpaid day is checked against pending regularisations — an open request means the day may have been worked",
                               title=f"{unpaid:g} unpaid day(s) while {len(open_regs)} regularisation(s) wait for the manager",
                               threshold={"unpaid_days": unpaid, "day_rate": rate},
                               linked_event={"type": "regularisation_pending", "id": str(open_regs[0].get("id")), "count": len(open_regs), "manager_id": str(open_regs[0].get("manager_id") or "")},
                               evidence=[{"label": f"Regularisation · {r.get('work_date')}", "link": "/attendance?tab=regularizations"} for r in open_regs[:5]] + _emp_link(e),
                               owner_role="manager", dollars_at_risk=round(n * rate, 2),
                               fix_hint=f"The manager approves or rejects the regularisation(s) before the run; each approved day restores about {money(rate)} gross."))
        if unpaid > 0 and open_leave:
            out.append(finding("PA-04", "critical", employee=e, component="UNPAID", prev=0, curr=unpaid,
                               rule="Every unpaid day is checked against leave requests still pending — the request must be decided first",
                               title=f"{unpaid:g} unpaid day(s) with {len(open_leave)} leave request(s) still pending",
                               threshold={"unpaid_days": unpaid, "day_rate": rate},
                               linked_event={"type": "leave_pending", "id": str(open_leave[0].get("id")), "count": len(open_leave), "manager_id": str(open_leave[0].get("manager_id") or "")},
                               evidence=[{"label": f"Leave {lv.get('start_date')} → {lv.get('end_date')}", "link": "/leave?tab=requests"} for lv in open_leave[:5]] + _emp_link(e),
                               owner_role="manager", dollars_at_risk=round(min(unpaid, sum(_f(lv.get("days")) for lv in open_leave) or unpaid) * rate, 2),
                               fix_hint="The manager decides the leave: approved paid leave removes the unpaid days, approved unpaid leave confirms them."))
        lv_days = leave.get(eid, 0.0)
        if not open_regs and not open_leave and abs(lv_days - unpaid) >= 0.5 and (lv_days or unpaid) and line.get("payable") is not None:
            out.append(finding("PA-04", "warning", employee=e, component="UNPAID", prev=lv_days, curr=unpaid,
                               rule="Unpaid days on the pay line should equal the approved unpaid leave in the period (tolerance half a day)",
                               title=f"{unpaid:g} unpaid day(s) on the pay line vs {lv_days:g} day(s) of approved unpaid leave",
                               threshold={"tolerance_days": 0.5, "day_rate": rate},
                               linked_event={"type": "unpaid_leave", "id": eid, "leave_days": lv_days},
                               evidence=[{"label": "Leave requests", "link": "/leave?tab=requests"}] + _emp_link(e), owner_role="payroll",
                               dollars_at_risk=round(abs(lv_days - unpaid) * rate, 2),
                               fix_hint="Compare the leave register with the run's unpaid days for this employee and correct the run input."))
        ab = absent.get(eid) or []
        if ab and unpaid <= 0 and lv_days <= 0 and not open_regs and not open_leave:
            out.append(finding("PA-04", "warning", employee=e, component="UNPAID", prev=len(ab), curr=0,
                               rule="Absent days with no approved leave and no unpaid days on the pay line",
                               title=f"{len(ab)} absent day(s) with no leave and no unpaid days", threshold={"day_rate": rate},
                               linked_event={"type": "attendance", "id": eid, "absent_days": len(ab)},
                               evidence=[{"label": "Attendance days", "link": f"/attendance?employee={eid}"}] + _emp_link(e),
                               owner_role="employee", dollars_at_risk=round(len(ab) * rate, 2),
                               fix_hint="The employee applies for leave or regularises the punches; otherwise payroll records the unpaid days."))
    return out


# ══════════════════════════ PA-05 component variance ════════════════════════════

def check_variance(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, cur, prev = _emps(inputs), inputs.get("current") or {}, inputs.get("previous") or {}
    pct, floor = float(th.get("net_delta_pct") or 5), float(th.get("net_delta_floor_cad") if th.get("net_delta_floor_cad") is not None else 1000)
    ded_floor = float(th.get("deduction_floor_cad") if th.get("deduction_floor_cad") is not None else 25)
    bank_pct = float(th.get("bank_change_pct") or pct / 2)
    for eid, line in sorted(cur.items()):
        p = prev.get(eid)
        if not p:
            continue                                               # a joiner has no previous run — PA-02 covers them
        e = emps.get(eid, {"id": eid})
        changed = bool(p.get("bank_last4") and e.get("bank_last4") and p.get("bank_last4") != e.get("bank_last4"))
        c, q = components(line), components(p)
        if not line.get("earnings") or not p.get("earnings") or line.get("source") != p.get("source"):
            c = {k: v for k, v in c.items() if k in TOTAL_KEYS}
            q = {k: v for k, v in q.items() if k in TOTAL_KEYS}
        for comp in sorted(set(c) | set(q)):
            a, b = q.get(comp, 0.0), c.get(comp, 0.0)
            delta = b - a
            use_pct = bank_pct if changed else pct
            use_floor = 0.0 if changed else (ded_floor if comp in DEDUCTION_COMPONENTS else floor)
            if abs(delta) <= use_floor:
                continue
            dp = _pct(a, b)
            if dp is None or abs(dp) <= use_pct:
                continue
            sev = "critical" if comp in ("NET", "GROSS") and abs(dp) >= 2 * use_pct else "warning"
            out.append(finding("PA-05", sev, employee=e, component=comp, prev=a, curr=b,
                               rule=f"Per-employee, per-component change vs the previous regular run: more than {use_pct:g} % AND more than {money(use_floor)}"
                                    + (" (direct-deposit details changed — tighter rule)" if changed else ""),
                               title=f"{COMPONENT_LABEL.get(comp, comp.title())} moved {dp:+.1f} % ({'+' if delta >= 0 else '-'}{money(abs(delta))})",
                               threshold={"pct": use_pct, "floor_cad": use_floor, "preset": th.get("preset"), "bank_changed": changed},
                               evidence=[{"label": "Previous pay stub", "link": f"/payroll/payslips?employee={eid}"}] + _emp_link(e),
                               owner_role="payroll", fix_hint="Needs a linked reason (joiner, leaver, salary revision, retro pay, unpaid days, hours, one-time earning, TD1 change, a maximum reached) — otherwise a person must explain it."))
    return out


# ══════════════════════════ PA-06 cost-centre roll-up ═══════════════════════════

def check_cost_centre(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, cur, prev = _emps(inputs), inputs.get("current") or {}, inputs.get("previous") or {}
    lim = float(th.get("cost_centre_pct") or 5)
    if not prev:
        return out

    def key(eid):
        e = emps.get(eid) or {}
        return e.get("cost_center") or e.get("department") or "Unassigned"
    agg_c, agg_p, heads_c, heads_p = defaultdict(float), defaultdict(float), defaultdict(int), defaultdict(int)
    for eid, ln in cur.items():
        agg_c[key(eid)] += _f(ln.get("gross"))
        heads_c[key(eid)] += 1
    for eid, ln in prev.items():
        agg_p[key(eid)] += _f(ln.get("gross"))
        heads_p[key(eid)] += 1
    for cc in sorted(set(agg_c) | set(agg_p)):
        a, b = agg_p.get(cc, 0.0), agg_c.get(cc, 0.0)
        dp = _pct(a, b)
        if dp is None or abs(dp) <= lim:
            continue
        hd = heads_c.get(cc, 0) - heads_p.get(cc, 0)
        out.append(finding("PA-06", "warning", component="GROSS", prev=a, curr=b, cost_centre=cc, department=cc,
                           rule=f"A cost-centre gross change above {lim:g} % vs the previous regular run needs a written reason",
                           title=f"{cc}: gross {dp:+.1f} % · headcount {hd:+d}", threshold={"pct": lim},
                           linked_event={"type": "headcount_change", "id": cc, "delta": hd} if hd else None, owner_role="payroll",
                           fix_hint="Write the reason on the pack (joiners / leavers / revisions / retro pay / overtime) before approval."))
    return out


# ══════════════════════════ PA-07 zero / negative net ═══════════════════════════

def check_net(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps = _emps(inputs)
    for eid, ln in sorted((inputs.get("current") or {}).items()):
        n, g = _f(ln.get("net")), _f(ln.get("gross"))
        if n <= 0:
            out.append(finding("PA-07", "critical", employee=emps.get(eid) or {"id": eid}, component="NET", prev=g, curr=n,
                               rule="Zero or negative net pay is a hard exception — the engine refuses to commit a negative net",
                               title="Negative net pay" if n < 0 else "Zero net pay", owner_role="payroll", dollars_at_risk=abs(n) or g,
                               fix_hint="Check the after-tax deductions, garnishments and recoveries; cap them so the employee receives a positive net and carry the rest forward."))
    return out


# ══════════════════════════ PA-08 minimum wage (gap) ════════════════════════════

def check_min_wage(inputs: dict, th: dict) -> list[dict]:
    """No provincial minimum-wage table exists in the Canada payroll code (only a ``min_wage_fn`` hook in
    payroll/revisions_logic.py). The auditor does not invent rates: this check reports itself as not available."""
    table = inputs.get("min_wage") or {}
    out = []
    if not table:
        return out
    emps = _emps(inputs)
    for eid, ln in sorted((inputs.get("current") or {}).items()):
        e = emps.get(eid) or {"id": eid}
        floor = _f(table.get((e.get("province") or "").upper()))
        hrs = sum(_f(v) for k, v in (ln.get("hours") or {}).items() if k in ("REG", "OT", "OT2", "STAT"))
        if floor > 0 and hrs > 0 and _f(ln.get("gross")) / hrs < floor:
            out.append(finding("PA-08", "critical", employee=e, component="GROSS", prev=floor, curr=round(_f(ln.get("gross")) / hrs, 2),
                               rule="Gross per hour worked below the provincial minimum wage (org minimum-wage table)",
                               title=f"{money(_f(ln.get('gross')) / hrs)} an hour is below {money(floor)} ({e.get('province')})", owner_role="hr",
                               fix_hint="Raise the hourly rate to at least the provincial minimum wage."))
    return out


# ══════════════════════════ PA-09 duplicates ════════════════════════════════════

def check_duplicates(inputs: dict, th: dict) -> list[dict]:
    out = []
    cur = inputs.get("current") or {}
    groups = {"bank": defaultdict(list), "SIN": defaultdict(list)}
    for e in inputs.get("employees") or []:
        if e.get("status") == "terminated" and str(e["id"]) not in cur:
            continue
        inst, tr, last4 = (str(e.get(k) or "").strip() for k in ("bank_institution", "bank_transit", "bank_last4"))
        if inst and tr and last4:
            groups["bank"][(inst, tr, last4)].append(e)
        if e.get("sin_key"):
            groups["SIN"][str(e["sin_key"])].append(e)
    for kind, g in groups.items():
        for _key, members in g.items():
            if len(members) < 2:
                continue
            refs = ", ".join(_ref(m) or "" for m in members)
            for m in members:
                if kind == "bank":
                    sev, rule = "warning", "The same institution, transit and last 4 account digits on more than one employee — possible shared or wrong account"
                    hint = "Confirm each employee's own account (void cheque); two people paid into one account is a ghost-employee red flag."
                else:
                    sev, rule = "critical", "The same SIN on more than one employee record — duplicate employee or a wrong SIN"
                    hint = "Confirm which record is right; a shared SIN breaks the T4 / RL-1 and the ROE and may be a duplicate employee."
                out.append(finding("PA-09", sev, employee=m, component=kind, rule=rule,
                                   title=f"{'Bank account' if kind == 'bank' else 'SIN'} shared by {len(members)} employees ({refs})", threshold={"duplicates": len(members)},
                                   linked_event={"type": "duplicate", "id": kind, "with": [str(x["id"]) for x in members if x["id"] != m["id"]]},
                                   evidence=[x for mm in members for x in _emp_link(mm)], owner_role="hr", dollars_at_risk=0, fix_hint=hint))
    return out


# ══════════════════════════ PA-10 retro pay approval ════════════════════════════

def check_retro(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, cur = _emps(inputs), inputs.get("current") or {}
    period_end, prev_end = inputs["period_end"], inputs.get("prev_period_end")
    retro_by = defaultdict(list)
    for r in inputs.get("retro") or []:
        retro_by[str(r.get("employee_id"))].append(r)
    revs = {str(r.get("id")): r for r in inputs.get("revisions") or []}
    for eid, ln in sorted(cur.items()):
        e = emps.get(eid) or {"id": eid}
        for code, amt in (ln.get("earnings") or {}).items():
            if str(code).upper() not in RETRO_CODES or _f(amt) <= 0:
                continue
            props = [r for r in retro_by.get(eid, []) if (r.get("status") or "") in ("accepted", "paid")]
            match = next((r for r in props if abs(_f(r.get("amount")) - _f(amt)) <= 1.0), None) or (props[0] if len(props) == 1 else None)
            if not match:
                out.append(finding("PA-10", "critical", employee=e, component=str(code).upper(), prev=0, curr=_f(amt),
                                   rule="Every retro / back-pay line must carry an approval reference: an accepted retro-pay proposal behind an approved salary revision",
                                   title=f"Retro pay {money(amt)} with no accepted retro-pay proposal", evidence=[{"label": "Salary revisions · retro pay", "link": "/payroll/revisions"}] + _emp_link(e),
                                   owner_role="payroll", linked_event={"type": "retro_line", "id": eid, "code": str(code).upper()},
                                   fix_hint="Post the back-dated salary revision and accept its retro-pay proposal (Payroll → Salary revisions); a hand-entered retro line is not accepted."))
                continue
            rev = revs.get(str(match.get("revision_id")))
            if rev is not None and (rev.get("status") or "") not in ("approved", "applied"):
                out.append(finding("PA-10", "warning", employee=e, component=str(code).upper(), prev=0, curr=_f(amt),
                                   rule="A retro-pay proposal must sit behind an approved salary revision", title=f"Retro pay {money(amt)} — its salary revision is '{rev.get('status')}'",
                                   linked_event={"type": "retro", "id": str(match.get("id")), "revision_id": str(match.get("revision_id"))}, owner_role="payroll",
                                   fix_hint="Approve the salary revision first, or dismiss the retro-pay proposal."))
    for r in inputs.get("revisions") or []:                       # revisions effective this period applied without a letter
        eff = _d(r.get("effective_from"))
        if not eff or (r.get("status") or "") not in ("approved", "applied") or r.get("letter_id"):
            continue
        if eff <= period_end and (prev_end is None or eff > prev_end):
            e = emps.get(str(r.get("employee_id"))) or {"id": r.get("employee_id")}
            out.append(finding("PA-10", "warning", employee=e, component="REVISION", prev=_f(r.get("old_amount")), curr=_f(r.get("new_amount")),
                               rule="A salary revision paid for the first time this period should have its letter issued", title="Revision effective this period without a letter",
                               linked_event={"type": "revision", "id": str(r.get("id")), "effective_from": eff.isoformat()},
                               evidence=[{"label": "Salary revisions", "link": "/payroll/revisions"}], owner_role="hr", dollars_at_risk=0,
                               fix_hint="Generate the revision letter (Letters → Salary revision) before the pay stubs go out."))
    return out


# ══════════════════════════ PA-11 register = GL journal ═════════════════════════

def check_tieout(inputs: dict, th: dict) -> list[dict]:
    out = []
    t = inputs.get("tieout") or {}
    reg, gl = t.get("register") or {}, t.get("gl") or {}
    if not reg:
        return out
    if gl:
        legs = [("Register net pay", _f(reg.get("net")), "GL net pay payable (bank)", gl.get("net_credit"), "register_vs_gl_net"),
                ("Register gross", _f(reg.get("gross")), "GL salaries & wages expense", gl.get("gross_debit"), "register_vs_gl_gross"),
                ("Register income tax", _f(reg.get("income_tax")), "GL income tax payable", gl.get("tax_credit"), "register_vs_gl_tax")]
        for a_label, a, b_label, b, key in legs:
            if b is None:
                continue
            b = _f(b)
            if abs(a - b) > 0.05:
                out.append(finding("PA-11", "critical", component=key, prev=a, curr=b, rule=f"{a_label} must equal {b_label} (to the cent, 5 ¢ rounding tolerance)",
                                   title=f"{a_label} {money(a)} ≠ {b_label} {money(b)}", threshold={"tolerance_cad": 0.05}, owner_role="payroll",
                                   dollars_at_risk=abs(a - b), evidence=[{"label": "GL journal", "link": "/payroll/gl-export"}],
                                   fix_hint="A gap means a stub is missing from one side, a deduction is not mapped to a GL account, or a rounding method differs; fix before release."))
            else:
                out.append(finding("PA-11", "info", component=key, prev=a, curr=b, rule=f"{a_label} = {b_label}", title=f"{a_label} ties to {b_label} ({money(a)})",
                                   owner_role="payroll", dollars_at_risk=0))
        if gl.get("balanced") is False:
            out.append(finding("PA-11", "critical", component="gl_balance", prev=_f(gl.get("total_debit")), curr=_f(gl.get("total_credit")),
                               rule="The payroll GL journal must balance (debits = credits)",
                               title=f"GL journal does not balance: debits {money(gl.get('total_debit'))} vs credits {money(gl.get('total_credit'))}",
                               owner_role="payroll", dollars_at_risk=abs(_f(gl.get("total_debit")) - _f(gl.get("total_credit"))),
                               fix_hint="Usually a deduction with no GL account (for example QPIP or a territorial payroll tax); map it before posting."))
    out.append(finding("PA-11", "info", component="register_vs_bank_file", rule="Register net = direct-deposit file total",
                       title="Bank-file leg not available — Lexora HRM has no direct-deposit (EFT) file to tie to", owner_role="payroll", dollars_at_risk=0,
                       fix_hint="Tie the register net to your bank's deposit report by hand until a direct-deposit file is generated."))
    return out


# ══════════════════════════ PA-12 timing KPIs ═══════════════════════════════════

def check_timing(inputs: dict, th: dict) -> list[dict]:
    out = []
    tm = inputs.get("timing") or {}
    pay, today = inputs.get("pay_date"), inputs.get("today") or inputs["period_end"]
    if not pay:
        return out
    lead, appr_lead = int(th.get("lead_days") or 3), int(th.get("approval_lead_days") or 1)
    freeze_by, approve_by = pay - timedelta(days=lead), pay - timedelta(days=appr_lead)
    att = inputs.get("attendance") or {}
    if att.get("tracked") and not att.get("locked") and today > freeze_by:
        out.append(finding("PA-12", "warning" if today <= approve_by else "critical", component="freeze",
                           rule=f"T-{lead}: inputs (timesheets, master data) frozen {lead} days before the pay date",
                           title=f"Timesheets not locked by T-{lead} ({freeze_by.isoformat()}) — {att.get('ready', 0)}/{att.get('total', 0)} approved",
                           threshold={"lead_days": lead, "freeze_by": freeze_by.isoformat()}, owner_role="hr", dollars_at_risk=0,
                           fix_hint="Approve and lock the remaining timesheets (Time → Timesheets); the agent chases the managers."))
    if tm.get("approved_at") is None and today > approve_by:
        out.append(finding("PA-12", "warning", component="approval", rule=f"T-{appr_lead}: variance review and approval {appr_lead} day(s) before the pay date",
                           title=f"Run not committed by T-{appr_lead} ({approve_by.isoformat()})", threshold={"approval_lead_days": appr_lead}, owner_role="payroll",
                           dollars_at_risk=0, fix_hint="Review the exception grid, then commit the run in Payroll so the pay stubs and deposits go out on time."))
    hrs = float(th.get("explanation_hours") or 4)
    if tm.get("draft_at") and tm.get("unexplained", 0):
        da = tm["draft_at"] if isinstance(tm["draft_at"], datetime) else None
        age_h = (datetime.now(da.tzinfo) - da).total_seconds() / 3600 if da else None
        if age_h is not None and age_h > hrs:
            out.append(finding("PA-12", "warning", component="explanations", prev=hrs, curr=round(age_h, 1), rule=f"Variance explanations within {hrs:g} h of the draft",
                               title=f"{tm['unexplained']} variance(s) still unexplained {age_h:.0f} h after the draft", threshold={"explanation_hours": hrs},
                               owner_role="payroll", dollars_at_risk=0))
    oc, lim = int(_f(tm.get("off_cycle_runs_quarter"))), int(th.get("off_cycle_runs_per_quarter") or 1)
    if oc > lim:
        out.append(finding("PA-12", "info", component="off_cycle", prev=lim, curr=oc, rule=f"Off-cycle runs at most {lim} per quarter (KPI)",
                           title=f"{oc} off-cycle run(s) this quarter", owner_role="payroll", dollars_at_risk=0))
    return out


# ══════════════════════════ PA-13 CPP / QPP · EI · QPIP rates ═══════════════════

def _before(line: dict, key: str, ded_key: str | None = None) -> float:
    """YTD before this pay = YTD after − this pay's amount."""
    y = line.get("ytd") or {}
    if key in ("pensionable", "insurable"):
        return _f(y.get(key)) - _f((line.get("bases") or {}).get(key))
    return _f(y.get(key)) - _f((line.get("deductions") or {}).get(ded_key or key))


def expected_contributions(line: dict, emp: dict, plan: dict, periods: int) -> dict:
    """Recompute the employee's CPP / QPP, CPP2 / QPP2, EI and QPIP for this pay from the engine's own constants, the
    line's pensionable / insurable bases and the YTD before the pay — the same formula calc.py applies."""
    b = line.get("bases") or {}
    pens, ins = _f(b.get("pensionable")), _f(b.get("insurable"))
    y_cpp, y_cpp2, y_ei, y_qpip = _before(line, "cpp"), _before(line, "cpp2"), _before(line, "ei"), _before(line, "qpip")
    pens_before = _before(line, "pensionable")
    if emp.get("cpp_exempt"):
        cpp = cpp2 = 0.0
    else:
        cpp = max(round(min(max(pens - plan["basic_exemption"] / periods, 0.0) * plan["rate"], plan["max"] - y_cpp), 2), 0.0)
        over = max(min(pens_before + pens, plan["yampe"]) - max(pens_before, plan["ympe"]), 0.0) if plan["yampe"] else 0.0
        cpp2 = max(round(min(over * plan["rate2"], plan["max2"] - y_cpp2), 2), 0.0)
    ei = 0.0 if emp.get("ei_exempt") else max(round(min(ins * plan["ei_rate"], plan["ei_max"] - y_ei), 2), 0.0)
    qpip = None
    if plan.get("qpip_rate") is not None:
        qpip = max(round(min(ins * plan["qpip_rate"], plan["qpip_max"] - y_qpip), 2), 0.0)
    return {"cpp": cpp, "cpp2": cpp2, "ei": ei, "qpip": qpip, "pens_before": round(pens_before, 2), "pens_after": round(pens_before + pens, 2)}


def check_rates(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, stat = _emps(inputs), inputs.get("statutory") or {}
    tol = float(th.get("rate_tolerance_cad") if th.get("rate_tolerance_cad") is not None else 1.0)
    for eid, ln in sorted((inputs.get("current") or {}).items()):
        if not ln.get("bases") or not ln.get("ytd"):
            continue
        e = emps.get(eid) or {"id": eid}
        plan = SX.plan_for(ln.get("province") or e.get("province"), stat)
        if not plan:
            continue
        exp = expected_contributions(ln, e, plan, _periods(ln, inputs))
        d = ln.get("deductions") or {}
        legs = [("CPP", plan["label_pension"], _f(d.get("cpp")), exp["cpp"], plan["rate"], (plan["keys"]["rate"], plan["keys"]["max"])),
                ("EI", plan["label_ei"], _f(d.get("ei")), exp["ei"], plan["ei_rate"], (plan["keys"]["ei_rate"], plan["keys"]["ei_max"]))]
        if exp["qpip"] is not None:
            legs.append(("QPIP", "QPIP", _f(d.get("qpip")), exp["qpip"], plan["qpip_rate"], (plan["keys"]["qpip_rate"], plan["keys"]["qpip_max"])))
        for comp, label, actual, expected, rate, keys in legs:
            diff = round(actual - expected, 2)
            if abs(diff) <= tol:
                continue
            sev = "critical" if abs(diff) > 10 * tol else "warning"
            ver = SX.is_verified(stat, *keys)
            exemption = f", basic exemption {money(plan['basic_exemption'])} a year" if comp == "CPP" else ""
            base = "pensionable" if comp == "CPP" else "insurable"
            out.append(finding("PA-13", sev, employee=e, component=comp, prev=expected, curr=actual,
                               rule=f"{label} recomputed from the payroll engine's {stat.get('year')} constants (rate {rate:g}{exemption}) on this pay's "
                                    f"{base} earnings and the YTD before it"
                                    + ("" if ver else " (constant not yet verified in the Verification Center)"),
                               title=f"{label} {money(actual)} vs {money(expected)} expected ({'+' if diff > 0 else '-'}{money(abs(diff))})",
                               threshold={"tolerance_cad": tol, "plan": plan["plan"], "rule_keys": list(keys), "source": stat.get("source")},
                               owner_role="payroll", verified=ver, dollars_at_risk=abs(diff),
                               fix_hint=("Open the pay stub: check the employee's province (Quebec uses QPP, QPIP and the Quebec EI rate), the CPP / EI exemption "
                                         "flags and the pay-component flags (pensionable / insurable) of each earning.")))
    return out


# ══════════════════════════ PA-14 YTD maximums ══════════════════════════════════

def check_maximums(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, stat = _emps(inputs), inputs.get("statutory") or {}
    yb = inputs.get("ytd_before") or {}
    for eid, ln in sorted((inputs.get("current") or {}).items()):
        e = emps.get(eid) or {"id": eid}
        plan = SX.plan_for(ln.get("province") or e.get("province"), stat)
        if not plan:
            continue
        d = ln.get("deductions") or {}
        if ln.get("ytd"):
            after = {k: _f((ln.get("ytd") or {}).get(k)) for k in ("cpp", "cpp2", "ei", "qpip")}
        elif yb.get(eid) is not None:                              # dry-run line: YTD on file + this pay
            before = yb[eid]
            after = {"cpp": _f(before.get("cpp")) + _f(d.get("cpp")), "cpp2": _f(before.get("cpp2")) + _f(d.get("cpp2")),
                     "ei": _f(before.get("ei")) + _f(d.get("ei")), "qpip": _f(before.get("qpip")) + _f(d.get("qpip"))}
        else:
            continue
        legs = [("CPP", plan["label_pension"], after["cpp"], plan["max"], plan["keys"]["max"]),
                ("CPP2", plan["label_pension2"], after["cpp2"], plan["max2"], plan["keys"]["max2"]),
                ("EI", plan["label_ei"], after["ei"], plan["ei_max"], plan["keys"]["ei_max"])]
        if plan.get("qpip_max") is not None:
            legs.append(("QPIP", "QPIP", after["qpip"], plan["qpip_max"], plan["keys"]["qpip_max"]))
        for comp, label, ytd, mx, key in legs:
            if mx and ytd > mx + 0.01:
                ver = SX.is_verified(stat, key)
                out.append(finding("PA-14", "critical", employee=e, component=comp, prev=mx, curr=ytd,
                                   rule=f"{label} stops at the annual employee maximum ({money(mx)}, payroll engine {stat.get('year')} constants)"
                                        + ("" if ver else " (maximum not yet verified in the Verification Center)"),
                                   title=f"YTD {label} {money(ytd)} is over the {money(mx)} maximum", threshold={"maximum": mx, "rule_key": key, "source": stat.get("source")},
                                   owner_role="payroll", verified=ver, dollars_at_risk=round(ytd - mx, 2),
                                   fix_hint="Check the employee's YTD (opening balances from a previous payroll provider, a second pay group, a voided run) and refund the over-deduction on the next pay."))
    return out


# ══════════════════════════ PA-15 YMPE / YAMPE in force ═════════════════════════

def check_ympe(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, stat, prev = _emps(inputs), inputs.get("statutory") or {}, inputs.get("previous") or {}
    pay = inputs.get("pay_date")
    if pay and not stat.get("edition_found") and stat.get("editions"):
        out.append(finding("PA-15", "critical", component="edition", rule="CPP / EI figures are set per calendar year by the pay date",
                           title=f"The payroll engine has no CPP / EI tables for {pay.year} (editions on file: {', '.join(str(x) for x in stat.get('editions') or [])})",
                           threshold={"pay_year": pay.year, "source": stat.get("source")}, owner_role="payroll", dollars_at_risk=0, verified=False,
                           fix_hint="Add the new year's CRA edition to the payroll engine (constants_ca.py) and verify it before paying in that year."))
    for eid, ln in sorted((inputs.get("current") or {}).items()):
        e = emps.get(eid) or {"id": eid}
        y, d = ln.get("ytd") or {}, ln.get("deductions") or {}
        p = prev.get(eid) or {}
        cur_pay, prev_pay = _d(ln.get("pay_date")) or pay, _d(p.get("pay_date"))
        if y and prev_pay and cur_pay and prev_pay.year != cur_pay.year and _f(y.get("cpp")) > _f(d.get("cpp")) + 0.01:
            out.append(finding("PA-15", "critical", employee=e, component="YTD", prev=_f((p.get("ytd") or {}).get("cpp")), curr=_f(y.get("cpp")),
                               rule="The YTD restarts on the first pay date of a new calendar year — the new year's YMPE, YAMPE and maximums apply",
                               title=f"YTD carried over from {prev_pay.year} into a {cur_pay.year} pay date", owner_role="payroll", dollars_at_risk=0,
                               fix_hint="Run the pay with the year of the PAY DATE (CRA rule) so the YTD and maximums restart."))
            continue
        plan = SX.plan_for(ln.get("province") or e.get("province"), stat)
        if not plan or not ln.get("bases") or not y:
            continue
        exp = expected_contributions(ln, e, plan, _periods(ln, inputs))
        actual = _f(d.get("cpp2"))
        diff = round(actual - exp["cpp2"], 2)
        tol = float(th.get("rate_tolerance_cad") if th.get("rate_tolerance_cad") is not None else 1.0)
        if abs(diff) > tol:
            ver = SX.is_verified(stat, plan["keys"]["ympe"], plan["keys"]["yampe"], plan["keys"]["rate2"])
            band = f"YMPE {money(plan['ympe'])} → YAMPE {money(plan['yampe'])}"
            why = ("deducted before the YTD pensionable earnings reached the YMPE" if actual > 0 and exp["pens_after"] <= plan["ympe"]
                   else "not deducted although the YTD pensionable earnings passed the YMPE" if actual == 0 else "does not match the band")
            out.append(finding("PA-15", "critical" if abs(diff) > 10 * tol else "warning", employee=e, component="CPP2", prev=exp["cpp2"], curr=actual,
                               rule=f"{plan['label_pension2']} is {plan['rate2']:g} of pensionable earnings inside the {band} band in force on the {stat.get('year')} pay date"
                                    + ("" if ver else " (YMPE / YAMPE not yet verified in the Verification Center)"),
                               title=f"{plan['label_pension2']} {money(actual)} vs {money(exp['cpp2'])} expected — {why}",
                               threshold={"ympe": plan["ympe"], "yampe": plan["yampe"], "ytd_pensionable_before": exp["pens_before"],
                                          "ytd_pensionable_after": exp["pens_after"], "source": stat.get("source")},
                               owner_role="payroll", verified=ver, dollars_at_risk=abs(diff),
                               fix_hint="Check the YTD pensionable earnings carried in (opening balances) and that the run used the pay date's year."))
    return out


# ══════════════════════════ PA-16 income tax · TD1 ══════════════════════════════

def _td1(line: dict) -> tuple:
    t = line.get("td1") or {}
    return (t.get("federal"), t.get("provincial"))


def check_tax(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, cur, prev = _emps(inputs), inputs.get("current") or {}, inputs.get("previous") or {}
    lim, floor = float(th.get("tax_delta_pct") or 20), float(th.get("tax_floor_cad") if th.get("tax_floor_cad") is not None else 10)
    for eid, ln in sorted(cur.items()):
        p = prev.get(eid)
        if not p:
            continue
        e = emps.get(eid) or {"id": eid}
        a, b = _f((p.get("deductions") or {}).get("income_tax")), _f((ln.get("deductions") or {}).get("income_tax"))
        dp = _pct(a, b)
        if dp is None or abs(dp) <= lim or abs(b - a) < floor:
            continue
        cur_td1 = _td1(ln) if ln.get("td1") else (e.get("td1_federal_claim"), e.get("td1_provincial_claim"))
        if p.get("td1") and cur_td1 != _td1(p):
            continue                                               # a TD1 change explains it
        out.append(finding("PA-16", "warning", employee=e, component="TAX", prev=a, curr=b,
                           rule=f"Income tax moved more than {lim:g} % vs the previous regular run with no TD1 / claim-code change",
                           title=f"Income tax {dp:+.1f} % ({'+' if b >= a else '-'}{money(abs(b - a))}) with the same TD1 claims",
                           threshold={"pct": lim, "floor_cad": floor, "td1_now": list(cur_td1), "td1_prev": list(_td1(p))},
                           evidence=[{"label": "Previous pay stub", "link": f"/payroll/payslips?employee={eid}"}] + _emp_link(e), owner_role="payroll",
                           fix_hint="A bonus (bonus method), more hours, a revision or a province change explains a jump; an additional-tax request or a projection error does not."))
    return out


# ══════════════════════════ PA-17 vacation pay accrual ══════════════════════════

def check_vacation(inputs: dict, th: dict) -> list[dict]:
    out = []
    emps, stat = _emps(inputs), inputs.get("statutory") or {}
    tol = float(th.get("vacation_tolerance_cad") if th.get("vacation_tolerance_cad") is not None else 0.05)
    pay = inputs.get("pay_date") or inputs["period_end"]
    for eid, ln in sorted((inputs.get("current") or {}).items()):
        v = ln.get("vacation")
        if not v:
            continue
        e = emps.get(eid) or {"id": eid}
        prov = (ln.get("province") or e.get("province") or "").upper()
        pct = _f(v.get("percent"))
        years = _years_between(_d(e.get("hire_date")), pay)
        mn, rule = SX.vacation_minimum(stat, prov, years)
        if mn is not None and pct + 1e-6 < mn:
            out.append(finding("PA-17", "critical", employee=e, component="VACATION", prev=mn, curr=pct,
                               rule=f"Vacation pay is at least {mn:g} % of vacationable earnings in {prov} after {years:g} completed year(s) of service ({rule.get('source')})",
                               title=f"Vacation pay {pct:g} % is below the {prov} minimum {mn:g} %", owner_role="hr", verified=bool(rule.get("verified")),
                               threshold={"minimum_pct": mn, "years_of_service": years, "province": prov, "source": rule.get("source")},
                               dollars_at_risk=round(_f(v.get("vacationable")) * (mn - pct) / 100, 2),
                               fix_hint="Raise the employee's vacation pay % on the employee record (Pay → Vacation %) to at least the provincial minimum for their service."))
        own = e.get("vacation_percent")
        if own is not None and abs(_f(own) - pct) > 1e-6:
            out.append(finding("PA-17", "warning", employee=e, component="VACATION", prev=_f(own), curr=pct,
                               rule="The run accrues vacation pay at the employee's own rate on the employee record",
                               title=f"Run accrued {pct:g} % but the employee record says {_f(own):g} %", owner_role="payroll",
                               fix_hint="Re-run the draft after the rate change, or correct the record if the run is right."))
        expected = round(_f(v.get("vacationable")) * pct / 100, 2)
        if abs(_f(v.get("accrued")) - expected) > tol:
            out.append(finding("PA-17", "warning", employee=e, component="VACATION", prev=expected, curr=_f(v.get("accrued")),
                               rule="Vacation accrued = vacationable earnings × the vacation pay %",
                               title=f"Vacation accrued {money(v.get('accrued'))} vs {money(expected)} ({pct:g} % of {money(v.get('vacationable'))})",
                               threshold={"tolerance_cad": tol}, owner_role="payroll", dollars_at_risk=abs(_f(v.get("accrued")) - expected),
                               fix_hint="Check which earnings are flagged vacationable in the pay-component master."))
    return out


# ══════════════════════════ runner ══════════════════════════════════════════════

CHECK_FNS = [("PA-01", check_headcount), ("PA-02", check_joiners), ("PA-03", check_leavers), ("PA-04", check_unpaid_leave),
             ("PA-05", check_variance), ("PA-06", check_cost_centre), ("PA-07", check_net), ("PA-08", check_min_wage),
             ("PA-09", check_duplicates), ("PA-10", check_retro), ("PA-11", check_tieout), ("PA-12", check_timing),
             ("PA-13", check_rates), ("PA-14", check_maximums), ("PA-15", check_ympe), ("PA-16", check_tax), ("PA-17", check_vacation)]


def _committed(i: dict) -> bool:
    return any((ln or {}).get("bases") and (ln or {}).get("ytd") for ln in (i.get("current") or {}).values())


SKIP_NOTES = {
    "PA-05": lambda i: None if i.get("previous") else "No previous regular run of this pay frequency to compare against — first cycle.",
    "PA-06": lambda i: None if i.get("previous") else "No previous regular run — roll-up comparison skipped.",
    "PA-08": lambda i: None if i.get("min_wage") else "Not available: the Canada payroll code has no provincial minimum-wage table, and the auditor does not invent rates.",
    "PA-09": lambda i: None if any(e.get("sin_key") for e in i.get("employees") or []) else "Bank-account leg ran; SIN leg skipped — the employee record keeps no SIN blind index to compare (SINs are never decrypted here).",
    "PA-10": lambda i: None if _committed(i) else "The dry run carries no earning lines — retro lines are checked on the committed run.",
    "PA-11": lambda i: None if (i.get("tieout") or {}).get("register") else "No committed run yet (the draft is a dry run) — the tie-out runs on the committed run.",
    "PA-13": lambda i: (None if (_committed(i) and ((i.get("statutory") or {}).get("roc") or (i.get("statutory") or {}).get("qc")))
                        else ("Statutory constants unavailable: " + str((i.get("statutory") or {}).get("source"))) if not ((i.get("statutory") or {}).get("roc") or (i.get("statutory") or {}).get("qc"))
                        else "The dry run carries no pensionable / insurable bases or YTD — rates are re-checked on the committed run."),
    "PA-14": lambda i: None if ((i.get("statutory") or {}).get("roc") or (i.get("statutory") or {}).get("qc")) else "Statutory constants unavailable — maximums not checked.",
    "PA-15": lambda i: None if _committed(i) else "The dry run carries no YTD — the YMPE / YAMPE band is re-checked on the committed run (the edition check ran).",
    "PA-16": lambda i: None if i.get("previous") else "No previous regular run — income-tax movement not compared.",
    "PA-17": lambda i: None if any((ln or {}).get("vacation") for ln in (i.get("current") or {}).values()) else "The dry run carries no vacation accrual — checked on the committed run.",
}


def run_checks(inputs: dict, th: dict) -> dict:
    findings, checks = [], []
    for code, fn in CHECK_FNS:
        note = SKIP_NOTES.get(code, lambda i: None)(inputs)
        try:
            rows = fn(inputs, th)
        except Exception as e:                                   # noqa: BLE001 — one broken check never hides the others
            rows, note = [], f"check failed: {type(e).__name__}: {str(e)[:120]}"
        for r in rows:
            r["rank"] = RANK.get(r["severity"], 2)
        findings += rows
        failed = bool(note and note.startswith("check failed"))
        unavailable = code == "PA-08" and not inputs.get("min_wage")
        checks.append({"code": code, "name": CHECK_NAME[code], "findings": len(rows), "critical": sum(1 for r in rows if r["severity"] == "critical"),
                       "warning": sum(1 for r in rows if r["severity"] == "warning"), "info": sum(1 for r in rows if r["severity"] == "info"),
                       "status": ("error" if failed else "unavailable" if unavailable else "skipped" if (note and not rows) else "ran"), "note": note})
    findings.sort(key=lambda r: (r["rank"], r["code"], -(abs(r.get("delta_cad") or 0))))
    return {"findings": findings, "checks": checks, "checks_run": sum(1 for c in checks if c["status"] == "ran"),
            "blocking": [r for r in findings if r["severity"] == "critical" and r["code"] in BLOCKING_CODES]}
