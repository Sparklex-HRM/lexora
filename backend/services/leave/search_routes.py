"""Global search — leave requests (2026-10-20).

    GET /leave/search?q=&types=leave&limit=

Same scope as GET /leave/requests: HR/admin (routers.ADMIN_ROLES) = the whole org (branch
filter when one is stamped), manager = own + requests whose approving manager is them,
everyone else = own only. A login with no employee record finds nothing (stricter than the
list's legacy fall-through). Payroll admin / accountant are not leave approvers → own only.
"""
from __future__ import annotations

import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import search as S  # noqa: E402

from database import get_db  # noqa: E402
from models import LeaveRequest  # noqa: E402
from routers import ADMIN_ROLES  # noqa: E402

router = APIRouter(prefix="/leave", tags=["search"])
TYPES = ("leave",)


def _uuid(v):
    try:
        return uuid.UUID(str(v or ""))
    except ValueError:
        return None


@router.get("/search")
async def search(request: Request, q: str = "", types: str | None = None, limit: int | None = None,
                 db: AsyncSession = Depends(get_db)):
    org = _uuid(request.headers.get("x-org-id"))
    if not org:
        raise HTTPException(401, "Missing organization context")
    role = (request.headers.get("x-role") or "").lower()
    qn, toks, lim = S.normalise(q), S.tokens(q), S.clamp_limit(limit)
    if not toks or not role or role == "superadmin" or "leave" not in S.parse_types(types, TYPES):
        return S.response(qn, [], lim, "leave")
    R = LeaveRequest
    qy = select(R).where(R.org_id == org, S.ilike_any(
        [R.employee_name, R.employee_number, R.leave_type_code, R.leave_type_name, R.status, R.reason], toks))
    me = _uuid(request.headers.get("x-employee-id"))
    if role not in ADMIN_ROLES:
        if not me:
            return S.response(qn, [], lim, "leave")
        qy = qy.where(or_(R.employee_id == me, R.manager_id == me) if role == "manager" else R.employee_id == me)
    rows = (await db.execute(qy.order_by(R.created_at.desc()).limit(100))).scalars().all()
    hits = []
    for r in rows:
        mine = bool(me) and r.employee_id == me
        span = r.start_date.isoformat() + (f" → {r.end_date.isoformat()}" if r.end_date and r.end_date != r.start_date else "")
        hits.append(S.hit("leave", r.id, f"{r.leave_type_name or r.leave_type_code} — {r.employee_name or ''}".strip(" —"),
                          S.subtitle(r.employee_number, span, f"{float(r.days or 0):g} day(s)"),
                          "/leave" if mine or role not in ADMIN_ROLES | {"manager"} else f"/leave?hl={r.employee_name or ''}",
                          S.score(qn, r.employee_name, r.employee_number, r.leave_type_name, r.leave_type_code, r.status),
                          r.status))
    return S.response(qn, hits, lim, "leave")
