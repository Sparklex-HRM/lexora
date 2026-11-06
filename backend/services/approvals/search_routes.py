"""Global search — approval items (2026-10-20).

    GET /approvals/search?q=&types=approval&limit=

A pending item is found only when the caller may act on it (approvals_client.may_act — the same
verdict as the "Waiting for me" / "All pending" tabs: approver role, manager of the employee,
named approver, delegate, or org admin override). Items the caller RAISED are found in any state
("My requests"). Nothing else. Title, requester name and kind label match (never the summary).
No inbox sync here — search must stay cheap; the inbox page syncs when opened.
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import search as S  # noqa: E402
from shared.utils.platform_models import ApprovalItem  # noqa: E402

from database import get_db  # noqa: E402
import routers as R  # noqa: E402

router = APIRouter(prefix="/approvals", tags=["search"])
TYPES = ("approval",)


@router.get("/search")
async def search(request: Request, q: str = "", types: str | None = None, limit: int | None = None,
                 db: AsyncSession = Depends(get_db)):
    org, actor = R._org(request), R._actor(request)
    qn, toks, lim = S.normalise(q), S.tokens(q), S.clamp_limit(limit)
    if not toks or not actor.get("role") or actor.get("role") == "superadmin" or "approval" not in S.parse_types(types, TYPES):
        return S.response(qn, [], lim, "approvals")
    A = ApprovalItem
    # kind labels ("Leave", "Offer"…) match too: turn matching labels into kind codes
    kinds = [k for k in ac.KINDS if all(t in f"{k} {ac.kind_label(k)}".lower() for t in toks)]
    text_match = S.ilike_any([A.title, A.requester_name, A.kind], toks)
    cond = or_(text_match, A.kind.in_(kinds)) if kinds else text_match
    rows = (await db.execute(select(A).where(A.org_id == org, cond, or_(
        A.status == "pending", A.requester_user_id == actor.get("user_id"))).order_by(A.opened_at.desc()).limit(200))).scalars().all()
    if not rows:
        return S.response(qn, [], lim, "approvals")
    _users, by_id, employees, delegations = await R._context(org)
    hits = []
    for row in rows:
        item = ac.item_dict(row)
        mine = bool(actor.get("user_id")) and item.get("requester_user_id") == actor.get("user_id")
        can = item["status"] == "pending" and ac.may_act(item, actor, employees=employees, users=by_id,
                                                         delegations=delegations)["allowed"]
        if not (mine or can):
            continue
        hits.append(S.hit("approval", item["id"], item["title"] or ac.kind_label(item["kind"]),
                          S.subtitle(ac.kind_label(item["kind"]), item.get("requester_name"),
                                     (item.get("opened_at") or "")[:10]),
                          "/approvals" if can else "/approvals?tab=mine",
                          S.score(qn, item["title"], item.get("requester_name"), ac.kind_label(item["kind"])),
                          "waiting for you" if can else item["status"]))
    return S.response(qn, hits, lim, "approvals")
