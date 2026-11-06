"""Hiring D2 — more PUBLIC routes (no login) under the same `/hiring/public/` prefix the
gateway already opens. Registered BEFORE hiring_public so `/careers/{org}/logo` and
`/careers/{org}/feed/indeed.xml` are not swallowed by `/careers/{org}/{posting_id}`.

  GET  /hiring/public/careers/{org_code}/logo                 the company logo (org_logos, read-only)
  GET  /hiring/public/careers/{org_code}/feed/indeed.xml      Indeed XML feed of the postings switched on for Indeed
  GET  /hiring/public/careers/{org_code}/feed/linkedin.xml    G6: LinkedIn Basic Jobs XML (404 until company id + poster e-mail set)
  GET  /hiring/public/interview/{token}                       the slots offered to a candidate
  POST /hiring/public/interview/{token}/pick                  {slot_index, message} — single use
  GET  /hiring/public/calendar/{provider}/callback            OAuth return (state is HMAC-signed, 15 min)

`hiring_d2_init(engine)` adds the one new column on an existing table (hire_documents.file_path).
"""
import logging
import os
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
import hiring_d2_logic as D  # noqa: E402
from shared.utils.docstore import image_ext, sniff_image  # noqa: E402
from shared.utils.email import dangerous_url  # noqa: E402
import hiring_integrations as ext  # noqa: E402
from database import get_db  # noqa: E402
from hiring_boards import careers_url, feed_url, live_jobs, public_api_base  # noqa: E402
from hiring_common import now  # noqa: E402
from hiring_d2_models import HireInterviewSlots  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HireInterview, HireRequisition  # noqa: E402
from hiring_public import APPLIES, READS, _ip, _limit, _org  # noqa: E402
from hiring_routers import load_settings  # noqa: E402

log = logging.getLogger("lexora.hiring.careers")
router = APIRouter(prefix="/hiring/public", tags=["hiring-public-d2"])


async def hiring_d2_init(engine) -> None:
    """Additive DDL that create_all cannot do: one new column on hire_documents."""
    try:
        if engine.dialect.name != "postgresql":
            return
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '15s'"))   # boot DDL never waits forever on a lock
            await conn.execute(text("ALTER TABLE hire_documents ADD COLUMN IF NOT EXISTS file_path VARCHAR(400)"))
    except Exception as e:                                   # noqa: BLE001
        log.warning("hiring_d2_init: %s", e)


# ── branding ────────────────────────────────────────────────────────────────
async def has_logo(db, org_id: str) -> bool:
    try:
        return (await db.execute(text("SELECT 1 FROM org_logos WHERE org_id = :o AND content_type <> 'image/svg+xml'"),
                                 {"o": str(org_id)})).first() is not None
    except Exception:                                        # noqa: BLE001
        await db.rollback()
        return False


async def branding(db, org: dict, s: dict) -> dict:
    code = org.get("code") or ""
    website = ""
    try:
        from shared.utils.org_settings import get_setting
        website = await get_setting(org["id"], "company.website", "") or ""
    except Exception:                                        # noqa: BLE001
        website = ""
    return {"org_name": org.get("name"), "tagline": s["careers_tagline"], "about": s["careers_about"],
            "primary_color": s["careers_primary_color"],
            "logo_url": f"{public_api_base()}/hiring/public/careers/{code}/logo" if code and await has_logo(db, org["id"]) else None,
            "website": "" if dangerous_url(website) else website, "careers_url": careers_url(code)}


@router.get("/careers/{org_code}/logo")
async def logo(org_code: str, request: Request, db: AsyncSession = Depends(get_db)):
    _limit(READS, _ip(request))
    org, _ = await _org(db, org_code)
    row = (await db.execute(text("SELECT content_type, data FROM org_logos WHERE org_id = :o"), {"o": org["id"]})).first()
    if not row:
        raise HTTPException(404, "No logo")
    data = bytes(row[1])
    kind = sniff_image(data)            # served only when the BYTES are PNG / JPEG / WEBP (old SVG rows → 404)
    if not kind or (row[0] or "").lower() == "image/svg+xml":
        raise HTTPException(404, "No logo")
    return Response(content=data, media_type=kind, headers={
        "Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": f'inline; filename="logo.{image_ext(kind)}"'})


# ── Indeed XML feed ─────────────────────────────────────────────────────────
@router.get("/careers/{org_code}/feed/indeed.xml")
async def indeed_feed(org_code: str, request: Request, db: AsyncSession = Depends(get_db)):
    _limit(READS, _ip(request))
    org, _ = await _org(db, org_code)
    jobs = await live_jobs(db, org["id"], org.get("code"), board="indeed")
    xml = D.indeed_xml(org.get("name") or "", careers_url(org.get("code")), jobs)
    return Response(content=xml, media_type="application/xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=900", "X-Feed": feed_url(org.get("code"))})


# ── G6: partner feed (LinkedIn) ────────────────────────────────────
@router.get("/careers/{org_code}/feed/linkedin.xml")
async def linkedin_feed(org_code: str, request: Request, db: AsyncSession = Depends(get_db)):
    _limit(READS, _ip(request))
    org, s = await _org(db, org_code)
    if not D.partner_configured("linkedin", s):
        raise HTTPException(404, "This company has not set up a LinkedIn feed.")
    jobs = await live_jobs(db, org["id"], org.get("code"), board="linkedin")
    xml = D.linkedin_xml(org.get("name") or "", careers_url(org.get("code")), jobs, s["linkedin_company_id"], s["linkedin_poster_email"])
    return Response(content=xml, media_type="application/xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=900", "X-Feed": feed_url(org.get("code"), "linkedin")})




# ── candidate self-schedule ─────────────────────────────────────────────────
PICK_MESSAGE = {"picked": "You have already picked a time for this interview.",
                "expired": "This link has expired. Please contact the hiring team for new slots.",
                "closed": "This link is no longer open."}


async def _slots_by_token(db, token: str, request: Request) -> HireInterviewSlots:
    _limit(READS, _ip(request))
    row = (await db.execute(select(HireInterviewSlots).where(HireInterviewSlots.token_hash == D.hash_token(token)))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "This link is not valid.")
    return row


@router.get("/interview/{token}")
async def interview_slots(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    row = await _slots_by_token(db, token, request)
    state = D.slot_link_state(row.status, row.token_expires_at)
    iv = (await db.execute(select(HireInterview).where(HireInterview.id == row.interview_id))).scalar_one_or_none()
    a = (await db.execute(select(HireApplication).where(HireApplication.id == row.application_id))).scalar_one_or_none()
    c = (await db.execute(select(HireCandidate).where(HireCandidate.id == a.candidate_id))).scalar_one_or_none() if a else None
    r = (await db.execute(select(HireRequisition).where(HireRequisition.id == a.requisition_id))).scalar_one_or_none() if a else None
    org = await ext.org_by_id(db, str(row.org_id))
    s = await load_settings(db, str(row.org_id))
    out = {"state": state, "message": PICK_MESSAGE.get(state), "org_name": org.get("name"), "primary_color": s["careers_primary_color"],
           "first_name": c.first_name if c else "", "job_title": r.designation if r else "", "round_name": iv.round_name if iv else "",
           "mode": iv.mode if iv else None, "duration_min": row.duration_min, "timezone": row.timezone,
           "picked": row.picked_slot, "scheduled_at": iv.scheduled_at.isoformat() if iv and iv.scheduled_at else None}
    if state == "ok":
        out["slots"] = [{"index": i, **s_} for i, s_ in enumerate(row.slots or []) if D._dt(s_["start"]) > now()]
        out["valid_until"] = row.token_expires_at.isoformat() if row.token_expires_at else None
    return out


@router.post("/interview/{token}/pick")
async def pick(token: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    _limit(APPLIES, _ip(request))
    row = await _slots_by_token(db, token, request)
    state = D.slot_link_state(row.status, row.token_expires_at)
    if state != "ok":
        raise HTTPException(410 if state == "expired" else 409, PICK_MESSAGE[state])
    body = body or {}
    from hiring_scheduling import apply_pick
    try:
        iv = await apply_pick(db, row, body.get("slot_index"), body.get("message"))
    except D.RuleError as e:
        raise HTTPException(e.status, e.message)
    except HTTPException as e:
        if e.status_code == 409:
            raise HTTPException(409, PICK_MESSAGE["picked"])
        raise
    from hiring_comms import fmt_when
    return {"ok": True, "scheduled_at": iv.scheduled_at.isoformat(), "when": fmt_when(iv.scheduled_at, row.timezone),
            "place": None if dangerous_url(iv.place) else iv.place, "mode": iv.mode,
            "message": "Thank you. Your interview time is confirmed. You will receive the details by e-mail if e-mail is set up."}


# ── calendar OAuth return ───────────────────────────────────────────────────
def _settings_url(provider: str, result: str, detail: str = "") -> str:
    base = os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    from urllib.parse import urlencode
    return f"{base}/hiring/settings?" + urlencode({"calendar": provider, "result": result, **({"detail": detail[:200]} if detail else {})})


@router.get("/calendar/{provider}/callback")
async def calendar_callback(provider: str, request: Request, code: str | None = None, state: str | None = None,
                            error: str | None = None, error_description: str | None = None, db: AsyncSession = Depends(get_db)):
    """The organiser's browser lands here after consenting at Google / Microsoft."""
    _limit(READS, _ip(request))
    from shared.utils.providers.calendar_base import calendar_adapter
    from hiring_scheduling import redirect_uri, store_tokens
    adapter = calendar_adapter(provider)
    if not adapter:
        raise HTTPException(404, "Unknown calendar provider")
    st = D.verify_state(state)
    if not st or st.get("provider") != provider:
        return RedirectResponse(_settings_url(provider, "error", "The sign-in state was invalid or expired. Try again."), status_code=302)
    if error or not code:
        return RedirectResponse(_settings_url(provider, "error", error_description or error or "No code returned"), status_code=302)
    tokens = await adapter.exchange_code(code, redirect_uri(provider), org_id=st["org"])
    if not tokens.ok:
        return RedirectResponse(_settings_url(provider, "error", tokens.error or "Token exchange failed"), status_code=302)
    await store_tokens(db, st["org"], provider, tokens, st.get("user") or "", st.get("name") or "")
    await db.commit()
    return RedirectResponse(_settings_url(provider, "connected"), status_code=302)
