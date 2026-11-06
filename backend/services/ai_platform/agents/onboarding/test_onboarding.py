"""Onboarding Agent tests (Canada): checklist checks, the CRA 3-day SIN clock, work-permit expiry, TD1, shadow opens
nothing, live opens deduplicated tasks to the right owner, fixed items resolve, appointment-letter draft is tier 1 and the
executor asks for a DRAFT only.

Run:  cd backend/services/ai_platform && python -m pytest -q agents/onboarding
"""
from datetime import date

import pytest

from agents.lifecycle_testkit import assert_fits_columns, CEO, E1, E2, HR, LOGINS, MGR, ORG, db, go_live, run, tasks  # noqa: F401

import agentic_proposals as PR  # noqa: E402
import agentic_registry as REG  # noqa: E402
from agents import lifecycle_common as LC  # noqa: E402
from agents.onboarding import checks as CK  # noqa: E402
from agents.onboarding import executors as EX  # noqa: E402
from agents.onboarding import run as RN  # noqa: E402

TODAY = date(2026, 10, 26)


def case(eid=E1, **over):
    fields = {"sin": True, "sin_valid": None, "bank_account": True, "bank_institution": "004", "bank_transit": "12345", "address": True,
              "td1_federal": True, "td1_provincial": True, "temporary_resident": False, "work_permit_expiry": None, "province": "ON",
              "hire_date": date(2026, 10, 20), "annual_salary": 72000.0}
    fields.update(over.pop("fields", {}))
    c = {"case_key": f"emp:{eid}", "kind": "employee", "status": "active", "session_id": None,
         "employee": {"id": eid, "employee_number": "MAP100", "name": "Chloe Tremblay", "manager_id": MGR, "gender": "F"},
         "fields": fields, "docs": {"types": ["photo_id", "education", "void_cheque"], "unverified": []}, "consents": ["privacy_pipeda"],
         "tasks": [], "letter": {"status": "issued"}, "assets": 1, "lms": 1}
    c.update(over)
    return c


def codes(items, sev=None):
    return sorted({x["code"] for x in items if sev is None or x["severity"] == sev})


def test_complete_joiner_has_nothing_to_do():
    assert CK.evaluate(case(), TODAY) == []


def test_payroll_blocking_fields():
    c = case(fields={"bank_account": False, "hire_date": None}, docs={"types": [], "unverified": []})
    items = CK.evaluate(c, TODAY)
    assert {"OB-04", "OB-14"} <= set(codes(items, "blocking"))
    assert next(x for x in items if x["code"] == "OB-04")["owner_role"] == "employee"
    assert next(x for x in items if x["code"] == "OB-14")["owner_role"] == "hr"
    assert "OB-01" in codes(items, "warning")                                       # no photo ID in the vault


def test_bank_number_shapes_and_void_cheque():
    assert "OB-04" in codes(CK.evaluate(case(fields={"bank_transit": "1234"}), TODAY), "blocking")
    assert "OB-04" in codes(CK.evaluate(case(fields={"bank_institution": "4"}), TODAY), "blocking")
    info = [x for x in CK.evaluate(case(docs={"types": ["photo_id", "education"], "unverified": []}), TODAY) if x["code"] == "OB-04"]
    assert info and info[0]["severity"] == "info" and not info[0]["chase"] and "void cheque" in info[0]["title"]


def test_sin_three_day_clock():
    ok = next(x for x in CK.evaluate(case(fields={"sin": False, "hire_date": date(2026, 10, 26)}), TODAY) if x["code"] == "OB-05")
    assert ok["clock"]["due"] == "2026-10-29" and ok["clock"]["verified"] is True and ok["severity"] == "warning"
    assert ok["owner_role"] == "employee"
    late = next(x for x in CK.evaluate(case(fields={"sin": False, "hire_date": date(2026, 10, 20)}), TODAY) if x["code"] == "OB-05")
    assert late["clock"]["status"] == "overdue" and late["severity"] == "blocking"


def test_work_permit_and_td1():
    no_exp = next(x for x in CK.evaluate(case(fields={"temporary_resident": True}), TODAY) if x["code"] == "OB-06")
    assert no_exp["severity"] == "blocking" and no_exp["owner_role"] == "hr"
    soon = next(x for x in CK.evaluate(case(fields={"temporary_resident": True, "work_permit_expiry": date(2026, 12, 1)}), TODAY) if x["code"] == "OB-06")
    assert soon["severity"] == "warning" and "36 day" in soon["title"]
    gone = next(x for x in CK.evaluate(case(fields={"work_permit_expiry": date(2026, 10, 1)}), TODAY) if x["code"] == "OB-06")
    assert gone["severity"] == "blocking"
    td1 = [x for x in CK.evaluate(case(fields={"td1_federal": False, "td1_provincial": False}), TODAY) if x["code"] == "OB-07"]
    assert {x["severity"] for x in td1} == {"warning", "info"} and any("ON" in x["title"] for x in td1)


def test_offer_accepted_without_onboarding_and_tasks():
    off = {"case_key": "offer:o1", "kind": "offer", "offer": {"id": "o1", "designation": "Analyst", "joining_date": date(2026, 11, 2)}, "employee": {"id": None, "name": "Analyst"}}
    items = CK.evaluate(off, TODAY)
    assert codes(items) == ["OB-00"] and items[0]["owner_role"] == "hr"
    c = case(tasks=[{"id": "t1", "title": "Laptop / phone issued", "owner": "IT", "due_date": "2026-10-27", "done": False},
                    {"id": "t2", "title": "ID card issued", "owner": "Admin", "due_date": "2026-12-30", "done": False}])
    ob12 = [x for x in CK.evaluate(c, TODAY) if x["code"] == "OB-12"]
    assert [x["owner_role"] for x in ob12] == ["it"]                   # due within 3 days only


def test_shadow_run_opens_nothing():
    c = case(fields={"sin": False}, letter=None)
    out = run(RN.run_onboarding(ORG, trigger="manual", cases=[c], today=TODAY, logins=LOGINS, requested_by="u-hr"))
    assert out["mode"] == "shadow" and out["chase"]["opened"] == 0 and out["chase"]["would_open"]
    assert tasks("onboarding") == []
    props = run(PR.list_proposals(ORG, agent="onboarding"))
    assert props and all(p["status"] == "shadow" for p in props)
    assert any(p["tool_name"] == "onboarding.draft_appointment_letter" for p in props)


def test_live_opens_deduped_tasks_to_the_right_owner_and_resolves(monkeypatch):
    go_live()
    c = case(fields={"sin": False, "temporary_resident": True})                # OB-05 → joiner, OB-06 → HR
    run(RN.run_onboarding(ORG, trigger="manual", cases=[c], today=TODAY, logins=LOGINS, requested_by="u-hr"))
    t = tasks("onboarding")
    owners = {x["kind"]: x["owner_user_id"] for x in t}
    assert owners == {"ob_05": "u-e1", "ob_06": "u-hr"}
    # re-run → same tasks (dedupe), nothing new
    run(RN.run_onboarding(ORG, trigger="manual", cases=[c], today=TODAY, logins=LOGINS))
    assert len(tasks("onboarding")) == 2
    # the joiner adds the SIN, HR records the permit expiry → items resolve, tasks close
    fixed = case()
    run(RN.run_onboarding(ORG, trigger="manual", cases=[fixed], today=TODAY, logins=LOGINS))
    st = {x["kind"]: x["status"] for x in tasks("onboarding")}
    assert st == {"ob_05": "done", "ob_06": "done"}
    assert_fits_columns()                                                       # Postgres VARCHAR limits
    assert all(i["code"] not in ("OB-05", "OB-06") for i in run(LC.list_items(ORG, "onboarding")))


def test_appointment_letter_is_tier1_and_only_a_draft(monkeypatch):
    go_live()
    posted = []

    async def fake_post(url, org, body):
        posted.append((url, body))
        return {"letter_id": "L1", "status": "draft", "number": None, "missing_fields": []}
    monkeypatch.setattr(EX, "_post", fake_post)
    run(RN.run_onboarding(ORG, trigger="manual", cases=[case(letter=None)], today=TODAY, logins=LOGINS, requested_by="u-hr"))
    p = next(p for p in run(PR.list_proposals(ORG, status="inbox")) if p["tool_name"] == "onboarding.draft_appointment_letter")
    assert p["risk_tier"] == 1 and posted == []                                   # nothing happens before HR approves
    done = run(PR.approve(ORG, p["id"], HR))
    assert done["status"] == "executed" and done["result"]["detail"]["status"] == "draft"
    url, body = posted[0]
    assert url.endswith(f"/employees/{E1}/letters") and body["issue"] is False and body["template_key"] == "appointment_letter"
    # proposed once per joiner
    run(RN.run_onboarding(ORG, trigger="manual", cases=[case(letter=None)], today=TODAY, logins=LOGINS))
    assert sum(1 for x in run(PR.list_proposals(ORG, agent="onboarding")) if x["tool_name"] == "onboarding.draft_appointment_letter") == 1


def test_joiner_seen_in_shadow_gets_the_real_letter_card_after_going_live():
    """The shadow proposal used to count as 'already proposed', so after the org went live the joiner never got the
    appointment-letter approval card. Shadow runs still de-duplicate among themselves."""
    run(RN.run_onboarding(ORG, trigger="manual", cases=[case(letter=None)], today=TODAY, logins=LOGINS, requested_by="u-hr"))
    run(RN.run_onboarding(ORG, trigger="manual", cases=[case(letter=None)], today=TODAY, logins=LOGINS))
    letter = lambda: [x for x in run(PR.list_proposals(ORG, agent="onboarding")) if x["tool_name"] == "onboarding.draft_appointment_letter"]  # noqa: E731
    assert [x["status"] for x in letter()] == ["shadow"]                          # one shadow row for two shadow runs
    go_live()
    run(RN.run_onboarding(ORG, trigger="manual", cases=[case(letter=None)], today=TODAY, logins=LOGINS))
    live = [x for x in letter() if x["status"] != "shadow"]
    assert len(live) == 1 and live[0]["risk_tier"] == 1 and live[0]["status"] == "proposed"
    run(RN.run_onboarding(ORG, trigger="manual", cases=[case(letter=None)], today=TODAY, logins=LOGINS))
    assert len([x for x in letter() if x["status"] != "shadow"]) == 1               # and once only in live


def test_tier_map_and_no_issue_tool():
    spec = REG.REGISTRY["onboarding"]
    assert spec.tier_map == {"onboarding.chase": 0, "onboarding.draft_appointment_letter": 1}
    assert set(EX.TOOLS) == set(spec.tier_map) and all(t in REG.EXECUTORS for t in spec.tier_map)
    assert not any("issue" in t for t in spec.tier_map)


def test_settings_round_trip_refuses_credentials():
    saved = run(LC.save_agent_settings(ORG, "onboarding", {"lookback_days": 60, "owners": {"it": "u-it"}}, CK.DEFAULTS, actor_id="u-ceo", actor_email="ceo@x.ca"))
    assert saved["lookback_days"] == 60 and saved["owners"]["it"] == "u-it"
    with pytest.raises(ValueError):
        LC.clean_settings({"lookback_days": -1}, {}, CK.DEFAULTS)
    # an IT owner from settings receives the IT task
    go_live()
    c = case(tasks=[{"id": "t1", "title": "E-mail and system access created", "owner": "IT", "due_date": "2026-10-26", "done": False}])
    run(RN.run_onboarding(ORG, trigger="manual", cases=[c], today=TODAY, logins=LOGINS))
    assert [x["owner_user_id"] for x in tasks("onboarding") if x["kind"] == "ob_12"] == ["u-it"]
    assert CEO and E2                                                               # fixtures imported for symmetry


def test_routes_guard_and_overview(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agents.onboarding.router import router
    from agents.channels_router import router as ch
    app = FastAPI()
    app.include_router(router)
    app.include_router(ch)
    c = TestClient(app)
    h = {"x-org-id": ORG, "x-user-id": "u-hr", "x-role": "hr_admin"}
    assert c.get("/ai/agents/onboarding/overview", headers={**h, "x-role": "employee"}).status_code == 403
    assert c.get("/ai/agents/onboarding/overview", headers={**h, "x-role": "manager"}).status_code == 403
    r = c.get("/ai/agents/onboarding/overview", headers=h)
    assert r.status_code == 200 and r.json()["role_name"] == "Onboarding Agent" and r.json()["state"]["mode"] == "shadow"
    assert c.put("/ai/agents/onboarding/settings", headers=h, json={"value": {"lookback_days": 30}}).status_code == 403   # org admin only
    assert c.put("/ai/agents/onboarding/settings", headers={**h, "x-role": "org_admin"}, json={"value": {"lookback_days": 30}}).json()["value"]["lookback_days"] == 30
    wa = c.get("/ai/agents/channels/whatsapp", headers=h).json()
    assert wa["value"]["enabled"] is False and wa["readiness"]["can_send"] is False and wa["can_edit"] is False
    bad = c.put("/ai/agents/channels/whatsapp", headers={**h, "x-role": "org_admin"}, json={"value": {"token_env": "EAAGsecret-token-value"}})
    assert bad.status_code == 422
