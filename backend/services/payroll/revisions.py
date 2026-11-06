"""Salary revisions (hikes) — API, mounted at /payroll/revisions.

PUBLISHED CONTRACT (Stream D's appraisal cycle calls this — keep the shape)
  POST /payroll/revisions/bulk-draft
       {source, items: [{employee_id, percent | new_annual, effective_from, reason, note}]}
       → {batch_id, created, skipped: [{employee_id, errors}], totals}
       Creates a DRAFT batch. Nothing is applied until it is submitted and approved.

Single revision
  POST /payroll/revisions/preview   {employee_id, effective_from, mode, value, reason}
  POST /payroll/revisions           same + note, submit?, corrects_revision_id?
  PUT  /payroll/revisions/{id}      edit a DRAFT
  POST /payroll/revisions/{id}/submit | approve | reject | cancel | letter
  GET  /payroll/revisions?status=&employee_id=&batch_id=

Bulk
  POST /payroll/revisions/bulk-preview   {items | (filters + mode + value), effective_from, reason}
  POST /payroll/revisions/bulk-upload    CSV / XLSX file → items (+ review)
  GET  /payroll/revisions/sheet-template
  GET  /payroll/revisions/batches · GET /payroll/revisions/batches/{id}
  POST /payroll/revisions/batches/{id}/submit | approve | reject | cancel

Other
  GET/PUT /payroll/revisions/settings          two-step approval threshold
  POST /payroll/revisions/apply-due            apply approved revisions whose date has arrived
  GET  /payroll/revisions/breakup?employee_id= current break-up (HR)
  GET  /payroll/revisions/breakup/mine         self-service "My compensation"
  GET  /payroll/revisions/retro-proposals · POST …/{id}/accept | dismiss      (Canada)

Approval: draft → submitted → approved | rejected. Approver = HR head (hr_admin) or
org admin; the maker can never approve their own; with two-step on, a revision at or
above the threshold needs a second, different approver who is the org admin.
"""
import sys
import uuid
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import comp as C  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.letters_client import LetterError, issue_letter  # noqa: E402

import revisions_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from revisions_hook import RETRO_CODE, apply_due, paid_periods, retro_for, sync_retro_paid  # noqa: E402
from revisions_models import CompRevision, CompRevisionBatch, CompRevisionSettings, RetroPayProposal  # noqa: E402
from shared.utils.org_time import local_today  # org-TZ business date (stream S1 helper)

router = APIRouter(prefix="/payroll/revisions", tags=["salary-revisions"])


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _actor(request: Request) -> dict:
    return {"id": request.headers.get("x-user-id") or "", "role": _role(request),
            "email": request.headers.get("x-user-email") or request.headers.get("x-user-id") or "system"}


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id") if request.headers.get(k)}


def _require_maker(request: Request):
    if _role(request) not in L.MAKER_ROLES:
        raise HTTPException(403, "Only HR or payroll can work on salary revisions")


def _uuid(v, what="id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, f"{what} is not valid")


async def _employees(request: Request) -> list[dict]:
    """Employees + grade (grade lives in the career module of the employee service)."""
    emps, extras = [], {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=_fwd(request))
            if r.status_code == 200:
                emps = r.json()
            x = await c.get(f"{settings.EMPLOYEE_URL}/employees/career/fields", headers=_fwd(request))
            if x.status_code == 200:
                extras = (x.json() or {}).get("extras") or {}
    except httpx.HTTPError:
        raise HTTPException(503, "The employee service could not be reached. Try again.")
    for e in emps:
        e["grade"] = (extras.get(e["id"]) or {}).get("grade")
    return emps


def _country(e: dict) -> str:
    return "CA"


def _current(e: dict) -> float:
    return float(e.get("hourly_rate") or 0) if e.get("pay_type") == "hourly" else float(e.get("annual_salary") or 0)


async def _component_names(db, org: str) -> dict:
    return {}


async def breakup(db, org: str, e: dict, annual: float, base_annual: float | None = None) -> dict:
    """Monthly break-up {CODE: amount} for an annual salary. Canadian pay has no statutory
    component structure (one base wage + pay-component earnings), so this is always empty."""
    return {}


async def _settings(db, org: str) -> dict:
    s = (await db.execute(select(CompRevisionSettings).where(CompRevisionSettings.org_id == _uuid(org)))).scalar_one_or_none()
    return {"two_step_enabled": bool(s.two_step_enabled) if s else False,
            "two_step_percent": float(s.two_step_percent) if s and s.two_step_percent is not None else None,
            "two_step_amount": float(s.two_step_amount) if s and s.two_step_amount is not None else None}


async def _pending_ids(db, org: str, exclude: uuid.UUID | None = None) -> set:
    q = select(CompRevision.employee_id, CompRevision.id).where(
        CompRevision.org_id == _uuid(org), CompRevision.status.in_(L.OPEN_STATUSES))
    return {str(r[0]) for r in (await db.execute(q)).all() if r[1] != exclude}


def rev_out(r: CompRevision, e: dict | None = None) -> dict:
    old, new = float(r.old_amount or 0), float(r.new_amount or 0)
    std = (e or {}).get("standard_hours_per_week")
    diff_a = L.annual_equivalent(new, r.pay_type, std) - L.annual_equivalent(old, r.pay_type, std)
    pct = L.change_percent(old, new)
    return {"id": str(r.id), "employee_id": str(r.employee_id), "employee": L.emp_label(e) if e else None,
            "employee_number": (e or {}).get("employee_number"), "department": (e or {}).get("department"),
            "job_title": (e or {}).get("job_title"), "grade": (e or {}).get("grade"),
            "batch_id": str(r.batch_id) if r.batch_id else None, "country": r.country, "currency": r.currency,
            "pay_type": r.pay_type, "effective_from": r.effective_from.isoformat(), "mode": r.mode,
            "value": float(r.input_value or 0), "old": old, "new": new, "diff_annual": round(diff_a, 2),
            "diff_monthly": round(diff_a / 12, 2), "percent": round(pct, 2) if pct is not None else None,
            "old_components": r.old_components or {}, "new_components": r.new_components or {},
            "reason": r.reason, "reason_label": L.REASONS.get(r.reason, r.reason), "note": r.note, "rating": r.rating,
            "source": r.source, "status": r.status, "needs_two_step": bool(r.needs_two_step),
            "approvals": r.approvals or [], "corrects_revision_id": str(r.corrects_revision_id) if r.corrects_revision_id else None,
            "created_by": r.created_by, "created_at": r.created_at.isoformat() if r.created_at else None,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "decided_by": r.decided_by, "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "decision_note": r.decision_note, "applied_at": r.applied_at.isoformat() if r.applied_at else None,
            "apply_result": r.apply_result or {}, "letter_id": r.letter_id}


# ── single revision ───────────────────────────────────────────────────────────
class RevisionIn(BaseModel):
    employee_id: str
    effective_from: str
    mode: str = "new_annual"            # new_annual | percent | amount
    value: float
    reason: str = "annual_increment"
    note: str | None = None
    submit: bool = False
    corrects_revision_id: str | None = None


async def _work_out(db, request: Request, body: RevisionIn, e: dict | None = None) -> dict:
    """Everything the preview shows and the save stores, for one revision."""
    org = _org(request)
    if e is None:
        e = next((x for x in await _employees(request) if x["id"] == body.employee_id), None)
    if not e:
        raise HTTPException(404, "Employee not found")
    if body.mode not in L.MODES:
        raise HTTPException(400, f"mode must be one of {L.MODES}")
    if body.reason not in L.REASONS:
        raise HTTPException(400, f"Reason must be one of: {', '.join(L.REASONS)}")
    eff = L.to_date(body.effective_from)
    if not eff:
        raise HTTPException(400, "Effective date must be a date (YYYY-MM-DD)")
    old = _current(e)
    if old <= 0:
        raise HTTPException(400, "This employee has no current salary on record. Set it on the employee first.")
    new = L.resolve_new_amount(old, body.mode, body.value)
    if new <= 0:
        raise HTTPException(400, "The new salary must be more than 0")
    pay_type, country = e.get("pay_type") or "salary", _country(e)
    std = e.get("standard_hours_per_week")
    old_a, new_a = L.annual_equivalent(old, pay_type, std), L.annual_equivalent(new, pay_type, std)
    old_c = await breakup(db, org, e, old, old)
    new_c = await breakup(db, org, e, new, old)
    periods = await paid_periods(db, org, e["id"])
    last_end = max((b for _, b, _ in periods), default=None)
    timing = C.revision_timing(eff, last_end)
    out = {"employee": {"id": e["id"], "name": L.emp_label(e), "employee_number": e.get("employee_number"),
                        "department": e.get("department"), "job_title": e.get("job_title")},
           "country": country, "currency": C.currency_for(country), "pay_type": pay_type,
           "effective_from": eff.isoformat(), "mode": body.mode, "value": body.value, "reason": body.reason,
           "old": old, "new": new, "old_annual": round(old_a, 2), "new_annual": round(new_a, 2),
           "diff_annual": round(new_a - old_a, 2), "diff_monthly": round((new_a - old_a) / 12, 2),
           "percent": round(L.change_percent(old, new) or 0, 2),
           "old_components": old_c, "new_components": new_c, "component_names": await _component_names(db, org),
           "timing": timing, "last_paid_period_end": last_end.isoformat() if last_end else None,
           "warnings": [], "arrears": None, "retro": None}
    if abs(new - old) < 0.005:
        out["warnings"].append("No change in salary.")
    elif new < old:
        out["warnings"].append("This is a decrease.")
    if timing == "backdated":
        snap = CompRevision(effective_from=eff, old_amount=old, new_amount=new, pay_type=pay_type)
        amount, lines = retro_for(snap, e, periods)
        out["retro"] = {"amount": amount, "lines": lines, "code": RETRO_CODE,
                        "note": "This is proposed as a Retroactive Pay earning for the next run after approval. "
                                "Deductions and tax on it are worked out by the pay run, not here."}
    elif timing == "future":
        out["warnings"].append("The date is in the future. The salary changes on that date, after approval.")
    if e["id"] in await _pending_ids(db, org):
        out["warnings"].append("Another revision is already waiting for this employee.")
    out["_employee"] = e
    return out


@router.post("/preview")
async def preview(body: RevisionIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    out = await _work_out(db, request, body)
    out.pop("_employee", None)
    return out


def _new_row(org: str, w: dict, a: dict, *, note=None, source="manual", batch_id=None, rating=None,
             corrects=None) -> CompRevision:
    return CompRevision(org_id=_uuid(org), employee_id=_uuid(w["employee"]["id"]), batch_id=batch_id,
                        country=w["country"], currency=w["currency"], pay_type=w["pay_type"],
                        effective_from=date.fromisoformat(w["effective_from"]), mode=w["mode"], input_value=w["value"],
                        old_amount=w["old"], new_amount=w["new"], old_components=w["old_components"],
                        new_components=w["new_components"], reason=w["reason"], note=note, rating=rating,
                        source=source, status="draft", corrects_revision_id=corrects,
                        created_by=a["email"], created_by_id=a["id"] or None)


async def _submit_row(db, org: str, r: CompRevision, std_hours=None):
    r.needs_two_step = L.needs_two_step(float(r.old_amount), float(r.new_amount), await _settings(db, org),
                                        r.pay_type, std_hours)
    r.status, r.submitted_at, r.approvals = "submitted", _now(), []


@router.post("", status_code=201)
async def create(body: RevisionIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    org = _org(request)
    w = await _work_out(db, request, body)
    e = w.pop("_employee")
    if abs(w["new"] - w["old"]) < 0.005:
        raise HTTPException(400, "The new salary is the same as the current salary")
    corrects = None
    if body.corrects_revision_id:
        prev = (await db.execute(select(CompRevision).where(CompRevision.id == _uuid(body.corrects_revision_id),
                                                            CompRevision.org_id == _uuid(org)))).scalar_one_or_none()
        if not prev or prev.status != "applied" or str(prev.employee_id) != e["id"]:
            raise HTTPException(400, "Only an applied revision of this employee can be corrected")
        corrects = prev.id
    r = _new_row(org, w, _actor(request), note=body.note, corrects=corrects)
    db.add(r)
    if body.submit:
        await _submit_row(db, org, r, e.get("standard_hours_per_week"))
    await db.commit()
    await record_audit(request, "revisions.create", entity_type="comp_revision", entity_id=str(r.id),
                       summary=f"Salary revision for {L.emp_label(e)} from {w['effective_from']} ({r.status})",
                       meta={"old": w["old"], "new": w["new"], "reason": r.reason})
    return {**rev_out(r, e), "warnings": w["warnings"], "arrears": w["arrears"], "retro": w["retro"]}


@router.get("")
async def list_revisions(request: Request, status: str | None = None, employee_id: str | None = None,
                         batch_id: str | None = None, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    org = _org(request)
    q = select(CompRevision).where(CompRevision.org_id == _uuid(org))
    if status:
        q = q.where(CompRevision.status == status)
    if employee_id:
        q = q.where(CompRevision.employee_id == _uuid(employee_id))
    if batch_id:
        q = q.where(CompRevision.batch_id == _uuid(batch_id))
    rows = (await db.execute(q.order_by(CompRevision.created_at.desc()).limit(5000))).scalars().all()
    emps = {e["id"]: e for e in await _employees(request)}
    return [rev_out(r, emps.get(str(r.employee_id))) for r in rows]


# ── settings · apply · break-up · retro (static paths before /{rid}) ──────────
class SettingsIn(BaseModel):
    two_step_enabled: bool = False
    two_step_percent: float | None = None
    two_step_amount: float | None = None


@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    return {**await _settings(db, _org(request)), "approver_roles": sorted(L.APPROVER_ROLES),
            "final_approver_roles": sorted(L.FINAL_APPROVER_ROLES), "reasons": L.REASONS}


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in L.FINAL_APPROVER_ROLES:
        raise HTTPException(403, "Only the org admin can change approval settings")
    org = _org(request)
    for v in (body.two_step_percent, body.two_step_amount):
        if v is not None and v < 0:
            raise HTTPException(400, "A threshold cannot be negative")
    s = (await db.execute(select(CompRevisionSettings).where(CompRevisionSettings.org_id == _uuid(org)))).scalar_one_or_none()
    if not s:
        s = CompRevisionSettings(org_id=_uuid(org))
        db.add(s)
    s.two_step_enabled, s.two_step_percent, s.two_step_amount = body.two_step_enabled, body.two_step_percent, body.two_step_amount
    s.updated_by = _actor(request)["email"]
    await db.commit()
    return await _settings(db, org)


@router.post("/apply-due")
async def apply_due_now(request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    return await apply_due(db, _org(request), _fwd(request))


async def _breakup_view(db, org: str, e: dict) -> dict:
    annual = float(e.get("annual_salary") or 0)
    comps = await breakup(db, org, e, annual, annual)
    return {"employee_id": e["id"], "employee": L.emp_label(e), "employee_number": e.get("employee_number"),
            "country": _country(e), "currency": C.currency_for(_country(e)), "pay_type": e.get("pay_type") or "salary",
            "annual_amount": annual, "monthly_amount": round(annual / 12, 2) if annual else 0,
            "hourly_rate": float(e.get("hourly_rate") or 0), "components": comps,
            "component_names": await _component_names(db, org) if comps else {}}


@router.get("/breakup")
async def get_breakup(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    e = next((x for x in await _employees(request) if x["id"] == employee_id), None)
    if not e:
        raise HTTPException(404, "Employee not found")
    return await _breakup_view(db, _org(request), e)


@router.get("/breakup/mine")
async def my_breakup(request: Request, db: AsyncSession = Depends(get_db)):
    """Self-service: the logged-in employee's current break-up. The employee service
    decides who 'me' is (stamped employee id, else the login email)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees/compensation/mine", headers=_fwd(request))
    except httpx.HTTPError:
        raise HTTPException(503, "The employee service could not be reached. Try again.")
    if r.status_code != 200:
        raise HTTPException(r.status_code, (r.json() or {}).get("detail", "Not available"))
    v = r.json()
    emp, cur = v["employee"], v["current"]
    e = {"id": emp["id"], "first_name": emp["name"], "last_name": "", "employee_number": emp.get("employee_number"),
         "address_country": emp.get("country"), "pay_type": cur.get("pay_type"),
         "annual_salary": cur.get("annual_amount"), "hourly_rate": cur.get("hourly_rate"),
         "province_of_employment": None}
    try:                                   # the state drives the derived structure; read it from the employee
        async with httpx.AsyncClient(timeout=8.0) as c:
            full = await c.get(f"{settings.EMPLOYEE_URL}/employees/{emp['id']}", headers=_fwd(request))
            if full.status_code == 200:
                e["province_of_employment"] = full.json().get("province_of_employment")
    except httpx.HTTPError:
        pass
    return {**await _breakup_view(db, _org(request), e), "history": v.get("history", [])}


@router.get("/retro-proposals")
async def retro_list(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    await sync_retro_paid(db, _org(request))
    q = select(RetroPayProposal).where(RetroPayProposal.org_id == _uuid(_org(request)))
    if status:
        q = q.where(RetroPayProposal.status == status)
    rows = (await db.execute(q.order_by(RetroPayProposal.created_at.desc()))).scalars().all()
    emps = {e["id"]: e for e in await _employees(request)}
    return [{"id": str(p.id), "employee_id": str(p.employee_id), "employee": L.emp_label(emps[str(p.employee_id)])
             if str(p.employee_id) in emps else None,
             "employee_number": (emps.get(str(p.employee_id)) or {}).get("employee_number"),
             "revision_id": str(p.revision_id), "code": p.code, "label": p.label, "amount": float(p.amount),
             "currency": p.currency, "lines": p.lines or [], "status": p.status, "decided_by": p.decided_by,
             "paid_period_end": p.paid_period_end.isoformat() if p.paid_period_end else None,
             "created_at": p.created_at.isoformat() if p.created_at else None} for p in rows]


async def _retro_decide(pid: str, to: str, request: Request, db) -> dict:
    if _role(request) not in L.APPROVER_ROLES | {"payroll_admin"}:
        raise HTTPException(403, "Only payroll, the HR head or org admin can decide retro pay")
    p = (await db.execute(select(RetroPayProposal).where(RetroPayProposal.id == _uuid(pid),
                                                         RetroPayProposal.org_id == _uuid(_org(request))))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Retro pay proposal not found")
    if p.status == "paid":
        raise HTTPException(409, "This retro pay is already paid in a run")
    p.status, p.decided_by, p.decided_at = to, _actor(request)["email"], _now()
    await db.commit()
    return {"id": str(p.id), "status": p.status}


@router.post("/retro-proposals/{pid}/accept")
async def retro_accept(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _retro_decide(pid, "accepted", request, db)


@router.post("/retro-proposals/{pid}/dismiss")
async def retro_dismiss(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _retro_decide(pid, "dismissed", request, db)


# ── bulk ──────────────────────────────────────────────────────────────────────
class BulkPreviewIn(BaseModel):
    items: list[dict] | None = None
    filters: dict | None = None          # {department, grade, job_title, work_location, country, employment_type, rating}
    mode: str | None = None              # with filters: percent | amount
    value: float | None = None
    effective_from: str | None = None
    reason: str | None = "annual_increment"


def _filter(emps: list[dict], f: dict | None) -> list[dict]:
    f = {k: str(v).strip().lower() for k, v in (f or {}).items() if v not in (None, "")}
    out = []
    for e in emps:
        if e.get("status") == "terminated":
            continue
        if any(str(e.get(k) or "").strip().lower() != f[k]
               for k in ("department", "grade", "job_title", "work_location", "employment_type") if k in f):
            continue
        if "country" in f and _country(e).lower() != f["country"]:
            continue
        out.append(e)
    return out


async def _review(db, request: Request, body: BulkPreviewIn) -> dict:
    org = _org(request)
    emps = await _employees(request)
    items = list(body.items or [])
    if not items and body.filters is not None:
        if body.mode not in ("percent", "amount") or body.value is None:
            raise HTTPException(400, "Give a % or an amount to apply to the filtered employees")
        key = "percent" if body.mode == "percent" else "amount"
        items = [{"employee_id": e["id"], key: body.value, "effective_from": body.effective_from,
                  "reason": body.reason} for e in _filter(emps, body.filters)]
    rating = str((body.filters or {}).get("rating") or "").strip().lower()
    if rating:                           # rating travels on the items (sheet column / appraisal draft)
        items = [i for i in items if str(i.get("rating") or "").strip().lower() == rating]
    for i in items:
        i.setdefault("reason", body.reason or "annual_increment")
    return L.bulk_review(items, emps, pending=await _pending_ids(db, org),
                         default_effective=L.to_date(body.effective_from))


@router.post("/bulk-preview")
async def bulk_preview(body: BulkPreviewIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    return await _review(db, request, body)


@router.post("/bulk-upload")
async def bulk_upload(request: Request, file: UploadFile = File(...), effective_from: str | None = None,
                      db: AsyncSession = Depends(get_db)):
    """Read a CSV / XLSX sheet (Employee no, New salary or %, Effective date) and return the
    review. Nothing is saved — the screen then sends the rows to bulk-draft."""
    _require_maker(request)
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "The file is too large (max 5 MB)")
    parsed = L.parse_sheet(file.filename or "", data)
    rev = await _review(db, request, BulkPreviewIn(items=parsed["items"], effective_from=effective_from))
    return {**rev, "file_errors": parsed["errors"], "items": parsed["items"]}


@router.get("/sheet-template")
async def sheet_template(request: Request):
    _require_maker(request)
    text = ",".join(L.SHEET_COLUMNS) + "\nE001,,8,2026-04-01,annual_increment,\nE002,960000,,2026-04-01,promotion,\n"
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="salary-revision-sheet.csv"'})


class BulkDraftIn(BaseModel):
    source: str = "bulk"
    items: list[dict]
    name: str | None = None
    note: str | None = None


@router.post("/bulk-draft", status_code=201)
async def bulk_draft(body: BulkDraftIn, request: Request, db: AsyncSession = Depends(get_db)):
    """PUBLISHED CONTRACT — see the module docstring."""
    _require_maker(request)
    org = _org(request)
    if not body.items:
        raise HTTPException(400, "There are no rows to save")
    review = await _review(db, request, BulkPreviewIn(items=L.normalise_items(body.items, body.source)))
    emps = {e["id"]: e for e in await _employees(request)}
    a = _actor(request)
    batch = CompRevisionBatch(org_id=_uuid(org), source=(body.source or "bulk")[:40], note=body.note,
                              name=(body.name or f"{(body.source or 'Bulk').replace('_', ' ').title()} revision "
                                                 f"{local_today().isoformat()}")[:160],
                              status="draft", created_by=a["email"], created_by_id=a["id"] or None)
    db.add(batch)
    await db.flush()
    created, skipped = 0, []
    src = ("appraisal" if (body.source or "").lower().startswith(("apprais", "performance"))
           else "sheet" if body.source == "sheet" else "bulk")
    for row in review["rows"]:
        if not row.get("ok") or abs(row["new"] - row["old"]) < 0.005:
            skipped.append({"employee_id": row.get("employee_id"), "employee_number": row.get("employee_number"),
                            "errors": row.get("errors") or ["No change in salary."]})
            continue
        e = emps[row["employee_id"]]
        w = {"employee": {"id": e["id"]}, "country": row["country"], "currency": C.currency_for(row["country"]),
             "pay_type": row["pay_type"], "effective_from": row["effective_from"], "mode": row["mode"],
             "value": row["value"], "old": row["old"], "new": row["new"], "reason": row["reason"],
             "old_components": await breakup(db, org, e, row["old"], row["old"]),
             "new_components": await breakup(db, org, e, row["new"], row["old"])}
        db.add(_new_row(org, w, a, note=row.get("note"), source=src, batch_id=batch.id, rating=row.get("rating")))
        created += 1
    if not created:
        await db.rollback()
        raise HTTPException(422, {"message": "No row could be saved.", "skipped": skipped})
    await db.commit()
    await record_audit(request, "revisions.bulk_draft", entity_type="comp_revision_batch", entity_id=str(batch.id),
                       summary=f"Draft salary revision batch '{batch.name}' with {created} employees",
                       meta={"source": batch.source, "skipped": len(skipped)})
    return {"batch_id": str(batch.id), "name": batch.name, "status": "draft", "created": created,
            "skipped": skipped, "totals": review["totals"]}


async def _batch(db, org: str, bid: str) -> CompRevisionBatch:
    b = (await db.execute(select(CompRevisionBatch).where(CompRevisionBatch.id == _uuid(bid),
                                                          CompRevisionBatch.org_id == _uuid(org)))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Batch not found")
    return b


async def _batch_rows(db, b: CompRevisionBatch) -> list[CompRevision]:
    return list((await db.execute(select(CompRevision).where(CompRevision.batch_id == b.id)
                                  .order_by(CompRevision.created_at))).scalars().all())


def _batch_out(b: CompRevisionBatch, rows: list[dict]) -> dict:
    live = [r for r in rows if r["status"] not in ("cancelled", "rejected")]
    by: dict = {}
    for r in live:
        t = by.setdefault(r["currency"], {"employees": 0, "cost_per_year": 0.0})
        t["employees"] += 1
        t["cost_per_year"] += r["diff_annual"]
    for t in by.values():
        t["cost_per_year"] = round(t["cost_per_year"], 2)
        t["cost_per_month"] = round(t["cost_per_year"] / 12, 2)
    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"id": str(b.id), "name": b.name, "source": b.source, "note": b.note, "status": b.status,
            "created_by": b.created_by, "created_at": b.created_at.isoformat() if b.created_at else None,
            "employees": len(rows), "counts": counts, "totals": by}


@router.get("/batches")
async def list_batches(request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    org = _org(request)
    bs = (await db.execute(select(CompRevisionBatch).where(CompRevisionBatch.org_id == _uuid(org))
                           .order_by(CompRevisionBatch.created_at.desc()))).scalars().all()
    out = []
    for b in bs:
        out.append(_batch_out(b, [rev_out(r) for r in await _batch_rows(db, b)]))
    return out


@router.get("/batches/{bid}")
async def get_batch(bid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    b = await _batch(db, _org(request), bid)
    emps = {e["id"]: e for e in await _employees(request)}
    rows = [rev_out(r, emps.get(str(r.employee_id))) for r in await _batch_rows(db, b)]
    return {**_batch_out(b, rows), "rows": rows}


class DecisionIn(BaseModel):
    note: str | None = None


def _approve_row(r: CompRevision, a: dict) -> str:
    """Record one approval on a row. → 'approved' | 'waiting' | the refusal text."""
    d = L.approval_decision({"status": r.status, "created_by": r.created_by, "created_by_id": r.created_by_id,
                             "needs_two_step": r.needs_two_step, "approvals": r.approvals}, a)
    if not d["allowed"]:
        return d["error"]
    r.approvals = list(r.approvals or []) + [{"by": a["email"], "by_id": a["id"], "role": a["role"],
                                              "at": _now().isoformat(), "step": d["step"]}]
    if d["final"]:
        r.status, r.decided_by, r.decided_at = "approved", a["email"], _now()
        return "approved"
    return "waiting"


@router.post("/batches/{bid}/{action}")
async def batch_action(bid: str, action: str, request: Request, body: DecisionIn | None = None,
                       db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    org = _org(request)
    if action not in ("submit", "approve", "reject", "cancel"):
        raise HTTPException(404, "Unknown action")
    b = await _batch(db, org, bid)
    rows = await _batch_rows(db, b)
    a = _actor(request)
    note = (body.note if body else None) or None
    emps = {e["id"]: e for e in await _employees(request)}
    res = {"approved": 0, "waiting_second_approval": 0, "refused": []}
    if action == "submit":
        if b.status != "draft":
            raise HTTPException(409, "Only a draft batch can be submitted")
        for r in rows:
            if r.status == "draft":
                await _submit_row(db, org, r, (emps.get(str(r.employee_id)) or {}).get("standard_hours_per_week"))
        b.status = "submitted"
    elif action == "approve":
        if b.status != "submitted":
            raise HTTPException(409, "Only a submitted batch can be approved")
        if a["role"] not in L.APPROVER_ROLES:
            raise HTTPException(403, "Only the HR head or org admin can approve a salary revision.")
        for r in rows:
            if r.status != "submitted":
                continue
            got = _approve_row(r, a)
            if got == "approved":
                r.decision_note = note
                res["approved"] += 1
            elif got == "waiting":
                res["waiting_second_approval"] += 1
            else:
                res["refused"].append(got)
        if not res["approved"] and not res["waiting_second_approval"]:
            raise HTTPException(403, (res["refused"] or ["Nothing to approve."])[0])
        if not any(r.status == "submitted" for r in rows):
            b.status = "approved"
    elif action == "reject":
        if b.status != "submitted":
            raise HTTPException(409, "Only a submitted batch can be rejected")
        if a["role"] not in L.APPROVER_ROLES:
            raise HTTPException(403, "Only the HR head or org admin can reject a salary revision.")
        if not note:
            raise HTTPException(400, "Give a reason for rejecting")
        for r in rows:
            if r.status == "submitted":
                r.status, r.decided_by, r.decided_at, r.decision_note = "rejected", a["email"], _now(), note
        b.status = "rejected"
    else:
        if any(r.status == "applied" for r in rows) and not any(r.status in L.OPEN_STATUSES for r in rows):
            raise HTTPException(409, "This batch is already applied. Reverse a salary with a new correcting revision.")
        for r in rows:
            if r.status in L.OPEN_STATUSES:
                r.status, r.decision_note = "cancelled", note or "Batch withdrawn"
        b.status = "cancelled"
    await db.commit()
    await record_audit(request, f"revisions.batch.{action}", entity_type="comp_revision_batch", entity_id=str(b.id),
                       summary=f"Salary revision batch '{b.name}' {action}", meta=res)
    if action == "approve":
        res["applied_now"] = (await apply_due(db, org, _fwd(request)))["applied"]
    out = [rev_out(r, emps.get(str(r.employee_id))) for r in await _batch_rows(db, b)]
    return {**_batch_out(b, out), "rows": out, "result": res}


# ── one revision: edit / decide / letter ──────────────────────────────────────
async def _rev(db, org: str, rid: str) -> CompRevision:
    r = (await db.execute(select(CompRevision).where(CompRevision.id == _uuid(rid),
                                                     CompRevision.org_id == _uuid(org)))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Revision not found")
    return r


@router.put("/{rid}")
async def edit_draft(rid: str, body: RevisionIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    org = _org(request)
    r = await _rev(db, org, rid)
    if r.status != "draft":
        raise HTTPException(409, "Only a draft can be edited. Withdraw it and make a new one.")
    body.employee_id = str(r.employee_id)
    w = await _work_out(db, request, body)
    e = w.pop("_employee")
    r.effective_from, r.mode, r.input_value = date.fromisoformat(w["effective_from"]), w["mode"], w["value"]
    r.old_amount, r.new_amount, r.old_components, r.new_components = w["old"], w["new"], w["old_components"], w["new_components"]
    r.reason, r.note = w["reason"], body.note
    if body.submit:
        await _submit_row(db, org, r, e.get("standard_hours_per_week"))
    await db.commit()
    return rev_out(r, e)


@router.post("/{rid}/letter", status_code=201)
async def revision_letter(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Issue the salary revision letter through the letter engine."""
    _require_maker(request)
    org = _org(request)
    r = await _rev(db, org, rid)
    if r.status not in ("approved", "applied"):
        raise HTTPException(409, "Approve the revision before issuing its letter")
    if r.pay_type == "hourly":
        raise HTTPException(400, "The standard salary revision letter is for salaried staff. "
                                 "Use Letters with your own template for hourly staff.")
    data = {"salary": {"old_annual": float(r.old_amount), "new_annual": float(r.new_amount),
                       "effective_from": r.effective_from.isoformat(), "components": r.new_components or {}},
            "component_names": await _component_names(db, org)}
    try:
        res = await issue_letter(org, str(r.employee_id), "salary_revision", data, _actor(request),
                                 effective_date=r.effective_from.isoformat(),
                                 allow_blank=not (r.new_components or {}), source="salary_revision")
    except LetterError as ex:
        raise HTTPException(ex.status if ex.status in (400, 404, 409, 422, 503) else 502, ex.detail)
    r.letter_id = res.get("letter_id")
    await db.commit()
    return res


@router.post("/{rid}/{action}")
async def decide(rid: str, action: str, request: Request, body: DecisionIn | None = None,
                 db: AsyncSession = Depends(get_db)):
    _require_maker(request)
    org = _org(request)
    if action not in ("submit", "approve", "reject", "cancel"):
        raise HTTPException(404, "Unknown action")
    r = await _rev(db, org, rid)
    a = _actor(request)
    note = (body.note if body else None) or None
    emps = {e["id"]: e for e in await _employees(request)}
    e = emps.get(str(r.employee_id))
    info: dict = {}
    if action == "submit":
        if r.status != "draft":
            raise HTTPException(409, "Only a draft can be submitted")
        await _submit_row(db, org, r, (e or {}).get("standard_hours_per_week"))
    elif action == "approve":
        got = _approve_row(r, a)
        if got not in ("approved", "waiting"):
            raise HTTPException(409 if got.startswith("Only a submitted") else 403, got)
        r.decision_note = note or r.decision_note
        info["waiting_second_approval"] = got == "waiting"
    elif action == "reject":
        if r.status != "submitted":
            raise HTTPException(409, "Only a submitted revision can be rejected")
        if a["role"] not in L.APPROVER_ROLES:
            raise HTTPException(403, "Only the HR head or org admin can reject a salary revision.")
        if not note:
            raise HTTPException(400, "Give a reason for rejecting")
        r.status, r.decided_by, r.decided_at, r.decision_note = "rejected", a["email"], _now(), note
    else:
        err = L.can_cancel(r.status)
        if err:
            raise HTTPException(409, err)
        r.status, r.decision_note = "cancelled", note or "Withdrawn"
    await db.commit()
    await record_audit(request, f"revisions.{action}", entity_type="comp_revision", entity_id=str(r.id),
                       summary=f"Salary revision {action} ({r.status})", meta={"employee": str(r.employee_id)})
    if action == "approve" and r.status == "approved":
        info["applied_now"] = (await apply_due(db, org, _fwd(request)))["applied"]
        await db.refresh(r)
    return {**rev_out(r, e), **info}
