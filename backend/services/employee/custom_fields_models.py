"""Custom fields (stream D1) — the per-org field registry.

VALUES are NOT a separate table: each entity table gets ONE additive JSONB column
``custom_data`` ({key: value}), added at boot with ``ALTER TABLE IF EXISTS … ADD COLUMN
IF NOT EXISTS`` (see custom_fields_routers.init_custom_fields). Why a column and not an
EAV values table: one read returns the row with its custom values, the report builder
projects ``custom_data->>'key'`` in the same SQL with no join, exports are a plain
projection, and a definition change never touches stored rows (a removed field's
values stay in the JSON, hidden, until the org deletes them on purpose).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class CustomFieldDef(Base):
    __tablename__ = "cf_definitions"
    __table_args__ = (UniqueConstraint("org_id", "entity", "key", name="ux_cf_definitions_key"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    entity: Mapped[str] = mapped_column(String(20), index=True)          # employee | candidate | asset | expense
    key: Mapped[str] = mapped_column(String(40))                          # snake_case, stable
    label: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(12))                         # text number date select multiselect boolean file lookup
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[list | None] = mapped_column(JSONB)                   # select / multiselect choices; lookup: ["employees"]
    validation: Mapped[dict | None] = mapped_column(JSONB)                # {regex, min, max, help}
    visible_to: Mapped[list | None] = mapped_column(JSONB)                # roles; [] = everyone who can see the record
    editable_by: Mapped[list | None] = mapped_column(JSONB)               # roles; [] = org admin + HR
    section: Mapped[str | None] = mapped_column(String(60))
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    show_in_grid: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
