"""Approvals inbox — the parts that are not HTTP.

1. BUILT-IN SOURCES. Leave requests, timesheets and attendance fixes existed before
   the registry and their services do not call ``open_item``. So the inbox mirrors
   them: every pending row becomes a registry item (source = "sync") and a mirrored
   item whose source row is no longer pending is closed with the source's outcome.
   The owning services stay the system of record; approving from the inbox calls
   their existing approve / reject routes.
2. Reminder arithmetic for items that have waited too long.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, text

from shared.utils import approvals_client as ac
from shared.utils import directory
from shared.utils import platform_models as pm
from shared.utils.platform_models import ApprovalItem

log = logging.getLogger("lexora.approvals.inbox")

# The roles the three owning services accept (plus the employee's manager).
BUILTIN_ROLES = ["org_admin", "hr_admin", "hr_manager", "hr"]
BUILTIN_KINDS = ("leave", "timesheet", "attendance_fix")
_REASON = {"missed_punch": "Missed punch", "wrong_time": "Wrong time", "forgot": "Forgot to punch", "other": "Other"}


def _d(v) -> str:
    """A date as "12 Oct 2026"."""
    try:
        return f"{v.day} {v.strftime('%b %Y')}"
    except Exception:                                        # noqa: BLE001
        return str(v or "")


def _num(v) -> str:
    return f"{float(v or 0):g}"


async def _query(sql: str, params: dict) -> list[dict]:
    """One read in its own session; a missing table (module not installed) is just empty."""
    try:
        async with pm.session() as s:
            return [dict(r) for r in (await s.execute(text(sql), params)).mappings().all()]
    except Exception as e:                                   # noqa: BLE001
        log.info("built-in source not readable: %s", str(e).splitlines()[0][:160])
        return []


async def fetch_builtin_pending(org_id: str) -> list[dict]:
    """Everything pending in leave / timesheets / attendance fixes, as registry rows."""
    p = {"org": str(org_id)}
    out: list[dict] = []
    for r in await _query(
            "SELECT id, employee_id, employee_name, leave_type_name, leave_type_code, start_date, end_date, "
            "days, half_day, reason, created_at FROM leave_requests "
            "WHERE org_id = CAST(:org AS uuid) AND status = 'pending'", p):
        span = _d(r["start_date"]) if r["start_date"] == r["end_date"] else f"{_d(r['start_date'])} to {_d(r['end_date'])}"
        lines = [f"{r['leave_type_name'] or r['leave_type_code'] or 'Leave'} · {span} · {_num(r['days'])} day(s)"]
        if r["reason"]:
            lines.append(f"Reason: {r['reason']}")
        out.append({"kind": "leave", "ref_id": str(r["id"]), "employee_id": str(r["employee_id"]),
                    "title": f"Leave — {r['employee_name'] or 'employee'}", "summary": lines,
                    "link": "/leave", "callback_path": f"/leave/requests/{r['id']}/{{decision}}",
                    "opened_at": r["created_at"],
                    "context": {"employee_id": str(r["employee_id"]), "leave_days": float(r["days"] or 0),
                                "leave_type": r["leave_type_code"] or r["leave_type_name"] or ""}})
    for r in await _query(
            "SELECT id, employee_id, employee_name, period_start, period_end, total_hours, reg_hours, ot_hours, "
            "ot2_hours, submitted_at, created_at FROM ta_timesheets "
            "WHERE org_id = CAST(:org AS uuid) AND status = 'submitted'", p):
        ot = float(r["ot_hours"] or 0) + float(r["ot2_hours"] or 0)
        line = f"{_d(r['period_start'])} to {_d(r['period_end'])} · {_num(r['total_hours'])} hours"
        if ot:
            line += f" ({_num(ot)} overtime)"
        out.append({"kind": "timesheet", "ref_id": str(r["id"]), "employee_id": str(r["employee_id"]),
                    "title": f"Timesheet — {r['employee_name'] or 'employee'}", "summary": [line],
                    "link": "/timesheets", "callback_path": f"/time/timesheets/{r['id']}/{{decision}}",
                    "opened_at": r["submitted_at"] or r["created_at"],
                    "context": {"employee_id": str(r["employee_id"]), "total_hours": float(r["total_hours"] or 0), "ot_hours": ot}})
    for r in await _query(
            "SELECT id, employee_id, employee_name, work_date, reason, requested_in, requested_out, note, "
            "created_at FROM ta_regularizations WHERE org_id = CAST(:org AS uuid) AND status = 'pending'", p):
        line = f"{_d(r['work_date'])} · {_REASON.get(r['reason'], r['reason'] or 'Correction')}"
        if r["requested_in"] or r["requested_out"]:
            line += f" · in {r['requested_in'] or '—'}, out {r['requested_out'] or '—'}"
        lines = [line] + ([f"Note: {r['note']}"] if r["note"] else [])
        out.append({"kind": "attendance_fix", "ref_id": str(r["id"]), "employee_id": str(r["employee_id"]),
                    "title": f"Attendance fix — {r['employee_name'] or 'employee'}", "summary": lines,
                    "link": "/attendance", "callback_path": f"/time/regularizations/{r['id']}/{{decision}}",
                    "opened_at": r["created_at"], "context": {"employee_id": str(r["employee_id"]), "reason": r["reason"] or ""}})
    return out


_SOURCE_SQL = {
    "leave": "SELECT id, status, reviewed_by, review_note AS note, reviewed_at FROM leave_requests "
             "WHERE org_id = CAST(:org AS uuid) AND CAST(id AS text) = ANY(CAST(:ids AS text[]))",
    "timesheet": "SELECT id, status, reviewed_by, review_note AS note, reviewed_at FROM ta_timesheets "
                 "WHERE org_id = CAST(:org AS uuid) AND CAST(id AS text) = ANY(CAST(:ids AS text[]))",
    "attendance_fix": "SELECT id, status, reviewed_by, NULL AS note, reviewed_at FROM ta_regularizations "
                      "WHERE org_id = CAST(:org AS uuid) AND CAST(id AS text) = ANY(CAST(:ids AS text[]))",
}
# what the source's status means for the mirrored item
_OUTCOME = {"approved": "approved", "locked": "approved", "rejected": "rejected",
            "cancelled": "cancelled", "draft": "withdrawn"}


async def fetch_builtin_outcomes(org_id: str, kind: str, ref_ids: list[str]) -> dict[str, dict]:
    """Current state of source rows that are no longer pending: {ref_id: {decision, actor, note, at}}."""
    if not ref_ids or kind not in _SOURCE_SQL:
        return {}
    rows = await _query(_SOURCE_SQL[kind], {"org": str(org_id), "ids": list(ref_ids)})
    out = {}
    for r in rows:
        out[str(r["id"])] = {"decision": _OUTCOME.get(r["status"]),
                             "actor_user_id": str(r["reviewed_by"]) if r["reviewed_by"] else None,
                             "note": r["note"], "at": r["reviewed_at"]}
    return out


async def apply_sync(org_id: str, pending: list[dict], outcomes_for, *, now: datetime | None = None) -> dict:
    """Bring the registry in line with the built-in sources.

    ``pending``       rows from ``fetch_builtin_pending``
    ``outcomes_for``  async (org_id, kind, ref_ids) -> {ref_id: {decision, actor_user_id, note, at}}
    Returns {"opened": n, "closed": n}.
    """
    from shared.utils.notify import notify
    org_id = str(org_id)
    now = now or pm.now_utc()
    users = await directory.org_users(org_id)
    user_of_emp = {u["employee_id"]: u["id"] for u in users if u.get("employee_id")}
    async with pm.session() as s:
        known = (await s.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == org_id, ApprovalItem.source == "sync",
            ApprovalItem.status == "pending"))).scalars().all()
    known_keys = {(i.kind, i.ref_id) for i in known}
    live_keys = {(p["kind"], p["ref_id"]) for p in pending}

    opened = 0
    for p in pending:
        if (p["kind"], p["ref_id"]) in known_keys:
            continue
        opened_at = pm.aware(p.get("opened_at")) or now
        try:
            await ac._open_item(
                org_id, kind=p["kind"], ref_id=p["ref_id"], title=p["title"], summary=p["summary"],
                requester_user_id=user_of_emp.get(p["employee_id"]), approver_roles=BUILTIN_ROLES,
                approver_user_ids=None, approver_manager_of_employee_id=p["employee_id"], link=p["link"],
                callback_path=p["callback_path"], source="sync", opened_at=opened_at,
                # an old backlog seen for the first time must not flood people with notifications
                tell=(now - opened_at) < timedelta(hours=24),
                # D1: the org's leave / timesheet / attendance-fix chain (default = the roles above + manager)
                context=p.get("context") if p.get("context") is not None else {})
            opened += 1
        except Exception as e:                               # noqa: BLE001  (two syncs at once → one wins)
            log.info("sync open %s/%s skipped: %s", p["kind"], p["ref_id"], str(e).splitlines()[0][:120])

    closed = 0
    gone: dict[str, list[ApprovalItem]] = {}
    for i in known:
        if (i.kind, i.ref_id) not in live_keys:
            gone.setdefault(i.kind, []).append(i)
    for kind, items in gone.items():
        outcomes = await outcomes_for(org_id, kind, [i.ref_id for i in items])
        for i in items:
            o = outcomes.get(i.ref_id) or {}
            decision = o.get("decision") or "withdrawn"        # source row gone / back to draft
            done = await ac._close_item(org_id, kind=kind, ref_id=i.ref_id, decision=decision,
                                        actor_user_id=o.get("actor_user_id"), comment=o.get("note"),
                                        via="sync", tell=False, at=pm.aware(o.get("at")))
            if done:
                closed += 1
                if i.requester_user_id and decision in ("approved", "rejected") \
                        and i.requester_user_id != o.get("actor_user_id"):
                    # the owning service sends its own e-mail where it has one → in-app only here
                    await notify(org_id, user_ids=[i.requester_user_id],
                                 title=f"{ac.kind_label(kind)} {decision}: {i.title}",
                                 body=o.get("note") or "", link=i.link or "/my-requests",
                                 kind="approval", email=False)
    return {"opened": opened, "closed": closed}


async def sync_builtin(org_id: str) -> dict:
    """Best-effort mirror of leave / timesheets / attendance fixes. Never raises."""
    try:
        return await apply_sync(org_id, await fetch_builtin_pending(org_id), fetch_builtin_outcomes)
    except Exception as e:                                   # noqa: BLE001
        log.warning("sync_builtin(%s) failed: %s", org_id, e)
        return {"opened": 0, "closed": 0}


# ══════════════════════════ reminders ══════════════════════════════════════════

def age_days(opened_at: datetime | None, now: datetime) -> int:
    opened_at = pm.aware(opened_at)
    if not opened_at:
        return 0
    return max(0, (now - opened_at).days)


def reminder_due(opened_at: datetime | None, reminders_sent: int, escalation_days: int, now: datetime) -> bool:
    """A reminder goes out each time another ``escalation_days`` have passed:
    with 3 days → on day 3, day 6, day 9 … ``escalation_days`` 0 switches it off."""
    if not escalation_days or escalation_days <= 0:
        return False
    return age_days(opened_at, now) >= escalation_days * ((reminders_sent or 0) + 1)


def age_bucket(days: int) -> str:
    if days <= 2:
        return "0–2 days"
    if days <= 7:
        return "3–7 days"
    if days <= 14:
        return "8–14 days"
    return "15+ days"


AGE_BUCKETS = ["0–2 days", "3–7 days", "8–14 days", "15+ days"]


async def send_due_reminders(org_id: str, now: datetime | None = None) -> int:
    """Remind approvers about items of one org that waited too long. Returns how many."""
    from shared.utils.org_settings import get_setting
    now = now or pm.now_utc()
    try:
        days = int(await get_setting(org_id, "approvals.escalation_days", 3) or 0)
    except (TypeError, ValueError):
        days = 3
    if days <= 0:
        return 0
    sent = 0
    async with pm.session() as s:
        items = (await s.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == str(org_id), ApprovalItem.status == "pending"))).scalars().all()
        for i in items:
            if not reminder_due(i.opened_at, i.reminders_sent, days, now):
                continue
            # from the second reminder on, HR is told as well
            await ac._tell_approvers(str(org_id), ac.item_dict(i), reminder=True,
                                     extra_roles=["hr"] if (i.reminders_sent or 0) >= 1 else None)
            i.reminders_sent, i.last_reminder_at = (i.reminders_sent or 0) + 1, now
            sent += 1
        await s.commit()
    return sent
