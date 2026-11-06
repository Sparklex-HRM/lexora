"""Compensation planning cycles — tables (employee service, row-level org_id).

All tables are NEW (prefix ``comp_cycle_``). Nothing here alters an existing table and
nothing here writes a salary: the cycle hands its approved lines to the EXISTING bulk
salary-revision path in payroll (see comp_cycle_routers.run_apply).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _org():
    return mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


def _ref(nullable=True, index=True):
    return mapped_column(PG_UUID(as_uuid=True), index=index, nullable=nullable)


class CompCycleSettings(Base):
    """One row per org: default merit matrix, compa-ratio bands, grade midpoints,
    budget rules. A missing row means the defaults in comp_cycle_logic."""
    __tablename__ = "comp_cycle_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_by: Mapped[str | None] = mapped_column(String(200))


class CompCycle(Base):
    """A compensation planning cycle. Everything managers and HR need to plan is frozen
    on the cycle (matrix, bands, midpoints, budgets) so later setting changes do not move
    numbers people have already seen."""
    __tablename__ = "comp_cycles"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(160))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    effective_from: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    perf_cycle_id: Mapped[uuid.UUID | None] = _ref(index=False)              # appraisal cycle the ratings come from
    eligibility: Mapped[dict] = mapped_column(JSONB, default=dict)          # {departments, grades, entity_ids, joined_before, require_rating, exclude_notice}
    budgets: Mapped[list] = mapped_column(JSONB, default=list)              # [{scope: department|entity|total, key, amount, pct}]
    budget_mode: Mapped[str] = mapped_column(String(10), default="soft")    # hard | soft
    default_budget_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))
    promotion_budget: Mapped[float | None] = mapped_column(Numeric(16, 2))
    matrix: Mapped[list] = mapped_column(JSONB, default=list)               # [{rating, cr_band, rec_pct, min_pct, max_pct}]
    compa_bands: Mapped[list] = mapped_column(JSONB, default=list)          # [{key, label, min_cr, max_cr}]
    grade_midpoints: Mapped[dict] = mapped_column(JSONB, default=dict)      # {grade: annual midpoint}
    guidelines: Mapped[str | None] = mapped_column(Text)
    allow_lump_sum: Mapped[bool] = mapped_column(Boolean, default=True)
    # draft | open | calibration | approval | applying | applied | closed
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    approval: Mapped[dict] = mapped_column(JSONB, default=dict)             # {state, chain, step, decisions, request_type, metadata}
    apply_progress: Mapped[dict] = mapped_column(JSONB, default=dict)       # {step, total, done, errors, batch_id, ...}
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CompCycleLine(Base):
    """One eligible employee in a cycle: the snapshot the plan was made on, the manager's
    proposal, HR's calibration and — after apply — what was written where."""
    __tablename__ = "comp_cycle_lines"
    __table_args__ = (UniqueConstraint("cycle_id", "employee_id", name="comp_cycle_line_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    department: Mapped[str | None] = mapped_column(String(120), index=True)
    designation: Mapped[str | None] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(60))
    gender: Mapped[str | None] = mapped_column(String(20))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    manager_id: Mapped[uuid.UUID | None] = _ref()
    manager_name: Mapped[str | None] = mapped_column(String(200))
    hire_date: Mapped[date | None] = mapped_column(Date)
    current_annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    rating: Mapped[str | None] = mapped_column(String(80))
    rating_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    midpoint: Mapped[float | None] = mapped_column(Numeric(14, 2))
    compa_ratio: Mapped[float | None] = mapped_column(Numeric(6, 3))
    compa_band: Mapped[str | None] = mapped_column(String(20))
    rec_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))             # from the matrix
    min_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))
    max_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))
    proposed_pct: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    proposed_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)  # annual increase
    lump_sum: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    promotion: Mapped[bool] = mapped_column(Boolean, default=False)
    promo_designation: Mapped[str | None] = mapped_column(String(120))
    promo_grade: Mapped[str | None] = mapped_column(String(60))
    manager_comment: Mapped[str | None] = mapped_column(String(600))
    hr_comment: Mapped[str | None] = mapped_column(String(600))
    hr_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    include: Mapped[bool] = mapped_column(Boolean, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    apply_result: Mapped[dict] = mapped_column(JSONB, default=dict)         # {revision, career_change_id, letter_id, promotion_letter_id, errors}
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CompCycleEvent(Base):
    """Append-only: status moves, approvals, locks, bulk adjustments, apply steps."""
    __tablename__ = "comp_cycle_events"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    by_user: Mapped[str | None] = mapped_column(String(200))
    by_role: Mapped[str | None] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
