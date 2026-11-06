"""Operations / Staffing — generic org-scoped CRUD across the PSA resources."""
import sys
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Date, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from database import get_db  # noqa: E402
from models import (  # noqa: E402
    Branch, Client, Document, Invoice, Policy, Project, RosterAssignment, Task, Vendor)

router = APIRouter(prefix="/ops", tags=["ops"])

from shared.utils.roles import ALL_STAFF, APPROVERS, FINANCE, HR_WRITE, OFFICE, has_role, require_roles  # noqa: E402

RESOURCES = {
    "branches": Branch, "clients": Client, "projects": Project,
    "vendors": Vendor, "roster": RosterAssignment, "invoices": Invoice,
    "documents": Document, "tasks": Task, "policies": Policy,
}

# Server-side role gates (mirror the frontend menu, frontend/src/components/DashboardLayout.tsx).
# READ: branches / projects / policies are needed by every staff member (employee form,
# time sheets against projects, policy library). Roster carries pay + bill rates, invoices
# carry money — kept to the people who manage them.
INVOICE_ROLES = FINANCE + ("hr_admin",)
READ_ROLES = {
    "branches": ALL_STAFF, "projects": ALL_STAFF, "policies": ALL_STAFF,
    "clients": APPROVERS, "roster": APPROVERS, "tasks": APPROVERS,
    "vendors": OFFICE, "documents": OFFICE, "invoices": INVOICE_ROLES,
}
WRITE_ROLES = {
    "branches": HR_WRITE, "vendors": HR_WRITE, "documents": HR_WRITE, "policies": HR_WRITE,
    "clients": HR_WRITE + ("manager",), "projects": HR_WRITE + ("manager",), "roster": HR_WRITE + ("manager",),
    "tasks": HR_WRITE + ("manager", "payroll"), "invoices": INVOICE_ROLES,
}
SUMMARY_ROLES = OFFICE          # counts only; no screen calls it — manager removed (WASA 2026-10-21)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _serialize(row) -> dict:
    out = {}
    for col in row.__table__.columns:
        v = getattr(row, col.name)
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, bool):
            v = float(v)
        out[col.name] = v
    # roster margin, invoice net
    if isinstance(row, RosterAssignment):
        out["margin"] = round(float(row.bill_rate or 0) - float(row.pay_rate or 0), 2)
    return out


def _coerce(model, body: dict) -> dict:
    cols = {c.name: c for c in model.__table__.columns}
    allowed = set(cols) - {"id", "org_id", "created_at", "is_active"}
    payload = {}
    for k, v in body.items():
        if k not in allowed:
            continue
        if isinstance(cols[k].type, Date) and isinstance(v, str):
            v = date.fromisoformat(v) if v else None
        payload[k] = v
    return payload


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    role = require_roles(request, SUMMARY_ROLES, "see the operations summary")
    out = {}
    for key, model in RESOURCES.items():
        if not has_role(role, READ_ROLES[key]):      # count only what this role may list (manager: no vendors/docs/invoices)
            continue
        rows = (await db.execute(select(model).where(model.org_id == org, model.is_active.is_(True)))).scalars().all()
        out[key] = len(rows)
    return out


@router.get("/{resource}")
async def list_resource(resource: str, request: Request, db: AsyncSession = Depends(get_db)):
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown resource '{resource}'")
    org = _org(request)
    require_roles(request, READ_ROLES[resource], f"see {resource}")
    rows = (await db.execute(
        select(model).where(model.org_id == org, model.is_active.is_(True)).order_by(model.created_at.desc()))).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("/{resource}", status_code=201)
async def create_resource(resource: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown resource '{resource}'")
    org = _org(request)
    require_roles(request, WRITE_ROLES[resource], f"add {resource}")
    payload = _coerce(model, body)
    if not payload.get("name"):
        raise HTTPException(400, "name is required")
    if model is Invoice and not payload.get("total"):
        payload["total"] = round(float(payload.get("amount") or 0) + float(payload.get("tax") or 0), 2)
    row = model(org_id=org, **payload)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize(row)


@router.delete("/{resource}/{item_id}")
async def delete_resource(resource: str, item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown resource '{resource}'")
    org = _org(request)
    require_roles(request, WRITE_ROLES[resource], f"remove {resource}")
    row = (await db.execute(select(model).where(model.id == item_id, model.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")
    row.is_active = False
    await db.commit()
    return {"ok": True, "id": item_id}
