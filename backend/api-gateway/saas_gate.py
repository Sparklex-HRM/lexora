"""API gateway — stream S4 checks (SaaS + account security), one call from _proxy.

1. Ended session: an access token carries `sid` (its user_sessions row). If that
   row is revoked (sign-out, "sign out other devices", admin MFA reset, password
   change) the request is refused 401. Tokens minted before S4 have no sid and
   are not affected. One PK read, cached LEXORA_SESSION_TTL_SEC (default 2 s).
2. Read-only subscription: when the org's trial ended / subscription was
   cancelled / a payment stayed overdue past the grace period, WRITES are refused
   with 402 `subscription_read_only` — except sign-in / account / billing (/auth/*),
   privacy rights (PIPEDA) and statutory downloads. Every GET keeps working,
   so payroll history and statutory files are never locked away. The rule is the
   same as services/auth/subscription.access_mode (both unit-tested).
3. Stamps X-Session-Id (the client can never send it — it is scrubbed).

The session check FAILS CLOSED (503) when the session store cannot be read — decision
2026-10-21, same as revocation.py; a failed read is not cached. The read-only subscription
check still fails open (it is a billing state, not a security control).
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone

import jwt
from fastapi.responses import JSONResponse

sys.path.append("/app")
from shared.utils.security import decode_token  # noqa: E402

PUBLIC_PATHS = ("/auth/signup", "/auth/mfa/challenge/", "/auth/billing/webhooks/")
OWNED_HEADERS = ("x-session-id",)

SESSION_TTL = min(float(os.getenv("LEXORA_SESSION_TTL_SEC", "2")), 10.0)   # was 10 s before 2026-10-21
ORG_TTL = float(os.getenv("LEXORA_SUBSCRIPTION_TTL_SEC", "30"))

READ_ONLY_STATUSES = {"cancelled", "expired"}
TIMED_STATUSES = {"trialing", "past_due"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Writes that stay open in read-only mode.
WRITE_ALLOW_PREFIXES = ("/auth/", "/notifications/")
WRITE_ALLOW_WORDS = ("export", "download", "pdf", "print", "preview", "report", "t4", "rl1", "roe", "payslip")


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_only(status: str | None, access_until, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    st = (status or "active").lower()
    if st in READ_ONLY_STATUSES:
        return True
    if st in TIMED_STATUSES:
        au = _aware(access_until)
        return au is not None and now >= au
    return False


def write_allowed_when_read_only(method: str, path: str) -> bool:
    if method.upper() in SAFE_METHODS:
        return True
    p = path.lower()
    if p.startswith(WRITE_ALLOW_PREFIXES):
        return True
    return any(w in seg for seg in p.split("/") for w in WRITE_ALLOW_WORDS)


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl, self.items = ttl, {}

    def get(self, key):
        hit = self.items.get(key)
        if hit and hit[0] > time.monotonic():
            return True, hit[1]
        return False, None

    def put(self, key, value):
        if len(self.items) > 50000:
            self.items.clear()
        self.items[key] = (time.monotonic() + self.ttl, value)


_sessions = _TTLCache(SESSION_TTL)
_orgs = _TTLCache(ORG_TTL)
_sm = None


def _session_maker():
    global _sm
    if _sm is None:
        from shared.utils.db import make_engine, make_sessionmaker
        _sm = make_sessionmaker(make_engine())
    return _sm


async def _load_session(sid: str):
    from sqlalchemy import text
    async with _session_maker()() as s:
        row = (await s.execute(text("SELECT revoked_at, expires_at FROM user_sessions WHERE id = :i"),
                               {"i": uuid.UUID(sid)})).first()
    return None if row is None else {"revoked": row[0] is not None}


async def _load_org(org_id: str):
    from sqlalchemy import text
    async with _session_maker()() as s:
        row = (await s.execute(text("SELECT subscription_status, access_until FROM organizations WHERE id = :i"),
                               {"i": uuid.UUID(org_id)})).first()
    return None if row is None else {"status": row[0], "access_until": row[1]}


# Loaders are module attributes so tests can swap them.
load_session = _load_session
load_org = _load_org


def _claims(request) -> dict:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return {}
    try:
        return decode_token(auth.split(" ", 1)[1])
    except jwt.PyJWTError:
        return {}


async def check(request, service: str, path: str) -> JSONResponse | None:
    """None = go on; else the response to return. Also sets request.state.session_id."""
    request.state.session_id = None
    if not getattr(request.state, "user_id", None):
        return None                                   # public path or API key (no login session)
    sid = _claims(request).get("sid")
    if sid:
        hit, st = _sessions.get(sid)
        if not hit:
            try:
                st = await load_session(sid)
            except Exception:  # noqa: BLE001 — fail CLOSED, and do not remember the failure
                return JSONResponse({"detail": "Could not check your session. Try again in a moment."},
                                    status_code=503)
            _sessions.put(sid, st)
        if st is not None and st.get("revoked"):
            return JSONResponse({"detail": "Your session has ended. Sign in again."}, status_code=401)
        request.state.session_id = sid
    role = getattr(request.state, "role", "") or ""
    org_id = getattr(request.state, "org_id", None)
    full_path = f"/{service}" + (f"/{path}" if path else "")
    if role == "superadmin" or not org_id or write_allowed_when_read_only(request.method, full_path):
        return None
    hit, org = _orgs.get(org_id)
    if not hit:
        try:
            org = await load_org(org_id)
        except Exception:
            org = None                                # fail open
        _orgs.put(org_id, org)
    if org and read_only(org.get("status"), org.get("access_until")):
        return JSONResponse({"detail": "subscription_read_only",
                             "message": "Your Lexora HRM subscription is in read-only mode. You can view and "
                                        "download everything; choose a plan in Plan & billing to add or change data."},
                            status_code=402)
    return None


def stamp(request) -> dict:
    sid = getattr(request.state, "session_id", None)
    return {"X-Session-Id": sid} if sid else {}


def forget(org_id: str | None = None, sid: str | None = None):
    if org_id:
        _orgs.items.pop(org_id, None)
    if sid:
        _sessions.items.pop(sid, None)
