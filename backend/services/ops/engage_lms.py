"""LMS (basic) — courses, modules, quiz, assignments, progress, certificate PDF, compliance. Stream G4.

/engage/lms/courses                 GET (HR: all · employee: active)   POST   PATCH /{id}
/engage/lms/courses/{id}/module-file  POST multipart (PDF module)    GET /engage/lms/files/{doc_id}
/engage/lms/assign                  POST {course_id, employee_ids | departments, due_on}
/engage/lms/assignments             GET ?scope=mine|all&course_id=
/engage/lms/assignments/{id}/progress   POST {module_key}        (tick a module)
/engage/lms/assignments/{id}/quiz       POST {answers}           (scored server-side)
/engage/lms/assignments/{id}/certificate.pdf   GET               (after completion)
/engage/lms/compliance              GET  mandatory-training compliance report
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
from shared.utils.org_time import org_today, to_local_date  # noqa: E402

import engage_common as C  # noqa: E402
import engage_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from engage_certificate import render_certificate  # noqa: E402
from engage_models import Course, CourseAssignment  # noqa: E402

router = APIRouter(prefix="/engage/lms", tags=["lms"])

MODULE_KINDS = ("pdf", "video", "url", "scorm")   # D7: scorm = zip package served sandboxed (lms_routers)


def _course_out(c: Course, assigned: int | None = None, completed: int | None = None) -> dict:
    return {"id": str(c.id), "title": c.title, "description": c.description, "modules": list(c.modules or []),
            "quiz_questions": len(c.quiz or []), "pass_mark": c.pass_mark, "mandatory": bool(c.mandatory), "active": bool(c.active),
            "created_by": c.created_by, "created_at": C.iso(c.created_at), "assigned": assigned, "completed": completed,
            # D7
            "max_attempts": c.max_attempts or 0, "question_tags": list(c.question_tags or []), "quiz_sample": c.quiz_sample or 0,
            "category": c.category}


def _quiz_public(quiz: list[dict]) -> list[dict]:
    return [{"q": q.get("q"), "options": list(q.get("options") or [])} for q in quiz or []]


def _assign_out(a: CourseAssignment, c: Course | None, today: date) -> dict:
    mods = list((c.modules if c else []) or [])
    has_quiz = bool(c and (c.quiz or c.question_tags))
    return {"id": str(a.id), "course_id": str(a.course_id), "course_title": c.title if c else None, "mandatory": bool(c and c.mandatory),
            "employee_id": str(a.employee_id), "employee_name": a.employee_name, "due_on": C.iso(a.due_on), "assigned_by": a.assigned_by,
            "assigned_at": C.iso(a.assigned_at), "completed_modules": list(a.completed_modules or []), "quiz_score": a.quiz_score,
            "quiz_passed": a.quiz_passed, "quiz_attempts": a.quiz_attempts or 0, "status": a.status,
            "state": L.assignment_state(a.status, a.due_on, today), "completed_at": C.iso(a.completed_at),
            "certificate_no": a.certificate_no,
            "progress_pct": L.progress_pct(mods, list(a.completed_modules or []), bool(a.quiz_passed), has_quiz),
            "modules": mods, "quiz": _quiz_public(c.quiz) if c else [], "pass_mark": c.pass_mark if c else None,
            # D7
            "from_bank": bool(c and c.question_tags), "max_attempts": (c.max_attempts or 0) if c else 0,
            "category": c.category if c else None, "path_id": str(a.path_id) if a.path_id else None}


MAX_LINK = 500


def safe_link(raw, title: str = "") -> str | None:
    """WASA follow-up (2026-10-24): a course link is shown to every learner as <a href>, so the SERVER checks it
    (the web form did before, the API did not): https / http only (no javascript:, data:, file:, vbscript: …),
    a host, no user:password@, no spaces / control characters, at most 500 characters (refused, not cut)."""
    from urllib.parse import urlsplit
    url = str(raw or "").strip()
    if not url:
        return None
    label = f"Module '{title[:40]}'" if title else "The link"
    if len(url) > MAX_LINK:
        raise HTTPException(422, f"{label}: the link is longer than {MAX_LINK} characters")
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in url):
        raise HTTPException(422, f"{label}: the link must not contain spaces or control characters")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise HTTPException(422, f"{label}: the link is not a valid web address")
    if parts.scheme.lower() not in ("https", "http") or not parts.hostname:
        raise HTTPException(422, f"{label}: use a web link that starts with https://")
    if parts.username or parts.password:
        raise HTTPException(422, f"{label}: the link must not carry a user name or password")
    return url


def _clean_modules(mods: list[dict]) -> list[dict]:
    out = []
    for i, m in enumerate(mods or []):
        title = str(m.get("title") or "").strip()
        if not title:
            continue
        kind = m.get("kind") if m.get("kind") in MODULE_KINDS else "url"
        row = {"key": str(m.get("key") or f"m{i + 1}")[:40], "title": title[:160], "kind": kind,
               "url": safe_link(m.get("url"), title) if kind in ("video", "url") else None,
               "doc_id": m.get("doc_id") or None, "pkg_id": m.get("pkg_id") or None}
        if kind == "scorm" and not row["pkg_id"]:
            raise HTTPException(422, f"Module '{title[:40]}' needs an uploaded package")
        try:
            row["minutes"] = int(m.get("minutes") or 0)
        except (TypeError, ValueError):
            row["minutes"] = 0
        if kind in ("video", "url") and not row["url"]:
            raise HTTPException(422, f"Module '{title[:40]}' needs a link")
        out.append(row)
    return out


def _clean_quiz(quiz: list[dict]) -> list[dict]:
    out = []
    for q in quiz or []:
        text = str(q.get("q") or "").strip()
        opts = [str(o).strip()[:200] for o in (q.get("options") or []) if str(o).strip()]
        if not text or len(opts) < 2:
            raise HTTPException(422, "Every quiz question needs text and at least two options")
        try:
            ans = int(q.get("answer"))
        except (TypeError, ValueError):
            raise HTTPException(422, f"Mark the correct option for '{text[:40]}'")
        if not 0 <= ans < len(opts):
            raise HTTPException(422, f"The correct option for '{text[:40]}' is out of range")
        out.append({"q": text[:400], "options": opts[:8], "answer": ans})
    return out


@router.get("/courses")
async def list_courses(request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    q = select(Course).where(Course.org_id == org)
    if not C.is_hr(request):
        q = q.where(Course.active.is_(True))
    rows = (await db.execute(q.order_by(Course.title))).scalars().all()
    asg = (await db.execute(select(CourseAssignment).where(CourseAssignment.org_id == org))).scalars().all()
    by_course: dict[str, list] = {}
    for a in asg:
        by_course.setdefault(str(a.course_id), []).append(a)
    out = []
    for c in rows:
        xs = by_course.get(str(c.id), [])
        d = _course_out(c, len(xs), sum(1 for a in xs if a.status == "completed"))
        if C.is_hr(request):
            d["quiz"] = list(c.quiz or [])
        out.append(d)
    return {"courses": out, "can_manage": C.is_hr(request)}


class CourseIn(BaseModel):
    title: str
    description: str | None = None
    modules: list[dict] | None = None
    quiz: list[dict] | None = None
    pass_mark: int = 70
    mandatory: bool = False
    active: bool = True
    # D7
    max_attempts: int = 0
    question_tags: list[str] | None = None
    quiz_sample: int = 0
    category: str | None = None


def _apply_d7(c: Course, body: dict) -> None:
    if "max_attempts" in body:
        c.max_attempts = max(0, min(int(body["max_attempts"] or 0), 50))
    if "question_tags" in body:
        c.question_tags = [str(t).strip()[:60] for t in (body["question_tags"] or []) if str(t).strip()][:20] or None
    if "quiz_sample" in body:
        c.quiz_sample = max(0, min(int(body["quiz_sample"] or 0), 200))
    if "category" in body:
        c.category = str(body["category"] or "").strip()[:60] or None


@router.post("/courses", status_code=201)
async def create_course(body: CourseIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "create a course")
    if not body.title.strip():
        raise HTTPException(422, "Give the course a title")
    if not 0 <= int(body.pass_mark) <= 100:
        raise HTTPException(422, "Pass mark must be 0 to 100")
    c = Course(org_id=uuid.UUID(C.org_of(request)), title=body.title.strip()[:200], description=(body.description or "")[:4000] or None,
               modules=_clean_modules(body.modules or []), quiz=_clean_quiz(body.quiz or []), pass_mark=int(body.pass_mark),
               mandatory=bool(body.mandatory), active=bool(body.active), created_by=C.actor_of(request))
    _apply_d7(c, body.model_dump())
    db.add(c)
    await db.commit()
    return {**_course_out(c, 0, 0), "quiz": list(c.quiz or [])}


@router.patch("/courses/{cid}")
async def update_course(cid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change a course")
    c = (await db.execute(select(Course).where(Course.id == C.to_uuid(cid, "course"), Course.org_id == C.org_of(request)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Course not found")
    if "title" in body and str(body["title"]).strip():
        c.title = str(body["title"]).strip()[:200]
    if "description" in body:
        c.description = str(body["description"] or "")[:4000] or None
    if "modules" in body:
        c.modules = _clean_modules(body["modules"] or [])
    if "quiz" in body:
        c.quiz = _clean_quiz(body["quiz"] or [])
    if "pass_mark" in body:
        if not 0 <= int(body["pass_mark"]) <= 100:
            raise HTTPException(422, "Pass mark must be 0 to 100")
        c.pass_mark = int(body["pass_mark"])
    for k in ("mandatory", "active"):
        if k in body:
            setattr(c, k, bool(body[k]))
    _apply_d7(c, body)
    await db.commit()
    return {**_course_out(c), "quiz": list(c.quiz or [])}


@router.post("/courses/{cid}/module-file", status_code=201)
async def module_file(cid: str, request: Request, file: UploadFile = File(...), title: str | None = None,
                      db: AsyncSession = Depends(get_db)):
    """Upload a PDF and add it as a module of the course."""
    C.require_hr(request, "upload course material")
    org = C.org_of(request)
    c = (await db.execute(select(Course).where(Course.id == C.to_uuid(cid, "course"), Course.org_id == org))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Course not found")
    data = await file.read()
    d = await C.store_doc(db, org, None, file.filename or "module.pdf", data, "lms_module", C.actor_of(request))
    if d.content_type != "application/pdf":
        raise HTTPException(422, "Course material must be a PDF. For video, add a link instead.")
    mods = list(c.modules or [])
    mods.append({"key": f"m{len(mods) + 1}-{str(d.id)[:8]}", "title": (title or file.filename or "Reading")[:160], "kind": "pdf",
                 "doc_id": str(d.id), "url": None, "minutes": 0})
    c.modules = mods
    await db.commit()
    return {**_course_out(c), "quiz": list(c.quiz or [])}


@router.get("/files/{doc_id}")
async def lms_file(doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    d = await C.load_doc(db, org, doc_id)
    if d.doc_type != "lms_module":
        raise HTTPException(404, "File not found")
    data = await db.run_sync(lambda s: d.data)
    return Response(content=data, media_type=d.content_type,
                    headers={"Content-Disposition": f'inline; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; sandbox"})


class AssignIn(BaseModel):
    course_id: str
    employee_ids: list[str] | None = None
    departments: list[str] | None = None
    due_on: str | None = None


@router.post("/assign", status_code=201)
async def assign(body: AssignIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "assign a course")
    org = C.org_of(request)
    c = (await db.execute(select(Course).where(Course.id == C.to_uuid(body.course_id, "course"), Course.org_id == org))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Course not found")
    emps = await C.employees(request)
    ids = set(body.employee_ids or [])
    if body.departments:
        ids |= {k for k, e in emps.items() if (e.get("department") or "") in body.departments}
    ids = [i for i in ids if i in emps and emps[i].get("status") != "terminated"]
    if not ids:
        raise HTTPException(422, "Pick at least one person or department")
    due = date.fromisoformat(body.due_on) if body.due_on else None
    existing = {str(a.employee_id) for a in (await db.execute(select(CourseAssignment).where(
        CourseAssignment.org_id == org, CourseAssignment.course_id == c.id))).scalars().all()}
    made = 0
    for i in ids:
        if i in existing:
            continue
        db.add(CourseAssignment(org_id=uuid.UUID(org), course_id=c.id, employee_id=uuid.UUID(i), employee_name=C.emp_name(emps[i]),
                                due_on=due, assigned_by=C.actor_of(request)))
        made += 1
    await db.commit()
    new_ids = [i for i in ids if i not in existing]
    if new_ids:
        await notify(org, employee_ids=new_ids, kind="general", title=f"New training: {c.title}" + (f" — due {due.isoformat()}" if due else ""),
                     body=c.description or "", link="/engage/learning", email=False)
    return {"assigned": made, "already": len(ids) - made}


@router.get("/assignments")
async def assignments(request: Request, scope: str = "mine", course_id: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    q = select(CourseAssignment).where(CourseAssignment.org_id == org)
    if scope == "all" and C.is_hr(request):
        pass
    elif scope == "team" and C.is_manager(request) and C.emp_of(request):
        team = [uuid.UUID(k) for k, e in (await C.employees(request)).items() if str(e.get("manager_id")) == C.emp_of(request)]
        q = q.where(CourseAssignment.employee_id.in_(team))
    else:
        me = C.emp_of(request)
        if not me:
            return {"assignments": []}
        q = q.where(CourseAssignment.employee_id == me)
    if course_id:
        q = q.where(CourseAssignment.course_id == C.to_uuid(course_id, "course"))
    rows = (await db.execute(q.order_by(CourseAssignment.assigned_at.desc()))).scalars().all()
    courses = {str(c.id): c for c in (await db.execute(select(Course).where(Course.org_id == org))).scalars().all()}
    today = await org_today(org)
    return {"assignments": [_assign_out(a, courses.get(str(a.course_id)), today) for a in rows]}


async def _own_assignment(db, request: Request, aid: str) -> tuple[CourseAssignment, Course]:
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


async def _finish_if_done(db, org: str, a: CourseAssignment, c: Course):
    mods = [m["key"] for m in (c.modules or [])]
    done_mods = all(k in (a.completed_modules or []) for k in mods)
    quiz_ok = (not c.quiz and not c.question_tags) or bool(a.quiz_passed)
    if done_mods and quiz_ok and a.status != "completed":
        s = await C.get_settings(db, org)
        s.certificate_seq = (s.certificate_seq or 0) + 1
        a.status = "completed"
        a.completed_at = C.now()
        a.certificate_no = L.certificate_number("CERT", s.certificate_seq, to_local_date(a.completed_at).year)
    elif a.status == "assigned":
        a.status = "in_progress"


class ProgressIn(BaseModel):
    module_key: str


@router.post("/assignments/{aid}/progress")
async def progress(aid: str, body: ProgressIn, request: Request, db: AsyncSession = Depends(get_db)):
    a, c = await _own_assignment(db, request, aid)
    keys = [m["key"] for m in (c.modules or [])]
    if body.module_key not in keys:
        raise HTTPException(404, "Module not found")
    done = list(a.completed_modules or [])
    if body.module_key not in done:
        done.append(body.module_key)
    a.completed_modules = done
    await _finish_if_done(db, C.org_of(request), a, c)
    await db.commit()
    return _assign_out(a, c, await org_today(C.org_of(request)))


class QuizIn(BaseModel):
    answers: dict | list


@router.post("/assignments/{aid}/quiz")
async def take_quiz(aid: str, body: QuizIn, request: Request, db: AsyncSession = Depends(get_db)):
    a, c = await _own_assignment(db, request, aid)
    if not c.quiz and not c.question_tags:
        raise HTTPException(409, "This course has no quiz")
    if a.quiz_passed:
        raise HTTPException(409, "You already passed this quiz")
    # D7: attempts cap + question-bank sets
    import lms_logic as LL
    left = LL.attempts_left(c.max_attempts, a.quiz_attempts)
    if left is not None and left <= 0:
        raise HTTPException(409, f"No attempts left (limit {c.max_attempts}). Ask HR to reset the quiz.")
    quiz = list(c.quiz or [])
    if c.question_tags:
        from lms_models import LmsQuestion
        if not a.quiz_set:
            raise HTTPException(409, "Open the quiz first to get your question set")
        rows = (await db.execute(select(LmsQuestion).where(LmsQuestion.id.in_([uuid.UUID(i) for i in a.quiz_set])))).scalars().all()
        quiz = LL.quiz_for([], [{"id": str(q.id), "q": q.q, "options": q.options, "answer": q.answer} for q in rows], a.quiz_set)
        if not quiz:
            raise HTTPException(409, "The question bank has no active questions for this course yet")
    r = L.quiz_score(quiz, body.answers)
    a.quiz_attempts = (a.quiz_attempts or 0) + 1
    a.quiz_score = r["score"]
    a.quiz_passed = L.passed(r["score"], c.pass_mark)
    if not a.quiz_passed and c.question_tags:
        a.quiz_set = None                       # a fresh draw next time
    await _finish_if_done(db, C.org_of(request), a, c)
    await db.commit()
    left = LL.attempts_left(c.max_attempts, a.quiz_attempts)
    msg = (f"You scored {r['score']}% — passed." if a.quiz_passed
           else f"You scored {r['score']}%. The pass mark is {c.pass_mark}%. "
                + ("You can try again." if left is None or left > 0 else "No attempts left — ask HR to reset the quiz."))
    return {**_assign_out(a, c, await org_today(C.org_of(request))), "message": msg, "correct": r["correct"], "total": r["total"],
            "attempts_left": left}


@router.get("/assignments/{aid}/certificate.pdf")
async def certificate(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    a, c = await _own_assignment(db, request, aid)
    if a.status != "completed" or not a.certificate_no:
        raise HTTPException(409, "Finish every module and pass the quiz to get the certificate")
    org_name = await directory.org_name(C.org_of(request)) or "Lexora HRM"
    data = render_certificate(person=a.employee_name or "Employee", course=c.title, number=a.certificate_no,
                              completed_on=to_local_date(a.completed_at).isoformat() if a.completed_at
                              else (await org_today(C.org_of(request))).isoformat(),
                              org_name=org_name, score=a.quiz_score if c.quiz else None)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="certificate-{a.certificate_no}.pdf"'})


@router.get("/compliance")
async def compliance(request: Request, db: AsyncSession = Depends(get_db)):
    if not (C.is_hr(request) or C.is_manager(request)):
        raise HTTPException(403, "Only HR or a manager can see the compliance report")
    org = C.org_of(request)
    courses = (await db.execute(select(Course).where(Course.org_id == org, Course.active.is_(True)))).scalars().all()
    q = select(CourseAssignment).where(CourseAssignment.org_id == org)
    if C.is_manager(request) and not C.is_hr(request):
        team = [uuid.UUID(k) for k, e in (await C.employees(request)).items() if str(e.get("manager_id")) == C.emp_of(request)]
        q = q.where(CourseAssignment.employee_id.in_(team))
    asg = (await db.execute(q)).scalars().all()
    today = await org_today(org)
    rep = L.compliance_report([{"id": c.id, "title": c.title, "mandatory": c.mandatory} for c in courses],
                              [{"course_id": a.course_id, "status": a.status, "due_on": a.due_on} for a in asg], today)
    by_course = {str(c.id): c for c in courses}
    people = [_assign_out(a, by_course.get(str(a.course_id)), today) for a in asg
              if by_course.get(str(a.course_id)) and by_course[str(a.course_id)].mandatory and a.status != "completed"]
    people.sort(key=lambda r: (r["state"] != "overdue", r["due_on"] or "9999", r["employee_name"] or ""))
    return {**rep, "pending_people": people}
