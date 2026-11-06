"""Hiring — offers, acceptance, and the hand-off to onboarding.

Offer: Draft -> Approved -> Sent -> Accepted / Declined / Expired / Withdrawn.
  * the offer carries the annual salary (CAD) only — no component break-up is
    computed here (Canadian payroll has no salary-structure preview)
  * above the requisition's budget => a second, different approver (setting)
  * the candidate answers through a single-use, expiring link (only the SHA-256
    of the link token is stored) or HR records the answer
  * Accepted => an onboarding session is created through stream A's create
    route, pre-filled, which also sends the joiner invite
  * Joined is set when that onboarding session is activated as an employee
"""
import asyncio
import os
import sys

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings as cfg  # noqa: E402
from shared.utils.email import brand_html, send_email, strong  # noqa: E402

import hiring_integrations as ext  # noqa: E402
import hiring_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, emp_name, employees_by_id, fail, get_actor, now, num, one, opt_uid, ser, to_date, uid  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HireOffer, HireRequisition  # noqa: E402
from hiring_routers import load_settings, move_application  # noqa: E402

router = APIRouter(prefix="/hiring", tags=["hiring-offers"])

LIVE = ("draft", "approved", "sent", "accepted")


def app_base() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def offer_out(o: HireOffer, c: HireCandidate | None = None, r: HireRequisition | None = None,
              actor: L.Actor | None = None) -> dict:
    d = ser(o, skip=("token_hash",))
    d["candidate_name"] = f"{c.first_name or ''} {c.last_name or ''}".strip() if c else None
    d["candidate_email"] = c.email if c else None
    d["req_number"] = r.req_number if r else None
    d["budget_max"] = float(r.budget_max) if r and r.budget_max is not None else None
    d["approvals_done"] = len(o.approvals or [])
    d["link_state"] = L.token_state(o.status, o.token_expires_at, o.token_used_at) if o.token_hash else None
    if actor:
        mine = any(str(a.get("by")) == actor.user_id for a in (o.approvals or []))
        d["can"] = {
            "edit": o.status == "draft" and actor.is_hr,
            "approve": o.status == "draft" and actor.is_hr_head and not mine,
            "send": o.status == "approved" and actor.is_hr,
            "resend": o.status == "sent" and actor.is_hr,
            "record": o.status == "sent" and actor.is_hr,
            "withdraw": o.status in ("draft", "approved", "sent") and actor.is_hr or (o.status == "accepted" and actor.is_hr_head),
            "retry_onboarding": o.status == "accepted" and not o.onboarding_session_id and actor.is_hr,
            "issue_letter": o.status in ("approved", "sent", "accepted") and not o.letter and actor.is_hr,
        }
    return d


async def _breakup(db, org: str, annual_ctc: float, actor: L.Actor) -> dict:
    """No component break-up in Canada: the offer shows the annual salary only."""
    return {"ok": False, "reason": "This offer shows the annual salary only."}


async def _system_move(db, a: HireApplication, to: str, reason: str, settings: dict, actor: L.Actor | None = None):
    try:
        await move_application(db, actor, a, to, reason, settings, system=True)
    except L.RuleError:
        pass                                           # the card is already somewhere that makes this moot


# ── expiry (lazy: checked whenever offers are read) ─────────────────────────
async def expire_due(db: AsyncSession, org: str) -> int:
    due = (await db.execute(select(HireOffer).where(
        HireOffer.org_id == org, HireOffer.status == "sent", HireOffer.token_expires_at < now()))).scalars().all()
    if not due:
        return 0
    s = await load_settings(db, org)
    n = 0
    for o in due:
        try:
            await cas(db, HireOffer, o, "status", "sent", status="expired", status_reason="The offer link expired.")
        except HTTPException:
            continue
        a = (await db.execute(select(HireApplication).where(HireApplication.id == o.application_id))).scalar_one_or_none()
        if a and a.stage == "offer":
            await _system_move(db, a, "selected", "Offer expired", s)
        await db.commit()
        n += 1
    return n


# ── joined: read back from onboarding ───────────────────────────────────────
async def sync_joined(db: AsyncSession, actor: L.Actor) -> dict:
    """An application becomes Joined when its onboarding session is activated as
    an employee. A requisition becomes Filled when joined >= openings."""
    org = actor.org
    waiting = (await db.execute(select(HireApplication).where(
        HireApplication.org_id == org, HireApplication.stage == "offer",
        HireApplication.onboarding_session_id.is_not(None)))).scalars().all()
    if not waiting:
        return {"checked": 0, "joined": 0}
    sessions = await ext.onboarding_sessions(org, actor.user_id, actor.role)
    if sessions is None:
        return {"checked": len(waiting), "joined": 0, "note": "Onboarding could not be reached."}
    by_id = {str(s.get("id")): s for s in sessions}
    s = await load_settings(db, org)
    joined, touched = 0, set()
    for a in waiting:
        sess = by_id.get(str(a.onboarding_session_id))
        if not sess or sess.get("status") != "activated" or not sess.get("employee_id"):
            continue
        try:
            await move_application(db, None, a, "joined", "Activated as an employee", s, system=True)
        except (L.RuleError, HTTPException):
            await db.rollback()
            continue
        fresh = (await db.execute(select(HireApplication).where(HireApplication.id == a.id))).scalar_one()
        fresh.employee_id, fresh.joined_at = opt_uid(sess.get("employee_id")), now()
        await db.commit()
        # the offer letter issued to the candidate now belongs to the employee
        from letters_routers import relink_recipient_letters    # late import: same service
        for (oid,) in (await db.execute(select(HireOffer.id).where(
                HireOffer.org_id == org, HireOffer.application_id == a.id))).all():
            await relink_recipient_letters(db, str(org), str(oid), str(sess.get("employee_id")))
        joined += 1
        touched.add(a.requisition_id)
    for rid in touched:
        r = (await db.execute(select(HireRequisition).where(HireRequisition.id == rid, HireRequisition.org_id == org))).scalar_one_or_none()
        if not r or r.status != "open":
            continue
        n = (await db.execute(select(func.count()).select_from(HireApplication).where(
            HireApplication.org_id == org, HireApplication.requisition_id == rid,
            HireApplication.stage == "joined"))).scalar() or 0
        if n >= int(r.openings or 0):
            try:
                await cas(db, HireRequisition, r, "status", "open", status="filled", filled_at=now())
                await db.commit()
            except HTTPException:
                pass
    return {"checked": len(waiting), "joined": joined}


@router.post("/sync-joined")
async def sync_joined_route(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can refresh joining status.")
    return await sync_joined(db, actor)


# ── salary preview ─────────────────────────────────────────────────────────────
@router.post("/offers/preview-ctc")
async def preview_ctc(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can prepare an offer.")
    ctc = num(body.get("annual_ctc"), "Annual salary", required=True, lo=1)
    return await _breakup(db, actor.org, ctc, actor)


# ── list / create / edit ────────────────────────────────────────────────────
@router.get("/offers")
async def list_offers(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see offers.")
    await expire_due(db, actor.org)
    rows = (await db.execute(select(HireOffer).where(HireOffer.org_id == actor.org)
                             .order_by(HireOffer.created_at.desc()))).scalars().all()
    if not rows:
        return []
    cands = {str(c.id): c for c in (await db.execute(select(HireCandidate).where(
        HireCandidate.org_id == actor.org, HireCandidate.id.in_({o.candidate_id for o in rows})))).scalars().all()}
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(
        HireRequisition.org_id == actor.org, HireRequisition.id.in_({o.requisition_id for o in rows})))).scalars().all()}
    return [offer_out(o, cands.get(str(o.candidate_id)), reqs.get(str(o.requisition_id)), actor) for o in rows]


async def _fill_offer(db, actor: L.Actor, o: HireOffer, r: HireRequisition, body: dict, settings: dict):
    for k in ("designation", "department", "grade", "location", "employment_type"):
        if k in body:
            setattr(o, k, (str(body.get(k) or "")).strip() or None)
    if not o.designation:
        raise HTTPException(422, "Designation is required.")
    if "manager_id" in body:
        mid = opt_uid(body.get("manager_id"))
        emps = await employees_by_id(db, actor.org, [mid])
        if mid and str(mid) not in emps:
            raise HTTPException(422, "The reporting manager was not found.")
        o.manager_id, o.manager_name = mid, (emp_name(emps[str(mid)]) if mid else None)
    if "joining_date" in body:
        o.joining_date = to_date(body.get("joining_date"))
    if "annual_ctc" in body or o.annual_ctc is None:
        o.annual_ctc = num(body.get("annual_ctc"), "Annual salary", required=True, lo=1)
        o.breakup = await _breakup(db, actor.org, float(o.annual_ctc), actor)
    o.above_budget = L.offer_above_budget(float(o.annual_ctc), float(r.budget_max) if r.budget_max is not None else None)
    o.approvals_required = L.offer_approvals_required(o.above_budget, settings)
    o.approvals = []                                   # any edit sends the offer back for approval


@router.post("/applications/{app_id}/offer", status_code=201)
async def create_offer(app_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not L.offer_can_act("create", actor):
        raise HTTPException(403, "Only HR can prepare an offer.")
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    if a.stage != "selected":
        raise HTTPException(409, "Mark the candidate as Selected before preparing an offer.")
    live = (await db.execute(select(HireOffer.id).where(
        HireOffer.org_id == actor.org, HireOffer.application_id == a.id, HireOffer.status.in_(LIVE)))).first()
    if live:
        raise HTTPException(409, "This candidate already has an offer in progress.")
    r = await one(db, HireRequisition, a.requisition_id, actor.org, "Requisition")
    if r.status not in ("open", "on_hold"):
        raise HTTPException(409, "This requisition is not open, so no new offer can be made on it.")
    s = await load_settings(db, actor.org)
    o = HireOffer(org_id=a.org_id, application_id=a.id, candidate_id=a.candidate_id, requisition_id=r.id,
                  designation=r.designation, department=r.department, grade=r.grade, location=r.location,
                  employment_type=r.employment_type, manager_id=r.hiring_manager_id,
                  manager_name=r.hiring_manager_name, status="draft", approvals=[], created_by=actor.user_id)
    await _fill_offer(db, actor, o, r, body, s)
    db.add(o)
    await move_application(db, actor, a, "offer", "Offer prepared", s, system=True)
    await db.commit()
    await db.refresh(o)
    c = await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate")
    return offer_out(o, c, r, actor)


@router.patch("/offers/{offer_id}")
async def update_offer(offer_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not L.offer_can_act("edit", actor):
        raise HTTPException(403, "Only HR can edit an offer.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if o.status != "draft":
        raise HTTPException(409, "Only a draft offer can be edited. Withdraw it and prepare a new one.")
    r = await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition")
    await _fill_offer(db, actor, o, r, body, await load_settings(db, actor.org))
    await db.commit()
    await db.refresh(o)
    return offer_out(o, await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate"), r, actor)


# ── approve / send ──────────────────────────────────────────────────────────
@router.post("/offers/{offer_id}/approve")
async def approve_offer(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    from hiring_d2_models import HireOfferApprovalStep           # D2: an offer in the chain is decided there
    chained = (await db.execute(select(HireOfferApprovalStep.id).where(
        HireOfferApprovalStep.offer_id == o.id, HireOfferApprovalStep.status.in_(("pending", "waiting"))))).first()
    if chained:
        raise HTTPException(409, "This offer is in the approval chain. Approve or reject it from its chain step (or the Approvals inbox).")
    try:
        L.offer_next(o.status, "approve")
        read = list(o.approvals or [])
        approvals, status = L.offer_approve(actor, read, int(o.approvals_required or 1))
    except L.RuleError as e:
        fail(e)
    await cas(db, HireOffer, o, "status", "draft",
              extra_where=[func.jsonb_array_length(HireOffer.approvals) == len(read)],
              approvals=approvals, status=status)
    await db.commit()
    await db.refresh(o)
    await record_audit(request, "hiring.offer.approve", entity_type="hire_offer", entity_id=str(o.id),
                       summary=f"Offer approval {len(approvals)} of {o.approvals_required}",
                       meta={"above_budget": o.above_budget, "status": status})
    return offer_out(o, await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate"),
                     await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition"), actor)


def letter_data(o: HireOffer) -> dict:
    """The offer in the letter engine's own field names ({{offer.*}})."""
    offer = {
        "position": o.designation, "location": o.location, "reporting_to": o.manager_name,
        "joining_date": o.joining_date.isoformat() if o.joining_date else None,
        "valid_until": o.token_expires_at.date().isoformat() if o.token_expires_at else None,
        "annual_ctc": float(o.annual_ctc),
    }
    return {"offer": {k: v for k, v in offer.items() if v is not None}}


async def _try_letter(db, o: HireOffer, actor: L.Actor) -> None:
    """Issue the offer letter once: to the candidate before joining, to the employee
    record after (see hiring_integrations). An offer that already has its letter
    keeps it — issuing again would give the same offer a second number."""
    if o.letter:
        return
    a = (await db.execute(select(HireApplication).where(HireApplication.id == o.application_id))).scalar_one_or_none()
    employee_id = str(a.employee_id) if a and a.employee_id else None
    recipient = None
    if not employee_id:                                # before joining: addressed to the candidate
        c = (await db.execute(select(HireCandidate).where(HireCandidate.id == o.candidate_id))).scalar_one_or_none()
        org = await ext.org_by_id(db, str(o.org_id))
        if c:
            recipient = {"kind": "candidate", "ref": str(o.id), "first_name": c.first_name,
                         "last_name": c.last_name, "email": c.email, "address": c.city,
                         "country": org.get("country"), "designation": o.designation, "department": o.department,
                         "location": o.location, "grade": o.grade, "manager_name": o.manager_name,
                         "joining_date": o.joining_date.isoformat() if o.joining_date else None,
                         "employment_type": o.employment_type, "annual_ctc": float(o.annual_ctc)}
    data = letter_data(o)
    lines = (o.breakup or {}).get("lines") or []
    if lines:                                          # a stored break-up (if any), as the annexure table
        data["salary"] = {"components": {ln.get("code") or ln.get("name"): ln.get("monthly") for ln in lines
                                         if ln.get("monthly") is not None}}
        data["component_names"] = {(ln.get("code") or ln.get("name")): ln.get("name") for ln in lines}
    res = await ext.issue_offer_letter(str(o.org_id), employee_id, data,
                                       {"id": actor.user_id, "role": actor.role, "email": actor.email or "system"},
                                       recipient=recipient)
    if res["ok"]:
        o.letter, o.letter_note = res["letter"], None
    else:
        o.letter_note = res["reason"][:400]


async def _issue_link(db, o: HireOffer, actor: L.Actor, read_status: str, settings: dict) -> str:
    raw, hashed = L.new_token()
    await cas(db, HireOffer, o, "status", read_status, status="sent", token_hash=hashed,
              token_expires_at=L.token_expiry(now(), settings["offer_valid_days"]), token_used_at=None,
              sent_at=now(), sent_by=actor.user_id, sent_by_role=actor.role)
    await db.commit()
    await db.refresh(o)
    return raw


async def _mail_offer(o: HireOffer, c: HireCandidate, org_name: str, link: str):
    if not c.email:
        return
    until = f"{o.token_expires_at:%d %b %Y}" if o.token_expires_at else ""
    html = brand_html(f"Your offer from {org_name or 'us'}",
                      [f"Hi {c.first_name},", ("We are pleased to offer you the role of ", strong(o.designation), "."),
                       f"Open the link to read the offer and answer it. The link works once and is valid until {until}."],
                      cta_label="View your offer", cta_url=link)
    asyncio.create_task(send_email(c.email, f"Your offer - {o.designation}",
                                   f"Hi {c.first_name},\n\nRead and answer your offer here:\n{link}\n\n"
                                   f"The link is valid until {until}.", html=html))


@router.post("/offers/{offer_id}/send")
async def send_offer(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Approved -> Sent. Issues the single-use link (returned ONCE here so HR can
    also copy it) and asks the letter engine for the offer letter."""
    actor = await get_actor(request, db)
    if not L.offer_can_act("send", actor):
        raise HTTPException(403, "Only HR can send an offer.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    try:
        L.offer_next(o.status, "send")
    except L.RuleError as e:
        fail(e)
    if not o.joining_date:
        raise HTTPException(422, "Set the joining date before sending.")
    s = await load_settings(db, actor.org)
    raw = await _issue_link(db, o, actor, "approved", s)
    await _try_letter(db, o, actor)
    await db.commit()
    await db.refresh(o)
    c = await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate")
    r = await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition")
    link = f"{app_base()}/offer/{raw}"
    await _mail_offer(o, c, (await ext.org_by_id(db, actor.org)).get("name"), link)
    await record_audit(request, "hiring.offer.send", entity_type="hire_offer", entity_id=str(o.id),
                       summary=f"Offer sent for {o.designation}", meta={"letter_issued": bool(o.letter)})
    return {**offer_out(o, c, r, actor), "accept_link": link, "emailed": bool(c.email)}


@router.post("/offers/{offer_id}/resend")
async def resend_offer(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A fresh link for an offer that is still Sent. The old link stops working."""
    actor = await get_actor(request, db)
    if not L.offer_can_act("send", actor):
        raise HTTPException(403, "Only HR can send an offer.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if o.status != "sent":
        raise HTTPException(409, "Only an offer that is Sent can be sent again.")
    raw = await _issue_link(db, o, actor, "sent", await load_settings(db, actor.org))
    c = await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate")
    r = await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition")
    link = f"{app_base()}/offer/{raw}"
    await _mail_offer(o, c, (await ext.org_by_id(db, actor.org)).get("name"), link)
    return {**offer_out(o, c, r, actor), "accept_link": link, "emailed": bool(c.email)}


@router.post("/offers/{offer_id}/issue-letter")
async def issue_letter_route(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can issue the offer letter.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if o.status not in ("approved", "sent", "accepted"):
        raise HTTPException(409, "The offer letter can be issued once the offer is approved.")
    await _try_letter(db, o, actor)
    await db.commit()
    await db.refresh(o)
    return offer_out(o, await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate"),
                     await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition"), actor)


# ── the answer (shared by the public link and by HR) ────────────────────────
def onboarding_payload(o: HireOffer, c: HireCandidate, country: str | None) -> tuple[dict, dict]:
    """(fields stream A's create route knows, extra fields merged into the session data)."""
    payload = {
        "first_name": c.first_name, "last_name": c.last_name or "", "email": c.email,
        "job_title": o.designation, "department": o.department, "work_location": o.location,
        "employment_type": o.employment_type if o.employment_type in ("full_time", "part_time", "contract", "seasonal") else "full_time",
        "hire_date": o.joining_date.isoformat() if o.joining_date else None,
        "manager_name": o.manager_name, "pay_type": "salary", "annual_salary": float(o.annual_ctc),
        # stream A's create schema takes these directly; they are ALSO merged as `extra`
        # below in case an older schema drops them
        "manager_id": str(o.manager_id) if o.manager_id else None, "grade": o.grade,
        "address_country": "CA",
    }
    extra = {
        "phone": c.phone, "grade": o.grade, "manager_id": str(o.manager_id) if o.manager_id else None,
        "city": c.city, "address_country": "CA",
        "offered_annual_ctc": float(o.annual_ctc), "offered_ctc_breakup": (o.breakup or {}).get("lines") or None,
        "hiring_offer_id": str(o.id), "hiring_application_id": str(o.application_id),
        "hiring_requisition_id": str(o.requisition_id), "source": "hiring_offer",
    }
    return ({k: v for k, v in payload.items() if v is not None or k == "province_of_employment"},
            {k: v for k, v in extra.items() if v is not None})


async def start_onboarding(db: AsyncSession, o: HireOffer, user_id: str, role: str) -> dict:
    """Create the onboarding session for an accepted offer. Safe to call again:
    it does nothing once the offer already carries a session."""
    if o.onboarding_session_id:
        return {"ok": True, "session_id": o.onboarding_session_id}
    c = (await db.execute(select(HireCandidate).where(HireCandidate.id == o.candidate_id))).scalar_one()
    org = await ext.org_by_id(db, str(o.org_id))
    payload, extra = onboarding_payload(o, c, org.get("country"))
    res = await ext.create_onboarding_session(str(o.org_id), payload, extra, user_id or "", role or "hr_admin")
    if res["ok"] and res.get("session_id"):
        o.onboarding_session_id, o.onboarding_note = str(res["session_id"]), None
        a = (await db.execute(select(HireApplication).where(HireApplication.id == o.application_id))).scalar_one_or_none()
        if a:
            a.onboarding_session_id = str(res["session_id"])
    else:
        o.onboarding_note = (res.get("reason") or "Onboarding did not return a session.")[:400]
    await db.commit()
    return res


async def answer_offer(db: AsyncSession, o: HireOffer, accepted: bool, via: str, *, signed_name: str | None = None,
                       reason: str | None = None, token_hash: str | None = None, actor: L.Actor | None = None) -> HireOffer:
    """Sent -> Accepted / Declined, written with WHERE status='sent' (and, for
    the public link, WHERE the token is the stored one, unused and unexpired),
    so a link can be used once and two answers cannot both win."""
    extra = []
    if via == "link":
        extra = [HireOffer.token_hash == token_hash, HireOffer.token_used_at.is_(None),
                 HireOffer.token_expires_at > now()]
    await cas(db, HireOffer, o, "status", "sent", extra_where=extra,
              status="accepted" if accepted else "declined", responded_at=now(), response_via=via,
              token_used_at=now(), signed_name=(signed_name or "").strip()[:200] or None,
              status_reason=(reason or "").strip()[:400] or None)
    await db.commit()
    await db.refresh(o)
    s = await load_settings(db, str(o.org_id))
    a = (await db.execute(select(HireApplication).where(HireApplication.id == o.application_id))).scalar_one_or_none()
    if accepted:
        await start_onboarding(db, o, (actor.user_id if actor else o.sent_by) or "",
                               (actor.role if actor else o.sent_by_role) or "hr_admin")
        await db.refresh(o)
    elif a and a.stage == "offer":
        await _system_move(db, a, "withdrawn", "Offer declined" + (f": {reason}" if reason else ""), s, actor)
        await db.commit()
    await ext.notify(str(o.org_id), roles=["hr"],
                     title="Offer accepted" if accepted else "Offer declined",
                     body=f"{o.designation}" + ("" if accepted or not reason else f" · {reason}"),
                     link="/hiring/offers", kind="info")
    return o


@router.post("/offers/{offer_id}/record-response")
async def record_response(offer_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """HR records what the candidate said (phone / e-mail / in person)."""
    actor = await get_actor(request, db)
    if not L.offer_can_act("record", actor):
        raise HTTPException(403, "Only HR can record the candidate's answer.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    accepted = bool(body.get("accepted"))
    try:
        L.offer_next(o.status, "accept" if accepted else "decline")
    except L.RuleError as e:
        fail(e)
    reason = (body.get("reason") or "").strip()
    if not accepted and not reason:
        raise HTTPException(422, "Type why the candidate declined.")
    o = await answer_offer(db, o, accepted, "hr", reason=reason, actor=actor)
    await record_audit(request, "hiring.offer.response", entity_type="hire_offer", entity_id=str(o.id),
                       summary=f"Offer {'accepted' if accepted else 'declined'} (recorded by HR)", meta={"reason": reason or None})
    return offer_out(o, await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate"),
                     await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition"), actor)


@router.post("/offers/{offer_id}/retry-onboarding")
async def retry_onboarding(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can start onboarding.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if o.status != "accepted":
        raise HTTPException(409, "Onboarding starts only for an accepted offer.")
    res = await start_onboarding(db, o, actor.user_id, actor.role)
    if not res.get("ok"):
        raise HTTPException(502, res.get("reason") or "Onboarding could not be started.")
    await db.refresh(o)
    return offer_out(o, await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate"),
                     await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition"), actor)


@router.post("/offers/{offer_id}/withdraw")
async def withdraw_offer(offer_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Withdraw before an answer: the candidate goes back to Selected.
    Withdraw AFTER acceptance (the person will not join): HR head only; the
    application closes as Withdrawn and the onboarding session is cancelled."""
    actor = await get_actor(request, db)
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    read = o.status
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(422, "Give a reason for withdrawing the offer.")
    if read == "accepted":
        if not actor.is_hr_head:
            raise HTTPException(403, "Only an HR head or an org admin can withdraw an accepted offer.")
    else:
        if not L.offer_can_act("withdraw", actor):
            raise HTTPException(403, "Only HR can withdraw an offer.")
        try:
            L.offer_next(read, "withdraw")
        except L.RuleError as e:
            fail(e)
    await cas(db, HireOffer, o, "status", read, status="withdrawn", status_reason=reason[:400], token_used_at=now())
    s = await load_settings(db, actor.org)
    a = (await db.execute(select(HireApplication).where(HireApplication.id == o.application_id))).scalar_one_or_none()
    if a and a.stage == "offer":
        await _system_move(db, a, "withdrawn" if read == "accepted" else "selected", f"Offer withdrawn: {reason}", s, actor)
    await db.commit()
    await db.refresh(o)
    if read == "accepted" and o.onboarding_session_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                await c.patch(f"{cfg.ONBOARDING_URL}/onboarding/sessions/{o.onboarding_session_id}",
                              json={"status": "cancelled"}, headers=ext.fwd_headers(actor.org, actor.user_id, actor.role))
        except httpx.HTTPError:
            pass
    await record_audit(request, "hiring.offer.withdraw", entity_type="hire_offer", entity_id=str(o.id),
                       summary=f"Offer withdrawn (was {read})", meta={"reason": reason})
    return offer_out(o, await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate"),
                     await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition"), actor)
