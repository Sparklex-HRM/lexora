"""Salary revisions — APPLY DUE + the one payroll hook.

apply_due(db, org, headers, as_of=None)
    Applies every APPROVED revision whose effective date has arrived (never a date
    that is still in the future in real time). Idempotent: each revision is locked,
    applied once, and every step it takes is recorded in `apply_result`, so a retry
    after a half-finished attempt never queues arrears or retro pay twice.

    For one revision:
      1. dated inside periods ALREADY PAID → a RetroPayProposal (earning code RETRO). No
         CPP / EI / tax is worked out here — once HR accepts it, it rides the next regular run
         as an ordinary extra earning and the run's existing `_resolve_bases` taxes it from the
         RETRO pay-component flags (CRA bonus method for non-periodic pay).
      2. the new salary + a history row are written by the employee service in ONE
         transaction (POST /employees/{id}/compensation/history).
    A date inside the CURRENT unpaid period needs nothing extra: the history row carries
    the date and the run blends old × days before + new × days from through
    shared.utils.comp.effective_salary_for_period().

before_payroll(request, db, emps, period_start, period_end, entries)
    THE hook — called once at the start of payroll preview and run. It applies due
    revisions, refreshes the employee dicts it just changed, hands
    every employee its salary history (`e["comp_history"]`, also registered for
    effective_salary_for_period), and adds accepted Canada retro lines to `entries`
    (each line carries its proposal_id; sync_retro_paid() closes the proposal once a
    saved stub shows it was paid). It never raises: a problem here is reported in the
    returned notes, payroll goes on.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select

sys.path.append("/app")
from shared.utils import comp as C  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.enums import periods_per_year  # noqa: E402

from database import SessionLocal  # noqa: E402
from models import PayRun, PayStub  # noqa: E402
from revisions_models import CompRevision, RetroPayProposal  # noqa: E402
from shared.utils.org_time import local_today  # org-TZ business date (stream S1 helper)

RETRO_CODE = "RETRO"          # the pay-component the master already seeds as "Retroactive Pay"


def _now():
    return datetime.now(timezone.utc)


def system_headers(org) -> dict:
    """The internal service identity (shared/utils/internal.py) for revisions applied on their date."""
    from shared.utils.internal import internal_headers
    return internal_headers(org, "salary-revisions", role="org_admin")


def offcycle_pg(org) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{org}:paygroup:offcycle")


async def fetch_employees(headers: dict) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=headers)
            if r.status_code == 200:
                return r.json()
    except httpx.HTTPError:
        pass
    return []


async def paid_periods(db, org, employee_id) -> list[tuple[date, date, dict]]:
    """Regular (not off-cycle) periods already paid to this employee, oldest first."""
    oid, eid = uuid.UUID(str(org)), uuid.UUID(str(employee_id))
    rows = (await db.execute(
        select(PayRun.period_start, PayRun.period_end, PayStub.detail)
        .join(PayStub, PayStub.pay_run_id == PayRun.id)
        .where(PayRun.org_id == oid, PayStub.employee_id == eid, PayRun.pay_group_id != offcycle_pg(org))
        .order_by(PayRun.period_end))).all()
    return [(r[0], r[1], r[2] or {}) for r in rows]


def retro_for(rev: CompRevision, emp: dict, periods: list) -> tuple[float, list[dict]]:
    """Canada: pay difference for the paid periods on or after the effective date."""
    ppy = periods_per_year(emp.get("pay_frequency") or "biweekly")
    spans = [(a, b) for a, b, _ in periods]
    old, new = float(rev.old_amount or 0), float(rev.new_amount or 0)
    if rev.pay_type == "hourly":
        std = float(emp.get("standard_hours_per_week") or 40)
        hours_by_end = {b: float(d.get("worked_hours") or 0) for _, b, d in periods}
        lines = C.retro_lines(rev.effective_from, spans, 0.0, 1.0, 1)      # day shares only
        for ln in lines:
            hrs = hours_by_end.get(date.fromisoformat(ln["period_end"])) or std * (C.WEEKS_PER_YEAR / ppy)
            ln["hours"] = hrs
            ln["amount"] = (new - old) * hrs * ln["days"] / ln["period_days"] if ln["period_days"] else 0.0
    else:
        lines = C.retro_lines(rev.effective_from, spans, old, new, ppy)
    for ln in lines:
        ln["amount"] = round(ln["amount"], 2)
    return round(sum(ln["amount"] for ln in lines), 2), lines


async def _handoff_canada(db, org: str, rev: CompRevision, emp: dict, periods: list, res: dict) -> None:
    amount, lines = retro_for(rev, emp, periods)
    if amount > 0:
        p = RetroPayProposal(org_id=rev.org_id, employee_id=rev.employee_id, revision_id=rev.id, code=RETRO_CODE,
                             label=f"Retro pay — salary revision effective {rev.effective_from.isoformat()}",
                             amount=amount, currency=rev.currency, lines=lines, status="proposed")
        db.add(p)
        await db.flush()
        res.update({"retro_proposal_id": str(p.id), "retro_amount": amount})
    elif amount < 0:
        res["retro_note"] = ("The new rate is lower for periods already paid. Nothing is recovered "
                             f"automatically (difference {amount:.2f}).")


async def apply_one(db, org: str, rev: CompRevision, emp: dict, headers: dict, today: date) -> bool:
    res = dict(rev.apply_result or {})
    periods = await paid_periods(db, org, rev.employee_id)
    last_end = max((b for _, b, _ in periods), default=None)
    timing = C.revision_timing(rev.effective_from, last_end, today)
    res["timing"] = timing
    if timing == "backdated" and not res.get("handoff_done"):
        await _handoff_canada(db, org, rev, emp, periods, res)
        res["handoff_done"] = True
        rev.apply_result = dict(res)
        await db.commit()                      # the hand-off is now on record — never repeated
    new_components = rev.new_components or {}
    payload = {"effective_from": rev.effective_from.isoformat(), "components": new_components,
               "reason": rev.reason, "note": rev.note, "source": "revision", "source_revision_id": str(rev.id),
               "created_by": rev.created_by, "approved_by": rev.decided_by, "apply_to_employee": True}
    payload["hourly_rate" if rev.pay_type == "hourly" else "annual_amount"] = float(rev.new_amount)
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{settings.EMPLOYEE_URL}/employees/{rev.employee_id}/compensation/history",
                             json=payload, headers=headers)
    except httpx.HTTPError as ex:
        res["error"] = f"employee service not reachable ({type(ex).__name__})"
        rev.apply_result = dict(res)
        await db.commit()
        return False
    if r.status_code not in (200, 201):
        res["error"] = f"employee service answered {r.status_code}"
        rev.apply_result = dict(res)
        await db.commit()
        return False
    res.pop("error", None)
    body = r.json() or {}
    res["history_id"] = body.get("id")
    res["live_record_updated"] = bool(body.get("applied_to_employee", body.get("already_recorded")))
    rev.new_components = new_components
    rev.status, rev.applied_at, rev.apply_result = "applied", _now(), dict(res)
    await db.commit()
    return True


async def apply_due(db, org: str, headers: dict, *, as_of: date | None = None,
                    employees: list[dict] | None = None) -> dict:
    today = local_today()
    cutoff = min(as_of or today, today)
    oid = uuid.UUID(str(org))
    base = select(CompRevision).where(CompRevision.org_id == oid, CompRevision.status == "approved",
                                      CompRevision.effective_from <= cutoff)
    ids = [r[0] for r in (await db.execute(base.with_only_columns(CompRevision.id)
                                           .order_by(CompRevision.effective_from, CompRevision.created_at))).all()]
    out = {"applied": 0, "failed": 0, "employee_ids": []}
    if not ids:
        return out
    headers = {k: v for k, v in headers.items() if k.lower() != "x-branch-id"}   # revisions are org-wide
    emps = {e["id"]: e for e in (await fetch_employees(headers))} or {e["id"]: e for e in (employees or [])}
    for rid in ids:
        rev = (await db.execute(select(CompRevision).where(CompRevision.id == rid, CompRevision.status == "approved")
                                .with_for_update(skip_locked=True))).scalar_one_or_none()
        if not rev:
            continue                              # another request is applying it, or it is done
        emp = emps.get(str(rev.employee_id))
        if not emp:
            rev.apply_result = {**(rev.apply_result or {}), "error": "employee not found"}
            await db.commit()
            out["failed"] += 1
            continue
        try:
            ok = await apply_one(db, org, rev, emp, headers, today)
        except Exception as ex:  # noqa: BLE001 — one bad revision must not block the others
            await db.rollback()
            ok = False
            print(f"[payroll] revision {rid} apply failed: {type(ex).__name__}: {ex}", flush=True)
        if ok:
            out["applied"] += 1
            out["employee_ids"].append(str(rev.employee_id))
            # the next revision of the same employee must see the salary just written
            if rev.pay_type == "hourly":
                emp["hourly_rate"] = float(rev.new_amount)
            else:
                emp["annual_salary"] = float(rev.new_amount)
        else:
            out["failed"] += 1
    return out


async def sync_retro_paid(db, org) -> int:
    """Close accepted retro proposals that a saved pay stub already carries. The run
    stores every entry line (with our proposal_id) on the stub, so 'paid' is read back
    from what was really paid — a proposal can never ride two runs, and one whose
    employee was left out of a run stays open for the next."""
    oid = uuid.UUID(str(org))
    props = (await db.execute(select(RetroPayProposal).where(
        RetroPayProposal.org_id == oid, RetroPayProposal.status == "accepted"))).scalars().all()
    n = 0
    for p in props:
        stubs = (await db.execute(
            select(PayStub.detail, PayRun.period_end).join(PayRun, PayRun.id == PayStub.pay_run_id)
            .where(PayStub.org_id == oid, PayStub.employee_id == p.employee_id,
                   PayRun.created_at >= p.created_at))).all()
        for detail, p_end in stubs:
            lines = ((detail or {}).get("entries") or {}).get("earnings") or []
            if any(str(it.get("proposal_id") or "") == str(p.id) for it in lines):
                p.status, p.paid_period_end = "paid", p_end
                n += 1
                break
    if n:
        await db.commit()
    return n


async def before_payroll(request, db, emps: list[dict], period_start: date | None, period_end: date | None,
                         entries: dict | None = None) -> tuple[list[dict], dict | None, list[str]]:
    """THE payroll hook (see module docstring). Returns (employees, entries, notes)."""
    notes: list[str] = []
    try:
        org = request.headers.get("x-org-id")
        headers = {k: request.headers.get(k, "") for k in
                   ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-branch-id") if request.headers.get(k)}
        res = await apply_due(db, org, headers, as_of=period_end, employees=emps)
        if res["applied"]:
            notes.append(f"{res['applied']} salary revision(s) took effect and were applied before this run.")
            fresh = {e["id"]: e for e in await fetch_employees(headers)}
            emps = [({**e, "annual_salary": fresh[e["id"]].get("annual_salary"),
                      "hourly_rate": fresh[e["id"]].get("hourly_rate")} if e["id"] in fresh else e) for e in emps]
        if res["failed"]:
            notes.append(f"{res['failed']} approved salary revision(s) could not be applied — see Salary revisions.")
        history = await C.fetch_history(settings.EMPLOYEE_URL, headers)
        if period_end:
            # approved revisions that fall inside the period but are not applied yet (a
            # period that ends after today): shown to the run as salary slices only
            pend = (await db.execute(select(CompRevision).where(
                CompRevision.org_id == uuid.UUID(str(org)), CompRevision.status == "approved",
                CompRevision.effective_from <= period_end))).scalars().all()
            for r in pend:
                row = {"effective_from": r.effective_from.isoformat(), "components": r.new_components or {},
                       "pay_type": r.pay_type, "source_revision_id": str(r.id), "created_at": "9"}
                row["hourly_rate" if r.pay_type == "hourly" else "annual_amount"] = float(r.new_amount)
                history.setdefault(str(r.employee_id), []).append(row)
        C.register_history(history)
        emps = [{**e, "comp_history": history.get(e["id"], [])} for e in emps]
        # accepted retro-pay proposals ride this run as an extra RETRO earning
        ca_ids = [uuid.UUID(e["id"]) for e in emps]
        if ca_ids:
            await sync_retro_paid(db, org)       # anything a finished run already paid is closed first
            props = (await db.execute(select(RetroPayProposal).where(
                RetroPayProposal.org_id == uuid.UUID(str(org)), RetroPayProposal.status == "accepted",
                RetroPayProposal.employee_id.in_(ca_ids)))).scalars().all()
            if props:
                entries = {k: {kk: list(vv) if isinstance(vv, list) else vv for kk, vv in (v or {}).items()}
                           for k, v in (entries or {}).items()}
                for p in props:
                    slot = entries.setdefault(str(p.employee_id), {"earnings": [], "deductions": []})
                    slot.setdefault("earnings", []).append({"code": p.code, "amount": float(p.amount),
                                                            "label": p.label, "source": "retro_proposal",
                                                            "proposal_id": str(p.id)})
                notes.append(f"{len(props)} retro-pay line(s) from back-dated salary revisions are included (code {RETRO_CODE}).")
    except Exception as ex:  # noqa: BLE001 — never stop payroll
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        notes.append(f"Salary revisions could not be checked ({type(ex).__name__}). Pay uses the salary on record.")
        print(f"[payroll] before_payroll hook failed: {type(ex).__name__}: {ex}", flush=True)
    return emps, entries, notes


# ── boot + tick ───────────────────────────────────────────────────────────────
async def revisions_tick() -> dict:
    """Apply due revisions for every org that has one. Safe to run any number of times."""
    out = {"orgs": 0, "applied": 0, "failed": 0}
    async with SessionLocal() as db:
        orgs = [r[0] for r in (await db.execute(select(CompRevision.org_id).where(
            CompRevision.status == "approved", CompRevision.effective_from <= local_today()).distinct())).all()]
        for org in orgs:
            res = await apply_due(db, str(org), system_headers(org))
            out["orgs"] += 1
            out["applied"] += res["applied"]
            out["failed"] += res["failed"]
    return out


async def _tick_loop():
    hours = float(os.getenv("LEXORA_LIFECYCLE_TICK_HOURS", "6"))
    await asyncio.sleep(60)                       # let the other services come up first
    while True:
        try:
            res = await revisions_tick()
            if res["orgs"]:
                print(f"[payroll] salary-revision tick: {res}", flush=True)
        except Exception as ex:  # noqa: BLE001 — the tick must never take the service down
            print(f"[payroll] salary-revision tick failed: {type(ex).__name__}: {ex}", flush=True)
        await asyncio.sleep(max(hours, 0.1) * 3600)


async def revisions_init() -> None:
    """Called once at payroll start: begin the daily tick (off with LEXORA_LIFECYCLE_TICK=0)."""
    if os.getenv("LEXORA_LIFECYCLE_TICK", "1").strip().lower() not in ("0", "false", "no", "off"):
        asyncio.create_task(_tick_loop())
