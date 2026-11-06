"""Headcount planning — positions (ops service, shared Postgres, row-level org_id). Stream D7.

eng_positions   one row per budgeted seat: title / grade / department / entity / location / budgeted CTC,
                status open | filled | frozen, who fills it (actual CTC snapshot), optional link to a
                hiring requisition (read-only reference — the hiring module is never written to from here).
eng_retention_actions   retention actions log (analytics / flight-risk follow-ups).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _org():
    return mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


class Position(Base):
    __tablename__ = "eng_positions"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    code: Mapped[str] = mapped_column(String(30), index=True)              # POS-0001
    title: Mapped[str] = mapped_column(String(200))
    grade: Mapped[str | None] = mapped_column(String(40))
    department: Mapped[str | None] = mapped_column(String(120), index=True)
    entity: Mapped[str | None] = mapped_column(String(200))                 # legal entity name / id (D4)
    location: Mapped[str | None] = mapped_column(String(120))
    fiscal_year: Mapped[str | None] = mapped_column(String(9))              # 2026-27
    budgeted_ctc: Mapped[float] = mapped_column(Float, default=0.0)         # annual, CAD
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)   # open | filled | frozen
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    actual_ctc: Mapped[float | None] = mapped_column(Float)                 # annual CTC of the holder (snapshot)
    filled_on: Mapped[date | None] = mapped_column(Date)
    requisition_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    requisition_number: Mapped[str | None] = mapped_column(String(40))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    manager_name: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RetentionAction(Base):
    __tablename__ = "eng_retention_actions"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(400))                        # what will be done
    kind: Mapped[str] = mapped_column(String(30), default="conversation")   # conversation | pay_review | role_change | learning | other
    owner_name: Mapped[str | None] = mapped_column(String(200))
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    due_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)   # open | done | dropped
    outcome: Mapped[str | None] = mapped_column(Text)
    risk_score_at: Mapped[int | None] = mapped_column(Integer)
    risk_band_at: Mapped[str | None] = mapped_column(String(10))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


POSITIONS_DDL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_positions_code ON eng_positions (org_id, code)",
]
