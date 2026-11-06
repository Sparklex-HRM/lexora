"""Hiring D2 — job-board publishing (signed-in routes).

  Indeed          a public XML feed per org  (/hiring/public/careers/{code}/feed/indeed.xml)
  Google for Jobs JobPosting JSON-LD on each public job page
  LinkedIn        G6: Basic Jobs XML feed per org (/hiring/public/careers/{code}/feed/linkedin.xml) — needs the
                  LinkedIn company id + poster e-mail and LinkedIn's onboarding of the feed; Export still available

  GET  /hiring/postings/{id}/boards              per-board state for one posting
  PUT  /hiring/postings/{id}/boards/{board}      {enabled} — publish toggle + published-at tracking
  GET  /hiring/postings/{id}/export/{board}      copy-ready text for linkedin
  GET  /hiring/postings/export.csv               every live posting as CSV
  GET  /hiring/boards                            summary: feed URL, counts per board
"""
import os
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402

import hiring_d2_logic as D  # noqa: E402
import hiring_integrations as ext  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import get_actor, now, one, uid  # noqa: E402
from hiring_d2_models import BOARDS, HirePostingBoard  # noqa: E402
from hiring_logic import merge_settings  # noqa: E402
from hiring_models import HirePosting, HireRequisition, HireSettings  # noqa: E402

router = APIRouter(prefix="/hiring", tags=["hiring-boards"])


def app_base() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def public_api_base() -> str:
    """Where the outside world reaches the API (feeds, logo, OAuth callbacks)."""
    return (os.getenv("LEXORA_PUBLIC_API_URL") or f"{app_base()}/api").rstrip("/")


def careers_url(org_code: str | None) -> str:
    return f"{app_base()}/careers/{org_code}" if org_code else ""


def feed_url(org_code: str | None, board: str = "indeed") -> str:
    return f"{public_api_base()}/hiring/public/careers/{org_code}/feed/{board}.xml" if org_code else ""


async def hiring_settings(db: AsyncSession, org) -> dict:
    row = (await db.execute(select(HireSettings).where(HireSettings.org_id == uid(org)))).scalar_one_or_none()
    return merge_settings(row.data if row else None)


def job_dict(p: HirePosting, r: HireRequisition, org_code: str | None, src: str | None = None) -> dict:
    city, state = D.split_location(r.location)
    url = f"{careers_url(org_code)}/{p.id}" + (f"?src={src}" if src else "")
    # No salary here: the requisition budget is an INTERNAL figure (it drives the above-budget offer
    # approval) and must never reach the public JSON-LD, the Indeed feed or the board exports.
    return {"id": str(p.id), "title": p.title, "description": p.description, "requirements": p.requirements,
            "reference": r.req_number, "department": r.department, "location": r.location, "city": city, "state": state,
            "country": "CA", "employment_type": r.employment_type, "published_at": p.published_at, "url": url,
            "salary_min": None, "salary_max": None}


async def live_jobs(db: AsyncSession, org, org_code: str | None, board: str | None = None) -> list[dict]:
    """Live postings (published + requisition open); with `board`, only the ones switched on for it."""
    q = (select(HirePosting, HireRequisition).join(HireRequisition, HireRequisition.id == HirePosting.requisition_id)
         .where(HirePosting.org_id == org, HirePosting.is_published.is_(True), HireRequisition.status == "open"))
    rows = (await db.execute(q.order_by(HirePosting.published_at.desc()))).all()
    if board:
        on = {str(b.posting_id) for b in (await db.execute(select(HirePostingBoard).where(
            HirePostingBoard.org_id == org, HirePostingBoard.board == board, HirePostingBoard.enabled.is_(True)))).scalars().all()}
        rows = [(p, r) for p, r in rows if str(p.id) in on]
    return [job_dict(p, r, org_code, src=board) for p, r in rows]


async def board_rows(db: AsyncSession, org, posting_id) -> dict[str, HirePostingBoard]:
    rows = (await db.execute(select(HirePostingBoard).where(
        HirePostingBoard.org_id == org, HirePostingBoard.posting_id == posting_id))).scalars().all()
    return {b.board: b for b in rows}


async def boards_out(db: AsyncSession, org, p: HirePosting, r: HireRequisition, org_code: str | None) -> dict:
    rows = await board_rows(db, org, p.id)
    live = bool(p.is_published and r.status == "open")
    settings = await hiring_settings(db, org)
    out = []
    for b in BOARDS:
        row = rows.get(b)
        st = D.board_state(b, bool(row and row.enabled), live, settings)
        if b in D.PARTNER_ONLY:
            st["feed_url"] = feed_url(org_code, b)
        st.update({"published_at": row.published_at.isoformat() if row and row.published_at else None,
                   "unpublished_at": row.unpublished_at.isoformat() if row and row.unpublished_at else None})
        out.append(st)
    j = job_dict(p, r, org_code)
    for st in out:
        if st["board"] == "linkedin":
            st["issues"] = D.linkedin_issues(j)
    org = await ext.org_by_id(db, str(org))
    return {"posting_id": str(p.id), "live": live, "boards": out, "feed_url": feed_url(org_code),
            "job_url": j["url"], "jsonld": D.google_jobs_jsonld(j, {"name": org.get("name")}, j["url"])}


@router.get("/postings/{posting_id}/boards")
async def get_boards(posting_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage job boards.")
    p = await one(db, HirePosting, posting_id, actor.org, "Posting")
    r = await one(db, HireRequisition, p.requisition_id, actor.org, "Requisition")
    org = await ext.org_by_id(db, actor.org)
    return await boards_out(db, actor.org, p, r, org.get("code"))


@router.put("/postings/{posting_id}/boards/{board}")
async def set_board(posting_id: str, board: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage job boards.")
    if board not in BOARDS:
        raise HTTPException(404, "Unknown job board")
    p = await one(db, HirePosting, posting_id, actor.org, "Posting")
    r = await one(db, HireRequisition, p.requisition_id, actor.org, "Requisition")
    want = bool(body.get("enabled"))
    if want and board in D.PARTNER_ONLY and not D.partner_configured(board, await hiring_settings(db, actor.org)):
        raise HTTPException(409, f"{D.BOARDS[board][0]} is a partner feed and the partner integration is not configured: add "
                                 f"{D.PARTNER_SETUP_TEXT[board]} first. Meanwhile use Export and post it yourself; "
                                 "the apply link still tracks the source.")
    if want and not p.is_published:
        raise HTTPException(409, "Publish the posting on the careers page first.")
    row = (await board_rows(db, actor.org, p.id)).get(board)
    if not row:
        row = HirePostingBoard(org_id=uid(actor.org), posting_id=p.id, board=board, enabled=False)
        db.add(row)
    if row.enabled != want:
        row.enabled = want
        if want:
            row.published_at, row.unpublished_at = now(), None
        else:
            row.unpublished_at = now()
    row.updated_by, row.updated_at = actor.user_id, now()
    await db.commit()
    await record_audit(request, "hiring.posting.board", entity_type="hire_posting", entity_id=str(p.id),
                       summary=f"{D.BOARDS[board][0]} {'on' if want else 'off'} for {p.title}", meta={"board": board, "enabled": want})
    org = await ext.org_by_id(db, actor.org)
    return await boards_out(db, actor.org, p, r, org.get("code"))


@router.get("/postings/export.csv")
async def export_all_csv(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can export postings.")
    org = await ext.org_by_id(db, actor.org)
    jobs = await live_jobs(db, actor.org, org.get("code"), board=None)
    return Response(content=D.export_csv(jobs, org.get("name") or ""), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="job-postings-export.csv"'})


@router.get("/postings/{posting_id}/export/{board}", response_class=PlainTextResponse)
async def export_text(posting_id: str, board: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can export postings.")
    if board not in D.PARTNER_ONLY:
        raise HTTPException(404, "Export is for LinkedIn (partner-only board).")
    p = await one(db, HirePosting, posting_id, actor.org, "Posting")
    r = await one(db, HireRequisition, p.requisition_id, actor.org, "Requisition")
    org = await ext.org_by_id(db, actor.org)
    j = job_dict(p, r, org.get("code"), src=board)
    return D.export_text(board, j, org.get("name") or "", j["url"])


@router.get("/boards")
async def boards_summary(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage job boards.")
    org = await ext.org_by_id(db, actor.org)
    counts = dict((b, n) for b, n in (await db.execute(
        select(HirePostingBoard.board, func.count()).where(HirePostingBoard.org_id == actor.org, HirePostingBoard.enabled.is_(True))
        .group_by(HirePostingBoard.board))).all())
    settings = await hiring_settings(db, actor.org)
    return {"feed_url": feed_url(org.get("code")), "careers_url": careers_url(org.get("code")),
            "boards": [{"board": b, "label": D.BOARDS[b][0], "how": D.BOARDS[b][1], "docs": D.BOARDS[b][2],
                        "partner_only": b in D.PARTNER_ONLY, "enabled_postings": counts.get(b, 0),
                        "feed_url": feed_url(org.get("code"), b) if b in ("indeed", "linkedin") else None,
                        "configured": D.partner_configured(b, settings) if b in D.PARTNER_ONLY else True,
                        "needs": D.PARTNER_SETUP_TEXT.get(b)} for b in BOARDS]}
