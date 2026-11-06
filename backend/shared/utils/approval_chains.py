"""Lexora HRM — no-code approval chains (stream D1).

An org defines ONE chain per request type ("kind" in the approvals registry). A chain
is a JSON definition the Settings screen edits; the pure functions here turn it into a
RUN for one request at open time, and move the run forward as people decide.

DEFINITION (stored in ``apr_chains.definition``)
    {"steps": [
        {"id": "s1", "name": "Reporting manager", "mode": "any",        # any | all (parallel)
         "approvers": [{"type": "manager", "level": 1}],
         "when": [],                                                    # conditions — all must hold
         "escalate_after_hours": 48, "escalate_to": {"type": "role", "role": "hr"},
         "sla_hours": 72},
        {"id": "s2", "name": "Finance", "mode": "all",
         "approvers": [{"type": "role", "role": "finance"}, {"type": "user", "user_id": "..."}],
         "when": [{"field": "amount", "op": "gte", "value": 10000}]}],
     "auto_approve": [{"name": "Small claims", "when": [{"field": "amount", "op": "lt", "value": 500}]}],
     "sla_hours": 72}

APPROVER SPECS
    {"type": "caller"}                      whoever the owning screen named (the seeded default —
                                            behaviour is unchanged until the org edits the chain)
    {"type": "role", "role": "hr"}          a role family (shared.utils.directory.ROLE_FAMILIES)
    {"type": "manager", "level": N}         the requester's manager (1), their manager (2), …
    {"type": "user", "user_id": "…"}        a named person
    {"type": "any_of", "approvers": [...]}  a group — any one of them (inside an "all" step)

CONTEXT is a flat dict the owning service passes to ``open_item(..., context=...)``:
amount, department, entity_id, leave_days, employee_id, grade, location, claim_type …
Unknown fields in a condition simply do not match. Nothing here touches the database.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.utils.directory import role_in
from shared.utils.platform_models import PlatformBase, _org, _pk, now_utc

# ══════════════════════════ request-type registry ══════════════════════════════
# key → label, the context fields a condition may use, and who (which stream) opens it.
REQUEST_TYPES: dict[str, dict] = {
    "leave": {"label": "Leave", "fields": ["leave_days", "leave_type", "department", "employee_id"]},
    "timesheet": {"label": "Timesheet", "fields": ["total_hours", "ot_hours", "department"]},
    "attendance_fix": {"label": "Attendance fix", "fields": ["reason", "department"]},
    "expense": {"label": "Expense / reimbursement", "fields": ["amount", "claim_type", "department", "grade", "entity_id",
                                                              "location", "policy_violation", "policy_flags", "has_receipt"]},
    "salary_revision": {"label": "Salary revision", "fields": ["amount", "change_pct", "department", "grade", "entity_id"]},
    "career_change": {"label": "Career change", "fields": ["change_type", "department", "grade"]},
    "confirmation": {"label": "Confirmation", "fields": ["department", "grade"]},
    "exit_case": {"label": "Exit case", "fields": ["exit_type", "department", "grade", "notice_days"]},
    "clearance_line": {"label": "Clearance line", "fields": ["department"]},
    "requisition": {"label": "Hiring requisition", "fields": ["openings", "budget_max", "department", "entity_id"]},
    "offer": {"label": "Offer", "fields": ["amount", "above_budget", "department", "entity_id"]},
    "hiring_offer": {"label": "Hiring offer", "fields": ["amount", "above_budget", "department", "grade", "entity_id"]},
    "goal_approval": {"label": "Goal approval", "fields": ["department"]},
    "appraisal_stage": {"label": "Appraisal stage", "fields": ["department", "stage"]},
    "comp_cycle": {"label": "Compensation cycle", "fields": ["amount", "budget_pct", "department", "entity_id"]},
    "custom_field_request": {"label": "Custom field change request", "fields": ["entity", "field_key"]},
    "device_change": {"label": "Phone change", "fields": []},
    # 2026-10-16 employee portal
    "permission": {"label": "Permission (short hours)", "fields": ["hours", "department", "employee_id"]},
    "leave_cancel": {"label": "Cancel approved leave", "fields": ["leave_days", "leave_type", "department", "employee_id"]},
    "optional_holiday": {"label": "Optional holiday", "fields": ["department", "employee_id"]},
    "comp_off": {"label": "Compensatory off", "fields": ["leave_days", "hours", "department", "employee_id"]},
    "employee_request": {"label": "Employee request (Raise a request)", "fields": ["request_type", "department", "employee_id"]},
    # 2026-10-23 G2: two employees swap rostered shifts (both agreed) → the manager approves
    "shift_swap": {"label": "Shift swap", "fields": ["department", "employee_id", "night_shift", "days_apart"]},
}
CONTEXT_FIELD_LABELS = {
    "amount": "Amount ($)", "change_pct": "Change %", "department": "Department", "entity_id": "Legal entity",
    "leave_days": "Leave days", "leave_type": "Leave type", "employee_id": "Employee", "grade": "Grade",
    "location": "Location", "claim_type": "Claim type", "total_hours": "Total hours", "ot_hours": "Overtime hours",
    "reason": "Reason", "change_type": "Change type", "exit_type": "Exit type", "notice_days": "Notice days",
    "openings": "Openings", "budget_max": "Budget max", "above_budget": "Above budget", "stage": "Stage",
    "budget_pct": "Budget %", "entity": "Entity", "field_key": "Field", "units": "Units", "employment_type": "Employment type",
    "hours": "Hours", "request_type": "Request type",
    "policy_violation": "Breaks an expense policy", "policy_flags": "Policy flags (count)", "has_receipt": "Receipt attached",
    "night_shift": "Night shift involved", "days_apart": "Days between the two shifts",
}
OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_empty", "not_empty")
OP_LABELS = {"eq": "is", "ne": "is not", "gt": "more than", "gte": "at least", "lt": "less than", "lte": "at most",
             "in": "is one of", "not_in": "is none of", "contains": "contains", "is_empty": "is empty", "not_empty": "is not empty"}
ROLE_CHOICES = [("org_admin", "Org admin"), ("hr", "HR (any HR role)"), ("hr_admin", "HR head"), ("hr_manager", "HR manager"),
                ("payroll", "Payroll"), ("finance", "Finance"), ("manager", "Any manager")]


def default_definition() -> dict:
    """The seeded chain: ONE step, whoever the owning screen names. Unchanged behaviour."""
    return {"steps": [{"id": "s1", "name": "Approver named by the screen", "mode": "any",
                       "approvers": [{"type": "caller"}], "when": [], "escalate_after_hours": 0,
                       "escalate_to": None, "sla_hours": 0}],
            "auto_approve": [], "sla_hours": 0}


def type_label(kind: str) -> str:
    return REQUEST_TYPES.get(kind, {}).get("label") or (kind or "").replace("_", " ").capitalize()


# ══════════════════════════ tables (created by the approvals service) ══════════

class ApprovalChain(PlatformBase):
    """One org's chain for one request type. Edits bump ``version``; a run keeps the
    version it was resolved with."""
    __tablename__ = "apr_chains"
    __table_args__ = (UniqueConstraint("org_id", "request_type", name="uq_apr_chain"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    request_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)     # still the seeded one-step chain
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ApprovalRun(PlatformBase):
    """The resolved chain of ONE inbox item (one per item per cycle)."""
    __tablename__ = "apr_chain_runs"
    __table_args__ = (UniqueConstraint("org_id", "item_id", "cycle", name="uq_apr_run"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    item_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    ref_id: Mapped[str] = mapped_column(String(80))
    cycle: Mapped[int] = mapped_column(Integer, default=1)
    chain_id: Mapped[str | None] = mapped_column(String(36))
    chain_name: Mapped[str | None] = mapped_column(String(160))
    chain_version: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    current: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|auto|approved|rejected|closed
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    history: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    step_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


CHAIN_TABLES = [ApprovalChain.__table__, ApprovalRun.__table__]


# ══════════════════════════ conditions ═════════════════════════════════════════

def _num(v):
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def condition_holds(cond: dict, context: dict) -> bool:
    field, op = str(cond.get("field") or ""), str(cond.get("op") or "eq")
    if not field or op not in OPS:
        return False
    have = (context or {}).get(field)
    want = cond.get("value")
    if op == "is_empty":
        return have in (None, "", [], {})
    if op == "not_empty":
        return have not in (None, "", [], {})
    if have is None:
        return False
    if isinstance(have, (list, tuple, set)):             # e.g. department = ["Sales", "Ops"] (a comp cycle): any element
        return any(condition_holds({**cond}, {**context, field: x}) for x in have)
    if op in ("in", "not_in"):
        opts = want if isinstance(want, list) else [x.strip() for x in str(want or "").split(",") if x.strip()]
        hit = str(have).strip().lower() in {str(o).strip().lower() for o in opts}
        return hit if op == "in" else not hit
    if op == "contains":
        return str(want or "").lower() in str(have).lower()
    a, b = _num(have), _num(want)
    if a is not None and b is not None:
        return {"eq": a == b, "ne": a != b, "gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    sa, sb = str(have).strip().lower(), str(want if want is not None else "").strip().lower()
    if op == "eq":
        return sa == sb
    if op == "ne":
        return sa != sb
    return {"gt": sa > sb, "gte": sa >= sb, "lt": sa < sb, "lte": sa <= sb}[op]


def conditions_hold(conds, context: dict) -> bool:
    return all(condition_holds(c, context) for c in (conds or []) if isinstance(c, dict))


def describe_condition(c: dict) -> str:
    f = CONTEXT_FIELD_LABELS.get(c.get("field"), c.get("field") or "?")
    op = OP_LABELS.get(c.get("op"), c.get("op"))
    if c.get("op") in ("is_empty", "not_empty"):
        return f"{f} {op}"
    v = c.get("value")
    return f"{f} {op} {', '.join(map(str, v)) if isinstance(v, list) else v}"


# ══════════════════════════ validation ═════════════════════════════════════════

class ChainError(ValueError):
    pass


def validate_definition(d: dict, *, users: dict[str, dict] | None = None) -> dict:
    """Normalise + check a definition the screen sends. Raises ChainError with a plain sentence."""
    if not isinstance(d, dict):
        raise ChainError("The chain definition must be an object")
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ChainError("A chain needs at least one step")
    if len(steps) > 12:
        raise ChainError("A chain can have at most 12 steps")
    out_steps = []
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict):
            raise ChainError(f"Step {i} is not valid")
        approvers = [a for a in (s.get("approvers") or []) if isinstance(a, dict)]
        if not approvers:
            raise ChainError(f"Step {i} needs at least one approver")
        for a in approvers:
            _check_spec(a, i, users)
        mode = (s.get("mode") or "any").lower()
        if mode not in ("any", "all"):
            raise ChainError(f"Step {i}: mode must be any or all")
        for c in s.get("when") or []:
            if not isinstance(c, dict) or not c.get("field") or c.get("op") not in OPS:
                raise ChainError(f"Step {i}: a condition needs a field and a known operator")
        esc = s.get("escalate_to")
        if esc:
            _check_spec(esc, i, users)
            if esc.get("type") == "caller":
                raise ChainError(f"Step {i}: cannot escalate to 'the screen's approver'")
        out_steps.append({"id": str(s.get("id") or f"s{i}")[:20], "name": str(s.get("name") or f"Step {i}")[:120],
                          "mode": mode, "approvers": approvers, "when": [c for c in (s.get("when") or []) if isinstance(c, dict)],
                          "escalate_after_hours": max(0, int(s.get("escalate_after_hours") or 0)),
                          "escalate_to": esc or None, "sla_hours": max(0, int(s.get("sla_hours") or 0))})
    autos = []
    for r in d.get("auto_approve") or []:
        if not isinstance(r, dict):
            continue
        conds = [c for c in (r.get("when") or []) if isinstance(c, dict)]
        if not conds:
            raise ChainError("An auto-approve rule needs at least one condition — a rule with none would approve everything")
        for c in conds:
            if not c.get("field") or c.get("op") not in OPS:
                raise ChainError("Auto-approve: a condition needs a field and a known operator")
        autos.append({"name": str(r.get("name") or "Auto-approve")[:120], "when": conds})
    return {"steps": out_steps, "auto_approve": autos, "sla_hours": max(0, int(d.get("sla_hours") or 0))}


def _check_spec(a: dict, step_no: int, users):
    t = a.get("type")
    if t == "role":
        if not a.get("role"):
            raise ChainError(f"Step {step_no}: pick a role")
    elif t == "manager":
        try:
            lvl = int(a.get("level") or 1)
        except (TypeError, ValueError):
            raise ChainError(f"Step {step_no}: manager level must be a number")
        if not 1 <= lvl <= 5:
            raise ChainError(f"Step {step_no}: manager level must be between 1 and 5")
        a["level"] = lvl
    elif t == "user":
        if not a.get("user_id"):
            raise ChainError(f"Step {step_no}: pick a person")
        if users is not None and str(a["user_id"]) not in users:
            raise ChainError(f"Step {step_no}: that person is not an active user of this company")
    elif t == "any_of":
        inner = [x for x in (a.get("approvers") or []) if isinstance(x, dict)]
        if not inner:
            raise ChainError(f"Step {step_no}: an 'any of' group needs at least one approver")
        for x in inner:
            if x.get("type") == "any_of":
                raise ChainError(f"Step {step_no}: groups cannot contain groups")
            _check_spec(x, step_no, users)
    elif t == "caller":
        pass
    else:
        raise ChainError(f"Step {step_no}: unknown approver type '{t}'")


# ══════════════════════════ resolution (definition + context → run) ════════════

def _name(u: dict | None) -> str:
    if not u:
        return ""
    return (f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()) or (u.get("email") or "")


def manager_at_level(employee_id: str | None, level: int, employees: dict[str, dict]) -> str | None:
    """Employee id of the manager ``level`` steps up, or None when the chain runs out."""
    cur = str(employee_id) if employee_id else None
    seen = set()
    for _ in range(level):
        e = employees.get(cur or "") if cur else None
        nxt = e.get("manager_id") if e else None
        if not nxt or str(nxt) in seen or str(nxt) == cur:
            return None
        seen.add(str(nxt))
        cur = str(nxt)
    return cur


def _resolve_spec(spec: dict, ctx: dict, caller: dict, employees: dict, users: dict, user_of_emp: dict) -> list[dict]:
    """One definition spec → one or more RESOLVED specs the inbox can target."""
    t = spec.get("type")
    if t == "caller":
        out = [{"type": "role", "role": r, "label": _role_label(r)} for r in (caller.get("approver_roles") or [])]
        out += [{"type": "user", "user_id": str(u), "label": _name(users.get(str(u))) or "Named person"}
                for u in (caller.get("approver_user_ids") or [])]
        if caller.get("approver_manager_of_employee_id"):
            emp = str(caller["approver_manager_of_employee_id"])
            out.append({"type": "manager", "employee_id": emp, "level": 1, "label": _manager_label(emp, 1, employees)})
        return out or [{"type": "role", "role": "org_admin", "label": "Org admin (nobody was named)"}]
    if t == "role":
        return [{"type": "role", "role": spec["role"], "label": _role_label(spec["role"])}]
    if t == "user":
        uid = str(spec["user_id"])
        return [{"type": "user", "user_id": uid, "label": _name(users.get(uid)) or "Named person"}]
    if t == "manager":
        lvl = int(spec.get("level") or 1)
        emp = str(ctx.get("employee_id") or caller.get("approver_manager_of_employee_id") or "")
        if not emp:
            return [{"type": "role", "role": "hr", "label": "HR (the request has no employee to find a manager for)"}]
        if lvl == 1:
            return [{"type": "manager", "employee_id": emp, "level": 1, "label": _manager_label(emp, 1, employees)}]
        mgr_emp = manager_at_level(emp, lvl, employees)
        uid = user_of_emp.get(mgr_emp or "")
        if mgr_emp and uid:
            return [{"type": "user", "user_id": uid, "level": lvl, "label": f"{_name(employees.get(mgr_emp))} (manager, level {lvl})"}]
        return [{"type": "role", "role": "hr", "label": f"HR (no manager at level {lvl})", "fallback": True}]
    if t == "any_of":
        out = []
        for x in spec.get("approvers") or []:
            out += _resolve_spec(x, ctx, caller, employees, users, user_of_emp)
        return [{"type": "any_of", "approvers": out, "label": " or ".join(o["label"] for o in out)}]
    return []


def _role_label(r: str) -> str:
    return dict(ROLE_CHOICES).get(r) or {"hr_staff": "HR staff", "payroll_admin": "Payroll admin", "employee": "Employee",
                                          "superadmin": "Superadmin"}.get(r, r.replace("_", " ").capitalize())


def _manager_label(emp: str, level: int, employees: dict) -> str:
    mgr = manager_at_level(emp, level, employees)
    who = _name(employees.get(mgr)) if mgr else ""
    person = _name(employees.get(emp)) or "the employee"
    return f"{who} (manager of {person})" if who else f"Manager of {person} (none linked yet)"


def resolve(definition: dict | None, context: dict | None, *, caller: dict | None = None,
            employees: dict[str, dict] | None = None, users: dict[str, dict] | None = None) -> dict:
    """Definition + context → the run's steps. Returns
    {"steps": [...], "auto_approved": bool, "auto_rule": name | None, "skipped": [step names whose conditions failed]}.
    ``caller`` = the approver_* the owning screen passed (used by the "caller" spec)."""
    d = definition or default_definition()
    ctx = dict(context or {})
    caller = caller or {}
    employees = {str(k): v for k, v in (employees or {}).items()}
    users = {str(k): v for k, v in (users or {}).items()}
    user_of_emp = {str(u["employee_id"]): str(uid) for uid, u in users.items() if u.get("employee_id")}
    for rule in d.get("auto_approve") or []:
        if rule.get("when") and conditions_hold(rule["when"], ctx):
            return {"steps": [], "auto_approved": True, "auto_rule": rule.get("name") or "Auto-approve", "skipped": []}
    steps, skipped = [], []
    for s in d.get("steps") or []:
        if s.get("when") and not conditions_hold(s["when"], ctx):
            skipped.append(s.get("name") or s.get("id") or "step")
            continue
        approvers = []
        for a in s.get("approvers") or []:
            approvers += _resolve_spec(a, ctx, caller, employees, users, user_of_emp)
        if not approvers:
            continue
        mode = (s.get("mode") or "any").lower()
        steps.append({"id": s.get("id"), "name": s.get("name") or f"Step {len(steps) + 1}", "mode": mode,
                      "approvers": approvers, "pending": list(range(len(approvers))),
                      "escalate_after_hours": int(s.get("escalate_after_hours") or 0),
                      "escalate_to": s.get("escalate_to") or None, "escalated": False,
                      "sla_hours": int(s.get("sla_hours") or d.get("sla_hours") or 0), "decided_by": []})
    if not steps:
        # every step was skipped by its conditions → nobody would approve; fall back to the caller's approvers
        fallback = _resolve_spec({"type": "caller"}, ctx, caller, employees, users, user_of_emp)
        steps.append({"id": "fallback", "name": "Approver named by the screen (no step matched)", "mode": "any",
                      "approvers": fallback, "pending": list(range(len(fallback))), "escalate_after_hours": 0,
                      "escalate_to": None, "escalated": False, "sla_hours": int(d.get("sla_hours") or 0), "decided_by": []})
    return {"steps": steps, "auto_approved": False, "auto_rule": None, "skipped": skipped}


# ══════════════════════════ a step → inbox targeting ═══════════════════════════

def _flat(specs: list[dict]) -> list[dict]:
    out = []
    for s in specs:
        out += _flat(s.get("approvers") or []) if s.get("type") == "any_of" else [s]
    return out


def targets(step: dict) -> dict:
    """The approver_roles / approver_user_ids / approver_manager_of_employee_id for a step's PENDING specs."""
    roles, uids, mgr_of = [], [], None
    pending = [step["approvers"][i] for i in step.get("pending") or [] if i < len(step.get("approvers") or [])]
    for s in _flat(pending):
        if s["type"] == "role" and s["role"] not in roles:
            roles.append(s["role"])
        elif s["type"] == "user" and s["user_id"] not in uids:
            uids.append(s["user_id"])
        elif s["type"] == "manager":
            mgr_of = mgr_of or s.get("employee_id")
    return {"approver_roles": roles, "approver_user_ids": uids, "approver_manager_of_employee_id": mgr_of}


def spec_matches(spec: dict, actor: dict, employees: dict[str, dict]) -> bool:
    """Does ``actor`` {user_id, role, employee_id} satisfy one resolved spec?"""
    t = spec.get("type")
    if t == "any_of":
        return any(spec_matches(x, actor, employees) for x in spec.get("approvers") or [])
    if t == "user":
        return str(actor.get("user_id") or "") == str(spec.get("user_id"))
    if t == "role":
        return role_in(actor.get("role"), [spec.get("role")])
    if t == "manager":
        e = employees.get(str(spec.get("employee_id") or ""))
        return bool(e and e.get("manager_id") and actor.get("employee_id") and str(e["manager_id"]) == str(actor["employee_id"]))
    return False


def apply_approval(step: dict, actor: dict, employees: dict[str, dict], *, by_name: str = "") -> bool:
    """Record one approval on the current step. Returns True when the step is complete.
    mode any → the first approval completes it; mode all → every pending spec must be satisfied."""
    hit = [i for i in list(step.get("pending") or []) if spec_matches(step["approvers"][i], actor, employees)]
    step.setdefault("decided_by", []).append({"user_id": str(actor.get("user_id") or ""), "name": by_name,
                                              "at": now_utc().isoformat()})
    if (step.get("mode") or "any") == "any" or not hit:
        step["pending"] = []
        return True
    step["pending"] = [i for i in step["pending"] if i not in hit]
    return not step["pending"]


def step_label(run_steps: list[dict], current: int) -> str:
    if not run_steps:
        return ""
    cur = run_steps[min(current, len(run_steps) - 1)]
    return f"Step {min(current, len(run_steps) - 1) + 1} of {len(run_steps)}: {cur.get('name') or ''}".strip()


def waiting_labels(step: dict) -> list[str]:
    return [step["approvers"][i].get("label") or step["approvers"][i].get("type") for i in step.get("pending") or []
            if i < len(step.get("approvers") or [])]


def sla_due(step: dict, step_started_at: datetime | None) -> datetime | None:
    h = int(step.get("sla_hours") or 0)
    if not h or not step_started_at:
        return None
    if step_started_at.tzinfo is None:
        step_started_at = step_started_at.replace(tzinfo=timezone.utc)
    return step_started_at + timedelta(hours=h)


def escalation_due(step: dict, step_started_at: datetime | None, now: datetime) -> bool:
    h = int(step.get("escalate_after_hours") or 0)
    if not h or step.get("escalated") or not step.get("escalate_to") or not step_started_at:
        return False
    if step_started_at.tzinfo is None:
        step_started_at = step_started_at.replace(tzinfo=timezone.utc)
    return now >= step_started_at + timedelta(hours=h)


def escalate(step: dict, context: dict, *, caller: dict | None = None, employees=None, users=None) -> list[dict]:
    """Add the escalation target to the step's pending approvers (the original approvers can still act).
    Returns the resolved specs that were added."""
    users = {str(k): v for k, v in (users or {}).items()}
    user_of_emp = {str(u["employee_id"]): str(uid) for uid, u in users.items() if u.get("employee_id")}
    added = _resolve_spec(step.get("escalate_to") or {}, context or {}, caller or {}, {str(k): v for k, v in (employees or {}).items()}, users, user_of_emp)
    for a in added:
        a["escalation"] = True
        step["approvers"].append(a)
        step.setdefault("pending", []).append(len(step["approvers"]) - 1)
    step["escalated"] = True
    return added


# ══════════════════════════ simulator text ═════════════════════════════════════

def explain(run: dict) -> list[str]:
    """Plain sentences for the test-run simulator."""
    if run.get("auto_approved"):
        return [f"Approved automatically by the rule '{run.get('auto_rule')}' — nobody has to decide."]
    lines = []
    for i, s in enumerate(run.get("steps") or [], start=1):
        who = [a.get("label") or a.get("type") for a in s.get("approvers") or []]
        joiner = " AND " if s.get("mode") == "all" else " or "
        extra = []
        if s.get("escalate_after_hours") and s.get("escalate_to"):
            extra.append(f"escalates after {s['escalate_after_hours']} h")
        if s.get("sla_hours"):
            extra.append(f"SLA {s['sla_hours']} h")
        lines.append(f"Step {i} — {s.get('name')}: {joiner.join(who)}" + (f" ({'; '.join(extra)})" if extra else ""))
    for name in run.get("skipped") or []:
        lines.append(f"Skipped: {name} (its conditions do not apply to this request)")
    return lines


# ══════════════════════════ the one resolver other streams call ════════════════
# Metadata keys other screens use → the context fields conditions are written against.
METADATA_ALIASES = {"ctc": "amount", "total_amount": "amount", "annual_ctc": "amount", "entity": "entity_id", "days": "leave_days",
                    "type": "claim_type", "dept": "department", "requester_employee_id": "employee_id"}


def normalise_metadata(metadata: dict | None) -> dict:
    out = {}
    for k, v in (metadata or {}).items():
        if v in (None, ""):
            continue
        out[METADATA_ALIASES.get(k, k)] = v
        out.setdefault(k, v)
    return out


def steps_for_callers(resolved: dict, *, chain_name: str, is_default: bool, employees: dict | None = None) -> list[dict]:
    """The run's steps in the shape D2 / D5 / anyone else consumes — ONE list that carries
    both conventions:
      roles[], user_ids[], manager_of_employee_id, mode (any|all), sla_hours, escalate_after_hours,
      label, approvers[labels], step (1-based), chain, is_default
      + kind / role / user_id / employee_id for the step's FIRST approver (one-approver-per-step
        callers: kind = manager (employee_id = the approving manager) | role | user)."""
    employees = {str(k): v for k, v in (employees or {}).items()}
    out = []
    for i, s in enumerate(resolved.get("steps") or [], start=1):
        t = targets(s)
        first = (_flat(s.get("approvers") or []) or [{}])[0]
        d = {"step": i, "name": s.get("name"), "label": " / ".join(waiting_labels(s)) or s.get("name"), "mode": s.get("mode") or "any",
             "sla_hours": int(s.get("sla_hours") or 0), "escalate_after_hours": int(s.get("escalate_after_hours") or 0),
             "roles": t["approver_roles"], "user_ids": t["approver_user_ids"], "manager_of_employee_id": t["approver_manager_of_employee_id"],
             "approvers": [a.get("label") for a in s.get("approvers") or []], "chain": chain_name, "is_default": is_default,
             "kind": None, "role": None, "user_id": None, "employee_id": None}
        if first.get("type") == "role":
            d.update(kind="role", role=first["role"])
        elif first.get("type") == "user":
            d.update(kind="user", user_id=first["user_id"])
        elif first.get("type") == "manager":
            mgr = manager_at_level(first.get("employee_id"), int(first.get("level") or 1), employees)
            if mgr:
                d.update(kind="manager", employee_id=mgr)
            else:
                d.update(kind="role", role="hr")
        out.append(d)
    return out


async def resolve_chain(org_id, request_type: str | None = None, metadata: dict | None = None, *, kind: str | None = None,
                        context: dict | None = None, approver_roles=None, approver_user_ids=None,
                        approver_manager_of_employee_id=None, employee_id: str | None = None) -> list[dict]:
    """ASYNC. The org's chain for ``request_type`` resolved against ``metadata`` → ordered steps
    (see ``steps_for_callers`` for the shape). Returns [] when the org has NOT customised the
    chain (seeded default) and you passed no approver_* of your own — keep using your own
    fallback chain then — and also [] when an auto-approve rule matches (nobody has to decide;
    ``metadata["_auto_rule"]`` is set to the rule's name). Never raises: any failure → [].

        steps = await resolve_chain(org, "hiring_offer", {"ctc": 950000, "department": "Sales", "entity": eid})
        steps = await resolve_chain(org, "comp_cycle", {"total_amount": 2.5e6, "department": ["Sales"]})

    The same function is exported as ``shared.utils.approvals_client.resolve_chain``."""
    try:
        from shared.utils import approvals_client as ac     # lazy: approvals_client imports this module
        key = (request_type or kind or "").strip().lower()
        ctx = normalise_metadata({**(context or {}), **(metadata or {})})
        if employee_id:
            ctx.setdefault("employee_id", str(employee_id))
        caller = {"approver_roles": list(approver_roles or []), "approver_user_ids": [str(u) for u in (approver_user_ids or [])],
                  "approver_manager_of_employee_id": approver_manager_of_employee_id or ctx.get("employee_id")}
        resolved, chain, employees = await ac.resolve_chain_run(str(org_id), key, ctx, caller=caller, with_employees=True)
        is_default = chain is None or bool(chain.is_default)
        if resolved.get("auto_approved"):
            if isinstance(metadata, dict):
                metadata["_auto_rule"] = resolved.get("auto_rule")
            return []
        if is_default and not (approver_roles or approver_user_ids or approver_manager_of_employee_id):
            return []
        return steps_for_callers(resolved, chain_name=(chain.name if chain else "Default"), is_default=is_default, employees=employees)
    except Exception as e:                                   # noqa: BLE001
        import logging
        logging.getLogger("lexora.approvals").warning("resolve_chain(%s) failed: %s", request_type or kind, e)
        return []


def new_run_row(org_id: str, item: dict, resolved: dict, *, chain=None, context: dict | None = None) -> ApprovalRun:
    return ApprovalRun(id=str(uuid.uuid4()), org_id=str(org_id), item_id=item["id"], kind=item["kind"], ref_id=item["ref_id"],
                       cycle=int(item.get("cycle") or 1), chain_id=getattr(chain, "id", None),
                       chain_name=getattr(chain, "name", None) or "Default", chain_version=int(getattr(chain, "version", 0) or 0),
                       steps=copy.deepcopy(resolved["steps"]), current=0,
                       status="auto" if resolved.get("auto_approved") else "pending", context=dict(context or {}),
                       history=[{"at": now_utc().isoformat(), "event": "resolved",
                                 "note": (f"auto-approve rule '{resolved.get('auto_rule')}'" if resolved.get("auto_approved")
                                          else f"{len(resolved['steps'])} step(s)")}],
                       started_at=now_utc(), step_started_at=now_utc())
