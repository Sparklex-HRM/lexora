"""Hiring D2 — interview scheduling depth: slot proposals + candidate self-pick link,
calendar sync (Google Calendar / Microsoft 365 through the shared adapters, OAuth per org,
tokens encrypted at rest), reminders, reschedule and cancel.

  GET    /hiring/calendar                              adapters + the org's connections (never the tokens)
  POST   /hiring/calendar/{provider}/connect           HR head: {auth_url} to send the organiser's browser to
  DELETE /hiring/calendar/{provider}                   disconnect (tokens wiped)
  POST   /hiring/interviews/{id}/slots                 propose slots → single-use pick link for the candidate
  GET    /hiring/interviews/{id}/scheduling            slots + calendar event state
  POST   /hiring/interviews/{id}/sync-calendar         create / update the provider event (Meet / Teams link when returned)
  POST   /hiring/interviews/{id}/reschedule            {scheduled_at, reason}
  POST   /hiring/interviews/{id}/cancel                {reason}
  POST   /hiring/interviews/send-reminders             HR: candidate reminders for rounds inside the reminder window

HONESTY: without the platform OAuth app (LEXORA_GOOGLE_* / LEXORA_MS_*) the providers say
"not configured"; without a connected organiser calendar nothing is synced and the screen says so.
A join link is stored only when the provider returned one.
"""
import logging
import os
import sys
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import crypto  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.providers.calendar_base import Event, all_calendar_adapters, calendar_adapter, token_is_fresh  # noqa: E402

import hiring_d2_logic as D  # noqa: E402
import hiring_integrations as ext  # noqa: E402
from database import get_db  # noqa: E402
from hiring_comms import fmt_when, send_event  # noqa: E402
from hiring_common import cas, get_actor, now, one, ser, to_datetime, uid  # noqa: E402
from hiring_d2_models import HireCalendarConnection, HireCalendarEvent, HireCommsLog, HireInterviewSlots  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HireInterview, HireRequisition  # noqa: E402
from hiring_routers import _app_guard, load_settings  # noqa: E402
from models import Employee  # noqa: E402

log = logging.getLogger("lexora.hiring.scheduling")
router = APIRouter(prefix="/hiring", tags=["hiring-scheduling"])
PROVIDERS = ("google", "microsoft")
AAD = "hire_calendar"


def app_base() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def public_api_base() -> str:
    return (os.getenv("LEXORA_PUBLIC_API_URL") or f"{app_base()}/api").rstrip("/")


def redirect_uri(provider: str) -> str:
    return f"{public_api_base()}/hiring/public/calendar/{provider}/callback"


# ── connections ─────────────────────────────────────────────────────────────
def conn_out(c: HireCalendarConnection | None) -> dict | None:
    if not c:
        return None
    return {"id": str(c.id), "provider": c.provider, "status": c.status, "account_email": c.account_email,
            "connected_by_name": c.connected_by_name, "last_error": c.last_error,
            "token_expires_at": c.token_expires_at.isoformat() if c.token_expires_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


async def connection(db: AsyncSession, org, provider: str) -> HireCalendarConnection | None:
    return (await db.execute(select(HireCalendarConnection).where(
        HireCalendarConnection.org_id == org, HireCalendarConnection.provider == provider))).scalar_one_or_none()


async def active_connection(db: AsyncSession, org, preferred: str | None = None) -> HireCalendarConnection | None:
    rows = (await db.execute(select(HireCalendarConnection).where(
        HireCalendarConnection.org_id == org, HireCalendarConnection.status == "connected"))).scalars().all()
    if preferred:
        for c in rows:
            if c.provider == preferred:
                return c
    return rows[0] if rows else None


async def store_tokens(db: AsyncSession, org, provider: str, tokens, user_id: str, user_name: str) -> HireCalendarConnection:
    c = await connection(db, org, provider)
    if not c:
        c = HireCalendarConnection(org_id=uid(org), provider=provider)
        db.add(c)
    c.access_token_enc = crypto.encrypt(tokens.access_token, aad=AAD)
    if tokens.refresh_token:
        c.refresh_token_enc = crypto.encrypt(tokens.refresh_token, aad=AAD)
    c.token_expires_at, c.scope = tokens.expires_at, tokens.scope
    if tokens.account_email:
        c.account_email = tokens.account_email
    c.connected_by_user_id, c.connected_by_name = user_id, user_name
    c.status, c.last_error, c.updated_at = "connected", None, now()
    return c


async def access_token(db: AsyncSession, c: HireCalendarConnection) -> str | None:
    """A fresh access token for the connection (refreshes through the provider when stale)."""
    try:
        if token_is_fresh(c.token_expires_at) and c.access_token_enc:
            return crypto.decrypt(c.access_token_enc, aad=AAD)
        refresh = crypto.decrypt(c.refresh_token_enc, aad=AAD) if c.refresh_token_enc else None
    except crypto.CryptoError as e:
        c.status, c.last_error = "error", f"Stored token unreadable ({e}). Connect the calendar again."
        return None
    if not refresh:
        c.status, c.last_error = "error", "No refresh token. Connect the calendar again."
        return None
    adapter = calendar_adapter(c.provider)
    t = await adapter.refresh(refresh, org_id=str(c.org_id))
    if not t.ok:
        c.status, c.last_error = "error", (t.error or "Token refresh failed")[:400]
        return None
    c.access_token_enc = crypto.encrypt(t.access_token, aad=AAD)
    if t.refresh_token:
        c.refresh_token_enc = crypto.encrypt(t.refresh_token, aad=AAD)
    c.token_expires_at, c.status, c.last_error, c.updated_at = t.expires_at, "connected", None, now()
    return t.access_token


@router.get("/calendar")
async def calendar_status(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see calendar connections.")
    out = []
    for a in all_calendar_adapters():
        out.append({**a.describe(), "redirect_uri": redirect_uri(a.key), "connection": conn_out(await connection(db, actor.org, a.key))})
    return {"providers": out, "can_connect": actor.is_hr_head,
            "note": "Interview invites are created on the connected organiser calendar; the candidate and the panel are attendees. "
                    "A Meet / Teams link is attached when the provider returns one."}


@router.post("/calendar/{provider}/connect")
async def connect(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can connect a calendar.")
    adapter = calendar_adapter(provider)
    if not adapter:
        raise HTTPException(404, "Unknown calendar provider")
    if not adapter.configured():
        raise HTTPException(409, adapter.status()["message"])
    state = D.sign_state({"org": actor.org, "user": actor.user_id, "name": actor.name, "provider": provider})
    return {"auth_url": adapter.auth_url(redirect_uri(provider), state), "redirect_uri": redirect_uri(provider)}


@router.delete("/calendar/{provider}")
async def disconnect(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can disconnect a calendar.")
    c = await connection(db, actor.org, provider)
    if not c:
        raise HTTPException(404, "No connection")
    c.access_token_enc = c.refresh_token_enc = None
    c.status, c.last_error, c.updated_at = "revoked", None, now()
    await db.commit()
    await record_audit(request, "hiring.calendar.disconnect", entity_type="hire_calendar_connection", entity_id=str(c.id),
                       summary=f"{provider} calendar disconnected", meta={})
    return {"ok": True}


# ── the provider event behind an interview ──────────────────────────────────
async def _panel_emails(db, org, panel: list) -> list[dict]:
    ids = [uid(p.get("employee_id")) for p in (panel or []) if p.get("employee_id")]
    if not ids:
        return []
    rows = (await db.execute(select(Employee).where(Employee.org_id == org, Employee.id.in_(ids)))).scalars().all()
    return [{"email": (e.work_email or e.email), "name": f"{e.first_name or ''} {e.last_name or ''}".strip()}
            for e in rows if (e.work_email or e.email)]


async def build_event(db, org, iv: HireInterview, s: dict) -> Event | None:
    if not iv.scheduled_at:
        return None
    a = (await db.execute(select(HireApplication).where(HireApplication.id == iv.application_id))).scalar_one_or_none()
    c = (await db.execute(select(HireCandidate).where(HireCandidate.id == a.candidate_id))).scalar_one_or_none() if a else None
    r = (await db.execute(select(HireRequisition).where(HireRequisition.id == a.requisition_id))).scalar_one_or_none() if a else None
    slots = (await db.execute(select(HireInterviewSlots).where(
        HireInterviewSlots.interview_id == iv.id, HireInterviewSlots.status == "picked"))).scalars().first()
    duration = int(slots.duration_min) if slots else 45
    attendees = await _panel_emails(db, org, iv.panel)
    if c and c.email:
        attendees.append({"email": c.email, "name": f"{c.first_name} {c.last_name or ''}".strip()})
    name = f"{c.first_name} {c.last_name or ''}".strip() if c else "Candidate"
    return Event(title=f"Interview: {r.designation if r else ''} — {iv.round_name} ({name})".strip(),
                 start=iv.scheduled_at, end=iv.scheduled_at + timedelta(minutes=duration),
                 description=f"Round {iv.round_no}: {iv.round_name}\nCandidate: {name}\nRequisition: {r.req_number if r else ''}\n"
                             f"Scheduled through Lexora HRM.",
                 attendees=attendees, timezone=(slots.timezone if slots else "America/Toronto"),
                 online_meeting=(iv.mode == "video"), location=(iv.place or "") if iv.mode != "video" else "",
                 request_id=f"lexora-iv-{iv.id}")


async def sync_event(db: AsyncSession, org, iv: HireInterview, *, cancel: bool = False, provider: str | None = None) -> dict:
    """Create / update / cancel the provider event for one interview. Best-effort; the
    outcome is written to hire_calendar_events and returned. Caller commits."""
    ev_row = (await db.execute(select(HireCalendarEvent).where(
        HireCalendarEvent.org_id == org, HireCalendarEvent.interview_id == iv.id)
        .order_by(HireCalendarEvent.synced_at.desc()))).scalars().first()
    conn = None
    if ev_row and ev_row.connection_id:
        conn = (await db.execute(select(HireCalendarConnection).where(HireCalendarConnection.id == ev_row.connection_id))).scalar_one_or_none()
    if not conn or conn.status != "connected":
        conn = await active_connection(db, org, provider)
    if not conn:
        return {"ok": False, "reason": "No organiser calendar is connected (Hiring, Settings, Calendars)."}
    adapter = calendar_adapter(conn.provider)
    if not adapter or not adapter.configured():
        return {"ok": False, "reason": adapter.status()["message"] if adapter else "Unknown provider"}
    token = await access_token(db, conn)
    if not token:
        return {"ok": False, "reason": conn.last_error or "The calendar connection needs to be made again."}
    if cancel:
        if not ev_row or not ev_row.event_id or ev_row.status == "cancelled":
            return {"ok": True, "status": "nothing_to_cancel"}
        res = await adapter.cancel_event(token, ev_row.event_id, org_id=str(org))
        ev_row.status, ev_row.last_error, ev_row.synced_at = ("cancelled" if res.ok else "failed"), (res.error if not res.ok else None), now()
        return {"ok": res.ok, "status": ev_row.status, "reason": res.error}
    s = await load_settings(db, org)
    ev = await build_event(db, org, iv, s)
    if ev is None:
        return {"ok": False, "reason": "Set the date and time first."}
    if ev_row and ev_row.event_id and ev_row.status in ("created", "updated"):
        res = await adapter.update_event(token, ev_row.event_id, ev, org_id=str(org))
        action = "updated"
    else:
        res = await adapter.create_event(token, ev, org_id=str(org))
        action = "created"
        ev_row = HireCalendarEvent(org_id=uid(org), interview_id=iv.id, provider=conn.provider, connection_id=conn.id)
        db.add(ev_row)
    if res.ok:
        ev_row.event_id = res.event_id or ev_row.event_id
        ev_row.join_url = res.join_url or ev_row.join_url
        ev_row.html_link = res.html_link or ev_row.html_link
        ev_row.status, ev_row.last_error = action, None
        if ev_row.join_url and iv.mode == "video" and not iv.place:
            iv.place = ev_row.join_url
    else:
        ev_row.status, ev_row.last_error = "failed", (res.error or "The provider refused")[:400]
    ev_row.synced_at = now()
    await db.flush()
    return {"ok": res.ok, "status": ev_row.status, "join_url": ev_row.join_url, "html_link": ev_row.html_link,
            "provider": conn.provider, "reason": ev_row.last_error}


async def event_out(db, org, iv_id) -> dict | None:
    row = (await db.execute(select(HireCalendarEvent).where(HireCalendarEvent.org_id == org, HireCalendarEvent.interview_id == iv_id)
                            .order_by(HireCalendarEvent.synced_at.desc()))).scalars().first()
    return ser(row) if row else None


# ── reminders ───────────────────────────────────────────────────────────────
async def arm_panel_reminder(db, org, iv: HireInterview, hours: int) -> None:
    """Panel reminder through the notification service's scheduler (idempotent per interview)."""
    try:
        from shared.utils.notify import cancel_later, notify_later
        key = f"hire_iv_{iv.id}"
        if iv.status != "scheduled" or not iv.scheduled_at:
            await cancel_later(str(org), key)
            return
        users = await ext.user_ids_for_employees(db, str(org), [p.get("employee_id") for p in (iv.panel or [])])
        if not users:
            return
        await notify_later(str(org), due_at=iv.scheduled_at - timedelta(hours=int(hours or 24)), key=key, user_ids=users,
                           title=f"Interview reminder: {iv.round_name} at {fmt_when(iv.scheduled_at)}",
                           link="/hiring/my-interviews", kind="hiring")
    except Exception as e:                                   # noqa: BLE001
        log.warning("panel reminder failed: %s", e)


async def after_scheduled(db: AsyncSession, actor, iv: HireInterview, a: HireApplication) -> dict:
    """Hook from hiring_routers when a round gets (or changes) its date: calendar sync when a
    calendar is connected, candidate message, panel reminder. Best-effort; caller commits."""
    out = {}
    try:
        s = await load_settings(db, actor.org if actor else str(iv.org_id))
        org = iv.org_id
        if iv.scheduled_at:
            if await active_connection(db, org):
                out["calendar"] = await sync_event(db, org, iv)
            c = (await db.execute(select(HireCandidate).where(HireCandidate.id == a.candidate_id))).scalar_one_or_none()
            ev = await event_out(db, org, iv.id)
            if c is not None:
                out["comms"] = await send_event(db, org, c, a, "interview_scheduled", {
                    "interview_time": fmt_when(iv.scheduled_at), "interview_mode": iv.mode.replace("_", " "),
                    "join_link": (ev or {}).get("join_url") or iv.place or "", "round_name": iv.round_name})
            await arm_panel_reminder(db, org, iv, s["interview_reminder_hours"])
    except Exception as e:                                   # noqa: BLE001
        log.warning("after_scheduled failed: %s", e)
    return out


@router.post("/interviews/send-reminders")
async def send_reminders(request: Request, db: AsyncSession = Depends(get_db)):
    """Candidate reminders (e-mail / WhatsApp) for rounds inside the reminder window, once per round."""
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can send reminders.")
    s = await load_settings(db, actor.org)
    until = now() + timedelta(hours=int(s["interview_reminder_hours"]))
    ivs = (await db.execute(select(HireInterview).where(
        HireInterview.org_id == actor.org, HireInterview.status == "scheduled",
        HireInterview.scheduled_at > now(), HireInterview.scheduled_at <= until))).scalars().all()
    done = {str(x[0]) for x in (await db.execute(select(HireCommsLog.application_id).where(
        HireCommsLog.org_id == actor.org, HireCommsLog.event == "interview_reminder", HireCommsLog.at > now() - timedelta(days=2)))).all()}
    sent = 0
    for iv in ivs:
        if str(iv.application_id) in done:
            continue
        a = (await db.execute(select(HireApplication).where(HireApplication.id == iv.application_id))).scalar_one_or_none()
        c = (await db.execute(select(HireCandidate).where(HireCandidate.id == a.candidate_id))).scalar_one_or_none() if a else None
        if not c:
            continue
        ev = await event_out(db, actor.org, iv.id)
        await send_event(db, actor.org, c, a, "interview_reminder", {
            "interview_time": fmt_when(iv.scheduled_at), "interview_mode": iv.mode.replace("_", " "),
            "join_link": (ev or {}).get("join_url") or iv.place or "", "round_name": iv.round_name})
        sent += 1
    await db.commit()
    return {"window_hours": s["interview_reminder_hours"], "rounds": len(ivs), "reminded": sent}


# ── slots + self-schedule link ──────────────────────────────────────────────
def slots_out(row: HireInterviewSlots | None) -> dict | None:
    if not row:
        return None
    d = ser(row, skip=("token_hash",))
    d["link_state"] = D.slot_link_state(row.status, row.token_expires_at)
    return d


@router.post("/interviews/{iv_id}/slots", status_code=201)
async def propose_slots(iv_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    if iv.status == "cancelled":
        raise HTTPException(409, "This interview is cancelled.")
    s = await load_settings(db, actor.org)
    try:
        slots = D.clean_slots(body.get("slots") or [], body.get("duration_min") or 45)
    except D.RuleError as e:
        raise HTTPException(e.status, e.message)
    for old in (await db.execute(select(HireInterviewSlots).where(
            HireInterviewSlots.interview_id == iv.id, HireInterviewSlots.status == "proposed"))).scalars().all():
        old.status = "cancelled"
    raw, hashed = D.new_token()
    row = HireInterviewSlots(org_id=iv.org_id, interview_id=iv.id, application_id=a.id, slots=slots,
                             duration_min=int(body.get("duration_min") or 45),
                             timezone=(body.get("timezone") or "America/Toronto")[:60], token_hash=hashed,
                             token_expires_at=now() + timedelta(days=int(s["interview_slot_link_days"])),
                             status="proposed", created_by=actor.user_id)
    db.add(row)
    await db.flush()
    link = f"{app_base()}/interview/{raw}"
    c = await one(db, HireCandidate, a.candidate_id, actor.org, "Candidate")
    comms = await send_event(db, actor.org, c, a, "slot_pick", {"link": link, "round_name": iv.round_name})
    await db.commit()
    await record_audit(request, "hiring.interview.slots", entity_type="hire_interview", entity_id=str(iv.id),
                       summary=f"{len(slots)} slot(s) proposed for {iv.round_name}", meta={"count": len(slots)})
    return {**slots_out(row), "pick_link": link, "comms": comms,
            "note": "The link is shown only now. It works once and expires with the slots."}


@router.get("/interviews/{iv_id}/scheduling")
async def scheduling(iv_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    row = (await db.execute(select(HireInterviewSlots).where(HireInterviewSlots.interview_id == iv.id)
                            .order_by(HireInterviewSlots.created_at.desc()))).scalars().first()
    conns = [conn_out(c) for c in (await db.execute(select(HireCalendarConnection).where(
        HireCalendarConnection.org_id == actor.org, HireCalendarConnection.status == "connected"))).scalars().all()]
    return {"interview": ser(iv), "slots": slots_out(row), "calendar": await event_out(db, actor.org, iv.id),
            "connections": conns, "can_sync": bool(conns) and bool(iv.scheduled_at) and iv.status == "scheduled"}


@router.post("/interviews/{iv_id}/sync-calendar")
async def sync_calendar(iv_id: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    if iv.status == "cancelled":
        raise HTTPException(409, "This interview is cancelled.")
    res = await sync_event(db, actor.org, iv, provider=(body or {}).get("provider"))
    await db.commit()
    if not res.get("ok"):
        raise HTTPException(409, res.get("reason") or "The calendar event could not be written.")
    return {**res, "calendar": await event_out(db, actor.org, iv.id)}


@router.post("/interviews/{iv_id}/reschedule")
async def reschedule(iv_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    if iv.status == "cancelled":
        raise HTTPException(409, "This interview is cancelled. Schedule a new round instead.")
    when = to_datetime(body.get("scheduled_at"))
    if not when:
        raise HTTPException(422, "Pick the new date and time.")
    if when <= now():
        raise HTTPException(422, "The new time must be in the future.")
    reason = (body.get("reason") or "").strip()[:400]
    old = iv.scheduled_at
    iv.scheduled_at = when
    for row in (await db.execute(select(HireInterviewSlots).where(
            HireInterviewSlots.interview_id == iv.id, HireInterviewSlots.status == "proposed"))).scalars().all():
        row.status = "cancelled"
    await db.flush()
    out = await after_scheduled(db, actor, iv, a)
    await db.commit()
    users = await ext.user_ids_for_employees(db, actor.org, [p.get("employee_id") for p in (iv.panel or [])])
    await ext.notify(actor.org, user_ids=users, title=f"Interview rescheduled: {iv.round_name} now {fmt_when(when)}",
                     body=reason, link="/hiring/my-interviews", kind="hiring")
    await record_audit(request, "hiring.interview.reschedule", entity_type="hire_interview", entity_id=str(iv.id),
                       summary=f"{iv.round_name} moved to {fmt_when(when)}", meta={"from": old.isoformat() if old else None, "reason": reason or None})
    return {**ser(iv), "sync": out}


@router.post("/interviews/{iv_id}/cancel")
async def cancel_interview(iv_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    reason = (body.get("reason") or "").strip()[:400]
    if not reason:
        raise HTTPException(422, "Say why the round is cancelled.")
    await cas(db, HireInterview, iv, "status", "scheduled", status="cancelled")
    for row in (await db.execute(select(HireInterviewSlots).where(
            HireInterviewSlots.interview_id == iv.id, HireInterviewSlots.status == "proposed"))).scalars().all():
        row.status = "cancelled"
    cal = await sync_event(db, actor.org, iv, cancel=True)
    await db.commit()
    await db.refresh(iv)
    await arm_panel_reminder(db, actor.org, iv, 0)          # status is cancelled → the reminder is withdrawn
    users = await ext.user_ids_for_employees(db, actor.org, [p.get("employee_id") for p in (iv.panel or [])])
    await ext.notify(actor.org, user_ids=users, title=f"Interview cancelled: {iv.round_name}", body=reason,
                     link="/hiring/my-interviews", kind="hiring")
    await record_audit(request, "hiring.interview.cancel", entity_type="hire_interview", entity_id=str(iv.id),
                       summary=f"{iv.round_name} cancelled", meta={"reason": reason, "calendar": cal.get("status")})
    return {**ser(iv), "calendar": cal}


# ── the candidate's pick (called by the public route) ───────────────────────
async def apply_pick(db: AsyncSession, row: HireInterviewSlots, index, message: str | None) -> HireInterview:
    """Guarded: WHERE status='proposed'. Sets the interview time and runs the schedule hook."""
    slot = D.pick_slot(row.slots or [], index)
    await cas(db, HireInterviewSlots, row, "status", "proposed", status="picked", picked_slot=slot, picked_at=now(),
              candidate_message=(message or "").strip()[:400] or None)
    iv = (await db.execute(select(HireInterview).where(HireInterview.id == row.interview_id))).scalar_one()
    a = (await db.execute(select(HireApplication).where(HireApplication.id == row.application_id))).scalar_one()
    iv.scheduled_at = to_datetime(slot["start"])
    await db.flush()
    await after_scheduled(db, None, iv, a)
    await db.commit()
    await db.refresh(iv)
    users = await ext.user_ids_for_employees(db, str(iv.org_id), [p.get("employee_id") for p in (iv.panel or [])])
    await ext.notify(str(iv.org_id), user_ids=users, roles=["hr"], title=f"Candidate picked a slot: {iv.round_name} on {fmt_when(iv.scheduled_at)}",
                     body=(message or ""), link="/hiring/my-interviews", kind="hiring")
    return iv


def new_request_id() -> str:
    return f"lexora-{uuid.uuid4()}"
