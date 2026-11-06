"""Hiring G6 (2026-10-23) — résumé parsing, job-description drafts, advisory fit scores (/hiring/ai).

  GET  /hiring/ai/status                                  which provider would run (rules / AI platform)
  POST /hiring/ai/parse-file                              HR: multipart résumé → fields to PRE-FILL "Add candidate" (nothing saved)
  POST /hiring/ai/candidates/{cid}/parse                  HR: parse the candidate's latest résumé → profile (status parsed)
  GET  /hiring/ai/candidates/{cid}/profile                HR / hiring manager / panel (CTC: HR only)
  PUT  /hiring/ai/candidates/{cid}/profile                HR: edit + confirm the profile
  POST /hiring/ai/jd                                      HR / hiring manager: draft a job description (not saved, not published)
  POST /hiring/ai/applications/{app_id}/match             HR / hiring manager: advisory fit score with the working
  GET  /hiring/ai/applications/{app_id}/match             latest score
  GET  /hiring/ai/requisitions/{req_id}/ranking           HR / hiring manager: applications by advisory score

A person decides. No route here moves, rejects or e-mails a candidate.
"""
import json
import sys

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402

import hiring_ai_logic as A  # noqa: E402
import hiring_ai_provider as PV  # noqa: E402
import hiring_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from hiring_ai_models import HireCandidateProfile, HireMatchScore  # noqa: E402
from hiring_common import fail, get_actor, now, one, ser, uid  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HireDocument, HirePosting, HireRequisition  # noqa: E402
from hiring_routers import _app_guard, _is_hm, _panel_app_ids, _visible_req_ids, load_settings, read_capped  # noqa: E402

router = APIRouter(prefix="/hiring/ai", tags=["hiring-ai"])

SYS_PARSE = ("You extract facts from a de-identified résumé for a recruiter. Reply with JSON only: "
             '{"skills": [lower-case skill names, max 40], "experience_years": number or null, '
             '"current_title": string or null, "summary": "two plain sentences about the work done"}. '
             "Do not guess personal details. Do not rate the person.")
SYS_JD = ("You write clear, inclusive job descriptions for Canadian employers. No gendered words, no age limits, "
          "no 'native speaker', no college names. Reply with JSON only: "
          '{"description": "plain text, short paragraphs and - bullets", "requirements": "plain text - bullets", "skills": [max 12]}.')
SYS_MATCH = ("You compare a de-identified candidate profile with a job for a recruiter. Use only skills, experience, "
             "qualification level and notice period. Never mention or infer gender, age, religion, caste, region or college. "
             'Reply with JSON only: {"strengths": [max 4 short strings], "gaps": [max 4], "summary": "one sentence"}. '
             "Do not recommend hiring or rejecting.")


def _need_hr(actor):
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can do this.")


async def _ai(org: str, settings: dict, feature: str, system: str, prompt: str, max_tokens: int) -> tuple[dict | None, str | None]:
    text, note = await PV.pick(settings).complete(org, feature, system, prompt, max_tokens)
    if text is None:
        return None, note
    parsed = A.parse_json_reply(text)
    return (parsed, None) if parsed is not None else (None, "The AI answer could not be read; rules were used.")


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role == "manager"):
        raise HTTPException(403, "Only HR or a hiring manager can use hiring assist.")
    s = await load_settings(db, actor.org)
    return {"ai_assist": bool(s.get("ai_assist", True)), "provider": PV.pick(s).name,
            "note": ("Rules always run (no key needed). With an AI key on the AI settings page, the AI platform adds skills, "
                     "a summary and a second opinion; names, contacts and money are removed before anything is sent."),
            "advisory": "Scores are advisory. A person decides; nothing is rejected automatically."}


def _profile_out(p: HireCandidateProfile | None, is_hr: bool) -> dict | None:
    if p is None:
        return None
    d = ser(p)
    if not is_hr:                                  # CTC is salary data: HR only
        d.pop("current_ctc", None)
        d.pop("expected_ctc", None)
        d["evidence"] = {k: v for k, v in (d.get("evidence") or {}).items() if "ctc" not in k}
    return d


async def _parse_bytes(org: str, settings: dict, content: bytes) -> dict:
    try:
        ext_, _ = L.check_upload(content, settings["resume_max_mb"])
    except L.RuleError as e:
        fail(e)
    text = A.extract_text(content, ext_)
    parsed = A.parse_resume(text)
    parsed["method"] = "rules"
    parsed["note"] = None
    if text.strip():
        ai, note = await _ai(org, settings, "hiring.resume_parse", SYS_PARSE, A.strip_for_ai(text, parsed), 600)
        if ai:
            parsed = A.merge_ai(parsed, ai)
            parsed["method"] = "rules+ai"
        parsed["note"] = note
    if ext_ in ("png", "jpg"):
        parsed["warnings"].append("This is an image; there is no text to read. Type the details in.")
    return parsed


@router.post("/parse-file")
async def parse_file(request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor)
    s = await load_settings(db, actor.org)
    content = await read_capped(file, s["resume_max_mb"])
    parsed = await _parse_bytes(actor.org, s, content)
    return {"fields": parsed, "saved": False,
            "advice": "Check every field against the résumé before saving. Nothing has been saved yet."}


@router.post("/candidates/{cand_id}/parse")
async def parse_candidate(cand_id: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor)
    c = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    s = await load_settings(db, actor.org)
    q = select(HireDocument).where(HireDocument.org_id == actor.org, HireDocument.candidate_id == c.id)
    if (body or {}).get("document_id"):
        q = q.where(HireDocument.id == uid(body["document_id"], "Document"))
    docs = (await db.execute(q.order_by(HireDocument.uploaded_at.desc()))).scalars().all()
    doc = next((d for d in docs if d.kind == "resume"), None) or (docs[0] if docs else None)
    if doc is None:
        raise HTTPException(409, "This candidate has no résumé uploaded yet. Add it under Documents first.")
    parsed = await _parse_bytes(actor.org, s, doc.content)
    p = (await db.execute(select(HireCandidateProfile).where(HireCandidateProfile.org_id == actor.org,
                                                             HireCandidateProfile.candidate_id == c.id))).scalar_one_or_none()
    if p is None:
        p = HireCandidateProfile(org_id=uid(actor.org), candidate_id=c.id)
        db.add(p)
    p.document_id, p.skills, p.experience_years, p.education = doc.id, parsed["skills"], parsed["experience_years"], parsed["education"]
    p.notice_period_days, p.current_ctc, p.expected_ctc = parsed["notice_period_days"], parsed["current_ctc"], parsed["expected_ctc"]
    p.current_title, p.summary, p.evidence = parsed.get("current_title"), parsed.get("summary"), parsed.get("evidence") or {}
    p.method, p.status, p.parsed_at, p.confirmed_by, p.confirmed_at = parsed["method"], "parsed", now(), None, None
    await db.commit()
    await record_audit(request, "hiring.ai.parse", entity_type="hire_candidate", entity_id=str(c.id),
                       summary="Résumé parsed (check before use)", meta={"method": parsed["method"], "skills": len(parsed["skills"])})
    return {"profile": _profile_out(p, True), "contacts_found": {k: parsed.get(k) for k in ("first_name", "last_name", "email", "phone", "city", "current_employer")},
            "warnings": parsed["warnings"], "note": parsed.get("note")}


async def _can_read_candidate(db, actor, cand_id) -> bool:
    if actor.is_hr:
        return True
    apps = (await db.execute(select(HireApplication).where(HireApplication.org_id == actor.org,
                                                           HireApplication.candidate_id == cand_id))).scalars().all()
    visible = await _visible_req_ids(db, actor) or set()
    mine = await _panel_app_ids(db, actor)
    return any(str(a.requisition_id) in visible or str(a.id) in mine for a in apps)


@router.get("/candidates/{cand_id}/profile")
async def get_profile(cand_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    c = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    if not await _can_read_candidate(db, actor, c.id):
        raise HTTPException(404, "Candidate not found")
    p = (await db.execute(select(HireCandidateProfile).where(HireCandidateProfile.org_id == actor.org,
                                                             HireCandidateProfile.candidate_id == c.id))).scalar_one_or_none()
    return {"profile": _profile_out(p, actor.is_hr)}


@router.put("/candidates/{cand_id}/profile")
async def put_profile(cand_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor)
    c = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    p = (await db.execute(select(HireCandidateProfile).where(HireCandidateProfile.org_id == actor.org,
                                                             HireCandidateProfile.candidate_id == c.id))).scalar_one_or_none()
    if p is None:
        p = HireCandidateProfile(org_id=uid(actor.org), candidate_id=c.id, method="manual", evidence={})
        db.add(p)
    if "skills" in body:
        raw = body["skills"] if isinstance(body["skills"], list) else str(body["skills"] or "").split(",")
        p.skills = sorted({str(x).strip().lower()[:60] for x in raw if str(x).strip()})[:60]

    def _n(k, lo, hi, cast=float):
        if k not in body:
            return
        v = body[k]
        if v in (None, ""):
            setattr(p, k, None)
            return
        try:
            v = cast(v)
        except (TypeError, ValueError):
            raise HTTPException(422, f"{k.replace('_', ' ')} must be a number.")
        if not lo <= v <= hi:
            raise HTTPException(422, f"{k.replace('_', ' ')} must be between {lo} and {hi}.")
        setattr(p, k, v)
    _n("experience_years", 0, 60)
    _n("notice_period_days", 0, 365, int)
    _n("current_ctc", 0, 1e9)
    _n("expected_ctc", 0, 1e9)
    if "current_title" in body:
        p.current_title = str(body.get("current_title") or "").strip()[:200] or None
    if "summary" in body:
        p.summary = str(body.get("summary") or "").strip()[:800] or None
    if "education" in body and isinstance(body["education"], list):
        p.education = [{"degree": str(e.get("degree") or "")[:80], "level": int(e.get("level") or 0), "line": str(e.get("line") or "")[:160]}
                       for e in body["education"] if isinstance(e, dict) and e.get("degree")][:10]
    if body.get("confirm"):
        p.status, p.confirmed_by, p.confirmed_at = "confirmed", actor.user_id, now()
    await db.commit()
    return {"profile": _profile_out(p, True)}


@router.post("/jd")
async def draft_jd(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    role = dict(body or {})
    if role.get("requisition_id"):
        r = await one(db, HireRequisition, role["requisition_id"], actor.org, "Requisition")
        if not (actor.is_hr or _is_hm(actor, r) or r.created_by == actor.user_id):
            raise HTTPException(404, "Requisition not found")
        role = {"designation": r.designation, "department": r.department, "location": r.location,
                "employment_type": r.employment_type, **{k: v for k, v in role.items() if v not in (None, "", [])}}
    elif not (actor.is_hr or actor.role == "manager"):
        raise HTTPException(403, "Only HR or a hiring manager can draft a job description.")
    if not str(role.get("designation") or "").strip():
        raise HTTPException(422, "Give the job title (designation).")
    if isinstance(role.get("skills"), str):
        role["skills"] = [x for x in role["skills"].split(",")]
    if isinstance(role.get("responsibilities"), str):
        role["responsibilities"] = [x.strip("-• ").strip() for x in role["responsibilities"].split("\n") if x.strip()]
    draft = A.draft_jd(role)
    s = await load_settings(db, actor.org)
    facts = {k: role.get(k) for k in ("designation", "department", "location", "employment_type", "experience_min", "experience_max",
                                     "skills", "responsibilities", "about")}
    ai, note = await _ai(actor.org, s, "hiring.jd_draft", SYS_JD, json.dumps(facts, default=str)[:6000], 900)
    if ai and ai.get("description"):
        draft = {"description": str(ai["description"])[:6000], "requirements": str(ai.get("requirements") or draft["requirements"])[:4000],
                 "skills": sorted({str(x).strip().lower()[:60] for x in (ai.get("skills") or draft["skills"]) if str(x).strip()})[:12],
                 "method": "ai", "rules_draft": draft}
    draft["note"] = note
    draft["advice"] = "A draft only. Read and edit it, then paste it into the posting. Nothing is published from here."
    return draft


async def _job_for(db, org: str, a: HireApplication) -> tuple[dict, HirePosting | None]:
    p = None
    if a.posting_id:
        p = (await db.execute(select(HirePosting).where(HirePosting.org_id == org, HirePosting.id == a.posting_id))).scalar_one_or_none()
    if p is None:
        p = (await db.execute(select(HirePosting).where(HirePosting.org_id == org, HirePosting.requisition_id == a.requisition_id)
                              .order_by(HirePosting.created_at.desc()).limit(1))).scalar_one_or_none()
    r = await one(db, HireRequisition, a.requisition_id, org, "Requisition")
    job = {"title": (p.title if p else r.designation), "description": (p.description if p else r.notes),
           "requirements": (p.requirements if p else None)}
    return job, p


async def _score(db, actor, a: HireApplication, settings: dict, with_ai: bool = True) -> HireMatchScore:
    prof = (await db.execute(select(HireCandidateProfile).where(HireCandidateProfile.org_id == actor.org,
                                                                HireCandidateProfile.candidate_id == a.candidate_id))).scalar_one_or_none()
    if prof is None:
        raise HTTPException(409, "Parse or fill in the candidate's profile first (skills, experience).")
    job, posting = await _job_for(db, actor.org, a)
    pd = ser(prof)
    req = A.job_requirements(job)
    res = A.match_score(pd, req)
    res["job"] = req
    method = "rules"
    if with_ai:
        prompt = json.dumps({"candidate": A.profile_for_ai(pd), "job": {"title": job["title"], "requirements": req,
                                                                        "text": (job.get("requirements") or job.get("description") or "")[:4000]}}, default=str)
        ai, note = await _ai(actor.org, settings, "hiring.match", SYS_MATCH, prompt, 400)
        if ai:
            res["ai_view"] = {"strengths": [str(x)[:200] for x in (ai.get("strengths") or [])][:4],
                              "gaps": [str(x)[:200] for x in (ai.get("gaps") or [])][:4], "summary": str(ai.get("summary") or "")[:300]}
            method = "rules+ai"
        res["note"] = note
    row = HireMatchScore(org_id=uid(actor.org), application_id=a.id, posting_id=posting.id if posting else None, score=res["score"],
                         band=res["band"], detail=res, method=method, by_user=actor.user_id)
    db.add(row)
    return row


@router.post("/applications/{app_id}/match")
async def match(app_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    row = await _score(db, actor, a, await load_settings(db, actor.org))
    await db.commit()
    return ser(row)


@router.get("/applications/{app_id}/match")
async def latest_match(app_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    row = (await db.execute(select(HireMatchScore).where(HireMatchScore.org_id == actor.org, HireMatchScore.application_id == a.id)
                            .order_by(HireMatchScore.at.desc()).limit(1))).scalar_one_or_none()
    return ser(row) if row else None


@router.get("/requisitions/{req_id}/ranking")
async def ranking(req_id: str, request: Request, refresh: bool = False, db: AsyncSession = Depends(get_db)):
    """Applications of one requisition by advisory score. refresh=true re-scores with RULES only (no AI calls in bulk)."""
    actor = await get_actor(request, db)
    r = await one(db, HireRequisition, req_id, actor.org, "Requisition")
    if not (actor.is_hr or _is_hm(actor, r)):
        raise HTTPException(404, "Requisition not found")
    apps = (await db.execute(select(HireApplication).where(HireApplication.org_id == actor.org, HireApplication.requisition_id == r.id))).scalars().all()
    cands = {c.id: c for c in (await db.execute(select(HireCandidate).where(
        HireCandidate.org_id == actor.org, HireCandidate.id.in_([a.candidate_id for a in apps])))).scalars().all()} if apps else {}
    s = await load_settings(db, actor.org)
    out = []
    for a in apps:
        row = (await db.execute(select(HireMatchScore).where(HireMatchScore.org_id == actor.org, HireMatchScore.application_id == a.id)
                                .order_by(HireMatchScore.at.desc()).limit(1))).scalar_one_or_none()
        if (row is None or refresh) and a.stage not in ("rejected", "withdrawn", "joined"):
            try:
                row = await _score(db, actor, a, s, with_ai=False)
            except HTTPException:
                row = None
        c = cands.get(a.candidate_id)
        out.append({"application_id": str(a.id), "candidate_name": f"{c.first_name} {c.last_name or ''}".strip() if c else "",
                    "stage": a.stage, "score": row.score if row else None, "band": row.band if row else None,
                    "matched_skills": (row.detail or {}).get("matched_skills") if row else None,
                    "missing_skills": (row.detail or {}).get("missing_skills") if row else None,
                    "scored_at": row.at.isoformat() if row and row.at else None,
                    "why_none": None if row else "No profile yet: parse the résumé first"})
    await db.commit()
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    return {"requisition": r.req_number, "items": out,
            "advisory": "Sorted by an advisory score. Review every candidate; the order is a reading aid, not a decision."}
