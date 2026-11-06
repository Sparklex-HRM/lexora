"""Go-live 2026-10-24: gateway flood guard keys on the RIGHT-most X-Forwarded-For entry and has a
tighter budget for uploads / exports; CORS is an explicit allow-list."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(_HERE), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ratelimit_mw  # noqa: E402
from ratelimit_mw import GatewayRateLimit, is_heavy  # noqa: E402
from shared.utils import ratelimit as R  # noqa: E402


def _app(rpm=100, heavy=2):
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    def anything(path: str):
        return {"ok": True}
    app.add_middleware(GatewayRateLimit, rpm=rpm, limiter=R.RateLimiter(rpm, 60),
                       heavy_rpm=heavy, heavy_limiter=R.RateLimiter(heavy, 60))
    return TestClient(app)


def test_spoofed_left_most_forwarded_for_does_not_reset_the_budget(monkeypatch):
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "1")
    c = _app(rpm=2)
    codes = [c.get("/employees", headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]          # same real client (right-most hop) despite fresh fake left entries


@pytest.mark.parametrize("method,path,ctype,heavy", [
    ("GET", "/payroll/runs/1/register.xlsx", None, True),
    ("GET", "/payroll/payslips/1/pdf", None, True),
    ("GET", "/reports/export", None, True),
    ("GET", "/employees/bulk-export", None, True),
    ("POST", "/employees/1/documents", "multipart/form-data; boundary=x", True),
    ("GET", "/employees", None, False),
    ("POST", "/leave/requests", "application/json", False),
    ("GET", "/payroll/runs", None, False),
])
def test_heavy_classification(method, path, ctype, heavy):
    headers = [(b"content-type", ctype.encode())] if ctype else []
    assert is_heavy({"method": method, "path": path, "headers": headers}) is heavy


def test_exports_have_their_own_tighter_budget():
    c = _app(rpm=100, heavy=2)
    assert [c.get("/reports/export").status_code for _ in range(3)] == [200, 200, 429]
    r = c.get("/reports/export")
    assert "uploads / downloads" in r.json()["detail"] and r.headers["retry-after"]
    assert c.get("/employees").status_code == 200        # normal reads unaffected


def test_heavy_budget_can_be_switched_off_locally():
    app = FastAPI()

    @app.get("/x.pdf")
    def pdf():
        return {}
    app.add_middleware(GatewayRateLimit, rpm=100, limiter=R.RateLimiter(100, 60), heavy_rpm=0)
    c = TestClient(app)
    assert all(c.get("/x.pdf").status_code == 200 for _ in range(5))


def test_gateway_cors_is_an_explicit_list():
    src = open(os.path.join(_HERE, "main.py"), encoding="utf-8").read()
    assert 'allow_methods=["*"]' not in src and "allow_origins=[o.strip()" in src
    assert ratelimit_mw.EXEMPT == ("/health", "/ready")
