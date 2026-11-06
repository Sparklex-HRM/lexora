"""HR Desk metrics — the definitions are code, published next to every number.

self_solved (ServiceNow "self-solved" definition, adopted 2026-10-25): a session that ended with at
least one cited / structured answer, NO ticket, NO hand-off, NO hard route — and, when the person
rated it, not a thumbs-down. Deflection rate = self_solved ÷ sessions (hard-routed sessions are
EXCLUDED from the denominator: they were never candidates for an AI answer).

abstain_rate   = turns the desk abstained on ÷ answered-or-abstained turns (hard-routed excluded)
handoff_rate   = sessions with a ticket ÷ sessions (hard-routed excluded)
csat           = thumbs-up ÷ (thumbs-up + thumbs-down) on rated turns; coverage = rated ÷ answered
median_ms      = median answer time over non-hard-routed turns
top_intents    = turn intents ranked (pay | leave | policy:<key> | <hard class>) — the weekly "top questions"
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

DEFINITIONS = {
    "self_solved": "Session ended with a cited or structured answer, no ticket, no hand-off, no hard route, and no thumbs-down (ServiceNow 'self-solved').",
    "deflection_rate": "self_solved sessions ÷ sessions that were candidates for an AI answer (hard-routed sessions excluded).",
    "abstain_rate": "abstained turns ÷ (answered + abstained) turns.",
    "handoff_rate": "sessions with a helpdesk ticket ÷ candidate sessions.",
    "csat": "thumbs-up ÷ rated turns; 'coverage' = rated ÷ answered turns.",
    "median_ms": "median time to answer over candidate turns.",
}


def session_outcome(sess: dict) -> str:
    """Pure classification used by the rollup and by the engine when a session is closed."""
    if sess.get("hard_routed") or sess.get("outcome") == "hard_routed":
        return "hard_routed"
    if sess.get("ticket_id") or sess.get("outcome") == "handed_off":
        return "handed_off"
    if int(sess.get("answered") or 0) > 0 and sess.get("csat") != "down":
        return "self_solved"
    if int(sess.get("turns") or 0) == 0:
        return "abandoned"
    return "unresolved"


def rollup(sessions: list[dict], turns: list[dict]) -> dict:
    """sessions: [{id, outcome, turns, answered, abstains, ticket_id, csat, hard_routed}], turns: [{session_id, intent, hard_routed, abstained, cited, rating, duration_ms}]."""
    outcomes = Counter(session_outcome(s) for s in sessions)
    candidates = [s for s in sessions if session_outcome(s) != "hard_routed"]
    n_c = len(candidates)
    self_solved = sum(1 for s in candidates if session_outcome(s) == "self_solved")
    handed = sum(1 for s in candidates if session_outcome(s) == "handed_off")
    cand_turns = [t for t in turns if not t.get("hard_routed")]
    answered = sum(1 for t in cand_turns if t.get("cited"))
    abst = sum(1 for t in cand_turns if t.get("abstained"))
    rated = [t for t in cand_turns if t.get("rating") in ("up", "down")]
    ups = sum(1 for t in rated if t["rating"] == "up")
    durs = [int(t.get("duration_ms") or 0) for t in cand_turns if t.get("duration_ms")]
    intents = Counter((t.get("intent") or "policy") for t in turns)
    return {
        "sessions": len(sessions), "candidate_sessions": n_c, "outcomes": dict(outcomes),
        "self_solved": self_solved, "deflection_rate": round(self_solved / n_c, 3) if n_c else None,
        "handoffs": handed, "handoff_rate": round(handed / n_c, 3) if n_c else None,
        "hard_routed_sessions": outcomes.get("hard_routed", 0),
        "turns": len(turns), "answered_turns": answered, "abstained_turns": abst,
        "abstain_rate": round(abst / (answered + abst), 3) if (answered + abst) else None,
        "csat": round(ups / len(rated), 3) if rated else None, "csat_rated": len(rated),
        "csat_coverage": round(len(rated) / answered, 3) if answered else None,
        "median_ms": int(statistics.median(durs)) if durs else None,
        "top_intents": [{"intent": k, "count": v} for k, v in intents.most_common(15)],
        "definitions": DEFINITIONS,
    }


def csat_prompt(turn_id: str) -> dict:
    """The feedback affordance appended to every delivered answer."""
    return {"turn_id": turn_id, "question": "Did this answer your question?", "options": [{"rating": "up", "label": "Yes"}, {"rating": "down", "label": "No — ask a person"}]}


async def rollup_db(org: str, days: int = 7) -> dict:
    from sqlalchemy import select
    from desk.policy_models import DeskSession, DeskTurn
    since = datetime.now(tz=__import__("datetime").timezone.utc) - timedelta(days=max(1, min(int(days or 7), 366)))
    async with pm.session() as s:
        ss = (await s.execute(select(DeskSession).where(DeskSession.org_id == org, DeskSession.started_at >= since))).scalars().all()
        ids = [x.id for x in ss]
        ts = (await s.execute(select(DeskTurn).where(DeskTurn.org_id == org, DeskTurn.created_at >= since))).scalars().all() if ids else []
    sess = [{"id": x.id, "outcome": x.outcome, "turns": x.turns, "answered": x.answered, "abstains": x.abstains, "ticket_id": x.ticket_id,
             "csat": x.csat, "hard_routed": x.outcome == "hard_routed"} for x in ss]
    turns = [{"session_id": t.session_id, "intent": t.intent, "hard_routed": t.hard_routed, "abstained": t.abstained, "cited": t.cited,
              "rating": t.rating, "duration_ms": t.duration_ms} for t in ts]
    out = rollup(sess, turns)
    out["since"] = since.isoformat()
    out["days"] = days
    return out
