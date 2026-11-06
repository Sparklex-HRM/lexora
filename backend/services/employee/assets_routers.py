"""Asset register — Stream G4. Extends Stream C's `employee_assets` list into a proper register.

Same table (`employee_assets`), new columns added idempotently at boot (ADD COLUMN IF NOT
EXISTS) and `employee_id` relaxed to nullable so stock items can exist before assignment.
The older `/exit/assets` routes keep working: a return there writes `returned_on`, which this
register reads as "returned", and the exit case's clearance view (which lists the same rows)
therefore sees outstanding assets from this register with no extra wiring.

/assets                     GET register (filters) · POST new item (in stock or assigned)
/assets/summary             GET counts, cost, warranties expiring
/assets/mine                GET the signed-in employee's assets (+ pending acknowledgements)
/assets/outstanding         GET ?employee_id → the exit-clearance view of one person
/assets/{id}                PATCH master fields · GET detail + events + photo ids
/assets/{id}/assign         POST {employee_id, note}        → status assigned, ack pending
/assets/{id}/acknowledge    POST (the employee)             → "I acknowledge" with time
/assets/{id}/return         POST {condition, note, returned_on, status?: lost}
/assets/{id}/photos         POST multipart (JPG/PNG through docstore)  · GET /assets/photos/{doc_id}
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import DateTime, String, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils import docstore  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import local_today, org_today  # noqa: E402

import assets_logic as AL  # noqa: E402
import exit_common as C  # noqa: E402
from database import Base, get_db  # noqa: E402
from exit_models import EmployeeAsset  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/assets", tags=["assets"])


def _now():
    return datetime.now(timezone.utc)


class AssetEvent(Base):
    __tablename__ = "asset_events"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(String(600))


# New columns on the existing table (create_all never ALTERs). All additive; employee_id
# becomes nullable (a relaxation, no data change) so stock can be recorded before issue.
_ALTERS = [
    "ALTER TABLE employee_assets ALTER COLUMN employee_id DROP NOT NULL",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS category VARCHAR(60)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS serial VARCHAR(120)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS purchase_date DATE",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS purchase_cost NUMERIC(14,2)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS warranty_until DATE",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS location VARCHAR(120)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS status VARCHAR(12)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS assigned_by VARCHAR(200)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS ack_at TIMESTAMPTZ",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS ack_by VARCHAR(200)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS return_condition VARCHAR(20)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS return_note VARCHAR(600)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS returned_to VARCHAR(200)",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS photo_doc_ids JSONB",
    "ALTER TABLE employee_assets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
]
_COLS = ("category", "serial", "purchase_date", "purchase_cost", "warranty_until", "location", "status", "assigned_by",
         "ack_at", "ack_by", "return_condition", "return_note", "returned_to", "photo_doc_ids", "updated_at")


async def init_assets_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '15s'"))   # boot DDL never waits forever on a lock
        await conn.run_sync(Base.metadata.create_all)
        for ddl in _ALTERS:
            await conn.execute(text(ddl))


# ── raw column access (the ORM model in exit_models.py is left untouched) ────

async def _extra(db: AsyncSession, asset_id) -> dict:
    row = (await db.execute(text(f"SELECT {', '.join(_COLS)} FROM employee_assets WHERE id = :id"), {"id": str(asset_id)})).mappings().first()
    return dict(row) if row else {k: None for k in _COLS}


async def _extras(db: AsyncSession, ids: list) -> dict[str, dict]:
    if not ids:
        return {}
    rows = (await db.execute(text(f"SELECT id, {', '.join(_COLS)} FROM employee_assets WHERE id = ANY(:ids)"),
                             {"ids": [str(i) for i in ids]})).mappings().all()
    return {str(r["id"]): dict(r) for r in rows}


async def _set(db: AsyncSession, asset_id, **vals) -> None:
    vals = {k: v for k, v in vals.items() if k in _COLS}
    if not vals:
        return
    vals["updated_at"] = _now()
    sets = ", ".join(f"{k} = :{k}" for k in vals)
    params = {"id": str(asset_id)}
    for k, v in vals.items():
        if k == "photo_doc_ids":
            import json
            params[k] = json.dumps(v if v is not None else [])
        else:
            params[k] = v
    if "photo_doc_ids" in vals:
        sets = sets.replace("photo_doc_ids = :photo_doc_ids", "photo_doc_ids = CAST(:photo_doc_ids AS jsonb)")
    await db.execute(text(f"UPDATE employee_assets SET {sets} WHERE id = :id"), params)


def _out(a: EmployeeAsset, x: dict, emp: Employee | dict | None = None, today: date | None = None) -> dict:
    today = today or local_today()
    status = AL.derive_status(a.employee_id, a.issued_on, a.returned_on, x.get("status"))
    name = None
    if isinstance(emp, Employee):
        name = f"{emp.first_name} {emp.last_name}".strip()
    elif isinstance(emp, dict):
        name = emp.get("name")
    return {"id": str(a.id), "employee_id": str(a.employee_id) if a.employee_id else None, "employee_name": name, "item": a.item,
            "tag": a.tag, "category": x.get("category"), "serial": x.get("serial"), "purchase_date": C.iso(x.get("purchase_date")),
            "purchase_cost": float(x["purchase_cost"]) if x.get("purchase_cost") is not None else None,
            "warranty_until": C.iso(x.get("warranty_until")), "warranty_state": AL.warranty_state(x.get("warranty_until"), today),
            "location": x.get("location"), "status": status, "issued_on": C.iso(a.issued_on), "returned_on": C.iso(a.returned_on),
            "condition": a.condition, "note": a.note, "assigned_by": x.get("assigned_by"), "ack_at": C.iso(x.get("ack_at")),
            "ack_by": x.get("ack_by"), "acknowledged": x.get("ack_at") is not None, "return_condition": x.get("return_condition"),
            "return_note": x.get("return_note"), "returned_to": x.get("returned_to"),
            "photo_doc_ids": list(x.get("photo_doc_ids") or []), "returned": a.returned_on is not None,
            "created_by": a.created_by, "created_at": C.iso(a.created_at), "updated_at": C.iso(x.get("updated_at"))}


async def _emps(db: AsyncSession, org: str) -> dict[str, Employee]:
    rows = (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()
    return {str(e.id): e for e in rows}


async def _asset(db: AsyncSession, org: str, aid: str) -> EmployeeAsset:
    try:
        uuid.UUID(aid)
    except ValueError:
        raise HTTPException(404, "Asset not found")
    a = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.id == aid, EmployeeAsset.org_id == org))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Asset not found")
    return a


def _may_manage(actor: dict) -> bool:
    return C.is_hr(actor) or actor["role"] in ("manager", "payroll_admin")


async def _team(db: AsyncSession, org: str, actor: dict) -> set[str] | None:
    """None = org-wide (HR / payroll_admin); a manager = self + direct reports only (WASA 2026-10-21)."""
    if actor["role"] != "manager" or C.is_hr(actor):
        return None
    me = actor["employee_id"]
    if not me:
        return set()
    rows = (await db.execute(select(Employee.id).where(Employee.org_id == org, Employee.manager_id == me))).all()
    return {str(x[0]) for x in rows} | {me}


async def _may_manage_emp(db: AsyncSession, org: str, actor: dict, employee_id) -> bool:
    """May manage assets held by this employee (None = an unassigned asset → HR / payroll only)."""
    if not _may_manage(actor):
        return False
    team = await _team(db, org, actor)
    return team is None or (employee_id is not None and str(employee_id) in team)


def _event(db, org, asset_id, actor: dict, action: str, note: str | None = None):
    db.add(AssetEvent(org_id=uuid.UUID(org), asset_id=asset_id, actor=actor["email"], action=action, note=(note or "")[:600] or None))


# ── register ──────────────────────────────────────────────────────────────────

@router.get("")
async def register(request: Request, status: str | None = None, category: str | None = None, employee_id: str | None = None,
                   q: str | None = None, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not _may_manage(actor):
        raise HTTPException(403, "Only HR or a manager can see the asset register")
    rows = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.org_id == org).order_by(EmployeeAsset.created_at.desc()))).scalars().all()
    emps = await _emps(db, org)
    if actor["role"] == "manager":
        team = {k for k, e in emps.items() if str(e.manager_id or "") == actor["employee_id"]} | {actor["employee_id"]}
        rows = [a for a in rows if a.employee_id and str(a.employee_id) in team]
    xs = await _extras(db, [a.id for a in rows])
    out = [_out(a, xs.get(str(a.id), {}), emps.get(str(a.employee_id)) if a.employee_id else None) for a in rows]
    if status:
        out = [o for o in out if o["status"] == status]
    if category:
        out = [o for o in out if (o["category"] or "") == category]
    if employee_id:
        out = [o for o in out if o["employee_id"] == employee_id]
    if q:
        n = q.lower()
        out = [o for o in out if any(n in str(o.get(k) or "").lower() for k in ("item", "tag", "serial", "employee_name", "location"))]
    return {"assets": out, "categories": AL.CATEGORIES, "states": list(AL.STATES), "conditions": list(AL.CONDITIONS)}


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not _may_manage(actor):
        raise HTTPException(403, "Only HR or a manager can see the asset register")
    rows = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.org_id == org))).scalars().all()
    team = await _team(db, org, actor)
    if team is not None:                      # a manager's tiles count their own team only
        rows = [a for a in rows if a.employee_id and str(a.employee_id) in team]
    xs = await _extras(db, [a.id for a in rows])
    today = await org_today(org)
    items = []
    for a in rows:
        x = xs.get(str(a.id), {})
        items.append({"status": AL.derive_status(a.employee_id, a.issued_on, a.returned_on, x.get("status")), "category": x.get("category"),
                      "purchase_cost": x.get("purchase_cost"), "warranty_until": x.get("warranty_until")})
    return AL.register_summary(items, today)


class AssetIn(BaseModel):
    item: str
    category: str | None = None
    tag: str | None = None
    serial: str | None = None
    purchase_date: str | None = None
    purchase_cost: float | None = None
    warranty_until: str | None = None
    location: str | None = None
    note: str | None = None
    employee_id: str | None = None       # assign straight away
    issued_on: str | None = None


@router.post("", status_code=201)
async def create(body: AssetIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not C.is_hr(actor):
        raise HTTPException(403, "Only HR can add an asset")
    if not body.item.strip():
        raise HTTPException(422, "Enter the item name")
    if body.purchase_cost is not None and body.purchase_cost < 0:
        raise HTTPException(422, "Purchase cost cannot be negative")
    emp = await C.load_employee(db, org, body.employee_id) if body.employee_id else None
    a = EmployeeAsset(org_id=uuid.UUID(org), employee_id=emp.id if emp else None, item=body.item.strip()[:160], tag=(body.tag or "")[:80] or None,
                      issued_on=(C.parse_date(body.issued_on, "issued_on") or C.today()) if emp else None, note=(body.note or "")[:400] or None,
                      created_by=actor["email"])
    db.add(a)
    await db.flush()
    await _set(db, a.id, category=(body.category or "Other")[:60], serial=(body.serial or "")[:120] or None,
               purchase_date=C.parse_date(body.purchase_date, "purchase_date"), purchase_cost=body.purchase_cost,
               warranty_until=C.parse_date(body.warranty_until, "warranty_until"), location=(body.location or "")[:120] or None,
               status="assigned" if emp else "in_stock", assigned_by=actor["email"] if emp else None, photo_doc_ids=[])
    _event(db, org, a.id, actor, "created", f"{a.item}" + (f" — issued to {emp.first_name} {emp.last_name}" if emp else " — in stock"))
    await db.commit()
    if emp:
        await notify(org, employee_ids=[str(emp.id)], kind="general", title=f"Please acknowledge: {a.item}",
                     body="Open My assets and tap I acknowledge.", link="/engage/my-assets", email=False)
    return _out(a, await _extra(db, a.id), emp)


@router.get("/mine")
async def mine(request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not actor["employee_id"]:
        return {"assets": [], "pending_ack": 0}
    rows = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.org_id == org, EmployeeAsset.employee_id == actor["employee_id"])
                             .order_by(EmployeeAsset.issued_on.desc()))).scalars().all()
    xs = await _extras(db, [a.id for a in rows])
    out = [_out(a, xs.get(str(a.id), {})) for a in rows]
    return {"assets": out, "pending_ack": sum(1 for o in out if o["status"] == "assigned" and not o["acknowledged"])}


@router.get("/outstanding")
async def outstanding(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The exit-clearance view: what this person still holds."""
    org, actor = C.org_of(request), C.actor_of(request)
    if not (actor["employee_id"] == employee_id or await _may_manage_emp(db, org, actor, employee_id)):
        raise HTTPException(403, "Not allowed")
    rows = (await db.execute(select(EmployeeAsset).where(EmployeeAsset.org_id == org, EmployeeAsset.employee_id == employee_id))).scalars().all()
    xs = await _extras(db, [a.id for a in rows])
    out = [_out(a, xs.get(str(a.id), {})) for a in rows]
    return {"employee_id": employee_id, "assets": out, **AL.clearance_summary(out)}


@router.get("/photos/{doc_id}")
async def photo(doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    try:
        did = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(404, "Photo not found")
    d = (await db.execute(select(docstore.StoredDocument).where(docstore.StoredDocument.id == did, docstore.StoredDocument.org_id == org,
                                                                docstore.StoredDocument.doc_type == "asset_photo"))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Photo not found")
    if not ((d.employee_id and str(d.employee_id) == actor["employee_id"]) or await _may_manage_emp(db, org, actor, d.employee_id)):
        raise HTTPException(403, "Not allowed")
    data = await db.run_sync(lambda s: d.data)
    return Response(content=data, media_type=d.content_type, headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
                                                                     "Content-Security-Policy": docstore.SANDBOX_CSP})


@router.get("/{aid}")
async def detail(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    a = await _asset(db, org, aid)
    if not ((a.employee_id and str(a.employee_id) == actor["employee_id"]) or await _may_manage_emp(db, org, actor, a.employee_id)):
        raise HTTPException(403, "Not allowed")
    emps = await _emps(db, org)
    evs = (await db.execute(select(AssetEvent).where(AssetEvent.asset_id == a.id).order_by(AssetEvent.at))).scalars().all()
    return {"asset": _out(a, await _extra(db, a.id), emps.get(str(a.employee_id)) if a.employee_id else None),
            "events": [{"id": str(e.id), "at": C.iso(e.at), "actor": e.actor, "action": e.action, "note": e.note} for e in evs]}


@router.patch("/{aid}")
async def update(aid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not C.is_hr(actor):
        raise HTTPException(403, "Only HR can change asset details")
    a = await _asset(db, org, aid)
    if body.get("item"):
        a.item = str(body["item"]).strip()[:160]
    if "tag" in body:
        a.tag = (body["tag"] or "")[:80] or None
    if "note" in body:
        a.note = (body["note"] or "")[:400] or None
    vals = {}
    for k in ("category", "serial", "location"):
        if k in body:
            vals[k] = (str(body[k] or "")[:120] or None)
    for k in ("purchase_date", "warranty_until"):
        if k in body:
            vals[k] = C.parse_date(body[k], k)
    if "purchase_cost" in body:
        if body["purchase_cost"] is not None and float(body["purchase_cost"]) < 0:
            raise HTTPException(422, "Purchase cost cannot be negative")
        vals["purchase_cost"] = body["purchase_cost"]
    if body.get("status") in ("lost", "retired", "in_stock"):
        if body["status"] in ("lost", "retired") or a.employee_id is None:
            vals["status"] = body["status"]
    await _set(db, a.id, **vals)
    _event(db, org, a.id, actor, "updated", ", ".join(f"{k}: {v}" for k, v in {**vals, **{k: body[k] for k in ("item", "tag") if k in body}}.items())[:600])
    await db.commit()
    return _out(a, await _extra(db, a.id))


class AssignIn(BaseModel):
    employee_id: str
    issued_on: str | None = None
    note: str | None = None


@router.post("/{aid}/assign")
async def assign(aid: str, body: AssignIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not _may_manage(actor):
        raise HTTPException(403, "Only HR or a manager can issue an asset")
    a = await _asset(db, org, aid)
    x = await _extra(db, a.id)
    if AL.derive_status(a.employee_id, a.issued_on, a.returned_on, x.get("status")) == "assigned":
        raise HTTPException(409, "This asset is already with someone. Record its return first.")
    emp = await C.load_employee(db, org, body.employee_id)
    if not await _may_manage_emp(db, org, actor, emp.id):
        raise HTTPException(403, "A manager can issue assets to their own team only")
    a.employee_id = emp.id
    a.issued_on = C.parse_date(body.issued_on, "issued_on") or C.today()
    a.returned_on, a.condition = None, None
    await _set(db, a.id, status="assigned", assigned_by=actor["email"], ack_at=None, ack_by=None, return_condition=None, return_note=None, returned_to=None)
    _event(db, org, a.id, actor, "assigned", f"Issued to {emp.first_name} {emp.last_name}" + (f" — {body.note}" if body.note else ""))
    await db.commit()
    await notify(org, employee_ids=[str(emp.id)], kind="general", title=f"Please acknowledge: {a.item}",
                 body="Open My assets and tap I acknowledge.", link="/engage/my-assets", email=False)
    return _out(a, await _extra(db, a.id), emp)


@router.post("/{aid}/acknowledge")
async def acknowledge(aid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The employee's 'I acknowledge I received this' click — time and login recorded. No e-sign."""
    org, actor = C.org_of(request), C.actor_of(request)
    a = await _asset(db, org, aid)
    if not a.employee_id or str(a.employee_id) != actor["employee_id"]:
        raise HTTPException(403, "Only the person holding the asset can acknowledge it")
    x = await _extra(db, a.id)
    if x.get("ack_at"):
        return _out(a, x)
    await _set(db, a.id, ack_at=_now(), ack_by=actor["email"])
    _event(db, org, a.id, actor, "acknowledged", "I acknowledge that I received this asset.")
    await db.commit()
    return _out(a, await _extra(db, a.id))


class ReturnIn(BaseModel):
    condition: str = "good"
    note: str | None = None
    returned_on: str | None = None
    lost: bool = False


@router.post("/{aid}/return")
async def do_return(aid: str, body: ReturnIn, request: Request, db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    if not _may_manage(actor):
        raise HTTPException(403, "Only HR or a manager can record a return")
    a = await _asset(db, org, aid)
    if a.employee_id is None:
        raise HTTPException(409, "This asset is not issued to anyone")
    if not await _may_manage_emp(db, org, actor, a.employee_id):
        raise HTTPException(403, "A manager can record returns for their own team only")
    ret = C.parse_date(body.returned_on, "returned_on") or C.today()
    cond = "lost" if body.lost else body.condition
    bad = AL.validate_return(cond, ret, a.issued_on, C.today())
    if bad:
        raise HTTPException(422, bad)
    a.returned_on = ret
    a.condition = cond
    await _set(db, a.id, status="lost" if body.lost else "returned", return_condition=cond, return_note=(body.note or "")[:600] or None,
               returned_to=actor["email"])
    _event(db, org, a.id, actor, "lost" if body.lost else "returned", f"Condition: {cond}" + (f" — {body.note}" if body.note else ""))
    await db.commit()
    return _out(a, await _extra(db, a.id))


@router.post("/{aid}/photos", status_code=201)
async def add_photo(aid: str, request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    org, actor = C.org_of(request), C.actor_of(request)
    a = await _asset(db, org, aid)
    if not ((a.employee_id and str(a.employee_id) == actor["employee_id"]) or await _may_manage_emp(db, org, actor, a.employee_id)):
        raise HTTPException(403, "Not allowed")
    data = await file.read()
    try:
        cols = docstore.check_upload(file.filename or "photo.jpg", data)
    except docstore.DocError as e:
        raise HTTPException(e.status, str(e))
    if cols["content_type"] == "application/pdf":
        raise HTTPException(422, "Asset photos must be JPG or PNG")
    d = docstore.StoredDocument(org_id=uuid.UUID(org), employee_id=a.employee_id, doc_type="asset_photo", uploaded_by=actor["email"],
                                uploaded_by_name=actor["email"], **cols)
    db.add(d)
    await db.flush()
    x = await _extra(db, a.id)
    ids = list(x.get("photo_doc_ids") or []) + [str(d.id)]
    await _set(db, a.id, photo_doc_ids=ids[:20])
    _event(db, org, a.id, actor, "photo", d.file_name)
    await db.commit()
    return {"doc_id": str(d.id), "photo_doc_ids": ids[:20]}
