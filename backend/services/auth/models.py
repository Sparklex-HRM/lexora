"""Auth service — tenancy + identity models."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, false
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Short uppercase handle a tenant enters at login (superadmin is exempt).
    code: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    # Canada-first: country + primary province drive payroll compliance.
    country: Mapped[str] = mapped_column(String(2), default="CA")
    province: Mapped[str | None] = mapped_column(String(2))          # ON / BC / AB / QC ...
    cra_business_number: Mapped[str | None] = mapped_column(String(15))   # 9-digit BN
    cra_rp_account: Mapped[str | None] = mapped_column(String(20))        # ...RP0001 payroll program
    revenu_quebec_id: Mapped[str | None] = mapped_column(String(20))      # QC only
    timezone: Mapped[str] = mapped_column(String(40), default="America/Toronto")
    # Subscription (Phase 4): plan + optional explicit module override list.
    plan: Mapped[str] = mapped_column(String(20), default="growth")
    modules: Mapped[list | None] = mapped_column(JSONB)
    # PEPM billing: chosen add-on keys, negotiated per-employee rate, GST/HST detail.
    addons: Mapped[list | None] = mapped_column(JSONB)
    pepm_override: Mapped[float | None] = mapped_column(Numeric(10, 2))
    gst_hst_number: Mapped[str | None] = mapped_column(String(15))   # customer's own 123456789RT0001 (optional)
    billing_email: Mapped[str | None] = mapped_column(String(200))
    billing_province: Mapped[str | None] = mapped_column(String(2))  # decides GST vs HST + rate
    billing_cycle: Mapped[str] = mapped_column(String(10), default="monthly")   # monthly | annual (prepay −15%)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # S4 subscription state (see subscription.py). NULL status = an org created before
    # self-serve billing existed → treated as "active" (never locked out by this column).
    subscription_status: Mapped[str | None] = mapped_column(String(20))  # trialing|active|past_due|cancelled|expired
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # full access until (trial end / grace end)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool | None] = mapped_column(Boolean, default=False)
    seats: Mapped[int | None] = mapped_column(Integer)                    # employees paid for (provider quantity)
    provider: Mapped[str | None] = mapped_column(String(20))              # payment provider (none built yet)
    provider_customer_id: Mapped[str | None] = mapped_column(String(60))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(60), index=True)
    billing_address: Mapped[str | None] = mapped_column(String(400))
    signup_source: Mapped[str | None] = mapped_column(String(20))         # self_serve | platform | register_api


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organizations.id"), index=True)
    # Unique PER ORG (case-insensitive) — enforced by the `users_org_email_uk`
    # index created in database.init_db, not by a global unique constraint.
    email: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(40), default="employee")
    # Link to the employee record (in the employee service) once created.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    # Tenancy scope (Phase 1 multi-branch): "org" = all branches (org admins),
    # "branch" = only branch_id (branch admins, managers, employees).
    branch_scope: Mapped[str] = mapped_column(String(10), default="branch")
    branch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Logins for employees (stream A). `password_set` is False until the person
    # has chosen a password through an invite link — until then nobody can sign in.
    password_set: Mapped[bool] = mapped_column(Boolean, default=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(String(300))
    disabled_by: Mapped[str | None] = mapped_column(String(64))
    # Any access token issued BEFORE this instant is refused by the gateway
    # (set on disable, on password set / reset and on a role change).
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # WASA #7 (2026-10-21): someone other than the person chose this password
    # (platform "New tenant" first admin). While true every token carries
    # "pwchg": true and the gateway allows only the change-password routes.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(),
                                                       nullable=False)


class UserToken(Base):
    """Single-use, expiring link token (invite = set first password, reset).
    Only the SHA-256 of the token is stored; the raw value lives in the link."""
    __tablename__ = "user_tokens"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(12))                   # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by: Mapped[str | None] = mapped_column(String(64))
    sent_to: Mapped[str | None] = mapped_column(String(200))


class IdentitySettings(Base):
    """Per-org login settings (one row per org; absent row = defaults)."""
    __tablename__ = "identity_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    auto_login_on_onboarding: Mapped[bool] = mapped_column(Boolean, default=True)
    invite_expiry_hours: Mapped[int] = mapped_column(Integer, default=72)
    reset_expiry_minutes: Mapped[int] = mapped_column(Integer, default=60)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(64))


class LoginSession(Base):
    """One row per sign-in attempt (success or failure) — the session audit screen."""
    __tablename__ = "login_sessions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    email: Mapped[str | None] = mapped_column(String(200), index=True)
    role: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str | None] = mapped_column(String(80))          # bad_password | locked | bad_org_code …
    jti: Mapped[str | None] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class BillingPlan(Base):
    """Superadmin-editable PEPM plan row (seeded from pricing.PEPM_PLANS)."""
    __tablename__ = "billing_plan_catalog"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country: Mapped[str] = mapped_column(String(2), index=True)
    plan_key: Mapped[str] = mapped_column(String(20), index=True)
    label: Mapped[str] = mapped_column(String(60))
    pepm: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    min_monthly: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    custom: Mapped[bool] = mapped_column(Boolean, default=False)
    includes: Mapped[str | None] = mapped_column(String(400))
    free_upto_employees: Mapped[int] = mapped_column(Integer, default=0)
    free_months: Mapped[int] = mapped_column(Integer, default=0)
    annual_discount_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    max_employees: Mapped[int | None] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer, default=99)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    # S4: entitlements as DATA. modules NULL → constants.PLAN_MODULES fallback.
    modules: Mapped[list | None] = mapped_column(JSONB)
    trial_days: Mapped[int | None] = mapped_column(Integer)
    self_serve: Mapped[bool | None] = mapped_column(Boolean, default=True)        # offered on the public sign-up
    provider_plan_id: Mapped[str | None] = mapped_column(String(60))             # payment provider plan id (set by operator)


class BillingAddon(Base):
    """Superadmin-editable add-on row (seeded from pricing.ADDONS)."""
    __tablename__ = "billing_addon_catalog"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country: Mapped[str] = mapped_column(String(2), index=True)
    addon_key: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="flat")     # pepm | flat | one_time
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    waived_on_annual: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=99)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class BillingInvoice(Base):
    """A generated PEPM invoice snapshot (one per org per month, idempotent)."""
    __tablename__ = "billing_invoices"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(7), index=True)            # YYYY-MM
    number: Mapped[str | None] = mapped_column(String(30))
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    plan: Mapped[str | None] = mapped_column(String(20))
    active_employees: Mapped[int] = mapped_column(Integer, default=0)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    gst: Mapped[float] = mapped_column(Numeric(12, 2), default=0)        # GST or HST charged (CAD)
    total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="issued")    # issued | paid | void
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # GST/HST document kind — "tax_invoice" only when the seller's GST/HST details are
    # configured; otherwise "proforma" (never presented as a tax invoice).
    kind: Mapped[str | None] = mapped_column(String(16))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_payment_id: Mapped[str | None] = mapped_column(String(60))
    provider_invoice_id: Mapped[str | None] = mapped_column(String(60))
