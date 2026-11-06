"""Onboarding service — session lifecycle + document vault + consent log.

A session is the whole new-hire intake: HR seeds the position/pay, the hire
self-serves the personal/tax/banking detail through a token link, HR reviews the
documents, and activation promotes the collected `data` into an Employee record.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    # Secret link token — the only credential the self-service flow needs.
    invite_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # invited -> submitted -> approved -> activated  (or cancelled)
    status: Mapped[str] = mapped_column(String(20), default="invited", index=True)

    # Preview identity (also seeded into `data`)
    employee_number: Mapped[str | None] = mapped_column(String(30))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(120))
    # Work country — always CA (Canada-only); the province decides payroll rules.
    country: Mapped[str | None] = mapped_column(String(2))
    review_note: Mapped[str | None] = mapped_column(String(500))       # HR's note when sending it back

    # Wizard progress
    current_step: Mapped[str] = mapped_column(String(30), default="identity")
    steps_completed: Mapped[list] = mapped_column(JSONB, default=list)
    # Flat collected payload, keyed by Employee field names.
    data: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Lifecycle audit
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnboardingDocument(Base):
    __tablename__ = "onboarding_documents"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    # sin_proof | work_permit | void_cheque | offer_letter | td1_federal | td1_provincial | photo_id | credential | resume
    doc_type: Mapped[str] = mapped_column(String(40))
    file_name: Mapped[str | None] = mapped_column(String(255))
    storage_url: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OnboardingConsent(Base):
    __tablename__ = "onboarding_consents"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    # privacy_pipeda | direct_deposit | code_of_conduct | ohsa | harassment | aoda | e_paystub | background_check | offer_acceptance
    consent_type: Mapped[str] = mapped_column(String(50))
    version: Mapped[str | None] = mapped_column(String(20))
    signed_name: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(45))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OnboardingTaskTemplate(Base):
    """The org's editable joining checklist (IT asset, ID card, induction …).
    Each new joiner gets a copy of the active rows as their own task list."""
    __tablename__ = "onboarding_task_templates"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(120))            # who does it: IT, Admin, HR, Manager …
    due_offset_days: Mapped[int] = mapped_column(Integer, default=0)  # days from the joining date (− = before)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OnboardingTask(Base):
    """One checklist line for one joiner: owner + due date + done."""
    __tablename__ = "onboarding_tasks"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[str | None] = mapped_column(String(10))          # YYYY-MM-DD
    sort: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
