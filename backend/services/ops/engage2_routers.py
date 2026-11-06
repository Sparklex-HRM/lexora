"""Engagement depth routes — Stream D7. Mounted under /engage (gateway prefix `engage` → ops service).

Schedules   GET/POST /survey-schedules   PATCH/DELETE /survey-schedules/{id}   POST /survey-schedules/run-due
Bank        GET/POST /survey-bank   PATCH/DELETE /survey-bank/{id}
Heatmap     GET /surveys/{id}/heatmap?by=department|manager       (HR; threshold per segment)
Insights    GET /insights/manager?manager_id=                     (manager = own team; HR any)
            GET /insights/enps                                     (company eNPS trend with deltas)
Budgets     GET /kudos-budgets?month=   PUT /kudos-budgets         GET /kudos-leaderboard?month=
Onboarding  POST /onboarding-surveys/run (HR; the tick also runs it)

Paths were chosen so they never collide with G4's /surveys/{sid} and /kudos/{kid}/{decision}.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import engage2_logic as E2  # noqa: E402
import engage_common as C  # noqa: E402
import engage_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from engage2_models import KudosBudget, OnboardingSurveyLog, SurveyQuestion, SurveySchedule  # noqa: E402
from engage_models import Kudos, Survey, SurveyResponse  # noqa: E402

router = APIRouter(prefix="/engage", tags=["engagement-depth"])


# ══════════════════════════ survey schedules ══════════════════════════════════

def _sched_out(s: SurveySchedule) -> dict:
    return {"id": str(s.id), "title": s.title, "kind": s.kind, "cadence": s.cadence, "weekday": s.weekday, "day_of_month": s.day_of_month,
            "open_days": s.open_days, "questions": list(s.questions or []), "anonymous": bool(s.anonymous), "target": s.target or {},
            "threshold": s.threshold, "active": bool(s.active), "next_run_on": C.iso(s.next_run_on), "last_run_on": C.iso(s.last_run_on),
            "last_survey_id": str(s.last_survey_id) if s.last_survey_id else None, "runs": s.runs or 0, "created_by": s.created_by,
            "created_at": C.iso(s.created_at)}


def _clean_questions(qs: list[dict]) -> list[dict]:
    out = []
    for i, q in enumerate(qs or []):
        text = str(q.get("text") or "").strip()
        if not text:
            continue
        typ = q.get("type") if q.get("type") in ("scale", "nps", "text", "choice") else "scale"
        row = {"key": str(q.get("key") or f"q{i + 1}")[:40], "type": typ, "text": text[:400]}
        if typ == "choice":
            row["options"] = [str(o).strip()[:80] for o in (q.get("options") or []) if str(o).strip()][:12]
            if not row["options"]:
                raise HTTPException(422, f"Question '{text[:40]}' needs at least one option")
        out.append(row)
    if not out:
        raise HTTPException(422, "Add at least one question")
    return out


@router.get("/survey-schedules")
async def list_schedules(request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "see survey schedules")
    rows = (await db.execute(select(SurveySchedule).where(SurveySchedule.org_id == C.org_of(request)).order_by(SurveySchedule.title))).scalars().all()
    return {"schedules": [_sched_out(s) for s in rows], "cadences": list(E2.CADENCES)}


class ScheduleIn(BaseModel):
    title: str
    kind: str = "pulse"
    cadence: str = "monthly"
    weekday: int = 0
    day_of_month: int = 1
    open_days: int = 7
    questions: list[dict] | None = None
    template: str | None = None
    anonymous: bool = True
    target: dict | None = None
    threshold: int | None = None
    active: bool = True
    start_on: str | None = None


@router.post("/survey-schedules", status_code=201)
async def create_schedule(body: ScheduleIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "create a survey schedule")
    org = C.org_of(request)
    if not body.title.strip():
        raise HTTPException(422, "Give the schedule a title")
    if body.cadence not in E2.CADENCES:
        raise HTTPException(422, "Cadence must be weekly, monthly or quarterly")
    tpl = L.SURVEY_TEMPLATES.get(body.template or "")
    qs = _clean_questions(body.questions or (tpl["questions"] if tpl else []))
    today = await org_today(org)
    start = date.fromisoformat(body.start_on) if body.start_on else today
    s = SurveySchedule(org_id=uuid.UUID(org), title=body.title.strip()[:200], kind=body.kind if body.kind in ("pulse", "enps") else "pulse",
                       cadence=body.cadence, weekday=max(0, min(6, body.weekday)), day_of_month=max(1, min(28, body.day_of_month)),
                       open_days=max(1, min(60, body.open_days)), questions=qs, anonymous=body.anonymous, target=body.target or {},
                       threshold=body.threshold, active=body.active, created_by=C.actor_of(request),
                       next_run_on=E2.next_run_on(body.cadence, body.weekday, body.day_of_month, start - timedelta(days=1)))
    db.add(s)
    await db.commit()
    return _sched_out(s)


@router.patch("/survey-schedules/{sid}")
async def update_schedule(sid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change a survey schedule")
    org = C.org_of(request)
    s = (await db.execute(select(SurveySchedule).where(SurveySchedule.id == C.to_uuid(sid, "schedule"), SurveySchedule.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Schedule not found")
    if "title" in body and str(body["title"]).strip():
        s.title = str(body["title"]).strip()[:200]
    if "cadence" in body:
        if body["cadence"] not in E2.CADENCES:
            raise HTTPException(422, "Cadence must be weekly, monthly or quarterly")
        s.cadence = body["cadence"]
    for k, lo, hi in (("weekday", 0, 6), ("day_of_month", 1, 28), ("open_days", 1, 60)):
        if k in body:
            setattr(s, k, max(lo, min(hi, int(body[k] or lo))))
    if "questions" in body:
        s.questions = _clean_questions(body["questions"])
    for k in ("anonymous", "active"):
        if k in body:
            setattr(s, k, bool(body[k]))
    if "target" in body:
        s.target = body["target"] or {}
    if "threshold" in body:
        s.threshold = int(body["threshold"]) if body["threshold"] else None
    if "kind" in body and body["kind"] in ("pulse", "enps"):
        s.kind = body["kind"]
    if any(k in body for k in ("cadence", "weekday", "day_of_month")) or "next_run_on" in body:
        base = date.fromisoformat(body["next_run_on"]) - timedelta(days=1) if body.get("next_run_on") else (await org_today(org))
        s.next_run_on = E2.next_run_on(s.cadence, s.weekday, s.day_of_month, base)
    await db.commit()
    return _sched_out(s)


@router.delete("/survey-schedules/{sid}")
async def delete_schedule(sid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "delete a survey schedule")
    org = C.org_of(request)
    s = (await db.execute(select(SurveySchedule).where(SurveySchedule.id == C.to_uuid(sid, "schedule"), SurveySchedule.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Schedule not found")
    await db.delete(s)
    await db.commit()
    return {"ok": True}


async def run_due_schedules(db, org: str | None = None) -> int:
    """Open one survey per due schedule, announce it, move next_run_on forward."""
    q = select(SurveySchedule).where(SurveySchedule.active.is_(True), SurveySchedule.next_run_on.is_not(None))
    if org:
        q = q.where(SurveySchedule.org_id == org)
    rows = (await db.execute(q)).scalars().all()
    n = 0
    for s in rows:
        o = str(s.org_id)
        today = await org_today(o)
        if not E2.schedules_due([{"id": s.id, "active": s.active, "next_run_on": s.next_run_on}], today):
            continue
        st = await C.get_settings(db, o)
        sv = Survey(org_id=s.org_id, title=E2.survey_title(s.title, s.next_run_on, s.cadence), kind=s.kind, questions=list(s.questions or []),
                    anonymous=bool(s.anonymous), status="open", opens_on=s.next_run_on, closes_on=s.next_run_on + timedelta(days=s.open_days or 7),
                    target=s.target or {}, threshold=int(s.threshold or st.anonymity_threshold or 5), created_by=f"schedule:{s.id}")
        db.add(sv)
        await db.flush()
        s.last_run_on, s.last_survey_id, s.runs = s.next_run_on, sv.id, (s.runs or 0) + 1
        s.next_run_on = E2.next_run_on(s.cadence, s.weekday, s.day_of_month, s.next_run_on)
        await db.commit()
        emps = await directory.org_employees(o)
        ids = [e["id"] for e in emps if L.in_target(sv.target, e)]
        if ids:
            await notify(o, employee_ids=ids[:2000], kind="general", title=f"Survey: {sv.title}",
                         body="Anonymous — nobody sees who said what." if sv.anonymous else "Your name is recorded with your answers.",
                         link=f"/engage/surveys?id={sv.id}", email=False)
        n += 1
    return n


@router.post("/survey-schedules/run-due")
async def run_due_now(request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "run survey schedules")
    return {"opened": await run_due_schedules(db, C.org_of(request))}


# ══════════════════════════ survey question bank ══════════════════════════════

def _bank_out(q: SurveyQuestion) -> dict:
    return {"id": str(q.id), "tag": q.tag, "type": q.type, "text": q.text, "options": list(q.options or []), "active": bool(q.active),
            "created_by": q.created_by, "created_at": C.iso(q.created_at)}


@router.get("/survey-bank")
async def list_bank(request: Request, tag: str | None = None, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "see the survey question bank")
    q = select(SurveyQuestion).where(SurveyQuestion.org_id == C.org_of(request))
    if tag:
        q = q.where(SurveyQuestion.tag == tag)
    rows = (await db.execute(q.order_by(SurveyQuestion.tag, SurveyQuestion.created_at))).scalars().all()
    return {"questions": [_bank_out(r) for r in rows], "tags": sorted({r.tag for r in rows}), "types": ["scale", "nps", "text", "choice"]}


class BankIn(BaseModel):
    tag: str
    type: str = "scale"
    text: str
    options: list[str] | None = None
    active: bool = True


def _check_bank(tag, typ, text, options):
    tag, text = (tag or "").strip()[:60], (text or "").strip()[:400]
    if not tag or not text:
        raise HTTPException(422, "A question needs a tag and text")
    if typ not in ("scale", "nps", "text", "choice"):
        raise HTTPException(422, "Type must be scale, nps, text or choice")
    opts = [str(o).strip()[:80] for o in (options or []) if str(o).strip()][:12]
    if typ == "choice" and not opts:
        raise HTTPException(422, "A choice question needs options")
    return tag, typ, text, opts if typ == "choice" else None


@router.post("/survey-bank", status_code=201)
async def add_bank(body: BankIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "add survey questions")
    tag, typ, text, opts = _check_bank(body.tag, body.type, body.text, body.options)
    q = SurveyQuestion(org_id=uuid.UUID(C.org_of(request)), tag=tag, type=typ, text=text, options=opts, active=body.active, created_by=C.actor_of(request))
    db.add(q)
    await db.commit()
    return _bank_out(q)


@router.patch("/survey-bank/{qid}")
async def edit_bank(qid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "change survey questions")
    org = C.org_of(request)
    q = (await db.execute(select(SurveyQuestion).where(SurveyQuestion.id == C.to_uuid(qid, "question"), SurveyQuestion.org_id == org))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Question not found")
    q.tag, q.type, q.text, q.options = _check_bank(body.get("tag", q.tag), body.get("type", q.type), body.get("text", q.text), body.get("options", q.options))
    if "active" in body:
        q.active = bool(body["active"])
    await db.commit()
    return _bank_out(q)


@router.delete("/survey-bank/{qid}")
async def delete_bank(qid: str, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "delete survey questions")
    org = C.org_of(request)
    q = (await db.execute(select(SurveyQuestion).where(SurveyQuestion.id == C.to_uuid(qid, "question"), SurveyQuestion.org_id == org))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Question not found")
    await db.delete(q)
    await db.commit()
    return {"ok": True}


# ══════════════════════════ heatmap ═══════════════════════════════════════════

@router.get("/surveys/{sid}/heatmap")
async def heatmap(sid: str, request: Request, by: str = "department", db: AsyncSession = Depends(get_db)):
    """Scores per department (or per manager) — a segment with fewer than `threshold` answers shows no numbers."""
    C.require_hr(request, "see survey heatmaps")
    org = C.org_of(request)
    s = (await db.execute(select(Survey).where(Survey.id == C.to_uuid(sid, "survey"), Survey.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Survey not found")
    rows = (await db.execute(select(SurveyResponse).where(SurveyResponse.survey_id == s.id))).scalars().all()
    names = {}
    if by == "manager":
        names = {e["id"]: f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() for e in await directory.org_employees(org)}
    resp = [{"answers": r.answers, "segment": (names.get(str(r.manager_id), "—") if by == "manager" else (r.department or "—"))} for r in rows]
    res = E2.segment_results(list(s.questions or []), resp, s.threshold, bool(s.anonymous))
    unstamped = sum(1 for r in rows if (r.manager_id is None if by == "manager" else not r.department))
    return {"survey": {"id": str(s.id), "title": s.title, "kind": s.kind, "anonymous": bool(s.anonymous), "threshold": s.threshold},
            "by": by, "questions": [{"key": q["key"], "text": q["text"], "type": q["type"]} for q in (s.questions or []) if q["type"] in ("nps", "scale")],
            "rows": res, "unstamped": unstamped,
            "note": "Segments are stamped at answer time (department / manager), never the person. Cells below the threshold stay hidden."}


# ══════════════════════════ manager insights ══════════════════════════════════

async def _company_score(db, s: Survey) -> tuple[float | None, int]:
    rows = (await db.execute(select(SurveyResponse).where(SurveyResponse.survey_id == s.id))).scalars().all()
    res = L.survey_results(s.questions or [], [{"answers": r.answers} for r in rows], s.threshold, bool(s.anonymous))
    score = None
    if res["visible"]:
        for q in res["questions"]:
            if q["type"] == "nps":
                score = q.get("enps")
                break
            if q["type"] == "scale" and score is None:
                score = q.get("average")
    return score, len(rows)


@router.get("/insights/manager")
async def manager_insights(request: Request, manager_id: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    if C.is_hr(request):
        mid = manager_id or C.emp_of(request) or ""
    elif C.is_manager(request) and C.emp_of(request):
        mid = C.emp_of(request)
    else:
        raise HTTPException(403, "Manager insights are for managers and HR")
    emps = await directory.org_employees(org)
    team = {e["id"] for e in emps if str(e.get("manager_id") or "") == mid}
    name = {e["id"]: f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() for e in emps}
    st = await C.get_settings(db, org)
    thr = int(st.anonymity_threshold or 5)
    surveys = (await db.execute(select(Survey).where(Survey.org_id == org, Survey.status != "draft", Survey.kind.in_(("pulse", "enps")))
                                .order_by(Survey.created_at))).scalars().all()[-12:]
    sv = []
    for s in surveys:
        rows = (await db.execute(select(SurveyResponse).where(SurveyResponse.survey_id == s.id))).scalars().all()
        cs, _ = await _company_score(db, s)
        sv.append({"id": str(s.id), "title": s.title, "kind": s.kind, "date": C.iso(s.created_at)[:10], "questions": list(s.questions or []),
                   "responses": [{"answers": r.answers, "manager_id": str(r.manager_id) if r.manager_id else None} for r in rows],
                   "company_score": cs})
    since = (await org_today(org)) - timedelta(days=90)
    kudos = (await db.execute(select(Kudos).where(Kudos.org_id == org, Kudos.created_at >= since))).scalars().all()
    ins = E2.manager_insights(team, sv, [{"from_employee_id": k.from_employee_id, "to_employee_id": k.to_employee_id, "points": k.points,
                                          "status": k.status} for k in kudos], thr, mid)
    month = (await org_today(org)).strftime("%Y-%m")
    b = (await db.execute(select(KudosBudget).where(KudosBudget.org_id == org, KudosBudget.manager_id == uuid.UUID(mid) if mid else None,
                                                    KudosBudget.month == month))).scalar_one_or_none() if mid else None
    budget = {"month": month, "points_budget": b.points_budget if b else int(st.manager_points_per_month or 0),
              "points_used": b.points_used if b else 0}
    budget["remaining"] = E2.budget_remaining(budget["points_budget"], budget["points_used"])
    # team learning + flight-risk live on their own screens; here: team roster for the drill-down
    return {"manager_id": mid, "manager_name": name.get(mid), "threshold": thr, **ins, "budget": budget,
            "team": [{"id": i, "name": name.get(i)} for i in sorted(team, key=lambda i: name.get(i) or "")],
            "window_days": 90}


@router.get("/insights/enps")
async def enps_trend(request: Request, db: AsyncSession = Depends(get_db)):
    """Company eNPS over time with deltas (threshold respected) — HR only (Surveys admin screen).
    A manager reads their own team's view through /engage/insights/manager."""
    if not C.is_hr(request):
        raise HTTPException(403, "The company eNPS trend is for HR")
    org = C.org_of(request)
    ss = (await db.execute(select(Survey).where(Survey.org_id == org, Survey.kind == "enps", Survey.status != "draft").order_by(Survey.created_at))).scalars().all()
    pts = []
    emps_n = len(await directory.org_employees(org))
    for s in ss:
        score, n = await _company_score(db, s)
        pts.append({"survey_id": str(s.id), "title": s.title, "date": C.iso(s.created_at)[:10], "responses": n, "score": score,
                    "visible": score is not None, "participation_pct": E2.participation(n, emps_n)})
    return {"points": E2.trend_with_delta(pts), "headcount": emps_n}


# ══════════════════════════ recognition budgets ═══════════════════════════════

def _budget_out(b: KudosBudget) -> dict:
    return {"id": str(b.id), "manager_id": str(b.manager_id), "manager_name": b.manager_name, "month": b.month,
            "points_budget": b.points_budget, "points_used": b.points_used, "remaining": E2.budget_remaining(b.points_budget, b.points_used),
            "note": b.note, "updated_by": b.updated_by, "updated_at": C.iso(b.updated_at)}


async def get_budget(db, org: str, manager_id: str, month: str, default_points: int, manager_name: str | None = None) -> KudosBudget:
    b = (await db.execute(select(KudosBudget).where(KudosBudget.org_id == org, KudosBudget.manager_id == uuid.UUID(manager_id),
                                                    KudosBudget.month == month))).scalar_one_or_none()
    if b is None:
        b = KudosBudget(org_id=uuid.UUID(org), manager_id=uuid.UUID(manager_id), manager_name=manager_name, month=month,
                        points_budget=int(default_points or 0), points_used=0)
        db.add(b)
        await db.flush()
    return b


@router.get("/kudos-budgets")
async def list_budgets(request: Request, month: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    month = month or (await org_today(org)).strftime("%Y-%m")
    st = await C.get_settings(db, org)
    emps = await directory.org_employees(org)
    managers = sorted({str(e["manager_id"]) for e in emps if e.get("manager_id")})
    name = {e["id"]: f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() for e in emps}
    if C.is_hr(request):
        out = [_budget_out(await get_budget(db, org, m, month, st.manager_points_per_month, name.get(m))) for m in managers if m in name]
    elif C.is_manager(request) and C.emp_of(request):
        out = [_budget_out(await get_budget(db, org, C.emp_of(request), month, st.manager_points_per_month, name.get(C.emp_of(request))))]
    else:
        raise HTTPException(403, "Budgets are for managers and HR")
    await db.commit()
    return {"month": month, "budgets": out, "default_points": int(st.manager_points_per_month or 0),
            "points_per_kudos": int(st.kudos_points_default or 0), "can_edit": C.is_hr(request)}


class BudgetIn(BaseModel):
    manager_id: str
    month: str
    points_budget: int
    note: str | None = None


@router.put("/kudos-budgets")
async def put_budget(body: BudgetIn, request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "set recognition budgets")
    org = C.org_of(request)
    if not (len(body.month) == 7 and body.month[4] == "-"):
        raise HTTPException(422, "Month must look like 2026-10")
    if not 0 <= body.points_budget <= 100000:
        raise HTTPException(422, "Points budget must be 0 to 100000")
    emps = {e["id"]: e for e in await directory.org_employees(org)}
    if body.manager_id not in emps:
        raise HTTPException(404, "Manager not found")
    st = await C.get_settings(db, org)
    b = await get_budget(db, org, body.manager_id, body.month, st.manager_points_per_month, C.emp_name(emps[body.manager_id]))
    b.points_budget, b.note, b.updated_by = body.points_budget, (body.note or "")[:400] or None, C.actor_of(request)
    await db.commit()
    return _budget_out(b)


@router.get("/kudos-leaderboard")
async def leaderboard(request: Request, month: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    month = month or (await org_today(org)).strftime("%Y-%m")
    rows = (await db.execute(select(Kudos).where(Kudos.org_id == org, Kudos.status == "approved"))).scalars().all()
    return {"month": month, "top": E2.points_leaderboard([{"to_employee_id": k.to_employee_id, "to_name": k.to_name, "points": k.points,
                                                           "created_at": k.created_at, "status": k.status} for k in rows], month)}


# ══════════════════════════ onboarding check-ins ══════════════════════════════

async def run_onboarding_surveys(db, org: str | None = None) -> int:
    """30 / 60 / 90-day named check-ins for new joiners (never twice). Employees come from the org directory."""
    orgs = [org] if org else [str(r[0]) for r in (await db.execute(select(Survey.org_id).distinct())).all()]
    # orgs with no survey yet still need check-ins: add every org that has an engagement settings row
    from engage_models import EngSettings
    orgs = sorted(set(orgs) | {str(r[0]) for r in (await db.execute(select(EngSettings.org_id))).all()}) if not org else orgs
    n = 0
    for o in orgs:
        try:
            emps = await _employees_with_hire_date(o)
        except Exception:                                   # noqa: BLE001
            continue
        if not emps:
            continue
        today = await org_today(o)
        sent = {(str(r.employee_id), r.stage) for r in (await db.execute(select(OnboardingSurveyLog).where(OnboardingSurveyLog.org_id == o))).scalars().all()}
        for d in E2.onboarding_due(emps, sent, today):
            e = next((x for x in emps if str(x["id"]) == d["employee_id"]), {})
            s = Survey(org_id=uuid.UUID(o), title=f"{d['stage']}-day check-in — {C.emp_name(e) or 'new joiner'}", kind="pulse",
                       questions=list(E2.ONBOARDING_QUESTIONS[d["stage"]]), anonymous=False, status="open", opens_on=today,
                       closes_on=today + timedelta(days=14), target={"employee_ids": [d["employee_id"]]}, threshold=1,
                       created_by=f"system@onboarding-{d['stage']}")
            db.add(s)
            await db.flush()
            db.add(OnboardingSurveyLog(org_id=uuid.UUID(o), employee_id=uuid.UUID(d["employee_id"]), stage=d["stage"], survey_id=s.id))
            await db.commit()
            await notify(o, employee_ids=[d["employee_id"]], kind="onboarding", title=f"Your {d['stage']}-day check-in is ready",
                         body="Three minutes — it goes to HR and helps us fix things early.", link=f"/engage/surveys?id={s.id}")
            n += 1
    return n


async def _employees_with_hire_date(org: str) -> list[dict]:
    from sqlalchemy import text
    from shared.utils import platform_models as pm
    async with pm.session() as s:
        rows = (await s.execute(text("SELECT id, first_name, last_name, hire_date, status FROM employees "
                                     "WHERE org_id = CAST(:org AS uuid) AND is_active IS TRUE"), {"org": org})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/onboarding-surveys/run")
async def onboarding_now(request: Request, db: AsyncSession = Depends(get_db)):
    C.require_hr(request, "send onboarding check-ins")
    return {"sent": await run_onboarding_surveys(db, C.org_of(request))}
