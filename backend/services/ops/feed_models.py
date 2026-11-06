"""Company feed tables (ops service, shared Postgres, row-level org_id) — portal batch 3, item 12.

eng_feed_posts      posts, polls and auto posts (birthday / anniversary / new_joiner). Auto posts are
                    idempotent through UNIQUE (org_id, auto_key). Birthday rows keep NO date of birth —
                    only the celebration day + "16 October" label.
eng_feed_comments   comments on a post or on an approved kudos (item_id = post uuid | "kudos:<uuid>")
eng_feed_reactions  one row per (item, person, reaction)  UNIQUE (org_id, item_id, actor_key, emoji)
eng_feed_votes      one vote per person per poll           UNIQUE (org_id, post_id, voter_key)
eng_feed_prefs      per-employee opt-outs of the auto kinds UNIQUE (org_id, employee_id)

All NEW tables (created by create_all at boot) — nothing existing is altered. Kudos are read from
eng_kudos (never copied).
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, String, Text, UniqueConstraint
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


class FeedPost(Base):
    __tablename__ = "eng_feed_posts"
    __table_args__ = (UniqueConstraint("org_id", "auto_key", name="uq_eng_feed_posts_auto_key"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    kind: Mapped[str] = mapped_column(String(20), index=True)          # post | poll | birthday | anniversary | new_joiner
    author_employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    author_user_id: Mapped[str | None] = mapped_column(String(64))
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_role: Mapped[str | None] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text, default="")
    department: Mapped[str | None] = mapped_column(String(120), index=True)   # author's (post) / subject's (auto)
    location: Mapped[str | None] = mapped_column(String(120), index=True)
    audience_department: Mapped[str | None] = mapped_column(String(120))
    audience_location: Mapped[str | None] = mapped_column(String(120))
    poll_options: Mapped[list | None] = mapped_column(JSONB)                 # [{id, label}]
    poll_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subject_employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    subject_name: Mapped[str | None] = mapped_column(String(200))
    event_date: Mapped[date | None] = mapped_column(Date)                    # the day celebrated (never a DOB)
    event_label: Mapped[str | None] = mapped_column(String(40))              # "16 October" / "3 years"
    auto_key: Mapped[str | None] = mapped_column(String(120))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pinned_by: Mapped[str | None] = mapped_column(String(200))
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_reason: Mapped[str | None] = mapped_column(String(300))
    hidden_by: Mapped[str | None] = mapped_column(String(200))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class FeedComment(Base):
    __tablename__ = "eng_feed_comments"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    item_id: Mapped[str] = mapped_column(String(80), index=True)
    author_employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    author_user_id: Mapped[str | None] = mapped_column(String(64))
    author_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_reason: Mapped[str | None] = mapped_column(String(300))
    hidden_by: Mapped[str | None] = mapped_column(String(200))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class FeedReaction(Base):
    __tablename__ = "eng_feed_reactions"
    __table_args__ = (UniqueConstraint("org_id", "item_id", "actor_key", "emoji", name="uq_eng_feed_reactions"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    item_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_key: Mapped[str] = mapped_column(String(80))
    emoji: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FeedVote(Base):
    __tablename__ = "eng_feed_votes"
    __table_args__ = (UniqueConstraint("org_id", "post_id", "voter_key", name="uq_eng_feed_votes"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    post_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    voter_key: Mapped[str] = mapped_column(String(80))
    option_id: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FeedPrefs(Base):
    __tablename__ = "eng_feed_prefs"
    __table_args__ = (UniqueConstraint("org_id", "employee_id", name="uq_eng_feed_prefs"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org()
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    opt_out_birthday: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_anniversary: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_new_joiner: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
