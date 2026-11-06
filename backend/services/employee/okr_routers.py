"""Performance G4 — objectives & key results (/performance/okrs).

  GET    /performance/okrs?cycle_id=&scope=          visible objectives (+ key results, roll-up, tree)
  POST   /performance/okrs                           new objective (+ key results)
  PATCH  /performance/okrs/{id}                      wording, alignment, weight, include-in-review
  POST   /performance/okrs/{id}/status               {status: draft | active | closed | cancelled}
  POST   /performance/okrs/{id}/key-results          add a key result
  PATCH  /performance/okrs/key-results/{kr_id}       change a key result (not its value: that is a check-in)
  DELETE /performance/okrs/key-results/{kr_id}
  GET    /performance/okrs/{id}/checkins             history
  POST   /performance/okrs/{id}/checkins             {key_result_id, value | milestones, confidence 1-10, comment}

Who may do what is perf_g4_logic.okr_can (checked HERE, not on the screen). Progress is never
typed: it is rolled up from key results, then up the alignment tree, after every change.
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402

import perf_g4_logic as G  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import emp_name, fail, get_actor, now, one, opt_uid, ser, uid  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from perf_g4_models import PerfKeyResult, PerfObjective, PerfOkrCheckin  # noqa: E402
from performance_models import PerfGoalCycle  # noqa: E402

router = APIRouter(prefix="/performance/okrs", tags=["performance-okrs"])


async def _emps(db, org: str) -> dict:
    rows = (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()
    return {str(e.id): e for e in rows}


def _mgr_of(emps: dict, employee_id) -> str | None:
    e = emps.get(str(employee_id or ""))
    return str(e.manager_id) if e is not None and e.manager_id else None


def _kr_dict(k: PerfKeyResult) -> dict:
    d = ser(k)
    d["confidence_band"] = G.confidence_band(k.confidence)
    return d


async def _cycle_rows(db, org: str, cycle_id) -> tuple[list[PerfObjective], dict[str, list[PerfKeyResult]]]:
    objs = (await db.execute(select(PerfObjective).where(PerfObjective.org_id == org, PerfObjective.cycle_id == cycle_id))).scalars().all()
    krs: dict[str, list[PerfKeyResult]] = {}
    if objs:
        for k in (await db.execute(select(PerfKeyResult).where(
                PerfKeyResult.org_id == org, PerfKeyResult.objective_id.in_([o.id for o in objs])))).scalars().all():
            krs.setdefault(str(k.objective_id), []).append(k)
    return list(objs), krs


async def recompute(db, org: str, cycle_id) -> None:
    """Roll progress up for the whole cycle (key results → objective → parents). Called after
    every change; cheap (one cycle's rows)."""
    objs, krs = await _cycle_rows(db, org, cycle_id)
    for ks in krs.values():
        for k in ks:
            k.progress_pct = G.kr_progress(_kr_dict(k))
    prog = G.rollup([ser(o) for o in objs], {oid: [_kr_dict(k) for k in ks] for oid, ks in krs.items()})
    for o in objs:
        o.progress_pct = prog.get(str(o.id), 0.0)


async def review_rows(db, org: str, cycle_id) -> dict[str, list[dict]]:
    """For the appraisal launch: {employee_id: [goal-like rows]} from individual objectives
    marked 'include in review' in this goal cycle (perf_g4_logic.review_snapshot)."""
    objs, krs = await _cycle_rows(db, org, cycle_id)
    prog = G.rollup([ser(o) for o in objs], {oid: [_kr_dict(k) for k in ks] for oid, ks in krs.items()})
    out: dict[str, list[dict]] = {}
    for o in objs:
        if o.owner_employee_id:
            rows = G.review_snapshot([ser(o)], prog)
            if rows:
                out.setdefault(str(o.owner_employee_id), []).extend(rows)
    return out


def _out(o: PerfObjective, krs: list[PerfKeyResult], actor: Actor, emps: dict) -> dict:
    d = ser(o)
    mgr = _mgr_of(emps, o.owner_employee_id)
    d.update({"key_results": [_kr_dict(k) for k in sorted(krs, key=lambda x: x.title or "")],
              "confidence_band": G.confidence_band(o.confidence), "level_label": G.LEVEL_LABEL.get(o.level, o.level),
              "relation": G.okr_relation(actor, d, mgr), "can_edit": G.okr_can("edit", actor, d, mgr),
              "can_checkin": G.okr_can("checkin", actor, d, mgr)})
    return d


async def _guarded(db, actor: Actor, obj_id: str, action: str) -> tuple[PerfObjective, dict]:
    o = await one(db, PerfObjective, obj_id, actor.org, "Objective")
    emps = await _emps(db, actor.org)
    if not G.okr_can("view", actor, ser(o), _mgr_of(emps, o.owner_employee_id)):
        raise HTTPException(404, "Objective not found")
    if action != "view" and not G.okr_can(action, actor, ser(o), _mgr_of(emps, o.owner_employee_id)):
        raise HTTPException(403, "You cannot change this objective.")
    return o, emps


@router.get("")
async def list_okrs(request: Request, cycle_id: str | None = None, scope: str = "all", db: AsyncSession = Depends(get_db)):
    """scope: all (everything I may see) | mine | team (my direct reports' + mine)."""
    actor = await get_actor(request, db)
    cycles = (await db.execute(select(PerfGoalCycle).where(PerfGoalCycle.org_id == actor.org)
                               .order_by(PerfGoalCycle.period_start.desc()))).scalars().all()
    if not cycles:
        return {"cycles": [], "cycle_id": None, "objectives": [], "tree": [], "summary": {}, "is_hr": actor.is_hr,
                "is_hr_head": actor.is_hr_head, "linked": bool(actor.employee_id)}
    cyc = next((c for c in cycles if str(c.id) == str(cycle_id)), None) if cycle_id else None
    cyc = cyc or next((c for c in cycles if c.status == "open"), cycles[0])
    objs, krs = await _cycle_rows(db, actor.org, cyc.id)
    emps = await _emps(db, actor.org)
    team = {i for i, e in emps.items() if actor.employee_id and str(e.manager_id or "") == actor.employee_id}
    out = []
    for o in objs:
        d = ser(o)
        if not G.okr_can("view", actor, d, _mgr_of(emps, o.owner_employee_id)):
            continue
        owner = str(o.owner_employee_id or "")
        if scope == "mine" and owner != actor.employee_id:
            continue
        if scope == "team" and owner not in team | {actor.employee_id}:
            continue
        out.append(_out(o, krs.get(str(o.id), []), actor, emps))
    bands = [o["confidence_band"] for o in out if o["status"] == "active"]
    summary = {"objectives": len(out), "avg_progress": G.weighted([(o["progress_pct"], 1) for o in out]) if out else 0,
               "on_track": bands.count("on_track"), "at_risk": bands.count("at_risk"), "off_track": bands.count("off_track"),
               "no_checkin": sum(1 for o in out if o["status"] == "active" and not o["confidence"])}
    return {"cycles": [ser(c) for c in cycles], "cycle_id": str(cyc.id), "objectives": out, "tree": G.build_tree(out),
            "summary": summary, "is_hr": actor.is_hr, "is_hr_head": actor.is_hr_head, "linked": bool(actor.employee_id),
            "metric_types": [{"value": k, "label": v} for k, v in G.METRIC_LABEL.items()],
            "levels": [{"value": k, "label": v} for k, v in G.LEVEL_LABEL.items()]}


def _fill(o: PerfObjective, body: dict):
    if "title" in body:
        o.title = str(body.get("title") or "").strip()[:300]
    if not o.title:
        raise HTTPException(422, "Give the objective a title.")
    if "description" in body:
        o.description = str(body.get("description") or "").strip()[:4000] or None
    if "weight" in body:
        try:
            w = float(body.get("weight"))
        except (TypeError, ValueError):
            raise HTTPException(422, "Weight must be a number.")
        if not 0 < w <= 100:
            raise HTTPException(422, "Weight must be between 0.01 and 100.")
        o.weight = w
    if "include_in_review" in body:
        o.include_in_review = bool(body.get("include_in_review"))
    if "department" in body:
        o.department = str(body.get("department") or "").strip()[:120] or None


@router.post("", status_code=201)
async def create_okr(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    cyc = await one(db, PerfGoalCycle, body.get("cycle_id"), actor.org, "Goal cycle")
    if cyc.status != "open":
        raise HTTPException(409, "This cycle is closed. Pick an open cycle.")
    level = body.get("level") or "individual"
    if level not in G.LEVELS:
        raise HTTPException(422, "Level must be company, department, team or individual.")
    emps = await _emps(db, actor.org)
    owner_id = body.get("owner_employee_id") or (actor.employee_id if level in ("individual", "team") else None)
    owner = emps.get(str(opt_uid(owner_id))) if owner_id else None
    if level in ("individual", "team") and owner is None:
        raise HTTPException(409 if not actor.employee_id else 422,
                            "Your login is not linked to an employee record yet. Ask HR to link it." if not actor.employee_id
                            else "Pick who owns this objective.")
    probe = {"level": level, "owner_employee_id": str(owner.id) if owner else None}
    if not G.okr_can("edit", actor, probe, _mgr_of(emps, owner.id) if owner else None):
        raise HTTPException(403, "Only HR can set company and department objectives; individual ones belong to the person, their manager or HR.")
    objs, _ = await _cycle_rows(db, actor.org, cyc.id)
    try:
        G.parent_ok(None, body.get("parent_id"), [ser(x) for x in objs], str(cyc.id))
        krs = [G.clean_key_result(k) for k in body.get("key_results") or []]
    except RuleError as e:
        fail(e)
    o = PerfObjective(org_id=uid(actor.org), cycle_id=cyc.id, level=level, owner_employee_id=owner.id if owner else None,
                      owner_name=emp_name(owner) if owner else None,
                      department=(owner.department if owner else None), title="", parent_id=opt_uid(body.get("parent_id")),
                      weight=100, status="active" if body.get("status") != "draft" else "draft", include_in_review=True,
                      created_by=actor.user_id)
    _fill(o, {"weight": 100, **body})
    db.add(o)
    await db.flush()
    for k in krs:
        db.add(PerfKeyResult(org_id=o.org_id, objective_id=o.id, **k))
    await db.flush()
    await recompute(db, actor.org, cyc.id)
    await db.commit()
    await record_audit(request, "performance.okr.create", entity_type="perf_okr", entity_id=str(o.id),
                       summary=f"Objective created: {o.title}", meta={"level": level, "key_results": len(krs)})
    objs, kr_map = await _cycle_rows(db, actor.org, cyc.id)
    me = next(x for x in objs if x.id == o.id)
    return _out(me, kr_map.get(str(o.id), []), actor, emps)


@router.patch("/key-results/{kr_id}")
async def update_kr(kr_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    k = await one(db, PerfKeyResult, kr_id, actor.org, "Key result")
    o, emps = await _guarded(db, actor, str(k.objective_id), "edit")
    merged = {**_kr_dict(k), **{x: body[x] for x in body if x != "current_value"}}
    try:
        clean = G.clean_key_result(merged)
    except RuleError as e:
        fail(e)
    clean.pop("current_value", None)
    if clean["metric_type"] == "milestone":
        clean["milestones"] = clean["milestones"]
    for f, v in clean.items():
        setattr(k, f, v)
    if k.metric_type != "milestone" and k.current_value is None:
        k.current_value = k.start_value
    k.updated_at = now()
    await recompute(db, actor.org, o.cycle_id)
    await db.commit()
    return _kr_dict(k)


@router.delete("/key-results/{kr_id}")
async def delete_kr(kr_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    k = await one(db, PerfKeyResult, kr_id, actor.org, "Key result")
    o, _ = await _guarded(db, actor, str(k.objective_id), "edit")
    await db.delete(k)
    await db.flush()
    await recompute(db, actor.org, o.cycle_id)
    await db.commit()
    return {"deleted": kr_id}


@router.patch("/{obj_id}")
async def update_okr(obj_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    o, emps = await _guarded(db, actor, obj_id, "edit")
    if "parent_id" in body:
        objs, _ = await _cycle_rows(db, actor.org, o.cycle_id)
        try:
            G.parent_ok(str(o.id), body.get("parent_id"), [ser(x) for x in objs], str(o.cycle_id))
        except RuleError as e:
            fail(e)
        o.parent_id = opt_uid(body.get("parent_id"))
    _fill(o, body)
    o.updated_at = now()
    await recompute(db, actor.org, o.cycle_id)
    await db.commit()
    objs, kr_map = await _cycle_rows(db, actor.org, o.cycle_id)
    me = next(x for x in objs if x.id == o.id)
    return _out(me, kr_map.get(str(o.id), []), actor, emps)


@router.post("/{obj_id}/status")
async def set_status(obj_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    o, emps = await _guarded(db, actor, obj_id, "edit")
    st = body.get("status")
    if st not in G.OBJ_STATUSES:
        raise HTTPException(422, "Status must be draft, active, closed or cancelled.")
    if st == o.status:
        raise HTTPException(409, f"This objective is already {st}.")
    old = o.status
    reason = str(body.get("reason") or "").strip()[:400] or None      # why it was closed / cancelled (audit only)
    o.status, o.updated_at = st, now()
    await recompute(db, actor.org, o.cycle_id)
    await db.commit()
    await record_audit(request, "performance.okr.status", entity_type="perf_okr", entity_id=str(o.id),
                       summary=f"Objective {o.title}: {old} → {st}" + (f" ({reason})" if reason else ""),
                       meta={"from": old, "to": st, "reason": reason})
    objs, kr_map = await _cycle_rows(db, actor.org, o.cycle_id)
    me = next(x for x in objs if x.id == o.id)
    return _out(me, kr_map.get(str(o.id), []), actor, emps)


@router.post("/{obj_id}/key-results", status_code=201)
async def add_kr(obj_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    o, _ = await _guarded(db, actor, obj_id, "edit")
    try:
        clean = G.clean_key_result(body)
    except RuleError as e:
        fail(e)
    k = PerfKeyResult(org_id=o.org_id, objective_id=o.id, **clean)
    db.add(k)
    await db.flush()
    await recompute(db, actor.org, o.cycle_id)
    await db.commit()
    return _kr_dict(k)


@router.get("/{obj_id}/checkins")
async def list_checkins(obj_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    o, _ = await _guarded(db, actor, obj_id, "view")
    rows = (await db.execute(select(PerfOkrCheckin).where(PerfOkrCheckin.org_id == actor.org, PerfOkrCheckin.objective_id == o.id)
                             .order_by(PerfOkrCheckin.at.desc()))).scalars().all()
    return [{**ser(r), "confidence_band": G.confidence_band(r.confidence)} for r in rows]


@router.post("/{obj_id}/checkins", status_code=201)
async def add_checkin(obj_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    o, _ = await _guarded(db, actor, obj_id, "checkin")
    if o.status != "active":
        raise HTTPException(409, "Only an active objective takes check-ins.")
    try:
        conf = G.clean_confidence(body.get("confidence"))
    except RuleError as e:
        fail(e)
    comment = str(body.get("comment") or "").strip()[:2000] or None
    k = None
    old_v = new_v = None
    ms = None
    if body.get("key_result_id"):
        k = await one(db, PerfKeyResult, body["key_result_id"], actor.org, "Key result")
        if k.objective_id != o.id:
            raise HTTPException(404, "Key result not found")
        if k.metric_type == "milestone":
            done = {str(x) for x in (body.get("done_milestones") or [])}
            ms = [{"label": m["label"], "done": m["label"] in done} for m in (k.milestones or [])]
            k.milestones = ms
        else:
            try:
                v = float(body.get("value"))
            except (TypeError, ValueError):
                raise HTTPException(422, "Type the new value of the key result.")
            if k.metric_type in ("currency",) and v < 0:
                raise HTTPException(422, "An amount cannot be negative.")
            old_v, new_v = (float(k.current_value) if k.current_value is not None else None), v
            k.current_value = v
        if conf is not None:
            k.confidence = conf
        k.updated_at = now()
    elif conf is None and not comment:
        raise HTTPException(422, "Pick a key result to update, or give a confidence or a comment.")
    if conf is not None:
        o.confidence = conf
    o.updated_at = now()
    await db.flush()
    await recompute(db, actor.org, o.cycle_id)
    row = PerfOkrCheckin(org_id=o.org_id, objective_id=o.id, key_result_id=k.id if k else None, old_value=old_v, new_value=new_v,
                         milestones=ms, progress_pct=(k.progress_pct if k else o.progress_pct), confidence=conf, comment=comment,
                         by_user=actor.user_id, by_name=actor.name)
    db.add(row)
    await db.commit()
    return {**ser(row), "objective_progress": float(o.progress_pct or 0), "confidence_band": G.confidence_band(conf)}
