"""People directory — every signed-in role may look colleagues up (stream S1).

    GET /employees/directory[?q=&department=]

Mounted INSIDE the /employees router (see routers.py) before `/{employee_id}`.
Returns WORK facts only, from an explicit allow-list (`DIRECTORY_FIELDS`): name,
designation, department, work e-mail, manager, work location / branch, initials. Never
salary or pay, SIN / bank, personal phone / e-mail / address, date of
birth, emergency or nominee details — those stay behind GET /employees (scoped by role).
People who have left (status terminated) are not listed. A branch-scoped login sees its
own branch, like the full list.
"""
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from database import get_db  # noqa: E402
from models import Employee  # noqa: E402

directory_sub = APIRouter(tags=["employees"])

DIRECTORY_FIELDS = ("id", "employee_number", "first_name", "last_name", "preferred_name", "display_name",
                    "initials", "job_title", "department", "work_email", "manager_id", "manager_name",
                    "work_location", "branch_name", "is_self")


def _initials(first: str | None, last: str | None) -> str:
    return ((first or "").strip()[:1] + (last or "").strip()[:1]).upper() or "?"


def directory_row(e: Employee, me: str = "") -> dict:
    """The allow-listed public card of one employee (pure)."""
    first = e.preferred_name or e.first_name
    row = {
        "id": str(e.id), "employee_number": e.employee_number,
        "first_name": e.first_name, "last_name": e.last_name, "preferred_name": e.preferred_name,
        "display_name": f"{first or ''} {e.last_name or ''}".strip(),
        "initials": _initials(first, e.last_name),
        "job_title": e.job_title, "department": e.department,
        "work_email": e.work_email or None,
        "manager_id": str(e.manager_id) if e.manager_id else None, "manager_name": e.manager_name,
        "work_location": e.work_location, "branch_name": e.branch_name,
        "is_self": bool(me) and str(e.id) == me,
    }
    return row


@directory_sub.get("/directory")
async def people_directory(request: Request, q: str | None = None, department: str | None = None,
                           db: AsyncSession = Depends(get_db)):
    try:
        org = uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")
    if not (request.headers.get("x-role") or "").strip():
        raise HTTPException(401, "Sign in again — no role on this request")
    query = select(Employee).where(Employee.org_id == org, Employee.is_active.is_(True),
                                   Employee.status != "terminated")
    try:
        branch = uuid.UUID(request.headers.get("x-branch-id") or "")
        query = query.where(Employee.branch_id == branch)
    except ValueError:
        pass
    if department:
        query = query.where(Employee.department == department)
    rows = (await db.execute(query.order_by(Employee.first_name, Employee.last_name))).scalars().all()
    me = (request.headers.get("x-employee-id") or "").strip()
    out = [directory_row(e, me) for e in rows]
    if q and q.strip():
        needle = q.strip().lower()
        out = [r for r in out if needle in " ".join(str(r[k] or "") for k in (
            "display_name", "first_name", "last_name", "employee_number", "job_title", "department",
            "work_email", "work_location", "branch_name", "manager_name")).lower()]
    return out
