"""Onboarding service — session lifecycle, the joiner's public page, approval.

Two audiences:
  • HR / admin  → /onboarding/sessions/*   (org-authenticated via X-Org-Id, HR roles only)
  • The new hire → /onboarding/invite/{token}/*  (public; the org comes from the
    secret token, so these paths are whitelisted in the gateway TenantMiddleware)

Life-cycle:  invited → submitted → activated      (or cancelled)
    * the joiner fills the form and SUBMITS once; after that the link is read-only
    * HR reviews; "Send back" re-opens the link, "Approve" creates the employee
      (active) and — setting, default ON — a login plus the set-password e-mail
"""
import asyncio
import os
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import docstore, id_validators, identity  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.docstore import StoredDocument  # noqa: E402
from shared.utils.email import brand_html, send_email  # noqa: E402
from shared.utils.ratelimit import RateLimiter, client_ip, make_limiter  # noqa: E402  (Redis-shared when REDIS_URL is set)
from shared.utils.org_time import local_today  # noqa: E402

import intake  # noqa: E402
from database import get_db  # noqa: E402
from models import (OnboardingConsent, OnboardingSession, OnboardingTask,  # noqa: E402
                    OnboardingTaskTemplate)
from schemas import (ConsentIn, ReviewIn, SessionCreate, StepPatch, TaskIn,  # noqa: E402
                     TemplateIn)
from consents import NOTICE_VERSION, consent_types_for, privacy_purposes_in  # noqa: E402

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}

# The joiner's steps on the public page, in order.
STEPS = ["personal", "statutory", "bank", "emergency", "documents", "consents"]

# Public page limits (per address): 120 requests / 5 min, 30 uploads / hour.
page_limiter = make_limiter(120, 300, name="onboarding_page")
upload_limiter = make_limiter(30, 3600, name="onboarding_upload")


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    return identity.aware(dt)


def _org(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _hr(request: Request) -> uuid.UUID:
    """Org id, after checking the caller is HR / admin."""
    org = _org(request)
    if (request.headers.get("x-role") or "").lower() not in HR_ROLES:
        raise HTTPException(403, "Only HR or an admin can manage onboarding")
    return org


def _uid(request: Request):
    return request.headers.get("x-user-id")


def _id(value, what="Record") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(404, f"{what} not found")


def _country(s: OnboardingSession) -> str:
    return (s.country or (s.data or {}).get("address_country") or "").upper()


def _invite_link(token: str) -> str:
    return f"{os.getenv('APP_BASE_URL', 'http://localhost:5173').rstrip('/')}/onboarding/invite/{token}"


def _link_fields(s: OnboardingSession) -> dict:
    """The joiner's personal link is NEVER shown to HR (decision 2026-10-21): it carries the
    joiner's own consent and bank / ID entry, so only the joiner's e-mail gets it — HR presses
    "New link" to send it again. A local test machine (LEXORA_DEV_SHOW_LINKS=1, forced off in
    production) gets it back so a tester can finish the flow without a mail server."""
    if not identity.show_links():
        return {}
    return {"invite_token": s.invite_token, "invite_link": _invite_link(s.invite_token)}


def _session_out(s: OnboardingSession) -> dict:
    done = [x for x in (s.steps_completed or []) if x in STEPS]
    iso = lambda d: _aware(d).isoformat() if d else None  # noqa: E731
    return {
        "id": str(s.id),
        "status": s.status,
        "country": _country(s) or None,
        "employee_number": s.employee_number,
        "first_name": s.first_name, "last_name": s.last_name, "email": s.email,
        "job_title": s.job_title,
        "current_step": s.current_step,
        "steps": STEPS,
        "steps_completed": done,
        "steps_total": len(STEPS),
        "progress_pct": 100 if s.status in ("submitted", "activated") else round(100 * len(done) / len(STEPS)),
        "employee_id": str(s.employee_id) if s.employee_id else None,
        "review_note": s.review_note,
        "created_at": iso(s.created_at), "invited_at": iso(s.invited_at),
        "invite_expires_at": iso(s.invite_expires_at), "submitted_at": iso(s.submitted_at),
        "activated_at": iso(s.activated_at),
    }


async def _send_invite(s: OnboardingSession, org_name: str | None, days: int) -> bool:
    if not s.email:
        return False
    link = _invite_link(s.invite_token)
    name = (s.first_name or "there").strip()
    company = org_name or "your new employer"
    lines = [f"Hi {name},", f"Welcome to {company}. Please fill in your joining details.",
             f"The link is personal to you and works for {days} days."]
    return await send_email(s.email, f"Your joining form — {company}",
                            "\n\n".join(lines + [link]),
                            html=brand_html("Your joining form", lines, "Open my joining form", link))


# ───────────────────────── checklist ──────────────────────────────────────────

async def _template(db: AsyncSession, org: uuid.UUID) -> list[OnboardingTaskTemplate]:
    """The org's checklist template; seeded with a starter list the first time."""
    rows = (await db.execute(select(OnboardingTaskTemplate).where(
        OnboardingTaskTemplate.org_id == org).order_by(OnboardingTaskTemplate.sort))).scalars().all()
    if rows:
        return [r for r in rows if r.is_active]
    seeded = [OnboardingTaskTemplate(org_id=org, title=t, owner=o, due_offset_days=d, sort=i)
              for i, (t, o, d) in enumerate(intake.DEFAULT_CHECKLIST)]
    db.add_all(seeded)
    await db.flush()
    return seeded


def _due(join: str | None, offset: int) -> str | None:
    try:
        return (date.fromisoformat(str(join)[:10]) + timedelta(days=offset)).isoformat()
    except (ValueError, TypeError):
        return None


def _task_out(t: OnboardingTask) -> dict:
    return {"id": str(t.id), "title": t.title, "owner": t.owner, "due_date": t.due_date,
            "done": bool(t.done), "done_at": _aware(t.done_at).isoformat() if t.done_at else None,
            "done_by": t.done_by, "note": t.note,
            "overdue": bool(t.due_date and not t.done and t.due_date < local_today().isoformat())}


@router.get("/checklist-template")
async def get_template(request: Request, db: AsyncSession = Depends(get_db)):
    org = _hr(request)
    rows = await _template(db, org)
    await db.commit()
    return [{"id": str(r.id), "title": r.title, "owner": r.owner, "due_offset_days": r.due_offset_days} for r in rows]


@router.put("/checklist-template")
async def put_template(body: TemplateIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Replace the org's checklist. Applies to joiners started from now on."""
    org = _hr(request)
    items = [i for i in body.items if i.title.strip()]
    if len(items) > 60:
        raise HTTPException(422, "A checklist can have 60 lines at most")
    for r in (await db.execute(select(OnboardingTaskTemplate).where(
            OnboardingTaskTemplate.org_id == org))).scalars().all():
        await db.delete(r)
    db.add_all([OnboardingTaskTemplate(org_id=org, title=i.title.strip()[:200], owner=(i.owner or "").strip()[:120] or None,
                                       due_offset_days=i.due_offset_days, sort=n) for n, i in enumerate(items)])
    if not items:           # keep one inactive marker so the starter list is not re-seeded
        db.add(OnboardingTaskTemplate(org_id=org, title="(empty)", is_active=False, sort=0))
    await db.commit()
    await record_audit(request, "onboarding.checklist.template", entity_type="organization", entity_id=str(org),
                       summary=f"Joining checklist changed ({len(items)} lines)")
    return await get_template(request, db)


# ───────────────────────── HR / admin (org-authenticated) ─────────────────────

@router.post("/sessions", status_code=201)
async def create_session(body: SessionCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """HR enters the job + pay and gets back the joiner's personal link."""
    org = _hr(request)
    d = body.model_dump()
    expiry_days = max(1, min(int(d.pop("invite_expiry_days", 14) or 14), 90))
    info = await identity.org_info(org) or {}
    try:
        country = id_validators.clean_country(d.get("address_country"), default=info.get("country"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    country = country or "CA"
    region = (d.get("province_of_employment") or info.get("province") or "").upper()
    if not intake.valid_region(country, region):
        raise HTTPException(422, "Choose the province of employment")
    d.update(address_country=country, province_of_employment=region,
             pay_frequency=d.get("pay_frequency") or "biweekly")
    seed = {k: v for k, v in d.items() if v is not None and v != ""}
    s = OnboardingSession(
        org_id=org, invite_token=secrets.token_urlsafe(32), status="invited", country=country,
        employee_number=(body.employee_number or "").strip() or None,
        first_name=body.first_name, last_name=body.last_name, email=(body.email or "").strip() or None,
        job_title=body.job_title, steps_completed=[], current_step=STEPS[0], data=seed,
        created_by=_id(_uid(request)) if _uid(request) else None,
        invited_at=_now(), invite_expires_at=_now() + timedelta(days=expiry_days),
    )
    db.add(s)
    await db.flush()
    for i, t in enumerate(await _template(db, org)):
        db.add(OnboardingTask(org_id=org, session_id=s.id, title=t.title, owner=t.owner, sort=i,
                              due_date=_due(body.hire_date, t.due_offset_days)))
    await db.commit()
    await db.refresh(s)
    sent = await _send_invite(s, info.get("name"), expiry_days)
    await record_audit(request, "onboarding.session.create", entity_type="onboarding_session", entity_id=str(s.id),
                       summary=f"Onboarding started for {s.first_name} {s.last_name} ({country})",
                       meta={"email_sent": sent})
    out = _session_out(s)
    out.update(email_sent=sent, **_link_fields(s))
    return out


@router.get("/sessions")
async def list_sessions(request: Request, status: str | None = None,
                        db: AsyncSession = Depends(get_db)):
    q = select(OnboardingSession).where(OnboardingSession.org_id == _hr(request))
    if status:
        q = q.where(OnboardingSession.status == status)
    rows = (await db.execute(q.order_by(OnboardingSession.created_at.desc()))).scalars().all()
    tasks = (await db.execute(select(OnboardingTask.session_id, OnboardingTask.done).where(
        OnboardingTask.org_id == _org(request)))).all()
    out = []
    for s in rows:
        mine = [done for sid, done in tasks if sid == s.id]
        out.append({**_session_out(s), "department": (s.data or {}).get("department"),
                    "hire_date": (s.data or {}).get("hire_date"),
                    "tasks_total": len(mine), "tasks_done": sum(1 for x in mine if x)})
    return out


async def _get_hr(session_id: str, request: Request, db: AsyncSession) -> OnboardingSession:
    s = (await db.execute(select(OnboardingSession).where(
        OnboardingSession.id == _id(session_id, "Onboarding session"),
        OnboardingSession.org_id == _hr(request)))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Onboarding session not found")
    return s


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    out = _session_out(s)
    out["data"] = intake.safe_view(s.data)          # identity / bank numbers masked
    out["missing"] = intake.missing_for_submit(_country(s), s.data)
    if s.status in ("invited", "submitted"):
        out.update(_link_fields(s))
    return out


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, body: dict, request: Request,
                        db: AsyncSession = Depends(get_db)):
    """HR edits the record (same checks as the joiner's page, plus the job / pay
    fields) or cancels the session."""
    s = await _get_hr(session_id, request, db)
    if s.status == "activated":
        raise HTTPException(409, "This joiner is already an employee. Edit the employee record instead.")
    if body.get("status") == "cancelled":
        s.status = "cancelled"
    patch = body.get("data")
    if isinstance(patch, dict):
        country = _country(s)
        if "address_country" in patch:
            try:
                country = id_validators.clean_country(patch.pop("address_country")) or country
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        merged, errors, _ = intake.clean_patch(country, patch, hr=True)
        if errors:
            raise HTTPException(422, " ".join(f"{m}." for m in errors.values()))
        if "province_of_employment" in merged and not intake.valid_region(country, merged["province_of_employment"]):
            raise HTTPException(422, "Choose the province of employment")
        s.country = country
        s.data = {**(s.data or {}), **merged, "address_country": country}
        for k in ("first_name", "last_name", "job_title"):
            if merged.get(k):
                setattr(s, k, merged[k])
        if "employee_number" in merged:
            s.employee_number = (merged["employee_number"] or "").strip() or None
    await db.commit()
    await db.refresh(s)
    return {**_session_out(s), "data": intake.safe_view(s.data)}


@router.post("/sessions/{session_id}/resend")
async def resend_invite(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A fresh link (the old one stops working) with a new expiry."""
    s = await _get_hr(session_id, request, db)
    if s.status not in ("invited", "submitted"):
        raise HTTPException(409, f"This joiner is {s.status}. A new link is not needed.")
    s.invite_token = secrets.token_urlsafe(32)
    s.invited_at, s.invite_expires_at = _now(), _now() + timedelta(days=14)
    await db.commit()
    info = await identity.org_info(s.org_id) or {}
    sent = await _send_invite(s, info.get("name"), 14)
    await record_audit(request, "onboarding.invite.resend", entity_type="onboarding_session", entity_id=str(s.id),
                       summary=f"Joining link re-sent to {s.first_name} {s.last_name}", meta={"email_sent": sent})
    return {**_session_out(s), "email_sent": sent, **_link_fields(s)}


@router.post("/sessions/{session_id}/return")
async def return_session(session_id: str, request: Request, body: ReviewIn = ReviewIn(),
                         db: AsyncSession = Depends(get_db)):
    """Send a submitted form back to the joiner to correct."""
    s = await _get_hr(session_id, request, db)
    if s.status != "submitted":
        raise HTTPException(409, "Only a submitted form can be sent back")
    s.status, s.submitted_at = "invited", None
    s.review_note = (body.note or "").strip()[:500] or None
    if not s.invite_expires_at or _aware(s.invite_expires_at) < _now() + timedelta(days=3):
        s.invite_expires_at = _now() + timedelta(days=7)
    await db.commit()
    await record_audit(request, "onboarding.session.return", entity_type="onboarding_session", entity_id=str(s.id),
                       summary=f"Joining form sent back to {s.first_name} {s.last_name}")
    return _session_out(s)


def _docs_q(s: OnboardingSession):
    return select(StoredDocument).where(StoredDocument.org_id == s.org_id,
                                        StoredDocument.onboarding_session_id == s.id,
                                        StoredDocument.deleted_at.is_(None)).order_by(StoredDocument.uploaded_at)


@router.get("/sessions/{session_id}/documents")
async def list_documents(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    return [docstore.doc_out(d) for d in (await db.execute(_docs_q(s))).scalars().all()]


async def _session_doc(s: OnboardingSession, doc_id: str, db: AsyncSession) -> StoredDocument:
    d = (await db.execute(_docs_q(s).where(StoredDocument.id == _id(doc_id, "Document")))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/sessions/{session_id}/documents/{doc_id}/download")
async def download_document(session_id: str, doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    d = await _session_doc(s, doc_id, db)
    data = (await db.execute(select(StoredDocument.data).where(StoredDocument.id == d.id))).scalar_one()
    await record_audit(request, "onboarding.document.read", entity_type="onboarding_session", entity_id=str(s.id),
                       summary=f"Joiner document opened: {d.doc_type} ({d.file_name})")
    return Response(content=bytes(data), media_type=d.content_type, headers={
        "Content-Disposition": f'attachment; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/sessions/{session_id}/documents/{doc_id}/verify")
async def verify_document(session_id: str, doc_id: str, request: Request, body: dict | None = None,
                          db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    d = await _session_doc(s, doc_id, db)
    d.verified = bool((body or {}).get("verified", True))
    d.verified_by = request.headers.get("x-user-email") if d.verified else None
    d.verified_at = _now() if d.verified else None
    await db.commit()
    return docstore.doc_out(d)


@router.get("/sessions/{session_id}/consents")
async def list_consents(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    res = await db.execute(select(OnboardingConsent).where(
        OnboardingConsent.session_id == s.id, OnboardingConsent.org_id == s.org_id))
    return [{"id": str(x.id), "consent_type": x.consent_type, "version": x.version,
             "signed_name": x.signed_name,
             "consented_at": _aware(x.consented_at).isoformat() if x.consented_at else None}
            for x in res.scalars().all()]


# ── checklist per joiner ──────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/tasks")
async def list_tasks(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    rows = (await db.execute(select(OnboardingTask).where(
        OnboardingTask.session_id == s.id, OnboardingTask.org_id == s.org_id).order_by(
        OnboardingTask.sort, OnboardingTask.created_at))).scalars().all()
    return [_task_out(t) for t in rows]


@router.post("/sessions/{session_id}/tasks", status_code=201)
async def add_task(session_id: str, body: TaskIn, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _get_hr(session_id, request, db)
    if not (body.title or "").strip():
        raise HTTPException(422, "Type what needs to be done")
    t = OnboardingTask(org_id=s.org_id, session_id=s.id, title=body.title.strip()[:200],
                       owner=(body.owner or "").strip()[:120] or None, due_date=_date(body.due_date), sort=999)
    db.add(t)
    await db.commit()
    return _task_out(t)


def _date(v: str | None) -> str | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise HTTPException(422, "Due date must be a date")


async def _task(task_id: str, request: Request, db: AsyncSession) -> OnboardingTask:
    t = (await db.execute(select(OnboardingTask).where(
        OnboardingTask.id == _id(task_id, "Task"), OnboardingTask.org_id == _hr(request)))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Task not found")
    return t


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskIn, request: Request, db: AsyncSession = Depends(get_db)):
    t = await _task(task_id, request, db)
    sent = body.model_fields_set
    if "title" in sent and (body.title or "").strip():
        t.title = body.title.strip()[:200]
    if "owner" in sent:
        t.owner = (body.owner or "").strip()[:120] or None
    if "due_date" in sent:
        t.due_date = _date(body.due_date)
    if "note" in sent:
        t.note = (body.note or "").strip()[:300] or None
    if "done" in sent and body.done is not None:
        t.done = body.done
        t.done_at = _now() if body.done else None
        t.done_by = request.headers.get("x-user-email") if body.done else None
    await db.commit()
    return _task_out(t)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    t = await _task(task_id, request, db)
    await db.delete(t)
    await db.commit()
    return {"ok": True}


# ── approve = employee + login ────────────────────────────────────────────────

async def _activate(s: OnboardingSession, request: Request, db: AsyncSession) -> dict:
    """Create the employee (active) from the collected data, attach the uploaded
    documents, and — setting, default ON — create the login and send the
    set-password e-mail."""
    payload = intake.employee_payload(s.data)
    payload.update(first_name=s.first_name, last_name=s.last_name, status="active",
                   address_country=_country(s) or None)
    if s.email and not payload.get("email"):
        payload["email"] = s.email
    if s.employee_number:
        payload["employee_number"] = s.employee_number
    else:
        payload.pop("employee_number", None)          # the employee service assigns the next number
    for k in ("invite_expiry_days", "equity_self_id", "no_tax_flag"):
        payload.pop(k, None)

    fwd = {"X-Org-Id": str(s.org_id), "X-User-Id": _uid(request) or "",
           "X-Role": request.headers.get("x-role", ""), "X-User-Email": request.headers.get("x-user-email", ""),
           "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{settings.EMPLOYEE_URL}/employees", json=payload, headers=fwd)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach the employee service: {exc.__class__.__name__}")
    if r.status_code >= 300:
        try:
            why = r.json().get("detail")
        except Exception:
            why = None
        raise HTTPException(r.status_code if r.status_code < 500 else 502,
                            f"The employee could not be created. {why or 'Check the form and try again.'}")
    emp = r.json()
    s.employee_id = _id(emp.get("id"), "Employee")
    s.employee_number = emp.get("employee_number") or s.employee_number
    s.status, s.activated_at = "activated", _now()
    for d in (await db.execute(_docs_q(s))).scalars().all():
        d.employee_id = s.employee_id                # the joiner's uploads now sit on the employee profile
    await db.commit()
    await db.refresh(s)
    out = _session_out(s)
    out["employee"] = {k: emp.get(k) for k in ("id", "employee_number", "first_name", "last_name", "status")}

    # Login (setting: auto_login_on_onboarding, default ON).
    out["login"] = {"created": False, "reason": "switched off in settings"}
    cfg = await identity.get_settings(s.org_id)
    login_mail = (s.data or {}).get("work_email") or s.email or (s.data or {}).get("email")
    if cfg.get("auto_login_on_onboarding", True):
        if not login_mail:
            out["login"] = {"created": False, "reason": "no e-mail on file — create the login from Users"}
        else:
            try:
                res = await identity.ensure_login_for_employee(
                    s.org_id, s.employee_id, login_mail, "employee", first_name=s.first_name,
                    last_name=s.last_name, actor=identity.actor_from(request))
                out["login"] = {k: res.get(k) for k in ("created", "linked", "invited", "email_sent", "email", "link")
                                if res.get(k) is not None}
            except identity.IdentityError as exc:
                out["login"] = {"created": False, "reason": str(exc)}
    await record_audit(request, "onboarding.session.activate", entity_type="onboarding_session", entity_id=str(s.id),
                       summary=f"{s.first_name} {s.last_name} approved — employee {s.employee_number} is active",
                       meta={"employee_id": str(s.employee_id), "login_created": bool(out["login"].get("created"))})

    # PIPEDA: mirror the purposes the hire consented to into the compliance privacy
    # centre's consent register so coverage counts them (best-effort).
    given = [c.consent_type for c in (await db.execute(select(OnboardingConsent).where(
        OnboardingConsent.session_id == s.id))).scalars().all()]
    purposes = privacy_purposes_in(given)
    if purposes:
        name = f"{s.first_name or ''} {s.last_name or ''}".strip()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{settings.COMPLIANCE_URL}/compliance/privacy/consents/bulk",
                                  json={"employee_id": emp["id"], "purposes": purposes, "channel": "onboarding",
                                        "employee_name": name, "signed_name": name}, headers=fwd)
        except httpx.HTTPError:
            pass
    out["privacy_consents_mirrored"] = purposes
    return out


@router.post("/sessions/{session_id}/approve")
async def approve_session(session_id: str, request: Request, body: ReviewIn = ReviewIn(),
                          db: AsyncSession = Depends(get_db)):
    """HR approves → the joiner becomes an ACTIVE employee and gets a login."""
    s = await _get_hr(session_id, request, db)
    if s.status not in ("submitted", "invited", "approved"):
        raise HTTPException(409, f"This joiner is already {s.status}")
    if s.status != "approved":
        s.status = "approved"                        # if the next step fails, "Approve" can be pressed again
        s.reviewed_by = _id(_uid(request)) if _uid(request) else None
        s.reviewed_at = _now()
        await db.commit()
    return await _activate(s, request, db)


@router.post("/sessions/{session_id}/activate")
async def activate_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Retry of the second half of Approve (kept for sessions left in 'approved')."""
    s = await _get_hr(session_id, request, db)
    if s.status == "activated" and s.employee_id:
        raise HTTPException(409, "This joiner is already an employee")
    if s.status == "cancelled":
        raise HTTPException(409, "This onboarding was cancelled")
    return await _activate(s, request, db)


@router.get("/consent-types")
async def consent_types(country: str = "CA"):
    """Consent catalogue the wizard shows (PIPEDA set)."""
    return {"country": "CA", "notice_version": NOTICE_VERSION,
            "items": consent_types_for(country)}


@router.get("/ping")
async def ping(request: Request):
    from shared.utils.roles import HR_WRITE, require_roles
    require_roles(request, HR_WRITE, "check the onboarding service")   # contract: /onboarding = HR (WASA 2026-10-21)
    return {"ok": True, "service": "onboarding"}


# ───────────────────────── The joiner's page (token — public) ─────────────────

def _throttle(limiter: RateLimiter, request: Request, token: str):
    wait = limiter.hit(f"{client_ip(request)}|{token[:12]}")
    if wait:
        raise HTTPException(429, f"Too many tries. Wait {max(wait // 60, 1)} minute(s) and try again.",
                            headers={"Retry-After": str(wait)})


async def _by_token(token: str, db: AsyncSession, request: Request, write: bool = False) -> OnboardingSession:
    """The session behind a link. The same message for 'wrong' and 'cancelled'
    so a link cannot be probed. `write=True` also refuses a form that was
    already submitted — the link is single-use for submitting."""
    _throttle(page_limiter, request, token)
    s = (await db.execute(select(OnboardingSession).where(
        OnboardingSession.invite_token == token))).scalar_one_or_none() if 20 <= len(token) <= 64 else None
    if not s or s.status == "cancelled":
        raise HTTPException(404, "This link is not valid. Ask your HR team for a new one.")
    if s.status in ("activated", "approved"):
        raise HTTPException(410, "Your joining is complete. This link is closed.")
    if s.invite_expires_at and _aware(s.invite_expires_at) < _now():
        raise HTTPException(410, "This link has expired. Ask your HR team to send a new one.")
    if write and s.status != "invited":
        raise HTTPException(409, "You have already submitted this form. Contact your HR team to change anything.")
    return s


async def _consents_given(s: OnboardingSession, db: AsyncSession) -> list[str]:
    return [c for (c,) in (await db.execute(select(OnboardingConsent.consent_type).where(
        OnboardingConsent.session_id == s.id))).all()]


@router.get("/invite/{token}")
async def get_invite(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Everything the joiner's page needs: company, country, what is already
    filled (identity / bank numbers masked), documents, consents."""
    s = await _by_token(token, db, request)
    country = _country(s)
    info = await identity.org_info(s.org_id) or {}
    view = intake.safe_view(s.data)
    allowed = intake.allowed_fields(country) | {f"{k}_masked" for k in intake.SENSITIVE} | {
        f"has_{k}" for k in intake.SENSITIVE}
    docs = (await db.execute(_docs_q(s))).scalars().all()
    out = _session_out(s)
    for k in ("employee_id", "employee_number", "created_at", "invited_at", "id"):
        out.pop(k, None)                              # the joiner's page shows no internal ids
    out.update(
        company=info.get("name"), locked=s.status != "invited",
        job_title=s.job_title, hire_date=(s.data or {}).get("hire_date"),
        data={k: v for k, v in view.items() if k in allowed},
        documents=[docstore.doc_out(d) for d in docs],
        doc_types=[{"value": k, "label": v} for k, v in docstore.DOC_TYPES.get(country, docstore.DOC_TYPES["CA"])],
        doc_rules={"allowed": docstore.ALLOWED_LABEL, "max_mb": docstore.MAX_BYTES // (1024 * 1024)},
        consent_types=consent_types_for(country), consents_given=await _consents_given(s, db),
        notice_version=NOTICE_VERSION,
        missing=intake.missing_for_submit(country, s.data),
    )
    return out


@router.get("/invite/{token}/consent-types")
async def invite_consent_types(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _by_token(token, db, request)
    country = _country(s) or "CA"
    return {"country": country, "notice_version": NOTICE_VERSION,
            "items": consent_types_for(country)}


@router.patch("/invite/{token}/step")
async def save_step(token: str, body: StepPatch, request: Request, db: AsyncSession = Depends(get_db)):
    """Save one section. Only the joiner's own fields are accepted; identity and
    bank numbers are checked for the work country and stored encrypted."""
    s = await _by_token(token, db, request, write=True)
    if body.step not in STEPS:
        raise HTTPException(422, "Unknown section")
    merged, errors, _ = intake.clean_patch(_country(s), body.data or {})
    if errors:
        raise HTTPException(422, " ".join(f"{m}." for m in errors.values()))
    s.data = {**(s.data or {}), **merged}
    if body.complete:
        done = [x for x in (s.steps_completed or []) if x in STEPS]
        if body.step not in done:
            done.append(body.step)
        s.steps_completed = done
        s.current_step = next((x for x in STEPS if x not in done), STEPS[-1])
    await db.commit()
    await db.refresh(s)
    return {**_session_out(s), "data": intake.safe_view(merged),
            "missing": intake.missing_for_submit(_country(s), s.data)}


@router.post("/invite/{token}/consents", status_code=201)
async def add_consent(token: str, body: ConsentIn, request: Request,
                      db: AsyncSession = Depends(get_db)):
    s = await _by_token(token, db, request, write=True)
    known = {c["key"] for c in consent_types_for(_country(s))}
    if body.consent_type not in known:
        raise HTTPException(422, "Unknown consent")
    if body.consent_type in await _consents_given(s, db):
        return {"consent_type": body.consent_type, "already": True}
    c = OnboardingConsent(org_id=s.org_id, session_id=s.id, consent_type=body.consent_type,
                          version=body.version, signed_name=(body.signed_name or "")[:200] or None,
                          ip=client_ip(request)[:45] or None)
    db.add(c)
    await db.commit()
    return {"id": str(c.id), "consent_type": c.consent_type}


@router.post("/invite/{token}/documents", status_code=201)
async def add_document(token: str, request: Request, file: UploadFile = File(...),
                       doc_type: str = Form("other"), db: AsyncSession = Depends(get_db)):
    """Upload one file (JPG / PNG / PDF, size-capped, type read from the bytes)."""
    s = await _by_token(token, db, request, write=True)
    _throttle(upload_limiter, request, token)
    if len((await db.execute(_docs_q(s))).scalars().all()) >= 20:
        raise HTTPException(409, "You have uploaded 20 files already. Remove one first.")
    data = await file.read(docstore.MAX_BYTES + 1)
    try:
        cols = docstore.check_upload(file.filename, data)
    except docstore.DocError as exc:
        raise HTTPException(exc.status, str(exc))
    known = {k for k, _ in docstore.DOC_TYPES.get(_country(s), docstore.DOC_TYPES["CA"])}
    d = StoredDocument(org_id=s.org_id, onboarding_session_id=s.id,
                       doc_type=doc_type if doc_type in known else "other", uploaded_by="new-hire",
                       uploaded_by_name=f"{s.first_name or ''} {s.last_name or ''}".strip() or "New hire", **cols)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return docstore.doc_out(d)


@router.delete("/invite/{token}/documents/{doc_id}")
async def remove_document(token: str, doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _by_token(token, db, request, write=True)
    d = await _session_doc(s, doc_id, db)
    d.deleted_at, d.deleted_by = _now(), "new-hire"
    await db.commit()
    return {"ok": True}


@router.post("/invite/{token}/submit")
async def submit_invite(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The joiner finishes → HR reviews. Works once; after it the link is read-only."""
    s = await _by_token(token, db, request, write=True)
    country = _country(s)
    missing = intake.missing_for_submit(country, s.data)
    if missing:
        raise HTTPException(422, "Please fill in: " + ", ".join(missing) + ".")
    given = set(await _consents_given(s, db))
    need = [c["label"] for c in consent_types_for(country) if c.get("required") and c["key"] not in given]
    if need:
        raise HTTPException(422, "Please tick: " + ", ".join(need) + ".")
    s.status, s.submitted_at, s.review_note = "submitted", _now(), None
    s.steps_completed = list(STEPS)
    await db.commit()
    await db.refresh(s)
    out = _session_out(s)
    for k in ("employee_id", "employee_number", "id"):
        out.pop(k, None)
    return {**out, "locked": True}
