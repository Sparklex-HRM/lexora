"""Employee service — which legal entity an employee belongs to, over time (stream D4).

`employees.entity_id` holds the CURRENT entity. This table keeps the history: one row per
spell, closed (to_date) when the person is transferred to another group company. A
transfer is also written to career_changes (change_type "entity_transfer") so it shows in
the career timeline. Each legal entity is its own employer for CRA (own payroll account),
so payroll reads this table to know which entity's T4 / ROE carries which spell.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class EmployeeEntityHistory(Base):
    __tablename__ = "employee_entity_history"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    entity_name: Mapped[str | None] = mapped_column(String(200))        # denormalised for history display
    from_date: Mapped[date | None] = mapped_column(Date)
    to_date: Mapped[date | None] = mapped_column(Date)                  # NULL = current spell
    reason: Mapped[str | None] = mapped_column(String(200))
    career_change_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(12), default="current")  # current | closed | scheduled
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
