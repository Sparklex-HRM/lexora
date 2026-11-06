"""Employee portal batch 3 (2026-10-16) — document centre: company policies + acknowledgements.

    emp_policy_docs   one row per policy; holds the CURRENT version (version_no / version_label /
                      effective_date / body / PDF). Earlier versions are kept in `history`.
    emp_policy_acks   "I have read and accept" — one row per policy + version + employee.
                      Publishing a new version bumps version_no, so everyone is pending again;
                      the old acknowledgements stay as the audit trail.

The PDF itself is a shared.utils.docstore StoredDocument (table employee_documents) with
employee_id NULL and doc_type "policy" — it never shows on anybody's own document list.
The older ops "policies" editor (services/ops) is a different thing and is not touched.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class EmpPolicyDoc(Base):
    __tablename__ = "emp_policy_docs"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(60))
    version_no: Mapped[int] = mapped_column(Integer, default=1)            # internal counter, bumps on new version
    version_label: Mapped[str] = mapped_column(String(40), default="v1")   # what HR calls it ("v2", "2026-27")
    effective_date: Mapped[date | None] = mapped_column(Date)
    body: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))   # employee_documents row (PDF)
    file_name: Mapped[str | None] = mapped_column(String(255))
    ack_required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="published", index=True)   # published | archived
    history: Mapped[list | None] = mapped_column(JSONB, default=list)     # earlier versions (no body text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    published_by: Mapped[str | None] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(String(200))
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EmpPolicyAck(Base):
    __tablename__ = "emp_policy_acks"
    __table_args__ = (UniqueConstraint("policy_id", "version_no", "employee_id", name="uq_emp_policy_ack"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(40))
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    user_id: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
