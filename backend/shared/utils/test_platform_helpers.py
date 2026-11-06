"""notify / approvals_client / org_settings — unit tests on a throw-away SQLite database.

Run:  cd backend && python -m pytest shared/utils/test_platform_helpers.py -q
"""
import asyncio
import os
import sys
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from shared.utils import approvals_client as ac  # noqa: E402
from shared.utils import directory, notify as nt, org_settings  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"

USERS = [
    {"id": "u-admin", "email": "admin@example.test", "role": "org_admin", "employee_id": None, "first_name": "Org", "last_name": "Admin"},
    {"id": "u-hr1", "email": "hr1@example.test", "role": "hr_admin", "employee_id": "e-hr1", "first_name": "Hr", "last_name": "One"},
    {"id": "u-hr2", "email": "hr2@example.test", "role": "hr", "employee_id": "e-hr2", "first_name": "Hr", "last_name": "Two"},
    {"id": "u-pay", "email": "pay@example.test", "role": "payroll_admin", "employee_id": None, "first_name": "Pay", "last_name": "Roll"},
    {"id": "u-mgr", "email": "mgr@example.test", "role": "manager", "employee_id": "e-mgr", "first_name": "Team", "last_name": "Lead"},
    {"id": "u-emp", "email": "emp@example.test", "role": "employee", "employee_id": "e-emp", "first_name": "Staff", "last_name": "Member"},
    {"id": "u-noemail", "email": None, "role": "employee", "employee_id": "e-noemail", "first_name": "No", "last_name": "Mail"},
]
EMPLOYEES = [
    {"id": "e-emp", "manager_id": "e-mgr", "first_name": "Staff", "last_name": "Member", "email": "emp@example.test"},
    {"id": "e-mgr", "manager_id": None, "first_name": "Team", "last_name": "Lead", "email": "mgr@example.test"},
    {"id": "e-nologin", "manager_id": "e-mgr", "first_name": "New", "last_name": "Joiner", "email": "joiner@example.test"},
]


class FakeTransport:
    def __init__(self, configured=True, fail=None):
        self._configured, self.fail, self.sent = configured, fail, []

    def configured(self):
        return self._configured

    def send(self, *, to, subject, text, sender_name="", reply_to=""):
        if self.fail:
            raise RuntimeError(self.fail)
        self.sent.append({"to": to, "subject": subject, "text": text, "sender_name": sender_name,
                          "reply_to": reply_to})


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Each test gets a fresh SQLite file, directory fixtures and a fake transport."""
    holder = {}

    async def setup():
        # a file + NullPool: every session gets its own connection, like Postgres
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'platform.db'}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(pm.PlatformBase.metadata.create_all)
        holder["engine"] = engine
        pm.set_sessionmaker(async_sessionmaker(engine, expire_on_commit=False))

    async def users(org_id):
        return [dict(u) for u in USERS] if org_id == ORG else []

    async def employees(org_id):
        return [dict(e) for e in EMPLOYEES] if org_id == ORG else []

    async def name(org_id):
        return "Example Co"

    monkeypatch.setattr(directory, "org_users", users)
    monkeypatch.setattr(directory, "org_employees", employees)
    monkeypatch.setattr(directory, "org_name", name)
    org_settings.invalidate()
    holder["setup"] = setup
    yield holder
    pm.set_sessionmaker(None)
    nt.set_transport(nt.SmtpTransport())


async def _rows(model, **where):
    async with pm.session() as s:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return (await s.execute(q)).scalars().all()


# ══════════════════════════ notify ═════════════════════════════════════════════

def test_notify_roles_resolve_to_users_and_dedupe(db):
    async def go():
        await db["setup"]()
        t = FakeTransport()
        nt.set_transport(t)
        # hr family = hr_admin + hr; u-hr1 is ALSO named and ALSO passed as employee → once only
        await nt.notify(ORG, roles=["hr"], user_ids=["u-hr1", "u-hr1"], employee_ids=["e-hr1"],
                        title="Hello", kind="general")
        await nt.drain()
        notes = await _rows(pm.Notification, org_id=ORG)
        assert sorted(n.user_id for n in notes) == ["u-hr1", "u-hr2"]
        assert sorted(m["to"] for m in t.sent) == ["hr1@example.test", "hr2@example.test"]
    run(go())


def test_notify_payroll_family_and_exact_role(db):
    async def go():
        await db["setup"]()
        nt.set_transport(FakeTransport())
        r = await nt.resolve_recipients(ORG, roles=["payroll"])
        assert [x["user_id"] for x in r] == ["u-pay"]
        r = await nt.resolve_recipients(ORG, roles=["hr_admin"])          # exact name → only that role
        assert [x["user_id"] for x in r] == ["u-hr1"]
        assert await nt.resolve_recipients(ORG, roles=[]) == []
    run(go())


def test_notify_never_raises(db, monkeypatch):
    async def go():
        await db["setup"]()

        async def boom(org_id):
            raise RuntimeError("directory is down")
        monkeypatch.setattr(directory, "org_users", boom)
        assert await nt.notify(ORG, roles=["hr"], title="x") is None        # swallowed
        pm.set_sessionmaker(None)                                           # and with a broken DB url
        monkeypatch.setattr(pm, "get_sessionmaker", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        assert await nt.notify(ORG, user_ids=["u-emp"], title="x") is None
    run(go())


def test_email_sent_only_when_smtp_accepts(db):
    async def go():
        await db["setup"]()
        t = FakeTransport()
        nt.set_transport(t)
        await nt.notify(ORG, user_ids=["u-emp"], title="Leave approved", kind="leave")
        await nt.drain()
        d = (await _rows(pm.Delivery, org_id=ORG, channel="email"))[0]
        assert d.status == "sent" and d.sent_at is not None and d.reason is None
        assert (await _rows(pm.Delivery, org_id=ORG, channel="in_app"))[0].status == "delivered"
    run(go())


def test_no_smtp_is_not_configured_and_in_app_still_goes(db):
    async def go():
        await db["setup"]()
        t = FakeTransport(configured=False)
        nt.set_transport(t)
        await nt.notify(ORG, user_ids=["u-emp"], title="Leave approved", kind="leave")
        await nt.drain()
        d = (await _rows(pm.Delivery, org_id=ORG, channel="email"))[0]
        assert d.status == "not_configured" and d.sent_at is None and t.sent == []
        assert len(await _rows(pm.Notification, org_id=ORG, user_id="u-emp")) == 1
    run(go())


def test_failure_keeps_reason_and_retry_can_succeed(db):
    async def go():
        await db["setup"]()
        nt.set_transport(FakeTransport(fail="550 mailbox unavailable"))
        await nt.notify(ORG, user_ids=["u-emp"], title="Hello")
        await nt.drain()
        d = (await _rows(pm.Delivery, org_id=ORG, channel="email"))[0]
        assert d.status == "failed" and "550 mailbox unavailable" in d.reason and d.sent_at is None
        good = FakeTransport()
        nt.set_transport(good)
        assert await nt.retry_delivery(OTHER_ORG, d.id) == "not_retryable"   # another org cannot retry it
        assert await nt.retry_delivery(ORG, d.id) == "sent"
        d = (await _rows(pm.Delivery, id=d.id))[0]
        assert d.status == "sent" and d.attempts == 2 and len(good.sent) == 1
        assert await nt.retry_delivery(ORG, d.id) == "not_retryable"         # a sent mail is never re-sent
    run(go())


def test_preference_off_no_address_no_login_and_email_false(db):
    async def go():
        await db["setup"]()
        t = FakeTransport()
        nt.set_transport(t)
        async with pm.session() as s:
            s.add(pm.NotifyPreference(org_id=ORG, user_id="u-emp", kind="leave", email_on=False))
            await s.commit()
        await nt.notify(ORG, user_ids=["u-emp", "u-noemail"], employee_ids=["e-nologin"],
                        title="Hello", kind="leave")
        await nt.drain()
        st = {(d.recipient_name, d.channel): d.status for d in await _rows(pm.Delivery, org_id=ORG)}
        assert st[("Staff Member", "email")] == "off_by_user"
        assert st[("Staff Member", "in_app")] == "delivered"          # in-app is always on
        assert st[("No Mail", "email")] == "no_address"
        assert st[("New Joiner", "in_app")] == "no_login"
        assert st[("New Joiner", "email")] == "sent"
        assert [m["to"] for m in t.sent] == ["joiner@example.test"]
        await nt.notify(ORG, user_ids=["u-mgr"], title="In-app only", email=False)
        await nt.drain()
        assert [d.channel for d in await _rows(pm.Delivery, org_id=ORG, user_id="u-mgr")] == ["in_app"]
    run(go())


def test_email_never_carries_body_or_amounts(db):
    async def go():
        await db["setup"]()
        t = FakeTransport()
        nt.set_transport(t)
        await nt.notify(ORG, user_ids=["u-emp"], title="Salary revised to $125,000 for SIN 046 454 286",
                        body="New salary 1250000. Review text: needs to improve.", link="/my-pay",
                        kind="compensation")
        await nt.drain()
        m = t.sent[0]
        for secret in ("125,000", "1250000", "046 454 286", "needs to improve"):
            assert secret not in m["subject"] and secret not in m["text"]
        assert "[hidden]" in m["subject"] and "/my-pay" in m["text"]
        n = (await _rows(pm.Notification, org_id=ORG))[0]
        assert "needs to improve" in n.body                         # the screen still shows it
    run(go())


def test_template_allow_list_and_org_sender(db):
    assert nt.unknown_placeholders("Hi {{recipient_name}} {{ salary }} {{__class__}}") == ["__class__", "salary"]
    out = nt.render_template("{{title}} {{nope}} {7*7} {{ link }}", {"title": "T", "link": "L", "nope": "X"})
    assert out == "T {{nope}} {7*7} L"

    async def go():
        await db["setup"]()
        t = FakeTransport()
        nt.set_transport(t)
        async with pm.session() as s:
            s.add(pm.NotifyTemplate(org_id=ORG, kind="leave", subject="[{{kind}}] {{title}}",
                                    body="Dear {{recipient_name}}, open {{link}} — {{org_name}}"))
            s.add(pm.OrgSetting(org_id=ORG, key="notification_sender",
                                value={"sender_name": "Example HR", "reply_to": "hr@example.test"}))
            await s.commit()
        await nt.notify(ORG, user_ids=["u-emp"], title="Leave approved", link="/leave", kind="leave")
        await nt.drain()
        m = t.sent[0]
        assert m["subject"] == "[Leave] Leave approved"
        assert m["text"].startswith("Dear Staff Member, open ") and m["text"].endswith("/leave — Example HR")
        assert m["sender_name"] == "Example HR" and m["reply_to"] == "hr@example.test"
    run(go())


def test_notify_later_fires_once_and_key_replaces(db):
    async def go():
        await db["setup"]()
        nt.set_transport(FakeTransport())
        now = pm.now_utc()
        await nt.notify_later(ORG, due_at=now - timedelta(minutes=1), key="probation:e-emp",
                              user_ids=["u-mgr"], title="Old wording")
        await nt.notify_later(ORG, due_at=now - timedelta(minutes=1), key="probation:e-emp",
                              user_ids=["u-mgr"], title="Probation review due")
        await nt.notify_later(ORG, due_at=now + timedelta(days=5), key="later", user_ids=["u-mgr"], title="Not yet")
        await nt.notify_later(ORG, due_at=now - timedelta(minutes=1), key="gone", user_ids=["u-mgr"], title="Cancelled")
        await nt.cancel_later(ORG, "gone")
        assert await nt.fire_due_reminders() == 1
        assert await nt.fire_due_reminders() == 0
        await nt.drain()
        assert [n.title for n in await _rows(pm.Notification, org_id=ORG)] == ["Probation review due"]
    run(go())


# ══════════════════════════ approvals: who may act (pure) ══════════════════════

EMP = {e["id"]: e for e in EMPLOYEES}
USR = {u["id"]: u for u in USERS}


def _actor(uid):
    u = USR[uid]
    return {"user_id": uid, "role": u["role"], "employee_id": u["employee_id"]}


def test_may_act_role_named_manager():
    item = {"requester_user_id": "u-emp", "approver_roles": ["hr"], "approver_user_ids": ["u-pay"],
            "approver_manager_of_employee_id": "e-emp"}
    assert ac.may_act(item, _actor("u-hr1"), employees=EMP)["reason"] == "role"        # hr_admin ∈ hr
    assert ac.may_act(item, _actor("u-hr2"), employees=EMP)["reason"] == "role"
    assert ac.may_act(item, _actor("u-pay"), employees=EMP)["reason"] == "named"
    assert ac.may_act(item, _actor("u-mgr"), employees=EMP)["reason"] == "manager"
    assert ac.may_act(item, _actor("u-noemail"), employees=EMP) == {"allowed": False, "reason": "none", "on_behalf_of": None}
    # nobody decides their own request …
    assert ac.may_act(item, _actor("u-emp"), employees=EMP)["reason"] == "own_request"
    own = dict(item, requester_user_id="u-hr1")
    assert ac.may_act(own, _actor("u-hr1"), employees=EMP)["allowed"] is False
    # … except an org admin (so a one-admin company is not stuck)
    assert ac.may_act(dict(item, requester_user_id="u-admin"), _actor("u-admin"), employees=EMP)["allowed"] is True
    assert ac.may_act({"approver_roles": ["payroll"]}, _actor("u-admin"), employees=EMP)["reason"] == "admin"
    # manager-of is the CURRENT manager only
    other = {"approver_manager_of_employee_id": "e-mgr"}
    assert ac.may_act(other, _actor("u-mgr"), employees=EMP)["allowed"] is False


def test_may_act_delegation_window_and_principal():
    today = date(2026, 10, 12)
    item = {"requester_user_id": "u-emp", "approver_manager_of_employee_id": "e-emp"}
    dele = [{"from_user_id": "u-mgr", "to_user_id": "u-noemail", "start_date": date(2026, 10, 10),
             "end_date": date(2026, 10, 20), "cancelled_at": None}]
    kw = dict(employees=EMP, users=USR)
    assert ac.may_act(item, _actor("u-noemail"), delegations=dele, today=today, **kw) == \
        {"allowed": True, "reason": "delegate", "on_behalf_of": "u-mgr"}
    assert ac.may_act(item, _actor("u-noemail"), delegations=dele, today=date(2026, 10, 9), **kw)["allowed"] is False
    assert ac.may_act(item, _actor("u-noemail"), delegations=dele, today=date(2026, 10, 21), **kw)["allowed"] is False
    cancelled = [dict(dele[0], cancelled_at="2026-10-11")]
    assert ac.may_act(item, _actor("u-noemail"), delegations=cancelled, today=today, **kw)["allowed"] is False
    # a delegation only passes on what the delegator could do themselves
    hr_only = {"requester_user_id": "u-emp", "approver_roles": ["hr"]}
    assert ac.may_act(hr_only, _actor("u-noemail"), delegations=dele, today=today, **kw)["allowed"] is False
    # and never lets the delegator's own request come back through the delegate
    mgr_own = {"requester_user_id": "u-mgr", "approver_user_ids": ["u-mgr"]}
    assert ac.may_act(mgr_own, _actor("u-noemail"), delegations=dele, today=today, **kw)["allowed"] is False
    ids = ac.approver_user_ids(item, USERS, EMP, dele, today)
    assert ids == ["u-mgr", "u-noemail"]


# ══════════════════════════ approvals: open / close ════════════════════════════

def test_open_item_is_idempotent_and_notifies_once(db):
    async def go():
        await db["setup"]()
        nt.set_transport(FakeTransport())
        kw = dict(kind="salary_revision", ref_id="rev-1", title="Salary revision — Staff Member",
                  summary=["Effective 1 Nov 2026"], requester_user_id="u-hr1",
                  approver_roles=["org_admin"], link="/revisions", callback_path="/payroll/rev/rev-1/{decision}")
        a = await ac.open_item(ORG, **kw)
        b = await ac.open_item(ORG, **dict(kw, title="Salary revision — updated"))
        await nt.drain()
        assert a and a == b
        items = await _rows(pm.ApprovalItem, org_id=ORG)
        assert len(items) == 1 and items[0].title == "Salary revision — updated" and items[0].cycle == 1
        assert items[0].requester_name == "Hr One"
        notes = await _rows(pm.Notification, org_id=ORG)
        assert [n.user_id for n in notes] == ["u-admin"]              # told once, not twice
        # another org with the same kind + ref is a different item
        c = await ac.open_item(OTHER_ORG, **kw)
        assert c and c != a
    run(go())


def test_close_item_is_idempotent_and_reopen_keeps_history(db):
    async def go():
        await db["setup"]()
        nt.set_transport(FakeTransport())
        kw = dict(kind="exit_case", ref_id="x-1", title="Exit — Staff Member", summary="Last day 30 Nov",
                  requester_user_id="u-emp", approver_manager_of_employee_id="e-emp", link="/exit", callback_path="")
        iid = await ac.open_item(ORG, **kw)
        assert await ac.close_item(ORG, kind="exit_case", ref_id="x-1", decision="approve", actor_user_id="u-mgr") == iid
        assert await ac.close_item(ORG, kind="exit_case", ref_id="x-1", decision="rejected", actor_user_id="u-hr1") is None
        assert await ac.close_item(ORG, kind="exit_case", ref_id="missing", decision="approved", actor_user_id="u-mgr") is None
        assert await ac.close_item(OTHER_ORG, kind="exit_case", ref_id="x-1", decision="approved", actor_user_id="u-mgr") is None
        decs = await _rows(pm.ApprovalDecision, org_id=ORG)
        assert len(decs) == 1 and decs[0].decision == "approved" and decs[0].actor_name == "Team Lead"
        item = (await _rows(pm.ApprovalItem, org_id=ORG))[0]
        assert item.status == "approved" and item.closed_at is not None
        # re-opened after a decision → same row, cycle 2, the first decision is still there
        assert await ac.open_item(ORG, **kw) == iid
        await ac.close_item(ORG, kind="exit_case", ref_id="x-1", decision="rejected", actor_user_id="u-hr1")
        await nt.drain()
        decs = sorted(await _rows(pm.ApprovalDecision, org_id=ORG), key=lambda d: d.cycle)
        assert [(d.cycle, d.decision) for d in decs] == [(1, "approved"), (2, "rejected")]
        # the requester is told about each decision
        mine = [n.title for n in await _rows(pm.Notification, org_id=ORG, user_id="u-emp")]
        assert len(mine) == 2 and mine[0].startswith("Exit case approved")
    run(go())


def test_open_close_never_raise(db, monkeypatch):
    async def go():
        monkeypatch.setattr(pm, "get_sessionmaker", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        pm.set_sessionmaker(None)
        assert await ac.open_item(ORG, kind="offer", ref_id="1", title="t", summary=None, requester_user_id=None,
                                  link="", callback_path="") == ""
        assert await ac.close_item(ORG, kind="offer", ref_id="1", decision="approved", actor_user_id=None) is None
        assert await ac.close_item(ORG, kind="offer", ref_id="1", decision="banana", actor_user_id=None) is None
    run(go())


# ══════════════════════════ settings ═══════════════════════════════════════════

def test_get_setting_defaults_subkeys_and_cache(db):
    async def go():
        await db["setup"]()
        assert await org_settings.get_setting(ORG, "numbering.employee_prefix") == "EMP"
        assert await org_settings.get_setting(ORG, "work_week.weekly_offs") == [5, 6]
        assert await org_settings.get_setting(ORG, "numbering.nope", "fallback") == "fallback"
        assert await org_settings.get_setting(ORG, "no_such_group", 7) == 7
        assert await org_settings.get_setting(ORG, "company.legal_name", "Unnamed") == "Unnamed"   # "" → default
        async with pm.session() as s:
            s.add(pm.OrgSetting(org_id=ORG, key="numbering", value={"employee_prefix": "LX"}))
            await s.commit()
        assert await org_settings.get_setting(ORG, "numbering.employee_prefix") == "EMP"   # still cached
        org_settings.invalidate(ORG)
        assert await org_settings.get_setting(ORG, "numbering.employee_prefix") == "LX"
        assert await org_settings.get_setting(ORG, "numbering.offer_prefix") == "OFR"      # default kept
        assert await org_settings.get_setting(OTHER_ORG, "numbering.employee_prefix") == "EMP"  # tenant-scoped
        whole = await org_settings.get_setting(ORG, "numbering")
        whole["employee_prefix"] = "MUTATED"                                                # a copy, not the cache
        assert await org_settings.get_setting(ORG, "numbering.employee_prefix") == "LX"
    run(go())


def test_get_setting_never_raises(db, monkeypatch):
    async def go():
        monkeypatch.setattr(pm, "get_sessionmaker", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        pm.set_sessionmaker(None)
        org_settings.invalidate()
        assert await org_settings.get_setting(ORG, "approvals.escalation_days") == 3
    run(go())


def test_settings_read_write_roles():
    assert org_settings.can_write("org_admin") and not org_settings.can_write("hr_admin")
    assert not org_settings.can_write("employee") and not org_settings.can_write(None)
    assert org_settings.can_read("company", "employee") and org_settings.can_read("numbering", "manager")
    assert org_settings.can_read("registrations", "hr_admin") and org_settings.can_read("registrations", "payroll")
    assert not org_settings.can_read("registrations", "employee")
    assert not org_settings.can_read("registrations", "manager")
    assert org_settings.can_read("notification_sender", "org_admin")
    assert not org_settings.can_read("notification_sender", "hr_admin")
    assert not org_settings.can_read("unknown_group", "org_admin")
