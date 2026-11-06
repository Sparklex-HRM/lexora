"""Employee service — Master Data + Roles.

Org-scoped reference data the HRM draws on: departments, designations (job
titles), locations, leave types — plus the read-only RBAC role catalog.
"""
import sys
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from database import get_db  # noqa: E402
from shared.utils.roles import HR_WRITE, require_roles  # noqa: E402
from masters_models import (  # noqa: E402
    BenefitPlan, CostCenter, Department, Designation, FiscalYear, Grade,
    LeaveType, Location, PayComponent, StatHoliday,
)

router = APIRouter(prefix="/masters", tags=["masters"])

RESOURCES = {
    "departments": Department,
    "designations": Designation,
    "grades": Grade,
    "locations": Location,
    "leave-types": LeaveType,
    "pay-components": PayComponent,
    "fiscal-years": FiscalYear,
    "cost-centers": CostCenter,
    "benefit-plans": BenefitPlan,
    "stat-holidays": StatHoliday,
}


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
        elif hasattr(v, "__float__") and not isinstance(v, bool) and col.name not in ("carryover_max",):
            try:
                v = float(v)
            except (TypeError, ValueError):
                pass
        out[col.name] = v
    return out


def _coerce(model, payload: dict) -> dict:
    """Turn wire values into column-typed values: ISO date strings → date,
    empty strings on Date/Numeric → None. (Booleans/Numerics pass through.)"""
    coltypes = {c.name: c.type.__class__.__name__ for c in model.__table__.columns}
    out = {}
    for k, v in payload.items():
        kind = coltypes.get(k)
        if kind == "Date":
            if v in (None, ""):
                v = None
            elif isinstance(v, str):
                try:
                    v = date.fromisoformat(v[:10])
                except ValueError:
                    raise HTTPException(400, f"{k}: expected a date (YYYY-MM-DD)")
        elif kind in ("Numeric", "Integer") and v == "":
            v = None
        out[k] = v
    return out


# ── RBAC role catalog (mirrors auth/constants.py ROLES) ─────────────────────
ROLE_CATALOG = [
    {"key": "org_admin",     "label": "Org Admin",     "scope": "Organization",
     "description": "Full control of the organization — people, payroll, settings, users.",
     "permissions": ["org:*", "user:*", "employee:*", "payroll:*", "report:read"]},
    {"key": "hr_admin",      "label": "HR Admin",      "scope": "HR",
     "description": "Runs HR end-to-end: employees, onboarding, leave, time, approvals.",
     "permissions": ["employee:*", "onboarding:*", "leave:*", "time:*", "approval:*", "report:read"]},
    {"key": "hr_manager",    "label": "HR Manager",    "scope": "HR (approve)",
     "description": "Manages onboarding and approves leave/time; read-only on employees.",
     "permissions": ["employee:read", "onboarding:*", "leave:approve", "time:approve", "approval:*"]},
    {"key": "payroll_admin", "label": "Payroll Admin", "scope": "Payroll",
     "description": "Owns payroll runs, remittances and pay data; read-only on employees.",
     "permissions": ["payroll:*", "employee:read", "report:read"]},
    {"key": "manager",       "label": "Manager",       "scope": "Team",
     "description": "Approves their team's leave and timesheets; read-only on their reports.",
     "permissions": ["employee:read", "leave:approve", "time:approve", "approval:act"]},
    {"key": "employee",      "label": "Employee",      "scope": "Self",
     "description": "Self-service: view own profile, request leave, submit timesheets.",
     "permissions": ["self:read", "self:write", "leave:request", "time:submit"]},
    {"key": "superadmin",    "label": "Platform Admin", "scope": "Platform",
     "description": "Above the org layer — platform operator across all tenants.",
     "permissions": ["*"]},
]

# Starter master data seeded on demand so a fresh org isn't empty.
DEFAULTS = {
    "departments": [
        {"name": "Operations", "code": "OPS", "cost_center": "CC-200", "gl_code": "5000"},
        {"name": "Finance", "code": "FIN", "cost_center": "CC-100", "gl_code": "5100"},
        {"name": "Human Resources", "code": "HR", "cost_center": "CC-100", "gl_code": "5200"},
        {"name": "Sales", "code": "SAL", "cost_center": "CC-300", "gl_code": "5300"},
        {"name": "Engineering", "code": "ENG", "cost_center": "CC-400", "gl_code": "5400"},
        {"name": "Customer Support", "code": "CS", "cost_center": "CC-200", "gl_code": "5500"},
    ],
    "designations": [
        {"name": "Associate", "code": "ASSOC", "level": "Junior", "job_family": "Individual Contributor", "overtime_exempt": False},
        {"name": "Specialist", "code": "SPEC", "level": "Mid", "job_family": "Individual Contributor", "overtime_exempt": False},
        {"name": "Senior Specialist", "code": "SR-SPEC", "level": "Senior", "job_family": "Individual Contributor", "overtime_exempt": False},
        {"name": "Team Lead", "code": "LEAD", "level": "Lead", "job_family": "Management", "overtime_exempt": True},
        {"name": "Manager", "code": "MGR", "level": "Manager", "job_family": "Management", "overtime_exempt": True},
        {"name": "Director", "code": "DIR", "level": "Exec", "job_family": "Executive", "overtime_exempt": True},
    ],
    "locations": [
        {"name": "Toronto HQ", "code": "TOR", "city": "Toronto", "province": "ON", "postal_code": "M5H 2N2", "timezone": "America/Toronto", "is_remote": False},
        {"name": "Vancouver Office", "code": "VAN", "city": "Vancouver", "province": "BC", "postal_code": "V6B 1A1", "timezone": "America/Vancouver", "is_remote": False},
        {"name": "Calgary Office", "code": "CAL", "city": "Calgary", "province": "AB", "postal_code": "T2P 1J9", "timezone": "America/Edmonton", "is_remote": False},
        {"name": "Remote (Canada)", "code": "REM", "city": "", "province": "ON", "timezone": "America/Toronto", "is_remote": True},
    ],
    "leave-types": [
        {"name": "Vacation", "code": "VAC", "days_per_year": 15, "paid": True, "accrual": "annual", "carryover_max": 5},
        {"name": "Sick Leave", "code": "SICK", "days_per_year": 10, "paid": True, "accrual": "annual", "carryover_max": 0},
        {"name": "Personal Day", "code": "PERS", "days_per_year": 3, "paid": True, "accrual": "annual", "carryover_max": 0},
        {"name": "Bereavement", "code": "BRV", "days_per_year": 5, "paid": True, "accrual": "annual", "carryover_max": 0},
        {"name": "Unpaid Leave", "code": "UNP", "days_per_year": 0, "paid": False, "accrual": "annual", "carryover_max": 0},
    ],
    # ── the payroll vocabulary. Statutory flags govern how each code hits
    #    CPP (pensionable) / EI (insurable) / tax (taxable) / vacation accrual. ──
    "pay-components": [
        {"name": "Regular Pay", "code": "REG", "kind": "earning", "calc_method": "rate_x_hours",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": True, "t4_box": "14", "gl_account": "6000"},
        {"name": "Salary", "code": "SAL", "kind": "earning", "calc_method": "flat",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": True, "t4_box": "14", "gl_account": "6000"},
        {"name": "Overtime (1.5×)", "code": "OT", "kind": "earning", "calc_method": "rate_x_hours",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": True, "t4_box": "14", "gl_account": "6010"},
        {"name": "Overtime (2× double-time)", "code": "OT2", "kind": "earning", "calc_method": "rate_x_hours",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": True, "t4_box": "14", "gl_account": "6011"},
        {"name": "Statutory Holiday Pay", "code": "STAT", "kind": "earning", "calc_method": "flat",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": False, "t4_box": "14", "gl_account": "6020"},
        {"name": "Vacation Pay", "code": "VACP", "kind": "earning", "calc_method": "percent",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": False, "t4_box": "14", "gl_account": "6030"},
        {"name": "Bonus", "code": "BON", "kind": "earning", "calc_method": "flat", "non_periodic": True,
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": False, "t4_box": "14", "gl_account": "6040"},
        {"name": "Commission", "code": "COMM", "kind": "earning", "calc_method": "flat", "non_periodic": True,
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": False, "t4_box": "42", "gl_account": "6050"},
        {"name": "Retroactive Pay", "code": "RETRO", "kind": "earning", "calc_method": "flat", "non_periodic": True,
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": True, "t4_box": "14", "gl_account": "6060"},
        {"name": "Taxable Benefit (Life)", "code": "TBEN", "kind": "earning", "calc_method": "flat",
         "taxable": True, "pensionable": True, "insurable": False, "vacationable": False, "t4_box": "40", "gl_account": "6070"},
        # deductions
        {"name": "RRSP (employee, pre-tax)", "code": "RRSP", "kind": "deduction", "calc_method": "percent",
         "taxable": False, "pensionable": True, "insurable": True, "vacationable": False, "t4_box": "20", "gl_account": "2100"},
        # union dues reduce income for withholding (CRA T4127 factor U1) → pre-tax (taxable=False)
        {"name": "Union Dues", "code": "DUES", "kind": "deduction", "calc_method": "flat",
         "taxable": False, "pensionable": True, "insurable": True, "vacationable": False, "t4_box": "44", "gl_account": "2110"},
        {"name": "Health/Dental Premium", "code": "BENP", "kind": "deduction", "calc_method": "flat",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": False, "gl_account": "2120"},
        {"name": "Garnishment", "code": "GARN", "kind": "deduction", "calc_method": "flat",
         "taxable": True, "pensionable": True, "insurable": True, "vacationable": False, "gl_account": "2130"},
        # employer costs (do not reduce net pay; tracked for remittance/GL — the
        # tax/CPP/EI/vac flags don't apply to an employer cost, so leave them off)
        {"name": "Employer CPP", "code": "ER-CPP", "kind": "employer", "taxable": False, "pensionable": False, "insurable": False, "vacationable": False, "gl_account": "6200"},
        {"name": "Employer EI (1.4×)", "code": "ER-EI", "kind": "employer", "taxable": False, "pensionable": False, "insurable": False, "vacationable": False, "gl_account": "6210"},
        {"name": "Employer RRSP Match", "code": "ER-RRSP", "kind": "employer", "taxable": False, "pensionable": False, "insurable": False, "vacationable": False, "gl_account": "6220"},
        {"name": "Employer Health Tax (ON)", "code": "EHT", "kind": "employer", "taxable": False, "pensionable": False, "insurable": False, "vacationable": False, "gl_account": "6230"},
        {"name": "WSIB", "code": "WSIB", "kind": "employer", "taxable": False, "pensionable": False, "insurable": False, "vacationable": False, "gl_account": "6240"},
    ],
    "fiscal-years": [
        {"name": "FY2026", "code": "2026", "start_date": date(2026, 1, 1), "end_date": date(2026, 12, 31), "status": "open", "is_current": True},
        {"name": "FY2025", "code": "2025", "start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "status": "closed", "is_current": False},
    ],
    "cost-centers": [
        {"name": "Corporate", "code": "CC-100", "gl_segment": "100"},
        {"name": "Operations", "code": "CC-200", "gl_segment": "200"},
        {"name": "Sales & Marketing", "code": "CC-300", "gl_segment": "300"},
        {"name": "Research & Development", "code": "CC-400", "gl_segment": "400"},
    ],
    "benefit-plans": [
        {"name": "Health & Dental (Standard)", "code": "HD-STD", "carrier": "Sun Life", "ee_cost": 45, "er_cost": 90, "taxable_benefit": False},
        {"name": "Health & Dental (Family)", "code": "HD-FAM", "carrier": "Sun Life", "ee_cost": 85, "er_cost": 170, "taxable_benefit": False},
        {"name": "Group RRSP Match (3%)", "code": "GRRSP", "carrier": "Manulife", "ee_cost": 0, "er_cost": 0, "taxable_benefit": False},
        {"name": "Group Life Insurance", "code": "LIFE", "carrier": "Canada Life", "ee_cost": 0, "er_cost": 12, "taxable_benefit": True},
        {"name": "Long-Term Disability", "code": "LTD", "carrier": "Canada Life", "ee_cost": 18, "er_cost": 0, "taxable_benefit": False},
    ],
    # Ontario statutory holidays, 2026 (9 public holidays).
    "stat-holidays": [
        {"name": "New Year's Day", "code": "NYD", "holiday_date": date(2026, 1, 1), "province": "ON", "year": 2026},
        {"name": "Family Day", "code": "FAM", "holiday_date": date(2026, 2, 16), "province": "ON", "year": 2026},
        {"name": "Good Friday", "code": "GF", "holiday_date": date(2026, 4, 3), "province": "ON", "year": 2026},
        {"name": "Victoria Day", "code": "VIC", "holiday_date": date(2026, 5, 18), "province": "ON", "year": 2026},
        {"name": "Canada Day", "code": "CAN", "holiday_date": date(2026, 7, 1), "province": "ON", "year": 2026},
        {"name": "Labour Day", "code": "LAB", "holiday_date": date(2026, 9, 7), "province": "ON", "year": 2026},
        {"name": "Thanksgiving", "code": "THX", "holiday_date": date(2026, 10, 12), "province": "ON", "year": 2026},
        {"name": "Christmas Day", "code": "XMAS", "holiday_date": date(2026, 12, 25), "province": "ON", "year": 2026},
        {"name": "Boxing Day", "code": "BOX", "holiday_date": date(2026, 12, 26), "province": "ON", "year": 2026},
        # British Columbia, 2026 (TODO-VERIFY)
        {"name": "New Year's Day", "code": "NYD", "holiday_date": date(2026, 1, 1), "province": "BC", "year": 2026},
        {"name": "Family Day", "code": "FAM", "holiday_date": date(2026, 2, 16), "province": "BC", "year": 2026},
        {"name": "Good Friday", "code": "GF", "holiday_date": date(2026, 4, 3), "province": "BC", "year": 2026},
        {"name": "Victoria Day", "code": "VIC", "holiday_date": date(2026, 5, 18), "province": "BC", "year": 2026},
        {"name": "Canada Day", "code": "CAN", "holiday_date": date(2026, 7, 1), "province": "BC", "year": 2026},
        {"name": "BC Day", "code": "BCD", "holiday_date": date(2026, 8, 3), "province": "BC", "year": 2026},
        {"name": "Labour Day", "code": "LAB", "holiday_date": date(2026, 9, 7), "province": "BC", "year": 2026},
        {"name": "Truth & Reconciliation", "code": "TRC", "holiday_date": date(2026, 9, 30), "province": "BC", "year": 2026},
        {"name": "Thanksgiving", "code": "THX", "holiday_date": date(2026, 10, 12), "province": "BC", "year": 2026},
        {"name": "Remembrance Day", "code": "REM", "holiday_date": date(2026, 11, 11), "province": "BC", "year": 2026},
        {"name": "Christmas Day", "code": "XMAS", "holiday_date": date(2026, 12, 25), "province": "BC", "year": 2026},
        # Alberta, 2026 (TODO-VERIFY)
        {"name": "New Year's Day", "code": "NYD", "holiday_date": date(2026, 1, 1), "province": "AB", "year": 2026},
        {"name": "Family Day", "code": "FAM", "holiday_date": date(2026, 2, 16), "province": "AB", "year": 2026},
        {"name": "Good Friday", "code": "GF", "holiday_date": date(2026, 4, 3), "province": "AB", "year": 2026},
        {"name": "Victoria Day", "code": "VIC", "holiday_date": date(2026, 5, 18), "province": "AB", "year": 2026},
        {"name": "Canada Day", "code": "CAN", "holiday_date": date(2026, 7, 1), "province": "AB", "year": 2026},
        {"name": "Labour Day", "code": "LAB", "holiday_date": date(2026, 9, 7), "province": "AB", "year": 2026},
        {"name": "Thanksgiving", "code": "THX", "holiday_date": date(2026, 10, 12), "province": "AB", "year": 2026},
        {"name": "Remembrance Day", "code": "REM", "holiday_date": date(2026, 11, 11), "province": "AB", "year": 2026},
        {"name": "Christmas Day", "code": "XMAS", "holiday_date": date(2026, 12, 25), "province": "AB", "year": 2026},
    ],
}


@router.get("/roles")
async def list_roles():
    return ROLE_CATALOG


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    out = {}
    for key, model in RESOURCES.items():
        res = await db.execute(select(model).where(model.org_id == org, model.is_active.is_(True)))
        out[key] = len(res.scalars().all())
    out["roles"] = len(ROLE_CATALOG)
    return out


@router.post("/seed-defaults", status_code=201)
async def seed_defaults(request: Request, db: AsyncSession = Depends(get_db)):
    """Idempotent-ish: only seeds a resource that is currently empty for the org."""
    require_roles(request, HR_WRITE, "seed master data")
    org = _org(request)
    added = {}
    for key, model in RESOURCES.items():
        existing = (await db.execute(
            select(model).where(model.org_id == org))).scalars().first()
        if existing:
            added[key] = 0
            continue
        rows = [model(org_id=org, **item) for item in DEFAULTS.get(key, [])]
        db.add_all(rows)
        added[key] = len(rows)
    await db.commit()
    return {"seeded": added}


@router.get("/{resource}")
async def list_master(resource: str, request: Request, include_inactive: bool = False,
                      db: AsyncSession = Depends(get_db)):
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown master '{resource}'")
    org = _org(request)
    q = select(model).where(model.org_id == org)
    if not include_inactive:
        q = q.where(model.is_active.is_(True))
    res = await db.execute(q.order_by(model.is_active.desc(), model.name))
    return [_serialize(r) for r in res.scalars().all()]


@router.post("/{resource}", status_code=201)
async def create_master(resource: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, HR_WRITE, "add master data")
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown master '{resource}'")
    org = _org(request)
    cols = {c.name for c in model.__table__.columns} - {"id", "org_id", "created_at", "is_active"}
    payload = _coerce(model, {k: v for k, v in body.items() if k in cols})
    if not payload.get("name"):
        raise HTTPException(400, "name is required")
    row = model(org_id=org, **payload)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize(row)


@router.patch("/{resource}/{item_id}")
async def update_master(resource: str, item_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Edit fields (name/code/description) and/or toggle is_active."""
    require_roles(request, HR_WRITE, "change master data")
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown master '{resource}'")
    org = _org(request)
    row = (await db.execute(
        select(model).where(model.id == item_id, model.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")
    cols = {c.name for c in model.__table__.columns} - {"id", "org_id", "created_at"}
    payload = _coerce(model, {k: v for k, v in body.items() if k in cols})
    for k, v in payload.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return _serialize(row)


@router.delete("/{resource}/{item_id}")
async def delete_master(resource: str, item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Permanent delete. (Use PATCH is_active=false to soft-deactivate instead.)"""
    require_roles(request, HR_WRITE, "delete master data")
    model = RESOURCES.get(resource)
    if not model:
        raise HTTPException(404, f"Unknown master '{resource}'")
    org = _org(request)
    row = (await db.execute(
        select(model).where(model.id == item_id, model.org_id == org))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True, "id": item_id}
