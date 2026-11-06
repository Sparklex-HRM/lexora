"""Decision 2026-10-21: the gateway's "is this login / session still good?" checks FAIL CLOSED.

If the login store (revocation.py) or the session store (saas_gate.py) cannot be read, the
request is refused with 503 — never let through — and the failure is not cached, so the next
request (store back) works again. There is no switch to fail open, and the cache windows are
short (default 2 s, capped at 10 s).
Run:  cd backend/api-gateway && PYTHONPATH=../ python -m pytest -q test_failclosed_20261021.py
"""
import os
import sys

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///unused-gateway-test.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import revocation  # noqa: E402
import saas_gate  # noqa: E402
from shared.utils.security import create_access_token  # noqa: E402

ORG, USER, SID = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", \
    "44444444-4444-4444-4444-444444444444"
calls: list[str] = []
state = {"user_down": False, "session_down": False}


class _Upstream:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, params=None, content=None, headers=None):
        calls.append(url)

        class R:
            content, status_code = b'{"ok":true}', 200
            headers = {"content-type": "application/json"}
        return R()


async def _user(uid):
    if state["user_down"]:
        raise ConnectionError("database is down")
    return {"is_active": True, "tokens_valid_after": None, "role": "org_admin", "employee_id": None}


async def _sess(sid):
    if state["session_down"]:
        raise ConnectionError("database is down")
    return {"revoked": False}


async def _org(oid):
    return None


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main.httpx, "AsyncClient", _Upstream)
    monkeypatch.setattr(revocation, "cache", revocation.UserStateCache(loader=_user, ttl=60))
    monkeypatch.setattr(saas_gate, "load_session", _sess)
    monkeypatch.setattr(saas_gate, "load_org", _org)
    monkeypatch.setattr(saas_gate, "_sessions", saas_gate._TTLCache(60))
    monkeypatch.setattr(saas_gate, "_orgs", saas_gate._TTLCache(0))
    calls.clear()
    state.update(user_down=False, session_down=False)
    return TestClient(main.app)


def _bearer(**claims):
    tok = create_access_token({"sub": USER, "org_id": ORG, "email": "ceo@maple.example", "role": "org_admin",
                               "modules": ["core", "payroll", "leave"], **claims})
    return {"Authorization": f"Bearer {tok}"}


def test_login_store_down_refuses_with_503_and_does_not_reach_the_service(client):
    state["user_down"] = True
    r = client.get("/employees", headers=_bearer())
    assert r.status_code == 503 and "session" in r.json()["detail"].lower()
    assert calls == []


def test_login_store_failure_is_not_cached(client):
    state["user_down"] = True
    assert client.get("/employees", headers=_bearer()).status_code == 503
    state["user_down"] = False
    assert client.get("/employees", headers=_bearer()).status_code == 200


def test_session_store_down_refuses_with_503(client):
    state["session_down"] = True
    r = client.get("/employees", headers=_bearer(sid=SID))
    assert r.status_code == 503 and calls == []
    state["session_down"] = False                      # not remembered → works on the next request
    assert client.get("/employees", headers=_bearer(sid=SID)).status_code == 200


def test_global_search_fails_closed_too(client):
    state["user_down"] = True
    assert client.get("/search?q=priya", headers=_bearer()).status_code == 503


def test_no_fail_open_switch_and_short_cache(monkeypatch):
    assert not hasattr(revocation, "FAIL_CLOSED")      # the old opt-in is gone: closed is the only mode
    assert revocation.TTL_SEC <= 10 and saas_gate.SESSION_TTL <= 10
