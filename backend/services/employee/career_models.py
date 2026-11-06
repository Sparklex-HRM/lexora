"""Career changes + probation — tables (employee service, row-level org_id)."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class CareerChange(Base):
    """A promotion / transfer / re-designation / manager, location, department or grade
    change AS A RECORD. old_values are captured when it is created; new_values reach the
    employee record only when it is approved AND its effective date has arrived.
    An applied record is never edited — it is reversed by a new record."""
    __tablename__ = "career_changes"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    change_type: Mapped[str] = mapped_column(String(24), index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    old_values: Mapped[dict] = mapped_column(JSONB, default=dict)
    new_values: Mapped[dict] = mapped_column(JSONB, default=dict)
    reason: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    linked_revision_id: Mapped[str | None] = mapped_column(String(40))     # payroll salary revision
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    # draft | submitted | approved | rejected | cancelled | applied
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(400))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    letter_id: Mapped[str | None] = mapped_column(String(40))


class CareerSnapshot(Base):
    """Last known designation / department / grade / manager / location of an employee.
    If the record later differs and no career change explains it, the difference was
    typed straight onto the employee form — it is then written to the history as an
    applied record marked 'edited directly', so nothing changes silently."""
    __tablename__ = "career_snapshots"
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProbationAction(Base):
    """Every step taken on an employee's probation: manager recommendation, confirm,
    extend, do-not-confirm. Append-only."""
    __tablename__ = "probation_actions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(20))            # recommend | confirm | extend | do_not_confirm | assign
    recommendation: Mapped[str | None] = mapped_column(String(20))   # confirm | extend | do_not_confirm
    old_end_date: Mapped[date | None] = mapped_column(Date)
    new_end_date: Mapped[date | None] = mapped_column(Date)
    confirmation_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(200))
    actor_role: Mapped[str | None] = mapped_column(String(40))
    letter_id: Mapped[str | None] = mapped_column(String(40))
    exit_handover: Mapped[str | None] = mapped_column(String(200))   # result of the exit hand-over
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LifecycleSettings(Base):
    """Org settings for probation (one row per org)."""
    __tablename__ = "lifecycle_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    probation_months: Mapped[int] = mapped_column(Integer, default=6)
    probation_rules: Mapped[list | None] = mapped_column(JSONB)   # [{grade?, employment_type?, months}]
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
