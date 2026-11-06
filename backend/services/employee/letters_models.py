"""Letter engine — tables (employee service, row-level org_id)."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class LetterTemplate(Base):
    """One org's template for a letter key. Saving bumps `version`; an issued letter
    keeps the version number and a copy of the body it was made from."""
    __tablename__ = "letter_templates"
    __table_args__ = (UniqueConstraint("org_id", "key", "country", "language", name="ux_letter_templates_key"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(160))
    country: Mapped[str] = mapped_column(String(2), default="")        # "" = any country
    language: Mapped[str] = mapped_column(String(8), default="en")
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)   # still the seeded text
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class LetterTemplateVersion(Base):
    """D1 — every published body of a template, plus at most ONE draft row per template.
    state: draft (unpublished editor text; letters never use it) | published (the body at
    that version number) . Rollback publishes an older row's body as a NEW version."""
    __tablename__ = "letter_template_versions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0)            # 0 for a draft
    state: Mapped[str] = mapped_column(String(10), default="published")  # draft | published
    name: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(String(300))
    saved_by: Mapped[str | None] = mapped_column(String(200))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LetterSettings(Base):
    """Org letterhead + signatory + numbering prefix (one row per org)."""
    __tablename__ = "letter_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    footer_text: Mapped[str | None] = mapped_column(String(300))
    signatory_name: Mapped[str | None] = mapped_column(String(160))
    signatory_designation: Mapped[str | None] = mapped_column(String(160))
    number_prefix: Mapped[str] = mapped_column(String(12), default="LTR")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_mime: Mapped[str | None] = mapped_column(String(40))
    signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    signature_mime: Mapped[str | None] = mapped_column(String(40))
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class LetterCounter(Base):
    """Last letter number used, per org per calendar year."""
    __tablename__ = "letter_counters"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


class IssuedLetter(Base):
    """A generated letter. Once status = issued the PDF bytes, hash, number and the
    values it was made from are never changed — it can only be voided (with a reason)
    or issued again as a NEW row with a NEW number."""
    __tablename__ = "issued_letters"
    __table_args__ = (UniqueConstraint("org_id", "number", name="ux_issued_letters_number"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    # NULL only for a letter issued BEFORE the person is an employee (a candidate's
    # offer letter). It is filled in when that person is activated (relink).
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=True)
    # Who the letter is addressed to: "employee" (default) or "candidate". For a
    # candidate, recipient_ref is the hiring offer id that later re-links the letter.
    recipient_kind: Mapped[str] = mapped_column(String(20), default="employee")
    recipient_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_email: Mapped[str | None] = mapped_column(String(200))
    template_key: Mapped[str] = mapped_column(String(60), index=True)
    template_name: Mapped[str] = mapped_column(String(160))
    template_version: Mapped[int] = mapped_column(Integer, default=1)
    body_snapshot: Mapped[str] = mapped_column(Text)                 # the template text used
    values: Mapped[dict] = mapped_column(JSONB, default=dict)        # the filled values
    tables: Mapped[dict] = mapped_column(JSONB, default=dict)
    number: Mapped[str | None] = mapped_column(String(40))           # NULL while a draft
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)   # draft | issued | void
    letter_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    text_copy: Mapped[str | None] = mapped_column(Text)
    visible_to_employee: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(40))           # screen / service that asked for it
    reissued_from: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    issued_by: Mapped[str | None] = mapped_column(String(200))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(400))
    voided_by: Mapped[str | None] = mapped_column(String(200))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
