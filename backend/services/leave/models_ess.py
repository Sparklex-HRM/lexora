"""Employee portal (2026-10-16) — leave tables (create_all at boot; migration 0007).

leave_optional_picks   an employee's pick of an OPTIONAL (restricted) holiday → approval chain
leave_comp_offs        a compensatory-off request for a worked holiday / weekly off (checked against
                       punches) → approval → credits the COMP balance with an expiry date
leave_ess_policy       company policy (per org, HR edits): optional holidays per year, comp-off hours and expiry

New columns on leave_requests (half session, CC, contact, attachment, cancellation request) are added
by database._ALTERS (ADD COLUMN IF NOT EXISTS).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class OptionalHolidayPick(Base):
    __tablename__ = "leave_optional_picks"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    holiday_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    holiday_date: Mapped[date] = mapped_column(Date, index=True)
    holiday_name: Mapped[str | None] = mapped_column(String(150))
    year: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)   # pending|approved|rejected|cancelled
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CompOff(Base):
    __tablename__ = "leave_comp_offs"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    worked_date: Mapped[date] = mapped_column(Date, index=True)
    day_kind: Mapped[str | None] = mapped_column(String(20))              # holiday | weekly_off
    day_label: Mapped[str | None] = mapped_column(String(150))
    hours_worked: Mapped[float] = mapped_column(Numeric(5, 2), default=0)  # from the punches
    first_in: Mapped[str | None] = mapped_column(String(5))
    last_out: Mapped[str | None] = mapped_column(String(5))
    days: Mapped[float] = mapped_column(Numeric(4, 2), default=0)          # 1 or 0.5 under company policy
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)   # pending|approved|rejected|cancelled|expired
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    expires_on: Mapped[date | None] = mapped_column(Date, index=True)
    credited: Mapped[bool] = mapped_column(Boolean, default=False)
    expired_days: Mapped[float] = mapped_column(Numeric(4, 2), default=0)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LeaveEssPolicy(Base):
    __tablename__ = "leave_ess_policy"
    __table_args__ = (UniqueConstraint("org_id", name="uq_leave_ess_policy_org"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    optional_holidays_per_year: Mapped[int] = mapped_column(Integer, default=2)
    comp_off_full_day_hours: Mapped[float] = mapped_column(Numeric(4, 2), default=8)
    comp_off_half_day_hours: Mapped[float] = mapped_column(Numeric(4, 2), default=4)
    comp_off_expiry_days: Mapped[int] = mapped_column(Integer, default=90)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
