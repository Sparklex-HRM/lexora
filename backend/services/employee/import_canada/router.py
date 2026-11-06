"""Bulk employee upsert / restore + salary-history insert for the CANADA migration kit.

POST /employees/import-canada/bulk-upsert     {batch_id, rows:[{employee_number, ...}]}
    → {items:[{employee_number, employee_id, action: insert|update, prior:{field: old}}]}
    Match = org + employee_number. Only non-null fields are written. New rows default
    to address_country CA, biweekly, salary. Province must be a Canadian province.
    bank_account goes through sensitive.set_field (encrypted + masked).
POST /employees/import-canada/bulk-restore    {items:[{employee_id, action, prior}]}
POST /employees/import-canada/comp-history    {batch_id, rows:[{employee_id, effective_from, effective_to, pay_type,
                                               annual_amount, hourly_rate, reason, note}]}
    → {items:[{id, employee_id, action: insert}]}   (source='opening' rows; a duplicate effective_from is replaced)
POST /employees/import-canada/comp-history/restore {ids:[...]}   deletes the rows a batch inserted.
"""
import logging
import sys
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.enums import PROVINCES_ALL  # noqa: E402

import sensitive  # noqa: E402
from comp_models import CompHistory  # noqa: E402
from database import get_db  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/employees/import-canada", tags=["canada-migration"])

_ALLOWED_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "hr_staff", "branch_admin", "payroll_admin"}
_DATE_FIELDS = {"hire_date", "termination_date", "date_of_birth", "work_permit_expiry", "probation_end_date"}
_NUM_FIELDS = {"annual_salary", "hourly_rate", "standard_hours_per_week", "fte_percent", "vacation_percent",
               "td1_federal_claim", "td1_provincial_claim", "additional_tax_per_period"}
_BOOL_FIELDS = {"cpp_exempt", "ei_exempt", "is_union", "overtime_eligible"}
_SENSITIVE = ("bank_account",)
_STORAGE_COLS = set()
for _spec in sensitive.FIELDS.values():
    for _k in ("plain", "enc", "hash", "mask"):
        if _spec.get(_k):
            _STORAGE_COLS.add(_spec[_k])
_NEVER = {"id", "org_id", "created_at", "enc_key_id", "erased_at", "erasure_hold", "employee_number"} | _STORAGE_COLS
_WRITABLE = {c.name for c in Employee.__table__.columns} - _NEVER


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _guard(request: Request):
    role = (request.headers.get("x-role") or "").lower()
    if role not in _ALLOWED_ROLES:
        raise HTTPException(403, "Only HR / payroll admins can import employees")


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


def _coerce(k: str, v):
    if v is None:
        return None
    if k in _DATE_FIELDS:
        return date.fromisoformat(str(v)[:10]) if v else None
    if k in _NUM_FIELDS:
        return float(v)
    if k in _BOOL_FIELDS:
        return bool(v)
    if k in ("branch_id", "manager_id", "pay_group_id"):
        return uuid.UUID(str(v))
    return v


class BulkUpsertIn(BaseModel):
    batch_id: str | None = None
    rows: list[dict]


class RestoreItem(BaseModel):
    employee_id: str
    action: str
    prior: dict | None = None


class BulkRestoreIn(BaseModel):
    items: list[RestoreItem]


@router.post("/bulk-upsert")
async def bulk_upsert(body: BulkUpsertIn, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    org = _org(request)
    if len(body.rows) > 20000:
        raise HTTPException(400, "Too many rows in one call (max 20,000)")
    existing = (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()
    by_code = {(e.employee_number or "").strip().upper(): e for e in existing}
    items = []
    try:
        for raw in body.rows:
            code = str(raw.get("employee_number") or "").strip().upper()
            if not code:
                raise HTTPException(400, "A row has no employee_number")
            sens = {k: (str(raw[k]).strip() or None) for k in _SENSITIVE if raw.get(k) is not None}
            fields = {k: v for k, v in raw.items() if k in _WRITABLE and v is not None}
            prov = fields.get("province_of_employment")
            if prov and str(prov).upper() not in PROVINCES_ALL:
                raise HTTPException(400, f"{code}: province '{prov}' is not a Canadian province/territory")
            e = by_code.get(code)
            if e is None:
                e = Employee(org_id=org, employee_number=code)
                e.address_country = "CA"
                e.pay_frequency = "biweekly"
                e.pay_type = "salary"
                e.province_of_employment = "ON"
                e.status = "active"
                for k, v in fields.items():
                    setattr(e, k, _coerce(k, v))
                for f, v in sens.items():
                    sensitive.set_field(e, f, v)
                db.add(e)
                await db.flush()
                by_code[code] = e
                items.append({"employee_number": code, "employee_id": str(e.id), "action": "insert", "prior": None})
            else:
                prior = {}
                for k, v in fields.items():
                    new = _coerce(k, v)
                    old = getattr(e, k)
                    if _jsonable(old) != _jsonable(new):
                        prior[k] = _jsonable(old)
                        setattr(e, k, new)
                for f, v in sens.items():
                    old = sensitive.get_field(e, f)
                    if (old or None) != (v or None):
                        prior[f] = old
                        sensitive.set_field(e, f, v)
                if not e.is_active:
                    prior["is_active"] = False
                    e.is_active = True
                items.append({"employee_number": code, "employee_id": str(e.id), "action": "update", "prior": prior})
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"items": items, "inserted": sum(1 for i in items if i["action"] == "insert"),
            "updated": sum(1 for i in items if i["action"] == "update")}


@router.post("/bulk-restore")
async def bulk_restore(body: BulkRestoreIn, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    org = _org(request)
    deleted = restored = 0
    try:
        for it in body.items:
            e = (await db.execute(select(Employee).where(
                Employee.id == uuid.UUID(it.employee_id), Employee.org_id == org))).scalar_one_or_none()
            if not e:
                continue
            if it.action == "insert":
                await db.delete(e)
                deleted += 1
            else:
                for k, v in (it.prior or {}).items():
                    if k in _SENSITIVE:
                        sensitive.set_field(e, k, v)
                    elif k in _WRITABLE:
                        setattr(e, k, _coerce(k, v))
                restored += 1
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"deleted": deleted, "restored": restored}


class CompHistoryIn(BaseModel):
    batch_id: str | None = None
    rows: list[dict]


class CompRestoreIn(BaseModel):
    ids: list[str]


@router.post("/comp-history")
async def comp_history_bulk(body: CompHistoryIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Insert opening salary-history rows. A row with the same employee + effective_from
    is REPLACED (its prior values are returned so the batch can restore them)."""
    _guard(request)
    org = _org(request)
    who = request.headers.get("x-user-email") or request.headers.get("x-user-id")
    items = []
    try:
        for r in body.rows:
            eid = uuid.UUID(str(r["employee_id"]))
            eff = date.fromisoformat(str(r["effective_from"])[:10])
            row = (await db.execute(select(CompHistory).where(
                CompHistory.org_id == org, CompHistory.employee_id == eid, CompHistory.effective_from == eff))).scalar_one_or_none()
            vals = {"effective_to": date.fromisoformat(str(r["effective_to"])[:10]) if r.get("effective_to") else None,
                    "pay_type": r.get("pay_type") or "salary",
                    "annual_amount": float(r["annual_amount"]) if r.get("annual_amount") is not None else None,
                    "hourly_rate": float(r["hourly_rate"]) if r.get("hourly_rate") is not None else None,
                    "currency": "CAD", "reason": (r.get("reason") or "opening")[:60], "note": r.get("note"),
                    "source": "opening", "created_by": who}
            if row:
                prior = {k: _jsonable(getattr(row, k)) for k in vals}
                for k, v in vals.items():
                    setattr(row, k, v)
                items.append({"id": str(row.id), "employee_id": str(eid), "action": "update", "prior": prior})
            else:
                row = CompHistory(org_id=org, employee_id=eid, effective_from=eff, **vals)
                db.add(row)
                await db.flush()
                items.append({"id": str(row.id), "employee_id": str(eid), "action": "insert", "prior": None})
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception:  # noqa: BLE001
        await db.rollback()
        ref = uuid.uuid4().hex[:8].upper()          # driver / SQL text goes to the log only
        logging.getLogger("lexora.import_canada").exception("salary history import failed (ref %s)", ref)
        raise HTTPException(400, f"Salary history rows refused: check the dates, amounts and pay types and try again "
                                 f"(ref {ref}).")
    return {"items": items}


class CompRestoreItems(BaseModel):
    items: list[dict]


@router.post("/comp-history/restore")
async def comp_history_restore(body: CompRestoreItems, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    org = _org(request)
    deleted = restored = 0
    try:
        for it in body.items:
            row = (await db.execute(select(CompHistory).where(
                CompHistory.id == uuid.UUID(str(it["id"])), CompHistory.org_id == org))).scalar_one_or_none()
            if not row:
                continue
            if it.get("action") == "insert":
                await db.delete(row)
                deleted += 1
            else:
                for k, v in (it.get("prior") or {}).items():
                    if k == "effective_to":
                        v = date.fromisoformat(v[:10]) if v else None
                    setattr(row, k, v)
                restored += 1
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"deleted": deleted, "restored": restored}
