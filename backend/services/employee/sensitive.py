"""Employee service — sensitive-identifier field map (encryption + masking).

One place that knows, for each protected identifier, which column holds the
legacy plaintext, which holds the AES-GCM token, which holds the blind index
(for uniqueness) and which holds the display mask.

Reads PREFER the encrypted column and fall back to legacy plaintext, so the
service keeps working on rows the boot backfill has not reached yet and on a
database that was never migrated. Writes go to the encrypted column (+ hash +
mask) and, until the verified cutover, ALSO to the legacy plaintext column so
an older build can still read the row (that is the rollback path).
"""
import sys

sys.path.append("/app")
from shared.utils import crypto  # noqa: E402

# field → (plain_col, enc_col, hash_col, mask_col, mask_kind, aad_scope)
FIELDS: dict[str, dict] = {
    "bank_account":      dict(plain="bank_account_encrypted", enc="bank_account_enc", hash=None, mask="bank_account_last4", kind="generic"),
    "sin":               dict(plain="sin_encrypted", enc="sin_enc", hash=None, mask=None, kind="sin"),
    # S4: passport — encrypted from the start (no legacy plaintext column).
    "passport":          dict(plain=None, enc="passport_enc", hash=None, mask="passport_masked", kind="generic"),
}


def clean_passport(value) -> str | None:
    """Shape check only (upper-case letters/digits, 6–15 long). Canadian passports
    are two letters + 6 digits, but a foreign national's may differ — so the format
    is not narrowed to Canada's."""
    import re
    v = re.sub(r"[\s\-]", "", str(value or "")).upper()
    if not v:
        return None
    if not re.fullmatch(r"[A-Z0-9]{6,15}", v):
        raise ValueError("Passport number should be 6–15 letters/digits")
    return v

# Roles that may see a full (unmasked) identifier — every such read is audited.
REVEAL_ROLES = {"payroll_admin", "org_admin", "superadmin"}

# Flipped at boot from the data_protection_state table: once the cutover has
# been verified we stop dual-writing plaintext.
CUTOVER_DONE = False


def _mask_value(spec: dict, value: str | None) -> str | None:
    if value is None:
        return None
    if spec["mask"] == "bank_account_last4":
        return crypto.last_n(value, 4)
    return crypto.mask(value, spec["kind"])


def set_field(e, field: str, value: str | None) -> None:
    """Write one protected identifier onto an Employee ORM object."""
    spec = FIELDS[field]
    value = (str(value).strip() if value is not None else None) or None
    setattr(e, spec["enc"], crypto.encrypt(value))
    if spec["hash"]:
        setattr(e, spec["hash"], crypto.blind_index(value, scope=field))
    if spec["mask"]:
        setattr(e, spec["mask"], _mask_value(spec, value))
    if spec["plain"]:
        # Dual-write until the verified cutover; afterwards plaintext stays NULL.
        setattr(e, spec["plain"], None if CUTOVER_DONE else value)
    e.enc_key_id = crypto.status()["key_id"]


def get_field(e, field: str) -> str | None:
    """Full plaintext value — encrypted column first, legacy plaintext second."""
    spec = FIELDS[field]
    token = getattr(e, spec["enc"], None)
    if token:
        try:
            return crypto.decrypt(token)
        except crypto.CryptoError:
            pass            # wrong key ring → fall through to legacy if present
    if spec["plain"]:
        legacy = getattr(e, spec["plain"], None)
        if legacy and not crypto.is_encrypted(legacy):
            return legacy
    return None


def masked(e, field: str) -> str | None:
    """Display mask — stored mask column first, else derived from the value."""
    spec = FIELDS[field]
    if spec["mask"]:
        m = getattr(e, spec["mask"], None)
        if m:
            return f"XXXX{m}" if field == "bank_account" else m
    v = get_field(e, field)
    return crypto.mask(v, spec["kind"]) if v else None


def has_value(e, field: str) -> bool:
    spec = FIELDS[field]
    return bool(getattr(e, spec["enc"], None) or (spec["plain"] and getattr(e, spec["plain"], None)))


def anonymise(e) -> list[str]:
    """Personal-information erasure: blank the personal / contact fields that have no
    statutory retention need. Payroll records (T4 / ROE) must stay reconcilable for the
    CRA retention period, so pay history is kept; the retention purge removes it when
    that period lapses."""
    cleared = []
    for col in ("email", "work_email", "phone", "home_phone", "address_line1", "address_line2",
                "city", "postal_code", "emergency_name", "emergency_relationship",
                "emergency_phone", "preferred_name", "pronouns", "date_of_birth",
                "nominee_name", "nominee_relationship", "nominee_phone", "marital_status",
                "work_permit_number"):
        if getattr(e, col, None) not in (None, ""):
            setattr(e, col, None)
            cleared.append(col)
    for field in ("bank_account", "sin", "passport"):
        if has_value(e, field):
            set_field(e, field, None)
            cleared.append(field)
    return cleared
