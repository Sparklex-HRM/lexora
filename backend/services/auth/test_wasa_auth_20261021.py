"""WASA hardening 2026-10-21 — auth service.

1. Our own proof-of-work CAPTCHA (pow_captcha.py, ported from the HMS):
   valid / expired / reused / tampered / wrong purpose / too easy / unsolved /
   not ticked / mode off, adaptive difficulty, and the routes (login, register-org,
   forgot, signup, MFA verify) refusing a request without a valid token.
2. Forgot-password limits (3 per e-mail, 10 per IP, same reply known/unknown).
3. Change-password brute force (400 on a wrong current password, 5 → 429, counts
   on the login's account lockout).
4. Forced password change for the platform-created first admin (pwchg claim).
5. Sign-up verify is single use under a race.
6. Provider / unexpected errors: fixed sentence + ref, raw text only in the log.

Run:  cd backend/services/auth && PYTHONPATH=../../ python -m pytest test_wasa_auth_20261021.py -q
"""
import base64
import json
import time
import uuid
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import abuse
import account_security
import anti_automation as aa
import billing_provider as bp
import database
import esign_routes
import http_errors
import models  # noqa: F401
import pow_captcha as pc
import saas_models  # noqa: F401
import security_routes
import signup_routes
from account_routes import router as account_router
from billing import router as billing_router
from billing_saas_routes import router as billing_saas_router
from esign_routes import router as esign_router
from routers import router as auth_router
from security_policy import account_limiter
from security_routes import router as security_router
from shared.utils import audit, identity  # noqa: F401
from shared.utils.ratelimit import RateLimiter
from shared.utils.security import decode_token
from signup_routes import router as signup_router
from users_routes import forgot_limiter, router as users_router

BITS = 10                      # fast tests
PW = "Wasa!Strong2026x"
SUPER = {"x-role": "superadmin", "x-org-id": "00000000-0000-0000-0000-000000000000",
         "x-user-id": str(uuid.uuid4())}


@asynccontextmanager
async def _lifespan(app):
    async with database.engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.run_sync(lambda c: identity.employees_ref.create(c, checkfirst=True))
    yield


app = FastAPI(lifespan=_lifespan)
for _r in (auth_router, security_router, users_router, signup_router, account_router,
           billing_saas_router, billing_router, esign_router):
    app.include_router(_r)
http_errors.install(app)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def pow_env(monkeypatch):
    monkeypatch.setenv("CAPTCHA_MODE", "pow")
    monkeypatch.setenv("CAPTCHA_HMAC_SECRET", "unit-test-secret")
    monkeypatch.setenv("POW_BASE_BITS", str(BITS))
    monkeypatch.setenv("CAPTCHA_VISIBLE", "true")
    monkeypatch.setattr(abuse, "signup_ip_limiter", RateLimiter(1000, 3600))
    monkeypatch.setattr(abuse, "signup_email_limiter", RateLimiter(1000, 3600))
    monkeypatch.setattr(abuse, "verify_limiter", RateLimiter(1000, 3600))
    forgot_limiter._hits.clear()
    aa.reset_for_tests()
    yield
    aa.reset_for_tests()


def token(client, purpose="login", account=None):
    q = f"/auth/challenge?purpose={purpose}" + (f"&account={account}" if account else "")
    ch = client.get(q).json()
    assert ch["enabled"] is True and ch["mode"] == "pow"
    return pc.encode_token(ch, pc.solve(ch), interactive=bool(ch.get("interactive")))


def solved(purpose="login", bits=BITS, now=None, interactive=False):
    ch = pc.make_challenge(purpose, bits, now=now, interactive=interactive)
    return ch, pc.encode_token(ch, pc.solve(ch))


def code_of(tok, purposes=("login",), min_bits=BITS, **kw):
    with pytest.raises(pc.PowError) as e:
        pc.verify(tok, purposes, min_bits, **kw)
    return e.value.code


def _register(client, email, name="Wasa Org"):
    r = client.post("/auth/register-org", json={
        "org_name": name, "country": "CA", "province": "ON", "admin_email": email,
        "admin_password": PW, "admin_first_name": "Asha", "captcha_token": token(client, "signup", email)})
    assert r.status_code == 201, r.text
    oid = r.json()["org_id"]

    async def code():
        async with database.SessionLocal() as s:
            return (await s.get(models.Organization, uuid.UUID(oid))).code
    return {"id": oid, "code": client.portal.call(code), "email": email, "tokens": r.json()}


def _login(client, org, email=None, password=PW, tok="auto"):
    email = email or org["email"]
    body = {"email": email, "password": password, "org_code": org["code"]}
    if tok == "auto":
        tok = token(client, "login", email)
    if tok:
        body["captcha_token"] = tok
    return client.post("/auth/login", json=body)


@pytest.fixture(scope="module")
def org(client):
    mp = pytest.MonkeyPatch()
    mp.setenv("CAPTCHA_MODE", "pow")
    mp.setenv("CAPTCHA_HMAC_SECRET", "unit-test-secret")
    mp.setenv("POW_BASE_BITS", str(BITS))
    try:
        return _register(client, "admin@wasa-auth.example")
    finally:
        mp.undo()


# ══════════════════════════ 1. the proof of work itself ══════════════════════════

def test_valid_token_passes_once():
    ch, tok = solved()
    assert pc.verify(tok, ("login",), BITS)["salt"] == ch["salt"]
    assert code_of(tok) == "reused"


def test_expired_tampered_wrong_purpose_too_easy_unsolved_malformed():
    _, old = solved(now=time.time() - 400)
    assert code_of(old) == "expired"
    _, tok = solved()
    body = pc.decode_token(tok)
    for field, val in (("difficulty", 1), ("salt", "0" * 24), ("expires", body["expires"] + 30),
                       ("signature", "f" * 64), ("purpose", "forgot")):
        bad = dict(body, **{field: val})
        raw = base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
        assert code_of(raw, purposes=("login", "forgot")) == "bad_signature", field
    _, f = solved("forgot")
    assert code_of(f, purposes=("login",)) == "wrong_purpose"
    _, easy = solved(bits=BITS)
    assert code_of(easy, min_bits=BITS + 2) == "too_easy"
    ch = pc.make_challenge("login", 20)
    n = 0
    while pc.meets(ch["salt"], n, 20):
        n += 1
    assert code_of(pc.encode_token(ch, n)) == "not_solved"
    assert code_of("") == "missing"
    assert code_of("not-json!!") == "malformed"


def test_not_ticked_and_interactive_is_signed():
    _, plain = solved()
    assert code_of(plain, require_interactive=True) == "not_interactive"
    ch, tok = solved(interactive=True)
    assert pc.verify(tok, ("login",), BITS, require_interactive=True)
    # adding interactive to a challenge issued without it breaks the signature
    ch2 = pc.make_challenge("login", BITS)
    forged = pc.encode_token(ch2, pc.solve(ch2), interactive=True)
    assert code_of(forged, require_interactive=True) == "bad_signature"


def test_encode_token_drops_reply_fields_and_keeps_interactive():
    ch = dict(pc.make_challenge("login", BITS, interactive=True), enabled=True, mode="pow")
    body = pc.decode_token(pc.encode_token(ch, 7))
    assert "enabled" not in body and "mode" not in body and body["interactive"] is True and body["number"] == 7


def test_secret_derived_from_jwt_secret_with_jethro_label(monkeypatch):
    import hashlib
    import hmac
    monkeypatch.delenv("CAPTCHA_HMAC_SECRET")
    monkeypatch.setenv("JWT_SECRET", "jwt-x")
    assert pc._secret() == hmac.new(b"jwt-x", b"jethro-pow-captcha-v1", hashlib.sha256).digest()


def test_challenge_reply_says_mode(client, monkeypatch):
    ch = client.get("/auth/challenge?purpose=forgot").json()
    assert ch["enabled"] and ch["mode"] == "pow" and ch["interactive"] is True and ch["difficulty"] == BITS
    assert client.get("/auth/challenge?purpose=nope").status_code == 422
    monkeypatch.setenv("CAPTCHA_MODE", "off")
    assert client.get("/auth/challenge?purpose=login").json() == {"enabled": False, "mode": "off",
                                                                  "interactive": False}


# ══════════════════════════ routes ══════════════════════════

def test_login_needs_a_solved_token(client, org):
    r = _login(client, org, tok=None)
    assert r.status_code == 400 and r.json()["code"] == "captcha_required" and isinstance(r.json()["detail"], str)
    tok = token(client, "login", org["email"])
    assert _login(client, org, tok=tok).status_code == 200
    assert _login(client, org, tok=tok).json()["code"] == "captcha_invalid"            # single use
    _, wrong = solved("forgot", interactive=True)
    assert _login(client, org, tok=wrong).json()["code"] == "captcha_invalid"          # wrong purpose
    _, invisible = solved("login")
    assert _login(client, org, tok=invisible).json()["code"] == "captcha_not_ticked"


def test_failures_raise_difficulty_then_captcha_harder(client, org):
    email = "harder@wasa-auth.example"
    for _ in range(3):
        assert _login(client, org, email=email, password="Wrong!pass1").status_code == 401
    ch = client.get(f"/auth/challenge?purpose=login&account={email}").json()
    assert ch["difficulty"] == BITS + 2
    old = pc.make_challenge("login", BITS, interactive=True)
    r = _login(client, org, email=email, password="Wrong!pass1", tok=pc.encode_token(old, pc.solve(old)))
    assert r.status_code == 400 and r.json()["code"] == "captcha_harder" and r.json()["difficulty"] == BITS + 2


def test_captcha_off_keeps_old_behaviour(client, org, monkeypatch):
    monkeypatch.setenv("CAPTCHA_MODE", "off")
    assert _login(client, org, tok=None).status_code == 200


def test_register_org_signup_forgot_mfa_need_a_token(client, org):
    r = client.post("/auth/register-org", json={"org_name": "No Token", "admin_email": "nt@wasa-auth.example",
                                                "admin_password": PW})
    assert r.status_code == 400 and r.json()["code"] == "captcha_required"
    r = client.post("/auth/signup", json={"org_name": "No Token", "admin_email": "nt2@wasa-auth.example",
                                          "admin_password": PW, "admin_first_name": "A", "province": "ON",
                                          "accept_terms": True})
    assert r.status_code == 400 and r.json()["code"] == "captcha_required"
    r = client.post("/auth/password/forgot", json={"email": org["email"], "org_code": org["code"]})
    assert r.status_code == 400 and r.json()["code"] == "captcha_required"

    async def mfa_tok():
        async with database.SessionLocal() as s:
            u = (await s.execute(sa.select(models.User).where(models.User.email == org["email"]))).scalars().first()
            return account_security.make_challenge(u, "verify", "password")
    mt = client.portal.call(mfa_tok)
    r = client.post("/auth/mfa/challenge/verify", json={"mfa_token": mt, "code": "123456"})
    assert r.status_code == 400 and r.json()["code"] == "captcha_required"
    r = client.post("/auth/mfa/challenge/verify", json={"mfa_token": mt, "code": "123456",
                                                         "captcha_token": token(client, "mfa", org["email"])})
    assert r.status_code == 401                          # past the CAPTCHA; the code itself is wrong


# ══════════════════════════ 2. forgot password ══════════════════════════

def _forgot(client, email, code):
    return client.post("/auth/password/forgot", json={"email": email, "org_code": code,
                                                      "captcha_token": token(client, "forgot", email)})


def test_forgot_reply_is_identical_for_known_and_unknown(client, org, monkeypatch):
    monkeypatch.setenv("LEXORA_DEV_SHOW_LINKS", "0")       # the real path: lookup + mail out of line
    known = _forgot(client, org["email"], org["code"])
    unknown = _forgot(client, "nobody@wasa-auth.example", org["code"])
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() and "dev_link" not in known.json()


def test_forgot_per_email_limit_any_ip(client, org):
    codes = [_forgot(client, "one@wasa-auth.example", org["code"]).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = _forgot(client, "one@wasa-auth.example", org["code"])
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_forgot_per_ip_limit_any_email(client, org):
    codes = [_forgot(client, f"ip{i}@wasa-auth.example", org["code"]).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_forgot_raises_difficulty(client, org):
    email = "busy@wasa-auth.example"
    for _ in range(2):
        _forgot(client, email, org["code"])
    assert client.get(f"/auth/challenge?purpose=forgot&account={email}").json()["difficulty"] == BITS + 2


def test_dev_links_still_returned_locally(client, org, monkeypatch):
    monkeypatch.setenv("LEXORA_DEV_SHOW_LINKS", "1")
    r = _forgot(client, org["email"], org["code"])
    assert r.status_code == 200 and "/reset-password/" in r.json()["dev_link"]


# ══════════════════════════ 3. change password ══════════════════════════

def _hdr(tokens):
    c = decode_token(tokens["access_token"])
    return {"x-user-id": c["sub"], "x-org-id": c["org_id"], "x-role": c["role"], "x-user-email": c["email"],
            "x-session-id": c.get("sid") or ""}


def test_change_password_wrong_current_is_400_then_429_and_counts_on_lockout(client):
    o = _register(client, "chg@wasa-auth.example", "Wasa Change")
    h = _hdr(o["tokens"])
    acct_key = f"chg@wasa-auth.example|{o['code'].upper()}"
    before = account_limiter.failures(acct_key)
    codes = [client.post("/auth/change-password", headers=h,
                         json={"current_password": "Nope!wrong1", "new_password": "Brand!New2026x"}).status_code
             for _ in range(6)]
    assert codes == [400] * 5 + [429]
    assert account_limiter.failures(acct_key) >= before + 5
    r = client.post("/auth/change-password", headers=h, json={"current_password": PW, "new_password": "Brand!New2026x"})
    assert r.status_code == 429                         # still locked, even with the right password
    security_routes.change_password_limiter.record_success(h["x-user-id"])
    account_limiter.record_success(acct_key)


# ══════════════════════════ 4. forced password change ══════════════════════════

def test_platform_tenant_admin_must_change_password(client):
    weak = client.post("/auth/platform/orgs", headers=SUPER, json={
        "org_name": "Op Weak", "country": "CA", "province": "ON", "admin_email": "weak@wasa-op.example",
        "admin_password": "Password@123"})
    assert weak.status_code == 422                       # breached list now applies here too
    r = client.post("/auth/platform/orgs", headers=SUPER, json={
        "org_name": "Op Tenant", "country": "CA", "province": "ON", "admin_email": "first@wasa-op.example",
        "admin_password": "Operator!Typed2026"})
    assert r.status_code == 201, r.text
    o = {"code": r.json()["code"], "email": "first@wasa-op.example"}
    lg = _login(client, o, password="Operator!Typed2026")
    assert lg.status_code == 200 and lg.json()["password_change_required"] is True
    assert decode_token(lg.json()["access_token"])["pwchg"] is True
    h = _hdr(lg.json())
    assert client.get("/auth/me", headers=h).json()["password_change_required"] is True
    ch = client.post("/auth/change-password", headers=h,
                     json={"current_password": "Operator!Typed2026", "new_password": "Mine!Own2026pass"})
    assert ch.status_code == 200 and ch.json()["password_change_required"] is False
    assert "pwchg" not in decode_token(ch.json()["access_token"])
    assert client.get("/auth/me", headers=_hdr(ch.json())).json()["password_change_required"] is False
    again = _login(client, o, password="Mine!Own2026pass")
    assert again.json()["password_change_required"] is False and "pwchg" not in decode_token(again.json()["access_token"])


def test_self_registered_admin_is_not_forced(client, org):
    lg = _login(client, org)
    assert lg.json()["password_change_required"] is False and "pwchg" not in decode_token(lg.json()["access_token"])


# ══════════════════════════ 5. sign-up verify race ══════════════════════════

def test_signup_verify_is_single_use_under_a_race(client, monkeypatch):
    monkeypatch.setenv("LEXORA_DEV_SHOW_LINKS", "1")

    async def _seed(org_id):
        return {"ok": True}
    monkeypatch.setattr(signup_routes, "seed_canada_defaults", _seed)
    cat = client.put("/auth/billing/catalog", headers=SUPER, json={"country": "CA", "plans": [
        {"plan_key": "growth", "label": "Growth", "pepm": 9, "min_monthly": 99, "sort": 2}]})
    assert cat.status_code == 200, cat.text
    r = client.post("/auth/signup", json={
        "org_name": "Race Freight", "admin_email": "race@wasa-signup.example", "admin_password": PW,
        "admin_first_name": "Asha", "province": "ON", "plan": "growth", "accept_terms": True,
        "captcha_token": token(client, "signup", "race@wasa-signup.example")})
    assert r.status_code == 202, r.text
    tok = r.json()["dev_link"].rsplit("/", 1)[1]
    # both requests passed the "not verified yet" check before either committed
    real = signup_routes._pending

    async def stale_pending(db, token_):
        s = (await db.execute(sa.select(saas_models.SignupRequest).where(
            saas_models.SignupRequest.token_hash == signup_routes._hash(token_)))).scalar_one()
        return s
    monkeypatch.setattr(signup_routes, "_pending", stale_pending)
    first = client.post(f"/auth/signup/verify/{tok}")
    second = client.post(f"/auth/signup/verify/{tok}")
    monkeypatch.setattr(signup_routes, "_pending", real)
    assert first.status_code == 201 and second.status_code == 410

    async def count():
        async with database.SessionLocal() as s:
            return (await s.execute(sa.text("SELECT COUNT(*) FROM organizations WHERE name = 'Race Freight'"))).scalar()
    assert client.portal.call(count) == 1


# ══════════════════════════ 6. error text ══════════════════════════

def test_esign_unexpected_error_is_masked_with_ref(client, monkeypatch):
    def boom(_p):
        raise RuntimeError("postgres password=hunter2 at 10.0.0.5")
    monkeypatch.setattr(esign_routes.E, "provider_can_sign", boom)
    r = client.post("/auth/esign/envelopes", headers={"x-org-id": str(uuid.uuid4()), "x-role": "hr_admin"},
                    data={"title": "x", "provider": "other"},
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 409 and "hunter2" not in r.text and len(r.json()["ref"]) == 8


def test_billing_provider_error_text_is_not_echoed(client):
    o = _register(client, "bill@wasa-auth.example", "Wasa Bill")

    async def set_sub():
        async with database.SessionLocal() as s:
            org = await s.get(models.Organization, uuid.UUID(o["id"]))
            org.provider_subscription_id = "sub_TEST"
            await s.commit()
    client.portal.call(set_sub)

    class Boom:
        name, configured, mode = "fakepay", True, "test"

        async def cancel_subscription(self, *_a, **_k):
            raise bp.ProviderError("Provider said: key live_SECRET is invalid", status=401)
    bp.set_provider(Boom())
    try:
        r = client.post("/auth/billing/cancel", headers=_hdr(o["tokens"]), json={"at_period_end": True})
    finally:
        bp.set_provider(None)
    assert r.status_code == 502 and "SECRET" not in r.text and len(r.json()["ref"]) == 8
