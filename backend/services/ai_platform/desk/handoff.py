"""Hand-off to a person — triggers, ticket creation, IC referral.

Triggers (any one is enough; evaluated by code, never by the model):
  explicit_request   "talk to a human / HR", "raise a ticket"
  unresolved_x2      two unresolved attempts in the session (repeat / thumbs-down / abstain)
  abstain_x2         two abstains in the session
  hard_route:<class> any sensitive class from desk.router

A hand-off creates ONE helpdesk ticket in the matching category (ops /engage/helpdesk/tickets, as the
caller, so RBAC + the category's SLA hold) with the transcript, the citations, the confidence and the
reason code in the body. A sexual-harassment report never creates a ticket — a sealed referral row only.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402

import copilot as CP  # noqa: E402

from desk import router as RT  # noqa: E402

REASONS = ("explicit_request", "unresolved_x2", "abstain_x2", "payroll_unreconciled")


@dataclass
class Trigger:
    fire: bool
    reason: str | None = None
    category: str = "policy"


def decide(*, wants_human: bool, unresolved: int, abstains: int, hard_class: str | None, needs_payroll_review: bool = False,
           cfg: dict | None = None, intent: str = "policy") -> Trigger:
    cfg = cfg or {}
    if hard_class:
        cat = RT.TICKET_CATEGORY.get(hard_class)
        return Trigger(fire=cat is not None, reason=f"hard_route:{hard_class}", category=cat or "grievance")
    if wants_human:
        return Trigger(fire=True, reason="explicit_request", category=_category_for_intent(intent))
    if needs_payroll_review:
        return Trigger(fire=True, reason="payroll_unreconciled", category="payroll")
    if unresolved >= int(cfg.get("handoff_unresolved_attempts") or 2):
        return Trigger(fire=True, reason="unresolved_x2", category=_category_for_intent(intent))
    if abstains >= int(cfg.get("handoff_abstains") or 2):
        return Trigger(fire=True, reason="abstain_x2", category=_category_for_intent(intent))
    return Trigger(fire=False)


def _category_for_intent(intent: str) -> str:
    i = (intent or "").lower()
    if i.startswith("pay"):
        return "payroll"
    if i.startswith("leave"):
        return "leave"
    if i.startswith("doc") or i.startswith("letter"):
        return "documents"
    return "policy"


def ticket_body(*, reason: str, transcript: list[dict], citations: list[dict], confidence: float | None, session_id: str,
                hard_class: str | None = None, extra: str = "") -> str:
    lines = [f"Opened by HR Desk (AI assistant) — reason: {reason}", f"Session: {session_id}"]
    if hard_class:
        lines.append(f"Routed class: {hard_class} (the assistant gave the fixed message only; no AI answer was generated)")
    if confidence is not None:
        lines.append(f"Assistant confidence on the last answer: {confidence:.2f}")
    if citations:
        lines.append("Clauses the assistant used: " + "; ".join(c.get("label") or str(c) for c in citations[:6]))
    if extra:
        lines.append(extra)
    lines.append("")
    lines.append("Transcript:")
    for t in transcript[-12:]:
        who = "Employee" if t.get("role") == "user" else "HR Desk"
        lines.append(f"{who}: {str(t.get('content') or '').strip()[:1500]}")
    return "\n".join(lines)[:7900]


def ticket_subject(reason: str, question: str, hard_class: str | None) -> str:
    if hard_class:
        label = {"mental_health": "Confidential: wellbeing — immediate contact requested", "harassment": "Confidential: harassment / discrimination",
                 "medical": "Confidential: medical / accommodation", "grievance": "Confidential: grievance", "disciplinary": "Confidential: disciplinary / termination",
                 "whistleblowing": "Confidential: whistle-blower report", "legal": "Confidential: legal matter", "salary_dispute": "Pay query from HR Desk"}
        return label.get(hard_class, "Confidential: routed by HR Desk")[:200]
    q = re.sub(r"\s+", " ", question or "").strip()
    return (f"HR Desk hand-off ({reason}): {q[:150]}" if q else f"HR Desk hand-off ({reason})")[:200]


PRIORITY = {"mental_health": "urgent", "sexual_harassment": "urgent", "harassment": "high", "whistleblowing": "high", "legal": "high", "medical": "high"}


async def open_ticket(headers: dict, *, category: str, subject: str, body: str, priority: str = "normal") -> tuple[dict | None, str | None]:
    """POST the ticket as the caller. Falls back to category 'other' if the org removed the category."""
    payload = {"category": category, "subject": subject, "body": body, "priority": priority, "source": "web"}
    data, err = await CP._post(f"{settings.OPS_URL}/engage/helpdesk/tickets", headers, payload)
    if err and category != "other" and "category" in str(err).lower():
        data, err = await CP._post(f"{settings.OPS_URL}/engage/helpdesk/tickets", headers, {**payload, "category": "other"})
    return data, err


async def create_ic_referral(org: str, employee_id: str | None, user_id: str | None, channel: str) -> str | None:
    """Sealed row: who + when + channel. Nothing else, ever."""
    from shared.utils import platform_models as pm
    from desk.policy_models import DeskIcReferral
    try:
        async with pm.session() as s:
            row = DeskIcReferral(org_id=org, employee_id=employee_id or None, user_id=user_id or None, channel=channel or "chat")
            s.add(row)
            await s.flush()
            rid = row.id
            await s.commit()
        return rid
    except Exception:                                        # noqa: BLE001 — the fixed message still goes out
        return None
