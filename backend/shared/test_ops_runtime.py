"""S3 production runtime tests: PII log filter, prod secret gate, request id, /ready, body limit.
Run:  cd backend && python -m pytest shared/test_ops_runtime.py -q
"""
import io
import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from shared import ops_runtime as ops

STRONG = "k" * 20 + "Q9x!LmZp2Rt7Vb3Nc8"          # 38 chars, no placeholder words

GOOD_ENV = {
    "LEXORA_ENV": "production",
    "JWT_SECRET": "a1" + STRONG,
    "LEXORA_INTERNAL_TOKEN": "b2" + STRONG,
    "LEXORA_DATA_KEY": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
    "DATABASE_URL": "postgresql+asyncpg://lexora:Zx81kQ3mPq7Ln2Vb@postgres:5432/lexora",
    "REDIS_URL": "redis://:Rr81kQ3mPq7Ln2Vb@redis:6379/0",
    "CORS_ORIGINS": "https://hr.example.ca",
    "APP_BASE_URL": "https://hr.example.ca",
}


# ── PII redaction ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,leak", [
    ("employee SIN 046 454 286 saved", "046 454 286"),
    ("sin=046454286", "046454286"),
    ("SIN 046-454-286 linked", "046-454-286"),
    ("bank a/c 1234567 transit 12345 inst 004", "1234567"),
    ("transit=12345 institution=004", "12345"),
    ('payload {"salary": 125000, "name": "x"}', "125000"),
    ("annual_salary=78000 hourly_rate=32.5", "78000"),
    ("net_pay: 54321.50", "54321.50"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij", "eyJhbGci"),
    ("password=Hunter2!!", "Hunter2"),
])
def test_redact_masks_pii(raw, leak):
    assert leak not in ops.redact(raw)


def test_redact_keeps_ids_and_plain_text():
    s = "employee 3f2b8c1e-0d4a-4b6e-9f0a-1234567890ab updated leave status 200"
    assert ops.redact(s) == s


def _capture(service="t"):
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(ops.JsonFormatter(service))
    h.addFilter(ops.PIIRedactingFilter())
    lg = logging.getLogger(f"lexora.test.{id(buf)}")
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    return lg, buf


def test_log_filter_redacts_args_extras_and_exceptions():
    lg, buf = _capture()
    lg.info("saved %s with salary=%s", "046454286", 99000, extra={"sin": "046454286", "acct": "50100123456789"})
    try:
        raise ValueError("bad sin 046 454 286")
    except ValueError:
        lg.exception("failed for SIN 046-454-286")
    out = buf.getvalue()
    for leak in ("046454286", "99000", "50100123456789", "046 454 286", "046-454-286"):
        assert leak not in out, leak
    first = json.loads(out.splitlines()[0])
    assert first["level"] == "info" and first["service"] == "t"


# ── production secret gate ────────────────────────────────────────────────────

def test_good_production_env_passes():
    assert ops.production_problems(dict(GOOD_ENV)) == []


@pytest.mark.parametrize("key,value,needle", [
    ("JWT_SECRET", "dev-only-insecure-key-change-me", "JWT_SECRET"),
    ("JWT_SECRET", "short", "JWT_SECRET"),
    ("LEXORA_INTERNAL_TOKEN", "", "LEXORA_INTERNAL_TOKEN"),
    ("LEXORA_DATA_KEY", "", "LEXORA_DATA_KEY"),
    ("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/lexora", "DATABASE_URL"),
    ("REDIS_URL", "redis://redis:6379/0", "REDIS_URL"),
    ("CORS_ORIGINS", "*", "wildcard"),
    ("CORS_ORIGINS", "http://hr.example.ca", "non-https"),
    ("CORS_ORIGINS", "https://localhost:3100", "localhost"),
])
def test_each_default_is_refused(key, value, needle):
    env = dict(GOOD_ENV, **{key: value})
    probs = ops.production_problems(env)
    assert any(needle in p for p in probs), probs
    # values never appear in the messages
    if len(value) > 6:
        assert not any(value in p for p in probs)


def test_internal_token_equal_to_jwt_refused():
    env = dict(GOOD_ENV, LEXORA_INTERNAL_TOKEN=GOOD_ENV["JWT_SECRET"])
    assert any("differ" in p for p in ops.production_problems(env))


def test_enforce_exits_78_and_forces_dev_links_off():
    env = dict(GOOD_ENV, JWT_SECRET="dev-only-insecure-key-change-me", LEXORA_DEV_SHOW_LINKS="1")
    with pytest.raises(SystemExit) as ei:
        ops.enforce_production_config("auth", env)
    assert ei.value.code == 78
    assert "LEXORA_DEV_SHOW_LINKS" not in env
    ok = dict(GOOD_ENV, LEXORA_DEV_SHOW_LINKS="true")
    ops.enforce_production_config("auth", ok)            # no raise
    assert "LEXORA_DEV_SHOW_LINKS" not in ok


# ── request id, /ready precedence over a catch-all, body limit ─────────────────

def _gateway_like(monkeypatch):
    monkeypatch.delenv("LEXORA_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///x.db")    # → db check "skipped"
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LEXORA_MAX_BODY_MB", "0.001")                 # ~1 KB
    app = FastAPI()

    @app.api_route("/{service}", methods=["GET", "POST"])
    async def catch_all(service: str, request: Request):              # like the gateway proxy
        body = await request.body()
        return {"service": service, "rid": request.headers.get("x-request-id"), "n": len(body)}

    ops.install(app, "api-gateway")
    return TestClient(app)


def test_ready_and_health_beat_catch_all(monkeypatch):
    c = _gateway_like(monkeypatch)
    r = c.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/employees").json()["service"] == "employees"


def test_request_id_generated_propagated_and_echoed(monkeypatch):
    c = _gateway_like(monkeypatch)
    r = c.get("/employees", headers={"X-Request-Id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123" and r.json()["rid"] == "abc-123"
    r = c.get("/employees", headers={"X-Request-Id": "bad id <script>"})
    rid = r.headers["x-request-id"]
    assert rid != "bad id <script>" and len(rid) == 32 and r.json()["rid"] == rid


def test_body_limit_413(monkeypatch):
    c = _gateway_like(monkeypatch)
    assert c.post("/employees", content=b"x" * 500).json()["n"] == 500
    r = c.post("/employees", content=b"x" * 5000)
    assert r.status_code == 413 and r.headers.get("x-request-id")


def test_ready_reports_503_when_db_down(monkeypatch):
    monkeypatch.delenv("LEXORA_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/none")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LEXORA_READY_TIMEOUT_SEC", "0.5")
    app = FastAPI()
    ops.install(app, "leave")
    r = TestClient(app).get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["db"] == "down"
