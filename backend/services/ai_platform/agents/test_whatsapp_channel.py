"""WhatsApp chase channel tests (2026-10-26): consent required (explicit agent_chase, current number), approved templates only,
in-app → e-mail → WhatsApp order, off by default, no real send when not configured ("would send"), credentials refused in
settings, and the foundation's chase_task uses it with a fake transport.

Run:  cd backend/services/ai_platform && python -m pytest -q agents/test_whatsapp_channel.py
"""
from datetime import timedelta

import pytest

from agents.lifecycle_testkit import E1, ORG, db, go_live, run  # noqa: F401

from shared.utils import platform_models as pm  # noqa: E402
from shared.utils import whatsapp_channel as WA  # noqa: E402

import agentic_tasks as TK  # noqa: E402

PHONE = "9876543210"
CFG = {**WA.DEFAULTS, "enabled": True, "phone_number_id": "1234567890", "token_env": "JETHRO_TEST_WA_TOKEN", "templates": {"agent_chase": "jethro_task_reminder"}}
OK = {"status": "opted_in", "phone_hash": WA.phone_hash(PHONE), "events": ["agent_chase"]}


def test_off_by_default_and_no_credentials_in_settings():
    assert WA.DEFAULTS["enabled"] is False
    from shared.utils.org_settings import GROUPS
    assert GROUPS["notify"]["default"]["whatsapp"]["enabled"] is False
    with pytest.raises(ValueError):
        WA.clean_config({"token_env": "EAAG1234secretvalue"}, {})               # a token VALUE is refused (must be an env var NAME)
    with pytest.raises(ValueError):
        WA.clean_config({"access_token": "x"}, {})
    with pytest.raises(ValueError):
        WA.clean_config({"templates": {"agent_chase": "Hello {{1}} free text"}}, {})
    c = WA.clean_config({"enabled": True, "token_env": "JETHRO_WA_TOKEN", "templates": {"agent_chase": "jethro_task_reminder"}}, {})
    assert c["token_env"] == "JETHRO_WA_TOKEN" and c["templates"]["agent_chase"] == "jethro_task_reminder"


def test_consent_rules():
    assert not WA.consent_decision(phone=PHONE, optin=None)["ok"]
    assert not WA.consent_decision(phone=PHONE, optin={**OK, "status": "opted_out"})["ok"]
    blanket = WA.consent_decision(phone=PHONE, optin={**OK, "events": []})
    assert not blanket["ok"] and "agent_chase" in blanket["reason"]               # a blanket opt-in does not cover agent reminders
    assert not WA.consent_decision(phone="9000000000", optin=OK)["ok"]            # number changed since the opt-in
    assert not WA.consent_decision(phone=None, optin=OK)["ok"]
    assert WA.consent_decision(phone=PHONE, optin=OK)["ok"]


def test_channel_order_and_template_only():
    assert WA.channel_plan(CFG, consent_ok=True, reminder_no=1) == ["in_app", "email"]
    assert WA.channel_plan(CFG, consent_ok=True, reminder_no=2) == ["in_app", "email", "whatsapp"]
    assert WA.channel_plan(CFG, consent_ok=False, reminder_no=5) == ["in_app", "email"]
    assert WA.channel_plan({**CFG, "enabled": False}, consent_ok=True, reminder_no=5) == ["in_app", "email"]
    m = WA.template_message(CFG, to=PHONE, kind="agent_chase", params=["Exit Agent", "2"])
    assert m["type"] == "template" and m["template"]["name"] == "jethro_task_reminder" and m["to"] == "919876543210"
    assert [p["text"] for p in m["template"]["components"][0]["parameters"]] == ["Exit Agent", "2"]
    assert "text" not in m                                                         # no free-text body anywhere
    with pytest.raises(ValueError):
        WA.template_message({**CFG, "templates": {"agent_chase": ""}}, to=PHONE, kind="agent_chase", params=[])


def test_send_with_fake_transport_and_would_send_when_unconfigured(monkeypatch):
    fake = WA.FakeTransport()
    good = {"ok": True, "phone": PHONE, "reason": None}
    r = run(WA.send_chase(ORG, employee_id=E1, params=["Onboarding Agent", "2"], reminder_no=2, cfg=CFG, transport=fake, consent=good))
    assert r["status"] == "sent" and len(fake.sent) == 1 and r["to"].endswith("3210") and PHONE not in str(r)
    r = run(WA.send_chase(ORG, employee_id=E1, params=["x", "1"], reminder_no=1, cfg=CFG, transport=fake, consent=good))
    assert r["status"] == "skipped" and len(fake.sent) == 1                        # first reminder: in-app + e-mail only
    r = run(WA.send_chase(ORG, employee_id=E1, params=["x", "3"], reminder_no=3, cfg=CFG, transport=fake,
                          consent={"ok": False, "phone": PHONE, "reason": "employee has not opted in to WhatsApp"}))
    assert r["status"] == "skipped" and "opted in" in r["reason"] and len(fake.sent) == 1
    monkeypatch.delenv("JETHRO_TEST_WA_TOKEN", raising=False)
    tr = WA.transport_for(CFG)
    assert isinstance(tr, WA.LogOnlyTransport) and "JETHRO_TEST_WA_TOKEN" in tr.reason   # token env not set → nothing leaves the machine
    r = run(WA.send_chase(ORG, employee_id=E1, params=["x", "2"], reminder_no=2, cfg=CFG, consent=good))
    assert r["status"] == "would_send" and r["transport"] == "log_only"


def test_chase_task_uses_the_channel_only_with_consent(monkeypatch):
    go_live()
    sent = []

    async def fake_load(org):
        return CFG

    async def fake_consent(org, employee_id):
        return {"ok": employee_id == E1, "phone": PHONE, "reason": None if employee_id == E1 else "employee has not opted in to WhatsApp"}
    fake = WA.FakeTransport()
    monkeypatch.setattr(WA, "load_config", fake_load)
    monkeypatch.setattr(WA, "consent_for", fake_consent)
    monkeypatch.setattr(WA, "transport_for", lambda cfg: fake)
    t = run(TK.open_task(ORG, agent="attendance", kind="at_01", title="Single punch on 2026-10-22", owner_employee_id=E1, dedupe_key="wa:1"))
    r1 = run(TK.chase_task(ORG, t["id"], now=pm.now_utc()))
    assert r1["whatsapp"]["status"] == "skipped" and fake.sent == []               # reminder 1 → in-app + e-mail
    r2 = run(TK.chase_task(ORG, t["id"], now=pm.now_utc() + timedelta(hours=25)))
    assert r2["whatsapp"]["status"] == "sent" and len(fake.sent) == 1
    assert fake.sent[0]["template"]["components"][0]["parameters"][0]["text"] == "Attendance Agent"
    r3 = run(TK.chase_task(ORG, t["id"], now=pm.now_utc() + timedelta(hours=50)))    # 3rd reminder escalates …
    assert r3["status"] == "escalated" and r3["whatsapp"]["status"] == "sent" and len(fake.sent) == 2   # … and still reports its WhatsApp leg
    other = run(TK.open_task(ORG, agent="attendance", kind="at_01", title="No punch", owner_employee_id="someone-else", dedupe_key="wa:2"))
    run(TK.chase_task(ORG, other["id"]))
    r = run(TK.chase_task(ORG, other["id"]))
    assert r["whatsapp"]["status"] == "skipped" and len(fake.sent) == 2 and sent == []
