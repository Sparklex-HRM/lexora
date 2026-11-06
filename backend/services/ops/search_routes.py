"""Global search — Engage records (2026-10-20).

    GET /engage/search?q=&types=ticket,course,policy&limit=

ticket   helpdesk tickets — HR (engage_common.HR_ROLES) = all; payroll admin (agent) = all
         NON-confidential + own; everyone else = own (employee_id or requester login). The number
         ("HD-0012"), subject, requester name and category match. Body text never matches.
course   learning courses — HR = every course; others = only courses assigned to them.
policy   ops policy register (/ops/policies) — OPS roles (HR_WRITE + manager + payroll admin).
"""
from __future__ import annotations

import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import search as S  # noqa: E402

import engage_common as C  # noqa: E402
from database import get_db  # noqa: E402
from engage_models import Course, CourseAssignment, Ticket  # noqa: E402

router = APIRouter(prefix="/engage", tags=["search"])
TYPES = ("ticket", "course", "policy")
OPS_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "manager", "payroll_admin"}


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
    role = C.role_of(request)
    qn, toks, lim = S.normalise(q), S.tokens(q), S.clamp_limit(limit)
    if not toks or not role or role == "superadmin":
        return S.response(qn, [], lim, "engage")
    want = S.parse_types(types, TYPES)
    me, uid = _uuid(C.emp_of(request)), C.uid_of(request)
    hits: list[dict] = []
    if "ticket" in want:
        T = Ticket
        qy = select(T).where(T.org_id == org, S.ilike_any([T.number, T.subject, T.employee_name, T.category, T.status], toks))
        own = []
        if me:
            own.append(T.employee_id == me)
        if uid:
            own.append(T.requester_user_id == uid)
        if C.is_hr(request):
            pass
        elif C.is_agent(request):
            qy = qy.where(or_(T.confidential.is_(False), *own))
        elif own:
            qy = qy.where(or_(*own))
        else:
            qy = None
        if qy is not None:
            for t in (await db.execute(qy.order_by(T.created_at.desc()).limit(100))).scalars().all():
                hits.append(S.hit("ticket", t.id, f"{t.number} — {t.subject}",
                                  S.subtitle(t.employee_name, (t.category or "").replace("_", " "),
                                             t.created_at.date().isoformat() if t.created_at else None),
                                  f"/engage/helpdesk?id={t.id}", S.score(qn, t.number, t.subject, t.employee_name, t.category),
                                  t.status))
    if "course" in want:
        qy = select(Course).where(Course.org_id == org, S.ilike_any([Course.title, Course.category], toks))
        if not C.is_hr(request):
            if not me:
                qy = None
            else:
                qy = qy.join(CourseAssignment, CourseAssignment.course_id == Course.id).where(
                    CourseAssignment.employee_id == me, CourseAssignment.org_id == org)
        if qy is not None:
            for c in (await db.execute(qy.limit(50))).scalars().all():
                hits.append(S.hit("course", c.id, c.title, S.subtitle(c.category, "mandatory" if c.mandatory else None),
                                  "/engage/learning?tab=courses&hl=" + c.title if C.is_hr(request) else "/learning/my?hl=" + c.title,
                                  S.score(qn, c.title, c.category), None if c.active else "inactive"))
    if "policy" in want and role in OPS_ROLES and S.has_module(request.headers, "ops"):   # /ops/* is the paid "ops" module
        from models import Policy
        P = Policy
        qy = select(P).where(P.org_id == org, P.is_active.is_(True), S.ilike_any([P.name, P.code, P.category], toks))
        for p in (await db.execute(qy.limit(50))).scalars().all():
            hits.append(S.hit("policy", p.id, p.name, S.subtitle(p.code, p.category, p.version),
                              f"/ops/policies?hl={p.name}", S.score(qn, p.name, p.code, p.category), p.status))
    return S.response(qn, hits, lim, "engage")
