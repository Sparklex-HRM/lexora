"""Employee service — organization master data. Org-scoped reference tables the
employee form, onboarding, leave, and PAYROLL draw their dropdowns from."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class _MasterBase:
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Department(_MasterBase, Base):
    __tablename__ = "master_departments"
    description: Mapped[str | None] = mapped_column(String(300))
    cost_center: Mapped[str | None] = mapped_column(String(30))
    gl_code: Mapped[str | None] = mapped_column(String(30))


class Designation(_MasterBase, Base):
    __tablename__ = "master_designations"
    level: Mapped[str | None] = mapped_column(String(50))            # Junior / Senior / Lead
    job_family: Mapped[str | None] = mapped_column(String(60))
    overtime_exempt: Mapped[bool] = mapped_column(Boolean, default=False)


class Grade(_MasterBase, Base):
    """Grade / band (e.g. G1, M2) — picked on the employee form."""
    __tablename__ = "master_grades"
    level: Mapped[int | None] = mapped_column(Integer)                # sort order, 1 = most junior
    description: Mapped[str | None] = mapped_column(String(300))


class Location(_MasterBase, Base):
    __tablename__ = "master_locations"
    city: Mapped[str | None] = mapped_column(String(100))
    province: Mapped[str | None] = mapped_column(String(2))          # ON / BC / AB …
    address: Mapped[str | None] = mapped_column(String(300))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    timezone: Mapped[str | None] = mapped_column(String(40))
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)


class LeaveType(_MasterBase, Base):
    __tablename__ = "master_leave_types"
    days_per_year: Mapped[float] = mapped_column(Numeric(5, 1), default=0)
    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    accrual: Mapped[str | None] = mapped_column(String(30))          # annual / monthly / per_pay
    carryover_max: Mapped[int] = mapped_column(Integer, default=0)
    # 2026-10-16 employee portal: usable (taken at will, counted in "days you can take") |
    # event (maternity, paternity … shown separately). Blank = decided by the code.
    category: Mapped[str | None] = mapped_column(String(20))
    # company policy: a request LONGER than this many days needs a supporting document (blank = never)
    attachment_after_days: Mapped[int | None] = mapped_column(Integer)


# ── payroll-facing masters ───────────────────────────────────────────────────

class PayComponent(_MasterBase, Base):
    """Earning / deduction / employer code — the vocabulary the payroll engine
    runs on. The statutory flags decide how each amount hits CPP/EI/tax/vacation."""
    __tablename__ = "master_pay_components"
    kind: Mapped[str] = mapped_column(String(20), default="earning")  # earning | deduction | employer
    calc_method: Mapped[str | None] = mapped_column(String(20))       # flat | percent | rate_x_hours
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)
    pensionable: Mapped[bool] = mapped_column(Boolean, default=True)  # CPP
    insurable: Mapped[bool] = mapped_column(Boolean, default=True)    # EI
    vacationable: Mapped[bool] = mapped_column(Boolean, default=True)
    non_periodic: Mapped[bool] = mapped_column(Boolean, default=False)  # bonus/retro → CRA bonus method
    gl_account: Mapped[str | None] = mapped_column(String(30))
    t4_box: Mapped[str | None] = mapped_column(String(10))


class FiscalYear(_MasterBase, Base):
    """Org GL/reporting year — DISTINCT from the CRA calendar tax year that
    drives YTD + T4 (that is always Jan–Dec)."""
    __tablename__ = "master_fiscal_years"
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="open")   # open | closed
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class CostCenter(_MasterBase, Base):
    __tablename__ = "master_cost_centers"
    gl_segment: Mapped[str | None] = mapped_column(String(30))


class BenefitPlan(_MasterBase, Base):
    __tablename__ = "master_benefit_plans"
    carrier: Mapped[str | None] = mapped_column(String(100))
    ee_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    er_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    taxable_benefit: Mapped[bool] = mapped_column(Boolean, default=False)


class StatHoliday(_MasterBase, Base):
    __tablename__ = "master_stat_holidays"
    holiday_date: Mapped[date | None] = mapped_column(Date)
    province: Mapped[str | None] = mapped_column(String(2))
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    # 2026-10-16: an OPTIONAL (restricted) holiday — employees pick up to the company's yearly quota
    is_optional: Mapped[bool | None] = mapped_column(Boolean, default=False)
