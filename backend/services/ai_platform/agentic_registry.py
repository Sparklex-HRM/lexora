"""Agent registry + risk-tier policy + executor registry (phase 1 = metadata and rails; the checks
and executors arrive with the agents in phase 2).

Plug-in contract for a phase-2 agent module
    from agentic_registry import register_executor, tier_for, HARD_DENY
    @register_executor("exit.run_final_pay")                # one deterministic executor per tool name
    async def _x(org: str, proposal: dict, args: dict) -> dict:   # args = the RAW (unsealed) tool args
        ...call the ordinary service API as the approver (RBAC holds)...
        return {"id": ..., "status": "done"}                # small JSON; stored on the proposal

Risk tiers (report §Trust): 0 auto-logged · 1 one named approver · 2 two DIFFERENT approvers
(bank, filings, money, bulk) · deny. ANY untrusted content in the run (an uploaded PDF, an inbound
e-mail, portal text) raises every outbound tool one tier. Aggregate impact above the org thresholds
(bulk headcount, $) also lifts a tool to tier 2. Hard-deny tools never become proposals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

AGENT_VERSION = "1.0"


@dataclass(frozen=True)
class AgentSpec:
    key: str
    role_name: str                  # the product name — a role, never "AI" / "Copilot"
    does: str                       # what it finishes end to end
    you_approve: str                # what the person approves
    triggers: tuple                 # run triggers (metadata; the scheduler wiring is phase 2)
    tier_map: dict = field(default_factory=dict)   # tool_name → base tier (0 / 1 / 2)
    never: tuple = ()               # hard limits spelled out for the screen
    link: str = "/ai/agents"


REGISTRY: dict[str, AgentSpec] = {
    # ── Payroll Auditor (Canada, restored 2026-10-26) — agents/payroll_auditor ──
    "payroll_auditor": AgentSpec(
        key="payroll_auditor", role_name="Payroll Auditor",
        does=("Starts a few days before each pay date (per pay frequency) and when the timesheets are locked: collects the period's "
              "inputs, finds the gaps (joiners without SIN, direct deposit or TD1 forms, unpaid days against pending regularisations or "
              "leave, final pay and ROE clocks for leavers, retro pay without an approved revision), chases the right person until "
              "fixed, runs the 17 deterministic checks on the engine's draft — CPP / QPP, EI, QPIP and the YTD maximums re-checked "
              "from the payroll engine's own constants, register = GL journal — and writes a plain-words reason per big change. "
              "Hands payroll a review pack: 'N ready, M need a look' plus the waiting-on list."),
        you_approve="Flagged payroll lines and input changes (one approver); anything touching direct-deposit details, net pay above the "
                    "threshold, off-cycle pay, the run hand-off to Payroll or more than the bulk limit of employees needs two different approvers.",
        triggers=("pay_date_minus_n", "attendance_lock", "manual"),
        tier_map={"run_checks": 0, "prepare_review_pack": 0, "notify_owner": 0, "explain_variance": 0,
                  "adjust_payroll_input": 1, "update_master_field": 1, "hold_payroll_line": 1,
                  "change_bank_details": 2, "net_pay_adjustment_over_threshold": 2, "off_cycle_payment": 2, "bulk_change": 2,
                  "generate_bank_file": 2, "release_payslips": 2,
                  # executors (agents/payroll_auditor/executors.py): chase + explain are logged; the input freeze = one approver;
                  # the hand-off to Payroll and ready-to-release = two DIFFERENT approvers (the agent never changes pay)
                  "payroll.chase_input": 0, "payroll.mark_variance_explained": 0, "payroll.lock_inputs": 1,
                  "payroll.send_for_approval": 2, "payroll.ready_to_release": 2},
        never=("calculates pay itself — the payroll engine does", "changes a salary or a deduction", "commits or voids a pay run",
               "skips an employee", "moves money", "invents a statutory rate — every figure comes from the payroll engine's constants"),
        link="/ai/agents/payroll-auditor",
    ),
    "hr_desk": AgentSpec(
        key="hr_desk", role_name="HR Desk",
        does=("Answers employees from their OWN records and the uploaded policies with the clause cited (pay stub line explained — "
              "CPP / QPP, EI, income tax, vacation pay — leave, statutory holidays, approver, TD1, T4 / RL-1), starts requests as "
              "confirm-cards, and hands over to a helpdesk ticket with the conversation attached when unsure, sensitive or asked. "
              "Weekly 'top questions' report for HR."),
        you_approve="Ticket closures, outbound e-mails and record changes (one approver). Workplace harassment / violence / medical / "
                    "grievance / disciplinary and another person's data are never answered by the model — a fixed message and a sealed referral.",
        triggers=("ticket", "question", "weekly_report"),
        tier_map={"answer_from_own_record": 0, "cite_policy": 0, "draft_reply": 0, "hand_off_ticket": 0,
                  # phase 2 (P2-C): the desk's own executing tools — a hand-off ticket is auto (logged); closing one needs a person
                  "desk.create_ticket": 0, "desk.weekly_report": 0, "desk.close_ticket": 1,
                  "close_ticket": 1, "send_email": 1, "update_master_field": 1, "apply_leave_for_employee": 1,
                  "bulk_change": 2},
        never=("answers harassment, medical or grievance questions", "shows another person's data", "closes a ticket the employee disputes"),
    ),
    # ── employee-lifecycle agents (2026-10-26) — agents/onboarding · agents/exit · agents/attendance ──
    "onboarding": AgentSpec(
        key="onboarding", role_name="Onboarding Agent",
        does=("Starts when an offer is accepted or a joiner is created: checks the joining checklist (photo ID, address and postal code, "
              "education, direct-deposit institution / transit / account, SIN inside the CRA 3-day window, work-permit expiry for a "
              "temporary resident, TD1 federal and provincial forms) against the documents vault, drafts the appointment letter from "
              "your template, opens IT / admin / induction tasks and chases the joiner, HR and IT (deduplicated) until the file is "
              "complete for payroll."),
        you_approve="The appointment-letter draft (one approver) — HR then reviews and issues it from Letters; the agent never issues a letter.",
        triggers=("offer_accepted", "joiner", "daily", "manual"),
        tier_map={"onboarding.chase": 0, "onboarding.draft_appointment_letter": 1},
        never=("issues a letter", "changes a joiner's bank details or SIN", "files anything with the CRA or Service Canada"),
        link="/ai/agents/onboarding",
    ),
    "exit": AgentSpec(
        key="exit", role_name="Exit Agent",
        does=("Starts on a resignation / exit case: shows notice and the last working day, chases every clearance (IT, finance, admin, "
              "manager, HR) until signed, asks the payroll service to run the final pay (vacation payout, extras, clearance "
              "recoveries) inside the provincial final-pay clock, reminds payroll to file the ROE within 5 days after the pay "
              "period of the last day, and drafts the separation and employment reference letters."),
        you_approve=("Running the final pay needs two different approvers (it is pay); the letter drafts need one approver."),
        triggers=("exit_case", "daily", "manual"),
        tier_map={"exit.chase": 0, "exit.run_final_pay": 2, "exit.draft_exit_letters": 1},
        never=("computes the final pay itself — the payroll service does", "files the ROE", "issues a letter", "changes the last working day"),
        link="/ai/agents/exit",
    ),
    "attendance": AgentSpec(
        key="attendance", role_name="Attendance Agent",
        does=("Every day: missing and single punches, late marks, regularisations waiting for the manager and unapproved leave — "
              "asks the employee to regularise, then the manager to decide (48–72 h), so the pay run sees clean hours before the "
              "cut-off."),
        you_approve="Nothing executes but reminders (logged): the people decide on the existing screens.",
        triggers=("daily", "manual"),
        tier_map={"attendance.chase": 0},
        never=("edits a punch", "approves a regularisation or leave", "publishes a roster"),
        link="/ai/agents/attendance",
    ),
}

# Tools no proposal is ever created for (report §Trust). Checked by name AND by shape (agentic_proposals).
HARD_DENY: dict[str, str] = {
    "pay_unlisted_account": "Pay to a bank account that is not in the employee master.",
    "change_agent_permissions": "Change the agent's own permissions or approver roles.",
    "disable_audit": "Switch off or alter the audit / action log.",
    "delete_action_log": "Remove rows from the action log.",
    # phase 2 (P2-C, HR Desk): anything that would read or show another person's data is never a proposal
    "desk.read_other_person_data": "Read, show or use another employee's records in an HR Desk answer or ticket.",
}
DEFAULT_TIER = 1                                             # an unknown tool is never auto-run
TIER_LABEL = {0: "Auto (logged)", 1: "One approver", 2: "Two approvers"}


def base_tier(agent: str, tool_name: str) -> int:
    spec = REGISTRY.get(agent)
    if spec and tool_name in spec.tier_map:
        return int(spec.tier_map[tool_name])
    return DEFAULT_TIER


def tier_for(agent: str, tool_name: str, *, untrusted_input: bool = False, aggregate_impact: dict | None = None,
             thresholds: dict | None = None) -> dict:
    """{base, tier, reasons[]} — base tier from the agent's map, +1 when the run held untrusted content,
    lifted to 2 when the impact crosses the org's bulk / $ thresholds. Capped at 2. Hard-deny → tier 'deny'."""
    if tool_name in HARD_DENY:
        return {"base": None, "tier": "deny", "reasons": [HARD_DENY[tool_name]]}
    b = base_tier(agent, tool_name)
    tier, reasons = b, []
    th = thresholds or {}
    imp = aggregate_impact or {}
    try:
        heads = int(imp.get("headcount") or 0)
        dollars = abs(float(imp.get("dollars") or 0))
    except (TypeError, ValueError):
        heads, dollars = 0, 0.0
    bulk = int(th.get("bulk_employees") or 25)
    if heads >= bulk and tier < 2:
        tier, reasons = 2, reasons + [f"affects {heads} employees (bulk limit {bulk})"]
    floor = float(th.get("net_delta_floor_cad") or 0)
    pct = float(th.get("net_delta_pct") or 0)
    if dollars and floor and dollars >= floor * 10 and tier < 2:      # ten times the per-line floor = money-class impact
        tier, reasons = 2, reasons + [f"${dollars:,.0f} impact is money-class (≥ 10 × floor {floor:,.0f}{', ' + str(pct) + '% rule' if pct else ''})"]
    if untrusted_input:
        if tier < 2:
            reasons.append("run contained untrusted content (uploaded file / inbound mail / portal text): +1 tier")
        else:
            reasons.append("run contained untrusted content; already at the top tier")
        tier = min(2, tier + 1)
    return {"base": b, "tier": tier, "reasons": reasons}


# ══════════════════════════ executor registry ═══════════════════════════════════

Executor = Callable[[str, dict, dict], Awaitable[dict]]
EXECUTORS: dict[str, Executor] = {}


def register_executor(tool_name: str):
    """Phase-2 agents register ONE deterministic executor per tool name. Tools without an executor are
    approved but not executed (the proposal says so) — nothing is ever improvised."""
    def deco(fn: Executor):
        EXECUTORS[tool_name] = fn
        return fn
    return deco


def executor_for(tool_name: str) -> Executor | None:
    return EXECUTORS.get(tool_name)


def registry_dict() -> list[dict]:
    return [{"key": a.key, "role_name": a.role_name, "does": a.does, "you_approve": a.you_approve, "triggers": list(a.triggers),
             "tier_map": {k: {"tier": v, "label": TIER_LABEL[v]} for k, v in a.tier_map.items()}, "never": list(a.never),
             "version": AGENT_VERSION, "executors_registered": sorted(t for t in a.tier_map if t in EXECUTORS), "link": a.link}
            for a in REGISTRY.values()]
