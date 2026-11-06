"""Hiring D2 — competency scorecards and structured feedback.

  GET    /hiring/scorecards                       templates (org default + per requisition)
  POST   /hiring/scorecards                       create / update  {id?, requisition_id?, name, competencies, hide_names}
  DELETE /hiring/scorecards/{id}                  retire a template
  GET    /hiring/interviews/{id}/scorecard        the template this round uses (+ bias guard label)
  POST   /hiring/interviews/{id}/scorecard-feedback   ratings per competency + overall + hire / no-hire
  GET    /hiring/applications/{id}/scorecard      the aggregated candidate view
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
import hiring_d2_logic as D  # noqa: E402
import hiring_integrations as ext  # noqa: E402
import hiring_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import fail, get_actor, now, one, opt_uid, ser, uid  # noqa: E402
from hiring_d2_models import HireScorecardAnswer, HireScorecardTemplate  # noqa: E402
from hiring_models import HireApplication, HireFeedback, HireInterview, HireRequisition  # noqa: E402
from hiring_routers import _app_guard, load_settings  # noqa: E402

router = APIRouter(prefix="/hiring", tags=["hiring-scorecards"])


async def template_for(db: AsyncSession, org, requisition_id) -> dict:
    """Per-requisition template, else the org default, else the built-in competencies."""
    rows = (await db.execute(select(HireScorecardTemplate).where(
        HireScorecardTemplate.org_id == org, HireScorecardTemplate.is_active.is_(True)))).scalars().all()
    mine = next((t for t in rows if t.requisition_id == requisition_id), None) if requisition_id else None
    org_default = next((t for t in rows if t.requisition_id is None), None)
    t = mine or org_default
    s = await load_settings(db, org)
    if t:
        return {"id": str(t.id), "name": t.name, "competencies": t.competencies or [], "source": "requisition" if mine else "org",
                "hide_names": bool(t.hide_names or s["scorecard_hide_names"])}
    return {"id": None, "name": "Standard scorecard", "competencies": D.DEFAULT_COMPETENCIES, "source": "built_in",
            "hide_names": bool(s["scorecard_hide_names"])}


@router.get("/scorecards")
async def list_templates(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage scorecards.")
    rows = (await db.execute(select(HireScorecardTemplate).where(
        HireScorecardTemplate.org_id == actor.org, HireScorecardTemplate.is_active.is_(True))
        .order_by(HireScorecardTemplate.requisition_id.nullsfirst(), HireScorecardTemplate.name))).scalars().all()
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(HireRequisition.org_id == actor.org))).scalars().all()}
    out = []
    for t in rows:
        d = ser(t)
        r = reqs.get(str(t.requisition_id)) if t.requisition_id else None
        d["req_number"] = r.req_number if r else None
        d["designation"] = r.designation if r else "Org default"
        out.append(d)
    return {"templates": out, "built_in": D.DEFAULT_COMPETENCIES, "can_edit": actor.is_hr}


@router.post("/scorecards", status_code=201)
async def save_template(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage scorecards.")
    try:
        comps = D.clean_competencies(body.get("competencies"))
    except D.RuleError as e:
        fail(e)
    name = (body.get("name") or "").strip()[:120]
    if not name:
        raise HTTPException(422, "Name the scorecard.")
    rid = opt_uid(body.get("requisition_id"))
    if rid:
        await one(db, HireRequisition, rid, actor.org, "Requisition")
    t = await one(db, HireScorecardTemplate, body["id"], actor.org, "Scorecard") if body.get("id") else None
    if t is None:
        dup = (await db.execute(select(HireScorecardTemplate).where(
            HireScorecardTemplate.org_id == actor.org, HireScorecardTemplate.is_active.is_(True),
            HireScorecardTemplate.requisition_id == rid if rid else HireScorecardTemplate.requisition_id.is_(None)))).scalar_one_or_none()
        if dup:
            t = dup                                        # one active template per requisition (or org default)
        else:
            t = HireScorecardTemplate(org_id=uid(actor.org), created_by=actor.user_id)
            db.add(t)
    t.requisition_id, t.name, t.competencies = rid, name, comps
    t.hide_names, t.updated_at = bool(body.get("hide_names")), now()
    await db.commit()
    await db.refresh(t)
    return ser(t)


@router.delete("/scorecards/{tpl_id}")
async def retire_template(tpl_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage scorecards.")
    t = await one(db, HireScorecardTemplate, tpl_id, actor.org, "Scorecard")
    t.is_active, t.updated_at = False, now()
    await db.commit()
    return {"ok": True}


@router.get("/interviews/{iv_id}/scorecard")
async def interview_scorecard(iv_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """What the interviewer fills in. With the bias guard on, no candidate name is returned."""
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    if not (L.is_panel_member(ser(iv), actor.employee_id) or actor.is_hr):
        raise HTTPException(404, "Interview not found")
    t = await template_for(db, actor.org, a.requisition_id)
    mine = None
    if actor.employee_id:
        mine = (await db.execute(select(HireFeedback).where(
            HireFeedback.interview_id == iv.id, HireFeedback.interviewer_employee_id == uid(actor.employee_id)))).scalar_one_or_none()
    answers = []
    if mine:
        answers = [ser(x) for x in (await db.execute(select(HireScorecardAnswer).where(
            HireScorecardAnswer.feedback_id == mine.id))).scalars().all()]
    return {**t, "interview_id": str(iv.id), "application_id": str(a.id), "round_name": iv.round_name,
            "display_name": D.blind_label(str(a.id)) if t["hide_names"] else None,
            "my_feedback": ser(mine) if mine else None, "my_scores": answers}


@router.post("/interviews/{iv_id}/scorecard-feedback", status_code=201)
async def submit_scorecard(iv_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    if not L.is_panel_member(ser(iv), actor.employee_id):
        raise HTTPException(404, "Interview not found")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    t = await template_for(db, actor.org, a.requisition_id)
    try:
        scores = D.clean_scores(body.get("scores"), t["competencies"])
        overall = body.get("rating")
        if overall in (None, ""):                          # overall = the weighted competency score, rounded
            overall = round(D.weighted_score(scores) or 0)
        clean = L.clean_feedback({**body, "rating": overall})
    except (D.RuleError, L.RuleError) as e:
        fail(e)
    f = HireFeedback(org_id=iv.org_id, interview_id=iv.id, application_id=iv.application_id,
                     interviewer_employee_id=uid(actor.employee_id), interviewer_name=actor.name, **clean)
    db.add(f)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "You have already submitted feedback for this round.")
    for s in scores:
        db.add(HireScorecardAnswer(org_id=iv.org_id, feedback_id=f.id, interview_id=iv.id, application_id=iv.application_id,
                                   interviewer_employee_id=f.interviewer_employee_id, **s))
    await db.commit()
    await ext.notify(actor.org, roles=["hr"], title="Interview scorecard submitted",
                     body=f"{iv.round_name} · {actor.name}", link="/hiring/candidates", kind="hiring")
    return {**ser(f), "scores": scores, "weighted_score": D.weighted_score(scores)}


@router.get("/applications/{app_id}/scorecard")
async def application_scorecard(app_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    fbs = [ser(f) for f in (await db.execute(select(HireFeedback).where(
        HireFeedback.org_id == actor.org, HireFeedback.application_id == a.id))).scalars().all()]
    ans = [ser(x) for x in (await db.execute(select(HireScorecardAnswer).where(
        HireScorecardAnswer.org_id == actor.org, HireScorecardAnswer.application_id == a.id))).scalars().all()]
    t = await template_for(db, actor.org, a.requisition_id)
    return {"template": t, **D.aggregate_scorecard(fbs, ans)}
