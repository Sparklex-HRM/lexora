"""Hiring — the PUBLIC routes (no login). Registered as public at the gateway
under the single prefix `/hiring/public/`.

  GET  /hiring/public/careers/{org_code}                    open jobs of one org
  GET  /hiring/public/careers/{org_code}/{posting_id}       one job + the privacy text
  POST /hiring/public/careers/{org_code}/{posting_id}/apply apply (multipart, resume)
  GET  /hiring/public/offer/{token}                         the offer behind a link
  POST /hiring/public/offer/{token}/accept | /decline       answer it (single use)

Rules: the org is found from the code in the path (or from the offer token),
never from a header. Nothing else of the org is exposed — only its name, the
published jobs and its privacy text. The apply form is rate-limited, the resume
type is decided from its first bytes, and its size is capped.
"""
import sys
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
import hiring_integrations as ext  # noqa: E402
import hiring_logic as L  # noqa: E402
from shared.utils.docstore import pdf_active_content  # noqa: E402
from shared.utils.ratelimit import make_limiter  # noqa: E402  (Redis-shared when REDIS_URL is set)
from database import get_db  # noqa: E402
from hiring_common import now, uid  # noqa: E402
from hiring_models import (HireApplication, HireCandidate, HireOffer, HirePosting, HireRequisition,  # noqa: E402
                           HireStageHistory)
from hiring_offers import answer_offer  # noqa: E402
from hiring_routers import load_settings, read_capped, store_document  # noqa: E402

router = APIRouter(prefix="/hiring/public", tags=["hiring-public"])

NOT_FOUND = "This careers page is not available."
# Per caller address: 60 page reads a minute, 5 applications an hour, 20 offer-link tries in 10 minutes.
READS = make_limiter(60, 60, name="hiring_reads")
APPLIES = make_limiter(5, 3600, name="hiring_applies")
APPLIES_PER_JOB = make_limiter(300, 3600, name="hiring_applies_job")      # a flood on one job from many addresses
OFFER_TRIES = make_limiter(20, 600, name="hiring_offer")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")       # set by the gateway, not by the browser
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:64] or "unknown"


def _limit(limiter, key: str):
    if limiter.hit(key):
        raise HTTPException(429, "Too many requests. Please wait a little and try again.")


async def _org(db, org_code: str) -> tuple[dict, dict]:
    org = await ext.org_by_code(db, org_code)
    if not org:
        raise HTTPException(404, NOT_FOUND)
    s = await load_settings(db, org["id"])
    if not s["careers_page_enabled"]:
        raise HTTPException(404, NOT_FOUND)             # same answer as "no such org": nothing leaks
    return org, s


def _job(p: HirePosting, r: HireRequisition) -> dict:
    return {"id": str(p.id), "title": p.title, "department": r.department, "location": r.location,
            "employment_type": r.employment_type,
            "posted_on": p.published_at.date().isoformat() if p.published_at else None}


# D2: ?src= on the careers page tells us where the person came from. Known boards / channels only.
SRC_MAP = {"careers": ("careers_page", None), "referral": ("referral", "careers link"), "indeed": ("job_board", "Indeed"),
           "google": ("job_board", "Google for Jobs"), "google_jobs": ("job_board", "Google for Jobs"),
           "linkedin": ("job_board", "LinkedIn"), "agency": ("agency", "agency link")}


def source_of(src: str | None) -> tuple[str, str | None]:
    s = (src or "").strip().lower()[:40]
    if s in SRC_MAP:
        return SRC_MAP[s]
    if s.startswith("agency:") and s[7:].strip():
        return "agency", s[7:].strip()[:200]
    if s.startswith("board:") and s[6:].strip():
        return "job_board", s[6:].strip()[:200]
    return "careers_page", None


def _filters(jobs: list[dict]) -> dict:
    return {"departments": sorted({j["department"] for j in jobs if j.get("department")}),
            "locations": sorted({j["location"] for j in jobs if j.get("location")}),
            "employment_types": sorted({j["employment_type"] for j in jobs if j.get("employment_type")})}


def _match(j: dict, q: str | None, department: str | None, location: str | None, employment_type: str | None) -> bool:
    if department and (j.get("department") or "") != department:
        return False
    if location and (j.get("location") or "") != location:
        return False
    if employment_type and (j.get("employment_type") or "") != employment_type:
        return False
    if q:
        hay = " ".join(str(j.get(k) or "") for k in ("title", "department", "location")).lower()
        return all(w in hay for w in q.lower().split())
    return True


async def _live_postings(db, org_id: str, posting_id=None):
    q = (select(HirePosting, HireRequisition)
         .join(HireRequisition, HireRequisition.id == HirePosting.requisition_id)
         .where(HirePosting.org_id == org_id, HireRequisition.org_id == org_id,
                HirePosting.is_published.is_(True), HireRequisition.status == "open"))
    if posting_id is not None:
        q = q.where(HirePosting.id == posting_id)
    return (await db.execute(q.order_by(HirePosting.published_at.desc()))).all()


@router.get("/careers/{org_code}")
async def careers(org_code: str, request: Request, q: str | None = None, department: str | None = None,
                  location: str | None = None, employment_type: str | None = None, db: AsyncSession = Depends(get_db)):
    _limit(READS, _ip(request))
    org, s = await _org(db, org_code)
    from hiring_careers import branding
    jobs = [_job(p, r) for p, r in await _live_postings(db, org["id"])]
    return {"org_name": org["name"], "branding": await branding(db, org, s), "filters": _filters(jobs),
            "jobs": [j for j in jobs if _match(j, q, department, location, employment_type)], "total": len(jobs)}


@router.get("/careers/{org_code}/{posting_id}")
async def career_job(org_code: str, posting_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _limit(READS, _ip(request))
    org, s = await _org(db, org_code)
    rows = await _live_postings(db, org["id"], uid(posting_id, "Job"))
    if not rows:
        raise HTTPException(404, "This job is no longer open.")
    p, r = rows[0]
    import hiring_d2_logic as D
    from hiring_boards import board_rows, job_dict
    from hiring_careers import branding
    b = await branding(db, org, s)
    j = job_dict(p, r, org.get("code"))
    boards = await board_rows(db, org["id"], p.id)
    jsonld = D.google_jobs_jsonld(j, {"name": org["name"], "website": b.get("website"), "logo_url": b.get("logo_url")}, j["url"]) \
        if (boards.get("google_jobs") and boards["google_jobs"].enabled) else None
    return {"org_name": org["name"], **_job(p, r), "description": p.description, "requirements": p.requirements,
            "privacy_text": s["privacy_text"], "resume_max_mb": s["resume_max_mb"],
            "accepted_files": "PDF, DOCX, JPG or PNG", "branding": b, "jsonld": jsonld,
            "whatsapp_text": s["careers_whatsapp_text"], "captcha": D.captcha_info(), "reference": r.req_number}


@router.post("/careers/{org_code}/{posting_id}/apply", status_code=201)
async def apply(org_code: str, posting_id: str, request: Request,
                first_name: str = Form(...), last_name: str = Form(""), email: str = Form(...),
                phone: str = Form(""), city: str = Form(""), current_employer: str = Form(""),
                current_title: str = Form(""), consent: str = Form(""), whatsapp_opt_in: str = Form(""),
                src: str = Form(""), captcha_token: str = Form(""),
                resume: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    import hiring_d2_logic as D
    ip = _ip(request)
    _limit(APPLIES, ip)
    org, s = await _org(db, org_code)
    rows = await _live_postings(db, org["id"], uid(posting_id, "Job"))
    if not rows:
        raise HTTPException(404, "This job is no longer open.")
    p, r = rows[0]
    _limit(APPLIES_PER_JOB, str(p.id))
    if consent.lower() not in ("true", "1", "yes", "on"):
        raise HTTPException(422, "Please tick the consent box to apply.")
    if not await D.verify_captcha(captcha_token or None, ip):
        raise HTTPException(422, "The captcha check did not pass. Please try again.")
    first_name, email = first_name.strip()[:100], email.strip()[:200]
    if not first_name:
        raise HTTPException(422, "Your first name is required.")
    if "@" not in email or "." not in email.split("@")[-1] or " " in email:
        raise HTTPException(422, "Type a valid e-mail address.")
    content = await read_capped(resume, s["resume_max_mb"])
    try:                                                # type by magic bytes + size cap, before anything is written
        ext_, _ct = L.check_upload(content, s["resume_max_mb"])
    except L.RuleError as e:
        raise HTTPException(e.status, e.message)
    if ext_ == "pdf" and pdf_active_content(content):
        raise HTTPException(422, "This PDF contains scripts, launch actions or embedded files and cannot be "
                                 "uploaded. Print it to a plain PDF and upload that.")
    done = {"ok": True, "message": "Thank you. Your application has been received."}
    # Same e-mail OR phone already applied to THIS job: answer the same way, add nothing (no leak, no duplicate).
    e_norm, p_norm = L.norm_email(email), L.norm_phone(phone)
    dup_where = [HireCandidate.email_norm == e_norm]
    if p_norm:
        dup_where.append(HireCandidate.phone_norm == p_norm)
    from sqlalchemy import or_
    same = (await db.execute(
        select(HireApplication.id).join(HireCandidate, HireCandidate.id == HireApplication.candidate_id)
        .where(HireApplication.org_id == org["id"], HireApplication.requisition_id == r.id,
               or_(*dup_where), HireCandidate.is_active.is_(True)))).first()
    if same:
        return done
    # Always a NEW candidate record: a match with someone on file is shown to HR
    # as a "possible duplicate" — the public form never merges into an existing person.
    c = HireCandidate(org_id=uid(org["id"]), first_name=first_name, last_name=last_name.strip()[:100] or None,
                      email=email, email_norm=e_norm, phone=phone.strip()[:30] or None,
                      phone_norm=p_norm, city=city.strip()[:100] or None,
                      current_employer=current_employer.strip()[:200] or None,
                      current_title=current_title.strip()[:200] or None, tags=[],
                      consent_at=now(), consent_text=s["privacy_text"], created_by=None)
    db.add(c)
    await db.flush()
    source_type, source_detail = source_of(src)
    a = HireApplication(org_id=c.org_id, candidate_id=c.id, requisition_id=r.id, posting_id=p.id,
                        source_type=source_type, source_detail=source_detail, stage="applied", created_by=None)
    db.add(a)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return done
    await store_document(db, org["id"], c.id, a.id, "resume", resume.filename, content, s["resume_max_mb"], None)
    db.add(HireStageHistory(org_id=c.org_id, application_id=a.id, from_stage=None, to_stage="applied",
                            reason="Applied on the careers page" + (f" (via {source_detail})" if source_detail else ""), by_name="Candidate"))
    wa = whatsapp_opt_in.lower() in ("true", "1", "yes", "on") and bool(p_norm)
    from hiring_comms import send_event, set_prefs
    await set_prefs(db, org["id"], c.id, email_opt_in=True, whatsapp_opt_in=wa, consent_text=s["careers_whatsapp_text"] if wa else None)
    await db.flush()
    comms = await send_event(db, org["id"], c, a, "applied")
    await db.commit()
    await ext.notify(org["id"], roles=["hr"], title="New application", body=f"{p.title}",
                     link="/hiring/candidates", kind="info")
    mail = next((x for x in comms if x["channel"] == "email"), {})
    return {**done, "confirmation_email": mail.get("status") or "not_configured"}


# ── offer link ──────────────────────────────────────────────────────────────
STATE_MESSAGE = {
    "used": "This offer has already been answered.",
    "expired": "This offer link has expired. Please contact the HR team for a new one.",
    "closed": "This offer is no longer open.",
}


async def _offer_by_token(db, token: str, request: Request) -> HireOffer:
    _limit(OFFER_TRIES, _ip(request))
    o = (await db.execute(select(HireOffer).where(HireOffer.token_hash == L.hash_token(token)))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "This offer link is not valid.")
    return o


@router.get("/offer/{token}")
async def offer_view(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    o = await _offer_by_token(db, token, request)
    state = L.token_state(o.status, o.token_expires_at, o.token_used_at)
    org = await ext.org_by_id(db, str(o.org_id))
    c = (await db.execute(select(HireCandidate).where(HireCandidate.id == o.candidate_id))).scalar_one()
    out = {"state": state, "message": STATE_MESSAGE.get(state), "org_name": org.get("name"),
           "first_name": c.first_name, "answer": o.status if o.status in ("accepted", "declined") else None}
    if state == "ok":
        b = o.breakup or {}
        out.update({
            "designation": o.designation, "department": o.department, "location": o.location,
            "joining_date": o.joining_date.isoformat() if o.joining_date else None,
            "annual_ctc": float(o.annual_ctc), "country": org.get("country"),
            "monthly_gross": b.get("monthly_gross") if b.get("ok") else None,
            "breakup": b.get("lines") if b.get("ok") else [],
            "valid_until": o.token_expires_at.isoformat() if o.token_expires_at else None,
            "reporting_to": o.manager_name,
        })
    return out


async def _answer(token: str, accepted: bool, body: dict, request: Request, db: AsyncSession):
    o = await _offer_by_token(db, token, request)
    state = L.token_state(o.status, o.token_expires_at, o.token_used_at)
    if state != "ok":
        raise HTTPException(410 if state == "expired" else 409, STATE_MESSAGE[state])
    signed = (body.get("signed_name") or "").strip()
    reason = (body.get("reason") or "").strip()
    if accepted and len(signed) < 2:
        raise HTTPException(422, "Type your full name to accept.")
    try:
        o = await answer_offer(db, o, accepted, "link", signed_name=signed, reason=reason,
                               token_hash=L.hash_token(token))
    except HTTPException as e:
        if e.status_code == 409:                        # lost the race: the link was used a moment ago
            raise HTTPException(409, STATE_MESSAGE["used"])
        raise
    try:                                                   # G3: outbound webhooks (best-effort)
        from shared.utils.events import emit as _g3_emit
        await _g3_emit(str(o.org_id), "candidate.offer.accepted" if accepted else "candidate.offer.declined",
                       {"offer_id": str(o.id), "status": o.status, "signed_name": signed if accepted else None,
                        "reason": reason if not accepted else None})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "answer": o.status,
            "message": ("Thank you. Your acceptance is recorded. You will receive an e-mail to complete your joining details."
                        if accepted else "Your answer is recorded. Thank you for letting us know.")}


@router.post("/offer/{token}/accept")
async def offer_accept(token: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    return await _answer(token, True, body or {}, request, db)


@router.post("/offer/{token}/decline")
async def offer_decline(token: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    return await _answer(token, False, body or {}, request, db)
