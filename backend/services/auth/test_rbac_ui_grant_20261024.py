"""Org Roles & Permissions + the signed UI grant (go-live 2026-10-24).

Run:  cd backend/services/auth && PYTHONPATH=../../ python -m pytest test_rbac_ui_grant_20261024.py -q
"""
import base64
import json
import time
import uuid
from contextlib import asynccontextmanager

import jwt
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import http_errors
import models  # noqa: F401
import ui_grant
from rbac_routes import router as rbac_router
from routers import router as auth_router
from shared.utils import identity
from shared.utils.security import decode_token
from users_routes import router as users_router

GOOD_PW = "Str0ng!Passw0rd"


def _keypair():
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return pem, k.public_key()


PEM, PUB = _keypair()


@pytest.fixture(autouse=True)
def _grant_key(monkeypatch):
    monkeypatch.setenv("UI_GRANT_PRIVATE_KEY", PEM.replace("\n", "\\n"))
    monkeypatch.setenv("UI_GRANT_KID", "t1")
    monkeypatch.delenv("UI_GRANT_PRIVATE_KEY_FILE", raising=False)
    ui_grant.reset_for_tests()
    yield
    ui_grant.reset_for_tests()


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.run_sync(lambda c: identity.employees_ref.create(c, checkfirst=True))
    yield


app = FastAPI(lifespan=_lifespan)
http_errors.install(app)
for r in (auth_router, users_router, rbac_router):
    app.include_router(r)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def org(client):
    ui_grant.reset_for_tests()
    import os
    os.environ["UI_GRANT_PRIVATE_KEY"], os.environ["UI_GRANT_KID"] = PEM.replace("\n", "\\n"), "t1"
    r = client.post("/auth/register-org", json={
        "org_name": "Rbac Grant Org", "country": "CA", "province": "ON",
        "admin_email": "admin@rbac.example", "admin_password": GOOD_PW, "admin_first_name": "Asha"})
    assert r.status_code == 201, r.text
    tok = r.json()["access_token"]
    claims = decode_token(tok)
    oid = r.json()["org_id"]
    hdr = {"X-Org-Id": oid, "X-User-Id": claims["sub"], "X-Role": "org_admin", "X-User-Email": "admin@rbac.example"}

    def mk(email, role):
        x = client.post("/auth/users", headers=hdr, json={"email": email, "role": role, "send_invite": False,
                                                          "first_name": email.split("@")[0]})
        assert x.status_code == 201, x.text
        return x.json()["user_id"]
    ids = {"hr": mk("hr@rbac.example", "hr_admin"), "emp": mk("emp@rbac.example", "employee"),
           "emp2": mk("emp2@rbac.example", "employee")}
    return {"id": oid, "admin": claims["sub"], "hdr": hdr, "token": tok, "login": r.json(), **ids,
            "hr_hdr": {"X-Org-Id": oid, "X-User-Id": ids["hr"], "X-Role": "hr_admin", "X-User-Email": "hr@rbac.example"},
            "emp_hdr": {"X-Org-Id": oid, "X-User-Id": ids["emp"], "X-Role": "employee"}}


# ── signed UI grant ───────────────────────────────────────────────────────────

def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_login_returns_a_verifiable_grant_bound_to_the_token(org):
    g = org["login"]["ui_grant"]
    assert isinstance(g, str) and g.count(".") == 2
    assert jwt.get_unverified_header(g) == {"alg": "ES256", "kid": "t1", "typ": "jethro-ui-grant"}
    p = ui_grant.verify(g, {"t1": PUB})
    c = decode_token(org["token"])
    assert p["role"] == "org_admin" and p["sub"] == c["sub"] and p["sid"] == c["sid"] and p["exp"] == c["exp"]
    assert "roles.create" in p["perms"] and p["reach"] == "org" and p["ui_roles"] == ["org_admin"]


def test_tampered_or_foreign_grants_are_refused(org):
    g = org["login"]["ui_grant"]
    h, p, s = g.split(".")
    body = json.loads(base64.urlsafe_b64decode(p + "==="))
    body["role"] = "superadmin"
    with pytest.raises(ValueError):
        ui_grant.verify(f"{h}.{_b64(body)}.{s}", {"t1": PUB})                   # edited payload
    _other_pem, other_pub = _keypair()
    with pytest.raises(ValueError):
        ui_grant.verify(g, {"t1": other_pub})                                 # another key under the same kid
    with pytest.raises(ValueError):
        ui_grant.verify(g, {"k9": PUB})                                       # unknown kid
    hs = jwt.encode({"typ": "jethro-ui-grant", "role": "org_admin", "exp": int(time.time()) + 60}, "s" * 40,
                    algorithm="HS256", headers={"kid": "t1"})
    with pytest.raises(ValueError):
        ui_grant.verify(hs, {"t1": PUB})                                      # wrong alg
    priv = serialization.load_pem_private_key(PEM.encode(), None)
    old = jwt.encode({"typ": "jethro-ui-grant", "role": "x", "exp": int(time.time()) - 5}, priv, algorithm="ES256",
                     headers={"kid": "t1"})
    with pytest.raises(ValueError):
        ui_grant.verify(old, {"t1": PUB})                                     # expired


def test_no_key_no_grant_and_dev_key_refused_in_production(monkeypatch):
    monkeypatch.delenv("UI_GRANT_PRIVATE_KEY", raising=False)
    ui_grant.reset_for_tests()
    assert ui_grant.sign({"sub": "u", "exp": int(time.time()) + 60}, {"role": "employee"}) is None
    monkeypatch.setenv("UI_GRANT_PRIVATE_KEY", PEM)
    monkeypatch.setenv("UI_GRANT_KID", "dev")
    monkeypatch.setenv("LEXORA_ENV", "production")
    ui_grant.reset_for_tests()
    assert not ui_grant.signer_ready()
    monkeypatch.setenv("LEXORA_ENV", "test")
    ui_grant.reset_for_tests()
    assert ui_grant.signer_ready()
    monkeypatch.setenv("UI_GRANT_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----")
    ui_grant.reset_for_tests()
    assert not ui_grant.signer_ready()


def test_me_signs_the_callers_bearer_token_only(client, org):
    hdr = {**org["hdr"], "Authorization": f"Bearer {org['token']}"}
    me = client.get("/auth/me", headers=hdr).json()
    p = ui_grant.verify(me["ui_grant"], {"t1": PUB})
    assert p["sub"] == org["admin"] and p["role"] == "org_admin" and me["role"] == "org_admin"
    # a bearer token of ANOTHER login (or none): no grant
    other = {**org["emp_hdr"], "Authorization": f"Bearer {org['token']}"}
    assert client.get("/auth/me", headers=other).json()["ui_grant"] is None
    assert client.get("/auth/me", headers=org["hdr"]).json()["ui_grant"] is None
    # a refresh token is not an access token
    from shared.utils.security import create_refresh_token
    rt = create_refresh_token({"sub": org["admin"], "org_id": org["id"]})
    assert client.get("/auth/me", headers={**org["hdr"], "Authorization": f"Bearer {rt}"}).json()["ui_grant"] is None


# ── custom roles ──────────────────────────────────────────────────────────────

def _role(client, hdr, **kw):
    body = {"name": "Leave desk", "description": "Sees and approves leave org-wide", "base_role": "hr_manager",
            "reach": "org", "permissions": ["leave.view", "leave.approve"], **kw}
    return client.post("/auth/rbac/roles", headers=hdr, json=body)


def test_catalogue_and_system_roles_read_only(client, org):
    c = client.get("/auth/rbac/catalogue", headers=org["hdr"]).json()
    keys = {m["key"] for m in c["modules"]}
    assert {"leave", "payroll", "statutory", "roles"} <= keys and "books" not in keys
    sysr = {r["key"]: r for r in c["system_roles"]}
    assert sysr["org_admin"]["can_clone"] is False and sysr["org_admin"]["platform_only"] is True
    assert sysr["hr_admin"]["can_clone"] is True and sysr["hr_admin"]["permissions"]
    assert client.get("/auth/rbac/roles", headers=org["emp_hdr"]).json()["code"] == "RBAC_READ_DENIED"


def test_create_assign_preview_retire_and_history(client, org):
    r = _role(client, org["hdr"])
    assert r.status_code == 201, r.text
    role = r.json()
    assert role["key"] == "leave_desk" and role["status"] == "active"
    assert _role(client, org["hdr"]).json()["code"] == "RBAC_NAME_TAKEN"
    # give it to an employee login
    a = client.post(f"/auth/rbac/roles/{role['id']}/members", headers=org["hdr"], json={"user_ids": [org["emp"]]})
    assert a.status_code == 200 and a.json()["added"] == 1
    pv = client.get(f"/auth/rbac/users/{org['emp']}", headers=org["hdr"]).json()
    assert "leave.approve" in pv["perms"] and pv["ui_roles"] == ["employee", "hr_manager"] and pv["reach"] == "org"
    assert "custom:leave_desk" in pv["sources"]["leave.approve"]
    # the gateway reads it live
    state = client.portal.call(identity.fetch_user_state, org["emp"])
    assert [c["key"] for c in state["custom_roles"]] == ["leave_desk"]
    # the employee's own grant now carries the role
    me = client.get("/auth/rbac/me", headers=org["emp_hdr"]).json()
    assert "leave.approve" in me["perms"]
    # in use -> cannot be deleted, only retired
    d = client.delete(f"/auth/rbac/roles/{role['id']}", headers=org["hdr"])
    assert d.status_code == 409 and d.json()["code"] == "RBAC_ROLE_IN_USE"
    rt = client.post(f"/auth/rbac/roles/{role['id']}/retire", headers=org["hdr"]).json()
    assert rt["status"] == "retired"
    assert client.portal.call(identity.fetch_user_state, org["emp"])["custom_roles"] == []
    x = client.post(f"/auth/rbac/roles/{role['id']}/members", headers=org["hdr"], json={"user_ids": [org["emp2"]]})
    assert x.status_code == 422 and x.json()["code"] == "RBAC_ROLE_RETIRED"
    client.post(f"/auth/rbac/roles/{role['id']}/restore", headers=org["hdr"])
    # edit bumps the version; history has every step
    e = client.put(f"/auth/rbac/roles/{role['id']}", headers=org["hdr"], json={
        "name": "Leave desk", "base_role": "hr_manager", "reach": "org", "permissions": ["leave.view"]})
    assert e.status_code == 200 and e.json()["version"] == 2
    h = client.get("/auth/rbac/history", headers=org["hdr"], params={"role_id": role["id"]}).json()
    acts = [x["action"] for x in h]
    for want in ("role.create", "user.assign", "role.retire", "role.restore", "role.edit"):
        assert want in acts, (want, acts)
    # never-assigned role can be deleted
    tmp = _role(client, org["hdr"], name="Temp role").json()
    assert client.delete(f"/auth/rbac/roles/{tmp['id']}", headers=org["hdr"]).json()["deleted"] is True


def test_org_admin_base_and_clone_are_platform_only(client, org):
    r = _role(client, org["hdr"], name="Mini admin", base_role="org_admin")
    assert r.status_code == 403 and r.json()["code"] == "RBAC_BASE_PLATFORM_ONLY"
    r = _role(client, org["hdr"], name="Mini admin 2", cloned_from="org_admin")
    assert r.status_code == 403 and r.json()["code"] == "RBAC_BASE_PLATFORM_ONLY"


def test_escalation_is_refused(client, org):
    # Lexora (no Books / payouts module): the HR Admin system role holds every permission a custom base carries, so
    # a system HR Admin cannot escalate through a base; a NARROWED granter is covered in shared/utils/test_rbac_grant.py.
    # A role can never carry more than its base: settings.edit is not on the Payroll Admin base → refused for everyone
    for hdr in (org["hr_hdr"], org["hdr"]):
        r = _role(client, hdr, name="Settings via payroll", base_role="payroll_admin", permissions=["settings.edit"])
        assert r.status_code == 422 and r.json()["code"] == "RBAC_OVER_BASE"
    # an Org Admin builds a pay-run role; HR (who holds payroll.run) may hand it out
    big = _role(client, org["hdr"], name="Pay runner", base_role="payroll_admin", permissions=["payroll.run", "payroll.view"]).json()
    a = client.post(f"/auth/rbac/roles/{big['id']}/members", headers=org["hr_hdr"], json={"user_ids": [org["emp2"]]})
    assert a.status_code == 200, a.text
    assert client.put(f"/auth/rbac/users/{org['emp2']}/roles", headers=org["hdr"], json={"role_ids": []}).status_code == 200   # leave emp2 clean
    # logins / roles management never goes on a custom role
    r = _role(client, org["hdr"], name="Login maker", base_role="hr_admin", permissions=["users.create"])
    assert r.status_code == 422 and r.json()["code"] == "RBAC_NON_DELEGABLE"
    # a custom role acting through the gateway never writes roles
    acting = {**org["hr_hdr"], "X-Acting-Role": "some_role"}
    assert _role(client, acting, name="Sneaky").json()["code"] == "RBAC_MANAGE_DENIED"
    # non-managers cannot write at all
    assert _role(client, org["emp_hdr"], name="Mine").json()["code"] == "RBAC_MANAGE_DENIED"


def test_self_and_org_admin_targets(client, org):
    role = _role(client, org["hdr"], name="Hiring helper", base_role="hr_manager", permissions=["hiring.view"]).json()
    r = client.put(f"/auth/rbac/users/{org['hr']}/roles", headers=org["hr_hdr"], json={"role_ids": [role["id"]]})
    assert r.status_code == 403 and r.json()["code"] == "RBAC_SELF_ASSIGN"
    r = client.put(f"/auth/rbac/users/{org['admin']}/roles", headers=org["hr_hdr"], json={"role_ids": [role["id"]]})
    assert r.status_code == 403 and r.json()["code"] == "RBAC_ORG_ADMIN_PLATFORM_ONLY"
    ok = client.put(f"/auth/rbac/users/{org['emp2']}/roles", headers=org["hr_hdr"], json={"role_ids": [role["id"]]})
    assert ok.status_code == 200 and ok.json()["changed"] == 1
    assert client.put(f"/auth/rbac/users/{uuid.uuid4()}/roles", headers=org["hdr"],
                      json={"role_ids": []}).json()["code"] == "RBAC_USER_NOT_FOUND"
    assert client.get("/auth/rbac/roles/not-a-uuid", headers=org["hdr"]).json()["code"] == "RBAC_BAD_ID"


def test_org_admin_logins_only_by_the_platform(client, org):
    r = client.post("/auth/users", headers=org["hdr"], json={"email": "second.admin@rbac.example",
                                                             "role": "org_admin", "send_invite": False})
    assert r.status_code == 403 and r.json()["code"] == "AUTH_ROLE_PLATFORM_ONLY"
    r = client.post(f"/auth/users/{org['emp2']}/role", headers=org["hdr"], json={"role": "org_admin"})
    assert r.status_code == 403 and r.json()["code"] == "AUTH_ROLE_PLATFORM_ONLY"
    sup = {"X-Org-Id": str(uuid.uuid4()), "X-User-Id": str(uuid.uuid4()), "X-Role": "superadmin"}
    r = client.post(f"/auth/platform/orgs/{org['id']}/admins", headers=sup, json={"email": "second.admin@rbac.example"})
    assert r.status_code == 201 and r.json()["role"] == "org_admin"
    r = client.post(f"/auth/platform/orgs/{org['id']}/users/{org['emp2']}/role", headers=sup, json={"role": "hr_manager"})
    assert r.status_code == 200 and r.json()["role"] == "hr_manager"
    assert client.post(f"/auth/platform/orgs/{org['id']}/admins", headers=org["hdr"],
                       json={"email": "x@rbac.example"}).status_code == 403
    users = client.get(f"/auth/platform/orgs/{org['id']}/users", headers=sup).json()
    assert any(u["email"] == "second.admin@rbac.example" and u["role"] == "org_admin" for u in users)


def test_rbac_tables_on_the_shared_base():
    names = set(database.Base.metadata.tables)
    assert {"rbac_roles", "rbac_user_roles", "rbac_changes"} <= names
    assert isinstance(sa.inspect(database.Base.metadata.tables["rbac_roles"]).c.permissions.type, sa.JSON)
