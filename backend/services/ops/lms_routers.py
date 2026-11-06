"""LMS depth routes — Stream D7. Mounted under /engage/lms (gateway prefix `engage` → ops service).

Paths        GET/POST /paths   PATCH /paths/{id}   POST /paths/{id}/assign   GET /paths/{id}/progress
Rules        GET/POST /rules   PATCH/DELETE /rules/{id}   POST /rules/{id}/run   POST /rules/run-all
Bank         GET/POST /questions   PATCH/DELETE /questions/{id}
Packages     POST /courses/{cid}/package (zip)   GET /packages/{pid}/{path}  (sandboxed)
Quiz         GET /assignments/{aid}/quiz-set   POST /assignments/{aid}/reset (HR)
Reports      GET /team?manager_id=   GET /report?by=department|course_title|manager_name   GET /my
Reminders    POST /reminders/run (HR; the service tick calls run_reminders too)
"""
from __future__ import annotations

import sys
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import engage_common as C  # noqa: E402
import engage_logic as L  # noqa: E402
import lms_logic as LL  # noqa: E402
from database import get_db  # noqa: E402
from engage_models import Course, CourseAssignment  # noqa: E402
from lms_models import LearningPath, LmsPackage, LmsQuestion, LmsReminderLog, LmsRule, PathAssignment  # noqa: E402

router = APIRouter(prefix="/engage/lms", tags=["lms-depth"])


# ══════════════════════════ helpers ═══════════════════════════════════════════

async def roster(request: Request, org: str) -> dict[str, dict]:
    """id → employee with login role, entity fields and hire_date. The employee API (scoped to the caller)
    is merged with the org directory so HR gets everyone; a missing API is not fatal."""
    out = {e["id"]: dict(e) for e in await directory.org_employees(org)}
    for k, e in (await C.employees(request)).items():
        out[k] = {**out.get(k, {}), **e}
    roles = {u.get("employee_id"): u.get("role") for u in await directory.org_users(org) if u.get("employee_id")}
    for k, e in out.items():
        e.setdefault("role", roles.get(k))
        e["id"] = k
    return out


def _team_ids(emps: dict[str, dict], manager_id: str) -> list[str]:
    return [k for k, e in emps.items() if str(e.get("manager_id") or "") == manager_id]


async def _courses(db, org) -> dict[str, Course]:
    return {str(c.id): c for c in (await db.execute(select(Course).where(Course.org_id == org))).scalars().all()}


def _asg_row(a: CourseAssignment, c: Course | None, today: date, emps: dict[str, dict] | None = None) -> dict:
    mods = list((c.modules if c else []) or [])
    e = (emps or {}).get(str(a.employee_id)) or {}
    return {"id": str(a.id), "course_id": str(a.course_id), "course_title": c.title if c else None, "category": c.category if c else None,
            "mandatory": bool(c and c.mandatory), "employee_id": str(a.employee_id), "employee_name": a.employee_name or C.emp_name(e),
            "department": e.get("department"), "manager_name": e.get("manager_name"), "due_on": C.iso(a.due_on), "status": a.status,
            "state": L.assignment_state(a.status, a.due_on, today), "completed_at": C.iso(a.completed_at), "quiz_score": a.quiz_score,
            "quiz_attempts": a.quiz_attempts or 0, "certificate_no": a.certificate_no, "assigned_at": C.iso(a.assigned_at),
            "progress_pct": L.progress_pct(mods, list(a.completed_modules or []), bool(a.quiz_passed), bool(c and (c.quiz or c.question_tags))),
            "path_id": str(a.path_id) if a.path_id else None, "rule_id": str(a.rule_id) if a.rule_id else None}


async def _assign_course(db, org: str, c: Course, ids: list[str], emps: dict, due: date | None, actor: str,
                         rule_id=None, path_id=None) -> list[str]:
    existing = {str(a.employee_id) for a in (await db.execute(select(CourseAssignment).where(
        CourseAssignment.org_id == org, CourseAssignment.course_id == c.id))).scalars().all()}
    made = []
    for i in ids:
        if i in existing or i not in emps:
            continue
        db.add(CourseAssignment(org_id=uuid.UUID(org), course_id=c.id, employee_id=uuid.UUID(i), employee_name=C.emp_name(emps[i]),
                                due_on=due, assigned_by=actor, rule_id=rule_id, path_id=path_id))
        made.append(i)
    return made


# ══════════════════════════ learning paths ════════════════════════════════════

def _path_out(p: LearningPath, courses: dict[str, Course], on_path: int | None = None, done: int | None = None) -> dict:
    return {"id": str(p.id), "title": p.title, "description": p.description, "course_ids": list(p.course_ids or []),
            "courses": [{"id": cid, "title": courses[cid].title} for cid in (p.course_ids or []) if cid in courses],
            "mandatory": bool(p.mandatory), "active": bool(p.active), "created_by": p.created_by, "created_at": C.iso(p.created_at),
            "people": on_path, "completed": done}


@router.get("/paths")
async def list_paths(request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    courses = await _courses(db, org)
    rows = (await db.execute(select(LearningPath).where(LearningPath.org_id == org).order_by(LearningPath.title))).scalars().all()
    if not C.is_hr(request):
        rows = [p for p in rows if p.active]
    pa = (await db.execute(select(PathAssignment).where(PathAssignment.org_id == org))).scalars().all()
    asg = (await db.execute(select(CourseAssignment).where(CourseAssignment.org_id == org))).scalars().all()
    out = []
    for p in rows:
        mine = [x for x in pa if x.path_id == p.id]
        done = 0
        for x in mine:
            pr = LL.path_progress(p.course_ids or [], [{"course_id": str(a.course_id), "status": a.status, "progress_pct": 0}
                                                        for a in asg if str(a.employee_id) == str(x.employee_id)])
            done += 1 if pr["status"] == "completed" else 0
        out.append(_path_out(p, courses, len(mine), done))
    return {"paths": out, "can_manage": C.is_hr(request)}


class PathIn(BaseModel):
    title: str
    description: str | None = None
    course_ids: list[str] = []
    mandatory: bool = False
    active: bool = True


@router.post("/paths", status_code=201)
async def create_path(body: PathIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "create a learning path")
    org = C.org_of(request)
    if not body.title.strip():
        raise HTTPException(422, "Give the path a title")
    courses = await _courses(db, org)
    ids = [c for c in body.course_ids if c in courses]
    if not ids:
        raise HTTPException(422, "Pick at least one course")
    p = LearningPath(org_id=uuid.UUID(org), title=body.title.strip()[:200], description=(body.description or "")[:4000] or None,
                     course_ids=ids, mandatory=body.mandatory, active=body.active, created_by=C.actor_of(request))
    db.add(p)
    await db.commit()
    return _path_out(p, courses, 0, 0)


@router.patch("/paths/{pid}")
async def update_path(pid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change a learning path")
    org = C.org_of(request)
    p = (await db.execute(select(LearningPath).where(LearningPath.id == C.to_uuid(pid, "path"), LearningPath.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Path not found")
    courses = await _courses(db, org)
    if "title" in body and str(body["title"]).strip():
        p.title = str(body["title"]).strip()[:200]
    if "description" in body:
        p.description = str(body["description"] or "")[:4000] or None
    if "course_ids" in body:
        ids = [c for c in (body["course_ids"] or []) if c in courses]
        if not ids:
            raise HTTPException(422, "Keep at least one course")
        p.course_ids = ids
    for k in ("mandatory", "active"):
        if k in body:
            setattr(p, k, bool(body[k]))
    await db.commit()
    return _path_out(p, courses)


class PathAssignIn(BaseModel):
    employee_ids: list[str] | None = None
    departments: list[str] | None = None
    due_on: str | None = None


@router.post("/paths/{pid}/assign", status_code=201)
async def assign_path(pid: str, body: PathAssignIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "assign a learning path")
    org = C.org_of(request)
    p = (await db.execute(select(LearningPath).where(LearningPath.id == C.to_uuid(pid, "path"), LearningPath.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Path not found")
    emps = await roster(request, org)
    ids = set(body.employee_ids or [])
    if body.departments:
        ids |= {k for k, e in emps.items() if (e.get("department") or "") in body.departments}
    ids = [i for i in ids if i in emps and emps[i].get("status") != "terminated"]
    if not ids:
        raise HTTPException(422, "Pick at least one person or department")
    due = date.fromisoformat(body.due_on) if body.due_on else None
    courses = await _courses(db, org)
    existing = {str(x.employee_id) for x in (await db.execute(select(PathAssignment).where(PathAssignment.path_id == p.id))).scalars().all()}
    made = 0
    for i in ids:
        if i in existing:
            continue
        db.add(PathAssignment(org_id=uuid.UUID(org), path_id=p.id, employee_id=uuid.UUID(i), employee_name=C.emp_name(emps[i]),
                              due_on=due, assigned_by=C.actor_of(request)))
        made += 1
    for cid in p.course_ids or []:
        if cid in courses:
            await _assign_course(db, org, courses[cid], [i for i in ids if i not in existing], emps, due, C.actor_of(request), path_id=p.id)
    await db.commit()
    new_ids = [i for i in ids if i not in existing]
    if new_ids:
        await notify(org, employee_ids=new_ids, kind="general", title=f"New learning path: {p.title}" + (f" — due {due.isoformat()}" if due else ""),
                     body=p.description or "", link="/learning/my", email=False)
    return {"assigned": made, "already": len(ids) - made}


@router.get("/paths/{pid}/progress")
async def path_progress(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    p = (await db.execute(select(LearningPath).where(LearningPath.id == C.to_uuid(pid, "path"), LearningPath.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Path not found")
    today = await org_today(org)
    courses = await _courses(db, org)
    pa = (await db.execute(select(PathAssignment).where(PathAssignment.path_id == p.id))).scalars().all()
    if not C.is_hr(request):
        me = C.emp_of(request) or ""
        pa = [x for x in pa if str(x.employee_id) == me]
    asg = (await db.execute(select(CourseAssignment).where(CourseAssignment.org_id == org))).scalars().all()
    people = []
    for x in pa:
        mine = [_asg_row(a, courses.get(str(a.course_id)), today) for a in asg if str(a.employee_id) == str(x.employee_id)]
        pr = LL.path_progress(p.course_ids or [], mine)
        for s in pr["steps"]:
            s["title"] = courses[s["course_id"]].title if s["course_id"] in courses else None
        people.append({"employee_id": str(x.employee_id), "employee_name": x.employee_name, "due_on": C.iso(x.due_on), **pr})
    return {"path": _path_out(p, courses, len(pa)), "people": people}


# ══════════════════════════ auto-assignment rules ═════════════════════════════

def _rule_out(r: LmsRule, courses: dict[str, Course], paths: dict[str, LearningPath]) -> dict:
    return {"id": str(r.id), "name": r.name, "course_id": str(r.course_id) if r.course_id else None,
            "course_title": courses[str(r.course_id)].title if r.course_id and str(r.course_id) in courses else None,
            "path_id": str(r.path_id) if r.path_id else None,
            "path_title": paths[str(r.path_id)].title if r.path_id and str(r.path_id) in paths else None,
            "roles": list(r.roles or []), "departments": list(r.departments or []), "entities": list(r.entities or []),
            "locations": list(r.locations or []), "joined_from": C.iso(r.joined_from), "joined_to": C.iso(r.joined_to),
            "due_days": r.due_days, "new_joiners_only": bool(r.new_joiners_only), "active": bool(r.active),
            "last_run_at": C.iso(r.last_run_at), "last_assigned": r.last_assigned or 0, "created_by": r.created_by, "created_at": C.iso(r.created_at)}


def _rule_dict(r: LmsRule) -> dict:
    return {"roles": r.roles, "departments": r.departments, "entities": r.entities, "locations": r.locations,
            "joined_from": r.joined_from, "joined_to": r.joined_to, "due_days": r.due_days,
            "new_joiners_only": r.new_joiners_only, "created_at": r.created_at}


@router.get("/rules")
async def list_rules(request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "see assignment rules")
    org = C.org_of(request)
    courses = await _courses(db, org)
    paths = {str(p.id): p for p in (await db.execute(select(LearningPath).where(LearningPath.org_id == org))).scalars().all()}
    rows = (await db.execute(select(LmsRule).where(LmsRule.org_id == org).order_by(LmsRule.name))).scalars().all()
    return {"rules": [_rule_out(r, courses, paths) for r in rows],
            "note": "Content placeholders only: the organisation uploads its own workplace-harassment / health-and-safety / code-of-conduct material. "
                    "Nothing legal is pre-written here."}


class RuleIn(BaseModel):
    name: str
    course_id: str | None = None
    path_id: str | None = None
    roles: list[str] = []
    departments: list[str] = []
    entities: list[str] = []
    locations: list[str] = []
    joined_from: str | None = None
    joined_to: str | None = None
    due_days: int = 30
    new_joiners_only: bool = False
    active: bool = True


def _apply_rule_body(r: LmsRule, body: dict):
    for k in ("name",):
        if k in body and str(body[k]).strip():
            r.name = str(body[k]).strip()[:200]
    for k in ("roles", "departments", "entities", "locations"):
        if k in body:
            setattr(r, k, [str(x)[:120] for x in (body[k] or []) if str(x).strip()][:50])
    for k in ("joined_from", "joined_to"):
        if k in body:
            setattr(r, k, date.fromisoformat(body[k]) if body[k] else None)
    if "due_days" in body:
        d = int(body["due_days"] or 0)
        if not 0 <= d <= 730:
            raise HTTPException(422, "Due days must be 0 to 730")
        r.due_days = d
    for k in ("new_joiners_only", "active"):
        if k in body:
            setattr(r, k, bool(body[k]))


@router.post("/rules", status_code=201)
async def create_rule(body: RuleIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "create an assignment rule")
    org = C.org_of(request)
    if not body.name.strip():
        raise HTTPException(422, "Name the rule")
    if bool(body.course_id) == bool(body.path_id):
        raise HTTPException(422, "Pick one course OR one learning path")
    courses = await _courses(db, org)
    paths = {str(p.id): p for p in (await db.execute(select(LearningPath).where(LearningPath.org_id == org))).scalars().all()}
    if body.course_id and body.course_id not in courses:
        raise HTTPException(404, "Course not found")
    if body.path_id and body.path_id not in paths:
        raise HTTPException(404, "Path not found")
    r = LmsRule(org_id=uuid.UUID(org), name="rule", course_id=uuid.UUID(body.course_id) if body.course_id else None,
                path_id=uuid.UUID(body.path_id) if body.path_id else None, created_by=C.actor_of(request))
    _apply_rule_body(r, body.model_dump())
    db.add(r)
    await db.commit()
    return _rule_out(r, courses, paths)


@router.patch("/rules/{rid}")
async def update_rule(rid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change an assignment rule")
    org = C.org_of(request)
    r = (await db.execute(select(LmsRule).where(LmsRule.id == C.to_uuid(rid, "rule"), LmsRule.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    _apply_rule_body(r, body)
    await db.commit()
    courses = await _courses(db, org)
    paths = {str(p.id): p for p in (await db.execute(select(LearningPath).where(LearningPath.org_id == org))).scalars().all()}
    return _rule_out(r, courses, paths)


@router.delete("/rules/{rid}")
async def delete_rule(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "delete an assignment rule")
    org = C.org_of(request)
    r = (await db.execute(select(LmsRule).where(LmsRule.id == C.to_uuid(rid, "rule"), LmsRule.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    await db.delete(r)
    await db.commit()
    return {"ok": True}


async def run_rule(db, org: str, r: LmsRule, emps: dict[str, dict], today: date, actor: str) -> int:
    """Assign the rule's course / path to everyone it matches who does not have it yet. Returns how many."""
    courses = await _courses(db, org)
    rule = _rule_dict(r)
    made_total = 0
    new_people: list[str] = []
    title = ""
    if r.course_id and str(r.course_id) in courses:
        c = courses[str(r.course_id)]
        title = c.title
        have = {str(a.employee_id) for a in (await db.execute(select(CourseAssignment).where(
            CourseAssignment.org_id == org, CourseAssignment.course_id == c.id))).scalars().all()}
        plan = LL.plan_assignments(rule, list(emps.values()), have, today)
        for p in plan:
            db.add(CourseAssignment(org_id=uuid.UUID(org), course_id=c.id, employee_id=uuid.UUID(p["employee_id"]),
                                    employee_name=C.emp_name(emps[p["employee_id"]]), due_on=p["due_on"], assigned_by=actor, rule_id=r.id))
            new_people.append(p["employee_id"])
        made_total = len(plan)
    elif r.path_id:
        p_ = (await db.execute(select(LearningPath).where(LearningPath.id == r.path_id))).scalar_one_or_none()
        if p_:
            title = p_.title
            have = {str(x.employee_id) for x in (await db.execute(select(PathAssignment).where(PathAssignment.path_id == p_.id))).scalars().all()}
            plan = LL.plan_assignments(rule, list(emps.values()), have, today)
            for p in plan:
                db.add(PathAssignment(org_id=uuid.UUID(org), path_id=p_.id, employee_id=uuid.UUID(p["employee_id"]),
                                      employee_name=C.emp_name(emps[p["employee_id"]]), due_on=p["due_on"], assigned_by=actor))
                for cid in p_.course_ids or []:
                    if cid in courses:
                        await _assign_course(db, org, courses[cid], [p["employee_id"]], emps, p["due_on"], actor, rule_id=r.id, path_id=p_.id)
                new_people.append(p["employee_id"])
            made_total = len(plan)
    r.last_run_at = C.now()
    r.last_assigned = made_total
    await db.commit()
    if new_people:
        await notify(org, employee_ids=new_people[:2000], kind="general", title=f"New training assigned: {title}",
                     body=f"Assigned by rule '{r.name}'. Open My learning to start.", link="/learning/my", email=False)
    return made_total


@router.post("/rules/{rid}/run")
async def run_one(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "run an assignment rule")
    org = C.org_of(request)
    r = (await db.execute(select(LmsRule).where(LmsRule.id == C.to_uuid(rid, "rule"), LmsRule.org_id == org))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    n = await run_rule(db, org, r, await roster(request, org), await org_today(org), C.actor_of(request))
    return {"assigned": n}


@router.post("/rules/run-all")
async def run_all(request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "run assignment rules")
    org = C.org_of(request)
    emps = await roster(request, org)
    today = await org_today(org)
    out = {}
    for r in (await db.execute(select(LmsRule).where(LmsRule.org_id == org, LmsRule.active.is_(True)))).scalars().all():
        out[str(r.id)] = await run_rule(db, org, r, emps, today, C.actor_of(request))
    return {"assigned": sum(out.values()), "by_rule": out}


# ══════════════════════════ question bank ═════════════════════════════════════

def _q_out(q: LmsQuestion, with_answer: bool) -> dict:
    d = {"id": str(q.id), "tag": q.tag, "q": q.q, "options": list(q.options or []), "active": bool(q.active),
         "created_by": q.created_by, "created_at": C.iso(q.created_at)}
    if with_answer:
        d["answer"] = q.answer
    return d


@router.get("/questions")
async def list_questions(request: Request, tag: str | None = None, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "see the question bank")
    org = C.org_of(request)
    q = select(LmsQuestion).where(LmsQuestion.org_id == org)
    if tag:
        q = q.where(LmsQuestion.tag == tag)
    rows = (await db.execute(q.order_by(LmsQuestion.tag, LmsQuestion.created_at))).scalars().all()
    tags = sorted({r.tag for r in rows})
    return {"questions": [_q_out(r, True) for r in rows], "tags": tags}


class QuestionIn(BaseModel):
    tag: str
    q: str
    options: list[str]
    answer: int
    active: bool = True


def _check_q(tag: str, text: str, options: list, answer) -> tuple[str, str, list[str], int]:
    tag = (tag or "").strip()[:60]
    text = (text or "").strip()[:400]
    opts = [str(o).strip()[:200] for o in (options or []) if str(o).strip()][:8]
    if not tag or not text or len(opts) < 2:
        raise HTTPException(422, "A question needs a tag, text and at least two options")
    try:
        ans = int(answer)
    except (TypeError, ValueError):
        raise HTTPException(422, "Mark the correct option")
    if not 0 <= ans < len(opts):
        raise HTTPException(422, "The correct option is out of range")
    return tag, text, opts, ans


@router.post("/questions", status_code=201)
async def create_question(body: QuestionIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "add questions")
    org = C.org_of(request)
    tag, text, opts, ans = _check_q(body.tag, body.q, body.options, body.answer)
    q = LmsQuestion(org_id=uuid.UUID(org), tag=tag, q=text, options=opts, answer=ans, active=body.active, created_by=C.actor_of(request))
    db.add(q)
    await db.commit()
    return _q_out(q, True)


@router.patch("/questions/{qid}")
async def update_question(qid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change questions")
    org = C.org_of(request)
    q = (await db.execute(select(LmsQuestion).where(LmsQuestion.id == C.to_uuid(qid, "question"), LmsQuestion.org_id == org))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Question not found")
    tag, text, opts, ans = _check_q(body.get("tag", q.tag), body.get("q", q.q), body.get("options", q.options), body.get("answer", q.answer))
    q.tag, q.q, q.options, q.answer = tag, text, opts, ans
    if "active" in body:
        q.active = bool(body["active"])
    await db.commit()
    return _q_out(q, True)


@router.delete("/questions/{qid}")
async def delete_question(qid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "delete questions")
    org = C.org_of(request)
    q = (await db.execute(select(LmsQuestion).where(LmsQuestion.id == C.to_uuid(qid, "question"), LmsQuestion.org_id == org))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Question not found")
    await db.delete(q)
    await db.commit()
    return {"ok": True}


# ══════════════════════════ SCORM-lite packages ═══════════════════════════════

@router.post("/courses/{cid}/package", status_code=201)
async def upload_package(cid: str, request: Request, file: UploadFile = File(...), title: str | None = None,
                         db: AsyncSession = Depends(get_db)):
    """Upload a zip with index.html; it becomes a 'scorm' module of the course, served sandboxed."""
    C.require_hr(request, "upload course packages")
    org = C.org_of(request)
    c = (await db.execute(select(Course).where(Course.id == C.to_uuid(cid, "course"), Course.org_id == org))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Course not found")
    data = await file.read()
    try:
        info = LL.inspect_package(data)
    except LL.PackageError as e:
        raise HTTPException(422, str(e))
    import hashlib
    p = LmsPackage(org_id=uuid.UUID(org), course_id=c.id, file_name=(file.filename or "package.zip")[:200], size_bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), entries=info["entries"], entry_point=info["entry_point"], data=data,
                   uploaded_by=C.actor_of(request))
    db.add(p)
    await db.flush()
    mods = list(c.modules or [])
    mods.append({"key": f"m{len(mods) + 1}-{str(p.id)[:8]}", "title": (title or file.filename or "Interactive module")[:160], "kind": "scorm",
                 "pkg_id": str(p.id), "url": None, "doc_id": None, "minutes": 0})
    c.modules = mods
    await db.commit()
    return {"package_id": str(p.id), "entries": len(info["entries"]), "entry_point": info["entry_point"], "modules": mods}


SANDBOX_HEADERS = {"Content-Security-Policy": "sandbox allow-scripts allow-same-origin; default-src 'self' data: blob:; "
                                              "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                              "img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'none'; frame-ancestors 'self'",
                   "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "private, max-age=600"}


@router.get("/packages/{pid}/manifest")
async def package_manifest(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The validated file list (the player fetches each file through the API and rewrites relative links)."""
    org = C.org_of(request)
    p = (await db.execute(select(LmsPackage).where(LmsPackage.id == C.to_uuid(pid, "package"), LmsPackage.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Package not found")
    return {"package_id": str(p.id), "entry_point": p.entry_point, "entries": list(p.entries or []), "size_bytes": p.size_bytes, "file_name": p.file_name}


@router.get("/packages/{pid}/{path:path}")
async def package_file(pid: str, path: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    p = (await db.execute(select(LmsPackage).where(LmsPackage.id == C.to_uuid(pid, "package"), LmsPackage.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Package not found")
    target = path or p.entry_point
    if target in ("", "/") or target.endswith("/"):
        target = p.entry_point
    data = await db.run_sync(lambda s: p.data)
    hit = LL.package_entry(data, list(p.entries or []), target)
    if hit is None:
        raise HTTPException(404, "File not in package")
    body, mime = hit
    return Response(content=body, media_type=mime, headers=SANDBOX_HEADERS)


@router.get("/packages/{pid}")
async def package_index(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await package_file(pid, "", request, db)


# ══════════════════════════ quiz: bank draw + reset ═══════════════════════════

async def _own(db, request: Request, aid: str) -> tuple[CourseAssignment, Course]:
    org = C.org_of(request)
    a = (await db.execute(select(CourseAssignment).where(CourseAssignment.id == C.to_uuid(aid, "assignment"),
                                                         CourseAssignment.org_id == org))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Assignment not found")
    if str(a.employee_id) != (C.emp_of(request) or "") and not C.is_hr(request):
        raise HTTPException(403, "This training is not yours")
    c = (await db.execute(select(Course).where(Course.id == a.course_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Course not found")
    return a, c


@router.get("/assignments/{aid}/quiz-set")
async def quiz_set(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The questions for the current attempt (answers hidden). Bank-based courses draw a fresh set per attempt."""
    a, c = await _own(db, request, aid)
    org = C.org_of(request)
    left = LL.attempts_left(c.max_attempts, a.quiz_attempts)
    if c.question_tags:
        if not a.quiz_set:
            bank = [{"id": str(q.id), "tag": q.tag, "active": q.active} for q in (await db.execute(select(LmsQuestion).where(
                LmsQuestion.org_id == org, LmsQuestion.active.is_(True)))).scalars().all()]
            drawn = LL.draw_questions(bank, list(c.question_tags or []), int(c.quiz_sample or 0), f"{a.id}:{a.quiz_attempts or 0}")
            a.quiz_set = [q["id"] for q in drawn]
            await db.commit()
        rows = (await db.execute(select(LmsQuestion).where(LmsQuestion.id.in_([uuid.UUID(i) for i in a.quiz_set or []])))).scalars().all() if a.quiz_set else []
        qs = LL.quiz_for([], [{"id": str(q.id), "q": q.q, "options": q.options, "answer": q.answer} for q in rows], a.quiz_set)
    else:
        qs = list(c.quiz or [])
    return {"questions": [{"q": q.get("q"), "options": list(q.get("options") or [])} for q in qs], "pass_mark": c.pass_mark,
            "attempts": a.quiz_attempts or 0, "attempts_left": left, "max_attempts": c.max_attempts or 0, "from_bank": bool(c.question_tags)}


@router.post("/assignments/{aid}/reset")
async def reset_attempts(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "reset quiz attempts")
    a, _ = await _own(db, request, aid)
    a.quiz_attempts, a.quiz_score, a.quiz_passed, a.quiz_set = 0, None, None, None
    if a.status == "completed":
        a.status = "in_progress"
        a.completed_at, a.certificate_no = None, None
    await db.commit()
    return {"ok": True, "attempts": 0}


# ══════════════════════════ reports ═══════════════════════════════════════════

@router.get("/team")
async def team(request: Request, manager_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """A manager's team completion (HR may pass manager_id; a manager always sees own reports)."""
    org = C.org_of(request)
    if C.is_hr(request):
        mid = manager_id or C.emp_of(request) or ""
    elif C.is_manager(request) and C.emp_of(request):
        mid = C.emp_of(request)
    else:
        raise HTTPException(403, "Only HR or a manager can see team learning")
    emps = await roster(request, org)
    team_ids = _team_ids(emps, mid)
    today = await org_today(org)
    courses = await _courses(db, org)
    asg = (await db.execute(select(CourseAssignment).where(CourseAssignment.org_id == org,
                                                           CourseAssignment.employee_id.in_([uuid.UUID(i) for i in team_ids] or [uuid.uuid4()])))).scalars().all()
    rows = [_asg_row(a, courses.get(str(a.course_id)), today, emps) for a in asg]
    summ = LL.team_summary(rows, today)
    seen = {p["employee_id"] for p in summ["people"]}
    for i in team_ids:
        if i not in seen:
            summ["people"].append({"employee_id": i, "employee_name": C.emp_name(emps[i]), "assigned": 0, "completed": 0, "overdue": 0,
                                   "in_progress": 0, "mandatory_pending": 0, "completion_pct": None})
    return {"manager_id": mid, "team_size": len(team_ids), **summ, "assignments": rows}


@router.get("/report")
async def report(request: Request, by: str = "department", course_id: str | None = None, department: str | None = None,
                 state: str | None = None, db: AsyncSession = Depends(get_db)):
    """Completion / compliance report with grouping and filters (HR). Rows are exportable line items."""
    C.require_hr(request, "see the learning report")
    org = C.org_of(request)
    if by not in ("department", "course_title", "manager_name", "category"):
        by = "department"
    emps = await roster(request, org)
    today = await org_today(org)
    courses = await _courses(db, org)
    q = select(CourseAssignment).where(CourseAssignment.org_id == org)
    if course_id:
        q = q.where(CourseAssignment.course_id == C.to_uuid(course_id, "course"))
    rows = [_asg_row(a, courses.get(str(a.course_id)), today, emps) for a in (await db.execute(q)).scalars().all()]
    if department:
        rows = [r for r in rows if (r.get("department") or "") == department]
    if state:
        rows = [r for r in rows if r["state"] == state]
    groups = LL.completion_by(rows, by, today)
    comp = L.compliance_report([{"id": c.id, "title": c.title, "mandatory": c.mandatory} for c in courses.values() if c.active],
                               [{"course_id": r["course_id"], "status": r["status"], "due_on": date.fromisoformat(r["due_on"]) if r["due_on"] else None}
                                for r in rows], today)
    return {"by": by, "groups": groups, "rows": rows, "mandatory": comp, "as_of": today.isoformat()}


@router.get("/my")
async def my_learning(request: Request, db: AsyncSession = Depends(get_db)):
    """ESS: counts, paths with step progress, certificates."""
    org = C.org_of(request)
    me = C.emp_of(request)
    if not me:
        return {"assigned": 0, "completed": 0, "overdue": 0, "due_soon": 0, "paths": [], "certificates": []}
    today = await org_today(org)
    courses = await _courses(db, org)
    asg = (await db.execute(select(CourseAssignment).where(CourseAssignment.org_id == org, CourseAssignment.employee_id == me))).scalars().all()
    rows = [_asg_row(a, courses.get(str(a.course_id)), today) for a in asg]
    s = await C.get_settings(db, org)
    remind = int(s.lms_remind_days or 3)
    pa = (await db.execute(select(PathAssignment).where(PathAssignment.org_id == org, PathAssignment.employee_id == me))).scalars().all()
    paths = []
    for x in pa:
        p = (await db.execute(select(LearningPath).where(LearningPath.id == x.path_id))).scalar_one_or_none()
        if not p:
            continue
        pr = LL.path_progress(p.course_ids or [], rows)
        for st in pr["steps"]:
            st["title"] = courses[st["course_id"]].title if st["course_id"] in courses else None
        paths.append({"path_id": str(p.id), "title": p.title, "description": p.description, "mandatory": bool(p.mandatory),
                      "due_on": C.iso(x.due_on), **pr})
    return {"assigned": len(rows), "completed": sum(1 for r in rows if r["status"] == "completed"),
            "overdue": sum(1 for r in rows if r["state"] == "overdue"),
            "due_soon": sum(1 for r in rows if LL.reminder_stage(r["status"], date.fromisoformat(r["due_on"]) if r["due_on"] else None, today, remind) == "due_soon"),
            "paths": paths, "certificates": [{"assignment_id": r["id"], "course_title": r["course_title"], "certificate_no": r["certificate_no"],
                                             "completed_at": r["completed_at"]} for r in rows if r["certificate_no"]]}


# ══════════════════════════ reminders ═════════════════════════════════════════

async def run_reminders(db, org: str | None = None) -> int:
    """Due-soon / overdue reminders through the notification service, once per assignment per stage."""
    q = select(CourseAssignment).where(CourseAssignment.status != "completed", CourseAssignment.due_on.is_not(None))
    if org:
        q = q.where(CourseAssignment.org_id == org)
    asg = (await db.execute(q)).scalars().all()
    if not asg:
        return 0
    sent = {(str(r.assignment_id), r.stage) for r in (await db.execute(select(LmsReminderLog))).scalars().all()}
    courses = {str(c.id): c for c in (await db.execute(select(Course))).scalars().all()}
    by_org: dict[str, list] = {}
    for a in asg:
        by_org.setdefault(str(a.org_id), []).append(a)
    n = 0
    for o, items in by_org.items():
        today = await org_today(o)
        s = await C.get_settings(db, o)
        remind = int(s.lms_remind_days or 3)
        due = LL.reminders_due([{"id": str(a.id), "status": a.status, "due_on": a.due_on, "employee_id": str(a.employee_id),
                                 "course_title": courses[str(a.course_id)].title if str(a.course_id) in courses else "Training",
                                 "course_id": str(a.course_id)} for a in items], sent, today, remind)
        for d in due:
            db.add(LmsReminderLog(org_id=uuid.UUID(o), assignment_id=uuid.UUID(d["id"]), stage=d["stage"]))
            await db.commit()
            title = (f"Training overdue: {d['course_title']}" if d["stage"] == "overdue"
                     else f"Training due {d['due_on'].isoformat() if isinstance(d['due_on'], date) else d['due_on']}: {d['course_title']}")
            await notify(o, employee_ids=[d["employee_id"]], kind="reminder", title=title, body="Open My learning to finish it.",
                         link="/learning/my", email=True)
            n += 1
    return n


@router.post("/reminders/run")
async def reminders_now(request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "send training reminders")
    return {"sent": await run_reminders(db, C.org_of(request))}
