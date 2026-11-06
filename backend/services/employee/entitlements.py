"""Employee service — plan employee limit (stream S4).

The org's plan row in `billing_plan_catalog` (auth service, same database) may
carry `max_employees`. Adding an employee beyond it is refused with 402
`employee_limit_reached`. NULL limit / no plan row / tables not there yet →
no limit (fail open: a missing catalogue must never stop HR from working).
Exited employees do not count.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def limit_for(db: AsyncSession, org_id) -> tuple[int | None, str | None]:
    try:
        row = (await db.execute(text(
            "SELECT p.max_employees, p.label FROM organizations o JOIN billing_plan_catalog p "
            "ON p.country = COALESCE(o.country, 'CA') AND p.plan_key = COALESCE(o.plan, 'growth') "
            "WHERE o.id = :o"), {"o": org_id})).first()
    except Exception:
        await db.rollback()
        return None, None
    if not row or row[0] is None:
        return None, None
    return int(row[0]), row[1]


async def active_count(db: AsyncSession, org_id) -> int:
    return int((await db.execute(text(
        "SELECT count(*) FROM employees WHERE org_id = :o AND is_active = TRUE "
        "AND COALESCE(status, 'active') <> 'terminated'"), {"o": org_id})).scalar() or 0)


async def require_seat(db: AsyncSession, org_id) -> None:
    limit, label = await limit_for(db, org_id)
    if limit is None:
        return
    n = await active_count(db, org_id)
    if n >= limit:
        raise HTTPException(402, {"code": "employee_limit_reached", "limit": limit, "plan": label,
                                  "message": f"Your {label} plan allows {limit} employees and you have {n}. "
                                             "Upgrade the plan in Plan & billing to add more."})
