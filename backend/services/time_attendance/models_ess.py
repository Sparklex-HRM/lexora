"""Employee portal (2026-10-16) — time tables, created by create_all at boot (migration 0007).

ta_permissions     short-hours permission (leave the office for 1–2 hours) → the approval chain
ta_ess_policy      company policy for permissions (per org; HR edits): how many a month, how long
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class Permission(Base):
    __tablename__ = "ta_permissions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    from_time: Mapped[str] = mapped_column(String(5))           # "HH:MM" wall clock
    to_time: Mapped[str] = mapped_column(String(5))
    hours: Mapped[float] = mapped_column(Numeric(4, 2), default=0)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)   # pending|approved|rejected|cancelled
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TimeEssPolicy(Base):
    __tablename__ = "ta_ess_policy"
    __table_args__ = (UniqueConstraint("org_id", name="uq_ta_ess_policy_org"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    permission_max_per_month: Mapped[int] = mapped_column(Integer, default=2)
    permission_max_hours: Mapped[float] = mapped_column(Numeric(4, 2), default=2)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
