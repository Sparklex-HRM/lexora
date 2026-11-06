"""Leave service — fiscal-year balances + request/approval lifecycle.

A LeaveBalance is an employee's entitlement for one leave type in one fiscal
year (allocated + carried-over, drawn down by used/pending). A LeaveRequest is
routed to the employee's manager (manager_id) and, once approved, decrements the
matching balance.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class LeaveBalance(Base):
    """One leave type's entitlement for an employee in a fiscal year."""
    __tablename__ = "leave_balances"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True)      # e.g. 2026
    leave_type_code: Mapped[str] = mapped_column(String(20), index=True)
    leave_type_name: Mapped[str | None] = mapped_column(String(100))
    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    allocated: Mapped[float] = mapped_column(Numeric(6, 2), default=0)     # this year's grant (days)
    carried_over: Mapped[float] = mapped_column(Numeric(6, 2), default=0)  # from last year (≤ carryover_max)
    used: Mapped[float] = mapped_column(Numeric(6, 2), default=0)          # approved & taken
    pending: Mapped[float] = mapped_column(Numeric(6, 2), default=0)       # submitted, awaiting approval
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class LeaveRequest(Base):
    """A leave application: employee → manager approval → balance decrement."""
    __tablename__ = "leave_requests"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True)
    leave_type_code: Mapped[str] = mapped_column(String(20))
    leave_type_name: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date)
    days: Mapped[float] = mapped_column(Numeric(5, 2), default=0)          # working days (half-day aware)
    half_day: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    # pending -> approved | rejected | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    reviewed_by_name: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # 2026-10-16 employee portal (database._ALTERS)
    half_session: Mapped[str | None] = mapped_column(String(10))            # first | second (half_day only)
    cc_employee_ids: Mapped[list | None] = mapped_column(JSONB)             # colleagues told about the leave
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    contact_note: Mapped[str | None] = mapped_column(String(300))
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))   # employee_documents.id
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
