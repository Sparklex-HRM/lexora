"""The thinnest possible language-model client for the copilot.

* Key: ``ANTHROPIC_API_KEY`` from the environment only — nothing hard-coded.
* Model: ``COPILOT_MODEL``, else ``LEXI_MODEL``, else the Lexi default.
* ``configured()`` is the single honest switch. With no key the copilot answers
  "AI assistant is not configured" and the rules-based tools still work.
* The caller passes ALREADY-SHIELDED text. This module never sees raw values.
"""
import os
import sys

sys.path.append("/app")

try:
    from lexi import LEXI_MODEL, cost_usd  # noqa: E402
except Exception:                                            # noqa: BLE001  (tests outside the service dir)
    LEXI_MODEL = "claude-opus-5"

    def cost_usd(model, i, o, cache_read=0, cache_write=0):  # pragma: no cover
        return 0.0

import claude_client as _cc  # noqa: E402  (prompt-caching helpers — the one place requests are shaped)

NOT_CONFIGURED = ("AI assistant is not configured on this installation (no ANTHROPIC_API_KEY). "
                  "I can still do the rules-based parts: leave balance, payslips, pending approvals, "
                  "applying for leave and explaining payslip lines.")


def model_name() -> str:
    return os.getenv("COPILOT_MODEL") or os.getenv("LEXI_MODEL") or LEXI_MODEL


def configured() -> bool:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:                                        # noqa: BLE001
        return False


class LlmError(RuntimeError):
    pass


async def complete(*, system: str, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int = 900, system_dynamic: str | None = None, auto_cache: bool = True) -> dict:
    """One model call. Returns {content, stop_reason, input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens, model, cost_usd}. ``system`` = static instructions (cached block, with the tools
    before it); ``system_dynamic`` = per-request tail after the breakpoint; ``auto_cache`` = top-level
    automatic caching for the tool loop. ``content`` is the SDK's block list. Raises LlmError on failure."""
    if not configured():
        raise LlmError("not_configured")
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic()
    model = model_name()
    try:
        kwargs = dict(model=model, max_tokens=max_tokens, system=_cc.system_blocks(system, system_dynamic), messages=messages)
        if tools:
            kwargs["tools"] = tools
        if auto_cache:
            kwargs["extra_body"] = _cc.with_auto_cache(None)
        resp = await client.messages.create(**kwargs)
    except Exception as e:                                   # noqa: BLE001
        raise LlmError(f"{type(e).__name__}: {str(e)[:200]}")
    u = _cc.usage_fields(resp.usage)
    return {"content": resp.content, "stop_reason": resp.stop_reason, **u, "model": model,
            "cost_usd": cost_usd(model, u["input_tokens"], u["output_tokens"], u["cache_read_tokens"], u["cache_write_tokens"])}


def text_of(content) -> str:
    return "".join(getattr(b, "text", "") for b in (content or []) if getattr(b, "type", "") == "text").strip()


def tool_uses(content) -> list:
    return [b for b in (content or []) if getattr(b, "type", "") == "tool_use"]
