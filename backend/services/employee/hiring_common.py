"""Hiring / performance — small router helpers shared by both modules."""
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, Request
from sqlalchemy import LargeBinary, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402

STALE = "Someone else changed this just now. Refresh and try again."


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid(value, what: str = "Record") -> uuid.UUID:
    """A path / body id as a UUID — a malformed id is simply 'not found'."""
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, f"{what} not found")


def opt_uid(value) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, "That selection is not valid. Pick it again from the list.")


def to_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(422, "Dates must look like 2026-10-31.")


def to_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "Date and time must look like 2026-10-31T10:30.")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def num(value, what: str, *, required: bool = False, lo: float | None = None, hi: float | None = None):
    if value in (None, ""):
        if required:
            raise HTTPException(422, f"{what} is required.")
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{what} must be a number.")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise HTTPException(422, f"{what} must be between {lo} and {hi}." if hi is not None else f"{what} must be at least {lo}.")
    return v


def ser(row, skip: tuple = ()) -> dict:
    """A row as JSON. Binary columns and anything in `skip` are left out."""
    out = {}
    for col in row.__table__.columns:
        if col.name in skip or isinstance(col.type, LargeBinary):
            continue
        v = getattr(row, col.name)
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = float(v)
        out[col.name] = v
    return out


def fail(exc: RuleError):
    raise HTTPException(exc.status, exc.message)


def emp_name(e) -> str:
    return f"{e.first_name or ''} {e.last_name or ''}".strip()


async def get_actor(request: Request, db: AsyncSession) -> Actor:
    """Who is calling, from the identity the gateway stamped. The employee id is
    the stamped one, or — for a login not yet linked — the employee whose e-mail
    matches the login e-mail in this org."""
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    a = Actor(org=org, user_id=request.headers.get("x-user-id") or "",
              role=(request.headers.get("x-role") or "").lower(),
              employee_id=request.headers.get("x-employee-id") or "",
              email=(request.headers.get("x-user-email") or "").lower())
    e = None
    if a.employee_id:
        try:
            e = (await db.execute(select(Employee).where(
                Employee.org_id == org, Employee.id == uuid.UUID(a.employee_id)))).scalar_one_or_none()
        except ValueError:
            e = None
        if e is None:
            a.employee_id = ""
    if e is None and a.email:
        from sqlalchemy import func
        e = (await db.execute(select(Employee).where(
            Employee.org_id == org, Employee.is_active.is_(True),
            or_(func.lower(Employee.email) == a.email, func.lower(Employee.work_email) == a.email)
        ).limit(1))).scalar_one_or_none()
        if e is not None:
            a.employee_id = str(e.id)
    a.name = emp_name(e) if e is not None else a.email
    return a


async def employees_by_id(db: AsyncSession, org: str, ids) -> dict:
    ids = [i for i in {opt_uid(i) for i in ids if i} if i]
    if not ids:
        return {}
    rows = (await db.execute(select(Employee).where(Employee.org_id == org, Employee.id.in_(ids)))).scalars().all()
    return {str(e.id): e for e in rows}


async def one(db: AsyncSession, model, row_id, org: str, what: str):
    row = (await db.execute(select(model).where(model.id == uid(row_id, what), model.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, f"{what} not found")
    return row


async def cas(db: AsyncSession, model, row, field: str, expected, extra_where=(), **values):
    """Status change guarded by `WHERE <field> = <what was read>`. Two people
    acting at once: the second one gets a 409 instead of overwriting the first."""
    q = update(model).where(model.id == row.id, model.org_id == row.org_id, getattr(model, field) == expected)
    for w in extra_where:
        q = q.where(w)
    res = await db.execute(q.values(**values))
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, STALE)
