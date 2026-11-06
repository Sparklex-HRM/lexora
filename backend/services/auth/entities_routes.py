"""Auth service — legal entities API + boot ensure (stream D4, multi-entity).

    GET    /auth/entities                    every signed-in user: brief list (id, name, code, default)
                                             admins / HR / payroll: full rows (bank accounts masked)
    GET    /auth/entities/{id}
    POST   /auth/entities                    org_admin / superadmin
    PATCH  /auth/entities/{id}
    DELETE /auth/entities/{id}               deactivate (never the default; never one with employees)
    POST   /auth/entities/{id}/default       make it the default entity
    POST   /auth/entities/{id}/bank-accounts            add one (number encrypted at rest)
    DELETE /auth/entities/{id}/bank-accounts/{acct_id}
    GET    /auth/entities/{id}/bank-accounts/reveal     org_admin: full numbers (audited)
    POST   /auth/entities/{id}/letterhead               logo upload (PNG/JPEG ≤ 300 KB, bytes sniffed; SVG refused) → data URL

Formats are checked for SHAPE (CRA Business Number 9 digits, payroll account BN + RP + 4 digits,
Canadian postal code, institution 3 / transit 5 digits); CRA remains the authority on whether a
number is registered. Every write is audited with a
PII-masked before/after diff.

`init_entities()` runs at boot: adds any column an older table lacks, the per-org indexes, and
creates the DEFAULT entity of every org that has none from the organization's own details.
Migration 0003 does the same (idempotently) plus the assignment of existing employees / pay
records to that default entity.
"""
from __future__ import annotations

import base64
import re
import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared import legal_entities as LE  # noqa: E402
from shared.crypto_fields import FieldDecryptError, decrypt_field, encrypt_field  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.docstore import sniff_image  # noqa: E402

from database import SessionLocal, engine, get_db  # noqa: E402
from entities_models import LegalEntity  # noqa: E402
from models import Organization  # noqa: E402

router = APIRouter(prefix="/auth/entities", tags=["legal-entities"])

WRITE_ROLES = {"org_admin", "superadmin"}
FULL_READ_ROLES = {"org_admin", "superadmin", "hr_admin", "hr_manager", "payroll_admin"}

BN_RE = LE.BN_RE
PAYROLL_ACCOUNT_RE = LE.PAYROLL_ACCOUNT_RE
RQ_RE = re.compile(r"^[0-9]{10}(TQ[0-9]{4})?$")   # Revenu Québec identification number (+ TQ file no.)
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,11}$")
PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")

_AUDIT_FIELDS = ("name", "short_code", "is_default", "is_active", "entity_type", "business_number", "payroll_account",
                 "rq_identification", "address_line1", "address_line2", "city", "province", "postal_code", "country",
                 "email", "phone", "wcb_accounts", "licences", "signatory", "numbering_prefix")


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _who(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or ""


def _require_write(request: Request) -> uuid.UUID:
    if _role(request) not in WRITE_ROLES:
        raise HTTPException(403, "Only an organization admin can manage legal entities")
    return _org(request)


def _uuid_or_404(v, what="Legal entity") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(404, f"{what} not found")


def _upper(v: str | None) -> str | None:
    v = (v or "").strip().upper().replace(" ", "")
    return v or None


def _clean_str(v: str | None, n: int) -> str | None:
    v = (v or "").strip()
    return v[:n] or None


# ── validation (shapes only) ─────────────────────────────────────────────────
def validate_identifiers(d: dict) -> list[str]:
    errs = []
    if d.get("business_number") and not BN_RE.match(d["business_number"]):
        errs.append("CRA Business Number must be 9 digits")
    if d.get("payroll_account"):
        if not PAYROLL_ACCOUNT_RE.match(d["payroll_account"]):
            errs.append("CRA payroll account must look like 123456789RP0001")
        elif d.get("business_number") and not d["payroll_account"].startswith(d["business_number"]):
            errs.append("The payroll account must start with the entity's Business Number")
    if d.get("rq_identification") and not RQ_RE.match(d["rq_identification"]):
        errs.append("Revenu Québec identification number is 10 digits (optionally + TQ and 4 digits)")
    if d.get("province") and d["province"] not in PROVINCES:
        errs.append(f"Province must be one of {', '.join(PROVINCES)}")
    if "postal_code" in d and d["postal_code"] == "":
        errs.append("Postal code must look like A1A 1A1")
    if d.get("country") and d["country"] != "CA":
        errs.append("Lexora HRM is Canada-only: country must be CA")
    if d.get("short_code") and not CODE_RE.match(d["short_code"]):
        errs.append("Short code: 1–12 letters/digits (no spaces)")
    if d.get("numbering_prefix") and not re.match(r"^[A-Z0-9-]{1,12}$", d["numbering_prefix"]):
        errs.append("Numbering prefix: up to 12 letters/digits")
    if d.get("entity_type") and d["entity_type"] not in LE.ENTITY_TYPES:
        errs.append(f"entity_type must be one of {', '.join(LE.ENTITY_TYPES)}")
    for prov in (d.get("wcb_accounts") or {}):
        if prov not in PROVINCES:
            errs.append(f"WCB account province {prov} is not a Canadian province/territory")
    return errs


def _digits(v) -> str | None:
    v = re.sub(r"[\s-]", "", str(v or "")).upper()
    return v or None


def _clean_wcb(items) -> dict:
    out = {}
    for prov, v in (items or {}).items():
        prov = _upper(prov)
        if not prov or not isinstance(v, dict):
            continue
        row = {k: _clean_str(v.get(k), 60) for k in ("board", "account_no", "rate_group", "industry_code") if v.get(k)}
        if v.get("rate") not in (None, ""):
            try:
                row["rate"] = float(v["rate"])
            except (TypeError, ValueError):
                pass
        if row:
            out[prov] = row
    return out


def _clean_licences(items) -> list[dict]:
    out = []
    for x in items or []:
        if not isinstance(x, dict) or not (x.get("number") or "").strip():
            continue
        out.append({"kind": _clean_str(x.get("kind"), 30) or "other", "number": _clean_str(x.get("number"), 60),
                    "province": _upper(x.get("province")), "valid_to": _clean_str(x.get("valid_to"), 10),
                    "label": _clean_str(x.get("label"), 80)})
    return out


def _clean_signatory(s) -> dict:
    s = s or {}
    return {k: _clean_str(s.get(k), 160) for k in ("name", "designation", "email", "phone") if s.get(k)}


# ── schema ──────────────────────────────────────────────────────────────────
class EntityBody(BaseModel):
    name: str | None = None
    short_code: str | None = None
    entity_type: str | None = None
    business_number: str | None = None
    payroll_account: str | None = None
    rq_identification: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    country: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    wcb_accounts: dict | None = None
    licences: list[dict] | None = None
    signatory: dict | None = None
    letterhead: dict | None = None
    numbering_prefix: str | None = None
    is_active: bool | None = None


def _apply(e: LegalEntity, body: EntityBody, sent: set) -> list[str]:
    d: dict = {}
    for k in sent:
        v = getattr(body, k)
        if k in ("short_code", "province", "country", "numbering_prefix"):
            d[k] = _upper(v)
        elif k in ("business_number", "payroll_account", "rq_identification"):
            d[k] = _digits(v)
        elif k == "postal_code":
            try:
                d[k] = LE.normalise_postal_code(v)
            except ValueError:
                d[k] = ""                       # flagged by validate_identifiers
        elif k == "wcb_accounts":
            d[k] = _clean_wcb(v)
        elif k == "licences":
            d[k] = _clean_licences(v)
        elif k == "signatory":
            d[k] = _clean_signatory(v)
        elif k == "letterhead":
            lh = v or {}
            d[k] = {x: _clean_str(lh.get(x), 400) for x in ("header_text", "footer_text") if lh.get(x)}
            if (e.letterhead or {}).get("logo_data_url"):        # the logo is uploaded separately; keep it
                d[k]["logo_data_url"] = e.letterhead["logo_data_url"]
        elif k == "entity_type":
            d[k] = (v or "").strip().lower() or None
        elif k == "is_active":
            d[k] = bool(v)
        elif isinstance(v, str):
            d[k] = _clean_str(v, 200)
        else:
            d[k] = v
    errs = validate_identifiers(d)
    if "name" in d and not d["name"]:
        errs.append("Name is required")
    if "short_code" in d and not d["short_code"]:
        errs.append("Short code is required")
    if errs:
        return errs
    for k, v in d.items():
        setattr(e, k, v)
    return []


def _logo_state(e: LegalEntity) -> dict:
    """has_logo only for a PNG / JPEG logo. An old SVG letterhead (accepted before 2026-10-21) is not
    used or served any more: logo_needs_reupload tells the settings screen to ask for a PNG / JPEG."""
    url = str((e.letterhead or {}).get("logo_data_url") or "")
    ok = url.startswith(("data:image/png;", "data:image/jpeg;"))
    return {"has_logo": ok, "logo_needs_reupload": bool(url) and not ok}


def _out(e: LegalEntity, brief: bool = False) -> dict:
    if brief:
        return {"id": str(e.id), "name": e.name, "short_code": e.short_code, "is_default": bool(e.is_default),
                "is_active": e.is_active is not False, "province": e.province, "country": e.country or "CA",
                "letterhead": {k: v for k, v in (e.letterhead or {}).items() if k != "logo_data_url"},
                **_logo_state(e)}
    d = LE.row_dict(e)
    d["registration_gaps"] = LE.registration_gaps({**d, "bank_accounts": e.bank_accounts})
    d.update(_logo_state(e))
    return d


async def _get(db, org: uuid.UUID, entity_id) -> LegalEntity:
    e = (await db.execute(select(LegalEntity).where(
        LegalEntity.id == _uuid_or_404(entity_id), LegalEntity.org_id == org))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Legal entity not found")
    return e


async def _employee_count(db, entity_id: uuid.UUID) -> int:
    try:
        return int((await db.execute(text(
            "SELECT count(*) FROM employees WHERE entity_id = :e AND is_active"), {"e": str(entity_id)})).scalar() or 0)
    except Exception:  # noqa: BLE001 — employees table absent (unit DB) → no blocker
        await db.rollback()
        return 0


# ── routes ──────────────────────────────────────────────────────────────────
@router.get("")
async def list_entities(request: Request, include_inactive: int = 0, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    await ensure_org_default(db, org)
    q = select(LegalEntity).where(LegalEntity.org_id == org)
    if not include_inactive:
        q = q.where(LegalEntity.is_active.is_not(False))
    rows = (await db.execute(q.order_by(LegalEntity.is_default.desc(), LegalEntity.name))).scalars().all()
    brief = _role(request) not in FULL_READ_ROLES
    out = [_out(e, brief=brief) for e in rows]
    if not brief:
        for d in out:
            d["employee_count"] = await _employee_count(db, uuid.UUID(d["id"]))
    return {"entities": out, "total": len(out), "entity_types": list(LE.ENTITY_TYPES)}


@router.get("/{entity_id}")
async def get_entity(entity_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    e = await _get(db, org, entity_id)
    d = _out(e, brief=_role(request) not in FULL_READ_ROLES)
    if _role(request) in FULL_READ_ROLES:
        d["employee_count"] = await _employee_count(db, e.id)
    return d


@router.post("", status_code=201)
async def create_entity(body: EntityBody, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    if not (body.name or "").strip():
        raise HTTPException(422, "Name is required")
    await ensure_org_default(db, org)      # an org registered after boot: its own default comes first
    e = LegalEntity(org_id=org, name=body.name.strip(), short_code=_upper(body.short_code) or _derive_code(body.name),
                    country="CA", is_default=False, is_active=True)
    errs = _apply(e, body, set(body.model_fields_set) - {"is_active"})
    if errs:
        raise HTTPException(422, "; ".join(errs))
    dup = (await db.execute(select(LegalEntity).where(
        LegalEntity.org_id == org, func.upper(LegalEntity.short_code) == e.short_code.upper()))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, f"Short code {e.short_code} is already used by {dup.name}")
    has_any = (await db.execute(select(func.count(LegalEntity.id)).where(LegalEntity.org_id == org))).scalar() or 0
    e.is_default = has_any == 0            # the first entity of an org is its default
    e.updated_by = _who(request)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    LE.invalidate(org)
    from shared.audit_trail import audit_change, snapshot
    await audit_change(request, "entity.create", entity_type="legal_entity", entity_id=str(e.id), before=None,
                       after=snapshot(e, _AUDIT_FIELDS), summary=f"Legal entity {e.short_code} · {e.name} added")
    return _out(e)


def _derive_code(name: str) -> str:
    words = re.sub(r"[^A-Za-z0-9 ]", " ", name or "").upper().split()
    code = "".join(w[0] for w in words[:4]) if len(words) > 1 else (words[0][:8] if words else "ENT")
    return (code or "ENT")[:12]


@router.patch("/{entity_id}")
async def update_entity(entity_id: str, body: EntityBody, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    from shared.audit_trail import audit_change, snapshot
    before = snapshot(e, _AUDIT_FIELDS)
    sent = set(body.model_fields_set)
    if "is_active" in sent and body.is_active is False and e.is_default:
        raise HTTPException(400, "The default entity cannot be deactivated — make another entity the default first")
    errs = _apply(e, body, sent)
    if errs:
        raise HTTPException(422, "; ".join(errs))
    if "short_code" in sent:
        dup = (await db.execute(select(LegalEntity).where(
            LegalEntity.org_id == org, LegalEntity.id != e.id,
            func.upper(LegalEntity.short_code) == e.short_code.upper()))).scalar_one_or_none()
        if dup:
            raise HTTPException(409, f"Short code {e.short_code} is already used by {dup.name}")
    e.updated_by = _who(request)
    e.updated_at = _now()
    await db.commit()
    await db.refresh(e)
    LE.invalidate(org)
    await audit_change(request, "entity.update", entity_type="legal_entity", entity_id=str(e.id), before=before,
                       after=snapshot(e, _AUDIT_FIELDS), summary=f"Legal entity {e.short_code} updated")
    return _out(e)


@router.delete("/{entity_id}")
async def deactivate_entity(entity_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    if e.is_default:
        raise HTTPException(400, "The default entity cannot be removed — make another entity the default first")
    n = await _employee_count(db, e.id)
    if n:
        raise HTTPException(400, f"{n} employee(s) still belong to this entity — transfer them first")
    e.is_active = False
    e.updated_by = _who(request)
    await db.commit()
    LE.invalidate(org)
    await record_audit(request, "entity.deactivate", entity_type="legal_entity", entity_id=str(e.id),
                       summary=f"Legal entity {e.short_code} deactivated")
    return {"ok": True, "id": str(e.id), "is_active": False}


@router.post("/{entity_id}/default")
async def make_default(entity_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    if e.is_active is False:
        raise HTTPException(400, "Activate the entity before making it the default")
    for row in (await db.execute(select(LegalEntity).where(LegalEntity.org_id == org))).scalars().all():
        row.is_default = row.id == e.id
    await db.commit()
    LE.invalidate(org)
    await record_audit(request, "entity.default", entity_type="legal_entity", entity_id=str(e.id),
                       summary=f"{e.short_code} is now the default legal entity")
    return _out(e)


# ── bank accounts (encrypted) ───────────────────────────────────────────────
class BankAccountBody(BaseModel):
    label: str | None = None
    bank_name: str | None = None
    institution: str                      # 3-digit financial institution number
    transit: str                          # 5-digit branch transit number
    account_no: str                       # 7–12 digits
    account_type: str | None = None       # chequing | savings
    is_primary: bool = False


@router.post("/{entity_id}/bank-accounts", status_code=201)
async def add_bank_account(entity_id: str, body: BankAccountBody, request: Request, db: AsyncSession = Depends(get_db)):
    from shared.utils import id_validators as V
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    try:
        inst = V.clean_institution(body.institution)
        transit = V.clean_transit(body.transit)
        acct = V.clean_bank_account_ca(body.account_no)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not (inst and transit and acct):
        raise HTTPException(422, "Institution (3 digits), transit (5 digits) and account number are required")
    accounts = list(e.bank_accounts or [])
    row = {"id": str(uuid.uuid4()), "label": _clean_str(body.label, 60), "bank_name": _clean_str(body.bank_name, 80),
           "institution": inst, "transit": transit, "account_enc": encrypt_field(acct),
           "account_last4": acct[-4:], "account_type": _clean_str(body.account_type, 12),
           "is_primary": bool(body.is_primary) or not accounts, "added_by": _who(request), "added_at": _now().isoformat()}
    if row["is_primary"]:
        for a in accounts:
            a["is_primary"] = False
    accounts.append(row)
    e.bank_accounts = accounts
    e.updated_by = _who(request)
    await db.commit()
    LE.invalidate(org)
    await record_audit(request, "entity.bank_account.add", entity_type="legal_entity", entity_id=str(e.id),
                       summary=f"Bank account ••••{acct[-4:]} ({inst}-{transit}) added to {e.short_code}",
                       meta={"account_id": row["id"], "institution": inst, "transit": transit, "last4": acct[-4:]})
    return {"ok": True, "bank_accounts": LE.masked_bank_accounts(accounts)}


@router.delete("/{entity_id}/bank-accounts/{account_id}")
async def remove_bank_account(entity_id: str, account_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    accounts = [a for a in (e.bank_accounts or []) if a.get("id") != account_id]
    if len(accounts) == len(e.bank_accounts or []):
        raise HTTPException(404, "Bank account not found")
    if accounts and not any(a.get("is_primary") for a in accounts):
        accounts[0]["is_primary"] = True
    e.bank_accounts = accounts
    e.updated_by = _who(request)
    await db.commit()
    LE.invalidate(org)
    await record_audit(request, "entity.bank_account.remove", entity_type="legal_entity", entity_id=str(e.id),
                       summary=f"Bank account removed from {e.short_code}", meta={"account_id": account_id})
    return {"ok": True, "bank_accounts": LE.masked_bank_accounts(accounts)}


@router.get("/{entity_id}/bank-accounts/reveal")
async def reveal_bank_accounts(entity_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    out = []
    for a in e.bank_accounts or []:
        try:
            number = decrypt_field(a.get("account_enc"))
        except FieldDecryptError:
            number = None
        out.append({**{k: v for k, v in a.items() if k != "account_enc"}, "account_no": number})
    await record_audit(request, "entity.bank_account.reveal", entity_type="legal_entity", entity_id=str(e.id),
                       summary=f"Full bank account numbers of {e.short_code} revealed", meta={"count": len(out)})
    return {"bank_accounts": out}


# ── letterhead logo ─────────────────────────────────────────────────────────
_LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpeg"}       # sniffed from the bytes; SVG refused
LOGO_MAX = 300 * 1024


@router.post("/{entity_id}/letterhead")
async def upload_letterhead(entity_id: str, request: Request, file: UploadFile = File(...),
                            db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    data = await file.read(LOGO_MAX + 1)
    declared = (file.content_type or "").lower()
    if len(data) > LOGO_MAX:
        raise HTTPException(400, "Logo must be 300 KB or smaller")
    # The BYTES decide the type (PNG / JPEG only — the letter PDFs embed it; SVG is refused).
    ctype = sniff_image(data)
    if ctype not in _LOGO_TYPES:
        raise HTTPException(400, "Upload a PNG or JPEG logo (SVG and other formats are not accepted)")
    if declared and declared not in ("application/octet-stream",) and \
            (declared if declared != "image/jpg" else "image/jpeg") != ctype:
        raise HTTPException(400, "The file is not the image type it claims to be. Upload a real PNG or JPEG file.")
    lh = dict(e.letterhead or {})
    lh["logo_data_url"] = f"data:{ctype};base64," + base64.b64encode(data).decode()
    lh["logo_filename"] = (file.filename or "logo")[:120]
    e.letterhead = lh
    e.updated_by = _who(request)
    await db.commit()
    LE.invalidate(org)
    await record_audit(request, "entity.letterhead", entity_type="legal_entity", entity_id=str(e.id),
                       summary=f"Letterhead logo uploaded for {e.short_code}", meta={"bytes": len(data), "type": ctype})
    return {"ok": True, "has_logo": True, "bytes": len(data)}


@router.get("/{entity_id}/letterhead/logo")
async def letterhead_logo(entity_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The logo itself (data URL) — any signed-in user of the org (it goes on letters)."""
    org = _org(request)
    e = await _get(db, org, entity_id)
    url = (e.letterhead or {}).get("logo_data_url")
    if url and not str(url).startswith(("data:image/png;", "data:image/jpeg;")):
        url = None                     # an old SVG letterhead is no longer served — re-upload a PNG/JPEG
    return {"logo_data_url": url,
            "header_text": (e.letterhead or {}).get("header_text"), "footer_text": (e.letterhead or {}).get("footer_text")}


@router.delete("/{entity_id}/letterhead")
async def remove_letterhead(entity_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _require_write(request)
    e = await _get(db, org, entity_id)
    lh = {k: v for k, v in (e.letterhead or {}).items() if k not in ("logo_data_url", "logo_filename")}
    e.letterhead = lh
    await db.commit()
    LE.invalidate(org)
    return {"ok": True, "has_logo": False}


# ── boot ensure ─────────────────────────────────────────────────────────────
async def ensure_org_default(session, org: uuid.UUID) -> bool:
    """The boot ensure only covers orgs that existed at boot: an org registered afterwards
    (register-org / signup) has no entity until this runs. Cheap when it has one already."""
    n = (await session.execute(select(func.count(LegalEntity.id)).where(LegalEntity.org_id == org))).scalar() or 0
    if n:
        return False
    created = await ensure_default_entities(session, org_id=org)
    if created:
        LE.invalidate(org)
    return bool(created)


async def ensure_default_entities(session, org_id: uuid.UUID | None = None) -> int:
    """Create the DEFAULT entity of every org (or just ``org_id``) that has none, from the org's
    own details. Idempotent; returns how many were created. Works on Postgres and SQLite (tests)."""
    q = select(Organization)
    if org_id is not None:
        q = q.where(Organization.id == org_id)
    orgs = (await session.execute(q)).scalars().all()
    have = {oid for (oid,) in (await session.execute(select(LegalEntity.org_id).distinct())).all()}
    n = 0
    taken_codes: set[str] = set()
    for o in orgs:
        if o.id in have:
            continue
        code = (o.code or _derive_code(o.name))[:12].upper()
        while code in taken_codes:
            code = (code[:10] + str(n))[:12]
        taken_codes.add(code)
        bn = _digits(getattr(o, "cra_business_number", None))
        session.add(LegalEntity(org_id=o.id, name=(o.legal_name or o.name or "Company")[:200], short_code=code,
                                is_default=True, is_active=True, entity_type="corporation", country="CA",
                                province=_upper(o.province), business_number=bn if bn and BN_RE.match(bn) else None,
                                updated_by="system:default-entity"))
        n += 1
    if n:
        await session.commit()
    return n


async def init_entities() -> dict:
    """Boot: columns an older table lacks + indexes + default entity per org."""
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text(LE.CREATE_SQL))
            for col, ddl in LE.COLUMNS:
                await conn.execute(text(f"ALTER TABLE legal_entities ADD COLUMN IF NOT EXISTS {col} {ddl}"))
            for ddl in LE.INDEX_SQL:
                await conn.execute(text(ddl))
    async with SessionLocal() as s:
        created = await ensure_default_entities(s)
    if created:
        print(f"[auth] legal entities: default entity created for {created} organization(s)", flush=True)
    return {"default_entities_created": created}
