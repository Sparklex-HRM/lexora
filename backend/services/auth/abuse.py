"""Auth service — abuse protection for the PUBLIC sign-up / MFA endpoints.

Rate limits
    Uses the platform's Redis-backed limiter when stream S1 has published one in
    shared.utils.ratelimit (any of: `RedisRateLimiter(max_hits, window_sec)` or
    `make_limiter(max_hits, window_sec, name=…)`); otherwise the in-process sliding
    window from the same module (per replica — looser on several replicas, never
    wrong). `limiter_backend()` reports which one is live.

CAPTCHA hook
    LEXORA_CAPTCHA_PROVIDER = turnstile | hcaptcha   (unset = no captcha)
    LEXORA_CAPTCHA_SECRET   = the provider's server secret
    LEXORA_CAPTCHA_SITE_KEY = the public site key the sign-up page renders
    `verify_captcha(token, ip)` posts to the provider's documented siteverify URL.
    With no provider configured it returns True and `captcha_info()` says so —
    the honest state is "captcha not configured", never a fake pass message.
"""
from __future__ import annotations

import os

import httpx

from shared.utils import ratelimit as _rl


def _make(max_hits: int, window: int, name: str):
    for attr in ("make_limiter",):
        fn = getattr(_rl, attr, None)
        if callable(fn):
            try:
                return fn(max_hits, window, name=name), "redis"
            except Exception:
                pass
    cls = getattr(_rl, "RedisRateLimiter", None)
    if cls is not None:
        try:
            return cls(max_hits, window), "redis"
        except Exception:
            pass
    return _rl.RateLimiter(max_hits, window), "memory"


signup_ip_limiter, _BACKEND = _make(int(os.getenv("LEXORA_SIGNUP_PER_IP_HOUR", "5")), 3600, "signup_ip")
signup_email_limiter, _ = _make(3, 3600, "signup_email")
verify_limiter, _ = _make(20, 900, "signup_verify")
mfa_limiter, _ = _make(int(os.getenv("LEXORA_MFA_MAX_TRIES", "5")), 900, "mfa_verify")


def limiter_backend() -> str:
    return getattr(signup_ip_limiter, "backend", _BACKEND)


def hit(limiter, key: str) -> int:
    """0 = allowed, else seconds to wait (works for sync or async limiter objects
    that expose .hit(key) returning seconds)."""
    try:
        return int(limiter.hit(key) or 0)
    except Exception:
        return 0


_VERIFY_URLS = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
}


def captcha_info() -> dict:
    prov = (os.getenv("LEXORA_CAPTCHA_PROVIDER") or "").strip().lower()
    ok = prov in _VERIFY_URLS and bool((os.getenv("LEXORA_CAPTCHA_SECRET") or "").strip())
    return {"provider": prov if ok else None, "configured": ok,
            "site_key": (os.getenv("LEXORA_CAPTCHA_SITE_KEY") or "").strip() or None if ok else None}


async def verify_captcha(token: str | None, ip: str | None = None, *,
                         transport: httpx.AsyncBaseTransport | None = None) -> bool:
    info = captcha_info()
    if not info["configured"]:
        return True                          # no captcha configured → nothing to check
    if not token:
        return False
    data = {"secret": os.getenv("LEXORA_CAPTCHA_SECRET", "").strip(), "response": token}
    if ip:
        data["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=8.0, transport=transport) as c:
            r = await c.post(_VERIFY_URLS[info["provider"]], data=data)
        return bool(r.status_code == 200 and r.json().get("success"))
    except Exception:
        return False                         # fail closed when a captcha IS configured
