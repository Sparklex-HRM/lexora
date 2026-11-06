"""Hiring D2 — offer approval chain through the approvals inbox.

Request type ``hiring_offer``, metadata {ctc, department, entity, level}. The chain comes
from the D1 no-code chain registry when it is installed; until then (or when the registry
has no chain for this type) the DEFAULT applies: hiring (reporting) manager → hr_admin.

  POST /hiring/offers/{id}/submit-approval          HR: start the chain (offer must be a draft)
  GET  /hiring/offers/{id}/approval                 steps + whether the caller may decide now
  POST /hiring/offers/{id}/approval/{decision}      approve | reject  (also the inbox callback)

Each step opens ONE item in the approvals inbox (shared.utils.approvals_client) addressed
to that step's approver; the inbox's Approve / Reject buttons call the route above with the
acting user's identity headers.
"""
import logging
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402

import hiring_d2_logic as D  # noqa: E402
import hiring_integrations as ext  # noqa: E402
import hiring_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, get_actor, now, one, opt_uid, ser, uid  # noqa: E402
from hiring_d2_models import HireOfferApprovalStep  # noqa: E402
from hiring_models import HireCandidate, HireOffer, HireRequisition  # noqa: E402
from hiring_offers import offer_out  # noqa: E402
from hiring_routers import load_settings  # noqa: E402

log = logging.getLogger("lexora.hiring.approvals")
router = APIRouter(prefix="/hiring", tags=["hiring-approvals"])

try:                                                        # the approvals inbox (registry) — best-effort helpers
    from shared.utils.approvals_client import close_item as _close_item, open_item as _open_item
except Exception:                                           # noqa: BLE001
    _open_item = _close_item = None


async def resolve_chain(org: str, metadata: dict, req: HireRequisition) -> tuple[list[dict], str]:
    """(steps, source). D1's registry is asked first — `shared.utils.approval_chains.resolve_chain(org_id,
    request_type=..., metadata=...)` returning [{kind: manager|role|user, employee_id|role|user_id, label}].
    Any failure or empty answer → the default chain. Never raises."""
    try:
        from shared.utils.approval_chains import resolve_chain as _rc  # type: ignore  (D1, may not exist yet)
        raw = await _rc(str(org), request_type=D.REQUEST_TYPE_OFFER, metadata=metadata)
        if raw:
            return D.clean_chain(raw), "registry"
    except ImportError:
        pass
    except Exception as e:                                   # noqa: BLE001
        log.warning("approval chain registry failed for hiring_offer: %s — using the default chain", e)
    return D.default_offer_chain(str(req.hiring_manager_id) if req.hiring_manager_id else None, req.hiring_manager_name), "default"


async def steps_for(db: AsyncSession, org, offer_id) -> list[HireOfferApprovalStep]:
    return list((await db.execute(select(HireOfferApprovalStep).where(
        HireOfferApprovalStep.org_id == org, HireOfferApprovalStep.offer_id == offer_id)
        .order_by(HireOfferApprovalStep.step_no))).scalars().all())


def _step_dict(s: HireOfferApprovalStep) -> dict:
    return {"kind": s.approver_kind, "employee_id": str(s.approver_employee_id) if s.approver_employee_id else None,
            "role": s.approver_role, "user_id": s.approver_user_id, "status": s.status}


async def _open_inbox(db, o: HireOffer, s: HireOfferApprovalStep, c: HireCandidate, requester: str) -> None:
    if _open_item is None:
        return
    kw = {}
    if s.approver_kind == "manager":
        kw["approver_user_ids"] = await ext.user_ids_for_employees(db, str(o.org_id), [s.approver_employee_id])
    elif s.approver_kind == "role":
        kw["approver_roles"] = [s.approver_role]
    else:
        kw["approver_user_ids"] = [s.approver_user_id]
    item = await _open_item(str(o.org_id), kind=D.REQUEST_TYPE_OFFER, ref_id=f"{o.id}:{s.step_no}",
                            title=f"Offer approval ({s.label}) — {c.first_name} {c.last_name or ''}".strip(),
                            summary=[f"{o.designation}", f"Step {s.step_no}: {s.label}", "Amounts are on the offer screen."],
                            requester_user_id=requester, link="/hiring/offers",
                            callback_path=f"/hiring/offers/{o.id}/approval/{{decision}}", **kw)
    s.inbox_item_id = item or None


async def _close_inbox(o: HireOffer, s: HireOfferApprovalStep, decision: str, actor_user_id: str) -> None:
    if _close_item is None:
        return
    await _close_item(str(o.org_id), kind=D.REQUEST_TYPE_OFFER, ref_id=f"{o.id}:{s.step_no}", decision=decision,
                      actor_user_id=actor_user_id)


@router.post("/offers/{offer_id}/submit-approval")
async def submit_approval(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can submit an offer for approval.")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if o.status != "draft":
        raise HTTPException(409, "Only a draft offer can be submitted for approval.")
    if not o.joining_date:
        raise HTTPException(422, "Set the joining date before submitting the offer for approval.")
    existing = await steps_for(db, actor.org, o.id)
    if any(s.status in ("pending", "waiting") for s in existing):
        raise HTTPException(409, "This offer is already waiting for approval.")
    for s in existing:                                      # an earlier (rejected) run is kept in the audit, steps re-created
        s.status = "skipped" if s.status in ("waiting", "pending") else s.status
    r = await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition")
    c = await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate")
    meta = {"ctc": float(o.annual_ctc), "department": o.department, "entity": None, "level": o.grade,
            "above_budget": bool(o.above_budget), "requisition_id": str(r.id)}
    chain, source = await resolve_chain(actor.org, meta, r)
    base_no = max([s.step_no for s in existing] or [0])
    rows = []
    for i, st in enumerate(chain, 1):
        row = HireOfferApprovalStep(org_id=uid(actor.org), offer_id=o.id, step_no=base_no + i, label=st["label"],
                                    approver_kind=st["kind"], approver_role=st.get("role"),
                                    approver_employee_id=opt_uid(st.get("employee_id")), approver_user_id=st.get("user_id"),
                                    status="pending" if i == 1 else "waiting", chain_source=source)
        db.add(row)
        rows.append(row)
    o.approvals_required, o.approvals, o.status_reason = len(chain), [], None
    await db.flush()
    await _open_inbox(db, o, rows[0], c, actor.user_id)
    await db.commit()
    await record_audit(request, "hiring.offer.submit_approval", entity_type="hire_offer", entity_id=str(o.id),
                       summary=f"Offer submitted for approval ({len(chain)} step(s), {source} chain)",
                       meta={"chain": [s["label"] for s in chain], "source": source, **meta})
    return await approval_view(db, actor, o)


async def approval_view(db, actor, o: HireOffer) -> dict:
    steps = await steps_for(db, actor.org, o.id)
    cur = next((s for s in steps if s.status == "pending"), None)
    is_admin = actor.role in ("org_admin", "superadmin")
    can = bool(cur) and D.step_may_decide(_step_dict(cur), actor.role, actor.user_id, actor.employee_id) \
        and (actor.user_id != o.created_by or is_admin)
    return {"offer_id": str(o.id), "status": o.status, "steps": [ser(s) for s in steps],
            "current_step": cur.step_no if cur else None, "can_decide": can,
            "chain_source": steps[-1].chain_source if steps else None, "inbox_available": _open_item is not None}


@router.get("/offers/{offer_id}/approval")
async def get_approval(offer_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """HR, or an approver named on this offer's chain (WASA 2026-10-21: no one else)."""
    actor = await get_actor(request, db)
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if not actor.is_hr and not await _named_on_chain(db, actor, o):
        raise HTTPException(404, "Offer not found")
    return await approval_view(db, actor, o)


async def _named_on_chain(db, actor, o: HireOffer) -> bool:
    """True when the caller is an approver on any step of this offer's chain."""
    for s in await steps_for(db, actor.org, o.id):
        d = _step_dict(s)
        if (d["employee_id"] and actor.employee_id and d["employee_id"] == str(actor.employee_id)) \
                or (d["user_id"] and actor.user_id and str(d["user_id"]) == str(actor.user_id)) \
                or (d["kind"] == "role" and D.step_may_decide(d, actor.role, actor.user_id, actor.employee_id)):
            return True
    return False


@router.post("/offers/{offer_id}/approval/{decision}")
async def decide(offer_id: str, decision: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    """approve | reject the current step. Called from the Offers screen and by the approvals inbox."""
    actor = await get_actor(request, db)
    body = body or {}
    decision = (body.get("decision") or decision or "").lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "Unknown decision")
    o = await one(db, HireOffer, offer_id, actor.org, "Offer")
    if o.status != "draft":
        raise HTTPException(409, f"An offer that is {o.status} is not waiting for approval.")
    steps = await steps_for(db, actor.org, o.id)
    cur = next((s for s in steps if s.status == "pending"), None)
    if cur is None:
        raise HTTPException(409, "This offer has not been submitted for approval.")
    if not D.step_may_decide(_step_dict(cur), actor.role, actor.user_id, actor.employee_id):
        raise HTTPException(403, f"This step is for: {cur.label}.")
    if actor.user_id == o.created_by and actor.role not in ("org_admin", "superadmin"):
        raise HTTPException(403, "You prepared this offer; someone else must approve it.")
    comment = (body.get("comment") or body.get("note") or "").strip()[:400]
    if decision == "reject" and not comment:
        raise HTTPException(422, "Say why the offer is rejected.")
    try:
        new_steps, offer_status = D.chain_after_decision([ser(s) for s in steps], decision)
    except D.RuleError as e:
        raise HTTPException(e.status, e.message)
    for s, ns in zip(steps, new_steps):
        s.status = ns["status"]
    cur.decided_by, cur.decided_by_name, cur.decided_at, cur.comment = actor.user_id, actor.name, now(), comment or None
    approvals = list(o.approvals or []) + [{"by": actor.user_id, "name": actor.name or actor.email, "role": actor.role,
                                            "at": now().isoformat(), "step": cur.step_no, "decision": decision}]
    if offer_status == "approved":
        await cas(db, HireOffer, o, "status", "draft", extra_where=[func.jsonb_array_length(HireOffer.approvals) == len(o.approvals or [])],
                  approvals=approvals, status="approved")
    elif offer_status == "draft":
        o.approvals, o.status_reason = approvals, f"Rejected at step {cur.step_no} ({cur.label}): {comment}"
    else:
        o.approvals = approvals
    await db.flush()
    await _close_inbox(o, cur, "approved" if decision == "approve" else "rejected", actor.user_id)
    c = await one(db, HireCandidate, o.candidate_id, actor.org, "Candidate")
    nxt = next((s for s in steps if s.status == "pending"), None)
    if nxt is not None:
        await _open_inbox(db, o, nxt, c, o.created_by or actor.user_id)
    await db.commit()
    await db.refresh(o)
    await record_audit(request, f"hiring.offer.chain_{decision}", entity_type="hire_offer", entity_id=str(o.id),
                       summary=f"Step {cur.step_no} ({cur.label}) {decision}d", meta={"comment": comment or None, "result": offer_status})
    if offer_status != "pending":
        await ext.notify(actor.org, roles=["hr"], title=("Offer approved" if offer_status == "approved" else "Offer rejected"),
                         body=f"{o.designation} · {c.first_name}", link="/hiring/offers", kind="approval")
    r = await one(db, HireRequisition, o.requisition_id, actor.org, "Requisition")
    s = await load_settings(db, actor.org)
    if not actor.is_hr:
        # a non-HR step approver (e.g. the hiring manager, from the inbox) gets the outcome only — not the
        # offer's pay figures (decision 2026-10-21: compensation stays with HR / payroll / org admin)
        return {"id": str(o.id), "status": o.status, "approval": await approval_view(db, actor, o),
                "chain_enabled": bool(s["offer_approval_chain"])}
    return {**offer_out(o, c, r, actor), "approval": await approval_view(db, actor, o), "chain_enabled": bool(s["offer_approval_chain"])}
