"""Weekly "top questions" report of the HR Desk (phase 2, P2-C) — generated from desk.metrics, sent to HR
through the ordinary notify (in-app + e-mail; honest when SMTP is blank — never crashes), recorded as one
``desk.weekly_report`` event in the agentic action log (the Monday tick uses it to send once per ISO week).
"""
from __future__ import annotations

import sys

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from desk import agent as AG  # noqa: E402
from desk import metrics as MX  # noqa: E402

REPORT_ROLES = ["hr_admin", "org_admin"]
LINK = "/ai/agents/hr-desk"


def _pct(v) -> str:
    return "—" if v is None else f"{round(float(v) * 100)}%"


def _label(intent: str) -> str:
    i = intent or "policy"
    if i.startswith("policy:"):
        return "Policy — " + i.split(":", 1)[1].replace("_", " ")
    if i.startswith("tools:"):
        return "Records — " + i.split(":", 1)[1].replace("_", " ")
    return {"pay_change": "Why did my pay change", "pay": "Payslip / pay", "leave": "Leave", "policy": "Policy (general)",
            "sexual_harassment": "Sexual harassment (sealed referral — count only)", "harassment": "Harassment (routed)", "medical": "Medical (routed)",
            "mental_health": "Wellbeing (routed, urgent)", "grievance": "Grievance (routed)", "disciplinary": "Disciplinary (routed)",
            "whistleblowing": "Whistle-blowing (routed)", "legal": "Legal (routed)", "salary_dispute": "Salary dispute (routed)",
            "other_person_data": "Another person's data (refused)"}.get(i, i.replace("_", " "))


def render(org_name: str, m: dict, value: dict, knowledge: dict, days: int) -> tuple[str, str]:
    """(subject, plain-text body). Every number comes with its definition — none is invented."""
    subject = f"HR Desk — weekly report, {org_name or 'your company'} (last {days} days)"
    lines = [f"HR Desk — what employees asked in the last {days} days", "",
             f"Sessions: {m.get('sessions', 0)} ({m.get('candidate_sessions', 0)} candidates for an AI answer; {m.get('hard_routed_sessions', 0)} routed to a person by rule)",
             f"Self-solved (deflection): {_pct(m.get('deflection_rate'))} — {m.get('self_solved', 0)} sessions",
             f"Abstain rate: {_pct(m.get('abstain_rate'))} — {m.get('abstained_turns', 0)} of {m.get('answered_turns', 0) + m.get('abstained_turns', 0)} turns",
             f"Hand-off rate: {_pct(m.get('handoff_rate'))} — {m.get('handoffs', 0)} tickets with transcript",
             f"CSAT: {_pct(m.get('csat'))} on {m.get('csat_rated', 0)} rated answers (coverage {_pct(m.get('csat_coverage'))})",
             f"Median time to answer: {('%.1f s' % (m['median_ms'] / 1000)) if m.get('median_ms') is not None else '—'}", "",
             "Top questions (by intent):"]
    for t in (m.get("top_intents") or [])[:12]:
        lines.append(f"  {t.get('count', 0):>4}  {_label(t.get('intent'))}")
    if not m.get("top_intents"):
        lines.append("  (no questions in this window)")
    lines += ["", "Value (agent ledger, net of revoked rows):",
              f"  queries resolved: {value.get('query_resolved', 0):g} (confirmed by the employee: {value.get('query_resolved_confirmed', 0)})",
              f"  hand-offs created: {value.get('handoff_created', 0):g}   sealed IC referrals: {value.get('ic_referral', 0):g} (count only)", "",
              "Knowledge readiness:",
              f"  approved clauses: {knowledge.get('approved', 0)}   drafts awaiting SME sign-off: {knowledge.get('draft', 0)}   "
              f"avg readiness (approved): {knowledge.get('avg_ready_approved') if knowledge.get('avg_ready_approved') is not None else '—'}/100"]
    if knowledge.get("abstained_questions"):
        lines += ["", "Questions the desk could not answer from policy (write a clause):"]
        for q in knowledge["abstained_questions"][:8]:
            lines.append(f"  • {q.get('question')}")
    lines += ["", "Definitions:"]
    for k, v in (m.get("definitions") or {}).items():
        lines.append(f"  {k}: {v}")
    lines += ["", f"Open the dashboard: {LINK}"]
    return subject, "\n".join(lines)


async def build(org: str, days: int = 7) -> dict:
    from shared.utils import directory
    from desk import routers as DR
    m = await MX.rollup_db(org, days)
    value = await AG.value_counts(org, days)
    knowledge = await DR.knowledge_summary(org)
    knowledge["abstained_questions"] = await DR.abstained_questions(org, days, limit=8)
    org_name = await directory.org_name(org)
    subject, body = render(org_name, m, value, knowledge, days)
    return {"subject": subject, "body": body, "metrics": m, "value": value, "knowledge": knowledge, "org_name": org_name, "days": days}


async def send_weekly(org: str, *, days: int = 7, requested_by: str | None = None) -> dict:
    """Generate + notify HR (in-app + e-mail via notify; SMTP blank = in-app only, delivery rows say so) + log event."""
    rep = await build(org, days)
    try:
        from shared.utils.notify import notify
        await notify(org, roles=REPORT_ROLES, title=rep["subject"], body=rep["body"], link=LINK, kind="hr_desk_report", email=True)
        notified = True
    except Exception:                                        # noqa: BLE001
        notified = False
    try:
        from shared.utils.email import smtp_configured
        smtp = bool(smtp_configured())
    except Exception:                                        # noqa: BLE001
        smtp = False
    try:
        from agentic_log import append_event
        from agentic_registry import AGENT_VERSION
        await append_event(org, event_type="desk.weekly_report", agent_name=AG.AGENT, agent_version=AGENT_VERSION, requested_by_user_id=requested_by,
                           run_as_identity=f"agent:{AG.AGENT}", tool_name="weekly_report", tool_version="1",
                           tool_input={"days": days, "roles": REPORT_ROLES, "smtp": smtp}, tool_output={"subject": rep["subject"], "body_len": len(rep["body"])},
                           risk_tier=0, policy_decision="allow", agent_reason_text=f"weekly report: {rep['metrics'].get('sessions', 0)} sessions")
    except Exception:                                        # noqa: BLE001
        pass
    return {"ok": True, "notified": notified, "roles": REPORT_ROLES, "smtp_configured": smtp,
            "note": "Sent in-app to HR admins and org admins" + (" and by e-mail." if smtp else "; e-mail is not configured on this server (SMTP blank), so in-app only."),
            "subject": rep["subject"], "report": rep["body"], "at": pm.iso(pm.now_utc())}
