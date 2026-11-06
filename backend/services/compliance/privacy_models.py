"""Compliance service — privacy centre tables (PIPEDA, Canada).

The employer org is the organization accountable for its employees' personal information
(PIPEDA Schedule 1, Principle 4.1); Lexora HRM is its service provider and processes the
information only for the employer (Principle 4.1.3). Québec employers also have Law 25
duties (CAI); the same registers serve both.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class PrivacySettings(Base):
    """Per-org Privacy Officer (the individual accountable, PIPEDA Principle 4.1) + complaints
    contact + notice / service-provider-terms versions. Column names keep `dpo_*` = Privacy Officer."""
    __tablename__ = "privacy_settings"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, index=True, nullable=False)
    dpo_name: Mapped[str | None] = mapped_column(String(200))
    dpo_email: Mapped[str | None] = mapped_column(String(200))
    dpo_phone: Mapped[str | None] = mapped_column(String(40))
    grievance_officer_name: Mapped[str | None] = mapped_column(String(200))
    grievance_officer_email: Mapped[str | None] = mapped_column(String(200))
    processor_contract_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processor_contract_accepted_by: Mapped[str | None] = mapped_column(String(200))
    processor_contract_version: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PrivacyNotice(Base):
    """Versioned privacy notice (Principle 4.8 openness / 4.2 identifying purposes) — org-editable text."""
    __tablename__ = "privacy_notices"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200), default="Employee privacy notice")
    body: Mapped[str] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    published_by: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConsentRecord(Base):
    """One consent per employee per purpose (Principle 4.3) — given / withdrawn, with the
    notice version the employee saw. Withdrawal keeps the row (audit) and flips
    status; a new 'given' row supersedes it."""
    __tablename__ = "privacy_consents"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(40), index=True)   # payroll_processing | government_reporting | benefits | background_check
    status: Mapped[str] = mapped_column(String(12), default="given", index=True)   # given | withdrawn
    notice_version: Mapped[int | None] = mapped_column(Integer)
    channel: Mapped[str | None] = mapped_column(String(30))        # self_service | onboarding | hr_recorded
    signed_name: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(64))
    given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdraw_reason: Mapped[str | None] = mapped_column(String(300))


class DataRequest(Base):
    """Individual's request (PIPEDA Principle 4.9 / s.8): access / correction / deletion /
    representative. SLA: answered within 30 days (s.8(3)), extendable by 30 (s.8(4)) — `due_at`."""
    __tablename__ = "privacy_requests"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    ref: Mapped[str | None] = mapped_column(String(30), index=True)          # PRQ-2026-001
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    request_type: Mapped[str] = mapped_column(String(20), index=True)       # access | correction | erasure | nomination
    details: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)                     # correction fields / nominee
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)   # open | in_progress | on_hold | completed | rejected
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[str | None] = mapped_column(String(200))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    hold_reason: Mapped[str | None] = mapped_column(String(400))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class BreachRecord(Base):
    """Breach-of-security-safeguards register (PIPEDA s.10.3: keep a record of EVERY breach for
    24 months) + the report / notify checklist (s.10.1: real risk of significant harm)."""
    __tablename__ = "privacy_breaches"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    ref: Mapped[str | None] = mapped_column(String(30), index=True)          # BR-2026-001
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(Text)
    data_categories: Mapped[list | None] = mapped_column(JSONB)              # ["sin", "bank", "salary", ...]
    individuals_affected: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(12), default="medium")     # low | medium | high | critical
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)   # open | contained | notified | closed
    commissioner_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    individuals_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checklist: Mapped[list | None] = mapped_column(JSONB)                    # [{key, label, done, at}]
    reported_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RetentionPolicy(Base):
    """Per-org retention per record type; purge job reads these."""
    __tablename__ = "privacy_retention_policies"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), index=True)
    retain_years: Mapped[int] = mapped_column(Integer, default=8)
    legal_basis: Mapped[str | None] = mapped_column(String(400))
    action: Mapped[str] = mapped_column(String(16), default="anonymise")    # anonymise | delete | review
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    # S4: a default period stays "needs confirmation" until the org (its counsel / CA)
    # confirms it — the defaults are a starting point, not legal advice.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(200))


class PurgeRun(Base):
    """Result of a scheduled / manual retention run (dry-run by default)."""
    __tablename__ = "privacy_purge_runs"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered_by: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[dict | None] = mapped_column(JSONB)
