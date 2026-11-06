"""Exit process, part 2 — /exit/*  (Stream C)

  * LAST WORKING DAY: separate_case() + the daily tick + POST /exit/lwd-tick (on demand)
  * FORMER EMPLOYEES + REHIRE (a new employment spell on the same person record)
  * ATTRITION summary from real cases
  * SETTINGS: notice defaults, notice policy table, interview + clearance templates
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402

import exit_logic as L  # noqa: E402
import exit_common as C  # noqa: E402
from database import SessionLocal, get_db  # noqa: E402
from exit_models import ExitCase, ExitNoticePolicy, ExitSettings, EmploymentSpell  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/exit", tags=["exit"])

SYSTEM_ACTOR = {"email": "system (last-working-day tick)", "role": "system", "employee_id": "", "user_id": ""}


# ── employment spells ─────────────────────────────────────────────────────────
async def _spells(db, org, employee_id) -> list[EmploymentSpell]:
    return list((await db.execute(select(EmploymentSpell).where(
        EmploymentSpell.org_id == org, EmploymentSpell.employee_id == employee_id)
        .order_by(EmploymentSpell.spell_no).execution_options(populate_existing=True))).scalars().all())


def _spell_out(s: EmploymentSpell) -> dict:
    return {"spell_no": s.spell_no, "employee_number": s.employee_number, "hire_date": C.iso(s.hire_date),
            "exit_date": C.iso(s.exit_date), "exit_type": s.exit_type,
            "exit_type_label": L.EXIT_TYPES.get(s.exit_type or "", s.exit_type), "exit_reason": s.exit_reason,
            "rehire_eligible": s.rehire_eligible, "rehire_note": s.rehire_note,
            "case_id": str(s.case_id) if s.case_id else None}


async def _current_spell(db, emp: Employee) -> EmploymentSpell:
    """The open spell of this employee (created on first need: older records have none)."""
    rows = await _spells(db, emp.org_id, emp.id)
    cur = next((s for s in reversed(rows) if s.exit_date is None), None)
    if cur:
        return cur
    last_no = rows[-1].spell_no if rows else 0
    # an employee with only CLOSED spells and still terminated: return the last closed one
    if rows and emp.status == "terminated":
        return rows[-1]
    cur = EmploymentSpell(org_id=emp.org_id, employee_id=emp.id, spell_no=last_no + 1,
                          employee_number=emp.employee_number, hire_date=emp.hire_date)
    db.add(cur)
    await db.flush()
    return cur


async def sync_spell_from_case(db, case: ExitCase) -> None:
    """Keep the spell's exit date / reason / rehire flag in step with the case."""
    emp = (await db.execute(select(Employee).where(Employee.id == case.employee_id, Employee.org_id == case.org_id)
                            .execution_options(populate_existing=True))).scalar_one_or_none()
    if not emp:
        return
    sp = await _current_spell(db, emp)
    if case.separated_at and case.lwd:
        sp.exit_date = case.lwd
        sp.exit_type = case.exit_type
        sp.exit_reason = (case.reason_category or case.reason or "")[:400] or None
        sp.case_id = case.id
    if sp.case_id == case.id or (case.separated_at and case.lwd):
        sp.rehire_eligible = case.rehire_eligible
        sp.rehire_note = case.rehire_note


# ── LAST WORKING DAY ──────────────────────────────────────────────────────────
async def _close_future_leave(headers: dict, employee_id: str, lwd: date) -> dict:
    """Cancel pending / approved leave that starts after the last working day (best effort)."""
    out = {"cancelled": 0, "ok": True}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.LEAVE_URL}/leave/requests", headers=headers,
                            params={"employee_id": employee_id, "status": "pending,approved", "scope": "org"})
            if r.status_code != 200:
                return {"cancelled": 0, "ok": False, "message": "Leave service did not answer – cancel future leave by hand."}
            for row in r.json() or []:
                if (L.to_date(row.get("start_date")) or lwd) > lwd:
                    x = await c.post(f"{settings.LEAVE_URL}/leave/requests/{row['id']}/cancel", headers=headers)
                    out["cancelled"] += 1 if x.status_code == 200 else 0
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"cancelled": out["cancelled"], "ok": False, "message": "Leave service could not be reached – cancel future leave by hand."}
    return out


async def _close_future_shifts(headers: dict, employee_id: str, lwd: date) -> dict:
    """Remove rostered shifts after the last working day (best effort; next 120 days)."""
    out = {"removed": 0, "ok": True}
    try:
        from datetime import timedelta
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.TIME_URL}/time/roster", headers=headers,
                            params={"start": (lwd + timedelta(days=1)).isoformat(), "end": (lwd + timedelta(days=120)).isoformat()})
            if r.status_code != 200:
                return {"removed": 0, "ok": False, "message": "Roster not available – remove future shifts by hand."}
            data = r.json() or {}
            shifts = data.get("shifts") if isinstance(data, dict) else data
            for s in shifts or []:
                if str(s.get("employee_id")) == str(employee_id) and s.get("id"):
                    x = await c.delete(f"{settings.TIME_URL}/time/roster/{s['id']}", headers=headers)
                    out["removed"] += 1 if x.status_code == 200 else 0
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {"removed": out["removed"], "ok": False, "message": "Roster could not be reached – remove future shifts by hand."}
    return out


async def separate_case(db: AsyncSession, case: ExitCase, headers: dict, actor: dict | None = None,
                        *, force: bool = False) -> dict:
    """Apply the last working day. IDEMPOTENT: the case is claimed with
    `UPDATE … WHERE separated_at IS NULL`; a second call (or a second tick) changes nothing.
    `force=True` re-runs the follow-up steps (each is itself idempotent) after a partial failure.

    1. employee.status → terminated with the REAL exit date (the case's last working day)
    2. the employment spell gets its exit date / reason
    3. logins are switched off (Stream A helper)
    4. future leave is cancelled, future shifts removed (best effort)
    5. direct reports are flagged for reassignment
    """
    actor = actor or SYSTEM_ACTOR
    org = str(case.org_id)
    today = C.today()
    if not L.separation_due(C.case_dict(case), today) and not (force and case.separated_at):
        return {"done": False, "reason": "Not due"}
    if not case.separated_at:
        res = await db.execute(update(ExitCase).where(ExitCase.id == case.id, ExitCase.separated_at.is_(None),
                                                      ExitCase.status.in_(L.AFTER_ACCEPTANCE))
                               .values(separated_at=C.now()))
        if res.rowcount != 1:
            await db.rollback()
            return {"done": False, "reason": "Already separated"}
    lwd = case.lwd
    # 1. the employee record: the REAL exit date, not today
    await db.execute(update(Employee).where(Employee.id == case.employee_id, Employee.org_id == case.org_id)
                     .values(status="terminated", termination_date=lwd))
    await db.flush()
    case = await C.load_case(db, org, str(case.id))
    # 2. spell
    await sync_spell_from_case(db, case)
    # 5. direct reports (read before commit so the list is on the case)
    reports = (await db.execute(select(Employee).where(
        Employee.org_id == case.org_id, Employee.manager_id == case.employee_id, Employee.status != "terminated",
        Employee.is_active.is_(True)))).scalars().all()
    report_list = [{"id": str(r.id), "name": f"{r.first_name} {r.last_name}".strip(), "employee_number": r.employee_number}
                   for r in reports]
    await db.commit()
    # 3. logins (after the commit: A's helper opens its own session)
    logins = await C.disable_logins(org, str(case.employee_id), f"Left the company on {lwd.isoformat()} (exit case {case.case_number})")
    # 4. future leave + shifts
    leave = await _close_future_leave(headers, str(case.employee_id), lwd)
    shifts = await _close_future_shifts(headers, str(case.employee_id), lwd)
    result = {"done": True, "exit_date": lwd.isoformat(), "logins": logins, "leave": leave, "shifts": shifts,
              "direct_reports": report_list, "applied_at": C.now().isoformat(), "applied_by": actor.get("email")}
    await db.execute(update(ExitCase).where(ExitCase.id == case.id).values(separation_result=result))
    C.add_event(db, case, actor, "separated",
                note=f"Last working day {lwd.isoformat()}: employee marked as left. "
                     f"Logins switched off: {logins.get('disabled', 0) if logins.get('ok') else 'NOT done'}. "
                     f"Future leave cancelled: {leave.get('cancelled', 0)}. Shifts removed: {shifts.get('removed', 0)}. "
                     f"Direct reports to reassign: {len(report_list)}.")
    await db.commit()
    try:                                                   # G3: outbound webhooks (best-effort)
        from shared.utils.events import emit as _g3_emit
        await _g3_emit(org, "employee.exited", {"employee_id": str(case.employee_id), "exit_case": case.case_number,
                                                "last_working_day": lwd.isoformat()})
    except Exception:  # noqa: BLE001
        pass
    if report_list:
        await C.notify(org, roles=["hr_admin", "org_admin"],
                       title=f"{len(report_list)} direct report(s) of {case.employee_name} need a new manager",
                       body=", ".join(r["name"] for r in report_list[:10]), link=f"/exits/{case.id}")
    return result


async def run_tick(db: AsyncSession, org: str | None = None, headers_for=None) -> dict:
    """Separate every accepted case whose last working day has arrived. Safe to run any
    number of times a day."""
    q = select(ExitCase).where(ExitCase.separated_at.is_(None), ExitCase.lwd.is_not(None), ExitCase.lwd <= C.today(),
                               ExitCase.status.in_(L.AFTER_ACCEPTANCE))
    if org:
        q = q.where(ExitCase.org_id == org)
    cases = list((await db.execute(q)).scalars().all())
    done, skipped = [], 0
    for c in cases:
        hdr = headers_for(c) if headers_for else C.system_headers(str(c.org_id))
        try:
            r = await separate_case(db, c, hdr)
        except Exception as ex:  # noqa: BLE001 — one bad case must not stop the rest
            await db.rollback()
            r = {"done": False, "reason": f"{type(ex).__name__}"}
        if r.get("done"):
            done.append({"case_number": c.case_number, "employee": c.employee_name, "exit_date": r.get("exit_date")})
        else:
            skipped += 1
    return {"checked": len(cases), "separated": done, "skipped": skipped}


async def _tick_loop():
    every = int(os.getenv("EXIT_TICK_SECONDS", "3600"))
    await asyncio.sleep(20)
    while True:
        try:
            async with SessionLocal() as db:
                r = await run_tick(db)
                if r["separated"]:
                    print(f"[employee] exit tick: {r}", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"[employee] exit tick failed: {type(ex).__name__}: {ex}", flush=True)
        await asyncio.sleep(max(every, 60))


_TICK_TASK = None


async def exit_init(engine) -> None:
    """Called from the employee service lifespan: exit tables + the daily tick."""
    global _TICK_TASK
    from exit_models import init_exit_schema
    await init_exit_schema(engine)
    if _TICK_TASK is None and os.getenv("EXIT_TICK_DISABLED", "").lower() not in ("1", "true", "yes"):
        _TICK_TASK = asyncio.create_task(_tick_loop())


class TickBody(BaseModel):
    case_id: str | None = None
    force: bool = False


@router.post("/lwd-tick")
async def lwd_tick(request: Request, body: TickBody = TickBody(), db: AsyncSession = Depends(get_db)):
    """On demand: apply the last working day now (the whole org, or one case; `force`
    re-runs the follow-up steps of an already separated case)."""
    org = C.org_of(request)
    actor = C.actor_of(request)
    C.require_hr(actor, "run the last-working-day update")
    hdr = C.fwd_headers(request)
    if body.case_id:
        case = await C.load_case(db, org, body.case_id)
        return {"checked": 1, "result": await separate_case(db, case, hdr, actor, force=body.force)}
    return await run_tick(db, org, headers_for=lambda c: hdr)


# ── FORMER EMPLOYEES + REHIRE ─────────────────────────────────────────────────
@router.get("/former-employees")
async def former_employees(request: Request, q: str | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    if not (C.is_hr(actor) or C.is_payroll(actor)):
        raise HTTPException(403, "Only HR or payroll can see former employees")
    emps = (await db.execute(select(Employee).where(Employee.org_id == org, Employee.status == "terminated",
                                                    Employee.is_active.is_(True)))).scalars().all()
    spells = (await db.execute(select(EmploymentSpell).where(EmploymentSpell.org_id == org)
                               .order_by(EmploymentSpell.spell_no))).scalars().all()
    by_emp: dict = {}
    for s in spells:
        by_emp.setdefault(str(s.employee_id), []).append(s)
    st = C.settings_out(await C.get_settings_row(db, org))
    ql = (q or "").strip().lower()
    out = []
    for e in emps:
        sp = by_emp.get(str(e.id), [])
        last = sp[-1] if sp else None
        name = f"{e.first_name} {e.last_name}".strip()
        if ql and ql not in f"{name} {e.employee_number} {e.department or ''} {e.job_title or ''}".lower():
            continue
        out.append({"id": str(e.id), "name": name, "employee_number": e.employee_number, "label": "Former employee",
                    "department": e.department, "job_title": e.job_title, "country": (e.address_country or "CA").upper(),
                    "hire_date": C.iso(e.hire_date), "exit_date": C.iso(e.termination_date),
                    "exit_type": last.exit_type if last else None,
                    "exit_type_label": L.EXIT_TYPES.get(last.exit_type, last.exit_type) if last and last.exit_type else None,
                    "exit_reason": last.exit_reason if last else None,
                    "rehire_eligible": last.rehire_eligible if last else None,
                    "rehire_note": last.rehire_note if last else None,
                    "case_id": str(last.case_id) if last and last.case_id else None,
                    "spells": [_spell_out(s) for s in sp], "spell_count": max(len(sp), 1)})
    out.sort(key=lambda r: r["exit_date"] or "", reverse=True)
    return {"former_employees": out, "rehire_number_policy": st["rehire_number_policy"], "can_rehire": C.is_hr(actor)}


class FlagBody(BaseModel):
    eligible: bool | None = None
    note: str | None = None


@router.patch("/former-employees/{employee_id}/rehire-flag")
async def former_rehire_flag(employee_id: str, body: FlagBody, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    C.require_hr(actor, "set the rehire flag")
    emp = await C.load_employee(db, org, employee_id)
    if emp.status != "terminated":
        raise HTTPException(409, "This person is still employed")
    sp = await _current_spell(db, emp)
    if sp.exit_date is None:
        sp.exit_date = emp.termination_date
    sp.rehire_eligible = body.eligible
    sp.rehire_note = (body.note or None)
    await db.commit()
    return {"ok": True, "spell": _spell_out(sp)}


class RehireBody(BaseModel):
    employee_id: str
    new_hire_date: str
    new_employee_number: str | None = None
    note: str | None = None
    job_title: str | None = None
    department: str | None = None
    annual_salary: float | None = None
    # 2026-10-24: terms of the new spell (see exit_logic.rehire_terms)
    employment_type: str | None = None
    work_location: str | None = None
    grade: str | None = None
    province_of_employment: str | None = None
    probation_end_date: str | None = None
    manager_id: str | None = None


@router.post("/rehire", status_code=201)
async def rehire(body: RehireBody, request: Request, db: AsyncSession = Depends(get_db)):
    """A NEW employment spell for a former employee, on the SAME person record: the
    SIN and the year-to-date payroll ledger (keyed by the person and the tax year) carry
    over by themselves — no previous-employer entry is invented. Old history is kept; the
    previous spell stays on file and is linked."""
    org = C.org_of(request)
    actor = C.actor_of(request)
    C.require_hr(actor, "rehire a former employee")
    emp = await C.load_employee(db, org, body.employee_id)
    st = C.settings_out(await C.get_settings_row(db, org))
    rows = await _spells(db, emp.org_id, emp.id)
    if not rows:                                      # a leaver from before the exit module: record that spell now
        rows = [await _current_spell(db, emp)]
    eb = C.emp_brief(emp)
    plan, err = L.rehire_plan({**eb, "termination_date": emp.termination_date},
                              [{"spell_no": s.spell_no, "rehire_eligible": s.rehire_eligible} for s in rows],
                              body.new_hire_date, st["rehire_number_policy"], body.new_employee_number)
    if err:
        raise HTTPException(409 if "eligible" in err or "former" in err else 422, err)
    from shared.utils.enums import EMPLOYMENT_TYPES, REGIONS_ALL
    terms, terr = L.rehire_terms(body.model_dump(), body.new_hire_date, EMPLOYMENT_TYPES, REGIONS_ALL)
    if terr:
        raise HTTPException(422, terr)
    if terms.get("manager_id"):
        mgr = (await db.execute(select(Employee).where(Employee.org_id == org, Employee.id == terms["manager_id"],
                                                       Employee.id != emp.id))).scalar_one_or_none()
        if not mgr:
            raise HTTPException(422, "Choose the reporting manager from the list")
        terms["manager_name"] = f"{mgr.first_name} {mgr.last_name}".strip()
    number = plan["new_spell"]["employee_number"]
    if number != emp.employee_number:
        clash = (await db.execute(select(Employee.id).where(Employee.org_id == org, Employee.employee_number == number,
                                                            Employee.id != emp.id))).first()
        if clash:
            raise HTTPException(409, f"Employee number {number} is already in use")
    open_case = (await db.execute(select(ExitCase.id).where(ExitCase.org_id == org, ExitCase.employee_id == emp.id,
                                                            ExitCase.status.in_(L.OPEN_STATES)))).first()
    if open_case:
        raise HTTPException(409, "Close the open exit case of this person first")
    last = rows[-1]
    if last.exit_date is None:
        last.exit_date = emp.termination_date
    if not last.employee_number:
        last.employee_number = emp.employee_number
    # WHERE status = 'terminated': two HR users cannot both rehire the same person
    patch = {**plan["employee_patch"], **terms}
    res = await db.execute(update(Employee).where(Employee.id == emp.id, Employee.org_id == emp.org_id,
                                                  Employee.status == "terminated").values(**patch))
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Someone else just changed this record. Refresh and try again.")
    db.add(EmploymentSpell(org_id=emp.org_id, employee_id=emp.id, spell_no=plan["new_spell"]["spell_no"],
                           employee_number=number, hire_date=plan["new_spell"]["hire_date"]))
    await db.commit()
    await record_audit(request, "exit.rehire", entity_type="employee", entity_id=str(emp.id),
                       summary=f"Rehired {eb['name']} from {plan['new_spell']['hire_date'].isoformat()} (spell {plan['new_spell']['spell_no']})",
                       meta={"employee_number": number, "previous_exit": C.iso(emp.termination_date), "note": body.note})
    # the login stays OFF: HR re-invites the person from Users (Stream A owns logins)
    return {"ok": True, "employee_id": str(emp.id), "employee_number": number, "spell_no": plan["new_spell"]["spell_no"],
            "hire_date": plan["new_spell"]["hire_date"].isoformat(),
            "spells": [_spell_out(s) for s in await _spells(db, emp.org_id, emp.id)],
            "next_steps": ["Switch the login back on (Users) – it was turned off at exit.",
                           "Check salary, manager and work location on the employee record.",
                           "Payroll: year-to-date figures of the earlier spell in this tax year stay on the same record."]}


# ── ATTRITION ─────────────────────────────────────────────────────────────────
@router.get("/attrition")
async def attrition(request: Request, year: int | None = None, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    if not (C.is_hr(actor) or C.is_payroll(actor)):
        raise HTTPException(403, "Only HR can see the attrition summary")
    rows = (await db.execute(select(ExitCase).where(ExitCase.org_id == org))).scalars().all()
    cases = [C.case_dict(r) for r in rows if not year or (r.lwd and r.lwd.year == year) or (not r.lwd and r.submitted_on.year == year)]
    head = (await db.execute(select(Employee.id).where(Employee.org_id == org, Employee.is_active.is_(True),
                                                       Employee.status.in_(("active", "on_leave"))))).all()
    out = L.attrition_summary(cases)
    out["year"] = year
    out["current_headcount"] = len(head)
    return out


# ── SETTINGS + NOTICE POLICY TABLE ────────────────────────────────────────────
class SettingsBody(BaseModel):
    default_notice_days: int | None = None
    probation_notice_days: int | None = None
    leave_extends_lwd: bool | None = None
    rehire_number_policy: str | None = None
    hold_letters_until_settled: bool | None = None
    interview_template: list[dict] | None = None
    clearance_template: list[dict] | None = None


@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    return {**C.settings_out(await C.get_settings_row(db, org)), "notice_policies": await C.load_policies(db, org),
            "note": "Notice days are your company policy, not a legal minimum."}


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    C.require_hr(actor, "change the exit settings")
    s = await C.get_settings_row(db, org, create=True)
    sent = body.model_dump(exclude_unset=True)
    for k in ("default_notice_days", "probation_notice_days"):
        if sent.get(k) is not None:
            if not 0 <= int(sent[k]) <= 365:
                raise HTTPException(422, "Notice days must be between 0 and 365")
            setattr(s, k, int(sent[k]))
    if sent.get("rehire_number_policy") is not None:
        if sent["rehire_number_policy"] not in ("keep", "new"):
            raise HTTPException(422, "rehire_number_policy must be keep or new")
        s.rehire_number_policy = sent["rehire_number_policy"]
    for k in ("leave_extends_lwd", "hold_letters_until_settled"):
        if sent.get(k) is not None:
            setattr(s, k, bool(sent[k]))
    if "interview_template" in sent and sent["interview_template"] is not None:
        s.interview_template = [{"key": (q.get("key") or f"q{i + 1}"), "question": str(q.get("question") or "").strip(),
                                 "type": q.get("type") or "text"}
                                for i, q in enumerate(sent["interview_template"]) if str(q.get("question") or "").strip()]
    if "clearance_template" in sent and sent["clearance_template"] is not None:
        s.clearance_template = [{"department": str(t.get("department") or "Other").strip(), "item": str(t.get("item") or "").strip(),
                                 "owner_role": (t.get("owner_role") or "hr_admin"), "owner_user_id": t.get("owner_user_id") or None,
                                 "owner_name": t.get("owner_name") or None}
                                for t in sent["clearance_template"] if str(t.get("item") or "").strip()]
    s.updated_by = actor["email"]
    await db.commit()
    await record_audit(request, "exit.settings.update", entity_type="exit_settings", entity_id=org,
                       summary="Exit settings updated", meta={"fields": list(sent)})
    return {**C.settings_out(await C.get_settings_row(db, org)), "notice_policies": await C.load_policies(db, org)}


class PolicyBody(BaseModel):
    grade: str | None = None
    employment_type: str | None = None
    stage: str = "any"
    notice_days: int = 30
    active: bool = True


@router.post("/notice-policies", status_code=201)
async def add_policy(body: PolicyBody, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    C.require_hr(C.actor_of(request), "change the notice policy")
    if body.stage not in ("any", "probation", "confirmed"):
        raise HTTPException(422, "stage must be any, probation or confirmed")
    if not 0 <= body.notice_days <= 365:
        raise HTTPException(422, "Notice days must be between 0 and 365")
    db.add(ExitNoticePolicy(org_id=uuid.UUID(org), grade=(body.grade or "").strip() or None,
                            employment_type=(body.employment_type or "").strip() or None, stage=body.stage,
                            notice_days=body.notice_days, active=body.active))
    await db.commit()
    return await C.load_policies(db, org)


@router.delete("/notice-policies/{policy_id}")
async def delete_policy(policy_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    C.require_hr(C.actor_of(request), "change the notice policy")
    p = (await db.execute(select(ExitNoticePolicy).where(ExitNoticePolicy.id == policy_id, ExitNoticePolicy.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Policy row not found")
    await db.delete(p)
    await db.commit()
    return await C.load_policies(db, org)


@router.get("/notice-preview")
async def notice_preview(employee_id: str, request: Request, on: str | None = None, exit_type: str = "resignation",
                         db: AsyncSession = Depends(get_db)):
    """What notice applies to this employee if the exit is accepted on `on` (default today)."""
    org = C.org_of(request)
    actor = C.actor_of(request)
    if not (C.is_hr(actor) or actor["employee_id"] == employee_id or actor["role"] == "manager"):
        raise HTTPException(403, "Not allowed")
    emp = await C.load_employee(db, org, employee_id)
    d = C.parse_date(on, "on") or C.today()
    st = C.settings_out(await C.get_settings_row(db, org))
    n = L.notice_days_for(C.emp_brief(emp), await C.load_policies(db, org), st, d)
    return {**n, "accepted_on": d.isoformat(), "expected_lwd": L.expected_lwd(d, n["days"]).isoformat(),
            "rule": "Notice starts on the acceptance date, which counts as day 1."}
