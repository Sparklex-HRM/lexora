"""Hiring G6 — the AI provider interface.

    provider = pick(settings)          RulesProvider (always) or PlatformProvider (AI platform)
    await provider.complete(org, feature, system, prompt, max_tokens) -> (text | None, note)

PlatformProvider calls the AI platform's internal endpoint (/ai/internal/complete), which resolves
the org / platform key, enforces the org's AI switch and monthly cap, runs the de-identify Shield
and meters the call. No key (the local default: ANTHROPIC_API_KEY is blank) → (None, note) and the
caller keeps its deterministic result. Tests install a fake with set_provider().
"""
from __future__ import annotations

import sys
from typing import Protocol

import httpx

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.internal import internal_headers  # noqa: E402


class Provider(Protocol):
    name: str

    async def complete(self, org: str, feature: str, system: str, prompt: str, max_tokens: int) -> tuple[str | None, str | None]: ...


class RulesProvider:
    name = "rules"

    async def complete(self, org, feature, system, prompt, max_tokens):
        return None, "AI assist is off for hiring; deterministic rules only."


class PlatformProvider:
    name = "ai_platform"

    async def complete(self, org, feature, system, prompt, max_tokens):
        try:
            async with httpx.AsyncClient(timeout=45.0) as c:
                r = await c.post(f"{settings.AI_PLATFORM_URL}/ai/internal/complete",
                                 headers=internal_headers(org, "employee"),
                                 json={"org_id": str(org), "feature": feature, "system": system, "prompt": prompt, "max_tokens": max_tokens})
            if r.status_code != 200:
                return None, "The AI service did not answer; deterministic rules were used."
            d = r.json()
        except Exception:                                    # noqa: BLE001 — AI is optional; never break hiring
            return None, "The AI service could not be reached; deterministic rules were used."
        if not d.get("ok"):
            return None, d.get("message") or "AI not available; deterministic rules were used."
        return d.get("text") or "", None


_override: Provider | None = None


def set_provider(p: Provider | None) -> None:
    global _override
    _override = p


def pick(hiring_settings: dict) -> Provider:
    if _override is not None:
        return _override
    return PlatformProvider() if hiring_settings.get("ai_assist", True) else RulesProvider()
