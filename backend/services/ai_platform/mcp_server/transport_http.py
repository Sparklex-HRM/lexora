"""Streamable-HTTP transport for the MCP server + two plain JSON endpoints, all behind the
AI access token (``Authorization: Bearer lxai_…``).

Mounted by main.py at ``/ai/mcp``:
  * ``POST /ai/mcp``           — MCP streamable HTTP (stateless, JSON responses — the API
                                  gateway buffers responses, so no SSE is relied on)
  * ``GET  /ai/mcp/tools``     — plain JSON tool list (used by the stdio bridge)
  * ``POST /ai/mcp/call``      — plain JSON {name, arguments} → result (used by the stdio bridge)
  * ``GET  /ai/mcp/whoami``    — the token's user / role / scopes (connection test)

Identity is derived ONLY from the token; any identity header the client sends is ignored.
The gateway must list ``/ai/mcp`` as a public (no-JWT) prefix — see docs/ai/MCP.md.
"""
from __future__ import annotations

import contextlib
import json
import sys

sys.path.append("/app")

import tokens as TK  # noqa: E402
import tools_registry as TR  # noqa: E402
from mcp_server import server as MS  # noqa: E402

_manager = None


def _json(status: int, body: dict):
    payload = json.dumps(body, default=str).encode()
    return status, [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())], payload


async def _send(send, status, headers, payload):
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


async def _read_body(receive) -> bytes:
    body, more = b"", True
    while more:
        m = await receive()
        body += m.get("body", b"")
        more = m.get("more_body", False)
    return body


def _bearer(scope) -> str | None:
    for k, v in scope.get("headers") or []:
        if k.lower() == b"authorization":
            val = v.decode(errors="ignore").strip()
            return val[7:].strip() if val.lower().startswith("bearer ") else val
    return None


def ctx_for(row: dict) -> TR.Ctx:
    return TR.Ctx(headers=TK.headers_for(row), channel="mcp", token_id=row["id"], scopes=set(row.get("scopes") or ["read"]))


async def app(scope, receive, send):
    """ASGI app: authenticate, then route."""
    if scope["type"] == "lifespan":                           # the manager's lifespan is run by main.py
        return
    if scope["type"] != "http":
        return
    row, err = await TK.authenticate(_bearer(scope))
    if err:
        return await _send(send, *_json(401 if "Rate" not in err else 429, {"detail": err}))
    ctx = ctx_for(row)
    path = (scope.get("path") or "").rstrip("/")
    sub = path.split("/ai/mcp", 1)[-1] if "/ai/mcp" in path else path
    method = scope.get("method", "GET").upper()
    if sub == "/whoami":
        return await _send(send, *_json(200, {"user_id": ctx.user_id, "user_email": ctx.user_email, "role": ctx.role, "scopes": sorted(ctx.scopes),
                                               "employee_linked": bool(ctx.employee_id), "server": MS.SERVER_NAME, "version": MS.SERVER_VERSION}))
    if sub == "/tools" and method == "GET":
        return await _send(send, *_json(200, {"tools": MS.list_tool_dicts(ctx)}))
    if sub == "/call" and method == "POST":
        try:
            body = json.loads((await _read_body(receive)) or b"{}")
        except ValueError:
            return await _send(send, *_json(400, {"detail": "Body must be JSON {name, arguments}"}))
        out = await MS.call_tool_dict(ctx, str(body.get("name") or ""), body.get("arguments") or {})
        return await _send(send, *_json(200, out))
    if sub in ("", "/mcp"):
        if _manager is None:
            return await _send(send, *_json(503, {"detail": "MCP transport not started (mcp package missing?)"}))
        tok = MS.current_ctx.set(ctx)
        try:
            scope = dict(scope, path="/", raw_path=b"/")
            await _manager.handle_request(scope, receive, send)
        finally:
            MS.current_ctx.reset(tok)
        return
    return await _send(send, *_json(404, {"detail": "Use POST /ai/mcp (MCP), GET /ai/mcp/tools, POST /ai/mcp/call or GET /ai/mcp/whoami"}))


class ExactPath:
    """ASGI wrapper (a class instance, so Starlette's Route treats it as a raw ASGI app) for the
    exact path ``/ai/mcp`` — without it the mount answers 307 to ``/ai/mcp/``."""

    async def __call__(self, scope, receive, send):
        await app(scope, receive, send)


def install(fastapi_app, prefix: str = "/ai/mcp") -> None:
    """Mount the transport at ``prefix`` AND route the exact path (no trailing slash) to it.
    A bare mount made Starlette answer ``POST /ai/mcp`` with 307 → ``/ai/mcp/`` carrying the
    container's internal host in Location (unreachable through the gateway, and before the 401)."""
    from starlette.routing import Route
    fastapi_app.mount(prefix, app)
    fastapi_app.router.routes.insert(0, Route(prefix, ExactPath(), methods=["GET", "POST", "DELETE"]))


@contextlib.asynccontextmanager
async def lifespan():
    """Run inside the FastAPI lifespan: starts the streamable-HTTP session manager."""
    global _manager
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] MCP streamable HTTP not available: {e}")
        yield
        return
    manager = StreamableHTTPSessionManager(app=MS.build_server(), json_response=True, stateless=True,
                                           security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    async with manager.run():
        _manager = manager
        try:
            yield
        finally:
            _manager = None
