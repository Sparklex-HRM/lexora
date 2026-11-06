"""HR Desk ↔ agentic foundation (phase 2, P2-C): the HR Desk as a registered agent.

* ``state(org)``                → agentic_state; the add-on gates everything that EXECUTES (tickets, model
                                   narration, CSAT, value ledger, weekly report, dashboard). The hard router
                                   and grounded cited answers run for EVERY plan — that is a legal safety rule.
* one desk SESSION = one RUN     of agent ``hr_desk`` (ai_agent_runs): started on the first turn, finished
                                   when the session ends (hand-off / hard route) or after IDLE_MINUTES by the
                                   scheduler hook; tokens + cost summed from the session's action-log events.
* value ledger (agentic_value)   ``query_resolved`` on a self-solved turn (provisional → confirmed on thumbs-up,
                                   compensated with units −1 on thumbs-down — the ledger stays append-only),
                                   ``handoff_created`` (count), ``ic_referral`` (count only, never $).
* weekly report tick            Monday (org day) once per ISO week per add-on org: desk.report → notify HR.

Everything here is best-effort: a failure never changes the answer the employee sees.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from desk.policy_models import DeskSession  # noqa: E402

log = logging.getLogger("lexora.desk.agent")
AGENT = "hr_desk"
IDLE_MINUTES = 30
_HOOKS_DONE = False


async def state(org: str) -> dict:
    try:
        from agentic_state import agentic_state
        return await agentic_state(org)
    except Exception as e:                                   # noqa: BLE001
        log.debug("agentic_state unavailable for %s: %s", org, e)
        return {"enabled": False, "addon_purchased": False, "paused": False, "mode": "shadow"}


# ══════════════════════════ runs ═══════════════════════════════════════════════

async def ensure_run(org: str, session_id: str, user_id: str | None, st: dict | None = None) -> str | None:
    """The run this session belongs to (started on the first turn). None when the add-on is off."""
    st = st or await state(org)
    if not st.get("enabled"):
        return None
    try:
        async with pm.session() as s:
            sess = await s.get(DeskSession, session_id)
            if sess is None or sess.org_id != org:
                return None
            if sess.run_id:
                return sess.run_id
        from agentic_runs import start_run
        run = await start_run(org, agent=AGENT, trigger="question", requested_by_user_id=user_id or None)
        async with pm.session() as s:
            sess = await s.get(DeskSession, session_id)
            sess.run_id = run["id"]
            await s.commit()
        return run["id"]
    except Exception as e:                                   # noqa: BLE001
        log.warning("hr_desk start_run failed for %s: %s", org, e)
        return None


async def _run_tokens(org: str, run_id: str) -> dict:
    try:
        from agentic_models import AgentActionLog
        async with pm.session() as s:
            i, o, cr, cw, usd = (await s.execute(
                select(func.coalesce(func.sum(AgentActionLog.input_tokens), 0), func.coalesce(func.sum(AgentActionLog.output_tokens), 0),
                       func.coalesce(func.sum(AgentActionLog.cache_read_tokens), 0), func.coalesce(func.sum(AgentActionLog.cache_write_tokens), 0),
                       func.coalesce(func.sum(AgentActionLog.cost_usd), 0.0))
                .where(AgentActionLog.org_id == str(org), AgentActionLog.run_id == run_id))).one()
        return {"input_tokens": int(i or 0), "output_tokens": int(o or 0), "cache_read_tokens": int(cr or 0), "cache_write_tokens": int(cw or 0),
                "cost_usd": float(usd or 0.0)}
    except Exception:                                        # noqa: BLE001
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0}


async def finish_session_run(org: str, session_id: str, *, reason: str = "ended") -> dict | None:
    """Close the run of a session (hand-off, hard route, idle). Idempotent: a finished run is left alone."""
    try:
        async with pm.session() as s:
            sess = await s.get(DeskSession, session_id)
            if sess is None or sess.org_id != org or not sess.run_id:
                return None
            snap = {"session_id": sess.id, "outcome": sess.outcome, "turns": int(sess.turns or 0), "answered": int(sess.answered or 0),
                    "abstains": int(sess.abstains or 0), "unresolved": int(sess.unresolved or 0), "ticket_id": sess.ticket_id,
                    "handoff_reason": sess.handoff_reason, "csat": sess.csat, "run_id": sess.run_id}
        from agentic_models import AgentRun
        async with pm.session() as s:
            r = (await s.execute(select(AgentRun).where(AgentRun.id == snap["run_id"], AgentRun.org_id == str(org)))).scalar_one_or_none()
            if r is None or r.status != "running":
                return None
        from agentic_runs import finish_run
        tok = await _run_tokens(org, snap["run_id"])
        headline = {"self_solved": "Answered from policy / own records — self-solved", "handed_off": "Handed off to a person (ticket)",
                    "hard_routed": "Sensitive topic — fixed message, routed to a person", "unresolved": "Not resolved",
                    "open": "Session ended without a rating"}.get(snap["outcome"] or "open", snap["outcome"])
        return await finish_run(org, snap["run_id"], summary={"headline": headline, "closed_by": reason, **{k: v for k, v in snap.items() if k != "run_id"}},
                                checks_run=snap["turns"], findings=snap["abstains"] + snap["unresolved"], proposals=0, tasks_opened=0,
                                input_tokens=tok["input_tokens"], output_tokens=tok["output_tokens"], cache_read_tokens=tok["cache_read_tokens"],
                                cache_write_tokens=tok["cache_write_tokens"], cost_usd=tok["cost_usd"])
    except Exception as e:                                   # noqa: BLE001
        log.warning("hr_desk finish_run failed for %s/%s: %s", org, session_id, e)
        return None


# ══════════════════════════ value ledger ═══════════════════════════════════════

def _turn_note(turn_id: str) -> str:
    return f"turn:{turn_id}"


async def record_turn_value(org: str, *, turn_id: str, kind: str, abstained: bool, ticket: dict | None, ic_referral: bool,
                            run_id: str | None, st: dict | None = None) -> list[dict]:
    """Outcomes of one turn. query_resolved is PROVISIONAL (unconfirmed) until the person rates it."""
    st = st or await state(org)
    if not st.get("enabled"):
        return []
    out = []
    try:
        from agentic_value import record_outcome
        if ic_referral:
            out.append(await record_outcome(org, agent=AGENT, kind="ic_referral", units=1, basis="sealed IC referral (count only — never $)",
                                            run_id=run_id, note=_turn_note(turn_id)))
        if ticket:
            out.append(await record_outcome(org, agent=AGENT, kind="handoff_created", units=1,
                                            basis=f"helpdesk ticket {ticket.get('number') or ticket.get('id') or ''} ({ticket.get('category') or 'policy'}) with transcript",
                                            run_id=run_id, note=_turn_note(turn_id)))
        elif kind in ("answer", "pay_change", "tools") and not abstained:
            out.append(await record_outcome(org, agent=AGENT, kind="query_resolved", units=1,
                                            basis="self-solved answer (cited / structured); provisional until the person rates it",
                                            run_id=run_id, note=_turn_note(turn_id)))
    except Exception as e:                                   # noqa: BLE001
        log.warning("hr_desk record_outcome failed for %s: %s", org, e)
    return out


async def on_feedback(org: str, *, turn_id: str | None, rating: str, user_id: str | None, st: dict | None = None) -> dict:
    """Thumbs-up confirms the provisional query_resolved row; thumbs-down compensates it (units −1)."""
    st = st or await state(org)
    if not st.get("enabled") or not turn_id:
        return {"applied": False}
    try:
        from agentic_models import AgentValueEvent
        from agentic_value import confirm_outcome, record_outcome
        async with pm.session() as s:
            rows = (await s.execute(select(AgentValueEvent).where(AgentValueEvent.org_id == str(org), AgentValueEvent.agent == AGENT,
                                                                   AgentValueEvent.kind == "query_resolved",
                                                                   AgentValueEvent.note.in_([_turn_note(turn_id), "revoke:" + _turn_note(turn_id)])))).scalars().all()
        base = next((r for r in rows if r.note == _turn_note(turn_id) and float(r.units or 0) > 0), None)
        revoked = any(r.note == "revoke:" + _turn_note(turn_id) for r in rows)
        if base is None:
            return {"applied": False}
        if rating == "up":
            if base.confirmed_by_user_id:
                return {"applied": False, "already": "confirmed"}
            ev = await confirm_outcome(org, base.id, actor_user_id=user_id or "employee")
            return {"applied": True, "action": "confirmed", "event_id": ev["id"]}
        if revoked:
            return {"applied": False, "already": "revoked"}
        ev = await record_outcome(org, agent=AGENT, kind="query_resolved", units=-1,
                                  basis="revoked: the person said the answer did not help (thumbs-down)", run_id=base.run_id,
                                  note="revoke:" + _turn_note(turn_id))
        return {"applied": True, "action": "revoked", "event_id": ev["id"]}
    except Exception as e:                                   # noqa: BLE001
        log.warning("hr_desk feedback value update failed for %s: %s", org, e)
        return {"applied": False, "error": str(e)[:120]}


async def value_counts(org: str, days: int = 7) -> dict:
    """Net outcome counts for the dashboard (units summed, so a revoked query nets to 0)."""
    out = {"query_resolved": 0.0, "query_resolved_confirmed": 0, "handoff_created": 0.0, "ic_referral": 0.0}
    try:
        from agentic_models import AgentValueEvent
        since = pm.now_utc() - timedelta(days=max(1, min(int(days or 7), 366)))
        async with pm.session() as s:
            rows = (await s.execute(select(AgentValueEvent).where(AgentValueEvent.org_id == str(org), AgentValueEvent.agent == AGENT,
                                                                   AgentValueEvent.at >= since))).scalars().all()
        for r in rows:
            if r.kind in out:
                out[r.kind] += float(r.units or 0)
            if r.kind == "query_resolved" and r.confirmed_by_user_id and float(r.units or 0) > 0:
                out["query_resolved_confirmed"] += 1
    except Exception:                                        # noqa: BLE001
        pass
    return out


# ══════════════════════════ scheduler hooks ═════════════════════════════════════

async def close_idle_sessions(org: str, st: dict) -> int:
    """Finish the run of every session idle for IDLE_MINUTES (add-on orgs only — the scheduler already filters)."""
    cutoff = pm.now_utc() - timedelta(minutes=IDLE_MINUTES)
    try:
        async with pm.session() as s:
            ids = (await s.execute(select(DeskSession.id).where(DeskSession.org_id == str(org), DeskSession.run_id.is_not(None),
                                                                 DeskSession.last_at < cutoff).limit(200))).scalars().all()
    except Exception:                                        # noqa: BLE001
        return 0
    n = 0
    for sid in ids:
        if await finish_session_run(org, sid, reason="idle"):
            n += 1
            try:
                async with pm.session() as s:
                    sess = await s.get(DeskSession, sid)
                    if sess and not sess.ended_at:
                        sess.ended_at = pm.now_utc()
                        await s.commit()
            except Exception:                                # noqa: BLE001
                pass
    return n


async def weekly_report_tick(org: str, st: dict) -> bool:
    """Monday (org calendar): one weekly 'top questions' report per ISO week → HR (in-app + e-mail via notify)."""
    try:
        from shared.utils.org_time import org_today
        today = await org_today(org)
    except Exception:                                        # noqa: BLE001
        today = date.today()
    if today.weekday() != 0:
        return False
    try:
        from agentic_log import recent
        iso = today.isocalendar()
        for ev in await recent(org, limit=50, event_type="desk.weekly_report"):
            ts = ev.get("ts_utc") or ""
            try:
                d = date.fromisoformat(str(ts)[:10])
            except ValueError:
                continue
            if d.isocalendar()[:2] == iso[:2]:
                return False                                 # already sent this week
        from desk import report as RP
        await RP.send_weekly(org, days=7, requested_by=None)
        return True
    except Exception as e:                                   # noqa: BLE001
        log.warning("hr_desk weekly report tick failed for %s: %s", org, e)
        return False


def register_hooks() -> None:
    global _HOOKS_DONE
    if _HOOKS_DONE:
        return
    import agentic_scheduler
    for h in (close_idle_sessions, weekly_report_tick):
        if h not in agentic_scheduler.tick_hooks:
            agentic_scheduler.tick_hooks.append(h)
    _HOOKS_DONE = True
