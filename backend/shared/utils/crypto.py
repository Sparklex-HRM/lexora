"""Lexora HRM — column-level encryption for sensitive identifiers.

AES-256-GCM with a key-encryption key (KEK) read from the environment:

    LEXORA_DATA_KEY      base64 of 32 random bytes  (the CURRENT key)
    LEXORA_DATA_KEY_ID   short label for that key, default "k1"
    LEXORA_DATA_KEYS     optional ring of OLDER keys for rotation,
                         "k0:<base64>,k1:<base64>" — decrypt-only

Token format (one string, safe for a VARCHAR column):

    enc:v1:<key_id>:<base64(nonce12 || ciphertext || tag16)>

A fresh random 96-bit nonce is drawn per encryption, so two rows holding the
same SIN never produce the same ciphertext. Equality / uniqueness checks use a
separate keyed hash (`blind_index`) instead — HMAC-SHA256 over the normalised
value with a key derived from the KEK — which is deterministic but reveals
nothing without the key.

Local development: if LEXORA_DATA_KEY is unset the module derives a key from
JWT_SECRET so the stack still boots, and reports `dev_fallback=True` in
`status()`. Production must set a real key; the compliance dashboard flags the
fallback as "not launch-ready".
"""
import base64
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "enc:v1:"
_NONCE_LEN = 12


class CryptoError(ValueError):
    pass


def _b64d(s: str) -> bytes:
    s = s.strip()
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _load_ring() -> tuple[str, dict[str, bytes], bool]:
    """Returns (current_key_id, {key_id: key_bytes}, dev_fallback)."""
    cur_id = os.getenv("LEXORA_DATA_KEY_ID", "k1").strip() or "k1"
    ring: dict[str, bytes] = {}
    fallback = False
    raw = os.getenv("LEXORA_DATA_KEY", "").strip()
    if raw:
        key = _b64d(raw)
        if len(key) != 32:
            raise CryptoError("LEXORA_DATA_KEY must decode to exactly 32 bytes")
        ring[cur_id] = key
    else:
        # Dev-only: derive from the JWT secret so local stacks work out of the box.
        secret = os.getenv("JWT_SECRET", "dev-only-insecure-key-change-me")
        ring[cur_id] = hashlib.sha256(f"lexora-data-key:{secret}".encode()).digest()
        fallback = True
    for part in os.getenv("LEXORA_DATA_KEYS", "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        kid, b64 = part.split(":", 1)
        key = _b64d(b64)
        if len(key) == 32:
            ring.setdefault(kid.strip(), key)
    return cur_id, ring, fallback


_CUR_ID, _RING, _DEV_FALLBACK = _load_ring()


def reload_keys():
    """Re-read the environment (tests + rotation without restart)."""
    global _CUR_ID, _RING, _DEV_FALLBACK
    _CUR_ID, _RING, _DEV_FALLBACK = _load_ring()


def status() -> dict:
    return {"algorithm": "AES-256-GCM", "key_id": _CUR_ID,
            "keys_in_ring": sorted(_RING.keys()), "dev_fallback": _DEV_FALLBACK,
            "configured": not _DEV_FALLBACK}


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(plain: str | None, *, aad: str = "") -> str | None:
    """Encrypt a string under the CURRENT key. None/empty → None."""
    if plain is None:
        return None
    plain = str(plain)
    if plain == "":
        return None
    if is_encrypted(plain):          # already a token — never double-encrypt
        return plain
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(_RING[_CUR_ID]).encrypt(nonce, plain.encode("utf-8"), aad.encode() or None)
    return f"{_PREFIX}{_CUR_ID}:{base64.b64encode(nonce + ct).decode()}"


def decrypt(token: str | None, *, aad: str = "") -> str | None:
    """Decrypt a token produced by `encrypt`. A plain (non-token) string is
    returned unchanged so callers can read mixed legacy/encrypted columns."""
    if token is None or token == "":
        return None
    if not is_encrypted(token):
        return token
    try:
        _, _, rest = token.partition(_PREFIX)
        kid, _, b64 = rest.partition(":")
        key = _RING.get(kid)
        if key is None:
            raise CryptoError(f"No key '{kid}' in the key ring")
        blob = _b64d(b64)
        nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        return AESGCM(key).decrypt(nonce, ct, aad.encode() or None).decode("utf-8")
    except CryptoError:
        raise
    except Exception as exc:  # tag mismatch, bad base64 …
        raise CryptoError(f"Cannot decrypt value: {exc.__class__.__name__}") from exc


def key_id_of(token: str | None) -> str | None:
    if not is_encrypted(token):
        return None
    return token[len(_PREFIX):].split(":", 1)[0]


def rotate(token: str | None, *, aad: str = "") -> str | None:
    """Re-encrypt under the current key (no-op if already current)."""
    if not is_encrypted(token):
        return encrypt(token, aad=aad)
    if key_id_of(token) == _CUR_ID:
        return token
    return encrypt(decrypt(token, aad=aad), aad=aad)


# ── Blind index (searchable keyed hash) ───────────────────────────────────────

def normalise(value: str | None) -> str:
    """Canonical form for equality: upper-case, no spaces / dashes / dots."""
    if value is None:
        return ""
    return re.sub(r"[\s\-\.]", "", str(value)).upper()


def blind_index(value: str | None, *, scope: str = "") -> str | None:
    """HMAC-SHA256(derived key, scope + normalised value) as hex. Deterministic,
    so it can back a UNIQUE index, but useless without the key."""
    norm = normalise(value)
    if not norm:
        return None
    hk = hashlib.sha256(b"lexora-blind-index:" + _RING[_CUR_ID]).digest()
    return hmac.new(hk, f"{scope}|{norm}".encode(), hashlib.sha256).hexdigest()


# ── Masking ───────────────────────────────────────────────────────────────────

def mask(value: str | None, kind: str = "generic") -> str | None:
    """Display form that keeps only a tail of the identifier.

    sin      123456789   → •••-•••-789
    generic  (bank / passport) → X…X + last 4
    """
    if value is None or value == "":
        return None
    v = str(value)
    if kind == "sin":
        return "•••-•••-" + v[-3:]
    tail = v[-4:] if len(v) > 4 else v[-1:]
    return "X" * max(len(v) - len(tail), 0) + tail


def last_n(value: str | None, n: int = 4) -> str | None:
    if not value:
        return None
    return str(value)[-n:]
