"""Hiring — requisitions, postings, candidates, applications, interviews, offers.

All tables are NEW (prefix ``hire_``) and org-scoped by ``org_id``. Nothing here
alters an existing table, so ``create_all`` is enough.
Files (resumes / candidate documents) are stored as bytes in ``hire_documents``
because there is no object store in the local stack.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, Date, DateTime, Integer, LargeBinary, Numeric, String, Text,
                        UniqueConstraint)
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


class HireSettings(Base):
    """One row per org. A missing row means the defaults in hiring_logic."""
    __tablename__ = "hire_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_by: Mapped[str | None] = mapped_column(String(64))


class HireRequisition(Base):
    __tablename__ = "hire_requisitions"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    req_number: Mapped[str] = mapped_column(String(30), index=True)
    department: Mapped[str | None] = mapped_column(String(120))
    designation: Mapped[str] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(60))
    location: Mapped[str | None] = mapped_column(String(120))
    employment_type: Mapped[str] = mapped_column(String(20), default="full_time")
    openings: Mapped[int] = mapped_column(Integer, default=1)
    reason_type: Mapped[str] = mapped_column(String(20), default="new")      # new | replacement
    replaces_employee_id: Mapped[uuid.UUID | None] = _ref(index=False)
    replaces_employee_name: Mapped[str | None] = mapped_column(String(200))
    budget_min: Mapped[float | None] = mapped_column(Numeric(14, 2))         # annual CTC
    budget_max: Mapped[float | None] = mapped_column(Numeric(14, 2))
    hiring_manager_id: Mapped[uuid.UUID | None] = _ref()                     # employee id
    hiring_manager_name: Mapped[str | None] = mapped_column(String(200))
    target_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    # draft | pending_approval | open | on_hold | filled | cancelled
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    approval_step: Mapped[str | None] = mapped_column(String(20))            # manager | hr (while pending)
    approvals: Mapped[list] = mapped_column(JSONB, default=list)             # [{step, by, name, at, action, reason}]
    status_reason: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_by_employee_id: Mapped[uuid.UUID | None] = _ref(index=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HirePosting(Base):
    __tablename__ = "hire_postings"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    requisition_id: Mapped[uuid.UUID] = _ref(nullable=False)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireCandidate(Base):
    """One record per person per org. Duplicates are FLAGGED, never merged silently."""
    __tablename__ = "hire_candidates"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    email_norm: Mapped[str | None] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    phone_norm: Mapped[str | None] = mapped_column(String(20), index=True)
    current_employer: Mapped[str | None] = mapped_column(String(200))
    current_title: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_text: Mapped[str | None] = mapped_column(Text)        # the privacy text the person ticked
    merged_into: Mapped[uuid.UUID | None] = _ref(index=False)     # set by an explicit HR merge
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64))    # null = came from the careers page
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireApplication(Base):
    __tablename__ = "hire_applications"
    __table_args__ = (UniqueConstraint("org_id", "candidate_id", "requisition_id", name="hire_app_cand_req_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    candidate_id: Mapped[uuid.UUID] = _ref(nullable=False)
    requisition_id: Mapped[uuid.UUID] = _ref(nullable=False)
    posting_id: Mapped[uuid.UUID | None] = _ref(index=False)
    # careers_page | referral | agency | walk_in | job_board
    source_type: Mapped[str] = mapped_column(String(20), default="walk_in")
    source_detail: Mapped[str | None] = mapped_column(String(200))           # agency / job board name
    referrer_employee_id: Mapped[uuid.UUID | None] = _ref(index=False)
    referrer_name: Mapped[str | None] = mapped_column(String(200))
    # applied | screening | interview | selected | offer | joined | rejected | withdrawn | on_hold
    stage: Mapped[str] = mapped_column(String(20), default="applied", index=True)
    prev_stage: Mapped[str | None] = mapped_column(String(20))               # where to return from hold / reopen
    stage_reason: Mapped[str | None] = mapped_column(String(400))
    stage_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    onboarding_session_id: Mapped[str | None] = mapped_column(String(64))
    employee_id: Mapped[uuid.UUID | None] = _ref(index=False)                # set when the person is activated
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireStageHistory(Base):
    __tablename__ = "hire_stage_history"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    application_id: Mapped[uuid.UUID] = _ref(nullable=False)
    from_stage: Mapped[str | None] = mapped_column(String(20))
    to_stage: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(400))
    by_user: Mapped[str | None] = mapped_column(String(64))
    by_name: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireDocument(Base):
    __tablename__ = "hire_documents"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    candidate_id: Mapped[uuid.UUID] = _ref(nullable=False)
    application_id: Mapped[uuid.UUID | None] = _ref(index=False)
    kind: Mapped[str] = mapped_column(String(20), default="resume")          # resume | document
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[str | None] = mapped_column(String(64))              # null = the candidate
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # D2: a copy on the files volume (LEXORA_FILES_DIR/<org>/hiring/<candidate>/<id>.<ext>); null = DB bytes only.
    # Added with ADD COLUMN IF NOT EXISTS in hiring_d2_init (create_all never ALTERs).
    file_path: Mapped[str | None] = mapped_column(String(400))


class HireNote(Base):
    __tablename__ = "hire_notes"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    candidate_id: Mapped[uuid.UUID] = _ref(nullable=False)
    application_id: Mapped[uuid.UUID | None] = _ref()
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str | None] = mapped_column(String(64))
    author_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireInterview(Base):
    __tablename__ = "hire_interviews"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    application_id: Mapped[uuid.UUID] = _ref(nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    round_name: Mapped[str] = mapped_column(String(120))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(20), default="in_person")       # in_person | video | phone
    place: Mapped[str | None] = mapped_column(String(300))                   # link or room
    panel: Mapped[list] = mapped_column(JSONB, default=list)                 # [{employee_id, name}]
    status: Mapped[str] = mapped_column(String(20), default="scheduled")     # scheduled | cancelled
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireFeedback(Base):
    __tablename__ = "hire_feedback"
    __table_args__ = (UniqueConstraint("interview_id", "interviewer_employee_id", name="hire_feedback_once_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    interview_id: Mapped[uuid.UUID] = _ref(nullable=False)
    application_id: Mapped[uuid.UUID] = _ref(nullable=False)
    interviewer_employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    interviewer_name: Mapped[str | None] = mapped_column(String(200))
    rating: Mapped[int] = mapped_column(Integer)                             # 1..5
    strengths: Mapped[str | None] = mapped_column(Text)
    concerns: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(String(20))                  # hire | no_hire | hold
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireOffer(Base):
    __tablename__ = "hire_offers"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    application_id: Mapped[uuid.UUID] = _ref(nullable=False)
    candidate_id: Mapped[uuid.UUID] = _ref(nullable=False)
    requisition_id: Mapped[uuid.UUID] = _ref(nullable=False)
    designation: Mapped[str] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(60))
    location: Mapped[str | None] = mapped_column(String(120))
    employment_type: Mapped[str | None] = mapped_column(String(20))
    manager_id: Mapped[uuid.UUID | None] = _ref(index=False)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    joining_date: Mapped[date | None] = mapped_column(Date)
    annual_ctc: Mapped[float] = mapped_column(Numeric(14, 2))
    # The break-up exactly as the payroll salary-structure preview returned it.
    breakup: Mapped[dict | None] = mapped_column(JSONB)
    above_budget: Mapped[bool] = mapped_column(Boolean, default=False)
    approvals_required: Mapped[int] = mapped_column(Integer, default=1)
    approvals: Mapped[list] = mapped_column(JSONB, default=list)             # [{by, name, role, at}]
    # draft | approved | sent | accepted | declined | expired | withdrawn
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(400))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[str | None] = mapped_column(String(64))
    sent_by_role: Mapped[str | None] = mapped_column(String(40))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_via: Mapped[str | None] = mapped_column(String(20))             # link | hr
    signed_name: Mapped[str | None] = mapped_column(String(200))
    letter: Mapped[dict | None] = mapped_column(JSONB)                       # what the letter engine returned
    letter_note: Mapped[str | None] = mapped_column(String(400))
    onboarding_session_id: Mapped[str | None] = mapped_column(String(64))
    onboarding_note: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
