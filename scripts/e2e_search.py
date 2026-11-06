"""Lexora HRM global search — end-to-end checks through the gateway (GET /search), MAPLE demo org, read-only.

    PYTHONIOENCODING=utf-8 python -X utf8 scripts/e2e_search.py          # gateway http://localhost:8200
    LEXORA_GATEWAY=http://host:8200 python -X utf8 scripts/e2e_search.py

Searching never writes, so this runs against the MAPLE demo org (scripts/seed_demo_canada.py, pw Maple@Demo2026).
PASS / FAIL per step, exit 1 on any FAIL. Covers: login required, identity from the token only, every source
answering, role scope per record type and the RBAC NEGATIVES:
  * chloe.tremblay (employee) can't find another employee's pay stub / salary / SIN
  * ops.manager (manager) sees only their own team's leave requests
  * pay runs are for the pay office only
and measures the server latency (p50 / p95 over the query set) per login.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _captcha  # noqa: E402

GW = os.getenv("LEXORA_GATEWAY", "http://localhost:8200").rstrip("/")
ORG, PW = "MAPLE", os.getenv("LEXORA_E2E_PASSWORD", "Maple@Demo2026")
USERS = ["admin", "hr", "payroll", "ops.manager", "chloe.tremblay"]
RESULTS: list[tuple[str, str]] = []
LAT: dict[str, list[float]] = {}
TOK: dict[str, str] = {}


def login(user: str) -> str:
    if user not in TOK:
        body = {"org_code": ORG, "email": f"{user}@maple.example", "password": PW}
        _captcha.attach(GW, "/auth/login", body)
        r = requests.post(f"{GW}/auth/login", json=body, timeout=30)
        r.raise_for_status()
        TOK[user] = r.json()["access_token"]
    return TOK[user]


def search(user: str, q: str, types: str | None = None, **hdr) -> dict:
    params = {"q": q, **({"types": types} if types else {})}
    t0 = time.perf_counter()
    r = requests.get(f"{GW}/search", params=params, headers={"Authorization": f"Bearer {login(user)}", **hdr}, timeout=30)
    LAT.setdefault(user, []).append((time.perf_counter() - t0) * 1000)
    assert r.status_code == 200, f"{user} /search?q={q} -> {r.status_code} {r.text[:300]}"
    return r.json()


def hits(user, q, types=None):
    return search(user, q, types)["hits"]


def step(name):
    def deco(fn):
        try:
            note = fn() or ""
            RESULTS.append((name, "PASS"))
            print(f"PASS  {name}" + (f"  — {note}" if note else ""), flush=True)
        except Exception as ex:  # noqa: BLE001
            RESULTS.append((name, f"FAIL {ex}"))
            print(f"FAIL  {name}  — {str(ex)[:600]}", flush=True)
        return fn
    return deco


def emp_hits(user, q):
    return [x for x in hits(user, q) if x["type"] == "employee"]


def run():
    @step("login required — no token and forged identity headers are refused")
    def _():
        assert requests.get(f"{GW}/search", params={"q": "chloe"}, timeout=30).status_code == 401
        r = requests.get(f"{GW}/search", params={"q": "chloe"}, timeout=30,
                         headers={"x-org-id": "00000000-0000-0000-0000-000000000000", "x-role": "org_admin"})
        assert r.status_code == 401, r.status_code

    @step("every source answers for the org admin, nothing partial")
    def _():
        res = search("admin", "chloe")
        bad = [s for s in (res.get("sources") or []) if not s.get("ok")]
        assert not bad and not res.get("partial"), (bad, res.get("partial"))
        return f"{len(res.get('sources') or {})} sources"

    @step("people: 'tremblay' finds Chloe Tremblay for every login")
    def _():
        for u in USERS:
            assert any("Chloe Tremblay" in x["title"] for x in emp_hits(u, "tremblay")), u

    @step("people: typo 'chloe tremblai' still finds her")
    def _():
        assert any("Chloe Tremblay" in x["title"] for x in emp_hits("admin", "chloe tremblai"))

    @step("people: no salary / SIN / bank in any people hit (employee login)")
    def _():
        blob = str(hits("chloe.tremblay", "operations"))
        for bad in ("annual_salary", "sin", "bank_account", "transit"):
            assert f"'{bad}'" not in blob, bad

    @step("RBAC: chloe finds her OWN pay stubs ('payslip')")
    def _():
        p = hits("chloe.tremblay", "payslip", "payslip")
        assert p, "no pay stubs"
        return f"{len(p)} pay stubs: {[x['title'] for x in p][:2]}"

    @step("RBAC negative: chloe can't find another employee's pay stub or salary")
    def _():
        for q in ("liam wong payslip", "claire martin salary"):
            p = [x for x in hits("chloe.tremblay", q) if x["type"] in ("payslip", "run", "salary")]
            assert p == [], (q, [x["title"] for x in p])
        runs = [x for x in hits("chloe.tremblay", "payroll run") if x["type"] == "run"]     # her own stubs may match; runs never
        assert runs == [], [x["title"] for x in runs]

    @step("RBAC: manager ops.manager sees only own team's leave requests")
    def _():
        got = hits("ops.manager", "leave", "leave")
        assert all("Liam Wong" not in x["title"] for x in got), [x["title"] for x in got]   # Liam reports to the western manager
        return f"{len(got)} leave hits"

    @step("RBAC: employee chloe sees only her own leave requests")
    def _():
        who = {x["title"].split(" — ")[-1] for x in hits("chloe.tremblay", "leave", "leave")}
        assert who <= {"Chloe Tremblay"}, who

    @step("payroll office: pay runs found; employee never gets runs")
    def _():
        assert [x for x in hits("payroll", "payroll run", "run")]
        assert hits("chloe.tremblay", "payroll run", "run") == []

    @step("short query (1 char) calls nobody")
    def _():
        res = search("admin", "c")
        assert res["hits"] == [], res["hits"][:2]

    @step("latency: server p95 < 500 ms per login")
    def _():
        LAT.clear()                                      # measure warm calls only (the checks above warmed every source)
        for u in USERS:                                  # a warm, realistic query mix
            for q in ("chloe", "MAP006", "t4", "payslip", "roe", "vacation", "leave oct"):
                search(u, q)
        lines, worst = [], 0.0
        for u, xs in LAT.items():
            xs = sorted(xs)
            p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
            worst = max(worst, p95)
            lines.append(f"{u} p50 {statistics.median(xs):.0f} / p95 {p95:.0f} ms (n={len(xs)})")
        assert worst < 500, lines
        return "; ".join(lines)


if __name__ == "__main__":
    run()
    failed = [n for n, r in RESULTS if not r.startswith("PASS")]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    sys.exit(1 if failed else 0)
