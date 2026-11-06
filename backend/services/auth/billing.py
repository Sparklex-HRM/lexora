"""Auth service — subscription billing (per employee per month, CAD, GST/HST by province).

Headcount rule: the active employees on the 1st of the billing month are
billed for the whole month (joiners after the 1st start next month, leavers
after the 1st are billed for their last month).
"""
import sys
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.org_time import local_today, to_local_date  # noqa: E402

from constants import PLAN_MODULES, resolve_modules  # noqa: E402
from database import get_db  # noqa: E402
from models import BillingAddon, BillingInvoice, BillingPlan, Organization  # noqa: E402
from pricing import (ADDONS, COUNTRY_META, PEPM_PLANS, PLAN_PRICING,  # noqa: E402
                     billing_anchor, currency_for, pepm_invoice)
import sales_tax_invoice  # noqa: E402

router = APIRouter(prefix="/auth/billing", tags=["billing"])

BILLING_ROLES = {"superadmin", "org_admin"}


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def _plan_dict(p: BillingPlan) -> dict:
    return {"label": p.label, "pepm": float(p.pepm), "min_monthly": float(p.min_monthly), "custom": p.custom,
            "includes": p.includes, "free_upto_employees": p.free_upto_employees, "free_months": p.free_months,
            "annual_discount_pct": float(p.annual_discount_pct or 0), "max_employees": p.max_employees,
            "sort": p.sort, "active": p.active,
            # S4 entitlements as data
            "modules": p.modules or PLAN_MODULES.get(p.plan_key), "trial_days": p.trial_days,
            "self_serve": p.self_serve is not False, "provider_plan_id": p.provider_plan_id}


def _addon_dict(a: BillingAddon) -> dict:
    return {"label": a.label, "kind": a.kind, "price": float(a.price), "waived_on_annual": a.waived_on_annual,
            "sort": a.sort, "active": a.active}


async def load_catalog(db: AsyncSession, country: str) -> tuple[dict, dict]:
    """The live (DB) plan + add-on catalogue for a country; seed constants as fallback."""
    c = "CA"
    plans = {p.plan_key: _plan_dict(p) for p in (await db.execute(
        select(BillingPlan).where(BillingPlan.country == c, BillingPlan.active.is_(True))
        .order_by(BillingPlan.sort))).scalars().all()}
    addons = {a.addon_key: _addon_dict(a) for a in (await db.execute(
        select(BillingAddon).where(BillingAddon.country == c, BillingAddon.active.is_(True))
        .order_by(BillingAddon.sort))).scalars().all()}
    return plans or PEPM_PLANS.get(c, {}), addons if addons or c not in ADDONS else ADDONS[c]


def _require(request: Request, roles=BILLING_ROLES):
    if _role(request) not in roles:
        raise HTTPException(403, "Only org admins may view billing")


async def _target_org(request: Request, db: AsyncSession, org_id: str | None = None) -> Organization:
    target = org_id if (org_id and _role(request) == "superadmin") else request.headers.get("x-org-id")
    import uuid as _uuid
    try:
        target = _uuid.UUID(str(target))
    except (ValueError, TypeError):
        raise HTTPException(404, "Organization not found")
    org = (await db.execute(select(Organization).where(Organization.id == target))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")
    return org


async def active_headcount(db: AsyncSession, org_id, anchor: date) -> int:
    """Employees on the payroll on `anchor` (hired on/before, not terminated before)."""
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM employees WHERE org_id = :o AND is_active = TRUE "
            "AND status IN ('active', 'on_leave', 'onboarding') "
            "AND (hire_date IS NULL OR hire_date <= :a) "
            "AND (termination_date IS NULL OR termination_date >= :a)"),
            {"o": org_id, "a": anchor})).scalar() or 0
    except Exception:
        n = 0
    return int(n)


async def build_preview(db: AsyncSession, org: Organization, period: date | None = None,
                        charge_one_time: bool = False) -> dict:
    anchor = billing_anchor(period)
    n = await active_headcount(db, org.id, anchor)
    plans, addons = await load_catalog(db, "CA")
    inv = pepm_invoice("CA", org.plan, n, org.addons or [], plans=plans, addon_catalog=addons,
                       pepm_override=float(org.pepm_override) if org.pepm_override is not None else None,
                       customer_province=org.billing_province or org.province, period=anchor,
                       annual=(org.billing_cycle or "monthly") == "annual",
                       org_created=to_local_date(org.created_at) if org.created_at else None,
                       charge_one_time=charge_one_time)
    inv["billing_model"] = "pepm"
    inv.update({"org_id": str(org.id), "org_name": org.name, "gst_hst_number": org.gst_hst_number,
                "billing_email": org.billing_email, "addons": org.addons or [],
                "billing_cycle": org.billing_cycle or "monthly",
                "modules": resolve_modules(org.plan, org.modules)})
    return inv


@router.get("/plans")
async def plans(country: str = "CA", db: AsyncSession = Depends(get_db)):
    """Plan catalogue (Canada): live PEPM tiers + add-ons in CAD, GST/HST rates by province."""
    c = "CA"
    pepm_plans, addons = await load_catalog(db, c)
    return {"country": c, "currency": currency_for(c), "billing_model": COUNTRY_META[c]["billing_model"],
            "pepm_plans": pepm_plans, "addons": addons,
            "flat_pricing": PLAN_PRICING[c], "plan_modules": PLAN_MODULES,
            "sales_tax_rates": sales_tax_invoice.rates(),
            "headcount_rule": "Active employees on the 1st of the month are billed for that month.",
            "catalog_source": "db"}


class PlanRow(BaseModel):
    plan_key: str
    label: str | None = None
    pepm: float | None = None
    min_monthly: float | None = None
    custom: bool | None = None
    includes: str | None = None
    free_upto_employees: int | None = None
    free_months: int | None = None
    annual_discount_pct: float | None = None
    max_employees: int | None = None
    sort: int | None = None
    active: bool | None = None
    modules: list[str] | None = None          # S4: module entitlements of the plan (data)
    trial_days: int | None = None
    self_serve: bool | None = None
    provider_plan_id: str | None = None       # payment provider's plan id (set by the operator)


class AddonRow(BaseModel):
    addon_key: str
    label: str | None = None
    kind: str | None = None
    price: float | None = None
    waived_on_annual: bool | None = None
    sort: int | None = None
    active: bool | None = None


class CatalogPatch(BaseModel):
    country: str = "CA"
    plans: list[PlanRow] = []
    addons: list[AddonRow] = []


@router.put("/catalog")
async def catalog_put(body: CatalogPatch, request: Request, db: AsyncSession = Depends(get_db)):
    """Superadmin edits the live plan / add-on numbers (upsert by key)."""
    if _role(request) != "superadmin":
        raise HTTPException(403, "Only the platform operator edits the price catalogue")
    c = body.country.upper()
    if c != "CA":
        raise HTTPException(422, "Lexora HRM is Canada-only: country must be CA")
    for pr in body.plans:
        row = (await db.execute(select(BillingPlan).where(
            BillingPlan.country == c, BillingPlan.plan_key == pr.plan_key))).scalar_one_or_none()
        if not row:
            if pr.plan_key not in PLAN_MODULES:
                raise HTTPException(422, f"plan_key must be one of {list(PLAN_MODULES)}")
            row = BillingPlan(country=c, plan_key=pr.plan_key, label=pr.label or pr.plan_key.title())
            db.add(row)
        for k, v in pr.model_dump(exclude_unset=True, exclude={"plan_key"}).items():
            if v is not None:
                if k == "modules":
                    from constants import MODULES
                    bad = [m for m in v if m not in MODULES]
                    if bad:
                        raise HTTPException(422, f"Unknown module(s) {bad}; allowed: {MODULES}")
                    v = ["core"] + [m for m in v if m != "core"]
                setattr(row, k, v)
    for ar in body.addons:
        row = (await db.execute(select(BillingAddon).where(
            BillingAddon.country == c, BillingAddon.addon_key == ar.addon_key))).scalar_one_or_none()
        if not row:
            row = BillingAddon(country=c, addon_key=ar.addon_key, label=ar.label or ar.addon_key)
            db.add(row)
        for k, v in ar.model_dump(exclude_unset=True, exclude={"addon_key"}).items():
            if v is not None:
                if k == "kind" and v not in ("pepm", "flat", "one_time"):
                    raise HTTPException(422, "kind must be pepm | flat | one_time")
                setattr(row, k, v)
    await db.commit()
    await record_audit(request, "billing.catalog.update", entity_type="catalog", entity_id=c,
                       summary=f"Price catalogue edited ({len(body.plans)} plans, {len(body.addons)} add-ons)")
    pl, ad = await load_catalog(db, c)
    return {"country": c, "pepm_plans": pl, "addons": ad}


@router.get("/preview")
async def preview(request: Request, org_id: str | None = None, period: str | None = None,
                  db: AsyncSession = Depends(get_db)):
    """Invoice preview for the caller's org (superadmin may pass org_id)."""
    _require(request)
    org = await _target_org(request, db, org_id)
    p = date.fromisoformat(period) if period else None
    return await build_preview(db, org, p)


class BillingSettings(BaseModel):
    addons: list[str] | None = None
    gst_hst_number: str | None = None    # the customer's own GST/HST registration (optional)
    billing_email: str | None = None
    billing_province: str | None = None  # decides GST vs HST and the rate
    billing_address: str | None = None   # printed on the invoice
    legal_name: str | None = None        # recipient name on the invoice
    billing_cycle: str | None = None     # monthly | annual
    plan: str | None = None              # superadmin only
    pepm_override: float | None = None   # superadmin only (negotiated rate)
    org_id: str | None = None            # superadmin only


@router.patch("/settings")
async def settings_patch(body: BillingSettings, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request)
    org = await _target_org(request, db, body.org_id)
    _, addon_catalog = await load_catalog(db, org.country)
    valid_addons = set(addon_catalog)
    if body.billing_cycle is not None:
        if body.billing_cycle not in ("monthly", "annual"):
            raise HTTPException(422, "billing_cycle must be monthly or annual")
        org.billing_cycle = body.billing_cycle
    if body.addons is not None:
        bad = [a for a in body.addons if a not in valid_addons]
        if bad:
            raise HTTPException(422, f"Unknown add-on(s): {bad}")
        org.addons = list(dict.fromkeys(body.addons))
    if body.gst_hst_number is not None:
        g = body.gst_hst_number.strip().upper().replace(" ", "")
        if g and not (len(g) == 15 and g[:9].isdigit() and g[9:11] == "RT" and g[11:].isdigit()):
            raise HTTPException(422, "GST/HST number must look like 123456789RT0001")
        org.gst_hst_number = g or None
    if body.billing_email is not None:
        org.billing_email = body.billing_email.strip() or None
    if body.billing_province is not None:
        p = body.billing_province.strip().upper()[:2]
        if p and p not in sales_tax_invoice.PROVINCE_NAMES:
            raise HTTPException(422, "Billing province must be a Canadian province or territory code (ON, BC, QC …)")
        org.billing_province = p or None
    if body.billing_address is not None:
        org.billing_address = body.billing_address.strip()[:400] or None
    if body.legal_name is not None and body.legal_name.strip():
        org.legal_name = body.legal_name.strip()[:200]
    if _role(request) == "superadmin":
        if body.plan and body.plan in PLAN_MODULES:
            org.plan = body.plan
        if "pepm_override" in body.model_fields_set:
            org.pepm_override = body.pepm_override
    await db.commit()
    await db.refresh(org)
    await record_audit(request, "billing.settings.update", entity_type="organization", entity_id=str(org.id),
                       summary="Billing settings updated", meta=body.model_dump(exclude_unset=True))
    return await build_preview(db, org)


@router.post("/invoices/generate", status_code=201)
async def generate_invoice(request: Request, org_id: str | None = None, period: str | None = None,
                           db: AsyncSession = Depends(get_db)):
    """Persist this month's invoice snapshot (idempotent per org + month). One-time
    add-ons (implementation fee) are charged on the org's FIRST invoice only."""
    _require(request)
    org = await _target_org(request, db, org_id)
    p = date.fromisoformat(period) if period else None
    row, created = await persist_invoice(db, org, p)
    if created:
        await record_audit(request, "billing.invoice.generate", entity_type="invoice", entity_id=row.number,
                           summary=f"{'Tax invoice' if row.kind == 'tax_invoice' else 'Proforma'} {row.number} "
                                   f"for {row.period}: {row.currency} {float(row.total)}")
    return _inv_out(row, created=created)


def buyer_block(org: Organization, entity: dict | None = None) -> dict:
    """The customer block on the subscription invoice. D4: when the org has a billing legal entity
    (its DEFAULT entity) its legal name, province and registered address are used; anything the
    entity leaves blank falls back to the organisation's billing fields."""
    out = {"name": org.legal_name or org.name, "gst_hst_number": org.gst_hst_number,
           "province": org.billing_province or org.province, "address": org.billing_address, "email": org.billing_email}
    if not entity:
        return out
    addr = ", ".join(x for x in (entity.get("address_line1"), entity.get("address_line2"), entity.get("city"),
                                 entity.get("province"), entity.get("postal_code")) if x)
    if (entity.get("name") or "").strip():
        out["name"] = entity["name"].strip()
    if entity.get("province") and not org.billing_province:
        out["province"] = entity["province"]
    if addr:
        out["address"] = addr
    if entity.get("email"):
        out["email"] = out["email"] or entity["email"]
    out["entity_id"] = str(entity.get("id") or "") or None
    return out


async def billing_entity(db: AsyncSession, org: Organization) -> dict | None:
    """The org's default (billing) legal entity as a plain dict, or None (single-entity / no table)."""
    try:
        from entities_models import LegalEntity
        async with db.begin_nested():   # savepoint: a missing table must not roll back the caller's work
            e = (await db.execute(select(LegalEntity).where(LegalEntity.org_id == org.id, LegalEntity.is_active.is_not(False))
                                  .order_by(LegalEntity.is_default.desc(), LegalEntity.name).limit(1))).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — legal_entities missing on an old DB → org fields only
        return None
    if not e:
        return None
    return {c: getattr(e, c, None) for c in ("id", "name", "address_line1", "address_line2", "city",
                                             "postal_code", "province", "email")}


async def buyer_for(db: AsyncSession, org: Organization) -> dict:
    return buyer_block(org, await billing_entity(db, org))


async def persist_invoice(db: AsyncSession, org: Organization, period: date | None = None):
    """Store this month's invoice (idempotent per org + month) → (row, created).

    A GST/HST document is built (sales_tax_invoice.build_document). It is an INVOICE with a
    calendar-year serial ("LX-2026-00001") only when the seller's GST/HST details are configured
    and the customer's province is known; otherwise a PRO FORMA ("PF-…")."""
    first = (await db.execute(select(func.count(BillingInvoice.id)).where(
        BillingInvoice.org_id == org.id))).scalar() == 0
    inv = await build_preview(db, org, period, charge_one_time=first)
    key = inv["period"][:7]
    existing = (await db.execute(select(BillingInvoice).where(
        BillingInvoice.org_id == org.id, BillingInvoice.period == key))).scalar_one_or_none()
    if existing:
        return existing, False
    seq = (await db.execute(text("SELECT count(*) FROM billing_invoices"))).scalar() or 0
    detail = dict(inv)
    issue = local_today()
    doc = sales_tax_invoice.build_document(number=None, issue_date=issue, buyer=await buyer_for(db, org),
                                           lines=[l for l in inv["lines"]], subtotal=inv["subtotal"],
                                           period_label=inv["period_label"], currency=inv["currency"])
    kind = doc["kind"]
    if kind == "tax_invoice":
        yr = sales_tax_invoice.invoice_year(issue)
        n_yr = (await db.execute(select(func.count(BillingInvoice.id)).where(
            BillingInvoice.number.like(f"LX-{yr}-%")))).scalar() or 0
        number = sales_tax_invoice.invoice_number(yr, n_yr + 1)
        doc["number"] = number
    else:
        number = f"PF-{key.replace('-', '')}-{seq + 1:05d}"
    detail["tax_document"] = doc
    subtotal, gst, total = doc["subtotal"], doc["gst"], doc["total"]
    row = BillingInvoice(org_id=org.id, period=key, number=number, kind=kind,
                         currency=inv["currency"], plan=org.plan, active_employees=inv.get("active_employees", 0),
                         subtotal=subtotal, gst=gst, total=total, detail=detail)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, True


def _inv_out(r: BillingInvoice, created: bool | None = None) -> dict:
    return {"id": str(r.id), "number": r.number, "period": r.period, "currency": r.currency,
            "plan": r.plan, "active_employees": r.active_employees, "subtotal": float(r.subtotal),
            "gst": float(r.gst), "total": float(r.total), "status": r.status,
            "kind": r.kind,
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
            "provider_payment_id": r.provider_payment_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "detail": r.detail, **({"created": created} if created is not None else {})}


@router.get("/invoices")
async def list_invoices(request: Request, org_id: str | None = None, db: AsyncSession = Depends(get_db)):
    _require(request)
    org = await _target_org(request, db, org_id)
    rows = (await db.execute(select(BillingInvoice).where(BillingInvoice.org_id == org.id)
                             .order_by(BillingInvoice.period.desc()))).scalars().all()
    return {"items": [_inv_out(r) for r in rows], "total": len(rows)}


@router.get("/platform-summary")
async def platform_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Superadmin: every tenant's current-month preview (MRR view)."""
    if _role(request) != "superadmin":
        raise HTTPException(403, "Platform operator only")
    orgs = (await db.execute(select(Organization).order_by(Organization.created_at.desc()))).scalars().all()
    out = []
    for o in orgs:
        p = await build_preview(db, o)
        out.append({"org_id": str(o.id), "name": o.name, "code": o.code, "country": o.country,
                    "plan": o.plan, "billing_model": p.get("billing_model"), "currency": p["currency"],
                    "active_employees": p.get("active_employees", 0), "subtotal": p["subtotal"],
                    "gst": p["gst"], "total": p["total"], "addons": o.addons or [],
                    "pepm_override": float(o.pepm_override) if o.pepm_override is not None else None})
    return {"orgs": out, "generated_at": datetime.now(timezone.utc).isoformat()}
