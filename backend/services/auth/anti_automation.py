"""Auth service — anti-automation for the pre-login endpoints (WASA hardening
2026-10-21, ported from the Medfidelity HMS captcha1/captcha2 build).

1. Our own CAPTCHA: proof of work (pow_captcha.py — no third party, no keys).
   ``CAPTCHA_MODE`` = pow (DEFAULT) | off.  ``CAPTCHA_VISIBLE`` = true (DEFAULT:
   the page shows an "I'm not a robot" box and the challenge must be
   ``interactive``) | false (solved in the background).
   Enforced on POST /auth/login, /auth/mfa/challenge/verify, /auth/mfa/challenge/
   enroll/finish, /auth/password/forgot, /auth/signup and /auth/register-org
   (field ``captcha_token`` in the JSON body). The existing Turnstile / hCaptcha
   hook in abuse.py stays on /auth/signup as an extra.

   Adaptive difficulty: failed sign-ins for the same account (any org, any IP)
   or from the same client IP, and forgot-password requests for the same e-mail
   / IP, in the last 15 minutes raise the work (+2 / +4 / +6 bits at 3 / 5 / 10).
   Counted in Redis when REDIS_URL is set (shared.utils.ratelimit, in-process
   twin when Redis is down).

   Refusals are HTTP 400 ``{"detail": "<sentence>", "code": ...}`` with code
   captcha_required (no token) | captcha_invalid (bad / reused / tampered /
   wrong purpose / not solved) | captcha_harder (solved, but this account now
   needs more work: fetch a new challenge naming the account) |
   captcha_expired | captcha_not_ticked (visible mode, token from a challenge
   that was not issued for the box).

2. Forgot-password limits: 3 per 15 minutes per e-mail (any IP) and 10 per 15
   minutes per client IP (any e-mail), on top of the older 5 per e-mail+IP.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Iterable, Optional

from fastapi import Request

from shared.utils import ratelimit as _rl
from shared.utils.ratelimit import client_ip, make_limiter, make_login_limiter

import pow_captcha as pc
from http_errors import PublicError

log = logging.getLogger("auth.anti_automation")

WINDOW_SEC = 900
FORGOT_MAX_PER_EMAIL = int(os.getenv("LEXORA_FORGOT_MAX_PER_EMAIL", "3"))
FORGOT_MAX_PER_IP = int(os.getenv("LEXORA_FORGOT_MAX_PER_IP", "10"))
forgot_email_limiter = make_limiter(FORGOT_MAX_PER_EMAIL, WINDOW_SEC, name="auth_forgot_email")
forgot_ip_limiter = make_limiter(FORGOT_MAX_PER_IP, WINDOW_SEC, name="auth_forgot_ip")

# Count-only failure counters (they never lock: the lock is security_policy's job).
_COUNT_ONLY = 10 ** 9
pow_account_failures = make_login_limiter(_COUNT_ONLY, WINDOW_SEC, 1, name="pow_fail_acct")
pow_client_failures = make_login_limiter(_COUNT_ONLY, WINDOW_SEC, 1, name="pow_fail_ip")
pow_forgot_email = make_login_limiter(_COUNT_ONLY, WINDOW_SEC, 1, name="pow_forgot_email")
pow_forgot_ip = make_login_limiter(_COUNT_ONLY, WINDOW_SEC, 1, name="pow_forgot_ip")

CAPTCHA_FAILED_MESSAGE = "Please complete the security check and try again."
CAPTCHA_NOT_TICKED_MESSAGE = "Please tick \"I'm not a robot\" and try again."


# ── mode ────────────────────────────────────────────────────────────────────
def captcha_mode() -> str:
    m = (os.getenv("CAPTCHA_MODE") or "pow").strip().lower()
    return "off" if m in ("off", "0", "false", "no", "none") else "pow"


def captcha_enabled() -> bool:
    return captcha_mode() == "pow"


def _acct(account: Optional[str]) -> str:
    return hashlib.sha256((account or "").strip().lower().encode()).hexdigest()[:32]


# ── failure counters → difficulty ───────────────────────────────────────────
def note_login_failure(account: Optional[str], req: Request) -> None:
    """Next to the login lockout counters: more work for this account + client."""
    if account:
        pow_account_failures.record_failure(_acct(account))
    ip = client_ip(req)
    if ip:
        pow_client_failures.record_failure(ip)


def note_login_success(account: Optional[str]) -> None:
    if account:
        pow_account_failures.record_success(_acct(account))


def note_forgot_request(email: Optional[str], req: Request) -> None:
    if email:
        pow_forgot_email.record_failure(_acct(email))
    ip = client_ip(req)
    if ip:
        pow_forgot_ip.record_failure(ip)


def failures_for(purpose: str, account: Optional[str], req: Request) -> int:
    ip = client_ip(req)
    n = pow_client_failures.failures(ip) if ip else 0
    if account:
        n = max(n, pow_account_failures.failures(_acct(account)))
    if purpose == "forgot":
        if account:
            n = max(n, pow_forgot_email.failures(_acct(account)) * 2)   # 3 / 15 min is the limit
        if ip:
            n = max(n, pow_forgot_ip.failures(ip))
    return n


def required_bits(purpose: str, account: Optional[str], req: Request) -> int:
    return pc.required_bits(failures_for(purpose, account, req))


# ── challenge ───────────────────────────────────────────────────────────────
def challenge_reply(purpose: str, account: Optional[str], req: Request) -> dict:
    """GET /auth/challenge. Always says whether the check is on, so scripts and
    the page know whether to solve anything."""
    if not captcha_enabled():
        return {"enabled": False, "mode": "off", "interactive": False}
    visible = pc.visible_required()
    ch = pc.make_challenge(purpose, required_bits(purpose, account, req), interactive=visible)
    return {**ch, "enabled": True, "mode": "pow", "interactive": visible}


# ── single use (Redis, memory fallback inside pow_captcha) ──────────────────
def _redis_for_pow():
    if not _rl.use_redis():
        return None
    try:
        return _rl._sync_client(_rl._redis_url())
    except Exception:  # noqa: BLE001
        return None


def _path(req: Request) -> str:
    return str(getattr(getattr(req, "url", None), "path", "?"))


def require_pow(token: Optional[str], req: Request, *, purposes: Iterable[str],
                account: Optional[str] = None) -> None:
    """No-op with CAPTCHA_MODE=off. Otherwise raise a 400 PublicError unless
    `token` is a fresh, unused, solved challenge for one of `purposes`."""
    if not captcha_enabled():
        return
    purposes = tuple(purposes)
    tok = (token or "").strip()
    if not tok:
        log.info("captcha missing on %s", _path(req))
        raise PublicError(400, CAPTCHA_FAILED_MESSAGE, code="captcha_required")
    need = required_bits(purposes[0], account, req)
    try:
        pc.verify(tok, purposes, need, redis_client=_redis_for_pow(),
                  require_interactive=pc.visible_required())
    except pc.PowError as e:
        log.info("captcha refused on %s: %s", _path(req), e.code)
        if e.code == "too_easy":
            raise PublicError(400, CAPTCHA_FAILED_MESSAGE, code="captcha_harder", difficulty=need)
        if e.code == "expired":
            raise PublicError(400, CAPTCHA_FAILED_MESSAGE, code="captcha_expired")
        if e.code == "not_interactive":
            raise PublicError(400, CAPTCHA_NOT_TICKED_MESSAGE, code="captcha_not_ticked")
        if e.code == "missing":
            raise PublicError(400, CAPTCHA_FAILED_MESSAGE, code="captcha_required")
        raise PublicError(400, CAPTCHA_FAILED_MESSAGE, code="captcha_invalid")


def reset_for_tests() -> None:
    pc.reset_for_tests()
    for lim in (forgot_email_limiter, forgot_ip_limiter):
        h = getattr(lim, "_hits", None)
        if h is not None:
            h.clear()
    for lim in (pow_account_failures, pow_client_failures, pow_forgot_email, pow_forgot_ip):
        tw = getattr(lim, "fallback", lim)
        tw._fails.clear()
        tw._locked_until.clear()
