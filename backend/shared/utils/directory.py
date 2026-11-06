"""Lexora HRM — tiny read-only org directory for the platform helpers.

``notify()`` and the approvals inbox need to turn "role hr" / "manager of employee X"
into user ids. The users table belongs to auth and the employees table to the
employee service; both live in the one shared Postgres, so the helpers read the few
columns they need directly (never write). Tests replace ``org_users`` /
``org_employees`` with fixtures.
"""
import logging

from sqlalchemy import text

from shared.utils import platform_models as pm

log = logging.getLogger("lexora.directory")

# A wanted role matches the user's role if it is the same word, or the user's role
# is in the wanted role's family. Stream A publishes the short names (hr, payroll);
# older logins still carry the long ones (hr_admin, payroll_admin).
ROLE_FAMILIES: dict[str, set[str]] = {
    "hr": {"hr", "hr_admin", "hr_manager", "hr_staff"},
    "payroll": {"payroll", "payroll_admin"},
    "finance": {"finance", "finance_admin", "payroll", "payroll_admin", "accountant"},   # accountant = finance reader
    "org_admin": {"org_admin"},
    "admin": {"org_admin"},
    "manager": {"manager"},
    "employee": {"employee"},
}


def role_matches(user_role: str | None, wanted: str | None) -> bool:
    if not user_role or not wanted:
        return False
    return user_role == wanted or user_role in ROLE_FAMILIES.get(wanted, ())


def role_in(user_role: str | None, wanted: list[str] | set[str] | tuple | None) -> bool:
    return any(role_matches(user_role, w) for w in (wanted or ()))


def display_name(row: dict | None) -> str:
    if not row:
        return ""
    name = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
    return name or (row.get("email") or "")


async def org_users(org_id: str) -> list[dict]:
    """Active logins of one org: id, email, role, employee_id, names."""
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT id, email, role, employee_id, first_name, last_name "
                "FROM users WHERE org_id = CAST(:org AS uuid) AND is_active IS TRUE"),
                {"org": str(org_id)})).mappings().all()
        return [{"id": str(r["id"]), "email": r["email"], "role": r["role"],
                 "employee_id": str(r["employee_id"]) if r["employee_id"] else None,
                 "first_name": r["first_name"], "last_name": r["last_name"]} for r in rows]
    except Exception as e:                                   # noqa: BLE001
        log.warning("org_users(%s) failed: %s", org_id, e)
        return []


async def org_employees(org_id: str) -> list[dict]:
    """Active employees of one org: id, manager_id, names, e-mail, department."""
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT id, manager_id, first_name, last_name, email, work_email, department, "
                "employee_number, branch_id FROM employees "
                "WHERE org_id = CAST(:org AS uuid) AND is_active IS TRUE"),
                {"org": str(org_id)})).mappings().all()
        return [{"id": str(r["id"]), "manager_id": str(r["manager_id"]) if r["manager_id"] else None,
                 "first_name": r["first_name"], "last_name": r["last_name"],
                 "email": r["work_email"] or r["email"], "department": r["department"],
                 "employee_number": r["employee_number"],
                 "branch_id": str(r["branch_id"]) if r["branch_id"] else None} for r in rows]
    except Exception as e:                                   # noqa: BLE001
        log.warning("org_employees(%s) failed: %s", org_id, e)
        return []


async def org_name(org_id: str) -> str:
    try:
        async with pm.session() as s:
            r = (await s.execute(text("SELECT name FROM organizations WHERE id = CAST(:org AS uuid)"),
                                 {"org": str(org_id)})).first()
        return r[0] if r else ""
    except Exception:                                        # noqa: BLE001
        return ""
