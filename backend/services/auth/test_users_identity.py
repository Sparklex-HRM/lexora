"""Logins for employees: link-token life-cycle, disable = sessions rejected,
per-org e-mail, one login per employee, who may do what.
Run:  cd backend/services/auth && PYTHONPATH=../../ python -m pytest test_users_identity.py -q
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import models  # noqa: F401
from routers import pick_login, router as auth_router
from security_routes import router as security_router
from shared.utils import audit, identity  # noqa: F401  (audit registers audit_events)
from shared.utils.security import decode_token
from users_routes import can_grant, forgot_limiter, link_limiter, router as users_router

GOOD_PW = "Str0ng!Passw0rd"
EMP_A, EMP_B = str(uuid.uuid4()), str(uuid.uuid4())


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.run_sync(lambda c: identity.employees_ref.create(c, checkfirst=True))
        # The same guards the Postgres boot migration creates.
        await conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS users_org_email_uk ON users (org_id, lower(email))"))
        await conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS users_org_employee_uk ON users (org_id, employee_id) "
                                   "WHERE employee_id IS NOT NULL"))
    yield


app = FastAPI(lifespan=_lifespan)
__import__("http_errors").install(app)          # refusals carry {"detail", "code"} as in main.py
for r in (auth_router, security_router, users_router):
    app.include_router(r)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def org(client):
    """A Canadian org with its admin, plus two employee rows to attach logins to."""
    r = client.post("/auth/register-org", json={
        "org_name": "Maple Logistics", "country": "CA", "province": "ON",
        "admin_email": "admin@maple.example", "admin_password": GOOD_PW, "admin_first_name": "Asha"})
    assert r.status_code == 201, r.text
    org_id = r.json()["org_id"]
    admin = decode_token(r.json()["access_token"])

    async def seed():
        async with database.SessionLocal() as s:
            for eid, no, fn, mail in ((EMP_A, "ML-0001", "Ravi", "ravi@maple.example"),
                                      (EMP_B, "ML-0002", "Meena", "meena@maple.example")):
                await s.execute(sa.insert(identity.employees_ref).values(
                    id=uuid.UUID(eid), org_id=uuid.UUID(org_id), employee_number=no, first_name=fn,
                    last_name="Kumar", email=mail, status="active", is_active=True))
            # this module's org only (other modules in the same test run register orgs in the same SQLite file)
            code = (await s.execute(sa.select(models.Organization.code).where(
                models.Organization.id == uuid.UUID(org_id)))).scalar()
            await s.commit()
            return code
    code = client.portal.call(seed)
    return {"id": org_id, "code": code, "admin_id": admin["sub"],
            "hdr": {"X-Org-Id": org_id, "X-User-Id": admin["sub"], "X-Role": "org_admin",
                    "X-User-Email": "admin@maple.example"}}


def _hdr(org, role, user_id=None):
    return {"X-Org-Id": org["id"], "X-Role": role, "X-User-Id": user_id or str(uuid.uuid4())}


def _token(link: str) -> str:
    return link.rsplit("/", 1)[1]


def _login(client, org, email, password, code=None):
    link_limiter._hits.clear()
    return client.post("/auth/login", json={"email": email, "password": password,
                                            "org_code": code if code is not None else org["code"]})


# ── pure rules ────────────────────────────────────────────────────────────────

def test_role_aliases_and_unknown_role():
    assert identity.normalise_role("hr") == "hr_admin"
    assert identity.normalise_role("payroll") == "payroll_admin"
    assert identity.normalise_role(None) == "employee"
    assert identity.normalise_role("Manager") == "manager"
    with pytest.raises(identity.IdentityError):
        identity.normalise_role("superadmin")          # never assignable from an org
    with pytest.raises(identity.IdentityError):
        identity.normalise_role("root")


def test_token_problem_states():
    now = datetime.now(timezone.utc)
    assert identity.token_problem(None) == "invalid"
    assert identity.token_problem({"used_at": now, "expires_at": now + timedelta(hours=1)}) == "used"
    assert identity.token_problem({"used_at": None, "expires_at": now - timedelta(seconds=1)}) == "expired"
    assert identity.token_problem({"used_at": None, "expires_at": now + timedelta(hours=1)}) is None
    raw, h = identity.new_token()
    assert identity.hash_token(raw) == h and raw != h and len(h) == 64


def test_who_may_grant_which_role():
    # 2026-10-24 (Entersoft Medium): an Org Admin is made ONLY by the Lexora HRM platform operator
    assert not can_grant("org_admin", "org_admin") and can_grant("org_admin", "hr_admin")
    assert can_grant("hr_admin", "employee") and can_grant("hr_admin", "payroll_admin")
    assert not can_grant("hr_admin", "org_admin")       # HR cannot mint an admin
    assert not can_grant("org_admin", "superadmin")
    assert not can_grant("manager", "employee") and not can_grant("employee", "employee")


def test_pick_login_uses_the_org_code():
    class U:
        def __init__(self, org_id, role="employee"):
            self.org_id, self.role = org_id, role
    a, b, sup = U("org-a"), U("org-b"), U(None, "superadmin")
    assert pick_login([a, b], "org-b", has_code=True) is b
    assert pick_login([a, b], "org-zzz", has_code=True) is None
    assert pick_login([a, b], None, has_code=True) is None          # unknown code
    assert pick_login([a], None, has_code=False) is a               # → "code is required"
    assert pick_login([a, sup], None, has_code=False) is sup        # platform operator, no code
    assert pick_login([a, b], None, has_code=False) is None


# ── create + invite link ─────────────────────────────────────────────────────

def test_create_login_then_set_password_once(client, org):
    r = client.post("/auth/users", headers=org["hdr"], json={
        "employee_id": EMP_A, "email": "ravi@maple.example", "role": "employee", "send_invite": True})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True and body["status"] == "invited" and body["invited"] is True
    assert body["email_sent"] is False and "/set-password/" in body["link"]   # no SMTP → link handed to HR
    assert body["user"]["employee_id"] == EMP_A and body["user"]["employee_id"] is not None
    tok = _token(body["link"])

    # Nobody can sign in before the password is set.
    assert _login(client, org, "ravi@maple.example", GOOD_PW).status_code == 401

    info = client.get(f"/auth/invite/{tok}")
    assert info.status_code == 200 and info.json()["org_name"] == "Maple Logistics"
    assert info.json()["email"] == "ravi@maple.example"

    weak = client.post(f"/auth/invite/{tok}", json={"password": "short"})
    assert weak.status_code == 422                                   # policy applies; link NOT burnt
    assert client.get(f"/auth/invite/{tok}").status_code == 200

    ok = client.post(f"/auth/invite/{tok}", json={"password": GOOD_PW})
    assert ok.status_code == 200 and ok.json()["org_code"] == org["code"]

    # Single use.
    again = client.post(f"/auth/invite/{tok}", json={"password": "An0ther!Passw0rd"})
    assert again.status_code == 410
    assert client.get(f"/auth/invite/{tok}").status_code == 410

    li = _login(client, org, "ravi@maple.example", GOOD_PW)
    assert li.status_code == 200
    claims = decode_token(li.json()["access_token"])
    assert claims["employee_id"] == EMP_A and claims["role"] == "employee"   # linked both ways
    # Signing in straight after setting the password gives a token the gateway accepts.
    assert _gateway_verdict(client, li.json()["access_token"]) is None


def test_invite_link_is_not_a_reset_link_and_garbage_is_404(client):
    assert client.get("/auth/invite/not-a-real-token").status_code == 404
    assert client.get("/auth/password/reset/not-a-real-token").status_code == 404


def test_expired_link_is_refused_and_resend_kills_the_old_link(client, org):
    r = client.post("/auth/users", headers=org["hdr"], json={
        "employee_id": EMP_B, "email": "meena@maple.example", "role": "manager"})
    assert r.status_code == 201
    uid, first = r.json()["user_id"], _token(r.json()["link"])

    r2 = client.post(f"/auth/users/{uid}/resend", headers=org["hdr"])
    assert r2.status_code == 200
    second = _token(r2.json()["link"])
    assert second != first
    assert client.get(f"/auth/invite/{first}").status_code == 410      # older link is dead
    assert client.get(f"/auth/invite/{second}").status_code == 200

    async def expire():
        async with database.SessionLocal() as s:
            await s.execute(sa.update(identity.user_tokens).where(
                identity.user_tokens.c.token_hash == identity.hash_token(second)).values(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
            await s.commit()
    client.portal.call(expire)
    assert client.get(f"/auth/invite/{second}").status_code == 410
    assert client.post(f"/auth/invite/{second}", json={"password": GOOD_PW}).status_code == 410

    # A fresh one works; the user list shows the right states.
    third = _token(client.post(f"/auth/users/{uid}/invite", headers=org["hdr"]).json()["link"])
    assert client.post(f"/auth/invite/{third}", json={"password": GOOD_PW}).status_code == 200
    # Already has a password → "invite" is refused, "reset" is the right action.
    assert client.post(f"/auth/users/{uid}/invite", headers=org["hdr"]).status_code == 409
    lst = client.get("/auth/users", headers=org["hdr"]).json()
    by_mail = {u["email"]: u for u in lst["items"]}
    assert by_mail["meena@maple.example"]["status"] == "active"
    assert by_mail["meena@maple.example"]["employee_number"] == "ML-0002"
    assert by_mail["meena@maple.example"]["employee_name"] == "Meena Kumar"


# ── uniqueness ───────────────────────────────────────────────────────────────

def test_one_login_per_employee_and_email_unique_per_org(client, org):
    # Same employee again → the SAME login comes back, nothing new is created.
    r = client.post("/auth/users", headers=org["hdr"], json={
        "employee_id": EMP_A, "email": "someone.else@maple.example", "role": "manager"})
    assert r.status_code == 201 and r.json()["created"] is False
    assert r.json()["email"] == "ravi@maple.example" and r.json()["role"] == "employee"

    # Another employee cannot take an e-mail that already has a login here.
    other = str(uuid.uuid4())
    r = client.post("/auth/users", headers=org["hdr"], json={
        "employee_id": other, "email": "RAVI@maple.example", "role": "employee"})
    assert r.status_code == 409

    r = client.get("/auth/users", headers=org["hdr"], params={"employee_id": EMP_A})
    assert r.json()["total"] == 1 and r.json()["items"][0]["email"] == "ravi@maple.example"


def test_same_email_may_exist_in_another_org_and_login_uses_the_code(client, org):
    r = client.post("/auth/register-org", json={
        "org_name": "Northwind Canada", "country": "CA", "province": "ON",
        "admin_email": "boss@northwind.example", "admin_password": GOOD_PW})
    org2 = r.json()["org_id"]
    hdr2 = {"X-Org-Id": org2, "X-Role": "org_admin", "X-User-Id": decode_token(r.json()["access_token"])["sub"]}
    emp2 = str(uuid.uuid4())
    r = client.post("/auth/users", headers=hdr2, json={
        "employee_id": emp2, "email": "ravi@maple.example", "role": "employee"})
    assert r.status_code == 201 and r.json()["created"] is True       # allowed: different org
    other_pw = "0ther!Passw0rdZZ"
    assert client.post(f"/auth/invite/{_token(r.json()['link'])}", json={"password": other_pw}).status_code == 200

    code2 = client.portal.call(_org_code, org2)
    a = _login(client, org, "ravi@maple.example", GOOD_PW)
    b = _login(client, org, "ravi@maple.example", other_pw, code=code2)
    assert a.status_code == 200 and a.json()["org_id"] == org["id"]
    assert b.status_code == 200 and b.json()["org_id"] == org2
    # Right password, wrong org → refused.
    assert _login(client, org, "ravi@maple.example", other_pw).status_code == 401
    # Org 2 never sees org 1's logins.
    assert all(u["email"] != "meena@maple.example"
               for u in client.get("/auth/users", headers=hdr2).json()["items"])


async def _org_code(org_id):
    async with database.SessionLocal() as s:
        return (await s.execute(sa.select(identity.organizations.c.code).where(
            identity.organizations.c.id == uuid.UUID(org_id)))).scalar()


# ── disable = sessions rejected ──────────────────────────────────────────────

def _gateway_verdict(client, token: str):
    """What the gateway decides for this token right now (no cache)."""
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "revocation", os.path.join(os.path.dirname(__file__), "..", "..", "api-gateway", "revocation.py"))
    rev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rev)
    claims = decode_token(token)
    state = client.portal.call(identity.fetch_user_state, claims["sub"])
    return rev.verdict(state, claims["iat"])


def test_disable_rejects_open_sessions_and_blocks_sign_in(client, org):
    token = _login(client, org, "ravi@maple.example", GOOD_PW).json()["access_token"]
    uid = decode_token(token)["sub"]
    assert _gateway_verdict(client, token) is None                     # working session

    r = client.post(f"/auth/users/{uid}/disable", headers=org["hdr"], json={"reason": "Left the company"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert r.json()["disabled_reason"] == "Left the company"

    assert _gateway_verdict(client, token) == "disabled"               # the OLD token stops working
    li = _login(client, org, "ravi@maple.example", GOOD_PW)
    assert li.status_code == 403 and "disabled" in li.json()["detail"]
    # Wrong password on a disabled login does not reveal that it is disabled.
    assert _login(client, org, "ravi@maple.example", "Wr0ng!Passw0rdX").status_code == 401
    # No link can be issued for a disabled login.
    assert client.post(f"/auth/users/{uid}/reset-password", headers=org["hdr"]).status_code == 409

    r = client.post(f"/auth/users/{uid}/enable", headers=org["hdr"])
    assert r.status_code == 200 and r.json()["status"] == "active"
    # Enabling does NOT revive the old session — the person signs in again.
    assert _gateway_verdict(client, token) == "stale"


def test_contract_helpers_disable_and_ensure(client, org):
    res = client.portal.call(identity.disable_logins_for_employee, org["id"], EMP_B, "Exit: last working day")
    assert res["disabled"] == 1 and len(res["user_ids"]) == 1
    again = client.portal.call(identity.disable_logins_for_employee, org["id"], EMP_B, "Exit")
    assert again == {"disabled": 0, "user_ids": []}                    # idempotent
    assert _login(client, org, "meena@maple.example", GOOD_PW).status_code == 403

    # ensure_ never re-enables a disabled login and never makes a second one.
    e = client.portal.call(identity.ensure_login_for_employee, org["id"], EMP_B, "meena@maple.example")
    assert e["created"] is False and e["status"] == "disabled" and e["invited"] is False

    fresh = str(uuid.uuid4())
    e = client.portal.call(identity.ensure_login_for_employee, org["id"], fresh, "new.joiner@maple.example", "hr")
    assert e["created"] is True and e["role"] == "hr_admin" and e["invited"] is True and e["link"]
    e2 = client.portal.call(identity.ensure_login_for_employee, org["id"], fresh, "new.joiner@maple.example")
    assert e2["created"] is False and e2["user_id"] == e["user_id"]

    # Every action is in the audit trail.
    async def actions():
        async with database.SessionLocal() as s:
            return {a for (a,) in (await s.execute(sa.text("SELECT action FROM audit_events"))).all()}
    seen = client.portal.call(actions)
    assert {"auth.user.create", "auth.user.invite", "auth.user.disable", "auth.user.enable",
            "auth.password.set"} <= seen


# ── who may do what ──────────────────────────────────────────────────────────

def test_only_hr_or_admin_manage_logins(client, org):
    for role in ("employee", "manager", "payroll_admin", "hr_manager"):
        r = client.post("/auth/users", headers=_hdr(org, role), json={
            "employee_id": str(uuid.uuid4()), "email": "x@maple.example"})
        assert r.status_code == 403, role
        assert client.get("/auth/users/unlinked-employees", headers=_hdr(org, role)).status_code == 403

    # HR can create an employee login, but not an admin one.
    hr = _hdr(org, "hr_admin")
    assert client.post("/auth/users", headers=hr, json={
        "employee_id": str(uuid.uuid4()), "email": "hr.made@maple.example", "role": "employee",
        "send_invite": False}).status_code == 201
    assert client.post("/auth/users", headers=hr, json={
        "employee_id": str(uuid.uuid4()), "email": "sneaky@maple.example", "role": "org_admin"}).status_code == 403
    # HR cannot touch the admin's login either.
    assert client.post(f"/auth/users/{org['admin_id']}/disable", headers=hr, json={}).status_code == 403
    assert client.post(f"/auth/users/{org['admin_id']}/role", headers=hr, json={"role": "employee"}).status_code == 403


def test_employee_sees_only_own_login(client, org):
    mine = decode_token(_login(client, org, "ravi@maple.example", GOOD_PW).json()["access_token"])["sub"]
    r = client.get("/auth/users", headers=_hdr(org, "employee", mine))
    assert r.status_code == 200 and [u["id"] for u in r.json()["items"]] == [mine]
    # Asking for someone else's login by employee id returns nothing.
    r = client.get("/auth/users", headers=_hdr(org, "employee", mine), params={"employee_id": EMP_B})
    assert r.json()["total"] == 0


def test_cannot_disable_self_or_the_last_admin(client, org):
    r = client.post(f"/auth/users/{org['admin_id']}/disable", headers=org["hdr"], json={})
    assert r.status_code == 409 and "own login" in r.json()["detail"]
    other_admin = {**org["hdr"], "X-User-Id": str(uuid.uuid4())}
    # 2026-10-24 (Entersoft Medium): another Org Admin may not touch an Org Admin login — the platform operator may
    r = client.post(f"/auth/users/{org['admin_id']}/disable", headers=other_admin, json={})
    assert r.status_code == 403 and r.json()["code"] == "AUTH_ORG_ADMIN_PLATFORM_ONLY"
    platform = {**org["hdr"], "X-User-Id": str(uuid.uuid4()), "X-Role": "superadmin"}
    r = client.post(f"/auth/users/{org['admin_id']}/disable", headers=platform, json={})
    assert r.status_code == 409 and "only active admin" in r.json()["detail"]
    r = client.post(f"/auth/users/{org['admin_id']}/role", headers=platform, json={"role": "employee"})
    assert r.status_code == 409


def test_role_change_ends_the_session_and_applies(client, org):
    token = _login(client, org, "ravi@maple.example", GOOD_PW).json()["access_token"]
    uid = decode_token(token)["sub"]
    r = client.post(f"/auth/users/{uid}/role", headers=org["hdr"], json={"role": "payroll"})
    assert r.status_code == 200 and r.json()["role"] == "payroll_admin"
    assert _gateway_verdict(client, token) == "stale"            # signed out; the new role comes with the next sign-in
    state = client.portal.call(identity.fetch_user_state, uid)
    assert state["role"] == "payroll_admin" and state["employee_id"] == EMP_A
    assert client.post(f"/auth/users/{uid}/role", headers=org["hdr"], json={"role": "wizard"}).status_code == 422
    client.post(f"/auth/users/{uid}/role", headers=org["hdr"], json={"role": "employee"})


# ── forgot / reset ───────────────────────────────────────────────────────────

def test_forgot_password_never_reveals_and_reset_works_once(client, org):
    forgot_limiter._hits.clear()
    unknown = client.post("/auth/password/forgot", json={"email": "nobody@maple.example", "org_code": org["code"]})
    known = client.post("/auth/password/forgot", json={"email": "ravi@maple.example", "org_code": org["code"]})
    assert unknown.status_code == known.status_code == 200
    assert unknown.json()["message"] == known.json()["message"]
    assert "dev_link" not in unknown.json()
    tok = _token(known.json()["dev_link"])                       # only because LEXORA_DEV_SHOW_LINKS=1
    assert "/reset-password/" in known.json()["dev_link"]

    assert client.get(f"/auth/invite/{tok}").status_code == 404     # a reset link is not an invite link
    assert client.get(f"/auth/password/reset/{tok}").status_code == 200
    new_pw = "Fresh!Passw0rd99"
    assert client.post(f"/auth/password/reset/{tok}", json={"password": new_pw}).status_code == 200
    assert client.post(f"/auth/password/reset/{tok}", json={"password": new_pw}).status_code == 410
    assert _login(client, org, "ravi@maple.example", GOOD_PW).status_code == 401
    assert _login(client, org, "ravi@maple.example", new_pw).status_code == 200


def test_forgot_password_is_rate_limited(client, org):
    import anti_automation
    forgot_limiter._hits.clear()
    anti_automation.reset_for_tests()
    codes = [client.post("/auth/password/forgot", json={"email": "flood@maple.example",
                                                         "org_code": org["code"]}).status_code for _ in range(7)]
    # WASA 2026-10-21 (#13): 3 per e-mail per 15 minutes from any address (was 5 per e-mail+IP)
    assert codes[:3] == [200] * 3 and codes[3:] == [429] * 4
    anti_automation.reset_for_tests()


def test_login_settings_defaults_and_admin_only_edit(client, org):
    s = client.get("/auth/settings/identity", headers=org["hdr"]).json()
    assert s["auto_login_on_onboarding"] is True and s["invite_expiry_hours"] == 72
    assert client.put("/auth/settings/identity", headers=_hdr(org, "hr_admin"),
                      json={"auto_login_on_onboarding": False}).status_code == 403
    r = client.put("/auth/settings/identity", headers=org["hdr"],
                   json={"auto_login_on_onboarding": False, "invite_expiry_hours": 24})
    assert r.status_code == 200 and r.json()["auto_login_on_onboarding"] is False
    assert client.portal.call(identity.get_settings, org["id"])["invite_expiry_hours"] == 24
    assert client.put("/auth/settings/identity", headers=org["hdr"],
                      json={"invite_expiry_hours": 0}).status_code == 422
