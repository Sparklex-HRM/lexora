"""Employee portal batch 3 (2026-10-16) — "Raise a request" + personal to-dos.

ess_request_types   one row per request type HR defines; ``fields`` holds the form — a list of
                    definitions in the SAME shape as D1 custom fields (custom_fields_logic)
ess_requests        one request an employee raised: the values, the form as it was when raised
                    (so a later edit of the type never changes an old request), status + decision
ess_todos           an employee's own to-do list (title, due date, done)

All NEW tables (create_all makes them on boot). Nothing existing is altered.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class EssRequestType(Base):
    __tablename__ = "ess_request_types"
    __table_args__ = (UniqueConstraint("org_id", "key", name="ux_ess_request_types_key"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(40))                         # stable snake_case; feeds the chain context
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(600))
    category: Mapped[str | None] = mapped_column(String(60))
    handler_note: Mapped[str | None] = mapped_column(String(300))        # "who handles it" — shown to the employee
    fields: Mapped[list | None] = mapped_column(JSONB)                   # [custom-field definitions]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_by: Mapped[str | None] = mapped_column(String(200))
    updated_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EssRequest(Base):
    __tablename__ = "ess_requests"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    type_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    type_key: Mapped[str] = mapped_column(String(40), index=True)
    type_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(60))
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(220))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    department: Mapped[str | None] = mapped_column(String(120))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    requester_user_id: Mapped[str | None] = mapped_column(String(60))
    answers: Mapped[dict | None] = mapped_column(JSONB)                  # {field key: stored value}
    fields_snapshot: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), index=True, default="pending")   # pending|approved|rejected|withdrawn
    decided_by: Mapped[str | None] = mapped_column(String(60))
    decided_by_name: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EssTodo(Base):
    __tablename__ = "ess_todos"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(60), index=True)          # the login that owns it
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(500))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
