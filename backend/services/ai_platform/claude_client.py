"""The one place that talks to Claude (Anthropic Python SDK).

* ``client_for(api_key)`` builds an ``AsyncAnthropic`` — or returns the FAKE client a test
  installed with ``set_fake(...)``, so no test ever makes a paid call.
* ``complete()`` is one Messages call: adaptive thinking (the model default — the
  ``thinking`` parameter is deliberately omitted), ``output_config.effort`` from the org
  setting, server-side refusal fallbacks on by default (beta ``server-side-fallback-2026-07-01``,
  ``fallbacks: "default"``), typed error chain → ``LlmError`` with a short reason.
* The caller passes ALREADY-SHIELDED text. This module never sees raw values.
* Prompt caching (2026-10-24) — the helpers below are the ONE place every Lexora HRM call
  site (assistant, internal completions, copilot ``llm.py``, Lexi ``lexi.py``)
  builds its cacheable request and reads cache usage:
    - ``system`` = the STATIC instructions -> one block carrying ``cache_control``
      (caches tools + system: the prefix order is tools -> system -> messages);
    - ``system_dynamic`` = per-request text (org name, today's date, role) -> an
      uncached block AFTER the breakpoint, so it never invalidates the prefix;
    - ``auto_cache=True`` (multi-turn tool loops) = top-level automatic caching,
      sent through ``extra_body`` so it works on every SDK version: the breakpoint
      moves to the last message block, each hop reads the previous hop's prefix;
    - usage -> ``cache_read_tokens`` / ``cache_write_tokens`` (priced in metering:
      read 0.1x input, write 1.25x for the 5-minute TTL, 2x for 1 hour).
  Below the model's minimum prefix (512 tokens on the 5.x models) nothing is cached
  and nothing extra is charged. Shielded text only — never raw values in a block.
"""
from __future__ import annotations

import json
from typing import Any

_fake: Any = None
FALLBACK_BETA = "server-side-fallback-2026-07-01"

TTL_5M, TTL_1H = "5m", "1h"
# Minimum cacheable prefix (tokens) by model family; below it the API caches nothing.
MIN_CACHEABLE = (("claude-haiku-4-5", 4096), ("claude-opus-4-5", 4096), ("claude-opus-4-6", 4096),
                 ("claude-opus-4-7", 2048), ("claude-opus-4-8", 1024), ("claude-sonnet-4", 1024), ("claude-", 512))


def cache_control(ttl: str | None = None) -> dict:
    if ttl in (None, TTL_5M):
        return {"type": "ephemeral"}
    if ttl == TTL_1H:
        return {"type": "ephemeral", "ttl": TTL_1H}
    raise ValueError(f"unsupported cache ttl {ttl!r}")


def system_blocks(static: str | None, dynamic: str | None = None, ttl: str | None = None) -> list[dict]:
    """Static instructions (cached) then the per-request tail (not cached)."""
    blocks: list[dict] = []
    if static:
        blocks.append({"type": "text", "text": static, "cache_control": cache_control(ttl)})
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})
    return blocks


def stable_json(obj: Any) -> str:
    """Sorted-key JSON for anything placed in a cached prefix."""
    return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)


def with_auto_cache(extra_body: dict | None, ttl: str | None = None) -> dict:
    """extra_body plus top-level automatic caching (multi-turn)."""
    out = dict(extra_body or {})
    out["cache_control"] = cache_control(ttl)
    return out


def usage_fields(usage: Any) -> dict:
    def g(name: str) -> int:
        try:
            return int(getattr(usage, name, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return {"input_tokens": g("input_tokens"), "output_tokens": g("output_tokens"),
            "cache_read_tokens": g("cache_read_input_tokens"), "cache_write_tokens": g("cache_creation_input_tokens")}


def add_usage(total: dict, res: dict) -> dict:
    for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        total[k] = int(total.get(k) or 0) + int(res.get(k) or 0)
    return total


def min_cacheable_tokens(model: str | None) -> int:
    m = (model or "").lower()
    return next((n for p, n in MIN_CACHEABLE if m.startswith(p)), 4096)


def set_fake(client: Any) -> None:
    """Tests install a stand-in exposing ``beta.messages.create(**kwargs)``."""
    global _fake
    _fake = client


def client_for(api_key: str | None):
    if _fake is not None:
        return _fake
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=api_key)


class LlmError(RuntimeError):
    """kind: not_configured | auth | rate_limit | bad_request | server | network | refusal | other"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail = kind, detail


def _classify(e: Exception) -> LlmError:
    try:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError):
            return LlmError("auth", "the API key was rejected")
        if isinstance(e, anthropic.PermissionDeniedError):
            return LlmError("auth", "the API key lacks permission")
        if isinstance(e, anthropic.RateLimitError):
            return LlmError("rate_limit", "the provider rate-limited the call")
        if isinstance(e, anthropic.BadRequestError):
            return LlmError("bad_request", str(getattr(e, "message", e))[:200])
        if isinstance(e, anthropic.APIStatusError):
            return LlmError("server" if e.status_code >= 500 else "other", f"HTTP {e.status_code}")
        if isinstance(e, anthropic.APIConnectionError):
            return LlmError("network", "could not reach the provider")
    except Exception:                                        # noqa: BLE001  (SDK missing in tests)
        pass
    return LlmError("other", f"{type(e).__name__}: {str(e)[:160]}")


async def complete(client, *, model: str, system: str, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int = 4000, effort: str = "medium", system_dynamic: str | None = None,
                   auto_cache: bool = False, cache: bool = True, ttl: str | None = None) -> dict:
    """One call. Returns {content (SDK blocks), stop_reason, input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens, model, refusal}. ``system`` is the STATIC part (cached unless ``cache=False``);
    ``system_dynamic`` goes after the breakpoint; ``auto_cache`` adds top-level automatic caching."""
    extra = {"fallbacks": "default"}                         # extra_body: works on SDKs that predate the field
    if auto_cache and cache:
        extra = with_auto_cache(extra, None)
    kwargs: dict = dict(model=model, max_tokens=max_tokens,
                        system=system_blocks(system, system_dynamic, ttl) if cache else
                        "\n".join(x for x in (system, system_dynamic) if x),
                        messages=messages,
                        output_config={"effort": effort if effort in ("low", "medium", "high") else "medium"},
                        betas=[FALLBACK_BETA], extra_body=extra)
    if tools:
        kwargs["tools"] = tools
    try:
        resp = await client.beta.messages.create(**kwargs)
    except Exception as e:                                   # noqa: BLE001 — classified below
        raise _classify(e)
    usage = getattr(resp, "usage", None)
    out = {"content": list(getattr(resp, "content", []) or []), "stop_reason": getattr(resp, "stop_reason", None),
           **usage_fields(usage), "model": getattr(resp, "model", None) or model, "refusal": None}
    if out["stop_reason"] == "refusal":
        sd = getattr(resp, "stop_details", None)
        out["refusal"] = {"category": getattr(sd, "category", None), "explanation": getattr(sd, "explanation", None)}
    return out


def text_of(content) -> str:
    return "".join(getattr(b, "text", "") for b in (content or []) if getattr(b, "type", "") == "text").strip()


def tool_uses(content) -> list:
    return [b for b in (content or []) if getattr(b, "type", "") == "tool_use"]


async def ping(client, model: str) -> dict:
    """Cheapest possible key test: one tiny call. Returns {ok, model, input_tokens, output_tokens} or raises LlmError."""
    r = await complete(client, model=model, system="Reply with the single word OK.", messages=[{"role": "user", "content": "ping"}],
                       max_tokens=16, effort="low", cache=False)        # one-off key test: nothing to reuse
    return {"ok": True, "model": r["model"], "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"]}
