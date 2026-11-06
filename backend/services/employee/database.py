"""Employee service — DB wiring + the column-encryption migration.

Migration policy (safe on a live database):
  1. ADD COLUMN IF NOT EXISTS for every new column (never drops anything).
  2. Backfill: every row whose legacy plaintext is set but whose `*_enc` token is
     empty gets encrypted + hashed + masked. Idempotent — re-running finds nothing.
     Plaintext is NOT touched, so an older build still reads the row.
  3. Cutover (explicit, env LEXORA_ENCRYPTION_CUTOVER=1): every row is VERIFIED —
     decrypt(token) must equal the plaintext — and only then are the plaintext
     columns set to NULL and the state row stamped. If one row fails, nothing
     is nulled and the boot log says which row.
  4. Rollback (env LEXORA_ENCRYPTION_ROLLBACK=1): decrypt every token back into
     the legacy plaintext columns and clear the cutover stamp. Needs the key.
  5. Rotation (env LEXORA_ENCRYPTION_ROTATE=1): re-encrypt every token under the
     current LEXORA_DATA_KEY (old keys must be in LEXORA_DATA_KEYS).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.append("/app")
from sqlalchemy import text  # noqa: E402

from shared.utils import crypto  # noqa: E402
from shared.utils.db import Base, make_engine, make_sessionmaker  # noqa: E402

engine = make_engine()
SessionLocal = make_sessionmaker(engine)


async def get_db():
    async with SessionLocal() as session:
        yield session


# Columns added after v0.1 — `create_all` won't ALTER an existing table, so we
# add them idempotently on startup (Postgres ADD COLUMN IF NOT EXISTS).
_NEW_EMPLOYEE_COLUMNS = [
    ("gender", "VARCHAR(20)"), ("phone", "VARCHAR(30)"),
    ("address_line1", "VARCHAR(200)"), ("address_line2", "VARCHAR(200)"),
    ("city", "VARCHAR(100)"), ("postal_code", "VARCHAR(10)"),
    ("job_title", "VARCHAR(120)"), ("department", "VARCHAR(120)"),
    ("work_location", "VARCHAR(120)"), ("manager_name", "VARCHAR(200)"),
    ("preferred_name", "VARCHAR(100)"), ("pronouns", "VARCHAR(30)"),
    ("work_eligibility", "VARCHAR(40)"), ("work_permit_expiry", "DATE"),
    ("emergency_name", "VARCHAR(200)"), ("emergency_relationship", "VARCHAR(60)"),
    ("emergency_phone", "VARCHAR(30)"),
    # Extended intake (onboarding spec)
    ("bank_account_encrypted", "VARCHAR"),
    ("middle_name", "VARCHAR(100)"), ("marital_status", "VARCHAR(20)"),
    ("work_email", "VARCHAR(200)"), ("home_phone", "VARCHAR(30)"),
    ("province_of_residence", "VARCHAR(2)"), ("address_country", "VARCHAR(2) DEFAULT 'CA'"),
    ("work_permit_type", "VARCHAR(40)"), ("work_permit_number", "VARCHAR(40)"),
    ("probation_end_date", "DATE"), ("fte_percent", "NUMERIC(5,2)"),
    ("overtime_eligible", "BOOLEAN DEFAULT TRUE"), ("vacation_percent", "NUMERIC(5,2) DEFAULT 4"),
    ("division", "VARCHAR(120)"), ("cost_center", "VARCHAR(60)"),
    ("branch_id", "UUID"), ("branch_name", "VARCHAR(180)"),
    ("entity_id", "UUID"),                                   # D4 multi-entity (legal entity)
    ("is_union", "BOOLEAN DEFAULT FALSE"), ("union_local", "VARCHAR(60)"),
    ("cpp_exempt", "BOOLEAN DEFAULT FALSE"), ("ei_exempt", "BOOLEAN DEFAULT FALSE"),
    ("td1_claim_lines", "JSONB"), ("td1_prov_claim_lines", "JSONB"),
    # Column encryption — tokens, masks
    ("sin_enc", "VARCHAR"), ("bank_account_enc", "VARCHAR"), ("enc_key_id", "VARCHAR(20)"),
    # Privacy (nominee, erasure)
    ("nominee_name", "VARCHAR(200)"), ("nominee_relationship", "VARCHAR(60)"),
    ("nominee_phone", "VARCHAR(30)"), ("erasure_hold", "BOOLEAN DEFAULT FALSE"),
    ("erased_at", "TIMESTAMPTZ"),
    # Identity (logins / onboarding work)
    ("father_name", "VARCHAR(200)"), ("spouse_name", "VARCHAR(200)"), ("grade", "VARCHAR(60)"),
    # S4: passport (encrypted only)
    ("passport_enc", "VARCHAR"), ("passport_masked", "VARCHAR(40)"), ("passport_expiry", "DATE"),
    # 2026-10-22 fixed-term / contract employees (current term)
    ("contract_start_date", "DATE"), ("contract_end_date", "DATE"), ("contract_notice_days", "INTEGER"), ("contract_ref", "VARCHAR(80)"),
]

# An employee number is unique PER ORG (never globally — two tenants may both
# have EMP-0001). Created in its own transaction: if an org already holds
# duplicates the index is skipped and reported, the service still boots, and
# the create route's own check keeps new numbers unique.
_UNIQUE_PER_ORG = (
    "employees_org_number_uk",
    "CREATE UNIQUE INDEX IF NOT EXISTS employees_org_number_uk "
    "ON employees (org_id, upper(employee_number)) WHERE is_active",
)

_NEW_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_employees_entity_id ON employees (entity_id)",
]

# New columns on EXISTING master tables (create_all makes new tables but never
# alters an existing one). (table, column, ddl) — idempotent ADD COLUMN IF NOT EXISTS.
_MASTER_COLUMN_ALTERS = [
    ("master_departments", "cost_center", "VARCHAR(30)"),
    ("master_departments", "gl_code", "VARCHAR(30)"),
    ("master_designations", "job_family", "VARCHAR(60)"),
    ("master_designations", "overtime_exempt", "BOOLEAN DEFAULT FALSE"),
    ("master_locations", "address", "VARCHAR(300)"),
    ("master_locations", "postal_code", "VARCHAR(10)"),
    ("master_locations", "timezone", "VARCHAR(40)"),
    ("master_locations", "is_remote", "BOOLEAN DEFAULT FALSE"),
    ("master_pay_components", "non_periodic", "BOOLEAN DEFAULT FALSE"),
    # 2026-10-16 employee portal (migration 0006)
    ("master_leave_types", "category", "VARCHAR(20)"),
    # batch 2 (migration 0007)
    ("master_leave_types", "attachment_after_days", "INTEGER"),
    ("master_stat_holidays", "is_optional", "BOOLEAN DEFAULT FALSE"),
]

# A letter may be issued to a CANDIDATE before they are an employee (offer letter):
# employee_id becomes nullable (a relaxation — no data changes) and the recipient is
# recorded. Idempotent.
_LETTER_RECIPIENT_ALTERS = [
    "ALTER TABLE issued_letters ALTER COLUMN employee_id DROP NOT NULL",
    "ALTER TABLE issued_letters ADD COLUMN IF NOT EXISTS recipient_kind VARCHAR(20) DEFAULT 'employee'",
    "ALTER TABLE issued_letters ADD COLUMN IF NOT EXISTS recipient_ref VARCHAR(64)",
    "ALTER TABLE issued_letters ADD COLUMN IF NOT EXISTS recipient_name VARCHAR(200)",
    "ALTER TABLE issued_letters ADD COLUMN IF NOT EXISTS recipient_email VARCHAR(200)",
    "CREATE INDEX IF NOT EXISTS ix_issued_letters_recipient_ref ON issued_letters (org_id, recipient_ref)",
]

# (plain_col, enc_col, hash_col, mask_col, mask_kind, scope)
_ENC_MAP = [
    ("sin_encrypted", "sin_enc", None, None, "sin", None),
    ("bank_account_encrypted", "bank_account_enc", None, "bank_account_last4", "last4", None),
]
_PLAIN_COLS = [m[0] for m in _ENC_MAP]
_ENC_COLS = [m[1] for m in _ENC_MAP]


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _now():
    return datetime.now(timezone.utc)


async def _fetch_rows(conn):
    cols = ", ".join(["id"] + _PLAIN_COLS + _ENC_COLS + ["passport_enc"])
    return (await conn.execute(text(f"SELECT {cols} FROM employees"))).mappings().all()


async def _backfill(conn) -> int:
    """Encrypt legacy plaintext into the token columns. Returns rows touched."""
    touched = 0
    for row in await _fetch_rows(conn):
        sets, params = [], {"id": row["id"]}
        for plain_col, enc_col, hash_col, mask_col, kind, scope in _ENC_MAP:
            plain = row[plain_col]
            if not plain or crypto.is_encrypted(plain) or row[enc_col]:
                continue
            params[enc_col] = crypto.encrypt(plain)
            sets.append(f"{enc_col} = :{enc_col}")
            if hash_col:
                params[hash_col] = crypto.blind_index(plain, scope=scope)
                sets.append(f"{hash_col} = :{hash_col}")
            if mask_col:
                params[mask_col] = crypto.last_n(plain, 4) if kind == "last4" else crypto.mask(plain, kind)
                sets.append(f"{mask_col} = :{mask_col}")
        if sets:
            params["kid"] = crypto.status()["key_id"]
            sets.append("enc_key_id = :kid")
            await conn.execute(text(f"UPDATE employees SET {', '.join(sets)} WHERE id = :id"), params)
            touched += 1
    return touched


async def _verify(conn) -> list[str]:
    """Every token must decrypt to its plaintext (where plaintext still exists)."""
    problems = []
    for row in await _fetch_rows(conn):
        for plain_col, enc_col, *_ in _ENC_MAP:
            plain, token = row[plain_col], row[enc_col]
            if not plain or crypto.is_encrypted(plain):
                continue
            if not token:
                problems.append(f"{row['id']}: {plain_col} has no token")
                continue
            try:
                got = crypto.decrypt(token)
            except crypto.CryptoError as exc:
                problems.append(f"{row['id']}: {enc_col} {exc}")
                continue
            want = plain
            if got != want:
                problems.append(f"{row['id']}: {enc_col} does not match plaintext")
    return problems


async def _cutover(conn) -> tuple[bool, str]:
    problems = await _verify(conn)
    if problems:
        return False, f"cutover refused — {len(problems)} row(s) failed verification: {problems[:3]}"
    nulls = ", ".join(f"{c} = NULL" for c in _PLAIN_COLS)
    await conn.execute(text(f"UPDATE employees SET {nulls}"))
    await conn.execute(text(
        "UPDATE data_protection_state SET cutover_done_at = :t, cutover_key_id = :k, rolled_back_at = NULL, "
        "notes = 'plaintext columns nulled after verified cutover' WHERE id = 1"),
        {"t": _now(), "k": crypto.status()["key_id"]})
    return True, "cutover done — plaintext columns nulled"


async def _rollback(conn) -> int:
    """Decrypt tokens back into the legacy plaintext columns (reverses cutover)."""
    n = 0
    for row in await _fetch_rows(conn):
        sets, params = [], {"id": row["id"]}
        for plain_col, enc_col, *_ in _ENC_MAP:
            if row[enc_col] and not row[plain_col]:
                params[plain_col] = crypto.decrypt(row[enc_col])
                sets.append(f"{plain_col} = :{plain_col}")
        if sets:
            await conn.execute(text(f"UPDATE employees SET {', '.join(sets)} WHERE id = :id"), params)
            n += 1
    await conn.execute(text(
        "UPDATE data_protection_state SET cutover_done_at = NULL, rolled_back_at = :t, "
        "notes = 'rolled back: plaintext restored from tokens' WHERE id = 1"), {"t": _now()})
    return n


async def _rotate(conn) -> int:
    n = 0
    cur = crypto.status()["key_id"]
    for row in await _fetch_rows(conn):
        sets, params = [], {"id": row["id"]}
        for _, enc_col, *_ in _ENC_MAP + [(None, "passport_enc", None, None, None, None)]:
            tok = row.get(enc_col)
            if tok and crypto.key_id_of(tok) != cur:
                params[enc_col] = crypto.rotate(tok)
                sets.append(f"{enc_col} = :{enc_col}")
        if sets:
            params["kid"] = cur
            sets.append("enc_key_id = :kid")
            await conn.execute(text(f"UPDATE employees SET {', '.join(sets)} WHERE id = :id"), params)
            n += 1
    return n


async def run_encryption_migration(conn) -> dict:
    """Backfill (always) + optional cutover / rollback / rotate. Returns a summary."""
    import sensitive
    await conn.execute(text(
        "INSERT INTO data_protection_state (id, backfilled_rows) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"))
    summary: dict = {"key": crypto.status()}
    if _flag("LEXORA_ENCRYPTION_ROLLBACK"):
        summary["rollback_rows"] = await _rollback(conn)
    touched = await _backfill(conn)
    summary["backfilled_rows"] = touched
    await conn.execute(text(
        "UPDATE data_protection_state SET backfilled_at = COALESCE(backfilled_at, :t), "
        "backfilled_rows = backfilled_rows + :n, last_boot_at = :t WHERE id = 1"),
        {"t": _now(), "n": touched})
    if _flag("LEXORA_ENCRYPTION_ROTATE"):
        summary["rotated_rows"] = await _rotate(conn)
    if _flag("LEXORA_ENCRYPTION_CUTOVER"):
        ok, msg = await _cutover(conn)
        summary["cutover"] = msg
    state = (await conn.execute(text("SELECT cutover_done_at FROM data_protection_state WHERE id = 1"))).first()
    sensitive.CUTOVER_DONE = bool(state and state[0])
    summary["cutover_done"] = sensitive.CUTOVER_DONE
    print(f"[employee] encryption migration: {summary}", flush=True)
    return summary


async def init_db():
    import models  # noqa: F401  (registers employees + data_protection_state)
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '15s'"))   # boot DDL never waits forever on a lock
        await conn.run_sync(Base.metadata.create_all)
        for col, ddl in _NEW_EMPLOYEE_COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE employees ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        for ddl in _NEW_INDEXES:
            await conn.execute(text(ddl))
        for tbl, col, ddl in _MASTER_COLUMN_ALTERS:
            await conn.execute(text(
                f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {ddl}"))
        for ddl in _LETTER_RECIPIENT_ALTERS:
            await conn.execute(text(ddl))
        await run_encryption_migration(conn)
    name, ddl = _UNIQUE_PER_ORG
    try:
        async with engine.begin() as conn:
            await conn.execute(text(ddl))
    except Exception as exc:
        print(f"[employee] could not create {name}: {exc.__class__.__name__} — "
              f"an organization has duplicate employee numbers; fix them and restart", flush=True)
