"""Exit Agent tests (Canada): the Ontario final-pay clock (verified) and the estimate elsewhere, the ROE 5-day clock from the
pay period of the last day, clearance chased to the right owner, shadow opens nothing, the final pay is NEVER computed by the
agent (the exit case's own pre-fill is passed through unchanged to the payroll final-pay run, which needs two DIFFERENT
approvers), letters drafted at tier 1.

Run:  cd backend/services/ai_platform && python -m pytest -q agents/exit
"""
import inspect
from datetime import date

import pytest

from agents.lifecycle_testkit import assert_fits_columns, CEO, E1, HR, LOGINS, MGR, ORG, PAY, db, go_live, run, tasks  # noqa: F401

import agentic_proposals as PR  # noqa: E402
import agentic_registry as REG  # noqa: E402
from agents import pay_calendar as PC  # noqa: E402
from agents.exit import checks as CK  # noqa: E402
from agents.exit import executors as EX  # noqa: E402
from agents.exit import run as RN  # noqa: E402

CID = "22222222-0000-0000-0000-000000000001"
RUN_ID = "33333333-0000-0000-0000-000000000001"


def case(**over):
    c = {"id": CID, "case_number": "EX-0007", "employee_id": E1, "country": "CA", "exit_type": "resignation", "status": "clearance",
         "submitted_on": date(2026, 10, 1), "notice_days": 14, "notice_source": "policy", "notice_served_days": 14, "notice_shortfall_days": 0,
         "notice_treatment": "none", "requested_lwd": "2026-10-30", "expected_lwd": date(2026, 10, 30), "lwd": date(2026, 10, 30),
         "province": "ON", "pay_period_end": date(2026, 10, 31), "next_pay_day": date(2026, 11, 6), "roe_reason_code": "E",
         "settlement_status": "none", "employee": {"id": E1, "employee_number": "MAP001", "name": "Liam Wong", "manager_id": MGR},
         "clearance": [{"id": "c-it", "department": "IT", "item": "Laptop returned", "owner_role": "hr_admin", "owner_user_id": None, "status": "pending"},
                       {"id": "c-mgr", "department": "Reporting manager", "item": "Handover", "owner_role": "manager", "owner_user_id": None, "status": "pending"},
                       {"id": "c-fin", "department": "Finance", "item": "Advance settled", "owner_role": "payroll_admin", "owner_user_id": "u-fin", "status": "cleared"}],
         "letter_rows": [], "case_letters": []}
    c.update(over)
    return c


def cleared(**over):
    items = [{**i, "status": "cleared"} for i in case()["clearance"]]
    return case(clearance=items, **over)


def test_ontario_final_pay_clock_is_the_later_of_7_days_or_next_pay_day():
    r = CK.evaluate(cleared(), date(2026, 10, 30))
    clk = r["clock"]
    assert clk["due"] == "2026-11-06" and clk["verified"] is True and clk["rule_key"] == "esa.on.final_pay"   # 30 Oct + 7 = 6 Nov = next pay day
    early_pay = CK.evaluate(cleared(next_pay_day=date(2026, 11, 2)), date(2026, 10, 30))["clock"]
    assert early_pay["due"] == "2026-11-06"                                                            # 7 days wins over an earlier pay day
    late_pay = CK.evaluate(cleared(next_pay_day=date(2026, 11, 13)), date(2026, 10, 30))["clock"]
    assert late_pay["due"] == "2026-11-13"
    ex3 = next(x for x in r["items"] if x["code"] == "EX-03")
    assert ex3["owner_role"] == "payroll" and "s.11(5)" in ex3["what"]
    late = CK.evaluate(cleared(), date(2026, 11, 7))["items"]
    assert next(x for x in late if x["code"] == "EX-03")["severity"] == "blocking"


def test_other_provinces_are_an_estimate_with_a_badge():
    clk = CK.evaluate(cleared(province="BC"), date(2026, 10, 30))["clock"]
    assert clk["verified"] is False and clk["due"] == "2026-11-06" and "confirm" in clk["badge"]
    ex3 = next(x for x in CK.evaluate(cleared(province="BC"), date(2026, 10, 30))["items"] if x["code"] == "EX-03")
    assert "estimate" in ex3["what"]


def test_roe_clock_is_five_days_after_the_pay_period_end():
    r = CK.evaluate(cleared(settlement_status="paid", settlement_paid_at="2026-11-04"), date(2026, 11, 2))
    roe = next(x for x in r["items"] if x["code"] == "EX-05")
    assert roe["clock"]["due"] == "2026-11-05" and roe["clock"]["verified"] is True and roe["owner_role"] == "payroll"
    assert roe["severity"] in ("warning", "blocking") and "ROE Web" in roe["title"]
    # before the final pay the ROE item is shown but not chased
    pre = next(x for x in CK.evaluate(cleared(), date(2026, 11, 2))["items"] if x["code"] == "EX-05")
    assert pre["severity"] == "info"


def test_pay_calendar_period_containing():
    sched = {"anchor_start": date(2026, 10, 4), "length_days": 14, "pay_lag_days": 6}
    assert PC.period_containing(sched, date(2026, 10, 30)) == (date(2026, 10, 31), date(2026, 11, 6))
    assert PC.period_containing(sched, date(2026, 10, 17)) == (date(2026, 10, 17), date(2026, 10, 23))
    assert PC.period_containing(None, date(2026, 10, 30)) == (None, None)


def test_items_of_a_case_that_left_the_population_resolve_on_a_full_scan():
    """A withdrawn / closed exit case drops out of the open population; its items used to stay open (and chased)
    forever. A full scan resolves them; a fixture run or an EMPTY read (likely a failed query) does not."""
    from agents import lifecycle_common as LC
    r = CK.evaluate(case(), date(2026, 10, 28))
    run(LC.store_items(ORG, "exit", "run-1", r["items"], scanned_cases={f"case:{CID}"}))
    open_ = lambda: [i for i in run(LC.list_items(ORG, "exit")) if i["case_key"] == f"case:{CID}"]  # noqa: E731
    n = len(open_())
    assert n >= 2
    run(LC.store_items(ORG, "exit", "run-2", [], scanned_cases={"case:other"}))                    # fixture-style: untouched
    assert len(open_()) == n
    run(LC.store_items(ORG, "exit", "run-3", [], scanned_cases=set(), full_scan=True))             # empty read: untouched
    assert len(open_()) == n
    run(LC.store_items(ORG, "exit", "run-4", [], scanned_cases={"case:other"}, full_scan=True))    # case gone → resolved
    assert open_() == []
    assert {i["status"] for i in run(LC.list_items(ORG, "exit", status="resolved")) if i["case_key"] == f"case:{CID}"} == {"resolved"}


def test_final_pay_never_computed_by_the_agent():
    r = CK.evaluate(cleared(), date(2026, 10, 30))
    act = next(a for a in r["actions"] if a["tool"] == "exit.run_final_pay")
    assert set(act["args"]) == {"case_id", "employee_id", "last_day"}            # no amount in what the agent sends
    src = inspect.getsource(CK) + inspect.getsource(EX)
    for word in ("vacation_pay =", "net_pay =", "* 0.04", "gross ="):
        assert word not in src
    # pending clearance → no final-pay proposal yet (recoveries are entered on the clearance)
    assert not [a for a in CK.evaluate(case(), date(2026, 10, 30))["actions"] if a["tool"] == "exit.run_final_pay"]


def test_run_final_pay_passes_the_case_prefill_unchanged_and_needs_two_approvers(monkeypatch):
    go_live()
    prefill = {"employee_id": E1, "last_day": "2026-10-30", "reason": "E", "extra_earnings": [],
               "extra_deductions": [{"code": "RECOVERY", "amount": 150.0}], "case_id": CID}
    calls = []

    class _R:
        def __init__(self, code, data):
            self.status_code, self._d, self.content = code, data, b"x"

        def json(self):
            return self._d

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append(("GET", url, None))
            return _R(200, {"case": {"status": "settlement"}, "ready": True, "prefill": {"kind": "canada_final", "body": prefill}})

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url, json))
            if url.endswith("/payroll/final-pay"):
                return _R(201, {"final_pay": {"id": RUN_ID, "net_pay": 2310.44}, "vacation_paid": 812.5, "roe": {"blocks": {}}})
            return _R(200, {"ok": True})
    monkeypatch.setattr(EX.httpx, "AsyncClient", _C)
    run(RN.run_exit(ORG, trigger="manual", cases=[cleared()], today=date(2026, 10, 30), logins=LOGINS, requested_by="u-hr"))
    p = next(p for p in run(PR.list_proposals(ORG, status="inbox")) if p["tool_name"] == "exit.run_final_pay")
    assert p["risk_tier"] == 2 and calls == []
    first = run(PR.approve(ORG, p["id"], CEO))
    assert first["status"] == "second_approval" and calls == []
    with pytest.raises(PR.ProposalError):
        run(PR.approve(ORG, p["id"], CEO))
    done = run(PR.approve(ORG, p["id"], PAY))
    assert done["status"] == "executed"
    fp = next(c for c in calls if c[0] == "POST" and c[1].endswith("/payroll/final-pay"))
    assert fp[2] == {**prefill, "year": 2026}                                        # passed through; only the tax year added
    det = done["result"]["detail"]
    assert det["net_pay_engine"] == 2310.44 and det["roe_ready"] is True and "payroll service" in det["computed_by"]


def test_clearance_chased_to_owner_shadow_then_live():
    out = run(RN.run_exit(ORG, trigger="manual", cases=[case()], today=date(2026, 10, 28), logins=LOGINS, requested_by="u-hr"))
    assert out["chase"]["opened"] == 0 and len(out["chase"]["would_open"]) == 2 and tasks("exit") == []
    go_live()
    run(RN.run_exit(ORG, trigger="manual", cases=[case()], today=date(2026, 10, 28), logins=LOGINS))
    by = {t["title"]: t["owner_user_id"] for t in tasks("exit")}
    assert by["Sign the Reporting manager clearance: Handover"] == "u-mgr"        # the employee's manager
    assert by["Sign the IT clearance: Laptop returned"] == "u-hr"                 # IT owner not named → HR
    run(RN.run_exit(ORG, trigger="manual", cases=[case()], today=date(2026, 10, 28), logins=LOGINS))
    assert len(tasks("exit")) == 2                                                 # dedupe
    run(RN.run_exit(ORG, trigger="manual", cases=[cleared()], today=date(2026, 10, 28), logins=LOGINS))
    st = {t["kind"]: (t["status"], t["owner_user_id"]) for t in tasks("exit")}
    assert st["ex_02"][0] == "done"                                                # signed → clearance tasks closed
    assert st["ex_03"] == ("open", "u-pay")                                        # … and the final-pay clock now chases payroll
    assert all((t.get("detail") or {}).get("case_key") == f"case:{CID}" and t.get("run_ref") is None for t in tasks("exit"))
    assert_fits_columns()                                                          # Postgres VARCHAR limits


def test_letters_drafted_tier1_and_review_reminder():
    r = CK.evaluate(cleared(status="settlement"), date(2026, 10, 31))
    act = next(a for a in r["actions"] if a["tool"] == "exit.draft_exit_letters")
    assert act["args"]["keys"] == ["relieving", "experience_certificate"] and "separation letter" in act["plain_action"]
    r2 = CK.evaluate(cleared(status="settled", letter_rows=[{"template_key": "relieving", "status": "draft"},
                                                            {"template_key": "experience_certificate", "status": "issued"}]), date(2026, 11, 5))
    assert not [a for a in r2["actions"] if a["tool"] == "exit.draft_exit_letters"]
    ex4 = [x for x in r2["items"] if x["code"] == "EX-04"]
    assert ex4 and ex4[0]["severity"] == "warning" and "separation letter" in ex4[0]["title"]


def test_review_and_lwd_items():
    sub = case(status="manager_review", lwd=None, submitted_on=date(2026, 10, 20))
    ex1 = next(x for x in CK.evaluate(sub, date(2026, 10, 26))["items"] if x["code"] == "EX-01")
    assert ex1["owner_role"] == "manager" and ex1["severity"] == "warning"
    nolwd = next(x for x in CK.evaluate(case(lwd=None), date(2026, 10, 26))["items"] if x["code"] == "EX-01")
    assert nolwd["severity"] == "blocking" and nolwd["owner_role"] == "hr"


def test_tier_map():
    spec = REG.REGISTRY["exit"]
    assert spec.tier_map == {"exit.chase": 0, "exit.run_final_pay": 2, "exit.draft_exit_letters": 1}
    assert set(EX.TOOLS) == set(spec.tier_map) and all(t in REG.EXECUTORS for t in spec.tier_map)
    assert not any(w in t for t in spec.tier_map for w in ("release", "bank", "pay_out"))
    assert HR
