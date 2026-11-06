"""Outbound HTTP for every provider call — ALWAYS behind a timeout, never logging
bodies or headers (they may carry tokens). Returns a small plain result instead
of raising, so a provider outage can never take a business action down."""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

DEFAULT_TIMEOUT = 10.0


class HttpResult:
    __slots__ = ("ok", "status", "text", "json", "error")

    def __init__(self, ok: bool, status: int | None = None, text: str = "", json=None, error: str | None = None):
        self.ok, self.status, self.text, self.json, self.error = ok, status, text, json, error

    def excerpt(self, n: int = 300) -> str:
        return (self.error or self.text or "")[:n]


async def request(method: str, url: str, *, json=None, data=None, headers: dict | None = None,
                  params: dict | None = None, timeout: float = DEFAULT_TIMEOUT, auth=None) -> HttpResult:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            r = await c.request(method, url, json=json, data=data, headers=headers, params=params, auth=auth)
    except httpx.TimeoutException:
        return HttpResult(False, error=f"Timed out after {timeout:g}s")
    except httpx.HTTPError as ex:
        return HttpResult(False, error=f"Could not connect ({type(ex).__name__})")
    body = None
    try:
        body = r.json()
    except ValueError:
        body = None
    return HttpResult(200 <= r.status_code < 300, r.status_code, r.text[:2000], body)


async def post_json(url: str, payload, **kw) -> HttpResult:
    return await request("POST", url, json=payload, **kw)


async def get_json(url: str, **kw) -> HttpResult:
    return await request("GET", url, **kw)


def check_https(url: str) -> str | None:
    """Why a provider / webhook URL is not acceptable, or None.

    The decision is made on the PARSED host, not a string prefix: "http://localhost@evil"
    used to pass as "localhost". Plain http is only allowed to a dev receiver on this
    machine (localhost / 127.x / host.docker.internal — the host seen from a container)."""
    u = (url or "").strip()
    try:
        p = urlsplit(u)
    except ValueError:
        return "The URL could not be read"
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host:
        return "The URL must start with https://"
    if "@" in p.netloc:
        return "The URL must not carry a user name or password"
    dev_host = host in ("localhost", "host.docker.internal") or host.startswith("127.")
    if p.scheme == "http" and not dev_host:
        return "The URL must start with https://"
    if len(u) > 600:
        return "The URL is too long"
    return None
