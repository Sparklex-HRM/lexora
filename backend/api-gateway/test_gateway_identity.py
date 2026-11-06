"""Gateway: client identity headers are ignored, a disabled login's token is
refused, X-Employee-Id / X-Role are stamped from the login's live state.
Run:  cd backend/api-gateway && PYTHONPATH=../ python -m pytest -q
"""
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///unused-gateway-test.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import revocation  # noqa: E402
from shared.utils.security import create_access_token  # noqa: E402

ORG, USER, EMP = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", \
    "33333333-3333-3333-3333-333333333333"
seen: list[dict] = []
state: dict = {}


class _Upstream:
    """Stands in for the service behind the gateway; records what it was sent."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, params=None, content=None, headers=None):
        seen.append({"url": url, "headers": {k.lower(): v for k, v in headers.items()}})

        class R:
            content, status_code = b'{"ok":true}', 200
            headers = {"content-type": "application/json", "content-disposition": 'attachment; filename="a.pdf"', "x-content-sha256": "ab" * 32,
                       "x-powered-by": "uvicorn"}
        return R()


async def _loader(user_id):
    return state.get(user_id)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main.httpx, "AsyncClient", _Upstream)
    monkeypatch.setattr(revocation, "cache", revocation.UserStateCache(loader=_loader, ttl=0))
    seen.clear()
    state.clear()
    state[USER] = {"is_active": True, "tokens_valid_after": None, "role": "employee", "employee_id": EMP}
    return TestClient(main.app)


def _bearer(**claims):
    tok = create_access_token({"sub": USER, "org_id": ORG, "email": "ravi@maple.example", "role": "employee",
                               "employee_id": None, **claims})
    return {"Authorization": f"Bearer {tok}"}


def test_identity_headers_from_a_client_do_not_replace_the_token(client):
    # No token, only forged headers → refused (this used to be let through).
    r = client.get("/employees", headers={"X-Org-Id": ORG, "X-Role": "superadmin", "X-User-Id": USER})
    assert r.status_code == 401 and not seen
    # With a real token, forged headers are thrown away and the token's identity is stamped.
    r = client.get("/employees", headers={**_bearer(), "X-Role": "org_admin", "X-Employee-Id": "forged",
                                          "X-Org-Id": "forged", "X-Branch-Scope": "org", "X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200
    h = seen[-1]["headers"]
    assert h["x-org-id"] == ORG and h["x-user-id"] == USER and h["x-role"] == "employee"
    assert h["x-branch-scope"] == "branch" and h["x-forwarded-for"] != "9.9.9.9"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-sha256"] == "ab" * 32 and "x-powered-by" not in r.headers   # integrity header kept, banner dropped


def test_employee_id_and_role_come_from_the_live_login(client):
    # The token says "no employee, role employee"; the login row is newer.
    state[USER].update(role="manager", employee_id=EMP)
    client.get("/leave/requests", headers=_bearer(modules=["core", "leave"]))
    h = seen[-1]["headers"]
    assert h["x-employee-id"] == EMP and h["x-role"] == "manager"


def test_disabled_login_is_refused_with_a_valid_token(client):
    hdr = _bearer()
    assert client.get("/employees", headers=hdr).status_code == 200
    state[USER]["is_active"] = False
    r = client.get("/employees", headers=hdr)
    assert r.status_code == 401 and "disabled" in r.json()["detail"] and len(seen) == 1   # never reached a service
    state.pop(USER)                                                                       # login deleted
    assert client.get("/employees", headers=hdr).status_code == 401


def test_token_older_than_the_cut_off_is_refused(client):
    hdr = _bearer()
    state[USER]["tokens_valid_after"] = datetime.now(timezone.utc) + timedelta(seconds=5)
    r = client.get("/employees", headers=hdr)
    assert r.status_code == 401 and "ended" in r.json()["detail"]
    state[USER]["tokens_valid_after"] = datetime.now(timezone.utc) - timedelta(hours=1)
    assert client.get("/employees", headers=hdr).status_code == 200


def test_verdict_rules_and_cache():
    now = datetime.now(timezone.utc)
    iat = int(now.timestamp())
    ok = {"is_active": True, "tokens_valid_after": None}
    assert revocation.verdict(ok, iat) is None and revocation.verdict(None, iat) == "gone"
    assert revocation.verdict({**ok, "is_active": False}, iat) == "disabled"
    assert revocation.verdict({**ok, "tokens_valid_after": now}, iat) == "stale"            # same second → refused
    assert revocation.verdict({**ok, "tokens_valid_after": now}, iat + 1) is None
    assert revocation.verdict({**ok, "tokens_valid_after": now}, None) == "stale"

    calls, t = [], [0.0]

    async def loader(uid):
        calls.append(uid)
        return ok
    import asyncio
    cache = revocation.UserStateCache(loader=loader, ttl=10, clock=lambda: t[0])

    async def run():
        await cache.get("u")
        await cache.get("u")
        t[0] = 11
        await cache.get("u")
    asyncio.run(run())
    assert calls == ["u", "u"]                       # one read per 10 s, not per request


def test_public_pages_need_no_token(client):
    for path in ("/auth/invite/abc", "/auth/password/reset/abc", "/onboarding/invite/abc"):
        assert client.get(path).status_code == 200, path
    assert client.post("/auth/password/forgot", json={}).status_code == 200
    # …and never carry an identity, even a forged one.
    client.get("/onboarding/invite/abc", headers={"X-Org-Id": ORG, "X-Role": "org_admin"})
    assert seen[-1]["headers"]["x-org-id"] == "" and seen[-1]["headers"]["x-role"] == ""
    assert client.get("/auth/users").status_code == 401
