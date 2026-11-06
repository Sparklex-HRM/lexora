"""Serialise appends to a per-org hash chain.

The agent action log (``ai_agent_action_log``) and the audit trail (``audit_events``) are hash-chained: each row takes
``seq = last.seq + 1`` and ``prev_hash = last.hash``. Two writers for the same org at the same moment (a manual agent
run + the scheduler tick, two requests) read the same ``last`` and the second INSERT hits the unique (org_id, seq)
constraint — the agent run answered 500 and an audit row would be dropped. A transaction-scoped Postgres advisory lock
per (chain, org) makes the read-then-insert atomic across processes and replicas; it is released at COMMIT / ROLLBACK.
Other databases (SQLite in the unit tests) have one writer at a time anyway: no-op.
"""
from __future__ import annotations

from sqlalchemy import text


async def lock_chain(session, chain: str, org) -> bool:
    """Take the (chain, org) append lock inside ``session``'s current transaction. True when a lock was taken."""
    conn = await session.connection()
    if conn.dialect.name != "postgresql":
        return False
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"{chain}:{org}"})
    return True
