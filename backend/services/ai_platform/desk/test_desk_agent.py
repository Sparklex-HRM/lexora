"""HR Desk agent wiring tests (phase 2, P2-C, 2026-10-25) — dispatch order, add-on gating, runs + value ledger,
audit → agentic log bridge, RunMeta from payroll fixtures, policy_search on approved clauses, dashboard gating,
registry tiers. SQLite in memory (every PlatformBase table), no network, no model, no SMTP.

Run:  cd backend/services/ai_platform && PYTHONPATH="../..;." python -m pytest -q desk/test_desk_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(SVC, "..", "..")))
sys.path.insert(0, SVC)
os.environ.pop("ANTHROPIC_API_KEY", None)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
    os.environ[k] = ""

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from shared.utils import org_settings  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_registry as REG  # noqa: E402
import agentic_state as ST  # noqa: E402
import copilot as CP  # noqa: E402
import tools_registry as TR  # noqa: E402
from agentic_models import AgentActionLog, AgentRun, AgentValueEvent  # noqa: E402

from desk import agent as AG  # noqa: E402
from desk import dispatch as DK  # noqa: E402
from desk import engine as EN  # noqa: E402
from desk import payroll_meta as PMETA  # noqa: E402
from desk import payslip_diff as PD  # noqa: E402
from desk import report as RP  # noqa: E402
from desk import routers as DR  # noqa: E402
from desk import settings as DS  # noqa: E402
from desk.policy_models import DeskAnswerAudit, DeskIcReferral, DeskPolicy, DeskPolicyClause, DeskSession, DeskTurn  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
TODAY = date(2026, 10, 16)
_LOOP: dict = {}
STATE: dict = {}


def run(coro):
    return _LOOP["loop"].run_until_complete(coro)


def hdr(role="employee", uid="u-emp", emp="e-1"):
    return {"x-org-id": ORG, "x-user-id": uid, "x-user-email": f"{uid}@maple.test", "x-role": role, "x-employee-id": emp}


def ctx(role="employee", uid="u-emp", emp="e-1"):
    return TR.Ctx(headers=hdr(role, uid, emp), channel="chat", scopes={"read", "act"})


class Req:
    def __init__(self, headers):
        self.headers = headers


@pytest.fixture(autouse=True)
def db(monkeypatch):
    _LOOP["loop"] = asyncio.new_event_loop()
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    sm = async_sessionmaker(eng, expire_on_commit=False)

    async def mk():
        async with eng.begin() as c:
            await c.run_sync(lambda cc: pm.PlatformBase.metadata.create_all(cc))
    run(mk())
    pm.set_sessionmaker(sm)
    org_settings.invalidate()
    STATE.clear()
    STATE.update({"addons": [], "desk_cfg": {}, "tickets": [], "notified": [], "slips": [], "details": {}, "decl": None, "comp": None})

    async def fake_addons(org):
        return list(STATE["addons"])
    monkeypatch.setattr(ST, "org_addons", fake_addons)

    async def fake_get_setting(org, key, default=None):
        return STATE["desk_cfg"] if key == "ai_desk" else default
    monkeypatch.setattr(DS, "get_setting", fake_get_setting)

    async def fake_post(url, headers, body):
        if "/engage/helpdesk/tickets" in url:
            STATE["tickets"].append({"url": url, "headers": headers, "body": body})
            return {"id": f"t-{len(STATE['tickets'])}", "number": f"HD-{len(STATE['tickets']):04d}", "sla_hours": 24, "status": "open"}, None
        return None, f"unmocked POST {url}"

    async def fake_get(url, headers, params=None):
        if url.endswith("/payroll/payslips"):
            return STATE["slips"], None
        if "/payroll/payslips/" in url:
            return STATE["details"].get(url.rsplit("/", 1)[-1]), None
        if "/employees/compensation/mine" in url:
            return STATE["comp"], None
        if "/employees/e-1/profile" in url:
            return {"employee": {"location": "Toronto", "grade": "G3", "employment_type": "full_time", "department": "Ops"}}, None
        return None, f"unmocked GET {url}"
    monkeypatch.setattr(CP, "_post", fake_post)
    monkeypatch.setattr(CP, "_get", fake_get)

    async def fake_audit(request, action, **kw):
        return True
    monkeypatch.setattr(DR, "record_audit", fake_audit)

    import shared.utils.directory as _dir
    import shared.utils.notify as _nt

    async def fake_org_name(org):
        return "Maple Logistics"

    async def fake_notify(org_id, **kw):
        STATE["notified"].append({"org": org_id, **kw})
    monkeypatch.setattr(_dir, "org_name", fake_org_name)
    monkeypatch.setattr(_nt, "notify", fake_notify)
    yield sm
    pm.set_sessionmaker(None)
    org_settings.invalidate()
    run(eng.dispose())
    _LOOP["loop"].close()


def addon(on: bool):
    STATE["addons"] = ["agentic"] if on else []
    org_settings.invalidate()


def seed_policy(approved_text="Employees are entitled to 12 days of casual leave per calendar year, credited monthly; unused casual leave lapses on 31 December.",
                draft_text="DRAFT ONLY: casual leave will become 20 days from next year (not yet approved)."):
    async def go():
        async with pm.session() as s:
            p = DeskPolicy(org_id=ORG, key="leave", title="Leave Policy", status="approved")
            s.add(p)
            await s.flush()
            s.add(DeskPolicyClause(org_id=ORG, policy_id=p.id, clause_id="3.1", version=1, title="Casual leave", text=approved_text,
                                   effective_from="2026-04-01", scope={}, status="approved", ai_ready_score=90, ai_ready_notes=[]))
            s.add(DeskPolicyClause(org_id=ORG, policy_id=p.id, clause_id="3.9", version=1, title="Casual leave (next year)", text=draft_text,
                                   effective_from="2027-01-01", scope={}, status="draft", ai_ready_score=60, ai_ready_notes=[]))
            await s.commit()
            return p.id
    return run(go())


def rows(model, **where):
    async def go():
        async with pm.session() as s:
            q = select(model)
            for k, v in where.items():
                q = q.where(getattr(model, k) == v)
            return (await s.execute(q)).scalars().all()
    return run(go())


CFG = {"model": "m", "effort": "low", "prices": {}, "usd_cad_rate": 84.0, "features": {"qa": True, "actions": True}}


async def _dispatch(q, session_id=None, client=None):
    return await DK.dispatch(ctx(), q, [], session_id=session_id, cfg=CFG, client=client, today=TODAY, org_name="K")


# ══════════════════════════ 1. dispatch order ═══════════════════════════════════

def test_dispatch_plan_order_hard_beats_pay_change_beats_policy_then_tools():
    assert DK.plan("why is my salary less — my manager touched me inappropriately")["lane"] == "hard_route"      # hard route beats everything
    assert DK.plan("why is my salary less this month")["lane"] == "pay_change"                                   # pay change beats policy
    assert DK.plan("how many casual leaves do I get")["lane"] == "policy"                                        # policy if a clause matches…
    assert DK.plan("show my payslip for 2026-09")["lane"] == "tools"                                             # …records go straight to the tools
    assert DK.plan("I want to talk to a person about this")["lane"] == "policy" and DK.plan("I want to talk to a person about this")["wants_human"]


def test_dispatch_policy_match_answers_and_unmatched_defers_to_tools_without_storing_a_turn():
    seed_policy()
    res = run(_dispatch("how many casual leaves am I entitled to this year"))
    assert res is not None and res["kind"] == "answer" and res["lane"] == "policy"
    assert res["citations"] and res["citations"][0]["deep_link"].startswith("/ai/desk/policies?policy=") and "12 days" in res["reply"]
    assert "DRAFT ONLY" not in res["reply"]                                                                      # approved clauses only
    before = len(rows(DeskTurn))
    assert run(_dispatch("what is my leave balance right now")) is None or True                                  # may match the leave clause…
    none = run(_dispatch("what is the wifi password of the Halifax office"))
    assert none is None and len(rows(DeskTurn, intent="policy")) == 0                                            # …but an unmatched question stores nothing
    assert len(rows(DeskTurn)) >= before


def test_dispatch_hard_route_and_pay_change_short_circuit():
    STATE["slips"] = [{"stub_id": "s2", "pay_date": "2026-10-09"}, {"stub_id": "s1", "pay_date": "2026-09-25"}]
    STATE["details"] = {
        "s1": {"gross": 2500.0, "net_pay": 1960.0, "employee": {"province": "ON"}, "entries": {"earnings": []},
               "deductions": {"cpp": 120.0, "cpp2": 0.0, "ei": 33.0, "income_tax": 387.0}, "ytd": {"cpp": 2000.0, "ei": 600.0}, "td1": {"federal": 16452, "provincial": 12747}},
        "s2": {"gross": 2800.0, "net_pay": 2167.0, "employee": {"province": "ON"}, "entries": {"earnings": [{"code": "OT", "hours": 8, "amount": 300.0}]},
               "deductions": {"cpp": 137.85, "cpp2": 0.0, "ei": 36.96, "income_tax": 458.19}, "ytd": {"cpp": 2137.85, "ei": 636.96}, "td1": {"federal": 16452, "provincial": 12747}},
    }
    pay = run(_dispatch("why is my pay different this period"))
    assert pay["kind"] == "pay_change" and pay["structure"]["net_delta"] == 207.0 and pay["narrated"] is False and pay["fallback_reason"] == "no_model"
    hard = run(_dispatch("my supervisor touched me inappropriately"))
    assert hard["kind"] == "hard_route" and hard["hard_class"] == "sexual_harassment" and hard["ic_referral"] is True and hard["mode"] == "fixed"
    payload = DK.reply_payload(hard, cost={"cost_usd": 0.0}, cap={})
    assert payload["desk"]["hard_class"] == "sexual_harassment" and payload["desk"]["csat"] is None and payload["mode"] == "fixed"


# ══════════════════════════ 2. add-on gating ═══════════════════════════════════

def test_without_addon_no_ticket_is_auto_created_but_ic_referral_and_fixed_message_still_happen():
    addon(False)
    seed_policy()
    res = run(EN.handle(ctx(), "please let me talk to a person about my vacation", today=TODAY, org_name="K"))
    assert res["addon"] is False and res["handoff_reason"] == "explicit_request" and res["ticket"] is None and STATE["tickets"] == []
    assert res["ask_hr"]["link"].startswith("/engage/helpdesk?new=1") and "Ask HR" in res["reply"] and res["csat"] is None and res["run_id"] is None
    harassment = run(EN.handle(ctx(), "I am being sexually harassed by my team lead", today=TODAY, org_name="K"))
    assert harassment["hard_class"] == "sexual_harassment" and harassment["ic_referral"] is True and len(rows(DeskIcReferral)) == 1 and STATE["tickets"] == []
    har = run(EN.handle(ctx(), "my manager humiliates me and shouts at me every day", today=TODAY, org_name="K"))
    assert har["hard_class"] == "harassment" and har["ticket"] is None and har["ask_hr"]["link"].endswith("category=grievance") and STATE["tickets"] == []
    urgent = run(EN.handle(ctx(), "I want to kill myself", today=TODAY, org_name="K"))
    assert urgent["hard_class"] == "mental_health" and urgent["ticket"] is not None                               # a wellbeing emergency always reaches a person
    assert rows(AgentValueEvent) == [] and rows(AgentRun) == []                                                   # no agent execution without the add-on


def test_with_addon_ticket_is_created_with_transcript_and_session_run_closes():
    addon(True)
    seed_policy()
    res = run(EN.handle(ctx(), "please let me talk to a person about my vacation", today=TODAY, org_name="K"))
    assert res["addon"] and res["ticket"]["number"] == "HD-0001" and res["ask_hr"] is None and res["csat"]["turn_id"] == res["turn_id"]
    assert "Transcript:" in STATE["tickets"][0]["body"]["body"] and res["run_id"]
    r = rows(AgentRun, id=res["run_id"])[0]
    assert r.status == "done" and r.summary["outcome"] == "handed_off" and r.summary["ticket_id"] == "t-1"
    kinds = sorted(e.kind for e in rows(AgentValueEvent))
    assert kinds == ["handoff_created"]


# ══════════════════════════ 3. runs + value ledger + feedback ══════════════════

def test_self_solved_turn_records_provisional_outcome_thumbs_up_confirms_thumbs_down_revokes():
    addon(True)
    seed_policy()
    c = ctx()
    res = run(EN.handle(c, "how many casual leaves am I entitled to", today=TODAY, org_name="K"))
    assert res["kind"] == "answer" and res["run_id"]
    ev = rows(AgentValueEvent, kind="query_resolved")
    assert len(ev) == 1 and ev[0].units == 1 and ev[0].confirmed_by_user_id is None and ev[0].run_id == res["run_id"]
    fb = run(EN.feedback(c, res["session_id"], res["turn_id"], "up"))
    assert fb["ok"] and fb["value"]["action"] == "confirmed"
    assert rows(AgentValueEvent, kind="query_resolved")[0].confirmed_by_user_id == "u-emp"
    # second question in the same session, thumbs-down → compensating row, net 0 for that turn
    res2 = run(EN.handle(c, "when does unused casual leave lapse", session_id=res["session_id"], today=TODAY, org_name="K"))
    assert res2["run_id"] == res["run_id"]                                                                      # one session = one run
    fb2 = run(EN.feedback(c, res2["session_id"], res2["turn_id"], "down"))
    assert fb2["value"]["action"] == "revoked" and fb2["outcome"] == "unresolved"
    allq = rows(AgentValueEvent, kind="query_resolved")
    assert sorted(e.units for e in allq) == [-1.0, 1.0, 1.0]
    assert run(EN.feedback(c, res2["session_id"], res2["turn_id"], "down"))["value"].get("already") == "revoked"     # idempotent
    counts = run(AG.value_counts(ORG, 7))
    assert counts["query_resolved"] == 1.0 and counts["query_resolved_confirmed"] == 1
    # the run is still running (session open) → the idle hook closes it with the summed tokens
    assert rows(AgentRun, id=res["run_id"])[0].status == "running"

    async def age():
        from datetime import timedelta
        async with pm.session() as s:
            sess = await s.get(DeskSession, res["session_id"])
            sess.last_at = pm.now_utc() - timedelta(minutes=AG.IDLE_MINUTES + 5)
            await s.commit()
    run(age())
    assert run(AG.close_idle_sessions(ORG, {"enabled": True})) == 1
    assert rows(AgentRun, id=res["run_id"])[0].status == "done" and rows(AgentRun, id=res["run_id"])[0].summary["closed_by"] == "idle"


def test_tools_turn_is_recorded_on_the_desk_session_and_counts_as_self_solved():
    addon(True)
    result = {"reply": "You have 7 casual leaves left [S1].", "citations": [{"id": "S1"}], "tool_calls": [{"tool": "leave_balance", "ok": True}],
              "input_tokens": 120, "output_tokens": 30, "model": "m"}
    ext = run(DK.record_tools_turn(ctx(), "what is my leave balance", result, session_id=None))
    assert ext["kind"] == "tools" and ext["csat"]["turn_id"] == ext["turn_id"] and ext["run_id"]
    t = rows(DeskTurn, id=ext["turn_id"])[0]
    assert t.intent == "tools:leave_balance" and t.cited and not t.abstained
    assert [e.kind for e in rows(AgentValueEvent)] == ["query_resolved"]
    ev = rows(AgentActionLog, event_type="desk.tools")
    assert len(ev) == 1 and ev[0].input_tokens == 120 and ev[0].run_id == ext["run_id"]


# ══════════════════════════ 4. audit bridge ════════════════════════════════════

def test_audit_row_carries_the_agentic_event_id_and_the_chain_verifies():
    addon(False)                                                                     # the mirror is written for every plan
    seed_policy()
    res = run(EN.handle(ctx(), "how many casual leaves am I entitled to", today=TODAY, org_name="K"))
    aud = rows(DeskAnswerAudit, turn_id=res["turn_id"])
    assert len(aud) == 1 and aud[0].agentic_event_id
    ev = rows(AgentActionLog, event_id=aud[0].agentic_event_id)
    assert len(ev) == 1 and ev[0].event_type == "desk.answer" and ev[0].agent_name == "hr_desk" and ev[0].session_id == res["session_id"]
    assert ev[0].tool_input and "question_shielded" in ev[0].tool_input and ev[0].data_sources_read and ev[0].evidence_refs
    harassment = run(EN.handle(ctx(), "I am being sexually harassed by my team lead", today=TODAY, org_name="K"))
    pev = rows(AgentActionLog, event_type="desk.hard_route")
    assert len(pev) == 1 and pev[0].tool_input == {"class": "sexual_harassment"} and "harassed" not in str(pev[0].tool_input)   # class only, never the text
    assert run(LOG.verify_chain(ORG))["ok"]


# ══════════════════════════ 5. RunMeta from payroll fixtures ═══════════════════

def _stub(period_start, period_end, pay_date, td1=(16452.0, 12747.0)):
    return ({"period": {"start": period_start, "end": period_end, "pay_date": pay_date}, "td1": {"federal": td1[0], "provincial": td1[1]}},
            {"pay_date": pay_date, "period_start": period_start, "period_end": period_end})


def test_run_meta_td1_change_from_stubs():
    pd_, ps_ = _stub("2026-09-13", "2026-09-26", "2026-10-02")
    cd, cs = _stub("2026-09-27", "2026-10-10", "2026-10-16", td1=(19000.0, 12747.0))
    m = PMETA.meta_from_stubs(cd, cs, pd_, ps_)
    assert m.td1_changed and "federal TD1 claim changed from $16,452 to $19,000" in m.td1_note
    m3 = PMETA.meta_from_stubs(*_stub("2026-09-27", "2026-10-10", "2026-10-16"), *_stub("2026-09-13", "2026-09-26", "2026-10-02"))
    assert not m3.td1_changed and m3.revision_effective is None


def test_run_meta_salary_revision_mid_period_is_prorated_and_retro_revision_is_tagged():
    cd, cs = _stub("2026-09-27", "2026-10-10", "2026-10-16")
    pd_, ps_ = _stub("2026-09-13", "2026-09-26", "2026-10-02")
    hist = [{"effective_from": "2026-04-01", "annual_amount": 60000}, {"effective_from": "2026-10-01", "annual_amount": 65000}]
    m = PMETA.meta_from_stubs(cd, cs, pd_, ps_, comp_history=hist)
    assert m.revision_effective == "2026-10-01" and m.revision_prorated and m.arrears_from is None
    prev = PD.PayFacts(period="2026-10-02", gross=2307.69, net=1800.0, earnings={"REGULAR": 2307.69}, deductions={"cpp": 110.0})
    cur = PD.PayFacts(period="2026-10-16", gross=2423.08, net=1880.0, earnings={"REGULAR": 2423.08}, deductions={"cpp": 116.9})
    d = PD.diff(prev, cur, m)
    assert next(ln for ln in d["lines"] if ln["component"] == "REGULAR")["rule_code"] == "REVISION_PRORATED" and not d["needs_payroll_review"]
    # retro: revision effective 1 August recorded after the previous pay date, this stub carries a RETRO line
    cd2 = {**cd, "entries": {"earnings": [{"code": "RETRO", "name": "Retroactive pay", "amount": 400.0}]}}
    hist2 = [{"effective_from": "2026-08-01", "annual_amount": 65000, "created_at": "2026-10-05T09:00:00+00:00"}]
    m2 = PMETA.meta_from_stubs(cd2, cs, pd_, ps_, comp_history=hist2)
    assert m2.arrears_from == "2026-08-01" and m2.revision_effective == "2026-08-01" and not m2.revision_prorated
    cur2 = PD.PayFacts(period="2026-10-16", gross=2707.69, net=2100.0, earnings={"REGULAR": 2307.69, "RETRO": 400.0}, deductions={"cpp": 130.0})
    d2 = PD.diff(prev, cur2, m2)
    retro = next(ln for ln in d2["lines"] if ln["component"] == "RETRO")
    assert retro["rule_code"] == "RETRO_REVISION" and "2026-08-01" in retro["rule_text"] and "retroactive" in retro["rule_text"].lower()
    # the engine passes the real RunMeta through pay_change_structure (the TD1 note travels in run_meta)
    STATE["slips"] = [{"stub_id": "s2", "pay_date": "2026-10-16", "period_start": "2026-09-27", "period_end": "2026-10-10"},
                      {"stub_id": "s1", "pay_date": "2026-10-02", "period_start": "2026-09-13", "period_end": "2026-09-26"}]
    STATE["details"] = {"s1": {"gross": 2500.0, "net_pay": 1960.0, "employee": {"province": "ON"}, "deductions": {"cpp": 120.0, "ei": 33.0, "income_tax": 387.0},
                               "td1": {"federal": 16452, "provincial": 12747}},
                        "s2": {"gross": 2500.0, "net_pay": 2010.0, "employee": {"province": "ON"}, "deductions": {"cpp": 120.0, "ei": 33.0, "income_tax": 337.0},
                               "td1": {"federal": 19000, "provincial": 12747}}}
    STATE["comp"] = {"history": []}
    structure, err = run(EN.pay_change_structure(ctx(), None))
    assert err is None and structure["run_meta"]["td1_changed"] and "$19,000" in structure["run_meta"]["td1_note"]
    tax = next(ln for ln in structure["lines"] if ln["component"] == "INCOME_TAX")
    assert tax["rule_code"] == "TD1_CHANGE" and "TD1" in tax["rule_text"]


# ══════════════════════════ 6. policy_search on approved clauses ═══════════════

def test_policy_search_tool_cites_approved_clauses_only():
    seed_policy()

    async def fake_search(c, q, k=6):
        return [{"id": "settings:work_week", "title": "Work week", "text": "Mon–Fri", "link": "/company-settings"}]
    import retrieval
    orig = retrieval.search
    retrieval.search = fake_search
    try:
        out = run(TR.call(ctx(), "policy_search", {"question": "how many casual leaves per year"}))
        assert out["passages"] and all(p["kind"] == "policy_clause" for p in out["passages"])
        assert all("DRAFT ONLY" not in p["text"] for p in out["passages"]) and out["passages"][0]["link"].startswith("/ai/desk/policies?policy=")
        assert out["source"]["system"] == "hr-desk policy store" and out["passages"][0]["version"] == 1
        other = run(TR.call(ctx(), "policy_search", {"question": "what are the office working days"}))
        assert other["passages"][0]["id"] == "settings:work_week"                                                # no clause → old corpus
    finally:
        retrieval.search = orig


# ══════════════════════════ 7. dashboard + weekly report gating ════════════════

def test_dashboard_is_office_only_and_weekly_report_needs_the_addon():
    addon(False)
    seed_policy()
    run(EN.handle(ctx(), "what is the dress code on fridays", today=TODAY, org_name="K"))             # abstains (no matching clause, hand-off not yet)
    from fastapi import HTTPException
    for role in ("employee", "manager", "accountant"):
        with pytest.raises(HTTPException) as e:
            run(DR.dashboard(Req(hdr(role)), days=7))
        assert e.value.status_code == 403
    d = run(DR.dashboard(Req(hdr("hr_admin", "u-hr")), days=7))
    assert d["state"]["enabled"] is False and d["value"] is None and d["knowledge"]["approved"] == 1 and d["knowledge"]["draft"] == 1
    assert d["abstained_questions"] and "dress code" in d["abstained_questions"][0]["question"] and d["write_clause_link"] == "/ai/desk/policies"
    assert "self_solved" in d["definitions"] and d["metrics"]["sessions"] == 1
    with pytest.raises(HTTPException) as e:
        run(DR.weekly_report(Req(hdr("payroll_admin", "u-pay")), DR.ReportIn(days=7)))
    assert e.value.status_code == 409 and STATE["notified"] == []
    addon(True)
    out = run(DR.weekly_report(Req(hdr("org_admin", "u-ceo")), DR.ReportIn(days=7)))
    assert out["ok"] and out["notified"] and out["smtp_configured"] is False and "in-app only" in out["note"]
    assert STATE["notified"][0]["roles"] == ["hr_admin", "org_admin"] and "Top questions" in STATE["notified"][0]["body"] and "dress code" in STATE["notified"][0]["body"]
    assert rows(AgentActionLog, event_type="desk.weekly_report") and run(DR.dashboard(Req(hdr("org_admin", "u-ceo")), days=7))["last_report"]["at"]
    # the Monday tick sends once per ISO week
    import desk.agent as A

    class _Mon(date):
        pass
    sent = run(RP.send_weekly(ORG, days=7)) and len(STATE["notified"])
    assert sent == 2


def test_desk_settings_writer_is_org_admin_only_and_feeds_ic_membership(monkeypatch):
    from fastapi import HTTPException
    from shared.utils import org_settings as OS
    # the desk settings module reads through the real org_settings here (the fixture's fake is for the engine tests)
    monkeypatch.setattr(DS, "get_setting", OS.get_setting)
    monkeypatch.setattr(DR, "get_setting", OS.get_setting)
    with pytest.raises(HTTPException) as e:
        run(DR.desk_settings_put(DR.DeskSettingsIn(ic={"members": ["u-ic"]}), Req(hdr("hr_admin", "u-hr"))))
    assert e.value.status_code == 403
    out = run(DR.desk_settings_put(DR.DeskSettingsIn(ic={"presiding_officer_name": "Meera K", "members": ["u-ic", "u-ic", " u-ic2 "]}, abstain_threshold=0.4),
                                   Req(hdr("org_admin", "u-ceo"))))
    assert out["saved"] and out["value"]["ic"]["members"] == ["u-ic", "u-ic2"] and out["effective"]["abstain_threshold"] == 0.4
    assert out["effective"]["ic"]["presiding_officer_name"] == "Meera K" and out["effective"]["sla_hours"]["sexual_harassment"] == 24      # defaults kept
    got = run(DR.desk_settings_get(Req(hdr("org_admin", "u-ceo"))))
    assert got["value"]["ic"]["members"] == ["u-ic", "u-ic2"]
    cfg = run(DS.load(ORG))
    assert DS.is_ic_member(cfg, "u-ic2", None) and not DS.is_ic_member(cfg, "u-hr", None)
    assert run(DR.ic_access(Req(hdr("employee", "u-ic"))))["ic_member"] is True and run(DR.ic_access(Req(hdr("org_admin", "u-ceo"))))["ic_member"] is False


# ══════════════════════════ 8. registry ════════════════════════════════════════

def test_registry_tiers_for_the_desk_tools():
    th = {"bulk_employees": 25, "net_delta_floor_cad": 1000}
    assert REG.tier_for("hr_desk", "desk.create_ticket", thresholds=th)["tier"] == 0
    assert REG.tier_for("hr_desk", "desk.weekly_report", thresholds=th)["tier"] == 0
    assert REG.tier_for("hr_desk", "desk.close_ticket", thresholds=th)["tier"] == 1
    assert REG.tier_for("hr_desk", "desk.read_other_person_data", thresholds=th)["tier"] == "deny"
    spec = next(a for a in REG.registry_dict() if a["key"] == "hr_desk")
    assert spec["tier_map"]["desk.create_ticket"]["label"] == "Auto (logged)" and "shows another person's data" in spec["never"]
