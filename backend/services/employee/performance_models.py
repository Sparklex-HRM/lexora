"""Performance — goals, appraisal cycles, reviews, calibration audit, increment proposals.

All tables are NEW (prefix ``perf_``) and org-scoped. Nothing alters an existing table.
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


class PerfSettings(Base):
    """One row per org: rating scale, score weights, guideline distribution,
    increment matrix. A missing row means the defaults in performance_logic."""
    __tablename__ = "perf_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_by: Mapped[str | None] = mapped_column(String(64))


class PerfGoalCycle(Base):
    __tablename__ = "perf_goal_cycles"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(120))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")          # open | closed
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfGoal(Base):
    __tablename__ = "perf_goals"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    title: Mapped[str] = mapped_column(String(300))
    measure: Mapped[str | None] = mapped_column(String(300))                 # how it is measured
    target: Mapped[str | None] = mapped_column(String(300))
    weight: Mapped[float] = mapped_column(Numeric(6, 2), default=0)          # % — a person's goals sum to 100
    due_date: Mapped[date | None] = mapped_column(Date)
    # draft | proposed | approved | returned
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    proposed_by: Mapped[str] = mapped_column(String(20), default="employee")  # employee | manager | hr
    progress_pct: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    status_reason: Mapped[str | None] = mapped_column(String(400))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfCheckin(Base):
    __tablename__ = "perf_checkins"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    goal_id: Mapped[uuid.UUID] = _ref(nullable=False)
    progress_pct: Mapped[float] = mapped_column(Numeric(6, 2))
    comment: Mapped[str | None] = mapped_column(Text)
    by_user: Mapped[str | None] = mapped_column(String(64))
    by_name: Mapped[str | None] = mapped_column(String(200))
    by_role: Mapped[str | None] = mapped_column(String(20))                  # employee | manager | hr
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfGoalLibrary(Base):
    __tablename__ = "perf_goal_library"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    designation: Mapped[str | None] = mapped_column(String(120), index=True)  # null = any designation
    title: Mapped[str] = mapped_column(String(300))
    measure: Mapped[str | None] = mapped_column(String(300))
    target: Mapped[str | None] = mapped_column(String(300))
    weight: Mapped[float | None] = mapped_column(Numeric(6, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfCycle(Base):
    """An appraisal cycle. `template` freezes the scale, competencies and score
    weights when the cycle is launched, so later setting changes do not move
    scores that people have already seen."""
    __tablename__ = "perf_cycles"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(160))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    goal_cycle_id: Mapped[uuid.UUID | None] = _ref(index=False)
    joined_before: Mapped[date | None] = mapped_column(Date)                 # eligibility
    exclude_notice: Mapped[bool] = mapped_column(Boolean, default=True)
    use_reviewer: Mapped[bool] = mapped_column(Boolean, default=False)       # skip-level stage
    template: Mapped[dict] = mapped_column(JSONB, default=dict)              # {scale, competencies, goal_weight, competency_weight, guideline}
    deadlines: Mapped[dict] = mapped_column(JSONB, default=dict)             # {self, manager, reviewer, calibration}
    # draft | active | published | closed
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfReview(Base):
    __tablename__ = "perf_reviews"
    __table_args__ = (UniqueConstraint("cycle_id", "employee_id", name="perf_review_cycle_emp_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    department: Mapped[str | None] = mapped_column(String(120))
    designation: Mapped[str | None] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(60))
    manager_id: Mapped[uuid.UUID | None] = _ref()
    manager_name: Mapped[str | None] = mapped_column(String(200))
    reviewer_id: Mapped[uuid.UUID | None] = _ref()                           # the manager's manager
    reviewer_name: Mapped[str | None] = mapped_column(String(200))
    goals: Mapped[list] = mapped_column(JSONB, default=list)                 # snapshot [{id,title,measure,target,weight,progress_pct}]
    # self | manager | reviewer | calibration | published
    stage: Mapped[str] = mapped_column(String(20), default="self", index=True)
    self_review: Mapped[dict | None] = mapped_column(JSONB)                  # {goal_ratings, competency_ratings, comments}
    self_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manager_review: Mapped[dict | None] = mapped_column(JSONB)               # + promotion_recommended, promotion_note
    manager_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_review: Mapped[dict | None] = mapped_column(JSONB)              # {comments, agree, suggested_score}
    reviewer_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scores: Mapped[dict | None] = mapped_column(JSONB)                       # the arithmetic, from the manager's ratings
    overall_score: Mapped[float | None] = mapped_column(Numeric(6, 2))       # computed
    final_score: Mapped[float | None] = mapped_column(Numeric(6, 2))         # after calibration
    final_band: Mapped[str | None] = mapped_column(String(80))
    calibration_note: Mapped[str | None] = mapped_column(String(400))
    promotion_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    promotion_note: Mapped[str | None] = mapped_column(String(400))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_status: Mapped[str | None] = mapped_column(String(20))               # acknowledged | disagreed
    ack_comment: Mapped[str | None] = mapped_column(Text)
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfReviewAudit(Base):
    """Append-only: every reopen, stage move and calibration change, with the reason."""
    __tablename__ = "perf_review_audit"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    review_id: Mapped[uuid.UUID] = _ref(nullable=False)
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)
    action: Mapped[str] = mapped_column(String(40))                          # calibrate | move_stage | publish
    field: Mapped[str | None] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(String(200))
    new_value: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(400))
    by_user: Mapped[str | None] = mapped_column(String(64))
    by_name: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfIncrementProposal(Base):
    __tablename__ = "perf_increment_proposals"
    __table_args__ = (UniqueConstraint("org_id", "cycle_id", name="perf_incr_cycle_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)
    budget: Mapped[float | None] = mapped_column(Numeric(16, 2))             # typed by HR
    effective_from: Mapped[date | None] = mapped_column(Date)
    matrix: Mapped[list] = mapped_column(JSONB, default=list)                # the matrix used, frozen on the proposal
    items: Mapped[list] = mapped_column(JSONB, default=list)                 # [{employee_id, name, band, grade, current_annual, min_pct, max_pct, proposed_pct, reason, include}]
    # draft | sent
    status: Mapped[str] = mapped_column(String(20), default="draft")
    batch_id: Mapped[str | None] = mapped_column(String(80))                 # what payroll returned
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
