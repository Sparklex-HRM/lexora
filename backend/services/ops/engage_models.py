"""Engagement tables (ops service, shared Postgres, row-level org_id) — Stream G4.

eng_settings            one row per org: helpdesk categories + SLA hours, anonymity threshold,
                        kudos values + approval flag, counters
eng_tickets             HR helpdesk tickets ("Ask HR")        eng_ticket_comments   the thread
eng_surveys             pulse / eNPS / exit surveys            eng_survey_responses  answers
eng_kudos               the recognition wall
eng_courses             LMS courses (modules + quiz as JSON)   eng_course_assignments  who, due, progress
eng_user_prefs          per-login preferences (language, voice)
eng_exit_survey_log     which exit cases already got their survey

All NEW tables — nothing existing is altered. Attachments / module files are rows of
`employee_documents` (shared.utils.docstore) referenced by id.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
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


class EngSettings(Base):
    __tablename__ = "eng_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    helpdesk_categories: Mapped[list | None] = mapped_column(JSONB)
    anonymity_threshold: Mapped[int] = mapped_column(Integer, default=5)
    kudos_values: Mapped[list | None] = mapped_column(JSONB)
    kudos_needs_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    ticket_seq: Mapped[int] = mapped_column(Integer, default=0)
    certificate_seq: Mapped[int] = mapped_column(Integer, default=0)
    # D7
    kudos_points_default: Mapped[int] = mapped_column(Integer, default=10)        # points one kudos carries
    manager_points_per_month: Mapped[int] = mapped_column(Integer, default=100)   # default monthly budget per manager
    lms_remind_days: Mapped[int] = mapped_column(Integer, default=3)              # "due soon" reminder lead time
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Ticket(Base):
    __tablename__ = "eng_tickets"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    number: Mapped[str] = mapped_column(String(20), index=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(120))
    requester_user_id: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    assignee_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    assignee_name: Mapped[str | None] = mapped_column(String(200))
    sla_hours: Mapped[int] = mapped_column(Integer, default=72)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidential: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(12), default="web")
    doc_ids: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TicketComment(Base):
    __tablename__ = "eng_ticket_comments"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    ticket_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    author_user_id: Mapped[str | None] = mapped_column(String(64))
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_role: Mapped[str | None] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)      # HR-only note
    kind: Mapped[str] = mapped_column(String(16), default="comment")    # comment | status | assign
    doc_ids: Mapped[list | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Survey(Base):
    __tablename__ = "eng_surveys"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(12), default="pulse")       # pulse | enps | exit | custom
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)   # draft | open | closed
    opens_on: Mapped[date | None] = mapped_column(Date)
    closes_on: Mapped[date | None] = mapped_column(Date)
    target: Mapped[dict | None] = mapped_column(JSONB)                   # {departments, employee_ids, locations}
    threshold: Mapped[int] = mapped_column(Integer, default=5)
    exit_case_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SurveyResponse(Base):
    __tablename__ = "eng_survey_responses"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    survey_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))   # NULL on anonymous surveys
    respondent_hash: Mapped[str] = mapped_column(String(64), index=True)          # one answer per person, even anonymous
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # D7: coarse segment stamped at submit time (never the person) so heatmaps / manager insights can
    # aggregate anonymous answers by department / manager — always behind the anonymity threshold.
    department: Mapped[str | None] = mapped_column(String(120))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class Kudos(Base):
    __tablename__ = "eng_kudos"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    from_employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    from_name: Mapped[str | None] = mapped_column(String(200))
    to_employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    to_name: Mapped[str | None] = mapped_column(String(200))
    value_tag: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(String(10), default="approved", index=True)   # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)                           # D7: recognition points (from a manager budget)


class Course(Base):
    __tablename__ = "eng_courses"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    modules: Mapped[list] = mapped_column(JSONB, default=list)   # [{key,title,kind: pdf|video|url, doc_id, url, minutes}]
    quiz: Mapped[list] = mapped_column(JSONB, default=list)      # [{q, options[], answer}]
    pass_mark: Mapped[int] = mapped_column(Integer, default=70)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # D7 LMS depth (ALTER … ADD COLUMN IF NOT EXISTS in _DDL for existing installations)
    max_attempts: Mapped[int] = mapped_column(Integer, default=0)            # 0 = unlimited
    question_tags: Mapped[list | None] = mapped_column(JSONB)                # draw quiz questions from the bank by tag
    quiz_sample: Mapped[int] = mapped_column(Integer, default=0)             # how many bank questions to draw (0 = all tagged)
    category: Mapped[str | None] = mapped_column(String(60))                 # e.g. "Compliance", "Skills"


class CourseAssignment(Base):
    __tablename__ = "eng_course_assignments"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    course_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    due_on: Mapped[date | None] = mapped_column(Date)
    assigned_by: Mapped[str | None] = mapped_column(String(200))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_modules: Mapped[list] = mapped_column(JSONB, default=list)
    quiz_score: Mapped[int | None] = mapped_column(Integer)
    quiz_passed: Mapped[bool | None] = mapped_column(Boolean)
    quiz_attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="assigned", index=True)   # assigned | in_progress | completed
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_no: Mapped[str | None] = mapped_column(String(30))
    # D7
    rule_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))       # auto-assignment rule that made it
    path_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))       # learning path it belongs to
    quiz_set: Mapped[list | None] = mapped_column(JSONB)                           # bank question ids drawn for this attempt


class UserPref(Base):
    __tablename__ = "eng_user_prefs"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    voice: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ExitSurveyLog(Base):
    __tablename__ = "eng_exit_survey_log"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    exit_case_id: Mapped[str] = mapped_column(String(64), index=True)
    survey_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


_DDL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_survey_resp ON eng_survey_responses (survey_id, respondent_hash)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_user_prefs ON eng_user_prefs (org_id, user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_exit_log ON eng_exit_survey_log (org_id, exit_case_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_assignment ON eng_course_assignments (org_id, course_id, employee_id)",
    "CREATE INDEX IF NOT EXISTS ix_eng_tickets_org_status ON eng_tickets (org_id, status)",
    # D7 additive columns (create_all never ALTERs an existing table)
    "ALTER TABLE eng_courses ADD COLUMN IF NOT EXISTS max_attempts INTEGER DEFAULT 0",
    "ALTER TABLE eng_courses ADD COLUMN IF NOT EXISTS question_tags JSONB",
    "ALTER TABLE eng_courses ADD COLUMN IF NOT EXISTS quiz_sample INTEGER DEFAULT 0",
    "ALTER TABLE eng_courses ADD COLUMN IF NOT EXISTS category VARCHAR(60)",
    "ALTER TABLE eng_course_assignments ADD COLUMN IF NOT EXISTS rule_id UUID",
    "ALTER TABLE eng_course_assignments ADD COLUMN IF NOT EXISTS path_id UUID",
    "ALTER TABLE eng_course_assignments ADD COLUMN IF NOT EXISTS quiz_set JSONB",
    "ALTER TABLE eng_survey_responses ADD COLUMN IF NOT EXISTS department VARCHAR(120)",
    "ALTER TABLE eng_survey_responses ADD COLUMN IF NOT EXISTS manager_id UUID",
    "ALTER TABLE eng_kudos ADD COLUMN IF NOT EXISTS points INTEGER DEFAULT 0",
    "ALTER TABLE eng_settings ADD COLUMN IF NOT EXISTS kudos_points_default INTEGER DEFAULT 10",
    "ALTER TABLE eng_settings ADD COLUMN IF NOT EXISTS manager_points_per_month INTEGER DEFAULT 100",
    "ALTER TABLE eng_settings ADD COLUMN IF NOT EXISTS lms_remind_days INTEGER DEFAULT 3",
]


async def init_engage_schema(engine) -> None:
    from sqlalchemy import text
    import shared.utils.docstore  # noqa: F401  (registers employee_documents on Base for create_all)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in _DDL:
            await conn.execute(text(ddl))
