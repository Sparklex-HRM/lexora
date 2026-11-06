"""Employee portal batch 3 (2026-10-16) — "Raise a request" + "My tasks".

Mounted BEFORE routers.router (``/employees/{employee_id}`` is a catch-all).

  GET    /employees/ess/request-types                 everyone: active types · ?all=1 (HR): incl. inactive
  POST   /employees/ess/request-types                 HR — audited
  PATCH  /employees/ess/request-types/{tid}           HR — audited (key never changes)
  GET    /employees/ess/requests?status=&type=&employee_id=   HR: all · manager: own + direct reports · employee: own
  POST   /employees/ess/requests                      employee raises {type_id|type_key, values} → D1 chain
  GET    /employees/ess/requests/{rid}                same visibility as the list
  GET    /employees/ess/requests/{rid}/files/{key}    download a file answer (same visibility)
  POST   /employees/ess/requests/{rid}/approve|reject   inbox callback + screen (HR or the manager, never the requester)
  POST   /employees/ess/requests/{rid}/withdraw       the requester, while pending
  GET    /employees/ess/todos · POST /employees/ess/todos · PATCH|DELETE /employees/ess/todos/{tid}   own only
  GET    /employees/ess/tasks                         to-dos + exit clearance + onboarding, merged (read-only except to-dos)

File answers are a StoredDocument id the employee uploaded through POST /employees/{id}/documents
(doc_type "request_attachment"); the document must belong to the requester.
"""
from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import approvals_client  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.docstore import StoredDocument  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import local_today  # noqa: E402

import ess_requests_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from ess_requests_models import EssRequest, EssRequestType, EssTodo  # noqa: E402
from models import Employee  # noqa: E402

log = logging.getLogger("lexora.ess_requests")
router = APIRouter(prefix="/employees/ess", tags=["employee-requests"])
HR_INBOX = ["org_admin", "hr_admin", "hr_manager", "hr"]
KIND = "employee_request"


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    try:
        uuid.UUID(org or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _me(request: Request) -> str | None:
    v = (request.headers.get("x-employee-id") or "").strip()
    try:
        return str(uuid.UUID(v)) if v else None
    except ValueError:
        return None


def _uid(request: Request) -> str | None:
    return request.headers.get("x-user-id")


def _email(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or ""


def _uuid(v, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(404, f"{what} not found")


def _require_hr(request: Request, what: str):
    if not L.is_hr(_role(request)):
        raise HTTPException(403, f"Only HR can {what}")


def _iso(v):
    return v.isoformat() if v is not None else None


# ══════════════════════════ request types ═════════════════════════════════════

def _type_out(t: EssRequestType) -> dict:
    return {"id": str(t.id), "key": t.key, "name": t.name, "description": t.description, "category": t.category,
            "handler_note": t.handler_note, "fields": t.fields or [], "active": bool(t.active),
            "sort_order": t.sort_order, "updated_by": t.updated_by, "updated_at": _iso(t.updated_at)}


@router.get("/request-types")
async def list_types(request: Request, all: int = 0, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    q = select(EssRequestType).where(EssRequestType.org_id == uuid.UUID(org))
    rows = (await db.execute(q.order_by(EssRequestType.sort_order, EssRequestType.name))).scalars().all()
    show_all = bool(all) and L.is_hr(_role(request))
    return [_type_out(t) for t in rows if show_all or t.active]


@router.post("/request-types", status_code=201)
async def create_type(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "define request types")
    org = _org(request)
    try:
        d = L.normalise_type(body or {})
    except L.RequestError as e:
        raise HTTPException(422, str(e))
    dup = (await db.execute(select(EssRequestType).where(EssRequestType.org_id == uuid.UUID(org),
                                                         EssRequestType.key == d["key"]))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, f"A request type with the key '{d['key']}' already exists ({dup.name})")
    t = EssRequestType(org_id=uuid.UUID(org), created_by=_email(request), updated_by=_email(request), **d)
    db.add(t)
    await db.commit()
    await record_audit(request, "ess.request_type.create", entity_type="ess_request_type", entity_id=str(t.id),
                       summary=f"Request type created: {t.name}", meta={"key": t.key, "fields": len(t.fields or [])})
    return _type_out(t)


@router.patch("/request-types/{tid}")
async def update_type(tid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "change request types")
    org = _org(request)
    t = (await db.execute(select(EssRequestType).where(EssRequestType.id == _uuid(tid, "Request type"),
                                                       EssRequestType.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Request type not found")
    merged = {**_type_out(t), **(body or {})}
    try:
        d = L.normalise_type(merged, existing_key=t.key)
    except L.RequestError as e:
        raise HTTPException(422, str(e))
    changed = [k for k, v in d.items() if getattr(t, k) != v]
    for k, v in d.items():
        setattr(t, k, v)
    t.updated_by, t.updated_at = _email(request), _now()
    await db.commit()
    await record_audit(request, "ess.request_type.update", entity_type="ess_request_type", entity_id=str(t.id),
                       summary=f"Request type changed: {t.name}", meta={"changed": changed})
    return _type_out(t)


# ══════════════════════════ requests ══════════════════════════════════════════

def _ref(r: EssRequest) -> str:
    return "REQ-" + str(r.id).replace("-", "")[:8].upper()


def _req_out(r: EssRequest, request: Request | None = None) -> dict:
    fields = r.fields_snapshot or []
    out = {"id": str(r.id), "ref": _ref(r), "type_id": str(r.type_id), "type_key": r.type_key, "type_name": r.type_name,
           "category": r.category, "employee_id": str(r.employee_id), "employee_name": r.employee_name,
           "employee_number": r.employee_number, "department": r.department,
           "manager_id": str(r.manager_id) if r.manager_id else None, "values": r.answers or {},
           "fields": fields, "rows": L.display_rows(fields, r.answers),
           "summary": " · ".join(L.summary_lines(fields, r.answers, limit=3)), "status": r.status,
           "decided_by_name": r.decided_by_name, "decided_at": _iso(r.decided_at), "decision_note": r.decision_note,
           "created_at": _iso(r.created_at)}
    if request is not None:
        role, me = _role(request), _me(request)
        mid = out["manager_id"]
        out["can_decide"] = r.status == "pending" and L.may_decide(
            role, me, out["employee_id"], mid, actor_user_id=_uid(request), requester_user_id=r.requester_user_id)
        out["can_withdraw"] = L.may_withdraw(me, out["employee_id"], r.status)[0]
    return out


async def _req_or_404(db, org: str, rid: str) -> EssRequest:
    r = (await db.execute(select(EssRequest).where(EssRequest.id == _uuid(rid, "Request"),
                                                   EssRequest.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Request not found")
    return r


def _visible_or_404(request: Request, r: EssRequest):
    if not L.may_view(_role(request), _me(request), str(r.employee_id), str(r.manager_id) if r.manager_id else None):
        raise HTTPException(404, "Request not found")


@router.get("/requests")
async def list_requests(request: Request, status: str | None = None, type: str | None = None,
                        employee_id: str | None = None, db: AsyncSession = Depends(get_db)):
    org, role, me = _org(request), _role(request), _me(request)
    scope = L.list_scope(role, me)
    if scope == "none":
        return []
    q = select(EssRequest).where(EssRequest.org_id == uuid.UUID(org))
    if status:
        q = q.where(EssRequest.status == status)
    if type:
        try:
            q = q.where(EssRequest.type_id == uuid.UUID(type))
        except ValueError:
            q = q.where(EssRequest.type_key == type)
    if employee_id:
        q = q.where(EssRequest.employee_id == _uuid(employee_id, "Employee"))
    if scope == "team":
        q = q.where((EssRequest.employee_id == uuid.UUID(me)) | (EssRequest.manager_id == uuid.UUID(me)))
    elif scope == "self":
        q = q.where(EssRequest.employee_id == uuid.UUID(me))
    rows = (await db.execute(q.order_by(EssRequest.created_at.desc()))).scalars().all()
    return [_req_out(r, request) for r in rows if L.may_view(role, me, str(r.employee_id), str(r.manager_id) if r.manager_id else None)]


class RaiseIn(BaseModel):
    type_id: str | None = None
    type_key: str | None = None
    values: dict = {}


async def _check_files(db, org: str, employee_id: str, fields: list[dict], values: dict):
    for k in L.file_keys(fields, values):
        try:
            did = uuid.UUID(str(values[k]))
        except ValueError:
            raise HTTPException(422, "Attach the file again — the upload id is not valid")
        d = (await db.execute(select(StoredDocument).where(
            StoredDocument.id == did, StoredDocument.org_id == uuid.UUID(org),
            StoredDocument.deleted_at.is_(None)))).scalar_one_or_none()
        if not d or str(d.employee_id) != str(employee_id):
            raise HTTPException(422, "The attached file was not found — upload it again")


@router.post("/requests", status_code=201)
async def raise_request(body: RaiseIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, me = _org(request), _me(request)
    if not me:
        raise HTTPException(400, "Your login is not linked to an employee record")
    q = select(EssRequestType).where(EssRequestType.org_id == uuid.UUID(org))
    if body.type_id:
        q = q.where(EssRequestType.id == _uuid(body.type_id, "Request type"))
    elif body.type_key:
        q = q.where(EssRequestType.key == body.type_key)
    else:
        raise HTTPException(422, "Pick a request type")
    t = (await db.execute(q)).scalar_one_or_none()
    if not t or not t.active:
        raise HTTPException(404, "That request type is not available")
    e = (await db.execute(select(Employee).where(Employee.id == uuid.UUID(me),
                                                 Employee.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not e:
        raise HTTPException(400, "Your employee record was not found")
    fields = t.fields or []
    try:
        vals = L.clean_values(fields, body.values)
    except L.RequestError as ex:
        raise HTTPException(422, str(ex))
    await _check_files(db, org, me, fields, vals)
    name = f"{e.first_name or ''} {e.last_name or ''}".strip()
    r = EssRequest(org_id=uuid.UUID(org), type_id=t.id, type_key=t.key, type_name=t.name, category=t.category,
                   employee_id=e.id, employee_name=name, employee_number=getattr(e, "employee_number", None),
                   department=e.department, manager_id=e.manager_id, requester_user_id=_uid(request),
                   answers=vals, fields_snapshot=[f for f in fields if f.get("active", True)], status="pending",
                   created_at=_now())
    db.add(r)
    await db.commit()
    await approvals_client.open_item(
        org, kind=KIND, ref_id=str(r.id), title=f"{t.name} — {name}",
        summary=L.summary_lines(r.fields_snapshot, vals) or [t.name],
        requester_user_id=_uid(request), approver_roles=HR_INBOX, approver_manager_of_employee_id=me,
        link="/raise-request", callback_path=f"/employees/ess/requests/{r.id}/{{decision}}",
        context={"request_type": t.key, "department": e.department or "", "employee_id": me})
    return _req_out(r, request)


@router.get("/requests/{rid}")
async def get_request(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    r = await _req_or_404(db, _org(request), rid)
    _visible_or_404(request, r)
    return _req_out(r, request)


@router.get("/requests/{rid}/files/{key}")
async def download_file(rid: str, key: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    r = await _req_or_404(db, org, rid)
    _visible_or_404(request, r)
    doc_id = (r.answers or {}).get(key)
    if not doc_id or key not in L.file_keys(r.fields_snapshot or [], r.answers or {}):
        raise HTTPException(404, "This request has no such file")
    d = (await db.execute(select(StoredDocument).where(StoredDocument.id == _uuid(doc_id, "File"),
                                                       StoredDocument.org_id == uuid.UUID(org)))).scalar_one_or_none()
    if not d or str(d.employee_id) != str(r.employee_id):
        raise HTTPException(404, "The file is no longer available")
    data = (await db.execute(select(StoredDocument.data).where(StoredDocument.id == d.id))).scalar_one()
    return Response(content=bytes(data), media_type=d.content_type, headers={
        "Content-Disposition": f'attachment; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


class DecideIn(BaseModel):
    decision: str | None = None
    comment: str | None = None
    note: str | None = None


@router.post("/requests/{rid}/{verdict}")
async def decide_request(rid: str, verdict: str, request: Request, body: DecideIn | None = None,
                         db: AsyncSession = Depends(get_db)):
    if verdict not in ("approve", "reject", "withdraw"):
        raise HTTPException(404, "Unknown action")
    org, role, me = _org(request), _role(request), _me(request)
    r = await _req_or_404(db, org, rid)
    _visible_or_404(request, r)
    note = ((body.comment or body.note) if body else None) or None
    if verdict == "withdraw":
        ok, why = L.may_withdraw(me, str(r.employee_id), r.status)
        if not ok:
            raise HTTPException(409 if "pending" in why else 403, why)
        r.status, decision = "withdrawn", "withdrawn"
    else:
        if not L.may_decide(role, me, str(r.employee_id), str(r.manager_id) if r.manager_id else None,
                            actor_user_id=_uid(request), requester_user_id=r.requester_user_id):
            raise HTTPException(403, "Only HR or this employee's manager can decide — never the person who raised it")
        if r.status != "pending":
            raise HTTPException(409, f"This request is already {r.status}")
        r.status = decision = "approved" if verdict == "approve" else "rejected"
    r.decided_by, r.decided_by_name, r.decided_at, r.decision_note = _uid(request), _email(request), _now(), note
    await db.commit()
    await approvals_client.close_item(org, kind=KIND, ref_id=str(r.id), decision=decision, actor_user_id=_uid(request))
    if verdict != "withdraw":
        await notify(org, employee_ids=[str(r.employee_id)], title=f"Your {r.type_name} request was {decision}",
                     body=note or "", link="/raise-request", kind=KIND, email=False)
    return _req_out(r, request)


# ══════════════════════════ to-dos ════════════════════════════════════════════

def _todo_dict(t: EssTodo) -> dict:
    return {"id": str(t.id), "title": t.title, "due_date": _iso(t.due_date), "note": t.note, "done": bool(t.done),
            "done_at": _iso(t.done_at), "created_at": _iso(t.created_at)}


def _owner(request: Request) -> str:
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Sign in to keep a to-do list")
    return uid


async def _my_todos(db, org: str, uid: str) -> list[EssTodo]:
    return (await db.execute(select(EssTodo).where(EssTodo.org_id == uuid.UUID(org), EssTodo.user_id == uid)
                             .order_by(EssTodo.done, EssTodo.due_date, EssTodo.created_at))).scalars().all()


async def _todo_or_404(db, org: str, uid: str, tid: str) -> EssTodo:
    t = (await db.execute(select(EssTodo).where(EssTodo.id == _uuid(tid, "To-do"), EssTodo.org_id == uuid.UUID(org),
                                                EssTodo.user_id == uid))).scalar_one_or_none()
    if not t or t.user_id != uid:
        raise HTTPException(404, "To-do not found")
    return t


@router.get("/todos")
async def list_todos(request: Request, db: AsyncSession = Depends(get_db)):
    return [_todo_dict(t) for t in await _my_todos(db, _org(request), _owner(request))]


@router.post("/todos", status_code=201)
async def add_todo(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _owner(request)
    try:
        d = L.clean_todo(body or {})
    except L.RequestError as e:
        raise HTTPException(422, str(e))
    done = d.pop("done", False)
    me = _me(request)
    t = EssTodo(org_id=uuid.UUID(org), user_id=uid, employee_id=uuid.UUID(me) if me else None, done=done,
                done_at=_now() if done else None, created_at=_now(), **d)
    db.add(t)
    await db.commit()
    return _todo_dict(t)


@router.patch("/todos/{tid}")
async def update_todo(tid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _owner(request)
    t = await _todo_or_404(db, org, uid, tid)
    try:
        d = L.clean_todo(body or {}, partial=True)
    except L.RequestError as e:
        raise HTTPException(422, str(e))
    if "done" in d and d["done"] != bool(t.done):
        t.done_at = _now() if d["done"] else None
    for k, v in d.items():
        setattr(t, k, v)
    await db.commit()
    return _todo_dict(t)


@router.delete("/todos/{tid}")
async def delete_todo(tid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, uid = _org(request), _owner(request)
    t = await _todo_or_404(db, org, uid, tid)
    await db.delete(t)
    await db.commit()
    return {"ok": True, "id": tid}


# ══════════════════════════ merged "My tasks" ═════════════════════════════════

async def exit_tasks(db, org: str, actor: dict) -> list[dict]:
    """My own open exit case's clearance lines + lines I must sign off on other open cases."""
    from exit_logic import OPEN_STATES
    from exit_models import ExitCase, ExitClearanceItem
    rows = (await db.execute(select(ExitClearanceItem, ExitCase).join(ExitCase, ExitCase.id == ExitClearanceItem.case_id)
                             .where(ExitCase.org_id == uuid.UUID(org), ExitClearanceItem.org_id == uuid.UUID(org),
                                    ExitCase.status.in_(list(OPEN_STATES))))).all()
    out = []
    for item, case in rows:
        line = {"id": str(item.id), "department": item.department, "item": item.item, "owner_role": item.owner_role,
                "owner_user_id": item.owner_user_id, "owner_name": item.owner_name, "status": item.status,
                "owner_employee_id": str(item.owner_employee_id) if item.owner_employee_id else None}
        cd = {"id": str(case.id), "case_number": case.case_number, "employee_id": str(case.employee_id),
              "employee_name": case.employee_name, "manager_id": str(case.manager_id) if case.manager_id else None,
              "lwd": case.lwd or case.expected_lwd}
        if actor.get("employee_id") and cd["employee_id"] == actor["employee_id"]:
            out.append(L.own_exit_task(line, cd))
        elif L.exit_line_is_mine(actor, line, cd):
            out.append(L.owner_exit_task(line, cd))
    return out


async def onboarding_tasks(request: Request, db, org: str, me: str | None, role: str) -> tuple[list[dict], str]:
    """A joiner's own checklist, and the 'Manager' lines of direct reports' checklists. The onboarding
    service's lists are HR-only, so this read uses the services' internal identity and is filtered
    here to the caller's own sessions (or their direct reports')."""
    if not me:
        return [], "skipped"
    from shared.utils.internal import internal_headers
    reports: set[str] = set()
    if role == "manager":
        reports = {str(x) for x in (await db.execute(select(Employee.id).where(
            Employee.org_id == uuid.UUID(org), Employee.manager_id == uuid.UUID(me)))).scalars().all()}
    hdr = internal_headers(org, "employee-ess-tasks", role="hr_admin")
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.ONBOARDING_URL}/onboarding/sessions", headers=hdr)
            if r.status_code != 200 or not isinstance(r.json(), list):
                return [], "unavailable"
            for s in r.json():
                sid_emp = str(s.get("employee_id") or "")
                if s.get("status") == "cancelled" or not sid_emp or (sid_emp != me and sid_emp not in reports):
                    continue
                tr = await c.get(f"{settings.ONBOARDING_URL}/onboarding/sessions/{s['id']}/tasks", headers=hdr)
                if tr.status_code != 200:
                    continue
                for t in tr.json() or []:
                    if sid_emp == me:
                        out.append(L.onboarding_task(t, s, for_manager=False))
                    elif (t.get("owner") or "").strip().lower() == "manager":
                        out.append(L.onboarding_task(t, s, for_manager=True))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("onboarding tasks unavailable: %s", e)
        return out, "unavailable"
    return out, "ok"


@router.get("/tasks")
async def my_tasks(request: Request, db: AsyncSession = Depends(get_db)):
    org, role, me, uid = _org(request), _role(request), _me(request), _owner(request)
    todos = [L.todo_task(_todo_dict(t)) for t in await _my_todos(db, org, uid)]
    actor = {"role": role, "employee_id": me or "", "user_id": uid}
    sources = {"todo": "ok"}
    try:
        ex = await exit_tasks(db, org, actor)
        sources["exit"] = "ok"
    except Exception as e:                                   # noqa: BLE001  (never lose the to-dos over a side source)
        log.warning("exit tasks unavailable: %s", e)
        ex, sources["exit"] = [], "unavailable"
    onb, sources["onboarding"] = await onboarding_tasks(request, db, org, me, role)
    rows = L.merge_tasks(todos, ex, onb)
    return {"items": rows, "counts": L.task_counts(rows, local_today().isoformat()), "sources": sources}
