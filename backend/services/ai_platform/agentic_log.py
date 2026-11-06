"""Append-only, hash-chained agent action log (report §Trust).

    from agentic_log import append_event, verify_chain
    await append_event(org, event_type="proposal.created", agent_name="exit", tool_name=..., ...)
    await verify_chain(org)   # {"ok": True, "checked": 412, "first_bad_seq": None}

* one row per tool call and per approval decision; every field from the report's schema is a column
  (``agentic_models.AgentActionLog``);
* ``event_hash = sha256(prev_event_hash + canonical(signed fields))`` — altering, re-ordering or
  removing any row breaks every later hash; ``verify_chain`` finds the first bad row;
* ``tool_input`` is PII-tokenised through the shield before it is hashed or stored; ``tool_output``
  is stored only as a digest;
* nothing here ever UPDATEs or DELETEs (Postgres also refuses: trigger in ``agentic_models``);
* retention ≥ 1 year: no purge job touches this table (access-log retention, payroll record law).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime

from sqlalchemy import desc, func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from agentic_models import AgentActionLog  # noqa: E402

GENESIS = "0" * 64
SIGNED_FIELDS = (
    "org_id", "seq", "event_type", "session_id", "run_id", "step_no", "ts_utc",
    "agent_name", "agent_version", "prompt_template_id", "prompt_hash", "model_id", "model_params", "provider_request_id",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "cost_cad",
    "requested_by_user_id", "run_as_identity", "roles_at_time_of_action", "credential_id",
    "tool_name", "tool_version", "tool_input", "tool_output_digest", "data_sources_read", "untrusted_inputs",
    "risk_tier", "policy_id", "policy_decision",
    "approval_id", "approver_user_id", "second_approver_user_id", "decision", "edited_input_diff", "deny_message",
    "decision_ts", "decision_latency_ms",
    "records_before_digest", "records_after_digest", "aggregate_impact", "reversal_plan_id", "reversed_by_event_id",
    "agent_reason_text", "evidence_refs", "confidence_or_unknown_flag",
)
_COLUMNS = {c.name for c in AgentActionLog.__table__.columns}


def _jsonable(v):
    if isinstance(v, datetime):
        return pm.aware(v).isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def canonical(fields: dict) -> str:
    """Order-independent, whitespace-free JSON of the signed fields only."""
    body = {k: _jsonable(fields.get(k)) for k in SIGNED_FIELDS if fields.get(k) is not None}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, fields: dict) -> str:
    return hashlib.sha256(f"{prev_hash}{canonical(fields)}".encode()).hexdigest()


def digest(obj) -> str | None:
    """sha256 of any JSON-able value (used for tool outputs and before/after records)."""
    if obj is None:
        return None
    return hashlib.sha256(json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def tokenise(obj):
    """PII tokens instead of values (shield): SIN / bank / amounts ≥ 1,000 / e-mails / phones / postal codes."""
    if obj is None:
        return None
    try:
        from shield import Shield
        return Shield().protect_value(obj)
    except Exception:                                        # noqa: BLE001
        return {"_tokenised": "unavailable"}


def row_fields(row: AgentActionLog) -> dict:
    return {k: getattr(row, k) for k in SIGNED_FIELDS}


async def append_event(org: str, *, event_type: str, **fields) -> dict:
    """Append one event; returns the stored row as a dict. ``tool_input`` is tokenised here."""
    if "tool_input" in fields and fields["tool_input"] is not None:
        fields["tool_input"] = tokenise(fields["tool_input"])
    if "tool_output" in fields:
        fields["tool_output_digest"] = digest(fields.pop("tool_output"))
    for k in ("records_before", "records_after"):
        if k in fields:
            fields[f"{k}_digest"] = digest(fields.pop(k))
    fields = {k: v for k, v in fields.items() if k in _COLUMNS}
    fields.setdefault("ts_utc", pm.now_utc())
    for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        fields[k] = int(fields.get(k) or 0)                  # column defaults apply only at INSERT — hash the stored value
    async with pm.session() as s:
        # one appender per org at a time (two concurrent writers both took last.seq + 1 → unique violation → 500)
        from shared.utils.chain_lock import lock_chain
        await lock_chain(s, "ai_agent_action_log", org)
        last = (await s.execute(select(AgentActionLog).where(AgentActionLog.org_id == str(org))
                                .order_by(desc(AgentActionLog.seq)).limit(1))).scalar_one_or_none()
        seq = (int(last.seq) + 1) if last else 1
        prev = last.event_hash if last else GENESIS
        row = AgentActionLog(org_id=str(org), seq=seq, prev_event_hash=prev, event_type=event_type, **fields)
        row.event_hash = compute_hash(prev, row_fields(row))
        s.add(row)
        await s.commit()
        return event_dict(row)


def event_dict(r: AgentActionLog) -> dict:
    d = {k: _jsonable(getattr(r, k)) for k in SIGNED_FIELDS}
    d.update({"event_id": r.event_id, "prev_event_hash": r.prev_event_hash, "event_hash": r.event_hash})
    return d


async def verify_chain(org: str, limit: int | None = None) -> dict:
    """Recompute every hash in order. ``first_bad_seq`` names the first row whose stored hash, previous
    hash or sequence does not match — tampering, a deleted row or a re-ordered one."""
    async with pm.session() as s:
        q = select(AgentActionLog).where(AgentActionLog.org_id == str(org)).order_by(AgentActionLog.seq)
        if limit:
            q = q.limit(limit)
        rows = (await s.execute(q)).scalars().all()
    prev, expect_seq, bad, why = GENESIS, 1, None, None
    for r in rows:
        if int(r.seq) != expect_seq:
            bad, why = int(r.seq), f"sequence gap: expected {expect_seq}, found {r.seq} (a row was removed or re-ordered)"
            break
        if r.prev_event_hash != prev:
            bad, why = int(r.seq), "previous-hash link broken"
            break
        if compute_hash(prev, row_fields(r)) != r.event_hash:
            bad, why = int(r.seq), "stored hash does not match the row's fields (row altered)"
            break
        prev, expect_seq = r.event_hash, expect_seq + 1
    return {"ok": bad is None, "checked": len(rows), "first_bad_seq": bad, "reason": why,
            "head_hash": rows[-1].event_hash if rows else GENESIS, "as_of": pm.iso(pm.now_utc())}


async def recent(org: str, *, limit: int = 200, run_id: str | None = None, event_type: str | None = None,
                 approval_id: str | None = None) -> list[dict]:
    q = select(AgentActionLog).where(AgentActionLog.org_id == str(org))
    if run_id:
        q = q.where(AgentActionLog.run_id == run_id)
    if event_type:
        q = q.where(AgentActionLog.event_type.like(f"{event_type}%"))
    if approval_id:
        q = q.where(AgentActionLog.approval_id == approval_id)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(desc(AgentActionLog.seq)).limit(min(int(limit or 200), 2000)))).scalars().all()
    return [event_dict(r) for r in rows]


async def cost_between(org: str, frm: datetime, to: datetime) -> dict:
    """Agent cost from the log's token fields (the ONLY cost source for the value dashboard)."""
    async with pm.session() as s:
        i, o, cr, cw, usd, cad, n, n_cad = (await s.execute(
            select(func.coalesce(func.sum(AgentActionLog.input_tokens), 0), func.coalesce(func.sum(AgentActionLog.output_tokens), 0),
                   func.coalesce(func.sum(AgentActionLog.cache_read_tokens), 0), func.coalesce(func.sum(AgentActionLog.cache_write_tokens), 0),
                   func.sum(AgentActionLog.cost_usd), func.sum(AgentActionLog.cost_cad), func.count(AgentActionLog.event_id),
                   func.count(AgentActionLog.cost_cad))
            .where(AgentActionLog.org_id == str(org), AgentActionLog.ts_utc >= frm, AgentActionLog.ts_utc < to))).one()
    return {"input_tokens": int(i or 0), "output_tokens": int(o or 0), "cache_read_tokens": int(cr or 0), "cache_write_tokens": int(cw or 0),
            "cost_usd": round(float(usd), 6) if usd is not None else 0.0,
            "cost_cad": round(float(cad), 2) if (cad is not None and n_cad == n and n) else None, "events": int(n or 0)}
