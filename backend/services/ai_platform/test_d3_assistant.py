"""D3 tests — AI that acts + MCP. No Postgres (SQLite in memory), no paid calls (fake
Claude), no network (service HTTP mocked).

Run:  cd backend/services/ai_platform && python -m pytest -q test_d3_assistant.py
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.setdefault("LEXORA_RATELIMIT_BACKEND", "memory")

import httpx as RealHttpx  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

RealAsyncClient = RealHttpx.AsyncClient

from shared.utils import platform_models as pm  # noqa: E402

import actions as ACT  # noqa: E402
import ai_config as CFG  # noqa: E402
import assistant as AS  # noqa: E402
import claude_client as LLM  # noqa: E402
import copilot as CP  # noqa: E402
import metering as MT  # noqa: E402
import tokens as TK  # noqa: E402
import tools_registry as TR  # noqa: E402
from ai_models import D3_TABLES  # noqa: E402
from g5_models import G5_TABLES  # noqa: E402
from mcp_server import server as MS  # noqa: E402
from shield import Shield, leaks  # noqa: E402

ORG, EMP_UID, MGR_UID = "11111111-1111-1111-1111-111111111111", "u-emp", "u-mgr"
TODAY = date(2026, 10, 16)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def db():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    sm = async_sessionmaker(eng, expire_on_commit=False)

    async def mk():
        async with eng.begin() as c:
            await c.run_sync(lambda cc: pm.PlatformBase.metadata.create_all(cc, tables=D3_TABLES + G5_TABLES))
    run(mk())
    pm.set_sessionmaker(sm)
    ACT._audit_rows = []

    async def fake_audit(request, action, **kw):
        ACT._audit_rows.append({"action": action, "actor": request.headers.get("x-user-id"), **kw})
        return True

    async def fake_audit_change(request, action, **kw):
        ACT._audit_rows.append({"action": action, "actor": request.headers.get("x-user-id"), "changes": kw})
        return True
    ACT._audit, ACT._audit_change = fake_audit, fake_audit_change
    yield sm
    LLM.set_fake(None)


# ── mocked sibling services ──────────────────────────────────────────────────────
BAL = {"fiscal_year": "2026", "balances": [{"leave_type_code": "VAC", "leave_type_name": "Vacation", "allocated": 12, "carried_over": 0, "used": 3, "pending": 1, "remaining": 8}]}
REQS = [{"id": "req-00000001", "leave_type_code": "VAC", "leave_type_name": "Vacation", "start_date": "2026-10-20", "end_date": "2026-10-21", "days": 2, "status": "pending",
         "employee_id": "e-1", "employee_name": "Asha Rao", "manager_name": "Ravi"}]
SLIPS = [{"stub_id": "stub-9", "pay_date": "2026-09-25", "period_end": "2026-09-19", "gross": 3269.23, "net_pay": 2412.5}]
SLIP = {"gross": 3269.23, "net_pay": 2412.5, "deductions": {"cpp": 173.73, "ei": 53.29, "income_tax": 629.71}, "entries": {"earnings": []}}
COMPLIANCE = {"as_of_edition": "2026", "constants": [
    {"key": "cpp.max_employee", "label": "CPP maximum employee contribution", "value": "4230.45", "source": "CRA T4127 (Jan 2026)",
     "status": "verified", "verified_on": "2026-08-26", "by": "CRA PDOC reconciliation"},
    {"key": "ON.bpa", "label": "Ontario basic personal amount", "value": "12747", "source": "CRA T4127 Ontario", "status": "verified",
     "verified_on": "2026-08-26", "by": "CRA PDOC reconciliation"},
    {"key": "NS.bpa", "label": "Nova Scotia basic personal amount", "value": "11744", "source": "CRA T4127 Nova Scotia", "status": "estimated"}]}


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data
        self.content = b"%PDF-1.4 demo" if status == 200 and data is None else json.dumps(data).encode()

    def json(self):
        if self._d is None:
            raise ValueError("binary")
        return self._d


class FakeHttp:
    """Replaces httpx.AsyncClient inside copilot / actions: routes by path."""
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _route(self, method, url, headers, body):
        FakeHttp.calls.append((method, url, headers or {}, body))
        p = url.split("8000", 1)[-1]
        role = (headers or {}).get("x-role", "")
        if p.startswith("/leave/balances"):
            return _Resp(200, BAL)
        if p.startswith("/leave/requests/") and p.endswith("/cancel"):
            return _Resp(200, {"id": p.split("/")[3], "status": "cancelled"})
        if p.startswith("/leave/requests/") and (p.endswith("/approve") or p.endswith("/reject")):
            if role not in ("manager", "hr_admin", "org_admin"):
                return _Resp(403, {"detail": "Only the manager or HR can decide"})
            return _Resp(200, {"id": p.split("/")[3], "status": "approved"})
        if p.startswith("/leave/requests") and method == "POST":
            return _Resp(201, {"id": "req-new", "status": "pending"})
        if p.startswith("/leave/requests"):
            return _Resp(200, {"items": REQS})
        if p.startswith("/payroll/payslips/stub-9/pdf"):
            return _Resp(200, None)
        if p.startswith("/payroll/payslips/stub-9"):
            return _Resp(200, SLIP)
        if p.startswith("/payroll/payslips"):
            return _Resp(200, SLIPS)
        if p.startswith("/payroll/compliance"):
            return _Resp(200, COMPLIANCE)
        if p.startswith("/approvals/inbox") or p.startswith("/approvals/mine"):
            return _Resp(200, {"items": []})
        if p.startswith("/approvals/chains"):
            return _Resp(404, {"detail": "not found"})
        if p.startswith("/employees/e-1/profile"):
            return _Resp(200, {"employee": {"first_name": "Asha", "last_name": "Rao", "employee_number": "EMP-0001", "department": "Sales", "sin": "046454286"},
                               "manager_chain": [{"name": "Ravi Kumar", "job_title": "Sales Head"}], "reports": []})
        if p.startswith("/employees/e-1") and method == "PATCH":
            return _Resp(200, {"id": "e-1", **(body or {})})
        if p.startswith("/employees/e-1"):
            return _Resp(200, {"id": "e-1", "phone": "4165550000", "city": "Toronto"})
        if p.startswith("/engage/helpdesk/tickets"):
            return _Resp(201, {"id": "tkt-1", "status": "open", "ref": "HD-0001"})
        if p.startswith("/compliance/privacy/requests"):
            return _Resp(201, {"id": "dpr-1", "ref": "DPR-0001", "status": "open"})
        if p.startswith("/compliance/privacy/notice"):
            return _Resp(200, {"text": "Why we collect your data\n\nWe keep attendance for 3 years."})
        if p.startswith("/exit/cases"):
            return _Resp(201, {"id": "exit-1", "case_number": "EXT-0001", "status": "requested"})
        if p.startswith("/time/regularizations") and method == "POST":
            return _Resp(201, {"id": "reg-1", "status": "pending"})
        return _Resp(404, {"detail": f"no mock for {p}"})

    async def get(self, url, headers=None, params=None):
        return self._route("GET", url, headers, params)

    async def post(self, url, headers=None, json=None):
        return self._route("POST", url, headers, json)

    async def patch(self, url, headers=None, json=None):
        return self._route("PATCH", url, headers, json)


@pytest.fixture(autouse=True)
def http(monkeypatch):
    FakeHttp.calls = []
    monkeypatch.setattr(CP.httpx, "AsyncClient", FakeHttp)
    monkeypatch.setattr(ACT.httpx, "AsyncClient", FakeHttp)

    async def _policy_text(org, h):
        return "Company: MAPLE Foods.\nWorking days: Monday, Tuesday, Wednesday, Thursday, Friday. Weekly off: Saturday, Sunday.\nPolicy document: Leave Policy (hr, version 2, effective 2026-01-01, published).\nLeave type: Vacation (VAC)."
    monkeypatch.setattr(CP, "policy_text", _policy_text)
    yield


def hdr(role="employee", uid=EMP_UID, emp="e-1", today=TODAY):
    return {"x-org-id": ORG, "x-user-id": uid, "x-user-email": f"{uid}@maple.test", "x-role": role, "x-employee-id": emp, "x-today": today.isoformat()}


def ctx(role="employee", uid=EMP_UID, emp="e-1", scopes=None, channel="chat"):
    return TR.Ctx(headers=hdr(role, uid, emp), channel=channel, scopes=set(scopes or {"read", "act"}))


# ── fake Claude ──────────────────────────────────────────────────────────────────
class FakeClaude:
    """Scripted: a list of turns; each is ('tool', name, args) or ('text', str). Records every request."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.beta = NS(messages=NS(create=self.create))

    async def create(self, **kw):
        self.requests.append(kw)
        kind, *rest = self.turns.pop(0)
        if kind == "tool":
            name, args = rest
            return NS(content=[NS(type="tool_use", id=f"tu_{len(self.requests)}", name=name, input=args)], stop_reason="tool_use",
                      usage=NS(input_tokens=120, output_tokens=30), model=kw["model"])
        if kind == "refusal":
            return NS(content=[], stop_reason="refusal", usage=NS(input_tokens=10, output_tokens=0), model=kw["model"],
                      stop_details=NS(category="cyber", explanation="no"))
        return NS(content=[NS(type="text", text=rest[0])], stop_reason="end_turn", usage=NS(input_tokens=200, output_tokens=60), model=kw["model"])


# ══════════════════════════ shield round-trip ═══════════════════════════════════

def test_shield_round_trip_through_tool_result_and_reply():
    sh = Shield()
    out = sh.protect_value({"gross": 85000.0, "sin": "046454286", "note": "phone 4165550142", "days": 12})
    assert leaks(json.dumps(out)) == []
    reply = f"Gross {out['gross']} on SIN {out['sin']}; 12 days."
    assert sh.restore(reply) == "Gross 85000 on SIN 046454286; 12 days."
    assert sh.restore_value({"a": out["sin"]}) == {"a": "046454286"}


def test_shield_keeps_record_ids_whose_middle_groups_are_all_digits():
    # 2026-10-25: a record id whose middle groups are all digits came back as a masked token on an approval card
    uid = "196c4340-9755-4672-8936-b8a6b4fb39ee"
    sh = Shield()
    assert sh.protect_value({"case_id": uid}) == {"case_id": uid}
    assert sh.protect(f"/ai/agents/exit?case={uid} SIN 046 454 286") == f"/ai/agents/exit?case={uid} SIN [[SIN_1]]"


# ══════════════════════════ tool argument validation + schema ═══════════════════

def test_tool_schemas_are_strict_and_validation_rejects_bad_args():
    for t in TR.claude_tools(list(TR.TOOLS.values())):
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False and "required" in t["input_schema"]
    r = run(TR.call(ctx(), "apply_leave", {"leave_type": "VAC", "start_date": "20-10-2026"}))
    assert "Invalid arguments" in r["error"] and "start_date" in r["error"]
    r = run(TR.call(ctx(role="manager"), "decide_leave", {"request_id": "req-00000001", "decision": "maybe"}))
    assert "Invalid arguments" in r["error"]
    r = run(TR.call(ctx(), "payslips", {"months": 99}))
    assert "Invalid arguments" in r["error"]
    r = run(TR.call(ctx(), "nope", {}))
    assert "Unknown tool" in r["error"]


def test_read_tools_return_sources_as_the_caller():
    r = run(TR.call(ctx(), "leave_balance", {}))
    assert r["balances"][0]["remaining"] == 8 and r["source"]["system"] == "leave-service" and r["source"]["link"] == "/leave"
    assert FakeHttp.calls[-1][2]["x-user-id"] == EMP_UID            # identity forwarded = the caller
    r = run(TR.call(ctx(), "statutory_rule", {"topic": "CPP maximum contribution"}))
    assert r["rules"][0]["id"] == "cpp.max_employee" and r["rules"][0]["status"] == "verified" and r["source"]["link"] == "/payroll/compliance"
    r = run(TR.call(ctx(), "policy_search", {"question": "what are the working days"}))
    assert any("Working days" in p["text"] for p in r["passages"]) and r["passages"][0]["link"]
    r = run(TR.call(ctx(), "my_profile", {}))
    assert "sin" not in r["profile"] and r["manager_chain"][0]["name"] == "Ravi Kumar"
    r = run(TR.call(ctx(), "who_approves", {"request_type": "leave"}))
    assert r["published_chain"] is None and "manager" in r["default_rule"] and r["my_manager_chain"] == ["Ravi Kumar"]
    r = run(TR.call(ctx(), "explain_payslip_line", {"line": "cpp", "month": "2026-09"}))
    assert r["values"][0]["amount"] == 173.73 and "5.95%" in r["how_it_is_worked_out"]


# ══════════════════════════ actions: refuse without confirmation ════════════════

def test_action_tool_only_proposes_and_nothing_is_written():
    c = ctx()
    r = run(TR.call(c, "apply_leave", {"leave_type": "vacation", "start_date": "2026-10-20", "end_date": "2026-10-21"}))
    assert r["proposed"] is True and r["card"]["requires_confirmations"] == 1 and "NOT done" in r["note"]
    assert not any(m == "POST" and "/leave/requests" in u for m, u, _, _ in FakeHttp.calls)      # no write happened
    cards = run(ACT.persist(c, c.proposals))
    assert cards[0]["status"] == "pending" and cards[0]["card"]["what"].startswith("Apply Vacation from 2026-10-20")
    assert "payload" not in cards[0]["card"]


def test_every_action_refuses_without_confirmation_and_executes_only_after():
    samples = {"apply_leave": {"leave_type": "VAC", "start_date": "2026-10-20"}, "cancel_leave": {"request_id": "req-00000001"},
               "raise_helpdesk_ticket": {"subject": "T4 missing", "category": "payroll"},
               "request_regularisation": {"work_date": "2026-10-10", "reason": "forgot punch", "requested_in": "09:30"},
               "send_payslip": {"month": "2026-09"}, "start_exit_request": {"requested_last_working_day": "2026-12-31", "reason": "relocating"},
               "request_letter": {"letter_type": "employment_verification", "purpose": "visa"},
               "update_my_contact": {"phone": "4165550199", "city": "Toronto"}}
    for name, args in samples.items():
        c = ctx()
        before = len(FakeHttp.calls)
        r = run(TR.call(c, name, args))
        assert r.get("proposed") is True, (name, r)
        writes = [x for x in FakeHttp.calls[before:] if x[0] in ("POST", "PATCH")]
        assert writes == [], (name, writes)                                                     # proposal never writes
        card = run(ACT.persist(c, c.proposals))[0]
        assert card["requires_confirmations"] == ACT.CONFIRMATIONS[name]
        # confirm (twice when required) → executes exactly once, audited "via: ai"
        ACT._audit_rows.clear()
        res = run(ACT.confirm(c, card["id"]))
        if ACT.CONFIRMATIONS[name] == 2:
            assert res["executed"] is False and res["status"] == "pending" and "confirm once more" in res["message"]
            assert [x for x in FakeHttp.calls[before:] if x[0] in ("POST", "PATCH")] == []
            res = run(ACT.confirm(c, card["id"]))
        if name == "send_payslip":
            assert res["executed"] is False and "not configured" in res["message"]              # SMTP absent → honest failure
            assert ACT._audit_rows[0]["meta"]["ok"] is False
            continue
        assert res["executed"] is True and res["status"] == "done", (name, res)
        writes = [x for x in FakeHttp.calls[before:] if x[0] in ("POST", "PATCH")]
        assert len(writes) == 1 and writes[0][2]["x-user-id"] == EMP_UID, (name, writes)
        a = ACT._audit_rows[0]
        assert a["action"] == f"ai.action.{name}" and a["meta"]["via"] == "ai" and a["meta"]["confirmations"] == ACT.CONFIRMATIONS[name]
        assert leaks(json.dumps(a["meta"]["payload"])) == []
        # a second confirm is refused — already done
        with pytest.raises(ACT.ActionError) as e:
            run(ACT.confirm(c, card["id"]))
        assert e.value.status == 409


def test_cancel_expiry_and_other_user_cannot_confirm():
    c = ctx()
    run(TR.call(c, "raise_helpdesk_ticket", {"subject": "Need ID card", "category": "it_access"}))
    card = run(ACT.persist(c, c.proposals))[0]
    with pytest.raises(ACT.ActionError) as e:
        run(ACT.confirm(ctx(uid="someone-else"), card["id"]))
    assert e.value.status == 404
    r = run(ACT.cancel(c, card["id"]))
    assert r["status"] == "cancelled" and not [x for x in FakeHttp.calls if x[0] == "POST"]
    # expiry
    run(TR.call(c, "raise_helpdesk_ticket", {"subject": "Late", "category": "other"}))
    card = run(ACT.persist(c, c.proposals[-1:]))[0]

    async def age():
        from sqlalchemy import update
        from ai_models import AiAction
        async with pm.session() as s:
            await s.execute(update(AiAction).where(AiAction.id == card["id"]).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
            await s.commit()
    run(age())
    with pytest.raises(ACT.ActionError) as e:
        run(ACT.confirm(c, card["id"]))
    assert "expired" in e.value.message


def test_update_contact_is_privacy_request_for_employee_and_direct_for_hr():
    c = ctx()
    run(TR.call(c, "update_my_contact", {"phone": "4165550199"}))
    card = run(ACT.persist(c, c.proposals))[0]
    run(ACT.confirm(c, card["id"]))
    w = [x for x in FakeHttp.calls if x[0] == "POST"][-1]
    assert "/compliance/privacy/requests" in w[1] and w[3]["request_type"] == "correction" and w[3]["payload"]["via"] == "ai"
    FakeHttp.calls.clear(); ACT._audit_rows.clear()
    h = ctx(role="hr_admin", uid="u-hr", emp="e-1")
    run(TR.call(h, "update_my_contact", {"phone": "4165550199"}))
    card = run(ACT.persist(h, h.proposals))[0]
    res = run(ACT.confirm(h, card["id"]))
    assert res["executed"] and [x for x in FakeHttp.calls if x[0] == "PATCH"]
    assert ACT._audit_rows[0]["action"] == "employee.self_update" and ACT._audit_rows[0]["changes"]["extra"]["privacy"] is True
    assert ACT._audit_rows[1]["action"] == "ai.action.update_my_contact"


# ══════════════════════════ RBAC ═════════════════════════════════════════════════

def test_rbac_employee_cannot_approve_and_scopes_hide_tools():
    r = run(TR.call(ctx(role="employee"), "decide_leave", {"request_id": "req-00000001", "decision": "approve"}))
    assert "cannot use decide_leave" in r["error"]
    names = {t.name for t in TR.visible_tools("employee", {"read", "act"})}
    assert "decide_leave" not in names and "team_leave_requests" not in names and "apply_leave" in names and "org_ai_usage" not in names
    assert "decide_leave" in {t.name for t in TR.visible_tools("manager", {"read", "act"})}
    assert "org_ai_usage" in {t.name for t in TR.visible_tools("org_admin", {"read", "act", "admin"})}
    # read-only token: act tools invisible AND refused
    ro = ctx(scopes={"read"})
    assert all(t.scope == "read" for t in TR.visible_tools("employee", ro.scopes))
    r = run(TR.call(ro, "apply_leave", {"leave_type": "CL", "start_date": "2026-10-20"}))
    assert "no 'act' scope" in r["error"]
    # the manager path forwards the manager's identity; the service's own gate is what finally decides
    m = ctx(role="manager", uid=MGR_UID, emp="e-9")
    r = run(TR.call(m, "decide_leave", {"request_id": "req-00000001", "decision": "approve"}))
    assert r["proposed"] and r["card"]["requires_confirmations"] == 2 and r["card"]["for_whom"] == "Asha Rao"


# ══════════════════════════ assistant loop with the fake model ══════════════════

def test_assistant_loop_shields_cites_and_proposes():
    fake = FakeClaude([("tool", "leave_balance", {}), ("tool", "apply_leave", {"leave_type": "VAC", "start_date": "2026-10-20", "end_date": "2026-10-21"}),
                       ("text", "You have 8 vacation days left [S1]. A request for 2026-10-20 to 2026-10-21 is ready — press Confirm.")])
    c = ctx()
    res = run(AS.answer(c, "vacation balance then apply VAC 20-21 Oct, my SIN is 046 454 286", [], client=fake, model="claude-opus-5-5", effort="medium",
                        role="employee", country="CA", today=TODAY, org_name="MAPLE"))
    assert res["mode"] == "live" and res["citations"][0]["id"] == "S1" and res["citations"][0]["tool"] == "leave_balance"
    assert res["cited_ids"] == ["S1"] and len(c.proposals) == 1 and c.proposals[0]["action"] == "apply_leave"
    assert res["input_tokens"] == 440 and res["output_tokens"] == 120
    # nothing raw reached the model: prompt + every tool result
    first = fake.requests[0]
    assert "046 454 286" not in json.dumps(first["messages"], default=vars) and leaks(first["messages"][0]["content"]) == []
    for req in fake.requests:
        for m in req["messages"]:
            if isinstance(m["content"], list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        assert leaks(b["content"]) == [], b["content"]
    # prompt caching (2026-10-24): fallbacks + top-level automatic caching ride in extra_body
    assert first["output_config"] == {"effort": "medium"} and "thinking" not in first
    assert first["extra_body"] == {"fallbacks": "default", "cache_control": {"type": "ephemeral"}}
    assert first["betas"] == [LLM.FALLBACK_BETA] and first["max_tokens"] == 4000
    assert {t["name"] for t in first["tools"]} >= {"leave_balance", "apply_leave", "policy_search"} and "decide_leave" not in {t["name"] for t in first["tools"]}


def test_assistant_refusal_and_actions_off():
    res = run(AS.answer(ctx(), "x", [], client=FakeClaude([("refusal",)]), model="claude-opus-5-5", effort="low", role="employee", country="CA",
                        today=TODAY, org_name="K"))
    assert res["refusal"]["category"] == "cyber" and res["stop_reason"] == "refusal"
    fake = FakeClaude([("text", "ok")])
    run(AS.answer(ctx(), "x", [], client=fake, model="claude-opus-5-5", effort="low", role="employee", country="CA", today=TODAY, org_name="K",
                  allow_actions=False))
    sys_text = " ".join(b["text"] for b in fake.requests[0]["system"])      # system = [cached rules, context]
    assert all(TR.TOOLS[t["name"]].scope == "read" for t in fake.requests[0]["tools"]) and "cannot perform actions" in sys_text


# ══════════════════════════ metering maths + caps ═══════════════════════════════

def test_metering_math_and_caps():
    c = MT.cost_of("claude-opus-5-5", 1_000_000, 100_000, {}, None)
    assert c["cost_usd"] == 6.0 and c["cost_cad"] is None and "USD→CAD" in c["note"]
    c = MT.cost_of("claude-opus-5-5", 1_000_000, 100_000, {}, 84.0)
    assert c["cost_cad"] == 504.0
    c = MT.cost_of("claude-opus-5-5", 500_000, 0, {"claude-opus-5-5": {"input_usd_per_mtok": 2, "output_usd_per_mtok": 10}}, 80)
    assert c["cost_usd"] == 1.0 and c["cost_cad"] == 80.0
    c = MT.cost_of("claude-unknown-9", 10, 10, {}, 80)
    assert c["cost_usd"] is None and c["cost_cad"] is None and "set on the AI settings page" in c["note"]
    s = MT.cap_status(85_000, None, 100_000, 0, "soft")
    assert s["warn"] and not s["over"] and not s["blocked"] and s["pct"] == 85.0
    s = MT.cap_status(120_000, 10.0, 100_000, 500, "hard")
    assert s["over"] and s["blocked"]
    s = MT.cap_status(10, None, 0, 500, "hard")
    assert s["pct"] is None and not s["blocked"] and s["cad_cap_enforceable"] is False
    run(MT.record(ORG, user_id=EMP_UID, user_email="a@x", channel="chat", feature="assistant_chat", model="claude-opus-5-5", key_source="org",
                  input_tokens=1000, output_tokens=200, cost=MT.cost_of("claude-opus-5-5", 1000, 200, {}, 84.0)))
    run(MT.record(ORG, user_id="u2", user_email="b@x", channel="mcp", feature="key_test", model="claude-opus-5-5", key_source="platform",
                  input_tokens=10, output_tokens=5, cost=MT.cost_of("claude-opus-5-5", 10, 5, {}, None)))
    t = run(MT.month_totals(ORG))
    assert t["tokens"] == 1215 and t["calls"] == 2 and t["cost_usd"] == round(0.004 + 0.004 + 0.00004 + 0.0001, 6) and t["cost_cad"] is None
    assert run(MT.month_totals(ORG, EMP_UID))["cost_cad"] == 0.67
    s = run(MT.summary(ORG))
    assert {u["user"] for u in s["by_user"]} == {"a@x", "b@x"} and s["by_channel"][0]["channel"] == "chat" and len(s["recent"]) == 2


def test_org_settings_key_is_encrypted_and_resolves_with_platform_fallback():
    cfg = run(CFG.load(ORG))
    assert not CFG.configured(cfg) and CFG.resolve_key(cfg) == (None, None)
    out = run(CFG.save(ORG, {"api_key": "sk-ant-test-abcd1234", "model": "claude-sonnet-5-5", "effort": "high", "monthly_cap_tokens": 500000,
                             "cap_mode": "hard", "usd_cad_rate": 84, "features": {"mcp": False}, "prices": {"claude-x": {"input_usd_per_mtok": 1, "output_usd_per_mtok": 2}}}, "admin@x"))
    assert out["has_org_key"] and out["key_last4"] == "1234" and out["features"] == {"qa": True, "actions": True, "mcp": False} and out["model"] == "claude-sonnet-5-5"
    cfg = run(CFG.load(ORG))
    assert cfg["_key_enc"].startswith("enc:") and "sk-ant" not in cfg["_key_enc"] and CFG.resolve_key(cfg) == ("sk-ant-test-abcd1234", "org")
    run(CFG.save(ORG, {"clear_key": True}, "admin@x"))
    assert CFG.resolve_key(run(CFG.load(ORG))) == (None, None)
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-platform"
    try:
        assert CFG.resolve_key(run(CFG.load(ORG))) == ("sk-ant-platform", "platform")
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


# ══════════════════════════ AI access tokens ═════════════════════════════════════

def test_access_tokens_lifecycle_scopes_and_rate_limit():
    raw, row = run(TK.create(ORG, name="Claude Desktop", user_id=EMP_UID, user_email="asha@x", role="employee", employee_id="e-1", scopes=["act"],
                             days=10, rate_per_min=2, created_by="admin@x"))
    assert raw.startswith("lxai_") and row["scopes"] == ["read", "act"] and TK.clean_scopes(["admin", "bogus"]) == ["read", "admin"]
    got, err = run(TK.authenticate(raw))
    assert err is None and got["id"] == row["id"] and TK.headers_for(got)["x-role"] == "employee" and TK.headers_for(got)["x-employee-id"] == "e-1"
    _, err = run(TK.authenticate(raw))
    assert err is None
    _, err = run(TK.authenticate(raw))
    assert err and "Rate limit" in err                           # 3rd call in the minute at 2/min
    assert run(TK.authenticate("nonsense"))[1].startswith("Send an AI access token")
    assert run(TK.authenticate("lxai_deadbeef_" + "x" * 32))[1] == "Unknown AI access token."
    assert run(TK.revoke(ORG, row["id"]))
    assert run(TK.authenticate(raw))[1] == "This AI access token was revoked."
    assert TK.verdict({"revoked_at": None, "expires_at": datetime.now(timezone.utc) - timedelta(days=1)}) == "expired"
    items = run(TK.list_for_org(ORG))
    assert items[0]["status"] == "revoked" and "token_hash" not in items[0]


# ══════════════════════════ MCP: in-process server + stdio ══════════════════════

def test_mcp_in_memory_list_and_call_with_scopes():
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    async def go():
        server = MS.build_server()
        MS.current_ctx.set(ctx(scopes={"read"}, channel="mcp"))
        async with connect(server) as client:
            tools = (await client.list_tools()).tools
            names = {t.name for t in tools}
            assert "leave_balance" in names and "apply_leave" not in names and "confirm_action" not in names
            assert next(t for t in tools if t.name == "leave_balance").annotations.readOnlyHint is True
            r = await client.call_tool("leave_balance", {})
            data = json.loads(r.content[0].text)
            assert data["balances"][0]["remaining"] == 8
            r = await client.call_tool("apply_leave", {"leave_type": "CL", "start_date": "2026-10-20"})
            assert "no 'act' scope" in json.loads(r.content[0].text)["error"]
        MS.current_ctx.set(ctx(scopes={"read", "act"}, channel="mcp"))
        async with connect(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"apply_leave", "confirm_action", "cancel_action"} <= names
            r = json.loads((await client.call_tool("cancel_leave", {"request_id": "req-00000001"})).content[0].text)
            assert r["proposed"] and r["pending_actions"][0]["requires_confirmations"] == 2 and "twice" in r["next_step"]
            aid = r["pending_actions"][0]["id"]
            assert not [x for x in FakeHttp.calls if x[0] == "POST"]
            r1 = json.loads((await client.call_tool("confirm_action", {"action_id": aid})).content[0].text)
            assert r1["executed"] is False
            r2 = json.loads((await client.call_tool("confirm_action", {"action_id": aid})).content[0].text)
            assert r2["executed"] is True and r2["channel"] == "mcp"
            assert ACT._audit_rows[-1]["meta"]["channel"] == "mcp"
    run(go())


def test_mcp_tool_calls_are_logged_with_shielded_args():
    from sqlalchemy import select
    from ai_models import AiToolCall
    c = ctx(channel="mcp")
    c.token_id = "tok-1"
    run(TR.call(c, "raise_helpdesk_ticket", {"subject": "SIN 046 454 286 wrong", "category": "payroll"}))

    async def rows():
        async with pm.session() as s:
            return (await s.execute(select(AiToolCall))).scalars().all()
    r = run(rows())
    assert len(r) == 1 and r[0].token_id == "tok-1" and r[0].channel == "mcp" and "046 454 286" not in json.dumps(r[0].args) and r[0].ok


def test_mcp_http_transport_auth_json_endpoints_and_jsonrpc():
    """The ASGI app main.py mounts at /ai/mcp: bearer token → identity; plain JSON endpoints; MCP JSON-RPC over POST."""
    from mcp_server import transport_http as TH
    raw, row = run(TK.create(ORG, name="Desktop", user_id=EMP_UID, user_email="asha@x", role="employee", employee_id="e-1", scopes=["act"],
                             days=5, rate_per_min=100, created_by="admin@x"))

    async def go():
        async with TH.lifespan():
            tr = RealHttpx.ASGITransport(app=TH.app)
            async with RealAsyncClient(transport=tr, base_url="http://t/ai/mcp") as c:    # the fixture fakes httpx.AsyncClient module-wide
                assert (await c.get("/tools")).status_code == 401                                   # no token
                assert (await c.get("/tools", headers={"Authorization": "Bearer lxai_bad_" + "y" * 30})).status_code == 401
                h = {"Authorization": f"Bearer {raw}"}
                who = (await c.get("/whoami", headers=h)).json()
                assert who["user_id"] == EMP_UID and who["scopes"] == ["act", "read"] and who["employee_linked"]
                names = {t["name"] for t in (await c.get("/tools", headers=h)).json()["tools"]}
                assert {"leave_balance", "apply_leave", "confirm_action"} <= names and "decide_leave" not in names
                r = (await c.post("/call", headers=h, json={"name": "leave_balance", "arguments": {}})).json()
                assert r["balances"][0]["remaining"] == 8
                # identity headers sent by the client are ignored — the token decides
                r = (await c.post("/call", headers={**h, "x-role": "org_admin", "x-user-id": "evil"}, json={"name": "decide_leave",
                                  "arguments": {"request_id": "req-00000001", "decision": "approve"}})).json()
                assert "cannot use decide_leave" in r["error"]
                # MCP JSON-RPC (streamable HTTP, stateless, JSON responses)
                mh = {**h, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
                init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}}
                r = await c.post("", headers=mh, json=init)
                assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "lexora-hrm", r.text
                r = await c.post("", headers=mh, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                assert "apply_leave" in {t["name"] for t in r.json()["result"]["tools"]}
                r = await c.post("", headers=mh, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "leave_balance", "arguments": {}}})
                assert json.loads(r.json()["result"]["content"][0]["text"])["balances"][0]["remaining"] == 8
    run(go())


def test_mcp_exact_path_is_served_not_redirected():
    """main.py installs the transport on a FastAPI app: POST /ai/mcp (no slash) must reach it — a bare
    mount answered 307 → /ai/mcp/ with the container's internal host, before any token check."""
    from fastapi import FastAPI
    from mcp_server import transport_http as TH
    raw, _ = run(TK.create(ORG, name="Exact", user_id=EMP_UID, user_email="asha@x", role="employee", employee_id="e-1", scopes=["read"],
                           days=5, rate_per_min=100, created_by="admin@x"))
    fa = FastAPI()
    TH.install(fa, "/ai/mcp")

    async def go():
        async with TH.lifespan():
            async with RealAsyncClient(transport=RealHttpx.ASGITransport(app=fa), base_url="http://t") as c:
                body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}}
                mh = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
                r = await c.post("/ai/mcp", headers=mh, json=body)
                assert r.status_code == 401, (r.status_code, r.headers.get("location"))
                r = await c.post("/ai/mcp", headers={**mh, "Authorization": f"Bearer {raw}"}, json=body)
                assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "lexora-hrm", r.text
                assert (await c.get("/ai/mcp/whoami", headers={"Authorization": f"Bearer {raw}"})).json()["scopes"] == ["read"]
    run(go())


def test_mcp_stdio_bridge_lists_and_calls_over_a_real_subprocess():
    """A true stdio round trip: spawn the bridge (fake backend), speak MCP through the official client."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def go():
        env = {**os.environ, "LEXORA_MCP_BACKEND": "fake", "PYTHONPATH": os.pathsep.join([HERE, os.path.abspath(os.path.join(HERE, "..", ".."))])}
        params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "mcp_server", "stdio_bridge.py")], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert names == {"leave_balance", "apply_leave"}
                r = await session.call_tool("leave_balance", {})
                assert json.loads(r.content[0].text)["balances"][0]["remaining"] == 7
                r = await session.call_tool("apply_leave", {"leave_type": "CL", "start_date": "2026-10-20"})
                assert json.loads(r.content[0].text)["pending_actions"][0]["id"] == "demo-1"
    try:
        run(go())
    except Exception:
        proc = subprocess.run([sys.executable, "-c", "import mcp, httpx"], capture_output=True)
        if proc.returncode != 0:
            pytest.skip("mcp / httpx not installed in this interpreter")
        raise


def test_statutory_rule_reads_the_verification_center_for_every_role():
    """The Canadian constants (with source + verified status) come from GET /payroll/compliance for every role."""
    FakeHttp.calls.clear()
    r = run(TR.call(ctx(), "statutory_rule", {"topic": "Ontario basic personal amount"}))
    assert r["rules"][0]["id"] == "ON.bpa" and r["rules"][0]["value"] == "12747" and r["rules"][0]["status"] == "verified"
    assert any("/payroll/compliance" in c[1] for c in FakeHttp.calls)
    r = run(TR.call(ctx(role="payroll_admin"), "statutory_rule", {"topic": "Nova Scotia basic personal amount"}))
    assert r["rules"][0]["status"] == "needs_confirmation"            # an estimated constant is said to need confirmation
    import retrieval
    hits = run(retrieval.corpus(TR.Ctx(headers=hdr("manager"), channel="chat", scopes={"read"})))
    assert any(h["kind"] == "statutory_rule" and "4230.45" in h["text"] for h in hits)
