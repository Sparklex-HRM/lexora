"""API gateway — org API keys as an alternative to a JWT (stream G3).

Runs BETWEEN ScrubIdentityHeaders (outermost, which has already dropped every
client identity header) and TenantMiddleware. When the request carries
``Authorization: Bearer lxk_…`` or ``X-Api-Key: lxk_…``:

  * the key is looked up by its SHA-256 (cached a few seconds), checked for
    revoke / expiry, and its SCOPES are checked against <service>:<read|write>
    for this path + method — a request outside the scopes is refused HERE;
  * the Authorization / X-Api-Key headers are removed and trusted identity
    headers are injected: X-Org-Id = the key's org, X-Role = the key's role,
    X-User-Email = apikey:<name> (so audit rows name the key), X-Branch-Scope
    = org. TenantMiddleware then takes its service-to-service path.

Anything that is not an API key passes through untouched (JWT as before).
"""
import json
import sys

sys.path.append("/app")
from shared.utils import apikeys  # noqa: E402

cache = apikeys.KeyCache()


def _json(status: int, body: dict):
    data = json.dumps(body).encode()
    return status, [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())], data


def extract_key(headers: list[tuple[bytes, bytes]]) -> str | None:
    for k, v in headers:
        kl = k.lower()
        if kl == b"authorization":
            s = v.decode("latin-1").strip()
            if s.lower().startswith("bearer ") and apikeys.looks_like_key(s[7:]):
                return s[7:].strip()
        elif kl == b"x-api-key":
            s = v.decode("latin-1").strip()
            if apikeys.looks_like_key(s):
                return s
    return None


def service_of(path: str) -> str:
    return (path or "/").strip("/").split("/", 1)[0]


def identity_headers(state: dict) -> list[tuple[bytes, bytes]]:
    return [(b"x-org-id", str(state["org_id"]).encode()), (b"x-user-id", b""),
            (b"x-user-email", f"apikey:{state.get('name') or state['id']}".encode("latin-1", "replace")),
            (b"x-role", str(state.get("role") or "employee").encode()), (b"x-employee-id", b""),
            (b"x-branch-id", b""), (b"x-branch-scope", b"org"), (b"x-api-key-id", str(state["id"]).encode())]


class ApiKeyAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw = extract_key(scope.get("headers", []))
        if not raw:
            return await self.app(scope, receive, send)
        try:
            state = await cache.get(raw)
        except Exception:                                    # noqa: BLE001
            return await self._reply(send, *_json(503, {"detail": "Could not check the API key. Try again."}))
        why = apikeys.verdict(state)
        if why:
            return await self._reply(send, *_json(401, {"detail": apikeys.MESSAGES[why]}))
        if not apikeys.scope_allows(state["scopes"], service_of(scope.get("path", "")), scope.get("method", "GET")):
            return await self._reply(send, *_json(403, {"detail": apikeys.MESSAGES["scope"],
                                                        "scopes": state["scopes"]}))
        scope["headers"] = [(k, v) for k, v in scope.get("headers", [])
                            if k.lower() not in (b"authorization", b"x-api-key")] + identity_headers(state)
        await apikeys.touch(state["id"])
        return await self.app(scope, receive, send)

    @staticmethod
    async def _reply(send, status, headers, body):
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
