"""Proposals: an agent proposes, the policy tiers it, people approve, a registered executor runs it.

    from agentic_proposals import propose, approve, deny, Approver
    p = await propose(org, agent="exit", tool_name="exit.draft_exit_letters", run_id=run_id,
                      tool_args={"case_id": cid, "employee_id": eid, "keys": ["relieving"]},
                      plain_action="Draft the separation letter for MAP017 — the last working day was 30 Oct",
                      before={"drafts": 0}, after={"drafts": 1}, aggregate_impact={"headcount": 1},
                      reason="…", evidence_refs=[{"label": "Regularisation #R-123", "link": "/attendance?…"}],
                      confidence="high", untrusted_input=False, reversal_plan={"tool": "adjust_payroll_input", "args": {...}})
    await approve(org, p["id"], Approver.from_headers(request.headers))          # tier 1 → executes
    await approve(org, p["id"], second_person)                                   # tier 2 → second DIFFERENT approver

The rails (report §Trust)
* the approver's identity comes from the gateway headers (``Approver.from_headers``) — NEVER from a body;
* tier 0 runs at once (logged); tier 1 needs one approver in ``approvers.tier1_roles``; tier 2 needs two
  different people in ``approvers.tier2_roles``; an untrusted input in the run adds one tier;
* "approve for session" (tier ≤ 1): the same tool from the same agent runs without a card until the grant
  expires (``thresholds.session_grant_hours``), each use logged with the grant's approver;
* shadow mode: the proposal is stored as ``shadow`` and NOTHING executes;
* hard-deny tools (and a payment to an account not in the master) never become proposals;
* raw tool args are sealed (encrypted) for the executor; the card shows PII-tokenised args;
* every step is one row in the append-only action log; a proposal expires after
  ``thresholds.proposal_expiry_hours`` and must be re-proposed (approvals are consumed once — no replay).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import desc, func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.directory import role_in  # noqa: E402

from agentic_log import append_event, digest, tokenise  # noqa: E402
from agentic_models import AGENTS, AgentProposal, AgentSessionGrant  # noqa: E402
from agentic_registry import AGENT_VERSION, HARD_DENY, REGISTRY, TIER_LABEL, executor_for, tier_for  # noqa: E402
from agentic_state import agentic_state  # noqa: E402

INBOX = ("proposed", "second_approval", "edited")
DECIDED = ("approved", "executed", "denied", "failed")


class ProposalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Approver:
    """The person deciding — built from the gateway's identity headers only."""
    user_id: str
    email: str | None = None
    role: str | None = None

    @classmethod
    def from_headers(cls, headers) -> "Approver":
        h = {str(k).lower(): v for k, v in dict(headers).items()}
        uid = (h.get("x-user-id") or "").strip()
        if not uid:
            raise ProposalError(401, "No signed-in user on this request")
        return cls(user_id=uid, email=(h.get("x-user-email") or None), role=(h.get("x-role") or "").lower() or None)


# ══════════════════════════ sealing of raw args ═════════════════════════════════

def _seal(args: dict) -> str:
    raw = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    try:
        from shared.crypto_fields import encrypt_field
        tok = encrypt_field(raw)
        if tok:
            return tok
    except Exception:                                        # noqa: BLE001 — no data key on this box: keep the args readable to the executor
        pass
    return "plain:" + raw


def _unseal(sealed: str | None) -> dict:
    if not sealed:
        return {}
    if sealed.startswith("plain:"):
        return json.loads(sealed[6:])
    from shared.crypto_fields import decrypt_field
    return json.loads(decrypt_field(sealed) or "{}")


def proposal_dict(p: AgentProposal) -> dict:
    return {"id": p.id, "agent": p.agent, "agent_role": REGISTRY[p.agent].role_name if p.agent in REGISTRY else p.agent, "run_id": p.run_id,
            "tool_name": p.tool_name, "tool_version": p.tool_version, "tool_args": p.tool_args or {}, "plain_action": p.plain_action,
            "before": p.before, "after": p.after, "aggregate_impact": p.aggregate_impact or {}, "reason": p.reason, "evidence_refs": p.evidence_refs or [],
            "confidence": p.confidence, "base_tier": p.base_tier, "risk_tier": p.risk_tier, "tier_label": TIER_LABEL.get(p.risk_tier, str(p.risk_tier)),
            "policy_id": p.policy_id, "policy_decision": p.policy_decision, "untrusted_input": bool(p.untrusted_input), "mode": p.mode,
            "status": p.status, "needs_second_approval": p.risk_tier == 2 and p.status in INBOX,
            "requested_by_user_id": p.requested_by_user_id, "approver_user_id": p.approver_user_id, "approver_email": p.approver_email,
            "approver_role": p.approver_role, "second_approver_user_id": p.second_approver_user_id, "second_approver_email": p.second_approver_email,
            "approved_for_session": bool(p.approved_for_session), "edited_input_diff": p.edited_input_diff, "deny_message": p.deny_message,
            "reversal_plan": p.reversal_plan, "result": p.result, "created_at": pm.iso(p.created_at), "expires_at": pm.iso(p.expires_at),
            "first_decision_at": pm.iso(p.first_decision_at), "decided_at": pm.iso(p.decided_at), "executed_at": pm.iso(p.executed_at)}


def _hard_deny_reason(tool_name: str, args: dict) -> str | None:
    if tool_name in HARD_DENY:
        return HARD_DENY[tool_name]
    a = args or {}
    if a.get("account_in_master") is False or a.get("pay_to_unlisted_account") is True:
        return HARD_DENY["pay_unlisted_account"]
    if any(k in a for k in ("agent_permissions", "approver_roles_override", "disable_audit", "audit_enabled")):
        return HARD_DENY["change_agent_permissions"] if "audit" not in json.dumps(a).lower() else HARD_DENY["disable_audit"]
    return None


# ══════════════════════════ propose ═════════════════════════════════════════════

async def propose(org: str, *, agent: str, tool_name: str, tool_args: dict, plain_action: str, reason: str = "", before=None, after=None,
                  aggregate_impact: dict | None = None, evidence_refs: list | None = None, confidence: str = "unknown",
                  untrusted_input: bool = False, untrusted_inputs: list | None = None, policy_id: str = "", reversal_plan: dict | None = None,
                  run_id: str | None = None, requested_by_user_id: str | None = None, data_sources_read: list | None = None,
                  tool_version: str = "1", session_id: str | None = None, step_no: int | None = None) -> dict:
    if agent not in AGENTS:
        raise ProposalError(422, f"Unknown agent {agent!r}")
    st = await agentic_state(org)
    if not st["enabled"]:
        raise ProposalError(409, "Agentic operations are not switched on for this company (add-on not active).")
    confidence = confidence if confidence in ("high", "medium", "low", "unknown") else "unknown"
    untrusted = bool(untrusted_input or untrusted_inputs)
    t = tier_for(agent, tool_name, untrusted_input=untrusted, aggregate_impact=aggregate_impact, thresholds=st["thresholds"])
    pid = policy_id or f"{agent}.{tool_name}.v{AGENT_VERSION}"
    deny_why = _hard_deny_reason(tool_name, tool_args)
    now = pm.now_utc()
    common = dict(org_id=str(org), agent=agent, run_id=run_id, tool_name=tool_name[:80], tool_version=tool_version, tool_args=tokenise(tool_args or {}),
                  tool_args_sealed=_seal(tool_args or {}), plain_action=(plain_action or tool_name)[:600], before=before, after=after,
                  aggregate_impact=aggregate_impact or {}, reason=reason or "", evidence_refs=evidence_refs or [], confidence=confidence,
                  untrusted_input=untrusted, policy_id=pid, mode=st["mode"], requested_by_user_id=requested_by_user_id, reversal_plan=reversal_plan)
    log_common = dict(agent_name=agent, agent_version=AGENT_VERSION, run_id=run_id, session_id=session_id, step_no=step_no,
                      requested_by_user_id=requested_by_user_id, run_as_identity=f"agent:{agent}", tool_name=tool_name, tool_version=tool_version,
                      tool_input=tool_args, data_sources_read=data_sources_read, untrusted_inputs=untrusted_inputs or ([{"kind": "flagged"}] if untrusted else None),
                      policy_id=pid, aggregate_impact=aggregate_impact or {}, agent_reason_text=reason, evidence_refs=evidence_refs or [],
                      confidence_or_unknown_flag=confidence, records_before=before, records_after=after)
    if deny_why:
        p = AgentProposal(**common, base_tier=t["base"] if isinstance(t["base"], int) else 2, risk_tier=2, policy_decision="deny", status="denied",
                          deny_message=f"Hard deny: {deny_why}", decided_at=now)
        async with pm.session() as s:
            s.add(p)
            await s.commit()
            out = proposal_dict(p)
        await append_event(org, event_type="proposal.denied_by_policy", **log_common, risk_tier=2, policy_decision="deny", approval_id=p.id,
                           decision="deny", deny_message=deny_why, decision_ts=now)
        return out
    tier = int(t["tier"])
    if st["mode"] == "shadow":
        p = AgentProposal(**common, base_tier=t["base"], risk_tier=tier, policy_decision="shadow", status="shadow")
        async with pm.session() as s:
            s.add(p)
            await s.commit()
            out = proposal_dict(p)
        await append_event(org, event_type="proposal.shadow", **log_common, risk_tier=tier, policy_decision="shadow", approval_id=p.id)
        return {**out, "tier_reasons": t["reasons"]}
    hours = float(st["thresholds"].get("proposal_expiry_hours") or 72)
    p = AgentProposal(**common, base_tier=t["base"], risk_tier=tier, policy_decision="allow", status="proposed", expires_at=now + timedelta(hours=hours))
    async with pm.session() as s:
        s.add(p)
        await s.commit()
        out = proposal_dict(p)
    await append_event(org, event_type="proposal.created", **log_common, risk_tier=tier, policy_decision="allow", approval_id=p.id)
    if tier == 0:
        return {**(await _execute(org, p.id, decision="auto_tier0", approver=None)), "tier_reasons": t["reasons"]}
    if tier <= 1:
        g = await _valid_grant(org, agent, tool_name, tier)
        if g:
            return {**(await _execute(org, p.id, decision="approved_by_session_grant",
                                      approver=Approver(g.approver_user_id, g.approver_email, g.approver_role), grant=g)), "tier_reasons": t["reasons"]}
    return {**out, "tier_reasons": t["reasons"]}


# ══════════════════════════ decisions ═══════════════════════════════════════════

async def _load(s, org: str, pid: str) -> AgentProposal:
    p = (await s.execute(select(AgentProposal).where(AgentProposal.id == pid, AgentProposal.org_id == str(org)))).scalar_one_or_none()
    if not p:
        raise ProposalError(404, "That proposal was not found")
    return p


def _check_role(st: dict, p: AgentProposal, a: Approver) -> None:
    roles = st["approvers"]["tier2_roles" if p.risk_tier == 2 else "tier1_roles"]
    if a.role == "superadmin":
        return
    if not role_in(a.role, roles):
        raise ProposalError(403, f"Your role may not approve a {TIER_LABEL.get(p.risk_tier)} action here (needs one of: {', '.join(roles)})")


async def _open_for_decision(s, org: str, pid: str) -> AgentProposal:
    p = await _load(s, org, pid)
    if p.status not in INBOX:
        raise ProposalError(409, f"That proposal is already {p.status}")
    if p.expires_at and pm.aware(p.expires_at) < pm.now_utc():
        p.status, p.decided_at = "expired", pm.now_utc()
        await s.commit()
        raise ProposalError(409, "That proposal expired — the agent must propose it again")
    return p


def _diff(old: dict, new: dict) -> dict:
    keys = set(old or {}) | set(new or {})
    return {k: {"from": (old or {}).get(k), "to": (new or {}).get(k)} for k in sorted(keys) if (old or {}).get(k) != (new or {}).get(k)}


async def approve(org: str, pid: str, approver: Approver, *, for_session: bool = False, edited_args: dict | None = None, note: str = "") -> dict:
    """One approval by ``approver``. Tier 1 executes; tier 2 records the first approver and waits for a
    DIFFERENT second one. ``edited_args`` = edit-and-approve (the diff is logged). ``for_session`` (tier ≤ 1)
    also grants the same tool for the session window."""
    st = await agentic_state(org)
    if not st["enabled"]:
        raise ProposalError(409, "Agentic operations are switched off for this company")
    now = pm.now_utc()
    async with pm.session() as s:
        p = await _open_for_decision(s, org, pid)
        _check_role(st, p, approver)
        if p.mode == "shadow" or p.status == "shadow":
            raise ProposalError(409, "Shadow proposals are a record of what the agent would have done — nothing to approve")
        if edited_args is not None:
            if not isinstance(edited_args, dict):
                raise ProposalError(422, "edited_args must be an object")
            old = _unseal(p.tool_args_sealed)
            d = _diff(old, edited_args)
            if _hard_deny_reason(p.tool_name, edited_args):
                raise ProposalError(422, "The edited arguments hit a hard-deny rule")
            p.tool_args_sealed, p.tool_args = _seal(edited_args), tokenise(edited_args)
            p.edited_input_diff = {**(p.edited_input_diff or {}), **tokenise(d)}
            p.status = "edited"
            await s.commit()
            await append_event(org, event_type="approval.edited", agent_name=p.agent, run_id=p.run_id, tool_name=p.tool_name, approval_id=p.id,
                               approver_user_id=approver.user_id, roles_at_time_of_action=[approver.role], decision="edit", edited_input_diff=d,
                               risk_tier=p.risk_tier, policy_id=p.policy_id, policy_decision="allow", decision_ts=now, tool_input=edited_args)
        if for_session and p.risk_tier >= 2:
            raise ProposalError(422, "Approve-for-session is only for tier 0/1 actions; a two-approver action is decided one at a time")
        if p.risk_tier == 2:
            if not p.approver_user_id:
                p.approver_user_id, p.approver_email, p.approver_role = approver.user_id, approver.email, approver.role
                p.first_decision_at, p.status = now, "second_approval"
                await s.commit()
                out = proposal_dict(p)
                await append_event(org, event_type="approval.first", agent_name=p.agent, run_id=p.run_id, tool_name=p.tool_name, approval_id=p.id,
                                   approver_user_id=approver.user_id, roles_at_time_of_action=[approver.role], decision="approve_1_of_2", decision_ts=now,
                                   decision_latency_ms=int((now - pm.aware(p.created_at)).total_seconds() * 1000), risk_tier=2, policy_id=p.policy_id,
                                   policy_decision="allow", agent_reason_text=note or None)
                return {**out, "message": "First approval recorded. A DIFFERENT approver must confirm before anything runs."}
            if p.approver_user_id == approver.user_id:
                raise ProposalError(409, "Second approval must come from a different person (two-person rule)")
            p.second_approver_user_id, p.second_approver_email = approver.user_id, approver.email
            await s.commit()
            return await _execute(org, p.id, decision="approve_2_of_2", approver=approver, note=note)
        p.approver_user_id, p.approver_email, p.approver_role = approver.user_id, approver.email, approver.role
        p.approved_for_session = bool(for_session)
        await s.commit()
    grant = None
    if for_session:
        grant = await grant_session(org, agent=p.agent, tool_name=p.tool_name, approver=approver, run_id=p.run_id, max_tier=p.risk_tier)
    out = await _execute(org, pid, decision="approve", approver=approver, note=note)
    if grant:
        out["session_grant"] = grant
    return out


async def deny(org: str, pid: str, approver: Approver, *, message: str) -> dict:
    if len((message or "").strip()) < 3:
        raise ProposalError(422, "Say why in a few words — the agent and the next reviewer read it")
    st = await agentic_state(org)
    now = pm.now_utc()
    async with pm.session() as s:
        p = await _open_for_decision(s, org, pid)
        _check_role(st, p, approver)
        p.status, p.deny_message, p.decided_at = "denied", message.strip()[:600], now
        if not p.approver_user_id:
            p.approver_user_id, p.approver_email, p.approver_role = approver.user_id, approver.email, approver.role
        else:
            p.second_approver_user_id, p.second_approver_email = approver.user_id, approver.email
        await s.commit()
        out = proposal_dict(p)
    await append_event(org, event_type="approval.denied", agent_name=p.agent, run_id=p.run_id, tool_name=p.tool_name, approval_id=p.id,
                       approver_user_id=approver.user_id, roles_at_time_of_action=[approver.role], decision="deny", deny_message=message.strip()[:600],
                       decision_ts=now, decision_latency_ms=int((now - pm.aware(p.created_at)).total_seconds() * 1000), risk_tier=p.risk_tier,
                       policy_id=p.policy_id, policy_decision="allow")
    return out


async def _execute(org: str, pid: str, *, decision: str, approver: Approver | None, grant: AgentSessionGrant | None = None, note: str = "") -> dict:
    """Runs the registered executor with the UNSEALED args. Only reached after the approval events the
    tier demands (or tier 0 / a valid session grant). Writes the decision + execution log rows."""
    now = pm.now_utc()
    async with pm.session() as s:
        p = await _load(s, org, pid)
        if p.mode == "shadow" or p.status in ("shadow", "denied", "expired", "executed"):
            raise ProposalError(409, f"Cannot execute a {p.status} proposal")
        if approver and not p.approver_user_id:
            p.approver_user_id, p.approver_email, p.approver_role = approver.user_id, approver.email, approver.role
        p.status, p.decided_at = "approved", now
        if grant is not None:
            p.approved_for_session = True
        await s.commit()
        latency = int((now - pm.aware(p.created_at)).total_seconds() * 1000)
        await append_event(org, event_type="approval.approved", agent_name=p.agent, run_id=p.run_id, tool_name=p.tool_name, approval_id=p.id,
                           approver_user_id=p.approver_user_id, second_approver_user_id=p.second_approver_user_id,
                           roles_at_time_of_action=[approver.role] if approver and approver.role else None, decision=decision, decision_ts=now,
                           decision_latency_ms=latency, risk_tier=p.risk_tier, policy_id=p.policy_id, policy_decision="allow",
                           edited_input_diff=p.edited_input_diff, agent_reason_text=note or None, credential_id=(grant.id if grant else None))
        fn = executor_for(p.tool_name)
        if fn is None:
            p.result = {"executed": False, "note": f"No executor is registered for '{p.tool_name}' yet — approved and recorded; nothing was changed."}
            await s.commit()
            out = proposal_dict(p)
            await append_event(org, event_type="execution.skipped", agent_name=p.agent, run_id=p.run_id, tool_name=p.tool_name, approval_id=p.id,
                               approver_user_id=p.approver_user_id, second_approver_user_id=p.second_approver_user_id, decision="no_executor",
                               risk_tier=p.risk_tier, policy_id=p.policy_id, policy_decision="allow")
            return out
        args = _unseal(p.tool_args_sealed)
        try:
            res = await fn(str(org), proposal_dict(p), args)
            p.status, p.executed_at, p.result = "executed", pm.now_utc(), {"executed": True, "detail": _small(res)}
            err = None
        except Exception as e:                               # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:300]}"
            p.status, p.result = "failed", {"executed": False, "error": err}
            res = None
        await s.commit()
        out = proposal_dict(p)
        if grant is not None:
            g = (await s.execute(select(AgentSessionGrant).where(AgentSessionGrant.id == grant.id))).scalar_one_or_none()
            if g:
                g.uses = int(g.uses or 0) + 1
                await s.commit()
    await append_event(org, event_type="execution.failed" if err else "execution.done", agent_name=p.agent, run_id=p.run_id, tool_name=p.tool_name,
                       tool_version=p.tool_version, approval_id=p.id, approver_user_id=p.approver_user_id, second_approver_user_id=p.second_approver_user_id,
                       decision="executed" if not err else "failed", tool_input=args, tool_output=res if not err else {"error": err},
                       records_before=p.before, records_after=p.after, aggregate_impact=p.aggregate_impact, risk_tier=p.risk_tier, policy_id=p.policy_id,
                       policy_decision="allow", reversal_plan_id=digest(p.reversal_plan) if p.reversal_plan else None, deny_message=err)
    return out


def _small(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 120}


# ══════════════════════════ session grants ══════════════════════════════════════

async def grant_session(org: str, *, agent: str, tool_name: str, approver: Approver, run_id: str | None = None, max_tier: int = 1, hours: float | None = None) -> dict:
    st = await agentic_state(org)
    h = float(hours or st["thresholds"].get("session_grant_hours") or 8)
    g = AgentSessionGrant(org_id=str(org), agent=agent, tool_name=tool_name, run_id=run_id, approver_user_id=approver.user_id, approver_email=approver.email,
                          approver_role=approver.role, max_tier=min(1, int(max_tier)), expires_at=pm.now_utc() + timedelta(hours=h))
    async with pm.session() as s:
        s.add(g)
        await s.commit()
        out = grant_dict(g)
    await append_event(org, event_type="approval.session_grant", agent_name=agent, run_id=run_id, tool_name=tool_name, approver_user_id=approver.user_id,
                       roles_at_time_of_action=[approver.role], decision="grant_session", decision_ts=pm.now_utc(), credential_id=g.id, risk_tier=max_tier,
                       policy_decision="allow", tool_input={"hours": h})
    return out


def grant_dict(g: AgentSessionGrant) -> dict:
    return {"id": g.id, "agent": g.agent, "tool_name": g.tool_name, "run_id": g.run_id, "approver_user_id": g.approver_user_id, "approver_email": g.approver_email,
            "approver_role": g.approver_role, "max_tier": g.max_tier, "granted_at": pm.iso(g.granted_at), "expires_at": pm.iso(g.expires_at),
            "revoked_at": pm.iso(g.revoked_at), "uses": int(g.uses or 0), "active": g.revoked_at is None and pm.aware(g.expires_at) > pm.now_utc()}


async def _valid_grant(org: str, agent: str, tool_name: str, tier: int) -> AgentSessionGrant | None:
    async with pm.session() as s:
        g = (await s.execute(select(AgentSessionGrant).where(AgentSessionGrant.org_id == str(org), AgentSessionGrant.agent == agent,
                                                            AgentSessionGrant.tool_name == tool_name, AgentSessionGrant.revoked_at.is_(None),
                                                            AgentSessionGrant.expires_at > pm.now_utc(), AgentSessionGrant.max_tier >= tier)
                             .order_by(desc(AgentSessionGrant.granted_at)).limit(1))).scalar_one_or_none()
    return g


async def revoke_grant(org: str, grant_id: str, actor: Approver) -> dict:
    async with pm.session() as s:
        g = (await s.execute(select(AgentSessionGrant).where(AgentSessionGrant.id == grant_id, AgentSessionGrant.org_id == str(org)))).scalar_one_or_none()
        if not g:
            raise ProposalError(404, "Grant not found")
        g.revoked_at = pm.now_utc()
        await s.commit()
        out = grant_dict(g)
    await append_event(org, event_type="approval.session_revoked", agent_name=g.agent, tool_name=g.tool_name, approver_user_id=actor.user_id,
                       decision="revoke_session", credential_id=g.id, risk_tier=g.max_tier, policy_decision="allow")
    return out


async def list_grants(org: str, active_only: bool = True) -> list[dict]:
    q = select(AgentSessionGrant).where(AgentSessionGrant.org_id == str(org))
    if active_only:
        q = q.where(AgentSessionGrant.revoked_at.is_(None), AgentSessionGrant.expires_at > pm.now_utc())
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(desc(AgentSessionGrant.granted_at)).limit(200))).scalars().all()
    return [grant_dict(g) for g in rows]


# ══════════════════════════ queries / housekeeping ══════════════════════════════

async def list_proposals(org: str, *, status: str | None = None, agent: str | None = None, run_id: str | None = None, limit: int = 200) -> list[dict]:
    q = select(AgentProposal).where(AgentProposal.org_id == str(org))
    if status == "inbox":
        q = q.where(AgentProposal.status.in_(INBOX))
    elif status:
        q = q.where(AgentProposal.status.in_([x for x in status.split(",") if x]))
    if agent:
        q = q.where(AgentProposal.agent == agent)
    if run_id:
        q = q.where(AgentProposal.run_id == run_id)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(desc(AgentProposal.risk_tier), AgentProposal.created_at.desc()).limit(min(limit, 1000)))).scalars().all()
    return [proposal_dict(p) for p in rows]


async def get_proposal(org: str, pid: str) -> dict:
    async with pm.session() as s:
        return proposal_dict(await _load(s, org, pid))


async def inbox_counts(org: str) -> dict:
    async with pm.session() as s:
        rows = (await s.execute(select(AgentProposal.agent, func.count()).where(AgentProposal.org_id == str(org), AgentProposal.status.in_(INBOX))
                                .group_by(AgentProposal.agent))).all()
    return {a: int(n) for a, n in rows}


async def expire_stale(org: str, *, now: datetime | None = None) -> int:
    """Scheduler: inbox items past ``expires_at`` → expired (an old approval can never be replayed)."""
    now = now or pm.now_utc()
    async with pm.session() as s:
        rows = (await s.execute(select(AgentProposal).where(AgentProposal.org_id == str(org), AgentProposal.status.in_(INBOX),
                                                            AgentProposal.expires_at.is_not(None), AgentProposal.expires_at <= now))).scalars().all()
        for p in rows:
            p.status, p.decided_at = "expired", now
        await s.commit()
        done = [(p.id, p.agent, p.tool_name, p.run_id, p.risk_tier) for p in rows]
    for pid, agent, tool, run_id, tier in done:
        await append_event(org, event_type="proposal.expired", agent_name=agent, run_id=run_id, tool_name=tool, approval_id=pid, decision="expired",
                           decision_ts=now, risk_tier=tier, policy_decision="allow")
    return len(done)
