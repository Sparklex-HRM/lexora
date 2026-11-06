"""Lexora HRM — field-level encryption helpers for OTHER services (stream S4).

The employee service stores sensitive identifiers encrypted (AES-256-GCM,
shared.utils.crypto, key id inside every token for rotation). Anything that
needs the FULL value — T4 / RL-1 slips, the ROE, a direct-deposit file — must
read the token column through
`decrypt_field()` (or `employee_identifier()` for a whole row), never the legacy
plaintext column: after the verified cutover (LEXORA_ENCRYPTION_CUTOVER=1) the
plaintext columns are NULL.

    from shared.crypto_fields import decrypt_field, employee_identifier, IDENTIFIER_SELECT
    rows = await db.execute(text(f"SELECT id, {IDENTIFIER_SELECT} FROM employees WHERE org_id = :o"), …)
    sin = employee_identifier(row, "sin")          # full SIN (9 digits)
    acct = employee_identifier(row, "bank_account")

Every function is pure (no DB, no request); decrypting does NOT audit — the
caller's own export / filing action is what gets audited.
"""
from __future__ import annotations

from typing import Any, Mapping

from shared.utils import crypto

# field → (token column, legacy plaintext column or None, mask kind)
COLUMNS: dict[str, tuple[str, str | None, str]] = {
    "bank_account":      ("bank_account_enc", "bank_account_encrypted", "generic"),
    "passport":          ("passport_enc", None, "generic"),
    "sin":               ("sin_enc", "sin_encrypted", "sin"),
}
FIELDS = tuple(COLUMNS)

# Columns to SELECT so employee_identifier() can answer for every field.
IDENTIFIER_SELECT = ", ".join(sorted({c for enc, plain, _ in COLUMNS.values() for c in (enc, plain) if c}))


class FieldDecryptError(ValueError):
    """A token could not be decrypted (wrong / missing key in the ring)."""


def decrypt_field(value: str | None) -> str | None:
    """Plaintext of one stored value. A token (``enc:v1:…``) is decrypted with
    the key ring (LEXORA_DATA_KEY + LEXORA_DATA_KEYS); a legacy plaintext value is
    returned unchanged; None / "" → None. Raises FieldDecryptError on a bad token
    — never returns ciphertext as if it were the value."""
    if value is None or value == "":
        return None
    try:
        return crypto.decrypt(value)
    except crypto.CryptoError as exc:
        raise FieldDecryptError(str(exc)) from exc


def encrypt_field(value: str | None) -> str | None:
    """Token for a value (None / "" → None). Idempotent on an existing token."""
    return crypto.encrypt(value)


def mask_field(value: str | None, field: str) -> str | None:
    """Display mask of a PLAINTEXT value (decrypt first if you hold a token)."""
    kind = COLUMNS.get(field, (None, None, "generic"))[2]
    return crypto.mask(value, kind)


def _get(row: Any, col: str):
    if isinstance(row, Mapping):
        return row.get(col)
    return getattr(row, col, None)


def employee_identifier(row: Any, field: str) -> str | None:
    """Full value of `field` from an employees row (mapping or ORM object): the
    token column first, the legacy plaintext column second (rows not yet
    backfilled). A passport number is upper-cased."""
    enc, plain, _ = COLUMNS[field]
    v = decrypt_field(_get(row, enc))
    if v is None and plain:
        legacy = _get(row, plain)
        if legacy and not crypto.is_encrypted(legacy):
            v = legacy
        elif legacy:
            v = decrypt_field(legacy)
    if v and field == "passport":
        v = v.upper()
    return v


def key_status() -> dict:
    """Key ring state (algorithm, current key id, dev fallback?) — no key material."""
    return crypto.status()
