"""Our own CAPTCHA: proof of work (Lexora HRM WASA hardening 2026-10-21, ported
from the Medfidelity HMS captcha1/captcha2 build — no third party, no keys).

How it works
------------
1. Before a login / MFA step / forgot-password / hospital sign-up the client
   asks `GET /auth/challenge?purpose=...`. The reply is
   ``{algorithm: "SHA-256", salt, difficulty, expires, purpose, signature}``.
   Nothing is stored on the server: the HMAC signature (secret
   ``CAPTCHA_HMAC_SECRET``, else derived from ``JWT_SECRET``) is what proves the challenge is ours and
   that nobody lowered its difficulty or stretched its expiry.
2. The client finds a whole number ``n`` such that
   ``SHA-256(salt + str(n))`` starts with ``difficulty`` zero BITS. On average
   that takes 2**difficulty hashes — a fraction of a second for a person,
   real CPU cost for a script that signs in thousands of times.
3. The client sends back the challenge plus ``number`` as ``captcha_token``
   (base64url JSON — the same field the Turnstile widget used).
4. The server checks, in this order: shape, signature, expiry, purpose,
   difficulty high enough for this account / client right now, the hash,
   and finally that the challenge was never used before (Redis
   ``SET NX``; an in-process set when Redis is down). Only then is the
   password looked at.

Adaptive difficulty: the base difficulty is low (tuned so a phone solves it
in well under a second). After failed logins / forgot-password requests for
the same account or the same public client IP it goes up (see
``extra_bits``), so a password-guessing script pays 4x … 64x more per try.
A challenge solved at the low difficulty is refused with
``captcha_harder`` once the account has failures — the client then asks for
a new challenge naming the account and solves that one.

Visible checkbox (2026-10-20, CAPTCHA_VISIBLE=true, the default): the login
page shows an "I'm not a robot" box and only starts solving when a person
ticks it. Challenges then carry ``interactive: true`` (inside the HMAC, so
nobody can add it to a challenge issued without it) and the server refuses
a token that does not echo it (``not_interactive``). Honest limit: a
browser-automation script can tick the box too — this is a visible deterrent
plus a work cost per try, not proof of a human. Rate limits and lockout
remain the real guard.

This module is stdlib-only on purpose: ``solve()`` is also the reference
client used by the tests and by our own scripts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from typing import Iterable, Optional

log = logging.getLogger("auth.pow_captcha")

ALGORITHM = "SHA-256"
PURPOSES = ("login", "mfa", "forgot", "signup")
MAX_NUMBER = 2 ** 40                 # a solution beyond this is not a real search
MIN_BITS, MAX_BITS = 8, 26


def ttl_sec() -> int:
    return int(os.getenv("POW_TTL_SEC", "120"))


def base_bits() -> int:
    """Default difficulty (env POW_BASE_BITS, default 16). 16 bits = ~65k
    hashes on average: well under a second in a Web Worker on a phone
    (HMS measurement 2026-10-20: desktop mean 49 ms, 390 px + 4x CPU
    throttle mean 368 ms / p95 986 ms)."""
    try:
        b = int(os.getenv("POW_BASE_BITS", "16"))
    except ValueError:
        b = 16
    return max(MIN_BITS, min(MAX_BITS, b))


class PowError(Exception):
    """A refused token. ``code`` is the machine reason (logged, never
    shown: the person only ever sees one generic sentence)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ── secret ──────────────────────────────────────────────────────────────────
_fallback_secret: Optional[bytes] = None


def _secret() -> bytes:
    """CAPTCHA_HMAC_SECRET when set. Without it, a key derived
    from JWT_SECRET (shared by every pod and worker, so a challenge issued by
    one worker verifies on another); without either, a per-process random
    key (dev only — then only single-worker setups verify)."""
    global _fallback_secret
    s = (os.getenv("CAPTCHA_HMAC_SECRET") or "").strip()
    if s:
        return s.encode()
    j = (os.getenv("JWT_SECRET") or "").strip()
    if j:
        return hmac.new(j.encode(), b"jethro-pow-captcha-v1", hashlib.sha256).digest()
    if _fallback_secret is None:
        log.warning("CAPTCHA_HMAC_SECRET not set: using a per-process key")
        _fallback_secret = secrets.token_bytes(32)
    return _fallback_secret


def visible_required() -> bool:
    """CAPTCHA_VISIBLE=true (default): the person ticks a visible box first,
    challenges are issued as interactive and tokens must echo it."""
    return (os.getenv("CAPTCHA_VISIBLE") or "true").strip().lower() not in ("0", "false", "no", "off")


def _sign(purpose: str, salt: str, difficulty: int, expires: int,
          interactive: bool = False) -> str:
    msg = f"v1|{ALGORITHM}|{purpose}|{salt}|{difficulty}|{expires}"
    if interactive:
        msg += "|i1"           # unchanged message for invisible challenges
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


# ── challenge ───────────────────────────────────────────────────────────────
def make_challenge(purpose: str, difficulty: Optional[int] = None,
                   *, now: Optional[float] = None, interactive: bool = False) -> dict:
    if purpose not in PURPOSES:
        raise PowError("bad_purpose")
    d = base_bits() if difficulty is None else int(difficulty)
    d = max(MIN_BITS, min(MAX_BITS, d))
    exp = int((time.time() if now is None else now) + ttl_sec())
    salt = secrets.token_hex(12)
    ch = {"algorithm": ALGORITHM, "salt": salt, "difficulty": d,
          "expires": exp, "purpose": purpose,
          "signature": _sign(purpose, salt, d, exp, interactive)}
    if interactive:
        ch["interactive"] = True
    return ch


def leading_zero_bits(digest: bytes) -> int:
    n = 0
    for byte in digest:
        if byte == 0:
            n += 8
            continue
        return n + (8 - byte.bit_length())
    return n


def meets(salt: str, number: int, difficulty: int) -> bool:
    d = hashlib.sha256(f"{salt}{number}".encode()).digest()
    return leading_zero_bits(d) >= difficulty


def solve(challenge: dict, *, limit: int = MAX_NUMBER) -> int:
    """Reference solver (tests, scripts). Same search order as the web and
    mobile solvers: 0, 1, 2, ..."""
    salt, d = challenge["salt"], int(challenge["difficulty"])
    n = 0
    while n < limit:
        if meets(salt, n, d):
            return n
        n += 1
    raise PowError("unsolved")


_TOKEN_KEYS = ("algorithm", "salt", "difficulty", "expires", "purpose", "signature")


def encode_token(challenge: dict, number: int, interactive: bool = False) -> str:
    """The captcha_token for a solved challenge. Only the signed fields travel
    (the /auth/challenge reply also says enabled / mode, which are not part of
    the challenge). `interactive` is carried when the challenge had it (or the
    caller says so) — the server refuses a visible-box token without it."""
    body = {k: challenge[k] for k in _TOKEN_KEYS if k in challenge}
    if interactive or challenge.get("interactive") is True:
        body["interactive"] = True
    body["number"] = int(number)
    raw = json.dumps(body, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_token(token: str) -> dict:
    t = (token or "").strip()
    if not t or len(t) > 2048:
        raise PowError("missing" if not t else "malformed")
    try:
        if t.startswith("{"):
            data = json.loads(t)
        else:
            data = json.loads(base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)))
    except Exception:  # noqa: BLE001
        raise PowError("malformed")
    if not isinstance(data, dict):
        raise PowError("malformed")
    return data


# ── single use ──────────────────────────────────────────────────────────────
_used_lock = threading.Lock()
_used_mem: dict[str, float] = {}


def _mark_used(salt: str, expires: int, now: float, redis_client=None) -> bool:
    """True the first time a challenge is presented, False on every replay."""
    ttl = max(5, int(expires - now) + 5)
    if redis_client is not None:
        try:
            return bool(redis_client.set(f"pow:used:{salt}", b"1", nx=True, ex=ttl))
        except Exception as e:  # noqa: BLE001
            log.warning("pow single-use: redis unavailable, using memory: %s", type(e).__name__)
    with _used_lock:
        if len(_used_mem) > 50000:
            for k in [k for k, v in _used_mem.items() if v < now]:
                _used_mem.pop(k, None)
        if salt in _used_mem and _used_mem[salt] >= now:
            return False
        _used_mem[salt] = now + ttl
        return True


def reset_for_tests() -> None:
    global _fallback_secret
    with _used_lock:
        _used_mem.clear()
    _fallback_secret = None


# ── verify ──────────────────────────────────────────────────────────────────
def verify(token: str, purposes: Iterable[str], min_bits: int,
           *, now: Optional[float] = None, redis_client=None,
           require_interactive: bool = False) -> dict:
    """Return the decoded token when it is a valid, fresh, unused, solved
    challenge for one of `purposes` at difficulty >= `min_bits` (and, with
    `require_interactive`, one issued for the visible checkbox); otherwise
    raise PowError."""
    now = time.time() if now is None else now
    data = decode_token(token)
    try:
        alg = str(data["algorithm"])
        salt = str(data["salt"])
        diff = int(data["difficulty"])
        exp = int(data["expires"])
        purpose = str(data["purpose"])
        sig = str(data["signature"])
        number = int(data["number"])
        interactive = data.get("interactive", False)
    except Exception:  # noqa: BLE001
        raise PowError("malformed")
    if type(interactive) is not bool:        # only a JSON boolean counts (1 == True)
        raise PowError("malformed")
    if alg != ALGORITHM or not (8 <= len(salt) <= 64) or not (0 <= number < MAX_NUMBER):
        raise PowError("malformed")
    if not hmac.compare_digest(sig, _sign(purpose, salt, diff, exp, interactive)):
        raise PowError("bad_signature")
    if exp < now:
        raise PowError("expired")
    if exp > now + ttl_sec() + 60:          # signed by us, but never with this long a life
        raise PowError("bad_signature")
    if purpose not in set(purposes):
        raise PowError("wrong_purpose")
    if require_interactive and interactive is not True:
        raise PowError("not_interactive")
    if diff < min_bits:
        raise PowError("too_easy")
    if not meets(salt, number, diff):
        raise PowError("not_solved")
    if not _mark_used(salt, exp, now, redis_client):
        raise PowError("reused")
    return data


# ── adaptive difficulty ─────────────────────────────────────────────────────
def extra_bits(failures: int) -> int:
    """Failures (login or forgot-password, same account or same client, in
    the current window) -> extra difficulty bits. Each bit doubles the work."""
    if failures >= 10:
        return 6
    if failures >= 5:
        return 4
    if failures >= 3:
        return 2
    return 0


def required_bits(failures: int) -> int:
    return min(MAX_BITS, base_bits() + extra_bits(failures))
