"""AI platform — token-usage ledger (the basis for token economics)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now():
    return datetime.now(timezone.utc)


class AiUsage(Base):
    """One row per AI interaction (Lexi chat, agent run). Powers /ai/usage."""
    __tablename__ = "ai_usage"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(40), default="lexi_chat")   # lexi_chat | agent_run
    model: Mapped[str] = mapped_column(String(60), default="lexi-local")
    mode: Mapped[str] = mapped_column(String(20), default="estimated")      # live | estimated
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)   # prompt cache (2026-10-24)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
