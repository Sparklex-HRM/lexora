"""Approval-gated ACTIONS: the model proposes, the person confirms, the service executes.

Flow (chat and MCP alike)
  1. an ``act`` tool runs → it only builds a CARD {what, for_whom, effect, risk,
     requires_confirmations} and appends it to ``ctx.proposals`` — nothing is written;
  2. the router / MCP layer persists the card as an ``ai_actions`` row (status pending,
     30-minute expiry) and shows it to the person;
  3. ``confirm()`` counts explicit confirmations: 1 for a normal action, 2 for a
     destructive / finance one (cancel leave, approve/reject someone's leave, e-mail a
     payslip, start an exit). Only the person who asked may confirm, only through the
     same org, only before expiry;
  4. the executor calls the ordinary service API with the person's own identity headers
     (RBAC holds) and writes ONE tamper-evident audit row ``ai.action.<name>`` with
     ``meta.via = "ai"`` (shared.utils.audit — hash-chained), plus the masked before / after diff
     for a contact change (shared.audit_trail).
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import date, timedelta
from email.message import EmailMessage

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select

sys.path.append("/app")
from shared.audit_trail import audit_change  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402

import copilot as CP  # noqa: E402
from ai_models import AiAction  # noqa: E402
from shield import Shield  # noqa: E402
from tools_registry import (APPROVERS, EMPLOYEE_EDIT_ROLES, Ctx, allowed_for_role, register, src)  # noqa: E402

EXPIRY_MINUTES = 30
CONFIRMATIONS = {"apply_leave": 1, "cancel_leave": 2, "decide_leave": 2, "raise_helpdesk_ticket": 1, "request_regularisation": 1,
                 "send_payslip": 2, "start_exit_request": 2, "request_letter": 1, "update_my_contact": 1}
HIGH_RISK = {k for k, v in CONFIRMATIONS.items() if v > 1}

# injectable for tests (no Postgres / SMTP there)
_audit = record_audit
_audit_change = audit_change


class _Req:
    """Enough of a Request for record_audit / audit_change: ``.headers`` with the identity."""

    def __init__(self, headers: dict):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


def _card(action: str, what: str, for_whom: str, effect: str, payload: dict, link: str = "") -> dict:
    return {"action": action, "what": what, "for_whom": for_whom, "effect": effect, "risk": "high" if action in HIGH_RISK else "normal",
            "requires_confirmations": CONFIRMATIONS[action], "payload": payload, "link": link}


def _propose(ctx: Ctx, card: dict) -> dict:
    ctx.proposals.append(card)
    return {"proposed": True, "card": {k: v for k, v in card.items() if k != "payload"},
            "note": ("NOT done. The person must confirm on screen" + (" TWICE (high-risk action)" if card["risk"] == "high" else "") +
                     " before anything is submitted. Tell them exactly what the card says and that they must confirm.")}


def _me(ctx: Ctx) -> str:
    return ctx.user_email or "you"


# ══════════════════════════ action tools (propose only) ═════════════════════════

class ApplyLeaveArgs(BaseModel):
    leave_type: str = Field(..., min_length=1, description="leave type code or name, e.g. CL or Casual")
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field("", description="YYYY-MM-DD; empty = same as start_date")
    half_day: bool = False
    reason: str = Field("", max_length=300)


@register("apply_leave", "Prepare a leave request for the caller. Proposes only; the person confirms on screen.", scope="act", args=ApplyLeaveArgs)
async def apply_leave(ctx: Ctx, a: ApplyLeaveArgs) -> dict:
    r = await CP.Tools(ctx.headers, []).apply_leave(leave_type=a.leave_type, start_date=a.start_date, end_date=a.end_date or a.start_date,
                                                   half_day=a.half_day, reason=a.reason)
    if "error" in r:
        return r                                             # copilot validated dates + leave type; rebuild the payload below
    sd, ed = a.start_date, a.end_date or a.start_date
    bal, _ = await CP._get(f"{settings.LEAVE_URL}/leave/balances", ctx.headers)
    types = {(b.get("leave_type_code") or ""): b.get("leave_type_name") for b in (bal or {}).get("balances") or []}
    want = a.leave_type.strip().lower()
    code = next((c for c, n in types.items() if c and (c.lower() == want or (n or "").lower() == want)), None) \
        or next((c for c, n in types.items() if c and (want in (n or "").lower() or want in c.lower())), None)
    payload = {"leave_type_code": code, "start_date": sd, "end_date": ed, "half_day": bool(a.half_day), "reason": a.reason or ""}
    days = (date.fromisoformat(ed) - date.fromisoformat(sd)).days + 1
    return _propose(ctx, _card("apply_leave", f"Apply {types.get(code) or code} from {sd} to {ed}" + (" (half day)" if a.half_day else ""),
                               _me(ctx), f"A leave request for {0.5 if a.half_day else days} day(s) goes to your manager for approval; the balance shows it as pending.",
                               payload, "/leave"))


class LeaveIdArgs(BaseModel):
    request_id: str = Field(..., min_length=8, description="the leave request id (from my_leave_requests / team_leave_requests)")


@register("cancel_leave", "Cancel one of the caller's own leave requests (pending or approved). High-risk: two confirmations.", scope="act",
          args=LeaveIdArgs, risk="high")
async def cancel_leave(ctx: Ctx, a: LeaveIdArgs) -> dict:
    data, err = await CP._get(f"{settings.LEAVE_URL}/leave/requests", ctx.headers, {"scope": "mine"})
    rows = (data.get("items") if isinstance(data, dict) else data) or []
    row = next((r for r in rows if str(r.get("id")) == a.request_id), None)
    if err or not row:
        return {"error": err or "That leave request is not one of yours (or does not exist)."}
    if row.get("status") not in ("pending", "approved"):
        return {"error": f"That request is {row.get('status')} — only pending or approved leave can be cancelled."}
    what = f"Cancel {row.get('leave_type_name') or row.get('leave_type_code')} leave {row.get('start_date')} to {row.get('end_date')} (currently {row.get('status')})"
    return _propose(ctx, _card("cancel_leave", what, _me(ctx), "The request is cancelled and the days return to your balance. This cannot be undone — re-apply if needed.",
                               {"request_id": a.request_id}, "/leave"))


class DecideLeaveArgs(BaseModel):
    request_id: str = Field(..., min_length=8)
    decision: str = Field(..., pattern=r"^(approve|reject)$")
    note: str = Field("", max_length=300, description="required for reject")


@register("decide_leave", "Approve or reject a team member's leave request (manager / HR). High-risk: two confirmations.", scope="act", roles=APPROVERS,
          args=DecideLeaveArgs, risk="high")
async def decide_leave(ctx: Ctx, a: DecideLeaveArgs) -> dict:
    if a.decision == "reject" and not a.note.strip():
        return {"error": "A rejection needs a note for the employee."}
    data, err = await CP._get(f"{settings.LEAVE_URL}/leave/requests", ctx.headers, {"scope": "team", "status": "pending"})
    rows = (data.get("items") if isinstance(data, dict) else data) or []
    row = next((r for r in rows if str(r.get("id")) == a.request_id), None)
    if err or not row:
        return {"error": err or "No pending leave request with that id in your team."}
    what = f"{a.decision.title()} {row.get('leave_type_name') or row.get('leave_type_code')} leave {row.get('start_date')} to {row.get('end_date')}"
    return _propose(ctx, _card("decide_leave", what, row.get("employee_name") or "the employee",
                               ("The employee is told the leave is approved and the days are deducted." if a.decision == "approve"
                                else "The employee is told the leave is rejected, with your note."),
                               {"request_id": a.request_id, "decision": a.decision, "note": a.note}, "/leave/approvals"))


class TicketArgs(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    description: str = Field("", max_length=2000)
    category: str = Field("other", description="payroll | leave | documents | benefits | it_access | policy | grievance | other")
    priority: str = Field("normal", pattern=r"^(low|normal|high|urgent)$")


@register("raise_helpdesk_ticket", "Raise an HR helpdesk ticket for the caller.", scope="act", args=TicketArgs)
async def raise_helpdesk_ticket(ctx: Ctx, a: TicketArgs) -> dict:
    cat = re.sub(r"[^a-z_]", "", a.category.lower()) or "other"
    payload = {"category": cat, "subject": a.subject.strip(), "body": a.description.strip(), "priority": a.priority, "source": "web"}
    return _propose(ctx, _card("raise_helpdesk_ticket", f"Raise a {cat} ticket: “{a.subject.strip()}” ({a.priority})", _me(ctx),
                               "HR gets the ticket in the helpdesk queue" + (" (grievance tickets are confidential)" if cat == "grievance" else "") + ".",
                               payload, "/engage/helpdesk"))


class RegularisationArgs(BaseModel):
    work_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: str = Field(..., min_length=3, max_length=300)
    requested_in: str = Field("", description="HH:MM")
    requested_out: str = Field("", description="HH:MM")


@register("request_regularisation", "Ask to regularise the caller's attendance for one day (missed / wrong punch).", scope="act", args=RegularisationArgs)
async def request_regularisation(ctx: Ctx, a: RegularisationArgs) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    for t in (a.requested_in, a.requested_out):
        if t and not re.fullmatch(r"\d{2}:\d{2}", t):
            return {"error": "Times must look like 09:30."}
    payload = {"employee_id": ctx.employee_id, "work_date": a.work_date, "reason": a.reason.strip(), "requested_in": a.requested_in or None,
               "requested_out": a.requested_out or None}
    times = " · ".join(x for x in (f"in {a.requested_in}" if a.requested_in else "", f"out {a.requested_out}" if a.requested_out else "") if x)
    return _propose(ctx, _card("request_regularisation", f"Regularise attendance on {a.work_date}" + (f" ({times})" if times else ""), _me(ctx),
                               "Your manager gets an attendance-fix approval; the day is corrected once approved.", payload, "/attendance"))


class SendPayslipArgs(BaseModel):
    month: str = Field("", description="YYYY-MM; empty = latest")


@register("send_payslip", "E-mail the caller's payslip PDF to the caller's own registered e-mail address. High-risk (pay data leaves the system): two confirmations.",
          scope="act", args=SendPayslipArgs, risk="high")
async def send_payslip(ctx: Ctx, a: SendPayslipArgs) -> dict:
    d, err = await CP.Tools(ctx.headers, [])._stub_for(a.month or None)
    if err:
        return {"error": err}
    if not ctx.user_email:
        return {"error": "Your login has no e-mail address."}
    stub, month = d["_summary"].get("stub_id"), (d["_summary"].get("pay_date") or "")[:7]
    return _propose(ctx, _card("send_payslip", f"E-mail payslip {month} (PDF) to {ctx.user_email}", _me(ctx),
                               "The PDF leaves Lexora HRM by e-mail to your registered address only (never another address).",
                               {"stub_id": stub, "month": month, "to": ctx.user_email}, f"/my-pay?stub={stub}"))


class ExitArgs(BaseModel):
    requested_last_working_day: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: str = Field(..., min_length=3, max_length=500)
    reason_category: str = Field("", max_length=60, description="e.g. better_opportunity, relocation, personal, higher_studies")


@register("start_exit_request", "Start the caller's own resignation (exit case). High-risk: two confirmations.", scope="act", args=ExitArgs, risk="high")
async def start_exit_request(ctx: Ctx, a: ExitArgs) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    payload = {"exit_type": "resignation", "reason": a.reason.strip(), "reason_category": a.reason_category or None,
               "requested_last_working_day": a.requested_last_working_day}
    return _propose(ctx, _card("start_exit_request", f"Submit resignation with requested last working day {a.requested_last_working_day}", _me(ctx),
                               "An exit case opens: your manager is asked to accept, then HR runs clearance, notice period and final settlement. "
                               "Withdrawing later needs HR.", payload, "/exit/my-exit"))


class LetterArgs(BaseModel):
    letter_type: str = Field(..., min_length=3, max_length=60, description="appointment_letter | confirmation | salary_revision | experience_certificate | employment_verification | relieving | ctc_annexure | other")
    purpose: str = Field("", max_length=300, description="why you need it (bank, visa, …)")


@register("request_letter", "Ask HR for a letter (employment verification, experience certificate, salary certificate …). Raised as a helpdesk ticket in the documents category — HR issues the letter from the template engine.",
          scope="act", args=LetterArgs)
async def request_letter(ctx: Ctx, a: LetterArgs) -> dict:
    lt = re.sub(r"[^a-z_]", "", a.letter_type.lower().replace(" ", "_")) or "letter"
    payload = {"category": "documents", "subject": f"Letter request: {lt.replace('_', ' ')}", "body": (f"Purpose: {a.purpose.strip()}\n" if a.purpose else "") +
               f"Requested through the AI assistant. Template: {lt}.", "priority": "normal", "source": "web"}
    return _propose(ctx, _card("request_letter", f"Request a {lt.replace('_', ' ')} from HR", _me(ctx),
                               "HR receives a documents ticket and issues the letter; it appears under My letters.", payload, "/engage/helpdesk"))


class ContactArgs(BaseModel):
    phone: str = Field("", max_length=20)
    address_line1: str = Field("", max_length=200)
    address_line2: str = Field("", max_length=200)
    city: str = Field("", max_length=80)
    state: str = Field("", max_length=80)
    postal_code: str = Field("", max_length=12)


@register("update_my_contact", "Change the caller's own phone / address. HR roles update the record directly; everyone else raises a privacy correction request (PIPEDA) that HR applies. Audited either way.",
          scope="act", args=ContactArgs)
async def update_my_contact(ctx: Ctx, a: ContactArgs) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    fields = {k: v.strip() for k, v in a.model_dump().items() if v and v.strip()}
    if not fields:
        return {"error": "Give at least one of phone, address_line1, address_line2, city, province, postal_code."}
    if "phone" in fields and not re.fullmatch(r"\+?[\d \-]{8,16}", fields["phone"]):
        return {"error": "That phone number does not look valid."}
    direct = allowed_for_role(ctx.role, EMPLOYEE_EDIT_ROLES)
    mode = "direct" if direct else "privacy_request"
    return _propose(ctx, _card("update_my_contact", "Update " + ", ".join(fields), _me(ctx),
                               ("Your employee record is changed now; the change is written to the audit trail (values masked)." if direct
                                else "A 'correct my information' request is raised in the privacy centre for HR to apply (PIPEDA, 30 days); it is audited."),
                               {"employee_id": ctx.employee_id, "fields": fields, "mode": mode}, "/my-profile"))


# ══════════════════════════ executors (run AFTER confirmation) ═══════════════════

async def _x_apply_leave(h: dict, p: dict) -> tuple[dict | None, str | None]:
    return await CP._post(f"{settings.LEAVE_URL}/leave/requests", h, {k: p[k] for k in ("leave_type_code", "start_date", "end_date", "half_day", "reason")})


async def _x_cancel_leave(h, p):
    return await CP._post(f"{settings.LEAVE_URL}/leave/requests/{p['request_id']}/cancel", h, {})


async def _x_decide_leave(h, p):
    return await CP._post(f"{settings.LEAVE_URL}/leave/requests/{p['request_id']}/{p['decision']}", h, {"note": p.get("note") or ""})


async def _x_ticket(h, p):
    return await CP._post(f"{settings.OPS_URL}/engage/helpdesk/tickets", h, {k: p.get(k) for k in ("category", "subject", "body", "priority", "source")})


async def _x_regularisation(h, p):
    return await CP._post(f"{settings.TIME_URL}/time/regularizations", h, p)


async def _x_exit(h, p):
    return await CP._post(f"{settings.EMPLOYEE_URL}/exit/cases", h, p)


async def _x_contact(h, p):
    emp, fields = p["employee_id"], p["fields"]
    if p.get("mode") == "direct":
        before, err = await CP._get(f"{settings.EMPLOYEE_URL}/employees/{emp}", h)
        if err:
            return None, err
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.patch(f"{settings.EMPLOYEE_URL}/employees/{emp}", headers=CP.fwd(h), json=fields)
        except (httpx.ConnectError, httpx.TimeoutException):
            return None, "The employee service did not answer."
        if r.status_code not in (200, 201):
            try:
                return None, r.json().get("detail") or f"HTTP {r.status_code}"
            except Exception:                                # noqa: BLE001
                return None, f"HTTP {r.status_code}"
        after = {**(before or {}), **fields}
        await _audit_change(_Req(h), "employee.self_update", entity_type="employee", entity_id=emp,
                            before={k: (before or {}).get(k) for k in fields}, after={k: after.get(k) for k in fields},
                            summary="Contact details updated via AI assistant", extra={"via": "ai", "privacy": True})
        return {"id": emp, "status": "updated", "fields": sorted(fields)}, None
    details = "Please correct my contact details: " + ", ".join(f"{k} → {v}" for k, v in fields.items())
    return await CP._post(f"{settings.COMPLIANCE_URL}/compliance/privacy/requests", h,
                          {"request_type": "correction", "details": details, "payload": {"fields": fields, "via": "ai"}})


def _smtp_send_with_pdf(to: str, subject: str, body: str, pdf: bytes, filename: str) -> None:
    import smtplib
    import ssl
    from shared.utils.email import _cfg
    c = _cfg()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = c["sender"], to, subject
    msg.set_content(body)
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    ctx = ssl.create_default_context()
    if c["port"] == 465:
        with smtplib.SMTP_SSL(c["host"], 465, context=ctx, timeout=20) as s:
            s.login(c["user"], c["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
            if c["tls"]:
                s.starttls(context=ctx)
            s.login(c["user"], c["password"])
            s.send_message(msg)


async def _x_send_payslip(h, p):
    from shared.utils.email import smtp_configured
    if not smtp_configured():
        return None, "E-mail is not configured on this installation (SMTP). Download the payslip from My pay instead."
    to = (h.get("x-user-email") or "").strip()
    if not to or to.lower() != str(p.get("to") or "").lower():
        return None, "The payslip can only go to your own registered e-mail address."
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{settings.PAYROLL_URL}/payroll/payslips/{p['stub_id']}/pdf", headers=CP.fwd(h))
    except (httpx.ConnectError, httpx.TimeoutException):
        return None, "The payroll service did not answer."
    if r.status_code != 200:
        return None, f"Could not fetch the payslip PDF (HTTP {r.status_code})."
    try:
        await asyncio.to_thread(_smtp_send_with_pdf, to, f"Your payslip {p.get('month')}", "Your payslip is attached. Sent on your request through the AI assistant.",
                                r.content, f"payslip-{p.get('month')}.pdf")
    except Exception as e:                                   # noqa: BLE001
        return None, f"E-mail failed: {type(e).__name__}"
    return {"sent_to": to, "month": p.get("month")}, None


EXECUTORS = {"apply_leave": _x_apply_leave, "cancel_leave": _x_cancel_leave, "decide_leave": _x_decide_leave, "raise_helpdesk_ticket": _x_ticket,
             "request_regularisation": _x_regularisation, "send_payslip": _x_send_payslip, "start_exit_request": _x_exit, "request_letter": _x_ticket,
             "update_my_contact": _x_contact}

ENTITY = {"apply_leave": "leave_request", "cancel_leave": "leave_request", "decide_leave": "leave_request", "raise_helpdesk_ticket": "helpdesk_ticket",
          "request_regularisation": "attendance_regularisation", "send_payslip": "payslip", "start_exit_request": "exit_case", "request_letter": "helpdesk_ticket",
          "update_my_contact": "employee"}


# ══════════════════════════ persistence: propose / confirm / cancel ═════════════

def _out(row: AiAction) -> dict:
    return {"id": row.id, "action": row.action, "status": row.status, "card": {k: v for k, v in (row.card or {}).items() if k != "payload"},
            "confirmations": row.confirmations, "requires_confirmations": int((row.card or {}).get("requires_confirmations") or 1),
            "result": row.result, "channel": row.channel, "created_at": pm.iso(row.created_at), "expires_at": pm.iso(row.expires_at),
            "decided_at": pm.iso(row.decided_at)}


async def persist(ctx: Ctx, cards: list[dict]) -> list[dict]:
    out = []
    if not cards or not ctx.org_id:
        return out
    async with pm.session() as s:
        for card in cards:
            row = AiAction(org_id=ctx.org_id, user_id=ctx.user_id, user_email=ctx.user_email or None, role=ctx.role or None, channel=ctx.channel,
                           token_id=ctx.token_id, action=card["action"], payload=card.get("payload") or {}, card=card,
                           expires_at=pm.now_utc() + timedelta(minutes=EXPIRY_MINUTES))
            s.add(row)
            await s.flush()
            out.append(_out(row))
        await s.commit()
    return out


class ActionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def _load(s, org: str, user_id: str, action_id: str) -> AiAction:
    row = (await s.execute(select(AiAction).where(AiAction.id == action_id, AiAction.org_id == org))).scalar_one_or_none()
    if not row or row.user_id != user_id:
        raise ActionError(404, "That proposed action was not found (only the person who asked can act on it).")
    if row.status != "pending":
        raise ActionError(409, f"That action is already {row.status}.")
    if row.expires_at and pm.aware(row.expires_at) < pm.now_utc():
        row.status, row.decided_at = "expired", pm.now_utc()
        await s.commit()
        raise ActionError(409, "That proposal expired — ask again.")
    return row


async def confirm(ctx: Ctx, action_id: str) -> dict:
    """One explicit confirmation. Executes only when the required count is reached."""
    async with pm.session() as s:
        row = await _load(s, ctx.org_id, ctx.user_id, action_id)
        need = int((row.card or {}).get("requires_confirmations") or CONFIRMATIONS.get(row.action, 1))
        row.confirmations = int(row.confirmations or 0) + 1
        if row.confirmations < need:
            await s.commit()
            return {**_out(row), "executed": False,
                    "message": f"First confirmation recorded. This is a high-risk action — confirm once more to {row.card.get('what', row.action)}."}
        fn = EXECUTORS.get(row.action)
        if not fn:
            row.status, row.result, row.decided_at = "failed", {"error": "Unknown action"}, pm.now_utc()
            await s.commit()
            raise ActionError(400, "Unknown action")
        data, err = await fn(ctx.headers, dict(row.payload or {}))
        row.decided_at = pm.now_utc()
        row.status = "failed" if err else "done"
        row.result = {"error": err} if err else {"id": (data or {}).get("id"), "status": (data or {}).get("status"), "detail": _small(data)}
        await s.commit()
        snapshot = _out(row)
    await _audit(_Req(ctx.headers), f"ai.action.{row.action}", entity_type=ENTITY.get(row.action), entity_id=(data or {}).get("id") or row.id,
                 summary=("Done" if not err else "Failed") + f" via AI assistant: {row.card.get('what', row.action)}",
                 meta={"via": "ai", "channel": row.channel, "action_id": row.id, "confirmations": row.confirmations, "ok": not err,
                       "error": err, "payload": Shield().protect_value(dict(row.payload or {})), "token_id": row.token_id})
    if err:
        return {**snapshot, "executed": False, "message": f"Not done: {err}"}
    return {**snapshot, "executed": True, "message": f"Done: {row.card.get('what', row.action)}.", "link": row.card.get("link")}


def _small(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) and k in ("id", "status", "ref", "number", "case_number", "sent_to", "month")}


async def cancel(ctx: Ctx, action_id: str) -> dict:
    async with pm.session() as s:
        row = await _load(s, ctx.org_id, ctx.user_id, action_id)
        row.status, row.decided_at = "cancelled", pm.now_utc()
        await s.commit()
        return {**_out(row), "executed": False, "message": "Cancelled — nothing was submitted."}


async def mine(ctx: Ctx, limit: int = 50) -> list[dict]:
    async with pm.session() as s:
        rows = (await s.execute(select(AiAction).where(AiAction.org_id == ctx.org_id, AiAction.user_id == ctx.user_id)
                                .order_by(AiAction.created_at.desc()).limit(limit))).scalars().all()
    return [_out(r) for r in rows]


# ── HR Desk (stream D, 2026-10-25) — append-only block: privacy complaint (desk/tools.py proposes, this executes) ──
async def _x_privacy_grievance(h, p):
    from desk.tools import execute_privacy_grievance
    return await execute_privacy_grievance(h, p)


CONFIRMATIONS["raise_privacy_grievance"] = 1
EXECUTORS["raise_privacy_grievance"] = _x_privacy_grievance
ENTITY["raise_privacy_grievance"] = "privacy_grievance"
