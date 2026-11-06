"""Onboarding Agent executors — reached only through the foundation's approval rails (shadow mode: never).

  onboarding.chase                     tier 0  open the deduplicated task for the joiner / HR / IT / payroll (lifecycle_common)
  onboarding.draft_appointment_letter  tier 1  HR approves → the letters service stores a DRAFT (no number, DRAFT watermark,
                                               ``issue: false``); HR issues it from Letters. The agent never issues a letter.
"""
from __future__ import annotations

import sys

import httpx

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.internal import internal_headers  # noqa: E402

import agentic_log as LOG  # noqa: E402
from agentic_registry import register_executor  # noqa: E402
from agents.lifecycle_common import register_chase_executor  # noqa: E402

AGENT = "onboarding"
TOOLS = {"onboarding.chase": 0, "onboarding.draft_appointment_letter": 1}
TIMEOUT = 60.0

register_chase_executor(AGENT)


async def _post(url: str, org: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(url, headers=internal_headers(org, "ai_platform", role="hr_admin"), json=body)
    if r.status_code not in (200, 201):
        try:
            detail = r.json().get("detail")
        except Exception:                                        # noqa: BLE001
            detail = r.text[:300]
        raise RuntimeError(f"letters service refused (HTTP {r.status_code}): {str(detail)[:280]}")
    return r.json() if r.content else {}


@register_executor("onboarding.draft_appointment_letter")
async def draft_appointment_letter(org: str, proposal: dict, args: dict) -> dict:
    eid = str(args["employee_id"])
    data = await _post(f"{settings.EMPLOYEE_URL}/employees/{eid}/letters", org,
                       {"template_key": args.get("template_key") or "appointment_letter", "data": {}, "issue": False})
    await LOG.append_event(org, event_type="letter.drafted", agent_name=AGENT, run_id=proposal.get("run_id"), approval_id=proposal.get("id"),
                           tool_name="onboarding.draft_appointment_letter", approver_user_id=proposal.get("approver_user_id"),
                           tool_input={"employee_id": eid, "template_key": "appointment_letter", "letter_id": data.get("letter_id")}, risk_tier=1,
                           policy_decision="allow", agent_reason_text="Draft only — HR reviews and issues it from Letters.")
    return {"letter_id": data.get("letter_id"), "status": data.get("status") or "draft", "missing_fields": data.get("missing_fields") or [],
            "next": "Employees → the joiner → Letters → review → Issue (the agent never issues)"}
