"""Stream B (lifecycle) — helpers shared by the comp / career / probation / letters modules.

Columns on `employees` this stream needs, ensured with ADD COLUMN IF NOT EXISTS and
read / written with plain SQL so the Employee model file (owned by Stream A) is not
touched:
    grade              VARCHAR(60)   job grade / band — the SAME column Stream A added
                                     to the model (→ masters.grades); ensured here too
                                     so this module never depends on boot order
    employment_status  VARCHAR(20)   probation | confirmed   (NULL = not set)
    confirmation_date  DATE
employment_status and confirmation_date are not on the model, so the plain employee
edit (PATCH /employees/{id}) cannot write them. `grade`, designation, department,
manager, location, division and cost centre ARE on the model: the edit form must
route those through POST /employees/{id}/career-changes (see career_routers.py).
"""
import sys
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import Date, bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

sys.path.append("/app")

from models import Employee  # noqa: E402

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}
APPROVER_ROLES = {"superadmin", "org_admin", "hr_admin"}        # HR head / org admin
SETUP_ROLES = {"superadmin", "org_admin", "hr_admin"}

EXTRA_COLUMNS = [("grade", "VARCHAR(60)"), ("employment_status", "VARCHAR(20)"), ("confirmation_date", "DATE")]


def now() -> datetime:
    return datetime.now(timezone.utc)


def org_of(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def role_of(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def uid_of(request: Request) -> str:
    return request.headers.get("x-user-id") or ""


def actor_of(request: Request) -> str:
    return request.headers.get("x-user-email") or uid_of(request) or "system"


def emp_of(request: Request) -> str:
    return request.headers.get("x-employee-id") or ""


def is_hr(request: Request) -> bool:
    return role_of(request) in HR_ROLES


def require_hr(request: Request):
    if not is_hr(request):
        raise HTTPException(403, "Only HR or payroll may do this")


# Decision 2026-10-21 (least privilege): letters and career / probation records are HR's. The pay
# office keeps compensation, exits / final pay and attendance summaries, but not letters or careers.
PEOPLE_HR_ROLES = HR_ROLES - {"payroll_admin"}


def is_people_hr(request: Request) -> bool:
    return role_of(request) in PEOPLE_HR_ROLES


def require_people_hr(request: Request):
    if not is_people_hr(request):
        raise HTTPException(403, "Only HR may do this")


def require_setup(request: Request):
    if role_of(request) not in SETUP_ROLES:
        raise HTTPException(403, "Only the HR head or org admin may change this")


def same_person(request: Request, made_by_id: str | None, made_by: str | None) -> bool:
    """True when the caller is the person who made the record (maker ≠ approver)."""
    uid, email = uid_of(request), (request.headers.get("x-user-email") or "").lower()
    return bool((uid and made_by_id and uid == made_by_id) or (email and made_by and email == made_by.lower()))


def to_uuid(v, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, f"{what} is not valid")


def parse_date(v, what: str = "date", required: bool = True) -> date | None:
    if v in (None, ""):
        if required:
            raise HTTPException(400, f"{what} is required")
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, f"{what} must be a date (YYYY-MM-DD)")


def full_name(e) -> str:
    return " ".join(x for x in [e.first_name, e.last_name] if x).strip() or (e.employee_number or "")


async def load_employee(db, org: str, employee_id: str) -> Employee:
    e = (await db.execute(select(Employee).where(Employee.id == to_uuid(employee_id, "employee"),
                                                 Employee.org_id == to_uuid(org, "org")))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    return e


def is_self(request: Request, e: Employee) -> bool:
    """The caller is this employee: the gateway-stamped employee id, or the login
    email matching the employee's email."""
    if emp_of(request) and emp_of(request) == str(e.id):
        return True
    email = (request.headers.get("x-user-email") or "").strip().lower()
    return bool(email) and email in {(e.email or "").lower(), (e.work_email or "").lower()}


async def my_employee(request: Request, db) -> Employee:
    org = org_of(request)
    if emp_of(request):
        return await load_employee(db, org, emp_of(request))
    email = (request.headers.get("x-user-email") or "").strip().lower()
    if email:
        rows = (await db.execute(select(Employee).where(Employee.org_id == to_uuid(org, "org")))).scalars().all()
        for e in rows:
            if email in {(e.email or "").lower(), (e.work_email or "").lower()}:
                return e
    raise HTTPException(404, "Your login is not linked to an employee record yet")


# ── the three extra columns ───────────────────────────────────────────────────
async def ensure_columns(conn) -> None:
    for col, ddl in EXTRA_COLUMNS:
        await conn.execute(text(f"ALTER TABLE employees ADD COLUMN IF NOT EXISTS {col} {ddl}"))


async def get_extras(db, org: str, employee_id: str | None = None) -> dict:
    """{employee_id: {grade, employment_status, confirmation_date}}"""
    sql = "SELECT id, grade, employment_status, confirmation_date FROM employees WHERE org_id = :org"
    params: dict = {"org": to_uuid(org, "org")}
    if employee_id:
        sql += " AND id = :id"
        params["id"] = to_uuid(employee_id, "employee")
    stmt = text(sql).bindparams(*[bindparam(k, type_=PG_UUID(as_uuid=True)) for k in params]).columns(
        id=PG_UUID(as_uuid=True), confirmation_date=Date)
    rows = (await db.execute(stmt, params)).mappings().all()
    return {str(r["id"]): {"grade": r["grade"], "employment_status": r["employment_status"],
                           "confirmation_date": r["confirmation_date"].isoformat() if r["confirmation_date"] else None}
            for r in rows}


async def set_extras(db, employee_id, **vals) -> None:
    allowed = {c for c, _ in EXTRA_COLUMNS}
    sets = {k: v for k, v in vals.items() if k in allowed}
    if not sets:
        return
    sql = "UPDATE employees SET " + ", ".join(f"{k} = :{k}" for k in sets) + " WHERE id = :id"
    binds = [bindparam("id", type_=PG_UUID(as_uuid=True))]
    if "confirmation_date" in sets:
        binds.append(bindparam("confirmation_date", type_=Date))
    await db.execute(text(sql).bindparams(*binds), {**sets, "id": to_uuid(employee_id, "employee")})
