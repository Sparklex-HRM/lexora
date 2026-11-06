"""Hiring D2 (recruiting depth) — all NEW tables, prefix ``hire_``.

Job-board publishing · competency scorecards · interview slots + calendar sync ·
offer approval chain · employee referrals + bonus · candidate communications.
Nothing here alters an existing table (the one new column on ``hire_documents``
is added with ADD COLUMN IF NOT EXISTS in ``hiring_d2_init``, see hiring_careers.py).
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


BOARDS = ("indeed", "google_jobs", "linkedin")


class HirePostingBoard(Base):
    """Per posting, per job board: is it published there, and since when."""
    __tablename__ = "hire_posting_boards"
    __table_args__ = (UniqueConstraint("posting_id", "board", name="hire_posting_board_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    posting_id: Mapped[uuid.UUID] = _ref(nullable=False)
    board: Mapped[str] = mapped_column(String(20))                           # indeed | google_jobs | linkedin
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unpublished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireScorecardTemplate(Base):
    """Competency scorecard: one per requisition, or the org default (requisition_id null)."""
    __tablename__ = "hire_scorecard_templates"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    requisition_id: Mapped[uuid.UUID | None] = _ref()
    name: Mapped[str] = mapped_column(String(120))
    competencies: Mapped[list] = mapped_column(JSONB, default=list)          # [{key, label, weight, description}]
    hide_names: Mapped[bool] = mapped_column(Boolean, default=False)         # bias guard: interviewer sees no name
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireScorecardAnswer(Base):
    """One competency rating inside one interviewer's feedback."""
    __tablename__ = "hire_scorecard_answers"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    feedback_id: Mapped[uuid.UUID] = _ref(nullable=False)
    interview_id: Mapped[uuid.UUID] = _ref(nullable=False)
    application_id: Mapped[uuid.UUID] = _ref(nullable=False)
    interviewer_employee_id: Mapped[uuid.UUID] = _ref(index=False, nullable=False)
    competency_key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    weight: Mapped[float] = mapped_column(Numeric(6, 2), default=1)
    rating: Mapped[int] = mapped_column(Integer)                             # 1..5
    note: Mapped[str | None] = mapped_column(Text)


class HireInterviewSlots(Base):
    """Proposed slots for one interview + the candidate's single-use pick link."""
    __tablename__ = "hire_interview_slots"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    interview_id: Mapped[uuid.UUID] = _ref(nullable=False)
    application_id: Mapped[uuid.UUID] = _ref(nullable=False)
    slots: Mapped[list] = mapped_column(JSONB, default=list)                 # [{start, end}] ISO 8601 with offset
    duration_min: Mapped[int] = mapped_column(Integer, default=45)
    timezone: Mapped[str] = mapped_column(String(60), default="America/Toronto")
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="proposed")     # proposed | picked | expired | cancelled
    picked_slot: Mapped[dict | None] = mapped_column(JSONB)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_message: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireCalendarConnection(Base):
    """One organiser calendar per org per provider (Google / Microsoft), tokens encrypted."""
    __tablename__ = "hire_calendar_connections"
    __table_args__ = (UniqueConstraint("org_id", "provider", name="hire_cal_conn_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    provider: Mapped[str] = mapped_column(String(20))                        # google | microsoft
    connected_by_user_id: Mapped[str | None] = mapped_column(String(64))
    connected_by_name: Mapped[str | None] = mapped_column(String(200))
    account_email: Mapped[str | None] = mapped_column(String(200))
    access_token_enc: Mapped[str | None] = mapped_column(Text)               # shared.utils.crypto token
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="connected")    # connected | error | revoked
    last_error: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireCalendarEvent(Base):
    """The provider event behind one interview (created / updated / cancelled through the API)."""
    __tablename__ = "hire_calendar_events"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    interview_id: Mapped[uuid.UUID] = _ref(nullable=False)
    provider: Mapped[str] = mapped_column(String(20))
    connection_id: Mapped[uuid.UUID | None] = _ref(index=False)
    event_id: Mapped[str | None] = mapped_column(String(200))
    join_url: Mapped[str | None] = mapped_column(String(600))                # Meet / Teams link the API returned
    html_link: Mapped[str | None] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(String(20), default="created")      # created | updated | cancelled | failed
    last_error: Mapped[str | None] = mapped_column(String(400))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireOfferApprovalStep(Base):
    """The approval chain of one offer, one row per step, in order."""
    __tablename__ = "hire_offer_approval_steps"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    offer_id: Mapped[uuid.UUID] = _ref(nullable=False)
    step_no: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(120))
    approver_kind: Mapped[str] = mapped_column(String(20))                   # manager | role | user
    approver_role: Mapped[str | None] = mapped_column(String(40))
    approver_employee_id: Mapped[uuid.UUID | None] = _ref(index=False)
    approver_user_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="waiting")      # waiting | pending | approved | rejected | skipped
    chain_source: Mapped[str] = mapped_column(String(20), default="default")  # registry (D1) | default
    inbox_item_id: Mapped[str | None] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_by_name: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireReferralRule(Base):
    """Referral bonus for one requisition: amount, payable N days after the hire joins."""
    __tablename__ = "hire_referral_rules"
    __table_args__ = (UniqueConstraint("requisition_id", name="hire_referral_rule_req_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    requisition_id: Mapped[uuid.UUID] = _ref(nullable=False)
    bonus_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    payable_after_days: Mapped[int] = mapped_column(Integer, default=90)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireReferral(Base):
    """An employee's referral of one person for one requisition."""
    __tablename__ = "hire_referrals"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    referrer_employee_id: Mapped[uuid.UUID] = _ref(nullable=False)
    referrer_name: Mapped[str | None] = mapped_column(String(200))
    requisition_id: Mapped[uuid.UUID] = _ref(nullable=False)
    posting_id: Mapped[uuid.UUID | None] = _ref(index=False)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(30))
    link_url: Mapped[str | None] = mapped_column(String(400))                # LinkedIn / portfolio link
    note: Mapped[str | None] = mapped_column(String(1000))
    candidate_id: Mapped[uuid.UUID | None] = _ref(index=False)
    application_id: Mapped[uuid.UUID | None] = _ref(index=False)
    # submitted | duplicate | in_process | hired | closed
    status: Mapped[str] = mapped_column(String(20), default="submitted", index=True)
    status_note: Mapped[str | None] = mapped_column(String(400))
    bonus_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))       # snapshot of the rule at referral time
    bonus_payable_after_days: Mapped[int | None] = mapped_column(Integer)
    # none | waiting_join | waiting_days | due | exported | paid | void
    bonus_status: Mapped[str] = mapped_column(String(20), default="none")
    bonus_due_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireReferralBonusDue(Base):
    """A referral bonus that is payable. Exposed to payroll (see hiring_referrals.py)."""
    __tablename__ = "hire_referral_bonuses_due"
    __table_args__ = (UniqueConstraint("referral_id", name="hire_referral_bonus_once_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    referral_id: Mapped[uuid.UUID] = _ref(nullable=False)
    employee_id: Mapped[uuid.UUID] = _ref(nullable=False)                    # the referrer, who is paid
    employee_name: Mapped[str | None] = mapped_column(String(200))
    hired_name: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    due_on: Mapped[date] = mapped_column(Date)
    pay_month: Mapped[str] = mapped_column(String(7))                        # YYYY-MM the bonus is paid in
    status: Mapped[str] = mapped_column(String(20), default="due", index=True)   # due | exported | paid | cancelled
    pay_entry_ref: Mapped[str | None] = mapped_column(String(80))            # payroll feed row id, when exported
    note: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireCommsTemplate(Base):
    """What the candidate is told at each event, per channel."""
    __tablename__ = "hire_comms_templates"
    __table_args__ = (UniqueConstraint("org_id", "event", "channel", name="hire_comms_tpl_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    event: Mapped[str] = mapped_column(String(40))                           # stage key | interview_scheduled | interview_reminder | offer_sent
    channel: Mapped[str] = mapped_column(String(12))                         # email | whatsapp
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    whatsapp_template: Mapped[str | None] = mapped_column(String(120))       # the approved Meta template name
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireCandidateComms(Base):
    """Per candidate: the channels they agreed to."""
    __tablename__ = "hire_candidate_comms"
    __table_args__ = (UniqueConstraint("candidate_id", name="hire_cand_comms_uq"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    candidate_id: Mapped[uuid.UUID] = _ref(nullable=False)
    email_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_consent_text: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HireCommsLog(Base):
    """Every message attempt to a candidate, with the TRUE outcome."""
    __tablename__ = "hire_comms_log"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    candidate_id: Mapped[uuid.UUID] = _ref(nullable=False)
    application_id: Mapped[uuid.UUID | None] = _ref(index=False)
    event: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(12))
    to_masked: Mapped[str | None] = mapped_column(String(120))
    # sent | not_configured | opted_out | no_address | disabled | failed
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(400))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    subject: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
