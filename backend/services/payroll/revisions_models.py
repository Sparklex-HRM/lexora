"""Salary revisions (hikes) — tables (payroll service, row-level org_id).

comp_revisions            one proposed salary change for one employee
comp_revision_batches     a bulk revision (filter / sheet / appraisal cycle)
comp_revision_settings    org approval settings (two-step threshold)
retro_pay_proposals       Canada: back-dated difference proposed as a RETRO earning
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class CompRevision(Base):
    """draft → submitted → approved → applied   (or rejected / cancelled).
    Only an APPROVED revision is ever applied, and only once its effective date has
    arrived. An applied revision is never edited: it is reversed by a new revision
    (`corrects_revision_id`)."""
    __tablename__ = "comp_revisions"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    country: Mapped[str] = mapped_column(String(2), default="CA")
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    pay_type: Mapped[str] = mapped_column(String(20), default="salary")            # salary | hourly
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    mode: Mapped[str] = mapped_column(String(12), default="new_annual")            # new_annual | percent | amount
    input_value: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    old_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)           # annual salary, or hourly rate
    new_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    old_components: Mapped[dict | None] = mapped_column(JSONB)                     # monthly {CODE: amount}
    new_components: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(30), default="annual_increment")
    note: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20), default="manual")              # manual | bulk | sheet | appraisal
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    needs_two_step: Mapped[bool] = mapped_column(Boolean, default=False)
    approvals: Mapped[list | None] = mapped_column(JSONB)                          # [{by, by_id, role, at}]
    corrects_revision_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(400))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apply_result: Mapped[dict | None] = mapped_column(JSONB)                       # what applying it did
    letter_id: Mapped[str | None] = mapped_column(String(40))


class CompRevisionBatch(Base):
    __tablename__ = "comp_revision_batches"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(40), default="bulk")               # bulk | sheet | appraisal:<cycle> | …
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft|submitted|approved|rejected|cancelled
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CompRevisionSettings(Base):
    __tablename__ = "comp_revision_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    two_step_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_step_percent: Mapped[float | None] = mapped_column(Numeric(7, 2))          # hike % at or above → 2 approvals
    two_step_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))          # annual increase at or above → 2 approvals
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RetroPayProposal(Base):
    """Canada. The pay difference for periods ALREADY paid before a back-dated revision
    was applied. It is only a proposed earning: HR accepts it and it then rides the next
    regular run as a `RETRO` earning line (the pay-component master decides how RETRO is
    taxed — nothing about CPP / EI / tax is decided here), or HR dismisses it."""
    __tablename__ = "retro_pay_proposals"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(24), default="RETRO")
    label: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    lines: Mapped[list | None] = mapped_column(JSONB)                              # per paid period
    status: Mapped[str] = mapped_column(String(12), default="proposed", index=True)  # proposed|accepted|paid|dismissed
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_period_end: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
