"""Compliance service — OHS safety register, tamper-evident audit trail, WCB/WSIB.

Row-level org isolation via the gateway-stamped X-Org-Id; branch-scoped writers
are restricted to their own branch. WCB assessment reuses the payroll T4 ledger
(box 14 income per employee + province) so there is one source of earnings truth.
"""
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import (AuditEvent, _GENESIS, compute_hash,  # noqa: E402
                                record_audit, signed_payload)
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

from database import get_db  # noqa: E402
from models import SafetyIncident, WcbRate  # noqa: E402

router = APIRouter(prefix="/compliance", tags=["compliance"])

ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
HR_WRITE = ADMIN_ROLES | {"manager"}


# ── WCB reference ceilings — DESIGN REFERENCES, NOT VERIFIED ─────────────────
# Maximum assessable earnings per worker per year, by province. These are the
# ceiling above which employer premiums are not assessed. The values below are
# planning references only and MUST be confirmed against each provincial board's
# published maximum for the year before any premium figure is relied upon.
PROVINCIAL_MAX_ASSESSABLE: dict[str, dict] = {
    "ON": {"board": "WSIB", "max_assessable": 117000.0, "verified": False},
    "BC": {"board": "WorkSafeBC", "max_assessable": 121500.0, "verified": False},
    "AB": {"board": "WCB-AB", "max_assessable": 104600.0, "verified": False},
}
WCB_VERIFY_NOTE = ("WCB maximum-assessable ceilings are unverified design references; "
                   "confirm each province's published maximum and enter your org's "
                   "board-assigned rate before relying on any premium figure.")


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _org_uuid(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(_org(request))
    except ValueError:
        raise HTTPException(400, "Invalid organization context")


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def _branch(request: Request) -> str | None:
    return request.headers.get("x-branch-id") or None


def _email(request: Request) -> str | None:
    return request.headers.get("x-user-email")


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id")
            if request.headers.get(k)}


def _require_office(request: Request) -> None:
    """WCB rates / premium estimate: org_admin + HR + payroll (the back office). WASA 2026-10-21."""
    from shared.utils.roles import OFFICE, require_roles
    require_roles(request, OFFICE, "see WCB rates and premiums")


def _require(request: Request, roles: set):
    if _role(request) not in roles:
        raise HTTPException(403, "Not permitted for your role")


# ── Safety incident register ─────────────────────────────────────────────────

INCIDENT_TYPES = ["injury", "near_miss", "illness", "property", "environmental"]
SEVERITIES = ["minor", "moderate", "serious", "critical"]
INCIDENT_STATUSES = ["open", "investigating", "corrective_action", "closed"]


class IncidentIn(BaseModel):
    occurred_on: date
    reported_on: date | None = None
    incident_type: str = "injury"
    severity: str = "minor"
    location: str | None = None
    branch_id: str | None = None
    branch_name: str | None = None
    employee_id: str | None = None
    employee_name: str | None = None
    description: str
    lost_time: bool = False
    lost_days: float = 0
    wcb_claim_no: str | None = None
    wcb_reportable: bool = False
    corrective_action: str | None = None
    status: str = "open"


class IncidentPatch(BaseModel):
    incident_type: str | None = None
    severity: str | None = None
    location: str | None = None
    employee_name: str | None = None
    description: str | None = None
    lost_time: bool | None = None
    lost_days: float | None = None
    wcb_claim_no: str | None = None
    wcb_reportable: bool | None = None
    corrective_action: str | None = None
    status: str | None = None


def _incident_out(i: SafetyIncident) -> dict:
    return {
        "id": str(i.id), "ref": i.ref, "occurred_on": i.occurred_on.isoformat(),
        "reported_on": i.reported_on.isoformat() if i.reported_on else None,
        "incident_type": i.incident_type, "severity": i.severity, "location": i.location,
        "branch_id": i.branch_id, "branch_name": i.branch_name,
        "employee_id": str(i.employee_id) if i.employee_id else None,
        "employee_name": i.employee_name, "description": i.description,
        "lost_time": i.lost_time, "lost_days": float(i.lost_days or 0),
        "wcb_claim_no": i.wcb_claim_no, "wcb_reportable": i.wcb_reportable,
        "corrective_action": i.corrective_action, "status": i.status,
        "reported_by": i.reported_by,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "closed_at": i.closed_at.isoformat() if i.closed_at else None,
    }


async def _next_ref(db: AsyncSession, org: uuid.UUID, year: int) -> str:
    n = (await db.execute(select(func.count(SafetyIncident.id)).where(
        SafetyIncident.org_id == org,
        func.extract("year", SafetyIncident.occurred_on) == year))).scalar() or 0
    return f"OHS-{year}-{n + 1:03d}"


@router.get("/safety")
async def list_incidents(request: Request, status: str | None = None,
                         incident_type: str | None = None, branch_id: str | None = None,
                         q: str | None = None, skip: int = 0, limit: int = 50,
                         db: AsyncSession = Depends(get_db)):
    org = _org_uuid(request)
    conds = [SafetyIncident.org_id == org]
    scope = _branch(request)
    if scope:                                   # branch-scoped users see only their branch
        conds.append(SafetyIncident.branch_id == scope)
    elif branch_id:
        conds.append(SafetyIncident.branch_id == branch_id)
    if status:
        conds.append(SafetyIncident.status.in_(status.split(",")))
    if incident_type:
        conds.append(SafetyIncident.incident_type.in_(incident_type.split(",")))
    if q:
        like = f"%{q}%"
        conds.append(or_(SafetyIncident.description.ilike(like),
                         SafetyIncident.location.ilike(like),
                         SafetyIncident.ref.ilike(like),
                         SafetyIncident.employee_name.ilike(like)))
    where = and_(*conds)
    total = (await db.execute(select(func.count(SafetyIncident.id)).where(where))).scalar() or 0
    rows = (await db.execute(select(SafetyIncident).where(where)
            .order_by(SafetyIncident.occurred_on.desc(), SafetyIncident.created_at.desc())
            .offset(skip).limit(min(limit, 200)))).scalars().all()
    return {"total": total, "skip": skip, "limit": limit,
            "items": [_incident_out(i) for i in rows]}


@router.get("/safety/summary")
async def safety_summary(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    org = _org_uuid(request)
    conds = [SafetyIncident.org_id == org,
             func.extract("year", SafetyIncident.occurred_on) == year]
    scope = _branch(request)
    if scope:
        conds.append(SafetyIncident.branch_id == scope)
    rows = (await db.execute(select(SafetyIncident).where(and_(*conds)))).scalars().all()
    by_type = {t: 0 for t in INCIDENT_TYPES}
    by_severity = {s: 0 for s in SEVERITIES}
    open_count = lost_time_count = total_lost_days = recordable = 0
    for i in rows:
        by_type[i.incident_type] = by_type.get(i.incident_type, 0) + 1
        by_severity[i.severity] = by_severity.get(i.severity, 0) + 1
        if i.status != "closed":
            open_count += 1
        if i.lost_time:
            lost_time_count += 1
            total_lost_days += float(i.lost_days or 0)
        if i.wcb_reportable:
            recordable += 1
    return {"year": year, "total": len(rows), "open": open_count,
            "lost_time_cases": lost_time_count, "total_lost_days": round(total_lost_days, 2),
            "wcb_reportable": recordable, "by_type": by_type, "by_severity": by_severity}


@router.post("/safety", status_code=201)
async def create_incident(body: IncidentIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require(request, HR_WRITE)
    if body.incident_type not in INCIDENT_TYPES:
        raise HTTPException(422, f"incident_type must be one of {INCIDENT_TYPES}")
    if body.severity not in SEVERITIES:
        raise HTTPException(422, f"severity must be one of {SEVERITIES}")
    if body.status not in INCIDENT_STATUSES:
        raise HTTPException(422, f"status must be one of {INCIDENT_STATUSES}")
    org = _org_uuid(request)
    scope = _branch(request)
    branch_id = scope or body.branch_id          # branch users are locked to their branch
    inc = SafetyIncident(
        org_id=org, branch_id=branch_id, branch_name=body.branch_name,
        ref=await _next_ref(db, org, body.occurred_on.year),
        occurred_on=body.occurred_on, reported_on=body.reported_on or await org_today(org),
        incident_type=body.incident_type, severity=body.severity, location=body.location,
        employee_id=uuid.UUID(body.employee_id) if body.employee_id else None,
        employee_name=body.employee_name, description=body.description,
        lost_time=body.lost_time, lost_days=body.lost_days or 0,
        wcb_claim_no=body.wcb_claim_no, wcb_reportable=body.wcb_reportable,
        corrective_action=body.corrective_action, status=body.status,
        reported_by=_email(request),
    )
    db.add(inc)
    await db.commit()
    await db.refresh(inc)
    await record_audit(request, "compliance.safety.create", entity_type="safety_incident",
                       entity_id=inc.ref, summary=f"Logged {inc.incident_type} incident {inc.ref}",
                       meta={"severity": inc.severity, "lost_time": inc.lost_time})
    return _incident_out(inc)


@router.patch("/safety/{incident_id}")
async def update_incident(incident_id: str, body: IncidentPatch, request: Request,
                          db: AsyncSession = Depends(get_db)):
    _require(request, HR_WRITE)
    org = _org_uuid(request)
    inc = (await db.execute(select(SafetyIncident).where(
        SafetyIncident.org_id == org, SafetyIncident.id == uuid.UUID(incident_id)))).scalar_one_or_none()
    if not inc:
        raise HTTPException(404, "Incident not found")
    scope = _branch(request)
    if scope and inc.branch_id != scope:
        raise HTTPException(403, "Incident belongs to another branch")
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] not in INCIDENT_STATUSES:
        raise HTTPException(422, f"status must be one of {INCIDENT_STATUSES}")
    for k, v in changes.items():
        setattr(inc, k, v)
    if changes.get("status") == "closed" and not inc.closed_at:
        inc.closed_at = _now()
    await db.commit()
    await db.refresh(inc)
    await record_audit(request, "compliance.safety.update", entity_type="safety_incident",
                       entity_id=inc.ref, summary=f"Updated incident {inc.ref}",
                       meta={"changes": list(changes.keys()), "status": inc.status})
    return _incident_out(inc)


# ── Tamper-evident audit trail (read + verify) ───────────────────────────────
# Decision 2026-10-21 (least privilege): the org admin reads the whole trail; the HR admin reads
# only the HR-module part of it (people records, hiring, exits, letters, leave …) — never logins /
# security, payroll or salary revisions, integrations or the AI. HR managers and everyone
# else: no access.
AUDIT_FULL = {"superadmin", "org_admin"}
AUDIT_HR = {"hr_admin"}
HR_AUDIT_PREFIXES = ("employee.", "hiring.", "exit.", "letters.", "onboarding.", "career.", "performance.",
                     "custom_field.", "leave.", "probation.", "ess.", "approvals.", "compliance.")


def _audit_scope(request: Request) -> str:
    """'all' | 'hr' — or 403."""
    role = _role(request)
    if role in AUDIT_FULL:
        return "all"
    if role in AUDIT_HR:
        return "hr"
    raise HTTPException(403, "Only the org admin (or the HR admin, for HR records) can read the audit trail")


def _hr_audit_filter():
    return or_(*[AuditEvent.action.like(p + "%") for p in HR_AUDIT_PREFIXES])


@router.get("/audit")
async def list_audit(request: Request, action: str | None = None, actor: str | None = None,
                     entity_type: str | None = None, q: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db)):
    scope = _audit_scope(request)
    org = _org_uuid(request)
    conds = [AuditEvent.org_id == org]
    if scope == "hr":
        conds.append(_hr_audit_filter())
    if action:
        conds.append(AuditEvent.action.in_(action.split(",")))
    if entity_type:
        conds.append(AuditEvent.entity_type == entity_type)
    if actor:
        conds.append(AuditEvent.actor_email.ilike(f"%{actor}%"))
    # Date range (YYYY-MM-DD, inclusive of the whole `date_to` day).
    if date_from:
        try:
            conds.append(AuditEvent.ts >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            conds.append(AuditEvent.ts < datetime.fromisoformat(date_to) + timedelta(days=1))
        except ValueError:
            pass
    if q:
        like = f"%{q}%"
        conds.append(or_(AuditEvent.summary.ilike(like), AuditEvent.action.ilike(like),
                         AuditEvent.actor_email.ilike(like)))
    where = and_(*conds)
    total = (await db.execute(select(func.count(AuditEvent.id)).where(where))).scalar() or 0
    rows = (await db.execute(select(AuditEvent).where(where)
            .order_by(AuditEvent.seq.desc()).offset(skip).limit(min(limit, 200)))).scalars().all()
    return {"total": total, "skip": skip, "limit": limit, "scope": scope,
            "scope_note": None if scope == "all" else "HR records only (people, hiring, exits, letters, leave). "
                                                       "Logins, payroll and integrations: org admin.",
            "items": [{
        "seq": r.seq, "ts": r.ts.isoformat() if r.ts else None,
        "actor_email": r.actor_email, "actor_role": r.actor_role, "action": r.action,
        "entity_type": r.entity_type, "entity_id": r.entity_id, "branch_id": r.branch_id,
        "summary": r.summary, "meta": r.meta,
        "entry_hash": r.entry_hash, "prev_hash": r.prev_hash,
    } for r in rows]}


@router.get("/audit/verify")
async def verify_audit(request: Request, db: AsyncSession = Depends(get_db)):
    """Re-walk the org's HMAC hash chain and report any break — the tamper check.

    Recomputes every entry_hash from its predecessor; a mismatch or a gap in the
    per-org seq means a row was altered, inserted or deleted after the fact.
    The answer carries sequence numbers and hashes only (no row content), so the HR admin may run it.
    """
    _audit_scope(request)
    org = _org_uuid(request)
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.org_id == org)
            .order_by(AuditEvent.seq.asc()))).scalars().all()
    prev = _GENESIS
    breaks = []
    expected_seq = 1
    for r in rows:
        if r.seq != expected_seq:
            breaks.append({"seq": r.seq, "issue": f"sequence gap (expected {expected_seq})"})
        if r.prev_hash != prev:
            breaks.append({"seq": r.seq, "issue": "prev_hash does not match prior entry"})
        recomputed = compute_hash(r.prev_hash, signed_payload(r))
        if recomputed != r.entry_hash:
            breaks.append({"seq": r.seq, "issue": "entry_hash mismatch (row was altered)"})
        prev = r.entry_hash
        expected_seq = r.seq + 1
    return {"chain_length": len(rows), "intact": not breaks, "breaks": breaks,
            "algorithm": "HMAC-SHA256 chain keyed by server secret"}


# ── WCB / WSIB assessment ────────────────────────────────────────────────────

class WcbRateIn(BaseModel):
    rate_per_100: float
    account_number: str | None = None
    year: int = 2026


async def _wcb_rates(db: AsyncSession, org: uuid.UUID, year: int) -> dict[str, WcbRate]:
    rows = (await db.execute(select(WcbRate).where(
        WcbRate.org_id == org, WcbRate.year == year))).scalars().all()
    return {r.province: r for r in rows}


@router.get("/wcb/rates")
async def get_wcb_rates(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    _require_office(request)
    org = _org_uuid(request)
    rates = await _wcb_rates(db, org, year)
    out = []
    for prov, ref in PROVINCIAL_MAX_ASSESSABLE.items():
        r = rates.get(prov)
        out.append({"province": prov, "board": ref["board"],
                    "max_assessable": ref["max_assessable"], "max_verified": ref["verified"],
                    "rate_per_100": float(r.rate_per_100) if r else None,
                    "account_number": r.account_number if r else None,
                    "configured": r is not None})
    return {"year": year, "provinces": out, "verify_note": WCB_VERIFY_NOTE}


@router.put("/wcb/rates/{province}")
async def set_wcb_rate(province: str, body: WcbRateIn, request: Request,
                       db: AsyncSession = Depends(get_db)):
    _require(request, ADMIN_ROLES)
    province = province.upper()
    if province not in PROVINCIAL_MAX_ASSESSABLE:
        raise HTTPException(422, f"province must be one of {list(PROVINCIAL_MAX_ASSESSABLE)}")
    if body.rate_per_100 < 0 or body.rate_per_100 > 30:
        raise HTTPException(422, "rate_per_100 out of range (0–30)")
    org = _org_uuid(request)
    r = (await db.execute(select(WcbRate).where(
        WcbRate.org_id == org, WcbRate.province == province,
        WcbRate.year == body.year))).scalar_one_or_none()
    if not r:
        r = WcbRate(org_id=org, province=province, year=body.year,
                    board=PROVINCIAL_MAX_ASSESSABLE[province]["board"])
        db.add(r)
    r.rate_per_100 = body.rate_per_100
    r.account_number = body.account_number
    await db.commit()
    await record_audit(request, "compliance.wcb.rate_set", entity_type="wcb_rate",
                       entity_id=province, summary=f"Set {province} WCB rate to {body.rate_per_100}/$100",
                       meta={"year": body.year})
    return {"province": province, "rate_per_100": body.rate_per_100, "year": body.year}


async def _fetch_t4(request: Request, year: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(f"{settings.PAYROLL_URL}/payroll/t4",
                            params={"year": year}, headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return {"slips": []}


@router.get("/wcb/assessment")
async def wcb_assessment(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    """Employer WCB premium estimate from the payroll T4 ledger.

    Per employee: assessable = min(box-14 employment income, province max
    assessable); premium = assessable x org rate / $100. Rates are org-entered;
    ceilings are unverified references (see WCB_VERIFY_NOTE) — this is a planning
    estimate, not a filed assessment.
    """
    _require_office(request)
    org = _org_uuid(request)
    rates = await _wcb_rates(db, org, year)
    t4 = await _fetch_t4(request, year)
    prov_roll: dict[str, dict] = {}
    for slip in t4.get("slips", []):
        prov = (slip.get("province") or "").upper()
        ref = PROVINCIAL_MAX_ASSESSABLE.get(prov)
        if not ref:
            prov = prov or "??"
            bucket = prov_roll.setdefault(prov, {"province": prov, "board": None,
                     "max_assessable": None, "employees": 0, "gross": 0.0,
                     "assessable": 0.0, "rate_per_100": None, "premium": None,
                     "rate_configured": False, "unsupported": True})
            bucket["employees"] += 1
            bucket["gross"] += float(slip.get("boxes", {}).get("box14_employment_income", 0) or 0)
            continue
        rate = rates.get(prov)
        b = prov_roll.setdefault(prov, {"province": prov, "board": ref["board"],
             "max_assessable": ref["max_assessable"], "max_verified": ref["verified"],
             "employees": 0, "gross": 0.0, "assessable": 0.0,
             "rate_per_100": float(rate.rate_per_100) if rate else None,
             "rate_configured": rate is not None, "premium": 0.0 if rate else None})
        income = float(slip.get("boxes", {}).get("box14_employment_income", 0) or 0)
        assessable = min(income, ref["max_assessable"])
        b["employees"] += 1
        b["gross"] += income
        b["assessable"] += assessable
        if rate is not None:
            b["premium"] = round((b["premium"] or 0) + assessable * float(rate.rate_per_100) / 100.0, 2)
    for b in prov_roll.values():
        b["gross"] = round(b["gross"], 2)
        b["assessable"] = round(b["assessable"], 2)
    provinces = sorted(prov_roll.values(), key=lambda x: x["province"])
    total_premium = round(sum(b["premium"] for b in provinces if b.get("premium")), 2)
    all_configured = all(b.get("rate_configured") for b in provinces if not b.get("unsupported")) and bool(provinces)
    return {"year": year, "provinces": provinces,
            "total_assessable": round(sum(b["assessable"] for b in provinces), 2),
            "total_premium": total_premium, "rates_complete": all_configured,
            "verified": False, "verify_note": WCB_VERIFY_NOTE,
            "source": "payroll T4 ledger (box 14)"}
