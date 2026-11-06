"""Engagement depth tables — Stream D7 (ops service, shared Postgres, row-level org_id).

eng_survey_schedules      recurring pulse / eNPS surveys (weekly / monthly / quarterly) made from a question set
eng_survey_questions      org-wide survey question bank (tag, type: scale | nps | text | choice)
eng_onboarding_survey_log which employee got which onboarding check-in (30 / 60 / 90 days) — never twice
eng_kudos_budgets         recognition points per manager per month (budget + used)

All NEW tables. Columns added to G4 tables for D7 live in engage_models (ALTER … IF NOT EXISTS).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _org():
    return mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


class SurveySchedule(Base):
    __tablename__ = "eng_survey_schedules"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(12), default="pulse")          # pulse | enps
    cadence: Mapped[str] = mapped_column(String(10), default="monthly")     # weekly | monthly | quarterly
    weekday: Mapped[int] = mapped_column(Integer, default=0)                # 0 = Monday (weekly)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)           # 1..28 (monthly / quarterly)
    open_days: Mapped[int] = mapped_column(Integer, default=7)              # how long each survey stays open
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=True)
    target: Mapped[dict | None] = mapped_column(JSONB)
    threshold: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_on: Mapped[date | None] = mapped_column(Date, index=True)
    last_run_on: Mapped[date | None] = mapped_column(Date)
    last_survey_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    runs: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SurveyQuestion(Base):
    __tablename__ = "eng_survey_questions"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    tag: Mapped[str] = mapped_column(String(60), index=True)                # e.g. "Manager", "Workload", "Growth"
    type: Mapped[str] = mapped_column(String(10), default="scale")          # scale | nps | text | choice
    text: Mapped[str] = mapped_column(String(400))
    options: Mapped[list | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OnboardingSurveyLog(Base):
    __tablename__ = "eng_onboarding_survey_log"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    stage: Mapped[int] = mapped_column(Integer)                             # 30 | 60 | 90
    survey_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KudosBudget(Base):
    __tablename__ = "eng_kudos_budgets"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    manager_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    month: Mapped[str] = mapped_column(String(7), index=True)               # YYYY-MM
    points_budget: Mapped[int] = mapped_column(Integer, default=0)
    points_used: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


ENGAGE2_DDL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_onboarding_log ON eng_onboarding_survey_log (org_id, employee_id, stage)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eng_kudos_budget ON eng_kudos_budgets (org_id, manager_id, month)",
]
