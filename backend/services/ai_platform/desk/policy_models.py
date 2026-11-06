"""HR Desk tables (stream D, 2026-10-25) — policy clause store, sessions, audit, IC referrals.

On ``PlatformBase`` with generic column types (String / JSON / DateTime) so the unit tests run the
same code on SQLite; created at boot by ``desk.ensure_tables()`` (additive, nothing existing is
altered) and by Alembic ``0016_desk`` (re-runs every service's boot DDL).

  desk_policies          one policy per org + key: title, owner, status draft | approved | retired
  desk_policy_clauses    versioned, effective-dated, SCOPED clauses; retrieval runs only over
                         status = approved rows (approval = SME sign-off)
  desk_sessions          one Ask-HR conversation: outcome, counters the hand-off triggers use
  desk_turns             one row per question; HARD-ROUTED turns keep class + timestamp only
                         (question / answer columns stay NULL — confidentiality, PIPEDA minimisation)
  desk_answer_audit      APPEND-ONLY row per answer (access logging and review under PIPEDA safeguards,
                         kept ≥ 1 year; same Postgres as the rest of the data)
  desk_ic_referrals      SEALED: org, employee_id, created_at, channel — NO free text. Visible only to
                         the designated recipient(s) (desk.ic_access) — never to HR admins / managers
  desk_privacy_grievances privacy complaint raised inside the desk, 30-day response clock (PIPEDA s.8(3))
  desk_feedback          CSAT thumbs per turn (self-solved definition uses it as an optional signal)
"""
from __future__ import annotations

import sys
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402

POLICY_STATES = ("draft", "approved", "retired")
CLAUSE_STATES = ("draft", "approved", "retired")
SESSION_OUTCOMES = ("open", "self_solved", "handed_off", "hard_routed", "abandoned", "unresolved")


def _pk():
    return mapped_column(String(36), primary_key=True, default=new_id)


def _org():
    return mapped_column(String(36), index=True, nullable=False)


class DeskPolicy(PlatformBase):
    __tablename__ = "desk_policies"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    key: Mapped[str] = mapped_column(String(60), index=True)                 # slug, unique per org (code-enforced)
    title: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(200))                   # SME / policy owner (name or e-mail)
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    source_doc: Mapped[str | None] = mapped_column(String(200))              # docstore id / file name / ops_policies id
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class DeskPolicyClause(PlatformBase):
    """One VERSION of one clause. ``clause_id`` is stable across versions (e.g. "4.2");
    ``version`` increments; an approved version is immutable — editing it creates a new draft."""
    __tablename__ = "desk_policy_clauses"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    policy_id: Mapped[str] = mapped_column(String(36), index=True)
    clause_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    effective_from: Mapped[str | None] = mapped_column(String(10))           # YYYY-MM-DD (calendar date, org zone)
    effective_to: Mapped[str | None] = mapped_column(String(10))             # inclusive; NULL = open
    scope: Mapped[dict] = mapped_column(JSON, default=dict)                  # {locations, grades, employment_types, roles} — empty list = everyone
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_ready_score: Mapped[int] = mapped_column(Integer, default=0)          # 0-100 heuristic (desk.policy_store.ai_ready_score)
    ai_ready_notes: Mapped[list] = mapped_column(JSON, default=list)         # what holds the score down
    source_doc: Mapped[str | None] = mapped_column(String(200))
    order_no: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeskSession(PlatformBase):
    __tablename__ = "desk_sessions"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    role: Mapped[str | None] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(12), default="chat")         # chat | mcp | mobile
    outcome: Mapped[str] = mapped_column(String(16), default="open", index=True)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    answered: Mapped[int] = mapped_column(Integer, default=0)                # cited / structured answers
    abstains: Mapped[int] = mapped_column(Integer, default=0)
    unresolved: Mapped[int] = mapped_column(Integer, default=0)              # repeated / thumbs-down / abstain attempts
    ticket_id: Mapped[str | None] = mapped_column(String(64))
    handoff_reason: Mapped[str | None] = mapped_column(String(40))
    csat: Mapped[str | None] = mapped_column(String(8))                      # up | down
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # phase 2 (P2-C): one desk session = one run of agent hr_desk (ai_agent_runs) when the add-on is on
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)


class DeskTurn(PlatformBase):
    __tablename__ = "desk_turns"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    intent: Mapped[str] = mapped_column(String(40), default="policy", index=True)   # pay | leave | policy:<key> | <hard class>
    hard_routed: Mapped[bool] = mapped_column(Boolean, default=False)
    question_shielded: Mapped[str | None] = mapped_column(Text)              # NULL when hard-routed
    answer_shielded: Mapped[str | None] = mapped_column(Text)                # NULL when hard-routed
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    cited: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[str | None] = mapped_column(String(8))                    # up | down
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class DeskAnswerAudit(PlatformBase):
    """Append-only. No UPDATE / DELETE route exists for this table."""
    __tablename__ = "desk_answer_audit"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    actor_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36))
    intent_class: Mapped[str] = mapped_column(String(40), default="policy")
    retrieved: Mapped[list] = mapped_column(JSON, default=list)              # [{clause_pk, policy, clause_id, version}]
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))              # sha256 of the static prefix + template
    inputs_tokenised: Mapped[str | None] = mapped_column(Text)               # SHIELDED question (placeholders only)
    answer_digest: Mapped[str | None] = mapped_column(String(64))            # sha256 of the delivered answer
    citations: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    validation: Mapped[str | None] = mapped_column(String(40))               # ok | rejected:<reason> | abstain:<reason> | hard_route:<class>
    escalation_reason: Mapped[str | None] = mapped_column(String(40))
    ticket_id: Mapped[str | None] = mapped_column(String(64))
    reviewer_actions: Mapped[list] = mapped_column(JSON, default=list)       # appended by later rows, never edited here
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    # phase 2 (P2-C): the hash-chained ai_agent_action_log event this row mirrors (agentic_log.append_event)
    agentic_event_id: Mapped[str | None] = mapped_column(String(36), index=True)


# Columns added after 0016_desk — create_all never ALTERs, so desk.ensure_tables runs these on Postgres (idempotent).
DESK_COLUMN_DDL = [
    "ALTER TABLE desk_sessions ADD COLUMN IF NOT EXISTS run_id VARCHAR(36)",
    "CREATE INDEX IF NOT EXISTS ix_desk_sessions_run_id ON desk_sessions (run_id)",
    "ALTER TABLE desk_answer_audit ADD COLUMN IF NOT EXISTS agentic_event_id VARCHAR(36)",
    "CREATE INDEX IF NOT EXISTS ix_desk_answer_audit_agentic_event_id ON desk_answer_audit (agentic_event_id)",
]


class DeskIcReferral(PlatformBase):
    """Sealed sexual-harassment referral: WHO and WHEN only. The free text was never stored anywhere."""
    __tablename__ = "desk_ic_referrals"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    employee_id: Mapped[str | None] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36))
    channel: Mapped[str] = mapped_column(String(12), default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(36))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeskPrivacyGrievance(PlatformBase):
    __tablename__ = "desk_privacy_grievances"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    details_shielded: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)   # open | in_progress | closed
    dpo_contact: Mapped[str | None] = mapped_column(String(300))
    compliance_ref: Mapped[str | None] = mapped_column(String(64))           # privacy-centre request ref when mirrored to compliance
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # raised_at + 30 days (PIPEDA s.8(3))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeskFeedback(PlatformBase):
    __tablename__ = "desk_feedback"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36))
    rating: Mapped[str] = mapped_column(String(8))                           # up | down
    comment_shielded: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


DESK_TABLES = [t.__table__ for t in (DeskPolicy, DeskPolicyClause, DeskSession, DeskTurn, DeskAnswerAudit,
                                     DeskIcReferral, DeskPrivacyGrievance, DeskFeedback)]
