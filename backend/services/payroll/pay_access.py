"""Lexora HRM payroll — who may read / change the pay office's data (WASA hardening 2026-10-21).

The pay office = org_admin + the HR family (hr_admin / hr_manager / hr_staff) + the payroll
family (payroll_admin) — shared.utils.roles.OFFICE; superadmin passes every gate. Everybody
else (employee, manager, accountant …) reads ONLY their own pay records.

A request with no role at all is another service only when it carries the services' shared
token (shared.utils.internal.is_internal); "no user on the request" alone is not enough.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from shared.utils.internal import is_internal
from shared.utils.roles import OFFICE, has_role

PAY_OFFICE = OFFICE
_NOBODY = "00000000-0000-0000-0000-000000000000"


def role_of(request: Request) -> str:
    return (request.headers.get("x-role") or "").strip().lower()


def is_pay_office(request: Request) -> bool:
    role = role_of(request)
    if role:
        return has_role(role, PAY_OFFICE)
    return is_internal(request.headers)


def require_pay_office(request: Request, what: str = "open the payroll registers") -> str:
    """403 unless the caller is the pay office (or another service with the internal token)."""
    if not is_pay_office(request):
        raise HTTPException(403, f"Only HR, payroll or an org admin can {what}.")
    return role_of(request) or "service"


def pay_office_only(what: str = "open the payroll registers"):
    """A router-level dependency for routers whose EVERY route is the pay office's
    (Canada statutory pages, the migration kits). Narrower write checks inside a route
    still apply on top of it."""
    def _dep(request: Request) -> None:
        require_pay_office(request, what)
    return _dep


async def own_employee_id(request: Request) -> str:
    """The caller's own employee id (a login without an employee record matches nobody)."""
    own = (request.headers.get("x-employee-id") or "").strip()
    if not own and request.headers.get("x-user-id"):
        from shared.utils.identity import employee_id_for_user
        own = await employee_id_for_user(request.headers["x-user-id"]) or ""
    return own or _NOBODY


async def guard_office_or_own(request: Request, employee_id) -> None:
    """The pay office sees everyone; anybody else only themself — 404 (same as 'not found')
    for another employee's pay, so ids cannot be probed. A manager does NOT see a report's pay."""
    if is_pay_office(request):
        return
    if str(employee_id or "") != await own_employee_id(request):
        raise HTTPException(404, "Not found")
