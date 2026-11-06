"""Lexora HRM MCP over STDIO — the one file a laptop needs.

Claude Desktop / Claude Code / any stdio MCP client launches this script; it speaks MCP on
stdin/stdout and forwards every tool call to the Lexora HRM server's token-guarded JSON
endpoints (``/ai/mcp/tools`` and ``/ai/mcp/call``) with the AI access token.

    LEXORA_URL=https://hrm.example.com/api  LEXORA_AI_TOKEN=lxai_...  python stdio_bridge.py

Only the standard ``mcp`` package and ``httpx`` are needed on the laptop. Nothing is cached
on disk; the token lives in the environment the client sets. ``LEXORA_MCP_BACKEND=fake`` serves
two demo tools without a server (used by the unit test and for a dry run).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Any

import httpx


class Backend:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    async def tools(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{self.url}/ai/mcp/tools", headers=self._h())
            r.raise_for_status()
            return r.json().get("tools") or []

    async def call(self, name: str, arguments: dict | None) -> dict:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{self.url}/ai/mcp/call", headers=self._h(), json={"name": name, "arguments": arguments or {}})
            if r.status_code != 200:
                try:
                    return {"error": r.json().get("detail") or f"HTTP {r.status_code}"}
                except ValueError:
                    return {"error": f"HTTP {r.status_code}"}
            return r.json()


class FakeBackend:
    """Two tools, no network — for tests and `--dry-run`."""
    TOOLS = [{"name": "leave_balance", "description": "demo", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
              "readOnlyHint": True, "destructiveHint": False},
             {"name": "apply_leave", "description": "demo (proposes)", "inputSchema": {"type": "object", "properties": {"leave_type": {"type": "string"},
              "start_date": {"type": "string"}}, "required": ["leave_type", "start_date"], "additionalProperties": False}, "readOnlyHint": False, "destructiveHint": False}]

    async def tools(self):
        return self.TOOLS

    async def call(self, name, arguments):
        if name == "leave_balance":
            return {"balances": [{"leave_type": "Casual", "remaining": 7}], "source": {"label": "demo"}}
        if name == "apply_leave":
            return {"proposed": True, "pending_actions": [{"id": "demo-1", "card": {"what": f"Apply {arguments.get('leave_type')} on {arguments.get('start_date')}",
                                                                                   "requires_confirmations": 1}}]}
        return {"error": f"unknown tool {name}"}


def make_backend():
    if os.getenv("LEXORA_MCP_BACKEND", "").lower() == "fake":
        return FakeBackend()
    url, token = os.getenv("LEXORA_URL", "").strip(), os.getenv("LEXORA_AI_TOKEN", "").strip()
    if not url or not token:
        sys.stderr.write("Set LEXORA_URL (e.g. https://hrm.example.com/api) and LEXORA_AI_TOKEN (lxai_...)\n")
        sys.exit(2)
    return Backend(url, token)


async def call_tool_safely(backend, name: str, arguments: dict | None) -> dict:
    """One tool call. An unexpected error never reaches the MCP client as text (it could carry
    URLs, tokens or stack details): the real error goes to the stderr log with a short ref."""
    try:
        return await backend.call(name, arguments or {})
    except Exception:                                        # noqa: BLE001
        ref = uuid.uuid4().hex[:8].upper()
        logging.getLogger("lexora.mcp.bridge").exception("tool %s failed (ref %s)", name, ref)
        return {"error": "The tool call failed. Give this reference to support.", "ref": ref}


async def serve(backend) -> None:
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as T

    server = Server("lexora-hrm")

    @server.list_tools()
    async def _list() -> list[T.Tool]:
        return [T.Tool(name=d["name"], description=d.get("description") or "", inputSchema=d.get("inputSchema") or {"type": "object", "properties": {}},
                       annotations=T.ToolAnnotations(readOnlyHint=bool(d.get("readOnlyHint", True)), destructiveHint=bool(d.get("destructiveHint", False))))
                for d in await backend.tools()]

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any] | None) -> list[T.TextContent]:
        out = await call_tool_safely(backend, name, arguments)
        return [T.TextContent(type="text", text=json.dumps(out, default=str))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    asyncio.run(serve(make_backend()))


if __name__ == "__main__":
    main()
