"""LMS depth tables (ops service, shared Postgres, row-level org_id) — Stream D7.

eng_lms_paths            learning paths: an ordered list of courses with one due date
eng_lms_path_assignments who is on a path (progress is derived from the course assignments)
eng_lms_rules            auto-assignment rules by role / department / entity / joining window
                         (e.g. "Workplace harassment awareness within 30 days of joining" — the org fills the content)
eng_lms_questions        org-wide quiz question bank (tagged), courses draw from it
eng_lms_packages         SCORM-lite packages: a zip with index.html, served sandboxed
eng_lms_reminder_log     which assignment got which reminder (never twice)

All NEW tables. Course / assignment columns added for D7 live in engage_models (ALTER … IF NOT EXISTS).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _org():
    return mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


class LearningPath(Base):
    __tablename__ = "eng_lms_paths"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    course_ids: Mapped[list] = mapped_column(JSONB, default=list)        # ordered
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PathAssignment(Base):
    __tablename__ = "eng_lms_path_assignments"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    path_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    due_on: Mapped[date | None] = mapped_column(Date)
    assigned_by: Mapped[str | None] = mapped_column(String(200))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LmsRule(Base):
    __tablename__ = "eng_lms_rules"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(200))
    course_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    path_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    roles: Mapped[list | None] = mapped_column(JSONB)              # login roles, e.g. ["manager"]
    departments: Mapped[list | None] = mapped_column(JSONB)
    entities: Mapped[list | None] = mapped_column(JSONB)           # legal entity ids / names (D4)
    locations: Mapped[list | None] = mapped_column(JSONB)
    joined_from: Mapped[date | None] = mapped_column(Date)         # joining-date window
    joined_to: Mapped[date | None] = mapped_column(Date)
    due_days: Mapped[int] = mapped_column(Integer, default=30)     # due = joining date + N days (never in the past)
    new_joiners_only: Mapped[bool] = mapped_column(Boolean, default=False)   # only people who joined after the rule was made
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_assigned: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LmsQuestion(Base):
    __tablename__ = "eng_lms_questions"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    tag: Mapped[str] = mapped_column(String(60), index=True)
    q: Mapped[str] = mapped_column(String(400))
    options: Mapped[list] = mapped_column(JSONB, default=list)
    answer: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LmsPackage(Base):
    __tablename__ = "eng_lms_packages"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    course_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    entries: Mapped[list] = mapped_column(JSONB, default=list)     # file paths inside the zip
    entry_point: Mapped[str] = mapped_column(String(300), default="index.html")
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    uploaded_by: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LmsReminderLog(Base):
    __tablename__ = "eng_lms_reminder_log"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    assignment_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(12))                 # due_soon | overdue
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


LMS_DDL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_lms_reminder ON eng_lms_reminder_log (assignment_id, stage)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_lms_path_asg ON eng_lms_path_assignments (org_id, path_id, employee_id)",
]
