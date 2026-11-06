"""G4 performance (2026-10-23): OKRs, 360 feedback anonymity, improvement plans, 1:1s — rules + routes.

Run:  cd backend/services/employee && PYTHONPATH="../..;." python -m pytest test_perf_g4.py -q
"""
import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


import feedback360_routers as FB  # noqa: E402
import okr_routers as OK  # noqa: E402
import oneonone_routers as ON  # noqa: E402
import perf_g4_logic as G  # noqa: E402
import perf_g4_models as M  # noqa: E402
import pip_routers as PR  # noqa: E402
from hiring_logic import Actor, RuleError  # noqa: E402
from models import Employee  # noqa: E402
from performance_models import PerfGoalCycle  # noqa: E402

HR = Actor(org="o", user_id="u-hr", role="hr_admin", employee_id="e-hr")
HRM = Actor(org="o", user_id="u-hrm", role="hr_manager", employee_id="e-hrm")
MGR = Actor(org="o", user_id="u-mgr", role="manager", employee_id="e-mgr")
EMP = Actor(org="o", user_id="u-emp", role="employee", employee_id="e-emp")
PEER = Actor(org="o", user_id="u-peer", role="employee", employee_id="e-peer")
OTHER_MGR = Actor(org="o", user_id="u-m2", role="manager", employee_id="e-m2")
PAY = Actor(org="o", user_id="u-pay", role="payroll_admin", employee_id="e-pay")


# ═════════════ pure: OKRs ═════════════
def test_key_result_progress_every_metric_type():
    up = G.clean_key_result({"title": "NPS", "metric_type": "number", "start_value": 20, "target_value": 60, "current_value": 50})
    assert G.kr_progress(up) == 75.0
    down = G.clean_key_result({"title": "Defects", "metric_type": "number", "start_value": 40, "target_value": 10, "current_value": 25})
    assert G.kr_progress(down) == 50.0                                    # lower is better also works
    assert G.kr_progress({**down, "current_value": 5}) == 100.0          # clamped
    assert G.kr_progress({**up, "current_value": 0}) == 0.0
    pct = G.clean_key_result({"title": "Coverage", "metric_type": "percent", "start_value": 0, "target_value": 80, "current_value": 20})
    assert pct["unit"] == "%" and G.kr_progress(pct) == 25.0
    cur = G.clean_key_result({"title": "Revenue", "metric_type": "currency", "start_value": 0, "target_value": 1000000})
    assert cur["current_value"] == 0 and cur["unit"] == "$"
    ms = G.clean_key_result({"title": "Launch", "metric_type": "milestone",
                             "milestones": [{"label": "Spec", "done": True}, {"label": "Build"}, {"label": "Ship"}, {"label": ""}]})
    assert len(ms["milestones"]) == 3 and G.kr_progress(ms) == 33.33
    for bad in ({"title": "x", "metric_type": "number", "start_value": 5, "target_value": 5},
                {"title": "x", "metric_type": "percent", "start_value": 0, "target_value": 120},
                {"title": "x", "metric_type": "milestone", "milestones": []},
                {"title": "", "metric_type": "number", "start_value": 0, "target_value": 1},
                {"title": "x", "metric_type": "vibes"}):
        with pytest.raises(RuleError):
            G.clean_key_result(bad)


def test_rollup_weights_and_tree():
    objs = [{"id": "c", "level": "company", "parent_id": None, "weight": 100, "status": "active", "cycle_id": "k"},
            {"id": "t1", "level": "team", "parent_id": "c", "weight": 3, "status": "active", "cycle_id": "k"},
            {"id": "t2", "level": "team", "parent_id": "c", "weight": 1, "status": "active", "cycle_id": "k"},
            {"id": "x", "level": "team", "parent_id": "c", "weight": 50, "status": "cancelled", "cycle_id": "k"}]
    krs = {"t1": [{"metric_type": "number", "start_value": 0, "target_value": 10, "current_value": 10, "weight": 1}],
           "t2": [{"metric_type": "number", "start_value": 0, "target_value": 10, "current_value": 0, "weight": 1},
                  {"metric_type": "number", "start_value": 0, "target_value": 10, "current_value": 10, "weight": 3}]}
    p = G.rollup(objs, krs)
    assert p["t1"] == 100.0 and p["t2"] == 75.0
    assert p["c"] == 93.75                                     # (100x3 + 75x1) / 4, cancelled child ignored
    tree = G.build_tree([dict(o) for o in objs])
    assert [n["id"] for n in tree] == ["c"] and len(tree[0]["children"]) == 3
    assert G.confidence_band(8) == "on_track" and G.confidence_band(5) == "at_risk" and G.confidence_band(2) == "off_track"
    with pytest.raises(RuleError):
        G.clean_confidence(11)


def test_alignment_never_loops_and_stays_in_cycle():
    objs = [{"id": "a", "parent_id": None, "cycle_id": "k", "status": "active"},
            {"id": "b", "parent_id": "a", "cycle_id": "k", "status": "active"},
            {"id": "c", "parent_id": "b", "cycle_id": "k", "status": "active"},
            {"id": "z", "parent_id": None, "cycle_id": "other", "status": "active"},
            {"id": "q", "parent_id": None, "cycle_id": "k", "status": "cancelled"}]
    G.parent_ok("c", "a", objs, "k")
    for oid, parent in (("a", "c"), ("a", "a"), ("b", "z"), ("b", "q"), ("b", "nope")):
        with pytest.raises(RuleError):
            G.parent_ok(oid, parent, objs, "k")


def test_okr_permissions():
    ind = {"level": "individual", "owner_employee_id": "e-emp"}
    assert G.okr_can("view", EMP, ind, "e-mgr") and G.okr_can("edit", EMP, ind, "e-mgr")
    assert G.okr_can("edit", MGR, ind, "e-mgr") and G.okr_can("view", HR, ind, "e-mgr")
    assert not G.okr_can("view", PEER, ind, "e-mgr") and not G.okr_can("view", OTHER_MGR, ind, "e-mgr")
    assert not G.okr_can("view", PAY, ind, "e-mgr")                       # payroll is not HR here
    comp = {"level": "company", "owner_employee_id": None}
    assert G.okr_can("view", PEER, comp, None) and not G.okr_can("edit", MGR, comp, None)
    assert G.okr_can("edit", HR, comp, None) and not G.okr_can("edit", HRM, comp, None) and G.okr_can("checkin", HRM, comp, None)


def test_review_snapshot_only_included_individual_objectives():
    objs = [{"id": "1", "level": "individual", "title": "Ship v2", "weight": 40, "include_in_review": True, "status": "active"},
            {"id": "2", "level": "individual", "title": "Side", "weight": 10, "include_in_review": False, "status": "active"},
            {"id": "3", "level": "team", "title": "Team", "weight": 10, "include_in_review": True, "status": "active"},
            {"id": "4", "level": "individual", "title": "Dropped", "weight": 10, "include_in_review": True, "status": "cancelled"}]
    rows = G.review_snapshot(objs, {"1": 62.5})
    assert rows == [{"id": "okr:1", "title": "Objective: Ship v2", "measure": "Key results (rolled up)", "target": "100% of key results",
                     "weight": 40.0, "progress_pct": 62.5, "source": "okr"}]


# ═════════════ pure: 360 ═════════════
Q = G.clean_questions(G.DEFAULT_QUESTIONS + G.UPWARD_QUESTIONS)


def _ans(v, text=None):
    a = {q["key"]: v for q in Q if q["kind"] == "rating" and not q["upward"]}
    if text:
        a["q6"] = text
    return a


def test_360_small_groups_are_pooled_or_hidden_and_never_leak_by_subtraction():
    resp = ([{"relationship": "self", "answers": _ans(5)}, {"relationship": "manager", "answers": _ans(3)}]
            + [{"relationship": "peer", "answers": _ans(v, f"peer note {v}")} for v in (4, 4, 2)]
            + [{"relationship": "direct_report", "answers": _ans(1, "report note")} for _ in range(2)])
    r = G.aggregate_360(Q, resp, 3)
    groups = {g["group"]: g for g in r["groups"]}
    assert set(groups) == {"self", "manager", "peer"}                    # 2 direct reports < 3 → hidden
    assert groups["peer"]["responses"] == 3 and groups["peer"]["scores"]["q1"] == 3.33
    assert r["hidden_responses"] == 2 and "fewer than 3" in r["hidden_note"]
    # overall = manager + shown peers only: (3 + 4 + 4 + 2) / 4 = 3.25 — the hidden 1s are NOT in it
    assert r["overall"]["q1"] == 3.25
    texts = [a["text"] for c in r["comments"] for a in c["answers"]]
    assert "report note" not in texts and len(texts) == 3
    assert all("name" not in str(g) for g in r["groups"])
    # a third report arrives: now direct reports show as their own group
    r2 = G.aggregate_360(Q, resp + [{"relationship": "direct_report", "answers": _ans(1)}], 3)
    assert {g["group"] for g in r2["groups"]} == {"self", "manager", "peer", "direct_report"} and r2["hidden_responses"] == 0


def test_360_pooling_and_threshold_floor():
    resp = ([{"relationship": "peer", "answers": _ans(4)} for _ in range(2)] + [{"relationship": "other", "answers": _ans(2)}])
    r = G.aggregate_360(Q, resp, 1)                                        # threshold can never go below 3
    assert r["threshold"] == 3 and [g["group"] for g in r["groups"]] == ["pooled"] and r["groups"][0]["responses"] == 3
    only_mgr = G.aggregate_360(Q, [{"relationship": "manager", "answers": _ans(4)}, {"relationship": "peer", "answers": _ans(1)}], 3)
    assert only_mgr["overall"] is None and [g["group"] for g in only_mgr["groups"]] == ["manager"]


def test_360_nomination_rules_and_report_scope():
    assert G.nomination_rule("self", "peer", "employee") == "proposed"   # waits for the manager
    assert G.nomination_rule("manager", "peer", "employee") == "approved"
    assert G.nomination_rule("hr", "direct_report", "hr") == "approved"
    for args in (("self", "peer", "manager"), ("self", "direct_report", "employee"), ("manager", "peer", "hr"), ("self", "manager", "employee")):
        with pytest.raises(RuleError):
            G.nomination_rule(*args)
    assert G.report_scope(EMP, "e-emp", "e-mgr", "closed") is None       # not until HR releases
    assert G.report_scope(EMP, "e-emp", "e-mgr", "released") == "self"
    assert G.report_scope(MGR, "e-emp", "e-mgr", "collecting") is None and G.report_scope(MGR, "e-emp", "e-mgr", "closed") == "manager"
    assert G.report_scope(PEER, "e-emp", "e-mgr", "released") is None and G.report_scope(OTHER_MGR, "e-emp", "e-mgr", "released") is None
    assert G.report_scope(HR, "e-emp", "e-mgr", "collecting") == "hr"
    hr_subject = Actor(org="o", user_id="u-hr", role="hr_admin", employee_id="e-emp")
    assert G.report_scope(hr_subject, "e-emp", "e-mgr", "closed") is None  # HR being reviewed is just the subject
    with pytest.raises(RuleError):
        G.check_answers(G.questions_for(Q, "peer"), {"q1": 9})
    assert all(q.get("upward") for q in G.questions_for(Q, "direct_report"))


# ═════════════ pure: PIP + 1:1 ═════════════
def test_pip_visibility_is_employee_manager_hr_only():
    p = {"employee_id": "e-emp", "manager_id": "e-mgr", "status": "active"}
    assert G.pip_scope(EMP, p) == "self" and G.pip_scope(MGR, p) == "manager" and G.pip_scope(HRM, p) == "hr"
    for a in (PEER, OTHER_MGR, PAY):
        assert G.pip_scope(a, p) is None
    assert G.pip_scope(EMP, {**p, "status": "draft"}) is None             # a draft is not shown to the employee
    assert G.pip_can("start", "hr", {"status": "draft"}) and not G.pip_can("start", "manager", {"status": "draft"})
    assert G.pip_can("ack", "self", {"status": "active"}) and not G.pip_can("ack", "self", {"status": "active", "ack_status": "acknowledged"})
    assert G.pip_can("link_exit", "hr", {"status": "closed", "outcome": "exit"}) and not G.pip_can("link_exit", "hr", {"status": "closed", "outcome": "successful"})


def test_pip_outcomes_and_schedule():
    start, end = date(2026, 11, 1), date(2026, 12, 31)
    G.pip_dates(start, end)
    with pytest.raises(RuleError):
        G.pip_dates(start, date(2026, 11, 5))
    with pytest.raises(RuleError):
        G.pip_dates(start, date(2027, 6, 1))
    assert G.checkin_schedule(start, end, 14) == [date(2026, 11, 15), date(2026, 11, 29), date(2026, 12, 13), date(2026, 12, 27), end]
    p = {"start_date": start, "end_date": end}
    assert G.decide_outcome(p, "extended", "needs 30 more days", date(2027, 1, 30), []) == {"status": "extended", "end_date": date(2027, 1, 30)}
    with pytest.raises(RuleError):
        G.decide_outcome(p, "extended", "x", date(2026, 12, 1), [])
    with pytest.raises(RuleError):
        G.decide_outcome(p, "exit", "", None, [])
    with pytest.raises(RuleError):
        G.decide_outcome(p, "successful", "done", None, [{"kind": "goal", "status": "open"}])
    assert G.decide_outcome(p, "exit", "goals not met", None, [{"kind": "goal", "status": "not_met"}]) == {"status": "closed", "outcome": "exit"}
    assert G.next_pip_number(["PIP-2026-0007", "PIP-2025-0100"], 2026) == "PIP-2026-0008"


def test_one_on_one_private_notes_stay_private():
    m = {"manager_id": "e-mgr", "employee_id": "e-emp", "shared_notes": "both", "manager_private": "M only", "employee_private": "E only",
         "agenda": [{"id": "a", "text": "growth"}], "action_items": [{"id": "x", "text": "do", "done": False}]}
    assert G.redact_one(m, "manager")["my_private"] == "M only" and "employee_private" not in G.redact_one(m, "manager")
    assert G.redact_one(m, "employee")["my_private"] == "E only" and "manager_private" not in G.redact_one(m, "employee")
    h = G.redact_one(m, "hr")
    assert "shared_notes" not in h and h["agenda"] == [] and h["action_items"] == [] and h["counts"] == {"agenda": 1, "actions": 1, "actions_open": 1}
    assert G.one_scope(PEER, m) is None and G.one_scope(HR, m) == "hr"
    carried = G.carry_over([{"id": "1", "text": "a", "done": True}, {"id": "2", "text": "b", "done": False}], "m1")
    assert carried == [{"id": "2", "text": "b", "done": False, "carried_from": "m1"}]


# ═════════════ routes (SQLite) ═════════════
ORG = uuid.uuid4()
E = {k: uuid.uuid4() for k in ("hr", "mgr", "emp", "p1", "p2", "p3", "r1", "stranger", "m2")}


class Req:
    def __init__(self, who="hr", role=None):
        roles = {"hr": "hr_admin", "mgr": "manager", "m2": "manager"}
        self.headers = {"x-org-id": str(ORG), "x-role": role or roles.get(who, "employee"), "x-user-id": f"u-{who}",
                        "x-user-email": f"{who}@acme.ca", "x-employee-id": str(E[who])}


@pytest.fixture
def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [Employee.__table__, PerfGoalCycle.__table__] + [t for n, t in M.Base.metadata.tables.items()
                                                              if n.startswith(("perf_okr_", "perf_fb_", "perf_pip_", "perf_one_"))]

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Employee.metadata.create_all(c, tables=tables))
    asyncio.run(make())

    async def _no(*a, **kw):
        return True

    async def _users(db, org, ids):
        return [str(i) for i in ids if i]
    import hiring_integrations as ext

    async def _actor(request, db):
        # same as hiring_common.get_actor, but the org id is a UUID object (SQLite's UUID binder needs one; Postgres takes either)
        h = request.headers
        a = Actor(org=uuid.UUID(h["x-org-id"]), user_id=h.get("x-user-id") or "", role=h.get("x-role") or "",
                  employee_id=h.get("x-employee-id") or "", email=h.get("x-user-email") or "")
        e = await db.get(Employee, uuid.UUID(a.employee_id)) if a.employee_id else None
        a.name = f"{e.first_name} {e.last_name}" if e else a.email
        return a
    for mod in (OK, FB, PR, ON):
        monkeypatch.setattr(mod, "get_actor", _actor)
        if hasattr(mod, "record_audit"):
            monkeypatch.setattr(mod, "record_audit", _no)
    monkeypatch.setattr(ext, "notify", _no)
    monkeypatch.setattr(ext, "user_ids_for_employees", _users)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        async with sm() as db:
            def emp(k, n, mgr=None):
                return Employee(id=E[k], org_id=ORG, employee_number=n, first_name=k.capitalize(), last_name="Test", email=f"{k}@acme.ca",
                                status="active", employment_type="full_time", address_country="CA", job_title="Analyst", department="Ops",
                                hire_date=date(2025, 1, 1), pay_type="salary", annual_salary=600000, pay_frequency="monthly", is_active=True,
                                province_of_employment="ON", standard_hours_per_week=40, manager_id=E[mgr] if mgr else None)
            db.add_all([emp("hr", "K1"), emp("mgr", "K2", "hr"), emp("emp", "K3", "mgr"), emp("p1", "K4", "mgr"), emp("p2", "K5", "mgr"),
                        emp("p3", "K6", "mgr"), emp("r1", "K7", "emp"), emp("stranger", "K8", "m2"), emp("m2", "K9", "hr")])
            db.add(PerfGoalCycle(id=uuid.uuid4(), org_id=ORG, name="Q3 FY26-27", period_start=date(2026, 10, 1), period_end=date(2026, 12, 31), status="open"))
            await db.commit()
    asyncio.run(seed())
    return sm


def run(c):
    return asyncio.run(c)


async def _denied(coro, code=None):
    with pytest.raises(HTTPException) as ex:
        await coro
    if code:
        assert ex.value.status_code == code, ex.value.detail
    return ex.value


def test_okr_routes_rollup_checkin_and_visibility(world):
    async def go():
        async with world() as db:
            lst = await OK.list_okrs(Req("hr"), db=db)
            cyc = lst["cycle_id"]
            comp = await OK.create_okr({"cycle_id": cyc, "level": "company", "title": "Grow revenue 30%"}, Req("hr"), db)
            mine = await OK.create_okr({"cycle_id": cyc, "level": "individual", "title": "Close 10 deals", "parent_id": comp["id"], "weight": 40,
                                        "key_results": [{"title": "Deals", "metric_type": "number", "start_value": 0, "target_value": 10},
                                                        {"title": "Demo kit", "metric_type": "milestone", "milestones": [{"label": "deck"}, {"label": "video"}]}]},
                                       Req("emp"), db)
            assert mine["owner_name"] == "Emp Test" and len(mine["key_results"]) == 2 and mine["progress_pct"] == 0
            kr = next(k for k in mine["key_results"] if k["metric_type"] == "number")
            ck = await OK.add_checkin(mine["id"], {"key_result_id": kr["id"], "value": 5, "confidence": 6, "comment": "half way"}, Req("emp"), db)
            assert ck["objective_progress"] == 25.0 and ck["confidence_band"] == "at_risk"     # (50 + 0) / 2
            ms = next(k for k in mine["key_results"] if k["metric_type"] == "milestone")
            await OK.add_checkin(mine["id"], {"key_result_id": ms["id"], "done_milestones": ["deck", "video"]}, Req("mgr"), db)
            view = await OK.list_okrs(Req("hr"), cycle_id=cyc, db=db)
            by = {o["id"]: o for o in view["objectives"]}
            assert by[mine["id"]]["progress_pct"] == 75.0 and by[comp["id"]]["progress_pct"] == 75.0   # rolls up the tree
            # a colleague sees the company objective but not the individual one
            peer = await OK.list_okrs(Req("p1"), cycle_id=cyc, db=db)
            assert [o["id"] for o in peer["objectives"]] == [comp["id"]]
            await _denied(OK.add_checkin(mine["id"], {"confidence": 9}, Req("p1"), db), 404)
            await _denied(OK.add_checkin(mine["id"], {"confidence": 9}, Req("stranger"), db), 404)
            await _denied(OK.create_okr({"cycle_id": cyc, "level": "company", "title": "x"}, Req("mgr"), db), 403)
            await _denied(OK.update_okr(comp["id"], {"parent_id": mine["id"]}, Req("hr"), db), 422)                  # loop
            rows = await OK.review_rows(db, ORG, uuid.UUID(cyc))
            assert rows[str(E["emp"])][0]["id"] == f"okr:{mine['id']}" and rows[str(E["emp"])][0]["progress_pct"] == 75.0
    run(go())


def test_360_round_nominate_answer_and_anonymous_report(world):
    async def go():
        async with world() as db:
            r = await FB.create_round({"name": "H2 360", "min_responses": 3}, Req("hr"), db)
            await _denied(FB.create_round({"name": "x", "min_responses": 2}, Req("hr"), db), 422)
            await _denied(FB.create_round({"name": "x"}, Req("mgr"), db), 403)
            await FB.add_subjects(r["id"], {"employee_ids": [str(E["emp"])]}, Req("hr"), db)
            await FB.advance_round(r["id"], {"action": "open_nominations"}, Req("hr"), db)
            det = await FB.get_round(r["id"], Req("emp"), db)
            sid = det["subjects"][0]["id"]
            assert det["subjects"][0]["scope"] == "self"
            rels = {n["relationship"] for n in (await FB.get_round(r["id"], Req("hr"), db))["subjects"][0]["nominations"]}
            assert rels == {"self", "manager", "direct_report"}                                   # auto: self, manager, upward
            noms = [await FB.nominate(sid, {"reviewer_employee_id": str(E[p]), "relationship": "peer"}, Req("emp"), db) for p in ("p1", "p2", "p3")]
            assert all(n["status"] == "proposed" for n in noms)
            await _denied(FB.decide_nomination(noms[0]["id"], {"approve": True}, Req("emp"), db), 403)   # not your own
            await _denied(FB.decide_nomination(noms[0]["id"], {"approve": True}, Req("m2"), db), 403)    # not the manager
            for n in noms:
                await FB.decide_nomination(n["id"], {"approve": True}, Req("mgr"), db)
            await FB.advance_round(r["id"], {"action": "start_collecting"}, Req("hr"), db)
            for who, v in (("p1", 4), ("p2", 4), ("p3", 2), ("mgr", 3), ("r1", 1)):
                req = await FB.my_requests(Req(who), db)
                item = next(i for i in req["items"] if i["subject_name"] == "Emp Test")
                answers = {q["key"]: v for q in item["questions"] if q["kind"] == "rating"}
                await FB.submit_feedback(item["id"], {"answers": {**answers, "q6": f"note from {who}"}}, Req(who), db)
            await _denied(FB.report(sid, Req("emp"), db), 404)                                          # not released yet
            await _denied(FB.report(sid, Req("p1"), db), 404)
            await FB.advance_round(r["id"], {"action": "close"}, Req("hr"), db)
            mrep = await FB.report(sid, Req("mgr"), db)
            assert {g["group"] for g in mrep["groups"]} == {"manager", "peer"} and mrep["hidden_responses"] == 1   # 1 upward answer hidden
            assert "note from r1" not in str(mrep) and "p1" not in str(mrep["groups"])
            await FB.advance_round(r["id"], {"action": "release"}, Req("hr"), db)
            mine = await FB.report(sid, Req("emp"), db)
            assert mine["scope"] == "self" and mine["groups"][-1]["responses"] == 3
            subj = (await FB.get_round(r["id"], Req("emp"), db))["subjects"][0]
            assert all(n["status"] != "submitted" for n in subj["nominations"])                   # who answered stays hidden
    run(go())


def test_pip_confidential_flow_and_exit_is_manual(world):
    async def go():
        async with world() as db:
            body = {"employee_id": str(E["emp"]), "reason": "Missed 4 of 6 delivery dates in Q2 (tickets listed).", "start_date": "2026-11-01",
                    "end_date": "2026-12-31", "items": [{"kind": "goal", "title": "Deliver sprint items on time", "measure": "9 of 10"},
                                                        {"kind": "support", "title": "Weekly mentoring with Lead", "owner": "Mgr"}]}
            await _denied(PR.create_pip(body, Req("m2"), db), 403)                                       # not their report
            await _denied(PR.create_pip({**body, "employee_id": str(E["mgr"])}, Req("mgr"), db), 403)   # not for yourself
            p = await PR.create_pip(body, Req("mgr"), db)
            assert p["status"] == "draft" and p["plan_number"].startswith("PIP-")
            await _denied(PR.get_pip(p["id"], Req("emp"), db), 404)                                      # draft hidden from employee
            await _denied(PR.start_pip(p["id"], Req("mgr"), db), 403)                                    # HR starts
            st = await PR.start_pip(p["id"], Req("hr"), db)
            assert st["status"] == "active" and len(st["checkins"]) == 5
            for who in ("p1", "stranger", "m2"):
                await _denied(PR.get_pip(p["id"], Req(who), db), 404)
            lst = await PR.list_pips(Req("p1"), db)
            assert lst["items"] == []
            seen = await PR.get_pip(p["id"], Req("emp"), db)
            assert "audit" not in seen and seen["can"]["ack"] is True
            await PR.acknowledge(p["id"], {"status": "acknowledged"}, Req("emp"), db)
            ck = seen["checkins"][0]
            await PR.record_checkin(p["id"], ck["id"], {"rating": "some_progress", "notes": "2 of 3 on time"}, Req("mgr"), db)
            await _denied(PR.outcome(p["id"], {"outcome": "exit", "note": "not met"}, Req("hr"), db), 409)   # goals still open
            goal = next(i for i in seen["items"] if i["kind"] == "goal")
            await PR.update_item(goal["id"], {"status": "not_met"}, Req("mgr"), db)
            await _denied(PR.outcome(p["id"], {"outcome": "exit", "note": "not met"}, Req("mgr"), db), 403)  # HR decides
            res = await PR.outcome(p["id"], {"outcome": "exit", "note": "Goals not met after support."}, Req("hr"), db)
            assert res["status"] == "closed" and res["outcome"] == "exit" and "does NOT end automatically" in res["next_step"]
            emp = await db.get(Employee, E["emp"])
            assert emp.status == "active" and emp.is_active is True                                # employment untouched
            linked = await PR.exit_link(p["id"], {"exit_case_id": str(uuid.uuid4())}, Req("hr"), db)
            assert linked["exit_case_id"] and [a["action"] for a in linked["audit"]][-1] == "exit.link"
    run(go())


def test_payroll_admin_cannot_read_a_plan(world):
    async def go():
        async with world() as db:
            p = await PR.create_pip({"employee_id": str(E["p1"]), "reason": "facts", "start_date": "2026-11-01", "end_date": "2026-12-15"}, Req("hr"), db)
            pay = Req("stranger", role="payroll_admin")
            await _denied(PR.get_pip(p["id"], pay, db), 404)
            assert (await PR.list_pips(pay, db))["items"] == []
    run(go())


def test_one_on_one_routes(world):
    async def go():
        async with world() as db:
            when = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).isoformat()
            m = await ON.create_one({"employee_id": str(E["emp"]), "scheduled_at": when, "repeat_days": 14,
                                     "agenda": [{"text": "Project status"}]}, Req("mgr"), db)
            assert m["scope"] == "manager" and m["agenda"][0]["by"] == "manager"
            await _denied(ON.create_one({"employee_id": str(E["stranger"]), "scheduled_at": when}, Req("mgr"), db), 403)
            await ON.update_one(m["id"], {"my_private": "watch workload", "shared_notes": "agreed priorities",
                                          "action_items": [{"text": "Send plan", "owner": "employee"}, {"text": "Book course", "owner": "manager", "done": True}]},
                                Req("mgr"), db)
            await ON.update_one(m["id"], {"my_private": "ask about growth"}, Req("emp"), db)
            e_view = await ON.get_one(m["id"], Req("emp"), db)
            assert e_view["my_private"] == "ask about growth" and "watch workload" not in str(e_view)
            m_view = await ON.get_one(m["id"], Req("mgr"), db)
            assert m_view["my_private"] == "watch workload" and "ask about growth" not in str(m_view)
            hr_view = await ON.get_one(m["id"], Req("hr"), db)
            assert "agreed priorities" not in str(hr_view) and hr_view["counts"]["actions"] == 2
            await _denied(ON.update_one(m["id"], {"shared_notes": "x"}, Req("hr"), db), 403)
            await _denied(ON.get_one(m["id"], Req("p1"), db), 404)
            done = await ON.complete_one(m["id"], Req("emp"), db)
            assert done["next"]["action_items"][0]["text"] == "Send plan" and len(done["next"]["action_items"]) == 1
    run(go())
