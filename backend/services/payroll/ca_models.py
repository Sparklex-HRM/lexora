"""Stream G6 — Canada completeness + migration kit tables (payroll service Base).

Everything here is ADDITIVE (create_all). Nothing law-derived is hard-coded in
these tables: `ca_statutory_values` holds the ORG-maintained figures (blank until
the admin enters them with a source URL + effective date); the sign-off itself
lives in `statutory_rule_verification` (rule_key = "ca.<key>").
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _org():
    return mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


class CaStatutoryValue(Base):
    """One org-maintained Canadian statutory figure (rate / threshold / due date /
    rule JSON). `value` is text; `kind` in the catalog says how to read it. Blank
    rows are never created — a missing row means 'not entered yet'."""
    __tablename__ = "ca_statutory_values"
    __table_args__ = (UniqueConstraint("org_id", "key", "effective_from", name="ux_ca_statutory_values"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    key: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(600))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, default=date(2026, 1, 1))
    note: Mapped[str | None] = mapped_column(String(600))
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CaOrgSettings(Base):
    """Org CHOICES (not law): which remitter type CRA assigned the org, etc."""
    __tablename__ = "ca_org_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    remitter_type: Mapped[str] = mapped_column(String(16), default="")        # quarterly|regular|threshold1|threshold2
    rq_remitter_type: Mapped[str] = mapped_column(String(16), default="")     # Revenu Québec frequency (org choice)
    cra_payroll_account: Mapped[str | None] = mapped_column(String(20))       # e.g. 123456789RP0001 (entered by org)
    rq_account: Mapped[str | None] = mapped_column(String(24))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CaWcbRateGroup(Base):
    """Board-assigned rate group (classification unit) with the org's premium rate
    per $100 of assessable earnings — employer-specific input, not law."""
    __tablename__ = "ca_wcb_rate_groups"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    province: Mapped[str] = mapped_column(String(2), index=True)
    group_code: Mapped[str] = mapped_column(String(24))
    description: Mapped[str | None] = mapped_column(String(160))
    rate_per_100: Mapped[float] = mapped_column(Numeric(8, 4), default=0)
    year: Mapped[int] = mapped_column(Integer, default=2026, index=True)
    account_number: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CaWcbAssignment(Base):
    """Which rate group an employee's earnings are assessed under."""
    __tablename__ = "ca_wcb_assignments"
    __table_args__ = (UniqueConstraint("org_id", "employee_id", name="ux_ca_wcb_assignment"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    rate_group_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CaContractor(Base):
    """Self-employed contractor paid alongside employees → T4A slip."""
    __tablename__ = "ca_contractors"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    code: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(160))
    business_number: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    province: Mapped[str | None] = mapped_column(String(2))
    default_box: Mapped[str] = mapped_column(String(4), default="048")   # 048 fees for services | 020 self-employed commissions
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CaContractorPayment(Base):
    __tablename__ = "ca_contractor_payments"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    contractor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    pay_date: Mapped[date] = mapped_column(Date, index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    invoice_no: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)          # fees / commission (before tax)
    gst_hst: Mapped[float] = mapped_column(Numeric(12, 2), default=0)         # tax on the invoice, NOT T4A income
    tax_withheld: Mapped[float] = mapped_column(Numeric(12, 2), default=0)    # only if a withholding was actually made
    box: Mapped[str] = mapped_column(String(4), default="048")
    status: Mapped[str] = mapped_column(String(12), default="paid")            # paid | cancelled
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CaOpeningYtd(Base):
    """Canada migration: YTD payroll per MONTH already paid by the old system this
    calendar year. Rolled up into employee_ytd on commit."""
    __tablename__ = "ca_opening_ytd"
    __table_args__ = (UniqueConstraint("org_id", "employee_id", "year", "month", name="ux_ca_opening_ytd"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)             # YYYY-MM
    gross: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    cpp: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    cpp2: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    ei: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    qpip: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    income_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    pensionable: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    insurable: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    vac_accrued: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    vac_paid: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    net: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    source: Mapped[str | None] = mapped_column(String(60))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CaImportBatch(Base):
    """Canada import batch: uploaded → validated → committed → rolled_back."""
    __tablename__ = "ca_import_batches"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor: Mapped[str] = mapped_column(String(32), default="lexora")
    file_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="uploaded", index=True)
    headers: Mapped[list | None] = mapped_column(JSONB)
    raw_rows: Mapped[list | None] = mapped_column(JSONB)
    mapping: Mapped[dict | None] = mapped_column(JSONB)
    rows: Mapped[list | None] = mapped_column(JSONB)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaImportRow(Base):
    """Ledger of every row a Canada batch touched → exact undo (newest first)."""
    __tablename__ = "ca_import_rows"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    batch_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    target_table: Mapped[str] = mapped_column(String(48), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_id: Mapped[str | None] = mapped_column(String(64))
    row_key: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(10), nullable=False)        # insert | update
    prior: Mapped[dict | None] = mapped_column(JSONB)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MigrationParallelRun(Base):
    """Parallel-run reconciliation: the incumbent's payroll register for one month
    vs Lexora HRM's stubs for the same month, line by line, with a sign-off."""
    __tablename__ = "migration_parallel_runs"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    country: Mapped[str] = mapped_column(String(2), default="CA")
    month: Mapped[str] = mapped_column(String(7), index=True)                # YYYY-MM (pay dates in this month)
    vendor: Mapped[str | None] = mapped_column(String(32))
    file_name: Mapped[str | None] = mapped_column(String(255))
    headers: Mapped[list | None] = mapped_column(JSONB)
    raw_rows: Mapped[list | None] = mapped_column(JSONB)
    mapping: Mapped[dict | None] = mapped_column(JSONB)                      # component → their column
    tolerance: Mapped[float] = mapped_column(Numeric(10, 2), default=1.0)
    result: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="uploaded")      # uploaded | compared | signed
    signed_by: Mapped[str | None] = mapped_column(String(200))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sign_note: Mapped[str | None] = mapped_column(String(600))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MigrationChecklistStep(Base):
    """Manual tick on a migration checklist step (auto steps come from counts)."""
    __tablename__ = "migration_checklist_steps"
    __table_args__ = (UniqueConstraint("org_id", "country", "step_key", name="ux_migration_checklist_step"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    country: Mapped[str] = mapped_column(String(2), default="CA")
    step_key: Mapped[str] = mapped_column(String(40), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    done_by: Mapped[str | None] = mapped_column(String(200))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MigrationImportPreset(Base):
    """Saved column mapping per org × source system × file type (the Map step's "save")."""
    __tablename__ = "migration_import_presets"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    vendor: Mapped[str] = mapped_column(String(32), default="custom")
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(80))
    mapping: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
