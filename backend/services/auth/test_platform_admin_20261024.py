"""Go-live 2026-10-24: the first platform operator on an EMPTY database (platform_admin.py) and the
fresh-tenant path it opens (operator signs in → creates a customer tenant → that admin signs in)."""
import asyncio
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import models  # noqa: F401
import platform_admin
from routers import router as auth_router
from shared.utils import audit, identity  # noqa: F401  (registers audit_events / employees ref)

PW = "Opr!Strong-Pass-2026x"


async def _sqlite_schema():
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.run_sync(lambda c: identity.employees_ref.create(c, checkfirst=True))
        await conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS users_org_email_uk ON users (org_id, lower(email))"))


@asynccontextmanager
async def _lifespan(app):
    await _sqlite_schema()
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(auth_router)


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(platform_admin, "_init_schema", _sqlite_schema)      # the Postgres boot DDL is not SQLite
    with TestClient(app) as c:
        yield c
    mp.undo()


def _run(coro):
    return asyncio.run(coro)


def test_weak_password_and_fake_email_refused(client):
    with pytest.raises(ValueError, match="Password needs"):
        _run(platform_admin.create_superadmin("ops@golive-test.ca", "short"))
    with pytest.raises(ValueError, match="real e-mail"):
        _run(platform_admin.create_superadmin("ops@demo.example", PW))


def test_create_once_then_fresh_tenant_path(client):
    import sqlalchemy as sa
    from database import SessionLocal
    from models import User

    async def _supers():
        async with SessionLocal() as s:
            return (await s.execute(sa.select(sa.func.count()).select_from(User)
                                    .where(User.role == "superadmin"))).scalar_one()

    before = _run(_supers())
    if before == 0:
        out = _run(platform_admin.create_superadmin("ops@golive-test.ca", PW, "Asha", "Rao"))
        assert out["org_code"] == "PLATFORM" and "password" not in str(out).lower()
        # a second one needs the explicit flag
        with pytest.raises(ValueError, match="already exist"):
            _run(platform_admin.create_superadmin("ops2@golive-test.ca", PW))
    else:                                   # another suite in this process created one first
        _run(platform_admin.create_superadmin("ops@golive-test.ca", PW, "Asha", "Rao", additional=True))
    # the same e-mail twice is refused even with --additional
    with pytest.raises(ValueError, match="already has a login"):
        _run(platform_admin.create_superadmin("ops@golive-test.ca", PW, additional=True))

    # operator signs in WITHOUT an org code (conftest: MFA enforcement off, CAPTCHA off)
    r = client.post("/auth/login", json={"email": "ops@golive-test.ca", "password": PW})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "superadmin"

    # the operator provisions a customer tenant (gateway would stamp X-Role from the token)
    r = client.post("/auth/platform/orgs", headers={"X-Role": "superadmin"},
                    json={"org_name": "Golive Fresh Inc.", "country": "CA", "province": "ON",
                          "plan": "growth", "admin_email": "admin@golive-fresh.ca",
                          "admin_first_name": "Meera", "admin_password": "Maple!Leaf-Quartz-2026"})
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    # the tenant admin signs in with the org code and must change the operator-chosen password
    r = client.post("/auth/login", json={"org_code": code, "email": "admin@golive-fresh.ca",
                                         "password": "Maple!Leaf-Quartz-2026"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "org_admin" and r.json().get("password_change_required") is True


def test_cli_list_and_password_stdin(monkeypatch, capsys, client):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("weak\n"))
    assert platform_admin.main(["create", "--email", "ops3@golive-test.ca", "--password-stdin", "--additional"]) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "weak" not in err          # the password itself is never echoed
    assert platform_admin.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "ops@golive-test.ca" in out and PW not in out
