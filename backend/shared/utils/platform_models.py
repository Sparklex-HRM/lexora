"""Lexora HRM — platform tables shared by the notify / approvals / settings helpers.

These tables live on their OWN declarative base (``PlatformBase``), not the
per-service ``Base``: only the notification service (``ntf_*``, ``org_*``) and the
approvals service (``apr_*``) create them at boot, so a service that merely imports
``notify()`` never races to create them.

Every row carries ``org_id``. Ids are stored as 36-char strings (the caller's uuid
text) so a ``ref_id`` from any owning service fits without a cast.

Column types are the generic SQLAlchemy ones (String / JSON / DateTime) so the unit
tests can run the same code against an in-memory SQLite database.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Integer, LargeBinary, String, Text,
                        UniqueConstraint)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from shared.utils.config import settings


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes — treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def new_id() -> str:
    return str(uuid.uuid4())


class PlatformBase(DeclarativeBase):
    pass


def _pk():
    return mapped_column(String(36), primary_key=True, default=new_id)


def _org():
    return mapped_column(String(36), index=True, nullable=False)


# ══════════════════════════ notifications (ntf_*) ══════════════════════════════

class Notification(PlatformBase):
    """One in-app notification for one user."""
    __tablename__ = "ntf_notifications"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(400), default="")
    kind: Mapped[str] = mapped_column(String(40), default="general", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delivery(PlatformBase):
    """The delivery log: one row per recipient per channel.

    status (in_app): delivered | no_login
    status (email):  queued | sending | sent | failed | not_configured | off_by_user | no_address
    ``sent`` is written ONLY after the SMTP server accepted the message.
    """
    __tablename__ = "ntf_deliveries"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    notification_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_email: Mapped[str | None] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="general")
    title: Mapped[str] = mapped_column(String(300))
    link: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str | None] = mapped_column(String(400))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotifyPreference(PlatformBase):
    """Per-user e-mail switch for one kind. No row = e-mail on. In-app is always on."""
    __tablename__ = "ntf_preferences"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "kind", name="uq_ntf_pref"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40))
    email_on: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class NotifyTemplate(PlatformBase):
    """Org override of the e-mail subject / body for one kind."""
    __tablename__ = "ntf_templates"
    __table_args__ = (UniqueConstraint("org_id", "kind", name="uq_ntf_template"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    kind: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Reminder(PlatformBase):
    """A notify() call to fire later (registered through ``notify_later``)."""
    __tablename__ = "ntf_reminders"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    key: Mapped[str | None] = mapped_column(String(200), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|fired|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ══════════════════════════ company settings (org_*) ═══════════════════════════

class OrgSetting(PlatformBase):
    __tablename__ = "org_settings"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_setting"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[dict | list | None] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class OrgSettingAudit(PlatformBase):
    """Append-only trail of every settings change."""
    __tablename__ = "org_settings_audit"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    key: Mapped[str] = mapped_column(String(60), index=True)
    old_value: Mapped[dict | list | None] = mapped_column(JSON)
    new_value: Mapped[dict | list | None] = mapped_column(JSON)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_email: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class OrgLogo(PlatformBase):
    """The company logo, stored as bytes in the database (one per org)."""
    __tablename__ = "org_logos"
    org_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(60))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# ══════════════════════════ approvals registry (apr_*) ═════════════════════════

class ApprovalItem(PlatformBase):
    """One thing waiting for a decision. The OWNING service keeps the real state
    machine; this row is only the inbox entry. Unique on (org, kind, ref_id)."""
    __tablename__ = "apr_items"
    __table_args__ = (UniqueConstraint("org_id", "kind", "ref_id", name="uq_apr_item"),)
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    kind: Mapped[str] = mapped_column(String(40), index=True)
    ref_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[list] = mapped_column(JSON, default=list)
    requester_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    requester_name: Mapped[str | None] = mapped_column(String(200))
    approver_roles: Mapped[list] = mapped_column(JSON, default=list)
    approver_user_ids: Mapped[list] = mapped_column(JSON, default=list)
    approver_manager_of_employee_id: Mapped[str | None] = mapped_column(String(36))
    link: Mapped[str] = mapped_column(String(400), default="")
    callback_path: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    cycle: Mapped[int] = mapped_column(Integer, default=1)      # +1 each time a decided item is re-opened
    source: Mapped[str] = mapped_column(String(12), default="registry")   # registry | sync
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalDecision(PlatformBase):
    """Who decided what, when, with which comment. Append-only — never updated."""
    __tablename__ = "apr_decisions"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    item_id: Mapped[str] = mapped_column(String(36), index=True)
    cycle: Mapped[int] = mapped_column(Integer, default=1)
    decision: Mapped[str] = mapped_column(String(12))
    comment: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[str | None] = mapped_column(String(36))
    actor_name: Mapped[str | None] = mapped_column(String(200))
    on_behalf_of_user_id: Mapped[str | None] = mapped_column(String(36))
    on_behalf_of_name: Mapped[str | None] = mapped_column(String(200))
    via: Mapped[str] = mapped_column(String(12), default="owner")   # inbox | owner | sync
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ApprovalDelegation(PlatformBase):
    """Out-of-office: ``from_user``'s approvals also go to ``to_user`` between two dates."""
    __tablename__ = "apr_delegations"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    from_user_id: Mapped[str] = mapped_column(String(36), index=True)
    to_user_id: Mapped[str] = mapped_column(String(36), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


NOTIFICATION_TABLES = [t.__table__ for t in (Notification, Delivery, NotifyPreference, NotifyTemplate,
                                             Reminder, OrgSetting, OrgSettingAudit, OrgLogo)]
APPROVAL_TABLES = [t.__table__ for t in (ApprovalItem, ApprovalDecision, ApprovalDelegation)]


# ══════════════════════════ one small shared engine ════════════════════════════
# The helpers are imported by every service. They share ONE lazily-built engine
# with a deliberately tiny pool so they cannot saturate the shared Postgres.

_sessionmaker = None
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_size=2,
                                      max_overflow=2, pool_timeout=10, pool_recycle=1800,
                                      pool_pre_ping=True)
    return _engine


def get_sessionmaker():
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


def set_sessionmaker(sm) -> None:
    """Tests point the helpers at an in-memory database."""
    global _sessionmaker
    _sessionmaker = sm


def session():
    return get_sessionmaker()()


async def create_tables(tables, retries: int = 30, delay: float = 2.0) -> None:
    """Create the given tables if missing. Waits for Postgres to come up (the two
    owning services have no compose ``depends_on``), then gives up loudly."""
    import asyncio
    last = None
    for _ in range(max(1, retries)):
        try:
            async with get_engine().begin() as conn:
                await conn.run_sync(lambda c: PlatformBase.metadata.create_all(c, tables=tables))
            return
        except Exception as e:                               # noqa: BLE001
            last = e
            await asyncio.sleep(delay)
    raise RuntimeError(f"Could not create platform tables: {last}")
