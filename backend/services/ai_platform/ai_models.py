"""D3 tables — AI settings per org, AI access tokens (MCP), proposed actions,
usage events and the tool-call audit.

On ``PlatformBase`` with generic column types so the unit tests run the same code
on SQLite. Every row carries ``org_id``. Created at boot by ``main.py`` through
``pm.create_tables(D3_TABLES)`` — additive, nothing existing is altered.
"""
import sys
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402


def _pk():
    return mapped_column(String(36), primary_key=True, default=new_id)


def _org():
    return mapped_column(String(36), index=True, nullable=False)


class AiOrgSettings(PlatformBase):
    """One row per org: bring-your-own key (encrypted), model, caps, feature switches,
    price overrides. The raw key is never stored or returned — only its last 4 chars."""
    __tablename__ = "ai_org_settings"
    org_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text)              # shared.crypto_fields.encrypt_field
    key_last4: Mapped[str | None] = mapped_column(String(8))
    model: Mapped[str] = mapped_column(String(60), default="claude-opus-5-5")
    effort: Mapped[str] = mapped_column(String(8), default="medium")   # low | medium | high
    features: Mapped[dict] = mapped_column(JSON, default=dict)         # {qa, actions, mcp}
    monthly_cap_tokens: Mapped[int] = mapped_column(Integer, default=0)       # 0 = no cap
    monthly_cap_cad: Mapped[float] = mapped_column(Float, default=0.0)        # 0 = no cap
    cap_mode: Mapped[str] = mapped_column(String(8), default="soft")   # soft (warn) | hard (refuse)
    usd_cad_rate: Mapped[float | None] = mapped_column(Float)          # set by the org; never invented
    prices: Mapped[dict] = mapped_column(JSON, default=dict)           # {model: {input_usd_per_mtok, output_usd_per_mtok}}
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AiAccessToken(PlatformBase):
    """A token an external AI client (Claude Desktop, ChatGPT, Copilot …) presents to the
    MCP server. Scoped to ONE user of the org; carries scopes read / act / admin;
    revocable; every call is rate limited and logged in ``ai_tool_calls``."""
    __tablename__ = "ai_access_tokens"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    name: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_email: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="employee")
    employee_id: Mapped[str | None] = mapped_column(String(36))
    scopes: Mapped[list] = mapped_column(JSON, default=list)           # ["read"], ["read","act"], ["read","act","admin"]
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    rate_per_min: Mapped[int] = mapped_column(Integer, default=60)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AiAction(PlatformBase):
    """An action the model PROPOSED. Nothing is written until the person confirms —
    once for a normal action, twice for a destructive / finance one."""
    __tablename__ = "ai_actions"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_email: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(8), default="chat")    # chat | mcp
    token_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    card: Mapped[dict] = mapped_column(JSON, default=dict)             # {what, for_whom, effect, risk, requires_confirmations}
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | done | cancelled | expired | failed
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiUsageEvent(PlatformBase):
    """One row per model call (chat turn, MCP-side model use, key test)."""
    __tablename__ = "ai_usage_events"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_email: Mapped[str | None] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(8), default="chat")
    feature: Mapped[str] = mapped_column(String(40), default="assistant_chat")
    model: Mapped[str | None] = mapped_column(String(60))
    key_source: Mapped[str | None] = mapped_column(String(12))         # org | platform
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)   # prompt-cache reads (0.1x input price)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)  # prompt-cache writes (1.25x / 2x)
    cost_usd: Mapped[float | None] = mapped_column(Float)              # None when no price is known
    cost_cad: Mapped[float | None] = mapped_column(Float)              # None until the org sets a USD→CAD rate
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class AiToolCall(PlatformBase):
    """Every tool invocation — chat or MCP — with SHIELDED arguments."""
    __tablename__ = "ai_tool_calls"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    token_id: Mapped[str | None] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(8), default="chat")
    tool: Mapped[str] = mapped_column(String(60), index=True)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(String(300))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


D3_TABLES = [t.__table__ for t in (AiOrgSettings, AiAccessToken, AiAction, AiUsageEvent, AiToolCall)]
