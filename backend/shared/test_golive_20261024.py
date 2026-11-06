"""Go-live 2026-10-24: the production boot guard refuses dev-only switches, warns about risky
business settings, refuses demo data in a production database; the gateway adds API security headers.
Run:  cd backend && python -m pytest shared/test_golive_20261024.py -q
"""
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from shared import ops_runtime as ops
from shared.test_ops_runtime import GOOD_ENV

GW_ENV = dict(GOOD_ENV, LEXORA_ALLOWED_HOSTS="hr.example.ca,api-gateway", TRUST_FORWARDED_FOR="1")


def test_good_env_passes_for_every_service_and_gateway():
    assert ops.production_problems(dict(GOOD_ENV), "auth") == []
    assert ops.production_problems(dict(GW_ENV), "api-gateway") == []


@pytest.mark.parametrize("key,value,needle", [
    ("CAPTCHA_MODE", "off", "CAPTCHA_MODE"),
    ("CAPTCHA_MODE", "OFF ", "CAPTCHA_MODE"),
    ("LEXORA_DIRECT_REGISTER", "1", "LEXORA_DIRECT_REGISTER"),
    ("DEBUG", "true", "DEBUG"),
    ("UVICORN_RELOAD", "1", "UVICORN_RELOAD"),
    ("LEXORA_UI_GRANT_DEV_KEY", "-----BEGIN PRIVATE KEY----- abc", "LEXORA_UI_GRANT_DEV_KEY"),
    ("LEXORA_DEV_BYPASS", "yes", "LEXORA_DEV_BYPASS"),
    ("LEXORA_ALLOW_INSECURE_TLS", "1", "LEXORA_ALLOW_INSECURE_TLS"),
])
def test_dev_only_switches_refused(key, value, needle):
    probs = ops.production_problems(dict(GOOD_ENV, **{key: value}), "auth")
    assert any(needle in p for p in probs), probs
    assert not any(value.strip() in p for p in probs if len(value.strip()) > 4)   # never the value


def test_names_that_only_look_like_dev_are_fine():
    env = dict(GOOD_ENV, LEXORA_DEVICE_WATCH_SECONDS="300", CAPTCHA_MODE="pow", LEXORA_DEV_SHOW_LINKS="1")
    assert ops.production_problems(env, "auth") == []          # DEV_SHOW_LINKS is forced off, not refused


@pytest.mark.parametrize("hosts,needle", [("", "LEXORA_ALLOWED_HOSTS"), ("*", "LEXORA_ALLOWED_HOSTS"),
                                          ("hr.example.ca,localhost", "localhost")])
def test_gateway_needs_a_host_allow_list(hosts, needle):
    probs = ops.production_problems(dict(GW_ENV, LEXORA_ALLOWED_HOSTS=hosts), "api-gateway")
    assert any(needle in p for p in probs), probs
    # the allow-list is a gateway setting only
    assert ops.production_problems(dict(GOOD_ENV, LEXORA_ALLOWED_HOSTS=hosts), "leave") == []


def test_gateway_flood_guard_cannot_be_switched_off():
    probs = ops.production_problems(dict(GW_ENV, LEXORA_GATEWAY_RPM="0"), "api-gateway")
    assert any("LEXORA_GATEWAY_RPM" in p for p in probs)


def test_warnings_are_loud_but_not_fatal(capsys):
    env = dict(GW_ENV, LEXORA_PUBLIC_SIGNUP="1", LEXORA_API_DOCS="1", LOG_LEVEL="debug")
    env.pop("TRUST_FORWARDED_FOR")
    w = ops.production_warnings(env, "api-gateway")
    assert any("TRUST_FORWARDED_FOR" in x for x in w) and any("LEXORA_API_DOCS" in x for x in w)
    assert any("PUBLIC_SIGNUP" in x for x in ops.production_warnings(env, "auth"))
    assert any("SMTP_HOST" in x for x in ops.production_warnings(env, "auth"))
    ops.enforce_production_config("api-gateway", env)           # no raise
    assert "PRODUCTION WARNING" in capsys.readouterr().err


def test_enforce_refuses_captcha_off_with_exit_78():
    with pytest.raises(SystemExit) as ei:
        ops.enforce_production_config("auth", dict(GOOD_ENV, CAPTCHA_MODE="off"))
    assert ei.value.code == 78


def test_mfa_zero_is_dropped_in_production():
    env = dict(GOOD_ENV, LEXORA_MFA_ENFORCE="0")
    ops.enforce_production_config("auth", env)
    assert "LEXORA_MFA_ENFORCE" not in env


# ── demo data in a production database ───────────────────────────────────────

def test_demo_data_guard_refuses_and_allows_explicit_opt_out(monkeypatch):
    async def found(url=None):
        return {"demo_orgs": 3, "demo_logins": 40}

    async def none(url=None):
        return {}

    import asyncio
    monkeypatch.setattr(ops, "demo_data_markers", found)
    monkeypatch.delenv("LEXORA_ALLOW_DEMO_DATA", raising=False)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(ops.enforce_no_demo_data("auth"))
    assert ei.value.code == 78
    monkeypatch.setenv("LEXORA_ALLOW_DEMO_DATA", "1")
    asyncio.run(ops.enforce_no_demo_data("auth"))                # opt-out: no raise
    monkeypatch.delenv("LEXORA_ALLOW_DEMO_DATA")
    monkeypatch.setattr(ops, "demo_data_markers", none)
    asyncio.run(ops.enforce_no_demo_data("auth"))                # empty DB: no raise


def test_demo_data_guard_runs_after_the_service_startup(monkeypatch):
    order = []

    async def found(url=None):
        order.append("guard")
        return {"demo_orgs": 1}

    @asynccontextmanager
    async def life(app):
        order.append("init_db")
        yield

    monkeypatch.setattr(ops, "demo_data_markers", found)
    monkeypatch.delenv("LEXORA_ALLOW_DEMO_DATA", raising=False)
    app = FastAPI(lifespan=life)
    ops._install_demo_data_guard(app, "auth")
    import asyncio

    async def _boot():
        async with app.router.lifespan_context(app):
            order.append("serving")

    with pytest.raises(SystemExit) as ei:      # uvicorn: lifespan.startup.failed -> process exits
        asyncio.run(_boot())
    assert ei.value.code == 78
    assert order == ["init_db", "guard"]


def test_demo_markers_skip_non_postgres():
    import asyncio
    assert asyncio.run(ops.demo_data_markers("sqlite+aiosqlite:///x.db")) == {}


def test_demo_markers_do_not_match_real_customer_codes():
    # codes are not markers (a real "Maple Foods" would be MAPLE); only exact demo names / .example logins
    assert "MAPLE" not in ops.DEMO_ORG_NAMES and "Maple Logistics Inc." in ops.DEMO_ORG_NAMES
    assert all(e.endswith((".io", ".in", ".ca")) for e in ops.DEMO_EMAILS)


# ── API security headers ─────────────────────────────────────────────────────

def _hdr_app(hsts=""):
    app = FastAPI()

    @app.get("/employees")
    async def emps():
        return [{"id": 1}]

    @app.get("/payroll/payslip.pdf")
    async def pdf():
        return Response(b"%PDF-1.4", media_type="application/pdf")

    @app.get("/auth/esign/public/tok/pdf")
    async def esign():
        return Response(b"%PDF-1.4", media_type="application/pdf")

    @app.get("/files/x")
    async def own_csp():
        return Response(b"x", media_type="image/png", headers={"Content-Security-Policy": "sandbox",
                                                                "Cache-Control": "private, max-age=60"})
    app.add_middleware(ops.SecurityHeadersMiddleware, hsts=hsts)
    return TestClient(app)


def test_json_answers_get_the_strict_set():
    r = _hdr_app().get("/employees")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "default-src 'none'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    assert "strict-transport-security" not in h and "server" not in h


def test_files_keep_working_and_own_headers_win():
    c = _hdr_app(hsts="max-age=31536000; includeSubDomains")
    r = c.get("/payroll/payslip.pdf")
    assert "content-security-policy" not in r.headers and r.headers["x-frame-options"] == "DENY"
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")
    r = c.get("/files/x")
    assert r.headers["content-security-policy"] == "sandbox" and r.headers["cache-control"] == "private, max-age=60"


def test_esign_document_is_frameable_by_our_own_page_only():
    r = _hdr_app().get("/auth/esign/public/tok/pdf")
    assert r.headers["x-frame-options"] == "SAMEORIGIN" and r.headers["content-security-policy"] == "frame-ancestors 'self'"


def test_404_and_errors_carry_headers_too():
    r = _hdr_app().get("/nope")
    assert r.status_code == 404 and r.headers["x-frame-options"] == "DENY"


def test_ui_grant_key_required_when_the_auth_service_ships_it(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "_ui_grant_present", lambda: True)
    probs = ops.production_problems(dict(GOOD_ENV), "auth")
    assert any("UI_GRANT_PRIVATE_KEY" in p for p in probs)
    # 2026-10-26: the guard now LOADS the key (shared/test_ui_grant_key_20261026.py) — a real P-256 key file
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = tmp_path / "ui_grant.pem"
    pem.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ok = dict(GOOD_ENV, UI_GRANT_PRIVATE_KEY_FILE=str(pem), UI_GRANT_KID="k2")
    assert ops.production_problems(ok, "auth") == []
    assert any("UI_GRANT_KID" in p for p in ops.production_problems(dict(ok, UI_GRANT_KID="dev"), "auth"))
    assert ops.production_problems(dict(GOOD_ENV), "leave") == []      # other services never need it


def test_gateway_permission_check_cannot_be_switched_off():
    probs = ops.production_problems(dict(GW_ENV, LEXORA_RBAC_ENFORCE="off"), "api-gateway")
    assert any("LEXORA_RBAC_ENFORCE" in p for p in probs)
    assert ops.production_problems(dict(GW_ENV, LEXORA_RBAC_ENFORCE="all"), "api-gateway") == []
