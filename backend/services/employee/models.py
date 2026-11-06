"""Employee service — the worker record (compensation + pay frequency + tax profile)."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_number: Mapped[str] = mapped_column(String(30), index=True)

    # Personal
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    sin_encrypted: Mapped[str | None] = mapped_column(String)         # column-crypto in Phase 2
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    pronouns: Mapped[str | None] = mapped_column(String(30))
    phone: Mapped[str | None] = mapped_column(String(30))

    # Canadian work eligibility (SIN 900-series ⇒ temporary; permit needs expiry)
    work_eligibility: Mapped[str | None] = mapped_column(String(40))   # citizen|permanent_resident|work_permit|study_permit
    work_permit_expiry: Mapped[date | None] = mapped_column(Date)

    # Contact / address
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(10))

    # Emergency contact
    emergency_name: Mapped[str | None] = mapped_column(String(200))
    emergency_relationship: Mapped[str | None] = mapped_column(String(60))
    emergency_phone: Mapped[str | None] = mapped_column(String(30))

    # Employment
    hire_date: Mapped[date | None] = mapped_column(Date)
    termination_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="onboarding")   # see enums.EMPLOYEE_STATUSES
    employment_type: Mapped[str] = mapped_column(String(20), default="full_time")
    province_of_employment: Mapped[str] = mapped_column(String(2), default="ON")  # drives provincial tax
    job_title: Mapped[str | None] = mapped_column(String(120))          # -> masters.designations
    department: Mapped[str | None] = mapped_column(String(120))         # -> masters.departments
    work_location: Mapped[str | None] = mapped_column(String(120))      # -> masters.locations
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    # Tenancy: the branch this employee belongs to (→ ops_branches). branch_name
    # denormalized for display. Phase 1 of the multi-branch foundation.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    branch_name: Mapped[str | None] = mapped_column(String(180))
    # D4 multi-entity: the legal entity (group company, → legal_entities in the auth service) that
    # employs this person and whose CRA payroll account the payroll files carry. NULL = the org's default.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)

    # Compensation
    pay_type: Mapped[str] = mapped_column(String(20), default="salary")     # salary | hourly
    annual_salary: Mapped[float | None] = mapped_column(Numeric(12, 2))
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(10, 2))
    standard_hours_per_week: Mapped[float] = mapped_column(Numeric(5, 2), default=40)

    # >>> PAY FREQUENCY — first-class, drives pay calendar + tax annualization <<<
    pay_frequency: Mapped[str] = mapped_column(String(20), default="biweekly")  # enums.PAY_FREQUENCIES
    pay_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)

    # Tax profile (TD1 claim amounts captured at onboarding)
    td1_federal_claim: Mapped[float | None] = mapped_column(Numeric(12, 2))
    td1_provincial_claim: Mapped[float | None] = mapped_column(Numeric(12, 2))
    additional_tax_per_period: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    # Direct deposit (masked; full detail encrypted in Phase 2)
    bank_transit: Mapped[str | None] = mapped_column(String(5))
    bank_institution: Mapped[str | None] = mapped_column(String(3))
    bank_account_last4: Mapped[str | None] = mapped_column(String(4))
    bank_account_encrypted: Mapped[str | None] = mapped_column(String)   # full account, crypto in Phase 2

    # ---- Extended intake (onboarding spec) ---------------------------------
    # Identity
    middle_name: Mapped[str | None] = mapped_column(String(100))
    marital_status: Mapped[str | None] = mapped_column(String(20))
    # Contact — province_of_employment is a payroll field, so residence is separate
    work_email: Mapped[str | None] = mapped_column(String(200))
    home_phone: Mapped[str | None] = mapped_column(String(30))
    province_of_residence: Mapped[str | None] = mapped_column(String(2))
    # Work country — Lexora HRM is Canada-only, so this is always "CA".
    address_country: Mapped[str | None] = mapped_column(String(2), default="CA")
    # Family names (optional, as typed by the employee).
    father_name: Mapped[str | None] = mapped_column(String(200))
    spouse_name: Mapped[str | None] = mapped_column(String(200))
    grade: Mapped[str | None] = mapped_column(String(60))               # -> masters.grades
    # Immigration detail (a 900-series SIN ties to this)
    work_permit_type: Mapped[str | None] = mapped_column(String(40))     # open | employer_specific(LMIA)
    work_permit_number: Mapped[str | None] = mapped_column(String(40))
    # Employment terms
    probation_end_date: Mapped[date | None] = mapped_column(Date)
    # 2026-10-22 fixed-term / contract employees: the CURRENT term (history in employee_contract_terms)
    contract_start_date: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    contract_notice_days: Mapped[int | None] = mapped_column(Integer)
    contract_ref: Mapped[str | None] = mapped_column(String(80))
    fte_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))
    overtime_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    vacation_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=4)   # ESA floor 4% / 6% at 5yrs
    division: Mapped[str | None] = mapped_column(String(120))
    cost_center: Mapped[str | None] = mapped_column(String(60))
    is_union: Mapped[bool] = mapped_column(Boolean, default=False)
    union_local: Mapped[str | None] = mapped_column(String(60))
    # Statutory exemptions (rare — under 18 / over 70 / receiving CPP; >40% share for EI)
    cpp_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    ei_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    # TD1 line-item breakdowns (optional, for audit)
    td1_claim_lines: Mapped[dict | None] = mapped_column(JSONB)
    td1_prov_claim_lines: Mapped[dict | None] = mapped_column(JSONB)

    # ---- Column encryption (AES-256-GCM, shared/utils/crypto.py) -----------
    # `*_enc` hold the ciphertext token; `*_hash` is a keyed blind index used for
    # per-org uniqueness; `*_masked` is the display form the API returns by
    # default. The legacy plaintext columns above (sin_encrypted,
    # bank_account_encrypted) are kept dual-written until the verified cutover.
    sin_enc: Mapped[str | None] = mapped_column(String)
    bank_account_enc: Mapped[str | None] = mapped_column(String)
    enc_key_id: Mapped[str | None] = mapped_column(String(20))
    # S4: passport number — encrypted only (no plaintext column ever existed).
    passport_enc: Mapped[str | None] = mapped_column(String)
    passport_masked: Mapped[str | None] = mapped_column(String(40))
    passport_expiry: Mapped[date | None] = mapped_column(Date)

    # ---- privacy (PIPEDA): consent / representative / erasure fields ----------
    nominee_name: Mapped[str | None] = mapped_column(String(200))        # s.14 nomination right
    nominee_relationship: Mapped[str | None] = mapped_column(String(60))
    nominee_phone: Mapped[str | None] = mapped_column(String(30))
    erasure_hold: Mapped[bool] = mapped_column(Boolean, default=False)   # statutory retention hold
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EmployeeNumberCounter(Base):
    """Running number for auto employee numbers, one row per org. The prefix and
    zero-padding come from company settings (`numbering.employee_prefix`,
    `numbering.padding`); only the counter lives here."""
    __tablename__ = "employee_number_counters"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class DataProtectionState(Base):
    """Single-row record of the encryption migration (backfill / cutover /
    rollback) so boot is idempotent and the compliance dashboard can report it."""
    __tablename__ = "data_protection_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfilled_rows: Mapped[int] = mapped_column(Integer, default=0)
    cutover_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cutover_key_id: Mapped[str | None] = mapped_column(String(20))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_boot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(400))
