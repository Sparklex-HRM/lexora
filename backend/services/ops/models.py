"""Operations / Staffing service — the PSA + multi-branch backbone.

Branches (org→branch hierarchy), Clients you place workers with, Projects,
Vendors/subcontractors, Roster assignments (employee → client/project for a
date range, with bill-rate vs pay-rate margin), and client Invoices.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class _OrgBase:
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(180))
    code: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Branch(_OrgBase, Base):
    __tablename__ = "ops_branches"
    city: Mapped[str | None] = mapped_column(String(100))
    province: Mapped[str | None] = mapped_column(String(2))
    address: Mapped[str | None] = mapped_column(String(300))
    is_hq: Mapped[bool] = mapped_column(Boolean, default=False)


class Client(_OrgBase, Base):
    __tablename__ = "ops_clients"
    contact_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(100))
    province: Mapped[str | None] = mapped_column(String(2))
    payment_terms: Mapped[str | None] = mapped_column(String(40))   # Net 15/30/45


class Project(_OrgBase, Base):
    __tablename__ = "ops_projects"
    client: Mapped[str | None] = mapped_column(String(180))
    status: Mapped[str | None] = mapped_column(String(30), default="active")
    budget: Mapped[float | None] = mapped_column(Numeric(14, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)


class Vendor(_OrgBase, Base):
    __tablename__ = "ops_vendors"
    contact_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    service_type: Mapped[str | None] = mapped_column(String(120))


class RosterAssignment(_OrgBase, Base):
    __tablename__ = "ops_roster"
    # `name` = employee name; reuse the base column.
    client: Mapped[str | None] = mapped_column(String(180))
    project: Mapped[str | None] = mapped_column(String(180))
    branch: Mapped[str | None] = mapped_column(String(180))
    role: Mapped[str | None] = mapped_column(String(120))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    bill_rate: Mapped[float | None] = mapped_column(Numeric(10, 2))
    pay_rate: Mapped[float | None] = mapped_column(Numeric(10, 2))


class Invoice(_OrgBase, Base):
    __tablename__ = "ops_invoices"
    # `name` = invoice number; reuse the base column.
    client: Mapped[str | None] = mapped_column(String(180))
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    tax: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total: Mapped[float | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str | None] = mapped_column(String(30), default="draft")   # draft|sent|paid|overdue


class Document(_OrgBase, Base):
    __tablename__ = "ops_documents"
    # `name` = document title.
    category: Mapped[str | None] = mapped_column(String(60))     # contract|td1|void_cheque|permit|policy
    employee: Mapped[str | None] = mapped_column(String(180))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(30), default="on_file")   # on_file|pending_signature|expired


class Task(_OrgBase, Base):
    __tablename__ = "ops_tasks"
    # `name` = task title.
    assignee: Mapped[str | None] = mapped_column(String(180))
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str | None] = mapped_column(String(20), default="normal")   # low|normal|high
    status: Mapped[str | None] = mapped_column(String(20), default="open")       # open|in_progress|done


class Policy(_OrgBase, Base):
    __tablename__ = "ops_policies"
    # `name` = policy title.
    version: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(60))
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(20), default="active")     # draft|active|archived
