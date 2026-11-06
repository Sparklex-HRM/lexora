"""Performance — goals, appraisal cycles, reviews, calibration, increment proposals.

Confidentiality is enforced HERE, not on the screen: an employee reads only
their own review, a manager only their direct reports' (Employee.manager_id),
the skip-level only the ones assigned, HR all. Fields a reader may not see are
removed from the answer (performance_logic.redact).
Stage changes are written with `WHERE stage = <what was read>`.
"""
import sys
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import hiring_integrations as ext  # noqa: E402
import performance_logic as P  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, emp_name, fail, get_actor, now, num, one, opt_uid, ser, to_date, uid  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from performance_models import (PerfCheckin, PerfCycle, PerfGoal, PerfGoalCycle, PerfGoalLibrary,  # noqa: E402
                                PerfIncrementProposal, PerfReview, PerfReviewAudit, PerfSettings)

router = APIRouter(prefix="/performance", tags=["performance"])


# ── loaders ─────────────────────────────────────────────────────────────────
async def load_settings(db, org: str) -> dict:
    row = (await db.execute(select(PerfSettings).where(PerfSettings.org_id == org))).scalar_one_or_none()
    return P.merge_settings(row.data if row else None)


async def _employees(db, org: str) -> dict:
    rows = (await db.execute(select(Employee).where(Employee.org_id == org, Employee.is_active.is_(True)))).scalars().all()
    return {str(e.id): e for e in rows}


async def _grades(db, org: str) -> dict:
    """Grade is a column another stream adds to `employees`; read it if it is there."""
    try:
        rows = (await db.execute(text("SELECT id, grade FROM employees WHERE org_id = :o"), {"o": uid(org)})).all()
        return {str(i): g for i, g in rows}
    except Exception:                                 # noqa: BLE001  (column not there yet)
        await db.rollback()
        return {}


def _need_hr(actor: Actor, what: str = "do this"):
    if not actor.is_hr:
        raise HTTPException(403, f"Only HR can {what}.")


def _team_ids(actor: Actor, emps: dict) -> set:
    return {i for i, e in emps.items() if actor.employee_id and str(e.manager_id or "") == actor.employee_id}


# ── settings ────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    s = await load_settings(db, actor.org)
    if not actor.is_hr:                               # staff need the scale to read their review; not the matrix
        s = {k: s[k] for k in ("rating_scale", "competencies", "goal_weight", "competency_weight")}
    return {**s, "can_edit": actor.is_hr_head, "linked": bool(actor.employee_id),
            "has_team": bool(_team_ids(actor, await _employees(db, actor.org))), "is_hr": actor.is_hr}


@router.put("/settings")
async def put_settings(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin may change performance settings.")
    try:
        clean = P.clean_settings(body, await load_settings(db, actor.org))
    except RuleError as e:
        fail(e)
    row = (await db.execute(select(PerfSettings).where(PerfSettings.org_id == actor.org))).scalar_one_or_none()
    if not row:
        row = PerfSettings(org_id=uid(actor.org), data={})
        db.add(row)
    row.data = {**(row.data or {}), **clean}
    row.updated_at, row.updated_by = now(), actor.user_id
    await db.commit()
    await record_audit(request, "performance.settings.save", entity_type="perf_settings", entity_id=actor.org,
                       summary="Performance settings changed", meta={"keys": sorted(clean)})
    return P.merge_settings(row.data)


# ── goal cycles ─────────────────────────────────────────────────────────────
@router.get("/goal-cycles")
async def list_goal_cycles(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    rows = (await db.execute(select(PerfGoalCycle).where(PerfGoalCycle.org_id == actor.org)
                             .order_by(PerfGoalCycle.period_start.desc()))).scalars().all()
    return [ser(r) for r in rows]


@router.post("/goal-cycles", status_code=201)
async def create_goal_cycle(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "create a goal cycle")
    name, start, end = (body.get("name") or "").strip(), to_date(body.get("period_start")), to_date(body.get("period_end"))
    if not name or not start or not end:
        raise HTTPException(422, "Name, start date and end date are required.")
    if end <= start:
        raise HTTPException(422, "The end date must be after the start date.")
    c = PerfGoalCycle(org_id=uid(actor.org), name=name[:120], period_start=start, period_end=end, created_by=actor.user_id)
    db.add(c)
    await db.commit()
    return ser(c)


@router.post("/goal-cycles/{cycle_id}/{action}")
async def act_goal_cycle(cycle_id: str, action: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "close or reopen a goal cycle")
    if action not in ("close", "reopen"):
        raise HTTPException(404, "Unknown action")
    c = await one(db, PerfGoalCycle, cycle_id, actor.org, "Goal cycle")
    frm, to = ("open", "closed") if action == "close" else ("closed", "open")
    if c.status != frm:
        raise HTTPException(409, f"This goal cycle is already {c.status}.")
    await cas(db, PerfGoalCycle, c, "status", frm, status=to)
    await db.commit()
    await db.refresh(c)
    return ser(c)


# ── goals ───────────────────────────────────────────────────────────────────
def _goal_out(g: PerfGoal, e: Employee | None, actor: Actor) -> dict:
    d = ser(g)
    mgr = str(e.manager_id) if e and e.manager_id else None
    d.update({"employee_name": emp_name(e) if e else "", "employee_number": e.employee_number if e else None,
              "department": e.department if e else None, "manager_name": e.manager_name if e else None,
              "relation": P.relation(actor, str(g.employee_id), mgr)})
    return d


async def _goal_guard(db, actor: Actor, employee_id, action: str) -> Employee:
    e = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == uid(employee_id, "Employee")))).scalar_one_or_none()
    if not e or not P.goal_can(action, actor, str(e.id), str(e.manager_id) if e.manager_id else None):
        raise HTTPException(404 if action == "view" else 403,
                            "Employee not found" if action == "view" else "You cannot do this for this employee's goals.")
    return e


@router.get("/goals")
async def list_goals(request: Request, cycle_id: str | None = None, scope: str = "mine",
                     db: AsyncSession = Depends(get_db)):
    """scope: mine | team (my direct reports) | all (HR)."""
    actor = await get_actor(request, db)
    emps = await _employees(db, actor.org)
    if scope == "all":
        _need_hr(actor, "see everyone's goals")
        ids = None
    elif scope == "team":
        ids = _team_ids(actor, emps)
    else:
        ids = {actor.employee_id} if actor.employee_id else set()
    q = select(PerfGoal).where(PerfGoal.org_id == actor.org)
    if cycle_id:
        q = q.where(PerfGoal.cycle_id == uid(cycle_id, "Goal cycle"))
    if ids is not None:
        if not ids:
            return []
        q = q.where(PerfGoal.employee_id.in_([uid(i) for i in ids]))
    rows = (await db.execute(q.order_by(PerfGoal.employee_id, PerfGoal.created_at))).scalars().all()
    return [_goal_out(g, emps.get(str(g.employee_id)), actor) for g in rows]


def _fill_goal(g: PerfGoal, body: dict):
    for k in ("title", "measure", "target"):
        if k in body:
            setattr(g, k, (str(body.get(k) or "")).strip()[:300] or None)
    if not g.title:
        raise HTTPException(422, "Give the goal a title.")
    if "weight" in body:
        g.weight = num(body.get("weight"), "Weight", required=True, lo=0.01, hi=100)
    if "due_date" in body:
        g.due_date = to_date(body.get("due_date"))


@router.post("/goals", status_code=201)
async def create_goal(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    employee_id = body.get("employee_id") or actor.employee_id
    if not employee_id:
        raise HTTPException(409, "Your login is not linked to an employee record yet. Ask HR to link it.")
    e = await _goal_guard(db, actor, employee_id, "edit")
    cyc = await one(db, PerfGoalCycle, body.get("cycle_id"), actor.org, "Goal cycle")
    if cyc.status != "open":
        raise HTTPException(409, "This goal cycle is closed.")
    rel = P.relation(actor, str(e.id), str(e.manager_id) if e.manager_id else None)
    g = PerfGoal(org_id=e.org_id, cycle_id=cyc.id, employee_id=e.id, title="", status="draft", weight=0,
                 proposed_by="employee" if rel == "self" else rel, created_by=actor.user_id)
    _fill_goal(g, {"weight": body.get("weight"), **body})
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return _goal_out(g, e, actor)


@router.patch("/goals/{goal_id}")
async def update_goal(goal_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    g = await one(db, PerfGoal, goal_id, actor.org, "Goal")
    e = await _goal_guard(db, actor, g.employee_id, "edit")
    rel = P.relation(actor, str(e.id), str(e.manager_id) if e.manager_id else None)
    if g.status == "approved":
        raise HTTPException(409, "An approved goal cannot be edited. Ask the manager to send it back first.")
    if g.status == "proposed" and rel == "self":
        raise HTTPException(409, "This goal is with your manager for approval.")
    _fill_goal(g, body)
    await db.commit()
    await db.refresh(g)
    return _goal_out(g, e, actor)


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    g = await one(db, PerfGoal, goal_id, actor.org, "Goal")
    await _goal_guard(db, actor, g.employee_id, "edit")
    if g.status not in ("draft", "returned"):
        raise HTTPException(409, "Only a draft goal can be deleted.")
    await db.delete(g)
    await db.commit()
    return {"ok": True}


@router.post("/goals/{action}")
async def act_goal_set(action: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """submit | approve | return — for ALL of one person's goals in a cycle,
    because the rule (weights add up to 100) is about the set, not one goal."""
    actor = await get_actor(request, db)
    if action not in P.GOAL_TRANSITIONS:
        raise HTTPException(404, "Unknown action")
    employee_id = body.get("employee_id") or actor.employee_id
    if not employee_id:
        raise HTTPException(409, "Your login is not linked to an employee record yet. Ask HR to link it.")
    e = await _goal_guard(db, actor, employee_id, action)
    cyc = await one(db, PerfGoalCycle, body.get("cycle_id"), actor.org, "Goal cycle")
    rel = P.relation(actor, str(e.id), str(e.manager_id) if e.manager_id else None)
    goals = (await db.execute(select(PerfGoal).where(
        PerfGoal.org_id == actor.org, PerfGoal.cycle_id == cyc.id, PerfGoal.employee_id == e.id))).scalars().all()
    frm, to = P.GOAL_TRANSITIONS[action]
    moving = [g for g in goals if g.status in frm]
    if not moving:
        raise HTTPException(409, {"submit": "There are no draft goals to send.", "approve": "There are no goals waiting for approval.",
                                  "return": "There are no goals to send back."}[action])
    reason = (body.get("reason") or "").strip()
    if action == "return" and not reason:
        raise HTTPException(422, "Say what should change.")
    if action in ("submit", "approve"):
        ok, total = P.weights_check([g.weight for g in goals])
        if not ok:
            raise HTTPException(422, f"The goal weights must add up to 100 and each must be above 0. They add up to {total:g}.")
    values = {"status": to, "status_reason": reason or None}
    if action == "submit" and rel in ("manager", "hr"):
        values["status"] = "approved"                  # goals set by the manager are approved by that act
    if values["status"] == "approved":
        values.update(approved_by=actor.user_id, approved_at=now())
    res = await db.execute(update(PerfGoal).where(
        PerfGoal.org_id == actor.org, PerfGoal.id.in_([g.id for g in moving]), PerfGoal.status.in_(frm)).values(**values))
    if res.rowcount != len(moving):
        await db.rollback()
        raise HTTPException(409, "Someone else changed these goals just now. Refresh and try again.")
    await db.commit()
    if values["status"] == "proposed" and e.manager_id:
        await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [e.manager_id]),
                         title="Goals are waiting for your approval", body=emp_name(e), link="/performance/goals", kind="approval")
    elif rel != "self":
        await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [e.id]),
                         title="Your goals were approved" if values["status"] == "approved" else "Your goals were sent back",
                         body=reason or cyc.name, link="/performance/goals", kind="info")
    return {"ok": True, "status": values["status"], "goals": len(moving)}


@router.get("/goals/{goal_id}/checkins")
async def list_checkins(goal_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    g = await one(db, PerfGoal, goal_id, actor.org, "Goal")
    await _goal_guard(db, actor, g.employee_id, "view")
    rows = (await db.execute(select(PerfCheckin).where(PerfCheckin.org_id == actor.org, PerfCheckin.goal_id == g.id)
                             .order_by(PerfCheckin.at.desc()))).scalars().all()
    return [ser(r) for r in rows]


@router.post("/goals/{goal_id}/checkins", status_code=201)
async def add_checkin(goal_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    g = await one(db, PerfGoal, goal_id, actor.org, "Goal")
    e = await _goal_guard(db, actor, g.employee_id, "checkin")
    if g.status != "approved":
        raise HTTPException(409, "Check-ins start once the goal is approved.")
    try:
        pct = P.clean_progress(body.get("progress_pct"))
    except RuleError as ex:
        fail(ex)
    rel = P.relation(actor, str(e.id), str(e.manager_id) if e.manager_id else None)
    c = PerfCheckin(org_id=g.org_id, goal_id=g.id, progress_pct=pct, comment=(body.get("comment") or "").strip()[:2000] or None,
                    by_user=actor.user_id, by_name=actor.name, by_role="employee" if rel == "self" else rel)
    db.add(c)
    g.progress_pct = pct
    await db.commit()
    return ser(c)


@router.get("/goal-library")
async def list_library(request: Request, designation: str | None = None, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    rows = (await db.execute(select(PerfGoalLibrary).where(
        PerfGoalLibrary.org_id == actor.org, PerfGoalLibrary.is_active.is_(True)).order_by(PerfGoalLibrary.designation, PerfGoalLibrary.title))).scalars().all()
    if designation:
        rows = [r for r in rows if not r.designation or r.designation.lower() == designation.lower()]
    return [ser(r) for r in rows]


@router.post("/goal-library", status_code=201)
async def add_library(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "edit the goal library")
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "Give the goal a title.")
    r = PerfGoalLibrary(org_id=uid(actor.org), designation=(body.get("designation") or "").strip()[:120] or None,
                        title=title[:300], measure=(body.get("measure") or "").strip()[:300] or None,
                        target=(body.get("target") or "").strip()[:300] or None,
                        weight=num(body.get("weight"), "Weight", lo=0, hi=100))
    db.add(r)
    await db.commit()
    return ser(r)


@router.delete("/goal-library/{item_id}")
async def remove_library(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "edit the goal library")
    r = await one(db, PerfGoalLibrary, item_id, actor.org, "Library goal")
    r.is_active = False
    await db.commit()
    return {"ok": True}


# ── appraisal cycles ────────────────────────────────────────────────────────
def _emp_dict(e: Employee) -> dict:
    return {"status": e.status, "is_active": e.is_active, "hire_date": e.hire_date, "termination_date": e.termination_date}


async def _cycle_out(db, c: PerfCycle) -> dict:
    d = ser(c)
    rows = (await db.execute(select(PerfReview.stage, PerfReview.ack_status).where(
        PerfReview.org_id == c.org_id, PerfReview.cycle_id == c.id))).all()
    d["reviews"] = len(rows)
    d["by_stage"] = {s: sum(1 for st, _ in rows if st == s) for s in P.STAGES}
    d["acknowledged"] = sum(1 for _, a in rows if a == "acknowledged")
    d["disagreed"] = sum(1 for _, a in rows if a == "disagreed")
    today = (await org_today(c.org_id)).isoformat()
    d["overdue"] = {s: bool((c.deadlines or {}).get(s) and (c.deadlines or {}).get(s) < today and d["by_stage"].get(s))
                    for s in ("self", "manager", "reviewer", "calibration")}
    return d


@router.get("/cycles")
async def list_cycles(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see appraisal cycles")
    rows = (await db.execute(select(PerfCycle).where(PerfCycle.org_id == actor.org).order_by(PerfCycle.created_at.desc()))).scalars().all()
    return [await _cycle_out(db, c) for c in rows]


async def _fill_cycle(db, actor: Actor, c: PerfCycle, body: dict):
    if "name" in body:
        c.name = (body.get("name") or "").strip()[:160]
    if "period_start" in body:
        c.period_start = to_date(body.get("period_start"))
    if "period_end" in body:
        c.period_end = to_date(body.get("period_end"))
    if not c.name or not c.period_start or not c.period_end:
        raise HTTPException(422, "Name, start date and end date are required.")
    if c.period_end <= c.period_start:
        raise HTTPException(422, "The end date must be after the start date.")
    if "goal_cycle_id" in body:
        gid = opt_uid(body.get("goal_cycle_id"))
        if gid:
            await one(db, PerfGoalCycle, gid, actor.org, "Goal cycle")
        c.goal_cycle_id = gid
    if "joined_before" in body:
        c.joined_before = to_date(body.get("joined_before"))
    for k in ("exclude_notice", "use_reviewer"):
        if k in body:
            setattr(c, k, bool(body[k]))
    if "deadlines" in body:
        d = body.get("deadlines") or {}
        c.deadlines = {k: to_date(d.get(k)).isoformat() for k in ("self", "manager", "reviewer", "calibration") if d.get(k)}


@router.post("/cycles", status_code=201)
async def create_cycle(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "create an appraisal cycle")
    c = PerfCycle(org_id=uid(actor.org), name="", status="draft", template={}, deadlines={}, created_by=actor.user_id,
                  exclude_notice=True, use_reviewer=False)
    await _fill_cycle(db, actor, c, body)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return await _cycle_out(db, c)


@router.patch("/cycles/{cycle_id}")
async def update_cycle(cycle_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "edit an appraisal cycle")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    if c.status != "draft" and set(body) - {"deadlines", "name"}:
        raise HTTPException(409, "After launch only the name and the deadlines can change.")
    await _fill_cycle(db, actor, c, body)
    await db.commit()
    await db.refresh(c)
    return await _cycle_out(db, c)


async def _eligibility(db, actor: Actor, c: PerfCycle) -> list[dict]:
    emps = await _employees(db, actor.org)
    grades = await _grades(db, actor.org)
    out = []
    today = await org_today(actor.org)
    for i, e in emps.items():
        ok, why = P.eligible(_emp_dict(e), c.joined_before, c.exclude_notice, today)
        out.append({"employee_id": i, "name": emp_name(e), "employee_number": e.employee_number,
                    "department": e.department, "designation": e.job_title, "grade": grades.get(i),
                    "hire_date": e.hire_date.isoformat() if e.hire_date else None,
                    "manager_name": emp_name(emps[str(e.manager_id)]) if e.manager_id and str(e.manager_id) in emps else None,
                    "has_manager": bool(e.manager_id and str(e.manager_id) in emps), "eligible": ok, "reason": why})
    return sorted(out, key=lambda r: r["name"])


@router.get("/cycles/{cycle_id}/eligibility")
async def cycle_eligibility(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see who is eligible")
    return await _eligibility(db, actor, await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle"))


@router.post("/cycles/{cycle_id}/launch")
async def launch_cycle(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Draft -> Active. Freezes the template (scale, competencies, weights) on
    the cycle and creates one review per eligible employee at Self review."""
    actor = await get_actor(request, db)
    _need_hr(actor, "launch an appraisal cycle")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    if c.status != "draft":
        raise HTTPException(409, "This cycle has already been launched.")
    s = await load_settings(db, actor.org)
    rows = [r for r in await _eligibility(db, actor, c) if r["eligible"]]
    if not rows:
        raise HTTPException(409, "Nobody is eligible. Check the 'joined before' date, then add employees first.")
    emps = await _employees(db, actor.org)
    goals_by: dict = {}
    if c.goal_cycle_id:
        for g in (await db.execute(select(PerfGoal).where(
                PerfGoal.org_id == actor.org, PerfGoal.cycle_id == c.goal_cycle_id, PerfGoal.status == "approved"))).scalars().all():
            goals_by.setdefault(str(g.employee_id), []).append(
                {"id": str(g.id), "title": g.title, "measure": g.measure, "target": g.target,
                 "weight": float(g.weight or 0), "progress_pct": float(g.progress_pct or 0)})
        # G4 (2026-10-23): the person's objectives marked "include in review" are rated like goals,
        # with their rolled-up key-result progress (ids "okr:<id>").
        import okr_routers as _okr  # noqa: PLC0415  (avoids an import cycle at module load)
        for oid, rows in (await _okr.review_rows(db, actor.org, c.goal_cycle_id)).items():
            goals_by.setdefault(oid, []).extend(rows)
    template = {"scale": s["rating_scale"], "competencies": s["competencies"], "goal_weight": s["goal_weight"],
                "competency_weight": s["competency_weight"], "guideline": s["guideline"]}
    await cas(db, PerfCycle, c, "status", "draft", status="active", template=template, launched_at=now())
    for r in rows:
        e = emps[r["employee_id"]]
        m = emps.get(str(e.manager_id)) if e.manager_id else None
        rv = emps.get(str(m.manager_id)) if (m and m.manager_id and c.use_reviewer) else None
        db.add(PerfReview(org_id=c.org_id, cycle_id=c.id, employee_id=e.id, employee_name=r["name"],
                          employee_number=e.employee_number, department=e.department, designation=e.job_title,
                          grade=r["grade"], manager_id=m.id if m else None, manager_name=emp_name(m) if m else None,
                          reviewer_id=rv.id if rv else None, reviewer_name=emp_name(rv) if rv else None,
                          goals=goals_by.get(r["employee_id"], []), stage="self"))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This cycle has already been launched.")
    await db.refresh(c)
    await record_audit(request, "performance.cycle.launch", entity_type="perf_cycle", entity_id=str(c.id),
                       summary=f"{c.name} launched for {len(rows)} employees", meta={"reviews": len(rows)})
    await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [r["employee_id"] for r in rows]),
                     title="Your self review is open", body=c.name, link="/performance/reviews", kind="task")
    return {**(await _cycle_out(db, c)), "without_manager": sum(1 for r in rows if not r["has_manager"])}


@router.post("/cycles/{cycle_id}/remind")
async def remind_cycle(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Send a reminder to everyone whose turn it is. (There is no scheduler in
    this build: reminders go when HR presses the button.)"""
    actor = await get_actor(request, db)
    _need_hr(actor, "send reminders")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    rows = (await db.execute(select(PerfReview).where(PerfReview.org_id == actor.org, PerfReview.cycle_id == c.id))).scalars().all()
    who = {"self": [r.employee_id for r in rows if r.stage == "self"],
           "manager": list({r.manager_id for r in rows if r.stage == "manager" and r.manager_id}),
           "reviewer": list({r.reviewer_id for r in rows if r.stage == "reviewer" and r.reviewer_id})}
    sent = 0
    for stage, ids in who.items():
        users = await ext.user_ids_for_employees(db, actor.org, ids)
        if users and await ext.notify(actor.org, user_ids=users, title=f"Reminder: {P.STAGE_LABEL[stage]} is waiting for you",
                                      body=c.name + (f" · due {c.deadlines.get(stage)}" if (c.deadlines or {}).get(stage) else ""),
                                      link="/performance/reviews", kind="reminder"):
            sent += len(users)
    return {"reminded": sent, "pending": {k: len(v) for k, v in who.items()}, "notify_available": ext.NOTIFY_AVAILABLE}


# ── reviews ─────────────────────────────────────────────────────────────────
def _review_view(r: PerfReview) -> dict:
    return {"employee_id": str(r.employee_id), "manager_id": str(r.manager_id) if r.manager_id else None,
            "reviewer_id": str(r.reviewer_id) if r.reviewer_id else None, "stage": r.stage,
            "final_score": float(r.final_score) if r.final_score is not None else None, "final_band": r.final_band,
            "calibration_note": r.calibration_note}


def _review_out(r: PerfReview, c: PerfCycle, actor: Actor, scope: str) -> dict:
    d = P.redact(ser(r), scope)
    d.update({"cycle_name": c.name, "cycle_status": c.status, "template": c.template or {}, "deadlines": c.deadlines or {},
              "stage_label": P.STAGE_LABEL.get(r.stage, r.stage),
              "my_turn": r.stage in P.STAGE_FIELD and P.can_submit(r.stage, actor, _review_view(r)),
              "can_ack": scope == "self" and r.stage == "published" and not r.ack_status})
    return d


@router.get("/reviews")
async def list_reviews(request: Request, cycle_id: str | None = None, scope: str = "mine",
                       db: AsyncSession = Depends(get_db)):
    """scope: mine | team (direct reports + the ones I am skip-level for) | all (HR)."""
    actor = await get_actor(request, db)
    q = select(PerfReview).where(PerfReview.org_id == actor.org)
    if cycle_id:
        q = q.where(PerfReview.cycle_id == uid(cycle_id, "Appraisal cycle"))
    if scope == "all":
        _need_hr(actor, "see every review")
    elif not actor.employee_id:
        return []
    elif scope == "team":
        me = uid(actor.employee_id)
        q = q.where((PerfReview.manager_id == me) | (PerfReview.reviewer_id == me))
    else:
        q = q.where(PerfReview.employee_id == uid(actor.employee_id))
    rows = (await db.execute(q.order_by(PerfReview.employee_name))).scalars().all()
    cycles = {str(c.id): c for c in (await db.execute(select(PerfCycle).where(PerfCycle.org_id == actor.org))).scalars().all()}
    out = []
    for r in rows:
        sc = P.view_scope(actor, _review_view(r))
        if sc and str(r.cycle_id) in cycles:
            out.append(_review_out(r, cycles[str(r.cycle_id)], actor, sc))
    return out


async def _review(db, actor: Actor, review_id: str) -> tuple[PerfReview, PerfCycle, str]:
    r = await one(db, PerfReview, review_id, actor.org, "Review")
    scope = P.view_scope(actor, _review_view(r))
    if not scope:
        raise HTTPException(404, "Review not found")        # not yours: it does not exist for you
    return r, await one(db, PerfCycle, r.cycle_id, actor.org, "Appraisal cycle"), scope


@router.get("/reviews/{review_id}")
async def get_review(review_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    r, c, scope = await _review(db, actor, review_id)
    return _review_out(r, c, actor, scope)


@router.put("/reviews/{review_id}/draft")
async def save_draft(review_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Save without submitting. Only the person whose turn it is."""
    actor = await get_actor(request, db)
    r, c, scope = await _review(db, actor, review_id)
    if r.stage not in P.STAGE_FIELD or not P.can_submit(r.stage, actor, _review_view(r)):
        raise HTTPException(409, "It is not your turn on this review.")
    read = r.stage
    await cas(db, PerfReview, r, "stage", read, **{P.STAGE_FIELD[read]: {**(body.get("payload") or {}), "draft": True}})
    await db.commit()
    await db.refresh(r)
    return _review_out(r, c, actor, scope)


@router.post("/reviews/{review_id}/submit")
async def submit_review(review_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Submit my stage. It is then locked: only HR can reopen it, with a reason."""
    actor = await get_actor(request, db)
    r, c, scope = await _review(db, actor, review_id)
    read = r.stage
    if read not in P.STAGE_FIELD or not P.can_submit(read, actor, _review_view(r)):
        raise HTTPException(409, "It is not your turn on this review, or it is already submitted.")
    if c.status != "active":
        raise HTTPException(409, "This appraisal cycle is not open for reviews.")
    try:
        payload = P.validate_stage_payload(read, body.get("payload") or {}, r.goals or [], c.template or {})
        to = P.next_stage(read, c.use_reviewer, bool(r.reviewer_id))
    except RuleError as e:
        fail(e)
    values = {P.STAGE_FIELD[read]: payload, f"{read}_submitted_at": now(), "stage": to}
    scores = r.scores
    if read == "manager":
        scores = P.compute_scores(r.goals or [], payload, c.template or {})
        values.update(scores=scores, overall_score=scores["overall"]["score"],
                      promotion_recommended=payload["promotion_recommended"], promotion_note=payload["promotion_note"])
    if to == "calibration":                             # the starting point HR calibrates from
        start = payload.get("suggested_score") if read == "reviewer" else None
        start = start if start is not None else ((scores or {}).get("overall") or {}).get("score")
        values.update(final_score=start, final_band=P.band_for(start, (c.template or {}).get("scale") or []))
    await cas(db, PerfReview, r, "stage", read, **values)
    await db.commit()
    await db.refresh(r)
    nxt = {"manager": r.manager_id, "reviewer": r.reviewer_id}.get(to)
    if nxt:
        await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [nxt]),
                         title=f"{P.STAGE_LABEL[to]} is waiting for you", body=f"{r.employee_name} · {c.name}",
                         link="/performance/reviews", kind="task")
    return _review_out(r, c, actor, scope)


async def _audit(db, r: PerfReview, actor: Actor, rows: list[dict]):
    for a in rows:
        db.add(PerfReviewAudit(org_id=r.org_id, review_id=r.id, cycle_id=r.cycle_id, by_user=actor.user_id,
                               by_name=actor.name, **a))


@router.post("/reviews/{review_id}/move-stage")
async def move_stage(review_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """HR only: reopen a submitted stage, or skip a stage nobody can complete
    (for example no manager on record). A reason is required and kept."""
    actor = await get_actor(request, db)
    _need_hr(actor, "reopen or skip a review stage")
    r = await one(db, PerfReview, review_id, actor.org, "Review")
    c = await one(db, PerfCycle, r.cycle_id, actor.org, "Appraisal cycle")
    if actor.employee_id and str(r.employee_id) == actor.employee_id:
        raise HTTPException(403, "You cannot move your own review. Ask another HR user.")
    read, to, reason = r.stage, body.get("to_stage"), (body.get("reason") or "").strip()
    if read == "published":
        raise HTTPException(409, "A published review cannot be reopened.")
    try:
        P.hr_move_ok(read, to, reason)
    except RuleError as e:
        fail(e)
    values = {"stage": to}
    if to == "calibration" and r.final_score is None and r.overall_score is not None:
        values.update(final_score=r.overall_score, final_band=P.band_for(float(r.overall_score), (c.template or {}).get("scale") or []))
    await cas(db, PerfReview, r, "stage", read, **values)
    await _audit(db, r, actor, [{"action": "move_stage", "field": "stage", "old_value": read, "new_value": to, "reason": reason}])
    await db.commit()
    await db.refresh(r)
    return _review_out(r, c, actor, "hr")


@router.post("/reviews/{review_id}/calibrate")
async def calibrate_review(review_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "calibrate ratings")
    r = await one(db, PerfReview, review_id, actor.org, "Review")
    c = await one(db, PerfCycle, r.cycle_id, actor.org, "Appraisal cycle")
    if actor.employee_id and str(r.employee_id) == actor.employee_id:
        raise HTTPException(403, "You cannot calibrate your own rating. Ask another HR user.")
    try:
        values, audit = P.calibrate(_review_view(r), body.get("final_score"), body.get("reason"), (c.template or {}).get("scale") or [])
        if "promotion_recommended" in body:
            pv, pa = P.calibrate_promotion({**_review_view(r), "promotion_recommended": r.promotion_recommended,
                                            "promotion_note": r.promotion_note}, body.get("promotion_recommended"), body.get("reason"))
            values, audit = {**values, **pv}, audit + pa
    except RuleError as e:
        fail(e)
    await cas(db, PerfReview, r, "stage", "calibration", **values)
    await _audit(db, r, actor, audit)
    await db.commit()
    await db.refresh(r)
    return _review_out(r, c, actor, "hr")


@router.get("/reviews/{review_id}/audit")
async def review_audit(review_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see the review history")
    r = await one(db, PerfReview, review_id, actor.org, "Review")
    rows = (await db.execute(select(PerfReviewAudit).where(PerfReviewAudit.org_id == actor.org, PerfReviewAudit.review_id == r.id)
                             .order_by(PerfReviewAudit.at.desc()))).scalars().all()
    return [ser(a) for a in rows]


@router.post("/reviews/{review_id}/ack")
async def acknowledge(review_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """The employee acknowledges the published rating, or disagrees with a comment. Once."""
    actor = await get_actor(request, db)
    r, c, scope = await _review(db, actor, review_id)
    if scope != "self":
        raise HTTPException(403, "Only the employee can acknowledge their own review.")
    status = body.get("status")
    comment = (body.get("comment") or "").strip()
    if status not in ("acknowledged", "disagreed"):
        raise HTTPException(422, "Choose Acknowledge or Disagree.")
    if status == "disagreed" and not comment:
        raise HTTPException(422, "Say why you disagree.")
    await cas(db, PerfReview, r, "stage", "published", extra_where=[PerfReview.ack_status.is_(None)],
              ack_status=status, ack_comment=comment[:4000] or None, ack_at=now())
    await db.commit()
    await db.refresh(r)
    if status == "disagreed":
        await ext.notify(actor.org, roles=["hr"], title="An employee disagreed with their rating",
                         body=f"{r.employee_name} · {c.name}", link="/performance/cycles", kind="info")
    return _review_out(r, c, actor, scope)


# ── calibration view + publish ──────────────────────────────────────────────
@router.get("/cycles/{cycle_id}/calibration")
async def calibration(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "open calibration")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    rows = (await db.execute(select(PerfReview).where(PerfReview.org_id == actor.org, PerfReview.cycle_id == c.id)
                             .order_by(PerfReview.employee_name))).scalars().all()
    audits = (await db.execute(select(PerfReviewAudit).where(PerfReviewAudit.org_id == actor.org, PerfReviewAudit.cycle_id == c.id)
                               .order_by(PerfReviewAudit.at.desc()))).scalars().all()
    changed = {str(a.review_id) for a in audits if a.action == "calibrate"}
    t = c.template or {}
    grid = []
    for r in rows:
        own = bool(actor.employee_id) and str(r.employee_id) == actor.employee_id
        grid.append({
            "id": str(r.id), "employee_name": r.employee_name, "employee_number": r.employee_number,
            "department": r.department, "designation": r.designation, "grade": r.grade, "manager_name": r.manager_name,
            "stage": r.stage, "stage_label": P.STAGE_LABEL.get(r.stage),
            "goal_score": None if own else ((r.scores or {}).get("goal") or {}).get("score"),
            "competency_score": None if own else ((r.scores or {}).get("competency") or {}).get("score"),
            "overall_score": None if own else (float(r.overall_score) if r.overall_score is not None else None),
            "overall_text": None if own else ((r.scores or {}).get("overall") or {}).get("text"),
            "final_score": None if own and r.stage != "published" else (float(r.final_score) if r.final_score is not None else None),
            "final_band": None if own and r.stage != "published" else r.final_band,
            "calibration_note": None if own else r.calibration_note, "calibrated": str(r.id) in changed,
            "promotion_recommended": False if own else bool(r.promotion_recommended), "ack_status": r.ack_status, "own": own,
            "promotion_note": None if own else r.promotion_note,
            "pip_recommended": False if own else bool((r.manager_review or {}).get("pip_recommended")),
            "pip_note": None if own else (r.manager_review or {}).get("pip_note"),
            "recommended_hike_pct": None if own else (r.manager_review or {}).get("recommended_hike_pct"),
        })
    counted = [r.final_band for r in rows if r.stage in ("calibration", "published")]
    return {"cycle": await _cycle_out(db, c), "scale": t.get("scale") or [], "rows": grid,
            "distribution": P.distribution(counted, t.get("scale") or [], t.get("guideline") or []),
            "counted": len([b for b in counted if b]), "audit": [{**ser(a), "employee_name": next(
                (r.employee_name for r in rows if r.id == a.review_id), "")} for a in audits[:200]]}


@router.post("/cycles/{cycle_id}/publish")
async def publish_cycle(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Publish every review that is at HR calibration with a final rating.
    Reviews still in an earlier stage are left alone and listed in the answer."""
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can publish ratings.")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    if c.status not in ("active", "published"):
        raise HTTPException(409, "Launch the cycle first.")
    rows = (await db.execute(select(PerfReview).where(PerfReview.org_id == actor.org, PerfReview.cycle_id == c.id))).scalars().all()
    ready = [r for r in rows if r.stage == "calibration" and r.final_score is not None]
    if not ready:
        raise HTTPException(409, "No review is at HR calibration with a final rating yet.")
    res = await db.execute(update(PerfReview).where(
        PerfReview.org_id == actor.org, PerfReview.id.in_([r.id for r in ready]), PerfReview.stage == "calibration")
        .values(stage="published", published_at=now()))
    for r in ready:
        await _audit(db, r, actor, [{"action": "publish", "field": "stage", "old_value": "calibration", "new_value": "published"}])
    if c.status == "active":
        c.status, c.published_at = "published", now()
    await db.commit()
    await record_audit(request, "performance.cycle.publish", entity_type="perf_cycle", entity_id=str(c.id),
                       summary=f"{c.name}: {res.rowcount} ratings published", meta={"published": res.rowcount})
    await ext.notify(actor.org, user_ids=await ext.user_ids_for_employees(db, actor.org, [r.employee_id for r in ready]),
                     title="Your appraisal rating is published", body=c.name, link="/performance/reviews", kind="info")
    return {"published": res.rowcount, "left_behind": len(rows) - len(ready) - sum(1 for r in rows if r.stage == "published")}


@router.get("/cycles/{cycle_id}/promotions")
async def promotions(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Promotion recommendations from a cycle — a LIST for the career-change flow. Nothing is changed here."""
    actor = await get_actor(request, db)
    _need_hr(actor, "see promotion recommendations")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    rows = (await db.execute(select(PerfReview).where(
        PerfReview.org_id == actor.org, PerfReview.cycle_id == c.id, PerfReview.promotion_recommended.is_(True))
        .order_by(PerfReview.employee_name))).scalars().all()
    return [{"review_id": str(r.id), "employee_id": str(r.employee_id), "employee_name": r.employee_name,
             "employee_number": r.employee_number, "department": r.department, "designation": r.designation,
             "grade": r.grade, "manager_name": r.manager_name, "note": r.promotion_note, "stage": r.stage,
             "final_band": r.final_band if r.stage == "published" else None}
            for r in rows if not (actor.employee_id and str(r.employee_id) == actor.employee_id)]


# ── rating -> increment proposal ────────────────────────────────────────────
def _proposal_out(p: PerfIncrementProposal, c: PerfCycle) -> dict:
    items = [{**i, "increase": P.item_cost(i), "problem": P.check_item(i) if i.get("include") else None} for i in (p.items or [])]
    return {"id": str(p.id), "cycle_id": str(p.cycle_id), "cycle_name": c.name, "status": p.status,
            "budget": float(p.budget) if p.budget is not None else None,
            "effective_from": p.effective_from.isoformat() if p.effective_from else None, "matrix": p.matrix or [],
            "items": items, "totals": P.totals(p.items or [], p.budget), "batch_id": p.batch_id,
            "sent_at": p.sent_at.isoformat() if p.sent_at else None}


async def _build_items(db, actor: Actor, c: PerfCycle, matrix: list) -> list[dict]:
    reviews = [ser(r) for r in (await db.execute(select(PerfReview).where(
        PerfReview.org_id == actor.org, PerfReview.cycle_id == c.id, PerfReview.stage == "published"))).scalars().all()]
    emps = await _employees(db, actor.org)
    annual = {i: float(e.annual_salary or 0) for i, e in emps.items()}
    return P.build_items([r for r in reviews if r["employee_id"] in emps], annual, matrix)


@router.get("/cycles/{cycle_id}/increment-proposal")
async def get_proposal(cycle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The cycle's increment proposal. Created on first open, pre-filled from the matrix."""
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can work on increment proposals.")
    c = await one(db, PerfCycle, cycle_id, actor.org, "Appraisal cycle")
    p = (await db.execute(select(PerfIncrementProposal).where(
        PerfIncrementProposal.org_id == actor.org, PerfIncrementProposal.cycle_id == c.id))).scalar_one_or_none()
    if not p:
        if c.status != "published":
            raise HTTPException(409, "Publish the cycle's ratings first. The proposal is built from published ratings.")
        matrix = (await load_settings(db, actor.org))["increment_matrix"]
        p = PerfIncrementProposal(org_id=c.org_id, cycle_id=c.id, matrix=matrix, status="draft", created_by=actor.user_id,
                                  items=await _build_items(db, actor, c, matrix))
        db.add(p)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            p = (await db.execute(select(PerfIncrementProposal).where(
                PerfIncrementProposal.org_id == actor.org, PerfIncrementProposal.cycle_id == c.id))).scalar_one()
    return _proposal_out(p, c)


@router.post("/increment-proposals/{proposal_id}/rebuild")
async def rebuild_proposal(proposal_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Throw away the draft lines and fill them again from the CURRENT matrix and published ratings."""
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can work on increment proposals.")
    p = await one(db, PerfIncrementProposal, proposal_id, actor.org, "Increment proposal")
    c = await one(db, PerfCycle, p.cycle_id, actor.org, "Appraisal cycle")
    matrix = (await load_settings(db, actor.org))["increment_matrix"]
    await cas(db, PerfIncrementProposal, p, "status", "draft", matrix=matrix, items=await _build_items(db, actor, c, matrix))
    await db.commit()
    await db.refresh(p)
    return _proposal_out(p, c)


@router.put("/increment-proposals/{proposal_id}")
async def save_proposal(proposal_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can work on increment proposals.")
    p = await one(db, PerfIncrementProposal, proposal_id, actor.org, "Increment proposal")
    c = await one(db, PerfCycle, p.cycle_id, actor.org, "Appraisal cycle")
    if p.status != "draft":
        raise HTTPException(409, "This proposal has been sent to salary revisions and can no longer change here.")
    edits = {str(i.get("employee_id")): i for i in (body.get("items") or [])}
    items = []
    for it in p.items or []:
        e = edits.get(it["employee_id"])
        if e:
            it = {**it, "proposed_pct": P.r2(num(e.get("proposed_pct", it["proposed_pct"]), "The %", required=True, lo=0, hi=100)),
                  "reason": (str(e.get("reason") or "")).strip()[:300] or None,
                  "include": bool(e.get("include", it.get("include")))}
        items.append(it)
    values = {"items": items}
    if "budget" in body:
        values["budget"] = num(body.get("budget"), "Budget", lo=0)
    if "effective_from" in body:
        values["effective_from"] = to_date(body.get("effective_from"))
    await cas(db, PerfIncrementProposal, p, "status", "draft", **values)
    await db.commit()
    await db.refresh(p)
    return _proposal_out(p, c)


@router.post("/increment-proposals/{proposal_id}/send")
async def send_proposal(proposal_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Hand the proposal to payroll as DRAFT salary revisions and stop.
    Payroll owns the review, the approval and the applying."""
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can send an increment proposal.")
    p = await one(db, PerfIncrementProposal, proposal_id, actor.org, "Increment proposal")
    c = await one(db, PerfCycle, p.cycle_id, actor.org, "Appraisal cycle")
    if p.status != "draft":
        raise HTTPException(409, "This proposal has already been sent.")
    if not p.effective_from:
        raise HTTPException(422, "Set the effective date first.")
    bad = [i["name"] for i in (p.items or []) if i.get("include") and P.check_item(i)]
    if bad:
        raise HTTPException(422, f"{len(bad)} line(s) are outside their range without a reason. First: {bad[0]}.")
    payload = P.bulk_draft_payload(str(c.id), c.name, p.items or [], p.effective_from)
    if not payload["items"]:
        raise HTTPException(422, "There is nothing to send. Include at least one person with a % above 0.")
    await cas(db, PerfIncrementProposal, p, "status", "draft", status="sending")    # nobody else can send it now
    await db.commit()
    res = await ext.bulk_draft_revisions(actor.org, payload, actor.user_id, actor.role, actor.email)
    await db.refresh(p)
    if not res["ok"]:
        p.status = "draft"
        await db.commit()
        raise HTTPException(502, res["reason"])
    p.status, p.batch_id, p.sent_at, p.sent_by = "sent", res.get("batch_id"), now(), actor.user_id
    await db.commit()
    await record_audit(request, "performance.increment.send", entity_type="perf_increment_proposal", entity_id=str(p.id),
                       summary=f"{c.name}: {len(payload['items'])} increment drafts sent to salary revisions",
                       meta={"batch_id": p.batch_id, "people": len(payload["items"]), "effective_from": p.effective_from.isoformat()})
    resp = res.get("response") if isinstance(res.get("response"), dict) else {}
    return {**_proposal_out(p, c), "sent_items": resp.get("created", len(payload["items"])),
            "skipped": resp.get("skipped") or []}
