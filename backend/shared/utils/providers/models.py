"""Lexora HRM — platform & integrations tables (stream G3).

All tables live on ``PlatformBase`` (``shared/utils/platform_models.py``) with
36-char string ids, so the same models run against Postgres and the SQLite test
database. The auth service creates them at boot (``ensure_tables``); the
notification service calls the same function (idempotent) because its
connectors write ``int_whatsapp_messages`` / read ``int_providers``.

Secrets (client secrets, webhook signing keys, API tokens) are NEVER stored in
clear: ``store.py`` encrypts them with the product's column-encryption helper
(``shared/utils/crypto.py`` — the same AES-256-GCM scheme used for the SIN)
before they reach the ``config`` JSON. Keys and tokens that only need to be
CHECKED (API keys, SCIM bearer tokens, e-sign links, OTPs) are stored as a
SHA-256 hash and shown once.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.utils import platform_models as pm
from shared.utils.platform_models import PlatformBase, _org, _pk, now_utc


class IntegrationProvider(PlatformBase):
    """One row per (org, provider): mode, config (secrets encrypted), status."""
    __tablename__ = "int_providers"
    __table_args__ = (UniqueConstraint("org_id", "provider", name="uq_int_provider"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    provider: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(30), default="")          # provider-specific (e.g. meta | twilio)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)             # secret fields hold enc:v1: tokens
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(String(400))
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SsoConfig(PlatformBase):
    """Per-org single sign-on (OIDC or SAML 2.0)."""
    __tablename__ = "int_sso_configs"
    org_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol: Mapped[str] = mapped_column(String(10), default="oidc")      # oidc | saml
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False)         # password login only for admins (break-glass)
    display_name: Mapped[str] = mapped_column(String(80), default="")      # "Sign in with <display_name>"
    # OIDC
    discovery_url: Mapped[str] = mapped_column(String(400), default="")
    client_id: Mapped[str] = mapped_column(String(300), default="")
    client_secret: Mapped[str | None] = mapped_column(Text)               # enc:v1: token
    # SAML
    idp_entity_id: Mapped[str] = mapped_column(String(400), default="")
    idp_sso_url: Mapped[str] = mapped_column(String(400), default="")
    idp_cert_pem: Mapped[str | None] = mapped_column(Text)                # public certificate, not a secret
    # attribute mapping + JIT
    attr_email: Mapped[str] = mapped_column(String(120), default="email")
    attr_employee_number: Mapped[str] = mapped_column(String(120), default="")
    jit_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    jit_default_role: Mapped[str] = mapped_column(String(40), default="employee")
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScimToken(PlatformBase):
    __tablename__ = "int_scim_tokens"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    label: Mapped[str] = mapped_column(String(80), default="")
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScimLog(PlatformBase):
    """The provisioning log: one row per SCIM request that touched a person."""
    __tablename__ = "int_scim_log"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    user_name: Mapped[str | None] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(30))        # created | updated | deactivated | linked | rejected | error
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    message: Mapped[str | None] = mapped_column(String(400))


class ApiKey(PlatformBase):
    __tablename__ = "int_api_keys"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16), index=True)          # shown on screen; the rest is hashed
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)             # ["employees:read", "leave:write", …]
    role: Mapped[str] = mapped_column(String(40), default="employee")    # stamped X-Role (bounded on creation)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Webhook(PlatformBase):
    __tablename__ = "int_webhooks"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    url: Mapped[str] = mapped_column(String(600))
    description: Mapped[str] = mapped_column(String(200), default="")
    secret: Mapped[str] = mapped_column(Text)                            # enc:v1: token (HMAC key)
    events: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(PlatformBase):
    """status: pending | delivered | failed (will retry) | dead (gave up) | replayed"""
    __tablename__ = "int_webhook_deliveries"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    webhook_id: Mapped[str] = mapped_column(String(36), index=True)
    event: Mapped[str] = mapped_column(String(60), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_excerpt: Mapped[str | None] = mapped_column(String(300))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_of: Mapped[str | None] = mapped_column(String(36))


class EsignEnvelope(PlatformBase):
    """status: draft | sent | signed | declined | cancelled"""
    __tablename__ = "int_esign_envelopes"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="letter")        # letter | onboarding | policy | other
    provider: Mapped[str] = mapped_column(String(30), default="internal")
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    signer_name: Mapped[str] = mapped_column(String(200))
    signer_email: Mapped[str | None] = mapped_column(String(200))
    signer_phone: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    signed_pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    signed_sha256: Mapped[str | None] = mapped_column(String(64))
    link_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_hash: Mapped[str | None] = mapped_column(String(64))
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    otp_channel: Mapped[str | None] = mapped_column(String(20))            # email | whatsapp
    otp_status: Mapped[str | None] = mapped_column(String(20))             # sent | not_configured | failed
    typed_name: Mapped[str | None] = mapped_column(String(200))
    signer_ip: Mapped[str | None] = mapped_column(String(64))
    signer_user_agent: Mapped[str | None] = mapped_column(String(300))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_reason: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    events: Mapped[list] = mapped_column(JSON, default=list)               # [{at, what, ip}] — the audit certificate


class BgvCheck(PlatformBase):
    """status: requested | in_progress | clear | discrepancy | failed | cancelled"""
    __tablename__ = "int_bgv_checks"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    employee_name: Mapped[str] = mapped_column(String(200))
    check_type: Mapped[str] = mapped_column(String(40))      # identity | address | education | employment | criminal | reference
    mode: Mapped[str] = mapped_column(String(10), default="manual")
    vendor: Mapped[str] = mapped_column(String(120), default="")
    reference: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    result_note: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[str | None] = mapped_column(String(36))   # employee_documents row (uploaded report)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WhatsappMessage(PlatformBase):
    """status: not_configured | sent | delivered | read | failed. ``sent`` only after the API accepted it."""
    __tablename__ = "int_whatsapp_messages"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    to_masked: Mapped[str] = mapped_column(String(30))
    template: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40), default="general")
    provider: Mapped[str] = mapped_column(String(20), default="")
    provider_message_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


G3_TABLES = [t.__table__ for t in (IntegrationProvider, SsoConfig, ScimToken, ScimLog, ApiKey, Webhook,
                                   WebhookDelivery, EsignEnvelope, BgvCheck, WhatsappMessage)]


async def ensure_tables(retries: int = 5) -> None:
    """Create the G3 tables if missing (idempotent; safe from several services)."""
    await pm.create_tables(G3_TABLES, retries=retries)
