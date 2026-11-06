"""Hiring — requisitions, job postings, candidates, applications, interviews, reports.

Signed-in routes only (the public careers / apply / offer-accept routes are in
hiring_public.py; offers are in hiring_offers.py). Everything is scoped by the
`X-Org-Id` the gateway stamps. Status changes are written with
`WHERE status = <what was read>`.
"""
import sys
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.docstore import pdf_active_content  # noqa: E402
from shared.utils.email import dangerous_url  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import hiring_integrations as ext  # noqa: E402
import hiring_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import (cas, emp_name, employees_by_id, fail, get_actor, now, num, one, opt_uid, ser,  # noqa: E402
                           to_date, to_datetime, uid)
from hiring_models import (HireApplication, HireCandidate, HireDocument, HireFeedback, HireInterview,  # noqa: E402
                           HireNote, HireOffer, HirePosting, HireRequisition, HireSettings, HireStageHistory)

router = APIRouter(prefix="/hiring", tags=["hiring"])

EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "seasonal", "intern")
SOURCES = ("careers_page", "referral", "agency", "walk_in", "job_board")
MODES = ("in_person", "video", "phone")
CONSENT_BY_HR = "Recorded by HR: the person agreed to their details being kept for hiring."


# ── shared loaders ──────────────────────────────────────────────────────────
async def load_settings(db: AsyncSession, org: str) -> dict:
    row = (await db.execute(select(HireSettings).where(HireSettings.org_id == org))).scalar_one_or_none()
    return L.merge_settings(row.data if row else None)


def _req_view(r: HireRequisition) -> dict:
    return {"created_by": r.created_by, "hiring_manager_id": str(r.hiring_manager_id or ""),
            "approval_step": r.approval_step, "status": r.status}


def _is_hm(actor: L.Actor, r: HireRequisition) -> bool:
    return bool(actor.employee_id) and str(r.hiring_manager_id or "") == actor.employee_id


async def _visible_req_ids(db: AsyncSession, actor: L.Actor) -> set | None:
    """None = HR sees every requisition. Otherwise the ones this person raised
    or is the hiring manager of."""
    if actor.is_hr:
        return None
    rows = (await db.execute(select(HireRequisition.id, HireRequisition.hiring_manager_id, HireRequisition.created_by)
                             .where(HireRequisition.org_id == actor.org))).all()
    return {str(i) for i, hm, cb in rows
            if (actor.employee_id and str(hm or "") == actor.employee_id) or (actor.user_id and cb == actor.user_id)}


async def _app_guard(db: AsyncSession, actor: L.Actor, app: HireApplication) -> HireRequisition:
    """HR, or the hiring manager of the application's requisition."""
    req = await one(db, HireRequisition, app.requisition_id, actor.org, "Requisition")
    if not (actor.is_hr or _is_hm(actor, req)):
        raise HTTPException(403, "Only HR or the hiring manager can open this application.")
    return req


async def _history(db, app: HireApplication, frm, to, reason, actor: L.Actor | None):
    db.add(HireStageHistory(org_id=app.org_id, application_id=app.id, from_stage=frm, to_stage=to,
                            reason=(reason or None), by_user=actor.user_id if actor else None,
                            by_name=(actor.name if actor else "System")))


# ── settings ────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    org = await ext.org_by_id(db, actor.org)
    s = await load_settings(db, actor.org)
    return {**s, "org_code": org.get("code"), "country": org.get("country"),
            "letters_available": ext.LETTERS_AVAILABLE, "notify_available": ext.NOTIFY_AVAILABLE,
            "can_edit": actor.is_hr_head}


@router.put("/settings")
async def put_settings(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin may change hiring settings.")
    try:
        clean = L.clean_settings(body)
    except L.RuleError as e:
        fail(e)
    row = (await db.execute(select(HireSettings).where(HireSettings.org_id == actor.org))).scalar_one_or_none()
    if not row:
        row = HireSettings(org_id=uid(actor.org), data={})
        db.add(row)
    row.data = {**(row.data or {}), **clean}
    row.updated_at, row.updated_by = now(), actor.user_id
    await db.commit()
    await record_audit(request, "hiring.settings.save", entity_type="hire_settings", entity_id=actor.org,
                       summary="Hiring settings changed", meta={"keys": sorted(clean)})
    return L.merge_settings(row.data)


# ── requisitions ────────────────────────────────────────────────────────────
async def _req_out(db, org: str, rows: list[HireRequisition], actor: L.Actor) -> list[dict]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    apps = (await db.execute(select(HireApplication.requisition_id, HireApplication.stage, func.count())
                             .where(HireApplication.org_id == org, HireApplication.requisition_id.in_(ids))
                             .group_by(HireApplication.requisition_id, HireApplication.stage))).all()
    acc = (await db.execute(select(HireOffer.requisition_id, func.count())
                            .where(HireOffer.org_id == org, HireOffer.requisition_id.in_(ids),
                                   HireOffer.status == "accepted").group_by(HireOffer.requisition_id))).all()
    posted = {str(r[0]) for r in (await db.execute(select(HirePosting.requisition_id).where(
        HirePosting.org_id == org, HirePosting.requisition_id.in_(ids), HirePosting.is_published.is_(True)))).all()}
    by_stage: dict = defaultdict(dict)
    for rid, stage, n in apps:
        by_stage[str(rid)][stage] = n
    accepted = {str(rid): n for rid, n in acc}
    out = []
    for r in rows:
        d = ser(r)
        st = by_stage.get(str(r.id), {})
        d.update({
            "applications": sum(st.values()), "in_pipeline": sum(n for s, n in st.items() if s in L.PIPELINE[:-1]),
            "offers_accepted": accepted.get(str(r.id), 0), "joined": st.get("joined", 0),
            "is_posted": str(r.id) in posted,
            "can": {a: L.req_can_act(a, actor, _req_view(r)) and r.status in L.REQ_TRANSITIONS[a][0]
                    for a in ("submit", "approve", "reject", "hold", "resume", "cancel", "reopen")},
        })
        d["can"]["edit"] = r.status == "draft" and L.req_can_act("edit", actor, _req_view(r))
        out.append(d)
    return out


@router.get("/requisitions")
async def list_requisitions(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if actor.is_hr:
        from hiring_offers import sync_joined          # late import: offers imports this module
        await sync_joined(db, actor)
    q = select(HireRequisition).where(HireRequisition.org_id == actor.org)
    if status:
        q = q.where(HireRequisition.status == status)
    rows = (await db.execute(q.order_by(HireRequisition.created_at.desc()))).scalars().all()
    visible = await _visible_req_ids(db, actor)
    if visible is not None:
        rows = [r for r in rows if str(r.id) in visible]
    return await _req_out(db, actor.org, rows, actor)


async def _fill_requisition(db, actor: L.Actor, r: HireRequisition, body: dict):
    emps = await employees_by_id(db, actor.org, [body.get("hiring_manager_id"), body.get("replaces_employee_id")])
    if "designation" in body:
        r.designation = (body.get("designation") or "").strip()
    for k in ("department", "grade", "location", "notes"):
        if k in body:
            setattr(r, k, (body.get(k) or "").strip() or None)
    if "employment_type" in body:
        if body["employment_type"] not in EMPLOYMENT_TYPES:
            raise HTTPException(422, "Pick an employment type from the list.")
        r.employment_type = body["employment_type"]
    if "openings" in body:
        r.openings = int(num(body.get("openings"), "Number of openings", required=True, lo=1, hi=999))
    if "reason_type" in body:
        if body["reason_type"] not in ("new", "replacement"):
            raise HTTPException(422, "Reason must be New position or Replacement.")
        r.reason_type = body["reason_type"]
    if "replaces_employee_id" in body or r.reason_type == "new":
        rid = opt_uid(body.get("replaces_employee_id")) if r.reason_type == "replacement" else None
        if rid and str(rid) not in emps:
            raise HTTPException(422, "The employee being replaced was not found.")
        r.replaces_employee_id = rid
        r.replaces_employee_name = emp_name(emps[str(rid)]) if rid else None
    if r.reason_type == "replacement" and not r.replaces_employee_id:
        raise HTTPException(422, "Pick the employee this position replaces.")
    if "budget_min" in body:
        r.budget_min = num(body.get("budget_min"), "Budget minimum", lo=0)
    if "budget_max" in body:
        r.budget_max = num(body.get("budget_max"), "Budget maximum", lo=0)
    if r.budget_min is not None and r.budget_max is not None and float(r.budget_min) > float(r.budget_max):
        raise HTTPException(422, "Budget minimum cannot be more than the maximum.")
    if "hiring_manager_id" in body:
        hm = opt_uid(body.get("hiring_manager_id"))
        if hm and str(hm) not in emps:
            raise HTTPException(422, "The hiring manager was not found.")
        r.hiring_manager_id = hm
        r.hiring_manager_name = emp_name(emps[str(hm)]) if hm else None
    if "target_date" in body:
        r.target_date = to_date(body.get("target_date"))
    if not r.designation:
        raise HTTPException(422, "Designation is required.")


@router.post("/requisitions", status_code=201)
async def create_requisition(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role == "manager"):
        raise HTTPException(403, "Only HR or a manager can raise a requisition.")
    n = (await db.execute(select(func.count()).select_from(HireRequisition).where(
        HireRequisition.org_id == actor.org))).scalar() or 0
    r = HireRequisition(org_id=uid(actor.org), req_number=f"REQ-{(await org_today(actor.org)).year}-{n + 1:04d}", designation="",
                        status="draft", approvals=[], created_by=actor.user_id,
                        created_by_employee_id=opt_uid(actor.employee_id), openings=1, reason_type="new",
                        employment_type="full_time")
    if actor.role == "manager" and not body.get("hiring_manager_id"):
        body = {**body, "hiring_manager_id": actor.employee_id or None}
    await _fill_requisition(db, actor, r, body)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return (await _req_out(db, actor.org, [r], actor))[0]


@router.patch("/requisitions/{req_id}")
async def update_requisition(req_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    r = await one(db, HireRequisition, req_id, actor.org, "Requisition")
    if not L.req_can_act("edit", actor, _req_view(r)):
        raise HTTPException(403, "You cannot edit this requisition.")
    if r.status != "draft":
        # After approval only the head-count and target date may move, by HR.
        allowed = {"openings", "target_date", "notes"}
        if not actor.is_hr or r.status not in ("open", "on_hold") or not set(body) <= allowed:
            raise HTTPException(409, "Only a draft can be edited. On an open requisition HR may change "
                                     "the number of openings, the target date and the notes.")
    await _fill_requisition(db, actor, r, body)
    await db.commit()
    await db.refresh(r)
    return (await _req_out(db, actor.org, [r], actor))[0]


@router.post("/requisitions/{req_id}/{action}")
async def act_requisition(req_id: str, action: str, request: Request, body: dict | None = None,
                          db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    body = body or {}
    r = await one(db, HireRequisition, req_id, actor.org, "Requisition")
    if action not in L.REQ_TRANSITIONS or action == "fill":
        raise HTTPException(404, "Unknown action")
    read_status, read_step = r.status, r.approval_step
    reason = (body.get("reason") or "").strip()
    try:
        to = L.req_next(read_status, action)
    except L.RuleError as e:
        fail(e)
    if not L.req_can_act(action, actor, _req_view(r)):
        raise HTTPException(403, "You are not the approver for this step." if action in ("approve", "reject")
                            else "You cannot do this on this requisition.")
    if action in L.REQ_REASON_REQUIRED and not reason:
        raise HTTPException(422, "Give a reason.")
    s = await load_settings(db, actor.org)
    step, values = None, {}
    entry = {"action": action, "step": read_step, "by": actor.user_id, "name": actor.name, "role": actor.role,
             "at": now().isoformat(), "reason": reason or None}
    if action == "submit":
        to, step = L.req_submit(s["requisition_approval"], actor, _req_view(r))
        values["submitted_at"] = now()
    elif action == "approve":
        to, step = L.req_approve(s["requisition_approval"], read_step)
    if to == "open" and not r.opened_at:
        values["opened_at"] = now()
    if to == "cancelled":
        values["closed_at"] = now()
    if action == "reopen":
        values["filled_at"] = None
    extra = [HireRequisition.approval_step == read_step] if read_step else [HireRequisition.approval_step.is_(None)]
    await cas(db, HireRequisition, r, "status", read_status, extra_where=extra, status=to, approval_step=step,
              approvals=list(r.approvals or []) + [entry], status_reason=reason or None, **values)
    if to == "cancelled":                             # a cancelled requisition takes its postings down
        for p in (await db.execute(select(HirePosting).where(
                HirePosting.org_id == actor.org, HirePosting.requisition_id == r.id,
                HirePosting.is_published.is_(True)))).scalars().all():
            p.is_published, p.closed_at = False, now()
    await db.commit()
    await db.refresh(r)
    await record_audit(request, f"hiring.requisition.{action}", entity_type="hire_requisition", entity_id=str(r.id),
                       summary=f"{r.req_number} {read_status} -> {to}", meta={"step": read_step, "reason": reason or None})
    if to == "pending_approval":
        if step == "manager" and r.hiring_manager_id:
            users = await ext.user_ids_for_employees(db, actor.org, [r.hiring_manager_id])
            await ext.notify(actor.org, user_ids=users, title="A requisition needs your approval",
                             body=f"{r.req_number} · {r.designation}", link="/hiring/requisitions", kind="approval")
        else:
            await ext.notify(actor.org, roles=["hr", "org_admin"], title="A requisition needs HR approval",
                             body=f"{r.req_number} · {r.designation}", link="/hiring/requisitions", kind="approval")
    return (await _req_out(db, actor.org, [r], actor))[0]


# ── job postings ────────────────────────────────────────────────────────────
def _posting_out(p: HirePosting, r: HireRequisition | None) -> dict:
    d = ser(p)
    d.update({"req_number": r.req_number if r else None, "requisition_status": r.status if r else None,
              "department": r.department if r else None, "location": r.location if r else None,
              "employment_type": r.employment_type if r else None,
              "live": bool(p.is_published and r and r.status == "open")})
    return d


@router.get("/postings")
async def list_postings(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage job postings.")
    rows = (await db.execute(select(HirePosting).where(HirePosting.org_id == actor.org)
                             .order_by(HirePosting.created_at.desc()))).scalars().all()
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(
        HireRequisition.org_id == actor.org))).scalars().all()}
    counts = dict((str(pid), n) for pid, n in (await db.execute(
        select(HireApplication.posting_id, func.count()).where(HireApplication.org_id == actor.org)
        .group_by(HireApplication.posting_id))).all())
    out = []
    for p in rows:
        d = _posting_out(p, reqs.get(str(p.requisition_id)))
        d["applications"] = counts.get(str(p.id), 0)
        out.append(d)
    return out


@router.post("/postings", status_code=201)
async def create_posting(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage job postings.")
    r = await one(db, HireRequisition, body.get("requisition_id"), actor.org, "Requisition")
    if r.status != "open":
        raise HTTPException(409, "A posting can only be written for an open requisition.")
    title = (body.get("title") or r.designation or "").strip()
    if not title:
        raise HTTPException(422, "Title is required.")
    p = HirePosting(org_id=uid(actor.org), requisition_id=r.id, title=title,
                    description=(body.get("description") or "").strip() or None,
                    requirements=(body.get("requirements") or "").strip() or None, created_by=actor.user_id)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _posting_out(p, r)


@router.patch("/postings/{posting_id}")
async def update_posting(posting_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can manage job postings.")
    p = await one(db, HirePosting, posting_id, actor.org, "Posting")
    r = await one(db, HireRequisition, p.requisition_id, actor.org, "Requisition")
    for k in ("title", "description", "requirements"):
        if k in body:
            setattr(p, k, (body.get(k) or "").strip() or None)
    if not p.title:
        raise HTTPException(422, "Title is required.")
    if "is_published" in body:
        want = bool(body["is_published"])
        if want and r.status != "open":
            raise HTTPException(409, "Only a posting for an open requisition can be published.")
        if want and not (p.description or "").strip():
            raise HTTPException(422, "Write the job description before publishing.")
        if want != p.is_published:
            p.is_published = want
            p.published_at = now() if want else p.published_at
            p.closed_at = None if want else now()
    await db.commit()
    await db.refresh(p)
    return _posting_out(p, r)


# ── candidates ──────────────────────────────────────────────────────────────
def _cand_name(c: HireCandidate) -> str:
    return f"{c.first_name or ''} {c.last_name or ''}".strip()


async def _all_candidates(db, org: str) -> list[HireCandidate]:
    return (await db.execute(select(HireCandidate).where(
        HireCandidate.org_id == org, HireCandidate.is_active.is_(True)))).scalars().all()


def _dup_index(cands: list[HireCandidate]) -> dict[str, list[dict]]:
    """candidate id -> possible duplicates (same e-mail or phone, another record)."""
    slim = [{"id": str(c.id), "email_norm": c.email_norm, "phone_norm": c.phone_norm, "name": _cand_name(c)} for c in cands]
    by_key = defaultdict(list)                       # only compare records that share a key
    for s in slim:
        for k in (("e", s["email_norm"]), ("p", s["phone_norm"])):
            if k[1]:
                by_key[k].append(s)
    out = {}
    for s in slim:
        near = {o["id"]: o for k in (("e", s["email_norm"]), ("p", s["phone_norm"])) if k[1] for o in by_key[k]}
        out[s["id"]] = L.find_duplicates(s["email_norm"], s["phone_norm"], list(near.values()), exclude_id=s["id"])
    return out


def _cand_out(c: HireCandidate, dups: list[dict] | None = None) -> dict:
    d = ser(c, skip=("email_norm", "phone_norm", "consent_text"))
    d["name"] = _cand_name(c)
    d["possible_duplicates"] = dups or []
    return d


@router.get("/candidates")
async def list_candidates(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see the candidate list.")
    cands = await _all_candidates(db, actor.org)
    dups = _dup_index(cands)
    napps = dict((str(cid), n) for cid, n in (await db.execute(
        select(HireApplication.candidate_id, func.count()).where(HireApplication.org_id == actor.org)
        .group_by(HireApplication.candidate_id))).all())
    out = []
    for c in sorted(cands, key=lambda c: c.created_at, reverse=True):
        d = _cand_out(c, dups.get(str(c.id)))
        d["applications"] = napps.get(str(c.id), 0)
        out.append(d)
    return out


@router.post("/candidates/check-duplicate")
async def check_duplicate(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see the candidate list.")
    cands = await _all_candidates(db, actor.org)
    slim = [{"id": str(c.id), "email_norm": c.email_norm, "phone_norm": c.phone_norm, "name": _cand_name(c)} for c in cands]
    return {"possible_duplicates": L.find_duplicates(body.get("email"), body.get("phone"), slim,
                                                     exclude_id=body.get("exclude_id"))}


def _fill_candidate(c: HireCandidate, body: dict):
    for k in ("first_name", "last_name", "email", "phone", "current_employer", "current_title", "city"):
        if k in body:
            setattr(c, k, (str(body.get(k) or "")).strip() or None)
    if "tags" in body:
        tags = body.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        c.tags = sorted({str(t).strip()[:40] for t in tags if str(t).strip()})[:20]
    if body.get("consent_given") and not c.consent_at:
        # HR records that the person agreed to their details being kept for hiring (PIPEDA consent). Never un-set here.
        c.consent_at = now()
        c.consent_text = str(body.get("consent_text") or "").strip()[:1000] or CONSENT_BY_HR
    if not c.first_name:
        raise HTTPException(422, "First name is required.")
    if not c.email and not c.phone:
        raise HTTPException(422, "Give an e-mail or a phone number so the person can be reached.")
    c.email_norm, c.phone_norm = L.norm_email(c.email), L.norm_phone(c.phone)


@router.post("/candidates", status_code=201)
async def create_candidate(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Add a person. If the e-mail or phone matches someone already on file the
    answer is 409 with the matches; HR then either opens the existing record or
    repeats the call with `confirm_duplicate: true`. Nothing is merged silently."""
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can add candidates.")
    c = HireCandidate(org_id=uid(actor.org), first_name="", tags=[], created_by=actor.user_id)
    _fill_candidate(c, body)
    cands = await _all_candidates(db, actor.org)
    slim = [{"id": str(x.id), "email_norm": x.email_norm, "phone_norm": x.phone_norm, "name": _cand_name(x)} for x in cands]
    dups = L.find_duplicates(c.email, c.phone, slim)
    if dups and not body.get("confirm_duplicate"):
        raise HTTPException(409, {"message": "This person may already be on file.", "possible_duplicates": dups})
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _cand_out(c, dups)


@router.patch("/candidates/{cand_id}")
async def update_candidate(cand_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can edit candidates.")
    c = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    _fill_candidate(c, body)
    await db.commit()
    await db.refresh(c)
    return _cand_out(c, _dup_index(await _all_candidates(db, actor.org)).get(str(c.id)))


@router.post("/candidates/{cand_id}/merge-into/{target_id}")
async def merge_candidate(cand_id: str, target_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """An explicit, HR-confirmed merge of a duplicate record into the one to keep."""
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can merge candidates.")
    src = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    dst = await one(db, HireCandidate, target_id, actor.org, "Candidate")
    if src.id == dst.id or not src.is_active or not dst.is_active:
        raise HTTPException(409, "Pick two different, current records.")
    dst_reqs = {str(a.requisition_id) for a in (await db.execute(select(HireApplication).where(
        HireApplication.org_id == actor.org, HireApplication.candidate_id == dst.id))).scalars().all()}
    moved = kept = 0
    for a in (await db.execute(select(HireApplication).where(
            HireApplication.org_id == actor.org, HireApplication.candidate_id == src.id))).scalars().all():
        if str(a.requisition_id) in dst_reqs:
            kept += 1                                   # both applied to the same job: leave the older trail in place
            continue
        a.candidate_id = dst.id
        moved += 1
    if kept:
        raise HTTPException(409, "Both records have an application for the same requisition. "
                                 "Reject or withdraw one of them first, then merge.")
    for model in (HireDocument, HireNote):
        for row in (await db.execute(select(model).where(model.org_id == actor.org, model.candidate_id == src.id))).scalars().all():
            row.candidate_id = dst.id
    for o in (await db.execute(select(HireOffer).where(HireOffer.org_id == actor.org, HireOffer.candidate_id == src.id))).scalars().all():
        o.candidate_id = dst.id
    dst.tags = sorted(set(dst.tags or []) | set(src.tags or []))
    for k in ("phone", "email", "current_employer", "current_title", "city", "last_name"):
        if not getattr(dst, k) and getattr(src, k):
            setattr(dst, k, getattr(src, k))
    dst.email_norm, dst.phone_norm = L.norm_email(dst.email), L.norm_phone(dst.phone)
    src.is_active, src.merged_into = False, dst.id
    await db.commit()
    await record_audit(request, "hiring.candidate.merge", entity_type="hire_candidate", entity_id=str(dst.id),
                       summary="Duplicate candidate merged", meta={"merged_from": str(src.id), "applications_moved": moved})
    return {"ok": True, "kept": str(dst.id), "applications_moved": moved}


# ── documents (bytes in the DB) ─────────────────────────────────────────────
async def store_document(db, org, candidate_id, application_id, kind: str, file_name: str | None,
                         content: bytes, max_mb: int, uploaded_by: str | None) -> HireDocument:
    try:
        ext_, ctype = L.check_upload(content, max_mb)
    except L.RuleError as e:
        fail(e)
    if ext_ == "pdf" and pdf_active_content(content):
        raise HTTPException(422, "This PDF contains scripts, launch actions or embedded files and cannot be "
                                 "uploaded. Print it to a plain PDF and upload that.")
    doc = HireDocument(org_id=uid(org), candidate_id=candidate_id, application_id=application_id,
                       kind=L.doc_kind(kind),
                       file_name=L.safe_file_name(file_name, ext_), content_type=ctype,
                       size_bytes=len(content), content=content, uploaded_by=uploaded_by)
    db.add(doc)
    await db.flush()
    try:                                                   # D2: a copy on the files volume (LEXORA_FILES_DIR), best-effort
        import hiring_d2_logic as D2
        doc.file_path = D2.write_file(D2.resume_path(str(org), str(candidate_id), str(doc.id), ext_), content)
    except Exception:                                      # noqa: BLE001
        doc.file_path = None
    return doc


async def read_capped(file: UploadFile, max_mb: int) -> bytes:
    """Read at most the limit + 1 byte, so an oversized upload is not held in memory."""
    return await file.read(int(max_mb) * 1024 * 1024 + 1)


def _doc_out(d: HireDocument) -> dict:
    return {"id": str(d.id), "kind": d.kind, "file_name": d.file_name, "content_type": d.content_type,
            "size_bytes": d.size_bytes, "application_id": str(d.application_id) if d.application_id else None,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "from_candidate": d.uploaded_by is None}


@router.post("/candidates/{cand_id}/documents", status_code=201)
async def upload_document(cand_id: str, request: Request, file: UploadFile = File(...), kind: str = Form("document"),
                          application_id: str | None = Form(None), db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can add candidate documents.")
    c = await one(db, HireCandidate, cand_id, actor.org, "Candidate")
    s = await load_settings(db, actor.org)
    content = await read_capped(file, s["resume_max_mb"])
    doc = await store_document(db, actor.org, c.id, opt_uid(application_id), kind, file.filename, content,
                               s["resume_max_mb"], actor.user_id)
    await db.commit()
    return _doc_out(doc)


async def _panel_app_ids(db, actor: L.Actor) -> set[str]:
    if not actor.employee_id:
        return set()
    ivs = (await db.execute(select(HireInterview).where(HireInterview.org_id == actor.org))).scalars().all()
    return L.interviewer_application_ids([ser(i) for i in ivs], actor.employee_id)


@router.get("/documents/{doc_id}")
async def download_document(doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """HR, the hiring manager of one of the person's applications, or a panel
    member who interviews this person. Nobody else."""
    actor = await get_actor(request, db)
    d = await one(db, HireDocument, doc_id, actor.org, "Document")
    if not actor.is_hr:
        apps = (await db.execute(select(HireApplication).where(
            HireApplication.org_id == actor.org, HireApplication.candidate_id == d.candidate_id))).scalars().all()
        visible = await _visible_req_ids(db, actor) or set()
        mine = await _panel_app_ids(db, actor)
        if not any(str(a.requisition_id) in visible or str(a.id) in mine for a in apps):
            raise HTTPException(404, "Document not found")
    return Response(content=d.content, media_type=d.content_type,
                    headers={"Content-Disposition": f'inline; filename="{d.file_name}"',
                             "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; sandbox"})


# ── applications ────────────────────────────────────────────────────────────
async def _app_rows(db, actor: L.Actor, apps: list[HireApplication]) -> list[dict]:
    if not apps:
        return []
    org = actor.org
    cands = {str(c.id): c for c in (await db.execute(select(HireCandidate).where(
        HireCandidate.org_id == org, HireCandidate.id.in_({a.candidate_id for a in apps})))).scalars().all()}
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(
        HireRequisition.org_id == org, HireRequisition.id.in_({a.requisition_id for a in apps})))).scalars().all()}
    ids = [a.id for a in apps]
    ivs = [ser(i) for i in (await db.execute(select(HireInterview).where(
        HireInterview.org_id == org, HireInterview.application_id.in_(ids)))).scalars().all()]
    fbs = [ser(f) for f in (await db.execute(select(HireFeedback).where(
        HireFeedback.org_id == org, HireFeedback.application_id.in_(ids)))).scalars().all()]
    resumes = {str(r[0]) for r in (await db.execute(select(HireDocument.candidate_id).where(
        HireDocument.org_id == org, HireDocument.candidate_id.in_({a.candidate_id for a in apps})))).all()}
    dups = _dup_index(await _all_candidates(db, org)) if actor.is_hr else {}
    s = await load_settings(db, org)
    out = []
    for a in apps:
        c, r = cands.get(str(a.candidate_id)), reqs.get(str(a.requisition_id))
        my_iv = [i for i in ivs if i["application_id"] == str(a.id)]
        my_fb = [f for f in fbs if f["application_id"] == str(a.id)]
        ratings = [f["rating"] for f in my_fb]
        d = ser(a)
        d.update({
            "candidate_name": _cand_name(c) if c else "", "email": c.email if c else None,
            "phone": c.phone if c else None, "tags": (c.tags if c else []) or [],
            "current_title": c.current_title if c else None, "current_employer": c.current_employer if c else None,
            "req_number": r.req_number if r else None, "designation": r.designation if r else None,
            "department": r.department if r else None, "requisition_status": r.status if r else None,
            "stage_label": L.STAGE_LABEL.get(a.stage, a.stage),
            "days_in_stage": max((now() - a.stage_changed_at).days, 0) if a.stage_changed_at else 0,
            "interviews": len([i for i in my_iv if i["status"] != "cancelled"]),
            "rounds_done": L.rounds_done(my_iv, my_fb), "rounds_required": int(s["required_interview_rounds"]),
            "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "has_documents": str(a.candidate_id) in resumes,
            "possible_duplicate": bool(dups.get(str(a.candidate_id))),
        })
        out.append(d)
    return out


@router.get("/applications")
async def list_applications(request: Request, requisition_id: str | None = None, stage: str | None = None,
                            db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    q = select(HireApplication).where(HireApplication.org_id == actor.org)
    if requisition_id:
        q = q.where(HireApplication.requisition_id == uid(requisition_id, "Requisition"))
    if stage:
        q = q.where(HireApplication.stage == stage)
    apps = (await db.execute(q.order_by(HireApplication.created_at.desc()))).scalars().all()
    visible = await _visible_req_ids(db, actor)
    if visible is not None:
        apps = [a for a in apps if str(a.requisition_id) in visible]
    return await _app_rows(db, actor, apps)


@router.post("/applications", status_code=201)
async def create_application(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can add an application.")
    c = await one(db, HireCandidate, body.get("candidate_id"), actor.org, "Candidate")
    r = await one(db, HireRequisition, body.get("requisition_id"), actor.org, "Requisition")
    if r.status != "open":
        raise HTTPException(409, "Applications can only be added to an open requisition.")
    src = body.get("source_type") or "walk_in"
    if src not in SOURCES:
        raise HTTPException(422, "Pick a source from the list.")
    ref_id = opt_uid(body.get("referrer_employee_id")) if src == "referral" else None
    ref_name = None
    if src == "referral":
        emps = await employees_by_id(db, actor.org, [ref_id])
        if not ref_id or str(ref_id) not in emps:
            raise HTTPException(422, "Pick the employee who referred this person.")
        ref_name = emp_name(emps[str(ref_id)])
    detail = (body.get("source_detail") or "").strip() or None
    if src in ("agency", "job_board") and not detail:
        raise HTTPException(422, "Type the name of the agency or the job board.")
    a = HireApplication(org_id=uid(actor.org), candidate_id=c.id, requisition_id=r.id, source_type=src,
                        source_detail=detail, referrer_employee_id=ref_id, referrer_name=ref_name,
                        stage="applied", created_by=actor.user_id)
    db.add(a)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This person already has an application for this requisition.")
    await _history(db, a, None, "applied", None, actor)
    await db.commit()
    await db.refresh(a)
    return (await _app_rows(db, actor, [a]))[0]


async def move_application(db, actor: L.Actor | None, a: HireApplication, to: str, reason: str | None,
                           settings: dict, system: bool = False) -> None:
    """One guarded stage move (+ history row). Caller commits."""
    read_stage = a.stage
    ivs = [ser(i) for i in (await db.execute(select(HireInterview).where(
        HireInterview.org_id == a.org_id, HireInterview.application_id == a.id))).scalars().all()]
    fbs = [ser(f) for f in (await db.execute(select(HireFeedback).where(
        HireFeedback.org_id == a.org_id, HireFeedback.application_id == a.id))).scalars().all()]
    res = L.app_move(read_stage, to, prev=a.prev_stage, reason=reason, rounds_done=L.rounds_done(ivs, fbs),
                     rounds_required=int(settings["required_interview_rounds"]), system=system)
    await cas(db, HireApplication, a, "stage", read_stage, stage=res["stage"], prev_stage=res["prev_stage"],
              stage_reason=(reason or "").strip() or None, stage_changed_at=now())
    await _history(db, a, read_stage, res["stage"], reason, actor)
    try:                                                   # D2: tell the candidate (opt-in respected, honest log)
        from hiring_comms import after_stage_move
        await after_stage_move(db, a, res["stage"], reason)
    except Exception:                                      # noqa: BLE001  never blocks the move
        pass


@router.post("/applications/move")
async def move_applications(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Move one or many applications to a stage (bulk move / bulk reject).
    Each row is checked on its own; the answer lists what moved and what did not."""
    actor = await get_actor(request, db)
    ids, to = body.get("ids") or [], body.get("to_stage")
    reason = (body.get("reason") or "").strip()
    if not ids or not isinstance(ids, list):
        raise HTTPException(422, "Tick at least one candidate.")
    if len(ids) > 200:
        raise HTTPException(422, "Move at most 200 candidates at a time.")
    s = await load_settings(db, actor.org)
    moved, failed = [], []
    for raw in ids:
        try:
            a = await one(db, HireApplication, raw, actor.org, "Application")
            await _app_guard(db, actor, a)
            await move_application(db, actor, a, to, reason, s)
            await db.commit()
            moved.append(str(a.id))
        except L.RuleError as e:
            await db.rollback()
            failed.append({"id": str(raw), "reason": e.message})
        except HTTPException as e:
            await db.rollback()
            failed.append({"id": str(raw), "reason": e.detail if isinstance(e.detail, str) else "Could not be moved."})
    if moved:
        await record_audit(request, "hiring.application.move", entity_type="hire_application", entity_id="bulk",
                           summary=f"{len(moved)} application(s) moved to {to}", meta={"to": to, "reason": reason or None,
                                                                                    "count": len(moved)})
    return {"moved": moved, "failed": failed}


@router.get("/applications/{app_id}")
async def get_application(app_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    org = actor.org
    row = (await _app_rows(db, actor, [a]))[0]
    c = await one(db, HireCandidate, a.candidate_id, org, "Candidate")
    cands = await _all_candidates(db, org)
    hist = (await db.execute(select(HireStageHistory).where(
        HireStageHistory.org_id == org, HireStageHistory.application_id == a.id).order_by(HireStageHistory.at))).scalars().all()
    ivs = (await db.execute(select(HireInterview).where(
        HireInterview.org_id == org, HireInterview.application_id == a.id).order_by(HireInterview.round_no))).scalars().all()
    fbs = (await db.execute(select(HireFeedback).where(
        HireFeedback.org_id == org, HireFeedback.application_id == a.id))).scalars().all()
    notes = (await db.execute(select(HireNote).where(
        HireNote.org_id == org, HireNote.candidate_id == a.candidate_id).order_by(HireNote.created_at.desc()))).scalars().all()
    docs = (await db.execute(select(HireDocument).where(
        HireDocument.org_id == org, HireDocument.candidate_id == a.candidate_id).order_by(HireDocument.uploaded_at.desc()))).scalars().all()
    offers = (await db.execute(select(HireOffer).where(
        HireOffer.org_id == org, HireOffer.application_id == a.id).order_by(HireOffer.created_at.desc()))).scalars().all()
    others = (await db.execute(select(HireApplication).where(
        HireApplication.org_id == org, HireApplication.candidate_id == a.candidate_id, HireApplication.id != a.id))).scalars().all()
    fb_by = defaultdict(list)
    for f in fbs:
        fb_by[str(f.interview_id)].append(ser(f))
    return {
        "application": row,
        "candidate": _cand_out(c, _dup_index(cands).get(str(c.id)) if actor.is_hr else []),
        "history": [ser(h) for h in hist],
        "interviews": [{**ser(i), "feedback": fb_by.get(str(i.id), []),
                        "feedback_pending": [p for p in (i.panel or []) if str(p.get("employee_id")) not in
                                             {x["interviewer_employee_id"] for x in fb_by.get(str(i.id), [])}]}
                       for i in ivs],
        "notes": [ser(n) for n in notes],
        "documents": [_doc_out(d) for d in docs],
        "offers": [ser(o, skip=("token_hash",)) for o in offers],
        "other_applications": [{"id": str(o.id), "stage": o.stage, "requisition_id": str(o.requisition_id)} for o in others],
        "stages": [{"key": k, "label": L.STAGE_LABEL[k]} for k in L.STAGES],
    }


@router.post("/applications/{app_id}/notes", status_code=201)
async def add_note(app_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    text_ = (body.get("body") or "").strip()
    if not text_:
        raise HTTPException(422, "Type the note first.")
    n = HireNote(org_id=a.org_id, candidate_id=a.candidate_id, application_id=a.id, body=text_[:4000],
                 author_id=actor.user_id, author_name=actor.name)
    db.add(n)
    await db.commit()
    return ser(n)


# ── interviews ──────────────────────────────────────────────────────────────
async def _panel(db, org: str, ids) -> list[dict]:
    ids = [i for i in (ids or []) if i]
    if not ids:
        raise HTTPException(422, "Pick at least one interviewer.")
    emps = await employees_by_id(db, org, ids)
    missing = [i for i in ids if str(i) not in emps]
    if missing:
        raise HTTPException(422, "One of the interviewers was not found. Pick again from the list.")
    return [{"employee_id": str(i), "name": emp_name(emps[str(i)])} for i in dict.fromkeys(str(i) for i in ids)]


def _clean_place(v) -> str | None:
    """Interview place / meeting link: an http(s) link or plain text (a room). A link that would run
    code when the candidate clicks it (javascript:, data:, vbscript:, …) is refused."""
    s = (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())
    if dangerous_url(s):
        raise HTTPException(422, "The place must be a web link (https://…) or plain text such as a room name.")
    return s[:300] or None


@router.post("/applications/{app_id}/interviews", status_code=201)
async def schedule_interview(app_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    a = await one(db, HireApplication, app_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    if a.stage not in ("applied", "screening", "interview"):
        raise HTTPException(409, f"An interview cannot be scheduled while the candidate is in {L.STAGE_LABEL[a.stage]}.")
    name = (body.get("round_name") or "").strip()
    if not name:
        raise HTTPException(422, "Name the round, for example Technical round.")
    mode = body.get("mode") or "in_person"
    if mode not in MODES:
        raise HTTPException(422, "Pick a mode from the list.")
    n = (await db.execute(select(func.count()).select_from(HireInterview).where(
        HireInterview.org_id == actor.org, HireInterview.application_id == a.id))).scalar() or 0
    iv = HireInterview(org_id=a.org_id, application_id=a.id, round_no=n + 1, round_name=name,
                       scheduled_at=to_datetime(body.get("scheduled_at")), mode=mode,
                       place=_clean_place(body.get("place")),
                       panel=await _panel(db, actor.org, body.get("panel")), created_by=actor.user_id)
    db.add(iv)
    if a.stage != "interview":                         # scheduling the first round moves the card
        await move_application(db, actor, a, "interview", None, await load_settings(db, actor.org))
    await db.commit()
    await db.refresh(iv)
    users = await ext.user_ids_for_employees(db, actor.org, [p["employee_id"] for p in iv.panel])
    await ext.notify(actor.org, user_ids=users, title="You are on an interview panel",
                     body=f"{name}" + (f" · {iv.scheduled_at:%d %b %Y %H:%M}" if iv.scheduled_at else ""),
                     link="/hiring/my-interviews", kind="task")
    sync = {}
    if iv.scheduled_at:                                    # D2: calendar event (if connected), candidate message, panel reminder
        try:
            from hiring_scheduling import after_scheduled
            sync = await after_scheduled(db, actor, iv, a)
            await db.commit()
        except Exception:                                  # noqa: BLE001
            sync = {}
    return {**ser(iv), "sync": sync}


@router.patch("/interviews/{iv_id}")
async def update_interview(iv_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    a = await one(db, HireApplication, iv.application_id, actor.org, "Application")
    await _app_guard(db, actor, a)
    if iv.status == "cancelled" and body.get("status") != "scheduled":
        raise HTTPException(409, "This interview is cancelled.")
    if "round_name" in body and (body.get("round_name") or "").strip():
        iv.round_name = body["round_name"].strip()
    if "scheduled_at" in body:
        iv.scheduled_at = to_datetime(body.get("scheduled_at"))
    if "mode" in body:
        if body["mode"] not in MODES:
            raise HTTPException(422, "Pick a mode from the list.")
        iv.mode = body["mode"]
    if "place" in body:
        iv.place = _clean_place(body.get("place"))
    if "panel" in body:
        iv.panel = await _panel(db, actor.org, body.get("panel"))
    if body.get("status") in ("scheduled", "cancelled") and body["status"] != iv.status:
        read = iv.status
        await db.flush()
        await cas(db, HireInterview, iv, "status", read, status=body["status"])
    await db.commit()
    await db.refresh(iv)
    return ser(iv)


@router.get("/interviews/mine")
async def my_interviews(request: Request, db: AsyncSession = Depends(get_db)):
    """Interviews assigned to me. A panel member sees ONLY the candidates they
    interview — name, current role, the documents, and their own feedback."""
    actor = await get_actor(request, db)
    if not actor.employee_id:
        return {"linked": False, "items": []}
    ivs = [i for i in (await db.execute(select(HireInterview).where(
        HireInterview.org_id == actor.org).order_by(HireInterview.scheduled_at.desc().nullslast()))).scalars().all()
        if L.is_panel_member(ser(i), actor.employee_id)]
    if not ivs:
        return {"linked": True, "items": []}
    apps = {str(a.id): a for a in (await db.execute(select(HireApplication).where(
        HireApplication.org_id == actor.org, HireApplication.id.in_({i.application_id for i in ivs})))).scalars().all()}
    cand_ids = {a.candidate_id for a in apps.values()}
    cands = {str(c.id): c for c in (await db.execute(select(HireCandidate).where(
        HireCandidate.org_id == actor.org, HireCandidate.id.in_(cand_ids)))).scalars().all()}
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(
        HireRequisition.org_id == actor.org, HireRequisition.id.in_({a.requisition_id for a in apps.values()})))).scalars().all()}
    docs = defaultdict(list)
    for d in (await db.execute(select(HireDocument).where(
            HireDocument.org_id == actor.org, HireDocument.candidate_id.in_(cand_ids)))).scalars().all():
        docs[str(d.candidate_id)].append(_doc_out(d))
    mine = {str(f.interview_id): f for f in (await db.execute(select(HireFeedback).where(
        HireFeedback.org_id == actor.org, HireFeedback.interviewer_employee_id == uid(actor.employee_id),
        HireFeedback.interview_id.in_([i.id for i in ivs])))).scalars().all()}
    # D2 bias guard: names hidden org-wide (setting) or per requisition (scorecard template)
    s = await load_settings(db, actor.org)
    hidden_reqs: set = set()
    if not actor.is_hr:
        try:
            from hiring_d2_models import HireScorecardTemplate
            hidden_reqs = {str(t.requisition_id) for t in (await db.execute(select(HireScorecardTemplate).where(
                HireScorecardTemplate.org_id == actor.org, HireScorecardTemplate.is_active.is_(True),
                HireScorecardTemplate.hide_names.is_(True)))).scalars().all()}
        except Exception:                                  # noqa: BLE001
            hidden_reqs = set()
    import hiring_d2_logic as D2
    items = []
    for i in ivs:
        a = apps.get(str(i.application_id))
        c = cands.get(str(a.candidate_id)) if a else None
        r = reqs.get(str(a.requisition_id)) if a else None
        f = mine.get(str(i.id))
        hide = (not actor.is_hr) and a is not None and (bool(s["scorecard_hide_names"]) or str(a.requisition_id) in hidden_reqs or "None" in hidden_reqs)
        items.append({
            "id": str(i.id), "round_no": i.round_no, "round_name": i.round_name,
            "scheduled_at": i.scheduled_at.isoformat() if i.scheduled_at else None, "mode": i.mode, "place": i.place,
            "panel": i.panel, "candidate_name": (D2.blind_label(str(a.id)) if hide else (_cand_name(c) if c else "")),
            "names_hidden": bool(hide),
            "current_title": c.current_title if c else None, "current_employer": (None if hide else (c.current_employer if c else None)),
            "designation": r.designation if r else None, "req_number": r.req_number if r else None,
            "stage": a.stage if a else None, "documents": docs.get(str(a.candidate_id), []) if a else [],
            "my_feedback": ser(f) if f else None,
            "feedback_status": "submitted" if f else "pending",
        })
    return {"linked": True, "items": items}


@router.post("/interviews/{iv_id}/feedback", status_code=201)
async def submit_feedback(iv_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """One feedback per interviewer per round. Only a panel member may write it."""
    actor = await get_actor(request, db)
    iv = await one(db, HireInterview, iv_id, actor.org, "Interview")
    if not L.is_panel_member(ser(iv), actor.employee_id):
        raise HTTPException(404, "Interview not found")       # not on the panel: it does not exist for you
    try:
        clean = L.clean_feedback(body)
    except L.RuleError as e:
        fail(e)
    f = HireFeedback(org_id=iv.org_id, interview_id=iv.id, application_id=iv.application_id,
                     interviewer_employee_id=uid(actor.employee_id), interviewer_name=actor.name, **clean)
    db.add(f)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "You have already submitted feedback for this round.")
    await ext.notify(actor.org, roles=["hr"], title="Interview feedback submitted",
                     body=f"{iv.round_name} · {actor.name}", link="/hiring/candidates", kind="info")
    return ser(f)


# ── reports ─────────────────────────────────────────────────────────────────
@router.get("/reports")
async def reports(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see hiring reports.")
    from hiring_offers import sync_joined
    await sync_joined(db, actor)
    org = actor.org
    reqs = [ser(r) for r in (await db.execute(select(HireRequisition).where(HireRequisition.org_id == org))).scalars().all()]
    apps = [ser(a) for a in (await db.execute(select(HireApplication).where(HireApplication.org_id == org))).scalars().all()]
    offers = [ser(o, skip=("token_hash", "breakup")) for o in (await db.execute(select(HireOffer).where(HireOffer.org_id == org))).scalars().all()]
    hist = [ser(h) for h in (await db.execute(select(HireStageHistory).where(HireStageHistory.org_id == org))).scalars().all()]
    return L.report(reqs, apps, offers, hist)
