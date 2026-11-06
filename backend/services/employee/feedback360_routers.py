"""Performance G4 — 360 feedback (/performance/feedback).

  GET  /performance/feedback/rounds                        rounds I take part in (HR: all)
  POST /performance/feedback/rounds                        HR: new round (questionnaire, threshold >= 3, rules)
  PATCH /performance/feedback/rounds/{id}                  HR: change a draft round
  POST /performance/feedback/rounds/{id}/subjects          HR: add people to review ({employee_ids} or {all: true})
  POST /performance/feedback/rounds/{id}/advance           HR: {action: open_nominations | start_collecting | close | release}
  GET  /performance/feedback/rounds/{id}                   detail for my part (HR: completion, never answers)
  GET  /performance/feedback/questions/default             our built-in questionnaire
  POST /performance/feedback/subjects/{sid}/nominations    nominate a reviewer (employee → waits for manager)
  POST /performance/feedback/nominations/{nid}/decide      manager / HR: {approve, reason}
  DELETE /performance/feedback/nominations/{nid}           withdraw a nomination (before an answer)
  GET  /performance/feedback/my-requests                   feedback I have been asked to give
  POST /performance/feedback/nominations/{nid}/submit      reviewer: {answers}
  POST /performance/feedback/nominations/{nid}/decline     reviewer: {reason}
  GET  /performance/feedback/subjects/{sid}/report         anonymity-safe report (self after release, manager, HR)

ANONYMITY: individual answers are never returned to anyone but the person who wrote them. The
report is perf_g4_logic.aggregate_360 (groups below the threshold pooled or hidden).
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402

import hiring_integrations as ext  # noqa: E402
import perf_g4_logic as G  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, emp_name, fail, get_actor, now, one, opt_uid, ser, to_date, uid  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from perf_g4_models import PerfFeedbackNomination, PerfFeedbackRound, PerfFeedbackSubject  # noqa: E402

router = APIRouter(prefix="/performance/feedback", tags=["performance-360"])


def _need_hr(actor: Actor, what: str):
    if not actor.is_hr:
        raise HTTPException(403, f"Only HR can {what}.")


async def _emps(db, org: str) -> dict:
    rows = (await db.execute(select(Employee).where(Employee.org_id == org, Employee.is_active.is_(True)))).scalars().all()
    return {str(e.id): e for e in rows}


def _round_out(r: PerfFeedbackRound) -> dict:
    return {**ser(r), "status_label": {"draft": "Draft", "nominating": "Picking reviewers", "collecting": "Collecting feedback",
                                         "closed": "Closed (HR / manager can read)", "released": "Released to employees"}.get(r.status, r.status)}


def _clean_round(body: dict, r: PerfFeedbackRound):
    if "name" in body:
        r.name = str(body.get("name") or "").strip()[:160]
    if not r.name:
        raise HTTPException(422, "Give the round a name.")
    if "questions" in body:
        try:
            r.questions = G.clean_questions(body["questions"])
        except RuleError as e:
            fail(e)
    for k, lo, hi in (("min_responses", G.MIN_THRESHOLD, 10), ("peers_min", 0, 15), ("peers_max", 1, 20)):
        if k in body and body[k] not in (None, ""):
            try:
                v = int(body[k])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{k.replace('_', ' ')} must be a whole number.")
            if not lo <= v <= hi:
                raise HTTPException(422, ("The anonymity threshold cannot be below 3 people." if k == "min_responses"
                                          else f"{k.replace('_', ' ')} must be between {lo} and {hi}."))
            setattr(r, k, v)
    if (r.peers_min or 0) > (r.peers_max or 0):
        raise HTTPException(422, "The minimum number of peers cannot be more than the maximum.")
    for k in ("include_upward", "include_self"):
        if k in body:
            setattr(r, k, bool(body[k]))
    if "nominate_by" in body:
        if body["nominate_by"] not in ("employee", "manager", "hr"):
            raise HTTPException(422, "Reviewers are picked by the employee (manager approves), the manager or HR.")
        r.nominate_by = body["nominate_by"]
    if "due_date" in body:
        r.due_date = to_date(body.get("due_date"))
    if "review_cycle_id" in body:
        r.review_cycle_id = opt_uid(body.get("review_cycle_id"))


@router.get("/questions/default")
async def default_questions(request: Request, db: AsyncSession = Depends(get_db)):
    await get_actor(request, db)
    return {"questions": G.DEFAULT_QUESTIONS + G.UPWARD_QUESTIONS, "min_threshold": G.MIN_THRESHOLD}


@router.get("/rounds")
async def list_rounds(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    rounds = (await db.execute(select(PerfFeedbackRound).where(PerfFeedbackRound.org_id == actor.org)
                               .order_by(PerfFeedbackRound.created_at.desc()))).scalars().all()
    if actor.is_hr:
        return {"rounds": [_round_out(r) for r in rounds], "is_hr": True, "linked": bool(actor.employee_id)}
    if not actor.employee_id:
        return {"rounds": [], "is_hr": False, "linked": False}
    me = uid(actor.employee_id)
    subj_rounds = {s.round_id for s in (await db.execute(select(PerfFeedbackSubject).where(
        PerfFeedbackSubject.org_id == actor.org, (PerfFeedbackSubject.employee_id == me) | (PerfFeedbackSubject.manager_id == me)))).scalars().all()}
    rev_rounds = {n.round_id for n in (await db.execute(select(PerfFeedbackNomination).where(
        PerfFeedbackNomination.org_id == actor.org, PerfFeedbackNomination.reviewer_employee_id == me))).scalars().all()}
    mine = subj_rounds | rev_rounds
    return {"rounds": [_round_out(r) for r in rounds if r.id in mine and r.status != "draft"], "is_hr": False, "linked": True}


@router.post("/rounds", status_code=201)
async def create_round(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "create a 360 feedback round")
    r = PerfFeedbackRound(org_id=uid(actor.org), name="", questions=G.clean_questions(G.DEFAULT_QUESTIONS + G.UPWARD_QUESTIONS),
                          min_responses=3, peers_min=3, peers_max=8, include_upward=True, include_self=True,
                          nominate_by="employee", status="draft", created_by=actor.user_id)
    _clean_round(body, r)
    db.add(r)
    await db.commit()
    await record_audit(request, "performance.360.round.create", entity_type="perf_fb_round", entity_id=str(r.id),
                       summary=f"360 round created: {r.name}", meta={"threshold": r.min_responses})
    return _round_out(r)


@router.patch("/rounds/{round_id}")
async def update_round(round_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "change a 360 feedback round")
    r = await one(db, PerfFeedbackRound, round_id, actor.org, "Feedback round")
    if r.status not in ("draft", "nominating"):
        raise HTTPException(409, "Feedback is already being collected; the questions and rules are frozen.")
    if r.status == "nominating" and "questions" in body:
        raise HTTPException(409, "The questions are frozen once reviewers are being picked.")
    _clean_round(body, r)
    await db.commit()
    return _round_out(r)


async def _auto_nominate(db, r: PerfFeedbackRound, s: PerfFeedbackSubject, emps: dict):
    """Self, the manager, and (upward) the direct reports — added for every subject."""
    have = {str(n.reviewer_employee_id) for n in (await db.execute(select(PerfFeedbackNomination).where(
        PerfFeedbackNomination.subject_id == s.id))).scalars().all()}
    rows = []
    if r.include_self:
        rows.append((str(s.employee_id), "self"))
    if s.manager_id:
        rows.append((str(s.manager_id), "manager"))
    if r.include_upward:
        rows += [(i, "direct_report") for i, e in emps.items() if str(e.manager_id or "") == str(s.employee_id)]
    for eid, rel in rows:
        if eid in have or eid not in emps:
            continue
        have.add(eid)
        db.add(PerfFeedbackNomination(org_id=r.org_id, round_id=r.id, subject_id=s.id, reviewer_employee_id=uid(eid),
                                      reviewer_name=emp_name(emps[eid]), relationship=rel, nominated_by="hr", status="approved",
                                      decided_at=now()))


@router.post("/rounds/{round_id}/subjects", status_code=201)
async def add_subjects(round_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "choose who is reviewed")
    r = await one(db, PerfFeedbackRound, round_id, actor.org, "Feedback round")
    if r.status not in ("draft", "nominating"):
        raise HTTPException(409, "People can be added only before feedback collection starts.")
    emps = await _emps(db, actor.org)
    ids = list(emps) if body.get("all") else [str(opt_uid(i)) for i in body.get("employee_ids") or [] if i]
    if not ids:
        raise HTTPException(422, "Pick at least one employee.")
    have = {str(s.employee_id) for s in (await db.execute(select(PerfFeedbackSubject).where(PerfFeedbackSubject.round_id == r.id))).scalars().all()}
    added = 0
    for i in ids:
        e = emps.get(i)
        if e is None or i in have:
            continue
        m = emps.get(str(e.manager_id)) if e.manager_id else None
        s = PerfFeedbackSubject(org_id=r.org_id, round_id=r.id, employee_id=e.id, employee_name=emp_name(e),
                                manager_id=m.id if m else None, manager_name=emp_name(m) if m else None)
        db.add(s)
        await db.flush()
        await _auto_nominate(db, r, s, emps)
        added += 1
    await db.commit()
    return {"added": added, "skipped": len(ids) - added}


@router.post("/rounds/{round_id}/advance")
async def advance_round(round_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "move a 360 round on")
    r = await one(db, PerfFeedbackRound, round_id, actor.org, "Feedback round")
    action = body.get("action")
    if action not in G.ROUND_NEXT:
        raise HTTPException(422, "Unknown step.")
    frm, to = G.ROUND_NEXT[action]
    if r.status != frm:
        raise HTTPException(409, f"This round is {r.status}; that step is not possible now.")
    subjects = (await db.execute(select(PerfFeedbackSubject).where(PerfFeedbackSubject.round_id == r.id))).scalars().all()
    if action in ("open_nominations", "start_collecting") and not subjects:
        raise HTTPException(409, "Add the people to be reviewed first.")
    await cas(db, PerfFeedbackRound, r, "status", frm, status=to)
    await db.commit()
    noms = (await db.execute(select(PerfFeedbackNomination).where(PerfFeedbackNomination.round_id == r.id))).scalars().all()
    if action == "open_nominations":
        users = await ext.user_ids_for_employees(db, actor.org, [s.employee_id for s in subjects] + [s.manager_id for s in subjects if s.manager_id])
        await ext.notify(actor.org, user_ids=users, title=f"360 feedback: pick reviewers ({r.name})",
                         body="Choose the colleagues who should give feedback. Your manager approves the list.",
                         link="/performance/feedback", kind="info")
    elif action == "start_collecting":
        asked = [n.reviewer_employee_id for n in noms if n.status == "approved"]
        users = await ext.user_ids_for_employees(db, actor.org, asked)
        await ext.notify(actor.org, user_ids=users, title=f"You have been asked for feedback ({r.name})",
                         body="Your answers to peers and managers are combined with others and never shown with your name.",
                         link="/performance/feedback", kind="info")
    elif action == "release":
        users = await ext.user_ids_for_employees(db, actor.org, [s.employee_id for s in subjects])
        await ext.notify(actor.org, user_ids=users, title=f"Your 360 feedback report is ready ({r.name})",
                         link="/performance/feedback", kind="info")
    await record_audit(request, "performance.360.round.advance", entity_type="perf_fb_round", entity_id=str(r.id),
                       summary=f"360 round {r.name}: {frm} → {to}", meta={"action": action})
    await db.refresh(r)
    return _round_out(r)


def _nom_public(n: PerfFeedbackNomination, scope: str, round_status: str) -> dict:
    """What a person may see about a nomination. The subject never learns who has answered."""
    d = {"id": str(n.id), "reviewer_employee_id": str(n.reviewer_employee_id), "reviewer_name": n.reviewer_name,
         "relationship": n.relationship, "relationship_label": G.REL_LABEL.get(n.relationship, n.relationship),
         "nominated_by": n.nominated_by}
    st = n.status
    if scope == "self" and st in ("submitted", "declined"):
        st = "approved"
    d["status"] = st
    return d


@router.get("/rounds/{round_id}")
async def get_round(round_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    r = await one(db, PerfFeedbackRound, round_id, actor.org, "Feedback round")
    subjects = (await db.execute(select(PerfFeedbackSubject).where(PerfFeedbackSubject.round_id == r.id)
                                 .order_by(PerfFeedbackSubject.employee_name))).scalars().all()
    noms = (await db.execute(select(PerfFeedbackNomination).where(PerfFeedbackNomination.round_id == r.id))).scalars().all()
    by_subject: dict = {}
    for n in noms:
        by_subject.setdefault(n.subject_id, []).append(n)
    out = []
    for s in subjects:
        if actor.employee_id and str(s.employee_id) == actor.employee_id:
            scope = "self"                      # checked FIRST: an HR person reviewed in the round is just the subject
        elif actor.employee_id and str(s.manager_id or "") == actor.employee_id:
            scope = "manager"
        elif actor.is_hr:
            scope = "hr"
        else:
            continue
        ns = by_subject.get(s.id, [])
        row = {**ser(s), "scope": scope,
               "counts": {rel: sum(1 for n in ns if n.relationship == rel and n.status in ("approved", "submitted")) for rel in G.RELATIONSHIPS},
               "peers_pending": sum(1 for n in ns if n.status == "proposed")}
        if scope in ("hr", "manager"):
            # who has answered (to chase), never what they wrote
            row["nominations"] = [{**_nom_public(n, scope, r.status), "status": n.status} for n in ns]
            row["submitted"] = sum(1 for n in ns if n.status == "submitted" and n.relationship != "self")
        else:
            row["nominations"] = [_nom_public(n, scope, r.status) for n in ns if n.relationship != "self"]
        row["can_read_report"] = G.report_scope(actor, str(s.employee_id), str(s.manager_id) if s.manager_id else None, r.status) is not None
        out.append(row)
    if not actor.is_hr and not out and not any(str(n.reviewer_employee_id) == actor.employee_id for n in noms):
        raise HTTPException(404, "Feedback round not found")
    return {"round": _round_out(r) if actor.is_hr else {k: v for k, v in _round_out(r).items() if k not in ("created_by",)},
            "subjects": out, "is_hr": actor.is_hr}


@router.post("/subjects/{subject_id}/nominations", status_code=201)
async def nominate(subject_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    s = await one(db, PerfFeedbackSubject, subject_id, actor.org, "Person in this round")
    r = await one(db, PerfFeedbackRound, s.round_id, actor.org, "Feedback round")
    if r.status != "nominating":
        raise HTTPException(409, "Reviewers can be picked only while the round is in 'Picking reviewers'.")
    rel_actor = ("self" if actor.employee_id == str(s.employee_id) else
                 "manager" if actor.employee_id and actor.employee_id == str(s.manager_id or "") else
                 "hr" if actor.is_hr else None)
    if rel_actor is None:
        raise HTTPException(404, "Person in this round not found")
    try:
        status = G.nomination_rule(rel_actor, body.get("relationship") or "peer", r.nominate_by)
    except RuleError as e:
        fail(e)
    emps = await _emps(db, actor.org)
    rid = str(opt_uid(body.get("reviewer_employee_id")) or "")
    rev = emps.get(rid)
    if rev is None:
        raise HTTPException(422, "Pick the colleague from the list.")
    if rid == str(s.employee_id):
        raise HTTPException(422, "Self feedback is added automatically.")
    if s.manager_id and rid == str(s.manager_id):
        raise HTTPException(422, "The manager is added automatically.")
    ns = (await db.execute(select(PerfFeedbackNomination).where(PerfFeedbackNomination.subject_id == s.id))).scalars().all()
    peers = [n for n in ns if n.relationship in ("peer", "other") and n.status in ("proposed", "approved", "submitted")]
    if (body.get("relationship") or "peer") in ("peer", "other") and len(peers) >= (r.peers_max or 8):
        raise HTTPException(409, f"This round allows at most {r.peers_max} peers per person.")
    n = PerfFeedbackNomination(org_id=s.org_id, round_id=r.id, subject_id=s.id, reviewer_employee_id=rev.id, reviewer_name=emp_name(rev),
                               relationship=body.get("relationship") or "peer", nominated_by=rel_actor, status=status,
                               decided_by=actor.user_id if status == "approved" else None, decided_at=now() if status == "approved" else None)
    db.add(n)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "That colleague is already on the list.")
    if status == "proposed" and s.manager_id:
        await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [s.manager_id]),
                         title=f"Approve 360 reviewers for {s.employee_name}", link="/performance/feedback", kind="info")
    return _nom_public(n, rel_actor, r.status)


@router.post("/nominations/{nom_id}/decide")
async def decide_nomination(nom_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    n = await one(db, PerfFeedbackNomination, nom_id, actor.org, "Nomination")
    s = await one(db, PerfFeedbackSubject, n.subject_id, actor.org, "Person in this round")
    is_mgr = bool(actor.employee_id) and actor.employee_id == str(s.manager_id or "")
    if actor.employee_id and actor.employee_id == str(s.employee_id):
        raise HTTPException(403, "You cannot approve reviewers for yourself.")
    if not (is_mgr or actor.is_hr):
        raise HTTPException(403, "Only the manager or HR approves reviewers.")
    if n.status != "proposed":
        raise HTTPException(409, "This nomination has already been decided.")
    to = "approved" if body.get("approve") else "rejected"
    await cas(db, PerfFeedbackNomination, n, "status", "proposed", status=to, decided_by=actor.user_id, decided_at=now())
    await db.commit()
    return {"id": nom_id, "status": to}


@router.delete("/nominations/{nom_id}")
async def withdraw_nomination(nom_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    n = await one(db, PerfFeedbackNomination, nom_id, actor.org, "Nomination")
    s = await one(db, PerfFeedbackSubject, n.subject_id, actor.org, "Person in this round")
    r = await one(db, PerfFeedbackRound, n.round_id, actor.org, "Feedback round")
    own = actor.employee_id == str(s.employee_id) and n.nominated_by == "self" and n.status == "proposed"
    if not (own or actor.is_hr or (actor.employee_id and actor.employee_id == str(s.manager_id or ""))):
        raise HTTPException(403, "You cannot remove this reviewer.")
    if n.status == "submitted" or n.relationship in ("self",) or r.status not in ("draft", "nominating"):
        raise HTTPException(409, "This reviewer can no longer be removed.")
    await db.delete(n)
    await db.commit()
    return {"deleted": nom_id}


@router.get("/my-requests")
async def my_requests(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.employee_id:
        return {"linked": False, "items": []}
    noms = (await db.execute(select(PerfFeedbackNomination).where(
        PerfFeedbackNomination.org_id == actor.org, PerfFeedbackNomination.reviewer_employee_id == uid(actor.employee_id),
        PerfFeedbackNomination.status.in_(("approved", "submitted", "declined"))))).scalars().all()
    rounds = {r.id: r for r in (await db.execute(select(PerfFeedbackRound).where(PerfFeedbackRound.org_id == actor.org))).scalars().all()}
    subs = {s.id: s for s in (await db.execute(select(PerfFeedbackSubject).where(PerfFeedbackSubject.org_id == actor.org))).scalars().all()}
    items = []
    for n in noms:
        r, s = rounds.get(n.round_id), subs.get(n.subject_id)
        if not r or not s or r.status not in ("collecting", "closed", "released"):
            continue
        items.append({"id": str(n.id), "round": r.name, "round_status": r.status, "due_date": r.due_date.isoformat() if r.due_date else None,
                      "subject_name": s.employee_name if n.relationship != "self" else "Yourself", "relationship": n.relationship,
                      "relationship_label": G.REL_LABEL.get(n.relationship, n.relationship), "status": n.status,
                      "questions": G.questions_for(r.questions or [], n.relationship),
                      "my_answers": n.answers if n.status == "submitted" else None,
                      "anonymous": n.relationship in G.ANON_GROUPS, "threshold": r.min_responses,
                      "can_answer": r.status == "collecting" and n.status == "approved"})
    return {"linked": True, "items": sorted(items, key=lambda x: (x["status"] != "approved", x["round"], x["subject_name"] or ""))}


async def _mine(db, actor: Actor, nom_id: str) -> tuple[PerfFeedbackNomination, PerfFeedbackRound]:
    n = await one(db, PerfFeedbackNomination, nom_id, actor.org, "Feedback request")
    if not actor.employee_id or str(n.reviewer_employee_id) != actor.employee_id:
        raise HTTPException(404, "Feedback request not found")
    r = await one(db, PerfFeedbackRound, n.round_id, actor.org, "Feedback round")
    if r.status != "collecting":
        raise HTTPException(409, "This round is not collecting feedback now.")
    if n.status != "approved":
        raise HTTPException(409, "You have already answered or declined this request.")
    return n, r


@router.post("/nominations/{nom_id}/submit")
async def submit_feedback(nom_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    n, r = await _mine(db, actor, nom_id)
    try:
        answers = G.check_answers(G.questions_for(r.questions or [], n.relationship), body.get("answers") or {})
    except RuleError as e:
        fail(e)
    await cas(db, PerfFeedbackNomination, n, "status", "approved", status="submitted", answers=answers, submitted_at=now())
    await db.commit()
    return {"id": nom_id, "status": "submitted"}


@router.post("/nominations/{nom_id}/decline")
async def decline_feedback(nom_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    n, r = await _mine(db, actor, nom_id)
    if n.relationship == "self":
        raise HTTPException(409, "Self feedback cannot be declined; skip it if you prefer.")
    await cas(db, PerfFeedbackNomination, n, "status", "approved", status="declined", decided_at=now())
    await db.commit()
    return {"id": nom_id, "status": "declined"}


@router.get("/subjects/{subject_id}/report")
async def report(subject_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    s = await one(db, PerfFeedbackSubject, subject_id, actor.org, "Report")
    r = await one(db, PerfFeedbackRound, s.round_id, actor.org, "Feedback round")
    scope = G.report_scope(actor, str(s.employee_id), str(s.manager_id) if s.manager_id else None, r.status)
    if scope is None:
        raise HTTPException(404, "Report not found")
    if scope == "hr" and r.status not in ("collecting", "closed", "released"):
        raise HTTPException(409, "There is nothing to report until feedback collection starts.")
    noms = (await db.execute(select(PerfFeedbackNomination).where(PerfFeedbackNomination.subject_id == s.id))).scalars().all()
    responses = [{"relationship": n.relationship, "answers": n.answers or {}} for n in noms if n.status == "submitted"]
    agg = G.aggregate_360([q for q in (r.questions or [])], responses, r.min_responses)
    await record_audit(request, "performance.360.report.view", entity_type="perf_fb_subject", entity_id=str(s.id),
                       summary=f"360 report read: {s.employee_name}", meta={"scope": scope})
    return {"subject": {"id": str(s.id), "employee_name": s.employee_name, "manager_name": s.manager_name},
            "round": {"id": str(r.id), "name": r.name, "status": r.status}, "scope": scope,
            "questions": r.questions or [], **agg,
            "asked": sum(1 for n in noms if n.status in ("approved", "submitted", "declined") and n.relationship != "self")}
