"""Calendar adapters (D2 interview scheduling) — the common shape.

Two providers, same contract, both written from the public docs cited in each file:

    adapter = calendar_adapter("google" | "microsoft")
    adapter.configured()                     -> bool   (OAuth app client id + secret in the env)
    adapter.auth_url(redirect_uri, state)    -> str    (where the organiser's browser goes)
    await adapter.exchange_code(code, redirect_uri)   -> TokenSet
    await adapter.refresh(refresh_token)              -> TokenSet
    await adapter.create_event(access_token, Event)   -> EventResult
    await adapter.update_event(access_token, event_id, Event) -> EventResult
    await adapter.cancel_event(access_token, event_id)        -> EventResult

HONESTY: ``configured()`` is false until the platform operator sets the OAuth app
credentials; nothing pretends to sync. A Meet / Teams link is reported ONLY when the
provider's response carried one. Every HTTP call goes through ``resilience.call`` (timeout,
retry on 429 / 5xx, masked audit row); tests inject ``resilience.TRANSPORT``.

Environment (platform-level OAuth apps; the per-org part is the connected account's
encrypted tokens, stored by the employee service):
    LEXORA_GOOGLE_CLIENT_ID / LEXORA_GOOGLE_CLIENT_SECRET
    LEXORA_MS_CLIENT_ID / LEXORA_MS_CLIENT_SECRET / LEXORA_MS_TENANT (default "common")
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from shared.utils.providers import resilience


def env(name: str) -> str:
    return (os.getenv(name) or "").strip()


@dataclass
class Event:
    title: str
    start: datetime                      # tz-aware
    end: datetime
    description: str = ""
    attendees: list[dict] = field(default_factory=list)   # [{email, name}]
    timezone: str = "America/Toronto"
    online_meeting: bool = True          # ask for a Meet / Teams link
    location: str = ""
    request_id: str = ""                 # idempotency / conference request id


@dataclass
class TokenSet:
    ok: bool
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None
    account_email: str | None = None
    error: str | None = None


@dataclass
class EventResult:
    ok: bool
    event_id: str | None = None
    join_url: str | None = None          # Meet / Teams link, only when the API returned one
    html_link: str | None = None
    error: str | None = None
    status: int | None = None


def expires_in_to_at(expires_in, now: datetime | None = None) -> datetime:
    try:
        secs = int(expires_in or 3600)
    except (TypeError, ValueError):
        secs = 3600
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=max(secs - 60, 60))


def token_is_fresh(expires_at: datetime | None, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > now + timedelta(seconds=30)


class CalendarAdapter:
    key: str = ""
    label: str = ""
    docs_url: str = ""
    oauth_docs_url: str = ""
    env_vars: tuple = ()

    def configured(self) -> bool:
        return all(env(v) for v in self.env_vars[:2])

    def status(self) -> dict:
        if self.configured():
            return {"state": "configured", "message": f"{self.label}: OAuth app set. An HR head can connect an organiser calendar."}
        return {"state": "not_configured",
                "message": f"{self.label}: not configured. Set {' and '.join(self.env_vars[:2])} on the server "
                           f"(the OAuth app from {self.oauth_docs_url})."}

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "docs_url": self.docs_url, "oauth_docs_url": self.oauth_docs_url,
                "env_vars": list(self.env_vars), **self.status()}

    async def _call(self, org_id, operation, method, url, **kw) -> resilience.CallResult:
        return await resilience.call(org_id, f"calendar_{self.key}", operation, method, url, timeout=12.0, **kw)

    # subclasses
    def auth_url(self, redirect_uri: str, state: str) -> str:          # pragma: no cover - abstract
        raise NotImplementedError

    async def exchange_code(self, code: str, redirect_uri: str, org_id: str = "") -> TokenSet:  # pragma: no cover
        raise NotImplementedError

    async def refresh(self, refresh_token: str, org_id: str = "") -> TokenSet:  # pragma: no cover
        raise NotImplementedError

    async def create_event(self, access_token: str, ev: Event, org_id: str = "") -> EventResult:  # pragma: no cover
        raise NotImplementedError

    async def update_event(self, access_token: str, event_id: str, ev: Event, org_id: str = "") -> EventResult:  # pragma: no cover
        raise NotImplementedError

    async def cancel_event(self, access_token: str, event_id: str, org_id: str = "") -> EventResult:  # pragma: no cover
        raise NotImplementedError


def calendar_adapter(provider: str) -> CalendarAdapter | None:
    if provider == "google":
        from shared.utils.providers.calendar_google import GoogleCalendar
        return GoogleCalendar()
    if provider == "microsoft":
        from shared.utils.providers.calendar_microsoft import MicrosoftCalendar
        return MicrosoftCalendar()
    return None


def all_calendar_adapters() -> list[CalendarAdapter]:
    return [a for a in (calendar_adapter("google"), calendar_adapter("microsoft")) if a]
