"""Mobile punch — geo-tag, geo-fence, selfie, device binding, offline sync (Stream G4).

Routes (all under /time/mobile, gateway-stamped identity):
  GET  /settings · PUT /settings                 HR: the org's mobile-punch policy
  GET  /sites · POST /sites · PATCH /sites/{id}  HR: work sites (geo-fences) — one per location
  GET  /context                                  the phone: policy, my phone status, sites, today
  POST /punch                                    one punch (online) — duplicate-safe by client_id
  POST /sync                                     a queue of offline punches, in device-time order
  GET  /punches?employee_id&day                  punches with mobile detail (selfie, distance, flags)
  GET  /selfie/{meta_id}                         the selfie image (self / manager / HR)
  GET  /today?day                                manager: today's punches of my team, distance from site
  GET  /devices?status · POST /devices/{id}/{decision} · POST /devices/{id}/revoke

Design: the existing `ta_punches` row is still the punch (attendance is derived from it
exactly as before). This module adds a 1:1 side table `ta_punch_mobile` with the phone
facts, so nothing in models.py changes. A refused punch is kept in the side table with
`punch_id` empty and the refusal sentence, so HR can see what the phone tried.
"""
from __future__ import annotations

import base64
import sys
import uuid
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from shared.utils import docstore  # noqa: E402
from shared.utils.approvals_client import close_item, open_item  # noqa: E402
from shared.utils.config import settings as cfg  # noqa: E402
from shared.utils.notify import notify  # noqa: E402
from shared.utils.org_time import org_today, to_local_date  # noqa: E402

import mobile_punch_logic as ML  # noqa: E402
from database import Base, get_db  # noqa: E402
from models import Punch  # noqa: E402
import routers as R  # noqa: E402  (reuses _fetch_employees / _schedule_map / _recompute_day)

router = APIRouter(prefix="/time/mobile", tags=["mobile-punch"])

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
DIRECTIONS = ("in", "out", "break_start", "break_end")


def _now():
    return datetime.now(timezone.utc)


# ── tables (all new) ──────────────────────────────────────────────────────────

class MobileSettings(Base):
    __tablename__ = "ta_mobile_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GeoFence(Base):
    __tablename__ = "ta_geo_fences"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120))
    location_id: Mapped[str | None] = mapped_column(String(64))      # master_locations id, when linked
    lat: Mapped[float] = mapped_column(Numeric(9, 6))
    lng: Mapped[float] = mapped_column(Numeric(9, 6))
    radius_m: Mapped[int] = mapped_column(Integer, default=150)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Device(Base):
    __tablename__ = "ta_devices"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(200))
    device_id: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)   # active | pending | revoked | rejected
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PunchMobile(Base):
    __tablename__ = "ta_punch_mobile"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    punch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(12))
    work_date: Mapped[date] = mapped_column(Date, index=True)
    device_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    synced_late: Mapped[bool] = mapped_column(Boolean, default=False)
    late_by_minutes: Mapped[int] = mapped_column(Integer, default=0)
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    accuracy_m: Mapped[int | None] = mapped_column(Integer)
    mock_location: Mapped[bool] = mapped_column(Boolean, default=False)
    device_id: Mapped[str | None] = mapped_column(String(64))
    fence_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    fence_name: Mapped[str | None] = mapped_column(String(120))
    distance_m: Mapped[int | None] = mapped_column(Integer)
    inside_fence: Mapped[bool | None] = mapped_column(Boolean)
    selfie_doc_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    flags: Mapped[list] = mapped_column(JSONB, default=list)
    refused_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(12), default="mobile")


_DDL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_ta_punch_mobile_client ON ta_punch_mobile (org_id, client_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_ta_devices_emp_dev ON ta_devices (org_id, employee_id, device_id)",
]


async def init_mobile_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in _DDL:
            await conn.execute(text(ddl))


# ── identity helpers ──────────────────────────────────────────────────────────

def _org(request: Request) -> str:
    return R._org(request)


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _is_hr(request: Request) -> bool:
    return _role(request) in HR_ROLES


def _me(request: Request) -> str | None:
    return request.headers.get("x-employee-id") or None


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or "unknown"


async def _settings(db: AsyncSession, org: str) -> dict:
    row = (await db.execute(select(MobileSettings).where(MobileSettings.org_id == org))).scalar_one_or_none()
    return ML.merged_settings(row.value if row else None)


def _fence_out(f: GeoFence) -> dict:
    return {"id": str(f.id), "name": f.name, "location_id": f.location_id, "lat": float(f.lat), "lng": float(f.lng),
            "radius_m": f.radius_m, "active": f.active}


async def _fences(db: AsyncSession, org: str, active_only=True) -> list[dict]:
    q = select(GeoFence).where(GeoFence.org_id == org)
    if active_only:
        q = q.where(GeoFence.active.is_(True))
    return [_fence_out(f) for f in (await db.execute(q.order_by(GeoFence.name))).scalars().all()]


def _meta_out(m: PunchMobile, emp: dict | None = None) -> dict:
    return {"id": str(m.id), "punch_id": str(m.punch_id) if m.punch_id else None, "employee_id": str(m.employee_id),
            "employee_name": (f"{emp['first_name']} {emp['last_name']}" if emp else None),
            "employee_number": (emp or {}).get("employee_number"), "department": (emp or {}).get("department"),
            "client_id": m.client_id, "direction": m.direction, "work_date": m.work_date.isoformat() if m.work_date else None,
            "device_time": m.device_time.isoformat() if m.device_time else None,
            "server_time": m.server_time.isoformat() if m.server_time else None,
            "synced_late": bool(m.synced_late), "late_by_minutes": m.late_by_minutes or 0,
            "lat": float(m.lat) if m.lat is not None else None, "lng": float(m.lng) if m.lng is not None else None,
            "accuracy_m": m.accuracy_m, "mock_location": bool(m.mock_location), "device_id": m.device_id,
            "fence_name": m.fence_name, "distance_m": m.distance_m, "inside_fence": m.inside_fence,
            "has_selfie": m.selfie_doc_id is not None,
            "selfie_url": f"/time/mobile/selfie/{m.id}" if m.selfie_doc_id else None,
            "flags": list(m.flags or []), "flag_labels": ML.flag_labels(list(m.flags or [])),
            "refused": m.punch_id is None, "refused_reason": m.refused_reason, "source": m.source}


# ── settings + sites ──────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    return {"value": await _settings(db, _org(request)), "can_edit": _is_hr(request)}


class SettingsIn(BaseModel):
    value: dict


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    if not _is_hr(request):
        raise HTTPException(403, "Only HR can change the mobile punch rules")
    try:
        value = ML.clean_settings(body.value)
    except ValueError as e:
        raise HTTPException(422, str(e))
    org = _org(request)
    row = (await db.execute(select(MobileSettings).where(MobileSettings.org_id == org))).scalar_one_or_none()
    if row is None:
        row = MobileSettings(org_id=uuid.UUID(org))
        db.add(row)
    row.value, row.updated_by = value, _actor(request)
    await db.commit()
    return {"value": value, "saved": True}


async def _master_locations(request: Request) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{cfg.EMPLOYEE_URL}/masters/locations", headers=R._fwd(request))
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


@router.get("/sites")
async def list_sites(request: Request, db: AsyncSession = Depends(get_db)):
    """Every geo-fence, plus the master locations that do not have one yet."""
    org = _org(request)
    fences = await _fences(db, org, active_only=False)
    by_loc = {f["location_id"] for f in fences if f["location_id"]}
    by_name = {f["name"].strip().lower() for f in fences}
    unlinked = [{"id": str(l.get("id")), "name": l.get("name"), "city": l.get("city")} for l in await _master_locations(request)
                if str(l.get("id")) not in by_loc and (l.get("name") or "").strip().lower() not in by_name]
    return {"fences": fences, "locations_without_fence": unlinked, "can_edit": _is_hr(request)}


class FenceIn(BaseModel):
    name: str
    lat: float
    lng: float
    radius_m: int = 150
    location_id: str | None = None
    active: bool = True


@router.post("/sites", status_code=201)
async def create_site(body: FenceIn, request: Request, db: AsyncSession = Depends(get_db)):
    if not _is_hr(request):
        raise HTTPException(403, "Only HR can add a work site")
    lat, lng = ML.clamp_coord(body.lat, -90, 90), ML.clamp_coord(body.lng, -180, 180)
    if lat is None or lng is None:
        raise HTTPException(422, "Latitude must be -90 to 90 and longitude -180 to 180")
    if not 10 <= int(body.radius_m) <= 5000:
        raise HTTPException(422, "The radius must be 10 to 5000 metres")
    if not body.name.strip():
        raise HTTPException(422, "Enter the site name")
    f = GeoFence(org_id=uuid.UUID(_org(request)), name=body.name.strip()[:120], lat=lat, lng=lng,
                 radius_m=int(body.radius_m), location_id=body.location_id, active=body.active)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _fence_out(f)


@router.patch("/sites/{fid}")
async def update_site(fid: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    if not _is_hr(request):
        raise HTTPException(403, "Only HR can change a work site")
    f = (await db.execute(select(GeoFence).where(GeoFence.id == fid, GeoFence.org_id == _org(request)))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "Work site not found")
    if "lat" in body or "lng" in body:
        lat = ML.clamp_coord(body.get("lat", f.lat), -90, 90)
        lng = ML.clamp_coord(body.get("lng", f.lng), -180, 180)
        if lat is None or lng is None:
            raise HTTPException(422, "Latitude must be -90 to 90 and longitude -180 to 180")
        f.lat, f.lng = lat, lng
    if "radius_m" in body:
        if not 10 <= int(body["radius_m"]) <= 5000:
            raise HTTPException(422, "The radius must be 10 to 5000 metres")
        f.radius_m = int(body["radius_m"])
    if body.get("name"):
        f.name = str(body["name"]).strip()[:120]
    if "active" in body:
        f.active = bool(body["active"])
    if "location_id" in body:
        f.location_id = body["location_id"] or None
    await db.commit()
    await db.refresh(f)
    return _fence_out(f)


# ── devices ───────────────────────────────────────────────────────────────────

def _device_out(d: Device) -> dict:
    return {"id": str(d.id), "employee_id": str(d.employee_id), "employee_name": d.employee_name, "device_id": d.device_id,
            "label": d.label, "status": d.status, "registered_at": d.registered_at.isoformat() if d.registered_at else None,
            "decided_by": d.decided_by, "decided_at": d.decided_at.isoformat() if d.decided_at else None}


async def _device_state(db: AsyncSession, org: str, emp: dict, device_id: str | None, label: str | None,
                        binding_on: bool, user_id: str | None) -> tuple[str | None, Device | None]:
    """active | pending | new → (state, row). Registers the first phone; files an HR approval for a second."""
    if not binding_on or not device_id:
        return None, None
    rows = (await db.execute(select(Device).where(Device.org_id == org, Device.employee_id == emp["id"]))).scalars().all()
    mine = next((d for d in rows if d.device_id == device_id), None)
    if mine:
        return ("active" if mine.status == "active" else "pending"), mine
    name = f"{emp['first_name']} {emp['last_name']}"
    has_active = any(d.status == "active" for d in rows)
    d = Device(org_id=uuid.UUID(org), employee_id=uuid.UUID(emp["id"]), employee_name=name, device_id=device_id,
               label=(label or "")[:160] or None, status="active" if not has_active else "pending")
    db.add(d)
    await db.flush()
    if d.status == "pending":
        await open_item(org, kind="device_change", ref_id=str(d.id), title=f"New phone for punching — {name}",
                        summary=[f"Phone: {d.label or d.device_id}", "The old phone stays active until you decide."],
                        requester_user_id=user_id, approver_roles=["hr", "org_admin"], link="/mobile/sites?tab=devices",
                        callback_path=f"/time/mobile/devices/{d.id}/{{decision}}")
        return "pending", d
    return "active", d


@router.get("/devices")
async def list_devices(request: Request, status: str | None = None, employee_id: str | None = None,
                       db: AsyncSession = Depends(get_db)):
    org = _org(request)
    q = select(Device).where(Device.org_id == org)
    if not _is_hr(request):
        me = _me(request)
        if _role(request) == "manager" and me:
            team = {e["id"] for e in await R._fetch_employees(request) if str(e.get("manager_id")) == me} | {me}
            q = q.where(Device.employee_id.in_([uuid.UUID(t) for t in team]))
        elif me:
            q = q.where(Device.employee_id == me)
        else:
            return []
    if status:
        q = q.where(Device.status == status)
    if employee_id:
        q = q.where(Device.employee_id == employee_id)
    return [_device_out(d) for d in (await db.execute(q.order_by(Device.registered_at.desc()))).scalars().all()]


class DecisionIn(BaseModel):
    decision: str | None = None
    comment: str | None = None


@router.post("/devices/{did}/revoke")
async def revoke_device(did: str, request: Request, db: AsyncSession = Depends(get_db)):
    if not _is_hr(request):
        raise HTTPException(403, "Only HR can remove a registered phone")
    d = (await db.execute(select(Device).where(Device.id == did, Device.org_id == _org(request)))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Phone not found")
    d.status, d.decided_by, d.decided_at = "revoked", _actor(request), _now()
    await db.commit()
    return _device_out(d)


@router.post("/devices/{did}/{decision}")
async def decide_device(did: str, decision: str, request: Request, body: DecisionIn | None = None,
                        db: AsyncSession = Depends(get_db)):
    """approve | reject — called from the Approvals inbox (callback) or the Work sites screen."""
    if not _is_hr(request):
        raise HTTPException(403, "Only HR can approve a phone change")
    decision = (decision or (body.decision if body else "") or "").lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    org = _org(request)
    d = (await db.execute(select(Device).where(Device.id == did, Device.org_id == org))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Phone not found")
    if d.status != "pending":
        raise HTTPException(409, f"This phone is already {d.status}")
    if decision == "approve":
        others = (await db.execute(select(Device).where(Device.org_id == org, Device.employee_id == d.employee_id,
                                                        Device.status == "active"))).scalars().all()
        for o in others:                                   # one active phone per person
            o.status, o.decided_by, o.decided_at = "revoked", _actor(request), _now()
        d.status = "active"
    else:
        d.status = "rejected"
    d.decided_by, d.decided_at = _actor(request), _now()
    await db.commit()
    await close_item(org, kind="device_change", ref_id=str(d.id), decision=decision, actor_user_id=request.headers.get("x-user-id"))
    await notify(org, employee_ids=[str(d.employee_id)], kind="attendance",
                 title=("Your new phone is approved for punching" if decision == "approve" else "Your new phone was not approved"),
                 body=(body.comment if body else "") or "", link="/mobile/punch", email=False)
    return _device_out(d)


# ── the punch ─────────────────────────────────────────────────────────────────

class MobilePunchIn(BaseModel):
    client_id: str
    direction: str
    device_time: str | None = None
    lat: float | None = None
    lng: float | None = None
    accuracy_m: float | None = None
    mock_location: bool = False
    device_id: str | None = None          # the browser's fingerprint source (hashed here)
    device_label: str | None = None
    selfie_base64: str | None = None      # data URL or raw base64 (JPG / PNG)
    note: str | None = None
    employee_id: str | None = None        # HR / manager punching for someone (rare); employees punch themselves


async def _store_selfie(db: AsyncSession, org: str, employee_id: str, b64: str | None, max_kb: int, actor: str):
    if not b64:
        return None
    raw = b64.split(",", 1)[1] if b64.startswith("data:") else b64
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "The selfie could not be read")
    if len(data) > max_kb * 1024:
        raise HTTPException(413, f"The selfie is too large. The limit is {max_kb} KB.")
    try:
        cols = docstore.check_upload("selfie.jpg", data)
    except docstore.DocError as e:
        raise HTTPException(e.status, "The selfie must be a JPG or PNG image")
    if cols["content_type"] == "application/pdf":
        raise HTTPException(422, "The selfie must be a JPG or PNG image")
    doc = docstore.StoredDocument(org_id=uuid.UUID(org), employee_id=uuid.UUID(employee_id), doc_type="punch_selfie",
                                  uploaded_by=actor, uploaded_by_name=actor, **cols)
    db.add(doc)
    await db.flush()
    return doc.id


async def _one_punch(db: AsyncSession, request: Request, body: MobilePunchIn, emps: dict, st: dict, fences: list[dict],
                     sched_cache: dict) -> dict:
    org = _org(request)
    if body.direction not in DIRECTIONS:
        raise HTTPException(422, "direction must be in | out | break_start | break_end")
    cid = (body.client_id or "").strip()[:64]
    if not cid:
        raise HTTPException(422, "client_id is required (the phone's own id for this punch)")
    dup = (await db.execute(select(PunchMobile).where(PunchMobile.org_id == org, PunchMobile.client_id == cid))).scalar_one_or_none()
    if dup:
        return {**_meta_out(dup), "duplicate": True, "accepted": dup.punch_id is not None}

    me = _me(request)
    emp_id = body.employee_id or me
    if body.employee_id and body.employee_id != me and not (_is_hr(request) or _role(request) == "manager"):
        raise HTTPException(403, "You can only punch for yourself")
    emp = emps.get(emp_id or "")
    if not emp:
        raise HTTPException(404, "Your login is not linked to an employee record")

    server_time = _now()
    device_time = ML.parse_iso(body.device_time) or server_time
    if device_time > server_time:
        device_time = server_time
    late, late_min = ML.late_sync(device_time, server_time, st["late_sync_minutes"])
    lat, lng = ML.clamp_coord(body.lat, -90, 90), ML.clamp_coord(body.lng, -180, 180)
    acc = int(body.accuracy_m) if body.accuracy_m is not None and body.accuracy_m >= 0 else None
    fence, dist = ML.pick_fence(lat, lng, fences, emp.get("work_location"))
    dev_id = ML.device_fingerprint(body.device_id) if body.device_id else None
    dev_state, _dev = await _device_state(db, org, emp, dev_id, body.device_label, st["device_binding"],
                                          request.headers.get("x-user-id"))
    has_selfie = bool(body.selfie_base64)
    verdict = ML.evaluate_punch(st, lat=lat, lng=lng, accuracy_m=acc, mock_location=body.mock_location, fence=fence,
                                distance_m=dist, device_state=dev_state, has_selfie=has_selfie)
    flags = list(verdict["flags"]) + (["synced_late"] if late else [])
    meta = PunchMobile(org_id=uuid.UUID(org), employee_id=uuid.UUID(emp["id"]), client_id=cid, direction=body.direction,
                       work_date=to_local_date(device_time), device_time=device_time, server_time=server_time, synced_late=late,
                       late_by_minutes=late_min, lat=lat, lng=lng, accuracy_m=acc, mock_location=bool(body.mock_location),
                       device_id=dev_id, fence_id=uuid.UUID(fence["id"]) if fence else None,
                       fence_name=fence["name"] if fence else None, distance_m=dist, inside_fence=verdict["inside_fence"],
                       flags=flags, refused_reason=verdict["refusal"])
    if not verdict["allowed"]:
        db.add(meta)
        await db.commit()
        raise HTTPException(422, verdict["refusal"])

    if st["selfie"] != "off":
        meta.selfie_doc_id = await _store_selfie(db, org, emp["id"], body.selfie_base64, st["selfie_max_kb"], _actor(request))
    if "sched" not in sched_cache:
        sched_cache["sched"] = await R._schedule_map(db, org)
        sched_cache["holidays"] = await R._fetch_stat_holidays(request)
    by_emp, default = sched_cache["sched"]
    p = Punch(org_id=org, employee_id=uuid.UUID(emp["id"]), work_date=to_local_date(device_time), at=device_time,
              direction=body.direction, source="mobile", note=(body.note or "")[:200] or None, lat=lat, lng=lng,
              geofence_ok=verdict["inside_fence"], created_by=request.headers.get("x-user-id") or None)
    db.add(p)
    await db.flush()
    meta.punch_id = p.id
    db.add(meta)
    holidays = {date.fromisoformat(h["holiday_date"][:10]) for h in sched_cache["holidays"]
                if h.get("holiday_date") and (h.get("province") in (None, emp.get("province_of_employment")))}
    row = await R._recompute_day(db, org, emp, by_emp.get(emp["id"]), default, to_local_date(device_time), holidays)
    await db.commit()
    out = {**_meta_out(meta, emp), "duplicate": False, "accepted": True, "status": row.status,
           "actual_hours": float(row.actual_hours or 0), "at": device_time.isoformat()}
    if "outside_fence" in flags or "synced_late" in flags or "mock_location" in flags or "poor_accuracy" in flags:
        mgr = emp.get("manager_id")
        if mgr:
            await notify(org, employee_ids=[str(mgr)], kind="attendance", email=False,
                         title=f"Punch to check: {emp['first_name']} {emp['last_name']} — {', '.join(ML.flag_labels(flags))}",
                         body=f"{body.direction} at {device_time.strftime('%H:%M')} UTC. Distance from site: {dist if dist is not None else '—'} m.",
                         link="/mobile/team-punches")
    return out


@router.post("/punch", status_code=201)
async def mobile_punch(body: MobilePunchIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emps = {e["id"]: e for e in await R._fetch_employees(request)}
    st = await _settings(db, org)
    fences = await _fences(db, org)
    return await _one_punch(db, request, body, emps, st, fences, {})


class SyncIn(BaseModel):
    punches: list[MobilePunchIn]


@router.post("/sync")
async def sync_punches(body: SyncIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Offline queue: every punch is processed in device-time order; one refusal does not
    stop the rest. Each result carries the client_id so the phone can drop it from the queue."""
    org = _org(request)
    emps = {e["id"]: e for e in await R._fetch_employees(request)}
    st = await _settings(db, org)
    fences = await _fences(db, org)
    cache: dict = {}
    results = []
    ordered = sorted(body.punches, key=lambda p: ML.parse_iso(p.device_time) or _now())
    for p in ordered[:200]:
        try:
            r = await _one_punch(db, request, p, emps, st, fences, cache)
            results.append({"client_id": p.client_id, "ok": True, "result": r})
        except HTTPException as e:
            await db.rollback()
            results.append({"client_id": p.client_id, "ok": False, "error": e.detail, "status": e.status_code})
    return {"results": results, "server_time": _now().isoformat()}


# ── reads ─────────────────────────────────────────────────────────────────────

@router.get("/context")
async def context(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    me = _me(request)
    st = await _settings(db, org)
    fences = await _fences(db, org)
    devices = []
    if me:
        devices = [_device_out(d) for d in (await db.execute(select(Device).where(
            Device.org_id == org, Device.employee_id == me, Device.status.in_(("active", "pending"))))).scalars().all()]
    today = await org_today(org)
    rows = []
    if me:
        rows = (await db.execute(select(PunchMobile).where(PunchMobile.org_id == org, PunchMobile.employee_id == me,
                                                          PunchMobile.work_date == today).order_by(PunchMobile.device_time))).scalars().all()
    return {"settings": st, "sites": [{"name": f["name"], "radius_m": f["radius_m"]} for f in fences],
            "my_devices": devices, "today": [_meta_out(m) for m in rows], "server_time": _now().isoformat(),
            "employee_id": me}


async def _may_see(request: Request, employee_id: str) -> bool:
    if _is_hr(request) or _me(request) == employee_id:
        return True
    if _role(request) == "manager":
        emps = {e["id"]: e for e in await R._fetch_employees(request)}
        e = emps.get(employee_id)
        return bool(e and str(e.get("manager_id")) == _me(request))
    return False


@router.get("/punches")
async def punches_with_detail(request: Request, employee_id: str, day: str | None = None,
                              db: AsyncSession = Depends(get_db)):
    if not await _may_see(request, employee_id):
        raise HTTPException(403, "Not allowed")
    org = _org(request)
    q = select(PunchMobile).where(PunchMobile.org_id == org, PunchMobile.employee_id == employee_id)
    if day:
        q = q.where(PunchMobile.work_date == date.fromisoformat(day))
    rows = (await db.execute(q.order_by(PunchMobile.device_time.desc()).limit(300))).scalars().all()
    return [_meta_out(m) for m in rows]


@router.get("/selfie/{meta_id}")
async def selfie(meta_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    m = (await db.execute(select(PunchMobile).where(PunchMobile.id == meta_id, PunchMobile.org_id == org))).scalar_one_or_none()
    if not m or not m.selfie_doc_id:
        raise HTTPException(404, "No selfie for this punch")
    if not await _may_see(request, str(m.employee_id)):
        raise HTTPException(403, "Not allowed")
    d = (await db.execute(select(docstore.StoredDocument).where(docstore.StoredDocument.id == m.selfie_doc_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "No selfie for this punch")
    data = await db.run_sync(lambda s: d.data)
    return Response(content=data, media_type=d.content_type,
                    headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/today")
async def team_today(request: Request, day: str | None = None, db: AsyncSession = Depends(get_db)):
    """Manager / HR: every phone punch of the day for my people, with distance from site."""
    org = _org(request)
    if not (_is_hr(request) or _role(request) == "manager"):
        raise HTTPException(403, "Only a manager or HR can see the team's punches")
    wd = date.fromisoformat(day) if day else await org_today(org)
    emps = await R._fetch_employees(request)
    me = _me(request)
    if _role(request) == "manager" and me:
        emps = [e for e in emps if str(e.get("manager_id")) == me]
    by_id = {e["id"]: e for e in emps}
    if not by_id:
        return {"date": wd.isoformat(), "rows": [], "summary": {"punches": 0, "outside": 0, "late_sync": 0, "refused": 0, "people": 0}}
    rows = (await db.execute(select(PunchMobile).where(PunchMobile.org_id == org, PunchMobile.work_date == wd,
                                                      PunchMobile.employee_id.in_([uuid.UUID(k) for k in by_id]))
                             .order_by(PunchMobile.device_time.desc()))).scalars().all()
    out = [_meta_out(m, by_id.get(str(m.employee_id))) for m in rows]
    summary = {"punches": len(out), "outside": sum(1 for r in out if r["inside_fence"] is False),
               "late_sync": sum(1 for r in out if r["synced_late"]), "refused": sum(1 for r in out if r["refused"]),
               "people": len({r["employee_id"] for r in out})}
    return {"date": wd.isoformat(), "rows": out, "summary": summary}
