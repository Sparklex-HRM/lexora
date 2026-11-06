"""Payroll Auditor tests (Canada, 2026-10-26) — every check with a passing and a failing fixture, the statutory figures
taken from the payroll engine (never typed in the agent), the explanation linker + validator, shadow vs live runs,
the tier map and "no proposal executed without an approval event". SQLite in memory, no network, no SMTP, no model.

The CPP / QPP, EI and QPIP fixtures are produced by the REAL payroll engine (services/payroll/calc.py), so a correct
pay stub must pass PA-13 / PA-14 / PA-15 and a perturbed one must fail.

Run:  cd backend/services/ai_platform && python -m pytest -q agents/payroll_auditor
"""
import importlib.util
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.abspath(os.path.join(HERE, "..", ".."))
PAYROLL = os.path.abspath(os.path.join(SVC, "..", "payroll"))

from agents.lifecycle_testkit import CEO, HR, PAY, ORG, db, run  # noqa: E402,F401  (autouse DB fixture)

from shared.utils import org_settings  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_proposals as PR  # noqa: E402
import agentic_registry as REG  # noqa: E402
import agentic_state as ST  # noqa: E402
import agentic_tasks as TK  # noqa: E402
from agentic_models import AGENTS, AgentActionLog, AgentProposal  # noqa: E402

from agents.payroll_auditor import checks as CK  # noqa: E402
from agents.payroll_auditor import executors as EX  # noqa: F401,E402
from agents.payroll_auditor import explain as XP  # noqa: E402
from agents.payroll_auditor import inputs as IN  # noqa: E402
from agents.payroll_auditor import pack as PK  # noqa: E402
from agents.payroll_auditor import run as RN  # noqa: E402
from agents.payroll_auditor import statutory as SX  # noqa: E402
from agents.payroll_auditor import thresholds as TH  # noqa: E402


def _engine_calc():
    """The payroll service's own calc.py (it imports constants_ca / constants_qc by name)."""
    if PAYROLL not in sys.path:
        sys.path.append(PAYROLL)                      # appended: the ai_platform modules keep precedence
    spec = importlib.util.spec_from_file_location("lexora_pa_test_engine_calc", os.path.join(PAYROLL, "calc.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CALC = _engine_calc()


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    async def _none(org):
        return None, None, "low", {}
    monkeypatch.setattr(RN, "_model_client", _none)


def go_live():
    run(ST.set_mode(ORG, "live", actor_id="u-ceo", actor_email="ceo@x.ca", reason="Payroll Auditor pilot approved by the CEO"))
    org_settings.invalidate()


# ══════════════════════════ fixtures ═══════════════════════════════════════════

PS, PE, PAYDAY, TODAY = date(2026, 10, 4), date(2026, 10, 17), date(2026, 10, 23), date(2026, 10, 21)
PPS, PPE, PPAY = date(2026, 9, 20), date(2026, 10, 3), date(2026, 10, 9)
E1, E2, E3, MGR = "e-0001", "e-0002", "e-0003", "e-0099"
STAT = SX.from_engine(2026)


def emp(i, num, **kw):
    base = {"id": i, "employee_number": num, "name": f"Emp {num}", "department": "Ops", "cost_center": "CC-OPS", "status": "active",
            "hire_date": date(2024, 1, 8), "termination_date": None, "manager_id": MGR, "province": "ON", "pay_frequency": "biweekly",
            "is_active": True, "annual_salary": 78000.0, "sin": True, "sin_key": None, "bank_institution": "004", "bank_transit": "12345",
            "bank_account": True, "bank_last4": num[-4:], "td1_federal": True, "td1_provincial": True, "td1_federal_claim": None,
            "td1_provincial_claim": None, "vacation_percent": 4.0, "cpp_exempt": False, "ei_exempt": False}
    base.update(kw)
    return base


def engine_line(eid, gross=3000.0, prov="ON", ytd=None, pay=PAYDAY, vac_pct=4.0, earnings=None, unpaid=0.0, **kw):
    """A pay line exactly as the payroll engine writes it (calc.py → the stub detail shape line_from_stub reads)."""
    y = {"cpp": 0.0, "cpp2": 0.0, "ei": 0.0, "pensionable": 0.0, "insurable": 0.0, **(ytd or {})}
    if prov == "QC":
        r = CALC.calculate_paystub_qc(cash_earnings=gross, pensionable_earnings=gross, insurable_earnings=gross, taxable_earnings=gross,
                                      pay_frequency="biweekly", ytd=y)
    else:
        r = CALC.calculate_paystub_components(cash_earnings=gross, pensionable_earnings=gross, insurable_earnings=gross, taxable_earnings=gross,
                                              pay_frequency="biweekly", province=prov, ytd=y)
    d = r["deductions"]
    out = {"employee_id": eid, "gross": r["gross"], "net": r["net_pay"], "province": prov, "pay_frequency": "biweekly", "pay_date": pay.isoformat(),
           "earnings": dict(earnings or {"REG": gross}), "hours": {}, "deductions": {"cpp": d["cpp"], "cpp2": d["cpp2"], "ei": d["ei"], "qpip": d.get("qpip", 0.0),
           "income_tax": d["income_tax"], "payroll_tax": d.get("territorial_payroll_tax", 0.0), "pre_tax": 0.0, "after_tax": 0.0},
           "bases": {"pensionable": r["bases"]["pensionable"], "insurable": r["bases"]["insurable"]}, "ytd": r["ytd"], "td1": {"federal": None, "provincial": None},
           "vacation": {"percent": vac_pct, "vacationable": gross, "accrued": round(gross * vac_pct / 100, 2), "paid": 0.0},
           "payable": {"payable_days": 10, "lop_days": unpaid}, "unpaid_days": unpaid, "bonus_taxable": 0.0, "bank_last4": None, "source": "run"}
    out.update(kw)
    return out


def make_inputs(**over):
    emps = [emp(E1, "E0001"), emp(E2, "E0002"), emp(E3, "E0003"), emp(MGR, "E0099", manager_id=None, annual_salary=130000.0)]
    cur = {E1: engine_line(E1), E2: engine_line(E2), E3: engine_line(E3), MGR: engine_line(MGR, gross=5000.0)}
    prev = {E1: engine_line(E1, pay=PPAY), E2: engine_line(E2, pay=PPAY), E3: engine_line(E3, pay=PPAY), MGR: engine_line(MGR, gross=5000.0, pay=PPAY)}
    d = {"org": ORG, "pay_frequency": "biweekly", "period": IN.period_key("biweekly", PS, PE), "period_start": PS, "period_end": PE, "pay_date": PAYDAY,
         "today": TODAY, "prev_period": IN.period_key("biweekly", PPS, PPE), "prev_period_start": PPS, "prev_period_end": PPE, "prev_pay_date": PPAY,
         "employees": emps, "logins": {"by_employee": {E1: "u-e1", MGR: "u-mgr"}, "hr": ["u-hr"], "payroll": ["u-pay"], "org_admin": ["u-ceo"]},
         "current": cur, "previous": prev, "excluded": {}, "unpaid_leave": {}, "pending_regularisations": [], "pending_leave": [], "absent_days": {},
         "attendance": {"tracked": True, "locked": True, "ready": 4, "total": 4, "locked_at": datetime(2026, 10, 18, 9, tzinfo=timezone.utc), "missing": []},
         "revisions": [], "retro": [], "exits": [], "vacation_balance": {}, "ytd_before": {}, "statutory": STAT, "min_wage": {}, "tieout": {},
         "timing": {"draft_at": datetime.now(timezone.utc), "approved_at": None, "off_cycle_runs_quarter": 0, "headcount": 4, "unexplained": 0},
         "sources": {"draft": "fixture"}, "previous_run": {"id": "run-prev", "period_end": PPE}, "current_run": None}
    d.update(over)
    return d


def th(**over):
    base = TH.effective({"thresholds": {"net_delta_pct": 5, "net_delta_floor_cad": 100, "bulk_employees": 25}, "payroll_auditor": {}})
    base.update(over)
    return base


def codes(findings, code=None, sev=None, comp=None):
    return [f for f in findings if (code is None or f["code"] == code) and (sev is None or f["severity"] == sev) and (comp is None or f.get("component") == comp)]


def all_findings(inp, **t):
    return CK.run_checks(inp, th(**t))["findings"]


# ══════════════════════════ thresholds + statutory source ══════════════════════

def test_presets_and_thresholds_saved():
    t = TH.effective({"thresholds": {"net_delta_pct": 5, "net_delta_floor_cad": 1000}, "payroll_auditor": {"preset": "tight"}})
    assert t["net_delta_pct"] == 2 and t["bank_change_pct"] == 1 and t["lead_days"] == 3 and t["net_delta_floor_cad"] == 1000
    assert TH.effective({"thresholds": {"net_delta_pct": 7}, "payroll_auditor": {"preset": "custom"}})["net_delta_pct"] == 7
    with pytest.raises(ValueError):
        TH.clean({"preset": "wild"}, {})
    with pytest.raises(ValueError):
        TH.clean({"tax_delta_pct": 250}, {})
    saved = run(TH.save_payroll_thresholds(ORG, {"preset": "loose", "deduction_floor_cad": 40}, actor_id="u-ceo", actor_email="ceo@x.ca"))
    org_settings.invalidate()
    eff = TH.effective(run(ST.agentic_state(ORG)))
    assert saved["deduction_floor_cad"] == 40 and eff["net_delta_pct"] == 10 and eff["deduction_floor_cad"] == 40


def test_statutory_figures_come_from_the_payroll_engine():
    ca = SX.engine_modules()[0]
    qc = SX.engine_modules()[1]
    assert STAT["source"] == SX.ENGINE_SOURCE and STAT["edition_found"]
    assert STAT["roc"]["cpp"] == ca.stat_tables(2026)["cpp"] and STAT["roc"]["ei"] == ca.stat_tables(2026)["ei"]
    assert STAT["qc"]["qpp"] == qc.STAT_QC_2026["qpp"] and STAT["qc"]["ei_qc"] == qc.STAT_QC_2026["ei_qc"]
    plan_on, plan_qc = SX.plan_for("ON", STAT), SX.plan_for("QC", STAT)
    assert plan_on["plan"] == "CPP" and plan_on["ympe"] == ca.stat_tables(2026)["cpp"]["ympe"]
    assert plan_qc["plan"] == "QPP" and plan_qc["ei_rate"] == qc.STAT_QC_2026["ei_qc"]["rate"] and plan_qc["qpip_rate"] == qc.STAT_QC_2026["qpip"]["rate"]
    # vacation minimum from the engine's code reference: 4 % under 5 years, 6 % from 5 years (ON)
    assert SX.vacation_minimum(STAT, "ON", 2)[0] == round(ca.VACATION_PAY["ON"][0][1] * 100, 4)
    assert SX.vacation_minimum(STAT, "ON", 6)[0] == round(ca.VACATION_PAY["ON"][1][1] * 100, 4)
    assert SX.vacation_minimum(STAT, "QC", 2) == (None, None)            # no QC minimum in the code: unknown, not guessed
    # no edition for a year the engine does not carry → nothing is invented
    assert SX.from_engine(2031)["edition_found"] is False and SX.from_engine(2031)["roc"] is None
    # the Verification Center fallback parses the same figures + verified flags
    rep = {"as_of_edition": "2026", "constants": [{"key": f"{g}.{k}", "value": str(v), "status": "verified"} for g in ("cpp", "cpp2", "ei")
                                                  for k, v in ca.stat_tables(2026)[g].items()]}
    http = SX.from_compliance(rep, 2026)
    assert http["roc"]["cpp"]["ympe"] == ca.stat_tables(2026)["cpp"]["ympe"] and SX.is_verified(http, "cpp.rate", "ei.max_employee")
    assert SX.from_compliance(rep, 2027)["roc"] is None


def test_org_vacation_rule_overrides_code_reference():
    stat = SX.merge_org_vacation(dict(STAT), {"QC": {"tiers": [{"after_years": 0, "percent": 4}, {"after_years": 3, "percent": 6}], "source": "org value", "verified": True}})
    assert SX.vacation_minimum(stat, "QC", 1)[0] == 4 and SX.vacation_minimum(stat, "QC", 3)[0] == 6


def test_clean_fixture_passes_every_check():
    res = CK.run_checks(make_inputs(), th())
    bad = [f for f in res["findings"] if f["severity"] != "info"]
    assert bad == [], bad
    st = {c["code"]: c["status"] for c in res["checks"]}
    assert st["PA-08"] == "unavailable" and st["PA-13"] == "ran" and st["PA-17"] == "ran"


# ══════════════════════════ PA-01 … PA-04 ══════════════════════════════════════

def test_pa01_headcount_missing_ghost_and_leaver():
    inp = make_inputs()
    del inp["current"][E3]
    inp["current"]["ghost"] = engine_line("ghost")
    inp["employees"][1]["status"], inp["employees"][1]["termination_date"] = "terminated", date(2026, 9, 1)
    f = codes(all_findings(inp), "PA-01")
    titles = " | ".join(x["title"] for x in f)
    assert "missing from payroll" in titles and "ghost" in titles and "Leaver still being paid" in titles
    assert all(x["severity"] == "critical" for x in f)
    inp2 = make_inputs(excluded={E3: "joins after the period"})
    del inp2["current"][E3]
    assert [x["severity"] for x in codes(all_findings(inp2), "PA-01")] == ["info"]


def test_pa02_joiner_completeness_canada():
    j = emp("e-new", "E0100", hire_date=date(2026, 10, 12), status="active", sin=False, bank_transit=None, td1_federal=False, td1_provincial=False)
    inp = make_inputs(employees=make_inputs()["employees"] + [j])
    inp["current"]["e-new"] = engine_line("e-new", gross=1500.0)
    f = codes(all_findings(inp), "PA-02")
    assert len(f) == 1 and f[0]["severity"] == "critical"
    t = f[0]["title"]
    assert "SIN (due 2026-10-15)" in t and "transit number" in t and "TD1 federal" in t and "TD1ON" in t
    # a paid non-joiner without direct-deposit details is blocking too
    inp2 = make_inputs()
    inp2["employees"][0]["bank_institution"] = None
    f2 = codes(all_findings(inp2), "PA-02")
    assert f2 and f2[0]["component"] == "BANK" and f2[0]["severity"] == "critical"


def test_pa03_final_pay_vacation_owed_and_roe():
    inp = make_inputs(exits=[{"employee_id": E2, "case_number": "EX-7", "lwd": date(2026, 10, 9), "settlement_status": None}],
                      vacation_balance={E2: {"accrued": 912.0, "paid": 0.0, "balance": 912.0}})
    inp["employees"][1]["termination_date"] = date(2026, 10, 9)
    f = codes(all_findings(inp), "PA-03")
    fp = [x for x in f if x["component"] == "FINAL_PAY"][0]
    roe = [x for x in f if x["component"] == "ROE"][0]
    assert fp["dollars_at_risk"] == 912.0 and "$912.00" in fp["title"] and fp["verified"] is True       # ON ESA s.11(5)
    assert fp["threshold"]["final_pay_due"] == "2026-10-23"                                             # later of +7 days and the next pay day
    assert roe["threshold"]["roe_due"] == "2026-10-22" and roe["severity"] == "warning"                  # 5 days after the period end
    # settled final pay with vacation still on the books → warning
    inp["exits"][0]["settlement_status"] = "paid"
    f2 = codes(all_findings(inp), "PA-03")
    assert any(x["component"] == "VACATION" for x in f2) and not any(x["component"] == "FINAL_PAY" for x in f2)


def test_pa04_unpaid_days_vs_attendance():
    inp = make_inputs(pending_regularisations=[{"id": "r1", "employee_id": E1, "work_date": date(2026, 10, 8), "manager_id": MGR}])
    inp["current"][E1] = engine_line(E1, gross=2700.0, unpaid=1.0)
    f = codes(all_findings(inp), "PA-04")
    assert f[0]["severity"] == "critical" and f[0]["owner_role"] == "manager" and f[0]["dollars_at_risk"] == 270.0
    inp2 = make_inputs(unpaid_leave={E2: 2.0})                                   # leave says 2 unpaid days, the run has none
    f2 = codes(all_findings(inp2), "PA-04")
    assert f2 and f2[0]["severity"] == "warning" and f2[0]["curr"] == 0 and f2[0]["prev"] == 2
    inp3 = make_inputs(absent_days={E3: ["2026-10-06", "2026-10-07"]})
    f3 = codes(all_findings(inp3), "PA-04")
    assert f3 and f3[0]["owner_role"] == "employee" and f3[0]["dollars_at_risk"] == 600.0


# ══════════════════════════ PA-05 … PA-12 ══════════════════════════════════════

def test_pa05_variance_with_cad_floors_and_bank_change():
    inp = make_inputs()
    inp["current"][E1] = engine_line(E1, gross=3600.0, earnings={"REG": 3000.0, "BONUSX": 600.0})
    f = codes(all_findings(inp), "PA-05")
    comps = {x["component"] for x in f}
    assert {"GROSS", "NET", "BONUSX"} <= comps and "$" in f[0]["title"]
    # below the dollar floor → nothing
    inp2 = make_inputs()
    inp2["current"][E1] = engine_line(E1, gross=3090.0)
    assert not codes(all_findings(inp2, net_delta_floor_cad=1000, deduction_floor_cad=100), "PA-05")
    # direct-deposit details changed since the previous run → half the % and no floor
    inp3 = make_inputs()
    inp3["previous"][E1]["bank_last4"] = "9999"
    inp3["current"][E1] = engine_line(E1, gross=3090.0)
    f3 = codes(all_findings(inp3, net_delta_floor_cad=1000, deduction_floor_cad=100), "PA-05")
    assert f3 and f3[0]["threshold"]["bank_changed"] is True


def test_pa06_cost_centre_and_pa07_net():
    inp = make_inputs()
    inp["current"][E1] = engine_line(E1, gross=6000.0)
    assert codes(all_findings(inp), "PA-06")
    inp2 = make_inputs()
    inp2["current"][E2]["net"] = -12.5
    f = codes(all_findings(inp2), "PA-07")
    assert f and f[0]["title"] == "Negative net pay" and f[0]["severity"] == "critical"


def test_pa08_minimum_wage_is_not_invented():
    res = CK.run_checks(make_inputs(), th())
    c = {x["code"]: x for x in res["checks"]}["PA-08"]
    assert c["status"] == "unavailable" and "no provincial minimum-wage table" in c["note"] and not codes(res["findings"], "PA-08")


def test_pa09_duplicate_bank_and_sin():
    inp = make_inputs()
    inp["employees"][1].update({"bank_institution": "004", "bank_transit": "12345", "bank_last4": "0001"})
    f = codes(all_findings(inp), "PA-09")
    assert len(f) == 2 and all(x["severity"] == "warning" and x["component"] == "bank" for x in f)
    inp2 = make_inputs()
    inp2["employees"][0]["sin_key"] = inp2["employees"][2]["sin_key"] = "blind-1"
    f2 = codes(all_findings(inp2), "PA-09", "critical")
    assert len(f2) == 2 and f2[0]["component"] == "SIN"
    assert {c["code"]: c for c in CK.run_checks(make_inputs(), th())["checks"]}["PA-09"]["note"]   # SIN leg skipped without a blind index


def test_pa10_retro_needs_an_accepted_proposal():
    inp = make_inputs()
    inp["current"][E1] = engine_line(E1, gross=3400.0, earnings={"REG": 3000.0, "RETRO": 400.0})
    f = codes(all_findings(inp), "PA-10", "critical")
    assert f and "no accepted retro-pay proposal" in f[0]["title"]
    inp["retro"] = [{"id": "rp1", "employee_id": E1, "revision_id": "rv1", "code": "RETRO", "amount": 400.0, "status": "accepted"}]
    inp["revisions"] = [{"id": "rv1", "employee_id": E1, "effective_from": date(2026, 8, 1), "status": "approved", "letter_id": "L1", "old_amount": 78000, "new_amount": 82000}]
    assert not codes(all_findings(inp), "PA-10")
    inp["revisions"][0]["status"] = "submitted"
    assert codes(all_findings(inp), "PA-10", "warning")


def test_pa11_register_vs_gl_and_bank_leg_gap():
    reg = {"net": 9000.0, "gross": 14000.0, "income_tax": 2000.0}
    ok = make_inputs(tieout={"register": reg, "gl": {"net_credit": 9000.0, "gross_debit": 14000.0, "tax_credit": 2000.0, "balanced": True, "total_debit": 1, "total_credit": 1}})
    f = codes(all_findings(ok), "PA-11")
    assert not [x for x in f if x["severity"] == "critical"]
    assert any(x["component"] == "register_vs_bank_file" and "not available" in x["title"] for x in f)
    bad = make_inputs(tieout={"register": reg, "gl": {"net_credit": 8950.0, "gross_debit": 14000.0, "tax_credit": 2000.0, "balanced": False,
                                                      "total_debit": 14500.0, "total_credit": 14480.0}})
    crit = codes(all_findings(bad), "PA-11", "critical")
    assert {x["component"] for x in crit} == {"register_vs_gl_net", "gl_balance"}


def test_pa12_timing():
    inp = make_inputs(attendance={"tracked": True, "locked": False, "ready": 2, "total": 4}, today=date(2026, 10, 23))
    f = codes(all_findings(inp), "PA-12")
    assert {x["component"] for x in f} >= {"freeze", "approval"}


# ══════════════════════════ Canada statutory sanity ════════════════════════════

def test_pa13_rates_recomputed_from_engine_constants():
    inp = make_inputs()
    inp["current"][E1]["deductions"]["cpp"] += 25.0                 # over-deducted CPP
    inp["current"][E2]["deductions"]["ei"] -= 5.0
    f = codes(all_findings(inp), "PA-13")
    assert {(x["employee_id"], x["component"]) for x in f} == {(E1, "CPP"), (E2, "EI")}
    assert f[0]["verified"] is False                                   # no Verification Center flags in the fixture


def test_pa13_quebec_uses_qpp_qpip_and_the_quebec_ei_rate():
    inp = make_inputs()
    inp["employees"][2]["province"] = "QC"
    inp["current"][E3] = engine_line(E3, prov="QC")
    inp["previous"][E3] = engine_line(E3, prov="QC", pay=PPAY)
    assert not [x for x in all_findings(inp) if x["severity"] != "info"]
    # the same Quebec line deducted at the rest-of-Canada rates is caught
    roc = engine_line(E3, prov="ON")
    inp["current"][E3]["deductions"].update({"cpp": roc["deductions"]["cpp"], "ei": roc["deductions"]["ei"], "qpip": 0.0})
    f = codes(all_findings(inp), "PA-13")
    assert {x["component"] for x in f} == {"CPP", "EI", "QPIP"} and all(x["threshold"]["plan"] == "QPP" for x in f)


def test_pa14_contributions_stop_at_the_ytd_maximum():
    ca = SX.engine_modules()[0].stat_tables(2026)
    near = {"cpp": ca["cpp"]["max_employee"] - 10, "cpp2": 0.0, "ei": ca["ei"]["max_employee"] - 3, "pensionable": 70000.0, "insurable": 66000.0}
    inp = make_inputs()
    inp["current"][E1] = engine_line(E1, ytd=near)
    assert not codes(all_findings(inp), "PA-14")                      # the engine stops at the maximum
    inp["current"][E1]["ytd"]["cpp"] = ca["cpp"]["max_employee"] + 40
    f = codes(all_findings(inp), "PA-14", "critical")
    assert f and f[0]["component"] == "CPP" and f[0]["prev"] == ca["cpp"]["max_employee"]
    # dry-run line: the YTD on file + this pay
    dry = make_inputs()
    ln = engine_line(E2)
    dry["current"][E2] = {**ln, "ytd": None, "bases": None, "source": "preview"}
    dry["ytd_before"] = {E2: {"cpp": ca["cpp"]["max_employee"], "cpp2": 0, "ei": 0, "qpip": 0}}
    assert codes(all_findings(dry), "PA-14", "critical")


def test_pa15_ympe_band_year_and_edition():
    ca = SX.engine_modules()[0].stat_tables(2026)
    ympe = ca["cpp"]["ympe"]
    inp = make_inputs()
    inp["current"][E1] = engine_line(E1, gross=6000.0, ytd={"pensionable": ympe - 3000, "cpp": 3900.0, "insurable": 60000, "ei": 1000})
    assert inp["current"][E1]["deductions"]["cpp2"] > 0               # the engine crosses the YMPE this pay
    assert not codes(all_findings(inp), "PA-15")
    inp["current"][E1]["deductions"]["cpp2"] = 0.0                    # …a run that ignores the band
    f = codes(all_findings(inp), "PA-15")
    assert f and f[0]["component"] == "CPP2" and "YMPE" in f[0]["rule"]
    # YTD carried over into a new-year pay date
    ny = make_inputs(pay_date=date(2027, 1, 8))
    ny["current"][E2] = engine_line(E2, ytd={"cpp": 2000.0, "pensionable": 40000.0}, pay=date(2027, 1, 8))
    ny["previous"][E2] = engine_line(E2, pay=date(2026, 12, 24))
    assert any(x["component"] == "YTD" for x in codes(all_findings(ny), "PA-15", "critical"))
    # no CPP / EI edition in the engine for the pay date's year
    noed = make_inputs(statutory=SX.from_engine(2031), pay_date=date(2031, 1, 10))
    assert any(x["component"] == "edition" for x in codes(all_findings(noed), "PA-15", "critical"))


def test_pa16_income_tax_jump_without_td1_change():
    inp = make_inputs()
    inp["current"][E1]["deductions"]["income_tax"] *= 1.5
    f = codes(all_findings(inp), "PA-16")
    assert f and f[0]["severity"] == "warning"
    inp["current"][E1]["td1"] = {"federal": 20000.0, "provincial": None}            # a TD1 change explains it
    assert not codes(all_findings(inp), "PA-16")


def test_pa17_vacation_pay_accrual():
    inp = make_inputs()
    inp["employees"][0]["hire_date"] = date(2019, 3, 1)                              # 7 years in ON → 6 % minimum (engine code reference)
    f = codes(all_findings(inp), "PA-17", "critical")
    assert f and f[0]["prev"] == 6.0 and f[0]["curr"] == 4.0
    inp2 = make_inputs()
    inp2["employees"][1]["vacation_percent"] = 6.0
    assert codes(all_findings(inp2), "PA-17", "warning")                             # run rate ≠ the employee's own rate
    inp3 = make_inputs()
    inp3["current"][E3]["vacation"]["accrued"] = 100.0
    assert codes(all_findings(inp3), "PA-17", "warning")


# ══════════════════════════ explanation ════════════════════════════════════════

def test_explain_links_variances_deterministically():
    inp = make_inputs()
    inp["current"][E1] = engine_line(E1, gross=3600.0, earnings={"REG": 3000.0, "RETRO": 600.0})
    inp["retro"] = [{"id": "rp1", "employee_id": E1, "revision_id": "rv1", "amount": 600.0, "status": "accepted"}]
    inp["current"][E2] = engine_line(E2, gross=2400.0, unpaid=2.0)
    inp["previous"][E2]["unpaid_days"] = 0.0
    res = CK.run_checks(inp, th())
    linked = XP.link_all(res["findings"], inp)
    v1 = [f for f in linked if f["code"] == "PA-05" and f["employee_id"] == E1 and f["component"] == "GROSS"][0]
    v2 = [f for f in linked if f["code"] == "PA-05" and f["employee_id"] == E2 and f["component"] == "GROSS"][0]
    assert v1["status"] == "explained" and v1["linked_event"]["type"] == "retro" and "$600.00" in v1["explanation"]
    assert v2["status"] == "explained" and v2["linked_event"]["type"] == "unpaid_days"


def test_validator_rejects_foreign_figures_and_names():
    item = {"employee": "E0001", "template": "E0001 started on 2026-10-12, so this is a part-period first pay ($1,840.00 less than the previous run).", "event": {}}
    assert XP.validate_sentence("E0001 started on 2026-10-12; gross is $1,840.00 lower.", item)[0]
    assert not XP.validate_sentence("E0001 started on 2026-10-12; gross is $1,900.00 lower.", item)[0]
    assert not XP.validate_sentence("E0002 started on 2026-10-12.", item)[0]
    assert XP.rough_tokens(XP.STATIC_PREFIX) >= 1024


# ══════════════════════════ runs, tiers, approvals ═════════════════════════════

def test_registry_and_tier_map():
    assert "payroll_auditor" in AGENTS and REG.REGISTRY["payroll_auditor"].link == "/ai/agents/payroll-auditor"
    thr = {"bulk_employees": 25, "net_delta_floor_cad": 1000}
    assert REG.tier_for("payroll_auditor", "payroll.chase_input", thresholds=thr)["tier"] == 0
    assert REG.tier_for("payroll_auditor", "payroll.lock_inputs", thresholds=thr)["tier"] == 1
    assert REG.tier_for("payroll_auditor", "payroll.send_for_approval", thresholds=thr)["tier"] == 2
    assert REG.tier_for("payroll_auditor", "change_bank_details", thresholds=thr)["tier"] == 2
    assert REG.tier_for("payroll_auditor", "payroll.lock_inputs", aggregate_impact={"headcount": 30}, thresholds=thr)["tier"] == 2
    assert set(EX.TOOLS) <= set(REG.REGISTRY["payroll_auditor"].tier_map)


def test_shadow_run_records_but_sends_nothing():
    inp = make_inputs()
    inp["employees"][0]["bank_institution"] = None
    out = run(RN.run_payroll_audit(ORG, trigger="manual", inputs=inp))
    assert out["mode"] == "shadow" and out["findings_stored"] >= 1 and out["chase"]["would_open"]
    assert run(TK.list_tasks(ORG, agent="payroll_auditor")) == []
    pack = run(PK.review_pack(ORG, out["id"]))
    assert pack["shadow"] and pack["summary"]["pay_period"] == inp["period"] and pack["summary"]["statutory"]["source"] == SX.ENGINE_SOURCE
    assert any(g["code"] == "PA-02" for g in pack["grid"]) and pack["summary"]["blocking"] >= 1


def test_live_run_chases_and_never_executes_tier2_without_two_approvers():
    go_live()
    inp = make_inputs(current_run=None)
    out = run(RN.run_payroll_audit(ORG, trigger="manual", inputs=inp, requested_by="u-pay"))
    assert out["gates"]["can_send"] and out["proposals_made"]
    send = [p for p in run(PR.list_proposals(ORG, agent="payroll_auditor")) if p["tool_name"] == "payroll.send_for_approval"][0]
    assert send["status"] == "proposed" and send["risk_tier"] == 2
    first = run(PR.approve(ORG, send["id"], PAY))
    assert first["status"] == "second_approval"
    with pytest.raises(PR.ProposalError):
        run(PR.approve(ORG, send["id"], PAY))                                       # the same person twice
    done = run(PR.approve(ORG, send["id"], CEO))
    assert done["status"] == "executed" and done["result"]["detail"]["status"] == "recorded"
    tasks = run(TK.list_tasks(ORG, agent="payroll_auditor"))
    assert any(t["kind"] == "commit_run" and t["owner_user_id"] == "u-pay" for t in tasks)     # a PERSON commits the run in Payroll

    async def executed_without_approval():
        from sqlalchemy import select
        from shared.utils import platform_models as pm
        async with pm.session() as s:
            props = (await s.execute(select(AgentProposal).where(AgentProposal.org_id == ORG, AgentProposal.status == "executed"))).scalars().all()
            logs = (await s.execute(select(AgentActionLog).where(AgentActionLog.org_id == ORG, AgentActionLog.event_type == "approval.approved"))).scalars().all()
        approved = {r.approval_id for r in logs}
        return [p.id for p in props if p.id not in approved]
    assert run(executed_without_approval()) == []
    assert run(LOG.verify_chain(ORG))["ok"]


def test_live_chase_opens_task_for_the_right_owner_and_checker_actions():
    go_live()
    inp = make_inputs(pending_regularisations=[{"id": "r1", "employee_id": E1, "work_date": date(2026, 10, 8), "manager_id": MGR}])
    inp["current"][E1] = engine_line(E1, gross=2700.0, unpaid=1.0)
    out = run(RN.run_payroll_audit(ORG, trigger="manual", inputs=inp))
    assert out["chase"]["opened"] >= 1
    tasks = run(TK.list_tasks(ORG, agent="payroll_auditor"))
    assert any(t["owner_user_id"] == "u-mgr" and t["kind"] == "pa_04" for t in tasks)
    grid = run(PK.findings_for_run(ORG, out["id"]))
    f = [g for g in grid if g["code"] == "PA-04"][0]
    c = run(PK.confirm(ORG, f["id"], actor_user_id="u-pay", note="real gap"))
    assert c["status"] == "confirmed" and c["value_event_id"]
    with pytest.raises(PK.PackError):
        run(PK.dismiss(ORG, f["id"], actor_user_id="u-pay", reason="x"))


def test_router_refuses_non_office_roles():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agents.payroll_auditor.router import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.post("/ai/agents/payroll-auditor/run", json={}, headers={"x-org-id": ORG, "x-user-id": "u-e1", "x-role": "employee"})
    assert r.status_code == 403
    r = c.put("/ai/agents/payroll-auditor/thresholds", json={"value": {}}, headers={"x-org-id": ORG, "x-user-id": "u-hr", "x-role": "hr_admin"})
    assert r.status_code == 403


def test_timesheet_lock_nudge_is_internal_only_and_runs_once_fully_locked(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from shared.utils.internal import internal_headers
    import internal_routers as IR
    state = {"enabled": False, "att": {"tracked": True, "locked": False, "ready": 1, "total": 2, "locked_count": 1}, "runs": []}

    class Req:
        def __init__(self, headers):
            self.headers = headers

    async def _st(org):
        return {"enabled": state["enabled"]}

    async def _emps(org):
        return [{"id": "e1", "status": "active", "is_active": True, "pay_frequency": "biweekly"}]

    async def _att(org, ps, pe, active):
        return [], [], {}, state["att"]

    async def _run(org, period):
        state["runs"].append((org, period))
    monkeypatch.setattr(ST, "agentic_state", _st)
    monkeypatch.setattr(IN, "employees", _emps)
    monkeypatch.setattr(IN, "attendance", _att)
    monkeypatch.setattr(RN, "on_attendance_locked", _run)
    body = {"org_id": ORG, "pay_frequency": "biweekly"}
    with pytest.raises(HTTPException) as e:
        run(IR.attendance_locked(body, Req({"x-org-id": ORG})))
    assert e.value.status_code == 404                                   # browsers never reach it
    hdr = Req(internal_headers(ORG, "time-attendance", role="payroll_admin"))
    assert run(IR.attendance_locked(body, hdr))["reason"] == "agentic add-on off"
    state["enabled"] = True
    r = run(IR.attendance_locked(body, hdr))
    assert r["triggered"] is False and "1/2" in r["reason"] and r["period"].startswith("biweekly:")
    state["att"] = {"tracked": True, "locked": True, "ready": 2, "total": 2, "locked_count": 2}

    async def _both():
        out = await IR.attendance_locked(body, hdr)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return out
    r = run(_both())
    assert r["triggered"] is True and state["runs"] and state["runs"][0][1] == r["period"]


def test_period_helpers_per_frequency():
    assert IN.period_after("monthly", date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert IN.period_after("semi_monthly", date(2026, 10, 1), date(2026, 10, 15)) == (date(2026, 10, 16), date(2026, 10, 31))
    assert IN.period_after("biweekly", PPS, PPE) == (PS, PE)
    assert IN.parse_key(IN.period_key("weekly", PS, PS + timedelta(days=6))) == ("weekly", PS, PS + timedelta(days=6))
    assert IN.pay_group_id(ORG, "biweekly") == IN.pay_group_id(ORG, "biweekly") != IN.pay_group_id(ORG, "weekly")
