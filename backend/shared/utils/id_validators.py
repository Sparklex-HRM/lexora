"""Lexora HRM — FORMAT checks for Canadian identity, bank and address numbers.

These check only the publicly documented SHAPE of a number (length, character
pattern, check digit). They never look a number up and never decide whether a
person is eligible for anything — the value the user types is what is stored.

Each function returns the cleaned value, or raises `ValueError` with a short
plain message that can go straight on screen. An empty value returns None.
"""
import re

CA = "CA"
COUNTRIES = (CA,)


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


# ── Check-digit algorithm ────────────────────────────────────────────────────
def luhn_ok(number: str) -> bool:
    """Luhn (mod 10): from the right, double every second digit, sum the digits."""
    if not number or not number.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# ── Canada ────────────────────────────────────────────────────────────────────
# SIN — Government of Canada: 9 digits validated with the Luhn algorithm.
def clean_sin(v) -> str | None:
    if _blank(v):
        return None
    d = _digits(v)
    if len(d) != 9:
        raise ValueError("SIN must be 9 digits")
    if not luhn_ok(d):
        raise ValueError("SIN is not valid — check the digits")
    return d


# Canadian direct deposit (Payments Canada): institution number 3 digits,
# branch transit number 5 digits, account number up to 12 digits.
def clean_institution(v) -> str | None:
    if _blank(v):
        return None
    d = _digits(v)
    if len(d) != 3 or d != str(v).strip():
        raise ValueError("Institution number must be 3 digits")
    return d


def clean_transit(v) -> str | None:
    if _blank(v):
        return None
    d = _digits(v)
    if len(d) != 5 or d != str(v).strip():
        raise ValueError("Transit number must be 5 digits")
    return d


def clean_bank_account_ca(v) -> str | None:
    if _blank(v):
        return None
    s = re.sub(r"[\s-]", "", str(v))
    if not s.isdigit() or not (5 <= len(s) <= 12):
        raise ValueError("Account number must be 5 to 12 digits")
    return s


# ── Postal code ───────────────────────────────────────────────────────────────
# Canada Post: A9A 9A9; never D F I O Q U; W and Z never the first letter.
_POSTAL_RE = re.compile(r"^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z][0-9][ABCEGHJ-NPRSTV-Z][0-9]$")


def clean_postal_code(v) -> str | None:
    if _blank(v):
        return None
    s = re.sub(r"\s+", "", str(v)).upper()
    if not _POSTAL_RE.match(s):
        raise ValueError("Postal code must look like K1A 0B1")
    return s[:3] + " " + s[3:]


# ── One entry point ───────────────────────────────────────────────────────────
def clean_country(v, default: str | None = None) -> str | None:
    c = (str(v).strip().upper() if not _blank(v) else (default or "")).upper()
    if not c:
        return None
    if c not in COUNTRIES:
        raise ValueError("Work country must be Canada (CA)")
    return c


# field → cleaner (only the fields present are checked).
_RULES = {"sin": clean_sin, "bank_institution": clean_institution, "bank_transit": clean_transit,
          "bank_account": clean_bank_account_ca}


def clean_identity_fields(country: str | None, values: dict) -> tuple[dict, dict]:
    """Validate the identity / bank fields present in `values`.

    Returns (cleaned, errors): `cleaned` holds only the fields that were present
    (None = the user cleared it), `errors` maps field → plain message. `country` is kept for
    the call shape; Lexora HRM is Canada-only.
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
