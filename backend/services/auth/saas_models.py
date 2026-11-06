"""Auth service — SaaS / account-security tables (stream S4).

All NEW tables (create_all makes them); new columns on existing tables are added
by saas_db.init_saas() with ADD COLUMN IF NOT EXISTS. Nothing is dropped.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class SignupRequest(Base):
    """A self-serve sign-up waiting for its e-mail to be verified. Only the
    SHA-256 of the verification token is stored; the org is created on verify."""
    __tablename__ = "signup_requests"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(200), index=True)
    org_name: Mapped[str] = mapped_column(String(200))
    admin_first_name: Mapped[str | None] = mapped_column(String(100))
    admin_last_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(2))                     # province / territory code (ON, BC, QC …)
    plan: Mapped[str] = mapped_column(String(20), default="growth")
    employees_estimate: Mapped[int | None] = mapped_column(Integer)
    hashed_password: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    accepted_terms_version: Mapped[str | None] = mapped_column(String(20))
    addons: Mapped[list | None] = mapped_column(JSONB)                # add-on keys picked at sign-up (e.g. "agentic"), copied to the org on verify
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSession(Base):
    """One signed-in session (one access token family). The access token carries
    its id as the `sid` claim; the gateway refuses a token whose session row is
    revoked. Rows are the 'Where you are signed in' list."""
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    method: Mapped[str] = mapped_column(String(20), default="password")   # password | mfa | sso | signup
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    device: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(64))
    revoked_reason: Mapped[str | None] = mapped_column(String(80))


class UserMfa(Base):
    """TOTP factor of one login. secret_enc = AES-GCM token (shared.utils.crypto)."""
    __tablename__ = "user_mfa"
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    secret_enc: Mapped[str | None] = mapped_column(String)
    pending_secret_enc: Mapped[str | None] = mapped_column(String)      # set-up started, not confirmed
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_step: Mapped[int | None] = mapped_column(Integer)              # replay guard
    recovery_hashes: Mapped[list | None] = mapped_column(JSONB)
    recovery_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_by: Mapped[str | None] = mapped_column(String(64))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordHistory(Base):
    """Previous password hashes of a login (newest first) for the reuse rule."""
    __tablename__ = "password_history"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class BillingEvent(Base):
    """Every payment-provider webhook received (idempotency + evidence).
    `event_id` is the provider's event id (no payment provider is built yet)."""
    __tablename__ = "billing_events"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(20), default="none")
    event_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(60))
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[str | None] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionChange(Base):
    """History of plan / status changes (trial → active → past_due …)."""
    __tablename__ = "subscription_changes"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    from_plan: Mapped[str | None] = mapped_column(String(20))
    to_plan: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str | None] = mapped_column(String(40))        # signup | admin | webhook | expiry …
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
