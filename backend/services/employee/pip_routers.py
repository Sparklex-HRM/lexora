"""Performance G4 — improvement plans (/performance/pips). CONFIDENTIAL.

Readers: the employee (once the plan is started), the employee's manager on the plan, HR.
Nobody else — a request from anyone else answers 404, as if the plan did not exist.

  GET    /performance/pips                      plans I may see
  POST   /performance/pips                      manager (own direct report) or HR: a draft plan
  GET    /performance/pips/{id}                 plan + goals + support + check-ins (+ audit for manager / HR)
  PATCH  /performance/pips/{id}                 manager / HR: wording, dates, check-in rhythm, HR owner
  POST   /performance/pips/{id}/items           goal or support line
  PATCH  /performance/pips/items/{item_id}      change a line / mark met, not met, done
  DELETE /performance/pips/items/{item_id}
  POST   /performance/pips/{id}/start           HR: draft → active (check-in dates generated, employee told)
  POST   /performance/pips/{id}/checkins/{cid}  manager / HR: record a check-in
  POST   /performance/pips/{id}/ack             employee: acknowledged | disagreed (+ comment)
  POST   /performance/pips/{id}/outcome         HR: successful | extended (new end date) | exit — with a reason
  POST   /performance/pips/{id}/cancel          HR: with a reason
  POST   /performance/pips/{id}/exit-link       HR: record the exit case HR started by hand (outcome = exit)

An 'exit' outcome NEVER ends employment: it records the recommendation; HR opens an exit case
from the Exits screen themselves (the screen offers the button), and links it here.
"""
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402  (plan year = org-TZ year)

import hiring_integrations as ext  # noqa: E402
import perf_g4_logic as G  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import cas, emp_name, fail, get_actor, now, one, opt_uid, ser, to_date, uid  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from perf_g4_models import PerfPip, PerfPipAudit, PerfPipCheckin, PerfPipItem  # noqa: E402

router = APIRouter(prefix="/performance/pips", tags=["performance-pip"])


def _scope(actor: Actor, p: PerfPip) -> str | None:
    return G.pip_scope(actor, {"employee_id": str(p.employee_id), "manager_id": str(p.manager_id) if p.manager_id else None,
                               "status": p.status})


async def _plan(db, actor: Actor, pip_id: str, action: str | None = None) -> tuple[PerfPip, str]:
    p = await one(db, PerfPip, pip_id, actor.org, "Plan")
    sc = _scope(actor, p)
    if sc is None:
        raise HTTPException(404, "Plan not found")            # confidential: it does not exist for you
    if action and not G.pip_can(action, sc, ser(p)):
        raise HTTPException(403 if sc != "self" else 403, "You cannot do this on this plan now.")
    return p, sc


async def _audit(db, p: PerfPip, actor: Actor, action: str, detail: str = ""):
    db.add(PerfPipAudit(org_id=p.org_id, pip_id=p.id, action=action, detail=(detail or "")[:600], by_user=actor.user_id,
                        by_name=actor.name, by_role=actor.role))


def _list_row(p: PerfPip, sc: str) -> dict:
    d = ser(p)
    d.update({"scope": sc, "outcome_label": G.OUTCOME_LABEL.get(p.outcome or "", None)})
    if sc == "self":
        d.pop("created_by", None)
    return d


@router.get("")
async def list_pips(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    q = select(PerfPip).where(PerfPip.org_id == actor.org)
    if not actor.is_hr:
        if not actor.employee_id:
            return {"items": [], "is_hr": False, "is_manager": False}
        me = uid(actor.employee_id)
        q = q.where((PerfPip.employee_id == me) | (PerfPip.manager_id == me))
    rows = (await db.execute(q.order_by(PerfPip.created_at.desc()))).scalars().all()
    items = []
    for p in rows:
        sc = _scope(actor, p)
        if sc:
            items.append(_list_row(p, sc))
    team = False
    if actor.employee_id:
        team = (await db.execute(select(Employee.id).where(Employee.org_id == actor.org, Employee.manager_id == uid(actor.employee_id))
                                 .limit(1))).first() is not None
    return {"items": items, "is_hr": actor.is_hr, "is_manager": team}


def _fill(p: PerfPip, body: dict):
    if "reason" in body:
        p.reason = str(body.get("reason") or "").strip()[:4000]
    if not (p.reason or "").strip():
        raise HTTPException(422, "Write down why the plan is needed: the facts and examples, not labels.")
    if "expectations" in body:
        p.expectations = str(body.get("expectations") or "").strip()[:4000] or None
    if "start_date" in body:
        p.start_date = to_date(body.get("start_date"))
    if "end_date" in body:
        p.end_date = to_date(body.get("end_date"))
    if "checkin_every_days" in body:
        try:
            v = int(body.get("checkin_every_days") or 14)
        except (TypeError, ValueError):
            raise HTTPException(422, "Check-in every N days must be a whole number.")
        if not 7 <= v <= 60:
            raise HTTPException(422, "Check-ins happen every 7 to 60 days.")
        p.checkin_every_days = v
    try:
        G.pip_dates(p.start_date, p.end_date)
    except RuleError as e:
        fail(e)


def _clean_item(body: dict) -> dict:
    kind = body.get("kind") or "goal"
    if kind not in ("goal", "support"):
        raise HTTPException(422, "A line is a goal or a support action.")
    title = str(body.get("title") or "").strip()[:300]
    if not title:
        raise HTTPException(422, "Describe the goal or the support action.")
    return {"kind": kind, "title": title, "measure": (str(body.get("measure") or "").strip()[:300] or None),
            "owner": (str(body.get("owner") or "").strip()[:200] or None), "due_date": to_date(body.get("due_date"))}


@router.post("", status_code=201)
async def create_pip(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    e = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == uid(body.get("employee_id"), "Employee")))).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, "Employee not found")
    if actor.employee_id and str(e.id) == actor.employee_id:
        raise HTTPException(403, "You cannot open an improvement plan for yourself.")
    is_mgr = bool(actor.employee_id) and str(e.manager_id or "") == actor.employee_id
    if not (is_mgr or actor.is_hr):
        raise HTTPException(403, "Only the employee's manager or HR can open an improvement plan.")
    m = (await db.execute(select(Employee).where(Employee.org_id == actor.org, Employee.id == e.manager_id))).scalar_one_or_none() if e.manager_id else None
    existing = [x for (x,) in (await db.execute(select(PerfPip.plan_number).where(PerfPip.org_id == actor.org))).all()]
    live = (await db.execute(select(PerfPip.id).where(PerfPip.org_id == actor.org, PerfPip.employee_id == e.id,
                                                       PerfPip.status.in_(("draft", "active", "extended"))))).first()
    if live:
        raise HTTPException(409, "This employee already has an open improvement plan.")
    p = PerfPip(org_id=uid(actor.org), plan_number=G.next_pip_number(existing, (await org_today(actor.org)).year), employee_id=e.id,
                employee_name=emp_name(e), employee_number=e.employee_number, manager_id=m.id if m else None,
                manager_name=emp_name(m) if m else None, reason="", status="draft", checkin_every_days=14, extensions=[],
                hr_owner_user_id=actor.user_id if actor.is_hr else None, hr_owner_name=actor.name if actor.is_hr else None,
                created_by=actor.user_id)
    _fill(p, body)
    if body.get("hr_owner_name") and actor.is_hr:
        p.hr_owner_name = str(body["hr_owner_name"]).strip()[:200]
    db.add(p)
    await db.flush()
    for it in body.get("items") or []:
        db.add(PerfPipItem(org_id=p.org_id, pip_id=p.id, **_clean_item(it)))
    await _audit(db, p, actor, "create", f"Draft plan for {p.employee_name}, {p.start_date} to {p.end_date}")
    await db.commit()
    await record_audit(request, "performance.pip.create", entity_type="perf_pip", entity_id=str(p.id),
                       summary=f"Improvement plan {p.plan_number} drafted", meta={"plan": p.plan_number})
    if not actor.is_hr:
        await ext.notify(actor.org, roles=["hr_admin"], title=f"Improvement plan {p.plan_number} waiting for HR to start",
                         body="A manager drafted an improvement plan. HR reviews and starts it.", link=f"/performance/improvement-plans?id={p.id}")
    return _list_row(p, _scope(actor, p) or "hr")


@router.get("/{pip_id}")
async def get_pip(pip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, sc = await _plan(db, actor, pip_id)
    items = (await db.execute(select(PerfPipItem).where(PerfPipItem.pip_id == p.id).order_by(PerfPipItem.kind, PerfPipItem.created_at))).scalars().all()
    cks = (await db.execute(select(PerfPipCheckin).where(PerfPipCheckin.pip_id == p.id).order_by(PerfPipCheckin.due_on))).scalars().all()
    out = _list_row(p, sc)
    out.update({"items": [ser(i) for i in items], "checkins": [ser(c) for c in cks],
                "can": {a: G.pip_can(a, sc, ser(p)) for a in ("edit", "start", "checkin", "extend", "close", "cancel", "ack", "link_exit")}})
    if sc in ("manager", "hr"):
        audit = (await db.execute(select(PerfPipAudit).where(PerfPipAudit.pip_id == p.id).order_by(PerfPipAudit.at))).scalars().all()
        out["audit"] = [ser(a) for a in audit]
    return out


@router.patch("/{pip_id}")
async def update_pip(pip_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, sc = await _plan(db, actor, pip_id, "edit")
    if p.status != "draft" and any(k in body for k in ("start_date", "end_date")):
        raise HTTPException(409, "Dates of a started plan change only through an extension (Outcome: extended).")
    _fill(p, body)
    if "hr_owner_name" in body and sc == "hr":
        p.hr_owner_name = str(body.get("hr_owner_name") or "").strip()[:200] or None
    await _audit(db, p, actor, "edit", ", ".join(sorted(body))[:300])
    await db.commit()
    return await get_pip(pip_id, request, db)


@router.post("/{pip_id}/items", status_code=201)
async def add_item(pip_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, _ = await _plan(db, actor, pip_id, "edit")
    it = PerfPipItem(org_id=p.org_id, pip_id=p.id, **_clean_item(body))
    db.add(it)
    await _audit(db, p, actor, "item.add", f"{it.kind}: {it.title}")
    await db.commit()
    return ser(it)


@router.patch("/items/{item_id}")
async def update_item(item_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    it = await one(db, PerfPipItem, item_id, actor.org, "Plan line")
    p, _ = await _plan(db, actor, str(it.pip_id), "edit")
    if any(k in body for k in ("title", "kind", "measure", "owner", "due_date")):
        clean = _clean_item({**ser(it), **body})
        for k, v in clean.items():
            setattr(it, k, v)
    if "status" in body:
        allowed = ("open", "met", "not_met") if it.kind == "goal" else ("open", "done")
        if body["status"] not in allowed:
            raise HTTPException(422, "Status must be one of: " + ", ".join(allowed))
        it.status = body["status"]
    if "note" in body:
        it.note = str(body.get("note") or "").strip()[:400] or None
    await _audit(db, p, actor, "item.change", f"{it.kind}: {it.title} → {it.status}")
    await db.commit()
    return ser(it)


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    it = await one(db, PerfPipItem, item_id, actor.org, "Plan line")
    p, _ = await _plan(db, actor, str(it.pip_id), "edit")
    if p.status != "draft":
        raise HTTPException(409, "Lines of a started plan are not deleted; mark them instead, so the record stays complete.")
    await _audit(db, p, actor, "item.delete", f"{it.kind}: {it.title}")
    await db.delete(it)
    await db.commit()
    return {"deleted": item_id}


@router.post("/{pip_id}/start")
async def start_pip(pip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, _ = await _plan(db, actor, pip_id, "start")
    items = (await db.execute(select(PerfPipItem).where(PerfPipItem.pip_id == p.id))).scalars().all()
    if not any(i.kind == "goal" for i in items):
        raise HTTPException(409, "Add at least one goal (what must improve and how it is measured) before starting.")
    if not any(i.kind == "support" for i in items):
        raise HTTPException(409, "Add at least one support action (training, mentoring, tools) before starting.")
    await cas(db, PerfPip, p, "status", "draft", status="active", started_at=now(),
              hr_owner_user_id=p.hr_owner_user_id or actor.user_id, hr_owner_name=p.hr_owner_name or actor.name)
    for d in G.checkin_schedule(p.start_date, p.end_date, p.checkin_every_days):
        db.add(PerfPipCheckin(org_id=p.org_id, pip_id=p.id, due_on=d))
    await _audit(db, p, actor, "start", f"Started; check-ins every {p.checkin_every_days} days")
    await db.commit()
    users = await ext.user_ids_for_employees(db, actor.org, [p.employee_id] + ([p.manager_id] if p.manager_id else []))
    await ext.notify(actor.org, user_ids=users, title=f"Improvement plan {p.plan_number} has started",
                     body="Please read the plan and acknowledge it. Only you, your manager and HR can see it.",
                     link=f"/performance/improvement-plans?id={p.id}", kind="info")
    await record_audit(request, "performance.pip.start", entity_type="perf_pip", entity_id=str(p.id), summary=f"Plan {p.plan_number} started")
    return await get_pip(pip_id, request, db)


@router.post("/{pip_id}/checkins/{checkin_id}")
async def record_checkin(pip_id: str, checkin_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, _ = await _plan(db, actor, pip_id, "checkin")
    c = await one(db, PerfPipCheckin, checkin_id, actor.org, "Check-in")
    if c.pip_id != p.id:
        raise HTTPException(404, "Check-in not found")
    if body.get("rating") not in G.CHECKIN_RATINGS:
        raise HTTPException(422, "Pick how it is going: on track, some progress or off track.")
    notes = str(body.get("notes") or "").strip()[:4000]
    if not notes:
        raise HTTPException(422, "Write what was discussed and agreed.")
    c.held_on = to_date(body.get("held_on")) or now().date()
    c.rating, c.notes, c.by_user, c.by_name, c.at = body["rating"], notes, actor.user_id, actor.name, now()
    await _audit(db, p, actor, "checkin", f"{c.due_on}: {c.rating}")
    await db.commit()
    return ser(c)


@router.post("/{pip_id}/ack")
async def acknowledge(pip_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, _ = await _plan(db, actor, pip_id, "ack")
    st = body.get("status")
    if st not in ("acknowledged", "disagreed"):
        raise HTTPException(422, "Choose 'I have read this plan' or 'I disagree'.")
    comment = str(body.get("comment") or "").strip()[:4000] or None
    if st == "disagreed" and not comment:
        raise HTTPException(422, "Say what you disagree with; HR reads it.")
    p.ack_status, p.ack_comment, p.ack_at = st, comment, now()
    await _audit(db, p, actor, "ack", st)
    await db.commit()
    if st == "disagreed":
        await ext.notify(actor.org, roles=["hr_admin"], title=f"{p.employee_name} disagrees with plan {p.plan_number}",
                         link=f"/performance/improvement-plans?id={p.id}", kind="warning")
    return await get_pip(pip_id, request, db)


@router.post("/{pip_id}/outcome")
async def outcome(pip_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    oc = body.get("outcome")
    p, _ = await _plan(db, actor, pip_id, "extend" if oc == "extended" else "close")
    items = (await db.execute(select(PerfPipItem).where(PerfPipItem.pip_id == p.id))).scalars().all()
    note = str(body.get("note") or "").strip()[:4000]
    try:
        change = G.decide_outcome(ser(p) | {"start_date": p.start_date, "end_date": p.end_date}, oc, note,
                                  to_date(body.get("new_end_date")), [ser(i) for i in items])
    except RuleError as e:
        fail(e)
    old_status = p.status
    if oc == "extended":
        ext_row = {"from": p.end_date.isoformat(), "to": change["end_date"].isoformat(), "reason": note, "by": actor.name, "at": now().isoformat()}
        await cas(db, PerfPip, p, "status", old_status, status="extended", end_date=change["end_date"],
                  extensions=[*(p.extensions or []), ext_row])
        last = max([c.due_on for c in (await db.execute(select(PerfPipCheckin).where(PerfPipCheckin.pip_id == p.id))).scalars().all()] or [p.start_date])
        for d in G.checkin_schedule(last, change["end_date"], p.checkin_every_days):
            db.add(PerfPipCheckin(org_id=p.org_id, pip_id=p.id, due_on=d))
        await _audit(db, p, actor, "extend", f"End date {ext_row['from']} → {ext_row['to']}: {note}")
    else:
        await cas(db, PerfPip, p, "status", old_status, status="closed", outcome=oc, outcome_note=note, outcome_at=now(), outcome_by=actor.user_id)
        await _audit(db, p, actor, "outcome", f"{G.OUTCOME_LABEL[oc]}: {note}")
    await db.commit()
    users = await ext.user_ids_for_employees(db, actor.org, [p.employee_id] + ([p.manager_id] if p.manager_id else []))
    await ext.notify(actor.org, user_ids=users, title=f"Improvement plan {p.plan_number}: {G.OUTCOME_LABEL[oc]}",
                     link=f"/performance/improvement-plans?id={p.id}", kind="info")
    await record_audit(request, "performance.pip.outcome", entity_type="perf_pip", entity_id=str(p.id),
                       summary=f"Plan {p.plan_number}: {oc}", meta={"outcome": oc})
    res = await get_pip(pip_id, request, db)
    if oc == "exit":
        res["next_step"] = ("Recorded as not successful. Employment does NOT end automatically: if the company decides to "
                            "separate, HR starts an exit case from Exits (Start exit) and links it to this plan.")
    return res


@router.post("/{pip_id}/cancel")
async def cancel(pip_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, _ = await _plan(db, actor, pip_id, "cancel")
    reason = str(body.get("reason") or "").strip()[:600]
    if not reason:
        raise HTTPException(422, "Give the reason for cancelling.")
    await cas(db, PerfPip, p, "status", p.status, status="cancelled")
    await _audit(db, p, actor, "cancel", reason)
    await db.commit()
    return await get_pip(pip_id, request, db)


@router.post("/{pip_id}/exit-link")
async def exit_link(pip_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    p, _ = await _plan(db, actor, pip_id, "link_exit")
    case_id = str(opt_uid(body.get("exit_case_id")) or "")
    if not case_id:
        raise HTTPException(422, "Pick the exit case HR started.")
    p.exit_case_id = case_id
    await _audit(db, p, actor, "exit.link", f"Exit case {case_id} started by HR")
    await db.commit()
    return await get_pip(pip_id, request, db)
