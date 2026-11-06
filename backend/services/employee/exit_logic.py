"""Exit process — the rules, with no database and no HTTP (Stream C).

State machine, who may act, notice maths, clearance → settlement, letters gate,
last-working-day tick, rehire spell, attrition summary. Everything here is a pure
function so it is unit-tested directly (test_exit_logic.py); exit_routers.py only
loads rows, calls these, and writes the result with `WHERE status = <what was read>`.

Nothing in this file is a statutory figure. Notice days (30 / 15) are ORG SETTINGS
defaults, not law.
"""
from __future__ import annotations

from datetime import date, timedelta

# ── vocabulary ────────────────────────────────────────────────────────────────
STATES = ("submitted", "manager_review", "hr_review", "accepted", "clearance", "settlement",
          "settled", "closed", "withdrawn", "rejected", "on_hold")
STATE_LABELS = {
    "submitted": "Submitted", "manager_review": "Manager review", "hr_review": "HR review",
    "accepted": "Accepted – notice running", "clearance": "Clearance", "settlement": "Settlement",
    "settled": "Settled", "closed": "Closed", "withdrawn": "Withdrawn", "rejected": "Rejected", "on_hold": "On hold",
}
OPEN_STATES = ("submitted", "manager_review", "hr_review", "accepted", "clearance", "settlement", "settled", "on_hold")
BEFORE_ACCEPTANCE = ("submitted", "manager_review", "hr_review")
AFTER_ACCEPTANCE = ("accepted", "clearance", "settlement", "settled", "closed")
FINAL_STATES = ("closed", "withdrawn", "rejected")

EXIT_TYPES = {
    "resignation": "Resignation", "termination": "Termination", "end_of_contract": "End of contract",
    "retirement": "Retirement", "death": "Death", "absconding": "Absconding", "mutual_separation": "Mutual separation",
}
EMPLOYEE_INITIATED = ("resignation",)
EMPLOYER_INITIATED = ("termination", "end_of_contract", "retirement", "death", "absconding", "mutual_separation")
MANAGER_MAY_RAISE = ("resignation", "absconding", "end_of_contract")

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "hr"}
PAYROLL_ROLES = {"payroll_admin", "payroll"}
NOTICE_TREATMENTS = {"buyout": "Buy-out – employee pays (recovered in the settlement)",
                     "waiver": "Waived by HR", "pay_in_lieu": "Employer pays in lieu of notice",
                     "none": "No shortfall"}
CLEARANCE_STATUSES = ("pending", "cleared", "cleared_with_recovery")
LETTER_KEYS = {"resignation_acceptance": "Resignation acceptance", "relieving": "Separation letter",
               "experience": "Employment reference letter", "fnf_cover": "Final pay cover letter"}
# Letters that wait for clearance + settlement (unless HR overrides with a reason).
LETTERS_HELD = ("relieving", "experience", "fnf_cover")
# SUGGESTED ROE reason code per exit type — only codes payroll's ROE_REASONS already has.
# HR confirms the code on the case; this is a default, not a ruling.
ROE_SUGGESTED = {"resignation": "E", "termination": "M", "end_of_contract": "A", "retirement": "G",
                 "death": "K", "absconding": "E", "mutual_separation": "K"}
ROE_CODES = ("A", "D", "E", "F", "G", "K", "M", "N")

DEFAULT_SETTINGS = {
    "default_notice_days": 30, "probation_notice_days": 15, "leave_extends_lwd": False,
    "rehire_number_policy": "keep",            # keep | new
    "hold_letters_until_settled": True,
}


def default_clearance_template() -> list[dict]:
    return [
        {"department": "Reporting manager", "item": "Handover of work and documents", "owner_role": "manager"},
        {"department": "IT", "item": "Laptop and devices returned", "owner_role": "hr_admin"},
        {"department": "IT", "item": "E-mail and system accounts closed", "owner_role": "hr_admin"},
        {"department": "Admin", "item": "ID card and access card returned", "owner_role": "hr_admin"},
        {"department": "Finance", "item": "Advances, loans and expense claims settled", "owner_role": "payroll_admin"},
        {"department": "Library / other", "item": "Manuals and other company property returned", "owner_role": "hr_admin"},
        {"department": "HR", "item": "Exit interview done and records updated", "owner_role": "hr_admin"},
    ]


def default_interview_template() -> list[dict]:
    return [
        {"key": "reason", "question": "What is the main reason you are leaving?", "type": "text"},
        {"key": "liked", "question": "What did you like most about working here?", "type": "text"},
        {"key": "improve", "question": "What should we do better?", "type": "text"},
        {"key": "manager", "question": "How was the support from your manager? (1 poor – 5 excellent)", "type": "rating"},
        {"key": "recommend", "question": "Would you recommend us as a place to work?", "type": "yes_no"},
        {"key": "rejoin", "question": "Would you consider working here again?", "type": "yes_no"},
    ]


def to_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ── who is acting ─────────────────────────────────────────────────────────────
def relation(actor: dict, case: dict) -> set:
    """How the actor relates to the case: {'hr','payroll','manager','self'}.
    actor = {role, employee_id, user_id}; case = {employee_id, manager_id}."""
    role = (actor.get("role") or "").lower()
    rel = set()
    if role in HR_ROLES:
        rel.add("hr")
    if role in PAYROLL_ROLES:
        rel.add("payroll")
    me = str(actor.get("employee_id") or "")
    if me and me == str(case.get("employee_id") or ""):
        rel.add("self")
    if me and case.get("manager_id") and me == str(case.get("manager_id")) and "self" not in rel:
        rel.add("manager")
    return rel


def may_view(actor: dict, case: dict) -> bool:
    return bool(relation(actor, case))


# ── the state machine ─────────────────────────────────────────────────────────
# action → (from states, to state, who may act)
TRANSITIONS = {
    "manager_review":   (("submitted",), "manager_review", {"manager", "hr"}),
    "manager_approve":  (("submitted", "manager_review"), "hr_review", {"manager", "hr"}),
    "manager_reject":   (("submitted", "manager_review"), "rejected", {"manager", "hr"}),
    "send_to_hr":       (("submitted", "manager_review"), "hr_review", {"hr"}),
    "accept":           (("hr_review",), "accepted", {"hr"}),
    "reject":           (("submitted", "manager_review", "hr_review"), "rejected", {"hr"}),
    "start_clearance":  (("accepted",), "clearance", {"hr"}),
    "start_settlement": (("clearance",), "settlement", {"hr", "payroll"}),
    "settlement_paid":  (("settlement",), "settled", {"hr", "payroll"}),
    "close":            (("settled",), "closed", {"hr"}),
    "withdraw":         (BEFORE_ACCEPTANCE, "withdrawn", {"self", "hr"}),
    "request_withdraw": (("accepted", "clearance"), None, {"self"}),          # no state change — HR decides
    "approve_withdraw": (("accepted", "clearance"), "withdrawn", {"hr"}),
    "decline_withdraw": (("accepted", "clearance"), None, {"hr"}),
    "hold":             (("submitted", "manager_review", "hr_review", "accepted", "clearance", "settlement"), "on_hold", {"hr"}),
    "resume":           (("on_hold",), "__held__", {"hr"}),
}
ACTION_LABELS = {
    "manager_review": "Start manager review", "manager_approve": "Manager – forward to HR",
    "manager_reject": "Manager – reject", "send_to_hr": "Send to HR", "accept": "Accept",
    "reject": "Reject", "start_clearance": "Start clearance", "start_settlement": "Start settlement",
    "settlement_paid": "Mark settlement paid", "close": "Close case", "withdraw": "Withdraw",
    "request_withdraw": "Ask to withdraw", "approve_withdraw": "Approve withdrawal",
    "decline_withdraw": "Decline withdrawal", "hold": "Put on hold", "resume": "Resume",
}
REASON_REQUIRED = {"manager_reject", "reject", "hold", "approve_withdraw", "decline_withdraw", "request_withdraw"}


def can(action: str, case: dict, actor: dict) -> tuple[bool, str]:
    """May this actor take this action on this case now? → (ok, why-not)."""
    t = TRANSITIONS.get(action)
    if not t:
        return False, "Unknown action"
    frm, _to, who = t
    status = case.get("status")
    if status not in frm:
        return False, f"Not possible while the case is '{STATE_LABELS.get(status, status)}'"
    rel = relation(actor, case)
    if not (rel & who):
        need = " or ".join(sorted({"hr": "HR", "payroll": "payroll", "manager": "the reporting manager",
                                   "self": "the employee"}[w] for w in who))
        return False, f"Only {need} can do this"
    if action in ("withdraw", "request_withdraw", "approve_withdraw") and case.get("separated_at"):
        return False, "The last working day has passed – the employee has already left"
    if action == "withdraw" and "hr" not in rel and case.get("initiated_by") != "employee":
        return False, "Only a resignation raised by the employee can be withdrawn by the employee"
    if action in ("approve_withdraw", "decline_withdraw") and not case.get("withdraw_requested"):
        if action == "decline_withdraw":
            return False, "The employee has not asked to withdraw"
    if action == "request_withdraw" and case.get("withdraw_requested"):
        return False, "A withdrawal request is already waiting for HR"
    return True, ""


def next_status(action: str, case: dict) -> str | None:
    """The status after the action; None = the status does not change."""
    to = TRANSITIONS[action][1]
    if to == "__held__":
        return case.get("held_from_status") or "hr_review"
    return to


def available_actions(case: dict, actor: dict) -> list[dict]:
    out = []
    for a in TRANSITIONS:
        ok, _ = can(a, case, actor)
        if ok:
            out.append({"action": a, "label": ACTION_LABELS[a], "needs_reason": a in REASON_REQUIRED})
    return out


def initial_status(exit_type: str, actor_rel: set) -> str:
    """Where a new case starts. Employee resignation → Submitted (manager first).
    Raised by the manager → HR review. Raised by HR → HR review (HR then accepts)."""
    if "self" in actor_rel and exit_type in EMPLOYEE_INITIATED and "hr" not in actor_rel:
        return "submitted"
    return "hr_review"


def may_create(exit_type: str, actor: dict, employee: dict, reason: str | None) -> tuple[bool, str, str]:
    """→ (ok, why-not, initiated_by). `employee` = {id, manager_id}."""
    if exit_type not in EXIT_TYPES:
        return False, f"Exit type must be one of: {', '.join(EXIT_TYPES)}", ""
    rel = relation(actor, {"employee_id": employee.get("id"), "manager_id": employee.get("manager_id")})
    if "hr" in rel and "self" not in rel:
        if exit_type in EMPLOYER_INITIATED and not (reason or "").strip():
            return False, "A reason is needed when the company starts the exit", ""
        return True, "", "hr"
    if "self" in rel:
        if exit_type not in EMPLOYEE_INITIATED:
            return False, "You can only submit your own resignation here", ""
        return True, "", "employee"
    if "manager" in rel:
        if exit_type not in MANAGER_MAY_RAISE:
            return False, "A manager can raise a resignation, end of contract or absconding case – HR starts the others", ""
        if not (reason or "").strip():
            return False, "A reason is needed", ""
        return True, "", "manager"
    return False, "Only the employee, their reporting manager or HR can start an exit", ""


def pending_with(case: dict) -> str:
    """Who the case is waiting for — the 'pending with' filter on the Exits grid."""
    s = case.get("status")
    if case.get("withdraw_requested") and s in ("accepted", "clearance"):
        return "HR (withdrawal request)"
    return {"submitted": "Manager", "manager_review": "Manager", "hr_review": "HR", "accepted": "HR (start clearance)",
            "clearance": "Clearance owners", "settlement": "Payroll", "settled": "HR (letters)", "on_hold": "HR"}.get(s, "—")


# ── notice ────────────────────────────────────────────────────────────────────
def employment_stage(employee: dict, on: date) -> str:
    """'probation' while the probation end date is still ahead, else 'confirmed'."""
    pe = to_date(employee.get("probation_end_date"))
    return "probation" if (pe and on <= pe) else "confirmed"


def notice_days_for(employee: dict, policies: list[dict] | None, settings: dict | None, on: date) -> dict:
    """Notice days from the org policy table: the MOST SPECIFIC active row that matches the
    employee's grade (designation) / employment type / stage wins; with no matching row
    the org defaults apply (30 days; 15 in probation — settings, not law)."""
    st = {**DEFAULT_SETTINGS, **(settings or {})}
    stage = employment_stage(employee, on)
    grade = (employee.get("job_title") or "").strip().lower()
    etype = (employee.get("employment_type") or "").strip().lower()
    best, best_score = None, -1
    for p in policies or []:
        if p.get("active") is False:
            continue
        pg = (p.get("grade") or "").strip().lower()
        pt = (p.get("employment_type") or "").strip().lower()
        ps = (p.get("stage") or "any").strip().lower()
        if pg and pg != grade:
            continue
        if pt and pt != etype:
            continue
        if ps not in ("any", "") and ps != stage:
            continue
        score = (4 if pg else 0) + (2 if pt else 0) + (1 if ps not in ("any", "") else 0)
        if score > best_score:
            best, best_score = p, score
    if best is not None:
        return {"days": int(best.get("notice_days") or 0), "stage": stage, "source": "policy",
                "policy": {k: best.get(k) for k in ("id", "grade", "employment_type", "stage", "notice_days")}}
    days = int(st["probation_notice_days"] if stage == "probation" else st["default_notice_days"])
    return {"days": days, "stage": stage, "source": "default", "policy": None}


def expected_lwd(accepted_on: date, notice_days: int, leave_days: float = 0, leave_extends: bool = False) -> date:
    """Notice runs from the acceptance date, which counts as day 1: the expected last
    working day = acceptance date + notice days − 1 (zero notice → the acceptance date).
    Leave taken during notice pushes it out only when the org setting says so."""
    n = max(int(notice_days or 0), 0)
    d = accepted_on + timedelta(days=max(n - 1, 0))
    if leave_extends and leave_days and leave_days > 0:
        d += timedelta(days=int(-(-float(leave_days) // 1)))          # ceil
    return d


def notice_position(*, accepted_on: date, notice_days: int, lwd: date, leave_days: float = 0,
                    leave_extends: bool = False) -> dict:
    """Days served / short. Served = acceptance date → last working day, inclusive (garden
    leave counts as served — the employee is still employed). When leave during notice
    extends the last working day, those leave days do not count as served."""
    n = max(int(notice_days or 0), 0)
    served = max((lwd - accepted_on).days + 1, 0)
    if leave_extends and leave_days:
        served = max(served - int(-(-float(leave_days) // 1)), 0)
    short = max(n - served, 0)
    return {"notice_days": n, "served_days": min(served, n) if short == 0 else served, "served_raw": served,
            "shortfall_days": short, "expected_lwd": expected_lwd(accepted_on, n, leave_days, leave_extends).isoformat(),
            "lwd": lwd.isoformat(), "early": short > 0}


def notice_treatment_ok(shortfall: int, treatment: str | None, reason: str | None, exit_type: str) -> tuple[bool, str]:
    """A shortfall must be settled one of three ways before the settlement starts."""
    if shortfall <= 0:
        return True, ""
    if treatment not in ("buyout", "waiver", "pay_in_lieu"):
        return False, f"Notice is short by {shortfall} day(s): choose buy-out, waiver or pay in lieu"
    if treatment == "waiver" and not (reason or "").strip():
        return False, "A waiver needs a reason"
    if treatment == "pay_in_lieu" and exit_type in EMPLOYEE_INITIATED:
        return False, "Pay in lieu is for exits started by the company. For a resignation choose buy-out or waiver"
    return True, ""


# ── clearance ─────────────────────────────────────────────────────────────────
def clearance_lines(template: list[dict] | None, case: dict) -> list[dict]:
    """The checklist for one case from the org template (one line per department / owner).
    A line owned by 'manager' is addressed to the employee's reporting manager."""
    out = []
    for i, t in enumerate(template or default_clearance_template()):
        owner_role = (t.get("owner_role") or "hr_admin").lower()
        owner_user = t.get("owner_user_id")
        owner_emp = None
        owner_name = t.get("owner_name")
        if owner_role == "manager":
            owner_emp = case.get("manager_id")
            owner_name = owner_name or case.get("manager_name")
        out.append({"department": t.get("department") or "Other", "item": t.get("item") or "Clearance",
                    "owner_role": owner_role, "owner_user_id": owner_user, "owner_employee_id": owner_emp,
                    "owner_name": owner_name, "status": "pending", "recovery_amount": 0.0, "sort": i})
    return out


def may_clear(actor: dict, case: dict, line: dict) -> tuple[bool, str]:
    """HR may clear any line; otherwise the named user, the owning role, or (for a manager
    line) the employee's reporting manager. Never the leaving employee."""
    rel = relation(actor, case)
    if "self" in rel and "hr" not in rel:
        return False, "You cannot clear your own exit checklist"
    if "hr" in rel:
        return True, ""
    role = (actor.get("role") or "").lower()
    if line.get("owner_user_id") and str(line["owner_user_id"]) == str(actor.get("user_id") or ""):
        return True, ""
    if line.get("owner_role") == "manager":
        return ("manager" in rel, "" if "manager" in rel else "Only the reporting manager or HR can clear this line")
    if role and role == (line.get("owner_role") or ""):
        return True, ""
    if role in PAYROLL_ROLES and (line.get("owner_role") or "") in PAYROLL_ROLES:
        return True, ""
    return False, "This line belongs to another owner"


def clearance_summary(items: list[dict]) -> dict:
    total = len(items)
    pending = [i for i in items if i.get("status") == "pending"]
    rec = round(sum(float(i.get("recovery_amount") or 0) for i in items if i.get("status") == "cleared_with_recovery"), 2)
    return {"total": total, "pending": len(pending), "cleared": total - len(pending),
            "complete": total > 0 and not pending, "recovery_total": rec,
            "pending_departments": sorted({i.get("department") or "Other" for i in pending})}


def clearance_update_ok(status: str, amount, note: str | None) -> tuple[bool, str]:
    if status not in CLEARANCE_STATUSES:
        return False, "Status must be pending, cleared or cleared with recovery"
    if status == "cleared_with_recovery":
        try:
            a = float(amount or 0)
        except (TypeError, ValueError):
            return False, "Recovery amount must be a number"
        if a <= 0:
            return False, "Enter the amount to recover"
        if not (note or "").strip():
            return False, "Say what the recovery is for"
    return True, ""


# ── settlement ────────────────────────────────────────────────────────────────
def settlement_ready(case: dict, items: list[dict], override_reason: str | None = None) -> tuple[bool, str]:
    """'Start settlement' needs: a last working day, the notice shortfall decided, and
    clearance complete — or an HR override with a reason."""
    if not to_date(case.get("lwd")):
        return False, "Set the last working day first"
    ok, why = notice_treatment_ok(int(case.get("notice_shortfall_days") or 0), case.get("notice_treatment"),
                                  case.get("notice_treatment_reason"), case.get("exit_type") or "")
    if not ok:
        return False, why
    cs = clearance_summary(items)
    if not cs["complete"] and not (override_reason or "").strip():
        if cs["total"] == 0:
            return False, "Start clearance first (or override with a reason)"
        return False, f"Clearance is not complete: {cs['pending']} line(s) pending ({', '.join(cs['pending_departments'])}). " \
                      "Finish them or override with a reason"
    return True, ""


def settlement_prefill(case: dict, items: list[dict], country: str) -> dict:
    """What the EXISTING Canada final-pay calculator (payroll /payroll/final-pay + ROE) is
    opened with. No amounts are worked out here except the sum of the clearance recoveries
    HR already entered."""
    cs = clearance_summary(items)
    lwd = to_date(case.get("lwd"))
    short = int(case.get("notice_shortfall_days") or 0)
    treat = case.get("notice_treatment") or "none"
    rec_lines = [{"department": i.get("department"), "item": i.get("item"), "amount": float(i.get("recovery_amount") or 0),
                  "note": i.get("comment")} for i in items if i.get("status") == "cleared_with_recovery"]
    base = {"case_id": str(case.get("id")), "employee_id": str(case.get("employee_id")),
            "last_working_day": lwd.isoformat() if lwd else None,
            "clearance_recoveries": rec_lines, "clearance_recovery_total": cs["recovery_total"],
            "notice": {"days": int(case.get("notice_days") or 0), "served": int(case.get("notice_served_days") or 0),
                       "shortfall": short, "treatment": treat}}
    return {**base, "kind": "canada_final", "calculator": "/offboarding",
            "body": {"employee_id": str(case.get("employee_id")), "last_day": base["last_working_day"],
                     "reason": case.get("roe_reason_code") or ROE_SUGGESTED.get(case.get("exit_type") or "", "K"),
                     "extra_earnings": [],
                     "extra_deductions": [{"code": "RECOVERY", "amount": cs["recovery_total"]}] if cs["recovery_total"] > 0 else [],
                     "case_id": str(case.get("id"))},
            "manual": (["Notice pay in lieu / termination pay is NOT worked out here – enter it as an extra earning "
                        "in the final-pay screen (the amount depends on provincial rules)."] if treat == "pay_in_lieu" else [])
                      + (["Notice buy-out recovery is not worked out for Canada – enter it as a deduction if it applies."]
                         if treat == "buyout" and short > 0 else [])}


# ── letters ───────────────────────────────────────────────────────────────────
def letter_allowed(key: str, case: dict, items: list[dict], settings: dict | None,
                   override_reason: str | None = None) -> tuple[bool, str]:
    if key not in LETTER_KEYS:
        return False, f"Unknown letter. Use one of: {', '.join(LETTER_KEYS)}"
    st = {**DEFAULT_SETTINGS, **(settings or {})}
    status = case.get("status")
    if key == "resignation_acceptance":
        if case.get("exit_type") != "resignation":
            return False, "A resignation acceptance letter is only for a resignation"
        if status not in AFTER_ACCEPTANCE:
            return False, "Accept the resignation first"
        return True, ""
    if status not in AFTER_ACCEPTANCE:
        return False, "The exit is not accepted yet"
    if key in LETTERS_HELD and st["hold_letters_until_settled"] and not (override_reason or "").strip():
        if not clearance_summary(items)["complete"]:
            return False, "Held until clearance is complete (HR can override with a reason)"
        if status not in ("settled", "closed"):
            return False, "Held until the settlement is paid (HR can override with a reason)"
    return True, ""


def letters_done(case: dict) -> bool:
    """The case can close when the relieving letter and the experience letter are issued."""
    issued = {l.get("key") for l in (case.get("letters") or [])}
    return {"relieving", "experience"} <= issued


# ── last working day ──────────────────────────────────────────────────────────
def separation_due(case: dict, today: date) -> bool:
    """True when the last-working-day tick must act: an ACCEPTED exit whose last working
    day has arrived and which has not been separated yet (idempotent on separated_at)."""
    lwd = to_date(case.get("lwd"))
    return bool(lwd and lwd <= today and not case.get("separated_at") and case.get("status") in AFTER_ACCEPTANCE)


def separation_patch(case: dict) -> dict:
    """What the employee record becomes on the last working day: the REAL exit date."""
    return {"status": "terminated", "termination_date": to_date(case.get("lwd"))}


# ── rehire ────────────────────────────────────────────────────────────────────
def rehire_plan(employee: dict, spells: list[dict], new_hire_date, policy: str = "keep",
                new_number: str | None = None) -> tuple[dict | None, str]:
    """A new employment spell for a FORMER employee, on the SAME person record so the
    statutory identity (SIN) and the year-to-date payroll ledger carry over.
    → ({closing_spell, new_spell, employee_patch}, error)."""
    if (employee.get("status") or "") != "terminated":
        return None, "Only a former employee can be rehired"
    last = max(spells, key=lambda s: int(s.get("spell_no") or 0)) if spells else None
    if last and last.get("rehire_eligible") is False:
        return None, "This person is marked not eligible for rehire"
    nh = to_date(new_hire_date)
    if not nh:
        return None, "Enter the new joining date"
    exit_d = to_date(employee.get("termination_date"))
    if exit_d and nh <= exit_d:
        return None, f"The new joining date must be after the exit date ({exit_d.isoformat()})"
    if policy == "new" and not (new_number or "").strip():
        return None, "Enter the new employee number"
    number = (new_number or "").strip() if policy == "new" else employee.get("employee_number")
    prev_no = int(last.get("spell_no") or 1) if last else 1
    closing = {"spell_no": prev_no, "employee_number": employee.get("employee_number"),
               "hire_date": to_date(employee.get("hire_date")), "exit_date": exit_d}
    new = {"spell_no": prev_no + 1, "employee_number": number, "hire_date": nh, "exit_date": None,
           "previous_spell_no": prev_no}
    patch = {"status": "active", "hire_date": nh, "termination_date": None, "employee_number": number,
             "probation_end_date": None}
    return {"closing_spell": closing, "new_spell": new, "employee_patch": patch}, ""


# 2026-10-24 forms wrap-up: the new spell's terms HR may set on the Rehire form (all plain
# employee-record columns). Blank = keep what the record has (probation stays cleared).
REHIRE_TERMS = ("job_title", "department", "annual_salary", "employment_type", "work_location", "grade",
                "province_of_employment", "probation_end_date", "manager_id")


def rehire_terms(body: dict, new_hire_date, employment_types, regions) -> tuple[dict, str]:
    """Clean the optional new-spell terms → (patch, error)."""
    out: dict = {}
    for k in REHIRE_TERMS:
        v = body.get(k)
        if v in (None, ""):
            continue
        if k == "annual_salary":
            try:
                v = float(v)
            except (TypeError, ValueError):
                return {}, "Annual salary must be a number"
            if v < 0:
                return {}, "Annual salary cannot be negative"
        elif k == "employment_type" and v not in employment_types:
            return {}, f"employment_type must be one of {', '.join(employment_types)}"
        elif k == "province_of_employment":
            v = str(v).upper()
            if v not in regions:
                return {}, "Choose a valid state of employment"
        elif k == "probation_end_date":
            v = to_date(v)
            nh = to_date(new_hire_date)
            if not v:
                return {}, "probation_end_date must be YYYY-MM-DD"
            if nh and v < nh:
                return {}, "Probation cannot end before the new joining date"
        elif k == "manager_id":
            import uuid as _uuid
            try:
                v = _uuid.UUID(str(v))
            except ValueError:
                return {}, "Choose the reporting manager from the list"
        elif isinstance(v, str):
            v = v.strip()[:120]
        out[k] = v
    return out, ""


# ── attrition summary (from real cases) ───────────────────────────────────────
def attrition_summary(cases: list[dict]) -> dict:
    """Exits by month / reason / department and the average notice served. Counts cases
    that were ACCEPTED (withdrawn and rejected ones are not exits)."""
    exits = [c for c in cases if c.get("status") in AFTER_ACCEPTANCE and to_date(c.get("lwd"))]
    by_month: dict = {}
    by_type: dict = {}
    by_dept: dict = {}
    by_reason: dict = {}
    served, short = [], 0
    for c in exits:
        lwd = to_date(c["lwd"])
        by_month[lwd.strftime("%Y-%m")] = by_month.get(lwd.strftime("%Y-%m"), 0) + 1
        t = EXIT_TYPES.get(c.get("exit_type") or "", c.get("exit_type") or "Other")
        by_type[t] = by_type.get(t, 0) + 1
        d = c.get("department") or "No department"
        by_dept[d] = by_dept.get(d, 0) + 1
        r = (c.get("reason_category") or "").strip() or "Not given"
        by_reason[r] = by_reason.get(r, 0) + 1
        if c.get("notice_served_days") is not None and int(c.get("notice_days") or 0) > 0:
            served.append(int(c["notice_served_days"]))
        if int(c.get("notice_shortfall_days") or 0) > 0:
            short += 1

    def rows(d):
        return [{"label": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {"total_exits": len(exits),
            "by_month": [{"label": k, "count": v} for k, v in sorted(by_month.items())],
            "by_type": rows(by_type), "by_department": rows(by_dept), "by_reason": rows(by_reason),
            "average_notice_served_days": round(sum(served) / len(served), 1) if served else None,
            "notice_cases": len(served), "short_notice_cases": short,
            "open_cases": sum(1 for c in cases if c.get("status") in OPEN_STATES),
            "withdrawn": sum(1 for c in cases if c.get("status") == "withdrawn")}
