"""Onboarding — pure rules for the joiner's form (no database, no HTTP).

  * which fields the NEW HIRE may fill (never pay, title, manager, number …)
  * Canadian format checks (SIN Luhn, bank institution / transit / account, postal code —
    shared/utils/id_validators.py)
  * identity / bank numbers are encrypted before they are stored in the
    session's JSON, and only ever shown back masked
  * what must be present before the joiner can submit
"""
import sys

sys.path.append("/app")
from shared.utils import crypto, id_validators  # noqa: E402
from shared.utils.enums import PROVINCES_ALL  # noqa: E402

SENSITIVE = ("sin", "bank_account", "work_permit_number")
_MASK_KIND = {"sin": "sin"}

# What the joiner may fill on the public page, by section.
HIRE_COMMON = {
    "middle_name", "preferred_name", "date_of_birth", "gender", "marital_status", "pronouns",
    "father_name", "spouse_name",
    "phone", "home_phone", "email", "address_line1", "address_line2", "city", "postal_code",
    "province_of_residence",
    "emergency_name", "emergency_relationship", "emergency_phone",
    "nominee_name", "nominee_relationship", "nominee_phone",
    "bank_account",
    "sin", "bank_institution", "bank_transit", "td1_federal_claim", "td1_provincial_claim",
    "additional_tax_per_period", "work_eligibility", "work_permit_type", "work_permit_number",
    "work_permit_expiry",
}
# HR may also set the job side of the record.
HR_EXTRA = {
    "first_name", "last_name", "work_email", "employee_number", "job_title", "department", "work_location",
    "employment_type", "province_of_employment", "hire_date", "manager_id", "manager_name", "grade",
    "probation_end_date", "pay_type", "annual_salary", "hourly_rate", "pay_frequency",
    "standard_hours_per_week", "overtime_eligible", "vacation_percent", "fte_percent", "division",
    "cost_center", "is_union", "union_local", "cpp_exempt", "ei_exempt", "branch_id", "branch_name",
    "equity_self_id", "no_tax_flag",
}

# Needed before the joiner can press Submit (the employer needs the SIN within three days of
# the start date and a direct-deposit account to pay).
REQUIRED = [("date_of_birth", "Date of birth"), ("phone", "Mobile number"), ("address_line1", "Address"),
            ("city", "City"), ("postal_code", "Postal code"), ("sin", "SIN"),
            ("emergency_name", "Emergency contact name"), ("emergency_phone", "Emergency contact phone"),
            ("bank_institution", "Institution number"), ("bank_transit", "Transit number"),
            ("bank_account", "Account number")]


def allowed_fields(country: str | None = None, hr: bool = False) -> set[str]:
    fields = set(HIRE_COMMON)
    if hr:
        fields |= HR_EXTRA
    return fields


def valid_region(country: str | None, region: str | None) -> bool:
    return bool(region) and str(region).upper() in PROVINCES_ALL


def clean_patch(country: str | None, patch: dict, hr: bool = False) -> tuple[dict, dict, list[str]]:
    """(values to merge into the session data, errors by field, ignored field names).

    Unknown / not-allowed fields are dropped (a joiner cannot set their own pay).
    Identity and bank numbers are shape-checked, then ENCRYPTED; a blank value
    for one of them means "leave what is on file" and is skipped."""
    allowed = allowed_fields(country, hr)
    ignored = sorted(k for k in patch if k not in allowed)
    patch = {k: v for k, v in patch.items() if k in allowed}
    for k in SENSITIVE:
        if k in patch and (patch[k] is None or str(patch[k]).strip() == ""):
            patch.pop(k)
    cleaned, errors = id_validators.clean_identity_fields("CA", patch)
    if patch.get("postal_code"):
        try:
            cleaned["postal_code"] = id_validators.clean_postal_code(patch["postal_code"])
        except ValueError as exc:
            errors["postal_code"] = str(exc)
    out = {**patch, **cleaned}
    for k in SENSITIVE:
        if out.get(k):
            out[k] = crypto.encrypt(str(out[k]))
    return out, errors, ignored


def _plain(value):
    """Full value via shared.crypto_fields (token decrypted, legacy plaintext as-is)."""
    from shared.crypto_fields import FieldDecryptError, decrypt_field
    try:
        return decrypt_field(value)
    except FieldDecryptError:
        return None


def safe_view(data: dict | None) -> dict:
    """The session data as it may be SHOWN: identity / bank numbers are replaced
    by `<field>_masked` + `has_<field>`; the stored value never leaves the service."""
    out = {}
    for k, v in (data or {}).items():
        if k in SENSITIVE:
            plain = _plain(v)
            out[f"has_{k}"] = bool(plain)
            out[f"{k}_masked"] = crypto.mask(plain, _MASK_KIND.get(k, "generic")) if plain else None
        else:
            out[k] = v
    return out


def employee_payload(data: dict | None) -> dict:
    """Decrypted data for the employee service at activation (in-cluster call only)."""
    return {k: (_plain(v) if k in SENSITIVE else v) for k, v in (data or {}).items()}


def missing_for_submit(country: str | None, data: dict | None) -> list[str]:
    d = data or {}
    return [label for field, label in REQUIRED
            if d.get(field) in (None, "") or (isinstance(d.get(field), str) and not d[field].strip())]


DEFAULT_CHECKLIST = [
    # (title, owner, days from the joining date)
    ("Offer letter signed and filed", "HR", -3),
    ("Laptop / phone issued", "IT", 0),
    ("E-mail and system access created", "IT", 0),
    ("ID card issued", "Admin", 1),
    ("Induction completed", "HR", 2),
    ("Company policies read and acknowledged", "HR", 3),
    ("SIN, TD1 forms and direct-deposit details checked", "Payroll", 3),
]
