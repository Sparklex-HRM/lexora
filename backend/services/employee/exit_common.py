"""Exit process — shared plumbing for exit_routers.py / exit_people.py (Stream C).

Identity from the gateway headers, row → dict mappers, the conditional status update
(`WHERE status = <what was read>`), the timeline writer, and the guarded calls into the
other streams' helpers (A: logins · B: letters · E: notifications). A helper that is not
on disk degrades with a clear message instead of failing the exit.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select, text, update

sys.path.append("/app")

import exit_logic as L  # noqa: E402
from exit_models import (ExitCase, ExitCaseEvent, ExitClearanceItem, ExitNoticePolicy, ExitSettings,  # noqa: E402
                         EmployeeAsset, EmploymentSpell)
from models import Employee  # noqa: E402


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    """The org's local business date (default America/Toronto; sync — callers are many) — never the UTC date."""
    from shared.utils.org_time import local_today
    return local_today()


def org_of(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def actor_of(request: Request) -> dict:
    return {"role": (request.headers.get("x-role") or "").lower(),
            "employee_id": request.headers.get("x-employee-id") or "",
            "user_id": request.headers.get("x-user-id") or "",
            "email": request.headers.get("x-user-email") or request.headers.get("x-user-id") or "unknown"}


def is_hr(actor: dict) -> bool:
    return actor.get("role") in L.HR_ROLES


def is_payroll(actor: dict) -> bool:
    return actor.get("role") in L.PAYROLL_ROLES


def require_hr(actor: dict, what: str = "do this"):
    if not is_hr(actor):
        raise HTTPException(403, f"Only HR can {what}")


def fwd_headers(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id") if request.headers.get(k)}


def system_headers(org: str) -> dict:
    """Headers for a call made by the daily tick (no user request) — the internal
    service identity (shared/utils/internal.py), acting as HR for this org."""
    from shared.utils.internal import internal_headers
    return internal_headers(org, "exit-tick", role="hr_admin")


def iso(v):
    return v.isoformat() if v is not None else None


def parse_date(v, field: str = "date") -> date | None:
    if v in (None, ""):
        return None
    d = L.to_date(v)
    if not d:
        raise HTTPException(422, f"{field} must be a date (YYYY-MM-DD)")
    return d


# ── mappers ───────────────────────────────────────────────────────────────────
def case_dict(c: ExitCase) -> dict:
    """Plain dict for exit_logic (ids as strings, dates as dates)."""
    return {"id": str(c.id), "case_number": c.case_number, "employee_id": str(c.employee_id),
            "manager_id": str(c.manager_id) if c.manager_id else None, "manager_name": c.manager_name,
            "status": c.status, "held_from_status": c.held_from_status, "exit_type": c.exit_type,
            "initiated_by": c.initiated_by, "lwd": c.lwd, "accepted_on": c.accepted_on,
            "notice_days": c.notice_days, "notice_served_days": c.notice_served_days,
            "notice_shortfall_days": c.notice_shortfall_days or 0, "notice_treatment": c.notice_treatment,
            "notice_treatment_reason": c.notice_treatment_reason, "separated_at": c.separated_at,
            "withdraw_requested": bool(c.withdraw_requested), "letters": c.letters or [],
            "roe_reason_code": c.roe_reason_code, "department": c.department,
            "reason_category": c.reason_category}


def case_out(c: ExitCase, actor: dict | None = None) -> dict:
    d = case_dict(c)
    out = {
        "id": str(c.id), "case_number": c.case_number, "employee_id": str(c.employee_id),
        "employee_name": c.employee_name, "employee_number": c.employee_number, "department": c.department,
        "job_title": c.job_title, "manager_name": c.manager_name, "country": c.country,
        "exit_type": c.exit_type, "exit_type_label": L.EXIT_TYPES.get(c.exit_type, c.exit_type),
        "initiated_by": c.initiated_by, "reason_category": c.reason_category, "reason": c.reason, "note": c.note,
        "documents": c.documents or [],
        "status": c.status, "status_label": L.STATE_LABELS.get(c.status, c.status), "pending_with": L.pending_with(d),
        "submitted_on": iso(c.submitted_on), "requested_lwd": iso(c.requested_lwd), "accepted_on": iso(c.accepted_on),
        "notice_days": c.notice_days, "notice_stage": c.notice_stage, "notice_source": c.notice_source,
        "expected_lwd": iso(c.expected_lwd), "lwd": iso(c.lwd), "lwd_reason": c.lwd_reason,
        "notice_served_days": c.notice_served_days, "notice_shortfall_days": c.notice_shortfall_days or 0,
        "notice_treatment": c.notice_treatment,
        "notice_treatment_label": L.NOTICE_TREATMENTS.get(c.notice_treatment or "", None),
        "notice_treatment_reason": c.notice_treatment_reason, "notice_decided_by": c.notice_decided_by,
        "garden_leave": bool(c.garden_leave), "leave_during_notice_days": float(c.leave_during_notice_days or 0),
        "leave_extends_lwd": bool(c.leave_extends_lwd),
        "clearance_override": c.clearance_override,
        "settlement_kind": c.settlement_kind, "settlement_ref": c.settlement_ref,
        "settlement_status": c.settlement_status, "settlement_paid_at": iso(c.settlement_paid_at),
        "settlement_net": float(c.settlement_net) if c.settlement_net is not None else None,
        "roe_reason_code": c.roe_reason_code,
        "separated": bool(c.separated_at), "separated_at": iso(c.separated_at), "separation_result": c.separation_result,
        "withdraw_requested": bool(c.withdraw_requested), "withdraw_reason": c.withdraw_reason,
        "letters": c.letters or [], "rehire_eligible": c.rehire_eligible, "rehire_note": c.rehire_note,
        "created_by": c.created_by, "created_at": iso(c.created_at), "closed_at": iso(c.closed_at),
    }
    if actor is not None:
        out["actions"] = L.available_actions(d, actor)
        out["my_relation"] = sorted(L.relation(actor, d))
    return out


def item_out(i: ExitClearanceItem) -> dict:
    return {"id": str(i.id), "department": i.department, "item": i.item, "owner_role": i.owner_role,
            "owner_user_id": i.owner_user_id, "owner_employee_id": str(i.owner_employee_id) if i.owner_employee_id else None,
            "owner_name": i.owner_name, "status": i.status, "recovery_amount": float(i.recovery_amount or 0),
            "comment": i.comment, "acted_by": i.acted_by, "acted_at": iso(i.acted_at), "sort": i.sort}


def event_out(e: ExitCaseEvent) -> dict:
    return {"id": str(e.id), "at": iso(e.at), "actor": e.actor, "actor_role": e.actor_role, "action": e.action,
            "from_status": e.from_status, "to_status": e.to_status, "note": e.note,
            "from_label": L.STATE_LABELS.get(e.from_status or "", None), "to_label": L.STATE_LABELS.get(e.to_status or "", None)}


def asset_out(a: EmployeeAsset) -> dict:
    return {"id": str(a.id), "employee_id": str(a.employee_id), "item": a.item, "tag": a.tag,
            "issued_on": iso(a.issued_on), "returned_on": iso(a.returned_on), "condition": a.condition,
            "note": a.note, "returned": a.returned_on is not None}


def emp_brief(e: Employee) -> dict:
    return {"id": str(e.id), "first_name": e.first_name, "last_name": e.last_name,
            "name": f"{e.first_name} {e.last_name}".strip(), "employee_number": e.employee_number,
            "manager_id": str(e.manager_id) if e.manager_id else None, "manager_name": e.manager_name,
            "department": e.department, "job_title": e.job_title, "employment_type": e.employment_type,
            "probation_end_date": e.probation_end_date, "hire_date": e.hire_date,
            "termination_date": e.termination_date, "status": e.status,
            "country": (e.address_country or "CA").upper()}


# ── loaders ───────────────────────────────────────────────────────────────────
async def load_employee(db, org: str, employee_id: str) -> Employee:
    try:
        uuid.UUID(str(employee_id))
    except ValueError:
        raise HTTPException(404, "Employee not found")
    e = (await db.execute(select(Employee).where(Employee.id == employee_id, Employee.org_id == org))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    return e


async def load_case(db, org: str, case_id: str) -> ExitCase:
    try:
        uuid.UUID(str(case_id))
    except ValueError:
        raise HTTPException(404, "Exit case not found")
    # populate_existing: status changes are Core UPDATEs — always re-read the row, never a cached object
    c = (await db.execute(select(ExitCase).where(ExitCase.id == case_id, ExitCase.org_id == org)
                          .execution_options(populate_existing=True))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Exit case not found")
    return c


async def load_items(db, case: ExitCase) -> list[ExitClearanceItem]:
    return list((await db.execute(select(ExitClearanceItem).where(
        ExitClearanceItem.case_id == case.id, ExitClearanceItem.org_id == case.org_id)
        .order_by(ExitClearanceItem.sort, ExitClearanceItem.department)
        .execution_options(populate_existing=True))).scalars().all())


def settings_out(s: ExitSettings | None) -> dict:
    d = dict(L.DEFAULT_SETTINGS)
    if s:
        d.update({"default_notice_days": s.default_notice_days, "probation_notice_days": s.probation_notice_days,
                  "leave_extends_lwd": bool(s.leave_extends_lwd), "rehire_number_policy": s.rehire_number_policy or "keep",
                  "hold_letters_until_settled": bool(s.hold_letters_until_settled)})
    d["interview_template"] = (s.interview_template if s and s.interview_template else None) or L.default_interview_template()
    d["clearance_template"] = (s.clearance_template if s and s.clearance_template else None) or L.default_clearance_template()
    return d


async def get_settings_row(db, org: str, create: bool = False) -> ExitSettings | None:
    s = (await db.execute(select(ExitSettings).where(ExitSettings.org_id == org))).scalar_one_or_none()
    if not s and create:
        await db.execute(text("INSERT INTO exit_settings (org_id, default_notice_days, probation_notice_days, "
                              "leave_extends_lwd, rehire_number_policy, hold_letters_until_settled, case_seq, updated_at) "
                              "VALUES (:o, 30, 15, FALSE, 'keep', TRUE, 0, now()) ON CONFLICT (org_id) DO NOTHING"),
                         {"o": uuid.UUID(str(org))})
        await db.flush()
        s = (await db.execute(select(ExitSettings).where(ExitSettings.org_id == org))).scalar_one_or_none()
    return s


async def load_policies(db, org: str) -> list[dict]:
    rows = (await db.execute(select(ExitNoticePolicy).where(ExitNoticePolicy.org_id == org)
                             .order_by(ExitNoticePolicy.created_at))).scalars().all()
    return [{"id": str(r.id), "grade": r.grade, "employment_type": r.employment_type, "stage": r.stage,
             "notice_days": r.notice_days, "active": bool(r.active)} for r in rows]


async def next_case_number(db, org: str) -> str:
    await get_settings_row(db, org, create=True)
    seq = (await db.execute(text("UPDATE exit_settings SET case_seq = case_seq + 1 WHERE org_id = :o RETURNING case_seq"),
                            {"o": uuid.UUID(str(org))})).scalar_one()
    return f"EX-{int(seq):04d}"


# ── writes ────────────────────────────────────────────────────────────────────
async def move(db, case: ExitCase, read_status: str, **values) -> None:
    """UPDATE … WHERE status = <what was read>. Two people cannot both act: the second
    one updates zero rows and gets a 409."""
    res = await db.execute(update(ExitCase).where(
        ExitCase.id == case.id, ExitCase.org_id == case.org_id, ExitCase.status == read_status)
        .values(updated_at=now(), **values))
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Someone else just changed this case. Refresh and try again.")


def add_event(db, case: ExitCase, actor: dict, action: str, *, frm: str | None = None, to: str | None = None,
              note: str | None = None, hr_only: bool = False) -> None:
    db.add(ExitCaseEvent(org_id=case.org_id, case_id=case.id, actor=actor.get("email"), actor_role=actor.get("role"),
                         action=action, from_status=frm, to_status=to, note=(note or None), hr_only=hr_only))


# ── other streams' helpers (guarded) ──────────────────────────────────────────
def helpers_present() -> dict:
    out = {}
    for name, mod, attr in (("logins", "shared.utils.identity", "disable_logins_for_employee"),
                            ("letters", "shared.utils.letters_client", "issue_letter"),
                            ("notify", "shared.utils.notify", "notify")):
        try:
            m = __import__(mod, fromlist=[attr])
            out[name] = callable(getattr(m, attr, None))
        except Exception:  # noqa: BLE001
            out[name] = False
    return out


async def disable_logins(org: str, employee_id: str, reason: str) -> dict:
    try:
        from shared.utils.identity import disable_logins_for_employee  # Stream A
    except Exception:  # noqa: BLE001
        return {"ok": False, "message": "Login switch-off is not available yet – disable the login by hand in Users."}
    try:
        r = await disable_logins_for_employee(org, employee_id, reason)
        return {"ok": True, **(r if isinstance(r, dict) else {"result": r})}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "message": f"Logins could not be switched off ({type(ex).__name__}) – do it by hand in Users."}


async def notify(org: str, *, title: str, body: str = "", link: str = "", roles: list | None = None,
                 employee_ids: list | None = None, user_ids: list | None = None, kind: str = "exit") -> None:
    """Best effort — never raises."""
    try:
        from shared.utils.notify import notify as _notify  # Stream E
        await _notify(str(org), user_ids=user_ids, roles=roles, employee_ids=employee_ids,
                      title=title, body=body, link=link, kind=kind)
    except Exception:  # noqa: BLE001
        return


# B's seeded template keys. `experience` (the published name) maps to `experience_certificate`.
LETTER_TEMPLATE_KEYS = {"relieving": ("relieving",), "experience": ("experience_certificate", "experience"),
                        "fnf_cover": ("fnf_cover",), "resignation_acceptance": ("resignation_acceptance",)}


async def issue_letter(org: str, employee_id: str, key: str, data: dict, actor: dict) -> dict:
    """Issue through Stream B's letter engine. → {ok, ...} — never raises."""
    try:
        from shared.utils.letters_client import issue_letter as _issue  # Stream B
    except Exception:  # noqa: BLE001
        return {"ok": False, "message": "The letter engine is not available yet – the letter was not issued."}
    last = None
    for tkey in LETTER_TEMPLATE_KEYS.get(key, (key,)):
        try:
            r = await _issue(org, employee_id, tkey, data, {"email": actor.get("email"), "id": actor.get("user_id"),
                                                            "role": actor.get("role") or "hr_admin"},
                             allow_blank=True, source="exit_case")
            return {"ok": True, "template_key": tkey, **(r if isinstance(r, dict) else {})}
        except Exception as ex:  # noqa: BLE001
            last = ex
    status = getattr(last, "status", None)
    if status in (404, 422) and key == "resignation_acceptance":
        return {"ok": False, "message": "There is no 'resignation_acceptance' letter template yet. "
                                        "Add one in Letters → Templates with that key, then issue again."}
    return {"ok": False, "message": f"The letter was not issued: {last}"}
