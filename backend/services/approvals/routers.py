"""Approvals service — the one inbox.

* `GET  /approvals/inbox`            everything waiting for ME (my role, my direct reports,
                                     named, or delegated to me), grouped by kind
* `POST /approvals/items/{id}/decide`  approve / reject with a comment — calls the owning
                                     service's callback, then records the decision
* `GET  /approvals/mine`             things I raised and where they stand
* `GET  /approvals/decided`          decisions I took
* `GET  /approvals/all`              everything pending in the org (org admin / HR)
* `GET/POST/DELETE /approvals/delegations`   out-of-office

A decision row is written once and never changed. This service holds no money rules:
the owning service's callback is the only thing that changes the real record.
"""
import os
import sys
from datetime import date, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402
from shared.utils.platform_models import ApprovalDecision, ApprovalDelegation, ApprovalItem  # noqa: E402

import chains_runtime as crt  # noqa: E402  (D1 — multi-step chains)
import inbox as ib  # noqa: E402
from database import get_db  # noqa: E402

router = APIRouter(prefix="/approvals", tags=["approvals"])

# first path segment of a callback → the service that owns it (same map as the gateway)
SERVICE_URLS = {
    "auth": settings.AUTH_URL, "employees": settings.EMPLOYEE_URL, "masters": settings.EMPLOYEE_URL,
    "onboarding": settings.ONBOARDING_URL, "payroll": settings.PAYROLL_URL, "time": settings.TIME_URL,
    "leave": settings.LEAVE_URL, "approvals": settings.APPROVALS_URL,
    "notifications": settings.NOTIFICATION_URL, "ai": settings.AI_PLATFORM_URL, "ops": settings.OPS_URL,
    "compliance": settings.COMPLIANCE_URL,
}
GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://api-gateway:8000")
ROLE_LABEL = {"org_admin": "Org admin", "hr": "HR", "hr_admin": "HR admin", "hr_manager": "HR manager",
              "hr_staff": "HR staff", "payroll": "Payroll", "payroll_admin": "Payroll admin",
              "manager": "Manager", "employee": "Employee", "finance": "Finance"}


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _actor(request: Request) -> dict:
    uid = request.headers.get("x-user-id")
    if not uid:
        raise HTTPException(401, "Missing user context")
    return {"user_id": uid, "role": request.headers.get("x-role", "") or "",
            "employee_id": request.headers.get("x-employee-id") or None,
            "email": request.headers.get("x-user-email") or ""}


def is_office(role: str) -> bool:
    return role in ("org_admin", "superadmin") or directory.role_in(role, ["hr"])


async def _context(org: str):
    users = await directory.org_users(org)
    employees = {e["id"]: e for e in await directory.org_employees(org)}
    delegations = await ac.load_delegations(org)
    return users, {u["id"]: u for u in users}, employees, delegations


def waiting_on(item: dict, users: list[dict], employees: dict) -> str:
    """Plain words for who an item waits for (no ids)."""
    parts = []
    named = [directory.display_name(u) for u in users if u["id"] in (item.get("approver_user_ids") or [])]
    parts += [n for n in named if n]
    emp = employees.get(item.get("approver_manager_of_employee_id") or "")
    if emp and emp.get("manager_id"):
        mgr = employees.get(emp["manager_id"])
        if mgr:
            parts.append(f"{directory.display_name(mgr)} (manager)")
    parts += [ROLE_LABEL.get(r, r.replace("_", " ").capitalize()) for r in (item.get("approver_roles") or [])]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out) or "Nobody is set"


def _row(item: dict, now, *, users_by_id=None, extra: dict | None = None) -> dict:
    out = {"id": item["id"], "kind": item["kind"], "kind_label": ac.kind_label(item["kind"]),
           "title": item["title"], "summary": item["summary"], "requester_name": item["requester_name"] or "",
           "status": item["status"], "opened_at": item["opened_at"], "closed_at": item["closed_at"],
           "age_days": ib.age_days(datetime.fromisoformat(item["opened_at"]) if item["opened_at"] else None, now),
           "link": item["link"], "can_decide_here": bool(item["callback_path"])}
    out.update(extra or {})
    return out


def _counts(rows: list[dict]) -> list[dict]:
    by: dict[str, int] = {}
    for r in rows:
        by[r["kind"]] = by.get(r["kind"], 0) + 1
    order = list(ac.KINDS) + sorted(k for k in by if k not in ac.KINDS)
    return [{"kind": k, "label": ac.kind_label(k), "count": by[k]} for k in order if k in by]


async def _pending(db, org) -> list[dict]:
    rows = (await db.execute(select(ApprovalItem).where(
        ApprovalItem.org_id == org, ApprovalItem.status == "pending")
        .order_by(ApprovalItem.opened_at))).scalars().all()
    return [ac.item_dict(i) for i in rows]


async def _chain_extras(db, org, items: list[dict], now) -> dict[str, dict]:
    """{item_id: chain summary} — the inbox rows say which step a request is on."""
    try:
        runs = await crt.runs_for(db, org, [i["id"] for i in items])
    except Exception:                                        # noqa: BLE001  (tables not there yet)
        runs = {}
    return {i["id"]: crt.run_summary(runs.get(i["id"]), now) for i in items}


# ══════════════════════════ inbox ══════════════════════════════════════════════

@router.get("/inbox")
async def inbox(request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    await ib.sync_builtin(org)
    users, by_id, employees, delegations = await _context(org)
    now = pm.now_utc()
    out = []
    pending = await _pending(db, org)
    chain = await _chain_extras(db, org, pending, now)
    for item in pending:
        verdict = ac.may_act(item, actor, employees=employees, users=by_id, delegations=delegations)
        if not verdict["allowed"] or verdict["reason"] == "admin":
            continue                 # an admin's override lives on the "All pending" tab, not in "for me"
        out.append(_row(item, now, extra={
            "why": verdict["reason"], **chain.get(item["id"], {}),
            "acting_for": directory.display_name(by_id.get(verdict["on_behalf_of"])) if verdict["on_behalf_of"] else ""}))
    return {"items": out, "total": len(out), "by_kind": _counts(out), "as_of": pm.iso(now)}


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Just the number waiting for me (menu badge / dashboard). No sync — cheap."""
    org, actor = _org(request), _actor(request)
    users, by_id, employees, delegations = await _context(org)
    n = 0
    for item in await _pending(db, org):
        v = ac.may_act(item, actor, employees=employees, users=by_id, delegations=delegations)
        if v["allowed"] and v["reason"] != "admin":
            n += 1
    return {"waiting_for_me": n}


@router.get("/all")
async def all_pending(request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    if not is_office(actor["role"]):
        raise HTTPException(403, "Only an org admin or HR can see every pending item")
    await ib.sync_builtin(org)
    users, by_id, employees, delegations = await _context(org)
    now = pm.now_utc()
    out = []
    pending = await _pending(db, org)
    chain = await _chain_extras(db, org, pending, now)
    for item in pending:
        v = ac.may_act(item, actor, employees=employees, users=by_id, delegations=delegations)
        out.append(_row(item, now, extra={"waiting_on": waiting_on(item, users, employees),
                                          "i_can_decide": v["allowed"], **chain.get(item["id"], {})}))
    return {"items": out, "total": len(out), "by_kind": _counts(out), "as_of": pm.iso(now)}


async def _decisions_for(db, org, item_ids: list[str]) -> dict[str, list[dict]]:
    if not item_ids:
        return {}
    rows = (await db.execute(select(ApprovalDecision).where(
        ApprovalDecision.org_id == org, ApprovalDecision.item_id.in_(item_ids))
        .order_by(ApprovalDecision.at))).scalars().all()
    out: dict[str, list[dict]] = {}
    for d in rows:
        out.setdefault(d.item_id, []).append(_decision_out(d))
    return out


def _decision_out(d: ApprovalDecision) -> dict:
    return {"decision": d.decision, "comment": d.comment or "", "by": d.actor_name or "",
            "on_behalf_of": d.on_behalf_of_name or "", "at": pm.iso(d.at), "round": d.cycle,
            "recorded_from": {"inbox": "Approvals inbox", "owner": "Owning screen", "sync": "Owning screen",
                              "step": "Approval step (chain)", "auto": "Auto-approval rule"}.get(d.via, d.via)}


@router.get("/mine")
async def my_requests(request: Request, db: AsyncSession = Depends(get_db)):
    """Things I raised and their state — replaces the old browser-only My Requests list."""
    org, actor = _org(request), _actor(request)
    await ib.sync_builtin(org)
    users, by_id, employees, _ = await _context(org)
    rows = (await db.execute(select(ApprovalItem).where(
        ApprovalItem.org_id == org, ApprovalItem.requester_user_id == actor["user_id"])
        .order_by(ApprovalItem.opened_at.desc()).limit(500))).scalars().all()
    items = [ac.item_dict(i) for i in rows]
    decisions = await _decisions_for(db, org, [i["id"] for i in items])
    now = pm.now_utc()
    out = []
    for i in items:
        hist = decisions.get(i["id"], [])
        last = hist[-1] if hist and i["status"] != "pending" else None
        out.append(_row(i, now, extra={
            "waiting_on": waiting_on(i, users, employees) if i["status"] == "pending" else "",
            "decided_by": (last or {}).get("by", ""), "decided_at": (last or {}).get("at"),
            "comment": (last or {}).get("comment", ""), "history": hist}))
    return {"items": out, "total": len(out), "by_kind": _counts(out), "as_of": pm.iso(now)}


@router.get("/decided")
async def decided_by_me(request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    decs = (await db.execute(select(ApprovalDecision).where(
        ApprovalDecision.org_id == org, ApprovalDecision.actor_user_id == actor["user_id"])
        .order_by(ApprovalDecision.at.desc()).limit(500))).scalars().all()
    ids = list({d.item_id for d in decs})
    items = {}
    if ids:
        items = {i.id: ac.item_dict(i) for i in (await db.execute(select(ApprovalItem).where(
            ApprovalItem.org_id == org, ApprovalItem.id.in_(ids)))).scalars().all()}
    out = []
    for d in decs:
        i = items.get(d.item_id)
        if not i:
            continue
        out.append({"id": d.id, "kind": i["kind"], "kind_label": ac.kind_label(i["kind"]), "title": i["title"],
                    "summary": i["summary"], "requester_name": i["requester_name"] or "", "link": i["link"],
                    **_decision_out(d)})
    return {"items": out, "total": len(out), "by_kind": _counts(out)}


@router.get("/items/{item_id}")
async def item_detail(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    i = (await db.execute(select(ApprovalItem).where(
        ApprovalItem.id == item_id, ApprovalItem.org_id == org))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Approval item not found")
    item = ac.item_dict(i)
    users, by_id, employees, delegations = await _context(org)
    v = ac.may_act(item, actor, employees=employees, users=by_id, delegations=delegations)
    if not (v["allowed"] or item["requester_user_id"] == actor["user_id"] or is_office(actor["role"])):
        raise HTTPException(403, "This item is not yours to see")
    hist = (await _decisions_for(db, org, [item["id"]])).get(item["id"], [])
    try:
        run = (await crt.runs_for(db, org, [item["id"]])).get(item["id"])
    except Exception:                                        # noqa: BLE001
        run = None
    return _row(item, pm.now_utc(), extra={"waiting_on": waiting_on(item, users, employees),
                                           "i_can_decide": v["allowed"] and item["status"] == "pending",
                                           "history": hist, **crt.run_summary(run), "chain": crt.run_detail(run)})


# ══════════════════════════ decide ═════════════════════════════════════════════

class DecideIn(BaseModel):
    decision: str            # approve | reject
    comment: str | None = None


def callback_target(callback_path: str, verb: str) -> tuple[str | None, str]:
    """(service base url | None when it must go through the gateway, path)."""
    path = (callback_path or "").replace("{decision}", verb)
    if not path.startswith("/") or "://" in path or ".." in path or "\\" in path:
        raise ValueError("The owning screen registered an unusable callback")
    prefix = path.strip("/").split("/", 1)[0]
    return SERVICE_URLS.get(prefix), path


async def call_owner(request: Request, item: dict, verb: str, comment: str, actor: dict,
                     principal: dict | None) -> tuple[bool, str]:
    """POST the owning service's callback. Returns (ok, message for the user)."""
    try:
        base, path = callback_target(item["callback_path"], verb)
    except ValueError as e:
        return False, str(e)
    body = {"decision": verb, "comment": comment, "note": comment, "kind": item["kind"],
            "ref_id": item["ref_id"], "actor_user_id": actor["user_id"]}
    who = principal or actor
    if base:
        headers = {"X-Org-Id": item["org_id"], "X-User-Id": who["user_id"], "X-Role": who.get("role") or "",
                   "X-Employee-Id": who.get("employee_id") or "",
                   "X-User-Email": (f"{actor['email']} (for {who.get('email')})" if principal else actor["email"]),
                   "X-Branch-Id": "" if principal else (request.headers.get("x-branch-id") or "")}
        if principal:
            headers["X-Delegate-User-Id"] = actor["user_id"]
        url = base + path
    else:
        # a service this map does not know → through the gateway with the user's own token
        auth = request.headers.get("authorization")
        if not auth:
            return False, "The owning screen cannot be reached from here. Open it to decide."
        headers, url = {"Authorization": auth}, GATEWAY_URL + path
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, json=body, headers=headers)
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


@router.post("/items/{item_id}/decide")
async def decide(item_id: str, body: DecideIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    decision = ac.normalise_decision(body.decision)
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "Decision must be approve or reject")
    comment = (body.comment or "").strip()
    if decision == "rejected" and not comment:
        raise HTTPException(422, "Add a comment to say why you are rejecting")
    i = (await db.execute(select(ApprovalItem).where(
        ApprovalItem.id == item_id, ApprovalItem.org_id == org))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Approval item not found")
    if i.status != "pending":
        raise HTTPException(409, f"This was already {i.status}")
    item = ac.item_dict(i)
    users, by_id, employees, delegations = await _context(org)
    v = ac.may_act(item, actor, employees=employees, users=by_id, delegations=delegations)
    if not v["allowed"]:
        raise HTTPException(403, "You raised this request, so someone else has to decide it"
                            if v["reason"] == "own_request" else "This item is not waiting for you")
    if not item["callback_path"]:
        raise HTTPException(409, "This one is decided on its own screen. Use Open.")
    principal = None
    if v["on_behalf_of"]:
        u = by_id.get(v["on_behalf_of"]) or {}
        principal = {"user_id": v["on_behalf_of"], "role": u.get("role"), "employee_id": u.get("employee_id"),
                     "email": u.get("email") or ""}
    # D1: a multi-step chain — an approval that is not the LAST step only moves the run forward.
    if decision == "approved":
        try:
            run = (await crt.runs_for(db, org, [item["id"]])).get(item["id"])
        except Exception:                                    # noqa: BLE001
            run = None
        if run and run.status == "pending" and run.cycle == i.cycle and len(run.steps or []) > 0:
            step = await crt.advance(db, org, i, run, actor, principal, comment, employees, by_id)
            if not step["final"]:
                return {"id": item["id"], "status": "pending", "recorded": True, "step": step["step"], "of": step["of"],
                        "message": f"Approved — now at step {step['step']} of {step['of']}"}
    ok, message = await call_owner(request, item, "approve" if decision == "approved" else "reject",
                                   comment, actor, principal)
    if not ok:
        raise HTTPException(409, message)
    # the owner may have called close_item already; if so this records nothing twice
    done = await ac._close_item(org, kind=item["kind"], ref_id=item["ref_id"], decision=decision,
                                actor_user_id=actor["user_id"], comment=comment or None, via="inbox",
                                on_behalf_of_user_id=v["on_behalf_of"], cycle=item["cycle"],
                                # the leave service e-mails its own decision → in-app only for leave
                                tell_email=item["kind"] != "leave")
    return {"id": item["id"], "status": decision, "recorded": bool(done)}


# ══════════════════════════ delegation (out of office) ═════════════════════════

class DelegationIn(BaseModel):
    to_user_id: str
    start_date: str
    end_date: str
    note: str | None = None
    from_user_id: str | None = None      # org admin may set this for someone else


def _deleg_out(d: ApprovalDelegation, by_id: dict, today: date) -> dict:
    state = ("cancelled" if d.cancelled_at else "upcoming" if d.start_date > today
             else "ended" if d.end_date < today else "active")
    return {"id": d.id, "from_name": directory.display_name(by_id.get(d.from_user_id)) or "Former user",
            "to_name": directory.display_name(by_id.get(d.to_user_id)) or "Former user",
            "from_user_id": d.from_user_id, "to_user_id": d.to_user_id,
            "start_date": d.start_date.isoformat(), "end_date": d.end_date.isoformat(),
            "note": d.note or "", "state": state}


@router.get("/people")
async def people(request: Request):
    """Names for the delegate picker (ids are values only — never shown)."""
    org = _org(request)
    users = await directory.org_users(org)
    return sorted(({"id": u["id"], "name": directory.display_name(u),
                    "role": ROLE_LABEL.get(u.get("role") or "", (u.get("role") or "").replace("_", " ").capitalize())}
                   for u in users if u.get("role") != "superadmin"), key=lambda x: x["name"].lower())


@router.get("/delegations")
async def list_delegations(request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    q = select(ApprovalDelegation).where(ApprovalDelegation.org_id == org)
    if not is_office(actor["role"]):
        q = q.where((ApprovalDelegation.from_user_id == actor["user_id"])
                    | (ApprovalDelegation.to_user_id == actor["user_id"]))
    rows = (await db.execute(q.order_by(ApprovalDelegation.start_date.desc()).limit(500))).scalars().all()
    by_id = {u["id"]: u for u in await directory.org_users(org)}
    today = await org_today(org)
    return [{**_deleg_out(d, by_id, today),
             "mine": d.from_user_id == actor["user_id"], "to_me": d.to_user_id == actor["user_id"]} for d in rows]


@router.post("/delegations", status_code=201)
async def create_delegation(body: DelegationIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    from_uid = actor["user_id"]
    if body.from_user_id and body.from_user_id != actor["user_id"]:
        if actor["role"] not in ("org_admin", "superadmin"):
            raise HTTPException(403, "Only an org admin can set a delegation for someone else")
        from_uid = body.from_user_id
    try:
        sd, ed = date.fromisoformat(body.start_date), date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(422, "Dates must be real calendar dates")
    if ed < sd:
        raise HTTPException(422, "The end date is before the start date")
    today = await org_today(org)
    if ed < today:
        raise HTTPException(422, "The end date is already in the past")
    if (ed - sd).days > 366:
        raise HTTPException(422, "A delegation can run for at most one year")
    by_id = {u["id"]: u for u in await directory.org_users(org)}
    if body.to_user_id not in by_id or from_uid not in by_id:
        raise HTTPException(404, "That person is not an active user of this company")
    if body.to_user_id == from_uid:
        raise HTTPException(422, "Pick someone other than the person going out of office")
    overlap = (await db.execute(select(ApprovalDelegation).where(
        ApprovalDelegation.org_id == org, ApprovalDelegation.from_user_id == from_uid,
        ApprovalDelegation.cancelled_at.is_(None), ApprovalDelegation.start_date <= ed,
        ApprovalDelegation.end_date >= sd))).scalars().first()
    if overlap:
        raise HTTPException(409, "There is already a delegation covering some of these dates. Cancel it first.")
    d = ApprovalDelegation(org_id=org, from_user_id=from_uid, to_user_id=body.to_user_id,
                           start_date=sd, end_date=ed, note=(body.note or "").strip()[:300] or None)
    db.add(d)
    await db.commit()
    from shared.utils.notify import notify
    await notify(org, user_ids=[body.to_user_id],
                 title=f"Approvals of {directory.display_name(by_id[from_uid])} come to you "
                       f"from {ib._d(sd)} to {ib._d(ed)}",
                 body=d.note or "", link="/approvals?tab=delegation", kind="approval")
    return _deleg_out(d, by_id, today)


@router.delete("/delegations/{did}")
async def cancel_delegation(did: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = _org(request), _actor(request)
    d = (await db.execute(select(ApprovalDelegation).where(
        ApprovalDelegation.id == did, ApprovalDelegation.org_id == org))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Delegation not found")
    if d.from_user_id != actor["user_id"] and actor["role"] not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only the person who set it, or an org admin, can cancel it")
    if not d.cancelled_at:
        d.cancelled_at = pm.now_utc()
        await db.commit()
    return {"id": d.id, "state": "cancelled"}
