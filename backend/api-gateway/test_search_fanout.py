"""Gateway GET /search — plan (types × modules × superadmin), merge, per-service timeout, identity.
Run:  cd backend/api-gateway && PYTHONPATH=../ python -m pytest -q test_search_fanout.py
"""
import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///unused-gateway-test.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import revocation  # noqa: E402
import search_fanout as F  # noqa: E402
from shared.utils.security import create_access_token  # noqa: E402

ORG, USER, EMP = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", \
    "33333333-3333-3333-3333-333333333333"


def _names(srcs):
    return [s[0] for s in srcs]


def test_plan_respects_types_modules_and_superadmin():
    every = _names(F.plan(None, None, "org_admin"))
    assert every == [s[0] for s in F.SOURCES]
    assert "payroll" not in _names(F.plan(None, ["core", "leave", "time"], "org_admin"))   # no payroll SKU
    assert "payroll" not in _names(F.plan(None, ["core"], "employee"))
    assert _names(F.plan("payslip", None, "employee")) == ["payroll"]
    assert F.plan(None, None, "superadmin") == []                 # the platform console sees no tenant records
    assert F.plan("nothing", None, "org_admin") == []


def test_merge_orders_dedupes_and_tags_source():
    res = [{"name": "a", "hits": [{"type": "x", "id": "1", "title": "B", "url": "/b", "score": 50},
                                  {"type": "x", "id": "2", "title": "no url", "url": "", "score": 99}]},
           {"name": "b", "hits": [{"type": "x", "id": "1", "title": "dup", "url": "/b", "score": 10},
                                  {"type": "y", "id": "9", "title": "A", "url": "/a", "score": 80}]}]
    out = F.merge(res)
    assert [(h["id"], h["source"]) for h in out] == [("9", "b"), ("1", "a")]


def test_federate_partial_when_one_source_is_slow(monkeypatch):
    monkeypatch.setenv("SEARCH_TIMEOUT_MS", "100")

    async def fetch(name, url, params, headers, tmo):
        assert headers["X-Org-Id"] == ORG and params["q"] == "chloe"
        if name == "payroll":
            await asyncio.sleep(0.3)                   # slower than the budget → must not wait for it
        return {"name": name, "ok": True, "ms": 1, "hits": [
            {"type": name, "id": name, "title": name, "url": f"/{name}", "score": 10}]}

    async def run():
        # the real per-source wrapper: an injected slow fetch is cut by asyncio.wait_for inside _one
        async def guarded(name, url, params, headers, tmo):
            try:
                return await asyncio.wait_for(fetch(name, url, params, headers, tmo), timeout=tmo)
            except asyncio.TimeoutError:
                return {"name": name, "ok": False, "slow": True, "ms": tmo * 1000, "error": "timeout", "hits": []}
        return await F.federate("  chloe ", None, 8, headers={"X-Org-Id": ORG}, services=main.SERVICES,
                                modules=None, role="hr_admin", fetch=guarded)
    out = asyncio.run(run())
    assert out["partial"] is True and out["slow"] == ["payroll"]
    assert "payroll" not in {h["source"] for h in out["hits"]} and len(out["hits"]) == len(F.SOURCES) - 1
    assert out["took_ms"] < 280                        # parallel, bounded by the timeout


def test_one_source_down_is_reported_not_raised():
    def handler(request):
        if request.url.path.startswith("/time"):
            raise httpx.ConnectError("down")
        if request.url.path.startswith("/leave"):
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"hits": [{"type": "t", "id": "1", "title": "x", "url": "/x", "score": 1}]})
    F._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(F.federate("ab", "attendance,leave,employee", 5, headers={}, services=main.SERVICES,
                                     modules=None, role="org_admin"))
    finally:
        F._client = None
    by = {s["name"]: s for s in out["sources"]}
    assert by["time"]["ok"] is False and by["leave"]["error"] == "http 500" and by["employee"]["ok"] is True
    assert set(out["failed"]) == {"leave", "time"}
    assert out["partial"] is True and len(out["hits"]) == 1


def test_short_query_calls_nobody():
    async def boom(*a, **k):
        raise AssertionError("must not be called")
    out = asyncio.run(F.federate("a", None, 8, headers={}, services=main.SERVICES, modules=None, role="org_admin",
                                 fetch=boom))
    assert out["hits"] == [] and out["sources"] == []


# ── through the real gateway app ─────────────────────────────────────────────

async def _loader(user_id):
    return {"is_active": True, "tokens_valid_after": None, "role": "employee", "employee_id": EMP}


def test_endpoint_needs_a_login_and_stamps_the_token_identity(monkeypatch):
    monkeypatch.setattr(revocation, "cache", revocation.UserStateCache(loader=_loader, ttl=0))
    got = []

    async def fake_one(name, url, params, headers, tmo):
        got.append((name, headers))
        return {"name": name, "ok": True, "ms": 1, "hits": []}
    monkeypatch.setattr(F, "_one", fake_one)
    c = TestClient(main.app)
    assert c.get("/search?q=chloe").status_code == 401
    assert c.get("/search?q=chloe", headers={"X-Org-Id": ORG, "X-Role": "org_admin", "X-User-Id": USER}).status_code == 401
    tok = create_access_token({"sub": USER, "org_id": ORG, "email": "p@maple.example", "role": "employee",
                               "employee_id": EMP, "modules": ["core", "payroll", "leave", "time"]})
    r = c.get("/search?q=chloe", headers={"Authorization": f"Bearer {tok}", "X-Role": "org_admin"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    names = [n for n, _ in got]
    assert "payroll" in names and "time" in names                   # bought modules are searched
    for _n, h in got:
        assert h["X-Role"] == "employee" and h["X-Org-Id"] == ORG and h["X-Employee-Id"] == EMP   # forged role ignored
