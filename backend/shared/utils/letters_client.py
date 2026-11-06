"""Lexora HRM — letter engine client (for every service that needs a letter).

    from shared.utils.letters_client import issue_letter
    res = await issue_letter(org_id, employee_id, "relieving",
                             {"exit": {"resignation_date": "2026-10-01", "last_working_date": "2026-10-31"}},
                             actor="hr@acme.ca")
    # → {"letter_id": "...", "number": "LTR/2026/0007", "pdf_url": "/letters/<id>/pdf", "status": "issued", ...}

It calls the employee service:  POST /employees/{employee_id}/letters
    {template_key, data, effective_date?, issue}

`data` fills the template's {{fields}}: nested ({"salary": {"new_annual": 900000}}),
dotted ({"salary.new_annual": 900000}) or bare keys (→ {{extra.<key>}}). A key that
is not on the engine's allow-list is refused (422). `data["salary"]["components"]`
= {CODE: monthly amount} draws the {{salary.breakup_table}}.

Template keys seeded for every org: offer_letter, appointment_letter, ctc_annexure,
confirmation, probation_extension, salary_revision, promotion, transfer, relieving,
experience_certificate, fnf_cover, employment_verification.

`actor` is who is issuing: an email string, or {"email", "id", "role"}.
Raises LetterError (with .status and .detail) when the letter was not created.
"""
from __future__ import annotations

import httpx

from shared.utils.config import settings


class LetterError(Exception):
    def __init__(self, status: int, detail):
        self.status, self.detail = status, detail
        msg = detail.get("message") if isinstance(detail, dict) else detail
        super().__init__(str(msg or f"Letter service error {status}"))


def _headers(org_id, actor) -> dict:
    a = actor if isinstance(actor, dict) else {"email": actor}
    h = {"x-org-id": str(org_id), "x-role": str(a.get("role") or "hr_admin"),
         "x-user-email": str(a.get("email") or "system")}
    if a.get("id"):
        h["x-user-id"] = str(a["id"])
    return h


async def issue_letter(org_id, employee_id, template_key, data, actor, *, effective_date: str | None = None,
                       issue: bool = True, allow_blank: bool = False, source: str | None = None,
                       timeout: float = 20.0) -> dict:
    """Generate (and by default ISSUE) a letter for an employee. Returns
    {letter_id, number, pdf_url, status, missing_fields}."""
    body = {"template_key": template_key, "data": data or {}, "issue": issue, "allow_blank": allow_blank}
    if effective_date:
        body["effective_date"] = str(effective_date)[:10]
    if source:
        body["source"] = source
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{settings.EMPLOYEE_URL}/employees/{employee_id}/letters", json=body,
                             headers=_headers(org_id, actor))
    except httpx.HTTPError as ex:
        raise LetterError(503, f"The letter service could not be reached ({type(ex).__name__})")
    if r.status_code not in (200, 201):
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        raise LetterError(r.status_code, detail)
    return r.json()


async def issue_recipient_letter(org_id, recipient: dict, template_key, data, actor, *,
                                 effective_date: str | None = None, issue: bool = True,
                                 source: str | None = None, timeout: float = 20.0) -> dict:
    """A letter for someone who is NOT an employee yet (a candidate's offer letter).

    recipient = {kind: "candidate", ref: <offer id>, first_name, last_name, email,
                 address, country, designation, department, location, grade,
                 manager_name, joining_date, employment_type, annual_ctc}
    Same numbering, immutability and template allow-list as issue_letter; only the
    pre-joining templates (offer_letter, ctc_annexure) are accepted. The letter is
    re-linked to the employee (POST /letters/recipient/relink) once they are activated."""
    body = {"template_key": template_key, "data": data or {}, "issue": issue, "recipient": recipient}
    if effective_date:
        body["effective_date"] = str(effective_date)[:10]
    if source:
        body["source"] = source
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{settings.EMPLOYEE_URL}/letters/recipient", json=body, headers=_headers(org_id, actor))
    except httpx.HTTPError as ex:
        raise LetterError(503, f"The letter service could not be reached ({type(ex).__name__})")
    if r.status_code not in (200, 201):
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        raise LetterError(r.status_code, detail)
    return r.json()


async def list_letters(org_id, employee_id, actor) -> list:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{settings.EMPLOYEE_URL}/employees/{employee_id}/letters", headers=_headers(org_id, actor))
    return r.json() if r.status_code == 200 else []
