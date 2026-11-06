"""`/reports/builder` — datasets, run, export, saved reports, schedules.

Scope: org admin / HR / payroll see the whole company; a manager sees direct reports;
nobody else. Money columns only for payroll / finance / org admin (they are not even
listed for anyone else). The client sends a spec, never SQL.
"""
import logging
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, text

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.notify import notify, smtp_status  # noqa: E402
from shared.utils.org_time import org_tz_name, valid_timezone  # noqa: E402
from shared.utils.roles import request_role  # noqa: E402

import report_builder as rb  # noqa: E402
from report_builder_models import ReportSchedule, SavedReport  # noqa: E402

router = APIRouter(prefix="/reports/builder", tags=["report-builder"])


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _who(request: Request) -> dict:
    role = request.headers.get("x-role", "") or ""
    if role.strip().lower() == "accountant":
        # The accountant is a finance reader (+ a names-only directory). The "finance" family would fold
        # them onto a whole-company, pay-visible builder — no screen of theirs opens it. WASA 2026-10-21.
        raise HTTPException(403, "The report builder is for HR, payroll, org admins and managers")
    # fold role families onto the names the builder knows
    if directory.role_in(role, ["hr"]):
        role_key = "hr"
    elif directory.role_in(role, ["payroll"]):
        role_key = "payroll"
    elif directory.role_in(role, ["finance"]):
        role_key = "finance"
    else:
        role_key = role
    if not (rb.is_office(role_key) or role_key == "manager"):
        raise HTTPException(403, "The report builder is for HR, payroll, finance, org admins and managers")
    # org RBAC 2026-10-26: the folded role carries the acting custom role's data classes (rb.can_see_pay)
    role_key = request_role(request, role_key)
    return {"role": role_key, "raw_role": role, "user_id": request.headers.get("x-user-id") or "",
            "email": request.headers.get("x-user-email") or "", "employee_id": request.headers.get("x-employee-id") or None}


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "hex") and not isinstance(v, (str, bytes)):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


async def custom_defs_for(org: str, role: str, raw_role: str | None = None) -> list[dict]:
    """D1: the org's active custom-field definitions this role may SEE (cf_definitions is
    owned by the employee service; an installation without it simply has none)."""
    from shared.utils.roles import has_role
    try:
        async with pm.session() as s:
            rows = (await s.execute(text(
                "SELECT entity, key, label, type, visible_to FROM cf_definitions "
                "WHERE org_id = CAST(:org AS uuid) AND active IS TRUE ORDER BY entity, sort_order, label"),
                {"org": org})).mappings().all()
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    for r in rows:
        vis = r["visible_to"] or []
        if isinstance(vis, str):
            import json
            try:
                vis = json.loads(vis)
            except ValueError:
                vis = []
        if vis and not has_role(raw_role or role, vis):
            continue
        out.append({"entity": r["entity"], "key": r["key"], "label": r["label"], "type": r["type"], "active": True})
    return out


async def _run(spec: dict, role: str, org: str, me: str | None, raw_role: str | None = None) -> dict:
    try:
        q = rb.build_query(spec, role=role, org=org, me=me, custom_defs=await custom_defs_for(org, role, raw_role),
                           tz=await org_tz_name(org))
    except rb.SpecError as e:
        raise HTTPException(422, str(e))
    try:
        async with pm.session() as s:
            res = (await s.execute(text(q["sql"]), q["params"])).mappings().all()
    except Exception:                                        # noqa: BLE001
        ref = uuid.uuid4().hex[:8].upper()                   # SQL / driver text → log only, never the answer
        logging.getLogger("lexora.report_builder").exception("report run failed (ref %s)", ref)
        raise HTTPException(503, f"That dataset is not available on this installation yet. (ref {ref})")
    rows = [{k: _plain(v) for k, v in r.items()} for r in res]
    return {"columns": q["columns"], "rows": rows, "total": len(rows), "truncated": len(rows) >= q["limit"], "limit": q["limit"],
            "grouped": q["grouped"], "as_of": pm.iso(pm.now_utc())}


@router.get("/datasets")
async def datasets(request: Request):
    org = _org(request)
    w = _who(request)
    return {"datasets": rb.catalog(w["role"], await custom_defs_for(org, w["role"], w["raw_role"])),
            "scope": "Whole company" if rb.is_office(w["role"]) else "Your direct reports",
            "shows_money": rb.can_see_pay(w["role"]), "max_rows": rb.MAX_ROWS,
            "ops": [{"op": k, "label": v} for k, v in (("eq", "is"), ("ne", "is not"), ("contains", "contains"), ("starts", "starts with"),
                                                     ("gt", "after / more than"), ("gte", "on or after / at least"), ("lt", "before / less than"),
                                                     ("lte", "on or before / at most"), ("in", "is one of (comma list)"), ("between", "between (two values)"),
                                                     ("is_null", "is empty"), ("not_null", "is not empty"))],
            "aggregates": ["count", "sum", "avg", "min", "max"]}


class SpecIn(BaseModel):
    spec: dict


@router.post("/run")
async def run(body: SpecIn, request: Request):
    org, w = _org(request), _who(request)
    return await _run(body.spec, w["role"], org, w["employee_id"], w["raw_role"])


@router.post("/export")
async def export(body: SpecIn, request: Request, format: str = "csv", name: str = "report"):
    org, w = _org(request), _who(request)
    out = await _run(body.spec, w["role"], org, w["employee_id"], w["raw_role"])
    safe = "".join(ch for ch in (name or "report") if ch.isalnum() or ch in "-_ ")[:60].strip() or "report"
    if format == "xlsx":
        data = rb.to_xlsx(out["columns"], out["rows"], sheet=safe)
        return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.xlsx"'})
    return Response(content=rb.to_csv(out["columns"], out["rows"]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'})


# ══════════════════════════ saved reports ══════════════════════════════════════

def _saved_out(r: SavedReport, me: str, sched: ReportSchedule | None = None) -> dict:
    return {"id": r.id, "name": r.name, "dataset": r.dataset, "spec": r.spec or {}, "shared": bool(r.shared), "owner": r.owner_name,
            "mine": r.owner_user_id == me, "created_at": pm.iso(r.created_at), "updated_at": pm.iso(r.updated_at),
            "schedule": _sched_out(sched) if sched else None}


def _sched_out(s: ReportSchedule) -> dict:
    return {"id": s.id, "cadence": s.cadence, "hour_utc": s.hour_utc, "local_time": s.local_time, "tz": s.tz,
            "weekday": s.weekday, "day_of_month": s.day_of_month,
            "roles": s.roles or [], "user_ids": s.user_ids or [], "enabled": bool(s.enabled), "next_run_at": pm.iso(s.next_run_at),
            "last_run_at": pm.iso(s.last_run_at), "last_rows": s.last_rows, "last_status": s.last_status}


async def _visible(s, org, me) -> list[SavedReport]:
    rows = (await s.execute(select(SavedReport).where(SavedReport.org_id == org).order_by(SavedReport.updated_at.desc()))).scalars().all()
    return [r for r in rows if r.shared or r.owner_user_id == me]


@router.get("/saved")
async def saved(request: Request):
    org, w = _org(request), _who(request)
    async with pm.session() as s:
        rows = await _visible(s, org, w["user_id"])
        scheds = {x.report_id: x for x in (await s.execute(select(ReportSchedule).where(ReportSchedule.org_id == org))).scalars().all()}
    return {"items": [_saved_out(r, w["user_id"], scheds.get(r.id)) for r in rows], "smtp": smtp_status()}


class SaveIn(BaseModel):
    name: str
    spec: dict
    shared: bool = False


@router.post("/saved", status_code=201)
async def save(body: SaveIn, request: Request):
    org, w = _org(request), _who(request)
    name = (body.name or "").strip()[:160]
    if not name:
        raise HTTPException(422, "Give the report a name")
    try:
        rb.build_query(body.spec, role=w["role"], org=org, me=w["employee_id"] or "",
                       custom_defs=await custom_defs_for(org, w["role"], w["raw_role"]))
    except rb.SpecError as e:
        raise HTTPException(422, str(e))
    async with pm.session() as s:
        users = {u["id"]: u for u in await directory.org_users(org)}
        r = SavedReport(org_id=org, name=name, dataset=body.spec.get("dataset"), spec=body.spec, shared=bool(body.shared),
                        owner_user_id=w["user_id"], owner_name=directory.display_name(users.get(w["user_id"])) or w["email"],
                        owner_role=w["role"], owner_employee_id=w["employee_id"])
        s.add(r)
        await s.commit()
        return _saved_out(r, w["user_id"])


@router.put("/saved/{rid}")
async def update(rid: str, body: SaveIn, request: Request):
    org, w = _org(request), _who(request)
    async with pm.session() as s:
        r = (await s.execute(select(SavedReport).where(SavedReport.id == rid, SavedReport.org_id == org))).scalar_one_or_none()
        if not r:
            raise HTTPException(404, "Saved report not found")
        if r.owner_user_id != w["user_id"] and w["role"] != "org_admin":
            raise HTTPException(403, "Only the owner (or an org admin) can change this report")
        try:
            rb.build_query(body.spec, role=w["role"], org=org, me=w["employee_id"] or "",
                           custom_defs=await custom_defs_for(org, w["role"], w["raw_role"]))
        except rb.SpecError as e:
            raise HTTPException(422, str(e))
        r.name, r.spec, r.shared, r.dataset = (body.name or r.name).strip()[:160], body.spec, bool(body.shared), body.spec.get("dataset")
        r.updated_at = pm.now_utc()
        await s.commit()
        return _saved_out(r, w["user_id"])


@router.delete("/saved/{rid}")
async def delete(rid: str, request: Request):
    org, w = _org(request), _who(request)
    async with pm.session() as s:
        r = (await s.execute(select(SavedReport).where(SavedReport.id == rid, SavedReport.org_id == org))).scalar_one_or_none()
        if not r:
            raise HTTPException(404, "Saved report not found")
        if r.owner_user_id != w["user_id"] and w["role"] != "org_admin":
            raise HTTPException(403, "Only the owner (or an org admin) can delete this report")
        for x in (await s.execute(select(ReportSchedule).where(ReportSchedule.report_id == rid))).scalars().all():
            await s.delete(x)
        await s.delete(r)
        await s.commit()
    return {"ok": True}


class ScheduleIn(BaseModel):
    cadence: str = "weekly"
    hour_utc: int = 6
    # 2026-10-25: send time on the org's clock ("09:00"); weekday / day_of_month are then local too.
    local_time: str | None = None
    tz: str | None = None                      # blank = the org's zone (company setting locale.timezone)
    weekday: int = 0
    day_of_month: int = 1
    roles: list[str] = []
    user_ids: list[str] = []
    enabled: bool = True


@router.put("/saved/{rid}/schedule")
async def put_schedule(rid: str, body: ScheduleIn, request: Request):
    org, w = _org(request), _who(request)
    if body.cadence not in ("daily", "weekly", "monthly"):
        raise HTTPException(422, "Cadence must be daily, weekly or monthly")
    if not (body.roles or body.user_ids):
        raise HTTPException(422, "Pick at least one recipient role or person")
    async with pm.session() as s:
        r = (await s.execute(select(SavedReport).where(SavedReport.id == rid, SavedReport.org_id == org))).scalar_one_or_none()
        if not r:
            raise HTTPException(404, "Saved report not found")
        if r.owner_user_id != w["user_id"] and w["role"] != "org_admin":
            raise HTTPException(403, "Only the owner (or an org admin) can schedule this report")
        x = (await s.execute(select(ReportSchedule).where(ReportSchedule.report_id == rid))).scalar_one_or_none()
        if not x:
            x = ReportSchedule(org_id=org, report_id=rid, created_by=w["email"])
            s.add(x)
        x.cadence, x.hour_utc, x.weekday, x.day_of_month = body.cadence, body.hour_utc, body.weekday, body.day_of_month
        if body.local_time:
            if not rb.valid_local_time(body.local_time):
                raise HTTPException(422, "Send time must be HH:MM (24-hour), e.g. 09:00")
            zone = body.tz if (body.tz and valid_timezone(body.tz)) else await org_tz_name(org)
            x.local_time, x.tz = body.local_time, zone
            first = rb.next_run_local(x.cadence, x.local_time, zone, x.weekday, x.day_of_month, pm.now_utc())
            x.hour_utc = first.hour                                  # kept in step for old readers
        else:
            x.local_time, x.tz = None, None
        x.roles, x.user_ids, x.enabled = [r_ for r_ in body.roles if r_][:10], [u for u in body.user_ids if u][:50], body.enabled
        x.next_run_at = rb.schedule_next(x, pm.now_utc())
        await s.commit()
        return {**_sched_out(x), "smtp": smtp_status(),
                "note": "At each run the recipients get an in-app notification and an e-mail with the report name and a link. "
                        "The rows stay behind the login — they are not attached to the e-mail."}


@router.delete("/saved/{rid}/schedule")
async def delete_schedule(rid: str, request: Request):
    org, w = _org(request), _who(request)
    async with pm.session() as s:
        x = (await s.execute(select(ReportSchedule).where(ReportSchedule.report_id == rid, ReportSchedule.org_id == org))).scalar_one_or_none()
        if x:
            await s.delete(x)
            await s.commit()
    return {"ok": True}


@router.post("/saved/{rid}/send-now")
async def send_now(rid: str, request: Request):
    org, w = _org(request), _who(request)
    async with pm.session() as s:
        x = (await s.execute(select(ReportSchedule).where(ReportSchedule.report_id == rid, ReportSchedule.org_id == org))).scalar_one_or_none()
        if not x:
            raise HTTPException(404, "No schedule on this report")
    return await _fire(x.id)


async def _fire(schedule_id: str) -> dict:
    async with pm.session() as s:
        x = (await s.execute(select(ReportSchedule).where(ReportSchedule.id == schedule_id))).scalar_one_or_none()
        if not x:
            return {"ok": False}
        r = (await s.execute(select(SavedReport).where(SavedReport.id == x.report_id))).scalar_one_or_none()
        if not r:
            x.enabled, x.last_status = False, "The saved report was deleted."
            await s.commit()
            return {"ok": False, "status": x.last_status}
        try:
            out = await _run(r.spec or {}, r.owner_role or "hr", r.org_id, r.owner_employee_id)
            n = out["total"]
            await notify(r.org_id, roles=x.roles or None, user_ids=x.user_ids or None, kind="report",
                         title=f"Scheduled report ready: {r.name} ({n} rows)",
                         body=f"Your scheduled report '{r.name}' ran with {n} row(s). Open it from the link.",
                         link=f"/reports/builder?saved={r.id}")
            x.last_rows, x.last_status = n, ("Ran; in-app notification sent. E-mail: " +
                                             ("queued for sending" if smtp_status()["configured"] else "not configured on this installation") +
                                             " — see Notifications → Delivery log.")
        except HTTPException as e:
            x.last_status = f"Failed: {e.detail}"[:300]
        x.last_run_at = pm.now_utc()
        x.next_run_at = rb.schedule_next(x, pm.now_utc())
        await s.commit()
        return {"ok": not x.last_status.startswith("Failed"), "status": x.last_status, "next_run_at": pm.iso(x.next_run_at)}


async def run_due_schedules(limit: int = 50) -> int:
    """Called from the approvals service tick. Fires every enabled schedule that is due."""
    now = pm.now_utc()
    async with pm.session() as s:
        due = (await s.execute(select(ReportSchedule.id).where(ReportSchedule.enabled.is_(True), ReportSchedule.next_run_at <= now)
                               .limit(limit))).scalars().all()
    n = 0
    for sid in due:
        try:
            await _fire(sid)
            n += 1
        except Exception:                                    # noqa: BLE001
            pass
    return n
