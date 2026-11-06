"""Exit process tables (employee service, shared Postgres, row-level org_id) — Stream C.

exit_settings          one row per org: notice defaults, leave-during-notice rule, rehire
                       number policy, letters hold, the interview + clearance templates
exit_notice_policies   notice days by grade (designation) / employment type / stage
exit_cases             the case: who, why, state, notice, last working day, settlement link
exit_case_events       the timeline (every action, who, when, note)
exit_clearance_items   one line per department / owner; a recovery flows to the settlement
employee_assets        simple "assets issued" list per employee (the product had no asset record)
employment_spells      one row per employment spell — exit date / reason / rehire flag; a
                       rehire adds the next spell on the SAME person record

All NEW tables (create_all makes them). Nothing existing is altered.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class ExitSettings(Base):
    __tablename__ = "exit_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    default_notice_days: Mapped[int] = mapped_column(Integer, default=30)        # org setting, not law
    probation_notice_days: Mapped[int] = mapped_column(Integer, default=15)      # org setting, not law
    leave_extends_lwd: Mapped[bool] = mapped_column(Boolean, default=False)
    rehire_number_policy: Mapped[str] = mapped_column(String(8), default="keep")  # keep | new
    hold_letters_until_settled: Mapped[bool] = mapped_column(Boolean, default=True)
    interview_template: Mapped[list | None] = mapped_column(JSONB)
    clearance_template: Mapped[list | None] = mapped_column(JSONB)
    case_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ExitNoticePolicy(Base):
    __tablename__ = "exit_notice_policies"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    grade: Mapped[str | None] = mapped_column(String(120))                # designation / grade; blank = any
    employment_type: Mapped[str | None] = mapped_column(String(20))       # blank = any
    stage: Mapped[str] = mapped_column(String(12), default="any")         # probation | confirmed | any
    notice_days: Mapped[int] = mapped_column(Integer, default=30)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExitCase(Base):
    __tablename__ = "exit_cases"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    case_number: Mapped[str] = mapped_column(String(20), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(220))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    department: Mapped[str | None] = mapped_column(String(120))
    job_title: Mapped[str | None] = mapped_column(String(120))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), default="CA")

    exit_type: Mapped[str] = mapped_column(String(24))                    # exit_logic.EXIT_TYPES
    initiated_by: Mapped[str] = mapped_column(String(10))                 # employee | manager | hr
    reason_category: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    documents: Mapped[list | None] = mapped_column(JSONB)                 # [{name, ref}]

    status: Mapped[str] = mapped_column(String(16), index=True, default="submitted")
    held_from_status: Mapped[str | None] = mapped_column(String(16))
    submitted_on: Mapped[date] = mapped_column(Date)
    requested_lwd: Mapped[date | None] = mapped_column(Date)
    accepted_on: Mapped[date | None] = mapped_column(Date)

    notice_days: Mapped[int | None] = mapped_column(Integer)
    notice_stage: Mapped[str | None] = mapped_column(String(12))
    notice_source: Mapped[str | None] = mapped_column(String(10))         # policy | default | manual
    expected_lwd: Mapped[date | None] = mapped_column(Date)
    lwd: Mapped[date | None] = mapped_column(Date, index=True)            # the REAL last working day
    lwd_reason: Mapped[str | None] = mapped_column(String(400))
    notice_served_days: Mapped[int | None] = mapped_column(Integer)
    notice_shortfall_days: Mapped[int] = mapped_column(Integer, default=0)
    notice_treatment: Mapped[str | None] = mapped_column(String(12))      # buyout | waiver | pay_in_lieu | none
    notice_treatment_reason: Mapped[str | None] = mapped_column(String(400))
    notice_decided_by: Mapped[str | None] = mapped_column(String(200))
    garden_leave: Mapped[bool] = mapped_column(Boolean, default=False)
    leave_during_notice_days: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    leave_extends_lwd: Mapped[bool] = mapped_column(Boolean, default=False)

    interview: Mapped[dict | None] = mapped_column(JSONB)                 # confidential to HR
    clearance_override: Mapped[dict | None] = mapped_column(JSONB)        # {by, at, reason}

    settlement_kind: Mapped[str | None] = mapped_column(String(16))       # canada_final
    settlement_ref: Mapped[str | None] = mapped_column(String(60))
    settlement_status: Mapped[str] = mapped_column(String(10), default="none")   # none | started | paid
    settlement_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_net: Mapped[float | None] = mapped_column(Numeric(14, 2))
    roe_reason_code: Mapped[str | None] = mapped_column(String(2))

    separated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    separation_result: Mapped[dict | None] = mapped_column(JSONB)

    withdraw_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    withdraw_reason: Mapped[str | None] = mapped_column(String(400))

    letters: Mapped[list | None] = mapped_column(JSONB)                   # [{key, label, at, by, ref, override_reason}]
    rehire_eligible: Mapped[bool | None] = mapped_column(Boolean)
    rehire_note: Mapped[str | None] = mapped_column(String(400))

    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExitCaseEvent(Base):
    __tablename__ = "exit_case_events"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str | None] = mapped_column(String(200))
    actor_role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    hr_only: Mapped[bool] = mapped_column(Boolean, default=False)


class ExitClearanceItem(Base):
    __tablename__ = "exit_clearance_items"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    department: Mapped[str] = mapped_column(String(80))
    item: Mapped[str] = mapped_column(String(240))
    owner_role: Mapped[str | None] = mapped_column(String(30))
    owner_user_id: Mapped[str | None] = mapped_column(String(60))
    owner_employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="pending")    # pending | cleared | cleared_with_recovery
    recovery_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    comment: Mapped[str | None] = mapped_column(String(600))
    acted_by: Mapped[str | None] = mapped_column(String(200))
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class EmployeeAsset(Base):
    __tablename__ = "employee_assets"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    item: Mapped[str] = mapped_column(String(160))
    tag: Mapped[str | None] = mapped_column(String(80))
    issued_on: Mapped[date | None] = mapped_column(Date)
    returned_on: Mapped[date | None] = mapped_column(Date)
    condition: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EmploymentSpell(Base):
    __tablename__ = "employment_spells"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    spell_no: Mapped[int] = mapped_column(Integer, default=1)
    employee_number: Mapped[str | None] = mapped_column(String(30))
    hire_date: Mapped[date | None] = mapped_column(Date)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_type: Mapped[str | None] = mapped_column(String(24))
    exit_reason: Mapped[str | None] = mapped_column(String(400))
    case_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    rehire_eligible: Mapped[bool | None] = mapped_column(Boolean)
    rehire_note: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_exit_cases_org_status ON exit_cases (org_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_exit_cases_org_emp ON exit_cases (org_id, employee_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_employment_spells ON employment_spells (org_id, employee_id, spell_no)",
    # One OPEN exit case per employee: a second resignation / termination is refused by the database.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_exit_cases_one_open ON exit_cases (org_id, employee_id) "
    "WHERE status NOT IN ('closed', 'withdrawn', 'rejected')",
]


async def init_exit_schema(engine) -> None:
    """Boot migration for the exit tables: create_all (new tables only) + idempotent indexes."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '15s'"))   # boot DDL never waits forever on a lock
        await conn.run_sync(Base.metadata.create_all)
        for ddl in _INDEXES:
            await conn.execute(text(ddl))
