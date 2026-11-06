"""Fixed-term / contract employees — the term history (employee service, row-level org_id). 2026-10-22."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class ContractTerm(Base):
    """Append-only: every step on a term-bound employee's contract.
    action: start | correct | recommend | renew | convert | end | alert
    For start / correct / renew the row carries the term (term_no, start_date, end_date)."""
    __tablename__ = "employee_contract_terms"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(16), index=True)
    term_no: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    old_end_date: Mapped[date | None] = mapped_column(Date)
    notice_days: Mapped[int | None] = mapped_column(Integer)
    contract_ref: Mapped[str | None] = mapped_column(String(80))
    recommendation: Mapped[str | None] = mapped_column(String(12))      # renew | convert | end
    alert_days: Mapped[int | None] = mapped_column(Integer)             # 60 / 30 / 7 / 0 (action alert)
    effective_date: Mapped[date | None] = mapped_column(Date)           # convert / end
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(200))
    actor_role: Mapped[str | None] = mapped_column(String(40))
    letter_id: Mapped[str | None] = mapped_column(String(40))
    exit_handover: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
