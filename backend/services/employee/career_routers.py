"""Career changes + probation → confirmation — API (employee service).

PUBLISHED
  POST /employees/{employee_id}/career-changes
       {change_type, effective_date, new_values: {job_title?, department?, grade?, manager_id?,
        work_location?, division?, cost_center?}, reason?, note?, linked_revision_id?, submit?: bool}
       → the record (old values are captured automatically)
  GET  /employees/{employee_id}/career-changes        timeline (HR, or the employee: applied only)
  GET  /employees/{employee_id}/career                grade · probation / confirmed · confirmation date

Career changes (HR)
  GET  /employees/career/changes?status=
  POST /employees/career/changes/{id}/submit | approve | reject | cancel | letter
  POST /employees/career/apply-due                    apply approved changes whose date has arrived
  GET  /employees/career/fields                       types, governed fields, extras per employee

Probation
  GET  /employees/career/probation-due?within=30&all=0
  POST /employees/career/probation/assign-missing     set end dates for joiners still inside probation
  POST /employees/{employee_id}/probation/recommend   manager step {recommendation, reason, new_end_date?}
  POST /employees/{employee_id}/probation/confirm     {confirmation_date?, reason?, issue_letter}
  POST /employees/{employee_id}/probation/extend      {new_end_date, reason, issue_letter}
  POST /employees/{employee_id}/probation/do-not-confirm {reason, last_working_date?}  → POST /exit/cases
  GET/PUT /employees/career/settings                  default probation months (+ per grade / type)

`apply_due` is also run by the service's lifecycle tick (see lifecycle_init / lifecycle_tick).
"""
import asyncio
import os
import sys
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import career_logic as L  # noqa: E402
from career_common import (APPROVER_ROLES, actor_of, emp_of, ensure_columns, full_name, get_extras,  # noqa: E402
                           is_self, load_employee, now, org_of, parse_date, require_setup, role_of,
                           same_person, set_extras, to_uuid, uid_of)
from career_common import is_people_hr as is_hr, require_people_hr as require_hr  # noqa: E402  (decision 2026-10-21: HR only, not the pay office)
from career_models import CareerChange, CareerSnapshot, LifecycleSettings, ProbationAction  # noqa: E402
from database import SessionLocal, engine, get_db  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/employees", tags=["career"])


# ── shared bits ───────────────────────────────────────────────────────────────
def _current(e: Employee, extra: dict) -> dict:
    return {"job_title": e.job_title, "department": e.department, "grade": extra.get("grade"),
            "manager_id": str(e.manager_id) if e.manager_id else None, "manager_name": e.manager_name,
            "work_location": e.work_location, "division": e.division, "cost_center": e.cost_center}


def change_out(c: CareerChange, e: Employee | None = None) -> dict:
    return {"id": str(c.id), "employee_id": str(c.employee_id),
            "employee": full_name(e) if e else None, "employee_number": e.employee_number if e else None,
            "change_type": c.change_type, "type_label": L.TYPES.get(c.change_type, (c.change_type,))[0],
            "effective_date": c.effective_date.isoformat(), "old_values": c.old_values or {},
            "new_values": c.new_values or {}, "reason": c.reason, "note": c.note,
            "linked_revision_id": c.linked_revision_id, "status": c.status,
            "created_by": c.created_by, "created_at": c.created_at.isoformat() if c.created_at else None,
            "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
            "decided_by": c.decided_by, "decided_at": c.decided_at.isoformat() if c.decided_at else None,
            "decision_note": c.decision_note, "applied_at": c.applied_at.isoformat() if c.applied_at else None,
            "letter_id": c.letter_id,
            "summary": "; ".join(f"{L.GOVERNED_FIELDS[k]}: {(_show(c.old_values, k)) or '—'} → {_show(c.new_values, k) or '—'}"
                                 for k in (c.new_values or {}) if k in L.GOVERNED_FIELDS)}


def _show(vals: dict | None, k: str):
    vals = vals or {}
    return vals.get("manager_name") if k == "manager_id" else vals.get(k)


async def _emps(db, org: str) -> dict:
    return {e.id: e for e in (await db.execute(select(Employee).where(
        Employee.org_id == to_uuid(org, "org")))).scalars().all()}


async def apply_change(db, c: CareerChange) -> bool:
    """Write an approved change onto the employee record. Idempotent (applied once)."""
    if c.status != "approved":
        return False
    e = (await db.execute(select(Employee).where(Employee.id == c.employee_id))).scalar_one_or_none()
    if not e:
        return False
    nv = c.new_values or {}
    for k in L.MODEL_FIELDS:
        if k in nv:
            setattr(e, k, to_uuid(nv[k], "manager") if (k == "manager_id" and nv[k]) else nv[k])
    extra = {k: nv[k] for k in L.EXTRA_FIELDS if k in nv}
    before = (await get_extras(db, str(c.org_id), str(e.id))).get(str(e.id), {})
    if extra:
        await set_extras(db, e.id, **extra)
    c.status, c.applied_at = "applied", now()
    await _save_snapshot(db, e, {**_current(e, before), **extra})      # this change is now the known state
    return True


async def _save_snapshot(db, e: Employee, values: dict) -> None:
    s = (await db.execute(select(CareerSnapshot).where(CareerSnapshot.employee_id == e.id))).scalar_one_or_none()
    if s:
        s.values = values
    else:
        db.add(CareerSnapshot(employee_id=e.id, org_id=e.org_id, values=values))


async def record_direct_edits(db, org: str) -> int:
    """SAFETY NET. A designation / department / grade / manager / location that was
    changed straight on the employee record (not through a career change) is written
    to the history as an applied record marked as a direct edit. The first time an
    employee is seen only the snapshot is taken. Idempotent."""
    emps = await _emps(db, org)
    extras = await get_extras(db, org)
    snaps = {s.employee_id: s for s in (await db.execute(select(CareerSnapshot).where(
        CareerSnapshot.org_id == to_uuid(org, "org")))).scalars().all()}
    n, dirty = 0, False
    for e in emps.values():
        cur = _current(e, extras.get(str(e.id), {}))
        s = snaps.get(e.id)
        if not s:
            db.add(CareerSnapshot(employee_id=e.id, org_id=e.org_id, values=cur))
            dirty = True
            continue
        diff = L.governed_diff(s.values, cur)
        if not diff:
            continue
        old = {k: (s.values or {}).get(k) for k in diff}
        if "manager_id" in diff:
            old["manager_name"], diff["manager_name"] = (s.values or {}).get("manager_name"), cur.get("manager_name")
        db.add(CareerChange(org_id=e.org_id, employee_id=e.id, change_type=L.infer_type(diff),
                            effective_date=await org_today(org), old_values=old, new_values=diff,
                            reason="Edited on the employee record",
                            note="Recorded automatically. This was changed on the employee form, "
                                 "without a career-change record or an approval.",
                            status="applied", created_by="system", applied_at=now()))
        s.values = cur
        n += 1
    if n or dirty:
        await db.commit()
    return n


async def apply_due(db, org: str | None = None, today: date | None = None) -> int:
    """Apply every approved change whose effective date has arrived, oldest first."""
    today = today or await org_today(org)          # no org (cross-org tick) → the default (America/Toronto) today
    q = select(CareerChange).where(CareerChange.status == "approved", CareerChange.effective_date <= today)
    if org:
        q = q.where(CareerChange.org_id == to_uuid(org, "org"))
    rows = (await db.execute(q.order_by(CareerChange.effective_date, CareerChange.created_at)
                             .with_for_update(skip_locked=True))).scalars().all()
    n = 0
    for c in rows:
        if await apply_change(db, c):
            n += 1
    if n:
        await db.commit()
    return n


# ── static routes (two segments, so they never collide with /{employee_id}) ───
@router.get("/career/fields")
async def career_fields(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    return {"types": [{"key": k, "label": v[0], "needs_one_of": list(v[1])} for k, v in L.TYPES.items()],
            "fields": [{"key": k, "label": v} for k, v in L.GOVERNED_FIELDS.items()],
            "letter_for_type": L.LETTER_FOR_TYPE,
            "locked_in_employee_form": sorted(list(L.GOVERNED_FIELDS) + ["manager_name", "probation_end_date"]),
            "extras": await get_extras(db, org)}


@router.get("/career/changes")
async def list_changes(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    await record_direct_edits(db, org)
    await apply_due(db, org)
    q = select(CareerChange).where(CareerChange.org_id == to_uuid(org, "org"))
    if status:
        q = q.where(CareerChange.status == status)
    rows = (await db.execute(q.order_by(CareerChange.created_at.desc()))).scalars().all()
    emps = await _emps(db, org)
    return [change_out(c, emps.get(c.employee_id)) for c in rows]


@router.post("/career/apply-due")
async def apply_due_now(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    return {"direct_edits_recorded": await record_direct_edits(db, org), "applied": await apply_due(db, org),
            "probation_dates_set": await assign_missing(db, org)}


async def _change(db, org: str, cid: str) -> CareerChange:
    c = (await db.execute(select(CareerChange).where(CareerChange.id == to_uuid(cid, "record"),
                                                     CareerChange.org_id == to_uuid(org, "org")))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Career change not found")
    return c


class DecisionIn(BaseModel):
    note: str | None = None


async def _decide(cid: str, action: str, body: DecisionIn | None, request: Request, db) -> dict:
    require_hr(request)
    org = org_of(request)
    c = await _change(db, org, cid)
    err = L.decision_error(c.status, action, is_maker=same_person(request, c.created_by_id, c.created_by),
                           role=role_of(request), approver_roles=APPROVER_ROLES)
    if err:
        raise HTTPException(403 if ("org admin" in err or "Someone else" in err) else 409, err)
    note = (body.note if body else None) or None
    if action == "submit":
        c.status, c.submitted_at = "submitted", now()
    elif action == "approve":
        c.status, c.decided_by, c.decided_at, c.decision_note = "approved", actor_of(request), now(), note
        if L.is_due("approved", c.effective_date, await org_today(org)):
            await record_direct_edits(db, org)          # anything typed on the form is recorded first
            await apply_change(db, c)
    elif action == "reject":
        if not note:
            raise HTTPException(400, "Give a reason for rejecting")
        c.status, c.decided_by, c.decided_at, c.decision_note = "rejected", actor_of(request), now(), note
    elif action == "cancel":
        c.status, c.decision_note = "cancelled", note or c.decision_note
    await db.commit()
    await record_audit(request, f"career.change.{action}", entity_type="career_change", entity_id=str(c.id),
                       summary=f"Career change {action}: {c.change_type} from {c.effective_date}", meta={"status": c.status})
    e = await load_employee(db, org, str(c.employee_id))
    return change_out(c, e)


@router.post("/career/changes/{cid}/submit")
async def submit_change(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _decide(cid, "submit", None, request, db)


@router.post("/career/changes/{cid}/approve")
async def approve_change(cid: str, request: Request, body: DecisionIn | None = None, db: AsyncSession = Depends(get_db)):
    return await _decide(cid, "approve", body, request, db)


@router.post("/career/changes/{cid}/reject")
async def reject_change(cid: str, body: DecisionIn, request: Request, db: AsyncSession = Depends(get_db)):
    return await _decide(cid, "reject", body, request, db)


@router.post("/career/changes/{cid}/cancel")
async def cancel_change(cid: str, request: Request, body: DecisionIn | None = None, db: AsyncSession = Depends(get_db)):
    return await _decide(cid, "cancel", body, request, db)


class LetterReq(BaseModel):
    template_key: str | None = None
    issue: bool = True


@router.post("/career/changes/{cid}/letter", status_code=201)
async def change_letter(cid: str, request: Request, body: LetterReq | None = None, db: AsyncSession = Depends(get_db)):
    """Issue the promotion / transfer letter for an approved or applied change."""
    require_hr(request)
    org = org_of(request)
    c = await _change(db, org, cid)
    if c.status not in ("approved", "applied"):
        raise HTTPException(409, "Approve the change before issuing its letter")
    key = (body.template_key if body else None) or L.LETTER_FOR_TYPE.get(c.change_type)
    if not key:
        raise HTTPException(400, "There is no standard letter for this type. Pick a template.")
    e = await load_employee(db, org, str(c.employee_id))
    o, n = c.old_values or {}, c.new_values or {}
    cur = _current(e, (await get_extras(db, org, str(e.id))).get(str(e.id), {}))

    def pair(field, key_old, key_new):
        return {key_old: o.get(field) if field in n else cur.get(field), key_new: n.get(field, cur.get(field))}
    data = {"career": {"change_type": L.TYPES[c.change_type][0], "effective_date": c.effective_date.isoformat(),
                       "reason": c.reason, **pair("job_title", "old_designation", "new_designation"),
                       **pair("department", "old_department", "new_department"),
                       **pair("grade", "old_grade", "new_grade"),
                       **pair("work_location", "old_location", "new_location"),
                       "old_entity": o.get("entity_name"), "new_entity": n.get("entity_name"),   # D4 entity_transfer
                       "old_manager": o.get("manager_name") if "manager_id" in n else cur.get("manager_name"),
                       "new_manager": n.get("manager_name", cur.get("manager_name"))}}
    from letters_routers import LetterIn, generate
    row = await generate(db, request, org, e, LetterIn(template_key=key, data=data,
                                                       effective_date=c.effective_date.isoformat(),
                                                       issue=(body.issue if body else True), source="career_change"))
    c.letter_id = str(row.id)
    await db.commit()
    return {"letter_id": str(row.id), "number": row.number, "pdf_url": f"/letters/{row.id}/pdf", "status": row.status}


# ── probation settings ────────────────────────────────────────────────────────
async def _settings(db, org: str) -> dict:
    s = (await db.execute(select(LifecycleSettings).where(LifecycleSettings.org_id == to_uuid(org, "org")))).scalar_one_or_none()
    return {"probation_months": s.probation_months if s else L.DEFAULT_PROBATION_MONTHS,
            "probation_rules": (s.probation_rules if s else None) or [], "is_default": s is None}


class SettingsIn(BaseModel):
    probation_months: int
    probation_rules: list[dict] | None = None


@router.get("/career/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    return await _settings(db, org_of(request))


@router.put("/career/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_setup(request)
    org = org_of(request)
    if not 0 <= body.probation_months <= 36:
        raise HTTPException(400, "Probation months must be between 0 and 36")
    rules = []
    for r in body.probation_rules or []:
        try:
            m = int(r.get("months"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Each rule needs a number of months")
        if not 0 <= m <= 36 or not (r.get("grade") or r.get("employment_type")):
            raise HTTPException(400, "Each rule needs a grade or an employment type, and 0–36 months")
        rules.append({"grade": (r.get("grade") or "").strip() or None,
                      "employment_type": (r.get("employment_type") or "").strip() or None, "months": m})
    s = (await db.execute(select(LifecycleSettings).where(LifecycleSettings.org_id == to_uuid(org, "org")))).scalar_one_or_none()
    if not s:
        s = LifecycleSettings(org_id=to_uuid(org, "org"))
        db.add(s)
    s.probation_months, s.probation_rules, s.updated_by = body.probation_months, rules, actor_of(request)
    await db.commit()
    return await _settings(db, org)


# ── probation list + actions ──────────────────────────────────────────────────
async def assign_missing(db, org: str, today: date | None = None) -> int:
    """Set the probation end date for joiners who have a joining date, no end date and
    no status, and who are STILL inside their probation window. People whose window
    has already passed are left untouched (their status is unknown, not assumed)."""
    today = today or await org_today(org)
    st = await _settings(db, org)
    extras = await get_extras(db, org)
    n = 0
    for e in (await _emps(db, org)).values():
        x = extras.get(str(e.id), {})
        if e.status == "terminated" or not e.is_active or not e.hire_date or x.get("employment_status"):
            continue
        if e.probation_end_date:
            await set_extras(db, e.id, employment_status="probation")     # date came from the joining form
            n += 1
            continue
        months = L.probation_months_for(st, x.get("grade"), e.employment_type)
        if months <= 0:
            continue
        end = L.probation_end(e.hire_date, months)
        if end < today:
            continue
        e.probation_end_date = end
        await set_extras(db, e.id, employment_status="probation")
        db.add(ProbationAction(org_id=e.org_id, employee_id=e.id, action="assign", new_end_date=end,
                               reason=f"Set from joining date + {months} months", actor="system"))
        n += 1
    if n:
        await db.commit()
    return n


@router.post("/career/probation/assign-missing")
async def assign_missing_now(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    return {"updated": await assign_missing(db, org_of(request))}


@router.get("/career/probation-due")
async def probation_due(request: Request, within: int = 30, all: int = 0, db: AsyncSession = Depends(get_db)):
    """People on probation. Default: overdue + ending within `within` days. HR sees
    everyone; a manager sees direct reports only."""
    org = org_of(request)
    role = role_of(request)
    if not is_hr(request) and role != "manager":
        raise HTTPException(403, "Only HR or a manager can see this list")
    if is_hr(request):
        await assign_missing(db, org)
    today = await org_today(org)
    extras = await get_extras(db, org)
    acts: dict = {}
    for a in (await db.execute(select(ProbationAction).where(ProbationAction.org_id == to_uuid(org, "org"))
                               .order_by(ProbationAction.created_at))).scalars().all():
        acts.setdefault(a.employee_id, []).append(a)
    out = []
    for e in (await _emps(db, org)).values():
        x = extras.get(str(e.id), {})
        if e.status == "terminated" or not e.is_active or not e.probation_end_date:
            continue
        if x.get("employment_status") == "confirmed":
            continue
        if not is_hr(request) and str(e.manager_id or "") != emp_of(request):
            continue
        bucket, left = L.probation_bucket(e.probation_end_date, today)
        if not all and bucket == "later" and left > within:
            continue
        mine = acts.get(e.id, [])
        rec = next((a for a in reversed(mine) if a.action == "recommend"), None)
        ext = [a for a in mine if a.action == "extend"]
        out.append({"employee_id": str(e.id), "employee": full_name(e), "employee_number": e.employee_number,
                    "job_title": e.job_title, "department": e.department, "grade": x.get("grade"),
                    "manager_name": e.manager_name, "employment_type": e.employment_type,
                    "hire_date": e.hire_date.isoformat() if e.hire_date else None,
                    "probation_end_date": e.probation_end_date.isoformat(), "days_left": left, "bucket": bucket,
                    "extended_times": len(ext),
                    "recommendation": rec.recommendation if rec else None,
                    "recommendation_by": rec.actor if rec else None,
                    "recommendation_note": rec.reason if rec else None,
                    "recommended_end_date": rec.new_end_date.isoformat() if rec and rec.new_end_date else None})
    out.sort(key=lambda r: r["days_left"])
    counts = {b: sum(1 for r in out if r["bucket"] == b) for b in ("overdue", "7", "15", "30", "later")}
    return {"today": today.isoformat(), "rows": out, "counts": counts, "settings": await _settings(db, org)}


class RecommendIn(BaseModel):
    recommendation: str                 # confirm | extend | do_not_confirm
    reason: str | None = None
    new_end_date: str | None = None


class ConfirmIn(BaseModel):
    confirmation_date: str | None = None
    reason: str | None = None
    issue_letter: bool = True


class ExtendIn(BaseModel):
    new_end_date: str
    reason: str
    issue_letter: bool = True


class NotConfirmIn(BaseModel):
    reason: str
    last_working_date: str | None = None


async def _on_probation(db, org: str, employee_id: str) -> tuple[Employee, dict]:
    e = await load_employee(db, org, employee_id)
    x = (await get_extras(db, org, employee_id)).get(str(e.id), {})
    if x.get("employment_status") == "confirmed":
        raise HTTPException(409, "This employee is already confirmed")
    if e.status == "terminated":
        raise HTTPException(409, "This employee has left")
    return e, x


async def _letter(db, request, org, e, key, data, eff: date) -> dict:
    """Issue a probation letter. A letter problem never undoes the HR action — the
    reason is returned so the screen can say the letter still needs issuing."""
    from letters_routers import LetterIn, generate
    try:
        row = await generate(db, request, org, e, LetterIn(template_key=key, data=data, effective_date=eff.isoformat(),
                                                           issue=True, source="probation"))
        return {"letter_id": str(row.id), "number": row.number, "pdf_url": f"/letters/{row.id}/pdf"}
    except HTTPException as ex:
        d = ex.detail
        return {"letter_id": None, "letter_error": d.get("message") if isinstance(d, dict) else str(d)}


@router.post("/{employee_id}/probation/recommend", status_code=201)
async def recommend(employee_id: str, body: RecommendIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e, _ = await _on_probation(db, org, employee_id)
    is_mgr = bool(emp_of(request)) and str(e.manager_id or "") == emp_of(request)
    if not (is_hr(request) or is_mgr):
        raise HTTPException(403, "Only the employee's manager or HR can recommend")
    if body.recommendation not in ("confirm", "extend", "do_not_confirm"):
        raise HTTPException(400, "Recommendation must be confirm, extend or do_not_confirm")
    if body.recommendation != "confirm" and not (body.reason or "").strip():
        raise HTTPException(400, "Give a reason")
    a = ProbationAction(org_id=e.org_id, employee_id=e.id, action="recommend", recommendation=body.recommendation,
                        old_end_date=e.probation_end_date,
                        new_end_date=parse_date(body.new_end_date, "new_end_date", required=False),
                        reason=(body.reason or "").strip() or None, actor=actor_of(request), actor_role=role_of(request))
    db.add(a)
    await db.commit()
    return {"ok": True, "recommendation": a.recommendation}


@router.post("/{employee_id}/probation/confirm")
async def confirm(employee_id: str, body: ConfirmIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e, _ = await _on_probation(db, org, employee_id)
    on = parse_date(body.confirmation_date, "confirmation_date", required=False) or (
        (e.probation_end_date + timedelta(days=1)) if e.probation_end_date else await org_today(org))
    if e.hire_date and on < e.hire_date:
        raise HTTPException(400, "The confirmation date cannot be before the joining date")
    await set_extras(db, e.id, employment_status="confirmed", confirmation_date=on)
    a = ProbationAction(org_id=e.org_id, employee_id=e.id, action="confirm", old_end_date=e.probation_end_date,
                        confirmation_date=on, reason=(body.reason or "").strip() or None,
                        actor=actor_of(request), actor_role=role_of(request))
    db.add(a)
    await db.commit()
    res = {"ok": True, "employment_status": "confirmed", "confirmation_date": on.isoformat()}
    if body.issue_letter:
        res.update(await _letter(db, request, org, e, "confirmation", {}, on))
        if res.get("letter_id"):
            a.letter_id = res["letter_id"]
            await db.commit()
    await record_audit(request, "probation.confirm", entity_type="employee", entity_id=employee_id,
                       summary=f"{full_name(e)} confirmed from {on.isoformat()}")
    return res


@router.post("/{employee_id}/probation/extend")
async def extend(employee_id: str, body: ExtendIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e, _ = await _on_probation(db, org, employee_id)
    new_end = parse_date(body.new_end_date, "new_end_date")
    if not body.reason.strip():
        raise HTTPException(400, "Give a reason for the extension")
    if e.probation_end_date and new_end <= e.probation_end_date:
        raise HTTPException(400, "The new end date must be after the current end date")
    old = e.probation_end_date
    e.probation_end_date = new_end
    await set_extras(db, e.id, employment_status="probation")
    a = ProbationAction(org_id=e.org_id, employee_id=e.id, action="extend", old_end_date=old, new_end_date=new_end,
                        reason=body.reason.strip(), actor=actor_of(request), actor_role=role_of(request))
    db.add(a)
    await db.commit()
    res = {"ok": True, "probation_end_date": new_end.isoformat()}
    if body.issue_letter:
        res.update(await _letter(db, request, org, e, "probation_extension",
                                 {"probation": {"end_date": old.isoformat() if old else None,
                                                "new_end_date": new_end.isoformat(), "reason": body.reason.strip()}},
                                 new_end))
        if res.get("letter_id"):
            a.letter_id = res["letter_id"]
            await db.commit()
    await record_audit(request, "probation.extend", entity_type="employee", entity_id=employee_id,
                       summary=f"Probation of {full_name(e)} extended to {new_end.isoformat()}")
    return res


async def start_exit_case(request: Request, e: Employee, reason: str, last_working_date: date | None) -> tuple[bool, str]:
    """HOOK → Stream C: start an exit case for a probationer who is not confirmed.
    POST {employee service}/exit/cases. If that endpoint is missing or refuses, the
    probation action is still recorded and the screen tells HR to start the exit by hand."""
    payload = {"employee_id": str(e.id), "exit_type": "termination", "reason_code": "probation_not_confirmed",
               "reason": reason, "source": "probation", "initiated_by": actor_of(request),
               "last_working_date": last_working_date.isoformat() if last_working_date else None}
    headers = {k: request.headers.get(k, "") for k in ("x-org-id", "x-user-id", "x-user-email", "x-role",
                                                       "x-employee-id", "x-branch-id") if request.headers.get(k)}
    base = os.getenv("EXIT_SERVICE_URL") or settings.EMPLOYEE_URL
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.post(f"{base}/exit/cases", json=payload, headers=headers)
        if r.status_code in (200, 201):
            body = r.json() if r.content else {}
            return True, f"Exit case started ({body.get('case_number') or body.get('id') or 'ok'})"
        return False, f"Exit case was not started (the exit service answered {r.status_code})"
    except httpx.HTTPError as ex:
        return False, f"Exit case was not started ({type(ex).__name__})"


@router.post("/{employee_id}/probation/do-not-confirm")
async def do_not_confirm(employee_id: str, body: NotConfirmIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e, _ = await _on_probation(db, org, employee_id)
    if not body.reason.strip():
        raise HTTPException(400, "Give a reason")
    lwd = parse_date(body.last_working_date, "last_working_date", required=False)
    a = ProbationAction(org_id=e.org_id, employee_id=e.id, action="do_not_confirm", old_end_date=e.probation_end_date,
                        reason=body.reason.strip(), actor=actor_of(request), actor_role=role_of(request))
    db.add(a)
    await db.commit()
    ok, msg = await start_exit_case(request, e, body.reason.strip(), lwd)
    a.exit_handover = msg[:200]
    await db.commit()
    await record_audit(request, "probation.do_not_confirm", entity_type="employee", entity_id=employee_id,
                       summary=f"{full_name(e)} not confirmed", meta={"exit_started": ok})
    return {"ok": True, "exit_started": ok, "message": msg if ok else msg + ". Start the exit from the Exit screen."}


# ── per-employee routes ───────────────────────────────────────────────────────
class ChangeIn(BaseModel):
    change_type: str
    effective_date: str
    new_values: dict
    reason: str | None = None
    note: str | None = None
    linked_revision_id: str | None = None
    submit: bool = False


@router.post("/{employee_id}/career-changes", status_code=201)
async def create_change(employee_id: str, body: ChangeIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    eff = parse_date(body.effective_date, "effective_date")
    cur = _current(e, (await get_extras(db, org, employee_id)).get(str(e.id), {}))
    nv, errs = L.clean_new_values(body.change_type, body.new_values, cur)
    if errs:
        raise HTTPException(422, " ".join(errs))
    if "manager_id" in nv:
        if nv["manager_id"]:
            if nv["manager_id"] == str(e.id):
                raise HTTPException(422, "An employee cannot be their own manager")
            m = await load_employee(db, org, nv["manager_id"])
            nv["manager_name"] = full_name(m)
        else:
            nv["manager_name"] = None
    c = CareerChange(org_id=e.org_id, employee_id=e.id, change_type=body.change_type, effective_date=eff,
                     old_values=L.capture_old(cur, nv), new_values=nv, reason=(body.reason or "").strip()[:200] or None,
                     note=body.note, linked_revision_id=body.linked_revision_id,
                     status="submitted" if body.submit else "draft", submitted_at=now() if body.submit else None,
                     created_by=actor_of(request), created_by_id=uid_of(request) or None)
    db.add(c)
    await db.commit()
    await record_audit(request, "career.change.create", entity_type="career_change", entity_id=str(c.id),
                       summary=f"{L.TYPES[c.change_type][0]} for {full_name(e)} from {eff.isoformat()}",
                       meta={"new": nv})
    return change_out(c, e)


@router.get("/{employee_id}/career-changes")
async def employee_changes(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    hr = is_hr(request)
    if not hr and not is_self(request, e):
        raise HTTPException(403, "You can only see your own history")
    if hr:
        await record_direct_edits(db, org)
        await apply_due(db, org)
    q = select(CareerChange).where(CareerChange.org_id == e.org_id, CareerChange.employee_id == e.id)
    if not hr:
        q = q.where(CareerChange.status == "applied")
    rows = (await db.execute(q.order_by(CareerChange.effective_date.desc(), CareerChange.created_at.desc()))).scalars().all()
    return [change_out(c, e) for c in rows]


@router.get("/{employee_id}/career")
async def employee_career(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    if not is_hr(request) and not is_self(request, e):
        raise HTTPException(403, "You can only see your own record")
    x = (await get_extras(db, org, employee_id)).get(str(e.id), {})
    acts = (await db.execute(select(ProbationAction).where(ProbationAction.employee_id == e.id,
                                                           ProbationAction.org_id == e.org_id)
                             .order_by(ProbationAction.created_at.desc()))).scalars().all()
    return {"employee_id": str(e.id), **_current(e, x), "employment_status": x.get("employment_status"),
            "confirmation_date": x.get("confirmation_date"),
            "probation_end_date": e.probation_end_date.isoformat() if e.probation_end_date else None,
            "probation_history": [{"action": a.action, "recommendation": a.recommendation,
                                   "old_end_date": a.old_end_date.isoformat() if a.old_end_date else None,
                                   "new_end_date": a.new_end_date.isoformat() if a.new_end_date else None,
                                   "confirmation_date": a.confirmation_date.isoformat() if a.confirmation_date else None,
                                   "reason": a.reason, "actor": a.actor, "exit_handover": a.exit_handover,
                                   "at": a.created_at.isoformat() if a.created_at else None}
                                  for a in acts if hr_or_public(is_hr(request), a)]}


def hr_or_public(hr: bool, a: ProbationAction) -> bool:
    """An employee sees confirm / extend on their own record, not the manager's notes."""
    return hr or a.action in ("confirm", "extend")


# ── boot + tick ───────────────────────────────────────────────────────────────
async def lifecycle_init() -> dict:
    """Run once at service start (after init_db): add this stream's columns, back-fill
    the opening salary rows, apply due career changes, start the daily tick."""
    from comp_routers import backfill
    summary: dict = {}
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '15s'"))   # boot DDL never waits forever on a lock
        await ensure_columns(conn)
    # Each step ends its transaction (commit) so no read lock on employees / career_changes is held
    # while a later boot step (or another service's boot) runs ALTER TABLE — the idle-in-transaction
    # pattern that once deadlocked the migration bootstrap.
    async with SessionLocal() as db:
        summary["opening_salary_rows"] = await backfill(db)
        await db.commit()
        orgs = {r[0] for r in (await db.execute(select(Employee.org_id).distinct())).all()}
        await db.commit()
        summary["direct_edits_recorded"] = sum([await record_direct_edits(db, str(o)) for o in orgs])
        await db.commit()
        summary["career_applied"] = await apply_due(db)
        await db.commit()
    if os.getenv("LEXORA_LIFECYCLE_TICK", "1").strip().lower() not in ("0", "false", "no", "off"):
        asyncio.create_task(_tick_loop())
        summary["tick"] = "on"
    print(f"[employee] lifecycle init: {summary}", flush=True)
    return summary


async def lifecycle_tick() -> dict:
    """What the daily tick does. Safe to run any number of times."""
    from comp_routers import backfill
    out = {"career_applied": 0, "probation_dates_set": 0, "opening_salary_rows": 0}
    async with SessionLocal() as db:
        out["opening_salary_rows"] = await backfill(db)
        orgs = {r[0] for r in (await db.execute(select(Employee.org_id).distinct())).all()}
        out["direct_edits_recorded"] = sum([await record_direct_edits(db, str(o)) for o in orgs])
        out["career_applied"] = await apply_due(db)
        for org in orgs:
            out["probation_dates_set"] += await assign_missing(db, str(org))
        # 2026-10-22: fixed-term contract expiry alerts (60 / 30 / 7 / 0 days, once each)
        try:
            from contracts_routers import scan_org
            out["contract_alerts"] = sum([await scan_org(db, str(o)) for o in orgs])
        except Exception as ex:  # noqa: BLE001
            out["contract_alerts_error"] = type(ex).__name__
    return out


async def _tick_loop():
    hours = float(os.getenv("LEXORA_LIFECYCLE_TICK_HOURS", "6"))
    while True:
        await asyncio.sleep(max(hours, 0.1) * 3600)
        try:
            print(f"[employee] lifecycle tick: {await lifecycle_tick()}", flush=True)
        except Exception as ex:  # noqa: BLE001 — the tick must never take the service down
            print(f"[employee] lifecycle tick failed: {type(ex).__name__}: {ex}", flush=True)
