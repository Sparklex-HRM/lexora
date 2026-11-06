"""Append-only answer audit (PIPEDA safeguards: access logs + review; sexual-harassment confidentiality minimisation).

One row per answer in ``desk_answer_audit``. Written best-effort AFTER the answer is composed; a
failure never changes what the person sees. Fields: actor, employee id, session, intent class,
retrieved clause ids + versions, model id, prompt hash, tokenised (shielded) inputs, answer digest,
citations, confidence, escalation reason, ticket id, human reviewer actions.

Bridge to the agentic foundation (phase 2, P2-C): every desk answer is ALSO one event in the
hash-chained, append-only ``ai_agent_action_log`` (``agentic_log.append_event``, agent ``hr_desk``,
event_type ``desk.answer`` / ``desk.hard_route`` / ``desk.tools`` / ``desk.review``). The event is
written FIRST so the desk row carries ``agentic_event_id``; when the agentic tables are unavailable the
desk row is still written (event id None). Tokens / cost on the event feed the agent's run cost.

Hard-routed turns: the row carries the class and NO question text (inputs_tokenised = None).
"""
from __future__ import annotations

import sys

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from desk.policy_models import DeskAnswerAudit  # noqa: E402

RETENTION_DAYS_MIN = 365        # access logs kept at least one year (privacy-centre retention default)
AGENT = "hr_desk"


def row(*, org: str, actor_user_id: str | None, employee_id: str | None, session_id: str | None, turn_id: str | None, intent_class: str,
        retrieved: list[dict], model: str | None, prompt_hash: str | None, inputs_tokenised: str | None, answer_digest: str | None,
        citations: list[dict], confidence: float | None, validation: str | None, escalation_reason: str | None, ticket_id: str | None,
        agentic_event_id: str | None = None, **_ignored) -> DeskAnswerAudit:
    return DeskAnswerAudit(org_id=org, actor_user_id=actor_user_id or None, employee_id=employee_id or None, session_id=session_id, turn_id=turn_id,
                           intent_class=(intent_class or "policy")[:40],
                           retrieved=[{"clause_pk": c.get("id"), "policy": c.get("policy"), "clause_id": c.get("clause_id"), "version": c.get("version")} for c in (retrieved or [])],
                           model=model, prompt_hash=prompt_hash, inputs_tokenised=inputs_tokenised, answer_digest=answer_digest,
                           citations=[{k: c.get(k) for k in ("tag", "policy", "clause_id", "version", "effective_from", "clause_pk")} for c in (citations or [])],
                           confidence=confidence, validation=(validation or "")[:40] or None, escalation_reason=(escalation_reason or "")[:40] or None,
                           ticket_id=ticket_id, agentic_event_id=agentic_event_id)


async def write(**kw) -> str | None:
    """kw = row() fields + optional ``run_id``, ``usage`` {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens},
    ``cost_usd``, ``event_type`` (default desk.answer). Returns the desk audit row id (None on failure)."""
    ev = await bridge(kw)
    try:
        r = row(**kw, agentic_event_id=(ev or {}).get("event_id"))
        async with pm.session() as s:
            s.add(r)
            await s.flush()
            rid = r.id
            await s.commit()
    except Exception:                                        # noqa: BLE001
        return None
    return rid


async def bridge(kw: dict) -> dict | None:
    """One hash-chained event in ai_agent_action_log for this answer (best effort — never raises)."""
    try:
        from agentic_log import append_event
        from agentic_registry import AGENT_VERSION
    except Exception:                                        # noqa: BLE001
        return None
    usage = kw.get("usage") or {}
    validation = kw.get("validation") or ""
    event_type = kw.get("event_type") or ("desk.hard_route" if str(validation).startswith("hard_route") else "desk.answer")
    conf = kw.get("confidence")
    try:
        return await append_event(
            kw.get("org"), event_type=event_type, agent_name=AGENT, agent_version=AGENT_VERSION,
            session_id=kw.get("session_id"), run_id=kw.get("run_id"),
            requested_by_user_id=kw.get("actor_user_id"), run_as_identity=f"agent:{AGENT}",
            prompt_template_id="desk.answer.v1" if kw.get("prompt_hash") else None, prompt_hash=kw.get("prompt_hash"), model_id=kw.get("model"),
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0),
            cost_usd=float(kw.get("cost_usd") or 0.0),
            tool_name=str(kw.get("intent_class") or "policy")[:40], tool_version="1",
            # hard-routed turns: class only — no question text reaches any log (confidentiality)
            tool_input=({"question_shielded": kw.get("inputs_tokenised")} if kw.get("inputs_tokenised") else {"class": kw.get("intent_class")}),
            tool_output={"answer_digest": kw.get("answer_digest"), "citations": [c.get("clause_pk") or c.get("clause_id") for c in (kw.get("citations") or [])]},
            data_sources_read=[{"clause_pk": c.get("id"), "policy": c.get("policy"), "clause_id": c.get("clause_id"), "version": c.get("version")}
                               for c in (kw.get("retrieved") or [])][:20],
            risk_tier=0, policy_decision="allow", agent_reason_text=str(validation)[:400] or None,
            evidence_refs=[{"label": c.get("label"), "link": c.get("deep_link")} for c in (kw.get("citations") or [])][:12],
            confidence_or_unknown_flag=(f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "unknown"),
            approval_id=kw.get("ticket_id"),
        )
    except Exception:                                        # noqa: BLE001
        return None


async def reviewer_action(org: str, audit_id: str, actor_user_id: str, action: str, note: str = "") -> str | None:
    """Human review is itself a NEW append-only row that points at the reviewed one (never an UPDATE)."""
    return await write(org=org, actor_user_id=actor_user_id, employee_id=None, session_id=None, turn_id=audit_id, intent_class="review",
                       retrieved=[], model=None, prompt_hash=None, inputs_tokenised=None, answer_digest=None, citations=[], confidence=None,
                       validation=f"review:{action}"[:40], escalation_reason=(note or "")[:40] or None, ticket_id=None, event_type="desk.review")
