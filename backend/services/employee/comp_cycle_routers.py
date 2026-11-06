"""Compensation planning cycles — API (employee service), mounted at /performance/comp
so it rides the gateway prefix the talent module already has.

Settings     GET/PUT /performance/comp/settings
Cycles       GET/POST /performance/comp/cycles · GET/PATCH /performance/comp/cycles/{id}
             GET  .../eligibility           who is in / out and why (nothing saved)
             POST .../populate              build the lines (draft / open: adds newly eligible people)
             POST .../move/{open|calibrate|reopen|submit|withdraw|close}
Worksheet    GET  .../worksheet             manager: own reports · HR: everyone (+ filters)
             PUT  .../lines/{line_id}       proposal / lump sum / promotion / comment (hard budget limit enforced)
             POST .../lines/bulk            HR bulk adjust · POST .../lock · .../unlock
             GET  .../export.csv · POST .../import  {csv}
Calibration  GET  .../calibration           department × rating grid, distributions, outliers, budgets
Approval     GET  .../approval · POST .../approval/{approve|reject}   (the inbox calls this back)
Apply        POST .../apply · GET .../apply-progress
Analytics    GET  .../analytics
Managers     GET  /performance/comp/my-worksheets

Apply never writes a salary itself: increases go through the EXISTING payroll bulk
revision path (draft batch → submit → approve → apply-due, retro pay for back-dated raises),
promotions become approved career-change records, letters come from the letter engine.
"""
import asyncio
import csv
import io
import logging
import sys
import uuid
from datetime import date
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import approvals_client  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import career_logic as CL  # noqa: E402
import career_routers as CR  # noqa: E402
import comp_cycle_logic as X  # noqa: E402
import hiring_integrations as ext  # noqa: E402
import letters_routers as LR  # noqa: E402
from career_common import full_name, get_extras  # noqa: E402
from career_models import CareerChange  # noqa: E402
from comp_cycle_models import CompCycle, CompCycleEvent, CompCycleLine, CompCycleSettings  # noqa: E402
from comp_models import CompHistory  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, emp_name, now, num, ser, to_date, uid  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from performance_models import PerfReview  # noqa: E402

router = APIRouter(prefix="/performance/comp", tags=["compensation-cycles"])
REQUEST_TYPE = "comp_cycle"


# ── helpers ──────────────────────────────────────────────────────────────────
async def get_actor(request: Request, db: AsyncSession) -> Actor:
    """Who is calling (gateway-stamped identity). The employee id is the stamped one,
    or — for a login not yet linked — the employee whose e-mail matches."""
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    a = Actor(org=org, user_id=request.headers.get("x-user-id") or "", role=(request.headers.get("x-role") or "").lower(),
              employee_id=request.headers.get("x-employee-id") or "", email=(request.headers.get("x-user-email") or "").lower())
    e = None
    if a.employee_id:
        try:
            e = (await db.execute(select(Employee).where(Employee.org_id == uid(org), Employee.id == uuid.UUID(a.employee_id)))).scalar_one_or_none()
        except ValueError:
            e = None
        if e is None:
            a.employee_id = ""
    if e is None and a.email:
        from sqlalchemy import func, or_
        e = (await db.execute(select(Employee).where(
            Employee.org_id == uid(org), Employee.is_active.is_(True),
            or_(func.lower(Employee.email) == a.email, func.lower(Employee.work_email) == a.email)).limit(1))).scalar_one_or_none()
        if e is not None:
            a.employee_id = str(e.id)
    a.name = emp_name(e) if e is not None else a.email
    return a


async def one(db: AsyncSession, model, row_id, org: str, what: str):
    row = (await db.execute(select(model).where(model.id == uid(row_id, what), model.org_id == uid(org)))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, f"{what} not found")
    return row
def _need_hr(actor: Actor, what: str = "do this"):
    if actor.role not in X.HR_ROLES:
        raise HTTPException(403, f"Only HR can {what}.")


def _need_head(actor: Actor, what: str = "do this"):
    if actor.role not in X.HR_HEAD_ROLES:
        raise HTTPException(403, f"Only an HR head or an org admin can {what}.")


def _fail(e: RuleError):
    raise HTTPException(e.status, e.message)


async def load_settings(db, org: str) -> dict:
    row = (await db.execute(select(CompCycleSettings).where(CompCycleSettings.org_id == uid(org)))).scalar_one_or_none()
    return X.merge_settings(row.data if row else None)


def cycle_dict(c: CompCycle) -> dict:
    d = ser(c)
    d["status_label"] = X.STATUS_LABEL.get(c.status, c.status)
    d["approval_state"] = (c.approval or {}).get("state")
    return d


def line_dict(ln: CompCycleLine) -> dict:
    d = ser(ln)
    d["new_annual"] = X.new_annual(d)
    d["flags"] = X.line_flags(d)
    return d


async def _cycle(db, actor: Actor, cid) -> CompCycle:
    return await one(db, CompCycle, cid, actor.org, "Compensation cycle")


async def _lines(db, c: CompCycle) -> list[CompCycleLine]:
    return list((await db.execute(select(CompCycleLine).where(CompCycleLine.cycle_id == c.id)
                                  .order_by(CompCycleLine.department, CompCycleLine.employee_name))).scalars().all())


def _usage(c: CompCycle, dicts: list[dict]) -> dict:
    return X.budget_usage(dicts, c.budgets or [], float(c.default_budget_pct) if c.default_budget_pct is not None else None,
                          float(c.promotion_budget) if c.promotion_budget is not None else None)


async def _event(db, c: CompCycle, actor: Actor, action: str, detail: dict | None = None) -> None:
    db.add(CompCycleEvent(org_id=c.org_id, cycle_id=c.id, action=action, detail=detail or {},
                          by_user=actor.email or actor.user_id, by_role=actor.role))


async def _entity_ids(db, org: str) -> dict:
    """D4 adds `entity_id` to employees; read it if it is there, else nothing."""
    try:
        async with db.begin_nested():                 # a savepoint: a missing column never expires the session
            stmt = text("SELECT id, entity_id FROM employees WHERE org_id = :o").bindparams(
                bindparam("o", type_=PG_UUID(as_uuid=True))).columns(id=PG_UUID(as_uuid=True))
            rows = (await db.execute(stmt, {"o": uid(org)})).all()
        return {str(i): (str(v) if v else None) for i, v in rows}
    except Exception:                                 # noqa: BLE001  (column not there yet)
        return {}


async def _ratings(db, org: str, perf_cycle_id) -> dict:
    """{employee_id: (final_band, final_score)} — the chosen appraisal cycle, else the
    latest PUBLISHED review of each employee."""
    q = select(PerfReview).where(PerfReview.org_id == uid(org), PerfReview.stage == "published")
    if perf_cycle_id:
        q = q.where(PerfReview.cycle_id == uid(perf_cycle_id))
    out: dict = {}
    for r in (await db.execute(q.order_by(PerfReview.published_at))).scalars().all():
        out[str(r.employee_id)] = (r.final_band, float(r.final_score) if r.final_score is not None else None)
    return out


def _cycle_rules(c: CompCycle) -> dict:
    return {"grade_midpoints": c.grade_midpoints or {}, "compa_bands": c.compa_bands or X.DEFAULT_COMPA_BANDS, "matrix": c.matrix or []}


# ── settings ─────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see compensation settings")
    return {**(await load_settings(db, actor.org)), "can_edit": actor.is_hr_head, "is_hr": actor.is_hr}


@router.put("/settings")
async def put_settings(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_head(actor, "change compensation settings")
    cur = await load_settings(db, actor.org)
    clean: dict = {}
    try:
        if "compa_bands" in body:
            clean["compa_bands"] = X.clean_compa_bands(body["compa_bands"])
        keys = {b["key"] for b in clean.get("compa_bands") or cur["compa_bands"]}
        if "default_matrix" in body:
            clean["default_matrix"] = X.clean_matrix(body["default_matrix"], keys)
    except RuleError as e:
        _fail(e)
    if "grade_midpoints" in body:
        mids = {}
        for g, v in (body.get("grade_midpoints") or {}).items():
            if str(g).strip() and v not in (None, ""):
                mids[str(g).strip()] = num(v, f"Midpoint for {g}", lo=0)
        clean["grade_midpoints"] = mids
    if "budget_mode" in body:
        if body["budget_mode"] not in ("hard", "soft"):
            raise HTTPException(422, "Budget mode must be hard or soft.")
        clean["budget_mode"] = body["budget_mode"]
    if "default_budget_pct" in body:
        clean["default_budget_pct"] = num(body["default_budget_pct"], "Default budget %", lo=0, hi=100)
    if "allow_lump_sum" in body:
        clean["allow_lump_sum"] = bool(body["allow_lump_sum"])
    if "guidelines" in body:
        clean["guidelines"] = str(body.get("guidelines") or "")[:4000]
    row = (await db.execute(select(CompCycleSettings).where(CompCycleSettings.org_id == uid(actor.org)))).scalar_one_or_none()
    if not row:
        row = CompCycleSettings(org_id=uid(actor.org), data={})
        db.add(row)
    row.data = {**(row.data or {}), **clean}
    row.updated_at, row.updated_by = now(), actor.email or actor.user_id
    await db.commit()
    await record_audit(request, "comp_cycle.settings.save", entity_type="comp_cycle_settings", entity_id=actor.org,
                       summary="Compensation cycle settings changed", meta={"keys": sorted(clean)})
    return {**X.merge_settings(row.data), "can_edit": True, "is_hr": True}


# ── cycles ───────────────────────────────────────────────────────────────────
@router.get("/cycles")
async def list_cycles(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see compensation cycles")
    rows = (await db.execute(select(CompCycle).where(CompCycle.org_id == uid(actor.org))
                             .order_by(CompCycle.created_at.desc()))).scalars().all()
    out = []
    for c in rows:
        dicts = [line_dict(x) for x in await _lines(db, c)]
        out.append({**cycle_dict(c), "summary": X.summary(dicts, _usage(c, dicts))})
    return out


def _apply_setup(c: CompCycle, body: dict, cur_settings: dict, creating: bool) -> None:
    if "name" in body or creating:
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(422, "Give the cycle a name.")
        c.name = name[:160]
    for k in ("period_start", "period_end", "effective_from"):
        if k in body or creating:
            v = to_date(body.get(k))
            if not v:
                raise HTTPException(422, f"{k.replace('_', ' ').capitalize()} is required.")
            setattr(c, k, v)
    if c.period_end <= c.period_start:
        raise HTTPException(422, "The period end must be after its start.")
    if "perf_cycle_id" in body:
        c.perf_cycle_id = uid(body["perf_cycle_id"], "Appraisal cycle") if body.get("perf_cycle_id") else None
    if "eligibility" in body or creating:
        e = dict(body.get("eligibility") or {})
        jb = to_date(e.get("joined_before"))
        c.eligibility = {"departments": [str(x) for x in (e.get("departments") or []) if str(x).strip()],
                         "grades": [str(x) for x in (e.get("grades") or []) if str(x).strip()],
                         "entity_ids": [str(x) for x in (e.get("entity_ids") or []) if str(x).strip()],
                         "joined_before": jb.isoformat() if jb else None,
                         "require_rating": bool(e.get("require_rating", False)),
                         "exclude_notice": bool(e.get("exclude_notice", True))}
    try:
        if "budgets" in body:
            c.budgets = X.clean_budgets(body["budgets"])
        if "compa_bands" in body or creating:
            c.compa_bands = X.clean_compa_bands(body.get("compa_bands") or cur_settings["compa_bands"])
        if "matrix" in body or creating:
            c.matrix = X.clean_matrix(body.get("matrix") if "matrix" in body else cur_settings["default_matrix"],
                                      {b["key"] for b in c.compa_bands})
    except RuleError as e:
        _fail(e)
    if "grade_midpoints" in body or creating:
        src = body.get("grade_midpoints") if "grade_midpoints" in body else cur_settings["grade_midpoints"]
        c.grade_midpoints = {str(g): num(v, f"Midpoint for {g}", lo=0) for g, v in (src or {}).items() if v not in (None, "")}
    if "budget_mode" in body or creating:
        mode = body.get("budget_mode") or cur_settings["budget_mode"]
        if mode not in ("hard", "soft"):
            raise HTTPException(422, "Budget mode must be hard or soft.")
        c.budget_mode = mode
    if "default_budget_pct" in body or creating:
        v = body.get("default_budget_pct") if "default_budget_pct" in body else cur_settings["default_budget_pct"]
        c.default_budget_pct = num(v, "Default budget %", lo=0, hi=100)
    if "promotion_budget" in body:
        c.promotion_budget = num(body.get("promotion_budget"), "Promotion budget", lo=0)
    if "guidelines" in body or creating:
        c.guidelines = str(body.get("guidelines") if "guidelines" in body else cur_settings["guidelines"] or "")[:4000]
    if "allow_lump_sum" in body or creating:
        c.allow_lump_sum = bool(body.get("allow_lump_sum", cur_settings["allow_lump_sum"]))


@router.post("/cycles", status_code=201)
async def create_cycle(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_head(actor, "create a compensation cycle")
    c = CompCycle(org_id=uid(actor.org), currency="CAD", status="draft", created_by=actor.email or actor.user_id,
                  created_by_id=actor.user_id or None, budgets=[], approval={}, apply_progress={})
    _apply_setup(c, body, await load_settings(db, actor.org), creating=True)
    db.add(c)
    await db.commit()
    await _event(db, c, actor, "create")
    await db.commit()
    await record_audit(request, "comp_cycle.create", entity_type="comp_cycle", entity_id=str(c.id),
                       summary=f"Compensation cycle '{c.name}' created")
    return cycle_dict(c)


@router.get("/cycles/{cid}")
async def get_cycle(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    c = await _cycle(db, actor, cid)
    lines = await _lines(db, c)
    if not actor.is_hr and not any(actor.employee_id and str(x.manager_id or "") == actor.employee_id for x in lines):
        raise HTTPException(403, "Only HR and the managers in this cycle can see it.")
    dicts = [line_dict(x) for x in lines]
    out = {**cycle_dict(c), "lines_count": len(lines), "is_hr": actor.is_hr, "is_hr_head": actor.is_hr_head}
    if actor.is_hr:
        usage = _usage(c, dicts)
        out["summary"] = X.summary(dicts, usage)
        out["budget_usage"] = usage
        out["events"] = [ser(e) for e in (await db.execute(select(CompCycleEvent).where(CompCycleEvent.cycle_id == c.id)
                                                            .order_by(CompCycleEvent.at.desc()).limit(50))).scalars().all()]
    return out


@router.patch("/cycles/{cid}")
async def update_cycle(cid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_head(actor, "change a compensation cycle")
    c = await _cycle(db, actor, cid)
    if c.status in ("applying", "applied", "closed"):
        raise HTTPException(409, "An applied cycle cannot be changed.")
    if c.status not in ("draft",):
        # once managers are in, only budgets / guidelines / promotion budget may still move
        body = {k: v for k, v in body.items() if k in ("name", "budgets", "budget_mode", "default_budget_pct", "promotion_budget", "guidelines")}
    _apply_setup(c, body, await load_settings(db, actor.org), creating=False)
    if c.status == "draft" and any(k in body for k in ("matrix", "grade_midpoints", "compa_bands")):
        for ln in await _lines(db, c):                       # re-derive the recommendations
            d = ser(ln)
            for k, v in X.recommend(d, _cycle_rules(c)).items():
                setattr(ln, k, v)
    await db.commit()
    await _event(db, c, actor, "update", {"keys": sorted(body)})
    await db.commit()
    return cycle_dict(c)


# ── eligibility + populate ──────────────────────────────────────────────────
async def _population(db, actor: Actor, c: CompCycle) -> list[dict]:
    """Every active employee with the data eligibility and the matrix need."""
    emps = (await db.execute(select(Employee).where(Employee.org_id == uid(actor.org)))).scalars().all()
    extras = await get_extras(db, actor.org)
    entities = await _entity_ids(db, actor.org)
    ratings = await _ratings(db, actor.org, c.perf_cycle_id)
    today = await org_today(actor.org)
    names = {str(e.id): full_name(e) for e in emps}
    out = []
    for e in emps:
        eid = str(e.id)
        grade = (extras.get(eid) or {}).get("grade") or getattr(e, "grade", None)
        rating, score = ratings.get(eid, (None, None))
        emp = {"id": eid, "status": e.status, "is_active": e.is_active, "hire_date": e.hire_date, "department": e.department,
               "grade": grade, "entity_id": entities.get(eid), "annual_salary": float(e.annual_salary or 0),
               "pay_type": e.pay_type, "termination_date": e.termination_date}
        ok, why = X.eligible(emp, c.eligibility or {}, today, rating)
        out.append({"employee_id": eid, "employee_name": full_name(e), "employee_number": e.employee_number,
                    "department": e.department, "designation": e.job_title, "grade": grade, "gender": e.gender,
                    "entity_id": entities.get(eid), "manager_id": str(e.manager_id) if e.manager_id else None,
                    "manager_name": names.get(str(e.manager_id)) if e.manager_id else e.manager_name,
                    "hire_date": e.hire_date, "current_annual": float(e.annual_salary or 0),
                    "rating": rating, "rating_score": score, "eligible": ok, "reason": why})
    return out


@router.get("/cycles/{cid}/eligibility")
async def eligibility(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "check eligibility")
    c = await _cycle(db, actor, cid)
    pop = await _population(db, actor, c)
    have = {str(x.employee_id) for x in await _lines(db, c)}
    for p in pop:
        p["in_cycle"] = p["employee_id"] in have
        p["hire_date"] = p["hire_date"].isoformat() if p["hire_date"] else None
    return {"eligible": sum(1 for p in pop if p["eligible"]), "excluded": sum(1 for p in pop if not p["eligible"]),
            "in_cycle": len(have), "people": pop}


@router.post("/cycles/{cid}/populate")
async def populate(cid: str, request: Request, reset: int = 0, db: AsyncSession = Depends(get_db)):
    """Create one line per eligible employee. In draft, ?reset=1 throws the lines away
    first; otherwise people already in the cycle are left exactly as they are."""
    actor = await get_actor(request, db)
    _need_head(actor, "build the cycle population")
    c = await _cycle(db, actor, cid)
    if c.status not in ("draft", "open"):
        raise HTTPException(409, "People can only be added while the cycle is a draft or open.")
    pop = await _population(db, actor, c)              # before any delete: reading entity ids may roll back
    existing = {str(x.employee_id): x for x in await _lines(db, c)}
    if reset and c.status == "draft":
        for x in existing.values():
            await db.delete(x)
        existing = {}
        await db.flush()
    added = 0
    for p in pop:
        if not p["eligible"] or p["employee_id"] in existing:
            continue
        rec = X.recommend(p, _cycle_rules(c))
        ln = CompCycleLine(org_id=c.org_id, cycle_id=c.id, employee_id=uid(p["employee_id"]),
                           employee_name=p["employee_name"], employee_number=p["employee_number"], department=p["department"],
                           designation=p["designation"], grade=p["grade"], gender=p["gender"], entity_id=p["entity_id"],
                           manager_id=uid(p["manager_id"]) if p["manager_id"] else None, manager_name=p["manager_name"],
                           hire_date=p["hire_date"], current_annual=p["current_annual"], rating=p["rating"],
                           rating_score=p["rating_score"], include=True, apply_result={}, **rec)
        d = X.set_proposal({"current_annual": p["current_annual"]}, pct=rec["rec_pct"] or 0)
        ln.proposed_pct, ln.proposed_amount = d["proposed_pct"], d["proposed_amount"]
        db.add(ln)
        added += 1
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Someone else was adding people at the same time. Refresh and try again.")
    await _event(db, c, actor, "populate", {"added": added, "reset": bool(reset)})
    await db.commit()
    return {"added": added, "total": len(existing) + added}


# ── status moves ─────────────────────────────────────────────────────────────
@router.post("/cycles/{cid}/move/{what}")
async def move(cid: str, what: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_head(actor, "move a compensation cycle")
    if what not in X.MOVES or what in ("submit", "apply"):
        raise HTTPException(404, "Unknown move")
    c = await _cycle(db, actor, cid)
    if c.status not in X.MOVES[what]:
        raise HTTPException(409, f"'{what}' is not possible while the cycle is {X.STATUS_LABEL.get(c.status, c.status)}.")
    lines = await _lines(db, c)
    to = {"open": "open", "calibrate": "calibration", "reopen": "open", "withdraw": "calibration", "close": "closed"}[what]
    values: dict = {"status": to}
    if what == "open":
        if not lines:
            raise HTTPException(422, "Build the population first (there is nobody in the cycle).")
        values["opened_at"] = now()
    if what == "withdraw":
        values["approval"] = {**(c.approval or {}), "state": "withdrawn"}
        await approvals_client.close_item(actor.org, kind=REQUEST_TYPE, ref_id=str(c.id), decision="withdrawn", actor_user_id=actor.user_id)
    if what == "close":
        values["closed_at"] = now()
    await cas(db, CompCycle, c, "status", c.status, **values)
    await db.commit()
    await db.refresh(c)
    await _event(db, c, actor, what)
    await db.commit()
    await record_audit(request, f"comp_cycle.{what}", entity_type="comp_cycle", entity_id=str(c.id),
                       summary=f"Compensation cycle '{c.name}': {what}")
    return cycle_dict(c)


# ── worksheet ────────────────────────────────────────────────────────────────
def _visible(actor: Actor, lines: list[CompCycleLine]) -> list[CompCycleLine]:
    if actor.is_hr:
        return lines
    if not actor.employee_id:
        return []
    return [x for x in lines if str(x.manager_id or "") == actor.employee_id]


def _can_edit(actor: Actor, c: CompCycle) -> bool:
    if actor.is_hr:
        return c.status in ("open", "calibration")
    return c.status == "open"


@router.get("/cycles/{cid}/worksheet")
async def worksheet(cid: str, request: Request, department: str | None = None, manager_id: str | None = None,
                    db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    c = await _cycle(db, actor, cid)
    all_lines = await _lines(db, c)
    mine = _visible(actor, all_lines)
    if not actor.is_hr and not mine:
        raise HTTPException(403, "You have no reports in this cycle.")
    all_dicts = [line_dict(x) for x in all_lines]
    usage = _usage(c, all_dicts)
    X.flag_budget(all_dicts, usage)
    by_id = {d["id"]: d for d in all_dicts}
    rows = [by_id[str(x.id)] for x in mine]
    if department:
        rows = [r for r in rows if (r.get("department") or "") == department]
    if manager_id:
        rows = [r for r in rows if (r.get("manager_id") or "") == manager_id]
    team_cost = X.r2(sum(X.line_cost(r) for r in rows))
    team_base = X.r2(sum(float(r.get("current_annual") or 0) for r in rows))
    scopes = sorted({f"department:{r['department']}" for r in rows if r.get("department")}
                    | {f"entity:{r['entity_id']}" for r in rows if r.get("entity_id")} | {"total:", "promotion"})
    return {"cycle": {**cycle_dict(c), "can_edit": _can_edit(actor, c), "is_hr": actor.is_hr, "is_hr_head": actor.is_hr_head},
            "lines": rows, "team": {"people": len(rows), "base": team_base, "used": team_cost,
                                    "used_pct": round(team_cost * 100 / team_base, 2) if team_base else 0.0},
            "budget_usage": {k: usage[k] for k in scopes if k in usage} if not actor.is_hr else usage,
            "departments": sorted({r["department"] for r in all_dicts if r.get("department")}) if actor.is_hr else [],
            "managers": sorted({(r["manager_id"], r["manager_name"]) for r in all_dicts if r.get("manager_id")}) if actor.is_hr else []}


def _edit_line(ln: CompCycleLine, body: dict, actor: Actor, c: CompCycle) -> dict:
    d = ser(ln)
    try:
        if "proposed_pct" in body:
            X.set_proposal(d, pct=body.get("proposed_pct") or 0)
        elif "proposed_amount" in body:
            X.set_proposal(d, amount=body.get("proposed_amount") or 0)
    except RuleError as e:
        _fail(e)
    if "lump_sum" in body:
        if not c.allow_lump_sum and (body.get("lump_sum") or 0):
            raise HTTPException(422, "Lump sums are switched off for this cycle.")
        d["lump_sum"] = X.r2(num(body.get("lump_sum") or 0, "Lump sum", lo=0))
    if "promotion" in body:
        d["promotion"] = bool(body["promotion"])
    if "promo_designation" in body:
        d["promo_designation"] = (str(body.get("promo_designation") or "").strip() or None)
    if "promo_grade" in body:
        d["promo_grade"] = (str(body.get("promo_grade") or "").strip() or None)
    if not d["promotion"]:
        d["promo_designation"] = d["promo_grade"] = None
    if "comment" in body:
        key = "hr_comment" if actor.is_hr else "manager_comment"
        d[key] = (str(body.get("comment") or "").strip()[:600] or None)
    if "include" in body and actor.is_hr:
        d["include"] = bool(body["include"])
    if actor.is_hr and any(k in body for k in ("proposed_pct", "proposed_amount", "lump_sum", "promotion", "include")):
        d["hr_adjusted"] = True
    return d


@router.put("/cycles/{cid}/lines/{lid}")
async def update_line(cid: str, lid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    c = await _cycle(db, actor, cid)
    ln = await one(db, CompCycleLine, lid, actor.org, "Worksheet line")
    if ln.cycle_id != c.id:
        raise HTTPException(404, "Worksheet line not found")
    if not _can_edit(actor, c):
        raise HTTPException(409, f"The worksheet cannot be edited while the cycle is {X.STATUS_LABEL.get(c.status, c.status)}.")
    if not actor.is_hr and str(ln.manager_id or "") != (actor.employee_id or "-"):
        raise HTTPException(403, "You can only plan for your own reports.")
    if ln.locked and not actor.is_hr:
        raise HTTPException(409, "HR has locked this line.")
    d = _edit_line(ln, body, actor, c)
    all_dicts = [line_dict(x) if x.id != ln.id else {**d, "flags": []} for x in await _lines(db, c)]
    usage = _usage(c, all_dicts)
    why = X.check_hard_limit(d, usage, c.budget_mode)
    if why:
        raise HTTPException(422, why)
    for k in ("proposed_pct", "proposed_amount", "lump_sum", "promotion", "promo_designation", "promo_grade",
              "manager_comment", "hr_comment", "include", "hr_adjusted"):
        setattr(ln, k, d[k])
    ln.updated_by = actor.email or actor.user_id
    await db.commit()
    await db.refresh(ln)
    out = line_dict(ln)
    X.flag_budget([out], usage)
    return {"line": out, "budget_usage": usage}


@router.post("/cycles/{cid}/lines/bulk")
async def bulk_adjust(cid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """HR: {line_ids?|department?|rating?|grade?, mode: set_pct | scale | to_recommended | cap_to_max | include, value?, reason?}
    ``reason`` (max 500 chars) and the filters used are written to the cycle's event log."""
    actor = await get_actor(request, db)
    _need_hr(actor, "bulk adjust")
    c = await _cycle(db, actor, cid)
    if c.status not in ("open", "calibration"):
        raise HTTPException(409, "Bulk changes are only possible while the cycle is open or in calibration.")
    mode = body.get("mode")
    if mode not in ("set_pct", "scale", "to_recommended", "cap_to_max", "include", "exclude"):
        raise HTTPException(422, "Unknown bulk mode.")
    ids = {str(x) for x in (body.get("line_ids") or [])}
    n = 0
    for ln in await _lines(db, c):
        if ids and str(ln.id) not in ids:
            continue
        if body.get("department") and (ln.department or "") != body["department"]:
            continue
        if body.get("rating") and (ln.rating or "") != body["rating"]:
            continue
        if body.get("grade") and (ln.grade or "") != body["grade"]:
            continue
        d = ser(ln)
        try:
            if mode == "set_pct":
                X.set_proposal(d, pct=num(body.get("value"), "The %", required=True, lo=0, hi=100))
            elif mode == "scale":
                f = num(body.get("value"), "The factor", required=True, lo=0)
                X.set_proposal(d, pct=round(float(d["proposed_pct"] or 0) * f, 2))
            elif mode == "to_recommended":
                X.set_proposal(d, pct=float(d["rec_pct"] or 0))
            elif mode == "cap_to_max":
                if d["max_pct"] is not None and float(d["proposed_pct"] or 0) > float(d["max_pct"]):
                    X.set_proposal(d, pct=float(d["max_pct"]))
            elif mode in ("include", "exclude"):
                d["include"] = mode == "include"
        except RuleError as e:
            _fail(e)
        ln.proposed_pct, ln.proposed_amount, ln.include = d["proposed_pct"], d["proposed_amount"], d["include"]
        ln.hr_adjusted, ln.updated_by = True, actor.email or actor.user_id
        n += 1
    await db.commit()
    reason = (str(body.get("reason") or "").strip()[:500]) or None
    await _event(db, c, actor, "bulk_adjust", {"mode": mode, "value": body.get("value"), "lines": n, "reason": reason,
                                               "filters": {k: body[k] for k in ("department", "rating", "grade") if body.get(k)}})
    await db.commit()
    return {"changed": n}


@router.post("/cycles/{cid}/lock")
async def lock_lines(cid: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    return await _lock(cid, "lock", body, request, db)


@router.post("/cycles/{cid}/unlock")
async def unlock_lines(cid: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    return await _lock(cid, "unlock", body, request, db)


async def _lock(cid: str, what: str, body: dict | None, request: Request, db: AsyncSession):
    actor = await get_actor(request, db)
    _need_hr(actor, "lock worksheet lines")
    c = await _cycle(db, actor, cid)
    ids = {str(x) for x in ((body or {}).get("line_ids") or [])}
    n = 0
    for ln in await _lines(db, c):
        if ids and str(ln.id) not in ids:
            continue
        ln.locked = what == "lock"
        n += 1
    await db.commit()
    await _event(db, c, actor, what, {"lines": n})
    await db.commit()
    return {"changed": n}


@router.get("/cycles/{cid}/export.csv")
async def export_csv(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    c = await _cycle(db, actor, cid)
    rows = [line_dict(x) for x in _visible(actor, await _lines(db, c))]
    if not rows and not actor.is_hr:
        raise HTTPException(403, "You have no reports in this cycle.")
    return Response(content=X.to_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="comp-worksheet-{c.name[:40].replace(" ", "-")}.csv"'})


@router.post("/cycles/{cid}/import")
async def import_csv(cid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """{csv: "..."} — the exported sheet with the editable columns changed. A manager may
    only import rows for their own reports; everything else is reported, not applied."""
    actor = await get_actor(request, db)
    c = await _cycle(db, actor, cid)
    lines = await _lines(db, c)
    visible = _visible(actor, lines)
    if not actor.is_hr and not visible:             # HR, or a manager with reports in this cycle
        raise HTTPException(403, "You have no reports in this cycle.")
    if not _can_edit(actor, c):
        raise HTTPException(409, "The worksheet cannot be edited now.")
    rows, errors = X.parse_csv(body.get("csv") or "")
    mine = {x.employee_number: x for x in visible}
    all_dicts = {str(x.id): line_dict(x) for x in lines}
    changed = 0
    for r in rows:
        ln = mine.get(r["employee_number"])
        if not ln:
            errors.append(f"{r['employee_number']}: not in your worksheet.")
            continue
        if ln.locked and not actor.is_hr:
            errors.append(f"{r['employee_number']}: locked by HR.")
            continue
        edit = {k: v for k, v in r.items() if k != "employee_number"}
        if "proposed_pct" in edit and "proposed_amount" in edit:
            edit.pop("proposed_amount")
        try:
            d = _edit_line(ln, edit, actor, c)
        except HTTPException as e:
            errors.append(f"{r['employee_number']}: {e.detail}")
            continue
        trial = {**all_dicts, str(ln.id): {**d, "flags": []}}
        why = X.check_hard_limit(d, _usage(c, list(trial.values())), c.budget_mode)
        if why:
            errors.append(f"{r['employee_number']}: {why}")
            continue
        all_dicts = trial
        for k in ("proposed_pct", "proposed_amount", "lump_sum", "promotion", "promo_designation", "promo_grade",
                  "manager_comment", "hr_comment", "hr_adjusted"):
            setattr(ln, k, d[k])
        ln.updated_by = actor.email or actor.user_id
        changed += 1
    await db.commit()
    await _event(db, c, actor, "import", {"changed": changed, "errors": len(errors)})
    await db.commit()
    return {"changed": changed, "errors": errors}


# ── calibration ──────────────────────────────────────────────────────────────
@router.get("/cycles/{cid}/calibration")
async def calibration(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see calibration")
    c = await _cycle(db, actor, cid)
    dicts = [line_dict(x) for x in await _lines(db, c)]
    usage = _usage(c, dicts)
    X.flag_budget(dicts, usage)
    return {"cycle": cycle_dict(c), "summary": X.summary(dicts, usage), "budget_usage": usage,
            "by_department": X.group_stats(dicts, "department"), "by_rating": X.group_stats(dicts, "rating"),
            "grid": X.rating_matrix(dicts), "compa": X.compa_distribution(dicts, c.compa_bands or X.DEFAULT_COMPA_BANDS),
            "outliers": [d for d in dicts if d.get("flags")], "lines": dicts}


# ── approval ─────────────────────────────────────────────────────────────────
async def _resolve_chain(org: str, metadata: dict) -> list[dict]:
    """D1's no-code chain registry when it is there (`approvals_client.resolve_chain`),
    else the fallback chain kept here."""
    fn = getattr(approvals_client, "resolve_chain", None)
    if fn:
        try:
            steps = await fn(org, REQUEST_TYPE, metadata)
            steps = [s for s in (steps or []) if s.get("roles") or s.get("user_ids")]
            if steps:
                return [{"roles": list(s.get("roles") or []), "user_ids": [str(u) for u in (s.get("user_ids") or [])],
                         "label": s.get("label") or ", ".join(s.get("roles") or []) or "Approver"} for s in steps]
        except Exception:                             # noqa: BLE001 — never let the registry block a cycle
            pass
    return X.default_chain()


async def _open_step(c: CompCycle, actor_user_id: str, step: dict, total: float) -> None:
    await approvals_client.open_item(
        str(c.org_id), kind=REQUEST_TYPE, ref_id=str(c.id), title=f"Compensation cycle: {c.name}",
        summary=[f"Effective {c.effective_from.isoformat()}", f"Total increase and lump sums: {total:,.2f} {c.currency}",
                 f"Step: {step.get('label') or 'approver'}"],
        requester_user_id=actor_user_id, approver_roles=step.get("roles") or None, approver_user_ids=step.get("user_ids") or None,
        link=f"/comp/cycles/{c.id}/approval", callback_path=f"/performance/comp/cycles/{c.id}/approval/{{decision}}")


@router.post("/cycles/{cid}/submit")
async def submit(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Calibration → approval: route through the approvals inbox as request type comp_cycle."""
    actor = await get_actor(request, db)
    _need_head(actor, "submit a cycle for approval")
    c = await _cycle(db, actor, cid)
    if c.status != "calibration":
        raise HTTPException(409, "Move the cycle to calibration first, then submit it.")
    dicts = [line_dict(x) for x in await _lines(db, c)]
    usage = _usage(c, dicts)
    if c.budget_mode == "hard" and any(v.get("over") for v in usage.values()):
        raise HTTPException(422, "A budget is overrun. Fix the lines (or raise the budget) before submitting.")
    if not any(X.line_cost(d) > 0 or d.get("promotion") for d in dicts):
        raise HTTPException(422, "There is nothing to approve: no increase, lump sum or promotion in the cycle.")
    meta = X.chain_metadata(dicts, usage)
    chain = await _resolve_chain(actor.org, meta)
    approval = {"state": "pending", "request_type": REQUEST_TYPE, "metadata": meta, "chain": chain, "step": 0,
                "decisions": [], "requester_user_id": actor.user_id, "requester": actor.email}
    await cas(db, CompCycle, c, "status", "calibration", status="approval", approval=approval, submitted_at=now())
    await db.commit()
    await db.refresh(c)
    await _open_step(c, actor.user_id, chain[0], meta["total_amount"])
    await _event(db, c, actor, "submit", {"chain": [s.get("label") for s in chain], "total": meta["total_amount"]})
    await db.commit()
    await record_audit(request, "comp_cycle.submit", entity_type="comp_cycle", entity_id=str(c.id),
                       summary=f"Compensation cycle '{c.name}' sent for approval", meta=meta)
    return cycle_dict(c)


@router.get("/cycles/{cid}/approval")
async def approval_state(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    c = await _cycle(db, actor, cid)
    a = c.approval or {}
    step = (a.get("chain") or [{}])[a.get("step", 0)] if a.get("chain") and a.get("step", 0) < len(a["chain"]) else None
    can = bool(step and a.get("state") == "pending" and X.may_decide(step, actor.role, a.get("requester_user_id"), actor.user_id) is None)
    return {"cycle": cycle_dict(c), "approval": a, "current_step": step, "can_decide": can,
            "events": [ser(e) for e in (await db.execute(select(CompCycleEvent).where(
                CompCycleEvent.cycle_id == c.id, CompCycleEvent.action.in_(("submit", "approve", "reject", "withdraw")))
                .order_by(CompCycleEvent.at))).scalars().all()]}


@router.post("/cycles/{cid}/approval/{decision}")
async def decide(cid: str, decision: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    """approve | reject — called from the Approval tab or by the inbox (callback contract)."""
    actor = await get_actor(request, db)
    decision = approvals_client.normalise_decision(decision)
    if decision not in ("approved", "rejected"):
        raise HTTPException(404, "Unknown decision")
    c = await _cycle(db, actor, cid)
    a = dict(c.approval or {})
    if c.status != "approval" or a.get("state") != "pending":
        raise HTTPException(409, "This cycle is not waiting for approval.")
    chain, i = a.get("chain") or X.default_chain(), int(a.get("step") or 0)
    step = chain[i] if i < len(chain) else chain[-1]
    why = X.may_decide(step, actor.role, a.get("requester_user_id"), actor.user_id)
    if why:
        raise HTTPException(403, why)
    comment = ((body or {}).get("comment") or (body or {}).get("note") or "").strip()[:400] or None
    if decision == "rejected" and not comment:
        raise HTTPException(400, "Give a reason for rejecting.")
    a["decisions"] = list(a.get("decisions") or []) + [{"step": i, "label": step.get("label"), "decision": decision,
                                                         "by": actor.email or actor.user_id, "by_id": actor.user_id,
                                                         "role": actor.role, "comment": comment, "at": now().isoformat()}]
    values: dict = {}
    if decision == "rejected":
        a["state"], a["step"] = "rejected", i
        values = {"status": "calibration", "approval": a}
    elif i + 1 < len(chain):
        a["step"] = i + 1
        values = {"approval": a}
    else:
        a["state"], a["step"] = "approved", i + 1
        values = {"approval": a, "approved_at": now()}
    await cas(db, CompCycle, c, "status", "approval", **values)
    await db.commit()
    await db.refresh(c)
    await approvals_client.close_item(actor.org, kind=REQUEST_TYPE, ref_id=str(c.id), decision=decision, actor_user_id=actor.user_id)
    if decision == "approved" and a["state"] == "pending":
        await _open_step(c, a.get("requester_user_id"), chain[a["step"]], float((a.get("metadata") or {}).get("total_amount") or 0))
    await _event(db, c, actor, "approve" if decision == "approved" else "reject", {"step": i, "comment": comment, "state": a["state"]})
    await db.commit()
    await record_audit(request, f"comp_cycle.{decision}", entity_type="comp_cycle", entity_id=str(c.id),
                       summary=f"Compensation cycle '{c.name}' {decision} at step {i + 1}", meta={"state": a["state"], "comment": comment})
    return {**cycle_dict(c), "approval": a}


# ── apply ────────────────────────────────────────────────────────────────────
def _payroll_headers(org: str, actor: Actor | None = None) -> dict:
    if actor:
        return ext.fwd_headers(org, actor.user_id, actor.role, actor.email)
    return ext.fwd_headers(org, "", "org_admin", "system@comp-cycles")


async def payroll_batch_action(org: str, batch_id: str, action: str, headers: dict) -> dict:
    """POST /payroll/revisions/batches/{id}/{submit|approve} → {ok, result | reason}."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{settings.PAYROLL_URL}/payroll/revisions/batches/{batch_id}/{action}", json={}, headers=headers)
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"The payroll service could not be reached ({exc.__class__.__name__})."}
    if r.status_code >= 300:
        return {"ok": False, "reason": f"Payroll refused '{action}' ({r.status_code}): {r.text[:300]}"}
    d = r.json() if r.content else {}
    return {"ok": True, "result": d.get("result") or {}, "status": d.get("status")}


async def _progress(db, c: CompCycle, **upd) -> None:
    c.apply_progress = {**(c.apply_progress or {}), **upd}
    await db.commit()


async def _issue(db, org: str, e: Employee, key: str, data: dict, eff: date, actor: Actor, src: str) -> dict:
    """One letter through the EXISTING engine. A missing break-up table alone does not
    stop the letter; any other empty field is reported, not invented."""
    req = SimpleNamespace(headers={"x-org-id": org, "x-role": actor.role or "hr_admin", "x-user-email": actor.email or "system",
                                   "x-user-id": actor.user_id or ""})
    body = LR.LetterIn(template_key=key, data=data, effective_date=eff.isoformat(), issue=True, allow_blank=False,
                       visible_to_employee=True, source=src)
    try:
        row = await LR.generate(db, req, org, e, body)
    except HTTPException as ex:
        missing = (ex.detail or {}).get("missing_fields") if isinstance(ex.detail, dict) else None
        if ex.status_code == 422 and missing and set(missing) <= {"salary.breakup_table"}:
            body.allow_blank = True
            row = await LR.generate(db, req, org, e, body)
        else:
            msg = ex.detail.get("message") if isinstance(ex.detail, dict) else str(ex.detail)
            return {"error": f"{key}: {msg}"}
    return {"letter_id": str(row.id), "number": row.number}


async def run_apply(session_factory, cycle_id: str, org: str, actor: Actor) -> dict:
    """The apply steps, each one recorded in cycle.apply_progress so a retry never
    repeats what is already done (the batch id, each career change, each letter)."""
    async with session_factory() as db:
        c = (await db.execute(select(CompCycle).where(CompCycle.id == uid(cycle_id), CompCycle.org_id == uid(org)))).scalar_one()
        lines = await _lines(db, c)
        dicts = {str(x.id): line_dict(x) for x in lines}
        work = [x for x in lines if x.include and (float(x.proposed_amount or 0) > 0 or x.promotion)]
        prog = dict(c.apply_progress or {})
        errors = list(prog.get("errors") or [])
        prog.update({"state": "running", "total": len(work), "done": 0, "errors": errors, "started_at": prog.get("started_at") or now().isoformat()})
        await _progress(db, c, **prog)
        # 1. increases → the EXISTING payroll bulk revision path (draft → submit → approve → apply-due + arrears)
        await _progress(db, c, step="revisions")
        if not prog.get("batch_id"):
            payload = X.bulk_payload(str(c.id), c.name, list(dicts.values()), c.effective_from)
            if payload["items"]:
                res = await ext.bulk_draft_revisions(org, payload, "", "org_admin", "system@comp-cycles")
                if not res.get("ok"):
                    errors.append(f"Salary revisions: {res.get('reason')}")
                    await _progress(db, c, state="failed", errors=errors, step="revisions")
                    c.status = "approval"
                    await db.commit()
                    return c.apply_progress
                resp = res.get("response") or {}
                prog.update({"batch_id": res.get("batch_id"), "revisions_created": resp.get("created"),
                             "revisions_skipped": resp.get("skipped") or []})
                await _progress(db, c, **prog)
                for x in lines:
                    if x.include and float(x.proposed_amount or 0) > 0:
                        x.apply_result = {**(x.apply_result or {}), "revision_batch_id": res.get("batch_id")}
                await db.commit()
            else:
                prog["batch_id"] = None
        if prog.get("batch_id") and not prog.get("batch_approved"):
            sub = await payroll_batch_action(org, prog["batch_id"], "submit", _payroll_headers(org))
            if not sub["ok"] and "Only a draft" not in sub.get("reason", ""):
                errors.append(f"Salary revisions: {sub['reason']}")
            appr = await payroll_batch_action(org, prog["batch_id"], "approve", _payroll_headers(org, actor))
            if appr["ok"]:
                r = appr["result"]
                prog.update({"batch_approved": True, "revisions_approved": r.get("approved"), "revisions_applied_now": r.get("applied_now"),
                             "revisions_waiting_second_approval": r.get("waiting_second_approval")})
            else:
                errors.append(f"Salary revisions: {appr['reason']} — approve the batch in Payroll > Salary revisions.")
                prog["batch_approved"] = False
            prog["errors"] = errors
            await _progress(db, c, **prog)
        # 2. promotions → approved career-change records (applied on their date by the existing tick)
        await _progress(db, c, step="promotions")
        extras = await get_extras(db, org)
        for x in work:
            if not x.promotion or (x.apply_result or {}).get("career_change_id"):
                continue
            e = (await db.execute(select(Employee).where(Employee.id == x.employee_id))).scalar_one_or_none()
            if not e:
                errors.append(f"{x.employee_name}: employee record not found")
                continue
            cur = CR._current(e, extras.get(str(e.id), {}))
            nv, errs = CL.clean_new_values("promotion", {"job_title": x.promo_designation or cur.get("job_title"),
                                                          "grade": x.promo_grade or cur.get("grade")}, cur)
            if errs:
                errors.append(f"{x.employee_name}: promotion skipped - {' '.join(errs)}")
                x.apply_result = {**(x.apply_result or {}), "promotion_error": " ".join(errs)}
                await db.commit()
                continue
            cc = CareerChange(org_id=c.org_id, employee_id=e.id, change_type="promotion", effective_date=c.effective_from,
                              old_values=CL.capture_old(cur, nv), new_values=nv, reason=f"Compensation cycle: {c.name}"[:200],
                              note=x.hr_comment or x.manager_comment, status="approved", submitted_at=now(),
                              created_by="system@comp-cycles", decided_by=actor.email or actor.user_id, decided_at=now(),
                              decision_note=f"Approved with compensation cycle '{c.name}'")
            db.add(cc)
            await db.flush()
            x.apply_result = {**(x.apply_result or {}), "career_change_id": str(cc.id), "old_values": cc.old_values}
            await db.commit()
        try:
            await CR.apply_due(db, org)
        except Exception:                             # noqa: BLE001
            await db.rollback()
            ref = uuid.uuid4().hex[:8].upper()        # real error → log; the run shows a fixed sentence + ref
            logging.getLogger("lexora.comp_cycle").exception("apply_due failed (ref %s)", ref)
            errors.append(f"Promotions due today could not be applied (ref {ref}).")
        # 3. letters → the EXISTING letter engine (salary_revision / promotion), visible in self-service
        await _progress(db, c, step="letters", errors=errors)
        done = 0
        for x in work:
            ar = dict(x.apply_result or {})
            e = (await db.execute(select(Employee).where(Employee.id == x.employee_id))).scalar_one_or_none()
            if e:
                if float(x.proposed_amount or 0) > 0 and not ar.get("letter_id"):
                    last = (await db.execute(select(CompHistory).where(CompHistory.employee_id == e.id)
                                             .order_by(CompHistory.effective_from.desc(), CompHistory.created_at.desc()))).scalars().first()
                    comps = last.components if last and last.components else None
                    res = await _issue(db, org, e, "salary_revision", X.revision_letter_data(dicts[str(x.id)], c.effective_from, comps),
                                       c.effective_from, actor, f"comp_cycle:{c.id}")
                    if res.get("error"):
                        errors.append(f"{x.employee_name}: {res['error']}")
                        ar["letter_error"] = res["error"]
                    else:
                        ar.update({"letter_id": res["letter_id"], "letter_number": res["number"]})
                        ar.pop("letter_error", None)
                if x.promotion and ar.get("career_change_id") and not ar.get("promotion_letter_id"):
                    old = ar.get("old_values") or {}
                    res = await _issue(db, org, e, "promotion", X.promotion_letter_data(dicts[str(x.id)], c.effective_from, old),
                                       c.effective_from, actor, f"comp_cycle:{c.id}")
                    if res.get("error"):
                        errors.append(f"{x.employee_name}: {res['error']}")
                        ar["promotion_letter_error"] = res["error"]
                    else:
                        ar.update({"promotion_letter_id": res["letter_id"], "promotion_letter_number": res["number"]})
                        ar.pop("promotion_letter_error", None)
            x.apply_result = ar
            done += 1
            await _progress(db, c, done=done, errors=errors)
        c.status, c.applied_at = "applied", now()
        await _progress(db, c, state="done", step="done", done=done, errors=errors, finished_at=now().isoformat())
        db.add(CompCycleEvent(org_id=c.org_id, cycle_id=c.id, action="apply", by_user=actor.email, by_role=actor.role,
                              detail={"batch_id": prog.get("batch_id"), "done": done, "errors": len(errors)}))
        await db.commit()
        return c.apply_progress


@router.post("/cycles/{cid}/apply")
async def apply(cid: str, request: Request, wait: int = 0, db: AsyncSession = Depends(get_db)):
    """Approved → applying. Runs in the background; poll /apply-progress. ?wait=1 runs inline."""
    actor = await get_actor(request, db)
    _need_head(actor, "apply a compensation cycle")
    c = await _cycle(db, actor, cid)
    if c.status != "approval" or (c.approval or {}).get("state") != "approved":
        raise HTTPException(409, "The cycle must be approved before it can be applied.")
    await cas(db, CompCycle, c, "status", "approval", status="applying",
              apply_progress={**(c.apply_progress or {}), "state": "queued", "errors": []})
    await db.commit()
    await record_audit(request, "comp_cycle.apply", entity_type="comp_cycle", entity_id=str(c.id),
                       summary=f"Compensation cycle '{c.name}': apply started")
    sf = session_factory()
    if wait:
        return await run_apply(sf, str(c.id), actor.org, actor)
    task = asyncio.create_task(run_apply(sf, str(c.id), actor.org, actor))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"state": "queued", "cycle_id": str(c.id)}


_TASKS: set = set()


def session_factory():
    """The service's session maker (tests swap this for their own engine)."""
    from database import SessionLocal
    return SessionLocal


@router.get("/cycles/{cid}/apply-progress")
async def apply_progress(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see apply progress")
    c = await _cycle(db, actor, cid)
    lines = [line_dict(x) for x in await _lines(db, c) if x.include and (float(x.proposed_amount or 0) > 0 or x.promotion)]
    return {"cycle": cycle_dict(c), "progress": c.apply_progress or {}, "lines": lines}


# ── analytics ────────────────────────────────────────────────────────────────
@router.get("/cycles/{cid}/analytics")
async def analytics(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    _need_hr(actor, "see cycle analytics")
    c = await _cycle(db, actor, cid)
    dicts = [line_dict(x) for x in await _lines(db, c)]
    usage = _usage(c, dicts)
    X.flag_budget(dicts, usage)
    return {"cycle": cycle_dict(c), "summary": X.summary(dicts, usage), "budget_usage": usage,
            "by_department": X.group_stats(dicts, "department"), "by_rating": X.group_stats(dicts, "rating"),
            "by_gender": X.group_stats(dicts, "gender"), "by_grade": X.group_stats(dicts, "grade"),
            "compa": X.compa_distribution(dicts, c.compa_bands or X.DEFAULT_COMPA_BANDS),
            "pay_equity": X.pay_equity(dicts),
            "note": "Pay-equity figures are descriptive averages for review. They are not a legal finding."}


# ── managers ─────────────────────────────────────────────────────────────────
@router.get("/my-worksheets")
async def my_worksheets(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    q = select(CompCycle).where(CompCycle.org_id == uid(actor.org), CompCycle.status.in_(("open", "calibration", "approval", "applying", "applied")))
    out = []
    for c in (await db.execute(q.order_by(CompCycle.created_at.desc()))).scalars().all():
        mine = _visible(actor, await _lines(db, c)) if not actor.is_hr else await _lines(db, c)
        if not mine:
            continue
        dicts = [line_dict(x) for x in mine]
        out.append({**cycle_dict(c), "can_edit": _can_edit(actor, c), "people": len(dicts),
                    "used": X.r2(sum(X.line_cost(d) for d in dicts)),
                    "base": X.r2(sum(float(d.get("current_annual") or 0) for d in dicts)),
                    "flagged": sum(1 for d in dicts if d["flags"])})
    return out
