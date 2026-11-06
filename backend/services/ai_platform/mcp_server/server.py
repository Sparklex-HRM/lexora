"""The in-process MCP server: the tool registry exposed through the official ``mcp`` SDK.

Identity comes from a context variable set by the transport layer (``http.py`` from the
AI access token; tests directly) — never from anything the MCP client sends. Besides the
registry's read / act tools it adds ``confirm_action`` and ``cancel_action`` so the
two-step confirmation works over MCP exactly as in the chat.
"""
from __future__ import annotations

import contextvars
import json
import sys
from typing import Any

sys.path.append("/app")

import actions as ACT  # noqa: E402
import tools_registry as TR  # noqa: E402

current_ctx: contextvars.ContextVar[TR.Ctx | None] = contextvars.ContextVar("lexora_mcp_ctx", default=None)

SERVER_NAME = "lexora-hrm"
SERVER_VERSION = "1.0.0"

CONFIRM_SCHEMA = {"type": "object", "properties": {"action_id": {"type": "string", "description": "id returned by an action tool"}},
                  "required": ["action_id"], "additionalProperties": False}


def meta_tools(scopes: set) -> list[dict]:
    if "act" not in scopes:
        return []
    return [{"name": "confirm_action", "description": "Record ONE explicit confirmation from the person for a proposed action. Only call it after the "
             "person has read the card and explicitly said to go ahead. High-risk actions need this called twice (ask again before the second call).",
             "inputSchema": CONFIRM_SCHEMA, "readOnlyHint": False, "destructiveHint": True, "scope": "act"},
            {"name": "cancel_action", "description": "Cancel a proposed action — nothing is submitted.", "inputSchema": CONFIRM_SCHEMA,
             "readOnlyHint": False, "destructiveHint": False, "scope": "act"}]


def list_tool_dicts(ctx: TR.Ctx) -> list[dict]:
    return TR.mcp_tool_dicts(TR.visible_tools(ctx.role, set(ctx.scopes))) + meta_tools(set(ctx.scopes))


async def call_tool_dict(ctx: TR.Ctx, name: str, args: dict | None) -> dict:
    """Run one tool for ``ctx`` and return a JSON-able dict (proposals are persisted here)."""
    if name in ("confirm_action", "cancel_action"):
        if "act" not in ctx.scopes:
            return {"error": "This access token has no 'act' scope."}
        aid = str((args or {}).get("action_id") or "")
        try:
            return await (ACT.confirm if name == "confirm_action" else ACT.cancel)(ctx, aid)
        except ACT.ActionError as e:
            return {"error": e.message}
    ctx.proposals = []
    out = await TR.call(ctx, name, args or {})
    if ctx.proposals:
        cards = await ACT.persist(ctx, ctx.proposals)
        out["pending_actions"] = cards
        out["next_step"] = ("Show the person the card(s). When they explicitly confirm, call confirm_action with the id"
                            + (" — twice for a high-risk action" if any(c["card"].get("risk") == "high" for c in cards) else "") + ".")
    return out


def build_server():
    """A ``mcp.server.lowlevel.Server`` wired to the registry. Imported lazily so the
    service still boots when the mcp package is missing (then only the chat works)."""
    from mcp.server.lowlevel import Server
    import mcp.types as T

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[T.Tool]:
        ctx = current_ctx.get()
        if ctx is None:
            return []
        return [T.Tool(name=d["name"], description=d["description"], inputSchema=d["inputSchema"],
                       annotations=T.ToolAnnotations(readOnlyHint=d["readOnlyHint"], destructiveHint=d["destructiveHint"]))
                for d in list_tool_dicts(ctx)]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[T.TextContent]:
        ctx = current_ctx.get()
        if ctx is None:
            out = {"error": "Not authenticated."}
        else:
            out = await call_tool_dict(ctx, name, arguments)
        return [T.TextContent(type="text", text=json.dumps(out, default=str))]

    return server
