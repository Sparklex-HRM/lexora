"""Compliance service — OHS safety incidents + per-org WCB/WSIB configuration.

The audit trail itself lives in the shared ``audit_events`` table
(``shared/utils/audit.py``) so every service can write to one chain; this service
only reads/verifies it. Safety incidents and WCB rate config are owned here.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class SafetyIncident(Base):
    """An OHS incident: injury / near-miss / illness / property, with follow-up.

    Lost-time days and the WCB claim number tie an incident to the workers'-comp
    process; the register is the employer's OHS record and feeds incident-rate
    reporting (recordables per 100 workers)."""
    __tablename__ = "safety_incidents"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    branch_name: Mapped[str | None] = mapped_column(String(160))
    ref: Mapped[str | None] = mapped_column(String(40), index=True)        # OHS-2026-001
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    reported_on: Mapped[date | None] = mapped_column(Date)
    # injury | near_miss | illness | property | environmental
    incident_type: Mapped[str] = mapped_column(String(20), index=True, default="injury")
    severity: Mapped[str] = mapped_column(String(16), default="minor")     # minor | moderate | serious | critical
    location: Mapped[str | None] = mapped_column(String(200))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    lost_time: Mapped[bool] = mapped_column(Boolean, default=False)        # did it cause time away?
    lost_days: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    wcb_claim_no: Mapped[str | None] = mapped_column(String(40))
    wcb_reportable: Mapped[bool] = mapped_column(Boolean, default=False)
    corrective_action: Mapped[str | None] = mapped_column(Text)
    # open | investigating | corrective_action | closed
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    reported_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WcbRate(Base):
    """Per-org, per-province WCB/WSIB assessment rate (employer premium).

    A rate is dollars of premium per $100 of assessable earnings, set for the
    org by the provincial board's classification of the business. It is
    employer-specific input — NOT a statutory constant — so the org enters it.
    The province's maximum assessable earnings ceiling is a design reference
    flagged for verification in constants (see routers.PROVINCIAL_MAX_ASSESSABLE).
    """
    __tablename__ = "wcb_rates"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    province: Mapped[str] = mapped_column(String(2), index=True)           # ON | BC | AB
    board: Mapped[str | None] = mapped_column(String(20))                  # WSIB | WorkSafeBC | WCB-AB
    rate_per_100: Mapped[float] = mapped_column(Numeric(8, 4), default=0)  # $ per $100 assessable
    account_number: Mapped[str | None] = mapped_column(String(40))
    year: Mapped[int] = mapped_column(Integer, default=2026, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
