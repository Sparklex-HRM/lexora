"""Exit Agent executors — reached only through the approval rails (shadow: never).

  exit.chase                   tier 0  deduplicated task to the clearance owner / HR / payroll
  exit.run_final_pay           tier 2  two DIFFERENT approvers (it runs pay): GET the exit case's own pre-fill
                                       (/exit/cases/{id}/settlement, kind canada_final) → POST /payroll/final-pay with that body.
                                       The payroll service pays out the vacation balance, applies the clearance recoveries,
                                       runs the off-cycle final cheque and returns the ROE draft. The agent passes the case's
                                       pre-fill through unchanged and NEVER computes, edits or rounds an amount.
  exit.draft_exit_letters      tier 1  separation + employment reference letters as DRAFTS (issue: false); HR issues them
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

AGENT = "exit"
TOOLS = {"exit.chase": 0, "exit.run_final_pay": 2, "exit.draft_exit_letters": 1}
TIMEOUT = 90.0

register_chase_executor(AGENT)


def _err(r) -> str:
    try:
        return str(r.json().get("detail"))[:280]
    except Exception:                                            # noqa: BLE001
        return r.text[:280]


@register_executor("exit.run_final_pay")
async def run_final_pay(org: str, proposal: dict, args: dict) -> dict:
    cid = str(args["case_id"])
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{settings.EMPLOYEE_URL}/exit/cases/{cid}/settlement", headers=internal_headers(org, "ai_platform", role="hr_admin"))
        if r.status_code != 200:
            raise RuntimeError(f"exit case settlement view refused (HTTP {r.status_code}): {_err(r)}")
        view = r.json()
        pre = (view.get("prefill") or {})
        if pre.get("kind") != "canada_final" or not pre.get("body"):
            raise RuntimeError(view.get("blocked_reason") or "the exit case has no final-pay pre-fill yet (last working day missing?)")
        body = dict(pre["body"])                                   # the case's own pre-fill — passed through unchanged
        if "year" not in body and body.get("last_day"):
            body["year"] = int(str(body["last_day"])[:4])           # the tax year the final cheque falls in (not an amount)
        r2 = await c.post(f"{settings.PAYROLL_URL}/payroll/final-pay", headers=internal_headers(org, "ai_platform", role="payroll_admin"), json=body)
        if r2.status_code not in (200, 201):
            raise RuntimeError(f"final-pay run refused (HTTP {r2.status_code}): {_err(r2)}")
        out = r2.json() if r2.content else {}
    final = out.get("final_pay") or {}
    await LOG.append_event(org, event_type="final_pay.run", agent_name=AGENT, run_id=proposal.get("run_id"), approval_id=proposal.get("id"),
                           tool_name="exit.run_final_pay", approver_user_id=proposal.get("approver_user_id"),
                           second_approver_user_id=proposal.get("second_approver_user_id"),
                           tool_input={"case_id": cid, "pay_run_id": final.get("id"), "computed_by": "payroll final-pay run"}, risk_tier=2,
                           policy_decision="allow",
                           agent_reason_text="Two approvers confirmed; the payroll service computed and ran the final pay from the exit case's pre-fill.")
    return {"pay_run_id": final.get("id"), "net_pay_engine": final.get("net_pay"), "vacation_paid_engine": out.get("vacation_paid"),
            "roe_ready": bool(out.get("roe")), "computed_by": "payroll service (/payroll/final-pay)",
            "next": "Payroll → ROE → review the draft and file it on ROE Web within the 5-day clock",
            "approvers": [proposal.get("approver_user_id"), proposal.get("second_approver_user_id")]}


@register_executor("exit.draft_exit_letters")
async def draft_exit_letters(org: str, proposal: dict, args: dict) -> dict:
    eid = str(args["employee_id"])
    out = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for key in args.get("keys") or ["relieving", "experience_certificate"]:
            if key not in ("relieving", "experience_certificate"):
                continue
            r = await c.post(f"{settings.EMPLOYEE_URL}/employees/{eid}/letters", headers=internal_headers(org, "ai_platform", role="hr_admin"),
                             json={"template_key": key, "data": {}, "issue": False})
            out.append({"key": key, "ok": r.status_code in (200, 201), "letter_id": (r.json().get("letter_id") if r.status_code in (200, 201) and r.content else None),
                        "error": None if r.status_code in (200, 201) else _err(r)})
    if not any(x["ok"] for x in out):
        raise RuntimeError("letters service refused every draft: " + "; ".join(str(x["error"]) for x in out))
    await LOG.append_event(org, event_type="letter.drafted", agent_name=AGENT, run_id=proposal.get("run_id"), approval_id=proposal.get("id"),
                           tool_name="exit.draft_exit_letters", approver_user_id=proposal.get("approver_user_id"), tool_input={"employee_id": eid, "letters": out},
                           risk_tier=1, policy_decision="allow", agent_reason_text="Drafts only — HR issues them.")
    return {"letters": out, "next": "Exits → the case → Letters → Issue (the agent never issues)"}
