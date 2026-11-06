"""Lexora HRM — legal entities (group companies under ONE org account), stream D4.

The auth service OWNS the `legal_entities` table (ORM in services/auth/entities_models.py,
CRUD in entities_routes.py). Every other service reads it through this module — the same
pattern as shared.utils.identity for `organizations`: one shared Postgres, row-level org_id.

    from shared.legal_entities import list_entities, entity_by_id, default_entity, request_entity_id

    ents = await list_entities(org_id)                 # active entities, default first
    ent  = await entity_by_id(org_id, entity_id)       # None when unknown / other org
    d    = await default_entity(org_id)                # the org's default entity (None before boot ran)
    eid  = request_entity_id(request)                  # ?entity_id= or the X-View-Entity header ("" = all)

Canadian registration numbers per entity: the CRA Business Number (BN, 9 digits), the payroll
program account (BN + "RP" + 4 digits, e.g. 123456789RP0001) used on PD7A remittances, T4 and
ROE, and — for Québec employers — the Revenu Québec identification number used on RL-1.

Every reader is tolerant: a missing table (an older database the auth service has not booted on
yet) or any DB error returns an empty result, so a single-entity tenant keeps working exactly as
before. Bank account numbers are stored ENCRYPTED (shared.crypto_fields) and are never returned
by these helpers in full — see `masked_bank_accounts()`.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

_md = sa.MetaData()

legal_entities = sa.Table(
    "legal_entities", _md,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("org_id", sa.Uuid, index=True, nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("short_code", sa.String(12), nullable=False),
    sa.Column("is_default", sa.Boolean, default=False),
    sa.Column("is_active", sa.Boolean, default=True),
    sa.Column("entity_type", sa.String(30)),
    sa.Column("business_number", sa.String(9)),
    sa.Column("payroll_account", sa.String(15)),
    sa.Column("rq_identification", sa.String(16)),
    sa.Column("address_line1", sa.String(200)),
    sa.Column("address_line2", sa.String(200)),
    sa.Column("city", sa.String(100)),
    sa.Column("province", sa.String(2)),
    sa.Column("postal_code", sa.String(7)),
    sa.Column("country", sa.String(2)),
    sa.Column("email", sa.String(200)),
    sa.Column("phone", sa.String(20)),
    sa.Column("website", sa.String(200)),
    sa.Column("wcb_accounts", JSONB),
    sa.Column("licences", JSONB),
    sa.Column("bank_accounts", JSONB),
    sa.Column("signatory", JSONB),
    sa.Column("letterhead", JSONB),
    sa.Column("numbering_prefix", sa.String(12)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
    sa.Column("updated_by", sa.String(160)),
)

# Columns every reader may rely on (the auth boot adds any that are missing).
COLUMNS: list[tuple[str, str]] = [
    ("is_default", "BOOLEAN DEFAULT FALSE"), ("is_active", "BOOLEAN DEFAULT TRUE"), ("entity_type", "VARCHAR(30)"),
    ("business_number", "VARCHAR(9)"), ("payroll_account", "VARCHAR(15)"), ("rq_identification", "VARCHAR(16)"),
    ("address_line1", "VARCHAR(200)"), ("address_line2", "VARCHAR(200)"), ("city", "VARCHAR(100)"),
    ("province", "VARCHAR(2)"), ("postal_code", "VARCHAR(7)"), ("country", "VARCHAR(2)"), ("email", "VARCHAR(200)"),
    ("phone", "VARCHAR(20)"), ("website", "VARCHAR(200)"), ("wcb_accounts", "JSONB"),
    ("licences", "JSONB"), ("bank_accounts", "JSONB"), ("signatory", "JSONB"), ("letterhead", "JSONB"),
    ("numbering_prefix", "VARCHAR(12)"), ("created_at", "TIMESTAMPTZ"), ("updated_at", "TIMESTAMPTZ"),
    ("updated_by", "VARCHAR(160)"),
]

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS legal_entities (
    id UUID PRIMARY KEY,
    org_id UUID NOT NULL,
    name VARCHAR(200) NOT NULL,
    short_code VARCHAR(12) NOT NULL
)"""
INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_legal_entities_org_id ON legal_entities (org_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS legal_entities_org_code_uk ON legal_entities (org_id, upper(short_code))",
    "CREATE UNIQUE INDEX IF NOT EXISTS legal_entities_org_default_uk ON legal_entities (org_id) WHERE is_default",
]

ENTITY_TYPES = ("corporation", "federal_corporation", "provincial_corporation", "partnership",
                "sole_proprietorship", "non_profit", "charity", "cooperative", "trust", "branch", "other")

BN_RE = re.compile(r"^[0-9]{9}$")
PAYROLL_ACCOUNT_RE = re.compile(r"^[0-9]{9}RP[0-9]{4}$")
POSTAL_CODE_RE = re.compile(r"^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z] ?[0-9][ABCEGHJ-NPRSTV-Z][0-9]$")

ALL = ""          # the "all entities" view

_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_SECONDS = 20


def _sm():
    from shared.utils.db import make_engine, make_sessionmaker
    global _engine, _session
    try:
        return _session
    except NameError:
        _engine = make_engine()
        _session = make_sessionmaker(_engine)
        return _session


def _uuid(v) -> uuid.UUID | None:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


def normalise_postal_code(v: str | None) -> str | None:
    """'k1a0b1' → 'K1A 0B1'. None / blank stays None. Raises ValueError when it is not a
    Canadian postal code (letter-digit-letter digit-letter-digit; no D F I O Q U; no W / Z first)."""
    if v is None or not str(v).strip():
        return None
    s = re.sub(r"\s+", "", str(v)).upper()
    if len(s) != 6 or not POSTAL_CODE_RE.match(s[:3] + " " + s[3:]):
        raise ValueError("Postal code must look like K1A 0B1")
    return s[:3] + " " + s[3:]


def row_dict(r: Any) -> dict:
    """Public shape of one entity row (mapping or ORM object): bank accounts MASKED."""
    g = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k, None))
    return {
        "id": str(g("id")), "org_id": str(g("org_id")), "name": g("name"), "short_code": g("short_code"),
        "is_default": bool(g("is_default")), "is_active": g("is_active") is not False,
        "entity_type": g("entity_type") or "corporation",
        "business_number": g("business_number"), "payroll_account": g("payroll_account"),
        "rq_identification": g("rq_identification"),
        "address_line1": g("address_line1"), "address_line2": g("address_line2"), "city": g("city"),
        "province": g("province"), "postal_code": g("postal_code"), "country": g("country") or "CA",
        "email": g("email"), "phone": g("phone"), "website": g("website"),
        "wcb_accounts": dict(g("wcb_accounts") or {}),
        "licences": list(g("licences") or []), "bank_accounts": masked_bank_accounts(g("bank_accounts")),
        "signatory": dict(g("signatory") or {}), "letterhead": dict(g("letterhead") or {}),
        "numbering_prefix": g("numbering_prefix"),
        "created_at": g("created_at").isoformat() if g("created_at") else None,
        "updated_at": g("updated_at").isoformat() if g("updated_at") else None, "updated_by": g("updated_by"),
    }


def masked_bank_accounts(accounts) -> list[dict]:
    out = []
    for a in accounts or []:
        a = dict(a or {})
        a.pop("account_enc", None)
        last4 = a.get("account_last4") or ""
        a["account_masked"] = ("•••• " + last4) if last4 else None
        out.append(a)
    return out


def primary_bank_account(accounts) -> dict | None:
    """The entity's payroll-disbursement account WITH the full number (decrypted) — for a
    direct-deposit file only. None when none is set or the token cannot be read."""
    accts = list(accounts or [])
    if not accts:
        return None
    a = next((x for x in accts if x.get("is_primary")), accts[0])
    from shared.crypto_fields import FieldDecryptError, decrypt_field
    try:
        number = decrypt_field(a.get("account_enc"))
    except FieldDecryptError:
        number = None
    return {**{k: v for k, v in a.items() if k != "account_enc"}, "account_no": number}


async def _fetch(org_id) -> list[dict]:
    oid = _uuid(org_id)
    if oid is None:
        return []
    try:
        async with _sm()() as s:
            rows = (await s.execute(sa.select(legal_entities).where(legal_entities.c.org_id == oid)
                                    .order_by(legal_entities.c.is_default.desc(), legal_entities.c.name))).mappings().all()
    except Exception:  # noqa: BLE001 — table not there yet / DB blip → single-entity behaviour
        return []
    return [dict(r) for r in rows]


async def list_entities(org_id, include_inactive: bool = False, fresh: bool = False) -> list[dict]:
    """Entities of an org (raw rows incl. encrypted bank tokens — call row_dict() before returning
    to a client). Cached per process for CACHE_SECONDS; `fresh=True` bypasses the cache."""
    key = str(org_id)
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        rows = hit[1]
    else:
        rows = await _fetch(org_id)
        _cache[key] = (time.monotonic(), rows)
    return [r for r in rows if include_inactive or r.get("is_active") is not False]


def invalidate(org_id) -> None:
    _cache.pop(str(org_id), None)


async def entity_by_id(org_id, entity_id) -> dict | None:
    eid = _uuid(entity_id)
    if eid is None:
        return None
    for r in await list_entities(org_id, include_inactive=True):
        if r["id"] == eid:
            return r
    # a miss may be an entity created in the auth service seconds ago (this process's cache
    # is up to CACHE_SECONDS old) → read once more before saying "not found"
    for r in await list_entities(org_id, include_inactive=True, fresh=True):
        if r["id"] == eid:
            return r
    return None


async def default_entity(org_id) -> dict | None:
    rows = await list_entities(org_id)
    return next((r for r in rows if r.get("is_default")), rows[0] if rows else None)


async def entity_map(org_id, fresh: bool = False) -> dict[str, dict]:
    """{entity_id: row} for an org (active + inactive, so old pay runs still resolve a name)."""
    return {str(r["id"]): r for r in await list_entities(org_id, include_inactive=True, fresh=fresh)}


def request_entity_id(request) -> str:
    """The entity VIEW the caller asked for: `?entity_id=` first, then the `X-View-Entity`
    header the frontend's entity picker sends. "" / "all" → every entity. An invalid value is
    treated as "all" rather than failing the request (it is a view filter, not a permission)."""
    v = ""
    try:
        v = (request.query_params.get("entity_id") or request.headers.get("x-view-entity") or "").strip()
    except Exception:  # noqa: BLE001
        return ALL
    if v.lower() in ("", "all", "*"):
        return ALL
    return str(_uuid(v) or ALL)


def entity_label(row: dict | None) -> str:
    if not row:
        return ""
    return row.get("name") or row.get("short_code") or ""


def registration_gaps(entity: dict) -> list[str]:
    """Which registration details an entity still lacks (plain words for the settings screen)."""
    gaps = []
    if not entity.get("business_number"):
        gaps.append("CRA Business Number")
    if not entity.get("payroll_account"):
        gaps.append("CRA payroll account (RP)")
    if (entity.get("province") or "").upper() == "QC" and not entity.get("rq_identification"):
        gaps.append("Revenu Québec identification number")
    if not (entity.get("signatory") or {}).get("name"):
        gaps.append("authorised signatory")
    if not entity.get("bank_accounts"):
        gaps.append("payroll bank account")
    return gaps
