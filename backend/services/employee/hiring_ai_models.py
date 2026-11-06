"""Hiring G6 (2026-10-23) — parsed candidate profile + advisory fit scores. NEW tables only."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class HireCandidateProfile(Base):
    """What the résumé says, after HR checked it. One per candidate. `status` parsed (not yet
    checked by a person) | confirmed. CTC figures are salary data: HR only (redacted for others)."""
    __tablename__ = "hire_candidate_profiles"
    __table_args__ = (UniqueConstraint("org_id", "candidate_id", name="hire_cand_profile_uq"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    experience_years: Mapped[float | None] = mapped_column(Numeric(5, 1))
    education: Mapped[list] = mapped_column(JSONB, default=list)            # [{degree, level, line}]
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    current_ctc: Mapped[float | None] = mapped_column(Numeric(14, 2))
    expected_ctc: Mapped[float | None] = mapped_column(Numeric(14, 2))
    current_title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    method: Mapped[str] = mapped_column(String(20), default="rules")        # rules | rules+ai
    status: Mapped[str] = mapped_column(String(20), default="parsed")       # parsed | confirmed
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    confirmed_by: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HireMatchScore(Base):
    """Advisory fit of one application to its posting, with the working. Latest wins; history kept."""
    __tablename__ = "hire_match_scores"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)               # lines, matched / missing, ai opinion
    method: Mapped[str] = mapped_column(String(20), default="rules")
    by_user: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
