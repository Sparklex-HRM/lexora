"""Time & Attendance — data model (shared Postgres, row-level org_id).

The chain the whole module exists to move: a punch → an attendance day →
an approved timesheet line → a pay-run `entries` earning (REG/OT/STAT).

Phase 1  WorkSchedule, ScheduleAssignment, Punch, AttendanceDay
Phase 2  Timesheet, TimesheetLine, Regularization
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


# ── Phase 1 — schedules & capture ─────────────────────────────────────────────

class WorkSchedule(Base):
    """A named shift pattern (org-level). Drives an employee's planned hours."""
    __tablename__ = "ta_work_schedules"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(30))
    days_of_week: Mapped[list] = mapped_column(JSONB, default=list)   # Python weekday()s: Mon=0..Sun=6
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")   # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), default="17:00")
    break_minutes: Mapped[int] = mapped_column(Integer, default=30)
    daily_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=8)
    timezone: Mapped[str] = mapped_column(String(40), default="America/Toronto")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Phase 4 — optional geofence for on-site punch validation.
    site_lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    site_lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    geofence_m: Mapped[int | None] = mapped_column(Integer)   # allowed radius in metres
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RosterShift(Base):
    """A published shift on a specific date — the roster (Phase 4). Distinct from
    ScheduleAssignment (the employee's default pattern); a roster shift overrides
    the pattern for that day."""
    __tablename__ = "ta_roster_shifts"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")
    end_time: Mapped[str] = mapped_column(String(5), default="17:00")
    planned_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=8)
    location: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="planned")   # planned | published
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # G2 2026-10-23 rotation patterns: who made the row and what it is (a generated row is an ordinary roster row)
    source: Mapped[str | None] = mapped_column(String(12), default="manual")     # manual | pattern | swap
    kind: Mapped[str | None] = mapped_column(String(8), default="work")          # work | off (weekly off / holiday)
    shift_code: Mapped[str | None] = mapped_column(String(8))
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class ScheduleAssignment(Base):
    """Which pattern an employee works (their default expected hours). One active
    row per employee — assigning again supersedes the prior one."""
    __tablename__ = "ta_schedule_assignments"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    schedule_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Punch(Base):
    """A raw clock event. Append-only; attendance is derived from these."""
    __tablename__ = "ta_punches"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    direction: Mapped[str] = mapped_column(String(12))   # in | out | break_start | break_end
    source: Mapped[str] = mapped_column(String(16), default="web")   # web | kiosk | mobile | admin
    note: Mapped[str | None] = mapped_column(String(200))
    # Optional geofence capture (Phase 4 enforces; captured opportunistically now)
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    geofence_ok: Mapped[bool | None] = mapped_column(Boolean)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AttendanceDay(Base):
    """Derived daily record — plan vs actual. One per employee per date."""
    __tablename__ = "ta_attendance_days"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    planned_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    actual_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    first_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    # scheduled | present | late | on_shift | absent | leave | holiday | rest_day
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


# ── Phase 2 — timesheets, approvals, regularization ───────────────────────────

class Timesheet(Base):
    """Payable hours for an employee over a pay period. State-machine mirrors
    onboarding: draft → submitted → approved → locked  (or rejected)."""
    __tablename__ = "ta_timesheets"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    employee_number: Mapped[str | None] = mapped_column(String(30))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    province: Mapped[str | None] = mapped_column(String(2))
    pay_type: Mapped[str | None] = mapped_column(String(20))          # salary | hourly
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(10, 2))
    pay_frequency: Mapped[str | None] = mapped_column(String(20))
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    reg_hours: Mapped[float] = mapped_column(Numeric(7, 2), default=0)
    ot_hours: Mapped[float] = mapped_column(Numeric(7, 2), default=0)       # 1.5×
    ot2_hours: Mapped[float] = mapped_column(Numeric(7, 2), default=0)      # 2× (BC daily >12h)
    stat_hours: Mapped[float] = mapped_column(Numeric(7, 2), default=0)
    stat_pay: Mapped[float] = mapped_column(Numeric(9, 2), default=0)       # computed avg-daily-wage $
    total_hours: Mapped[float] = mapped_column(Numeric(7, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TimesheetLine(Base):
    """One day's payable split on a timesheet."""
    __tablename__ = "ta_timesheet_lines"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    timesheet_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date)
    reg_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    ot_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    ot2_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    stat_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    # Phase: employee self-entry books worked hours to a project (billable/internal).
    hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)          # worked hours (self-entry)
    project_id: Mapped[str | None] = mapped_column(String(64))
    project_name: Mapped[str | None] = mapped_column(String(180))
    billable: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_center: Mapped[str | None] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(String(200))


class Regularization(Base):
    """A request to correct attendance (missed punch, wrong time)."""
    __tablename__ = "ta_regularizations"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(40))          # missed_punch | wrong_time | forgot | other
    requested_in: Mapped[str | None] = mapped_column(String(5))    # "HH:MM"
    requested_out: Mapped[str | None] = mapped_column(String(5))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
