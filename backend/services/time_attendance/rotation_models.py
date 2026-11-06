"""Gap G2 (2026-10-23) — shift rotation patterns, assignments, swap requests, settings.

ta_rotation_patterns     an N-day cycle of shift codes / OFF + the shift definitions (JSON)
ta_rotation_assignments  employee (or a department = a crew) on a pattern from a date, with a cycle offset
ta_shift_swaps           two employees swap rostered shifts: colleague accepts → manager approves (one inbox)
ta_rotation_settings     company rules: minimum rest between shifts, weeks generated ahead, nightly auto-generate

Generated shifts are ordinary ``ta_roster_shifts`` rows (source = pattern), so the roster grid, the
publish gate, attendance and payroll read them exactly like hand-made rows.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class RotationPattern(Base):
    __tablename__ = "ta_rotation_patterns"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(20))
    template: Mapped[str | None] = mapped_column(String(40))             # the template it was started from
    cycle: Mapped[list] = mapped_column(JSONB, default=list)             # ["A","A",…,"OFF"]
    shifts: Mapped[dict] = mapped_column(JSONB, default=dict)            # {"A": {name,start,end,break_minutes,hours,color}}
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RotationAssignment(Base):
    __tablename__ = "ta_rotation_assignments"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    pattern_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)   # one person …
    department: Mapped[str | None] = mapped_column(String(120))                               # … or a whole team
    crew: Mapped[str | None] = mapped_column(String(40))                                      # label, e.g. "Crew B"
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    offset_days: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ShiftSwap(Base):
    __tablename__ = "ta_shift_swaps"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    requester_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    requester_shift_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    partner_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    partner_shift_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    snapshot: Mapped[dict | None] = mapped_column(JSONB)          # both shifts as they were when asked
    reason: Mapped[str | None] = mapped_column(String(400))
    # pending_partner → pending_manager → approved | declined (colleague) | rejected (manager) | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending_partner", index=True)
    requested_by_uid: Mapped[str | None] = mapped_column(String(64))
    partner_note: Mapped[str | None] = mapped_column(String(400))
    partner_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RotationSettings(Base):
    __tablename__ = "ta_rotation_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    min_rest_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=11)   # COMPANY rule, not a statutory figure
    weeks_ahead: Mapped[int] = mapped_column(Integer, default=4)
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=False)
    swaps_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_auto_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
