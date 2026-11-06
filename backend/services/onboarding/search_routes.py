"""Global search — onboarding cases (2026-10-20).

    GET /onboarding/search?q=&types=onboarding&limit=

HR only, exactly like GET /onboarding/sessions (routers.HR_ROLES); every other role gets an
empty list (not a 403 — the gateway merges silently). Org-scoped by the stamped X-Org-Id.
"""
from __future__ import annotations

import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import search as S  # noqa: E402

from database import get_db  # noqa: E402
from models import OnboardingSession  # noqa: E402
from routers import HR_ROLES  # noqa: E402

router = APIRouter(prefix="/onboarding", tags=["search"])
TYPES = ("onboarding",)


@router.get("/search")
async def search(request: Request, q: str = "", types: str | None = None, limit: int | None = None,
                 db: AsyncSession = Depends(get_db)):
    try:
        org = uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")
    role = (request.headers.get("x-role") or "").lower()
    qn, toks, lim = S.normalise(q), S.tokens(q), S.clamp_limit(limit)
    if not toks or role not in HR_ROLES or role == "superadmin" or "onboarding" not in S.parse_types(types, TYPES):
        return S.response(qn, [], lim, "onboarding")
    O = OnboardingSession
    rows = (await db.execute(select(O).where(O.org_id == org, S.ilike_any(
        [O.first_name, O.last_name, O.email, O.employee_number, O.job_title, O.status], toks)
    ).order_by(O.created_at.desc()).limit(100))).scalars().all()
    hits = []
    for s in rows:
        name = f"{s.first_name or ''} {s.last_name or ''}".strip()
        hits.append(S.hit("onboarding", s.id, f"Onboarding — {name}",
                          S.subtitle(s.employee_number, s.job_title, (s.data or {}).get("department"),
                                     (s.data or {}).get("hire_date")),
                          f"/onboarding?hl={s.email or name}", S.score(qn, name, s.employee_number, s.email, s.job_title),
                          s.status))
    return S.response(qn, hits, lim, "onboarding")
