"""WhatsApp consent (opt-in) + event templates. Sending is the G3 connector (Meta Cloud API / Twilio).

    GET  /integrations/whatsapp/events                    the 4 business events + which template each uses
    GET  /integrations/whatsapp/optins                    HR: every opt-in / opt-out on record
    GET  /integrations/whatsapp/optin/me                  the signed-in employee's own consent
    POST /integrations/whatsapp/optin {employee_id?, events?, consent_text?}
                                                          self (no employee_id) or HR recording a consent given on paper
    POST /integrations/whatsapp/optout {employee_id?}     self or HR
    POST /integrations/whatsapp/send {employee_id, event} admin: send one event template now (opted-in people only)

Rule (Meta WhatsApp Business policy: business-initiated messages need the person's opt-in):
the notification mirror sends ONLY to employees with an active opt-in for the SAME phone number;
a changed number needs a fresh opt-in. Consent text, who recorded it and when are kept.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from shared.utils import platform_models as pm
from shared.utils.audit import record_audit
from shared.utils.providers import mirror, store, whatsapp as WA
from shared.utils.providers.models_s5 import WhatsappOptin
from shared.utils.roles import has_role, role_of

import people
from common import HR_WRITE, actor, app_base, org_of, require_roles

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp-optin"])
DEFAULT_CONSENT = ("I agree to receive work messages from my employer on WhatsApp at this number: payslip ready, "
                   "leave decisions, missing attendance and joining invitations. I can stop this at any time in Lexora HRM.")
# The employee's own opt-in for reminders from Lexora HRM's agents (Notifications → My preferences).
AGENT_CHASE_CONSENT = ("I agree to receive reminders from Lexora HRM's agents on WhatsApp at this number when a task is waiting on me "
                       "(for example: upload a document, fix a missing punch, sign a clearance). The message names the task "
                       "and links into Lexora HRM — it never carries amounts, ID numbers or anyone else's data. In-app and e-mail "
                       "reminders come first. I can stop this at any time in Lexora HRM.")


def _out(o: WhatsappOptin) -> dict:
    return {"id": o.id, "employee_id": o.employee_id, "phone": o.phone_masked, "status": o.status, "source": o.source,
            "events": o.events or [], "consent_text": o.consent_text, "recorded_by": o.recorded_by,
            "at": pm.iso(o.at), "revoked_at": pm.iso(o.revoked_at)}


def _target(request: Request, employee_id: str | None) -> tuple[str, bool]:
    """(employee id, recorded_by_hr)."""
    me = request.headers.get("x-employee-id") or ""
    if not employee_id or employee_id == me:
        if not me:
            raise HTTPException(400, "Your login is not linked to an employee record")
        return me, False
    if not has_role(role_of(request), HR_WRITE):
        raise HTTPException(403, "Only HR can record WhatsApp consent for someone else")
    return employee_id, True


@router.get("/events")
async def events(request: Request):
    org = org_of(request)
    require_roles(request, ("org_admin",), "see the WhatsApp event templates")   # Integrations screen (admin)
    wa = await store.load(org, "whatsapp")
    cfg = wa["config"] or {}
    return {"events": [{"key": ev, "label": lbl, "kind": kind, "template": WA.template_for(cfg, kind) or None,
                        "own_template": bool(cfg.get(f"template_{ev}"))} for ev, (lbl, kind) in WA.EVENTS.items()],
            "default_consent": DEFAULT_CONSENT, "provider_enabled": bool(wa["enabled"]), "mode": wa["mode"],
            # G7 2026-10-23: consent-only keys (not notification mirrors) — "approvals" = Approve / Reject buttons
            "extra_events": [{"key": k, "label": v} for k, v in WA.OPTIN_EXTRA.items()],
            "approval_consent": WA.APPROVAL_CONSENT}


@router.get("/optins")
async def optins(request: Request):
    org = org_of(request)
    require_roles(request, HR_WRITE, "see WhatsApp consents")
    async with pm.session() as s:
        rows = (await s.execute(select(WhatsappOptin).where(WhatsappOptin.org_id == org)
                                .order_by(WhatsappOptin.at.desc()))).scalars().all()
    return {"items": [_out(o) for o in rows]}


@router.get("/optin/me")
async def my_optin(request: Request):
    org = org_of(request)
    me = request.headers.get("x-employee-id") or ""
    if not me:
        return {"optin": None, "default_consent": DEFAULT_CONSENT}
    async with pm.session() as s:
        o = (await s.execute(select(WhatsappOptin).where(WhatsappOptin.org_id == org, WhatsappOptin.employee_id == me))).scalar_one_or_none()
    return {"optin": _out(o) if o else None, "default_consent": DEFAULT_CONSENT, "agent_chase_consent": AGENT_CHASE_CONSENT,
            "agent_chase_label": WA.OPTIN_EXTRA["agent_chase"]}


class OptIn(BaseModel):
    employee_id: str | None = None
    events: list[str] = []
    consent_text: str | None = None


@router.post("/optin")
async def opt_in(body: OptIn, request: Request):
    org = org_of(request)
    emp_id, by_hr = _target(request, body.employee_id)
    bad = [e for e in body.events if e not in WA.EVENTS and e not in WA.OPTIN_EXTRA]   # G7: + "approvals"
    if bad:
        raise HTTPException(422, f"Unknown message type: {', '.join(bad)}")
    if by_hr and not (body.consent_text or "").strip():
        raise HTTPException(422, "Say how the employee gave consent (for example: signed joining form, 12 Oct 2026)")
    emp = await people.get(org, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    phone = WA.normalise_phone(emp.get("phone") or "")
    if len(phone) < 11:
        raise HTTPException(422, "No mobile number on the employee record — add it first")
    async with pm.session() as s:
        o = (await s.execute(select(WhatsappOptin).where(WhatsappOptin.org_id == org, WhatsappOptin.employee_id == emp_id))).scalar_one_or_none()
        before = list(o.events or []) if (o and o.status == "opted_in") else None    # None = nothing active before
        if not o:
            o = WhatsappOptin(org_id=org, employee_id=emp_id)
            s.add(o)
        o.user_id = emp.get("user_id")
        o.phone_masked, o.phone_hash = WA.mask_phone(phone), WA.phone_hash(phone)
        o.status, o.source, o.events = "opted_in", ("hr_recorded" if by_hr else "self"), list(body.events)
        o.consent_text = (body.consent_text or DEFAULT_CONSENT).strip()[:2000]
        o.recorded_by, o.at, o.revoked_at = actor(request), pm.now_utc(), None
        await s.commit()
        out = _out(o)
    # say exactly which message types were added / withdrawn (agent reminders are revoked by re-saving without them)
    added = sorted(set(out["events"]) - set(before or []))
    removed = sorted(set(before or []) - set(out["events"])) if before is not None else []
    change = "; ".join(x for x in (f"added {', '.join(added)}" if added else "", f"withdrawn {', '.join(removed)}" if removed else "") if x)
    await record_audit(request, "integration.whatsapp.optin", entity_type="employee", entity_id=emp_id,
                       summary=f"WhatsApp opt-in {'recorded by HR' if by_hr else 'by the employee'} for {out['phone']}" + (f" — {change}" if change else ""),
                       meta={"events": out["events"], "added": added, "removed": removed, "previous": before})
    return out


class OptOut(BaseModel):
    employee_id: str | None = None


@router.post("/optout")
async def opt_out(body: OptOut, request: Request):
    org = org_of(request)
    emp_id, by_hr = _target(request, body.employee_id)
    async with pm.session() as s:
        o = (await s.execute(select(WhatsappOptin).where(WhatsappOptin.org_id == org, WhatsappOptin.employee_id == emp_id))).scalar_one_or_none()
        if not o:
            raise HTTPException(404, "No WhatsApp consent on record")
        o.status, o.revoked_at, o.recorded_by = "opted_out", pm.now_utc(), actor(request)
        await s.commit()
        out = _out(o)
    await record_audit(request, "integration.whatsapp.optout", entity_type="employee", entity_id=emp_id,
                       summary=f"WhatsApp opt-out {'recorded by HR' if by_hr else 'by the employee'}")
    return out


class SendIn(BaseModel):
    employee_id: str
    event: str


@router.post("/send")
async def send_event(body: SendIn, request: Request):
    org = org_of(request)
    require_roles(request, ("org_admin",), "send WhatsApp messages by hand")
    if body.event not in WA.EVENTS:
        raise HTTPException(422, "Unknown message type")
    emp = await people.get(org, body.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    phone = emp.get("phone") or ""
    async with pm.session() as s:
        o = (await s.execute(select(WhatsappOptin).where(WhatsappOptin.org_id == org, WhatsappOptin.employee_id == body.employee_id))).scalar_one_or_none()
    if not o or o.status != "opted_in" or o.phone_hash != WA.phone_hash(phone) or (o.events and body.event not in o.events):
        raise HTTPException(409, "This employee has not opted in to this WhatsApp message on their current number")
    label, kind = WA.EVENTS[body.event]
    wa = await store.load(org, "whatsapp")
    link = {"payslip_ready": "/payslips", "leave_approved": "/leave", "attendance_missing": "/attendance",
            "invite": "/login"}[body.event]
    res = await mirror.send_whatsapp(org, wa, to=phone, template=WA.template_for(wa["config"], kind),
                                     params=[label, f"{app_base()}{link}"], kind=kind)
    await record_audit(request, "integration.whatsapp.send", entity_type="employee", entity_id=body.employee_id,
                       summary=f"WhatsApp '{label}' to {WA.mask_phone(phone)}: {res['status']}")
    return res
