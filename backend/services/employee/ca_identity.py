"""Employee service — Canadian identity / direct-deposit field checks (Lexora HRM is Canada-only).

SIN (Luhn) and the Payments Canada direct-deposit numbers (institution 3 digits,
transit 5 digits, account 5–12 digits) are shape-checked here; the cleaners
themselves live in shared/utils/id_validators.py.
"""
import sys

sys.path.append("/app")
from shared.utils.id_validators import (clean_bank_account_ca, clean_institution,  # noqa: E402
                                        clean_sin, clean_transit)

CA_PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")

_RULES = {"sin": clean_sin, "bank_institution": clean_institution, "bank_transit": clean_transit,
          "bank_account": clean_bank_account_ca}


def clean_country(v, default: str | None = None) -> str:
    """Work country — always CA. Blank → CA; anything else is refused."""
    c = (str(v).strip().upper() if v not in (None, "") and str(v).strip() else "") or "CA"
    if c != "CA":
        raise ValueError("Work country must be Canada (CA) — Lexora HRM is Canada-only")
    return "CA"


def clean_identity_fields(values: dict) -> tuple[dict, dict]:
    """Validate the identity / bank fields present in `values`.

    Returns (cleaned, errors): `cleaned` holds only the fields that were present
    (None = the user cleared it), `errors` maps field → plain message.
    """
    cleaned, errors = {}, {}
    for field, fn in _RULES.items():
        if field not in values:
            continue
        try:
            cleaned[field] = fn(values[field])
        except ValueError as exc:
            errors[field] = str(exc)
    return cleaned, errors


def valid_province(region: str | None) -> bool:
    return bool(region) and region.upper() in CA_PROVINCES
