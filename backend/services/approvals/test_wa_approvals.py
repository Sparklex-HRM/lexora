"""G7 WhatsApp approvals — signed single-use buttons, executed as the approver through the web-inbox route.
Mocked provider (no Meta keys exist). Run:  cd backend/services/approvals && python -m pytest -q test_wa_approvals.py
"""
import asyncio
import os
import sys
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import wa_approvals as wa  # noqa: E402
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.platform_models import ApprovalItem  # noqa: E402
from shared.utils.providers import mirror, store  # noqa: E402
from shared.utils.providers import whatsapp as WA  # noqa: E402
from shared.utils.providers.models import WhatsappMessage  # noqa: E402,F401
from shared.utils.providers.models_s5 import WhatsappOptin  # noqa: E402

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER_ORG = "00000000-0000-0000-0000-0000000000b2"
MGR_PHONE, EMP_PHONE = "+919800000001", "+919800000002"
USERS = [{"id": "u-mgr", "email": "mgr@x.ca", "role": "manager", "employee_id": "e-mgr", "first_name": "Meera", "last_name": "M"},
         {"id": "u-emp", "email": "emp@x.ca", "role": "employee", "employee_id": "e-emp", "first_name": "Ravi", "last_name": "R"}]
EMPS = [{"id": "e-mgr", "manager_id": None, "first_name": "Meera", "last_name": "M"},
        {"id": "e-emp", "manager_id": "e-mgr", "first_name": "Ravi", "last_name": "R"}]
META = {"mode": "meta", "enabled": True, "config": {"phone_number_id": "111", "access_token": "t", "template_approval": "jethro_approval"}}


def run(c):
    return asyncio.run(c)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    async def setup():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wa.db'}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(pm.PlatformBase.metadata.create_all)
        pm.set_sessionmaker(async_sessionmaker(engine, expire_on_commit=False))
        wa._READY["v"] = True
    run(setup())
    sent, replies, decided, audits = [], [], [], []
    cfg = {"wa": dict(META)}

    async def users(org):
        return [dict(u) for u in USERS]

    async def emps(org):
        return [dict(e) for e in EMPS]

    async def delegations(org):
        return []

    async def phones(org, uids):
        return {u: {"u-mgr": MGR_PHONE, "u-emp": EMP_PHONE}[u] for u in uids if u in ("u-mgr", "u-emp")}

    async def load(org, key):
        return cfg["wa"]

    async def send_buttons(c, *, to, template, params, payloads, lang="en"):
        sent.append({"to": to, "template": template, "params": params, "payloads": payloads})
        return {"status": "sent", "message_id": f"wamid.{len(sent)}"}

    async def reply(org, to, msg):
        replies.append((to, msg))

    async def execute(org, row, decision, comment):
        decided.append((row.approver_user_id, row.item_id, decision, comment))
        return True, "Approved" if decision == "approve" else "Rejected"

    async def audit(org, row, decision, ok, msg):
        audits.append((decision, ok))

    monkeypatch.setattr(directory, "org_users", users)
    monkeypatch.setattr(directory, "org_employees", emps)
    monkeypatch.setattr(wa.ac, "load_delegations", delegations)
    monkeypatch.setattr(mirror, "phones_for_users", phones)
    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(WA, "send_buttons_template", send_buttons)
    monkeypatch.setattr(wa, "_reply", reply)
    monkeypatch.setattr(wa, "execute_as_approver", execute)
    monkeypatch.setattr(wa, "_audit", audit)
    yield {"sent": sent, "replies": replies, "decided": decided, "audits": audits, "cfg": cfg}
    pm.set_sessionmaker(None)


async def _seed(events=("approvals",), phone=MGR_PHONE, item_status="pending"):
    async with pm.session() as s:
        s.add(ApprovalItem(id="item-1", org_id=ORG, kind="leave", ref_id="L1", title="Leave — Liam W · 3 days",
                           summary=[], requester_user_id="u-emp", requester_name="Ravi R",
                           approver_manager_of_employee_id="e-emp", link="/leave",
                           callback_path="/leave/requests/L1/{decision}", status=item_status, cycle=1))
        s.add(WhatsappOptin(org_id=ORG, employee_id="e-mgr", user_id="u-mgr", phone_masked="•••0001",
                            phone_hash=WA.phone_hash(phone), status="opted_in", events=list(events)))
        await s.commit()


async def _rows():
    async with pm.session() as s:
        return (await s.execute(select(wa.WaApproval))).scalars().all()


def _press(env, i, frm=MGR_PHONE, mid="m1"):
    return run(wa.handle_message(ORG, {"id": mid, "from": frm, "type": "button", "payload": env["sent"][0]["payloads"][i]}))


# ── tokens ────────────────────────────────────────────────────────────────────
def test_token_sign_parse_and_tamper():
    row = wa.WaApproval(id=pm.new_id(), org_id=ORG, item_id="item-1", approver_user_id="u-mgr", cycle=1,
                        expires_at=pm.now_utc() + timedelta(hours=1))
    t = wa.make_token(row, "approve")
    rid, d, sig = wa.parse_token(t)
    assert rid == row.id and d == "a" and wa.token_valid(row, d, sig)
    assert not wa.token_valid(row, "r", sig)                                   # approve sig ≠ reject
    for field, value in (("org_id", OTHER_ORG), ("approver_user_id", "u-emp"), ("cycle", 2), ("item_id", "item-2")):
        other = wa.WaApproval(**{**{k: getattr(row, k) for k in ("id", "org_id", "item_id", "approver_user_id", "cycle", "expires_at")}, field: value})
        assert not wa.token_valid(other, d, sig)
    assert wa.parse_token("wa1.x.a.y") is None and wa.parse_token("garbage") is None and wa.parse_token(t + "0") is None


# ── dispatch ──────────────────────────────────────────────────────────────────
def test_dispatch_sends_once_to_the_opted_in_approver_only(env):
    run(_seed())
    out = run(wa.dispatch(ORG))
    assert out["sent"] == 1 and len(env["sent"]) == 1
    msg = env["sent"][0]
    assert msg["to"] == MGR_PHONE and msg["template"] == "jethro_approval" and len(msg["payloads"]) == 2
    assert "5,000" not in msg["params"][0]                                      # amounts scrubbed from the title
    assert run(wa.dispatch(ORG))["sent"] == 0 and len(env["sent"]) == 1        # never twice for the same item / cycle
    rows = run(_rows())
    assert rows[0].status == "sent" and rows[0].approver_user_id == "u-mgr"


def test_dispatch_needs_explicit_approvals_consent_and_same_number(env):
    run(_seed(events=()))                                                       # blanket opt-in does NOT cover approvals
    assert run(wa.dispatch(ORG))["sent"] == 0 and not env["sent"]


def test_dispatch_changed_number_is_skipped(env):
    run(_seed(phone="+919811111111"))
    out = run(wa.dispatch(ORG))
    assert out["sent"] == 0 and out["skipped"] == 1 and not env["sent"]


def test_not_configured_is_honest(env):
    env["cfg"]["wa"] = {"mode": "meta", "enabled": False, "config": {}}
    run(_seed())
    out = run(wa.dispatch(ORG))
    assert out["sent"] == 0 and out["not_configured"] and not env["sent"]
    assert run(_rows())[0].status == "not_configured"


# ── replies ───────────────────────────────────────────────────────────────────
def test_approve_button_executes_once_as_the_approver(env):
    run(_seed())
    run(wa.dispatch(ORG))
    assert _press(env, 0) == "done"
    assert env["decided"] == [("u-mgr", "item-1", "approve", "Approved on WhatsApp")]
    assert env["audits"] == [("approve", True)]
    assert _press(env, 0, mid="m2") == "used" and _press(env, 1, mid="m3") == "used"   # single use, both buttons
    assert len(env["decided"]) == 1


def test_wrong_phone_tampered_and_other_org_are_refused(env):
    run(_seed())
    run(wa.dispatch(ORG))
    assert _press(env, 0, frm=EMP_PHONE) == "wrong_phone"
    bad = env["sent"][0]["payloads"][0][:-1] + ("0" if env["sent"][0]["payloads"][0][-1] != "0" else "1")
    assert run(wa.handle_message(ORG, {"id": "x", "from": MGR_PHONE, "payload": bad})) == "bad_token"
    assert run(wa.handle_message(OTHER_ORG, {"id": "y", "from": MGR_PHONE, "payload": env["sent"][0]["payloads"][0]})) == "bad_token"
    assert not env["decided"]


def test_expired_button(env):
    run(_seed())
    run(wa.dispatch(ORG, now=pm.now_utc() - timedelta(hours=wa.TTL_HOURS + 1)))
    assert _press(env, 0) == "expired" and not env["decided"]


def test_reject_asks_for_a_reason_then_uses_it(env):
    run(_seed())
    run(wa.dispatch(ORG))
    assert _press(env, 1) == "awaiting_reason"
    assert "reason" in env["replies"][-1][1].lower()
    assert run(wa.handle_message(ORG, {"id": "t1", "from": MGR_PHONE, "type": "text", "text": "ok"})) == "short_reason"
    assert run(wa.handle_message(ORG, {"id": "t2", "from": MGR_PHONE, "type": "text", "text": "Team is short that week"})) == "done"
    assert env["decided"] == [("u-mgr", "item-1", "reject", "Team is short that week")]
    assert run(wa.handle_message(ORG, {"id": "t3", "from": MGR_PHONE, "type": "text", "text": "hello again"})) == "ignored"


def test_reject_cancel_leaves_it_pending(env):
    run(_seed())
    run(wa.dispatch(ORG))
    _press(env, 1)
    assert run(wa.handle_message(ORG, {"id": "t1", "from": MGR_PHONE, "type": "text", "text": "CANCEL"})) == "cancelled"
    assert not env["decided"]


def test_decided_elsewhere_is_superseded(env):
    run(_seed())
    run(wa.dispatch(ORG))

    async def close():
        async with pm.session() as s:
            i = (await s.execute(select(ApprovalItem))).scalar_one()
            i.status = "approved"
            await s.commit()
    run(close())
    assert _press(env, 0) == "superseded" and not env["decided"]


def test_consent_withdrawn_after_send(env):
    run(_seed())
    run(wa.dispatch(ORG))

    async def out():
        async with pm.session() as s:
            o = (await s.execute(select(WhatsappOptin))).scalar_one()
            o.status = "opted_out"
            await s.commit()
    run(out())
    assert _press(env, 0) == "no_consent" and not env["decided"]


# ── execution goes through the web-inbox route with the approver's identity ──
def test_execute_as_approver_calls_the_inbox_route(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(req.url), dict(req.headers), req.content
        return httpx.Response(403, json={"detail": "This item is not waiting for you"})

    real = httpx.AsyncClient
    monkeypatch.setattr(wa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))

    async def users(org):
        return [dict(u) for u in USERS]
    monkeypatch.setattr(directory, "org_users", users)
    row = wa.WaApproval(id="r1", org_id=ORG, item_id="item-1", approver_user_id="u-mgr", cycle=1)
    ok, msg = run(wa.execute_as_approver(ORG, row, "approve", "via WA"))
    assert not ok and msg == "This item is not waiting for you"
    assert seen["url"].endswith("/approvals/items/item-1/decide")
    assert seen["headers"]["x-user-id"] == "u-mgr" and seen["headers"]["x-role"] == "manager" and seen["headers"]["x-org-id"] == ORG
    row.approver_user_id = "u-gone"
    assert run(wa.execute_as_approver(ORG, row, "approve", "")) == (False, "Your Lexora HRM login is not active, so nothing was recorded.")


def test_inbound_route_is_service_only():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(wa.router)
    c = TestClient(app)
    assert c.post("/approvals/whatsapp/inbound", json={"messages": []}, headers={"x-org-id": ORG}).status_code == 403
    assert c.post("/approvals/whatsapp/inbound", json={"messages": []},
                  headers={"x-org-id": ORG, "x-internal-auth": "forged"}).status_code == 403


def test_parse_meta_inbound_shapes():
    body = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "a", "from": "919800000001", "type": "button", "button": {"payload": "wa1.x", "text": "Approve"}},
        {"id": "b", "from": "919800000001", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "wa1.y", "title": "Reject"}}},
        {"id": "c", "from": "919800000001", "type": "text", "text": {"body": "reason here"}},
        {"id": "d", "from": "919800000001", "type": "image"}]}}]}]}
    got = WA.parse_meta_inbound(body)
    assert [(m["id"], m["payload"], m["text"]) for m in got] == [("a", "wa1.x", "Approve"), ("b", "wa1.y", "Reject"), ("c", None, "reason here")]
    assert got[0]["from"] == "+919800000001"
