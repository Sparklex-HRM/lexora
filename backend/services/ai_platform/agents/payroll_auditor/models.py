"""Payroll Auditor — the one table the agent owns: the findings of a run, with the checker's decisions.

``ai_payroll_findings`` is the exception grid of the review pack (one row per finding of one run). Everything
else the agent produces lives in the foundation tables (runs, proposals, tasks, action log, value events).
On ``PlatformBase`` with generic column types so the tests run on SQLite; created at boot by ``main.py``
through ``pm.create_tables(PAYROLL_AUDITOR_TABLES)`` (additive).

Status of a finding: ``unexplained`` (needs a reason / a fix) · ``explained`` (deterministically linked to an
event, or model-rephrased from that link) · ``confirmed`` (a person agreed it is a real exception — the dollars
at risk then count in the value ledger) · ``dismissed`` (a person said it is fine, with a reason) · ``info``.
"""
from __future__ import annotations

import sys
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402

FINDING_STATUSES = ("unexplained", "explained", "confirmed", "dismissed", "info")
SEVERITIES = ("critical", "warning", "info")


class PayrollFinding(PlatformBase):
    __tablename__ = "ai_payroll_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), index=True)                 # ai_agent_runs.id
    pay_period: Mapped[str] = mapped_column(String(48), index=True)             # "biweekly:2026-10-04..2026-10-17"
    code: Mapped[str] = mapped_column(String(8), index=True)                    # PA-01 … PA-17
    check_name: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(8), index=True)                # critical | warning | info
    rank: Mapped[int] = mapped_column(Integer, default=99)
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)
    employee_ref: Mapped[str | None] = mapped_column(String(160))               # "E0017 · A. Tremblay"
    department: Mapped[str | None] = mapped_column(String(120))
    cost_centre: Mapped[str | None] = mapped_column(String(60))
    component: Mapped[str | None] = mapped_column(String(40))
    prev: Mapped[float | None] = mapped_column(Float)
    curr: Mapped[float | None] = mapped_column(Float)
    delta_cad: Mapped[float | None] = mapped_column(Float)
    delta_pct: Mapped[float | None] = mapped_column(Float)
    rule: Mapped[str] = mapped_column(Text, default="")                         # the rule in plain words
    threshold: Mapped[dict | None] = mapped_column(JSON)
    linked_event: Mapped[dict | None] = mapped_column(JSON)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)             # [{label, link}]
    fix_hint: Mapped[str | None] = mapped_column(Text)                          # how to fix, plain words
    owner_role: Mapped[str | None] = mapped_column(String(24))                  # employee | manager | hr | payroll
    explanation: Mapped[str | None] = mapped_column(Text)
    explained_by: Mapped[str | None] = mapped_column(String(12))                # template | model | person | None
    verified: Mapped[bool | None] = mapped_column(Boolean)                      # statutory figure verified (None = n/a)
    dollars_at_risk: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="unexplained", index=True)
    checker_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(36))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value_event_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


PAYROLL_AUDITOR_TABLES = [PayrollFinding.__table__]
