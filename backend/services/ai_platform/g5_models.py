"""G5 tables — watchdog run log + findings, copilot audit + pending actions.

On the shared ``PlatformBase`` with generic column types (String / JSON / DateTime)
so the same code runs against SQLite in tests. Every row carries ``org_id``.
The ai_platform service creates these at boot via ``pm.create_tables(G5_TABLES)``.
"""
import sys
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402


def _pk():
    return mapped_column(String(36), primary_key=True, default=new_id)


def _org():
    return mapped_column(String(36), index=True, nullable=False)


class WatchdogRun(PlatformBase):
    """One execution of one watchdog for one org (the run log)."""
    __tablename__ = "ai_wd_runs"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    agent_key: Mapped[str] = mapped_column(String(40), index=True)
    trigger: Mapped[str] = mapped_column(String(12), default="schedule")        # schedule | manual
    status: Mapped[str] = mapped_column(String(12), default="ok", index=True)   # ok | attention | skipped | error
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    new_findings: Mapped[int] = mapped_column(Integer, default=0)
    severity_max: Mapped[str | None] = mapped_column(String(8))                 # info | warn | high
    summary: Mapped[str] = mapped_column(String(400), default="")
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)                # the thresholds used
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(400))


class WatchdogFinding(PlatformBase):
    """One finding with its evidence rows. Read-only data copied at run time."""
    __tablename__ = "ai_wd_findings"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_key: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(8), default="warn", index=True)
    confidence: Mapped[str] = mapped_column(String(8), default="high")          # high | medium | low
    subject_employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    subject_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)                  # [{column: value}]
    fingerprint: Mapped[str] = mapped_column(String(120), index=True)
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class CopilotAudit(PlatformBase):
    """Every copilot exchange: the SHIELDED prompt and reply, the tools called, tokens."""
    __tablename__ = "ai_copilot_audit"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_email: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(12), default="rules")              # live | rules | refused
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_shielded: Mapped[str] = mapped_column(Text, default="")
    reply_shielded: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)                # [{tool, args(shielded), ok}]
    masked_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)   # prompt cache (2026-10-24)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class CopilotPending(PlatformBase):
    """A write the copilot proposed and the person has not yet confirmed."""
    __tablename__ = "ai_copilot_pending"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(40))                             # apply_leave
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)   # pending | done | cancelled | expired | failed
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


G5_TABLES = [t.__table__ for t in (WatchdogRun, WatchdogFinding, CopilotAudit, CopilotPending)]
