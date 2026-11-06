"""Global search — the gateway's federation (2026-10-20, spec project_jethro_global_search).

    GET /search?q=&types=&limit=

Fans out IN PARALLEL to every owning service's `/…/search` with the same trusted identity headers
the proxy stamps (each service applies its own org scope + role rules — the gateway never
filters records itself), waits at most SEARCH_TIMEOUT_MS (default 800 ms) per service, and
merges what came back. A slow / failed service does not fail the search: its name is listed in
`slow` / `failed` and `partial` is true, so the box can say "some sources were slow".

Services whose paid module the org has not bought are not called at all (same rule as the
proxy's paywall). Superadmin (platform console) gets no tenant records.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx

# name, gateway service prefix (→ SERVICES base), path, paid module (None = core), record types
SOURCES: list[tuple[str, str, str, str | None, frozenset]] = [
    ("employee", "employees", "/employees/search", None, frozenset(
        {"employee", "candidate", "requisition", "offer", "letter", "exit", "asset", "request", "policy"})),
    ("onboarding", "onboarding", "/onboarding/search", None, frozenset({"onboarding"})),
    ("leave", "leave", "/leave/search", "leave", frozenset({"leave"})),
    ("time", "time", "/time/search", "time", frozenset({"attendance", "permission"})),
    ("payroll", "payroll", "/payroll/search", "payroll", frozenset({"payslip", "run"})),
    ("engage", "engage", "/engage/search", None, frozenset({"ticket", "course", "policy"})),
    ("approvals", "approvals", "/approvals/search", None, frozenset({"approval"})),
]
ALL_TYPES = frozenset().union(*(s[4] for s in SOURCES))
MIN_Q = 2
MAX_TOTAL = 60

_client: httpx.AsyncClient | None = None


def timeout_s() -> float:
    try:
        return max(0.05, float(os.getenv("SEARCH_TIMEOUT_MS", "800")) / 1000.0)
    except ValueError:
        return 0.8


def client() -> httpx.AsyncClient:
    """One pooled client for the fan-out (keep-alive to every service = lower latency)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))
    return _client


def plan(types: str | None, modules: list[str] | None, role: str) -> list[tuple[str, str, str, str | None, frozenset]]:
    """Which sources to call (pure): asked types ∩ offered, minus unsubscribed modules."""
    if role == "superadmin":
        return []
    asked = {t.strip().lower() for t in (types or "").split(",") if t.strip()} or set(ALL_TYPES)
    out = []
    for src in SOURCES:
        _name, _svc, _path, module, offered = src
        if not (offered & asked):
            continue
        if module and modules is not None and module not in modules:
            continue
        out.append(src)
    return out


def merge(results: list[dict], limit_total: int = MAX_TOTAL) -> list[dict]:
    """All hits, best score first, deduplicated by (type, id) (pure)."""
    seen, hits = set(), []
    for r in results:
        for h in r.get("hits") or []:
            key = (h.get("type"), h.get("id"))
            if key in seen or not h.get("url"):
                continue
            seen.add(key)
            h = {**h, "source": r.get("name")}
            hits.append(h)
    hits.sort(key=lambda h: (-float(h.get("score") or 0), str(h.get("title") or "").lower()))
    return hits[:limit_total]


async def _one(name: str, url: str, params: dict, headers: dict, tmo: float) -> dict:
    t0 = time.perf_counter()
    try:
        r = await asyncio.wait_for(client().get(url, params=params, headers=headers, timeout=tmo), timeout=tmo)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if r.status_code != 200:
            return {"name": name, "ok": False, "ms": ms, "error": f"http {r.status_code}", "hits": []}
        body = r.json()
        return {"name": name, "ok": True, "ms": ms, "hits": body.get("hits") or []}
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {"name": name, "ok": False, "slow": True, "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": "timeout", "hits": []}
    except Exception as ex:  # noqa: BLE001 — one source down never breaks the search
        return {"name": name, "ok": False, "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": type(ex).__name__, "hits": []}


async def federate(q: str, types: str | None, limit: int, *, headers: dict, services: dict,
                   modules: list[str] | None, role: str, fetch=None) -> dict:
    """Call every planned source in parallel and merge. `fetch` is injectable for tests."""
    t0 = time.perf_counter()
    q = " ".join((q or "").split())[:80]
    if len(q) < MIN_Q:
        return {"q": q, "hits": [], "sources": [], "partial": False, "slow": [], "failed": [], "took_ms": 0.0}
    tmo = timeout_s()
    srcs = plan(types, modules, role)
    params = {"q": q, "limit": str(max(1, min(int(limit or 8), 25)))}
    if types:
        params["types"] = types
    fetch = fetch or _one
    jobs = []
    for name, svc, path, _module, _offered in srcs:
        base = services.get(svc)
        if not base:
            continue
        jobs.append(fetch(name, f"{base}{path}", params, headers, tmo))
    results = list(await asyncio.gather(*jobs)) if jobs else []
    slow = [r["name"] for r in results if r.get("slow")]
    failed = [r["name"] for r in results if not r.get("ok") and not r.get("slow")]
    return {"q": q, "hits": merge(results),
            "sources": [{"name": r["name"], "ok": r.get("ok", False), "ms": r.get("ms"), "count": len(r.get("hits") or []),
                         **({"error": r["error"]} if r.get("error") else {})} for r in results],
            "partial": bool(slow or failed), "slow": slow, "failed": failed,
            "took_ms": round((time.perf_counter() - t0) * 1000, 1)}
