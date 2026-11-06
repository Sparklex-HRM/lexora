"""Shared plumbing for the three employee-lifecycle agents (2026-10-26): Onboarding Agent, Exit Agent, Attendance Agent.

Same rails as the Payroll Auditor / Compliance Clerk (agentic_runs · agentic_proposals · agentic_tasks · agentic_log ·
agentic_value) — this module only removes the copy-paste between the three packages:

  LifecycleItem / ai_lifecycle_items   one row per open checklist item / finding per case (boot create_tables; additive)
  store_items(...)                     upsert by (agent, case_key, code, ref); items of a scanned case that are no
                                       longer found are RESOLVED (the person fixed it) and their chase task closed
  chase(...)                           one tier-0 ``<agent>.chase`` proposal per chaseable item. LIVE: the executor opens
                                       the deduplicated task for the right owner (in-app + e-mail, WhatsApp only through
                                       the channel adapter with consent). SHADOW: the proposal is stored as ``shadow`` —
                                       nothing is opened, nothing is sent — and the item lands in ``would_open``
  register_chase_executor(agent)       the executor behind ``<agent>.chase``
  agent_settings / save_agent_settings the agent's own keys under ``ai_agentic.<agent>`` (org admin saves)
  guard(request, what, admin=False)    permission check: ``shared.utils.permissions.require_permission`` when that helper
                                       exists (the permissions stream), else the shared role FAMILIES (never a raw literal)

Owner roles an item may name: employee · manager · hr · payroll · it · admin · finance (it / admin / finance fall back to
HR when the org has not named a login for them in the agent settings).
"""
from __future__ import annotations

import copy
import logging
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import JSON, DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.platform_models import PlatformBase, new_id, now_utc  # noqa: E402

import agentic_proposals as PR  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
import agentic_tasks as TK  # noqa: E402
import agentic_value as VAL  # noqa: E402
from agentic_registry import register_executor  # noqa: E402

log = logging.getLogger("lexora.agentic.lifecycle")

SEVERITIES = ("blocking", "warning", "info")
OWNER_ROLES = ("employee", "manager", "hr", "payroll", "it", "admin", "finance")
ITEM_STATUSES = ("open", "resolved", "dismissed")
MAX_TASKS_PER_RUN = 200


# ══════════════════════════ table ═══════════════════════════════════════════════

class LifecycleItem(PlatformBase):
    __tablename__ = "ai_lifecycle_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)                # onboarding | exit | attendance
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    case_key: Mapped[str] = mapped_column(String(80), index=True)            # onboarding session / employee · exit case · employee:date
    case_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    employee_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    code: Mapped[str] = mapped_column(String(16), index=True)                # OB-04 · EX-02 · AT-01 …
    ref: Mapped[str] = mapped_column(String(80), default="-")                # sub-item (clearance item id, work date …)
    severity: Mapped[str] = mapped_column(String(10), default="warning")
    title: Mapped[str] = mapped_column(String(300))
    what: Mapped[str | None] = mapped_column(Text, nullable=True)
    how_to_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    link: Mapped[str | None] = mapped_column(String(300), nullable=True)
    due_on: Mapped[str | None] = mapped_column(String(10), nullable=True)    # calendar date YYYY-MM-DD
    clock: Mapped[dict | None] = mapped_column(JSON, nullable=True)          # {kind, due, days_left, verified, source, rule_key}
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


LIFECYCLE_TABLES = [LifecycleItem.__table__]


def item_dict(i: LifecycleItem) -> dict:
    return {"id": i.id, "agent": i.agent, "run_id": i.run_id, "case_key": i.case_key, "case_label": i.case_label, "employee_id": i.employee_id,
            "employee_ref": i.employee_ref, "code": i.code, "ref": i.ref, "severity": i.severity, "title": i.title, "what": i.what,
            "how_to_fix": i.how_to_fix, "owner_role": i.owner_role, "link": i.link, "due_on": i.due_on, "clock": i.clock or None,
            "detail": i.detail or {}, "status": i.status, "task_id": i.task_id, "resolved_at": pm.iso(i.resolved_at),
            "created_at": pm.iso(i.created_at), "updated_at": pm.iso(i.updated_at)}


def item(code: str, *, case_key: str, title: str, severity: str = "warning", owner_role: str = "hr", what: str | None = None,
         how_to_fix: str | None = None, employee: dict | None = None, case_label: str | None = None, ref: str = "-", link: str | None = None,
         due_on: date | str | None = None, clock: dict | None = None, detail: dict | None = None, chase: bool | None = None,
         owner_user_id: str | None = None, owner_employee_id: str | None = None) -> dict:
    """One finding / open checklist item (the dict every check returns)."""
    e = employee or {}
    return {"code": code, "case_key": str(case_key)[:80], "case_label": (case_label or e.get("name") or None), "employee_id": (str(e["id"]) if e.get("id") else None),
            "employee_ref": (f"{e.get('employee_number') or ''} {e.get('name') or ''}".strip() or None) if e else None,
            "ref": str(ref or "-")[:80], "severity": severity if severity in SEVERITIES else "warning", "title": title[:300], "what": what, "how_to_fix": how_to_fix,
            "owner_role": owner_role if owner_role in OWNER_ROLES else "hr", "link": link,
            "due_on": (due_on.isoformat() if isinstance(due_on, date) else (str(due_on)[:10] if due_on else None)), "clock": clock, "detail": detail or {},
            "chase": (severity != "info") if chase is None else bool(chase), "owner_user_id": owner_user_id, "owner_employee_id": owner_employee_id}


def _key(d) -> tuple:
    return (d["case_key"] if isinstance(d, dict) else d.case_key, d["code"] if isinstance(d, dict) else d.code, d["ref"] if isinstance(d, dict) else d.ref)


async def store_items(org: str, agent: str, run_id: str, items: list[dict], *, scanned_cases: set[str], full_scan: bool = False) -> list[dict]:
    """Upsert this run's items; open items of a SCANNED case that were not found again → resolved (+ task closed).

    ``full_scan`` (the run read the whole population itself, not a fixture): an open item whose case is no longer in
    the population at all — exit withdrawn / closed, joiner past the look-back window, employee gone — is resolved too
    (otherwise such items stayed open and their tasks kept being chased forever). An EMPTY population is treated as a
    failed read and resolves nothing out of scope."""
    now = pm.now_utc()
    seen = {_key(x) for x in items}
    closed_tasks: list[str] = []
    out: list[dict] = []
    async with pm.session() as s:
        cur = (await s.execute(select(LifecycleItem).where(LifecycleItem.org_id == str(org), LifecycleItem.agent == agent,
                                                           LifecycleItem.status == "open"))).scalars().all()
        by_key = {_key(r): r for r in cur}
        out_of_scope = bool(full_scan and scanned_cases)
        for r in cur:
            if (r.case_key in scanned_cases or (out_of_scope and r.case_key not in scanned_cases)) and _key(r) not in seen:
                r.status, r.resolved_at, r.updated_at = "resolved", now, now
                if r.task_id:
                    closed_tasks.append(r.task_id)
        for x in items:
            r = by_key.get(_key(x))
            if r is None:
                r = LifecycleItem(org_id=str(org), agent=agent, case_key=x["case_key"], code=x["code"], ref=x["ref"])
                s.add(r)
            r.run_id, r.case_label, r.employee_id, r.employee_ref = run_id, (x.get("case_label") or None), x.get("employee_id"), x.get("employee_ref")
            r.severity, r.title, r.what, r.how_to_fix, r.owner_role = x["severity"], x["title"], x.get("what"), x.get("how_to_fix"), x.get("owner_role")
            r.link, r.due_on, r.clock, r.detail, r.updated_at = x.get("link"), x.get("due_on"), x.get("clock"), x.get("detail") or {}, now
            await s.flush()
            x["id"] = r.id
            x["task_id"] = r.task_id
        await s.commit()
        out = [dict(x) for x in items]
    for tid in closed_tasks:
        try:
            await TK.complete_task(org, tid, actor_user_id=None, result={"resolved_by": "agent re-check: the item is no longer open"})
        except TK.TaskError:
            pass
    return out


async def set_task_ids(org: str, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    async with pm.session() as s:
        for r in (await s.execute(select(LifecycleItem).where(LifecycleItem.org_id == str(org), LifecycleItem.id.in_(list(mapping))))).scalars().all():
            r.task_id = mapping.get(r.id)
        await s.commit()


async def list_items(org: str, agent: str, *, status: str | None = "open", case_key: str | None = None, limit: int = 2000) -> list[dict]:
    q = select(LifecycleItem).where(LifecycleItem.org_id == str(org), LifecycleItem.agent == agent)
    if status:
        q = q.where(LifecycleItem.status.in_([x for x in status.split(",") if x in ITEM_STATUSES] or ITEM_STATUSES))
    if case_key:
        q = q.where(LifecycleItem.case_key == case_key)
    async with pm.session() as s:
        rows = (await s.execute(q.order_by(LifecycleItem.case_key, LifecycleItem.code).limit(min(limit, 5000)))).scalars().all()
    order = {k: i for i, k in enumerate(SEVERITIES)}
    return sorted((item_dict(r) for r in rows), key=lambda d: (order.get(d["severity"], 9), d["due_on"] or "9999", d["case_key"], d["code"]))


async def dismiss_item(org: str, agent: str, item_id: str, *, reason: str, actor_user_id: str | None) -> dict:
    import agentic_log as LOG
    if len((reason or "").strip()) < 3:
        raise ValueError("Say why the item does not apply (3 characters or more)")
    async with pm.session() as s:
        r = (await s.execute(select(LifecycleItem).where(LifecycleItem.id == item_id, LifecycleItem.org_id == str(org), LifecycleItem.agent == agent))).scalar_one_or_none()
        if not r:
            raise LookupError("That item was not found")
        r.status, r.resolved_at, r.updated_at = "dismissed", pm.now_utc(), pm.now_utc()
        r.detail = {**(r.detail or {}), "dismissed_reason": reason.strip()[:300], "dismissed_by": actor_user_id}
        await s.commit()
        out = item_dict(r)
    if out.get("task_id"):
        try:
            await TK.complete_task(org, out["task_id"], actor_user_id=actor_user_id, result={"dismissed": reason.strip()[:300]}, status="cancelled")
        except TK.TaskError:
            pass
    await LOG.append_event(org, event_type="item.dismissed", agent_name=agent, tool_name="dismiss_item", approver_user_id=actor_user_id,
                           tool_input={"item_id": item_id, "code": out["code"], "reason": reason.strip()[:300]}, risk_tier=0, policy_decision="allow")
    return out


# ══════════════════════════ owners ══════════════════════════════════════════════

async def logins(org: str) -> dict:
    from agents import pay_calendar as PC
    return await PC.logins(org)


def owner_for(x: dict, lg: dict, cfg: dict, *, manager_of: dict | None = None) -> tuple[str | None, str | None, str]:
    """→ (owner_user_id, owner_employee_id, resolved_role)."""
    if x.get("owner_user_id") or x.get("owner_employee_id"):
        return x.get("owner_user_id"), (None if x.get("owner_user_id") else x.get("owner_employee_id")), x.get("owner_role") or "hr"
    by_emp = lg.get("by_employee") or {}
    role = x.get("owner_role") or "hr"
    eid = str(x.get("employee_id") or "")
    if role == "employee" and eid:
        return by_emp.get(eid), (None if by_emp.get(eid) else eid), "employee"
    if role == "manager":
        mid = str((x.get("detail") or {}).get("manager_id") or (manager_of or {}).get(eid) or "")
        if mid:
            return by_emp.get(mid), (None if by_emp.get(mid) else mid), "manager"
        role = "hr"
    if role in ("it", "admin", "finance"):
        uid = ((cfg or {}).get("owners") or {}).get(role)
        if uid:
            return str(uid), None, role
        role = "payroll" if role == "finance" else "hr"
    office = (lg.get("payroll") or []) if role == "payroll" else (lg.get("hr") or [])
    office = office or (lg.get("hr") or []) or (lg.get("payroll") or []) or (lg.get("org_admin") or [])
    return (office[0] if office else None), None, role


# ══════════════════════════ chase ═══════════════════════════════════════════════

def register_chase_executor(agent: str) -> str:
    tool = f"{agent}.chase"

    @register_executor(tool)
    async def _chase_exec(org: str, proposal: dict, args: dict) -> dict:
        due_h = float(args.get("due_hours") or 48)
        # the case key ("case:<uuid>", "emp:<uuid>" …) lives in detail — run_ref is VARCHAR(36) for a payroll run id, and
        # putting the case key there failed EVERY live chase on Postgres (SQLite tests do not enforce lengths)
        t = await TK.open_task(org, agent=agent, kind=str(args.get("kind") or "lifecycle_item")[:60], title=str(args.get("title") or "Action needed")[:300],
                               detail={**dict(args.get("detail") or {}), "case_key": args.get("case_key")},
                               owner_user_id=args.get("owner_user_id"), owner_employee_id=args.get("owner_employee_id"),
                               due_at=pm.now_utc() + timedelta(hours=due_h), employee_id=args.get("employee_id"), run_ref=None,
                               created_by_run=proposal.get("run_id"), dedupe_key=args.get("dedupe_key"), first_chase_in_hours=args.get("first_chase_in_hours"))
        if args.get("item_id"):
            await set_task_ids(org, {str(args["item_id"]): t["id"]})
        if not t.get("deduplicated"):
            await VAL.record_outcome(org, agent=agent, kind="input_chased", units=1, run_id=proposal.get("run_id"), proposal_id=proposal.get("id"),
                                     task_id=t["id"], note=str(args.get("code") or "")[:60])
        return {"task_id": t["id"], "deduplicated": bool(t.get("deduplicated")), "status": t["status"]}
    return tool


async def chase(org: str, run: dict, agent: str, items: list[dict], lg: dict, cfg: dict, *, link_base: str,
                hours_for: dict | None = None, manager_of: dict | None = None, data_sources: list | None = None) -> dict:
    """One tier-0 ``<agent>.chase`` proposal per chaseable open item. Shadow: recorded only (``would_open``)."""
    out = {"proposed": 0, "opened": 0, "deduplicated": 0, "skipped_no_owner": 0, "would_open": []}
    hours_for = hours_for or {}
    n = 0
    for x in items:
        if not x.get("chase") or x["severity"] == "info":
            continue
        if n >= MAX_TASKS_PER_RUN:
            break
        uid, emp_owner, role = owner_for(x, lg, cfg, manager_of=manager_of)
        if not uid and not emp_owner:
            out["skipped_no_owner"] += 1
            continue
        n += 1
        hours = float(hours_for.get(role) or hours_for.get("default") or 24)
        dedupe = f"{agent}:{x['code']}:{x['case_key']}:{x['ref']}"[:160]
        args = {"kind": x["code"].lower().replace("-", "_"), "title": x["title"], "code": x["code"], "case_key": x["case_key"], "item_id": x.get("id"),
                "detail": {"what": x.get("what") or x["title"], "why": (x.get("detail") or {}).get("why"), "how_to_fix": x.get("how_to_fix"),
                           "link": x.get("link") or f"{link_base}?case={x['case_key']}", "code": x["code"], "severity": x["severity"], "due_on": x.get("due_on"),
                           "clock": x.get("clock")},
                "owner_user_id": uid, "owner_employee_id": emp_owner, "owner_role": role, "employee_id": x.get("employee_id"),
                "dedupe_key": dedupe, "due_hours": hours * 2, "first_chase_in_hours": hours}
        p = await PR.propose(org, agent=agent, tool_name=f"{agent}.chase", run_id=run["id"], tool_args=args,
                             plain_action=f"Ask {role} to fix: {x['title']}"[:600], reason=x.get("what") or "", confidence="high",
                             aggregate_impact={"headcount": 1 if x.get("employee_id") else 0}, requested_by_user_id=run.get("requested_by_user_id"),
                             data_sources_read=data_sources or [])
        out["proposed"] += 1
        if p["status"] == "shadow":
            out["would_open"].append({"owner_role": role, "title": x["title"], "code": x["code"], "case_key": x["case_key"], "employee_ref": x.get("employee_ref"),
                                      "proposal_id": p["id"]})
        elif p["status"] == "executed":
            res = (p.get("result") or {}).get("detail") or p.get("result") or {}
            out["opened"] += 0 if res.get("deduplicated") else 1
            out["deduplicated"] += 1 if res.get("deduplicated") else 0
            if res.get("task_id"):
                x["task_id"] = res["task_id"]
    return out


async def already_proposed(org: str, agent: str, tool: str, match: dict, *, mode: str | None = None) -> bool:
    """A live (not denied / expired / failed) proposal for the same tool + identifying args exists.

    ``mode`` = the org's CURRENT mode. A SHADOW proposal only de-duplicates further shadow runs: once the org goes live,
    a joiner / exit already seen in shadow must still get its real approval card (the shadow row used to block it forever)."""
    statuses = "proposed,second_approval,edited,approved,executed" + (",shadow" if mode in (None, "shadow") else "")
    for p in await PR.list_proposals(org, agent=agent, status=statuses, limit=500):
        if p["tool_name"] != tool:
            continue
        a = p.get("tool_args") or {}
        if all(str(a.get(k)) == str(v) for k, v in match.items()):
            return True
    return False


async def ran_today(org: str, agent: str, trigger: str, today: date) -> bool:
    for r in await RUNS.list_runs(org, agent=agent, limit=50):
        if r.get("trigger") == trigger and r.get("status") != "error" and (r.get("summary") or {}).get("run_date") == today.isoformat():
            return True
    return False


# ══════════════════════════ settings ════════════════════════════════════════════

def agent_settings(state: dict, agent: str, defaults: dict) -> dict:
    out = copy.deepcopy(defaults)
    own = state.get(agent) if isinstance(state.get(agent), dict) else {}
    for k, v in (own or {}).items():
        if k in out:
            out[k] = ({**out[k], **v} if isinstance(out[k], dict) and isinstance(v, dict) else v)
    out["chase_every_hours"] = float((state.get("thresholds") or {}).get("chase_every_hours") or 24)
    return out


FORBIDDEN = ("password", "secret", "token", "otp", "pin", "credential")


def clean_settings(patch: dict, current: dict, defaults: dict) -> dict:
    out = {k: copy.deepcopy(current.get(k, defaults[k])) for k in defaults}
    for k, v in (patch or {}).items():
        if k not in defaults:
            continue
        if any(f in k.lower() for f in FORBIDDEN):
            raise ValueError(f"{k} is not a setting this agent keeps")
        d = defaults[k]
        if isinstance(d, dict):
            if not isinstance(v, dict):
                raise ValueError(f"{k} must be an object")
            out[k] = {kk: (str(vv)[:64] if vv not in (None, "") else None) for kk, vv in v.items() if kk in d}
        elif isinstance(d, bool):
            out[k] = bool(v)
        else:
            try:
                n = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if n < 0 or n > 10000:
                raise ValueError(f"{k} must be between 0 and 10000")
            out[k] = int(n) if n == int(n) else n
    return out


async def save_agent_settings(org: str, agent: str, patch: dict, defaults: dict, *, actor_id: str | None, actor_email: str | None) -> dict:
    cur = await ST.raw_settings(org)
    cur[agent] = clean_settings(patch or {}, cur.get(agent) or {}, defaults)
    await ST._write(org, cur, actor_id, actor_email)
    return cur[agent]


# ══════════════════════════ request guards ══════════════════════════════════════

def org_of(request) -> str:
    from fastapi import HTTPException
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def user_of(request) -> str:
    from fastapi import HTTPException
    uid = (request.headers.get("x-user-id") or "").strip()
    if not uid:
        raise HTTPException(401, "No signed-in user on this request")
    return uid


# permission → the role family it maps to while the permission catalogue helper is not importable here
_PERM_FAMILY = {"ai.view": "OFFICE", "ai.create": "OFFICE", "ai.admin": "ORG_ADMIN"}


def guard(request, permission: str, what: str) -> str:
    """Permission check for the lifecycle agents' routes (catalogue keys ai.view / ai.create / ai.admin).
    1. the role FAMILY the permission maps to (``shared.utils.roles``: OFFICE = org admin + HR family + payroll, or org
       admin) — always, so an employee who holds ``ai.view`` for the assistant never opens an agent workspace; the gateway
       stamps a custom role's ACTING role in X-Role, so custom roles pass here exactly like their system role;
    2. ``shared.utils.permissions.require_permission`` as well, when the permissions stream has landed it."""
    from fastapi import HTTPException
    from shared.utils.roles import OFFICE, has_role
    role = (request.headers.get("x-role") or "").lower()
    fam = _PERM_FAMILY.get(permission, "OFFICE")
    ok = has_role(role, ("org_admin",)) if fam == "ORG_ADMIN" else has_role(role, OFFICE)
    if not ok:
        raise HTTPException(403, what)
    try:
        from shared.utils.permissions import require_permission  # type: ignore
    except Exception:                                            # noqa: BLE001 — helper not there yet
        require_permission = None
    if require_permission is not None:
        try:
            require_permission(request, permission)
        except HTTPException:
            raise
        except Exception:                                        # noqa: BLE001 — helper shape differs: the family check stands
            pass
    return role


def raise_http(e: Exception):
    from fastapi import HTTPException
    status = getattr(e, "status", None)
    if isinstance(status, int):
        raise HTTPException(status, getattr(e, "message", str(e)))
    if isinstance(e, LookupError):
        raise HTTPException(404, str(e))
    if isinstance(e, ValueError):
        raise HTTPException(422, str(e))
    raise e


def summary_counts(items: list[dict]) -> dict:
    return {"open": len(items), "blocking": sum(1 for x in items if x["severity"] == "blocking"),
            "warning": sum(1 for x in items if x["severity"] == "warning"), "info": sum(1 for x in items if x["severity"] == "info"),
            "by_owner": {r: sum(1 for x in items if x.get("owner_role") == r and x["severity"] != "info") for r in OWNER_ROLES},
            "cases": len({x["case_key"] for x in items})}
