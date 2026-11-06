"""Employee 360 — ONE aggregated read behind the profile page (stream U1).

GET /employees/{id_or_number}/profile
    {id} may be the employee's UUID or the employee NUMBER (EMP-0004). Returns
    {employee, tenure, permissions, manager, manager_chain, reports, compensation,
     career, career_changes, letters, letters_count, documents_count, assets,
     logins, prev, next}

Who sees what is exactly the rule the single-purpose endpoints apply:
    the record itself ........ access.can_see (hr/payroll/admin: all · manager:
                               self + direct reports · everyone else: self)
    compensation ............. career_common.is_hr (HR / payroll / admin) OR the employee (self)
    career / letters ......... career_common.is_people_hr (HR / admin, NOT payroll — 2026-10-21) OR self —
                               the employee sees applied changes and issued,
                               visible letters only (same as /{id}/career-changes)
    documents count .......... scope ALL or self (identity_routes._doc_access)
    assets ................... HR / manager / payroll (assets_routers._may_manage) or self
A section the viewer may not read is returned as null, never as a 403 — so
the page renders what the API gives and hides the rest.
"""
import sys
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import identity  # noqa: E402
from shared.utils.docstore import StoredDocument  # noqa: E402
from shared.utils.org_time import local_today  # noqa: E402

import access  # noqa: E402
import assets_routers as A  # noqa: E402
import exit_common as C  # noqa: E402
from career_common import is_hr as lifecycle_hr, is_self as lifecycle_self  # noqa: E402
from career_common import is_people_hr  # noqa: E402  (decision 2026-10-21: letters / career are HR's, not the pay office's)
from career_common import get_extras  # noqa: E402
from career_models import CareerChange, ProbationAction  # noqa: E402
from career_routers import change_out, hr_or_public  # noqa: E402
from comp_routers import _employee_view  # noqa: E402
from database import get_db  # noqa: E402
from exit_models import EmployeeAsset  # noqa: E402
from letters_models import IssuedLetter  # noqa: E402
from letters_routers import letter_out  # noqa: E402
from models import Employee  # noqa: E402
from routers import _branch, _load, _names_card, _org, _out  # noqa: E402

router = APIRouter(prefix="/employees", tags=["profile"])


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


async def resolve_id(db: AsyncSession, org: uuid.UUID, id_or_number: str) -> str:
    """A UUID is returned as-is; anything else is looked up as an employee number
    (case-insensitive) inside the org. Unknown → 404, exactly like a wrong id."""
    if _is_uuid(id_or_number):
        return str(id_or_number)
    number = (id_or_number or "").strip()
    if not number:
        raise HTTPException(404, "Employee not found")
    row = (await db.execute(select(Employee.id).where(
        Employee.org_id == org, func.lower(Employee.employee_number) == number.lower()))).first()
    if not row:
        raise HTTPException(404, "Employee not found")
    return str(row[0])


def tenure(hire: date | None, until: date | None = None) -> dict:
    """Whole years + months between joining and today (or the leaving date)."""
    if not hire:
        return {"years": 0, "months": 0, "days": 0, "text": None}
    end = until or local_today()
    if end < hire:
        return {"years": 0, "months": 0, "days": 0, "text": "Joins " + hire.isoformat()}
    months = (end.year - hire.year) * 12 + (end.month - hire.month) - (1 if end.day < hire.day else 0)
    years, months = divmod(max(months, 0), 12)
    days = (end - hire).days
    text = (f"{years} y {months} m" if years else f"{months} m") if days >= 28 else f"{days} d"
    return {"years": years, "months": months, "days": days, "text": text}


def _brief(e: Employee | None) -> dict | None:
    if not e:
        return None
    return {"id": str(e.id), "employee_number": e.employee_number,
            "name": " ".join(x for x in (e.first_name, e.last_name) if x).strip(),
            "job_title": e.job_title, "department": e.department, "status": e.status,
            "work_email": e.work_email or e.email}


async def _manager_chain(db: AsyncSession, org: uuid.UUID, e: Employee) -> list[dict]:
    """Up the reporting line from the direct manager (max 8, loop-safe)."""
    chain, seen, cur = [], {e.id}, e
    while cur.manager_id and cur.manager_id not in seen and len(chain) < 8:
        seen.add(cur.manager_id)
        m = (await db.execute(select(Employee).where(
            Employee.id == cur.manager_id, Employee.org_id == org))).scalar_one_or_none()
        if not m:
            break
        chain.append(_brief(m))
        cur = m
    return chain


async def _neighbours(db: AsyncSession, request: Request, v: dict, e: Employee) -> tuple[dict | None, dict | None]:
    """Previous / next employee by number, inside what this viewer may see."""
    q = access.restrict(select(Employee.id, Employee.employee_number).where(
        Employee.org_id == e.org_id, Employee.is_active.is_(True)), v)
    branch = _branch(request)
    if branch and v["scope"] == access.ALL:
        q = q.where(Employee.branch_id == branch)
    rows = sorted((await db.execute(q)).all(), key=lambda r: (r[1] or ""))
    ids = [str(r[0]) for r in rows]
    if str(e.id) not in ids:
        return None, None
    i = ids.index(str(e.id))
    out = lambda r: {"id": str(r[0]), "employee_number": r[1]}  # noqa: E731
    return (out(rows[i - 1]) if i > 0 else None), (out(rows[i + 1]) if i + 1 < len(rows) else None)


@router.get("/{employee_id}/profile")
async def employee_profile(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    eid = await resolve_id(db, org, employee_id)
    e = await _load(eid, request, db)                 # 404 for a record the viewer may not see
    v = await access.viewer(request, db)
    role = access.role_of(request)
    me = str(v.get("self_id") or "")
    is_self = me == str(e.id)
    org_s = str(org)
    hr = lifecycle_hr(request)                        # HR / payroll / admin — the lifecycle rule
    see_pay = hr or lifecycle_self(request, e)
    actor = C.actor_of(request)
    see_assets = A._may_manage(actor) or is_self
    see_docs = v["scope"] == access.ALL or is_self

    # ── reporting line ────────────────────────────────────────────────────────
    manager = None
    if e.manager_id:
        manager = _brief((await db.execute(select(Employee).where(
            Employee.id == e.manager_id, Employee.org_id == org))).scalar_one_or_none())
    reports = [_brief(r) for r in (await db.execute(select(Employee).where(
        Employee.org_id == org, Employee.manager_id == e.id, Employee.is_active.is_(True))
        .order_by(Employee.employee_number))).scalars().all()]
    chain = await _manager_chain(db, org, e)

    # ── pay, career, letters (lifecycle rule) ─────────────────────────────────
    compensation = career = career_changes = letters = None
    letters_count = 0
    if see_pay:
        compensation = await _employee_view(db, org_s, e)
    # Letters and career / probation: HR or the employee — not the pay office (decision 2026-10-21).
    hr = is_people_hr(request)
    if hr or lifecycle_self(request, e):
        x = (await get_extras(db, org_s, str(e.id))).get(str(e.id), {})
        acts = (await db.execute(select(ProbationAction).where(
            ProbationAction.employee_id == e.id, ProbationAction.org_id == e.org_id)
            .order_by(ProbationAction.created_at.desc()))).scalars().all()
        career = {"grade": x.get("grade") or e.grade, "employment_status": x.get("employment_status"),
                  "confirmation_date": x.get("confirmation_date"),
                  "probation_end_date": e.probation_end_date.isoformat() if e.probation_end_date else None,
                  "probation_history": [
                      {"action": a.action, "recommendation": a.recommendation,
                       "old_end_date": a.old_end_date.isoformat() if a.old_end_date else None,
                       "new_end_date": a.new_end_date.isoformat() if a.new_end_date else None,
                       "confirmation_date": a.confirmation_date.isoformat() if a.confirmation_date else None,
                       "reason": a.reason, "actor": a.actor, "exit_handover": a.exit_handover,
                       "at": a.created_at.isoformat() if a.created_at else None}
                      for a in acts if hr_or_public(hr, a)]}
        cq = select(CareerChange).where(CareerChange.org_id == e.org_id, CareerChange.employee_id == e.id)
        if not hr:
            cq = cq.where(CareerChange.status == "applied")
        career_changes = [change_out(c, e) for c in (await db.execute(
            cq.order_by(CareerChange.effective_date.desc(), CareerChange.created_at.desc()))).scalars().all()]
        lq = select(IssuedLetter).where(IssuedLetter.org_id == e.org_id, IssuedLetter.employee_id == e.id)
        if not hr:
            lq = lq.where(IssuedLetter.status == "issued", IssuedLetter.visible_to_employee.is_(True))
        letters = [letter_out(r, e) for r in (await db.execute(
            lq.order_by(IssuedLetter.created_at.desc()))).scalars().all()]
        letters_count = len(letters)

    # ── documents (count only; the list stays on /documents) ──────────────────
    documents_count = None
    if see_docs:
        documents_count = (await db.execute(select(func.count()).select_from(StoredDocument).where(
            StoredDocument.org_id == e.org_id, StoredDocument.employee_id == e.id,
            StoredDocument.deleted_at.is_(None)))).scalar() or 0

    # ── assets ────────────────────────────────────────────────────────────────
    assets = None
    if see_assets:
        rows = (await db.execute(select(EmployeeAsset).where(
            EmployeeAsset.org_id == org, EmployeeAsset.employee_id == e.id)
            .order_by(EmployeeAsset.created_at.desc()))).scalars().all()
        xs = await A._extras(db, [a.id for a in rows])
        assets = [A._out(a, xs.get(str(a.id), {}), e) for a in rows]

    # ── logins (admin / HR only — the same people who see Setup → Users) ──────
    logins = None
    if role in access.HR_WRITE:
        try:
            logins = [{"id": u["id"], "email": u["email"], "role": u["role"], "role_label": u["role_label"],
                       "status": u["status"], "last_login": u["last_login"]}
                      for u in await identity.logins_for_employee(org, e.id)]
        except Exception:                                   # the users table is optional here
            logins = None

    prev_e, next_e = await _neighbours(db, request, v, e)
    until = e.termination_date if e.status == "terminated" else None
    return {
        # decision 2026-10-21: a manager opening a report sees no pay / identifiers
        # accountant (names-only directory): another person's profile = the work card only, like GET /employees/{id}
        # (go-live live matrix 2026-10-25: annual_salary + bank details were in $.employee here)
        "employee": {**(_names_card(e) if access.names_only(request) and not is_self
                        else access.redact(request, e, _out(e))), "is_self": is_self},
        "tenure": tenure(e.hire_date, until),
        "permissions": {
            "scope": v["scope"], "is_self": is_self, "role": role,
            "can_edit": role in access.EDIT_ROLES, "can_hr_write": role in access.HR_WRITE,
            "can_see_pay": bool(see_pay), "can_see_assets": bool(see_assets),
            "can_see_documents": bool(see_docs), "can_manage_logins": role in access.HR_WRITE,
            "can_start_exit": role in access.HR_WRITE or (role == "manager" and not is_self),
        },
        "manager": manager, "manager_chain": chain, "reports": reports,
        "compensation": compensation, "career": career, "career_changes": career_changes,
        "letters": letters, "letters_count": letters_count,
        "documents_count": documents_count, "assets": assets, "logins": logins,
        "prev": prev_e, "next": next_e,
    }
