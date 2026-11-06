"""Exit cases — /exit/*  (Stream C).  The PROCESS around the existing calculators.

PUBLISHED CONTRACT
    POST /exit/cases
        {employee_id?            default: the caller's own employee record
         exit_type               resignation | termination | end_of_contract | retirement |
                                 death | absconding | mutual_separation   (default resignation)
         reason_category?, reason?, note?
         requested_last_working_day?   YYYY-MM-DD
         documents?              [{name, ref}]
         immediate?              HR only: accept at once ("immediate termination" form)
         last_working_day?       with immediate: the REAL last working day (may be in the past)
         lwd_reason?, accepted_on?
         reason_code?, last_working_date?, source?   aliases (reason_category / requested_last_working_day)}
        → 201 {id, case_number, status, ...}   · 409 when the employee already has an open case
    GET  /exit/cases/open?employee_id=   → {open: bool, case: {...}|null}   (Stream B "do not confirm")

States: submitted → manager_review → hr_review → accepted → clearance → settlement →
settled → closed, plus withdrawn / rejected / on_hold. Every status change is
`UPDATE … WHERE status = <what was read>`.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402

import exit_logic as L  # noqa: E402
import exit_common as C  # noqa: E402
from database import get_db  # noqa: E402
from exit_models import ExitCase, ExitCaseEvent, ExitClearanceItem, EmployeeAsset  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/exit", tags=["exit"])

NO_NOTICE_TYPES = ("death", "absconding", "end_of_contract", "retirement")   # notice does not apply by default (HR can set days)


# ── meta ──────────────────────────────────────────────────────────────────────
@router.get("/meta")
async def meta(request: Request):
    C.org_of(request)
    a = C.actor_of(request)
    return {"exit_types": [{"value": k, "label": v, "employer_initiated": k in L.EMPLOYER_INITIATED} for k, v in L.EXIT_TYPES.items()],
            "states": [{"value": s, "label": L.STATE_LABELS[s]} for s in L.STATES],
            "notice_treatments": [{"value": k, "label": v} for k, v in L.NOTICE_TREATMENTS.items() if k != "none"],
            "letters": [{"key": k, "label": v, "held": k in L.LETTERS_HELD} for k, v in L.LETTER_KEYS.items()],
            "roe_codes": list(L.ROE_CODES), "roe_suggested": L.ROE_SUGGESTED,
            "is_hr": C.is_hr(a), "is_payroll": C.is_payroll(a), "role": a["role"], "my_employee_id": a["employee_id"] or None,
            "helpers": C.helpers_present()}


# ── create ────────────────────────────────────────────────────────────────────
class CaseCreate(BaseModel):
    employee_id: str | None = None
    exit_type: str = "resignation"
    reason_category: str | None = None
    reason: str | None = None
    note: str | None = None
    requested_last_working_day: str | None = None
    documents: list[dict] | None = None
    immediate: bool = False
    last_working_day: str | None = None
    lwd_reason: str | None = None
    accepted_on: str | None = None
    # aliases other streams send (Stream B "do not confirm"): same meaning as the fields above
    reason_code: str | None = None             # → reason_category
    last_working_date: str | None = None       # → requested_last_working_day
    source: str | None = None                  # e.g. "probation"


async def _accept(db: AsyncSession, case: ExitCase, emp: Employee, actor: dict, *, accepted_on: date,
                  lwd: date | None, lwd_reason: str | None, read_status: str) -> dict:
    """hr_review → accepted: fix the notice period from the policy table and the last working day."""
    st = C.settings_out(await C.get_settings_row(db, str(case.org_id)))
    if case.exit_type in NO_NOTICE_TYPES:
        n = {"days": 0, "stage": L.employment_stage(C.emp_brief(emp), accepted_on), "source": "not_applicable"}
    else:
        n = L.notice_days_for(C.emp_brief(emp), await C.load_policies(db, str(case.org_id)), st, accepted_on)
    extends = bool(st["leave_extends_lwd"])
    expected = L.expected_lwd(accepted_on, n["days"], float(case.leave_during_notice_days or 0), extends)
    final = lwd or expected
    if final != expected and not (lwd_reason or "").strip():
        raise HTTPException(422, f"The expected last working day is {expected.isoformat()}. "
                                 "Give a reason for a different date.")
    hire = emp.hire_date
    if hire and final < hire:
        raise HTTPException(422, "The last working day cannot be before the joining date")
    pos = L.notice_position(accepted_on=accepted_on, notice_days=n["days"], lwd=final,
                            leave_days=float(case.leave_during_notice_days or 0), leave_extends=extends)
    await C.move(db, case, read_status, status="accepted", accepted_on=accepted_on, notice_days=n["days"],
                 notice_stage=n["stage"], notice_source=n["source"], expected_lwd=expected, lwd=final,
                 lwd_reason=(lwd_reason or None), notice_served_days=pos["served_raw"],
                 notice_shortfall_days=pos["shortfall_days"],
                 notice_treatment=("none" if pos["shortfall_days"] == 0 else None), leave_extends_lwd=extends,
                 roe_reason_code=(case.roe_reason_code or L.ROE_SUGGESTED.get(case.exit_type)),
                 held_from_status=None)
    C.add_event(db, case, actor, "accept", frm=read_status, to="accepted",
                note=f"Notice {n['days']} day(s) ({n['source']}). Last working day {final.isoformat()}."
                     + (f" Reason: {lwd_reason}" if lwd_reason else ""))
    return pos


@router.post("/cases", status_code=201)
async def create_case(body: CaseCreate, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    employee_id = body.employee_id or actor["employee_id"]
    if not employee_id:
        raise HTTPException(422, "Choose the employee")
    emp = await C.load_employee(db, org, employee_id)
    eb = C.emp_brief(emp)
    ok, why, initiated_by = L.may_create(body.exit_type, actor, eb, body.reason or body.reason_category)
    if not ok:
        raise HTTPException(403 if why.startswith("Only") or why.startswith("You can") or why.startswith("A manager") else 422, why)
    if emp.status == "terminated":
        raise HTTPException(409, "This person has already left the company")
    if body.immediate and initiated_by != "hr":
        raise HTTPException(403, "Only HR can record an immediate exit")
    req_lwd = C.parse_date(body.requested_last_working_day or body.last_working_date, "requested_last_working_day")
    body.reason_category = body.reason_category or (body.reason_code or "").replace("_", " ").capitalize() or None
    imm_lwd = C.parse_date(body.last_working_day, "last_working_day")
    if body.immediate and not imm_lwd:
        raise HTTPException(422, "Enter the real last working day")
    rel = L.relation(actor, {"employee_id": eb["id"], "manager_id": eb["manager_id"]})
    status = L.initial_status(body.exit_type, rel)
    case = ExitCase(id=uuid.uuid4(), org_id=uuid.UUID(org), case_number=await C.next_case_number(db, org),
                    employee_id=emp.id, employee_name=eb["name"], employee_number=emp.employee_number,
                    department=emp.department, job_title=emp.job_title, manager_id=emp.manager_id,
                    manager_name=emp.manager_name, country=eb["country"], exit_type=body.exit_type,
                    initiated_by=initiated_by, reason_category=(body.reason_category or None), reason=(body.reason or None),
                    note=(body.note or None), documents=body.documents or [], status=status, submitted_on=C.today(),
                    requested_lwd=req_lwd, created_by=actor["email"], settlement_status="none",
                    notice_shortfall_days=0, garden_leave=False, leave_during_notice_days=0, leave_extends_lwd=False,
                    withdraw_requested=False, letters=[])
    db.add(case)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This employee already has an open exit case")
    C.add_event(db, case, actor, "create", to=status,
                note=f"{L.EXIT_TYPES[body.exit_type]} raised by {initiated_by}."
                     + (f" Requested last working day {req_lwd.isoformat()}." if req_lwd else "")
                     + (f" Source: {body.source}." if body.source else ""))
    if body.immediate:
        await db.flush()
        await _accept(db, case, emp, actor, accepted_on=C.parse_date(body.accepted_on, "accepted_on") or min(C.today(), imm_lwd),
                      lwd=imm_lwd, lwd_reason=body.lwd_reason or body.reason or "Immediate exit", read_status=status)
    await db.commit()
    case = await C.load_case(db, org, str(case.id))
    await record_audit(request, "exit.case.create", entity_type="exit_case", entity_id=str(case.id),
                       summary=f"Exit case {case.case_number} · {case.employee_name} · {L.EXIT_TYPES[case.exit_type]}",
                       meta={"exit_type": case.exit_type, "initiated_by": initiated_by, "immediate": body.immediate})
    link = f"/exits/{case.id}"
    if status == "submitted" and emp.manager_id:
        await C.notify(org, employee_ids=[str(emp.manager_id)], title=f"{case.employee_name} has resigned",
                       body="Open the case to review it.", link=link)
    await C.notify(org, roles=["hr_admin", "org_admin"], title=f"Exit case {case.case_number}: {case.employee_name}",
                   body=L.EXIT_TYPES[case.exit_type], link=link)
    out = C.case_out(case, actor)
    if body.immediate and case.lwd and case.lwd <= C.today():
        from exit_people import separate_case
        out["separation"] = await separate_case(db, case, C.fwd_headers(request), actor)
        out = {**C.case_out(await C.load_case(db, org, str(case.id)), actor), "separation": out["separation"]}
    return out


# ── list ──────────────────────────────────────────────────────────────────────
@router.get("/cases/open")
async def open_case_for(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Is there an open exit case for this employee? (Stream B calls this before a confirmation.)"""
    org = C.org_of(request)
    c = (await db.execute(select(ExitCase).where(ExitCase.org_id == org, ExitCase.employee_id == employee_id,
                                                 ExitCase.status.in_(L.OPEN_STATES)))).scalars().first()
    return {"open": bool(c), "case": ({"id": str(c.id), "case_number": c.case_number, "status": c.status,
                                       "status_label": L.STATE_LABELS.get(c.status), "exit_type": c.exit_type,
                                       "lwd": C.iso(c.lwd)} if c else None)}


@router.get("/cases")
async def list_cases(request: Request, scope: str | None = None, status: str | None = None,
                     department: str | None = None, exit_type: str | None = None, lwd_month: str | None = None,
                     pending_with: str | None = None, q: str | None = None, db: AsyncSession = Depends(get_db)):
    """scope: org (HR / payroll) | team (my direct reports) | mine. Default by role."""
    org = C.org_of(request)
    actor = C.actor_of(request)
    hr_like = C.is_hr(actor) or C.is_payroll(actor)
    scope = scope or ("org" if hr_like else "team" if actor["role"] == "manager" else "mine")
    if scope not in ("org", "team", "mine"):
        # an unknown scope must never fall through to "no filter" (= every case in the org)
        raise HTTPException(422, "scope must be org, team or mine")
    if scope == "org" and not hr_like:
        raise HTTPException(403, "Only HR or payroll can see every exit case")
    qy = select(ExitCase).where(ExitCase.org_id == org)
    if scope == "team":
        if not actor["employee_id"]:
            return {"scope": scope, "cases": [], "counts": {}, "departments": []}
        qy = qy.where(ExitCase.manager_id == actor["employee_id"])
    elif scope == "mine":
        if not actor["employee_id"]:
            return {"scope": scope, "cases": [], "counts": {}, "departments": []}
        qy = qy.where(ExitCase.employee_id == actor["employee_id"])
    rows = list((await db.execute(qy.order_by(ExitCase.created_at.desc()))).scalars().all())
    counts: dict = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    counts["all"] = len(rows)
    counts["open"] = sum(1 for r in rows if r.status in L.OPEN_STATES)
    departments = sorted({r.department for r in rows if r.department})
    out = []
    ql = (q or "").strip().lower()
    for r in rows:
        if status and status != "all" and (r.status not in L.OPEN_STATES if status == "open" else r.status != status):
            continue
        if department and (r.department or "") != department:
            continue
        if exit_type and r.exit_type != exit_type:
            continue
        if lwd_month and (not r.lwd or r.lwd.strftime("%Y-%m") != lwd_month):
            continue
        d = C.case_out(r, actor)
        if pending_with and not d["pending_with"].lower().startswith(pending_with.lower()):
            continue
        if ql and ql not in f"{r.employee_name} {r.employee_number} {r.case_number} {r.department or ''}".lower():
            continue
        if not hr_like:
            d.pop("rehire_eligible", None); d.pop("rehire_note", None)
        out.append(d)
    return {"scope": scope, "cases": out, "counts": counts, "departments": departments}


# ── detail ────────────────────────────────────────────────────────────────────
async def _case_for(db, request: Request, case_id: str, *, need: str | None = None) -> tuple[ExitCase, dict, set]:
    org = C.org_of(request)
    actor = C.actor_of(request)
    case = await C.load_case(db, org, case_id)
    rel = L.relation(actor, C.case_dict(case))
    if not rel:
        # a clearance owner who is not HR / manager may still open the case they must clear
        own = (await db.execute(select(ExitClearanceItem.id).where(
            ExitClearanceItem.case_id == case.id,
            (ExitClearanceItem.owner_user_id == (actor["user_id"] or "-")) | (ExitClearanceItem.owner_role == actor["role"])))).first()
        if not own:
            raise HTTPException(404, "Exit case not found")
        rel = {"owner"}
    if need == "hr" and "hr" not in rel:
        raise HTTPException(403, "Only HR can do this")
    return case, actor, rel


def _interview_view(case: ExitCase, rel: set) -> dict | None:
    """Confidential to HR. The employee sees only whether they have filled it."""
    iv = case.interview or None
    if "hr" in rel:
        return iv
    if "self" in rel:
        return {"submitted": bool(iv and iv.get("submitted")), "by_employee": bool(iv and iv.get("by_employee")),
                "answers": (iv or {}).get("answers") if (iv and iv.get("by_employee") and not iv.get("submitted")) else None}
    return None


@router.get("/cases/{case_id}")
async def case_detail(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id)
    org = str(case.org_id)
    hr = "hr" in rel
    items = await C.load_items(db, case)
    ev_q = select(ExitCaseEvent).where(ExitCaseEvent.case_id == case.id).order_by(ExitCaseEvent.at)
    events = [C.event_out(e) for e in (await db.execute(ev_q)).scalars().all() if hr or not e.hr_only]
    assets = (await db.execute(select(EmployeeAsset).where(
        EmployeeAsset.org_id == case.org_id, EmployeeAsset.employee_id == case.employee_id)
        .order_by(EmployeeAsset.issued_on))).scalars().all()
    st = C.settings_out(await C.get_settings_row(db, org))
    cd = C.case_dict(case)
    item_dicts = [C.item_out(i) for i in items]
    for i, row in zip(item_dicts, items):
        ok, _ = L.may_clear(actor, cd, {"owner_user_id": row.owner_user_id, "owner_role": row.owner_role})
        i["can_clear"] = ok and case.status in ("clearance", "settlement", "accepted")
    out = C.case_out(case, actor)
    if not (hr or "payroll" in rel):
        out.pop("rehire_eligible", None); out.pop("rehire_note", None)
    ready, why = L.settlement_ready(cd, item_dicts)
    letters = []
    for k, label in L.LETTER_KEYS.items():
        ok, why_l = L.letter_allowed(k, cd, item_dicts, st)
        issued = [l for l in (case.letters or []) if l.get("key") == k]
        letters.append({"key": k, "label": label, "allowed": ok, "held_reason": None if ok else why_l,
                        "issued": issued[-1] if issued else None})
    notice = None
    if case.accepted_on and case.lwd:
        notice = L.notice_position(accepted_on=case.accepted_on, notice_days=case.notice_days or 0, lwd=case.lwd,
                                   leave_days=float(case.leave_during_notice_days or 0), leave_extends=bool(case.leave_extends_lwd))
        notice["days_left"] = max((case.lwd - C.today()).days, 0)
    return {"case": out, "timeline": events, "clearance": item_dicts, "clearance_summary": L.clearance_summary(item_dicts),
            "assets": [C.asset_out(a) for a in assets], "notice": notice,
            "interview": _interview_view(case, rel), "interview_template": st["interview_template"],
            "can_see_interview": hr, "can_fill_interview": hr or "self" in rel,
            "settlement": {"ready": ready, "blocked_reason": None if ready else why,
                           "prefill": L.settlement_prefill(cd, item_dicts, case.country) if case.lwd else None},
            "letters": letters, "letters_done": L.letters_done(cd), "settings": {k: st[k] for k in L.DEFAULT_SETTINGS},
            "helpers": C.helpers_present()}


# ── actions (state changes) ───────────────────────────────────────────────────
class ActionBody(BaseModel):
    action: str
    reason: str | None = None
    accepted_on: str | None = None
    last_working_day: str | None = None
    lwd_reason: str | None = None
    override_reason: str | None = None


@router.post("/cases/{case_id}/action")
async def case_action(case_id: str, body: ActionBody, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id)
    org = str(case.org_id)
    cd = C.case_dict(case)
    ok, why = L.can(body.action, cd, actor)
    if not ok:
        raise HTTPException(403 if why.startswith("Only") else 409, why)
    if body.action in L.REASON_REQUIRED and not (body.reason or "").strip():
        raise HTTPException(422, "Give a reason")
    read = case.status
    to = L.next_status(body.action, cd)
    result: dict = {}
    if body.action == "accept":
        emp = await C.load_employee(db, org, str(case.employee_id))
        result["notice"] = await _accept(db, case, emp, actor,
                                         accepted_on=C.parse_date(body.accepted_on, "accepted_on") or C.today(),
                                         lwd=C.parse_date(body.last_working_day, "last_working_day"),
                                         lwd_reason=body.lwd_reason, read_status=read)
    elif body.action == "start_clearance":
        existing = await C.load_items(db, case)
        await C.move(db, case, read, status="clearance")
        if not existing:
            st = C.settings_out(await C.get_settings_row(db, org))
            for line in L.clearance_lines(st["clearance_template"], cd):
                db.add(ExitClearanceItem(org_id=case.org_id, case_id=case.id, **{
                    **line, "owner_employee_id": uuid.UUID(line["owner_employee_id"]) if line.get("owner_employee_id") else None}))
        C.add_event(db, case, actor, body.action, frm=read, to="clearance", note="Clearance checklist created.")
    elif body.action == "start_settlement":
        items = [C.item_out(i) for i in await C.load_items(db, case)]
        ready, why2 = L.settlement_ready(cd, items, body.override_reason)
        if not ready:
            raise HTTPException(409, why2)
        over = None
        if not L.clearance_summary(items)["complete"]:
            if "hr" not in rel:
                raise HTTPException(403, "Only HR can start the settlement before clearance is complete")
            over = {"by": actor["email"], "at": C.now().isoformat(), "reason": body.override_reason}
        kind = "canada_final"
        await C.move(db, case, read, status="settlement", settlement_kind=kind, settlement_status="started",
                     clearance_override=over)
        C.add_event(db, case, actor, body.action, frm=read, to="settlement",
                    note=("Clearance complete." if not over else f"Clearance overridden: {body.override_reason}"))
        result["prefill"] = L.settlement_prefill(cd, items, case.country)
    elif body.action == "close":
        if not L.letters_done(cd) and not (body.override_reason or "").strip():
            raise HTTPException(409, "Issue the separation letter and the employment reference letter first (or close with a reason)")
        await C.move(db, case, read, status="closed", closed_at=C.now())
        C.add_event(db, case, actor, body.action, frm=read, to="closed", note=body.override_reason or body.reason)
    elif body.action == "hold":
        await C.move(db, case, read, status="on_hold", held_from_status=read)
        C.add_event(db, case, actor, body.action, frm=read, to="on_hold", note=body.reason)
    elif body.action == "resume":
        await C.move(db, case, read, status=to, held_from_status=None)
        C.add_event(db, case, actor, body.action, frm=read, to=to, note=body.reason)
    elif body.action == "request_withdraw":
        await C.move(db, case, read, withdraw_requested=True, withdraw_reason=body.reason)
        C.add_event(db, case, actor, body.action, note=body.reason)
    elif body.action == "decline_withdraw":
        await C.move(db, case, read, withdraw_requested=False)
        C.add_event(db, case, actor, body.action, note=body.reason)
    elif body.action in ("withdraw", "approve_withdraw"):
        await C.move(db, case, read, status="withdrawn", withdraw_requested=False,
                     withdraw_reason=body.reason or case.withdraw_reason, closed_at=C.now())
        C.add_event(db, case, actor, body.action, frm=read, to="withdrawn", note=body.reason)
    elif body.action == "settlement_paid":
        await C.move(db, case, read, status="settled", settlement_status="paid", settlement_paid_at=C.now())
        C.add_event(db, case, actor, body.action, frm=read, to="settled", note=body.reason or "Marked paid by hand.")
    else:   # manager_review, manager_approve, manager_reject, send_to_hr, reject
        vals = {"closed_at": C.now()} if to == "rejected" else {}
        await C.move(db, case, read, status=to, **vals)
        C.add_event(db, case, actor, body.action, frm=read, to=to, note=body.reason)
    await db.commit()
    case = await C.load_case(db, org, case_id)
    await record_audit(request, f"exit.case.{body.action}", entity_type="exit_case", entity_id=case_id,
                       summary=f"{case.case_number} · {L.ACTION_LABELS[body.action]} → {L.STATE_LABELS.get(case.status)}",
                       meta={"from": read, "to": case.status})
    link = f"/exits/{case.id}"
    if body.action in ("manager_approve", "send_to_hr", "request_withdraw"):
        await C.notify(org, roles=["hr_admin", "org_admin"], title=f"{case.case_number}: waiting for HR", link=link,
                       body=f"{case.employee_name} – {L.ACTION_LABELS[body.action]}")
    if body.action in ("accept", "reject", "manager_reject", "approve_withdraw", "decline_withdraw", "close"):
        await C.notify(org, employee_ids=[str(case.employee_id)], title=f"Your exit request: {L.STATE_LABELS.get(case.status)}",
                       link="/my-resignation")
    if body.action == "start_clearance":
        await C.notify(org, roles=["hr_admin", "payroll_admin"], employee_ids=[str(case.manager_id)] if case.manager_id else None,
                       title=f"{case.case_number}: clearance to do for {case.employee_name}", link=link)
    if body.action == "start_settlement":
        await C.notify(org, roles=["payroll_admin"], title=f"{case.case_number}: settlement to work out", link=link)
    out = {"case": C.case_out(case, actor), **result}
    if body.action == "accept" and case.lwd and case.lwd <= C.today():
        from exit_people import separate_case
        out["separation"] = await separate_case(db, case, C.fwd_headers(request), actor)
        out["case"] = C.case_out(await C.load_case(db, org, case_id), actor)
    return out


# ── notice / last working day ─────────────────────────────────────────────────
class NoticeBody(BaseModel):
    last_working_day: str | None = None
    lwd_reason: str | None = None
    notice_days: int | None = None
    notice_treatment: str | None = None
    notice_treatment_reason: str | None = None
    garden_leave: bool | None = None
    leave_during_notice_days: float | None = None
    roe_reason_code: str | None = None


@router.patch("/cases/{case_id}/notice")
async def update_notice(case_id: str, body: NoticeBody, request: Request, db: AsyncSession = Depends(get_db)):
    """HR: earlier / later last working day (with a reason), notice days by hand, how a
    shortfall is settled (buy-out / waiver / pay in lieu), garden leave, leave during notice."""
    case, actor, rel = await _case_for(db, request, case_id, need="hr")
    if case.status not in ("accepted", "clearance", "settlement"):
        raise HTTPException(409, "Notice can be changed after acceptance and before the settlement is paid")
    vals: dict = {}
    notes = []
    n_days = case.notice_days or 0
    if body.notice_days is not None and int(body.notice_days) != n_days:
        if int(body.notice_days) < 0 or int(body.notice_days) > 365:
            raise HTTPException(422, "Notice days must be between 0 and 365")
        n_days = int(body.notice_days)
        vals.update(notice_days=n_days, notice_source="manual")
        notes.append(f"Notice set to {n_days} day(s) by hand.")
    leave_days = float(case.leave_during_notice_days or 0)
    if body.leave_during_notice_days is not None:
        leave_days = max(float(body.leave_during_notice_days), 0.0)
        vals["leave_during_notice_days"] = leave_days
    if body.garden_leave is not None and bool(body.garden_leave) != bool(case.garden_leave):
        vals["garden_leave"] = bool(body.garden_leave)
        notes.append("Garden leave " + ("on." if body.garden_leave else "off."))
    lwd = case.lwd
    new_lwd = C.parse_date(body.last_working_day, "last_working_day")
    expected = L.expected_lwd(case.accepted_on, n_days, leave_days, bool(case.leave_extends_lwd))
    vals["expected_lwd"] = expected
    if new_lwd and new_lwd != case.lwd:
        if case.separated_at:
            raise HTTPException(409, "The employee has already been separated on the last working day – it cannot be moved")
        if not (body.lwd_reason or "").strip():
            raise HTTPException(422, "Give a reason for changing the last working day")
        if new_lwd < case.accepted_on and case.exit_type == "resignation":
            raise HTTPException(422, "The last working day cannot be before the acceptance date")
        lwd = new_lwd
        vals.update(lwd=lwd, lwd_reason=body.lwd_reason)
        notes.append(f"Last working day set to {lwd.isoformat()}: {body.lwd_reason}")
    pos = L.notice_position(accepted_on=case.accepted_on, notice_days=n_days, lwd=lwd, leave_days=leave_days,
                            leave_extends=bool(case.leave_extends_lwd))
    vals.update(notice_served_days=pos["served_raw"], notice_shortfall_days=pos["shortfall_days"])
    treat = body.notice_treatment if body.notice_treatment is not None else case.notice_treatment
    treat_reason = body.notice_treatment_reason if body.notice_treatment_reason is not None else case.notice_treatment_reason
    if pos["shortfall_days"] == 0:
        treat = "none"
    elif treat in (None, "", "none"):
        treat = None
    else:
        ok, why = L.notice_treatment_ok(pos["shortfall_days"], treat, treat_reason, case.exit_type)
        if not ok:
            raise HTTPException(422, why)
    if treat != case.notice_treatment or treat_reason != case.notice_treatment_reason:
        vals.update(notice_treatment=treat, notice_treatment_reason=treat_reason, notice_decided_by=actor["email"])
        if treat and treat != "none":
            notes.append(f"Shortfall of {pos['shortfall_days']} day(s): {L.NOTICE_TREATMENTS[treat]}."
                         + (f" {treat_reason}" if treat_reason else ""))
    if body.roe_reason_code is not None:
        code = body.roe_reason_code.strip().upper()
        if code and code not in L.ROE_CODES:
            raise HTTPException(422, f"ROE reason code must be one of {', '.join(L.ROE_CODES)}")
        vals["roe_reason_code"] = code or None
    await C.move(db, case, case.status, **vals)
    C.add_event(db, case, actor, "notice_update", note=" ".join(notes) or "Notice details updated.")
    await db.commit()
    case = await C.load_case(db, str(case.org_id), case_id)
    return {"case": C.case_out(case, actor), "notice": pos}


# ── exit interview (confidential to HR) ───────────────────────────────────────
class InterviewBody(BaseModel):
    answers: list[dict]            # [{key, question, answer}]
    submit: bool = False


@router.put("/cases/{case_id}/interview")
async def save_interview(case_id: str, body: InterviewBody, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id)
    if not ("hr" in rel or "self" in rel):
        raise HTTPException(403, "Only HR or the employee can fill the exit interview")
    if case.status in ("withdrawn", "rejected"):
        raise HTTPException(409, "This case is not active")
    cur = case.interview or {}
    if cur.get("submitted") and "hr" not in rel:
        raise HTTPException(409, "You have already submitted the exit interview")
    iv = {"answers": [{"key": a.get("key"), "question": a.get("question"), "answer": a.get("answer")} for a in body.answers],
          "by_employee": "hr" not in rel, "filled_by": actor["email"], "filled_at": C.now().isoformat(),
          "submitted": bool(body.submit) or bool(cur.get("submitted"))}
    await C.move(db, case, case.status, interview=iv)
    C.add_event(db, case, actor, "interview", note="Exit interview " + ("submitted." if iv["submitted"] else "saved."), hr_only=False)
    await db.commit()
    return {"ok": True, "submitted": iv["submitted"]}


# ── clearance ─────────────────────────────────────────────────────────────────
class ClearBody(BaseModel):
    status: str
    recovery_amount: float | None = None
    comment: str | None = None


@router.patch("/cases/{case_id}/clearance/{item_id}")
async def clear_item(case_id: str, item_id: str, body: ClearBody, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id)
    if case.status not in ("accepted", "clearance", "settlement"):
        raise HTTPException(409, "Clearance is closed for this case")
    if case.settlement_status == "paid":
        raise HTTPException(409, "The settlement is already paid – clearance cannot change")
    it = (await db.execute(select(ExitClearanceItem).where(
        ExitClearanceItem.id == item_id, ExitClearanceItem.case_id == case.id))).scalar_one_or_none()
    if not it:
        raise HTTPException(404, "Clearance line not found")
    ok, why = L.may_clear(actor, C.case_dict(case), {"owner_user_id": it.owner_user_id, "owner_role": it.owner_role})
    if not ok:
        raise HTTPException(403, why)
    ok, why = L.clearance_update_ok(body.status, body.recovery_amount, body.comment)
    if not ok:
        raise HTTPException(422, why)
    amount = round(float(body.recovery_amount or 0), 2) if body.status == "cleared_with_recovery" else 0.0
    res = await db.execute(update(ExitClearanceItem).where(
        ExitClearanceItem.id == it.id, ExitClearanceItem.status == it.status)
        .values(status=body.status, recovery_amount=amount, comment=(body.comment or None),
                acted_by=actor["email"] if body.status != "pending" else None,
                acted_at=C.now() if body.status != "pending" else None))
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Someone else just updated this line. Refresh and try again.")
    C.add_event(db, case, actor, "clearance", note=f"{it.department} – {it.item}: {body.status.replace('_', ' ')}"
                + (f" ({amount:,.2f}: {body.comment})" if amount else ""))
    await db.commit()
    items = [C.item_out(i) for i in await C.load_items(db, case)]
    summary = L.clearance_summary(items)
    if summary["complete"]:
        await C.notify(str(case.org_id), roles=["hr_admin", "payroll_admin"],
                       title=f"{case.case_number}: clearance complete", link=f"/exits/{case.id}")
    return {"clearance": items, "clearance_summary": summary}


class LineBody(BaseModel):
    department: str
    item: str
    owner_role: str = "hr_admin"
    owner_user_id: str | None = None
    owner_name: str | None = None


@router.post("/cases/{case_id}/clearance", status_code=201)
async def add_clearance_line(case_id: str, body: LineBody, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id, need="hr")
    if case.status not in ("accepted", "clearance"):
        raise HTTPException(409, "Lines can be added while clearance is open")
    n = len(await C.load_items(db, case))
    db.add(ExitClearanceItem(org_id=case.org_id, case_id=case.id, department=body.department[:80], item=body.item[:240],
                             owner_role=body.owner_role, owner_user_id=body.owner_user_id, owner_name=body.owner_name,
                             owner_employee_id=case.manager_id if body.owner_role == "manager" else None, sort=n))
    C.add_event(db, case, actor, "clearance", note=f"Line added: {body.department} – {body.item}")
    await db.commit()
    return {"clearance": [C.item_out(i) for i in await C.load_items(db, case)]}


# ── assets issued (simple list on the employee) ───────────────────────────────
class AssetBody(BaseModel):
    employee_id: str
    item: str
    tag: str | None = None
    issued_on: str | None = None
    note: str | None = None


class AssetPatch(BaseModel):
    returned_on: str | None = None
    condition: str | None = None
    note: str | None = None
    item: str | None = None
    tag: str | None = None


def _asset_roles(actor: dict):
    if not (C.is_hr(actor) or actor["role"] in ("manager", "payroll_admin")):
        raise HTTPException(403, "Only HR or a manager can change the assets list")


@router.get("/assets")
async def list_assets(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    if not (C.is_hr(actor) or actor["role"] in ("manager", "payroll_admin") or actor["employee_id"] == employee_id):
        raise HTTPException(403, "Not allowed")
    rows = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.org_id == org, EmployeeAsset.employee_id == employee_id)
                             .order_by(EmployeeAsset.issued_on))).scalars().all()
    return [C.asset_out(a) for a in rows]


@router.post("/assets", status_code=201)
async def add_asset(body: AssetBody, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    _asset_roles(actor)
    await C.load_employee(db, org, body.employee_id)
    if not body.item.strip():
        raise HTTPException(422, "Enter the item")
    a = EmployeeAsset(org_id=uuid.UUID(org), employee_id=uuid.UUID(body.employee_id), item=body.item.strip()[:160],
                      tag=(body.tag or None), issued_on=C.parse_date(body.issued_on, "issued_on") or C.today(),
                      note=body.note, created_by=actor["email"])
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return C.asset_out(a)


@router.patch("/assets/{asset_id}")
async def update_asset(asset_id: str, body: AssetPatch, request: Request, db: AsyncSession = Depends(get_db)):
    org = C.org_of(request)
    actor = C.actor_of(request)
    _asset_roles(actor)
    a = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.id == asset_id, EmployeeAsset.org_id == org))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Asset not found")
    sent = body.model_dump(exclude_unset=True)
    if "returned_on" in sent:
        a.returned_on = C.parse_date(body.returned_on, "returned_on")
        if a.returned_on and a.issued_on and a.returned_on < a.issued_on:
            raise HTTPException(422, "Returned date cannot be before the issue date")
    for k in ("condition", "note", "item", "tag"):
        if k in sent and sent[k] is not None:
            setattr(a, k, sent[k])
    await db.commit()
    await db.refresh(a)
    return C.asset_out(a)


# ── settlement (links to the EXISTING calculators) ────────────────────────────
async def _payroll_pay_status(request_headers: dict, case: ExitCase, hire_date) -> dict | None:
    if not case.lwd:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.PAYROLL_URL}/payroll/exit/pay-status", headers=request_headers,
                            params={"employee_id": str(case.employee_id), "last_working_day": case.lwd.isoformat(),
                                    **({"hire_date": hire_date.isoformat()} if hire_date else {})})
            if r.status_code == 200:
                return r.json()
    except httpx.HTTPError:
        pass
    return None


async def _mark_paid(db, case: ExitCase, actor: dict, *, ref: str | None, kind: str | None, net, note: str) -> bool:
    """settlement → settled. False (no error) when the case is already settled — the payroll
    callback and the screen's own sync may both arrive."""
    if case.status in ("settled", "closed"):
        return False
    if case.status != "settlement":
        raise HTTPException(409, "Start the settlement on the case first")
    await C.move(db, case, "settlement", status="settled", settlement_status="paid", settlement_paid_at=C.now(),
                 settlement_ref=ref or case.settlement_ref, settlement_kind=kind or case.settlement_kind,
                 settlement_net=net)
    C.add_event(db, case, actor, "settlement_paid", frm="settlement", to="settled", note=note)
    await db.commit()
    await C.notify(str(case.org_id), roles=["hr_admin"], title=f"{case.case_number}: settlement paid – issue the letters",
                   link=f"/exits/{case.id}")
    return True


@router.get("/cases/{case_id}/settlement")
async def settlement_view(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The pre-fill for the calculator + what payroll has already paid for the exit month."""
    case, actor, rel = await _case_for(db, request, case_id)
    if not ({"hr", "payroll"} & rel):
        raise HTTPException(403, "Only HR or payroll can open the settlement")
    items = [C.item_out(i) for i in await C.load_items(db, case)]
    cd = C.case_dict(case)
    emp = await C.load_employee(db, str(case.org_id), str(case.employee_id))
    ready, why = L.settlement_ready(cd, items)
    pay = await _payroll_pay_status(C.fwd_headers(request), case, emp.hire_date)
    return {"case": C.case_out(case, actor), "ready": ready, "blocked_reason": None if ready else why,
            "prefill": L.settlement_prefill(cd, items, case.country) if case.lwd else None,
            "pay_status": pay, "pay_status_available": pay is not None}


class LinkBody(BaseModel):
    settlement_ref: str
    kind: str | None = None


@router.post("/cases/{case_id}/settlement/link")
async def settlement_link(case_id: str, body: LinkBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Record which final-pay statement (Canada final cheque / run) belongs to this case."""
    case, actor, rel = await _case_for(db, request, case_id)
    if not ({"hr", "payroll"} & rel):
        raise HTTPException(403, "Only HR or payroll can do this")
    if case.status != "settlement":
        raise HTTPException(409, "Start the settlement on the case first")
    await C.move(db, case, "settlement", settlement_ref=body.settlement_ref, settlement_kind=body.kind or case.settlement_kind,
                 settlement_status="started")
    C.add_event(db, case, actor, "settlement_link", note="Settlement statement drawn up.")
    await db.commit()
    return {"ok": True}


class PaidBody(BaseModel):
    settlement_ref: str | None = None
    kind: str | None = None
    paid_run_id: str | None = None
    net_paid: float | None = None
    pay_date: str | None = None


@router.post("/cases/{case_id}/settlement/paid")
async def settlement_paid(case_id: str, body: PaidBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Called by payroll when the final cheque is paid (and by the screen's sync)."""
    case, actor, rel = await _case_for(db, request, case_id)
    if not ({"hr", "payroll"} & rel):
        raise HTTPException(403, "Only HR or payroll can do this")
    changed = await _mark_paid(db, case, actor, ref=body.settlement_ref, kind=body.kind, net=body.net_paid,
                               note=f"Settlement paid{' on ' + body.pay_date if body.pay_date else ''}.")
    case = await C.load_case(db, str(case.org_id), case_id)
    return {"case": C.case_out(case, actor), "changed": changed}


@router.post("/cases/{case_id}/settlement/sync")
async def settlement_sync(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Re-read payroll: if the settlement linked to this case is paid, mark the case settled."""
    case, actor, rel = await _case_for(db, request, case_id)
    if not ({"hr", "payroll"} & rel):
        raise HTTPException(403, "Only HR or payroll can do this")
    emp = await C.load_employee(db, str(case.org_id), str(case.employee_id))
    pay = await _payroll_pay_status(C.fwd_headers(request), case, emp.hire_date)
    if pay is None:
        raise HTTPException(503, "Payroll could not be reached. Try again in a moment.")
    changed = False
    if case.status == "settlement":
        if pay.get("final_cheques") or []:
            fc = pay["final_cheques"][-1]
            changed = await _mark_paid(db, case, actor, ref=fc.get("run_id"), kind="canada_final", net=fc.get("net_pay"),
                                       note="Final pay found in payroll.")
    case = await C.load_case(db, str(case.org_id), case_id)
    return {"case": C.case_out(case, actor), "changed": changed, "pay_status": pay}


# ── letters (through Stream B's engine) ───────────────────────────────────────
class LetterBody(BaseModel):
    key: str
    override_reason: str | None = None


@router.post("/cases/{case_id}/letters")
async def issue_case_letter(case_id: str, body: LetterBody, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id, need="hr")
    org = str(case.org_id)
    items = [C.item_out(i) for i in await C.load_items(db, case)]
    st = C.settings_out(await C.get_settings_row(db, org))
    cd = C.case_dict(case)
    ok, why = L.letter_allowed(body.key, cd, items, st, body.override_reason)
    if not ok:
        raise HTTPException(409, why)
    data = {"exit": {"resignation_date": C.iso(case.submitted_on), "last_working_date": C.iso(case.lwd)}}
    if body.key == "fnf_cover" and case.settlement_net is not None:
        data["exit"]["fnf_amount"] = float(case.settlement_net)
    res = await C.issue_letter(org, str(case.employee_id), body.key, data, actor)
    if not res.get("ok"):
        raise HTTPException(502, res.get("message") or "The letter was not issued")
    entry = {"key": body.key, "label": L.LETTER_KEYS[body.key], "at": C.now().isoformat(), "by": actor["email"],
             "letter_id": res.get("letter_id"), "number": res.get("number"), "pdf_url": res.get("pdf_url"),
             "missing_fields": res.get("missing_fields") or [], "override_reason": body.override_reason or None}
    letters = list(case.letters or []) + [entry]
    await C.move(db, case, case.status, letters=letters)
    C.add_event(db, case, actor, "letter", note=f"{L.LETTER_KEYS[body.key]} issued"
                + (f" (number {res.get('number')})" if res.get("number") else "")
                + (f". Hold overridden: {body.override_reason}" if body.override_reason else "."))
    await db.commit()
    case = await C.load_case(db, org, case_id)
    closed = False
    if case.status == "settled" and L.letters_done(C.case_dict(case)):
        await C.move(db, case, "settled", status="closed", closed_at=C.now())
        C.add_event(db, case, actor, "close", frm="settled", to="closed", note="Letters issued – case closed.")
        await db.commit()
        case = await C.load_case(db, org, case_id)
        closed = True
    await C.notify(org, employee_ids=[str(case.employee_id)], title=f"{L.LETTER_KEYS[body.key]} is ready", link="/my-resignation")
    return {"case": C.case_out(case, actor), "letter": entry, "closed": closed}


class RehireFlagBody(BaseModel):
    eligible: bool | None = None
    note: str | None = None


@router.patch("/cases/{case_id}/rehire-flag")
async def set_rehire_flag(case_id: str, body: RehireFlagBody, request: Request, db: AsyncSession = Depends(get_db)):
    case, actor, rel = await _case_for(db, request, case_id, need="hr")
    await C.move(db, case, case.status, rehire_eligible=body.eligible, rehire_note=(body.note or None))
    C.add_event(db, case, actor, "rehire_flag", hr_only=True,
                note=("Eligible for rehire" if body.eligible else "Not eligible for rehire" if body.eligible is False else "Rehire flag cleared")
                     + (f": {body.note}" if body.note else "."))
    from exit_people import sync_spell_from_case
    await db.flush()
    await sync_spell_from_case(db, await C.load_case(db, str(case.org_id), case_id))
    await db.commit()
    return {"ok": True}
