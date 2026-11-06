"""HR Desk tools added to the ONE catalogue (tools_registry) — stream D, 2026-10-25.

  explain_my_pay_change   read   deterministic pay-stub diff P vs P-1 with the rule behind each delta
                                 (desk.payslip_diff); the model only narrates the structure
  my_data_summary         read   PIPEDA "what do you hold about me": categories, purposes, your rights,
                                 the Privacy Officer contact and the 30-day response clock (s.8(3))
  raise_privacy_grievance act    a privacy complaint with a 30-day clock; executor in actions.py (marked block)
  (request_correction)           = the existing ``update_my_contact`` tool (privacy correction request)

Imported by tools_registry.py at the bottom (same pattern as actions.py), so ``register`` exists.
"""
from __future__ import annotations

import re
import sys
from datetime import timedelta

from pydantic import BaseModel, Field

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402

import copilot as CP  # noqa: E402
from tools_registry import Ctx, register, src  # noqa: E402

from desk import engine as EN  # noqa: E402
from desk import payslip_diff as PD  # noqa: E402

GRIEVANCE_SLA_DAYS = 30        # PIPEDA s.8(3): respond to a request within thirty days


class PayChangeArgs(BaseModel):
    month: str = Field("", description="YYYY-MM (or YYYY-MM-DD pay date) of the pay stub to explain; empty = latest. Compared with the one before it.")
    line: str = Field("", max_length=40, description="optional: one line to focus on (CPP, EI, income tax, overtime, vacation pay …)")


@register("explain_my_pay_change", "Why the caller's pay changed: a line-by-line comparison of the latest pay stub with the previous one, each change tied to the "
          "payroll rule behind it (hours / overtime / stat-holiday pay, vacation payout, CPP or EI maximum reached, CPP2 starting, a new TD1, the bonus method, "
          "retroactive pay, one-off payments). "
          "Computed by the payroll engine — narrate ONLY figures present in the structure; never add or derive numbers.",
          scope="read", args=PayChangeArgs)
async def explain_my_pay_change(ctx: Ctx, a: PayChangeArgs) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    structure, err = await EN.pay_change_structure(ctx, a.month or None)
    if err:
        return {"error": err}
    out = {"structure": structure, "plain_words": PD.render_plain(structure),
           "rule": "Quote only numbers that appear in 'structure' exactly as written; say payroll will check anything marked UNEXPLAINED."}
    if a.line:
        out["focus_line"] = PD.line_for(structure, a.line) or {"note": f"{a.line} did not change between the two pay stubs."}
    out["source"] = src(f"Pay stub {structure['period']}" + (f" vs {structure['prev_period']}" if structure.get("prev_period") else ""),
                        (structure.get("links") or ["/my-pay"])[0], "payroll-service", [structure.get("run_id") or structure["period"]])
    return out


class NoArgs(BaseModel):
    pass


DATA_CATEGORIES = [
    {"category": "Identity and contact", "examples": "name, employee number, work e-mail, phone, address and postal code, date of birth, photo", "purpose": "employment record, communication"},
    {"category": "Government identifiers", "examples": "Social Insurance Number (shown masked), work permit number and expiry", "purpose": "CRA / Service Canada / Revenu Québec reporting (legal obligation), right to work"},
    {"category": "Bank details", "examples": "institution, transit, account (last 4 digits shown)", "purpose": "direct deposit of pay"},
    {"category": "Pay and tax", "examples": "salary or rate, pay stubs, TD1 claims, T4 / RL-1 slips, Records of Employment", "purpose": "payroll and source deductions (legal obligation)"},
    {"category": "Time and leave", "examples": "punches, shifts, timesheets, leave requests and balances", "purpose": "hours paid, overtime, leave administration"},
    {"category": "Employment history", "examples": "hiring, promotions, transfers, letters, exit", "purpose": "employment record"},
    {"category": "Documents you uploaded", "examples": "ID, void cheque, permits, certificates", "purpose": "verification"},
    {"category": "Helpdesk and assistant use", "examples": "tickets, HR Desk questions (identifiers masked), feedback", "purpose": "support; access logs kept at least one year"},
]
RIGHTS = ["access the personal information held about you (this summary, or a full access request)",
          "ask for a correction (say 'update my phone / address' — a privacy correction request)",
          "withdraw consent for optional uses (payroll and government reporting are required to employ and pay you)",
          "ask for information that is no longer needed to be deleted (records the law requires are kept for their period)",
          "complain to the Privacy Officer — answered within 30 days (PIPEDA s.8(3)); you may also complain to the Office of the Privacy Commissioner of Canada"]


async def _dpo(headers: dict) -> dict:
    data, _ = await CP._get(f"{settings.COMPLIANCE_URL}/compliance/privacy/settings", headers)
    d = data or {}
    contact = " · ".join(x for x in (d.get("dpo_name"), d.get("dpo_email"), d.get("dpo_phone")) if x)
    return {"dpo_name": d.get("dpo_name"), "dpo_email": d.get("dpo_email"), "dpo_phone": d.get("dpo_phone"),
            "grievance_officer": d.get("grievance_officer_name"), "grievance_officer_email": d.get("grievance_officer_email"),
            "contact": contact or "not yet published — HR sets the Privacy Officer in Privacy Centre → Contacts"}


@register("my_data_summary", "Privacy rights inside the desk (PIPEDA): what categories of personal information the company holds about the caller and why, the "
          "caller's rights (access, correction, withdrawal of consent, complaint), the Privacy Officer contact and the 30-day response clock. No raw identifiers are returned.",
          scope="read", args=NoArgs)
async def my_data_summary(ctx: Ctx, a: NoArgs) -> dict:
    if not ctx.employee_id:
        return {"error": "Your login is not linked to an employee record."}
    prof, _ = await CP._get(f"{settings.EMPLOYEE_URL}/employees/{ctx.employee_id}/profile", ctx.headers)
    e = (prof or {}).get("employee") or prof or {}
    held = [c for c in DATA_CATEGORIES]
    docs = len((prof or {}).get("documents") or [])
    return {"about": {"employee_number": e.get("employee_number"), "department": e.get("department"), "joined": e.get("hire_date"), "documents_on_file": docs},
            "categories": held, "rights": RIGHTS, "response_clock_days": GRIEVANCE_SLA_DAYS, "dpo": await _dpo(ctx.headers),
            "legal_basis": "Employment purposes with your consent (PIPEDA Principle 4.3); records the Income Tax Act, Employment Insurance Act and provincial employment standards require.",
            "source": src("Privacy notice and my profile", "/privacy", "compliance-service + employee-service", [ctx.employee_id])}


class GrievanceArgs(BaseModel):
    details: str = Field(..., min_length=10, max_length=2000, description="what the person believes is wrong with how their data is handled")


@register("raise_privacy_grievance", "Raise a privacy complaint for the caller (wrong / excessive / unexplained use of personal information). Proposes only; once confirmed "
          "it is recorded with a 30-day response clock and the Privacy Officer contact. For a simple correction use update_my_contact instead.",
          scope="act", args=GrievanceArgs)
async def raise_privacy_grievance(ctx: Ctx, a: GrievanceArgs) -> dict:
    import actions as ACT
    dpo = await _dpo(ctx.headers)
    payload = {"details": a.details.strip(), "employee_id": ctx.employee_id, "dpo_contact": dpo["contact"]}
    return ACT._propose(ctx, ACT._card("raise_privacy_grievance", "Raise a privacy complaint", ACT._me(ctx),
                                       f"Recorded for the Privacy Officer ({dpo['contact']}); you get a reference and a reply within {GRIEVANCE_SLA_DAYS} days.",
                                       payload, "/privacy"))


async def execute_privacy_grievance(h: dict, p: dict) -> tuple[dict | None, str | None]:
    """actions.py executor (runs AFTER confirmation)."""
    from shield import Shield
    from desk.policy_models import DeskPrivacyGrievance
    org = h.get("x-org-id") or ""
    if not org:
        return None, "Missing organisation."
    now = pm.now_utc()
    try:
        async with pm.session() as s:
            row = DeskPrivacyGrievance(org_id=org, user_id=h.get("x-user-id") or None, employee_id=p.get("employee_id") or None,
                                       details_shielded=Shield().protect(p.get("details") or "")[:4000], dpo_contact=(p.get("dpo_contact") or "")[:300] or None,
                                       raised_at=now, due_at=now + timedelta(days=GRIEVANCE_SLA_DAYS))
            s.add(row)
            await s.flush()
            rid = row.id
            await s.commit()
    except Exception as e:                                   # noqa: BLE001
        return None, f"Could not record the complaint ({type(e).__name__})."
    # mirror to the compliance privacy-centre request register (best effort — 'access' is the nearest request type)
    await CP._post(f"{settings.COMPLIANCE_URL}/compliance/privacy/requests", h,
                   {"request_type": "access", "details": "PRIVACY COMPLAINT (HR Desk): " + re.sub(r"\s+", " ", p.get("details") or "")[:1500],
                    "payload": {"via": "ai_desk", "grievance_id": rid, "kind": "grievance"}})
    return {"id": rid, "status": "open", "due_at": (now + timedelta(days=GRIEVANCE_SLA_DAYS)).isoformat(), "ref": rid[:8].upper()}, None
