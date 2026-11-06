"""Effective-dated compensation history (employee service).

One row per salary in force for an employee. Rows are never edited once written:
a wrong salary is corrected by a NEW row (a correcting revision). `effective_to`
is only ever filled in to close a row the day before the next one starts.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class CompHistory(Base):
    __tablename__ = "comp_history"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date)
    pay_type: Mapped[str] = mapped_column(String(20), default="salary")          # salary | hourly
    annual_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))          # annual salary / CTC
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    components: Mapped[dict | None] = mapped_column(JSONB)                       # monthly {CODE: amount}
    reason: Mapped[str | None] = mapped_column(String(60))                       # opening | annual_increment | promotion | …
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="revision")         # opening | revision | arrears | direct_edit
    source_revision_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    approved_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
