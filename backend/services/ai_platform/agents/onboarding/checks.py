"""Onboarding Agent — the joining checklist as deterministic checks (pure: case dict in → items out, no I/O).

Severity: ``blocking`` = the joiner cannot be paid or reported correctly (direct-deposit account, SIN, date of joining) or a
legal stop (a temporary resident working past the permit expiry); ``warning`` = needs a person this week; ``info`` = shown,
never chased.
"""
from __future__ import annotations

from datetime import date

from agents import statutory_clocks as CLK
from agents.lifecycle_common import item

from .inputs import INSTITUTION_RE, TRANSIT_RE

CHECKS = [
    ("OB-00", "Offer accepted, onboarding not started"), ("OB-01", "Photo ID"), ("OB-02", "Address + postal code"),
    ("OB-03", "Education documents"), ("OB-04", "Direct deposit (institution, transit, account)"), ("OB-05", "SIN (CRA 3-day window)"),
    ("OB-06", "Work permit (temporary residents)"), ("OB-07", "TD1 federal + provincial"), ("OB-08", "Documents vault verified"),
    ("OB-09", "Appointment letter"), ("OB-12", "IT / Admin onboarding tasks (assets, access)"), ("OB-13", "Induction"),
    ("OB-14", "Date of joining"),
]
CHECK_NAME = dict(CHECKS)
LINK = "/ai/agents/onboarding"

DEFAULTS: dict = {
    "lookback_days": 45,             # joiners whose DOJ is within the last N days (and the next ahead_days) are in scope
    "ahead_days": 30,
    "require_education": True,       # OB-03 asks for an education document
    "require_induction": True,       # OB-13
    "permit_warn_days": 60,          # OB-06: a work permit expiring within N days is flagged
    "chase_joiner_hours": 24,
    "chase_hr_hours": 24,
    "chase_it_hours": 24,
    "task_due_soon_days": 3,         # OB-12: an onboarding task due within N days (or overdue) is chased
    "owners": {"it": None, "admin": None},   # login ids for the IT / Admin owners (fallback HR)
}


def _link(case: dict) -> str:
    if case.get("session_id"):
        return f"/onboarding?session={case['session_id']}"
    if (case.get("employee") or {}).get("id"):
        return f"/employees/{case['employee']['id']}"
    return "/hiring/offers"


def evaluate(case: dict, today: date, cfg: dict | None = None) -> list[dict]:
    cfg = {**DEFAULTS, **(cfg or {})}
    e = case.get("employee") or {}
    ck = case["case_key"]
    f = case.get("fields") or {}
    out: list[dict] = []
    emp = e if e.get("id") else {"name": e.get("name")}
    link = _link(case)

    def add(code, title, **kw):
        out.append(item(code, case_key=ck, title=title, employee=emp, case_label=e.get("name"), link=kw.pop("link", link), **kw))

    if case.get("kind") == "offer":
        o = case.get("offer") or {}
        add("OB-00", f"Offer accepted ({o.get('designation') or 'role'}) — onboarding was not started", severity="warning", owner_role="hr",
            what="The candidate accepted the offer but no onboarding invite exists, so nothing below can be collected.",
            how_to_fix="Hiring → Offers → the offer → Retry onboarding (sends the joiner their invite).", link="/hiring/offers",
            due_on=o.get("joining_date"))
        return out

    types = set((case.get("docs") or {}).get("types") or [])
    doj = f.get("hire_date")
    if not doj:
        add("OB-14", "Date of joining is missing", severity="blocking", owner_role="hr",
            what="Payroll cannot pro-rate the first pay or start vacation accrual without a date of joining.",
            how_to_fix="Add the date of joining on the joiner's record.")
    if not (types & {"photo_id", "passport", "work_permit"}):
        add("OB-01", "Upload a photo ID (driver's licence, passport or provincial ID)", severity="warning", owner_role="employee",
            what="No photo ID is in the vault.", how_to_fix="Upload it from the onboarding link / My documents.")
    if not f.get("address"):
        add("OB-02", "Add your current address and postal code", severity="warning", owner_role="employee",
            what="Street, city and a valid postal code (A1A 1A1) are needed for the T4 / RL-1 slips and letters.",
            how_to_fix="Fill the address step of onboarding (or My profile).")
    if cfg.get("require_education") and "education" not in types:
        add("OB-03", "Upload your highest education certificate", severity="warning", owner_role="employee", what="No education document is in the vault.",
            how_to_fix="Upload it as 'Education' from the onboarding link / My documents.")
    # direct deposit: institution (3) + transit (5) + account
    inst, transit = f.get("bank_institution"), f.get("bank_transit")
    if not f.get("bank_account") or not inst or not transit:
        add("OB-04", "Add your direct-deposit account (institution, transit and account number)", severity="blocking", owner_role="employee",
            what="Pay cannot be deposited without the 3-digit institution, 5-digit transit and the account number.",
            how_to_fix="Fill the bank step and upload a void cheque or the bank's direct-deposit form.")
    elif not INSTITUTION_RE.match(inst) or not TRANSIT_RE.match(transit):
        add("OB-04", f"Bank numbers look wrong (institution '{inst}', transit '{transit}')", severity="blocking", owner_role="employee",
            what="The institution number is 3 digits and the transit (branch) number is 5 digits; the deposit file would be rejected.",
            how_to_fix="Copy them from a void cheque: transit (5) – institution (3) – account.")
    elif "void_cheque" not in types:
        add("OB-04", "No void cheque / direct-deposit form on file", severity="info", owner_role="hr", chase=False,
            what="The numbers are filled in but nothing in the vault confirms them.",
            how_to_fix="Ask for a void cheque or the bank's direct-deposit form and check the numbers against it.")
    # SIN — CRA: ask for it within 3 days of the start date
    if not f.get("sin"):
        if doj:
            clk = CLK.sin_request(doj, today)
            sev = "blocking" if clk["status"] in ("overdue", "due_soon") else "warning"
            add("OB-05", f"Add your Social Insurance Number (due {clk['due']})", severity=sev, owner_role="employee", clock=clk, due_on=clk["due"],
                what=f"{clk['label']} — {clk['days_left']} day(s) left. Payroll needs it for CPP / EI, tax and the T4.",
                how_to_fix="Enter the 9-digit SIN in the onboarding link (it is stored encrypted and shown masked).")
        else:
            add("OB-05", "Add your Social Insurance Number", severity="blocking", owner_role="employee",
                what="Payroll needs the SIN for CPP / EI, tax and the T4.", how_to_fix="Enter the 9-digit SIN in the onboarding link.")
    elif f.get("sin_valid") is False:
        add("OB-05", "The SIN on file fails the check-digit test", severity="blocking", owner_role="employee",
            what="A SIN that fails the Luhn check is rejected on the T4.", how_to_fix="Re-enter the SIN from the SIN confirmation letter.")
    # temporary residents: a 9xx SIN or a work / study permit needs a permit expiry date
    temp = f.get("temporary_resident")
    exp = f.get("work_permit_expiry")
    if temp and not exp:
        add("OB-06", "Add the work permit expiry date", severity="blocking", owner_role="hr",
            what="A temporary resident (SIN starting with 9 or a work / study permit) may only work until the permit expires.",
            how_to_fix="Record the permit type, number and expiry on the employee and upload the permit.")
    elif exp:
        left = (exp - today).days
        if left <= int(cfg.get("permit_warn_days") or 60):
            add("OB-06", ("Work permit expired " if left < 0 else "Work permit expires in ") + f"{abs(left)} day(s)",
                severity="blocking" if left < 0 else "warning", owner_role="hr", due_on=exp,
                what=f"Permit expiry {exp.isoformat()}.", how_to_fix="Get the renewed permit (or proof of maintained status) and update the record.")
    # TD1 forms
    if not f.get("td1_federal"):
        add("OB-07", "Fill in your TD1 (federal) personal tax credits return", severity="warning", owner_role="employee",
            what="Without a TD1 payroll uses only the basic personal amount, which may over-withhold tax.",
            how_to_fix="Enter the TD1 claim amounts in the onboarding link, or upload the signed TD1.")
    if not f.get("td1_provincial"):
        add("OB-07", f"Fill in your TD1 ({f.get('province') or 'provincial'}) personal tax credits return", severity="info", owner_role="employee",
            ref="provincial", chase=False, what="Payroll uses the provincial basic personal amount until it is received.",
            how_to_fix="Enter the provincial TD1 claim amount in the onboarding link.")
    unv = (case.get("docs") or {}).get("unverified") or []
    if unv:
        add("OB-08", f"Verify {len(unv)} uploaded document(s) in the vault", severity="warning", owner_role="hr",
            what=", ".join(sorted({d['doc_type'].replace('_', ' ') for d in unv})), how_to_fix="Onboarding → the joiner → Documents → Verify each.",
            detail={"documents": unv[:20]})
    # appointment letter
    let = case.get("letter")
    if e.get("id"):
        if not let:
            add("OB-09", "Appointment letter not drafted yet", severity="warning", owner_role="hr", chase=False,
                what="I can draft it from your appointment-letter template; HR reviews and issues it (nothing is issued by the agent).",
                how_to_fix="Approve the 'Draft the appointment letter' card in the Approvals inbox, then issue it from Letters.", link=f"/employees/{e['id']}?tab=letters")
        elif let.get("status") == "draft":
            add("OB-09", "Appointment letter drafted — review and issue it", severity="warning", owner_role="hr",
                what="A draft (no number, DRAFT watermark) is waiting.", how_to_fix="Employees → the joiner → Letters → Issue.", link=f"/employees/{e['id']}?tab=letters")
    # onboarding tasks (IT / Admin / HR / Payroll)
    soon = int(cfg.get("task_due_soon_days") or 3)
    for t in case.get("tasks") or []:
        due = t.get("due_date")
        try:
            dd = date.fromisoformat(str(due)[:10]) if due else None
        except ValueError:
            dd = None
        if dd is None or (dd - today).days > soon:
            continue
        owner = (t.get("owner") or "HR").strip().lower()
        role = "it" if owner.startswith("it") else "admin" if owner.startswith("admin") else "payroll" if owner.startswith("payroll") else "hr"
        add("OB-12", f"{t.get('title') or 'Onboarding task'} ({t.get('owner') or 'HR'})", severity="warning", owner_role=role, ref=str(t.get("id")),
            what=f"Onboarding task {'overdue since' if dd < today else 'due'} {dd.isoformat()}.", due_on=dd,
            how_to_fix="Do it, then tick it on Onboarding → the joiner → Tasks.")
    if e.get("id") and case.get("status") in ("active", "activated") and not case.get("assets") and not any(
            (t.get("owner") or "").lower().startswith("it") for t in case.get("tasks") or []):
        add("OB-12", "No laptop / asset issued to the joiner yet", severity="info", owner_role="it", ref="assets", chase=False,
            what="No asset is assigned and no IT task is open.", how_to_fix="Assets → Assign (or add an IT task on the onboarding checklist).")
    if cfg.get("require_induction") and e.get("id") and not case.get("lms") and not any("induction" in (t.get("title") or "").lower() for t in case.get("tasks") or []):
        add("OB-13", "Induction not assigned", severity="warning", owner_role="hr",
            what="No induction course or task exists for the joiner (health and safety awareness is part of it in every province).",
            how_to_fix="Learning → assign the induction course (or add an induction task).", link="/engage/lms")
    return out


def run_checks(cases: list[dict], today: date, cfg: dict | None = None) -> dict:
    items: list[dict] = []
    per_check = {c: 0 for c, _ in CHECKS}
    for c in cases:
        for x in evaluate(c, today, cfg):
            items.append(x)
            per_check[x["code"]] = per_check.get(x["code"], 0) + 1
    complete = [c["case_key"] for c in cases if c.get("kind") != "offer" and not any(x["case_key"] == c["case_key"] and x["severity"] == "blocking" for x in items)]
    return {"items": items, "checks": [{"code": k, "name": n, "items": per_check.get(k, 0)} for k, n in CHECKS], "checks_run": len(CHECKS),
            "complete_for_payroll": complete}
