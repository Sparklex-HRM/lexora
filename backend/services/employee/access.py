"""Employee service — who may read / change which employee record.

    hr / payroll / admin   → every employee of the org        (scope "all")
    manager                → self + direct reports            (scope "team")
    everyone else          → only their own record            (scope "self")

"Own record" is the employee the login is linked to: the gateway stamps it as
X-Employee-Id. For logins that were made before linking existed, it falls back
to the login row, then to an exact e-mail match on exactly one employee.

A service doing its own background work (the daily exit tick, salary revisions
falling due, payroll lookups) reads the whole org ONLY when it carries the
services' shared token (X-Internal-Auth, see shared/utils/internal.py). The
gateway strips that header from every client request, so "no user on the
request" alone is never enough.
"""
import sys
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import func, or_, select

sys.path.append("/app")
from shared.utils import identity  # noqa: E402
from shared.utils.internal import is_internal  # noqa: E402
from shared.utils.roles import holds_data  # noqa: E402

from models import Employee  # noqa: E402

ORG_WIDE = identity.ORG_WIDE_ROLES
# May add / change employees (payroll changes pay fields through PATCH).
HR_WRITE = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
EDIT_ROLES = HR_WRITE | {"payroll_admin"}

ALL, TEAM, SELF = "all", "team", "self"


def role_of(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def is_system_call(request: Request) -> bool:
    """Another service acting on its own, proven by the shared internal token."""
    return is_internal(request.headers) and not request.headers.get("x-user-id")


def scope_for(role: str, system: bool = False) -> str:
    """Pure rule: role → all | team | self."""
    if system or role in ORG_WIDE:
        return ALL
    return TEAM if role == "manager" else SELF


async def self_employee_id(request: Request, db) -> str | None:
    """The employee record this login belongs to, if any."""
    eid = (request.headers.get("x-employee-id") or "").strip()
    if eid:
        return eid
    uid = request.headers.get("x-user-id")
    if uid:
        linked = await identity.employee_id_for_user(uid)
        if linked:
            return linked
    email = (request.headers.get("x-user-email") or "").strip().lower()
    try:
        org = uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        org = None
    if email and org:
        rows = (await db.execute(select(Employee.id).where(
            Employee.org_id == org, Employee.is_active.is_(True),
            or_(func.lower(Employee.email) == email, func.lower(Employee.work_email) == email)))).all()
        if len(rows) == 1:
            return str(rows[0][0])
    return None


async def viewer(request: Request, db) -> dict:
    """{role, scope, self_id} for this request."""
    role = role_of(request)
    scope = scope_for(role, is_system_call(request))
    return {"role": role, "scope": scope,
            "self_id": None if scope == ALL and not request.headers.get("x-employee-id")
            else await self_employee_id(request, db)}


def can_see(v: dict, emp) -> bool:
    """Pure rule: may this viewer read this employee row?"""
    if v["scope"] == ALL:
        return True
    me = v.get("self_id")
    if not me:
        return False
    if str(emp.id) == str(me):
        return True
    return v["scope"] == TEAM and emp.manager_id is not None and str(emp.manager_id) == str(me)


def restrict(query, v: dict):
    """Apply the viewer's scope to a `select(Employee)`."""
    if v["scope"] == ALL:
        return query
    try:
        me = uuid.UUID(str(v.get("self_id") or ""))
    except ValueError:
        me = None
    if not me:
        return query.where(Employee.id.is_(None))            # nothing
    if v["scope"] == TEAM:
        return query.where(or_(Employee.id == me, Employee.manager_id == me))
    return query.where(Employee.id == me)


# The accountant is a finance reader; on people records the contract gives them a names-only
# directory (identity.ORG_WIDE_ROLES still lets them reach every row — shared, not changed
# here — so the employee service narrows the ANSWER instead). WASA 2026-10-21.
NAMES_ONLY_ROLES = {"accountant"}


def names_only(request: Request) -> bool:
    """True when this caller may see other people's work card only (no pay / identifiers)."""
    return role_of(request) in NAMES_ONLY_ROLES and not is_system_call(request)


# Decision 2026-10-21: a manager sees the team's work record but NO pay or identifiers — compensation
# is for HR, payroll and the org admin only. These keys are blanked (None) on every record of the
# team that is not the manager's own, wherever the employee service answers with a full record.
PAY_FIELDS = (
    "annual_salary", "hourly_rate", "pay_type", "pay_frequency", "pay_periods_per_year",
    "td1_federal_claim", "td1_provincial_claim", "additional_tax_per_period", "vacation_percent",
    "cpp_exempt", "ei_exempt",
)
IDENTITY_FIELDS = (
    "bank_institution", "bank_transit", "bank_account_last4", "bank_account_masked", "has_bank_account",
    "sin_masked", "has_sin",
    "passport", "passport_masked", "has_passport",
)
PAY_HIDDEN_FIELDS = PAY_FIELDS + IDENTITY_FIELDS
PAY_HIDDEN_ROLES = {"manager"}


def hidden_fields(request: Request, emp) -> tuple[str, ...]:
    """The pay / identifier fields this caller must not see on `emp`. () = the full record.
    * a manager on a report: all of them (decision 2026-10-21);
    * org RBAC 2026-10-26: a CUSTOM role acting as an office role sees only the data classes it carries
      (data.pay -> PAY_FIELDS, data.identity -> IDENTITY_FIELDS) — the acting system role alone would show them."""
    if is_system_call(request) or is_own(request, emp):
        return ()
    if role_of(request) in PAY_HIDDEN_ROLES:
        return PAY_HIDDEN_FIELDS
    return ((() if holds_data(request, "pay") else PAY_FIELDS)
            + (() if holds_data(request, "identity") else IDENTITY_FIELDS))


def hides_pay(request: Request, emp) -> bool:
    """True when this caller may see the record but not all of its pay / identifiers."""
    return bool(hidden_fields(request, emp))


def strip_pay(out: dict, fields: tuple[str, ...] = PAY_HIDDEN_FIELDS) -> dict:
    """The record with `fields` (default: every pay / identifier field) blanked and `pay_hidden` set (pure)."""
    return {**out, **{k: None for k in fields if k in out}, "pay_hidden": True}


def redact(request: Request, emp, out: dict) -> dict:
    """`out` (the answer for `emp`) with the fields this caller may not see blanked."""
    f = hidden_fields(request, emp)
    return strip_pay(out, f) if f else out


def is_own(request: Request, emp) -> bool:
    me = (request.headers.get("x-employee-id") or "").strip()
    return bool(me) and str(emp.id) == me


def require_hr_write(request: Request, what: str = "add or change employees"):
    if is_system_call(request) or role_of(request) in HR_WRITE:
        return
    raise HTTPException(403, f"Only HR or an admin can {what}")


def require_edit(request: Request):
    if is_system_call(request) or role_of(request) in EDIT_ROLES:
        return
    raise HTTPException(403, "Only HR, payroll or an admin can change an employee record")
