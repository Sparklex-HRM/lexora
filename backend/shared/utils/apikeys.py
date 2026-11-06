"""Lexora HRM — org-scoped API keys (accepted by the gateway instead of a JWT).

Key format (shown ONCE at creation):   lxk_<8 chars prefix>_<32 chars secret>
Stored: the prefix (for the screen) and SHA-256 of the whole key. Never the key.

A key carries SCOPES ``<service>:read`` / ``<service>:write`` (``*:read`` for
all). The gateway refuses any request outside the scopes BEFORE it reaches a
service, then stamps the key's org, the role chosen when the key was made
(bounded by the creator's own grantable roles) and ``X-User-Email:
apikey:<name>`` so the audit trail names the key.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from shared.utils import platform_models as pm
from shared.utils.providers.models import ApiKey

PREFIX = "lxk_"
# Services a key may be scoped to (gateway prefixes). auth / ai are never key-reachable.
SCOPED_SERVICES = ("employees", "masters", "exit", "letters", "hiring", "performance", "onboarding", "payroll",
                   "time", "leave", "approvals", "notifications", "settings", "reports", "ops", "compliance")
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
ROLES_FOR_KEYS = ("employee", "manager", "hr_admin", "payroll_admin")


def generate() -> tuple[str, str, str]:
    """(raw key, prefix to store, sha256 to store)."""
    pre = secrets.token_hex(4)
    raw = f"{PREFIX}{pre}_{secrets.token_urlsafe(24)}"
    return raw, PREFIX + pre, hash_key(raw)


def hash_key(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def looks_like_key(value: str | None) -> bool:
    v = (value or "").strip()
    return v.startswith(PREFIX) and 20 < len(v) < 120


def clean_scopes(scopes) -> list[str]:
    """Keep only '<service>:read|write' and '*:read|write'; dedupe; sorted."""
    out = set()
    for s in scopes or []:
        s = str(s).strip().lower()
        if ":" not in s:
            continue
        svc, lvl = s.split(":", 1)
        if lvl not in ("read", "write") or (svc != "*" and svc not in SCOPED_SERVICES):
            continue
        out.add(f"{svc}:{lvl}")
    return sorted(out)


def scope_allows(scopes: list | None, service: str, method: str) -> bool:
    need = "read" if (method or "GET").upper() in READ_METHODS else "write"
    sc = set(scopes or [])
    if service not in SCOPED_SERVICES:
        return False
    for svc in (service, "*"):
        if f"{svc}:write" in sc:
            return True
        if need == "read" and f"{svc}:read" in sc:
            return True
    return False


def verdict(row: dict | None, now: datetime | None = None) -> str | None:
    """None = usable; else 'gone' | 'revoked' | 'expired'."""
    if row is None:
        return "gone"
    if row.get("revoked_at"):
        return "revoked"
    exp = row.get("expires_at")
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= (now or datetime.now(timezone.utc)):
            return "expired"
    return None


MESSAGES = {"gone": "Unknown API key.", "revoked": "This API key was revoked.", "expired": "This API key has expired.",
            "scope": "This API key does not allow that."}


async def lookup(raw: str) -> dict | None:
    """DB read by hash → the key's state (no secret). None when unknown."""
    async with pm.session() as s:
        k = (await s.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw)))).scalar_one_or_none()
    if not k:
        return None
    return {"id": k.id, "org_id": k.org_id, "name": k.name, "scopes": list(k.scopes or []), "role": k.role,
            "expires_at": k.expires_at, "revoked_at": k.revoked_at}


_touched: dict[str, float] = {}


async def touch(key_id: str) -> None:
    """Stamp last_used_at at most once a minute per key."""
    t = time.monotonic()
    if t - _touched.get(key_id, 0) < 60:
        return
    _touched[key_id] = t
    try:
        async with pm.session() as s:
            await s.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=pm.now_utc()))
            await s.commit()
    except Exception:                                        # noqa: BLE001
        pass


class KeyCache:
    """hash → state for a few seconds, so a busy integration is one DB read per TTL."""

    def __init__(self, loader=lookup, ttl: float = 10.0, clock=time.monotonic):
        self.loader, self.ttl, self.clock = loader, ttl, clock
        self._items: dict[str, tuple[float, dict | None]] = {}

    async def get(self, raw: str) -> dict | None:
        h = hash_key(raw)
        t = self.clock()
        hit = self._items.get(h)
        if hit and hit[0] > t:
            return hit[1]
        state = await self.loader(raw)
        if len(self._items) > 10000:
            self._items.clear()
        self._items[h] = (t + self.ttl, state)
        return state

    def forget(self) -> None:
        self._items.clear()
