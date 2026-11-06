"""Gateway WASA hardening (2026-10-21): host allow-list, forced password change, client IP,
docs off, masked 500s, header pass-through, public-path boundary.
Run:  cd backend/api-gateway && PYTHONPATH=../ python -m pytest -q test_hardening.py
"""
import os
import sys

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///unused-gateway-test.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import hardening  # noqa: E402
import main  # noqa: E402
import revocation  # noqa: E402
import shared.middleware.tenant as tenant  # noqa: E402
from shared import ops_runtime  # noqa: E402
from shared.utils.security import create_access_token  # noqa: E402

ORG, USER = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
seen = []


class _Upstream:
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
            headers = {"content-type": "application/json",
                       "content-security-policy": "default-src 'none'; sandbox",
                       "cache-control": "private, max-age=60", "server": "uvicorn", "x-powered-by": "x"}
        return R()


async def _loader(user_id):
    return {"is_active": True, "tokens_valid_after": None, "role": "employee", "employee_id": None}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main.httpx, "AsyncClient", _Upstream)
    monkeypatch.setattr(revocation, "cache", revocation.UserStateCache(loader=_loader, ttl=0))
    seen.clear()
    return TestClient(main.app)


def _tok(**extra):
    claims = {"sub": USER, "org_id": ORG, "role": "employee", "email": "e@x.example"}
    claims.update(extra)
    return {"Authorization": "Bearer " + create_access_token(claims)}


# ── forced password change ───────────────────────────────────────────────────
def test_pwchg_token_refused_outside_change_password(client):
    r = client.get("/employees", headers=_tok(pwchg=True))
    assert r.status_code == 403 and r.json()["detail"] == "password_change_required"
    assert not seen                                    # never reached a service


@pytest.mark.parametrize("path", ["/auth/me", "/auth/change-password", "/auth/logout", "/auth/password-policy"])
def test_pwchg_token_allowed_on_change_password_routes(client, path):
    method = client.get if path in ("/auth/me", "/auth/password-policy") else client.post
    r = method(path, headers=_tok(pwchg=True))
    assert r.status_code == 200


def test_pwchg_search_refused(client):
    assert client.get("/search?q=a", headers=_tok(pwchg=True)).status_code == 403


def test_normal_token_unaffected(client):
    assert client.get("/employees", headers=_tok()).status_code == 200


def test_pwchg_prefix_boundary():
    class Req:
        headers = {k.lower(): v for k, v in _tok(pwchg=True).items()}

        class state:
            user_id = USER
    assert hardening.password_change_denied(Req, "/auth/me") is None
    assert hardening.password_change_denied(Req, "/auth/mex") is not None
    assert hardening.password_change_denied(Req, "/auth/users") is not None


# ── host allow-list ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("host,ok", [
    ("app.example.com", True), ("app.example.com:443", True), ("APP.EXAMPLE.COM", True),
    ("evil.example", False), ("", False), ("app.example.com.evil.example", False), ("x.tenant.example.com", True),
])
def test_host_ok(host, ok):
    assert hardening.host_ok(host, ["app.example.com", "*.tenant.example.com"]) is ok


def test_host_ok_unset_allows_all():
    assert hardening.host_ok("anything", [])


def test_host_allowlist_middleware(monkeypatch):
    monkeypatch.setenv("LEXORA_ALLOWED_HOSTS", "app.example.com")
    app = FastAPI()

    @app.get("/x")
    def x():
        return {"ok": 1}

    @app.get("/health")
    def h():
        return {"ok": 1}
    hardening.install_host_allowlist(app)
    c = TestClient(app)
    assert c.get("/x", headers={"Host": "app.example.com"}).status_code == 200
    assert c.get("/x", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/health", headers={"Host": "10.0.0.7:8000"}).status_code == 200   # probes by pod address


# ── client IP for rate limits ────────────────────────────────────────────────
class _R:
    def __init__(self, fwd, peer="10.0.0.2"):
        self.headers = {"x-forwarded-for": fwd} if fwd is not None else {}

        class C:
            host = peer
        self.client = C


def test_client_ip_ignores_forwarded_without_trust(monkeypatch):
    monkeypatch.delenv("TRUST_FORWARDED_FOR", raising=False)
    assert hardening.client_ip(_R("1.2.3.4")) == "10.0.0.2"


def test_client_ip_takes_rightmost_hop(monkeypatch):
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "1")
    # the client wrote "6.6.6.6"; our edge proxy appended the real address
    assert hardening.client_ip(_R("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert hardening.client_ip(_R(None)) == "10.0.0.2"


def test_proxy_sends_single_client_ip(client, monkeypatch):
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "1")
    client.get("/employees", headers={**_tok(), "X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert seen[-1]["headers"]["x-forwarded-for"] == "203.0.113.9"


# ── headers passed through / added ───────────────────────────────────────────
def test_file_safety_headers_pass_through(client):
    r = client.get("/settings/logo", headers=_tok())
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["cache-control"] == "private, max-age=60"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "server" not in r.headers and "x-powered-by" not in r.headers


# ── docs off, public-path boundary, masked 500 ───────────────────────────────
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_off_by_default(client, path):
    assert client.get(path).status_code in (401, 404)


def test_login_info_is_not_public():
    assert tenant._is_public("/auth/login")
    assert not tenant._is_public("/auth/login-infox")
    assert main and tenant._is_public("/auth/login-info")          # listed on purpose (PIPEDA Privacy Officer login footer)
    assert not tenant._is_public("/healthz")
    assert tenant._is_public("/auth/challenge")
    assert tenant._is_public("/hiring/public/careers/ACME")
    assert not tenant._is_public("/hiring/publicX")


def test_masked_500_has_ref_and_no_exception_text(monkeypatch):
    monkeypatch.delenv("LEXORA_API_DOCS", raising=False)
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("password=hunter2 at /app/svc/secret.py line 9")
    ops_runtime.install_error_masking(app, "test")
    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    body = r.text
    assert "hunter2" not in body and "RuntimeError" not in body and "secret.py" not in body
    assert len(r.json()["ref"]) == 8


def test_docs_can_be_enabled_for_development(monkeypatch):
    monkeypatch.setenv("LEXORA_API_DOCS", "1")
    monkeypatch.delenv("LEXORA_ENV", raising=False)
    assert ops_runtime.api_docs_enabled()
    monkeypatch.setenv("LEXORA_ENV", "production")
    assert not ops_runtime.api_docs_enabled()
