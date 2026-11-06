"""Saved custom reports + their e-mail schedules (PlatformBase, generic types)."""
import sys
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402


class SavedReport(PlatformBase):
    __tablename__ = "rb_saved_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160))
    dataset: Mapped[str] = mapped_column(String(40))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_name: Mapped[str | None] = mapped_column(String(200))
    owner_role: Mapped[str | None] = mapped_column(String(40))
    owner_employee_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReportSchedule(PlatformBase):
    __tablename__ = "rb_schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    report_id: Mapped[str] = mapped_column(String(36), index=True)
    cadence: Mapped[str] = mapped_column(String(10), default="weekly")        # daily | weekly | monthly
    hour_utc: Mapped[int] = mapped_column(Integer, default=6)                 # legacy rows (UTC hour); new rows set local_time
    # 2026-10-25 time-zone rule: the send time is the org's WALL clock ('09:00' in America/Toronto); weekday and
    # day_of_month are local too. next_run_at stays a UTC instant. NULL local_time = old row, hour_utc applies.
    local_time: Mapped[str | None] = mapped_column(String(5))
    tz: Mapped[str | None] = mapped_column(String(64))
    weekday: Mapped[int] = mapped_column(Integer, default=0)                   # Monday = 0
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    user_ids: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_rows: Mapped[int | None] = mapped_column(Integer)
    last_status: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


RB_TABLES = [SavedReport.__table__, ReportSchedule.__table__]
