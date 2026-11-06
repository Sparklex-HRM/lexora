"""Gateway S4: an ended session (sid revoked) is refused; a read-only subscription
blocks WRITES but never reads / statutory downloads / sign-in / PIPEDA privacy rights;
X-Session-Id cannot be forged; the read-only rule matches the auth service's.
Run:  cd backend/api-gateway && PYTHONPATH=../ python -m pytest -q test_saas_gate.py
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
import saas_gate  # noqa: E402
from shared.utils.security import create_access_token  # noqa: E402

ORG, USER, SID = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", \
    "44444444-4444-4444-4444-444444444444"
seen: list[dict] = []
sessions: dict = {}
orgs: dict = {}
NOW = datetime.now(timezone.utc)


class _Upstream:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, params=None, content=None, headers=None):
        seen.append({"method": method, "url": url, "headers": {k.lower(): v for k, v in headers.items()}})

        class R:
            content, status_code = b'{"ok":true}', 200
            headers = {"content-type": "application/json"}
        return R()


async def _user(uid):
    return {"is_active": True, "tokens_valid_after": None, "role": "org_admin", "employee_id": None}


async def _sess(sid):
    return sessions.get(sid)


async def _org(oid):
    return orgs.get(oid)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main.httpx, "AsyncClient", _Upstream)
    monkeypatch.setattr(revocation, "cache", revocation.UserStateCache(loader=_user, ttl=0))
    monkeypatch.setattr(saas_gate, "load_session", _sess)
    monkeypatch.setattr(saas_gate, "load_org", _org)
    monkeypatch.setattr(saas_gate, "_sessions", saas_gate._TTLCache(0))
    monkeypatch.setattr(saas_gate, "_orgs", saas_gate._TTLCache(0))
    seen.clear()
    sessions.clear()
    orgs.clear()
    return TestClient(main.app)


def _bearer(**claims):
    tok = create_access_token({"sub": USER, "org_id": ORG, "email": "ceo@maple.example", "role": "org_admin",
                               "modules": ["core", "payroll", "leave", "compliance"], **claims})
    return {"Authorization": f"Bearer {tok}"}


# ── the rule itself (same cases as services/auth/test_saas_s4.py::test_access_mode) ──
CASES = [
    (None, None, False), ("active", None, False), ("trialing", NOW + timedelta(days=3), False),
    ("trialing", NOW - timedelta(seconds=1), True), ("past_due", NOW + timedelta(days=2), False),
    ("past_due", NOW - timedelta(hours=1), True), ("cancelled", None, True), ("expired", None, True),
]


@pytest.mark.parametrize("status,until,ro", CASES)
def test_read_only_rule(status, until, ro):
    assert saas_gate.read_only(status, until, NOW) is ro


def test_write_allow_list():
    w = saas_gate.write_allowed_when_read_only
    assert w("GET", "/employees") and w("POST", "/auth/billing/checkout") and w("POST", "/auth/logout")
    assert w("POST", "/payroll/t4/export") and w("GET", "/payroll/roe/1/xml")
    assert w("POST", "/payroll/runs/123/payslips/pdf")
    assert not w("POST", "/employees") and not w("PATCH", "/employees/1") and not w("POST", "/payroll/runs")


def test_revoked_session_is_refused(client):
    sessions[SID] = {"revoked": False}
    hdr = _bearer(sid=SID)
    assert client.get("/employees", headers=hdr).status_code == 200
    assert seen[-1]["headers"]["x-session-id"] == SID
    sessions[SID] = {"revoked": True}
    r = client.get("/employees", headers=hdr)
    assert r.status_code == 401 and "ended" in r.json()["detail"] and len(seen) == 1


def test_token_without_sid_still_works(client):
    assert client.get("/employees", headers=_bearer()).status_code == 200
    assert "x-session-id" not in seen[-1]["headers"]


def test_session_id_header_cannot_be_forged(client):
    client.get("/employees", headers={**_bearer(), "X-Session-Id": SID})
    assert "x-session-id" not in seen[-1]["headers"]


def test_read_only_org_blocks_writes_not_reads(client):
    orgs[ORG] = {"status": "trialing", "access_until": NOW - timedelta(minutes=5)}
    hdr = _bearer()
    assert client.get("/employees", headers=hdr).status_code == 200                 # read: open
    r = client.post("/employees", headers=hdr, json={})
    assert r.status_code == 402 and r.json()["detail"] == "subscription_read_only"
    assert client.post("/auth/billing/checkout", headers=hdr, json={}).status_code == 200   # pay: open
    assert client.post("/payroll/t4/export", headers=hdr, json={}).status_code == 200      # year-end slip file
    orgs[ORG] = {"status": "active", "access_until": None}
    assert client.post("/employees", headers=hdr, json={}).status_code == 200


def test_signup_and_webhook_are_public(client):
    assert client.post("/auth/signup", json={}).status_code == 200
    assert client.post("/auth/billing/webhooks/razorpay", content=b"{}").status_code == 200
    assert client.post("/auth/mfa/challenge/verify", json={}).status_code == 200
    assert client.post("/auth/billing/checkout", json={}).status_code == 401           # not public
