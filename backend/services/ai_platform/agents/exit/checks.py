"""Exit Agent — deterministic checks (pure: case dict in → items + proposed actions out).

The final pay AMOUNT is never computed here: the payroll service's Canada final-pay run (vacation payout + extras −
clearance recoveries, then the ROE draft) does it when the agent's proposal is approved. Clocks come from
agents/statutory_clocks.py (provincial final-pay rule, electronic ROE timing), each with its verified flag.
"""
from __future__ import annotations

from datetime import date

from agents import statutory_clocks as CLK
from agents.lifecycle_common import item

CHECKS = [("EX-01", "Notice period + last working day"), ("EX-02", "Clearance per department"), ("EX-03", "Final pay (provincial clock)"),
          ("EX-04", "Separation + employment reference letters"), ("EX-05", "Record of Employment (5-day clock)")]
LINK = "/ai/agents/exit"
CLEARANCE_STATUSES = ("accepted", "clearance", "settlement")
PAY_STATUSES = ("clearance", "settlement")

DEFAULTS: dict = {
    "review_after_days": 3,          # EX-01: a resignation still in manager / HR review after N days is chased
    "chase_owner_hours": 24,
    "chase_hr_hours": 24,
    "chase_payroll_hours": 12,
    "clearance_lead_days": 3,        # clearance items are chased from N days before the last working day
    "owners": {"it": None, "admin": None, "finance": None},
}

LETTER_NAMES = {"relieving": "separation letter", "experience_certificate": "employment reference letter"}


def _role_for_item(i: dict) -> str:
    dep = (i.get("department") or "").lower()
    r = (i.get("owner_role") or "").lower()
    if r == "manager" or "manager" in dep:
        return "manager"
    if dep.startswith("it"):
        return "it"
    if dep.startswith("admin") or "library" in dep:
        return "admin"
    if dep.startswith("finance") or r in ("payroll_admin", "payroll", "finance"):
        return "finance"
    return "hr"


def _has_letter(case: dict, key: str) -> str | None:
    """→ 'issued' | 'draft' | None for the separation (relieving) / reference (experience_certificate) letters."""
    alias = {"relieving": ("relieving",), "experience_certificate": ("experience", "experience_certificate")}[key]
    for l in case.get("case_letters") or []:
        if isinstance(l, dict) and l.get("key") in alias and l.get("number"):
            return "issued"
    st = None
    for l in case.get("letter_rows") or []:
        if l.get("template_key") == key:
            st = "issued" if l.get("status") == "issued" else (st or "draft")
    return st


def evaluate(case: dict, today: date, cfg: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(cfg or {})}
    e = case.get("employee") or {}
    ck = f"case:{case['id']}"
    label = f"{case.get('case_number') or ''} {e.get('name') or ''}".strip()
    link = f"/exits/{case['id']}"
    status = case.get("status")
    items: list[dict] = []
    actions: list[dict] = []
    clock = None

    def add(code, title, **kw):
        items.append(item(code, case_key=ck, title=title, employee=e, case_label=label, link=kw.pop("link", link), **kw))

    lwd = case.get("lwd")
    # EX-01 notice + last working day
    if status in ("submitted", "manager_review", "hr_review"):
        sub = case.get("submitted_on")
        age = (today - sub).days if sub else 0
        who = "manager" if status in ("submitted", "manager_review") else "hr"
        add("EX-01", f"Resignation waiting for {who} review ({age} day(s))", severity="warning" if age >= int(cfg["review_after_days"]) else "info",
            owner_role=who, chase=age >= int(cfg["review_after_days"]), detail={"manager_id": e.get("manager_id")},
            what=f"Notice {case.get('notice_days') or '?'} day(s) ({case.get('notice_source') or 'policy'}); requested last day {case.get('requested_lwd') or '—'}; expected {case.get('expected_lwd') or '—'}.",
            how_to_fix="Exits → the case → Accept (confirm the last working day) or discuss with the employee.")
    elif not lwd and status in CLEARANCE_STATUSES:
        add("EX-01", "Last working day not confirmed", severity="blocking", owner_role="hr",
            what="Clearance, the final-pay clock and the ROE clock all start from the last working day.",
            how_to_fix="Exits → the case → Notice → set the last working day.")
    elif lwd:
        short = int(case.get("notice_shortfall_days") or 0)
        add("EX-01", f"Last working day {lwd.isoformat()} · notice {case.get('notice_days') or 0} day(s)" + (f" · shortfall {short} ({case.get('notice_treatment') or 'none'})" if short else ""),
            severity="info", owner_role="hr", chase=False, due_on=lwd,
            what=f"Notice served {case.get('notice_served_days') or 0} of {case.get('notice_days') or 0} day(s); source {case.get('notice_source') or 'policy'}.")
    # EX-02 clearance — chased from N days before the last day until signed
    pending = [i for i in case.get("clearance") or [] if i.get("status") == "pending"]
    if status in CLEARANCE_STATUSES and pending and (lwd is None or (lwd - today).days <= int(cfg["clearance_lead_days"])):
        for i in pending:
            role = _role_for_item(i)
            sev = "blocking" if (lwd and lwd < today) else "warning"
            add("EX-02", f"Sign the {i.get('department') or 'department'} clearance: {i.get('item') or 'clearance'}", severity=sev, owner_role=role, ref=str(i["id"]),
                owner_user_id=i.get("owner_user_id") or None, owner_employee_id=(i.get("owner_employee_id") if not i.get("owner_user_id") else None),
                detail={"manager_id": e.get("manager_id"), "department": i.get("department")}, due_on=lwd,
                what=f"{i.get('department')}: '{i.get('item')}' is still pending" + (f" — the last working day ({lwd.isoformat()}) has passed." if sev == "blocking" else "."),
                how_to_fix="Exits → the case → Clearance → mark it cleared (enter any recovery amount there).")
    # EX-03 final pay inside the provincial clock (the payroll final-pay run computes it; the agent reads)
    paid = case.get("settlement_status") == "paid"
    if lwd and status in PAY_STATUSES + ("settled",):
        clock = CLK.final_pay(case.get("province"), lwd, case.get("next_pay_day"), today)
        badge = "" if clock["verified"] else f" ({clock['badge']})"
        if paid:
            paid_on = case.get("settlement_paid_at")
            late = bool(paid_on and str(paid_on)[:10] > (clock["due"] or "9999"))
            add("EX-03", f"Final pay run{' after the clock' if late else ' within the clock'}", severity="info", owner_role="payroll",
                chase=False, clock={**clock, "status": "late" if late else "met"}, what=f"Run on {str(paid_on)[:10] if paid_on else '—'}; due {clock['due']}.")
        else:
            sev = "blocking" if clock["status"] in ("overdue", "due_soon") else "warning"
            ready = not pending
            add("EX-03", f"Final pay not run — due {clock['due']}", severity=sev, owner_role="payroll" if ready else "hr", clock=clock, due_on=clock["due"],
                chase=ready,                                          # while clearances are pending, EX-02 is what gets chased
                what=f"{clock['label']}: {clock['days_left']} day(s) left{badge}." + ("" if ready else f" {len(pending)} clearance item(s) still pending."),
                how_to_fix=("Approve 'Run the final pay' in the Approvals inbox (two approvers) — the payroll service pays out the vacation balance, "
                            "applies the clearance recoveries and produces the ROE draft." if ready else
                            "Get the clearances signed first (they carry the recovery amounts)."))
            if ready:
                actions.append({"tool": "exit.run_final_pay", "args": {"case_id": case["id"], "employee_id": e.get("id"), "last_day": lwd.isoformat()},
                                "plain_action": f"Run the final pay for {label} (last day {lwd.isoformat()}) — vacation payout, extras and recoveries from the exit case",
                                "reason": f"Clearance complete; {clock['label']} — due {clock['due']}{badge}.", "headcount": 1})
    # EX-05 ROE — 5 calendar days after the end of the pay period in which the last day falls
    if lwd and status in PAY_STATUSES + ("settled",):
        roe = CLK.roe_electronic(case.get("pay_period_end"), today)
        if roe["due"]:
            sev = "blocking" if roe["status"] in ("overdue", "due_soon") else "warning"
            add("EX-05", f"File the Record of Employment on ROE Web by {roe['due']}" + (" (final pay done — the ROE draft is ready)" if paid else ""),
                severity=sev if paid else "info", owner_role="payroll", clock=roe, due_on=roe["due"], chase=paid,
                link=f"/payroll/roe?employee={e.get('id')}",
                what=f"Reason code {case.get('roe_reason_code') or 'suggested from the exit type'}; pay period ends {case.get('pay_period_end')}.",
                how_to_fix="Payroll → ROE → review the draft (insurable earnings and hours from the pay history) → file it on ROE Web.")
    # EX-04 letters (drafts proposed; HR issues; held until settled when the exit settings say so)
    if lwd and (status in ("settlement", "settled") or lwd <= today) and status not in ("submitted", "manager_review", "hr_review"):
        missing = [k for k in ("relieving", "experience_certificate") if not _has_letter(case, k)]
        drafts = [k for k in ("relieving", "experience_certificate") if _has_letter(case, k) == "draft"]
        if missing:
            add("EX-04", f"Exit letters not drafted ({', '.join(LETTER_NAMES[m] for m in missing)})", severity="info", owner_role="hr", chase=False,
                what="I can draft them from your templates; HR issues them (held until settled when the exit settings say so).")
            actions.append({"tool": "exit.draft_exit_letters", "args": {"case_id": case["id"], "employee_id": e.get("id"), "keys": missing},
                            "plain_action": f"Draft the {' and '.join(LETTER_NAMES[m] for m in missing)} for {label} (HR reviews and issues)",
                            "reason": "Last working day reached; the letters are drafts with no number until HR issues them.", "headcount": 1})
        if drafts:
            add("EX-04", f"Issue the drafted {' and '.join(LETTER_NAMES[d] for d in drafts)}", severity="warning" if status == "settled" else "info",
                owner_role="hr", chase=status == "settled", what="Drafts are waiting.", how_to_fix="Exits → the case → Letters → Issue.")
    return {"items": items, "actions": actions, "clock": clock}


def run_checks(cases: list[dict], today: date, cfg: dict | None = None) -> dict:
    items, actions, clocks = [], [], {}
    for c in cases:
        r = evaluate(c, today, cfg)
        items += r["items"]
        actions += r["actions"]
        if r["clock"]:
            clocks[c["id"]] = r["clock"]
    return {"items": items, "actions": actions, "clocks": clocks, "checks_run": len(CHECKS),
            "checks": [{"code": k, "name": n, "items": sum(1 for x in items if x["code"] == k)} for k, n in CHECKS]}
