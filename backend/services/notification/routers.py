"""Notification service — the bell, the notifications page, preferences, templates and
the admin delivery log. Every query is scoped by the gateway's X-Org-Id (and X-User-Id
for a person's own notifications); nothing identifying is read from the request body.
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import notify as nt  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.directory import role_in  # noqa: E402
from shared.utils.platform_models import Delivery, Notification, NotifyPreference, NotifyTemplate  # noqa: E402

from database import get_db  # noqa: E402

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _uid(request: Request) -> str:
    uid = request.headers.get("x-user-id")
    if not uid:
        raise HTTPException(401, "Missing user context")
    return uid


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def is_admin(role: str) -> bool:
    """Who may see the delivery log and edit templates: org admin and HR."""
    return role in ("org_admin", "superadmin") or role_in(role, ["hr"])


def _need_admin(request: Request):
    if not is_admin(_role(request)):
        raise HTTPException(403, "Only an org admin or HR can open this")


def is_log_reader(role: str) -> bool:
    """Decision 2026-10-21 (least privilege): the delivery LOG (who was sent what, to which address,
    and why it failed) is an audit record — the org admin only. HR keeps the templates."""
    return role in ("org_admin", "superadmin")


def _need_log_reader(request: Request):
    if not is_log_reader(_role(request)):
        raise HTTPException(403, "Only the org admin can open the delivery log")


def _note_out(n: Notification) -> dict:
    return {"id": n.id, "title": n.title, "body": n.body or "", "link": n.link or "", "kind": n.kind,
            "kind_label": nt.kind_label(n.kind), "created_at": pm.iso(n.created_at),
            "read_at": pm.iso(n.read_at), "read": n.read_at is not None}


# ══════════════════════════ my notifications ═══════════════════════════════════

@router.get("")
async def list_mine(request: Request, unread: bool = False, kind: str | None = None, q: str | None = None,
                    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    base = [Notification.org_id == org, Notification.user_id == uid]
    where = list(base)
    if unread:
        where.append(Notification.read_at.is_(None))
    if kind:
        where.append(Notification.kind == kind)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(or_(func.lower(Notification.title).like(like), func.lower(Notification.body).like(like)))
    limit = max(1, min(limit, 200))
    rows = (await db.execute(select(Notification).where(*where)
                             .order_by(Notification.created_at.desc()).limit(limit).offset(max(0, offset)))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(Notification).where(*where))).scalar_one()
    unread_n = (await db.execute(select(func.count()).select_from(Notification).where(
        *base, Notification.read_at.is_(None)))).scalar_one()
    kinds = (await db.execute(select(Notification.kind).where(*base).distinct())).scalars().all()
    return {"items": [_note_out(n) for n in rows], "total": total, "unread": unread_n,
            "kinds": [{"kind": k, "label": nt.kind_label(k)} for k in sorted(kinds)]}


@router.get("/unread-count")
async def unread_count(request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    n = (await db.execute(select(func.count()).select_from(Notification).where(
        Notification.org_id == org, Notification.user_id == uid, Notification.read_at.is_(None)))).scalar_one()
    latest = (await db.execute(select(Notification).where(
        Notification.org_id == org, Notification.user_id == uid)
        .order_by(Notification.created_at.desc()).limit(8))).scalars().all()
    return {"unread": n, "latest": [_note_out(x) for x in latest]}


class ReadAllIn(BaseModel):
    kind: str | None = None


@router.post("/read-all")
async def read_all(request: Request, body: ReadAllIn = ReadAllIn(), db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    q = update(Notification).where(Notification.org_id == org, Notification.user_id == uid,
                                   Notification.read_at.is_(None))
    if body.kind:
        q = q.where(Notification.kind == body.kind)
    res = await db.execute(q.values(read_at=pm.now_utc()))
    await db.commit()
    return {"marked": res.rowcount}


@router.post("/{nid}/read")
async def mark_read(nid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    res = await db.execute(update(Notification).where(
        Notification.id == nid, Notification.org_id == org, Notification.user_id == uid,
        Notification.read_at.is_(None)).values(read_at=pm.now_utc()))
    await db.commit()
    return {"marked": res.rowcount}


@router.post("/{nid}/unread")
async def mark_unread(nid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    res = await db.execute(update(Notification).where(
        Notification.id == nid, Notification.org_id == org, Notification.user_id == uid).values(read_at=None))
    await db.commit()
    return {"marked": res.rowcount}


# ══════════════════════════ my preferences ═════════════════════════════════════

@router.get("/preferences")
async def my_preferences(request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    rows = (await db.execute(select(NotifyPreference).where(
        NotifyPreference.org_id == org, NotifyPreference.user_id == uid))).scalars().all()
    stored = {p.kind: bool(p.email_on) for p in rows}
    kinds = list(nt.KINDS) + sorted(k for k in stored if k not in nt.KINDS)
    return {"in_app": "always on",
            "email_available": nt.smtp_status(),
            "kinds": [{"kind": k, "label": nt.kind_label(k), "email_on": stored.get(k, True)} for k in kinds]}


class PrefsIn(BaseModel):
    email: dict[str, bool]          # {kind: on/off}


@router.put("/preferences")
async def save_preferences(body: PrefsIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _uid(request)
    rows = (await db.execute(select(NotifyPreference).where(
        NotifyPreference.org_id == org, NotifyPreference.user_id == uid))).scalars().all()
    have = {p.kind: p for p in rows}
    for kind, on in body.email.items():
        kind = (kind or "").strip()[:40]
        if not kind:
            continue
        if kind in have:
            have[kind].email_on, have[kind].updated_at = bool(on), pm.now_utc()
        else:
            db.add(NotifyPreference(org_id=org, user_id=uid, kind=kind, email_on=bool(on)))
    await db.commit()
    return await my_preferences(request, db)


# ══════════════════════════ admin: status, delivery log, retry ═════════════════

@router.get("/status")
async def status(request: Request):
    _org(request)
    return nt.smtp_status()


def _delivery_out(d: Delivery) -> dict:
    return {"id": d.id, "recipient_name": d.recipient_name or "", "recipient_email": d.recipient_email or "",
            "channel": d.channel, "kind": d.kind, "kind_label": nt.kind_label(d.kind), "title": d.title,
            "status": d.status, "reason": d.reason or "", "attempts": d.attempts or 0,
            "created_at": pm.iso(d.created_at), "sent_at": pm.iso(d.sent_at),
            "can_retry": d.channel == "email" and d.status in ("failed", "not_configured")}


@router.get("/deliveries")
async def deliveries(request: Request, status: str | None = None, channel: str | None = None,
                     kind: str | None = None, q: str | None = None, limit: int = 200, offset: int = 0,
                     db: AsyncSession = Depends(get_db)):
    _need_log_reader(request)
    org = _org(request)
    where = [Delivery.org_id == org]
    if status:
        where.append(Delivery.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if channel:
        where.append(Delivery.channel == channel)
    if kind:
        where.append(Delivery.kind == kind)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(or_(func.lower(Delivery.title).like(like), func.lower(Delivery.recipient_name).like(like),
                         func.lower(Delivery.recipient_email).like(like)))
    limit = max(1, min(limit, 1000))
    rows = (await db.execute(select(Delivery).where(*where).order_by(Delivery.created_at.desc())
                             .limit(limit).offset(max(0, offset)))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(Delivery).where(*where))).scalar_one()
    by_status = dict((await db.execute(select(Delivery.status, func.count()).where(Delivery.org_id == org)
                                       .group_by(Delivery.status))).all())
    return {"items": [_delivery_out(d) for d in rows], "total": total, "by_status": by_status,
            "smtp": nt.smtp_status()}


@router.post("/deliveries/{did}/retry")
async def retry(did: str, request: Request):
    _need_log_reader(request)
    st = await nt.retry_delivery(_org(request), did)
    if st == "not_retryable":
        raise HTTPException(409, "Only a failed or not-configured e-mail can be retried")
    return {"status": st}


@router.post("/test-email")
async def test_email(request: Request, db: AsyncSession = Depends(get_db)):
    """Send a test notification to the caller and report what REALLY happened to the e-mail."""
    _need_log_reader(request)
    org, uid = _org(request), _uid(request)
    queued = await nt._notify(org, user_ids=[uid], roles=None, employee_ids=None,
                              title="Test notification", body="This is a test from Notifications > Delivery log.",
                              link="/notifications", kind="system", email=True)
    await nt.drain()
    if not queued:
        last = (await db.execute(select(Delivery).where(
            Delivery.org_id == org, Delivery.user_id == uid, Delivery.channel == "email")
            .order_by(Delivery.created_at.desc()).limit(1))).scalar_one_or_none()
        return {"in_app": "delivered", "email": last.status if last else "none",
                "reason": (last.reason if last else "") or ""}
    d = (await db.execute(select(Delivery).where(Delivery.id == queued[0]))).scalar_one()
    return {"in_app": "delivered", "email": d.status, "reason": d.reason or ""}


# ══════════════════════════ admin: e-mail templates ════════════════════════════

@router.get("/templates")
async def templates(request: Request, db: AsyncSession = Depends(get_db)):
    _need_admin(request)
    org = _org(request)
    rows = {t.kind: t for t in (await db.execute(select(NotifyTemplate).where(
        NotifyTemplate.org_id == org))).scalars().all()}
    kinds = list(nt.KINDS) + sorted(k for k in rows if k not in nt.KINDS)
    return {"placeholders": [{"name": k, "meaning": v} for k, v in nt.PLACEHOLDERS.items()],
            "default": {"subject": nt.DEFAULT_SUBJECT, "body": nt.DEFAULT_BODY},
            "note": ("The message text and any amounts or id numbers are never e-mailed. "
                     "The e-mail carries the title and a link to the screen."),
            "templates": [{"kind": k, "label": nt.kind_label(k),
                           "subject": rows[k].subject if k in rows else nt.DEFAULT_SUBJECT,
                           "body": rows[k].body if k in rows else nt.DEFAULT_BODY,
                           "customised": k in rows,
                           "updated_by": rows[k].updated_by if k in rows else None,
                           "updated_at": pm.iso(rows[k].updated_at) if k in rows else None} for k in kinds]}


class TemplateIn(BaseModel):
    subject: str
    body: str


@router.put("/templates/{kind}")
async def save_template(kind: str, body: TemplateIn, request: Request, db: AsyncSession = Depends(get_db)):
    _need_admin(request)
    org = _org(request)
    subject, text = body.subject.strip(), body.body.strip()
    if not subject or not text:
        raise HTTPException(422, "Subject and body are both needed")
    if len(subject) > 300 or len(text) > 5000:
        raise HTTPException(422, "Subject is limited to 300 characters and body to 5000")
    bad = nt.unknown_placeholders(subject + " " + text)
    if bad:
        raise HTTPException(422, "Unknown placeholder: " + ", ".join("{{" + b + "}}" for b in bad)
                            + ". Allowed: " + ", ".join("{{" + p + "}}" for p in nt.PLACEHOLDERS))
    t = (await db.execute(select(NotifyTemplate).where(
        NotifyTemplate.org_id == org, NotifyTemplate.kind == kind))).scalar_one_or_none()
    if t is None:
        t = NotifyTemplate(org_id=org, kind=kind[:40], subject=subject, body=text)
        db.add(t)
    t.subject, t.body = subject, text
    t.updated_by, t.updated_at = request.headers.get("x-user-email"), pm.now_utc()
    await db.commit()
    sample = {"title": "Leave approved", "recipient_name": "the recipient", "org_name": "your company",
              "kind": nt.kind_label(kind), "link": nt.absolute_link("/notifications"),
              "date": pm.now_utc().date().isoformat()}
    return {"kind": kind, "subject": subject, "body": text, "customised": True,
            "preview": {"subject": nt.render_template(subject, sample), "body": nt.render_template(text, sample)}}


@router.delete("/templates/{kind}")
async def reset_template(kind: str, request: Request, db: AsyncSession = Depends(get_db)):
    _need_admin(request)
    org = _org(request)
    t = (await db.execute(select(NotifyTemplate).where(
        NotifyTemplate.org_id == org, NotifyTemplate.kind == kind))).scalar_one_or_none()
    if t:
        await db.delete(t)
        await db.commit()
    return {"kind": kind, "subject": nt.DEFAULT_SUBJECT, "body": nt.DEFAULT_BODY, "customised": False}
