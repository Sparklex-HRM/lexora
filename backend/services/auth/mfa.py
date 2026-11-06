"""Auth service — TOTP multi-factor authentication (RFC 6238) + recovery codes.

Pure Python (hmac / hashlib / base64), no extra dependency. Works with any
authenticator app (Google Authenticator, Microsoft Authenticator, Authy,
1Password …): SHA-1, 6 digits, 30-second step — the defaults every app reads
from an ``otpauth://`` URI.

Policy (who MUST use it) lives here too so the login route and the screens ask
one place:

    MFA_REQUIRED_ROLES   org_admin, hr_admin, payroll_admin (+ superadmin)
    LEXORA_MFA_ENFORCE   "0" turns the mandatory part off (local e2e scripts
                         that sign in through the API); any other value / unset
                         = enforced. Optional MFA for other roles always works.

Secrets are stored ENCRYPTED (shared.utils.crypto, AES-256-GCM, aad bound to
the user id). Recovery codes are stored as SHA-256 hashes; each works once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6
WINDOW = 1                     # accept the previous / next step too (clock drift)
RECOVERY_COUNT = 10

MFA_REQUIRED_ROLES = {"superadmin", "org_admin", "hr_admin", "payroll_admin"}


def enforced() -> bool:
    return os.getenv("LEXORA_MFA_ENFORCE", "1").strip().lower() not in ("0", "false", "no", "off")


def required_for(role: str | None) -> bool:
    return enforced() and (role or "") in MFA_REQUIRED_ROLES


# ── TOTP ─────────────────────────────────────────────────────────────────────

def new_secret() -> str:
    """160-bit random secret, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    s += "=" * (-len(s) % 8)
    return base64.b32decode(s)


def hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    mac = hmac.new(_key(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP)


def totp(secret_b32: str, now: float | None = None) -> str:
    return hotp(secret_b32, current_step(now))


def verify_totp(secret_b32: str, code: str, *, now: float | None = None,
                last_step: int | None = None) -> int | None:
    """The matched time step, or None. A step at or before `last_step` is refused
    (a code cannot be replayed inside its 30-second life)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    for delta in range(-WINDOW, WINDOW + 1):
        step = base + delta
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(secret_b32, step), code):
            return step
    return None


def otpauth_uri(secret_b32: str, account: str, issuer: str = "Lexora HRM") -> str:
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


# ── Recovery codes ───────────────────────────────────────────────────────────

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I


def _norm_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def hash_recovery(code: str) -> str:
    return hashlib.sha256(("lexora-recovery:" + _norm_code(code)).encode()).hexdigest()


def new_recovery_codes(n: int = RECOVERY_COUNT) -> list[str]:
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def use_recovery(code: str, hashes: list[str] | None) -> list[str] | None:
    """If `code` matches an unused recovery hash, return the remaining list
    (the code is consumed); else None."""
    if not code or not hashes:
        return None
    h = hash_recovery(code)
    for i, stored in enumerate(hashes):
        if hmac.compare_digest(stored, h):
            return hashes[:i] + hashes[i + 1:]
    return None


def looks_like_recovery(code: str) -> bool:
    n = _norm_code(code)
    return len(n) == 10 and not n.isdigit()
