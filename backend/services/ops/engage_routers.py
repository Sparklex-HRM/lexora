"""Engagement routes — helpdesk, surveys, recognition, preferences, settings (Stream G4).

Mounted at /engage (gateway prefix `engage` → ops service, not module-gated).

Helpdesk     GET /helpdesk/tickets?scope=mine|team|all&status=   POST /helpdesk/tickets (Ask HR)
             GET /helpdesk/tickets/{id}   POST .../comments   POST .../attachments (multipart)
             GET /helpdesk/files/{doc_id}   PATCH /helpdesk/tickets/{id} {status, assignee_user_id, priority, category}
             GET /helpdesk/report (ageing + SLA breaches)   GET /helpdesk/agents
Surveys      GET /surveys (HR: all · employee: open ones for me)   POST /surveys   PATCH /surveys/{id}
             GET /surveys/templates   GET /surveys/{id}   POST /surveys/{id}/respond   GET /surveys/{id}/results
             GET /surveys-trend?kind=enps
Recognition  GET /kudos (feed)   POST /kudos   POST /kudos/{id}/{decision}   GET /kudos/summary?month=
Prefs        GET /me/prefs   PUT /me/prefs {language, voice}
Settings     GET /settings   PUT /settings (HR)
"""
from __future__ import annotations

import hashlib
import sys
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import local_today, org_today  # noqa: E402

import engage_common as C  # noqa: E402
import engage_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from engage_models import Kudos, Survey, SurveyResponse, Ticket, TicketComment, UserPref  # noqa: E402

router = APIRouter(prefix="/engage", tags=["engagement"])

LANGS = ("en", "fr")


# ══════════════════════════ settings ══════════════════════════════════════════

@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    s = await C.get_settings(db, C.org_of(request))
    await db.commit()
    return {**C.settings_out(s), "can_edit": C.is_hr(request)}


class SettingsIn(BaseModel):
    helpdesk_categories: list[dict] | None = None
    anonymity_threshold: int | None = None
    kudos_values: list[str] | None = None
    kudos_needs_approval: bool | None = None
    # D7
    kudos_points_default: int | None = None
    manager_points_per_month: int | None = None
    lms_remind_days: int | None = None


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change engagement settings")
    s = await C.get_settings(db, C.org_of(request))
    if body.helpdesk_categories is not None:
        cats = []
        for c in body.helpdesk_categories[:30]:
            key = str(c.get("key") or "").strip().lower().replace(" ", "_")[:40]
            label = str(c.get("label") or "").strip()[:80]
            if not key or not label:
                raise HTTPException(422, "Every category needs a key and a label")
            try:
                sla = int(c.get("sla_hours") or 0)
            except (TypeError, ValueError):
                raise HTTPException(422, f"SLA hours for {label} must be a whole number")
            if not 1 <= sla <= 720:
                raise HTTPException(422, f"SLA hours for {label} must be 1 to 720")
            cats.append({"key": key, "label": label, "sla_hours": sla, "assign_role": str(c.get("assign_role") or "hr")[:20]})
        if not cats:
            raise HTTPException(422, "Keep at least one category")
        s.helpdesk_categories = cats
    if body.anonymity_threshold is not None:
        if not 1 <= int(body.anonymity_threshold) <= 100:
            raise HTTPException(422, "The anonymity threshold must be 1 to 100 responses")
        s.anonymity_threshold = int(body.anonymity_threshold)
    if body.kudos_values is not None:
        vals = [v.strip()[:60] for v in body.kudos_values if v and v.strip()][:20]
        if not vals:
            raise HTTPException(422, "Keep at least one value")
        s.kudos_values = vals
    if body.kudos_needs_approval is not None:
        s.kudos_needs_approval = bool(body.kudos_needs_approval)
    for k, lo, hi in (("kudos_points_default", 0, 1000), ("manager_points_per_month", 0, 100000), ("lms_remind_days", 0, 60)):
        v = getattr(body, k)
        if v is not None:
            if not lo <= int(v) <= hi:
                raise HTTPException(422, f"{k.replace('_', ' ')} must be {lo} to {hi}")
            setattr(s, k, int(v))
    s.updated_by = C.actor_of(request)
    await db.commit()
    return {**C.settings_out(s), "saved": True}


# ══════════════════════════ helpdesk ══════════════════════════════════════════

def _ticket_out(t: Ticket, now: datetime, comments: int | None = None) -> dict:
    return {"id": str(t.id), "number": t.number, "employee_id": str(t.employee_id) if t.employee_id else None,
            "employee_name": t.employee_name, "department": t.department, "category": t.category, "subject": t.subject,
            "body": t.body, "priority": t.priority, "status": t.status, "assignee_user_id": t.assignee_user_id,
            "assignee_name": t.assignee_name, "sla_hours": t.sla_hours, "due_at": C.iso(t.due_at),
            "sla_state": L.sla_state(now, t.due_at, t.status, t.resolved_at),
            "age_hours": L.age_hours(now, t.created_at, t.resolved_at),
            "first_response_at": C.iso(t.first_response_at), "resolved_at": C.iso(t.resolved_at),
            "closed_at": C.iso(t.closed_at), "confidential": bool(t.confidential), "source": t.source,
            "doc_ids": list(t.doc_ids or []), "created_at": C.iso(t.created_at), "updated_at": C.iso(t.updated_at),
            "comments": comments}


def _comment_out(c: TicketComment) -> dict:
    return {"id": str(c.id), "author_name": c.author_name, "author_role": c.author_role, "body": c.body,
            "internal": bool(c.internal), "kind": c.kind, "doc_ids": list(c.doc_ids or []), "at": C.iso(c.at)}


async def _ticket(db, org, tid) -> Ticket:
    t = (await db.execute(select(Ticket).where(Ticket.id == C.to_uuid(tid, "ticket"), Ticket.org_id == org))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t


async def _can_see(request: Request, t: Ticket) -> bool:
    if C.is_agent(request):
        return not (t.confidential and not C.is_hr(request))
    me = C.emp_of(request)
    if me and t.employee_id and str(t.employee_id) == me:
        return True
    if C.uid_of(request) and t.requester_user_id == C.uid_of(request):
        return True
    if C.is_manager(request) and me and t.employee_id and not t.confidential:
        e = (await C.employees(request)).get(str(t.employee_id))
        return bool(e and str(e.get("manager_id")) == me)
    return False


@router.get("/helpdesk/tickets")
async def list_tickets(request: Request, scope: str = "mine", status: str | None = None, category: str | None = None,
                       q: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    now = C.now()
    qry = select(Ticket).where(Ticket.org_id == org)
    if scope == "all" and C.is_agent(request):
        if not C.is_hr(request):
            qry = qry.where(Ticket.confidential.is_(False))
    elif scope == "team" and C.is_manager(request) and C.emp_of(request):
        team = [uuid.UUID(k) for k, e in (await C.employees(request)).items() if str(e.get("manager_id")) == C.emp_of(request)]
        qry = qry.where(Ticket.employee_id.in_(team), Ticket.confidential.is_(False))
    elif scope == "assigned" and C.is_agent(request):
        qry = qry.where(Ticket.assignee_user_id == C.uid_of(request))
    else:
        me = C.emp_of(request)
        uid = C.uid_of(request)
        if me:
            qry = qry.where((Ticket.employee_id == me) | (Ticket.requester_user_id == uid))
        elif uid:
            qry = qry.where(Ticket.requester_user_id == uid)
        else:
            return {"tickets": [], "counts": {}}
    if status:
        qry = qry.where(Ticket.status == status)
    if category:
        qry = qry.where(Ticket.category == category)
    rows = (await db.execute(qry.order_by(Ticket.created_at.desc()).limit(1000))).scalars().all()
    if q:
        needle = q.lower()
        rows = [t for t in rows if needle in (t.subject or "").lower() or needle in (t.number or "").lower()
                or needle in (t.employee_name or "").lower()]
    out = [_ticket_out(t, now) for t in rows]
    counts: dict[str, int] = {}
    for t in out:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    counts["breached"] = sum(1 for t in out if t["sla_state"] == "breached" and t["status"] not in ("resolved", "closed"))
    return {"tickets": out, "counts": counts}


class TicketIn(BaseModel):
    category: str
    subject: str
    body: str | None = None
    priority: str = "normal"
    source: str = "web"
    employee_id: str | None = None     # HR raising on behalf of someone


@router.post("/helpdesk/tickets", status_code=201)
async def create_ticket(body: TicketIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    if not body.subject.strip():
        raise HTTPException(422, "Write a short subject")
    if body.priority not in L.PRIORITIES:
        raise HTTPException(422, "Priority must be low, normal, high or urgent")
    s = await C.get_settings(db, org)
    cats = C.settings_out(s)["helpdesk_categories"]
    cat = L.category_for(cats, body.category)
    if cat["key"] != body.category:
        raise HTTPException(422, "Pick a category from the list")
    emps = await C.employees(request)
    emp_id = body.employee_id if (body.employee_id and C.is_hr(request)) else C.emp_of(request)
    e = emps.get(emp_id or "")
    s.ticket_seq = (s.ticket_seq or 0) + 1
    now = C.now()
    t = Ticket(org_id=uuid.UUID(org), number=L.next_number("HD", s.ticket_seq), employee_id=uuid.UUID(emp_id) if emp_id else None,
               employee_name=C.emp_name(e) or C.actor_of(request), department=(e or {}).get("department"),
               requester_user_id=C.uid_of(request), category=cat["key"], subject=body.subject.strip()[:200],
               body=(body.body or "").strip()[:8000] or None, priority=body.priority, status="open",
               sla_hours=int(cat["sla_hours"]), due_at=L.sla_due(now, cat["sla_hours"]),
               confidential=cat["key"] == "grievance", source=body.source if body.source in ("web", "mobile") else "web",
               created_at=now)
    db.add(t)
    await db.commit()
    role = cat.get("assign_role") or "hr"
    # HR owns the helpdesk and sees every ticket, so HR is always told; the category's assign
    # role (payroll, org_admin …) is told as well. Before, a payroll ticket reached payroll only.
    roles = ["hr", "org_admin"] if role == "hr" else [role, "hr"]
    await notify(org, roles=roles, kind="general", title=f"New HR ticket {t.number}: {t.subject}",
                 body=f"{cat['label']} · from {t.employee_name} · answer within {cat['sla_hours']} h", link=f"/engage/helpdesk?id={t.id}")
    return _ticket_out(t, now, 0)


@router.get("/helpdesk/agents")
async def agents(request: Request):
    """People a ticket can be assigned to (HR, payroll, admin logins)."""
    if not C.is_agent(request):
        raise HTTPException(403, "Only HR can assign tickets")
    users = await directory.org_users(C.org_of(request))
    return [{"user_id": u["id"], "name": directory.display_name(u), "role": u["role"]} for u in users
            if directory.role_in(u.get("role"), ["hr", "payroll", "org_admin"])]


@router.get("/helpdesk/tickets/{tid}")
async def ticket_detail(tid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    t = await _ticket(db, org, tid)
    if not await _can_see(request, t):
        raise HTTPException(403, "This ticket is not yours to see")
    cs = (await db.execute(select(TicketComment).where(TicketComment.ticket_id == t.id).order_by(TicketComment.at))).scalars().all()
    agent = C.is_agent(request)
    return {"ticket": _ticket_out(t, C.now(), len(cs)), "comments": [_comment_out(c) for c in cs if agent or not c.internal],
            "can_work": agent, "states": list(L.TICKET_STATES)}


class CommentIn(BaseModel):
    body: str
    internal: bool = False


@router.post("/helpdesk/tickets/{tid}/comments", status_code=201)
async def add_comment(tid: str, body: CommentIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    t = await _ticket(db, org, tid)
    if not await _can_see(request, t):
        raise HTTPException(403, "This ticket is not yours")
    if not body.body.strip():
        raise HTTPException(422, "Write something")
    if t.status == "closed":
        raise HTTPException(409, "This ticket is closed. Raise a new one.")
    agent = C.is_agent(request)
    c = TicketComment(org_id=uuid.UUID(org), ticket_id=t.id, author_user_id=C.uid_of(request), author_name=C.actor_of(request),
                      author_role=C.role_of(request), body=body.body.strip()[:8000], internal=bool(body.internal and agent))
    db.add(c)
    if agent and not c.internal and t.first_response_at is None:
        t.first_response_at = C.now()
    if agent and t.status == "open" and not c.internal:
        t.status = "in_progress"
    if not agent and t.status == "waiting":
        t.status = "in_progress"
    await db.commit()
    if agent and not c.internal and t.requester_user_id:
        await notify(org, user_ids=[t.requester_user_id], kind="general", title=f"HR replied on {t.number}: {t.subject}",
                     body=c.body[:300], link=f"/engage/helpdesk?id={t.id}", email=False)
    elif not agent and t.assignee_user_id:
        await notify(org, user_ids=[t.assignee_user_id], kind="general", title=f"Reply on {t.number}: {t.subject}",
                     body=c.body[:300], link=f"/engage/helpdesk?id={t.id}", email=False)
    return _comment_out(c)


@router.post("/helpdesk/tickets/{tid}/attachments", status_code=201)
async def attach(tid: str, request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    t = await _ticket(db, org, tid)
    if not await _can_see(request, t):
        raise HTTPException(403, "This ticket is not yours")
    data = await file.read()
    d = await C.store_doc(db, org, str(t.employee_id) if t.employee_id else None, file.filename or "file",
                          data, "helpdesk", C.actor_of(request))
    t.doc_ids = list(t.doc_ids or []) + [str(d.id)]
    db.add(TicketComment(org_id=uuid.UUID(org), ticket_id=t.id, author_user_id=C.uid_of(request), author_name=C.actor_of(request),
                         author_role=C.role_of(request), body=f"Attached {d.file_name}", kind="comment", doc_ids=[str(d.id)]))
    await db.commit()
    return {"doc_id": str(d.id), "file_name": d.file_name, "size_bytes": d.size_bytes}


@router.get("/helpdesk/files/{doc_id}")
async def get_file(doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    d = await C.load_doc(db, org, doc_id)
    t = (await db.execute(select(Ticket).where(Ticket.org_id == org, Ticket.doc_ids.contains([str(d.id)])))).scalars().first()
    if not t or not await _can_see(request, t):
        raise HTTPException(403, "Not allowed")
    data = await db.run_sync(lambda s: d.data)
    return Response(content=data, media_type=d.content_type,
                    headers={"Content-Disposition": f'inline; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; sandbox"})


class TicketPatch(BaseModel):
    status: str | None = None
    assignee_user_id: str | None = None
    priority: str | None = None
    category: str | None = None
    note: str | None = None


@router.patch("/helpdesk/tickets/{tid}")
async def update_ticket(tid: str, body: TicketPatch, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    t = await _ticket(db, org, tid)
    agent = C.is_agent(request)
    own = (C.emp_of(request) and t.employee_id and str(t.employee_id) == C.emp_of(request)) or t.requester_user_id == C.uid_of(request)
    if not (agent or own):
        raise HTTPException(403, "This ticket is not yours")
    now = C.now()
    notes = []
    if body.status is not None:
        if body.status not in L.TICKET_STATES:
            raise HTTPException(422, "Unknown status")
        if not agent and body.status != "closed":
            raise HTTPException(403, "You can only close your own ticket")
        if body.status != t.status:
            notes.append(f"Status: {t.status} → {body.status}")
            t.status = body.status
            if body.status == "resolved" and t.resolved_at is None:
                t.resolved_at = now
            if body.status == "closed":
                t.closed_at = now
                t.resolved_at = t.resolved_at or now
            if body.status in ("open", "in_progress", "waiting"):
                t.resolved_at, t.closed_at = None, None
    if agent and body.assignee_user_id is not None:
        users = {u["id"]: u for u in await directory.org_users(org)}
        u = users.get(body.assignee_user_id)
        if body.assignee_user_id and not u:
            raise HTTPException(404, "That person has no login here")
        t.assignee_user_id = body.assignee_user_id or None
        t.assignee_name = directory.display_name(u) if u else None
        notes.append(f"Assigned to {t.assignee_name or 'nobody'}")
    if agent and body.priority:
        if body.priority not in L.PRIORITIES:
            raise HTTPException(422, "Priority must be low, normal, high or urgent")
        t.priority = body.priority
    if agent and body.category:
        s = await C.get_settings(db, org)
        cat = L.category_for(C.settings_out(s)["helpdesk_categories"], body.category)
        t.category = cat["key"]
        t.sla_hours = int(cat["sla_hours"])
        t.due_at = L.sla_due(t.created_at, cat["sla_hours"])
        notes.append(f"Category: {cat['label']}")
    if notes or body.note:
        db.add(TicketComment(org_id=uuid.UUID(org), ticket_id=t.id, author_user_id=C.uid_of(request), author_name=C.actor_of(request),
                             author_role=C.role_of(request), body="; ".join(notes + ([body.note] if body.note else [])), kind="status"))
    await db.commit()
    if "Assigned" in " ".join(notes) and t.assignee_user_id:
        await notify(org, user_ids=[t.assignee_user_id], kind="general", title=f"Ticket {t.number} assigned to you: {t.subject}",
                     link=f"/engage/helpdesk?id={t.id}", email=False)
    if body.status in ("resolved", "closed") and t.requester_user_id and agent:
        await notify(org, user_ids=[t.requester_user_id], kind="general", title=f"Ticket {t.number} is {body.status}: {t.subject}",
                     body=body.note or "", link=f"/engage/helpdesk?id={t.id}", email=False)
    return _ticket_out(t, now)


@router.get("/helpdesk/report")
async def helpdesk_report(request: Request, days: int = 90, db: AsyncSession = Depends(get_db)):
    if not C.is_agent(request):
        raise HTTPException(403, "Only HR can see the helpdesk report")
    org = C.org_of(request)
    now = C.now()
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    since = since - timedelta(days=max(1, min(days, 365)))
    rows = (await db.execute(select(Ticket).where(Ticket.org_id == org, Ticket.created_at >= since))).scalars().all()
    if not C.is_hr(request):
        rows = [t for t in rows if not t.confidential]
    rep = L.ageing_report([{"status": t.status, "created_at": t.created_at, "due_at": t.due_at, "resolved_at": t.resolved_at,
                            "category": t.category} for t in rows], now)
    breaches = [_ticket_out(t, now) for t in rows if L.sla_state(now, t.due_at, t.status, t.resolved_at) == "breached"]
    breaches.sort(key=lambda r: r["due_at"] or "")
    return {"since": since.date().isoformat(), "days": days, **rep, "breaches": breaches[:300]}


# ══════════════════════════ surveys ═══════════════════════════════════════════

def _survey_out(s: Survey, responses: int | None = None, today: date | None = None, answered: bool | None = None) -> dict:
    today = today or local_today()
    return {"id": str(s.id), "title": s.title, "kind": s.kind, "questions": list(s.questions or []), "anonymous": bool(s.anonymous),
            "status": s.status, "opens_on": C.iso(s.opens_on), "closes_on": C.iso(s.closes_on), "target": s.target or {},
            "threshold": s.threshold, "exit_case_id": s.exit_case_id, "created_by": s.created_by, "created_at": C.iso(s.created_at),
            "closed_at": C.iso(s.closed_at), "responses": responses, "is_open": L.survey_open(s.status, s.opens_on, s.closes_on, today),
            "answered": answered}


def _hash(survey_id, employee_or_user: str) -> str:
    return hashlib.sha256(f"{survey_id}:{employee_or_user}".encode()).hexdigest()


async def _resp_count(db, sid) -> int:
    return len((await db.execute(select(SurveyResponse.id).where(SurveyResponse.survey_id == sid))).all())


@router.get("/surveys/templates")
async def survey_templates():
    return [{"key": k, **v} for k, v in L.SURVEY_TEMPLATES.items()]


@router.get("/surveys")
async def list_surveys(request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    rows = (await db.execute(select(Survey).where(Survey.org_id == org).order_by(Survey.created_at.desc()))).scalars().all()
    today = await org_today(org)
    me_key = C.emp_of(request) or C.uid_of(request) or ""
    if C.is_hr(request):
        out = []
        for s in rows:
            out.append(_survey_out(s, await _resp_count(db, s.id), today))
        return {"surveys": out, "can_manage": True}
    emps = await C.employees(request)
    me = emps.get(C.emp_of(request) or "", {"id": C.emp_of(request)})
    mine = []
    for s in rows:
        if s.status == "draft" or not L.in_target(s.target, me):
            continue
        done = (await db.execute(select(SurveyResponse.id).where(SurveyResponse.survey_id == s.id,
                                                                SurveyResponse.respondent_hash == _hash(s.id, me_key)))).first() is not None
        if s.status == "open" or done:
            mine.append(_survey_out(s, None, today, done))
    return {"surveys": mine, "can_manage": False}


class SurveyIn(BaseModel):
    title: str
    kind: str = "pulse"
    questions: list[dict] | None = None
    template: str | None = None
    anonymous: bool = True
    opens_on: str | None = None
    closes_on: str | None = None
    target: dict | None = None
    threshold: int | None = None
    status: str = "draft"


def _clean_questions(qs: list[dict]) -> list[dict]:
    out = []
    for i, q in enumerate(qs or []):
        text = str(q.get("text") or "").strip()
        if not text:
            continue
        typ = q.get("type") if q.get("type") in ("scale", "nps", "text", "choice") else "scale"
        row = {"key": str(q.get("key") or f"q{i + 1}")[:40], "type": typ, "text": text[:400]}
        if typ == "choice":
            row["options"] = [str(o).strip()[:80] for o in (q.get("options") or []) if str(o).strip()][:12]
            if not row["options"]:
                raise HTTPException(422, f"Question '{text[:40]}' needs at least one option")
        out.append(row)
    if not out:
        raise HTTPException(422, "Add at least one question")
    return out


@router.post("/surveys", status_code=201)
async def create_survey(body: SurveyIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "create a survey")
    org = C.org_of(request)
    st = await C.get_settings(db, org)
    tpl = L.SURVEY_TEMPLATES.get(body.template or "")
    questions = _clean_questions(body.questions or (tpl["questions"] if tpl else []))
    kind = body.kind if body.kind in ("pulse", "enps", "exit", "custom") else (tpl["kind"] if tpl else "pulse")
    s = Survey(org_id=uuid.UUID(org), title=body.title.strip()[:200] or (tpl["title"] if tpl else "Survey"), kind=kind,
               questions=questions, anonymous=bool(body.anonymous), status="open" if body.status == "open" else "draft",
               opens_on=date.fromisoformat(body.opens_on) if body.opens_on else None,
               closes_on=date.fromisoformat(body.closes_on) if body.closes_on else None, target=body.target or {},
               threshold=int(body.threshold or st.anonymity_threshold or 5), created_by=C.actor_of(request))
    db.add(s)
    await db.commit()
    if s.status == "open":
        await _announce(request, org, s)
    return _survey_out(s, 0)


async def _announce(request: Request, org: str, s: Survey):
    emps = await C.employees(request)
    ids = [e["id"] for e in emps.values() if e.get("status") != "terminated" and L.in_target(s.target, e)]
    if ids:
        await notify(org, employee_ids=ids[:2000], kind="general", title=f"Survey: {s.title}",
                     body="Anonymous — nobody sees who said what." if s.anonymous else "Your name is recorded with your answers.",
                     link=f"/engage/surveys?id={s.id}", email=False)


@router.patch("/surveys/{sid}")
async def update_survey(sid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change a survey")
    org = C.org_of(request)
    s = (await db.execute(select(Survey).where(Survey.id == C.to_uuid(sid, "survey"), Survey.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Survey not found")
    was = s.status
    if "status" in body:
        if body["status"] not in ("draft", "open", "closed"):
            raise HTTPException(422, "Status must be draft, open or closed")
        if s.status == "closed" and body["status"] != "closed":
            raise HTTPException(409, "A closed survey stays closed. Make a new one.")
        s.status = body["status"]
        if s.status == "closed":
            s.closed_at = C.now()
    if s.status == "draft" or was == "draft":
        if "title" in body:
            s.title = str(body["title"]).strip()[:200]
        if "questions" in body:
            s.questions = _clean_questions(body["questions"])
        if "anonymous" in body:
            s.anonymous = bool(body["anonymous"])
        if "target" in body:
            s.target = body["target"] or {}
        if "threshold" in body:
            s.threshold = max(1, int(body["threshold"] or 1))
    for k in ("opens_on", "closes_on"):
        if k in body:
            setattr(s, k, date.fromisoformat(body[k]) if body[k] else None)
    await db.commit()
    if was == "draft" and s.status == "open":
        await _announce(request, org, s)
    return _survey_out(s, await _resp_count(db, s.id))


@router.get("/surveys/{sid}")
async def survey_detail(sid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    s = (await db.execute(select(Survey).where(Survey.id == C.to_uuid(sid, "survey"), Survey.org_id == org))).scalar_one_or_none()
    if not s or (s.status == "draft" and not C.is_hr(request)):
        raise HTTPException(404, "Survey not found")
    me_key = C.emp_of(request) or C.uid_of(request) or ""
    done = (await db.execute(select(SurveyResponse).where(SurveyResponse.survey_id == s.id,
                                                         SurveyResponse.respondent_hash == _hash(s.id, me_key)))).scalar_one_or_none()
    return {**_survey_out(s, await _resp_count(db, s.id), None, done is not None),
            "my_answers": (done.answers if (done and not s.anonymous) else None)}


class RespondIn(BaseModel):
    answers: dict


@router.post("/surveys/{sid}/respond", status_code=201)
async def respond(sid: str, body: RespondIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    s = (await db.execute(select(Survey).where(Survey.id == C.to_uuid(sid, "survey"), Survey.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Survey not found")
    if not L.survey_open(s.status, s.opens_on, s.closes_on, await org_today(org)):
        raise HTTPException(409, "This survey is not open")
    me = C.emp_of(request)
    emps = await C.employees(request)
    if not L.in_target(s.target, emps.get(me or "", {"id": me})):
        raise HTTPException(403, "This survey was not sent to you")
    me_key = me or C.uid_of(request) or ""
    if not me_key:
        raise HTTPException(403, "Sign in as an employee to answer")
    h = _hash(s.id, me_key)
    if (await db.execute(select(SurveyResponse.id).where(SurveyResponse.survey_id == s.id, SurveyResponse.respondent_hash == h))).first():
        raise HTTPException(409, "You already answered this survey")
    answers = {}
    for q in s.questions or []:
        a = body.answers.get(q["key"])
        if a is None or a == "":
            continue
        if q["type"] in ("scale", "nps"):
            try:
                v = int(a)
            except (TypeError, ValueError):
                raise HTTPException(422, f"'{q['text'][:40]}' needs a number")
            hi = 10 if q["type"] == "nps" else 5
            lo = 0 if q["type"] == "nps" else 1
            if not lo <= v <= hi:
                raise HTTPException(422, f"'{q['text'][:40]}' must be {lo} to {hi}")
            answers[q["key"]] = v
        elif q["type"] == "choice":
            if str(a) not in (q.get("options") or []):
                raise HTTPException(422, f"Pick one of the options for '{q['text'][:40]}'")
            answers[q["key"]] = str(a)
        else:
            answers[q["key"]] = str(a)[:2000]
    if not answers:
        raise HTTPException(422, "Answer at least one question")
    me_row = emps.get(me or "") or {}
    mgr = me_row.get("manager_id")
    db.add(SurveyResponse(org_id=uuid.UUID(org), survey_id=s.id, employee_id=None if s.anonymous else (uuid.UUID(me) if me else None),
                          respondent_hash=h, answers=answers,
                          # D7: coarse segment for heatmaps / manager insights (never the person; threshold applies)
                          department=(me_row.get("department") or None), manager_id=uuid.UUID(str(mgr)) if mgr else None))
    await db.commit()
    return {"ok": True, "anonymous": bool(s.anonymous)}


@router.get("/surveys/{sid}/results")
async def results(sid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "see survey results")
    org = C.org_of(request)
    s = (await db.execute(select(Survey).where(Survey.id == C.to_uuid(sid, "survey"), Survey.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Survey not found")
    rows = (await db.execute(select(SurveyResponse).where(SurveyResponse.survey_id == s.id))).scalars().all()
    res = L.survey_results(s.questions or [], [{"answers": r.answers} for r in rows], s.threshold, bool(s.anonymous))
    named = None
    if not s.anonymous and res["visible"]:
        emps = await C.employees(request)
        named = [{"employee_id": str(r.employee_id) if r.employee_id else None,
                  "name": C.emp_name(emps.get(str(r.employee_id))) if r.employee_id else "—",
                  "answers": r.answers, "at": C.iso(r.submitted_at)} for r in rows]
    return {"survey": _survey_out(s, len(rows)), "results": res, "named": named}


@router.get("/surveys-trend")
async def trend(request: Request, kind: str = "enps", db: AsyncSession = Depends(get_db)):
    """eNPS / pulse score per closed-or-open survey of the kind, oldest first (threshold respected)."""
    C.require_hr(request, "see survey trends")
    org = C.org_of(request)
    ss = (await db.execute(select(Survey).where(Survey.org_id == org, Survey.kind == kind, Survey.status != "draft")
                           .order_by(Survey.created_at))).scalars().all()
    points = []
    for s in ss:
        rows = (await db.execute(select(SurveyResponse).where(SurveyResponse.survey_id == s.id))).scalars().all()
        res = L.survey_results(s.questions or [], [{"answers": r.answers} for r in rows], s.threshold, bool(s.anonymous))
        score = None
        if res["visible"]:
            for q in res["questions"]:
                if q["type"] == "nps":
                    score = q.get("enps")
                    break
                if q["type"] == "scale" and score is None:
                    score = q.get("average")
        points.append({"survey_id": str(s.id), "title": s.title, "date": C.iso(s.created_at)[:10], "responses": len(rows),
                       "visible": res["visible"], "score": score})
    return {"kind": kind, "points": points}


# ══════════════════════════ recognition ═══════════════════════════════════════

def _kudos_out(k: Kudos) -> dict:
    return {"id": str(k.id), "from_employee_id": str(k.from_employee_id) if k.from_employee_id else None, "from_name": k.from_name,
            "to_employee_id": str(k.to_employee_id), "to_name": k.to_name, "value_tag": k.value_tag, "message": k.message,
            "status": k.status, "decided_by": k.decided_by, "created_at": C.iso(k.created_at), "points": k.points or 0}


@router.get("/kudos")
async def kudos_feed(request: Request, status: str | None = None, limit: int = 100, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    q = select(Kudos).where(Kudos.org_id == org)
    if status and C.is_hr(request):
        q = q.where(Kudos.status == status)
    elif not C.is_hr(request):
        me = C.emp_of(request)
        q = q.where((Kudos.status == "approved") | (Kudos.from_employee_id == me) if me else (Kudos.status == "approved"))
    rows = (await db.execute(q.order_by(Kudos.created_at.desc()).limit(max(1, min(limit, 500))))).scalars().all()
    s = await C.get_settings(db, org)
    await db.commit()
    return {"kudos": [_kudos_out(k) for k in rows], "values": C.settings_out(s)["kudos_values"],
            "needs_approval": bool(s.kudos_needs_approval), "can_moderate": C.is_hr(request)}


class KudosIn(BaseModel):
    to_employee_id: str
    value_tag: str | None = None
    message: str
    points: int | None = None          # D7: managers spend from a monthly budget (default = org points per kudos)


@router.get("/people")
async def people(request: Request):
    """Names only (no pay, no ids beyond the employee number) of every active colleague — what the
    kudos picker needs. GET /employees is scoped to the caller, so an employee saw nobody there."""
    org = C.org_of(request)
    rows = await directory.org_employees(org)
    return [{"id": e["id"], "first_name": e["first_name"], "last_name": e["last_name"], "department": e["department"],
             "employee_number": e["employee_number"], "manager_id": e["manager_id"]} for e in rows]


@router.post("/kudos", status_code=201)
async def give_kudos(body: KudosIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    # the org directory, not the caller-scoped employee list: an employee gives kudos to colleagues
    emps = {e["id"]: e for e in await directory.org_employees(org)}
    to = emps.get(body.to_employee_id)
    if not to:
        raise HTTPException(404, "Pick a colleague from the list")
    me = C.emp_of(request)
    if me and me == body.to_employee_id:
        raise HTTPException(422, "You cannot give kudos to yourself")
    if not body.message.strip():
        raise HTTPException(422, "Say what they did well")
    s = await C.get_settings(db, org)
    vals = C.settings_out(s)["kudos_values"]
    tag = (body.value_tag or "").strip()
    if tag and tag not in vals:
        raise HTTPException(422, "Pick a value from the list")
    # D7: points — managers (and HR) draw from a monthly budget; others give kudos without points
    points = 0
    if me and (C.is_manager(request) or C.is_hr(request)):
        import engage2_logic as E2
        from engage2_routers import get_budget
        points = int(body.points if body.points is not None else (s.kudos_points_default or 0))
        if points < 0:
            raise HTTPException(422, "Points cannot be negative")
        if points:
            month = (await org_today(org)).strftime("%Y-%m")
            b = await get_budget(db, org, me, month, s.manager_points_per_month, C.emp_name(emps.get(me)))
            if not E2.can_spend(b.points_budget, b.points_used, points):
                raise HTTPException(409, f"Only {E2.budget_remaining(b.points_budget, b.points_used)} recognition points left this month")
            b.points_used = (b.points_used or 0) + points
    k = Kudos(org_id=uuid.UUID(org), from_employee_id=uuid.UUID(me) if me else None,
              from_name=C.emp_name(emps.get(me or "")) or C.actor_of(request), to_employee_id=uuid.UUID(body.to_employee_id),
              to_name=C.emp_name(to), value_tag=tag or None, message=body.message.strip()[:600],
              status="pending" if s.kudos_needs_approval else "approved", points=points)
    db.add(k)
    await db.commit()
    if k.status == "approved":
        await notify(org, employee_ids=[body.to_employee_id], kind="general", title=f"{k.from_name} gave you kudos",
                     body=k.message, link="/engage/kudos", email=False)
    else:
        await notify(org, roles=["hr", "org_admin"], kind="approval", title=f"Kudos waiting for approval: {k.from_name} → {k.to_name}",
                     body=k.message, link="/engage/kudos?tab=pending", email=False)
    return _kudos_out(k)


@router.post("/kudos/{kid}/{decision}")
async def moderate_kudos(kid: str, decision: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "approve kudos")
    org = C.org_of(request)
    k = (await db.execute(select(Kudos).where(Kudos.id == C.to_uuid(kid, "kudos"), Kudos.org_id == org))).scalar_one_or_none()
    if not k:
        raise HTTPException(404, "Kudos not found")
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    k.status = "approved" if decision == "approve" else "rejected"
    k.decided_by = C.actor_of(request)
    await db.commit()
    if k.status == "approved":
        await notify(org, employee_ids=[str(k.to_employee_id)], kind="general", title=f"{k.from_name} gave you kudos",
                     body=k.message, link="/engage/kudos", email=False)
    return _kudos_out(k)


@router.get("/kudos/summary")
async def kudos_summary(request: Request, month: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    month = month or (await org_today(org)).strftime("%Y-%m")
    rows = (await db.execute(select(Kudos).where(Kudos.org_id == org, Kudos.status == "approved"))).scalars().all()
    return L.monthly_summary([{"created_at": k.created_at, "to_employee_id": k.to_employee_id, "to_name": k.to_name,
                               "from_name": k.from_name, "value_tag": k.value_tag, "status": k.status} for k in rows], month)


# ══════════════════════════ preferences ═══════════════════════════════════════

@router.get("/me/prefs")
async def my_prefs(request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = C.org_of(request), C.uid_of(request)
    if not uid:
        return {"language": "en", "voice": False, "languages": list(LANGS)}
    p = (await db.execute(select(UserPref).where(UserPref.org_id == org, UserPref.user_id == uid))).scalar_one_or_none()
    return {"language": p.language if p else "en", "voice": bool(p.voice) if p else False, "languages": list(LANGS),
            "extra": (p.extra if p else None) or {}}


class PrefsIn(BaseModel):
    language: str | None = None
    voice: bool | None = None
    extra: dict | None = None


@router.put("/me/prefs")
async def save_prefs(body: PrefsIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = C.org_of(request), C.uid_of(request)
    if not uid:
        raise HTTPException(401, "Sign in first")
    if body.language is not None and body.language not in LANGS:
        raise HTTPException(422, "Language must be one of: " + ", ".join(LANGS))
    p = (await db.execute(select(UserPref).where(UserPref.org_id == org, UserPref.user_id == uid))).scalar_one_or_none()
    if p is None:
        p = UserPref(org_id=uuid.UUID(org), user_id=uid)
        db.add(p)
    if body.language is not None:
        p.language = body.language
    if body.voice is not None:
        p.voice = bool(body.voice)
    if body.extra is not None:
        p.extra = {k: v for k, v in body.extra.items() if isinstance(k, str)}
    await db.commit()
    return {"language": p.language, "voice": bool(p.voice), "saved": True}
