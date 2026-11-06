"""Employee service — CRUD, org-scoped (row-level tenancy via X-Org-Id).

Sensitive identifiers (SIN, bank account, passport) are
stored encrypted (see sensitive.py) and returned MASKED by default. A full
value is returned only when the caller asks (`?reveal=full`) AND is a
payroll_admin / org_admin / superadmin, or is reading their own record; every
such read writes a `employee.sensitive_read` audit entry.
"""
import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import false as sa_false, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import crypto  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.enums import periods_per_year  # noqa: E402

from shared.utils import identity  # noqa: E402
import ca_identity  # noqa: E402

import access  # noqa: E402
import sensitive  # noqa: E402
from database import get_db  # noqa: E402
from identity_routes import assign_number, number_in_use, sub as identity_sub  # noqa: E402
from models import DataProtectionState, Employee  # noqa: E402
from schemas import EmployeeCreate  # noqa: E402

router = APIRouter(prefix="/employees", tags=["employees"])
# Static paths (form-options, next-number) + documents — mounted here, BEFORE
# the `/{employee_id}` routes below, so they are never read as an employee id.
router.include_router(identity_sub)
# S1: GET /employees/directory — work facts of colleagues for every role (no pay / ids / personal data).
from directory_routes import directory_row, directory_sub  # noqa: E402
router.include_router(directory_sub)
# D4: GET /employees/entities (+ transfers) — mounted before /{employee_id} like the others.
from entities_routes import entities_sub, request_entity_filter  # noqa: E402
router.include_router(entities_sub)

SENSITIVE_INPUTS = ("sin", "bank_account",
                    "passport")    # S4: passport encrypted like the rest
ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin"}


def _org(request: Request) -> uuid.UUID:
    org = request.headers.get("x-org-id")
    try:
        return uuid.UUID(org or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _branch(request: Request) -> uuid.UUID | None:
    """The resolved branch scope stamped by the gateway. Non-empty → restrict to
    that branch; empty → org-wide (an org-scope user viewing all branches). A
    value that is not an id is ignored rather than crashing the query."""
    try:
        return uuid.UUID(request.headers.get("x-branch-id") or "")
    except ValueError:
        return None


def _data_scope(request: Request) -> tuple[str, str] | None:
    """2026-10-24 org Roles & Permissions: a custom role with department / location / entity reach acts here with
    X-Data-Scope "<kind>:<value>" (stamped by the gateway only — clients cannot send it). None = no narrowing."""
    raw = (request.headers.get("x-data-scope") or "").strip()
    if not raw or ":" not in raw:
        return None
    kind, val = raw.split(":", 1)
    return (kind, val) if kind in ("department", "location", "entity") and val else None


def _scope_filter(q, request: Request):
    ds = _data_scope(request)
    if not ds:
        return q
    kind, val = ds
    if kind == "department":
        return q.where(Employee.department == val)
    try:
        v = uuid.UUID(val)
    except ValueError:
        return q.where(sa_false())
    return q.where((Employee.branch_id if kind == "location" else Employee.entity_id) == v)


def _in_scope(request: Request, e) -> bool:
    ds = _data_scope(request)
    if not ds:
        return True
    kind, val = ds
    if kind == "department":
        return (e.department or "") == val
    got = e.branch_id if kind == "location" else e.entity_id
    return bool(got) and str(got) == val


def _uuid_or_404(value, what: str = "Employee") -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, f"{what} not found")


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _self_employee(request: Request) -> str:
    return request.headers.get("x-employee-id") or ""


def _can_reveal(request: Request, employee_id: str | None = None) -> bool:
    # org RBAC 2026-10-26: a custom role acting as a reveal role also needs data.identity
    if _role(request) in sensitive.REVEAL_ROLES and access.holds_data(request, "identity"):
        return True
    return bool(employee_id) and _self_employee(request) == str(employee_id)


def _sensitive_block(e: Employee, reveal: bool) -> dict:
    """The identifier fields — full or masked."""
    if reveal:
        return {
            "bank_account": sensitive.get_field(e, "bank_account"),
            "sin": sensitive.get_field(e, "sin"),
            "passport": sensitive.get_field(e, "passport"),
        }
    return {
        "passport": sensitive.masked(e, "passport"),
    }


def _out(e: Employee, reveal: bool = False) -> dict:
    return {
        "id": str(e.id), "employee_number": e.employee_number,
        "first_name": e.first_name, "last_name": e.last_name, "email": e.email,
        "date_of_birth": e.date_of_birth.isoformat() if e.date_of_birth else None,
        "gender": e.gender, "preferred_name": e.preferred_name, "pronouns": e.pronouns, "phone": e.phone,
        "sin_masked": sensitive.masked(e, "sin"),
        "work_eligibility": e.work_eligibility,
        "work_permit_expiry": e.work_permit_expiry.isoformat() if e.work_permit_expiry else None,
        "address_line1": e.address_line1, "address_line2": e.address_line2,
        "city": e.city, "postal_code": e.postal_code,
        "emergency_name": e.emergency_name, "emergency_relationship": e.emergency_relationship,
        "emergency_phone": e.emergency_phone,
        "status": e.status, "employment_type": e.employment_type,
        "province_of_employment": e.province_of_employment,
        "job_title": e.job_title, "department": e.department,
        "work_location": e.work_location,
        "branch_id": str(e.branch_id) if e.branch_id else None, "branch_name": e.branch_name,
        "entity_id": str(e.entity_id) if getattr(e, "entity_id", None) else None,     # D4 legal entity
        "manager_id": str(e.manager_id) if e.manager_id else None, "manager_name": e.manager_name,
        "pay_type": e.pay_type, "annual_salary": float(e.annual_salary or 0),
        "hourly_rate": float(e.hourly_rate or 0),
        "standard_hours_per_week": float(e.standard_hours_per_week or 0),
        "pay_frequency": e.pay_frequency,
        "pay_periods_per_year": periods_per_year(e.pay_frequency),
        "td1_federal_claim": float(e.td1_federal_claim) if e.td1_federal_claim is not None else None,
        "td1_provincial_claim": float(e.td1_provincial_claim) if e.td1_provincial_claim is not None else None,
        "bank_institution": e.bank_institution, "bank_transit": e.bank_transit,
        "bank_account_last4": e.bank_account_last4,
        "bank_account_masked": sensitive.masked(e, "bank_account"),
        "has_bank_account": sensitive.has_value(e, "bank_account"),
        "has_sin": sensitive.has_value(e, "sin"),
        "father_name": e.father_name, "spouse_name": e.spouse_name, "grade": e.grade,
        "country": (e.address_country or "").upper() or None,
        # Sensitive identifiers — masked unless revealed (see _sensitive_block)
        **_sensitive_block(e, reveal),
        "passport_masked": sensitive.masked(e, "passport"),
        "has_passport": sensitive.has_value(e, "passport"),
        "passport_expiry": e.passport_expiry.isoformat() if getattr(e, "passport_expiry", None) else None,
        "identifiers_encrypted": bool(e.enc_key_id),
        "masked": not reveal,
        "hire_date": e.hire_date.isoformat() if e.hire_date else None,
        "termination_date": e.termination_date.isoformat() if e.termination_date else None,
        # Extended intake
        "middle_name": e.middle_name, "marital_status": e.marital_status,
        "work_email": e.work_email, "home_phone": e.home_phone,
        "province_of_residence": e.province_of_residence, "address_country": e.address_country,
        "work_permit_type": e.work_permit_type, "work_permit_number": e.work_permit_number,
        "probation_end_date": e.probation_end_date.isoformat() if e.probation_end_date else None,
        "contract_start_date": e.contract_start_date.isoformat() if getattr(e, "contract_start_date", None) else None,
        "contract_end_date": e.contract_end_date.isoformat() if getattr(e, "contract_end_date", None) else None,
        "contract_notice_days": getattr(e, "contract_notice_days", None), "contract_ref": getattr(e, "contract_ref", None),
        "fte_percent": float(e.fte_percent) if e.fte_percent is not None else None,
        "overtime_eligible": e.overtime_eligible,
        "vacation_percent": float(e.vacation_percent) if e.vacation_percent is not None else None,
        "division": e.division, "cost_center": e.cost_center,
        "is_union": e.is_union, "union_local": e.union_local,
        "cpp_exempt": e.cpp_exempt, "ei_exempt": e.ei_exempt,
        "additional_tax_per_period": float(e.additional_tax_per_period) if e.additional_tax_per_period is not None else 0,
        # Privacy (nominee + erasure)
        "nominee_name": e.nominee_name, "nominee_relationship": e.nominee_relationship,
        "nominee_phone": e.nominee_phone,
        "erasure_hold": bool(e.erasure_hold), "erased_at": e.erased_at.isoformat() if e.erased_at else None,
    }


async def _check_unique(db: AsyncSession, org: str, e: Employee, field: str, value: str | None):
    """A blind-indexed identifier must be unique within the org — compared on the blind index."""
    if not value:
        return
    spec = sensitive.FIELDS[field]
    h = crypto.blind_index(value, scope=field)
    col = getattr(Employee, spec["hash"])
    q = select(Employee.id).where(Employee.org_id == org, col == h, Employee.is_active.is_(True))
    if e.id:
        q = q.where(Employee.id != e.id)
    if (await db.execute(q)).first():
        raise HTTPException(409, f"{field.upper()} is already used by another employee in this organization")


def _fail(errors: dict):
    """422 with one plain sentence per bad field."""
    raise HTTPException(422, " ".join(f"{m}." for m in errors.values()))


async def _apply_sensitive(db: AsyncSession, org, e: Employee, values: dict):
    """Shape-check (Canadian rules), then encrypt + mask. Never logged."""
    cleaned, errors = ca_identity.clean_identity_fields(values)
    if "passport" in values:                     # S4: not in the shared validators
        try:
            cleaned["passport"] = sensitive.clean_passport(values["passport"])
        except ValueError as exc:
            errors["passport"] = str(exc)
    if errors:
        _fail(errors)
    values = {**values, **cleaned}
    for field, spec in sensitive.FIELDS.items():
        if spec["hash"] and field in values:
            await _check_unique(db, org, e, field, values[field])
    for field, v in values.items():
        sensitive.set_field(e, field, v)
        if field == "bank_account":
            e.bank_account_last4 = crypto.last_n(v, 4) if v else None


def _clean_plain(country: str | None, data: dict):
    """Transit / institution are not secret but still shape-checked."""
    present = {k: data[k] for k in ("bank_transit", "bank_institution") if k in data}
    cleaned, errors = ca_identity.clean_identity_fields(present)
    if "postal_code" in data:                  # Canadian postal code A1A 1A1, stored normalised
        from shared.utils.id_validators import clean_postal_code
        try:
            cleaned["postal_code"] = clean_postal_code(data["postal_code"])
        except ValueError as exc:
            errors["postal_code"] = str(exc)
    if errors:
        _fail(errors)
    data.update(cleaned)


def _valid_region(country: str, region: str | None) -> bool:
    return ca_identity.valid_province(region)


async def _resolve_manager(db: AsyncSession, org, e: Employee, manager_id, manager_name):
    """Reporting manager = a real employee of the org, saved as `manager_id`
    (the name is kept beside it for display). Text alone is linked only when it
    matches exactly one employee."""
    from shared.utils.employee_fixes import match_manager
    if manager_id in (None, ""):
        e.manager_id = None
        e.manager_name = (manager_name or "").strip() or None
        if e.manager_name:
            rows = (await db.execute(select(Employee).where(
                Employee.org_id == org, Employee.is_active.is_(True)))).scalars().all()
            hit = match_manager(e.manager_name, e.id, [
                {"id": r.id, "first_name": r.first_name, "last_name": r.last_name, "middle_name": r.middle_name,
                 "preferred_name": r.preferred_name, "employee_number": r.employee_number,
                 "email": r.email, "work_email": r.work_email} for r in rows])
            if hit["action"] == "fix":
                e.manager_id, e.manager_name = uuid.UUID(hit["manager_id"]), hit["manager_name"]
        return
    mid = _uuid_or_404(manager_id, "Manager")
    if e.id and mid == e.id:
        raise HTTPException(422, "An employee cannot report to themselves")
    m = (await db.execute(select(Employee).where(
        Employee.id == mid, Employee.org_id == org, Employee.is_active.is_(True)))).scalar_one_or_none()
    if not m:
        raise HTTPException(422, "Pick the reporting manager from the employee list")
    # Walk up from the chosen manager: this employee must not already be above them.
    seen, cur = set(), m
    while cur is not None and cur.manager_id and cur.id not in seen and len(seen) < 50:
        seen.add(cur.id)
        if e.id and cur.manager_id == e.id:
            raise HTTPException(422, f"{m.first_name} {m.last_name} already reports to this employee")
        cur = (await db.execute(select(Employee).where(
            Employee.id == cur.manager_id, Employee.org_id == org))).scalar_one_or_none()
    e.manager_id, e.manager_name = m.id, f"{m.first_name} {m.last_name}".strip()


# ── Static routes (declared BEFORE /{employee_id}) ───────────────────────────

@router.get("/encryption-status")
async def encryption_status(request: Request, db: AsyncSession = Depends(get_db)):
    """What the compliance dashboard shows: key state, backfill, cutover, rows
    still holding plaintext."""
    st = (await db.execute(select(DataProtectionState).where(DataProtectionState.id == 1))).scalar_one_or_none()
    org = request.headers.get("x-org-id")
    rows = (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all() if org else []
    pending = sum(1 for e in rows if any(
        getattr(e, spec["plain"]) and not getattr(e, spec["enc"])
        for spec in sensitive.FIELDS.values() if spec["plain"]))
    encrypted_rows = sum(1 for e in rows if e.enc_key_id)
    plaintext_present = sum(1 for e in rows if any(
        spec["plain"] and getattr(e, spec["plain"]) for spec in sensitive.FIELDS.values()))
    return {**crypto.status(),
            "enabled": True,
            "backfilled_at": st.backfilled_at.isoformat() if st and st.backfilled_at else None,
            "cutover_done": bool(st and st.cutover_done_at),
            "cutover_done_at": st.cutover_done_at.isoformat() if st and st.cutover_done_at else None,
            "rolled_back_at": st.rolled_back_at.isoformat() if st and st.rolled_back_at else None,
            "org_rows": len(rows), "org_rows_encrypted": encrypted_rows,
            "org_rows_pending_backfill": pending,
            "org_rows_with_plaintext": plaintext_present,
            "fields": list(sensitive.FIELDS.keys()),
            "launch_ready": (not crypto.status()["dev_fallback"]) and pending == 0}


@router.post("", status_code=201)
async def create_employee(body: EmployeeCreate, request: Request, db: AsyncSession = Depends(get_db)):
    access.require_hr_write(request)

    data = body.model_dump()
    status = data.pop("status", None)
    sens = {k: data.pop(k, None) for k in SENSITIVE_INPUTS}
    data.pop("bank_account_last4", None)       # derived from the account number
    manager_id, manager_name = data.pop("manager_id", None), data.pop("manager_name", None)
    number = (data.pop("employee_number", None) or "").strip()
    # Drop unset values so DB column defaults apply (esp. non-null bool columns).
    data = {k: v for k, v in data.items() if v is not None}
    org = _org(request)

    # WORK COUNTRY — Canada-only: always CA.
    info = await identity.org_info(org) or {}
    try:
        country = ca_identity.clean_country(data.get("address_country"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    data["address_country"] = country
    region = (data.get("province_of_employment") or info.get("province") or "").upper()
    if not _valid_region(country, region):
        raise HTTPException(422, "Choose the province of employment")
    data["province_of_employment"] = region
    _clean_plain(country, data)
    if data.get("branch_id"):
        data["branch_id"] = _uuid_or_404(data["branch_id"], "Branch")
    # D4: legal entity — the one chosen on the form (must belong to this org), else the default.
    from entities_routes import resolve_entity_for_create
    data["entity_id"] = await resolve_entity_for_create(org, data.get("entity_id"))

    # S4: the plan's employee limit (billing catalogue data) — 402 when full.
    import entitlements
    await entitlements.require_seat(db, org)
    # EMPLOYEE NUMBER — typed by hand (must be free in this org) or the next auto number.
    if number:
        if await number_in_use(db, org, number):
            raise HTTPException(409, f"Employee number {number} is already used in this organization")
    else:
        number = await assign_number(db, org, data.get("entity_id"))
    e = Employee(org_id=org, employee_number=number, **data)
    await _apply_sensitive(db, org, e, {k: v for k, v in sens.items() if v})
    await _resolve_manager(db, org, e, manager_id, manager_name)
    e.status = status or "onboarding"
    branch = _branch(request)
    if branch and e.branch_id != branch:   # branch users create only in their branch
        e.branch_id = branch
        e.branch_name = None         # id is authoritative; name is looked up on display/edit
    db.add(e)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, f"Employee number {number} was just taken. Save again to get the next one.")
    await db.refresh(e)
    # S4: create row carries the (PII-masked) field values, not just the fact.
    from shared.audit_trail import audit_change, snapshot
    await audit_change(request, "employee.create", entity_type="employee", entity_id=str(e.id),
                       before=None, after=snapshot(e),
                       summary=f"Employee {e.employee_number} added ({country})",
                       extra={"country": country, "identifiers": [k for k, v in sens.items() if v]})
    from shared.utils.events import emit as _g3_emit      # G3: outbound webhooks (best-effort)
    await _g3_emit(org, "employee.created", {"employee_id": str(e.id), "employee_number": e.employee_number,
                                             "status": e.status, "country": country})
    return _out(e)


@router.get("")
async def list_employees(request: Request, reveal: str | None = None,
                         db: AsyncSession = Depends(get_db)):
    """hr / payroll / admin: everyone. Manager: self + direct reports. Employee: self."""
    v = await access.viewer(request, db)
    q = access.restrict(select(Employee).where(Employee.org_id == _org(request), Employee.is_active.is_(True)), v)
    branch = _branch(request)
    if branch and v["scope"] == access.ALL:   # branch-scoped user, or an org user viewing one branch
        q = q.where(Employee.branch_id == branch)
    q = await request_entity_filter(request, q, _org(request))   # D4: entity view (?entity_id= / X-View-Entity)
    q = _scope_filter(q, request)                                  # custom role: department / location / entity
    rows = (await db.execute(q)).scalars().all()
    full = reveal == "full"
    if full:
        if not _can_reveal(request):
            raise HTTPException(403, "Only payroll or org admins may view full identifiers")
        await record_audit(request, "employee.sensitive_read", entity_type="employee", entity_id="list",
                           summary=f"Full identifiers read for {len(rows)} employees (list)",
                           meta={"count": len(rows), "fields": list(sensitive.FIELDS)})
    me = str(v.get("self_id") or "")
    if access.names_only(request):                 # accountant: work card only, own record in full
        return [{**_out(e), "is_self": True} if str(e.id) == me else _names_card(e, me) for e in rows]
    out = [{**_out(e, reveal=full), "is_self": str(e.id) == me} for e in rows]
    # manager: the team's records without pay / IDs; a custom role: only the data classes it carries (2026-10-26)
    return [access.redact(request, e, o) for o, e in zip(out, rows)]


def _names_card(e: Employee, me: str = "") -> dict:
    """Names-only directory row (accountant): work facts, no pay / identifiers / personal data."""
    return {**directory_row(e, me), "status": e.status, "employment_type": e.employment_type,
            "masked": True, "names_only": True}


async def _load(employee_id: str, request: Request, db: AsyncSession) -> Employee:
    """One employee the caller is allowed to see — anyone else's record is a 404,
    exactly like a record that does not exist."""
    res = await db.execute(
        select(Employee).where(Employee.id == _uuid_or_404(employee_id), Employee.org_id == _org(request)))
    e = res.scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    v = await access.viewer(request, db)
    if not access.can_see(v, e):
        raise HTTPException(404, "Employee not found")
    if v["scope"] == access.ALL and _branch(request) and e.branch_id != _branch(request):
        raise HTTPException(404, "Employee not found")   # out-of-branch is invisible
    if not _in_scope(request, e):
        raise HTTPException(404, "Employee not found")   # outside a custom role's department / location / entity
    return e


@router.get("/{employee_id}")
async def get_employee(employee_id: str, request: Request, reveal: str | None = None,
                       db: AsyncSession = Depends(get_db)):
    e = await _load(employee_id, request, db)
    if access.names_only(request) and not access.is_own(request, e):
        if reveal == "full":
            raise HTTPException(403, "Only payroll or org admins (or the employee) may view full identifiers")
        return _names_card(e)
    full = reveal == "full"
    if full:
        if not _can_reveal(request, employee_id):
            raise HTTPException(403, "Only payroll or org admins (or the employee) may view full identifiers")
        await record_audit(request, "employee.sensitive_read", entity_type="employee", entity_id=employee_id,
                           summary=f"Full identifiers read for {e.first_name} {e.last_name}",
                           meta={"fields": list(sensitive.FIELDS), "self": _self_employee(request) == employee_id})
    return access.redact(request, e, _out(e)) if access.hides_pay(request, e) else _out(e, reveal=full)


@router.get("/{employee_id}/sensitive")
async def get_sensitive(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Only the identifier block, in full. Same guard + audit as ?reveal=full."""
    e = await _load(employee_id, request, db)
    if not _can_reveal(request, employee_id):
        raise HTTPException(403, "Only payroll or org admins (or the employee) may view full identifiers")
    await record_audit(request, "employee.sensitive_read", entity_type="employee", entity_id=employee_id,
                       summary=f"Full identifiers read for {e.first_name} {e.last_name}",
                       meta={"fields": list(sensitive.FIELDS), "self": _self_employee(request) == employee_id})
    return {"id": str(e.id), **_sensitive_block(e, True),
            "key_id": e.enc_key_id}


from datetime import date as _date  # noqa: E402
_DATE_FIELDS = {"hire_date", "termination_date", "date_of_birth", "work_permit_expiry", "probation_end_date",
                "contract_start_date", "contract_end_date",
                "passport_expiry"}
_UUID_FIELDS = {"branch_id", "pay_group_id"}
# D4: entity_id changes ONLY through POST /employees/{id}/entity-transfer (effective-dated, history kept).
_READ_ONLY_FIELDS = {"entity_id"}
_ENC_COLS = {spec["enc"] for spec in sensitive.FIELDS.values()} | {
    spec["hash"] for spec in sensitive.FIELDS.values() if spec["hash"]} | {
    spec["mask"] for spec in sensitive.FIELDS.values() if spec["mask"]} | {
    spec["plain"] for spec in sensitive.FIELDS.values() if spec["plain"]} | {"enc_key_id"}


@router.patch("/{employee_id}")
async def update_employee(employee_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Partial update — also the path for termination/resignation
    (status='terminated' + termination_date). Identifier fields (sin,
    bank_account, passport) go through
    the encryption helper; the storage columns themselves are not writable."""
    access.require_edit(request)
    e = await _load(employee_id, request, db)
    from shared.audit_trail import audit_change, snapshot
    before = snapshot(e)                       # S4: before/after audit (PII masked)
    org = _org(request)
    cols = {c.name for c in Employee.__table__.columns} - {"id", "org_id", "created_at", "erased_at"} - _ENC_COLS - _READ_ONLY_FIELDS
    sens = {k: body.pop(k) for k in list(body.keys()) if k in SENSITIVE_INPUTS}
    body.pop("bank_account_last4", None)
    body.pop("country", None)                  # read-only alias of address_country
    # Work country: Canada-only — anything but CA is refused.
    if "address_country" in body:
        try:
            country = ca_identity.clean_country(body["address_country"])
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        body["address_country"] = country
    country = body.get("address_country") or e.address_country
    if "address_country" in body or "province_of_employment" in body:
        region = (body.get("province_of_employment") or e.province_of_employment or "").upper()
        if country and not _valid_region(country, region):
            raise HTTPException(422, "Choose the province of employment")
        body["province_of_employment"] = region
    _clean_plain(country, body)
    if "employee_number" in body:
        number = str(body["employee_number"] or "").strip()
        if not number:
            raise HTTPException(422, "Employee number cannot be empty")
        if await number_in_use(db, org, number, except_id=e.id):
            raise HTTPException(409, f"Employee number {number} is already used in this organization")
        body["employee_number"] = number
    manager_change = "manager_id" in body or "manager_name" in body
    # "No manager" picked on the form clears the display name too (unless new text came with it).
    cleared = "manager_id" in body and not body["manager_id"] and "manager_name" not in body
    new_mid, new_mname = body.pop("manager_id", e.manager_id), body.pop("manager_name", e.manager_name)
    if cleared:
        new_mname = None
    for k, v in body.items():
        if k not in cols:
            continue
        if k in _DATE_FIELDS and isinstance(v, str):
            v = _date.fromisoformat(v) if v else None
        if k in _UUID_FIELDS:
            v = _uuid_or_404(v, "Record") if v else None
        setattr(e, k, v)
    if manager_change:
        await _resolve_manager(db, org, e, new_mid, new_mname)
    if sens:
        await _apply_sensitive(db, org, e, sens)
    await db.commit()
    await db.refresh(e)
    if sens:
        await record_audit(request, "employee.sensitive_write", entity_type="employee", entity_id=employee_id,
                           summary=f"Identifiers updated: {', '.join(sens)}", meta={"fields": list(sens)})
    await audit_change(request, "employee.terminate" if body.get("status") == "terminated" else "employee.update",
                       entity_type="employee", entity_id=employee_id, before=before, after=snapshot(e),
                       summary=None)
    from shared.utils.events import emit as _g3_emit      # G3: outbound webhooks (field NAMES only, never values)
    await _g3_emit(org, "employee.updated", {"employee_id": str(e.id), "employee_number": e.employee_number,
                                             "fields": sorted(k for k in body if k in cols) + sorted(sens)})
    if body.get("status") == "terminated":
        await _g3_emit(org, "employee.exited", {"employee_id": str(e.id), "employee_number": e.employee_number,
                                                "termination_date": str(e.termination_date or "")})
    return _out(e)


@router.post("/{employee_id}/erase")
async def erase_employee(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Personal-information erasure: blank personal/contact data and non-statutory
    identifiers. Refused while a retention hold is set or the employee is still
    active. Pay records stay until the CRA retention purge."""
    if _role(request) not in ADMIN_ROLES | {"payroll_admin"}:
        raise HTTPException(403, "Only admins may apply an erasure")
    e = await _load(employee_id, request, db)
    if e.status not in ("terminated",) and e.is_active:
        raise HTTPException(409, "Erasure applies to exited employees only — terminate the record first")
    if e.erasure_hold:
        raise HTTPException(409, "A statutory retention hold is set on this record")
    cleared = sensitive.anonymise(e)
    e.first_name = (e.first_name or "")[:1] + "." if e.first_name else "Erased"
    e.last_name = "Erased"
    e.erased_at = datetime.now(timezone.utc)
    e.is_active = False
    await db.commit()
    await record_audit(request, "employee.erased", entity_type="employee", entity_id=employee_id,
                       summary="Personal data erased (privacy request)", meta={"cleared": cleared})
    return {"ok": True, "employee_id": employee_id, "cleared": cleared,
            "retained_encrypted": [],
            "erased_at": e.erased_at.isoformat()}


@router.post("/{employee_id}/retention-hold")
async def set_hold(employee_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in ADMIN_ROLES | {"payroll_admin"}:
        raise HTTPException(403, "Only admins may change a retention hold")
    e = await _load(employee_id, request, db)
    e.erasure_hold = bool(body.get("hold", True))
    await db.commit()
    await record_audit(request, "employee.retention_hold", entity_type="employee", entity_id=employee_id,
                       summary=f"Retention hold {'set' if e.erasure_hold else 'cleared'}",
                       meta={"reason": body.get("reason")})
    return {"ok": True, "erasure_hold": e.erasure_hold}
