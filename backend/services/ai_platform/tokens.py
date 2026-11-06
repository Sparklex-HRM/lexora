"""AI access tokens — what an external AI client presents to the MCP server.

Format (shown ONCE):  lxai_<8 hex>_<32 url-safe chars>. Stored: prefix + SHA-256. Scoped
to one user of the org (the token can never see more than that person), scopes
read / act / admin, expiry (default 90 days), revocable, per-token rate limit.
"""
from __future__ import annotations

import hashlib
import secrets
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.ratelimit import make_limiter  # noqa: E402

from ai_models import AiAccessToken  # noqa: E402

PREFIX = "lxai_"
SCOPES = ("read", "act", "admin")
DEFAULT_DAYS = 90
MAX_DAYS = 365


def generate() -> tuple[str, str, str]:
    pre = secrets.token_hex(4)
    raw = f"{PREFIX}{pre}_{secrets.token_urlsafe(24)}"
    return raw, PREFIX + pre, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def looks_like(value: str | None) -> bool:
    v = (value or "").strip()
    return v.startswith(PREFIX) and 20 < len(v) < 120


def clean_scopes(scopes) -> list[str]:
    out = {str(s).strip().lower() for s in (scopes or []) if str(s).strip().lower() in SCOPES}
    out.add("read")                                          # act / admin always include read
    return [s for s in SCOPES if s in out]


def verdict(row: dict | None, now: datetime | None = None) -> str | None:
    """None = usable; else gone | revoked | expired."""
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


MESSAGES = {"gone": "Unknown AI access token.", "revoked": "This AI access token was revoked.", "expired": "This AI access token has expired."}


def _dict(k: AiAccessToken) -> dict:
    return {"id": k.id, "org_id": k.org_id, "name": k.name, "user_id": k.user_id, "user_email": k.user_email, "role": k.role,
            "employee_id": k.employee_id, "scopes": list(k.scopes or []), "prefix": k.prefix, "rate_per_min": k.rate_per_min, "calls": k.calls,
            "expires_at": k.expires_at, "revoked_at": k.revoked_at, "last_used_at": k.last_used_at, "created_by": k.created_by, "created_at": k.created_at}


def public(d: dict) -> dict:
    return {**{k: v for k, v in d.items() if k not in ("expires_at", "revoked_at", "last_used_at", "created_at")},
            "expires_at": pm.iso(d.get("expires_at")), "revoked_at": pm.iso(d.get("revoked_at")), "last_used_at": pm.iso(d.get("last_used_at")),
            "created_at": pm.iso(d.get("created_at")), "status": verdict(d) or "active"}


async def create(org_id: str, *, name: str, user_id: str, user_email: str | None, role: str, employee_id: str | None, scopes,
                 days: int | None, rate_per_min: int | None, created_by: str | None) -> tuple[str, dict]:
    raw, prefix, h = generate()
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    row = AiAccessToken(org_id=org_id, name=(name or "AI client")[:80], user_id=user_id, user_email=user_email, role=role or "employee",
                        employee_id=employee_id or None, scopes=clean_scopes(scopes), prefix=prefix, token_hash=h,
                        rate_per_min=max(1, min(int(rate_per_min or 60), 600)), expires_at=pm.now_utc() + timedelta(days=days), created_by=created_by)
    async with pm.session() as s:
        s.add(row)
        await s.commit()
        return raw, _dict(row)


async def lookup(raw: str) -> dict | None:
    async with pm.session() as s:
        k = (await s.execute(select(AiAccessToken).where(AiAccessToken.token_hash == hash_token(raw)))).scalar_one_or_none()
    return _dict(k) if k else None


async def list_for_org(org_id: str) -> list[dict]:
    async with pm.session() as s:
        rows = (await s.execute(select(AiAccessToken).where(AiAccessToken.org_id == org_id).order_by(AiAccessToken.created_at.desc()))).scalars().all()
    return [public(_dict(r)) for r in rows]


async def revoke(org_id: str, token_id: str) -> bool:
    async with pm.session() as s:
        k = (await s.execute(select(AiAccessToken).where(AiAccessToken.id == token_id, AiAccessToken.org_id == org_id))).scalar_one_or_none()
        if not k:
            return False
        if not k.revoked_at:
            k.revoked_at = pm.now_utc()
            await s.commit()
        return True


async def touch(token_id: str) -> None:
    try:
        async with pm.session() as s:
            await s.execute(update(AiAccessToken).where(AiAccessToken.id == token_id)
                            .values(last_used_at=pm.now_utc(), calls=AiAccessToken.calls + 1))
            await s.commit()
    except Exception:                                        # noqa: BLE001
        pass


_limiters: dict[int, object] = {}


def limiter_for(rate_per_min: int):
    """One shared limiter per rate (keyed by token id on hit) — Redis when configured."""
    r = max(1, int(rate_per_min or 60))
    if r not in _limiters:
        _limiters[r] = make_limiter(r, 60, name=f"ai_mcp_{r}")
    return _limiters[r]


async def authenticate(raw: str | None) -> tuple[dict | None, str | None]:
    """(token row, error message). Checks format, state, expiry and the per-token rate limit."""
    if not looks_like(raw):
        return None, "Send an AI access token: Authorization: Bearer lxai_…"
    row = await lookup(raw.strip())
    v = verdict(row)
    if v:
        return None, MESSAGES[v]
    wait = await limiter_for(row["rate_per_min"]).ahit(row["id"])
    if wait:
        return None, f"Rate limit: this token allows {row['rate_per_min']} calls a minute — retry in {wait}s."
    await touch(row["id"])
    return row, None


def headers_for(row: dict) -> dict:
    """Identity headers the tools forward — the token's user, never anything from the client."""
    return {"x-org-id": row["org_id"], "x-user-id": row["user_id"], "x-user-email": row.get("user_email") or f"aitoken:{row['name']}",
            "x-role": row.get("role") or "employee", "x-employee-id": row.get("employee_id") or ""}
