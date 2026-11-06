"""Company feed — /engage/feed (ops service). Employee portal batch 3, spec item 12.

GET    /engage/feed?department=&location=&kind=&limit=&before=   merged feed: posts + polls + auto posts
                                                                 (birthday · anniversary · new joiner, written
                                                                 lazily + idempotently for today and the last 7
                                                                 days) + approved kudos (read from eng_kudos)
POST   /engage/feed                          post or poll {kind, body, options, closes_at, audience_department, audience_location}
GET    /engage/feed/prefs   PUT /engage/feed/prefs    my opt-outs {birthday, anniversary, new_joiner}
POST   /engage/feed/{item}/react {emoji}     toggle (like | celebrate | support | insightful); item = post id | kudos:<id>
GET    /engage/feed/{item}/comments   POST /engage/feed/{item}/comments {body}
POST   /engage/feed/{post}/vote {option_id}  one vote per person, until closes_at
POST   /engage/feed/{post}/hide {reason} | unhide | pin | unpin      HR only
DELETE /engage/feed/{post}                   author only (post / poll)
DELETE /engage/feed/comments/{id}            author only
POST   /engage/feed/comments/{id}/hide {reason} | unhide             HR only

Every query is org-scoped. A birthday is shown as "16 October" — no response ever carries a
date of birth, a birth year or an age.
"""
from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import engage_common as C  # noqa: E402
import feed_logic as F  # noqa: E402
from database import get_db  # noqa: E402
from engage_models import Kudos  # noqa: E402
from feed_models import FeedComment, FeedPost, FeedPrefs, FeedReaction, FeedVote  # noqa: E402

router = APIRouter(prefix="/engage/feed", tags=["company-feed"])

_ROSTER_TTL = 60.0
_GEN_TTL = 300.0
_roster_cache: dict[str, tuple[float, dict]] = {}
_gen_cache: dict[str, tuple[str, float]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def load_roster(org: str) -> dict[str, dict]:
    """id → {first_name, last_name, department, work_location, date_of_birth, hire_date, status}.
    INTERNAL ONLY — date_of_birth never leaves this module except as a "day month" label."""
    hit = _roster_cache.get(org)
    if hit and time.monotonic() - hit[0] < _ROSTER_TTL:
        return hit[1]
    out: dict[str, dict] = {}
    try:
        from sqlalchemy import text
        from shared.utils import platform_models as pm
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT id, first_name, last_name, department, work_location, date_of_birth, hire_date, status "
                "FROM employees WHERE org_id = CAST(:org AS uuid) AND is_active IS TRUE"), {"org": org})).mappings().all()
        out = {str(r["id"]): {**dict(r), "id": str(r["id"])} for r in rows}
    except Exception:                                   # noqa: BLE001 — feed still works without the roster
        out = {}
    _roster_cache[org] = (time.monotonic(), out)
    return out


def _ident(request: Request):
    return C.org_of(request), C.role_of(request), C.emp_of(request), C.uid_of(request)


def _me_key(request: Request) -> str | None:
    return F.actor_key(C.emp_of(request), C.uid_of(request))


async def _prefs_map(db, org: str) -> dict[str, dict]:
    rows = (await db.execute(select(FeedPrefs).where(FeedPrefs.org_id == org))).scalars().all()
    return {str(p.employee_id): {"birthday": bool(p.opt_out_birthday), "anniversary": bool(p.opt_out_anniversary),
                                 "new_joiner": bool(p.opt_out_new_joiner)} for p in rows}


async def ensure_auto_posts(db, org: str, roster: dict[str, dict], prefs: dict[str, dict], force: bool = False) -> int:
    """Write the missing auto posts for today and the last 7 days. Idempotent (UNIQUE org_id+auto_key);
    a parallel request that wins the race just makes this one a no-op."""
    if not roster:
        return 0
    today = await org_today(org)
    hit = _gen_cache.get(org)
    if not force and hit and hit[0] == today.isoformat() and time.monotonic() - hit[1] < _GEN_TTL:
        return 0
    events = F.auto_events(list(roster.values()), today, await directory.org_name(org), prefs)
    if not events:
        _gen_cache[org] = (today.isoformat(), time.monotonic())
        return 0
    keys = [e["auto_key"] for e in events]
    existing = {p.auto_key for p in (await db.execute(select(FeedPost).where(
        FeedPost.org_id == org, FeedPost.auto_key.in_(keys)))).scalars().all()}
    todo = F.missing_events(events, existing)
    for ev in todo:
        label = ev["day_month"] if ev["kind"] == "birthday" else (
            f"{ev['years']} year{'s' if ev['years'] != 1 else ''}" if ev["kind"] == "anniversary" else ev["day_month"])
        db.add(FeedPost(org_id=uuid.UUID(org), kind=ev["kind"], author_name="Company feed", author_role="system",
                        body=ev["body"], department=ev.get("department"), location=ev.get("location"),
                        subject_employee_id=uuid.UUID(ev["subject_employee_id"]), subject_name=ev["subject_name"],
                        event_date=ev["event_date"], event_label=label, auto_key=ev["auto_key"],
                        created_at=F.event_created_at(ev["event_date"])))
    if todo:
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return 0
    _gen_cache[org] = (today.isoformat(), time.monotonic())
    return len(todo)


def _post_item(p: FeedPost) -> dict:
    return {"id": str(p.id), "kind": p.kind, "author_employee_id": str(p.author_employee_id) if p.author_employee_id else None,
            "author_user_id": p.author_user_id, "author_name": p.author_name, "body": p.body,
            "department": p.department, "location": p.location,
            "audience_department": p.audience_department, "audience_location": p.audience_location,
            "created_at": C.iso(p.created_at), "pinned": bool(p.pinned), "hidden": bool(p.hidden),
            "deleted": p.deleted_at is not None, "subject_employee_id": str(p.subject_employee_id) if p.subject_employee_id else None,
            "subject_name": p.subject_name, "event_label": p.event_label, "_row": p}


def _kudos_item(k: Kudos, roster: dict[str, dict]) -> dict:
    to = roster.get(str(k.to_employee_id)) or {}
    return {"id": f"kudos:{k.id}", "kind": "kudos", "author_employee_id": str(k.from_employee_id) if k.from_employee_id else None,
            "author_user_id": None, "author_name": k.from_name, "body": k.message, "department": to.get("department"),
            "location": to.get("work_location"), "audience_department": None, "audience_location": None,
            "created_at": C.iso(k.created_at), "pinned": False, "hidden": False, "deleted": False,
            "subject_employee_id": str(k.to_employee_id), "subject_name": k.to_name, "event_label": None,
            "kudos": {"to_name": k.to_name, "value_tag": k.value_tag, "points": k.points or 0}}


def _out(item: dict, request: Request, reactions: list[dict], comments_count: int, votes: list[dict]) -> dict:
    role, me_emp, uid = C.role_of(request), C.emp_of(request), C.uid_of(request)
    hr = F.is_hr(role)
    row = item.get("_row")
    o = {"id": item["id"], "kind": item["kind"],
         "author": {"employee_id": item["author_employee_id"], "name": item["author_name"]},
         "body": item["body"], "created_at": item["created_at"], "department": item["department"],
         "location": item["location"],
         "audience": {"department": item["audience_department"], "location": item["audience_location"]},
         "reactions": F.reaction_summary(reactions, _me_key(request)), "comments_count": comments_count,
         "pinned": item["pinned"], "hidden": item["hidden"],
         "subject": ({"employee_id": item["subject_employee_id"], "name": item["subject_name"]}
                     if item["subject_employee_id"] else None),
         "event_label": item["event_label"], "kudos": item.get("kudos"), "poll": None,
         "can_delete": F.can_delete(item["kind"], item["author_employee_id"], item["author_user_id"], me_emp, uid),
         "can_moderate": hr and item["kind"] != "kudos",
         "mine": F.is_owner(item["author_employee_id"], item["author_user_id"], me_emp, uid)}
    if hr and item["hidden"] and row is not None:
        o["hidden_reason"], o["hidden_by"] = row.hidden_reason, row.hidden_by
    if item["kind"] == "poll" and row is not None:
        o["poll"] = F.poll_results(row.poll_options or [], votes, _me_key(request), row.poll_closes_at, _utcnow())
    return o


async def _decorate(db, org: str, request: Request, items: list[dict]) -> list[dict]:
    if not items:
        return []
    ids = [i["id"] for i in items]
    reacts = (await db.execute(select(FeedReaction).where(FeedReaction.org_id == org, FeedReaction.item_id.in_(ids)))).scalars().all()
    comments = (await db.execute(select(FeedComment).where(FeedComment.org_id == org, FeedComment.item_id.in_(ids),
                                                           FeedComment.deleted_at.is_(None)))).scalars().all()
    poll_ids = [i["_row"].id for i in items if i["kind"] == "poll" and i.get("_row") is not None]
    votes = (await db.execute(select(FeedVote).where(FeedVote.org_id == org, FeedVote.post_id.in_(poll_ids)))).scalars().all() if poll_ids else []
    hr = C.is_hr(request)
    out = []
    for i in items:
        rs = [{"emoji": r.emoji, "actor_key": r.actor_key} for r in reacts if r.item_id == i["id"]]
        cc = sum(1 for c in comments if c.item_id == i["id"] and (hr or not c.hidden))
        vs = [{"option_id": v.option_id, "voter_key": v.voter_key} for v in votes
              if i.get("_row") is not None and str(v.post_id) == str(i["_row"].id)]
        out.append(_out(i, request, rs, cc, vs))
    return out


def _parse_before(before: str | None) -> datetime | None:
    if not before:
        return None
    try:
        d = datetime.fromisoformat(before.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, "before must be an ISO timestamp")


# ══════════════════════════ feed ══════════════════════════════════════════════

@router.get("")
async def feed(request: Request, department: str | None = None, location: str | None = None, kind: str | None = None,
               limit: int = 30, before: str | None = None, db: AsyncSession = Depends(get_db)):
    org, role, me_emp, _uid = _ident(request)
    if kind and kind not in F.KIND_FILTERS:
        raise HTTPException(422, f"kind must be one of {', '.join(F.KIND_FILTERS)}")
    limit = max(1, min(int(limit or 30), 100))
    cut = _parse_before(before)
    roster = await load_roster(org)
    prefs = await _prefs_map(db, org)
    try:
        await ensure_auto_posts(db, org, roster, prefs)
    except Exception:                                   # noqa: BLE001 — auto posts are best-effort
        try:
            await db.rollback()
        except Exception:                               # noqa: BLE001
            pass
    me = roster.get(me_emp or "") or {}
    fetch = limit * 3 + 20
    q = select(FeedPost).where(FeedPost.org_id == org, FeedPost.deleted_at.is_(None))
    if cut is not None:
        q = q.where(FeedPost.created_at < cut)
    posts = list((await db.execute(q.order_by(FeedPost.created_at.desc()).limit(fetch))).scalars().all())
    if cut is None:                                     # pinned posts can be old — always on page one
        seen = {p.id for p in posts}
        posts += [p for p in (await db.execute(select(FeedPost).where(
            FeedPost.org_id == org, FeedPost.deleted_at.is_(None), FeedPost.pinned.is_(True)))).scalars().all() if p.id not in seen]
    kq = select(Kudos).where(Kudos.org_id == org, Kudos.status == "approved")
    if cut is not None:
        kq = kq.where(Kudos.created_at < cut)
    kudos = (await db.execute(kq.order_by(Kudos.created_at.desc()).limit(fetch))).scalars().all()
    items = [_post_item(p) for p in posts] + [_kudos_item(k, roster) for k in kudos]
    items = [i for i in items if F.visible_to(i, role, me_emp, me) and not F.suppressed_by_optout(i, prefs)
             and F.matches_filters(i, department, location, kind)]
    items = F.order_items(items, first_page=cut is None)[:limit]
    out = await _decorate(db, org, request, items)
    unpinned = [i for i in items if not i["pinned"]] or items
    nxt = unpinned[-1]["created_at"] if len(items) >= limit and unpinned else None
    depts = sorted({(e.get("department") or "").strip() for e in roster.values() if (e.get("department") or "").strip()})
    locs = sorted({(e.get("work_location") or "").strip() for e in roster.values() if (e.get("work_location") or "").strip()})
    return {"items": out, "next_before": nxt, "departments": depts, "locations": locs, "reactions": list(F.REACTIONS),
            "kinds": list(F.KIND_FILTERS), "can_moderate": F.can_moderate(role),
            "me": {"department": me.get("department"), "location": me.get("work_location")}}


class PostIn(BaseModel):
    kind: str = "post"
    body: str
    options: list[str] | None = None
    closes_at: datetime | None = None
    audience_department: str | None = None
    audience_location: str | None = None


@router.post("", status_code=201)
async def create_post(body: PostIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, role, me_emp, uid = _ident(request)
    if not F.can_post(role, me_emp, uid):
        raise HTTPException(403, "Sign in to post")
    roster = await load_roster(org)
    me = roster.get(me_emp or "") or {}
    ad = (body.audience_department or "").strip() or None
    al = (body.audience_location or "").strip() or None
    e = F.check_audience(role, me, ad, al)
    if e:
        raise HTTPException(403, e)
    e, options = F.validate_post(body.kind, body.body, body.options, body.closes_at, _utcnow())
    if e:
        raise HTTPException(422, e)
    closes = body.closes_at
    if closes is not None and closes.tzinfo is None:
        closes = closes.replace(tzinfo=timezone.utc)
    p = FeedPost(org_id=uuid.UUID(org), kind=body.kind, author_employee_id=uuid.UUID(me_emp) if me_emp else None,
                 author_user_id=uid, author_name=F.name_of(me) if me else (C.actor_of(request)), author_role=role,
                 body=body.body.strip(), department=me.get("department"), location=me.get("work_location"),
                 audience_department=ad, audience_location=al, poll_options=options or None,
                 poll_closes_at=closes if body.kind == "poll" else None, created_at=_utcnow())
    db.add(p)
    await db.commit()
    return (await _decorate(db, org, request, [_post_item(p)]))[0]


# ══════════════════════════ my opt-outs ═══════════════════════════════════════

class PrefsIn(BaseModel):
    birthday: bool | None = None
    anniversary: bool | None = None
    new_joiner: bool | None = None


def _prefs_out(p: FeedPrefs | None, can_edit: bool) -> dict:
    return {"birthday": bool(p and p.opt_out_birthday), "anniversary": bool(p and p.opt_out_anniversary),
            "new_joiner": bool(p and p.opt_out_new_joiner), "can_edit": can_edit,
            "meaning": "true = do NOT post this about me"}


@router.get("/prefs")
async def get_prefs(request: Request, db: AsyncSession = Depends(get_db)):
    org, _role, me_emp, _uid = _ident(request)
    if not me_emp:
        return _prefs_out(None, False)
    p = (await db.execute(select(FeedPrefs).where(FeedPrefs.org_id == org, FeedPrefs.employee_id == C.to_uuid(me_emp, "employee")))).scalar_one_or_none()
    return _prefs_out(p, True)


@router.put("/prefs")
async def put_prefs(body: PrefsIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, _role, me_emp, _uid = _ident(request)
    if not me_emp:
        raise HTTPException(400, "Your login is not linked to an employee record")
    eid = C.to_uuid(me_emp, "employee")
    p = (await db.execute(select(FeedPrefs).where(FeedPrefs.org_id == org, FeedPrefs.employee_id == eid))).scalar_one_or_none()
    if p is None:
        p = FeedPrefs(org_id=uuid.UUID(org), employee_id=eid, opt_out_birthday=False, opt_out_anniversary=False,
                      opt_out_new_joiner=False)
        db.add(p)
    for k in ("birthday", "anniversary", "new_joiner"):
        v = getattr(body, k)
        if v is not None:
            setattr(p, f"opt_out_{k}", bool(v))
    p.updated_at = _utcnow()
    await db.commit()
    return _prefs_out(p, True)


# ══════════════════════════ comments (static paths before /{item}) ═════════════

async def _load_comment(db, org: str, cid: str) -> FeedComment:
    c = (await db.execute(select(FeedComment).where(FeedComment.id == C.to_uuid(cid, "comment"), FeedComment.org_id == org,
                                                    FeedComment.deleted_at.is_(None)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Comment not found")
    return c


def _comment_out(c: FeedComment, request: Request) -> dict:
    me_emp, uid = C.emp_of(request), C.uid_of(request)
    hr = C.is_hr(request)
    o = {"id": str(c.id), "item_id": c.item_id, "author": {"employee_id": str(c.author_employee_id) if c.author_employee_id else None,
                                                          "name": c.author_name},
         "body": c.body, "created_at": C.iso(c.created_at), "hidden": bool(c.hidden),
         "can_delete": F.is_owner(str(c.author_employee_id) if c.author_employee_id else None, c.author_user_id, me_emp, uid),
         "can_moderate": hr}
    if hr and c.hidden:
        o["hidden_reason"], o["hidden_by"] = c.hidden_reason, c.hidden_by
    return o


@router.delete("/comments/{cid}")
async def delete_comment(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, _role, me_emp, uid = _ident(request)
    c = await _load_comment(db, org, cid)
    if not F.is_owner(str(c.author_employee_id) if c.author_employee_id else None, c.author_user_id, me_emp, uid):
        raise HTTPException(403, "You can delete only your own comment")
    c.deleted_at = _utcnow()
    await db.commit()
    return {"ok": True, "id": str(c.id)}


class ReasonIn(BaseModel):
    reason: str | None = None


@router.post("/comments/{cid}/{action}")
async def moderate_comment(cid: str, action: str, request: Request, body: ReasonIn | None = None,
                           db: AsyncSession = Depends(get_db)):
    if action not in ("hide", "unhide"):
        raise HTTPException(404, "Not found")
    org, role, _me, _uid = _ident(request)
    if not F.can_moderate(role):
        raise HTTPException(403, "Only HR can moderate the feed")
    c = await _load_comment(db, org, cid)
    reason = (body.reason if body else None) or ""
    if action == "hide":
        e = F.check_hide_reason(reason)
        if e:
            raise HTTPException(422, e)
        c.hidden, c.hidden_reason, c.hidden_by, c.hidden_at = True, reason.strip(), C.actor_of(request), _utcnow()
    else:
        c.hidden, c.hidden_reason, c.hidden_by, c.hidden_at = False, None, None, None
    await db.commit()
    await record_audit(request, f"feed.comment.{action}", entity_type="feed_comment", entity_id=str(c.id),
                       summary=f"Feed comment {action}" + (f": {reason.strip()}" if action == "hide" else ""))
    if action == "hide" and c.author_employee_id:
        await notify(org, employee_ids=[str(c.author_employee_id)], kind="general", email=False,
                     title="HR hid one of your feed comments", body=f"Reason: {reason.strip()}", link="/engage/feed")
    return _comment_out(c, request)


# ══════════════════════════ item routes ═══════════════════════════════════════

async def _load_item(db, org: str, request: Request, item_id: str) -> tuple[str, dict]:
    """→ (canonical item id, item dict) if the caller may see it; 404 otherwise."""
    parsed = F.parse_item_id(item_id)
    if not parsed:
        raise HTTPException(404, "Feed item not found")
    kind, pk = parsed
    if kind == "kudos":
        k = (await db.execute(select(Kudos).where(Kudos.id == pk, Kudos.org_id == org, Kudos.status == "approved"))).scalar_one_or_none()
        if not k:
            raise HTTPException(404, "Feed item not found")
        return f"kudos:{k.id}", _kudos_item(k, {})
    p = (await db.execute(select(FeedPost).where(FeedPost.id == pk, FeedPost.org_id == org, FeedPost.deleted_at.is_(None)))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Feed item not found")
    item = _post_item(p)
    roster = await load_roster(org) if (p.audience_department or p.audience_location) else {}
    me = roster.get(C.emp_of(request) or "") or {}
    if not F.visible_to(item, C.role_of(request), C.emp_of(request), me):
        raise HTTPException(404, "Feed item not found")
    return str(p.id), item


class ReactIn(BaseModel):
    emoji: str


@router.post("/{item_id}/react")
async def react(item_id: str, body: ReactIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    if body.emoji not in F.REACTIONS:
        raise HTTPException(422, f"Reaction must be one of {', '.join(F.REACTIONS)}")
    key = _me_key(request)
    if not key:
        raise HTTPException(403, "Sign in to react")
    iid, _item = await _load_item(db, org, request, item_id)
    r = (await db.execute(select(FeedReaction).where(FeedReaction.org_id == org, FeedReaction.item_id == iid,
                                                     FeedReaction.actor_key == key, FeedReaction.emoji == body.emoji))).scalar_one_or_none()
    if r is not None:
        await db.delete(r)
        on = False
    else:
        db.add(FeedReaction(org_id=uuid.UUID(org), item_id=iid, actor_key=key, emoji=body.emoji))
        on = True
    try:
        await db.commit()
    except IntegrityError:                              # a double click raced us — already reacted
        await db.rollback()
    rows = (await db.execute(select(FeedReaction).where(FeedReaction.org_id == org, FeedReaction.item_id == iid))).scalars().all()
    return {"item_id": iid, "emoji": body.emoji, "on": on,
            "reactions": F.reaction_summary([{"emoji": x.emoji, "actor_key": x.actor_key} for x in rows], key)}


@router.get("/{item_id}/comments")
async def list_comments(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    iid, _item = await _load_item(db, org, request, item_id)
    rows = (await db.execute(select(FeedComment).where(FeedComment.org_id == org, FeedComment.item_id == iid,
                                                       FeedComment.deleted_at.is_(None)).order_by(FeedComment.created_at))).scalars().all()
    hr = C.is_hr(request)
    rows = sorted([c for c in rows if hr or not c.hidden], key=lambda c: c.created_at or _utcnow())
    return {"item_id": iid, "comments": [_comment_out(c, request) for c in rows]}


class CommentIn(BaseModel):
    body: str


@router.post("/{item_id}/comments", status_code=201)
async def add_comment(item_id: str, body: CommentIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, role, me_emp, uid = _ident(request)
    if not F.can_post(role, me_emp, uid):
        raise HTTPException(403, "Sign in to comment")
    e = F.check_comment(body.body)
    if e:
        raise HTTPException(422, e)
    iid, item = await _load_item(db, org, request, item_id)
    roster = await load_roster(org)
    me = roster.get(me_emp or "")
    c = FeedComment(org_id=uuid.UUID(org), item_id=iid, author_employee_id=uuid.UUID(me_emp) if me_emp else None,
                    author_user_id=uid, author_name=F.name_of(me) if me else C.actor_of(request), body=body.body.strip(),
                    created_at=_utcnow())
    db.add(c)
    await db.commit()
    author = item.get("author_employee_id")
    if author and author != me_emp and item["kind"] in F.POST_KINDS:
        await notify(org, employee_ids=[author], kind="general", email=False,
                     title=f"{c.author_name} commented on your post", body=c.body[:140], link="/engage/feed")
    return _comment_out(c, request)


class VoteIn(BaseModel):
    option_id: str


@router.post("/{item_id}/vote")
async def vote(item_id: str, body: VoteIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    key = _me_key(request)
    if not key:
        raise HTTPException(403, "Sign in to vote")
    iid, item = await _load_item(db, org, request, item_id)
    p: FeedPost | None = item.get("_row")
    if p is None or p.kind != "poll":
        raise HTTPException(422, "This item is not a poll")
    votes = (await db.execute(select(FeedVote).where(FeedVote.org_id == org, FeedVote.post_id == p.id))).scalars().all()
    already = any(v.voter_key == key for v in votes)
    e = F.check_vote(p.poll_options or [], body.option_id, p.poll_closes_at, _utcnow(), already)
    if e:
        raise HTTPException(409 if ("already" in e or "closed" in e) else 422, e)
    v = FeedVote(org_id=uuid.UUID(org), post_id=p.id, voter_key=key, option_id=body.option_id)
    db.add(v)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "You have already voted in this poll")
    rows = [{"option_id": x.option_id, "voter_key": x.voter_key} for x in votes] + [{"option_id": v.option_id, "voter_key": key}]
    return {"item_id": iid, "poll": F.poll_results(p.poll_options or [], rows, key, p.poll_closes_at, _utcnow())}


@router.post("/{item_id}/{action}")
async def moderate_post(item_id: str, action: str, request: Request, body: ReasonIn | None = None,
                        db: AsyncSession = Depends(get_db)):
    if action not in ("hide", "unhide", "pin", "unpin"):
        raise HTTPException(404, "Not found")
    org, role, _me, _uid = _ident(request)
    if not F.can_moderate(role):
        raise HTTPException(403, "Only HR can moderate the feed")
    parsed = F.parse_item_id(item_id)
    if parsed and parsed[0] == "kudos":
        raise HTTPException(422, "Kudos are moderated on the Recognition screen")
    iid, item = await _load_item(db, org, request, item_id)
    p: FeedPost = item["_row"]
    reason = ((body.reason if body else None) or "").strip()
    actor = C.actor_of(request)
    if action == "hide":
        e = F.check_hide_reason(reason)
        if e:
            raise HTTPException(422, e)
        p.hidden, p.hidden_reason, p.hidden_by, p.hidden_at = True, reason, actor, _utcnow()
    elif action == "unhide":
        p.hidden, p.hidden_reason, p.hidden_by, p.hidden_at = False, None, None, None
    elif action == "pin":
        p.pinned, p.pinned_at, p.pinned_by = True, _utcnow(), actor
    else:
        p.pinned, p.pinned_at, p.pinned_by = False, None, None
    await db.commit()
    await record_audit(request, f"feed.post.{action}", entity_type="feed_post", entity_id=iid,
                       summary=f"Feed {p.kind} {action}" + (f": {reason}" if action == "hide" else ""))
    if action == "hide" and p.author_employee_id:
        await notify(org, employee_ids=[str(p.author_employee_id)], kind="general", email=False,
                     title="HR hid one of your feed posts", body=f"Reason: {reason}", link="/engage/feed")
    return (await _decorate(db, org, request, [_post_item(p)]))[0]


@router.delete("/{item_id}")
async def delete_post(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, _role, me_emp, uid = _ident(request)
    iid, item = await _load_item(db, org, request, item_id)
    if not F.can_delete(item["kind"], item["author_employee_id"], item["author_user_id"], me_emp, uid):
        raise HTTPException(403, "You can delete only your own post")
    p: FeedPost = item["_row"]
    p.deleted_at = _utcnow()
    await db.commit()
    return {"ok": True, "id": iid}
