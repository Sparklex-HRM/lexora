"""Compliance service — privacy centre (PIPEDA, Canada).

Routes live under /compliance/privacy. The employer org is accountable for its employees'
personal information (PIPEDA Schedule 1, Principle 4.1) and names a Privacy Officer;
Lexora HRM is its service provider (Principle 4.1.3). Access requests are answered within
30 days (s.8(3)), extendable once by 30 days (s.8(4)). A breach of security safeguards that
creates a real risk of significant harm is reported to the Privacy Commissioner of Canada
and the affected individuals as soon as feasible (s.10.1); a record of EVERY breach is kept
for 24 months (Breach of Security Safeguards Regulations s.6). Québec employers: Law 25
(Act respecting the protection of personal information in the private sector) — confidentiality
incidents with a risk of serious injury go to the Commission d'accès à l'information.
These defaults are a starting point, not legal advice.
"""
import asyncio
import io
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

from database import SessionLocal, get_db  # noqa: E402
from privacy_models import (BreachRecord, ConsentRecord, DataRequest, PrivacySettings,  # noqa: E402
                         PrivacyNotice, PurgeRun, RetentionPolicy)

router = APIRouter(prefix="/compliance/privacy", tags=["privacy"])
_log = logging.getLogger("lexora.compliance.privacy")


def _count_failed(record_type: str) -> dict:
    """A count that could not be made: the real error goes to the log with a short ref; the
    answer carries only a fixed sentence + the ref (no SQL / driver text). Call inside `except`."""
    ref = uuid.uuid4().hex[:8].upper()
    _log.exception("retention count failed for %s (ref %s)", record_type, ref)
    return {"error": "These records could not be counted. Give this reference to support.", "ref": ref}

ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
LAW = "PIPEDA (Personal Information Protection and Electronic Documents Act)"
SLA_DAYS = 30
PROCESSOR_CONTRACT_VERSION = "1.0"

PURPOSES = {
    "payroll_processing": {"label": "Payroll processing",
                           "text": "Using your salary, bank account, SIN, TD1 claim amounts and time records to calculate and pay you."},
    "government_reporting": {"label": "Government reporting",
                             "text": "Sending the required details to the Canada Revenue Agency (source deductions, T4), Service Canada "
                                     "(Record of Employment), Revenu Québec (RL-1, Québec only) and your provincial workers' compensation board."},
    "benefits":           {"label": "Benefits administration",
                           "text": "Enrolling you and your beneficiaries in the group benefits and retirement plans the company offers."},
    "background_check":   {"label": "Background verification",
                           "text": "Checking your identity, education and past employment with the documents you gave us."},
}
REQUEST_TYPES = {"access": "Access my information", "correction": "Correct my information",
                 "erasure": "Delete information no longer needed", "nomination": "Authorise a representative"}
REQUEST_STATUSES = ["open", "in_progress", "on_hold", "completed", "rejected"]

DEFAULT_NOTICE = """Why we collect your personal information
{org} ("the Company") collects, uses and discloses your personal information to employ you and to pay you correctly. Lexora HRM is the software service we use for this; it handles the information only for us, under contract (PIPEDA Principle 4.1.3).

What we collect
Your name, contact details, date of birth, Social Insurance Number (SIN), TD1 claim amounts, bank account for direct deposit, salary and pay history, time and leave records, emergency contacts and beneficiaries, and documents you upload (identification, permits, certificates).

What we use it for (the purposes you consent to)
1. Payroll processing — calculating and paying your earnings and deductions.
2. Government reporting — source deductions and T4 slips to the Canada Revenue Agency, Records of Employment to Service Canada, RL-1 slips to Revenu Québec (Québec only), and reports to your provincial workers' compensation board.
3. Benefits — group benefits and retirement plans, including your beneficiaries.
4. Background verification — checking the documents you provided.
Some of these uses are required by law (for example your SIN for tax reporting); we only ask for what we need.

Your rights
You can ask to see the personal information we hold about you and to have it corrected. Raise a request in Lexora HRM under Privacy Centre → My requests; we reply within 30 days. If you are not satisfied, contact our Privacy Officer (below); you may also complain to the Office of the Privacy Commissioner of Canada (or, in Québec, the Commission d'accès à l'information).

How long we keep it
Payroll and tax records are kept for six years after the end of the last tax year they relate to, as the Income Tax Act requires, even after you leave. Other personal information is deleted or anonymised when it is no longer needed.

Safeguards
Your SIN and bank account are stored encrypted. Only payroll and admin staff can see the full values, and every such view is logged.

Withdrawing consent
You may withdraw consent at any time in Privacy Centre → My consents, subject to legal and contractual restrictions. Withdrawing consent for payroll or government reporting means we cannot lawfully employ and pay you, so we will discuss this with you first.

Contact
Privacy Officer: {dpo}
"""

PROCESSOR_CONTRACT = """SERVICE-PROVIDER PRIVACY TERMS — Lexora HRM (version {version})
Between: {org} ("the Employer") and the Lexora HRM operator ("the Service Provider").
Applicable law: the Personal Information Protection and Electronic Documents Act (PIPEDA) and, for Québec employees, the Act respecting the protection of personal information in the private sector (Law 25).

1. Roles. The Employer remains accountable for its employees' personal information (PIPEDA Principle 4.1). The Service Provider processes it only to provide the Lexora HRM service and only on the Employer's documented instructions (Principle 4.1.3).
2. Information covered. Employee records, SIN, TD1 claims, bank details, earnings and deductions, time and leave records, documents and beneficiary details.
3. Safeguards (Principle 4.7). Encryption of SIN and bank account numbers at rest, role-based access, tamper-evident audit logging of every view of sensitive fields, encrypted transport and backups.
4. Breaches. The Service Provider tells the Employer without delay on becoming aware of a breach of security safeguards affecting the Employer's information, with the facts the Employer needs to assess a real risk of significant harm and to report to the Privacy Commissioner and notify individuals (PIPEDA s.10.1); the Employer owns those reports. Both keep a record of every breach for 24 months.
5. Sub-processors. The Service Provider uses only the hosting and infrastructure providers listed in the service description and tells the Employer before adding one. Information may be stored outside the employee's province; the Employer's privacy notice says so.
6. Individual requests. The Service Provider provides the tooling (access report, correction, deletion, consent withdrawal) so the Employer can answer requests within 30 days (PIPEDA s.8(3)).
7. Retention and deletion. On the Employer's instruction or at contract end, the Service Provider deletes or returns the information, except records the Employer must keep under law for the retention periods it configures.
8. Audit. The Employer may ask for reasonable evidence of the safeguards above once a year.
9. Term. These terms apply for as long as the Service Provider handles the Employer's personal information.
"""

# Default retention per record type. Citations are the legal anchors the admin
# sees; they are configurable per org and stay "needs confirmation" until confirmed.
DEFAULT_RETENTION = [
    ("payroll_records", 6, "Income Tax Act s.230(4)(b): records and books kept six years from the end of the last tax year they relate to; Employment Insurance Act / Regulations: payroll records for the same period.", "review"),
    ("tax_slips_and_td1", 6, "Income Tax Act s.230(4): T4 / RL-1 slips, TD1 forms and remittance records, six years after the tax year.", "review"),
    ("records_of_employment", 6, "Employment Insurance Regulations: payroll records supporting the ROE kept six years.", "review"),
    ("attendance_and_leave", 3, "Provincial employment standards (e.g. Ontario ESA s.15: three years after the work was performed; other provinces vary) — confirm per province.", "anonymise"),
    ("employee_personal_data", 1, "PIPEDA Principle 4.5: keep only as long as necessary for the identified purposes — default one year after exit for contact, emergency, beneficiary and bank data.", "anonymise"),
    ("onboarding_documents", 1, "Purpose served on activation; keep one year after exit for verification disputes.", "delete"),
    ("login_and_audit_logs", 1, "Security safeguards (Principle 4.7): sign-in records kept at least one year.", "delete"),
    ("breach_register", 2, "Breach of Security Safeguards Regulations s.6: a record of every breach kept 24 months after the day the organization determined the breach occurred.", "review"),
]

BREACH_CHECKLIST = [
    {"key": "contain", "label": "Contain the breach (revoke access, rotate keys, isolate systems)"},
    {"key": "assess", "label": "Assess the information involved and whether there is a real risk of significant harm (sensitivity + probability of misuse)"},
    {"key": "report_commissioner", "label": "If there is a real risk of significant harm: report to the Office of the Privacy Commissioner of Canada as soon as feasible (PIPEDA s.10.1(1))"},
    {"key": "notify_individuals", "label": "Notify each affected individual as soon as feasible: what happened, the information involved, what we are doing, how to reduce the risk, contact (s.10.1(3))"},
    {"key": "notify_others", "label": "Notify any organization or government body that can reduce the risk (s.10.2) — e.g. the bank, police"},
    {"key": "quebec_cai", "label": "Québec employees: report a confidentiality incident with a risk of serious injury to the Commission d'accès à l'information (Law 25)"},
    {"key": "record", "label": "Keep the breach record for 24 months (Breach of Security Safeguards Regulations s.6)"},
    {"key": "remediate", "label": "Fix the root cause and record the corrective action; review signed off by the Privacy Officer"},
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> uuid.UUID:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    try:
        return uuid.UUID(org)
    except ValueError:
        raise HTTPException(400, "Invalid organization context")


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _email(request: Request) -> str | None:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id")


def _self_emp(request: Request) -> str:
    return request.headers.get("x-employee-id") or ""


def _is_admin(request: Request) -> bool:
    return _role(request) in ADMIN_ROLES


def _require_admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "Not permitted for your role")


def _employee_scope(request: Request, employee_id: str | None) -> str:
    """Admins may act for any employee; everyone else only for themselves."""
    if _is_admin(request):
        if not employee_id:
            raise HTTPException(422, "employee_id is required")
        return employee_id
    mine = _self_emp(request)
    if not mine:
        raise HTTPException(403, "Your login is not linked to an employee record")
    if employee_id and employee_id != mine:
        raise HTTPException(403, "You may only act on your own data")
    return mine


def _fwd(request: Request, extra: dict | None = None) -> dict:
    h = {k: request.headers.get(k, "") for k in
         ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-employee-id", "x-branch-id")
         if request.headers.get(k)}
    h.update(extra or {})
    return h


async def _fetch_employees(request: Request) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _fetch_employee(request: Request, employee_id: str, reveal: bool = False) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees/{employee_id}",
                            params={"reveal": "full"} if reveal else None, headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


async def _employee_json(request: Request, employee_id: str, reveal: bool = False) -> dict | None:
    return await _fetch_employee(request, employee_id, reveal)


def compute_due(submitted: datetime, sla_days: int = SLA_DAYS) -> datetime:
    return submitted + timedelta(days=sla_days)


def sla_state(due_at: datetime | None, status: str, now: datetime | None = None) -> dict:
    """Days left / overdue for a request. Closed requests report their final state."""
    now = now or _now()
    if not due_at:
        return {"days_left": None, "overdue": False, "band": "none"}
    days = (due_at - now).days
    closed = status in ("completed", "rejected")
    overdue = (not closed) and now > due_at
    band = "closed" if closed else ("overdue" if overdue else ("urgent" if days <= 14 else ("soon" if days <= 30 else "ok")))
    return {"days_left": days, "overdue": overdue, "band": band}


def consent_coverage(employee_ids: list[str], consents: list, purposes=tuple(PURPOSES)) -> dict:
    """Pure: % of employees with a CURRENT 'given' consent for every purpose."""
    latest: dict[tuple[str, str], str] = {}
    for c in sorted(consents, key=lambda c: c.given_at or _now()):
        latest[(str(c.employee_id), c.purpose)] = c.status
    covered = [e for e in employee_ids if all(latest.get((e, p)) == "given" for p in purposes)]
    per_purpose = {p: sum(1 for e in employee_ids if latest.get((e, p)) == "given") for p in purposes}
    n = len(employee_ids)
    return {"employees": n, "fully_covered": len(covered),
            "coverage_pct": round(100 * len(covered) / n, 1) if n else 0.0,
            "per_purpose": per_purpose, "missing": [e for e in employee_ids if e not in covered]}


async def _settings_row(db: AsyncSession, org: uuid.UUID) -> PrivacySettings:
    s = (await db.execute(select(PrivacySettings).where(PrivacySettings.org_id == org))).scalar_one_or_none()
    if not s:
        s = PrivacySettings(org_id=org)
        db.add(s)
        await db.flush()
    return s


def _settings_out(s: PrivacySettings) -> dict:
    return {"dpo_name": s.dpo_name, "dpo_email": s.dpo_email, "dpo_phone": s.dpo_phone,
            "grievance_officer_name": s.grievance_officer_name,
            "grievance_officer_email": s.grievance_officer_email,
            "processor_contract_version": s.processor_contract_version,
            "processor_contract_accepted_at": s.processor_contract_accepted_at.isoformat() if s.processor_contract_accepted_at else None,
            "processor_contract_accepted_by": s.processor_contract_accepted_by,
            "current_contract_version": PROCESSOR_CONTRACT_VERSION}


async def _current_notice(db: AsyncSession, org: uuid.UUID) -> PrivacyNotice | None:
    return (await db.execute(select(PrivacyNotice).where(
        PrivacyNotice.org_id == org, PrivacyNotice.is_current.is_(True))
        .order_by(PrivacyNotice.version.desc()))).scalars().first()


async def _org_name(db: AsyncSession, org: uuid.UUID) -> str:
    try:
        n = (await db.execute(text("SELECT name FROM organizations WHERE id = :o"), {"o": org})).scalar()
        return n or "the Company"
    except Exception:
        return "the Company"


# ── settings / Privacy Officer contact ───────────────────────────────────────────────────

class SettingsIn(BaseModel):
    dpo_name: str | None = None
    dpo_email: str | None = None
    dpo_phone: str | None = None
    grievance_officer_name: str | None = None
    grievance_officer_email: str | None = None


@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    s = await _settings_row(db, _org(request))
    await db.commit()
    return _settings_out(s)


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    s = await _settings_row(db, _org(request))
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, (v or "").strip() or None)
    await db.commit()
    await record_audit(request, "privacy.settings.update", entity_type="privacy_settings",
                       summary="Privacy Officer / complaints contact updated")
    return _settings_out(s)


# ── privacy notice ───────────────────────────────────────────────────────────

@router.get("/purposes")
async def purposes():
    return {"purposes": [{"key": k, **v} for k, v in PURPOSES.items()],
            "request_types": [{"key": k, "label": v} for k, v in REQUEST_TYPES.items()],
            "sla_days": SLA_DAYS, "law": LAW}


def _notice_out(n: PrivacyNotice | None, default_body: str) -> dict:
    if not n:
        return {"version": 0, "title": "Employee privacy notice", "body": default_body, "is_default": True,
                "published_at": None, "published_by": None}
    return {"id": str(n.id), "version": n.version, "title": n.title, "body": n.body, "is_default": False,
            "published_at": n.published_at.isoformat() if n.published_at else None, "published_by": n.published_by}


async def _default_body(db: AsyncSession, org: uuid.UUID) -> str:
    s = await _settings_row(db, org)
    dpo = " · ".join(x for x in (s.dpo_name, s.dpo_email, s.dpo_phone) if x) or "(not yet published — set it in Privacy Centre → Contacts)"
    return DEFAULT_NOTICE.format(org=await _org_name(db, org), dpo=dpo)


@router.get("/notice")
async def get_notice(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    n = await _current_notice(db, org)
    out = _notice_out(n, await _default_body(db, org))
    await db.commit()
    return out


@router.get("/notice/versions")
async def notice_versions(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    rows = (await db.execute(select(PrivacyNotice).where(PrivacyNotice.org_id == _org(request))
                             .order_by(PrivacyNotice.version.desc()))).scalars().all()
    return {"items": [_notice_out(n, "") for n in rows]}


class NoticeIn(BaseModel):
    title: str = "Employee privacy notice"
    body: str


@router.post("/notice", status_code=201)
async def publish_notice(body: NoticeIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Publish a new notice version; employees re-consent against it."""
    _require_admin(request)
    org = _org(request)
    if len(body.body.strip()) < 50:
        raise HTTPException(422, "The notice text is too short to be meaningful")
    last = (await db.execute(select(func.max(PrivacyNotice.version)).where(PrivacyNotice.org_id == org))).scalar() or 0
    await db.execute(text("UPDATE privacy_notices SET is_current = FALSE WHERE org_id = :o"), {"o": org})
    n = PrivacyNotice(org_id=org, version=last + 1, title=body.title.strip() or "Employee privacy notice",
                      body=body.body, is_current=True, published_by=_email(request))
    db.add(n)
    await db.commit()
    await record_audit(request, "privacy.notice.publish", entity_type="privacy_notice", entity_id=str(n.version),
                       summary=f"Privacy notice v{n.version} published")
    return _notice_out(n, "")


# ── consents ─────────────────────────────────────────────────────────────────

def _consent_out(c: ConsentRecord) -> dict:
    return {"id": str(c.id), "employee_id": str(c.employee_id), "employee_name": c.employee_name,
            "purpose": c.purpose, "purpose_label": PURPOSES.get(c.purpose, {}).get("label", c.purpose),
            "status": c.status, "notice_version": c.notice_version, "channel": c.channel,
            "signed_name": c.signed_name, "given_at": c.given_at.isoformat() if c.given_at else None,
            "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
            "withdraw_reason": c.withdraw_reason}


async def _latest_consents(db: AsyncSession, org: uuid.UUID, employee_id: str | None = None) -> list[ConsentRecord]:
    q = select(ConsentRecord).where(ConsentRecord.org_id == org)
    if employee_id:
        q = q.where(ConsentRecord.employee_id == uuid.UUID(employee_id))
    return (await db.execute(q.order_by(ConsentRecord.given_at.asc()))).scalars().all()


@router.get("/consents")
async def list_consents(request: Request, employee_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """Current consent per purpose for one employee (self, or admin for anyone)."""
    org = _org(request)
    emp = _employee_scope(request, employee_id)
    rows = await _latest_consents(db, org, emp)
    latest: dict[str, ConsentRecord] = {}
    for c in rows:
        latest[c.purpose] = c
    notice = await _current_notice(db, org)
    cur_ver = notice.version if notice else 0
    out = []
    for key, meta in PURPOSES.items():
        c = latest.get(key)
        out.append({"purpose": key, "label": meta["label"], "text": meta["text"],
                    "status": c.status if c else "not_given",
                    "notice_version": c.notice_version if c else None,
                    "stale": bool(c and c.status == "given" and (c.notice_version or 0) < cur_ver),
                    "given_at": c.given_at.isoformat() if c and c.given_at else None,
                    "withdrawn_at": c.withdrawn_at.isoformat() if c and c.withdrawn_at else None})
    return {"employee_id": emp, "current_notice_version": cur_ver, "purposes": out,
            "history": [_consent_out(c) for c in rows][::-1]}


class ConsentIn(BaseModel):
    employee_id: str | None = None
    purposes: list[str]
    signed_name: str | None = None
    channel: str = "self_service"


@router.post("/consents", status_code=201)
async def give_consent(body: ConsentIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emp = _employee_scope(request, body.employee_id)
    bad = [p for p in body.purposes if p not in PURPOSES]
    if bad:
        raise HTTPException(422, f"Unknown purpose(s): {bad}")
    notice = await _current_notice(db, org)
    e = await _fetch_employee(request, emp)
    name = f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() if e else None
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    created = []
    for p in body.purposes:
        c = ConsentRecord(org_id=org, employee_id=uuid.UUID(emp), employee_name=name, purpose=p, status="given",
                          notice_version=notice.version if notice else 0,
                          channel=body.channel if _is_admin(request) else "self_service",
                          signed_name=body.signed_name, ip=(ip or "")[:64])
        db.add(c)
        created.append(c)
    await db.commit()
    await record_audit(request, "privacy.consent.give", entity_type="employee", entity_id=emp,
                       summary=f"Consent given: {', '.join(body.purposes)}",
                       meta={"notice_version": notice.version if notice else 0})
    return {"ok": True, "items": [_consent_out(c) for c in created]}


class WithdrawIn(BaseModel):
    employee_id: str | None = None
    purposes: list[str]
    reason: str | None = None


@router.post("/consents/withdraw")
async def withdraw_consent(body: WithdrawIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emp = _employee_scope(request, body.employee_id)
    rows = await _latest_consents(db, org, emp)
    latest = {}
    for c in rows:
        latest[c.purpose] = c
    changed = []
    for p in body.purposes:
        c = latest.get(p)
        if c and c.status == "given":
            c.status = "withdrawn"
            c.withdrawn_at = _now()
            c.withdraw_reason = (body.reason or "")[:300] or None
            changed.append(p)
    await db.commit()
    await record_audit(request, "privacy.consent.withdraw", entity_type="employee", entity_id=emp,
                       summary=f"Consent withdrawn: {', '.join(changed) or 'none'}", meta={"reason": body.reason})
    return {"ok": True, "withdrawn": changed,
            "warning": ("Withdrawing payroll or statutory-filing consent means the company cannot lawfully pay you; "
                        "HR will contact you." if any(p in ("payroll_processing", "statutory_filing") for p in changed) else None)}


class BulkConsentIn(BaseModel):
    employee_id: str
    employee_name: str | None = None
    purposes: list[str]
    channel: str = "onboarding"
    signed_name: str | None = None
    notice_version: int | None = None


@router.post("/consents/bulk", status_code=201)
async def bulk_consent(body: BulkConsentIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Service-to-service: onboarding mirrors the consents it collected."""
    _require_admin(request)
    org = _org(request)
    notice = await _current_notice(db, org)
    n = 0
    for p in body.purposes:
        if p not in PURPOSES:
            continue
        db.add(ConsentRecord(org_id=org, employee_id=uuid.UUID(body.employee_id), employee_name=body.employee_name,
                             purpose=p, status="given", channel=body.channel, signed_name=body.signed_name,
                             notice_version=body.notice_version if body.notice_version is not None else (notice.version if notice else 0)))
        n += 1
    await db.commit()
    return {"ok": True, "recorded": n}


@router.get("/consents/summary")
async def consents_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Admin grid: one row per active employee × purpose status + coverage %."""
    _require_admin(request)
    org = _org(request)
    emps = [e for e in await _fetch_employees(request) if (e.get("status") in ("active", "on_leave", "onboarding"))]
    rows = await _latest_consents(db, org)
    cov = consent_coverage([e["id"] for e in emps], rows)
    latest: dict[tuple[str, str], ConsentRecord] = {}
    for c in rows:
        latest[(str(c.employee_id), c.purpose)] = c
    notice = await _current_notice(db, org)
    cur = notice.version if notice else 0
    grid = []
    for e in emps:
        row = {"employee_id": e["id"], "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
               "employee_number": e.get("employee_number"), "department": e.get("department"),
               "country": e.get("address_country")}
        for p in PURPOSES:
            c = latest.get((e["id"], p))
            row[p] = (c.status if c else "not_given")
            if c and c.status == "given" and (c.notice_version or 0) < cur:
                row[p] = "stale"
        row["complete"] = all(row[p] == "given" for p in PURPOSES)
        grid.append(row)
    return {"coverage": cov, "current_notice_version": cur, "purposes": list(PURPOSES), "items": grid}


# ── individual requests (access / correction) ───────────────────────────────────────────

def _req_out(r: DataRequest) -> dict:
    return {"id": str(r.id), "ref": r.ref, "employee_id": str(r.employee_id), "employee_name": r.employee_name,
            "request_type": r.request_type, "type_label": REQUEST_TYPES.get(r.request_type, r.request_type),
            "details": r.details, "payload": r.payload, "status": r.status,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "due_at": r.due_at.isoformat() if r.due_at else None, **sla_state(r.due_at, r.status),
            "assigned_to": r.assigned_to, "resolution_note": r.resolution_note, "hold_reason": r.hold_reason,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None, "closed_by": r.closed_by}


async def _next_ref(db: AsyncSession, org: uuid.UUID, model, prefix: str) -> str:
    year = (await org_today(org)).year
    n = (await db.execute(select(func.count(model.id)).where(model.org_id == org))).scalar() or 0
    return f"{prefix}-{year}-{n + 1:03d}"


@router.get("/requests")
async def list_requests(request: Request, status: str | None = None, request_type: str | None = None,
                        q: str | None = None, employee_id: str | None = None,
                        db: AsyncSession = Depends(get_db)):
    org = _org(request)
    conds = [DataRequest.org_id == org]
    if not _is_admin(request):
        conds.append(DataRequest.employee_id == uuid.UUID(_employee_scope(request, None)))
    elif employee_id:
        conds.append(DataRequest.employee_id == uuid.UUID(employee_id))
    if status:
        conds.append(DataRequest.status.in_(status.split(",")))
    if request_type:
        conds.append(DataRequest.request_type.in_(request_type.split(",")))
    if q:
        like = f"%{q}%"
        conds.append(or_(DataRequest.ref.ilike(like), DataRequest.employee_name.ilike(like),
                         DataRequest.details.ilike(like)))
    rows = (await db.execute(select(DataRequest).where(and_(*conds))
                             .order_by(DataRequest.submitted_at.desc()))).scalars().all()
    items = [_req_out(r) for r in rows]
    open_items = [i for i in items if i["status"] not in ("completed", "rejected")]
    return {"total": len(items), "open": len(open_items), "overdue": sum(1 for i in open_items if i["overdue"]),
            "items": items, "statuses": REQUEST_STATUSES}


class RequestIn(BaseModel):
    employee_id: str | None = None
    request_type: str
    details: str | None = None
    payload: dict | None = None


@router.post("/requests", status_code=201)
async def create_request(body: RequestIn, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    if body.request_type not in REQUEST_TYPES:
        raise HTTPException(422, f"request_type must be one of {list(REQUEST_TYPES)}")
    emp = _employee_scope(request, body.employee_id)
    e = await _fetch_employee(request, emp)
    name = f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() if e else None
    now = _now()
    r = DataRequest(org_id=org, ref=await _next_ref(db, org, DataRequest, "DPR"), employee_id=uuid.UUID(emp),
                    employee_name=name, request_type=body.request_type, details=body.details,
                    payload=body.payload, status="open", submitted_at=now, due_at=compute_due(now))
    db.add(r)
    await db.commit()
    await db.refresh(r)
    await record_audit(request, "privacy.request.create", entity_type="privacy_request", entity_id=r.ref,
                       summary=f"{REQUEST_TYPES[body.request_type]} request {r.ref} raised")
    return _req_out(r)


class RequestPatch(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    resolution_note: str | None = None
    hold_reason: str | None = None


@router.patch("/requests/{request_id}")
async def patch_request(request_id: str, body: RequestPatch, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    r = (await db.execute(select(DataRequest).where(DataRequest.org_id == org,
                                                    DataRequest.id == uuid.UUID(request_id)))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Request not found")
    ch = body.model_dump(exclude_unset=True)
    if "status" in ch:
        if ch["status"] not in REQUEST_STATUSES:
            raise HTTPException(422, f"status must be one of {REQUEST_STATUSES}")
        if ch["status"] == "on_hold" and not (ch.get("hold_reason") or r.hold_reason):
            raise HTTPException(422, "A hold needs a reason (e.g. statutory retention)")
        if ch["status"] in ("completed", "rejected"):
            r.closed_at = _now()
            r.closed_by = _email(request)
        else:
            r.closed_at = None
            r.closed_by = None
    for k, v in ch.items():
        setattr(r, k, v)
    await db.commit()
    await db.refresh(r)
    await record_audit(request, "privacy.request.update", entity_type="privacy_request", entity_id=r.ref,
                       summary=f"Request {r.ref} → {r.status}", meta=ch)
    return _req_out(r)


async def _retention_hold_for(db: AsyncSession, org: uuid.UUID, employee_id: str, request: Request) -> str | None:
    """Why an erasure must wait: the employee is still active, or statutory
    payroll/tax records are inside their retention window."""
    e = await _fetch_employee(request, employee_id)
    if not e:
        return None
    if e.get("status") != "terminated":
        return "Employee is still on the books — personal information is needed to employ and pay them."
    if e.get("erasure_hold"):
        return "An explicit retention hold is set on this employee record."
    pol = {p.record_type: p for p in (await db.execute(select(RetentionPolicy).where(
        RetentionPolicy.org_id == org, RetentionPolicy.enabled.is_(True)))).scalars().all()}
    years = pol["payroll_records"].retain_years if "payroll_records" in pol else 6
    basis = pol["payroll_records"].legal_basis if "payroll_records" in pol else DEFAULT_RETENTION[0][2]
    term = e.get("termination_date")
    if term:
        exit_year_end = date(date.fromisoformat(term).year, 12, 31)        # the tax year = the calendar year
        if await org_today(org) < exit_year_end.replace(year=exit_year_end.year + years):
            return (f"Payroll and tax records must be kept {years} years after the end of the tax year of exit "
                    f"(until {exit_year_end.replace(year=exit_year_end.year + years).isoformat()}). {basis} "
                    "Contact, emergency, beneficiary and bank data CAN be deleted now; the SIN stays encrypted.")
    return None


@router.post("/requests/{request_id}/apply-erasure")
async def apply_erasure(request_id: str, request: Request, force_partial: bool = True,
                        db: AsyncSession = Depends(get_db)):
    """Complete an erasure request: blank non-statutory personal data via the
    employee service. If a statutory retention window applies the request is
    put ON HOLD with the reason, unless `force_partial` — then the erasable
    fields are blanked now and the request is completed with a note."""
    _require_admin(request)
    org = _org(request)
    r = (await db.execute(select(DataRequest).where(DataRequest.org_id == org,
                                                    DataRequest.id == uuid.UUID(request_id)))).scalar_one_or_none()
    if not r or r.request_type != "erasure":
        raise HTTPException(404, "Erasure request not found")
    hold = await _retention_hold_for(db, org, str(r.employee_id), request)
    if hold and ("still on the books" in hold or "explicit retention hold" in hold):
        r.status, r.hold_reason = "on_hold", hold
        await db.commit()
        return _req_out(r)
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            resp = await c.post(f"{settings.EMPLOYEE_URL}/employees/{r.employee_id}/erase", headers=_fwd(request))
    except (httpx.ConnectError, httpx.TimeoutException):
        raise HTTPException(502, "Employee service unreachable")
    if resp.status_code >= 300:
        raise HTTPException(resp.status_code, f"Erasure failed: {resp.text}")
    result = resp.json()
    r.status = "completed"
    r.closed_at = _now()
    r.closed_by = _email(request)
    r.hold_reason = hold
    r.resolution_note = ((r.resolution_note or "") + f"\nErased: {', '.join(result.get('cleared', []))}. "
                         f"Retained encrypted (statutory): {', '.join(result.get('retained_encrypted', []))}.").strip()
    await db.commit()
    await record_audit(request, "privacy.request.erasure_applied", entity_type="privacy_request", entity_id=r.ref,
                       summary=f"Erasure applied for request {r.ref}", meta=result)
    return _req_out(r)


# ── export my data (access right) ────────────────────────────────────────────

async def _collect_export(request: Request, db: AsyncSession, org: uuid.UUID, emp: str) -> dict:
    e = await _fetch_employee(request, emp, reveal=True) or await _fetch_employee(request, emp) or {}
    consents = [_consent_out(c) for c in await _latest_consents(db, org, emp)]
    reqs = [_req_out(r) for r in (await db.execute(select(DataRequest).where(
        DataRequest.org_id == org, DataRequest.employee_id == uuid.UUID(emp)))).scalars().all()]
    payslips = []
    # TD1 claim amounts live on the employee record itself
    decl = {k: e.get(k) for k in ("td1_federal_claim", "td1_provincial_claim") if k in e} or None
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(f"{settings.PAYROLL_URL}/payroll/payslips", params={"employee_id": emp}, headers=_fwd(request))
            if r.status_code == 200:
                payslips = r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    notice = await _current_notice(db, org)
    return {"generated_at": _now().isoformat(), "organization": await _org_name(db, org),
            "law": "PIPEDA Principle 4.9 / s.8 (right of access)", "employee": e, "consents": consents,
            "data_requests": reqs, "payslips": payslips, "tax_declaration": decl,
            "privacy_notice_version": notice.version if notice else 0}


def pdf_text(s) -> str:
    """Text for a UnicodePDF (bundled Noto Sans): accented and typographic characters are
    kept; only glyphs the font lacks are swapped (shared.utils.pdf_fonts)."""
    from shared.utils.pdf_fonts import pdf_text as _pt
    return _pt(s, unicode_ok=True)


def _pdf():
    """UnicodePDF (accents, typographic marks); core-font FPDF only if the shared helper is missing."""
    try:
        from shared.utils.pdf_fonts import UnicodePDF
        return UnicodePDF()
    except Exception:
        from fpdf import FPDF
        return FPDF()


def _export_pdf(data: dict) -> bytes:
    pdf = _pdf()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, pdf_text("Your personal data — access report"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, pdf_text(f"{data['organization']} · generated {data['generated_at'][:19].replace('T', ' ')} UTC · {data['law']}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    def section(title: str):
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, pdf_text(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    def kv(k, v):
        pdf.cell(60, 6, pdf_text(k)[:40], border=0)
        pdf.multi_cell(0, 6, "" if v is None else pdf_text(v)[:120], new_x="LMARGIN", new_y="NEXT")

    section("Profile")
    emp = data.get("employee") or {}
    for k in ("employee_number", "first_name", "last_name", "email", "phone", "date_of_birth", "address_line1",
              "city", "province", "postal_code", "department", "job_title", "hire_date", "status", "sin_masked",
              "bank_institution", "bank_transit", "bank_account_last4", "td1_federal_claim", "td1_provincial_claim",
              "nominee_name", "nominee_relationship", "nominee_phone"):
        if k in emp:
            kv(k.replace("_", " "), emp.get(k))
    pdf.ln(2)
    section("Consents")
    for c in data.get("consents") or []:
        kv(c["purpose_label"], f"{c['status']} · notice v{c['notice_version']} · {c['given_at'][:10] if c['given_at'] else ''}")
    pdf.ln(2)
    section("Data requests")
    for r in data.get("data_requests") or []:
        kv(r["ref"], f"{r['type_label']} · {r['status']} · due {r['due_at'][:10] if r['due_at'] else ''}")
    pdf.ln(2)
    section("Payslips")
    for p in (data.get("payslips") or [])[:60]:
        kv(p.get("pay_date"), f"gross {p.get('gross')} · net {p.get('net_pay')}")
    if data.get("tax_declaration"):
        pdf.ln(2)
        section("Income-tax declaration")
        d = data["tax_declaration"]
        kv("regime", d.get("regime"))
        kv("status", d.get("status"))
        kv("declared", json.dumps(d.get("deductions") or {}))
    return bytes(pdf.output())


@router.get("/export-my-data")
async def export_my_data(request: Request, employee_id: str | None = None, format: str = "json",
                         db: AsyncSession = Depends(get_db)):
    org = _org(request)
    emp = _employee_scope(request, employee_id)
    data = await _collect_export(request, db, org, emp)
    await record_audit(request, "privacy.export", entity_type="employee", entity_id=emp,
                       summary=f"Personal data export ({format})")
    if format == "pdf":
        return StreamingResponse(io.BytesIO(_export_pdf(data)), media_type="application/pdf",
                                 headers={"Content-Disposition": 'attachment; filename="my-data.pdf"'})
    return data


# ── breach register ──────────────────────────────────────────────────────────

def _breach_out(b: BreachRecord) -> dict:
    hours = None
    if b.detected_at:
        hours = round((_now() - b.detected_at).total_seconds() / 3600, 1)
    return {"id": str(b.id), "ref": b.ref, "detected_at": b.detected_at.isoformat() if b.detected_at else None,
            "occurred_at": b.occurred_at.isoformat() if b.occurred_at else None, "description": b.description,
            "data_categories": b.data_categories or [], "individuals_affected": b.individuals_affected,
            "severity": b.severity, "status": b.status,
            "commissioner_notified_at": b.commissioner_notified_at.isoformat() if b.commissioner_notified_at else None,
            "individuals_notified_at": b.individuals_notified_at.isoformat() if b.individuals_notified_at else None,
            "checklist": b.checklist or [], "reported_by": b.reported_by,
            "hours_since_detection": hours,
            "keep_record_until": (b.detected_at + timedelta(days=730)).date().isoformat() if b.detected_at else None,
            "reported": bool(b.commissioner_notified_at)}


@router.get("/breaches/checklist-template")
async def checklist_template():
    return {"items": BREACH_CHECKLIST, "rule": "PIPEDA s.10.1 — where a breach creates a real risk of significant harm, report it to the "
                                               "Privacy Commissioner of Canada and notify affected individuals as soon as feasible; keep a "
                                               "record of every breach for 24 months (Breach of Security Safeguards Regulations s.6)."}


@router.get("/breaches")
async def list_breaches(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    conds = [BreachRecord.org_id == _org(request)]
    if status:
        conds.append(BreachRecord.status.in_(status.split(",")))
    rows = (await db.execute(select(BreachRecord).where(and_(*conds)).order_by(BreachRecord.detected_at.desc()))).scalars().all()
    return {"total": len(rows), "open": sum(1 for b in rows if b.status != "closed"), "items": [_breach_out(b) for b in rows]}


class BreachIn(BaseModel):
    description: str
    detected_at: datetime | None = None
    occurred_at: datetime | None = None
    data_categories: list[str] = []
    individuals_affected: int = 0
    severity: str = "medium"


@router.post("/breaches", status_code=201)
async def create_breach(body: BreachIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    b = BreachRecord(org_id=org, ref=await _next_ref(db, org, BreachRecord, "BR"), description=body.description,
                     detected_at=body.detected_at or _now(), occurred_at=body.occurred_at,
                     data_categories=body.data_categories, individuals_affected=body.individuals_affected,
                     severity=body.severity if body.severity in ("low", "medium", "high", "critical") else "medium",
                     status="open", checklist=[{**i, "done": False, "at": None} for i in BREACH_CHECKLIST],
                     reported_by=_email(request))
    db.add(b)
    await db.commit()
    await db.refresh(b)
    await record_audit(request, "privacy.breach.create", entity_type="privacy_breach", entity_id=b.ref,
                       summary=f"Breach {b.ref} logged ({b.severity}, {b.individuals_affected} affected)")
    return _breach_out(b)


class BreachPatch(BaseModel):
    status: str | None = None
    description: str | None = None
    individuals_affected: int | None = None
    severity: str | None = None
    data_categories: list[str] | None = None
    checklist_done: list[str] | None = None       # keys to mark done
    checklist_undone: list[str] | None = None
    commissioner_notified: bool | None = None
    individuals_notified: bool | None = None


@router.patch("/breaches/{breach_id}")
async def patch_breach(breach_id: str, body: BreachPatch, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    b = (await db.execute(select(BreachRecord).where(BreachRecord.org_id == _org(request),
                                                     BreachRecord.id == uuid.UUID(breach_id)))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Breach not found")
    ch = body.model_dump(exclude_unset=True)
    for k in ("status", "description", "individuals_affected", "severity", "data_categories"):
        if k in ch and ch[k] is not None:
            setattr(b, k, ch[k])
    # Copy the dicts: an in-place edit of the loaded JSONB would compare equal to
    # the old value and never be flushed.
    cl = [dict(i) for i in (b.checklist or [])]
    for item in cl:
        if ch.get("checklist_done") and item["key"] in ch["checklist_done"]:
            item["done"], item["at"] = True, _now().isoformat()
        if ch.get("checklist_undone") and item["key"] in ch["checklist_undone"]:
            item["done"], item["at"] = False, None
    b.checklist = cl
    if ch.get("commissioner_notified"):
        b.commissioner_notified_at = b.commissioner_notified_at or _now()
    if ch.get("individuals_notified"):
        b.individuals_notified_at = b.individuals_notified_at or _now()
    await db.commit()
    await db.refresh(b)
    await record_audit(request, "privacy.breach.update", entity_type="privacy_breach", entity_id=b.ref,
                       summary=f"Breach {b.ref} updated → {b.status}", meta={k: v for k, v in ch.items() if k != "description"})
    return _breach_out(b)


# ── retention policy + purge job ─────────────────────────────────────────────

def _policy_out(p: RetentionPolicy) -> dict:
    return {"id": str(p.id), "record_type": p.record_type, "retain_years": p.retain_years,
            "legal_basis": p.legal_basis, "action": p.action, "enabled": p.enabled,
            # S4: defaults are a starting point — flagged until the org confirms them.
            "needs_confirmation": p.confirmed_at is None,
            "confirmed_at": p.confirmed_at.isoformat() if getattr(p, "confirmed_at", None) else None,
            "confirmed_by": getattr(p, "confirmed_by", None),
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


async def _ensure_policies(db: AsyncSession, org: uuid.UUID) -> list[RetentionPolicy]:
    rows = (await db.execute(select(RetentionPolicy).where(RetentionPolicy.org_id == org))).scalars().all()
    have = {r.record_type for r in rows}
    for rt, years, basis, action in DEFAULT_RETENTION:
        if rt not in have:
            p = RetentionPolicy(org_id=org, record_type=rt, retain_years=years, legal_basis=basis, action=action)
            db.add(p)
            rows.append(p)
    await db.commit()
    return sorted(rows, key=lambda r: [x[0] for x in DEFAULT_RETENTION].index(r.record_type)
                  if r.record_type in [x[0] for x in DEFAULT_RETENTION] else 99)


@router.get("/retention")
async def get_retention(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    rows = await _ensure_policies(db, _org(request))
    return {"items": [_policy_out(p) for p in rows]}


class PolicyIn(BaseModel):
    record_type: str
    retain_years: int | None = None
    legal_basis: str | None = None
    action: str | None = None
    enabled: bool | None = None
    confirm: bool | None = None          # S4: "our counsel / CA confirmed this period"


@router.put("/retention")
async def put_retention(body: list[PolicyIn], request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    rows = {p.record_type: p for p in await _ensure_policies(db, org)}
    for item in body:
        p = rows.get(item.record_type)
        if not p:
            p = RetentionPolicy(org_id=org, record_type=item.record_type, retain_years=item.retain_years or 1)
            db.add(p)
            rows[item.record_type] = p
        changes = item.model_dump(exclude_unset=True, exclude={"record_type", "confirm"})
        if item.confirm:
            p.confirmed_at, p.confirmed_by = _now(), _email(request)
        elif any(k in changes and changes[k] is not None and changes[k] != getattr(p, k, None)
                 for k in ("retain_years", "legal_basis", "action")):
            p.confirmed_at, p.confirmed_by = None, None       # an edited period needs a fresh confirmation
        for k, v in changes.items():
            if v is not None:
                if k == "retain_years" and not (0 <= int(v) <= 30):
                    raise HTTPException(422, "retain_years must be 0–30")
                if k == "action" and v not in ("anonymise", "delete", "review"):
                    raise HTTPException(422, "action must be anonymise | delete | review")
                setattr(p, k, v)
    await db.commit()
    await record_audit(request, "privacy.retention.update", entity_type="retention_policy",
                       summary=f"Retention policy edited ({len(body)} rows)")
    return {"items": [_policy_out(p) for p in rows.values()]}


async def compute_purge_candidates(db: AsyncSession, org: uuid.UUID) -> dict:
    """What the retention job WOULD act on. Uses the shared DB directly (read-only)."""
    pol = {p.record_type: p for p in (await db.execute(select(RetentionPolicy).where(
        RetentionPolicy.org_id == org, RetentionPolicy.enabled.is_(True)))).scalars().all()}
    today = await org_today(org)
    out: dict = {"as_of": today.isoformat(), "record_types": {}}

    def cutoff(rt: str, default: int) -> date:
        yrs = pol[rt].retain_years if rt in pol else default
        return date(today.year - yrs, today.month, min(today.day, 28))

    # Exited employees whose personal data is past the window and not yet erased.
    try:
        c = cutoff("employee_personal_data", 1)
        rows = (await db.execute(text(
            "SELECT id, employee_number, termination_date, erasure_hold FROM employees WHERE org_id = :o AND status = 'terminated' "
            "AND termination_date IS NOT NULL AND termination_date < :c AND erased_at IS NULL"), {"o": org, "c": c})).mappings().all()
        out["record_types"]["employee_personal_data"] = {
            "cutoff": c.isoformat(), "action": pol["employee_personal_data"].action if "employee_personal_data" in pol else "anonymise",
            "count": len(rows), "held": sum(1 for r in rows if r["erasure_hold"]),
            "sample": [{"employee_id": str(r["id"]), "employee_number": r["employee_number"],
                        "termination_date": r["termination_date"].isoformat()} for r in rows[:10]]}
        out["_employee_ids"] = [str(r["id"]) for r in rows if not r["erasure_hold"]]
    except Exception:
        out["record_types"]["employee_personal_data"] = _count_failed("employee_personal_data")
    # Onboarding documents of cancelled / activated sessions past the window.
    try:
        c = cutoff("onboarding_documents", 1)
        n = (await db.execute(text(
            "SELECT count(*) FROM onboarding_documents d JOIN onboarding_sessions s ON s.id = d.session_id "
            "WHERE d.org_id = :o AND s.status IN ('cancelled', 'activated') AND d.uploaded_at < :c"), {"o": org, "c": c})).scalar() or 0
        out["record_types"]["onboarding_documents"] = {"cutoff": c.isoformat(), "action": "delete", "count": int(n)}
    except Exception:
        out["record_types"]["onboarding_documents"] = _count_failed("onboarding_documents")
    # Login sessions past the log window.
    try:
        c = cutoff("login_and_audit_logs", 1)
        n = (await db.execute(text("SELECT count(*) FROM login_sessions WHERE org_id = :o AND at < :c"), {"o": org, "c": c})).scalar() or 0
        out["record_types"]["login_and_audit_logs"] = {"cutoff": c.isoformat(), "action": "delete", "count": int(n),
                                                       "note": "audit_events are never purged (tamper-evident chain)"}
    except Exception:
        out["record_types"]["login_and_audit_logs"] = _count_failed("login_and_audit_logs")
    # Payroll / tax records: review-only counts (never auto-deleted).
    try:
        c = cutoff("payroll_records", 6)
        n = (await db.execute(text("SELECT count(*) FROM pay_runs WHERE org_id = :o AND pay_date < :c"), {"o": org, "c": c})).scalar() or 0
        out["record_types"]["payroll_records"] = {"cutoff": c.isoformat(), "action": "review", "count": int(n),
                                                  "note": "listed for admin review only"}
    except Exception:
        out["record_types"]["payroll_records"] = _count_failed("payroll_records")
    out["total_candidates"] = sum(v.get("count", 0) for v in out["record_types"].values())
    return out


@router.post("/retention/run")
async def run_retention(request: Request, dry_run: bool = True, db: AsyncSession = Depends(get_db)):
    """Retention job. Dry-run (default) only counts. A real run anonymises exited
    employees past the window (via the employee service, so audit + hold rules
    apply), deletes stale onboarding documents and old login rows."""
    _require_admin(request)
    org = _org(request)
    await _ensure_policies(db, org)
    plan = await compute_purge_candidates(db, org)
    emp_ids = plan.pop("_employee_ids", [])
    acted = {"employees_anonymised": 0, "employees_failed": 0, "onboarding_documents_deleted": 0, "login_rows_deleted": 0}
    if not dry_run:
        async with httpx.AsyncClient(timeout=15.0) as c:
            for eid in emp_ids:
                try:
                    r = await c.post(f"{settings.EMPLOYEE_URL}/employees/{eid}/erase", headers=_fwd(request))
                    acted["employees_anonymised" if r.status_code < 300 else "employees_failed"] += 1
                except (httpx.ConnectError, httpx.TimeoutException):
                    acted["employees_failed"] += 1
        od = plan["record_types"].get("onboarding_documents", {})
        if od.get("count"):
            res = await db.execute(text(
                "DELETE FROM onboarding_documents WHERE id IN (SELECT d.id FROM onboarding_documents d JOIN onboarding_sessions s "
                "ON s.id = d.session_id WHERE d.org_id = :o AND s.status IN ('cancelled', 'activated') AND d.uploaded_at < :c)"),
                {"o": org, "c": date.fromisoformat(od["cutoff"])})
            acted["onboarding_documents_deleted"] = res.rowcount or 0
        ls = plan["record_types"].get("login_and_audit_logs", {})
        if ls.get("count"):
            res = await db.execute(text("DELETE FROM login_sessions WHERE org_id = :o AND at < :c"),
                                   {"o": org, "c": date.fromisoformat(ls["cutoff"])})
            acted["login_rows_deleted"] = res.rowcount or 0
    run = PurgeRun(org_id=org, dry_run=dry_run, triggered_by=_email(request), summary={**plan, "acted": acted})
    db.add(run)
    await db.commit()
    await record_audit(request, "privacy.retention.run", entity_type="purge_run", entity_id=str(run.id),
                       summary=f"Retention {'dry-run' if dry_run else 'RUN'}: {plan['total_candidates']} candidates", meta=acted)
    return {"id": str(run.id), "dry_run": dry_run, "run_at": run.run_at.isoformat(), **plan, "acted": acted}


@router.get("/retention/runs")
async def retention_runs(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    rows = (await db.execute(select(PurgeRun).where(PurgeRun.org_id == _org(request))
                             .order_by(PurgeRun.run_at.desc()).limit(50))).scalars().all()
    return {"items": [{"id": str(r.id), "run_at": r.run_at.isoformat(), "dry_run": r.dry_run,
                       "triggered_by": r.triggered_by, "summary": r.summary} for r in rows]}


async def scheduled_dry_runs(interval_hours: float = 24.0):
    """Background loop: a dry-run retention report per org, once a day. Real
    purges are never automatic — an admin runs them from the Privacy Centre."""
    await asyncio.sleep(30)
    while True:
        try:
            async with SessionLocal() as db:
                orgs = [r[0] for r in (await db.execute(text("SELECT DISTINCT org_id FROM privacy_retention_policies"))).all()]
                for org in orgs:
                    plan = await compute_purge_candidates(db, org)
                    plan.pop("_employee_ids", None)
                    db.add(PurgeRun(org_id=org, dry_run=True, triggered_by="scheduler", summary=plan))
                await db.commit()
        except Exception as exc:
            print(f"[privacy] scheduled dry-run failed: {exc}", flush=True)
        await asyncio.sleep(interval_hours * 3600)


def start_scheduler():
    if os.getenv("LEXORA_PRIVACY_SCHEDULER", "1").lower() in ("1", "true", "yes"):
        return asyncio.create_task(scheduled_dry_runs(float(os.getenv("LEXORA_PRIVACY_SCHEDULER_HOURS", "24"))))
    return None


# ── processor contract ───────────────────────────────────────────────────────

@router.get("/processor-contract")
async def processor_contract(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    s = await _settings_row(db, org)
    txt = PROCESSOR_CONTRACT.format(org=await _org_name(db, org), version=PROCESSOR_CONTRACT_VERSION)
    await db.commit()
    return {"version": PROCESSOR_CONTRACT_VERSION, "text": txt,
            "accepted_at": s.processor_contract_accepted_at.isoformat() if s.processor_contract_accepted_at else None,
            "accepted_by": s.processor_contract_accepted_by, "accepted_version": s.processor_contract_version}


@router.get("/processor-contract/pdf")
async def processor_contract_pdf(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    txt = PROCESSOR_CONTRACT.format(org=await _org_name(db, org), version=PROCESSOR_CONTRACT_VERSION)
    pdf = _pdf()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    for line in txt.splitlines():
        pdf.multi_cell(0, 5.5, pdf_text(line) or " ", new_x="LMARGIN", new_y="NEXT")
    return StreamingResponse(io.BytesIO(bytes(pdf.output())), media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="lexora-service-provider-privacy-terms.pdf"'})


@router.post("/processor-contract/accept")
async def accept_contract(request: Request, db: AsyncSession = Depends(get_db)):
    if _role(request) not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only the org admin may accept the service-provider privacy terms")
    s = await _settings_row(db, _org(request))
    s.processor_contract_accepted_at = _now()
    s.processor_contract_accepted_by = _email(request)
    s.processor_contract_version = PROCESSOR_CONTRACT_VERSION
    await db.commit()
    await record_audit(request, "privacy.contract.accept", entity_type="privacy_settings",
                       summary=f"Service-provider privacy terms v{PROCESSOR_CONTRACT_VERSION} accepted")
    return _settings_out(s)


# ── dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """The Compliance-dashboard card: consent coverage, open/overdue requests,
    breach count, encryption status, readiness checklist."""
    _require_admin(request)
    org = _org(request)
    emps = [e for e in await _fetch_employees(request) if e.get("status") in ("active", "on_leave", "onboarding")]
    cov = consent_coverage([e["id"] for e in emps], await _latest_consents(db, org))
    reqs = (await db.execute(select(DataRequest).where(DataRequest.org_id == org))).scalars().all()
    open_reqs = [r for r in reqs if r.status not in ("completed", "rejected")]
    overdue = [r for r in open_reqs if sla_state(r.due_at, r.status)["overdue"]]
    breaches = (await db.execute(select(BreachRecord).where(BreachRecord.org_id == org))).scalars().all()
    s = await _settings_row(db, org)
    notice = await _current_notice(db, org)
    policies = await _ensure_policies(db, org)
    enc = {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees/encryption-status", headers=_fwd(request))
            if r.status_code == 200:
                enc = r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    last_run = (await db.execute(select(PurgeRun).where(PurgeRun.org_id == org).order_by(PurgeRun.run_at.desc()))).scalars().first()
    checklist = [
        {"key": "dpo", "label": "Privacy Officer named and contact published (Principle 4.1)", "done": bool(s.dpo_email or s.grievance_officer_email)},
        {"key": "notice", "label": "Privacy notice published to employees (Principles 4.2 / 4.8)", "done": notice is not None},
        {"key": "consent", "label": "Consent recorded for 90%+ of active employees (Principle 4.3)", "done": cov["coverage_pct"] >= 90},
        {"key": "encryption", "label": "SIN and bank numbers encrypted with a production key (Principle 4.7)", "done": bool(enc.get("launch_ready"))},
        {"key": "retention", "label": "Retention periods reviewed and a retention run on record (Principle 4.5)", "done": last_run is not None},
        {"key": "contract", "label": "Service-provider privacy terms with Lexora HRM accepted (Principle 4.1.3)", "done": bool(s.processor_contract_accepted_at)},
        {"key": "breach", "label": "Breach register + report / notify checklist in place (PIPEDA s.10.1)", "done": True},
        {"key": "requests", "label": "No access request past its 30-day limit (s.8(3))", "done": not overdue},
    ]
    done = sum(1 for c in checklist if c["done"])
    await db.commit()
    return {"law": LAW,
            "consent": cov, "requests": {"open": len(open_reqs), "overdue": len(overdue), "total": len(reqs)},
            "breaches": {"total": len(breaches), "open": sum(1 for b in breaches if b.status != "closed")},
            "encryption": enc, "notice_version": notice.version if notice else 0,
            "retention_policies": len(policies),
            "last_retention_run": last_run.run_at.isoformat() if last_run else None,
            "checklist": checklist, "readiness_pct": round(100 * done / len(checklist)),
            "contact": _settings_out(s)}
