"""Auth service — subscription lifecycle: status, plan change, cancel, GST/HST invoice PDF.

    GET  /auth/billing/subscription          status, trial, seats vs limit, provider + seller config state
    POST /auth/billing/change-plan           {plan}             (org admin)
    POST /auth/billing/checkout              {plan?}            → 503: online card payment is not built (pay by invoice)
    POST /auth/billing/cancel                {at_period_end}
    GET  /auth/billing/history               subscription changes of my org
    GET  /auth/billing/invoices/{id}/pdf     GST/HST invoice / pro forma PDF
    GET  /auth/billing/events                superadmin: payment-event log (empty until a provider exists)
    POST /auth/billing/expire-trials         superadmin: flip ended trials to 'expired'
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.org_time import local_today, to_local_date  # noqa: E402

import billing_provider as bp  # noqa: E402
import sales_tax_invoice  # noqa: E402
import subscription  # noqa: E402
from billing import BILLING_ROLES, _require, _target_org, buyer_for, load_catalog  # noqa: E402
from database import get_db  # noqa: E402
from models import BillingInvoice, BillingPlan, Organization  # noqa: E402
from saas_models import BillingEvent, SubscriptionChange  # noqa: E402

router = APIRouter(prefix="/auth/billing", tags=["subscription"])

# WASA 2026-10-21: the payment provider's own error text is logged with a short
# reference, never sent to the browser.
from http_errors import failure  # noqa: E402
PROVIDER_FAILED = ("The payment provider could not complete this just now. Nothing was changed. "
                   "Try again in a few minutes; if it keeps failing, give this reference to Lexora HRM support.")


def _now():
    return datetime.now(timezone.utc)


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or "system"


def _ts(unix) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc) if unix else None
    except (TypeError, ValueError):
        return None


@router.get("/subscription")
async def get_subscription_state(request: Request, org_id: str | None = None, db: AsyncSession = Depends(get_db)):
    _require(request)
    org = await _target_org(request, db, org_id)
    limit = await subscription.employee_limit(db, org)
    count = await subscription.employee_count(db, org.id)
    prov = bp.get_provider()
    seller = sales_tax_invoice.seller()
    plans, _ = await load_catalog(db, org.country)
    return {"org_id": str(org.id), "plan": org.plan, "plans": plans,
            "status": subscription.effective_status(org.subscription_status, org.access_until),
            "stored_status": org.subscription_status,
            "mode": subscription.access_mode(org.subscription_status, org.access_until),
            "banner": subscription.banner(org.subscription_status, org.access_until),
            "trial_ends_at": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
            "access_until": org.access_until.isoformat() if org.access_until else None,
            "current_period_end": org.current_period_end.isoformat() if org.current_period_end else None,
            "cancel_at_period_end": bool(org.cancel_at_period_end),
            "employees": count, "employee_limit": limit, "seats": org.seats,
            "modules": await subscription.org_modules(db, org),
            "provider": {**prov.public_info(), "subscription_id": org.provider_subscription_id},
            "seller_tax": {"configured": seller["configured"],
                           **({"missing": seller["missing"]} if request.headers.get("x-role") == "superadmin" else {})},
            "invoice_kind": "tax_invoice" if seller["configured"] else "proforma",
            "buyer": await buyer_for(db, org)}


class PlanIn(BaseModel):
    plan: str
    org_id: str | None = None


async def _check_plan(db: AsyncSession, org: Organization, plan: str) -> BillingPlan:
    row = await subscription.plan_row(db, org.country, plan)
    if row is None or not row.active:
        raise HTTPException(422, "That plan is not offered")
    limit = row.max_employees
    n = await subscription.employee_count(db, org.id)
    if limit is not None and n > limit:
        raise HTTPException(409, f"The {row.label} plan allows {limit} employees; you have {n}. "
                                 "Choose a larger plan or exit employees first.")
    return row


@router.post("/change-plan")
async def change_plan(body: PlanIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request)
    org = await _target_org(request, db, body.org_id)
    if body.plan == org.plan:
        return {"ok": True, "plan": org.plan, "changed": False}
    row = await _check_plan(db, org, body.plan)
    if row.custom and request.headers.get("x-role") != "superadmin":
        raise HTTPException(409, f"{row.label} is quoted — contact Lexora HRM sales to move to it")
    prov = bp.get_provider()
    before = (org.subscription_status, org.plan)
    if org.provider_subscription_id and prov.configured:
        if not row.provider_plan_id:
            raise HTTPException(409, f"{row.label} is not linked to an online-payment plan yet — contact Lexora HRM")
        try:
            await prov.update_subscription(org.provider_subscription_id, plan_id=row.provider_plan_id,
                                           quantity=org.seats or None)
        except bp.ProviderError as exc:
            raise failure(502, PROVIDER_FAILED, exc, "billing.change_plan")
    org.plan = body.plan
    await subscription.record_change(db, org, from_status=before[0], from_plan=before[1], source="plan_change",
                                     actor=_actor(request))
    await db.commit()
    await record_audit(request, "billing.plan.change", entity_type="organization", entity_id=str(org.id),
                       summary=f"Plan changed {before[1]} → {org.plan}",
                       meta={"from": before[1], "to": org.plan, "provider": bool(org.provider_subscription_id)})
    return {"ok": True, "plan": org.plan, "changed": True,
            "note": "New modules apply from the next sign-in of each user."}


class CheckoutIn(BaseModel):
    plan: str | None = None
    org_id: str | None = None


@router.post("/checkout")
async def checkout(body: CheckoutIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Online card payment is not built in Lexora HRM (no Canadian processor adapter yet):
    always 503 `billing_not_configured`; customers pay the monthly invoice by EFT / cheque."""
    _require(request)
    await _target_org(request, db, body.org_id)
    return JSONResponse({"detail": "billing_not_configured",
                         "message": "Online payment is not set up on this server. Pay the monthly invoice by "
                                    "EFT or cheque, or contact Lexora HRM."}, status_code=503)


class CancelIn(BaseModel):
    at_period_end: bool = True
    org_id: str | None = None


@router.post("/cancel")
async def cancel(body: CancelIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request)
    org = await _target_org(request, db, body.org_id)
    prov = bp.get_provider()
    before = (org.subscription_status, org.plan)
    if org.provider_subscription_id and prov.configured:
        try:
            await prov.cancel_subscription(org.provider_subscription_id, at_cycle_end=body.at_period_end)
        except bp.ProviderError as exc:
            raise failure(502, PROVIDER_FAILED, exc, "billing.cancel")
    if body.at_period_end and org.provider_subscription_id:
        org.cancel_at_period_end = True          # the webhook flips the status when it ends
    else:
        subscription.mark_cancelled(org)
    await subscription.record_change(db, org, from_status=before[0], from_plan=before[1], source="cancel",
                                     actor=_actor(request), note="at period end" if body.at_period_end else "now")
    await db.commit()
    await record_audit(request, "billing.cancel", entity_type="organization", entity_id=str(org.id),
                       summary="Subscription cancelled" + (" at period end" if body.at_period_end else ""))
    return {"ok": True, "status": org.subscription_status, "cancel_at_period_end": bool(org.cancel_at_period_end)}


@router.get("/history")
async def history(request: Request, org_id: str | None = None, db: AsyncSession = Depends(get_db)):
    _require(request)
    org = await _target_org(request, db, org_id)
    rows = (await db.execute(select(SubscriptionChange).where(SubscriptionChange.org_id == org.id)
                             .order_by(SubscriptionChange.at.desc()).limit(200))).scalars().all()
    return {"items": [{"at": r.at.isoformat() if r.at else None, "from_status": r.from_status,
                       "to_status": r.to_status, "from_plan": r.from_plan, "to_plan": r.to_plan,
                       "source": r.source, "note": r.note, "actor": r.actor} for r in rows]}


@router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request)
    import uuid as _uuid
    try:
        row = (await db.execute(select(BillingInvoice).where(BillingInvoice.id == _uuid.UUID(invoice_id)))).scalar_one_or_none()
    except ValueError:
        row = None
    if not row or (request.headers.get("x-role") != "superadmin" and str(row.org_id) != request.headers.get("x-org-id")):
        raise HTTPException(404, "Invoice not found")
    doc = (row.detail or {}).get("tax_document")
    if not doc:                                    # no stored document → re-rendered as a pro forma
        org = (await db.execute(select(Organization).where(Organization.id == row.org_id))).scalar_one()
        d = row.detail or {}
        doc = sales_tax_invoice.build_document(number=None, issue_date=to_local_date(row.created_at) if row.created_at else local_today(),
                                         buyer=await buyer_for(db, org), lines=d.get("lines") or [],
                                         subtotal=float(row.subtotal), period_label=d.get("period_label") or row.period)
        doc["notes"] = list(doc.get("notes") or []) + [f"Re-rendered from an older stored invoice {row.number}."]
    pdf = sales_tax_invoice.render_pdf(doc)
    name = (row.number or "invoice").replace("/", "-")
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.get("/events")
async def events(request: Request, db: AsyncSession = Depends(get_db)):
    if request.headers.get("x-role") != "superadmin":
        raise HTTPException(403, "Platform operator only")
    rows = (await db.execute(select(BillingEvent).order_by(BillingEvent.received_at.desc()).limit(200))).scalars().all()
    return {"items": [{"event_id": r.event_id, "event_type": r.event_type, "org_id": str(r.org_id) if r.org_id else None,
                       "result": r.result, "received_at": r.received_at.isoformat() if r.received_at else None}
                      for r in rows]}


@router.post("/expire-trials")
async def expire_trials(request: Request, db: AsyncSession = Depends(get_db)):
    from shared.utils.internal import is_internal
    if request.headers.get("x-role") != "superadmin" and not is_internal(request.headers):
        raise HTTPException(403, "Platform operator only")
    return {"expired": await subscription.expire_trials(db)}


__all__ = ["router", "BILLING_ROLES"]
