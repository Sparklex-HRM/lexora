"""Global search — attendance requests (2026-10-20).

    GET /time/search?q=&types=attendance,permission&limit=

attendance  attendance corrections (ta_regularizations) — scope as GET /time/regularizations:
            HR/admin (routers.ADMIN_ROLES) = org; manager = own + direct reports (manager_id);
            everyone else = own.
permission  short-hours permissions (ta_permissions) — same scope.
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
from models import Regularization  # noqa: E402
from models_ess import Permission  # noqa: E402
from routers import ADMIN_ROLES  # noqa: E402

router = APIRouter(prefix="/time", tags=["search"])
TYPES = ("attendance", "permission")
REASON = {"missed_punch": "missed punch", "wrong_time": "wrong time", "forgot": "forgot to punch"}


def _uuid(v):
    try:
        return uuid.UUID(str(v or ""))
    except ValueError:
        return None


def _scoped(qy, M, role, me):
    if role in ADMIN_ROLES:
        return qy
    if not me:
        return None
    return qy.where(or_(M.employee_id == me, M.manager_id == me) if role == "manager" else M.employee_id == me)


@router.get("/search")
async def search(request: Request, q: str = "", types: str | None = None, limit: int | None = None,
                 db: AsyncSession = Depends(get_db)):
    org = _uuid(request.headers.get("x-org-id"))
    if not org:
        raise HTTPException(401, "Missing organization context")
    role = (request.headers.get("x-role") or "").lower()
    qn, toks, lim = S.normalise(q), S.tokens(q), S.clamp_limit(limit)
    want = S.parse_types(types, TYPES)
    if not toks or not role or role == "superadmin":
        return S.response(qn, [], lim, "time")
    me = _uuid(request.headers.get("x-employee-id"))
    hits: list[dict] = []
    words = " ".join(toks)
    if "attendance" in want:
        R = Regularization
        qy = _scoped(select(R).where(R.org_id == org), R, role, me)
        if qy is not None:
            # "correction" / "regularization" / "missed punch" find the request type itself
            generic = any(w in words for w in ("correction", "regulari", "missed", "punch", "attendance"))
            if not generic:
                qy = qy.where(S.ilike_any([R.employee_name, R.reason, R.status, R.note], toks))
            for r in (await db.execute(qy.order_by(R.created_at.desc()).limit(100))).scalars().all():
                label = REASON.get(r.reason, r.reason)
                sc = S.score(qn, r.employee_name, label, r.status, "attendance correction regularization")
                hits.append(S.hit("attendance", r.id, f"Attendance correction — {r.employee_name or ''}".strip(" —"),
                                  S.subtitle(r.work_date.isoformat() if r.work_date else None, label,
                                             f"{r.requested_in or '—'}–{r.requested_out or '—'}"),
                                  "/attendance" if me and r.employee_id == me else "/approvals", sc or 45.0, r.status))
    if "permission" in want:
        P = Permission
        qy = _scoped(select(P).where(P.org_id == org), P, role, me)
        if qy is not None:
            generic = "permission" in words or "short hours" in words
            if not generic:
                qy = qy.where(S.ilike_any([P.employee_name, P.reason, P.status], toks))
            for p in (await db.execute(qy.order_by(P.created_at.desc()).limit(100))).scalars().all():
                sc = S.score(qn, p.employee_name, p.reason, p.status, "permission short hours")
                hits.append(S.hit("permission", p.id, f"Permission — {p.employee_name or ''}".strip(" —"),
                                  S.subtitle(p.work_date.isoformat() if p.work_date else None,
                                             f"{p.from_time}–{p.to_time}", f"{float(p.hours or 0):g} h"),
                                  "/attendance" if me and p.employee_id == me else "/approvals", sc or 45.0, p.status))
    return S.response(qn, hits, lim, "time")
