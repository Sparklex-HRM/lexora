"""Agentic operations add-on — foundation tables (2026-10-25).

The agents that EXECUTE (Payroll Auditor, HR Desk, the lifecycle agents) share one runtime:
work items that chase people, proposals that wait for approval by risk tier, an
append-only hash-chained action log, run records and a value ledger.

On ``PlatformBase`` with generic column types so the unit tests run the same code on
SQLite. Every row carries ``org_id``. Created at boot by ``main.py`` through
``pm.create_tables(AGENTIC_TABLES)`` — additive, nothing existing is altered; the
Alembic revision ``0015_agentic`` re-runs that boot DDL and installs the append-only
trigger on ``ai_agent_action_log`` (``APPEND_ONLY_DDL``).

Rules the tables encode
* a proposal is never executed without an approval event by a server-authenticated
  approver (``approver_user_id`` comes from the gateway identity headers, never a body);
* tier 2 needs two DIFFERENT approvers; an untrusted input in the run raises the tier;
* in shadow mode a proposal is stored as ``shadow`` and nothing runs;
* the action log is INSERT-only (trigger in Postgres; ``agentic_log`` never updates) and is
  kept at least one year — no purge job may touch it (access-log retention / payroll record law).
"""
import sys
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402

AGENTS = ("payroll_auditor",            # Payroll Auditor (Canada, restored 2026-10-26): agents/payroll_auditor
          "hr_desk",
          # employee-lifecycle agents (2026-10-26): agents/onboarding · agents/exit · agents/attendance
          "onboarding", "exit", "attendance")
TASK_STATUSES = ("open", "waiting", "done", "escalated", "cancelled")
PROPOSAL_STATUSES = ("proposed", "approved", "second_approval", "executed", "denied", "edited", "expired", "shadow", "failed")
RUN_MODES = ("shadow", "live")


def _pk():
    return mapped_column(String(36), primary_key=True, default=new_id)


def _org():
    return mapped_column(String(36), index=True, nullable=False)


class AgentTask(PlatformBase):
    """A work item an agent opened for a PERSON to act on (missing punch, bank detail, approval…).
    The agent chases the owner on a cadence and escalates after N chases."""
    __tablename__ = "ai_agent_tasks"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    agent: Mapped[str] = mapped_column(String(32), index=True)                   # hr_desk | onboarding | exit | attendance
    kind: Mapped[str] = mapped_column(String(60), index=True)                    # missing_punch | bank_missing | filing_due …
    employee_id: Mapped[str | None] = mapped_column(String(36), index=True)      # subject refs
    run_ref: Mapped[str | None] = mapped_column(String(36))                      # payroll run id the task belongs to
    filing_id: Mapped[str | None] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)                     # plain-words detail {what, why, how_to_fix, link}
    owner_user_id: Mapped[str | None] = mapped_column(String(36), index=True)    # who must act (login)
    owner_employee_id: Mapped[str | None] = mapped_column(String(36))            # when the owner has no login yet
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    chase_count: Mapped[int] = mapped_column(Integer, default=0)
    last_chased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_chase_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    escalation_to: Mapped[dict | None] = mapped_column(JSON)                     # {roles: [...], user_ids: [...]}
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_run: Mapped[str | None] = mapped_column(String(36), index=True)   # ai_agent_runs.id
    dedupe_key: Mapped[str | None] = mapped_column(String(160), index=True)      # same open item is not opened twice
    result: Mapped[dict | None] = mapped_column(JSON)
    completed_by: Mapped[str | None] = mapped_column(String(36))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AgentProposal(PlatformBase):
    """An action an agent wants to take. Persisted BEFORE anything happens; executed only after the
    approval events the tier demands. ``tool_args`` is PII-tokenised (shield) — the executor
    restores values from the sealed copy, never from the card."""
    __tablename__ = "ai_agent_proposals"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    agent: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    tool_version: Mapped[str] = mapped_column(String(20), default="1")
    tool_args: Mapped[dict] = mapped_column(JSON, default=dict)                  # tokenised (what the approver sees)
    tool_args_sealed: Mapped[str | None] = mapped_column(Text)                   # encrypted raw args for the executor
    plain_action: Mapped[str] = mapped_column(String(600))                       # "Hold Asha Rao's bank change until a cancelled cheque is attached"
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    aggregate_impact: Mapped[dict] = mapped_column(JSON, default=dict)           # {headcount, dollars}
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)              # [{label, link, source, hash}]
    confidence: Mapped[str] = mapped_column(String(12), default="unknown")       # high | medium | low | unknown
    base_tier: Mapped[int] = mapped_column(Integer, default=1)
    risk_tier: Mapped[int] = mapped_column(Integer, default=1, index=True)       # 0 auto-logged · 1 one approver · 2 two approvers
    policy_id: Mapped[str] = mapped_column(String(80), default="")
    policy_decision: Mapped[str] = mapped_column(String(12), default="allow")    # allow | deny | shadow
    untrusted_input: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(8), default="live")                 # shadow | live (at proposal time)
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    requested_by_user_id: Mapped[str | None] = mapped_column(String(36))         # the person whose session the run acts for (None = schedule)
    approver_user_id: Mapped[str | None] = mapped_column(String(36))             # server-authenticated (gateway headers)
    approver_email: Mapped[str | None] = mapped_column(String(200))
    approver_role: Mapped[str | None] = mapped_column(String(40))
    second_approver_user_id: Mapped[str | None] = mapped_column(String(36))
    second_approver_email: Mapped[str | None] = mapped_column(String(200))
    approved_for_session: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_input_diff: Mapped[dict | None] = mapped_column(JSON)                 # {field: {from, to}} when edit-and-approve
    deny_message: Mapped[str | None] = mapped_column(String(600))
    reversal_plan: Mapped[dict | None] = mapped_column(JSON)                     # pre-commit staging: how to undo
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSessionGrant(PlatformBase):
    """"Approve for session": the approver lets the same tool from the same agent run without a card
    for a few hours (tier ≤ 1 only). Revocable; expires on its own."""
    __tablename__ = "ai_agent_session_grants"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    agent: Mapped[str] = mapped_column(String(32), index=True)
    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36))
    approver_user_id: Mapped[str] = mapped_column(String(36))
    approver_email: Mapped[str | None] = mapped_column(String(200))
    approver_role: Mapped[str | None] = mapped_column(String(40))
    max_tier: Mapped[int] = mapped_column(Integer, default=1)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(Integer, default=0)


class AgentActionLog(PlatformBase):
    """APPEND-ONLY, hash-chained: one row per tool call and per approval decision (report §Trust).
    ``event_hash = sha256(prev_event_hash + canonical(fields))``; per org, ordered by ``seq``.
    Never UPDATE or DELETE a row — Postgres refuses through the trigger installed by
    ``APPEND_ONLY_DDL``; ``agentic_log.verify_chain`` recomputes the chain."""
    __tablename__ = "ai_agent_action_log"
    event_id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()                                                 # tenant
    seq: Mapped[int] = mapped_column(Integer, index=True)
    prev_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)              # proposal.created | approval.* | execution.* | task.* | run.* | settings.*
    session_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    step_no: Mapped[int | None] = mapped_column(Integer)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    # agent
    agent_name: Mapped[str | None] = mapped_column(String(32), index=True)
    agent_version: Mapped[str | None] = mapped_column(String(20))
    prompt_template_id: Mapped[str | None] = mapped_column(String(80))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(60))
    model_params: Mapped[dict | None] = mapped_column(JSON)
    provider_request_id: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_cad: Mapped[float | None] = mapped_column(Float)
    # principal
    requested_by_user_id: Mapped[str | None] = mapped_column(String(36))
    run_as_identity: Mapped[str | None] = mapped_column(String(80))              # "agent:exit" or the user id it runs as
    roles_at_time_of_action: Mapped[list | None] = mapped_column(JSON)
    credential_id: Mapped[str | None] = mapped_column(String(64))
    # action
    tool_name: Mapped[str | None] = mapped_column(String(80), index=True)
    tool_version: Mapped[str | None] = mapped_column(String(20))
    tool_input: Mapped[dict | None] = mapped_column(JSON)                        # PII tokenised
    tool_output_digest: Mapped[str | None] = mapped_column(String(64))
    data_sources_read: Mapped[list | None] = mapped_column(JSON)
    untrusted_inputs: Mapped[list | None] = mapped_column(JSON)                  # [{kind, hash}]
    risk_tier: Mapped[int | None] = mapped_column(Integer)
    policy_id: Mapped[str | None] = mapped_column(String(80))
    policy_decision: Mapped[str | None] = mapped_column(String(12))
    # oversight
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True)      # ai_agent_proposals.id
    approver_user_id: Mapped[str | None] = mapped_column(String(36))
    second_approver_user_id: Mapped[str | None] = mapped_column(String(36))
    decision: Mapped[str | None] = mapped_column(String(24))
    edited_input_diff: Mapped[dict | None] = mapped_column(JSON)
    deny_message: Mapped[str | None] = mapped_column(String(600))
    decision_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_latency_ms: Mapped[int | None] = mapped_column(Integer)
    # effect
    records_before_digest: Mapped[str | None] = mapped_column(String(64))
    records_after_digest: Mapped[str | None] = mapped_column(String(64))
    aggregate_impact: Mapped[dict | None] = mapped_column(JSON)
    reversal_plan_id: Mapped[str | None] = mapped_column(String(64))
    reversed_by_event_id: Mapped[str | None] = mapped_column(String(36))
    # explanation
    agent_reason_text: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list | None] = mapped_column(JSON)
    confidence_or_unknown_flag: Mapped[str | None] = mapped_column(String(12))


class AgentRun(PlatformBase):
    """One run of one agent for one org (scheduled, event-triggered or manual)."""
    __tablename__ = "ai_agent_runs"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    agent: Mapped[str] = mapped_column(String(32), index=True)
    agent_version: Mapped[str] = mapped_column(String(20), default="1")
    trigger: Mapped[str] = mapped_column(String(24), default="schedule")         # schedule | manual | attendance_lock | pay_date_minus_n | filing_due | ticket
    mode: Mapped[str] = mapped_column(String(8), default="shadow", index=True)   # shadow | live
    status: Mapped[str] = mapped_column(String(12), default="running", index=True)  # running | done | error
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)                    # {headline, ready, need_a_look, waiting_on: [...], would_have_done: [...]}
    checks_run: Mapped[int] = mapped_column(Integer, default=0)
    findings: Mapped[int] = mapped_column(Integer, default=0)
    proposals: Mapped[int] = mapped_column(Integer, default=0)
    tasks_opened: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cad: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(400))
    requested_by_user_id: Mapped[str | None] = mapped_column(String(36))


class AgentValueEvent(PlatformBase):
    """Value ledger row. ``kind`` is a minutes-table key for the PROXY layer (resolved query, exception
    confirmed, filing prepared…); ``dollars_avoided`` counts in the AVOIDED layer ONLY when a human
    confirmed it (``confirmed_by_user_id`` set) — an alert is an attention signal, not a finding."""
    __tablename__ = "ai_agent_value_events"
    id: Mapped[str] = _pk()
    org_id: Mapped[str] = _org()
    agent: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    proposal_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(60), index=True)
    units: Mapped[float] = mapped_column(Float, default=1.0)
    dollars_avoided: Mapped[float | None] = mapped_column(Float)                  # the org's OWN amounts (interest / fees / wrong pay caught)
    basis: Mapped[str | None] = mapped_column(String(300))                       # "late-remittance penalty 10% on $18,000"
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(400))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


AGENTIC_TABLES = [t.__table__ for t in (AgentTask, AgentProposal, AgentSessionGrant, AgentActionLog, AgentRun, AgentValueEvent)]

# Postgres: the action log is INSERT-only. Owners cannot be REVOKEd from their own table, so a trigger
# is the control (and it holds for every DB role). Idempotent; run at boot and by Alembic 0015_agentic.
APPEND_ONLY_DDL = [
    """CREATE OR REPLACE FUNCTION ai_agent_action_log_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'ai_agent_action_log is append-only (agentic action log): % refused', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS ai_agent_action_log_no_update ON ai_agent_action_log",
    """CREATE TRIGGER ai_agent_action_log_no_update BEFORE UPDATE OR DELETE ON ai_agent_action_log
FOR EACH ROW EXECUTE FUNCTION ai_agent_action_log_append_only()""",
    "DROP TRIGGER IF EXISTS ai_agent_action_log_no_truncate ON ai_agent_action_log",
    """CREATE TRIGGER ai_agent_action_log_no_truncate BEFORE TRUNCATE ON ai_agent_action_log
FOR EACH STATEMENT EXECUTE FUNCTION ai_agent_action_log_append_only()""",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_agent_action_log_org_seq ON ai_agent_action_log (org_id, seq)",
]


async def ensure_append_only(engine=None) -> bool:
    """Install the append-only trigger (Postgres only; SQLite tests rely on the code rule). Never raises."""
    from sqlalchemy import text
    from shared.utils import platform_models as pm
    eng = engine or pm.get_engine()
    if eng is None or eng.dialect.name != "postgresql":
        return False
    try:
        async with eng.begin() as conn:
            for stmt in APPEND_ONLY_DDL:
                await conn.execute(text(stmt))
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] append-only trigger not installed: {e}")
        return False
