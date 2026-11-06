"""Fixed-term / contract employees — API (employee service). 2026-10-22.

  GET  /employees/fixed-term?within=90&all=0        term-bound employees with end date, bucket, recommendation
                                                     (HR: whole org; manager: own direct reports; others 403)
  POST /employees/fixed-term/alerts/scan            notify HR + the manager at 60 / 30 / 7 / 0 days left (once each)
  POST /employees/{id}/fixed-term                   set / correct the current term {start_date, end_date, notice_days?,
                                                     contract_ref?, employment_type?}                       (HR)
  POST /employees/{id}/fixed-term/recommend         {recommendation renew|convert|end, reason, new_end_date?}
                                                                                    (the employee's manager or HR)
  POST /employees/{id}/fixed-term/renew             {new_end_date, reason, notice_days?, contract_ref?, issue_letter} (HR)
  POST /employees/{id}/fixed-term/convert           {effective_date, reason, issue_letter, probation_months?}   (HR)
                                                     → employment_type full_time, contract dates cleared
  POST /employees/{id}/fixed-term/end               {reason, last_working_date?} → exit case end_of_contract     (HR)
  GET  /employees/{id}/fixed-term/history           HR · the manager · the employee

HR here = org_admin / hr_admin / hr_manager (decision 2026-10-21: people actions are HR's, not the pay office's).
These static paths are registered BEFORE routers.router so /employees/{employee_id} does not swallow them.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import contracts_logic as L  # noqa: E402
from career_common import (actor_of, emp_of, full_name, load_employee, org_of, parse_date, role_of,  # noqa: E402
                           to_uuid, is_self)
from career_common import is_people_hr as is_hr, require_people_hr as require_hr  # noqa: E402
from contracts_models import ContractTerm  # noqa: E402
from database import get_db  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/employees", tags=["fixed-term"])

RULE_NOTE = ("Fixed-term employment (Canada): the employment standards act of the province of employment applies. "
             "A fixed-term contract that ends on its stated date generally needs no notice, but repeated renewals can turn it into "
             "indefinite employment, and an early termination may trigger notice / severance or damages for the rest of the term — "
             "check the contract wording and your province's rules with your lawyer.")


def _iso(d):
    return d.isoformat() if d else None


async def _history(db, org: str, employee_id) -> list[ContractTerm]:
    return (await db.execute(select(ContractTerm).where(ContractTerm.org_id == to_uuid(org, "org"),
                                                        ContractTerm.employee_id == employee_id)
                             .order_by(ContractTerm.created_at))).scalars().all()


def _term_row(t: ContractTerm) -> dict:
    return {"id": str(t.id), "action": t.action, "term_no": t.term_no, "start_date": _iso(t.start_date),
            "end_date": _iso(t.end_date), "old_end_date": _iso(t.old_end_date), "notice_days": t.notice_days,
            "contract_ref": t.contract_ref, "recommendation": t.recommendation, "alert_days": t.alert_days,
            "effective_date": _iso(t.effective_date), "reason": t.reason, "actor": t.actor, "actor_role": t.actor_role,
            "letter_id": t.letter_id, "exit_handover": t.exit_handover, "at": t.created_at.isoformat() if t.created_at else None}


def _start_row(e: Employee) -> ContractTerm:
    return ContractTerm(org_id=e.org_id, employee_id=e.id, action="start", term_no=1, start_date=e.contract_start_date or e.hire_date,
                        end_date=e.contract_end_date, notice_days=e.contract_notice_days, contract_ref=e.contract_ref,
                        reason="Term entered on the employee record", actor="employee record", actor_role="hr",
                        created_at=e.created_at if getattr(e, "created_at", None) else None)


def _is_mgr(request: Request, e: Employee) -> bool:
    return bool(emp_of(request)) and str(e.manager_id or "") == emp_of(request)


def _row(e: Employee, today: date, hist: list[ContractTerm]) -> dict:
    b, left = L.bucket(e.contract_end_date, today)
    terms = [t for t in hist if t.action in ("start", "correct", "renew")]
    rec = next((t for t in reversed(hist) if t.action == "recommend"), None)
    last_act = next((t for t in reversed(hist) if t.action in ("renew", "convert", "end")), None)
    nd = L.notice_date(e.contract_end_date, e.contract_notice_days)
    first_start = min([t.start_date for t in hist if t.start_date] + [d for d in (e.hire_date, e.contract_start_date) if d], default=None)
    service = L.tenure_days(first_start, e.contract_end_date)
    return {"employee_id": str(e.id), "employee": full_name(e), "employee_number": e.employee_number,
            "job_title": e.job_title, "department": e.department, "work_location": e.work_location,
            "manager_id": str(e.manager_id) if e.manager_id else None, "manager_name": e.manager_name,
            "employment_type": e.employment_type, "status": e.status,
            "hire_date": _iso(e.hire_date), "contract_start_date": _iso(e.contract_start_date),
            "contract_end_date": _iso(e.contract_end_date), "contract_notice_days": e.contract_notice_days,
            "contract_ref": e.contract_ref, "notice_by": _iso(nd),
            "notice_overdue": bool(nd and nd < today and b != "expired"),
            "days_left": left, "bucket": b, "term_no": max([t.term_no or 1 for t in terms], default=1),
            "renewals": sum(1 for t in hist if t.action == "renew"),
            "recommendation": rec.recommendation if rec else None, "recommendation_by": rec.actor if rec else None,
            "recommendation_note": rec.reason if rec else None,
            "recommended_end_date": _iso(rec.end_date) if rec else None,
            "last_action": last_act.action if last_act else None, "exit_handover": last_act.exit_handover if last_act else None,
            "service_days_at_end": service}


@router.get("/fixed-term")
async def list_fixed_term(request: Request, within: int = 3650, all: int = 1, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    hr = is_hr(request)
    if not hr and role_of(request) != "manager":
        raise HTTPException(403, "Only HR or a manager can see fixed-term contracts")
    today = await org_today(org)
    emps = (await db.execute(select(Employee).where(Employee.org_id == to_uuid(org, "org"), Employee.is_active == True))).scalars().all()  # noqa: E712
    rows = []
    hist_all = (await db.execute(select(ContractTerm).where(ContractTerm.org_id == to_uuid(org, "org"))
                                 .order_by(ContractTerm.created_at))).scalars().all()
    by_emp: dict = {}
    for t in hist_all:
        by_emp.setdefault(t.employee_id, []).append(t)
    added = False
    for e in emps:
        if e.status == "terminated" or not L.is_term_bound(e.employment_type):
            continue
        if not hr and str(e.manager_id or "") != emp_of(request):
            continue
        if e.contract_end_date and not any(t.action in ("start", "correct", "renew") for t in by_emp.get(e.id, [])):
            # term entered on the employee form (create / edit) → record it as the first term once
            t = _start_row(e)
            db.add(t)
            by_emp.setdefault(e.id, []).insert(0, t)
            added = True
        r = _row(e, today, by_emp.get(e.id, []))
        if not all and r["days_left"] is not None and r["days_left"] > within:
            continue
        rows.append(r)
    if added:
        await db.commit()
    rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0))
    return {"today": today.isoformat(), "rows": rows, "counts": L.summarise(rows), "can_act": hr,
            "rule_note": RULE_NOTE, "types": list(L.TERM_TYPES),
            "converted_last_90d": sum(1 for t in hist_all if t.action == "convert" and t.created_at and
                                      (today - t.created_at.date()).days <= 90)}


class TermIn(BaseModel):
    start_date: str | None = None
    end_date: str
    notice_days: int | None = None
    contract_ref: str | None = None
    employment_type: str | None = None
    reason: str | None = None


@router.post("/{employee_id}/fixed-term", status_code=201)
async def set_term(employee_id: str, body: TermIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    if e.status == "terminated":
        raise HTTPException(409, "This employee has left")
    et = (body.employment_type or e.employment_type or "").strip().lower()
    if et not in L.TERM_TYPES:
        raise HTTPException(422, "Set the employment type to contract or fixed_term first (or pass employment_type)")
    start = parse_date(body.start_date, "start_date", required=False) or e.contract_start_date or e.hire_date
    end = parse_date(body.end_date, "end_date")
    errs = L.validate_term(start, end, e.hire_date)
    if errs:
        raise HTTPException(422, " ".join(errs))
    if body.notice_days is not None and not 0 <= body.notice_days <= 365:
        raise HTTPException(422, "Notice days must be between 0 and 365")
    first = e.contract_end_date is None
    e.employment_type = et
    e.contract_start_date, e.contract_end_date = start, end
    if body.notice_days is not None:
        e.contract_notice_days = body.notice_days
    if body.contract_ref is not None:
        e.contract_ref = body.contract_ref.strip()[:80] or None
    hist = await _history(db, org, e.id)
    term_no = max([t.term_no or 1 for t in hist if t.term_no], default=1)
    db.add(ContractTerm(org_id=e.org_id, employee_id=e.id, action="start" if first else "correct", term_no=term_no,
                        start_date=start, end_date=end, notice_days=e.contract_notice_days, contract_ref=e.contract_ref,
                        reason=(body.reason or "").strip() or None, actor=actor_of(request), actor_role=role_of(request)))
    await db.commit()
    await record_audit(request, "fixed_term.set", entity_type="employee", entity_id=employee_id,
                       summary=f"Contract term of {full_name(e)}: {start} to {end}")
    return _row(e, await org_today(org), await _history(db, org, e.id))


class RecommendIn(BaseModel):
    recommendation: str
    reason: str | None = None
    new_end_date: str | None = None


async def _term_bound(db, org, employee_id) -> Employee:
    e = await load_employee(db, org, employee_id)
    if e.status == "terminated":
        raise HTTPException(409, "This employee has left")
    if not L.is_term_bound(e.employment_type) or not e.contract_end_date:
        raise HTTPException(409, "This employee has no fixed-term contract on file")
    hist = await _history(db, org, e.id)
    if not any(t.action in ("start", "correct", "renew") for t in hist):
        db.add(_start_row(e))
        await db.commit()
    return e


@router.post("/{employee_id}/fixed-term/recommend", status_code=201)
async def recommend(employee_id: str, body: RecommendIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e = await _term_bound(db, org, employee_id)
    if not (is_hr(request) or _is_mgr(request, e)):
        raise HTTPException(403, "Only the employee's manager or HR can recommend")
    if body.recommendation not in L.RECOMMENDATIONS:
        raise HTTPException(422, "Recommendation must be renew, convert or end")
    if not (body.reason or "").strip():
        raise HTTPException(422, "Give a reason")
    new_end = parse_date(body.new_end_date, "new_end_date", required=False)
    if body.recommendation == "renew" and new_end and new_end <= e.contract_end_date:
        raise HTTPException(422, "The proposed end date must be after the current end date")
    db.add(ContractTerm(org_id=e.org_id, employee_id=e.id, action="recommend", recommendation=body.recommendation,
                        end_date=new_end, old_end_date=e.contract_end_date, reason=body.reason.strip(),
                        actor=actor_of(request), actor_role=role_of(request)))
    await db.commit()
    try:
        from shared.utils.notify import notify
        await notify(org, roles=["org_admin", "hr_admin", "hr_manager"], title=f"Fixed-term contract: {full_name(e)} — {body.recommendation}",
                     body=f"{actor_of(request)} recommends '{body.recommendation}' for the contract ending {e.contract_end_date}. {body.reason.strip()[:200]}",
                     link=f"/fixed-term?hl={e.employee_number}", kind="lifecycle")
    except Exception:  # noqa: BLE001 — a notification never undoes the step
        pass
    return {"ok": True, "recommendation": body.recommendation}


class RenewIn(BaseModel):
    new_end_date: str
    reason: str
    notice_days: int | None = None
    contract_ref: str | None = None
    issue_letter: bool = True


async def _letter(db, request, org, e, key, data, eff):
    from career_routers import _letter as career_letter
    return await career_letter(db, request, org, e, key, data, eff)


@router.post("/{employee_id}/fixed-term/renew")
async def renew(employee_id: str, body: RenewIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e = await _term_bound(db, org, employee_id)
    if not body.reason.strip():
        raise HTTPException(422, "Give a reason for the renewal")
    new_end = parse_date(body.new_end_date, "new_end_date")
    try:
        start, end = L.renewal_window(e.contract_end_date, new_end)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    hist = await _history(db, org, e.id)
    term_no = max([t.term_no or 1 for t in hist if t.term_no], default=1) + 1
    old = e.contract_end_date
    e.contract_start_date, e.contract_end_date = start, end
    if body.notice_days is not None:
        e.contract_notice_days = body.notice_days
    if body.contract_ref is not None:
        e.contract_ref = body.contract_ref.strip()[:80] or None
    t = ContractTerm(org_id=e.org_id, employee_id=e.id, action="renew", term_no=term_no, start_date=start, end_date=end,
                     old_end_date=old, notice_days=e.contract_notice_days, contract_ref=e.contract_ref,
                     reason=body.reason.strip(), actor=actor_of(request), actor_role=role_of(request))
    db.add(t)
    await db.commit()
    res = {"ok": True, "term_no": term_no, "contract_start_date": start.isoformat(), "contract_end_date": end.isoformat()}
    if body.issue_letter:
        res.update(await _letter(db, request, org, e, "contract_renewal",
                                 {"contract": {"old_end_date": old.isoformat(), "start_date": start.isoformat(),
                                               "end_date": end.isoformat(), "term_no": term_no,
                                               "notice_days": e.contract_notice_days or "", "reason": body.reason.strip()}}, start))
        if res.get("letter_id"):
            t.letter_id = res["letter_id"]
            await db.commit()
    await record_audit(request, "fixed_term.renew", entity_type="employee", entity_id=employee_id,
                       summary=f"Contract of {full_name(e)} renewed to {end} (term {term_no})")
    return res


class ConvertIn(BaseModel):
    effective_date: str | None = None
    reason: str
    issue_letter: bool = True
    probation_months: int | None = None


@router.post("/{employee_id}/fixed-term/convert")
async def convert(employee_id: str, body: ConvertIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e = await _term_bound(db, org, employee_id)
    if not body.reason.strip():
        raise HTTPException(422, "Give a reason")
    eff = parse_date(body.effective_date, "effective_date", required=False) or await org_today(org)
    if e.hire_date and eff < e.hire_date:
        raise HTTPException(422, "The conversion date cannot be before the joining date")
    if body.probation_months is not None and not 0 <= body.probation_months <= 12:
        raise HTTPException(422, "Probation months must be between 0 and 12")
    old_end, old_type = e.contract_end_date, e.employment_type
    e.employment_type = "full_time"
    e.contract_end_date = None
    if body.probation_months:
        from career_logic import probation_end
        e.probation_end_date = probation_end(eff, body.probation_months)
    t = ContractTerm(org_id=e.org_id, employee_id=e.id, action="convert", old_end_date=old_end, effective_date=eff,
                     reason=body.reason.strip(), actor=actor_of(request), actor_role=role_of(request))
    db.add(t)
    await db.commit()
    res = {"ok": True, "employment_type": "full_time", "effective_date": eff.isoformat(), "previous_type": old_type,
           "probation_end_date": _iso(e.probation_end_date) if body.probation_months else None}
    if body.issue_letter:
        res.update(await _letter(db, request, org, e, "contract_conversion",
                                 {"contract": {"old_end_date": old_end.isoformat(), "effective_date": eff.isoformat(),
                                               "reason": body.reason.strip()}}, eff))
        if res.get("letter_id"):
            t.letter_id = res["letter_id"]
            await db.commit()
    await record_audit(request, "fixed_term.convert", entity_type="employee", entity_id=employee_id,
                       summary=f"{full_name(e)} converted from {old_type} to permanent from {eff}")
    return res


class EndIn(BaseModel):
    reason: str
    last_working_date: str | None = None


async def _start_exit(request: Request, e: Employee, reason: str, lwd: date) -> tuple[bool, str]:
    payload = {"employee_id": str(e.id), "exit_type": "end_of_contract", "reason_code": "contract_expiry",
               "reason": reason, "source": "fixed_term", "initiated_by": actor_of(request),
               "last_working_date": lwd.isoformat()}
    headers = {k: request.headers.get(k, "") for k in ("x-org-id", "x-user-id", "x-user-email", "x-role",
                                                       "x-employee-id", "x-branch-id") if request.headers.get(k)}
    base = os.getenv("EXIT_SERVICE_URL") or settings.EMPLOYEE_URL
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.post(f"{base}/exit/cases", json=payload, headers=headers)
        if r.status_code in (200, 201):
            b = r.json() if r.content else {}
            return True, f"Exit case started ({b.get('case_number') or b.get('id') or 'ok'})"
        try:
            d = r.json().get("detail")
        except ValueError:
            d = None
        d = d.get("message") if isinstance(d, dict) else d
        return False, f"Exit case was not started ({d or 'the exit service answered ' + str(r.status_code)})"
    except httpx.HTTPError as ex:
        return False, f"Exit case was not started ({type(ex).__name__})"


@router.post("/{employee_id}/fixed-term/end")
async def end_contract(employee_id: str, body: EndIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e = await _term_bound(db, org, employee_id)
    if not body.reason.strip():
        raise HTTPException(422, "Give a reason")
    lwd = parse_date(body.last_working_date, "last_working_date", required=False) or e.contract_end_date
    t = ContractTerm(org_id=e.org_id, employee_id=e.id, action="end", old_end_date=e.contract_end_date, effective_date=lwd,
                     reason=body.reason.strip(), actor=actor_of(request), actor_role=role_of(request))
    db.add(t)
    await db.commit()
    ok, msg = await _start_exit(request, e, body.reason.strip(), lwd)
    t.exit_handover = msg[:200]
    await db.commit()
    await record_audit(request, "fixed_term.end", entity_type="employee", entity_id=employee_id,
                       summary=f"Contract of {full_name(e)} ends {lwd}", meta={"exit_started": ok})
    return {"ok": True, "exit_started": ok, "last_working_date": lwd.isoformat(),
            "message": msg if ok else msg + ". Start the exit from the Exits screen."}


@router.get("/{employee_id}/fixed-term/history")
async def history(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    if not (is_hr(request) or _is_mgr(request, e) or is_self(request, e)):
        raise HTTPException(403, "Only HR, the manager or the employee can see this contract history")
    hist = await _history(db, org, e.id)
    if e.contract_end_date is not None or any(t.action in ("renew", "convert", "end") for t in hist):
        if not any(t.action in ("start", "correct") for t in hist) and (e.contract_start_date or e.hire_date):
            t = _start_row(e)
            if hist and t.created_at and hist[0].created_at and t.created_at > hist[0].created_at:
                t.created_at = hist[0].created_at
            db.add(t)
            await db.commit()
            hist = await _history(db, org, e.id)
    if not is_hr(request):
        hist = [t for t in hist if t.action != "alert"]
    return {"employee_id": str(e.id), "employee": full_name(e), "rows": [_term_row(t) for t in hist],
            "current": {"employment_type": e.employment_type, "contract_start_date": _iso(e.contract_start_date),
                        "contract_end_date": _iso(e.contract_end_date), "contract_notice_days": e.contract_notice_days,
                        "contract_ref": e.contract_ref}}


async def scan_org(db, org: str) -> int:
    """Send due 60/30/7/0-day alerts for one org. Idempotent (one alert row per employee × end date × threshold)."""
    from shared.utils.notify import notify
    today = await org_today(org)
    emps = (await db.execute(select(Employee).where(Employee.org_id == to_uuid(org, "org"), Employee.is_active == True))).scalars().all()  # noqa: E712
    sent = 0
    for e in emps:
        if e.status == "terminated" or not L.is_term_bound(e.employment_type) or not e.contract_end_date:
            continue
        hist = await _history(db, org, e.id)
        if any(t.action in ("end", "convert") and t.old_end_date == e.contract_end_date for t in hist):
            continue                    # already decided for this end date
        already = {t.alert_days for t in hist if t.action == "alert" and t.end_date == e.contract_end_date}
        d = L.due_alert(e.contract_end_date, today, already)
        if d is None:
            continue
        left = (e.contract_end_date - today).days
        title = (f"Fixed-term contract of {full_name(e)} ends {e.contract_end_date.isoformat()}"
                 + (f" ({left} days)" if left >= 0 else f" (ended {-left} days ago)"))
        body = "Decide: renew, convert to permanent, or end on expiry (Fixed-term contracts screen)."
        db.add(ContractTerm(org_id=e.org_id, employee_id=e.id, action="alert", alert_days=d, end_date=e.contract_end_date,
                            actor="system", actor_role="system"))
        await db.commit()
        try:
            await notify(org, roles=["org_admin", "hr_admin", "hr_manager"], title=title, body=body,
                         link=f"/fixed-term?hl={e.employee_number}", kind="lifecycle")
            if e.manager_id:
                await notify(org, employee_ids=[str(e.manager_id)], title=title, body=body + " Add your recommendation.",
                             link=f"/fixed-term?hl={e.employee_number}", kind="lifecycle")
        except Exception:  # noqa: BLE001
            pass
        sent += 1
    return sent


@router.post("/fixed-term/alerts/scan")
async def scan(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    return {"sent": await scan_org(db, org_of(request))}
