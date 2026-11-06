"""Lexora HRM — approvals registry client: put an item in the one inbox, take it out.

The approvals service is a REGISTRY + INBOX, not a workflow engine. Your service keeps
its own state machine and its own money rules; it only tells the inbox "this is waiting
for someone" and "this is decided".

    from shared.utils.approvals_client import open_item, close_item

    await open_item(org_id, kind="salary_revision", ref_id=str(rev.id),
                    title="Salary revision — A. Rao",
                    summary=["Effective 1 Nov 2026", "Raised by HR"],   # no amounts needed
                    requester_user_id=user_id,
                    approver_roles=["org_admin"],                 # and / or
                    approver_user_ids=None,                       # named people
                    approver_manager_of_employee_id=None,         # "manager of <employee>"
                    link="/compensation/revisions?id=...",        # the owning screen
                    callback_path="/payroll/revisions/<id>/{decision}")
    ...
    await close_item(org_id, kind="salary_revision", ref_id=str(rev.id),
                     decision="approved", actor_user_id=user_id)

CALLBACK CONTRACT (so "Approve / Reject" works from the inbox row):
  The inbox sends ``POST <callback_path>`` to your service with the acting user's
  identity headers (X-Org-Id, X-User-Id, X-Role, X-Employee-Id, X-User-Email) and the
  JSON body ``{"decision": "approve" | "reject", "comment": "...", "note": "..."}``.
  ``{decision}`` in the path is replaced by ``approve`` / ``reject``. Any 2xx answer
  means "done" — the inbox then records the decision and closes the item (calling
  ``close_item`` yourself as well is harmless). A non-2xx answer is shown to the user
  and nothing is recorded. When a delegate acts, the identity headers are those of
  the person they stand in for and ``X-Delegate-User-Id`` carries the real actor.
  Leave ``callback_path`` empty and the row only offers "Open" (your ``link``).

Both helpers are best-effort and idempotent on (org, kind, ref_id): opening twice
keeps one row; closing twice records one decision; re-opening a decided item starts
a new cycle and keeps the old decision. Neither ever raises into the caller.

NO-CODE CHAINS (stream D1 — shared/utils/approval_chains.py):
  Pass ``context={"amount": 25000, "department": "Sales", "employee_id": ...}`` to
  ``open_item``. If the org has defined a chain for ``kind`` (Settings → Approval
  workflows) it is resolved HERE: the first step's approvers replace the ones you
  passed, later steps follow as people approve, and the owning callback is called
  only when the LAST step approves (or on any reject). Without an org chain — or for
  a kind nobody registered — the seeded default applies: one step, the approvers you
  passed. So nothing changes until an org edits a chain. A matching auto-approve rule
  calls your callback with ``approve`` a moment later (actor "Auto-approval rule").
  To register a new request type (D2 hiring_offer, D5 comp_cycle …): add its key to
  ``approval_chains.REQUEST_TYPES`` (label + the context fields conditions may use)
  and call ``open_item(kind=<key>, context=...)``. Unregistered keys still work.
"""
import asyncio
import logging
import os
from datetime import date

from sqlalchemy import select

from shared.utils import approval_chains as chains
from shared.utils import directory
from shared.utils import platform_models as pm
from shared.utils.platform_models import ApprovalDecision, ApprovalDelegation, ApprovalItem
from shared.utils.org_time import local_today

log = logging.getLogger("lexora.approvals")

# Kinds the inbox knows how to label. Any other kind still works.
KINDS: dict[str, str] = {
    "leave": "Leave",
    "timesheet": "Timesheet",
    "attendance_fix": "Attendance fix",
    "expense": "Expense / reimbursement",
    "salary_revision": "Salary revision",
    "career_change": "Career change",
    "confirmation": "Confirmation",
    "exit_case": "Exit case",
    "clearance_line": "Clearance line",
    "requisition": "Requisition",
    "offer": "Offer",
    "hiring_offer": "Hiring offer",
    "goal_approval": "Goal approval",
    "appraisal_stage": "Appraisal stage",
    "comp_cycle": "Compensation cycle",
    "custom_field_request": "Custom field change request",
    "permission": "Permission (short hours)",
    "leave_cancel": "Cancel approved leave",
    "optional_holiday": "Optional holiday",
    "comp_off": "Compensatory off",
    "employee_request": "Employee request",
    "shift_swap": "Shift swap",                      # 2026-10-23 G2
}
AUTO_APPROVE_DELAY_SECONDS = float(os.getenv("APPROVALS_AUTO_DELAY_SECONDS", "2") or 2)
DECISIONS = {"approved", "rejected", "cancelled", "withdrawn"}
_DECISION_ALIASES = {"approve": "approved", "reject": "rejected", "cancel": "cancelled",
                     "withdraw": "withdrawn", "declined": "rejected", "decline": "rejected"}


def kind_label(kind: str) -> str:
    return KINDS.get(kind) or (kind or "").replace("_", " ").capitalize()


def normalise_decision(decision: str) -> str:
    d = (decision or "").strip().lower()
    return _DECISION_ALIASES.get(d, d)


def _lines(summary) -> list[str]:
    if summary is None:
        return []
    if isinstance(summary, str):
        return [ln.strip() for ln in summary.splitlines() if ln.strip()]
    return [str(x) for x in summary if x is not None and str(x).strip()]


# ══════════════════════════ who may act (pure) ═════════════════════════════════

def direct_reason(item: dict, actor: dict, employees: dict[str, dict]) -> str | None:
    """Why ``actor`` may act on ``item`` in their own right — or None.

    item:  {approver_roles, approver_user_ids, approver_manager_of_employee_id}
    actor: {user_id, role, employee_id}
    employees: {employee_id: {manager_id}}
    """
    uid = str(actor.get("user_id") or "")
    if uid and uid in [str(x) for x in (item.get("approver_user_ids") or [])]:
        return "named"
    if directory.role_in(actor.get("role"), item.get("approver_roles") or []):
        return "role"
    emp = item.get("approver_manager_of_employee_id")
    if emp and actor.get("employee_id"):
        e = employees.get(str(emp))
        if e and e.get("manager_id") and str(e["manager_id"]) == str(actor["employee_id"]):
            return "manager"
    return None


def active_delegators(actor_user_id: str, delegations: list[dict], today: date) -> list[str]:
    """User ids whose approvals currently also go to ``actor_user_id``."""
    out = []
    for d in delegations:
        if d.get("cancelled_at"):
            continue
        if str(d.get("to_user_id")) != str(actor_user_id):
            continue
        if d["start_date"] <= today <= d["end_date"]:
            out.append(str(d["from_user_id"]))
    return out


def may_act(item: dict, actor: dict, *, employees: dict[str, dict], users: dict[str, dict] | None = None,
            delegations: list[dict] | None = None, today: date | None = None) -> dict:
    """Decide whether ``actor`` may approve / reject ``item``.

    Returns {"allowed": bool, "reason": "named" | "role" | "manager" | "delegate" | "admin" |
    "own_request" | "none", "on_behalf_of": user_id | None}.

    Order: nobody decides their own request (an org admin may, so a one-admin company
    is not stuck) → named / role / manager-of → delegate of someone who may → org admin.
    """
    uid = str(actor.get("user_id") or "")
    is_admin = actor.get("role") in ("org_admin", "superadmin")
    if uid and uid == str(item.get("requester_user_id") or "") and not is_admin:
        return {"allowed": False, "reason": "own_request", "on_behalf_of": None}
    why = direct_reason(item, actor, employees)
    if why:
        return {"allowed": True, "reason": why, "on_behalf_of": None}
    users = users or {}
    for from_uid in active_delegators(uid, delegations or [], today or local_today()):
        u = users.get(from_uid)
        if not u:
            continue
        principal = {"user_id": from_uid, "role": u.get("role"), "employee_id": u.get("employee_id")}
        if from_uid == str(item.get("requester_user_id") or ""):
            continue
        if direct_reason(item, principal, employees):
            return {"allowed": True, "reason": "delegate", "on_behalf_of": from_uid}
    if is_admin:
        return {"allowed": True, "reason": "admin", "on_behalf_of": None}
    return {"allowed": False, "reason": "none", "on_behalf_of": None}


def approver_user_ids(item: dict, users: list[dict], employees: dict[str, dict],
                      delegations: list[dict] | None = None, today: date | None = None) -> list[str]:
    """Everyone who should be told about ``item``: direct approvers + their active delegates."""
    direct = []
    for u in users:
        if str(u["id"]) == str(item.get("requester_user_id") or ""):
            continue
        actor = {"user_id": u["id"], "role": u.get("role"), "employee_id": u.get("employee_id")}
        if direct_reason(item, actor, employees):
            direct.append(str(u["id"]))
    out = list(direct)
    today = today or local_today()
    for d in delegations or []:
        if d.get("cancelled_at") or not (d["start_date"] <= today <= d["end_date"]):
            continue
        if str(d["from_user_id"]) in direct and str(d["to_user_id"]) not in out:
            out.append(str(d["to_user_id"]))
    return out


def item_dict(i: ApprovalItem) -> dict:
    return {"id": i.id, "org_id": i.org_id, "kind": i.kind, "ref_id": i.ref_id, "title": i.title,
            "summary": list(i.summary or []), "requester_user_id": i.requester_user_id,
            "requester_name": i.requester_name, "approver_roles": list(i.approver_roles or []),
            "approver_user_ids": list(i.approver_user_ids or []),
            "approver_manager_of_employee_id": i.approver_manager_of_employee_id,
            "link": i.link or "", "callback_path": i.callback_path or "", "status": i.status,
            "cycle": i.cycle, "source": i.source, "opened_at": pm.iso(i.opened_at),
            "closed_at": pm.iso(i.closed_at)}


def delegation_dict(d: ApprovalDelegation) -> dict:
    return {"id": d.id, "from_user_id": d.from_user_id, "to_user_id": d.to_user_id,
            "start_date": d.start_date, "end_date": d.end_date, "note": d.note,
            "cancelled_at": d.cancelled_at}


async def load_delegations(org_id: str) -> list[dict]:
    async with pm.session() as s:
        rows = (await s.execute(select(ApprovalDelegation).where(
            ApprovalDelegation.org_id == str(org_id),
            ApprovalDelegation.cancelled_at.is_(None)))).scalars().all()
    return [delegation_dict(d) for d in rows]


# ══════════════════════════ open / close ═══════════════════════════════════════

async def open_item(org_id, *, kind, ref_id, title, summary, requester_user_id, approver_roles=None,
                    approver_user_ids=None, approver_manager_of_employee_id=None, link,
                    callback_path, context: dict | None = None) -> str:
    """Put one item in the approvals inbox. Returns the inbox item id ("" on failure).

    Idempotent on (org, kind, ref_id). Tells the approvers (in-app + e-mail) the first
    time an item opens, and again when a decided item is re-opened. ``context`` feeds
    the org's no-code chain for ``kind`` (amount, department, employee_id …).
    """
    try:
        return await _open_item(str(org_id), kind=kind, ref_id=str(ref_id), title=title, summary=summary,
                                requester_user_id=requester_user_id, approver_roles=approver_roles,
                                approver_user_ids=approver_user_ids,
                                approver_manager_of_employee_id=approver_manager_of_employee_id,
                                link=link, callback_path=callback_path, context=context)
    except Exception as e:                                   # noqa: BLE001
        log.warning("open_item(%s, %s, %s) failed: %s", org_id, kind, ref_id, e)
        return ""


# ── chains: load the org's definition, resolve it, keep the run beside the item ──

async def load_chain(org_id: str, kind: str):
    """The org's ACTIVE chain row for ``kind`` or None (None = seeded default behaviour).
    Best-effort: a missing table (approvals service not booted yet) is just None."""
    try:
        async with pm.session() as s:
            return (await s.execute(select(chains.ApprovalChain).where(
                chains.ApprovalChain.org_id == str(org_id), chains.ApprovalChain.request_type == kind,
                chains.ApprovalChain.active.is_(True)))).scalar_one_or_none()
    except Exception as e:                                   # noqa: BLE001
        log.info("chain lookup skipped for %s: %s", kind, str(e).splitlines()[0][:120])
        return None


async def resolve_chain_run(org_id: str, kind: str, context: dict | None, *, caller: dict | None = None,
                            definition: dict | None = None, with_employees: bool = False):
    """(resolved run dict, chain row | None[, employees]). ``definition`` overrides the stored one (simulator)."""
    chain = None if definition is not None else await load_chain(org_id, kind)
    d = definition if definition is not None else (chain.definition if chain else None)
    employees = {e["id"]: e for e in await directory.org_employees(org_id)}
    users = {u["id"]: u for u in await directory.org_users(org_id)}
    resolved = chains.resolve(d, context, caller=caller, employees=employees, users=users)
    return (resolved, chain, employees) if with_employees else (resolved, chain)


# The one resolver for other streams (D2 hiring_offer, D5 comp_cycle …): same function as
# shared.utils.approval_chains.resolve_chain — async, returns ordered steps or [] (see its docstring).
resolve_chain = chains.resolve_chain


def _actor_for_callback(kind: str) -> dict:
    return {"user_id": "", "role": "org_admin", "employee_id": "", "email": "auto-approval@lexora.local"}


_SERVICE_URLS: dict[str, str] | None = None


def service_url_for(path: str) -> str | None:
    """Base URL of the service owning a callback path (same map as the gateway); None = unknown."""
    global _SERVICE_URLS
    if _SERVICE_URLS is None:
        from shared.utils.config import settings
        _SERVICE_URLS = {
            "auth": settings.AUTH_URL, "employees": settings.EMPLOYEE_URL, "masters": settings.EMPLOYEE_URL,
            "exit": settings.EMPLOYEE_URL, "letters": settings.EMPLOYEE_URL, "hiring": settings.EMPLOYEE_URL,
            "performance": settings.EMPLOYEE_URL, "assets": settings.EMPLOYEE_URL,
            "onboarding": settings.ONBOARDING_URL, "payroll": settings.PAYROLL_URL, "time": settings.TIME_URL,
            "leave": settings.LEAVE_URL, "approvals": settings.APPROVALS_URL,
            "notifications": settings.NOTIFICATION_URL, "ai": settings.AI_PLATFORM_URL, "ops": settings.OPS_URL,
            "compliance": settings.COMPLIANCE_URL,
        }
    prefix = (path or "").strip("/").split("/", 1)[0]
    return _SERVICE_URLS.get(prefix)


async def post_callback(org_id: str, callback_path: str, verb: str, *, comment: str, kind: str, ref_id: str,
                        actor: dict, delegate_user_id: str | None = None) -> tuple[bool, str]:
    """POST the owning service's callback (the CALLBACK CONTRACT above). (ok, message)."""
    import httpx
    path = (callback_path or "").replace("{decision}", verb)
    if not path.startswith("/") or "://" in path or ".." in path or "\\" in path:
        return False, "The owning screen registered an unusable callback"
    base = service_url_for(path)
    if not base:
        return False, "The owning service of this callback is not known to the approvals client"
    headers = {"X-Org-Id": str(org_id), "X-User-Id": str(actor.get("user_id") or ""), "X-Role": actor.get("role") or "",
               "X-Employee-Id": actor.get("employee_id") or "", "X-User-Email": actor.get("email") or ""}
    if delegate_user_id:
        headers["X-Delegate-User-Id"] = str(delegate_user_id)
    body = {"decision": verb, "comment": comment, "note": comment, "kind": kind, "ref_id": ref_id,
            "actor_user_id": actor.get("user_id") or ""}
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(base + path, json=body, headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException):
        return False, "The owning screen did not answer. Nothing was changed — try again."
    if 200 <= r.status_code < 300:
        return True, ""
    try:
        detail = r.json().get("detail")
    except Exception:                                        # noqa: BLE001
        detail = None
    return False, (detail if isinstance(detail, str) and detail else
                   f"The owning screen refused this ({r.status_code}). Nothing was changed.")


async def execute_auto_approval(org_id: str, item: dict) -> bool:
    """Carry out a matched auto-approve rule: call the owner's approve callback, then close
    the inbox item with the actor 'Auto-approval rule'. Returns True when done."""
    ok, msg = True, ""
    if item.get("callback_path"):
        ok, msg = await post_callback(org_id, item["callback_path"], "approve", comment="Approved automatically by a rule",
                                      kind=item["kind"], ref_id=item["ref_id"], actor=_actor_for_callback(item["kind"]))
    async with pm.session() as s:
        run = (await s.execute(select(chains.ApprovalRun).where(
            chains.ApprovalRun.org_id == str(org_id), chains.ApprovalRun.item_id == item["id"],
            chains.ApprovalRun.cycle == int(item.get("cycle") or 1)))).scalar_one_or_none()
        if run:
            hist = list(run.history or [])
            hist.append({"at": pm.now_utc().isoformat(), "event": "auto_approved" if ok else "auto_failed", "note": msg})
            run.history = hist
            if ok:
                run.status, run.closed_at = "approved", pm.now_utc()
            else:
                run.status = "pending"
            await s.commit()
    if ok:
        await _close_item(str(org_id), kind=item["kind"], ref_id=item["ref_id"], decision="approved", actor_user_id=None,
                          actor_name="Auto-approval rule", comment="Approved automatically by a rule", via="auto")
    else:
        log.warning("auto-approval of %s/%s failed: %s — left for a person", item["kind"], item["ref_id"], msg)
        await _tell_approvers(str(org_id), item)
    return ok


async def _auto_later(org_id: str, item: dict):
    await asyncio.sleep(AUTO_APPROVE_DELAY_SECONDS)          # the owner's own transaction commits first
    try:
        await execute_auto_approval(org_id, item)
    except Exception as e:                                   # noqa: BLE001
        log.warning("auto-approval task failed: %s", e)


async def _open_item(org_id, *, kind, ref_id, title, summary, requester_user_id, approver_roles,
                     approver_user_ids, approver_manager_of_employee_id, link, callback_path,
                     source="registry", opened_at=None, requester_name=None, tell=True, context=None) -> str:
    caller = dict(approver_roles=[r for r in (approver_roles or []) if r],
                  approver_user_ids=[str(u) for u in (approver_user_ids or []) if u],
                  approver_manager_of_employee_id=(str(approver_manager_of_employee_id)
                                                   if approver_manager_of_employee_id else None))
    # The org's chain decides who the FIRST approvers are and the inbox drives the later steps —
    # ONLY when the caller passes ``context`` (even {}). A caller that runs its own step machine
    # (hiring offers, comp cycles: one item per step, approvers from resolve_chain) passes no
    # context and gets the plain registry, so a chain is never driven twice.
    resolved, chain = None, None
    if context is not None:
        try:
            resolved, chain = await resolve_chain_run(org_id, kind, chains.normalise_metadata(context), caller=caller)
        except Exception as e:                               # noqa: BLE001
            log.warning("chain resolution failed for %s/%s — using the screen's approvers: %s", kind, ref_id, e)
    first = chains.targets(resolved["steps"][0]) if resolved and resolved["steps"] else dict(caller)
    fields = dict(
        title=(title or kind_label(kind))[:300], summary=_lines(summary),
        requester_user_id=str(requester_user_id) if requester_user_id else None,
        approver_roles=first["approver_roles"], approver_user_ids=first["approver_user_ids"],
        approver_manager_of_employee_id=first["approver_manager_of_employee_id"],
        link=(link or "")[:400], callback_path=(callback_path or "")[:400])
    is_new = False
    async with pm.session() as s:
        item = (await s.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == org_id, ApprovalItem.kind == kind,
            ApprovalItem.ref_id == ref_id))).scalar_one_or_none()
        if item is None:
            item = ApprovalItem(id=pm.new_id(), org_id=org_id, kind=kind, ref_id=ref_id, status="pending",
                                cycle=1, source=source, opened_at=opened_at or pm.now_utc(),
                                requester_name=requester_name, **fields)
            s.add(item)
            is_new = True
        else:
            for k, v in fields.items():
                setattr(item, k, v)
            if requester_name:
                item.requester_name = requester_name
            if item.status != "pending":                      # decided earlier → a new cycle
                item.status, item.cycle = "pending", (item.cycle or 1) + 1
                item.opened_at, item.closed_at = opened_at or pm.now_utc(), None
                item.reminders_sent, item.last_reminder_at = 0, None
                is_new = True
        if not item.requester_name and item.requester_user_id:
            users = {u["id"]: u for u in await directory.org_users(org_id)}
            item.requester_name = directory.display_name(users.get(item.requester_user_id)) or None
        await s.commit()
        snapshot = item_dict(item)
    auto = False
    if is_new and resolved is not None:
        try:
            async with pm.session() as s:
                old = (await s.execute(select(chains.ApprovalRun).where(
                    chains.ApprovalRun.org_id == org_id, chains.ApprovalRun.item_id == snapshot["id"],
                    chains.ApprovalRun.cycle == snapshot["cycle"]))).scalar_one_or_none()
                if old:
                    await s.delete(old)
                s.add(chains.new_run_row(org_id, snapshot, resolved, chain=chain, context=context))
                await s.commit()
            auto = bool(resolved.get("auto_approved"))
        except Exception as e:                               # noqa: BLE001
            log.info("chain run not stored for %s/%s: %s", kind, ref_id, str(e).splitlines()[0][:120])
    if auto:
        asyncio.get_running_loop().create_task(_auto_later(org_id, snapshot))
    elif is_new and tell:
        await _tell_approvers(org_id, snapshot)
    return snapshot["id"]


async def _tell_approvers(org_id: str, item: dict, *, reminder: bool = False, extra_roles=None) -> None:
    from shared.utils.notify import notify
    try:
        users = await directory.org_users(org_id)
        employees = {e["id"]: e for e in await directory.org_employees(org_id)}
        ids = approver_user_ids(item, users, employees, await load_delegations(org_id))
        if not ids and not extra_roles:
            return
        prefix = "Still waiting for you: " if reminder else "Waiting for your approval: "
        await notify(org_id, user_ids=ids, roles=extra_roles, title=prefix + item["title"],
                     body="\n".join(item.get("summary") or []), link="/approvals", kind="approval")
    except Exception as e:                                   # noqa: BLE001
        log.warning("approver notification failed: %s", e)


async def close_item(org_id, *, kind, ref_id, decision, actor_user_id):
    """Mark the inbox item decided. Idempotent; never raises. ``decision`` is
    approved | rejected | cancelled | withdrawn (approve / reject are accepted too)."""
    try:
        return await _close_item(str(org_id), kind=kind, ref_id=str(ref_id), decision=decision,
                                 actor_user_id=actor_user_id)
    except Exception as e:                                   # noqa: BLE001
        log.warning("close_item(%s, %s, %s) failed: %s", org_id, kind, ref_id, e)
        return None


async def _close_item(org_id, *, kind, ref_id, decision, actor_user_id, comment=None, via="owner",
                      on_behalf_of_user_id=None, actor_name=None, tell=True, tell_email=True, at=None,
                      cycle=None):
    """``cycle`` (optional): close only while the item is still in that cycle. The inbox passes the
    cycle it decided, so an owner that runs its own step machine (comp cycles, hiring offers: close
    step N, re-open the SAME item for step N+1 inside the callback) keeps its next step open."""
    decision = normalise_decision(decision)
    if decision not in DECISIONS:
        log.warning("close_item: unknown decision %r for %s/%s", decision, kind, ref_id)
        return None
    async with pm.session() as s:
        item = (await s.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == org_id, ApprovalItem.kind == kind,
            ApprovalItem.ref_id == ref_id))).scalar_one_or_none()
        if item is None or item.status != "pending":
            return None                                       # nothing open → nothing to do
        if cycle is not None and (item.cycle or 1) != cycle:
            return None                                       # re-opened for a later step meanwhile
        users = {u["id"]: u for u in await directory.org_users(org_id)}
        actor_user_id = str(actor_user_id) if actor_user_id else None
        item.status, item.closed_at = decision, at or pm.now_utc()
        s.add(ApprovalDecision(
            org_id=org_id, item_id=item.id, cycle=item.cycle, decision=decision, comment=comment,
            actor_user_id=actor_user_id,
            actor_name=actor_name or directory.display_name(users.get(actor_user_id)) or None,
            on_behalf_of_user_id=on_behalf_of_user_id,
            on_behalf_of_name=directory.display_name(users.get(on_behalf_of_user_id)) or None,
            via=via, at=at or pm.now_utc()))
        await s.commit()
        snapshot = item_dict(item)
    try:                                                     # the chain run (if any) follows the item
        async with pm.session() as s:
            run = (await s.execute(select(chains.ApprovalRun).where(
                chains.ApprovalRun.org_id == org_id, chains.ApprovalRun.item_id == snapshot["id"],
                chains.ApprovalRun.cycle == snapshot["cycle"]))).scalar_one_or_none()
            if run and run.status in ("pending", "auto"):
                run.status = decision if decision in ("approved", "rejected") else "closed"
                run.closed_at = at or pm.now_utc()
                hist = list(run.history or [])
                hist.append({"at": pm.now_utc().isoformat(), "event": decision, "via": via})
                run.history = hist
                await s.commit()
    except Exception as e:                                   # noqa: BLE001
        log.info("chain run not closed for %s/%s: %s", kind, ref_id, str(e).splitlines()[0][:120])
    if tell and snapshot["requester_user_id"] and snapshot["requester_user_id"] != actor_user_id:
        from shared.utils.notify import notify
        await notify(org_id, user_ids=[snapshot["requester_user_id"]],
                     title=f"{kind_label(kind)} {decision}: {snapshot['title']}",
                     body=comment or "", link=snapshot["link"] or "/my-requests", kind="approval",
                     email=tell_email)
    return snapshot["id"]
