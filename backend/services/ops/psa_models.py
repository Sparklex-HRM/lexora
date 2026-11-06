"""Stream G2 — Project Services (PSA) tables. All NEW; the existing ops resources
(clients, projects, invoices) are reused, never changed. Columns PSA needs on the
existing `ops_invoices` row are added with ADD COLUMN IF NOT EXISTS (see INVOICE_COLUMNS).
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


def _org():
    return mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


class RateCard(Base):
    """What a client is charged for an hour / day of work. The most specific card wins:
    employee+project > employee > project+role > project > client+role > client > role."""
    __tablename__ = "psa_rate_cards"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    client: Mapped[str | None] = mapped_column(String(180), index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), index=True)
    project_name: Mapped[str | None] = mapped_column(String(180))
    role: Mapped[str | None] = mapped_column(String(120))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    rate: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    unit: Mapped[str] = mapped_column(String(6), default="hour")          # hour | day
    currency: Mapped[str] = mapped_column(String(3), default="")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Assignment(Base):
    """An employee on a project for a date range with a % allocation."""
    __tablename__ = "psa_assignments"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    department: Mapped[str | None] = mapped_column(String(120))
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    project_name: Mapped[str | None] = mapped_column(String(180))
    client: Mapped[str | None] = mapped_column(String(180))
    role: Mapped[str | None] = mapped_column(String(120))
    allocation_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=100)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InvoiceLine(Base):
    """A line of an `ops_invoices` row. T&M lines carry the timesheet line ids they bill."""
    __tablename__ = "psa_invoice_lines"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    invoice_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), default="tm")            # tm | milestone | retainer | other
    description: Mapped[str] = mapped_column(String(300))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    employee_name: Mapped[str | None] = mapped_column(String(200))
    project_id: Mapped[str | None] = mapped_column(String(64))
    project_name: Mapped[str | None] = mapped_column(String(180))
    hours: Mapped[float] = mapped_column(Numeric(9, 2), default=0)
    unit: Mapped[str | None] = mapped_column(String(6))
    rate: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="")
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    line_ids: Mapped[list] = mapped_column(JSONB, default=list)            # timesheet line ids billed here
    missing_rate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FxRate(Base):
    """A manually entered rate: 1 unit of `currency` = `rate_to_home` of the org's home currency."""
    __tablename__ = "psa_fx_rates"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    currency: Mapped[str] = mapped_column(String(3))
    rate_to_home: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Added to the EXISTING invoice resource (additive; the generic /ops/invoices routes ignore them).
INVOICE_COLUMNS = [
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS currency VARCHAR(3)",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(14,2) DEFAULT 0",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS paid_on DATE",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS period_start DATE",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS period_end DATE",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS invoice_type VARCHAR(12)",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS note VARCHAR(400)",
    "ALTER TABLE ops_invoices ADD COLUMN IF NOT EXISTS created_by VARCHAR(200)",
]
