"""Auth service — account-security helpers shared by login, MFA and sessions.

* Sessions: every token issue creates a `user_sessions` row; the access token
  carries its id as `sid`. Revoking the row ends that session at the gateway
  (api-gateway/saas_gate.py) within its cache window.
* MFA challenge token: after a correct password, a login that needs MFA gets a
  short-lived token (10 min) signed with a key DERIVED from JWT_SECRET, so it can
  never be used as an access token (the gateway's decode with JWT_SECRET fails).
* Password history: last N bcrypt hashes per login.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.utils.config import settings

from password_guard import HISTORY_DEPTH
from saas_models import PasswordHistory, UserSession

CHALLENGE_MIN = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def client_ip(request) -> str:
    if request is None:
        return ""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def device_label(ua: str | None) -> str:
    """'Chrome on Windows' style label from a User-Agent (best effort)."""
    ua = ua or ""
    if not ua:
        return "Unknown device"
    browser = ("Edge" if "Edg/" in ua else "Opera" if "OPR/" in ua else "Chrome" if "Chrome/" in ua
               else "Firefox" if "Firefox/" in ua else "Safari" if "Safari/" in ua
               else "API client" if re.match(r"(python|curl|httpx|okhttp|axios)", ua, re.I) else "Browser")
    osn = ("Windows" if "Windows" in ua else "Android" if "Android" in ua
           else "iOS" if re.search(r"iPhone|iPad", ua) else "macOS" if "Mac OS" in ua
           else "Linux" if "Linux" in ua else "")
    return f"{browser} on {osn}" if osn else browser


# ── sessions ─────────────────────────────────────────────────────────────────

async def open_session(db: AsyncSession, user, request=None, method: str = "password") -> str:
    ua = (request.headers.get("user-agent") if request is not None else "") or ""
    s = UserSession(user_id=user.id, org_id=user.org_id, method=method, ip=client_ip(request),
                    user_agent=ua[:300], device=device_label(ua),
                    expires_at=_now() + timedelta(days=settings.REFRESH_EXPIRE_DAYS))
    db.add(s)
    await db.flush()
    return str(s.id)


async def revoke_sessions(db: AsyncSession, user_id, *, except_sid: str | None = None,
                          by: str | None = None, reason: str = "revoked") -> int:
    rows = (await db.execute(select(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)))).scalars().all()
    n = 0
    for s in rows:
        if except_sid and str(s.id) == except_sid:
            continue
        s.revoked_at, s.revoked_by, s.revoked_reason = _now(), by, reason
        n += 1
    return n


# ── MFA challenge token ──────────────────────────────────────────────────────

def _challenge_key() -> str:
    return hmac.new(settings.JWT_SECRET.encode(), b"lexora-mfa-challenge-v1", hashlib.sha256).hexdigest()


def make_challenge(user, purpose: str, method: str = "password") -> str:
    now = _now()
    return jwt.encode({"sub": str(user.id), "org_id": str(user.org_id), "purpose": purpose, "method": method,
                       "type": "mfa_challenge", "iat": now, "exp": now + timedelta(minutes=CHALLENGE_MIN),
                       "jti": uuid.uuid4().hex}, _challenge_key(), algorithm="HS256")


def read_challenge(token: str, purposes: tuple[str, ...]) -> dict:
    """Claims of a valid challenge token for one of `purposes`, else ValueError."""
    try:
        c = jwt.decode(token or "", _challenge_key(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("This sign-in step has expired. Sign in again.") from exc
    if c.get("type") != "mfa_challenge" or c.get("purpose") not in purposes:
        raise ValueError("This sign-in step has expired. Sign in again.")
    return c


# ── password history ─────────────────────────────────────────────────────────

async def history_hashes(db: AsyncSession, user) -> list[str]:
    rows = (await db.execute(select(PasswordHistory.hashed_password).where(
        PasswordHistory.user_id == user.id).order_by(PasswordHistory.created_at.desc())
        .limit(HISTORY_DEPTH))).scalars().all()
    return [user.hashed_password] + list(rows)


async def remember_password(db: AsyncSession, user_id, hashed: str) -> None:
    db.add(PasswordHistory(user_id=user_id, hashed_password=hashed))
