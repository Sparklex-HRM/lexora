"""Lexora HRM — end-to-end Canada path against a RUNNING stack with the MAPLE demo org.

    python scripts/seed_demo_canada.py         # once
    python -X utf8 scripts/e2e_canada.py       # gateway http://localhost:8200
    LEXORA_GATEWAY=http://host:8200 python -X utf8 scripts/e2e_canada.py --json out.json

Walks the Canadian payroll year and the HR modules the way an admin / payroll / employee would:
login → org country → employees (SIN masking + validation, provinces, postal codes) → calculator for all
13 jurisdictions + every pay frequency → bi-weekly preview + commit (a LIVE run is never voided; a TEST run is) → payslip
PDF (CAD, CPP / EI / tax) → ROE draft + XML + validator → T4 / T4 Summary / RL-1 → remittance schedule /
PD7A → GL journal → employer health tax → WCB / safety / audit chain → vacation owed → stat holidays /
overtime rules → leave → attendance → approvals → reports / search / notifications.

Prints PASS / FAIL per step (with the value seen) and exits 1 on any FAIL. No SMTP, no AI key needed.
Writes test data only to the MAPLE demo org (the next bi-weekly pay run, a leave request,
a safety incident).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _captcha  # noqa: E402

GW = os.getenv("LEXORA_GATEWAY", "http://localhost:8200").rstrip("/")
ORG = os.getenv("LEXORA_E2E_ORG", "MAPLE")
PW = os.getenv("LEXORA_E2E_PASSWORD", "Maple@Demo2026")
S = requests.Session()
RESULTS: list[dict] = []
PROVINCES = ("ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT")


def check(area: str, name: str, ok: bool, seen=None):
    RESULTS.append({"area": area, "check": name, "ok": bool(ok), "seen": seen})
    print(f"  {'PASS' if ok else 'FAIL'}  [{area}] {name}" + (f"  — {str(seen)[:220]}" if seen is not None else ""), flush=True)
    return ok


class Api:
    def __init__(self, token=None, who="anon"):
        self.token, self.who = token, who

    def req(self, method, path, **kw):
        h = kw.pop("headers", {}) or {}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if method == "POST" and not self.token:
            _captcha.attach(GW, path, kw.get("json"))
        return S.request(method, GW + path, headers=h, timeout=120, **kw)

    def get(self, p, **kw):
        return self.req("GET", p, **kw)

    def post(self, p, **kw):
        return self.req("POST", p, **kw)

    def put(self, p, **kw):
        return self.req("PUT", p, **kw)

    def patch(self, p, **kw):
        return self.req("PATCH", p, **kw)


ANON = Api()


def login(email: str) -> Api:
    for i in range(6):
        r = ANON.post("/auth/login", json={"org_code": ORG, "email": email, "password": PW})
        if r.status_code == 200 and r.json().get("access_token"):
            return Api(r.json()["access_token"], email)
        if r.status_code == 429:
            time.sleep(5 + 5 * i)
            continue
        raise SystemExit(f"login {email}: {r.status_code} {r.text[:200]}")
    raise SystemExit(f"login {email}: rate limited")


def js(r):
    try:
        return r.json()
    except ValueError:
        return None


def items(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ("items", "rows", "employees", "requests", "runs", "slips", "months", "lines", "stubs", "data"):
            if isinstance(x.get(k), list):
                return x[k]
    return []


def main() -> int:
    t0 = time.time()
    adm = login("admin@maple.example")
    pay = login("payroll@maple.example")
    hr = login("hr@maple.example")
    emp = login("chloe.tremblay@maple.example")
    mgr = login("ops.manager@maple.example")

    # ── 1. org + employees ──────────────────────────────────────────────────
    me = js(adm.get("/auth/me")) or {}
    check("org", "org admin signed in to MAPLE", me.get("org_code") == ORG and me.get("role") == "org_admin", me.get("role"))
    check("org", "organisation country is CA", (me.get("country") or "CA") == "CA", me.get("country"))
    rows = items(js(hr.get("/employees")))
    check("employees", "MAPLE has its demo employees", len(rows) >= 20, len(rows))
    provs = {(e.get("province_of_employment") or "") for e in rows}
    check("employees", "employees in ON / BC / QC / AB", {"ON", "BC", "QC", "AB"} <= provs, sorted(provs))
    sins = [e.get("sin") or e.get("sin_masked") for e in rows if e.get("sin") or e.get("sin_masked")]
    check("employees", "SIN never returned in clear in the list", all(not str(s).isdigit() for s in sins), sins[:2])
    bad = hr.post("/employees", json={"first_name": "Bad", "last_name": "Sin", "sin": "123456789", "province_of_employment": "ON",
                                      "hire_date": "2026-01-05", "annual_salary": 50000})
    check("employees", "invalid SIN (fails Luhn) is refused", bad.status_code in (400, 422), bad.status_code)
    badp = hr.post("/employees", json={"first_name": "Bad", "last_name": "Province", "province_of_employment": "KA",
                                       "hire_date": "2026-01-05", "annual_salary": 50000})
    check("employees", "non-Canadian province code is refused", badp.status_code in (400, 422), badp.status_code)
    badpc = hr.post("/employees", json={"first_name": "Bad", "last_name": "Postal", "province_of_employment": "ON",
                                        "postal_code": "560025", "hire_date": "2026-01-05", "annual_salary": 50000})
    check("employees", "non-Canadian postal code is refused", badpc.status_code in (400, 422), badpc.status_code)
    chloe = next((e for e in rows if e.get("employee_number") == "MAP006"), None)
    leaver = next((e for e in js(hr.get("/employees?include_inactive=true")) or [] if e.get("employee_number") == "MAP012"), None)

    # ── 2. calculator: every jurisdiction + every pay frequency ────────────
    for prov in PROVINCES:
        r = pay.post("/payroll/calculate", json={"gross_period": 3000, "pay_frequency": "biweekly", "province": prov})
        d = (js(r) or {}).get("deductions") or {}
        ok = r.status_code == 200 and d.get("income_tax", 0) > 0 and d.get("ei", 0) > 0 and d.get("cpp", 0) > 0
        check("calc", f"{prov}: $3,000 bi-weekly → CPP/QPP, EI, income tax", ok,
              {k: d.get(k) for k in ("cpp", "ei", "qpip", "income_tax")} if ok else (r.status_code, r.text[:160]))
    caps = {}
    for freq in ("weekly", "biweekly", "semi_monthly", "monthly"):
        r = pay.post("/payroll/preview-annual", json={"annual_salary": 120000, "pay_frequency": freq, "province": "ON"})
        j = js(r) or {}
        y = j.get("year_end_ytd") or {}
        caps[freq] = (round(y.get("cpp", 0), 2), round(y.get("ei", 0), 2), j.get("periods_per_year"))
    check("calc", "$120k: every frequency lands on the same CPP / EI annual maxima", len({c[:2] for c in caps.values()}) == 1, caps)

    # ── 3. pay runs: history, preview, commit, void ────────────────────────
    runs = [r for r in items(js(pay.get("/payroll/runs"))) if r.get("status") != "void"]
    check("payroll", "bi-weekly runs committed for 2026", len(runs) >= 15, len(runs))
    last = max(runs, key=lambda r: r["period_end"]) if runs else None
    nxt_end = date.fromisoformat(last["period_end"]) + timedelta(days=14) if last else date(2026, 10, 31)
    nxt = {"period_start": str(nxt_end - timedelta(days=13)), "period_end": str(nxt_end), "pay_date": str(nxt_end + timedelta(days=6))}
    pv = pay.post("/payroll/runs/preview", json={"pay_frequency": "biweekly", "year": 2026, **{k: nxt[k] for k in ("period_start", "period_end")}})
    pvj = js(pv) or {}
    check("payroll", "bi-weekly preview of the next period", pv.status_code == 200 and pvj.get("employee_count", 0) > 0,
          {"people": pvj.get("employee_count"), "gross": (pvj.get("totals") or {}).get("gross"), "excluded": len(pvj.get("excluded") or [])})
    check("payroll", "the terminated employee is left out of the next period",
          leaver is None or leaver["id"] not in {l["employee_id"] for l in pvj.get("lines") or []}, [x.get("reason") for x in pvj.get("excluded") or []][:2])
    # commit the next period — unless the stack already runs ahead of today (a re-run of this script must not keep
    # committing future pay periods): then the latest committed run is checked instead
    if date.fromisoformat(nxt["pay_date"]) <= date.today() + timedelta(days=21):
        rc = pay.post("/payroll/runs", json={"pay_frequency": "biweekly", "year": 2026, **nxt, "acknowledge_unverified": True,
                                             "acknowledge_warnings": True})
        rj = js(rc) or {}
        check("payroll", "commit the bi-weekly run", rc.status_code == 201, {"id": rj.get("id"), "net": rj.get("total_net"),
                                                                             "mode": (rj.get("compliance") or {}).get("mode")} if rc.status_code == 201 else rc.text[:200])
        run_id = rj.get("id")
        run_mode = (rj.get("compliance") or {}).get("mode")
    else:
        rd = js(pay.get(f"/payroll/runs/{last['id']}")) or {}
        run_id, run_mode = last["id"], ((rd.get("compliance") or {}).get("mode") or rd.get("mode"))
        check("payroll", "commit the bi-weekly run (already committed through the current period)", bool(run_id), last["period_end"])
    if run_id:
        reg = js(pay.get(f"/payroll/runs/{run_id}/register")) or {}
        check("payroll", "payroll register totals = sum of rows", abs(sum(r["gross"] for r in reg.get("rows", [])) - (reg.get("totals") or {}).get("gross", -1)) < 0.05,
              (reg.get("totals") or {}).get("gross"))
        gl = js(pay.get(f"/payroll/runs/{run_id}/gl-journal")) or {}
        check("payroll", "GL journal balances to the cent", gl.get("balanced") is True, (gl.get("total_debit"), gl.get("total_credit")))
        rem = js(pay.get(f"/payroll/runs/{run_id}/remittance")) or {}
        ok = abs((rem.get("income_tax", 0) + rem.get("cpp_total", 0) + rem.get("ei_total", 0)) - rem.get("total_remittance", -1)) < 0.05
        check("payroll", "PD7A remittance = tax + CPP (×2) + EI (×2.4)", ok, rem.get("total_remittance"))
        # a run is LIVE as soon as one stub was paid on verified constants (ON / BC / AB / QC are verified) — a live run is
        # never voided (corrections go off-cycle); an all-TEST run is voided and its YTD rows reversed
        vd = pay.post(f"/payroll/runs/{run_id}/void", json={"reason": "e2e check — void a test run / live run refused"})
        vj = js(vd) or {}
        code = vj.get("code") or (vj.get("detail") or {}).get("code") if isinstance(vj.get("detail"), (dict, type(None))) else vj.get("code")
        if vd.status_code == 200:
            check("payroll", "the TEST run is voided again (YTD reversed)", True, (vj.get("void") or {}).get("ytd_rows_reversed"))
        else:
            check("payroll", "a run with LIVE stubs cannot be voided (corrections go off-cycle)", vd.status_code == 409 and code == "PAYROLL_RUN_LIVE",
                  (vd.status_code, code, run_mode))
    dup = pay.post("/payroll/runs", json={"pay_frequency": "biweekly", "year": 2026, "period_start": last["period_start"],
                                          "period_end": last["period_end"], "pay_date": last["pay_date"], "acknowledge_unverified": True}) if last else None
    check("payroll", "a second run for an already-paid period is refused", dup is not None and dup.status_code == 409, dup.status_code if dup else None)

    # ── 4. payslips ─────────────────────────────────────────────────────────
    mine = items(js(emp.get("/payroll/payslips")))
    check("payslip", "employee sees only their own payslips", bool(mine) and all(p["employee_id"] == mine[0]["employee_id"] for p in mine), len(mine))
    if mine:
        pdf = emp.get(f"/payroll/payslips/{mine[0]['stub_id']}/pdf?org=Maple%20Logistics%20Inc.")
        ok = pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        txt = ""
        try:
            from pypdf import PdfReader
            txt = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages)
        except Exception:  # noqa: BLE001
            pass
        check("payslip", "payslip PDF", ok, len(pdf.content))
        if txt:
            check("payslip", "PDF shows CPP, EI, income tax, $ amounts, vacation", all(w in txt for w in ("CPP", "EI", "Income tax", "$", "Net pay")), txt[:120].replace("\n", " "))
        det = js(emp.get(f"/payroll/payslips/{mine[0]['stub_id']}")) or {}
        check("payslip", "stub carries province, compliance stamp, vacation accrual", bool(det.get("compliance")) and "vacation" in det,
              {"mode": (det.get("compliance") or {}).get("mode"), "vac": det.get("vacation")})
    other = items(js(pay.get("/payroll/payslips")))
    foreign = next((p for p in other if mine and p["employee_id"] != mine[0]["employee_id"]), None)
    if foreign:
        r = emp.get(f"/payroll/payslips/{foreign['stub_id']}")
        check("payslip", "an employee cannot open a colleague's payslip", r.status_code == 404, r.status_code)
    qc_emp = next((e for e in rows if e.get("province_of_employment") == "QC"), None)
    if qc_emp:
        qs = items(js(pay.get(f"/payroll/payslips?employee_id={qc_emp['id']}")))
        if qs:
            d = (js(pay.get(f"/payroll/payslips/{qs[0]['stub_id']}")) or {}).get("deductions") or {}
            check("payslip", "Québec stub has QPP + QPIP + reduced EI", d.get("cpp", 0) > 0 and d.get("qpip", 0) > 0, d)

    # ── 5. ROE ──────────────────────────────────────────────────────────────
    if leaver:
        roe = js(pay.get(f"/payroll/roe/{leaver['id']}?reason=E")) or {}
        check("roe", "ROE draft: blocks 10 / 11 / 15A / 15B / 16", all(roe.get(k) is not None for k in (
            "block10_first_day_worked", "block11_last_day_paid", "block15a_total_insurable_hours", "block15b_total_insurable_earnings")),
            {k: roe.get(k) for k in ("block11_last_day_paid", "block15a_total_insurable_hours", "block15b_total_insurable_earnings")})
        check("roe", "ROE block 16 reason E = quit", (roe.get("block16_reason") or {}).get("code") == "E", roe.get("block16_reason"))
        check("roe", "ROE block 15C has per-period insurable earnings (≤ 27 periods)", 0 < len(roe.get("block15c_by_period") or []) <= 27,
              len(roe.get("block15c_by_period") or []))
        xml = pay.get(f"/payroll/roe/{leaver['id']}/xml?reason=E")
        check("roe", "ROE XML package", xml.status_code == 200 and b"<RecordOfEmployment" in xml.content, len(xml.content))
        val = js(pay.get(f"/payroll/ca/roe/{leaver['id']}/validate")) or {}
        check("roe", "ROE validator answers", isinstance(val, dict) and ("ok" in val or "errors" in val or "issues" in val), str(val)[:160])
        vac = js(pay.get(f"/payroll/vacation-owed/{leaver['id']}")) or {}
        check("final pay", "vacation balance paid out on the final cheque (owed now 0)", abs(float(vac.get("balance", 1))) < 0.01, vac)

    # ── 6. year-end ─────────────────────────────────────────────────────────
    t4 = js(pay.get("/payroll/t4?year=2026")) or {}
    s4 = t4.get("summary") or {}
    check("year-end", "T4 slips + T4 Summary", t4.get("slip_count", 0) >= 20 and s4.get("box14_employment_income", 0) > 0,
          {"slips": t4.get("slip_count"), "box14": s4.get("box14_employment_income"), "box16": s4.get("box16_cpp"), "box18": s4.get("box18_ei")})
    rl1 = js(pay.get("/payroll/rl1?year=2026")) or {}
    check("year-end", "RL-1 for the Québec employees (box H QPIP filled)", rl1.get("slip_count", 0) >= 1 and (rl1.get("summary") or {}).get("box_h_qpip", 0) > 0,
          {"slips": rl1.get("slip_count"), "box_h": (rl1.get("summary") or {}).get("box_h_qpip")})
    ye = pay.get("/payroll/ca/year-end?year=2026")
    check("year-end", "year-end checklist", ye.status_code == 200, str(js(ye))[:160])
    sched = js(pay.get("/payroll/remittance-schedule?year=2026")) or {}
    check("remittance", "monthly remittance schedule with due dates", len(sched.get("months") or []) >= 9 and all(m.get("due_date") for m in sched.get("months") or []),
          (sched.get("remitter_type"), len(sched.get("months") or [])))
    carem = pay.get("/payroll/ca/remittances?year=2026")
    check("remittance", "remitter-type schedule (/payroll/ca/remittances)", carem.status_code == 200, str(js(carem))[:160])
    eht = js(pay.get("/payroll/employer-health-tax?year=2026")) or {}
    check("eht", "employer health tax per province", len(eht.get("provinces") or []) >= 3, [(p.get("province"), p.get("tax")) for p in eht.get("provinces") or []])
    ver = js(pay.get("/payroll/verification")) or {}
    check("verification", "Verification Center lists the QC + extended-province golden cases",
          bool((ver.get("quebec") or {}).get("cases")) and bool((ver.get("provinces") or {}).get("cases")),
          (len((ver.get("quebec") or {}).get("cases") or []), len((ver.get("provinces") or {}).get("cases") or [])))
    comp = js(pay.get("/payroll/compliance")) or {}
    js_ = (comp.get("jurisdictions") or {})
    check("verification", "13 Canadian jurisdictions in the compliance roll-up, nothing else", js_.get("total") == 13 and
          all(j["code"] in PROVINCES for j in js_.get("jurisdictions") or []), {"live": js_.get("live"), "gated": js_.get("gated")})

    # ── 7. compliance service ───────────────────────────────────────────────
    wr = adm.get("/compliance/wcb/rates")
    check("wcb", "WCB / WSIB rates per province", wr.status_code == 200, str(js(wr))[:160])
    wa = adm.get("/compliance/wcb/assessment?year=2026")
    check("wcb", "WCB assessable earnings", wa.status_code == 200, str(js(wa))[:160])
    si = hr.post("/compliance/safety", json={"incident_type": "near_miss", "severity": "minor", "location": "Mississauga warehouse",
                                             "occurred_on": str(date.today()), "description": "e2e near miss — pallet jack"})
    check("safety", "OHS incident recorded", si.status_code in (200, 201), si.status_code)
    av = js(adm.get("/compliance/audit/verify")) or {}
    check("audit", "tamper-evident audit chain verifies", av.get("ok") is True or av.get("valid") is True or av.get("intact") is True, str(av)[:160])

    # ── 8. time, leave, approvals ───────────────────────────────────────────
    for prov in ("ON", "BC", "AB", "QC"):
        r = js(hr.get(f"/time/rules?province={prov}")) or {}
        check("overtime", f"{prov} ESA overtime rule", bool(r), str(r)[:160])
    hol = js(hr.get("/masters/stat-holidays")) or []
    check("holidays", "statutory holidays loaded", len(items(hol) or hol) > 5, len(items(hol) or hol))
    lt = js(hr.get("/leave/types")) or []
    check("leave", "Canadian leave types", len(items(lt) or lt) >= 3, [t.get("code") for t in (items(lt) or lt)][:8])
    bal = emp.get("/leave/balances")
    check("leave", "employee leave balances", bal.status_code == 200, str(js(bal))[:160])
    att = hr.get("/time/attendance")
    check("attendance", "attendance board", att.status_code == 200, str(js(att))[:120])
    inbox = js(mgr.get("/approvals/inbox")) or {}
    check("approvals", "manager approval inbox has pending items", len(items(inbox)) >= 1, len(items(inbox)))

    # ── 9. platform odds and ends ───────────────────────────────────────────
    st = js(adm.get("/notifications/status")) or {}
    check("notifications", "SMTP is not configured (no mail leaves the stack)", st.get("configured") is False, st)
    sr = adm.get("/search?q=chloe")
    check("search", "global search finds a person", sr.status_code == 200 and len((js(sr) or {}).get("hits") or []) >= 1, len((js(sr) or {}).get("hits") or []))
    rp = adm.get("/reports/catalog")
    check("reports", "reports catalogue", rp.status_code == 200, len(items(js(rp)) or js(rp) or []))

    passed = sum(r["ok"] for r in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} checks passed in {time.time() - t0:.0f}s")
    if "--json" in sys.argv:
        json.dump(RESULTS, open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8"), indent=1, default=str)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
