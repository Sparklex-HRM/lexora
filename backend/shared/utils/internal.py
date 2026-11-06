"""Lexora HRM — the identity a SERVICE uses when it acts on its own (no user request).

A user's request carries the identity the gateway stamped from the JWT
(X-Org-Id / X-User-Id / X-Role / X-Employee-Id), and every service reads through
that identity: an employee sees only themself, a manager their team.

Some work is not done on behalf of a user and needs the whole organisation —
the daily last-working-day tick, applying salary revisions on their effective
date, a payroll run's background lookups. Those calls carry

    X-Internal-Auth: <token>

The token is a secret shared by the services only (env LEXORA_INTERNAL_TOKEN, or
derived from JWT_SECRET, which every service already holds). The gateway strips
X-Internal-Auth from anything a client sends (it is in SPOOFABLE) and never sets
it, so a browser can never claim it. "No user on the request" is NOT enough on its
own to read the whole org — the token is.
"""
from __future__ import annotations

import hashlib
import hmac
import os

HEADER = "x-internal-auth"


def _token() -> str:
    explicit = (os.getenv("LEXORA_INTERNAL_TOKEN") or "").strip()
    if explicit:
        return explicit
    secret = os.getenv("JWT_SECRET", "dev-only-insecure-key-change-me")
    return hmac.new(secret.encode(), b"lexora-internal-service-identity-v1", hashlib.sha256).hexdigest()


def internal_headers(org_id, service: str, role: str = "org_admin") -> dict:
    """Headers for a call a service makes on its own behalf for one org.
    `role` is what the receiving route's role checks see (the work is org-wide)."""
    return {"x-org-id": str(org_id), "x-role": role, "x-user-email": f"system@{service}",
            HEADER: _token()}


def is_internal(headers) -> bool:
    """True only for a request carrying the services' shared token."""
    got = (headers.get(HEADER) or "") if headers is not None else ""
    return bool(got) and hmac.compare_digest(got, _token())
