"""Custom fields (D1) — registry routes + the values API over each entity's ``custom_data``.

Mounted under /employees (routed to this service by the gateway) and registered BEFORE
the plain employee router so ``/employees/custom-fields/...`` is never read as an id.

  GET    /employees/custom-fields/meta                       entities, types, roles, lookups
  GET    /employees/custom-fields/definitions?entity=        definitions this role may see (setup roles: all, incl. inactive)
  POST   /employees/custom-fields/definitions                org admin / HR head — audited
  PUT    /employees/custom-fields/definitions/{id}           audited (key and entity cannot change)
  DELETE /employees/custom-fields/definitions/{id}           soft: active=false (values stay); audited
  GET    /employees/custom-fields/values/{entity}/{id}       {values, fields, missing_required, can_edit}
  PUT    /employees/custom-fields/values/{entity}/{id}       {values: {key: value}} — validated, role-checked, audited
  GET    /employees/custom-fields/values/{entity}?ids=a,b    bulk for grids / exports → {id: {key: value}}
"""
from __future__ import annotations

import logging
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Boolean, String, column, select, table, text, update
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.audit_trail import audit_change  # noqa: E402
from shared.utils.roles import has_role, require_roles, role_of  # noqa: E402

import custom_fields_logic as L  # noqa: E402
from custom_fields_models import CustomFieldDef  # noqa: E402
from database import get_db  # noqa: E402

log = logging.getLogger("lexora.custom_fields")
router = APIRouter(prefix="/employees/custom-fields", tags=["custom-fields"])
SETUP_ROLES = ("org_admin", "hr_admin")
OFFICE = ("org_admin", "hr", "payroll", "finance")

# one ALTER per entity table — additive, idempotent, tolerant of a table another service
# has not created yet (IF EXISTS): the column appears on that table's next boot of ours.
_ALTERS = [f"ALTER TABLE IF EXISTS {e['table']} ADD COLUMN IF NOT EXISTS custom_data JSONB" for e in L.ENTITIES.values()]


async def init_custom_fields(engine) -> None:
    """Boot: the registry table + the custom_data column on the four entity tables.

    Every statement runs in its OWN short transaction with a lock_timeout, so a boot
    never waits forever on another session's lock (a hang becomes a logged error and the
    column is added on the next boot). Call this BEFORE any init that opens a read session."""
    is_pg = engine.dialect.name == "postgresql"
    async with engine.begin() as conn:
        if is_pg:
            await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        await conn.run_sync(lambda c: CustomFieldDef.__table__.create(c, checkfirst=True))
    for ddl in _ALTERS:
        try:
            async with engine.begin() as conn:
                if is_pg:
                    await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
                await conn.execute(text(ddl))
        except Exception as e:                               # noqa: BLE001  (SQLite in tests, a missing table, or a lock timeout)
            log.warning("custom_data column not added (%s): %s", ddl.split()[3], str(e).splitlines()[0][:160])


def _tbl(entity: str):
    meta = L.ENTITIES[entity]
    cols = [column("id", PG_UUID(as_uuid=True)), column("org_id", PG_UUID(as_uuid=True)), column("custom_data", JSONB)]
    if meta["owner_col"] == "employee_id":
        cols.append(column("employee_id", PG_UUID(as_uuid=True)))
    if entity == "employee":
        cols.append(column("manager_id", PG_UUID(as_uuid=True)))
        cols.append(column("is_active", Boolean))
    return table(meta["table"], *cols)


def _org(request: Request) -> uuid.UUID:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    try:
        return uuid.UUID(org)
    except ValueError:
        raise HTTPException(400, "Organization id is not valid")


def _uuid(v: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(400, f"{what} is not valid")


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or "system"


def _entity(entity: str) -> str:
    e = (entity or "").lower()
    if e not in L.ENTITIES:
        raise HTTPException(404, "Unknown entity")
    return e


def _def_out(d: CustomFieldDef) -> dict:
    return {"id": str(d.id), "entity": d.entity, "key": d.key, "label": d.label, "type": d.type, "type_label": L.TYPE_LABELS.get(d.type, d.type),
            "required": bool(d.required), "options": d.options or ([] if d.type in ("select", "multiselect", "lookup") else None),
            "validation": d.validation or {}, "visible_to": d.visible_to or [], "editable_by": d.editable_by or [],
            "section": d.section or "", "sort_order": d.sort_order or 100, "show_in_grid": bool(d.show_in_grid),
            "active": bool(d.active), "updated_by": d.updated_by, "updated_at": d.updated_at.isoformat() if d.updated_at else None}


async def _defs(db, org: uuid.UUID, entity: str | None = None, *, include_inactive=False) -> list[dict]:
    q = select(CustomFieldDef).where(CustomFieldDef.org_id == org)
    if entity:
        q = q.where(CustomFieldDef.entity == entity)
    if not include_inactive:
        q = q.where(CustomFieldDef.active.is_(True))
    rows = (await db.execute(q.order_by(CustomFieldDef.entity, CustomFieldDef.sort_order, CustomFieldDef.label))).scalars().all()
    return [_def_out(d) for d in rows]


def _for_role(defs: list[dict], request: Request) -> list[dict]:
    role = role_of(request)
    out = []
    for d in L.visible_defs(defs, role, include_inactive=True):
        out.append({**d, "can_edit": L.can_edit(d, role)})
    return out


# ── meta + definitions ─────────────────────────────────────────────────────────

@router.get("/meta")
async def meta(request: Request):
    _org(request)
    return {"entities": [{"key": k, "label": v["label"]} for k, v in L.ENTITIES.items()],
            "types": [{"key": t, "label": L.TYPE_LABELS[t]} for t in L.TYPES],
            "roles": [{"key": k, "label": v} for k, v in L.ROLE_CHOICES], "lookups": list(L.LOOKUPS),
            "can_setup": has_role(role_of(request), SETUP_ROLES)}


@router.get("/definitions")
async def list_definitions(request: Request, entity: str | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    ent = _entity(entity) if entity else None
    setup = has_role(role_of(request), SETUP_ROLES)
    defs = await _defs(db, org, ent, include_inactive=setup)
    return {"items": _for_role(defs, request) if not setup else [{**d, "can_edit": True} for d in defs], "can_setup": setup}


class DefIn(BaseModel):
    entity: str | None = None
    key: str | None = None
    label: str
    type: str = "text"
    required: bool = False
    options: list | str | None = None
    validation: dict | None = None
    visible_to: list[str] | None = None
    editable_by: list[str] | None = None
    section: str | None = None
    sort_order: int | None = None
    show_in_grid: bool = True
    active: bool = True


def _check(body: dict, is_new: bool) -> dict:
    try:
        return L.validate_definition(body, is_new=is_new)
    except L.FieldError as e:
        raise HTTPException(422, str(e))


@router.post("/definitions", status_code=201)
async def create_definition(body: DefIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, SETUP_ROLES, "define custom fields")
    d = _check(body.model_dump(), True)
    dup = (await db.execute(select(CustomFieldDef.id).where(CustomFieldDef.org_id == org, CustomFieldDef.entity == d["entity"],
                                                              CustomFieldDef.key == d["key"]))).first()
    if dup:
        raise HTTPException(409, f"A {d['entity']} field with the key '{d['key']}' already exists")
    row = CustomFieldDef(org_id=org, created_by=_actor(request), updated_by=_actor(request), **d)
    db.add(row)
    await db.commit()
    await audit_change(request, "custom_field.definition.create", entity_type="custom_field_definition", entity_id=str(row.id),
                       after=_def_out(row), summary=f"Custom field '{row.label}' ({row.entity}.{row.key}) created")
    return _def_out(row)


async def _load(db, org, did: str) -> CustomFieldDef:
    row = (await db.execute(select(CustomFieldDef).where(CustomFieldDef.id == _uuid(did, "field"), CustomFieldDef.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Custom field not found")
    return row


@router.put("/definitions/{did}")
async def update_definition(did: str, body: DefIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, SETUP_ROLES, "change custom fields")
    row = await _load(db, org, did)
    data = body.model_dump()
    data["entity"], data["key"] = row.entity, row.key                  # fixed for life: stored values are keyed by them
    if data["type"] != row.type and row.type != data["type"]:
        # a type change would silently invalidate stored values; allow only text ↔ file/lookup widening
        if not ({row.type, data["type"]} <= {"text", "file", "lookup"}):
            raise HTTPException(422, "The type cannot be changed once values may exist. Deactivate this field and add a new one.")
    d = _check(data, False)
    before = _def_out(row)
    for k, v in d.items():
        setattr(row, k, v)
    row.updated_by = _actor(request)
    await db.commit()
    await audit_change(request, "custom_field.definition.update", entity_type="custom_field_definition", entity_id=str(row.id),
                       before=before, after=_def_out(row), summary=f"Custom field '{row.label}' ({row.entity}.{row.key}) changed")
    return _def_out(row)


@router.delete("/definitions/{did}")
async def deactivate_definition(did: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    require_roles(request, SETUP_ROLES, "remove custom fields")
    row = await _load(db, org, did)
    before = _def_out(row)
    row.active, row.updated_by = False, _actor(request)
    await db.commit()
    await audit_change(request, "custom_field.definition.deactivate", entity_type="custom_field_definition", entity_id=str(row.id),
                       before=before, after=_def_out(row), summary=f"Custom field '{row.label}' ({row.entity}.{row.key}) deactivated (values kept)")
    return {"ok": True, "id": str(row.id), "active": False}


# ── values ─────────────────────────────────────────────────────────────────────

async def _row(db, entity: str, org: uuid.UUID, rid: uuid.UUID):
    t = _tbl(entity)
    r = (await db.execute(select(t).where(t.c.id == rid, t.c.org_id == org))).mappings().first()
    if not r:
        raise HTTPException(404, f"{L.ENTITIES[entity]['label']} not found")
    return r


def _may_touch(request: Request, entity: str, row, *, write: bool) -> bool:
    """Office roles: every row. A manager: own + direct reports (employee entity) / own rows.
    An employee: only rows that are theirs. Candidates: office only."""
    role = role_of(request)
    if has_role(role, OFFICE):
        return True
    me = request.headers.get("x-employee-id") or ""
    if not me:
        return False
    owner = L.ENTITIES[entity]["owner_col"]
    if not owner:
        return False
    mine = str(row["id"] if owner == "id" else row.get("employee_id") or "") == me
    if mine:
        return True
    if entity == "employee" and role_in_manager(role) and str(row.get("manager_id") or "") == me:
        return not write or True
    return False


def role_in_manager(role: str) -> bool:
    return has_role(role, ("manager",))


@router.get("/values/{entity}/{rid}")
async def get_values(entity: str, rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, ent = _org(request), _entity(entity)
    row = await _row(db, ent, org, _uuid(rid, "id"))
    if not _may_touch(request, ent, row, write=False):
        raise HTTPException(403, "This record is not yours to see")
    defs = await _defs(db, org, ent)
    role = role_of(request)
    data = row.get("custom_data") or {}
    return {"entity": ent, "id": str(row["id"]), "values": L.visible_values(defs, data, role),
            "fields": _for_role(defs, request), "missing_required": L.missing_required(defs, data, role),
            "can_edit": any(L.can_edit(d, role) for d in L.visible_defs(defs, role))}


class ValuesIn(BaseModel):
    values: dict


@router.put("/values/{entity}/{rid}")
async def put_values(entity: str, rid: str, body: ValuesIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, ent = _org(request), _entity(entity)
    row = await _row(db, ent, org, _uuid(rid, "id"))
    if not _may_touch(request, ent, row, write=True):
        raise HTTPException(403, "This record is not yours to change")
    defs = await _defs(db, org, ent)
    role = role_of(request)
    before = dict(row.get("custom_data") or {})
    try:
        after, changed = L.apply_values(defs, before, body.values or {}, role)
    except L.FieldError as e:
        raise HTTPException(422, str(e))
    if changed:
        t = _tbl(ent)
        await db.execute(update(t).where(t.c.id == row["id"], t.c.org_id == org).values(custom_data=after))
        await db.commit()
        await audit_change(request, f"custom_field.values.update", entity_type=ent, entity_id=str(row["id"]),
                           before={k: before.get(k) for k in changed}, after={k: after.get(k) for k in changed},
                           summary=f"Custom fields changed on {ent}: {', '.join(changed)}")
    return {"entity": ent, "id": str(row["id"]), "values": L.visible_values(defs, after, role), "changed": changed,
            "missing_required": L.missing_required(defs, after, role)}


@router.get("/values/{entity}")
async def bulk_values(entity: str, request: Request, ids: str = "", db: AsyncSession = Depends(get_db)):
    """Grid / export helper: {id: {key: value}} for up to 2000 ids, filtered by what this role may see."""
    org, ent = _org(request), _entity(entity)
    role = role_of(request)
    if not has_role(role, OFFICE + ("manager",)):
        raise HTTPException(403, "Your role cannot list custom values")
    want = [x.strip() for x in (ids or "").split(",") if x.strip()][:2000]
    defs = await _defs(db, org, ent)
    vis = L.visible_defs(defs, role)
    if not vis:
        return {"entity": ent, "fields": [], "values": {}}
    t = _tbl(ent)
    q = select(t).where(t.c.org_id == org)
    if want:
        q = q.where(t.c.id.in_([_uuid(x, "id") for x in want]))
    if not has_role(role, OFFICE):                              # a manager: own direct reports / own rows only
        me = request.headers.get("x-employee-id") or ""
        if not me:
            return {"entity": ent, "fields": _for_role(defs, request), "values": {}}
        if ent == "employee":
            q = q.where((t.c.manager_id == uuid.UUID(me)) | (t.c.id == uuid.UUID(me)))
        elif L.ENTITIES[ent]["owner_col"] == "employee_id":
            q = q.where(t.c.employee_id == uuid.UUID(me))
        else:
            return {"entity": ent, "fields": _for_role(defs, request), "values": {}}
    rows = (await db.execute(q.limit(2000))).mappings().all()
    return {"entity": ent, "fields": _for_role(defs, request),
            "values": {str(r["id"]): L.visible_values(defs, r.get("custom_data") or {}, role) for r in rows if r.get("custom_data")}}
