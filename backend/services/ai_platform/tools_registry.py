"""The ONE tool catalogue used by the chat assistant (Claude) and the MCP server.

Rules
* IDENTITY: every tool calls a sibling service with the caller's own identity headers,
  so RBAC holds end-to-end — the assistant can never show more than the screens do.
* SCOPE: ``read`` tools only read; ``act`` tools only PROPOSE (they return a confirmation
  card and write nothing). Execution lives in ``actions.py`` after explicit confirmation.
* ARGUMENTS: every tool has a pydantic model → JSON schema (Claude ``input_schema`` and MCP
  ``inputSchema``) and server-side validation; bad arguments never reach a service.
* CITATIONS: every read result carries ``source`` {label, link, system, records} — the
  assistant turns these into the citations shown next to the answer.
* AUDIT: every call (chat or MCP) is logged in ``ai_tool_calls`` with SHIELDED arguments.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, ValidationError

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.directory import role_in  # noqa: E402

import copilot as CP  # noqa: E402
from ai_models import AiToolCall  # noqa: E402
from shield import Shield  # noqa: E402
from shared.utils.org_time import local_today  # org-TZ business date (UTC store, local today)

ADMIN_ROLES = ("org_admin", "superadmin")
HR_FAMILY = ("org_admin", "superadmin", "hr")                       # role_in resolves the hr family
APPROVERS = ("org_admin", "superadmin", "hr", "manager")
EMPLOYEE_EDIT_ROLES = ("superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin")   # employee-service PATCH gate


# ══════════════════════════ context ═════════════════════════════════════════════

@dataclass
class Ctx:
    """Who is calling, from where, with which scopes. ``proposals`` collects the action
    cards a turn produced; the router / MCP layer persists them."""
    headers: dict
    channel: str = "chat"                                   # chat | mcp
    token_id: str | None = None
    scopes: set = field(default_factory=lambda: {"read", "act"})
    proposals: list = field(default_factory=list)
    log: bool = True

    def h(self, k: str) -> str:
        return (self.headers.get(k) or self.headers.get(k.lower()) or "")

    @property
    def org_id(self) -> str:
        return self.h("x-org-id")

    @property
    def user_id(self) -> str:
        return self.h("x-user-id")

    @property
    def user_email(self) -> str:
        return self.h("x-user-email")

    @property
    def role(self) -> str:
        return (self.h("x-role") or "").lower()

    @property
    def employee_id(self) -> str:
        return self.h("x-employee-id")


def allowed_for_role(role: str, roles) -> bool:
    if roles == "*" or "*" in roles:
        return True
    return role == "superadmin" or role_in(role, list(roles))


# ══════════════════════════ tool spec ═══════════════════════════════════════════

Handler = Callable[[Ctx, BaseModel], Awaitable[dict]]


@dataclass
class ToolSpec:
    name: str
    description: str
    scope: str                                              # read | act | admin
    roles: Any                                              # "*" or tuple of role keys / families
    args: type[BaseModel]
    handler: Handler
    risk: str = "normal"                                    # normal | high  (high = two confirmations)
    read_only: bool = True

    def input_schema(self) -> dict:
        sch = self.args.model_json_schema()
        sch.pop("title", None)
        sch["additionalProperties"] = False
        for p in sch.get("properties", {}).values():
            p.pop("title", None)
        sch.setdefault("required", [])
        return {"type": "object", "properties": sch.get("properties", {}), "required": sch["required"], "additionalProperties": False}


TOOLS: dict[str, ToolSpec] = {}


def register(name: str, description: str, *, scope: str, roles="*", args: type[BaseModel], risk: str = "normal"):
    def deco(fn: Handler):
        TOOLS[name] = ToolSpec(name=name, description=description, scope=scope, roles=roles, args=args, handler=fn,
                               risk=risk, read_only=(scope != "act"))
        return fn
    return deco


def visible_tools(role: str, scopes: set) -> list[ToolSpec]:
    out = []
    for t in TOOLS.values():
        if t.scope == "act" and "act" not in scopes:
            continue
        if t.scope == "admin" and "admin" not in scopes:
            continue
        if not allowed_for_role(role, t.roles):
            continue
        out.append(t)
    return out


def claude_tools(specs: list[ToolSpec]) -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema(), "strict": True} for t in specs]


def mcp_tool_dicts(specs: list[ToolSpec]) -> list[dict]:
    return [{"name": t.name, "description": t.description + ("" if t.read_only else " PROPOSES only — nothing happens until confirm_action is called."),
             "inputSchema": t.input_schema(), "readOnlyHint": t.read_only, "destructiveHint": (not t.read_only and t.risk == "high"),
             "scope": t.scope} for t in specs]


def validate_args(spec: ToolSpec, args: dict | None) -> tuple[BaseModel | None, str | None]:
    try:
        return spec.args.model_validate(args or {}), None
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(str(x) for x in err['loc']) or 'input'}: {err['msg']}" for err in e.errors()[:4])
        return None, f"Invalid arguments — {errs}"


async def call(ctx: Ctx, name: str, args: dict | None) -> dict:
    """Validate → RBAC/scope → run → log. Never raises; errors come back as {"error": …}."""
    t0 = time.monotonic()
    spec = TOOLS.get(name)
    if not spec:
        return await _log(ctx, name, args, {"error": f"Unknown tool {name}"}, t0)
    if (spec.scope == "act" and "act" not in ctx.scopes) or (spec.scope == "admin" and "admin" not in ctx.scopes):
        return await _log(ctx, name, args, {"error": f"This access token has no '{spec.scope}' scope."}, t0)
    if not allowed_for_role(ctx.role, spec.roles):
        return await _log(ctx, name, args, {"error": f"Your role ({ctx.role or 'none'}) cannot use {name}."}, t0)
    model, err = validate_args(spec, args)
    if err:
        return await _log(ctx, name, args, {"error": err}, t0)
    try:
        out = await spec.handler(ctx, model)
    except Exception as e:                                   # noqa: BLE001 — a tool must never take the turn down
        out = {"error": f"{name} failed: {type(e).__name__}"}
    return await _log(ctx, name, args, out, t0)


async def _log(ctx: Ctx, name: str, args, out: dict, t0: float) -> dict:
    if ctx.log and ctx.org_id:
        try:
            async with pm.session() as s:
                s.add(AiToolCall(org_id=ctx.org_id, user_id=ctx.user_id or None, token_id=ctx.token_id, channel=ctx.channel, tool=name,
                                 args=Shield().protect_value(dict(args or {})), ok="error" not in out, error=(out.get("error") or "")[:300] or None,
                                 duration_ms=int((time.monotonic() - t0) * 1000)))
                await s.commit()
        except Exception:                                    # noqa: BLE001
            pass
    return out


def src(label: str, link: str, system: str, records: list | None = None) -> dict:
    return {"label": label, "link": link, "system": system, "records": [str(r) for r in (records or [])][:20]}


# ══════════════════════════ argument models ═════════════════════════════════════

class NoArgs(BaseModel):
    pass


class MonthArg(BaseModel):
    month: str = Field("", description="YYYY-MM; empty = latest")


class MonthsArg(BaseModel):
    months: int = Field(6, ge=1, le=24)


class LineArg(BaseModel):
    line: str = Field(..., min_length=1, description="pay stub line, e.g. CPP, CPP2, QPP, EI, QPIP, income tax, vacation pay, regular pay")
    month: str = Field("", description="YYYY-MM; empty = latest")


class StatusArg(BaseModel):
    status: str = Field("", description="pending | approved | rejected | cancelled; empty = all")


class PeriodArg(BaseModel):
    start: str = Field("", description="YYYY-MM-DD; empty = first of the current month")
    end: str = Field("", description="YYYY-MM-DD; empty = today")


class QuestionArg(BaseModel):
    question: str = Field(..., min_length=2, max_length=400)


class TopicArg(BaseModel):
    topic: str = Field(..., min_length=2, max_length=120, description="e.g. CPP maximum, YMPE, EI insurable earnings, Ontario basic personal amount")


class RequestTypeArg(BaseModel):
    request_type: str = Field("leave", description="leave | attendance_fix | timesheet | expense | salary_revision | exit | requisition | letter")


class DirectoryArg(BaseModel):
    q: str = Field("", max_length=80, description="name, e-mail, title or department fragment")
    department: str = Field("", max_length=80)


class CalendarArg(BaseModel):
    year: int = Field(0, ge=0, le=2100, description="0 = current year")
    state: str = Field("", max_length=40, description="province code (ON, BC, QC …) for the provincial statutory holidays")


# ══════════════════════════ read tools ══════════════════════════════════════════

def _cp(ctx: Ctx) -> CP.Tools:
    return CP.Tools(ctx.headers, ctx.proposals)


def _today(ctx: Ctx) -> date:
    return date.fromisoformat(ctx.h("x-today")) if ctx.h("x-today") else local_today()


@register("my_profile", "The caller's own employee record: name, number, department, title, manager chain, joining date, location. No pay or identifiers.",
          scope="read", args=NoArgs)
async def my_profile(ctx: Ctx, a: NoArgs) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    data, err = await CP._get(f"{settings.EMPLOYEE_URL}/employees/{ctx.employee_id}/profile", ctx.headers)
    if err:
        return {"error": err}
    e = data.get("employee") or data
    keep = {k: e.get(k) for k in ("first_name", "last_name", "employee_number", "department", "job_title", "location", "branch", "hire_date",
                                  "employment_type", "status", "work_email") if e.get(k) is not None}
    chain = [{"name": m.get("name") or f"{m.get('first_name', '')} {m.get('last_name', '')}".strip(), "title": m.get("job_title")}
             for m in (data.get("manager_chain") or [])][:5]
    return {"profile": keep, "manager_chain": chain, "reports": len(data.get("reports") or []),
            "source": src("My profile", "/my-profile", "employee-service", [ctx.employee_id])}


@register("leave_balance", "The caller's leave balances for the current year, per leave type (allocated, used, pending, remaining).",
          scope="read", args=NoArgs)
async def leave_balance(ctx: Ctx, a: NoArgs) -> dict:
    r = await _cp(ctx).get_leave_balance()
    if "error" in r:
        return r
    r["source"] = src(f"Leave balances {r.get('fiscal_year') or ''}".strip(), "/leave", "leave-service", [b.get("code") for b in r.get("balances", [])])
    return r


@register("my_leave_requests", "The caller's own leave requests with status, dates and who it is waiting on.", scope="read", args=StatusArg)
async def my_leave_requests(ctx: Ctx, a: StatusArg) -> dict:
    params = {"scope": "mine"}
    if a.status:
        params["status"] = a.status
    data, err = await CP._get(f"{settings.LEAVE_URL}/leave/requests", ctx.headers, params)
    if err:
        return {"error": err}
    rows = data.get("items") if isinstance(data, dict) else data
    out = [{"id": r.get("id"), "leave_type": r.get("leave_type_name") or r.get("leave_type_code"), "start_date": r.get("start_date"), "end_date": r.get("end_date"),
            "days": r.get("days"), "status": r.get("status"), "half_day": r.get("half_day"), "reason": r.get("reason"), "manager": r.get("manager_name")}
           for r in (rows or [])][:40]
    return {"requests": out, "source": src("My leave requests", "/leave", "leave-service", [r["id"] for r in out])}


@register("team_leave_requests", "Leave requests of the caller's team (manager / HR): pending by default.", scope="read", roles=APPROVERS, args=StatusArg)
async def team_leave_requests(ctx: Ctx, a: StatusArg) -> dict:
    params = {"scope": "team", "status": a.status or "pending"}
    data, err = await CP._get(f"{settings.LEAVE_URL}/leave/requests", ctx.headers, params)
    if err:
        return {"error": err}
    rows = data.get("items") if isinstance(data, dict) else data
    out = [{"id": r.get("id"), "employee": r.get("employee_name"), "leave_type": r.get("leave_type_name") or r.get("leave_type_code"),
            "start_date": r.get("start_date"), "end_date": r.get("end_date"), "days": r.get("days"), "status": r.get("status"), "reason": r.get("reason")}
           for r in (rows or [])][:40]
    return {"requests": out, "source": src("Team leave requests", "/leave/approvals", "leave-service", [r["id"] for r in out])}


@register("attendance_summary", "The caller's attendance for a period: per-day status and hours, totals (present, absent, late, overtime). Default = this month.",
          scope="read", args=PeriodArg)
async def attendance_summary(ctx: Ctx, a: PeriodArg) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    today = _today(ctx)
    start = a.start or today.replace(day=1).isoformat()
    end = a.end or today.isoformat()
    data, err = await CP._get(f"{settings.TIME_URL}/time/attendance/{ctx.employee_id}", ctx.headers, {"start": start, "end": end})
    if err:
        return {"error": err}
    days = data.get("days") or data.get("items") or []
    rows = [{"date": d.get("work_date") or d.get("date"), "status": d.get("status"), "hours": d.get("actual_hours") or d.get("hours"),
             "first_in": d.get("first_in"), "last_out": d.get("last_out")} for d in days][:62]
    return {"period": {"start": start, "end": end}, "totals": data.get("totals") or data.get("summary") or {}, "days": rows,
            "source": src(f"Attendance {start} to {end}", "/attendance", "time-attendance-service", [ctx.employee_id])}


@register("my_regularisations", "The caller's attendance regularisation requests and their status.", scope="read", args=StatusArg)
async def my_regularisations(ctx: Ctx, a: StatusArg) -> dict:
    data, err = await CP._get(f"{settings.TIME_URL}/time/regularizations", ctx.headers, {"status": a.status} if a.status else None)
    if err:
        return {"error": err}
    rows = data.get("items") if isinstance(data, dict) else data
    mine = [r for r in (rows or []) if str(r.get("employee_id")) == str(ctx.employee_id)]       # the service does not filter employees
    out = [{"id": r.get("id"), "work_date": r.get("work_date"), "requested_in": r.get("requested_in"), "requested_out": r.get("requested_out"),
            "reason": r.get("reason"), "status": r.get("status"), "decided_by": r.get("decided_by_name")} for r in mine][:40]
    return {"regularisations": out, "source": src("My regularisation requests", "/attendance", "time-attendance-service", [r["id"] for r in out])}


@register("payslips", "The caller's recent payslips (month, pay date, gross, net, link).", scope="read", args=MonthsArg)
async def payslips(ctx: Ctx, a: MonthsArg) -> dict:
    r = await _cp(ctx).list_payslips(months=a.months)
    if "error" in r:
        return r
    r["source"] = src("My payslips", "/my-pay", "payroll-service", [p.get("month") for p in r.get("payslips", [])])
    return r


@register("payslip", "One payslip of the caller (YYYY-MM): gross, net, every deduction and component.", scope="read", args=MonthArg)
async def payslip(ctx: Ctx, a: MonthArg) -> dict:
    r = await _cp(ctx).get_payslip(month=a.month)
    if "error" in r:
        return r
    r["source"] = src(f"Payslip {r.get('month')}", r.get("link") or "/my-pay", "payroll-service", [r.get("month")])
    return r


@register("explain_payslip_line", "Explain one line of the caller's pay stub (CPP / CPP2 or QPP, EI, QPIP, income tax, vacation pay, regular / overtime pay …) from the stored components and the rule behind it.",
          scope="read", args=LineArg)
async def explain_payslip_line(ctx: Ctx, a: LineArg) -> dict:
    r = await _cp(ctx).explain_payslip_line(line=a.line, month=a.month)
    if "error" in r:
        return r
    # ── HR Desk (stream D, 2026-10-25): the deterministic change of THIS line vs the previous payslip, with the rule behind it ──
    try:
        from desk import engine as _DE
        from desk import payslip_diff as _PD
        structure, _err = await _DE.pay_change_structure(ctx, a.month or None)
        if structure:
            r["change_vs_previous"] = _PD.line_for(structure, a.line) or {"note": f"{a.line} did not change vs the previous payslip ({structure.get('prev_period') or 'none'})."}
            r["engine_note"] = "Quote only figures present in values / change_vs_previous; rule_text is the reason. Never derive numbers."
    except Exception:                                        # noqa: BLE001 — the plain explanation still goes out
        pass
    r["source"] = src(f"Payslip {r.get('month')} — {a.line}", r.get("link") or "/my-pay", "payroll-service", [r.get("month")])
    return r


@register("download_payslip", "A download link for the caller's payslip PDF (YYYY-MM).", scope="read", args=MonthArg)
async def download_payslip(ctx: Ctx, a: MonthArg) -> dict:
    d, err = await _cp(ctx)._stub_for(a.month or None)
    if err:
        return {"error": err}
    stub = d["_summary"].get("stub_id")
    return {"month": (d["_summary"].get("pay_date") or "")[:7], "pdf_link": f"/api/payroll/payslips/{stub}/pdf", "open_link": f"/my-pay?stub={stub}",
            "source": src("Payslip PDF", f"/my-pay?stub={stub}", "payroll-service", [stub])}


@register("my_pending_approvals", "Approvals waiting for the caller to decide, and the caller's own requests still pending (with who they wait on).",
          scope="read", args=NoArgs)
async def my_pending_approvals(ctx: Ctx, a: NoArgs) -> dict:
    r = await _cp(ctx).my_pending_approvals()
    r["source"] = src("Approvals inbox", "/approvals", "approvals-service")
    return r


@register("policy_search", "Search the company's policy documents, company settings (working week, hours, approvals), leave types, privacy notice and the payroll engine's statutory constants. Returns the best matching passages with their source.",
          scope="read", args=QuestionArg)
async def policy_search(ctx: Ctx, a: QuestionArg) -> dict:
    import retrieval
    # ── HR Desk (phase 2, P2-C): APPROVED, in-force, ACL-admitted policy clauses first (desk.policy_store) — cited by policy / clause / version ──
    try:
        from desk import policy_store as _PS
        from desk import settings as _DSET
        from shared.utils.org_time import org_today as _org_today
        _who = {"role": ctx.role, "employee_id": ctx.employee_id}
        _dcfg = await _DSET.load(ctx.org_id)
        _pc = await _PS.answer_context_db(ctx.org_id, _who, a.question, await _org_today(ctx.org_id), threshold=float(_dcfg.get("abstain_threshold") or 0.35), k=6)
        if not _pc["abstain"] and _pc["clauses"]:
            _cl = [{"id": c["id"], "title": f"{c['policy']} — {c['clause_id']} {c['title']}".strip(), "text": c["text"], "link": c["deep_link"], "kind": "policy_clause",
                    "policy": c["policy"], "clause_id": c["clause_id"], "version": c["version"], "effective_from": c["effective_from"], "score": c.get("relevance")}
                   for c in _pc["clauses"]]
            return {"question": a.question, "passages": _cl, "note": "Approved policy clauses (versioned, effective-dated). Cite the policy, clause and version; nothing outside these passages.",
                    "source": src("Approved policy clauses", _cl[0]["link"], "hr-desk policy store", [c["id"] for c in _cl])}
    except Exception:                                        # noqa: BLE001 — fall back to the settings / register corpus
        pass
    hits = await retrieval.search(ctx, a.question, k=6)
    return {"question": a.question, "passages": hits,
            "note": "Policy documents are stored by title, version and effective date; their full text is not in this system — answer from the passages only.",
            "source": src("Policies and settings", "/ops/policies", "ops-service + settings + statutory register", [h.get("id") for h in hits])}


@register("statutory_rule", "Look up the Canadian statutory figures the payroll engine applies (CPP / CPP2 rates and maximums, YMPE / YAMPE, EI rate and maximum insurable earnings, federal and provincial tax brackets and basic personal amounts, Québec QPP / QPIP) with the source and whether it is verified against CRA PDOC / WebRAS.",
          scope="read", args=TopicArg)
async def statutory_rule(ctx: Ctx, a: TopicArg) -> dict:
    import retrieval
    rows, err = await retrieval.statutory_rows(ctx.headers)     # register for the pay office, statutory-rates for everyone else
    if err:
        return {"error": err}
    words = [w for w in re.findall(r"[a-z0-9]{2,}", a.topic.lower())]
    scored = []
    for r in rows or []:
        hay = " ".join(str(r.get(k) or "") for k in ("key", "area", "description", "note", "applies_to", "code_does")).lower()
        score = sum(hay.count(w) for w in words)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    out = [{"id": r.get("id") or r.get("key"), "area": r.get("area"), "rule": r.get("description"), "value": r.get("value"),
            "effective_from": r.get("effective_from"), "status": r.get("status"), "applies_to": r.get("applies_to"),
            "source_title": r.get("source_title"), "source_url": r.get("source_url"), "org_override": r.get("org_override"), "note": r.get("note")}
           for _, r in scored[:6]]
    return {"rules": out, "note": "status 'needs_confirmation' means the figure is not yet verified against CRA PDOC / Revenu Québec WebRAS — say so.",
            "source": src("Payroll Verification Center", "/payroll/compliance", "payroll-service", [o["id"] for o in out])}


CHAIN_DEFAULTS = {
    "leave": "Your manager decides (HR / org admin can also act). Approval chains per request type come from the approval-chain builder when one is published.",
    "attendance_fix": "Your manager decides (HR / org admin can also act).",
    "timesheet": "Your manager decides (HR / org admin can also act).",
    "requisition": "Set by the hiring setting requisition_approval (default manager, then HR).",
    "salary_revision": "Org admin decides.",
    "exit": "Manager accepts the resignation, then HR runs clearance and settlement.",
    "expense": "Manager, then finance / payroll.",
    "letter": "HR issues letters; an employee asks through a helpdesk ticket (category: documents).",
}


@register("who_approves", "Who approves a given request type for the caller (leave, attendance_fix, timesheet, expense, salary_revision, exit, requisition, letter): the published approval chain when one exists, else the default rule, plus the caller's own manager chain.",
          scope="read", args=RequestTypeArg)
async def who_approves(ctx: Ctx, a: RequestTypeArg) -> dict:
    kind = (a.request_type or "leave").strip().lower()
    chain, chain_src = None, None
    for path in (f"/approvals/chains/resolve?request_type={kind}", f"/approvals/chains?request_type={kind}", f"/approvals/chains/{kind}"):
        data, err = await CP._get(f"{settings.APPROVALS_URL}{path}", ctx.headers)
        if data and not err:
            chain, chain_src = data, path
            break
    managers = []
    if ctx.employee_id:
        prof, _ = await CP._get(f"{settings.EMPLOYEE_URL}/employees/{ctx.employee_id}/profile", ctx.headers)
        managers = [m.get("name") or f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() for m in ((prof or {}).get("manager_chain") or [])][:3]
    req_setting = None
    if kind == "requisition":
        hs, _ = await CP._get(f"{settings.EMPLOYEE_URL}/hiring/settings", ctx.headers)
        req_setting = (hs or {}).get("requisition_approval")
    return {"request_type": kind, "published_chain": chain, "default_rule": CHAIN_DEFAULTS.get(kind, "No default rule recorded for this request type."),
            "requisition_setting": req_setting, "my_manager_chain": managers,
            "source": src(f"Approval chain — {kind}", "/approvals", "approvals-service" + (f" ({chain_src})" if chain_src else " (default rules)"))}


@register("employee_directory", "People directory (allow-listed fields only: name, title, department, work e-mail, location). Search by name / department.",
          scope="read", args=DirectoryArg)
async def employee_directory(ctx: Ctx, a: DirectoryArg) -> dict:
    params = {k: v for k, v in (("q", a.q), ("department", a.department)) if v}
    data, err = await CP._get(f"{settings.EMPLOYEE_URL}/employees/directory", ctx.headers, params or None)
    if err:
        return {"error": err}
    rows = data.get("items") if isinstance(data, dict) else data
    out = [{"name": r.get("name") or f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(), "job_title": r.get("job_title"),
            "department": r.get("department"), "work_email": r.get("work_email") or r.get("email"), "location": r.get("location")} for r in (rows or [])][:30]
    return {"people": out, "count": len(rows or []), "source": src("People directory", "/people", "employee-service")}


@register("org_calendar", "Company holidays for a year (the org's statutory-holiday master, by province).", scope="read", args=CalendarArg)
async def org_calendar(ctx: Ctx, a: CalendarArg) -> dict:
    year = a.year or _today(ctx).year
    master, e2 = await CP._get(f"{settings.EMPLOYEE_URL}/masters/stat-holidays", ctx.headers)
    if e2:
        return {"error": e2}
    rows_m = master.get("items") if isinstance(master, dict) else master
    prov = (a.state or "").strip().upper()
    hol = [{"date": h.get("date") or h.get("holiday_date"), "name": h.get("name"), "province": h.get("province"), "type": "statutory"}
           for h in (rows_m or []) if str(h.get("date") or h.get("holiday_date") or "").startswith(str(year))
           and (not prov or (h.get("province") or "").upper() in (prov, ""))]
    hol = sorted({(h["date"], h["name"], h.get("province")): h for h in hol if h.get("date")}.values(), key=lambda h: h["date"])
    return {"year": year, "holidays": hol[:80], "source": src(f"Statutory holidays {year}", "/masters", "employee-service masters")}


@register("open_requisitions", "Open hiring requisitions the caller may see (title, department, openings, hiring manager, status).", scope="read", args=NoArgs)
async def open_requisitions(ctx: Ctx, a: NoArgs) -> dict:
    data, err = await CP._get(f"{settings.EMPLOYEE_URL}/hiring/requisitions", ctx.headers, {"status": "open"})
    if err:
        return {"error": err}
    rows = data.get("items") if isinstance(data, dict) else data
    out = [{"id": r.get("id"), "number": r.get("number") or r.get("requisition_number"), "title": r.get("title") or r.get("job_title"),
            "department": r.get("department"), "openings": r.get("openings"), "hiring_manager": r.get("hiring_manager_name"), "status": r.get("status")}
           for r in (rows or [])][:30]
    return {"requisitions": out, "source": src("Open requisitions", "/hiring/requisitions", "employee-service (hiring)", [r["id"] for r in out])}


@register("my_letters", "Letters issued to the caller (type, date, link) and the letter types HR can issue.", scope="read", args=NoArgs)
async def my_letters(ctx: Ctx, a: NoArgs) -> dict:
    data, err = await CP._get(f"{settings.EMPLOYEE_URL}/letters/mine", ctx.headers)
    rows = (data.get("items") if isinstance(data, dict) else data) or []
    out = [{"id": r.get("id"), "type": r.get("template_key") or r.get("title"), "issued_on": r.get("issued_at") or r.get("created_at"),
            "link": f"/api/letters/{r.get('id')}/pdf"} for r in rows][:30]
    return {"letters": out, "error": err, "letter_types": ["appointment_letter", "confirmation", "salary_revision", "promotion", "transfer", "experience_certificate",
                                                           "employment_verification", "relieving", "ctc_annexure"],
            "source": src("My letters", "/my-letters", "employee-service (letters)", [r["id"] for r in out])}


# ══════════════════════════ admin tools (MCP admin scope) ═══════════════════════

class SinceArg(BaseModel):
    days: int = Field(30, ge=1, le=366)


@register("org_ai_usage", "AI usage of the whole company for the last N days: calls, tokens, estimated cost by user / feature / model.",
          scope="admin", roles=ADMIN_ROLES + ("hr",), args=SinceArg)
async def org_ai_usage(ctx: Ctx, a: SinceArg) -> dict:
    import metering
    from datetime import datetime, timezone
    s = await metering.summary(ctx.org_id, since=datetime.now(timezone.utc) - timedelta(days=a.days))
    s.pop("recent", None)
    s["source"] = src("AI usage", "/ai/usage", "ai-platform")
    return s


# action tools register themselves on import (they live in actions.py so the executor and
# the proposal share one definition each)
import actions  # noqa: E402,F401

# ── HR Desk tools (stream D, 2026-10-25) — append-only block: explain_my_pay_change, my_data_summary, raise_privacy_grievance ──
import desk.tools  # noqa: E402,F401
