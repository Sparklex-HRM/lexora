"""Payroll service — pay groups, runs, stubs, and the YTD accumulator ledger."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class PayGroup(Base):
    """A set of employees paid on the same frequency + calendar."""
    __tablename__ = "pay_groups"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    pay_frequency: Mapped[str] = mapped_column(String(20), default="biweekly")  # drives the calendar
    province: Mapped[str] = mapped_column(String(2), default="ON")
    first_pay_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PayRun(Base):
    __tablename__ = "pay_runs"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    pay_group_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    pay_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|calculated|approved|paid
    total_gross: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_net: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    # Run-level facts (frequency, 27-pay year, notes).
    meta: Mapped[dict | None] = mapped_column(JSONB)
    # D4 multi-entity: the legal entity this run was executed for; NULL = all entities
    # (the stubs then carry their own entity_id and the statutory files split per entity).
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PayStub(Base):
    __tablename__ = "pay_stubs"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    pay_run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    gross: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    cpp: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    cpp2: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    ei: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    income_tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    net_pay: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)   # full calc breakdown
    # D4: the employee's legal entity when this stub was computed (drives the per-entity T4 / remittance split).
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)


class VoidedPayStub(Base):
    """A pay stub of a VOIDED test run (POST /payroll/runs/{id}/void). The live `pay_stubs`
    row is removed so every reader (YTD, payslips, T4, remittances, statements) stops
    seeing it; this copy keeps the audit trail. Never read by the engine."""
    __tablename__ = "pay_stubs_voided"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    pay_run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    stub_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))        # the original pay_stubs.id
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    gross: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    cpp: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    cpp2: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    ei: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    income_tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    net_pay: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    voided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    voided_by: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(400))


class EmployeeYtd(Base):
    """The heart of the engine — YTD running totals that also become the T4."""
    __tablename__ = "employee_ytd"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    cpp: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    cpp2: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    ei: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    pensionable: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    insurable: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    gross: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    income_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    bonus: Mapped[float] = mapped_column(Numeric(14, 2), default=0)   # YTD non-periodic taxable (bonus method stacking)
    qpip: Mapped[float] = mapped_column(Numeric(12, 2), default=0)    # Québec: QPIP premiums YTD (RL-1 box H, QPIP max)
    # Vacation-pay engine (ESA 4%/6%): accrued as a % of vacationable earnings,
    # drawn down as it's paid (VACP). Balance owed = vac_accrued − vac_paid.
    vac_accrued: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    vac_paid: Mapped[float] = mapped_column(Numeric(14, 2), default=0)


class RuleVerification(Base):
    """The 'verify before real pay' sign-off of a Canada statutory value AS DATA (rule_key "ca.<key>"). One row per rule_key per scope:
    org_id NULL = platform-wide sign-off (superadmin); an org row (payroll_admin/org_admin)
    overrides it for that org. `value_snapshot` is the stringified value that was signed
    off — if the constant later changes, the row is 'stale' and the gate re-opens."""
    __tablename__ = "statutory_rule_verification"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=True)
    rule_key: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    value_snapshot: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(16), default="verified")   # verified | estimated
    verified_by: Mapped[str | None] = mapped_column(String(160))
    verified_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(String(600))
    note: Mapped[str | None] = mapped_column(String(600))
