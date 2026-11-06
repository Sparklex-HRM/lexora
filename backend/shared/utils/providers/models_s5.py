"""Lexora HRM — S5 integration tables (live provider adapters).

On ``PlatformBase`` like the G3 tables (36-char string ids, generic column types,
so the unit tests run them on SQLite). The integrations service creates them at
boot (``ensure_tables``); the notification service calls it too because the
WhatsApp mirror reads ``int_whatsapp_optins``. Additive only.

PII rule for every table here: store the RESULT of a check and a MASKED id —
never a full SIN / account number, never a document.
"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.utils import platform_models as pm
from shared.utils.platform_models import PlatformBase, _org, _pk, now_utc


class ProviderCall(PlatformBase):
    """Every outbound provider request: path, status, attempts, masked bodies. No headers, no keys."""
    __tablename__ = "int_provider_calls"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = mapped_column(String(36), index=True, default="")
    provider: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(60))
    method: Mapped[str] = mapped_column(String(8))
    host: Mapped[str] = mapped_column(String(120), default="")
    path: Mapped[str] = mapped_column(String(300))
    http_status: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str] = mapped_column(String(20), default="")      # ok | http_error | timeout | unknown | connect_error
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    request_masked: Mapped[dict | None] = mapped_column(JSON)
    response_masked: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class InboundWebhook(PlatformBase):
    """Provider → Lexora HRM webhooks: one row per provider event id (duplicates are ignored)."""
    __tablename__ = "int_inbound_webhooks"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_int_inbound_event"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = mapped_column(String(36), index=True, default="")
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(120))
    event: Mapped[str] = mapped_column(String(60), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)       # signature checked / status re-pulled
    outcome: Mapped[str] = mapped_column(String(40), default="")
    payload_masked: Mapped[dict | None] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class WhatsappOptin(PlatformBase):
    """WhatsApp consent per employee (Meta Business policy: opt-in before business-initiated messages)."""
    __tablename__ = "int_whatsapp_optins"
    __table_args__ = (UniqueConstraint("org_id", "employee_id", name="uq_int_wa_optin"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    phone_masked: Mapped[str] = mapped_column(String(30), default="")
    phone_hash: Mapped[str] = mapped_column(String(64), default="")        # sha256 of the normalised number at opt-in
    status: Mapped[str] = mapped_column(String(12), default="opted_in")    # opted_in | opted_out
    source: Mapped[str] = mapped_column(String(20), default="self")        # self | hr_recorded
    consent_text: Mapped[str] = mapped_column(Text, default="")
    events: Mapped[list] = mapped_column(JSON, default=list)               # message types the person agreed to ([] = all)
    recorded_by: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


S5_TABLES = [t.__table__ for t in (ProviderCall, InboundWebhook, WhatsappOptin)]


async def ensure_tables(retries: int = 5) -> None:
    await pm.create_tables(S5_TABLES, retries=retries)
