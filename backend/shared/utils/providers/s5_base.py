"""Common pieces of the S5 live adapters.

``LiveProvider`` adds to the G3 ``Provider``:
  * ``docs_url``  — the public API documentation the adapter was written from;
  * environment   — every status says ``sandbox`` or ``live`` (or not configured);
  * env fallback  — a platform operator may set the credentials once in the
    environment (``env`` maps field → variable). An org's own saved value wins.
    Env values are read at call time and are never written to the database or logs.

Honesty: ``status`` is "configured" only when every required field is present;
it never claims a call succeeded — that is what ``test`` (a real request) is for.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

from shared.utils.providers.base import Provider


def env_value(name: str) -> str:
    return (os.getenv(name) or "").strip()


class LiveProvider(Provider):
    docs_url: str = ""
    env: dict[str, str] = {}                 # field key → environment variable (platform default)

    def merged(self, mode: str, cfg: dict | None) -> dict:
        out = dict(cfg or {})
        for k, var in self.env.items():
            if not out.get(k) and env_value(var):
                out[k] = env_value(var)
        return out

    def environment(self, mode: str, cfg: dict) -> str | None:
        """sandbox | live for this mode/config, or None when the mode is not a live API."""
        if mode in ("sandbox", "live"):
            return mode
        return None

    def missing(self, mode: str, cfg: dict) -> list[str]:
        return super().missing(mode, self.merged(mode, cfg))

    async def status(self, mode: str, cfg: dict, enabled: bool) -> dict:
        if not enabled:
            return {"state": "not_configured", "message": "Switched off.", "environment": None}
        miss = self.missing(mode, cfg)
        if miss:
            return {"state": "not_configured", "message": "Not configured: missing " + ", ".join(miss), "environment": None}
        env = self.environment(mode, self.merged(mode, cfg))
        label = {"sandbox": "Sandbox — test calls only, nothing real happens",
                 "live": "Live — real calls, real effects"}.get(env or "", "Configured")
        return {"state": "configured", "message": f"{label}. Use Test to make a real call.", "environment": env}

    def describe(self) -> dict:
        d = super().describe()
        d["docs_url"] = self.docs_url
        d["env_vars"] = sorted(set(self.env.values()))
        return d


def webhook_token(org_id: str, provider: str) -> str:
    """Unguessable per-org path token for providers whose webhooks carry no signature
    (provider callbacks). Derived from the internal service secret — nothing to store."""
    from shared.utils.internal import _token
    return hmac.new(_token().encode(), f"webhook|{provider}|{org_id}".encode(), hashlib.sha256).hexdigest()[:40]


def check_webhook_token(org_id: str, provider: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(webhook_token(org_id, provider), token)


def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z ]", " ", (s or "").lower())
    return " ".join(w for w in s.split() if w not in ("mr", "mrs", "ms", "dr", "shri", "smt", "kumari"))


def name_match(expected: str, got: str) -> str:
    """match | mismatch | unknown — exact token-set match after normalising (no fuzzy guessing:
    initials or a missing middle name read as MISMATCH and go to a person to decide)."""
    a, b = norm_name(expected), norm_name(got)
    if not a or not b:
        return "unknown"
    return "match" if sorted(a.split()) == sorted(b.split()) else "mismatch"
