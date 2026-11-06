"""Auth service — legal entities (group companies under one org), stream D4.

One row per legal entity of a tenant. The DEFAULT entity is created from the org's
own details on first boot (entities_routes.init_entities). Bank account numbers
live encrypted inside `bank_accounts` (shared.crypto_fields tokens); the API only
ever returns the last four digits unless an org admin explicitly reveals them.

Canadian registration numbers: CRA Business Number (9 digits), CRA payroll program
account (BN + RP + 4 digits) and, for Québec, the Revenu Québec identification number.

Column list must stay in step with shared/legal_entities.py (what other services read).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class LegalEntity(Base):
    __tablename__ = "legal_entities"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)             # legal name as registered
    short_code: Mapped[str] = mapped_column(String(12), nullable=False)        # unique per org (upper-case)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    entity_type: Mapped[str | None] = mapped_column(String(30))                # corporation | partnership | …
    business_number: Mapped[str | None] = mapped_column(String(9))             # CRA BN (9 digits)
    payroll_account: Mapped[str | None] = mapped_column(String(15))            # 123456789RP0001
    rq_identification: Mapped[str | None] = mapped_column(String(16))          # Revenu Québec id (RL-1)
    address_line1: Mapped[str | None] = mapped_column(String(200))             # registered office
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    province: Mapped[str | None] = mapped_column(String(2))
    postal_code: Mapped[str | None] = mapped_column(String(7))                 # A1A 1A1
    country: Mapped[str | None] = mapped_column(String(2), default="CA")
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    website: Mapped[str | None] = mapped_column(String(200))
    wcb_accounts: Mapped[dict | None] = mapped_column(JSONB)                   # {province: {board, account_no, rate_group}}
    licences: Mapped[list | None] = mapped_column(JSONB)                       # [{kind, number, province, valid_to}]
    bank_accounts: Mapped[list | None] = mapped_column(JSONB)                  # [{id, label, bank_name, institution, transit, account_enc, account_last4, is_primary}]
    signatory: Mapped[dict | None] = mapped_column(JSONB)                      # {name, designation, email, phone}
    letterhead: Mapped[dict | None] = mapped_column(JSONB)                     # {logo_data_url, header_text, footer_text}
    numbering_prefix: Mapped[str | None] = mapped_column(String(12))           # optional per-entity number prefix
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(String(160))
