"""Performance G4 (2026-10-23) — objectives & key results, 360 feedback, improvement plans, 1:1s.

All tables are NEW (prefixes ``perf_okr_``, ``perf_fb_``, ``perf_pip_``, ``perf_one_``) and org-scoped.
Nothing alters an existing table. Created by the service's boot ``create_all`` (migration 0013 re-runs it).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
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


# ═════════════════════════ objectives & key results ═════════════════════════
class PerfObjective(Base):
    """One objective. Scoped to a goal cycle (a quarter, a half or a year — HR names it).
    `level` company | department | team | individual. `parent_id` aligns it under a
    higher objective (the tree). Progress is ROLLED UP from its key results (and, when it
    has none, from its aligned children) — never typed."""
    __tablename__ = "perf_okr_objectives"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    cycle_id: Mapped[uuid.UUID] = _ref(nullable=False)                      # perf_goal_cycles.id
    level: Mapped[str] = mapped_column(String(20), default="individual")    # company | department | team | individual
    owner_employee_id: Mapped[uuid.UUID | None] = _ref()                    # null for company / department level
    owner_name: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = _ref()
    weight: Mapped[float] = mapped_column(Numeric(6, 2), default=100)        # share of the owner's review score
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)   # draft | active | closed | cancelled
    progress_pct: Mapped[float] = mapped_column(Numeric(6, 2), default=0)    # rolled up
    confidence: Mapped[int | None] = mapped_column(Integer)                  # last check-in, 1..10
    include_in_review: Mapped[bool] = mapped_column(Boolean, default=True)   # snapshot into the appraisal review
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfKeyResult(Base):
    __tablename__ = "perf_okr_key_results"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    objective_id: Mapped[uuid.UUID] = _ref(nullable=False)
    title: Mapped[str] = mapped_column(String(300))
    metric_type: Mapped[str] = mapped_column(String(20), default="number")  # percent | number | currency | milestone
    unit: Mapped[str | None] = mapped_column(String(30))
    start_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    target_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    current_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    milestones: Mapped[list] = mapped_column(JSONB, default=list)            # [{label, done}] for metric_type=milestone
    weight: Mapped[float] = mapped_column(Numeric(6, 2), default=1)
    progress_pct: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    confidence: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfOkrCheckin(Base):
    """Append-only: every value update with its confidence and comment."""
    __tablename__ = "perf_okr_checkins"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    objective_id: Mapped[uuid.UUID] = _ref(nullable=False)
    key_result_id: Mapped[uuid.UUID | None] = _ref()
    old_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    new_value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    milestones: Mapped[list | None] = mapped_column(JSONB)
    progress_pct: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    confidence: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    by_user: Mapped[str | None] = mapped_column(String(64))
    by_name: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ═════════════════════════ 360 feedback ═════════════════════════════════════
class PerfFeedbackRound(Base):
    """A 360 round. `questions` is frozen when the round opens for nomination.
    `min_responses` is the anonymity threshold (never below 3)."""
    __tablename__ = "perf_fb_rounds"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(160))
    review_cycle_id: Mapped[uuid.UUID | None] = _ref()                      # optional link to an appraisal cycle
    questions: Mapped[list] = mapped_column(JSONB, default=list)             # [{key, competency, text, kind: rating|text}]
    min_responses: Mapped[int] = mapped_column(Integer, default=3)
    peers_min: Mapped[int] = mapped_column(Integer, default=3)
    peers_max: Mapped[int] = mapped_column(Integer, default=8)
    include_upward: Mapped[bool] = mapped_column(Boolean, default=True)      # direct reports rate their manager
    include_self: Mapped[bool] = mapped_column(Boolean, default=True)
    nominate_by: Mapped[str] = mapped_column(String(20), default="employee")  # employee (manager approves) | manager | hr
    due_date: Mapped[date | None] = mapped_column(Date)
    # draft | nominating | collecting | closed | released
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfFeedbackSubject(Base):
    """One person being reviewed in a round."""
    __tablename__ = "perf_fb_subjects"
    __table_args__ = (UniqueConstraint("round_id", "employee_id", name="perf_fb_subject_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    round_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    manager_id: Mapped[uuid.UUID | None] = _ref()
    manager_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfFeedbackNomination(Base):
    """Who gives feedback to whom. The answers live here too; they are NEVER returned one
    by one to the subject — only the anonymity-safe aggregate (perf_g4_logic.aggregate_360)."""
    __tablename__ = "perf_fb_nominations"
    __table_args__ = (UniqueConstraint("subject_id", "reviewer_employee_id", name="perf_fb_nom_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    round_id: Mapped[uuid.UUID] = _ref(nullable=False)
    subject_id: Mapped[uuid.UUID] = _ref(nullable=False)
    reviewer_employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    reviewer_name: Mapped[str | None] = mapped_column(String(200))
    relationship: Mapped[str] = mapped_column(String(20))                   # self | manager | peer | direct_report | other
    nominated_by: Mapped[str] = mapped_column(String(20))                   # self | manager | hr
    # proposed (waiting for the manager) | approved | rejected | declined | submitted
    status: Mapped[str] = mapped_column(String(20), default="approved", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answers: Mapped[dict | None] = mapped_column(JSONB)                      # {question_key: rating | text}
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ═════════════════════════ improvement plans ════════════════════════════════
class PerfPip(Base):
    """A performance improvement plan. Confidential: the employee, their manager and HR only.
    An unsuccessful outcome never ends employment by itself — HR starts an exit case by hand."""
    __tablename__ = "perf_pip_plans"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    plan_number: Mapped[str] = mapped_column(String(30), index=True)
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    manager_id: Mapped[uuid.UUID | None] = _ref()
    manager_name: Mapped[str | None] = mapped_column(String(200))
    hr_owner_user_id: Mapped[str | None] = mapped_column(String(64))
    hr_owner_name: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)                               # why the plan exists (facts, not labels)
    expectations: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    checkin_every_days: Mapped[int] = mapped_column(Integer, default=14)
    # draft | active | extended | closed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    outcome: Mapped[str | None] = mapped_column(String(20))                 # successful | extended | exit
    outcome_note: Mapped[str | None] = mapped_column(Text)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_by: Mapped[str | None] = mapped_column(String(64))
    extensions: Mapped[list] = mapped_column(JSONB, default=list)            # [{from, to, reason, by, at}]
    ack_status: Mapped[str | None] = mapped_column(String(20))              # acknowledged | disagreed
    ack_comment: Mapped[str | None] = mapped_column(Text)
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_case_id: Mapped[str | None] = mapped_column(String(64))            # set when HR starts the exit by hand
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PerfPipItem(Base):
    """A plan line: kind=goal (what must improve, how it is measured) or kind=support (what the
    company does to help: training, mentoring, tools)."""
    __tablename__ = "perf_pip_items"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    pip_id: Mapped[uuid.UUID] = _ref(nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="goal")           # goal | support
    title: Mapped[str] = mapped_column(String(300))
    measure: Mapped[str | None] = mapped_column(String(300))
    owner: Mapped[str | None] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")         # goal: open | met | not_met · support: open | done
    note: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerfPipCheckin(Base):
    __tablename__ = "perf_pip_checkins"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    pip_id: Mapped[uuid.UUID] = _ref(nullable=False)
    due_on: Mapped[date] = mapped_column(Date)
    held_on: Mapped[date | None] = mapped_column(Date)
    rating: Mapped[str | None] = mapped_column(String(20))                  # on_track | some_progress | off_track
    notes: Mapped[str | None] = mapped_column(Text)
    by_user: Mapped[str | None] = mapped_column(String(64))
    by_name: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PerfPipAudit(Base):
    """Append-only: create, start, items, check-ins, extension, outcome, acknowledgement, exit link."""
    __tablename__ = "perf_pip_audit"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    pip_id: Mapped[uuid.UUID] = _ref(nullable=False)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(String(600))
    by_user: Mapped[str | None] = mapped_column(String(64))
    by_name: Mapped[str | None] = mapped_column(String(200))
    by_role: Mapped[str | None] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ═════════════════════════ 1:1 conversations ═════════════════════════════════
class PerfOneOnOne(Base):
    """A 1:1 between a manager and a direct report. Shared notes are seen by both; each side's
    private notes only by that side. HR sees that meetings happen (dates, counts), not notes."""
    __tablename__ = "perf_one_meetings"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    manager_id: Mapped[uuid.UUID] = _ref(nullable=False)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    repeat_days: Mapped[int | None] = mapped_column(Integer)                # 7 / 14 / 30 — the next one is offered on close
    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)   # scheduled | done | cancelled
    agenda: Mapped[list] = mapped_column(JSONB, default=list)                # [{id, text, by: manager|employee, discussed}]
    shared_notes: Mapped[str | None] = mapped_column(Text)
    manager_private: Mapped[str | None] = mapped_column(Text)
    employee_private: Mapped[str | None] = mapped_column(Text)
    action_items: Mapped[list] = mapped_column(JSONB, default=list)          # [{id, text, owner: manager|employee, due, done, carried_from}]
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
