"""Lexora HRM — seed the Canada demo organisation MAPLE (Maple Logistics Inc., Toronto ON).

    python scripts/seed_demo_canada.py                     # gateway http://localhost:8200
    LEXORA_GATEWAY=http://host:8200 python scripts/seed_demo_canada.py

What it builds (through the public API only — gateway + real logins, no SQL, no header forging):
  * the org (enterprise plan, country CA, province ON) + company settings (CAD, Jan fiscal year,
    America/Toronto, Mon–Fri 9–5) + the Canadian starter masters (/masters/seed-defaults);
  * 25 employees across ON / BC / QC / AB (salaried + hourly, bi-weekly, 4 % / 6 % vacation pay,
    Luhn-valid made-up SINs, made-up Canadian bank details, a 3-level manager tree);
  * logins: org admin, HR admin, payroll admin, two managers and four employees (password below);
  * every bi-weekly pay run of 2026 whose pay date has passed (committed; TEST-stamped where a
    province's statutory constants are still pending verification) + the next period as a PREVIEW;
  * one resignation (Ontario, ROE reason E) with the final cheque (vacation owed paid out) and
    the ROE draft; leave balances + approved and pending leave; a week of punches; an
    attendance fix waiting in Approvals.

Rules kept: IDEMPOTENT (looked up by org code / employee number / natural keys — a second run creates
nothing new); refuses to run when the stack has SMTP switched on (made-up e-mail addresses would be
mailed); no real people (made-up names, .example e-mails, SINs that pass Luhn but are invented).

Logins (org code MAPLE, password Maple@Demo2026):
    admin@maple.example (org admin) · hr@maple.example (HR admin) · payroll@maple.example (payroll admin)
    ops.manager@maple.example / west.manager@maple.example (managers) · amelia.campbell@maple.example (HR manager)
    harper.morin@maple.example (accountant)
    chloe.tremblay@maple.example · liam.wong@maple.example · emma.gagnon@maple.example · noah.singh@maple.example
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _captcha  # noqa: E402  (scripts/_captcha.py: solves /auth/challenge for login / sign-up)
import _seed_guard  # noqa: E402

GW = os.getenv("LEXORA_GATEWAY", "http://localhost:8200").rstrip("/")
_seed_guard.refuse_unless_local(GW)
ORG_CODE = "MAPLE"
ORG_NAME = "Maple Logistics Inc."
PW = "Maple@Demo2026"
TODAY = datetime.now(timezone.utc).date()
SESSION = requests.Session()
SUMMARY: dict[str, dict] = {}
CTX: dict = {}


class StepFail(Exception):
    pass


class Api:
    def __init__(self, token: str | None = None, who: str = "anon"):
        self.token, self.who = token, who

    def call(self, method: str, path: str, expect=(200, 201), raw: bool = False, **kw):
        h = kw.pop("headers", {}) or {}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if method.upper() == "POST" and not self.token:
            _captcha.attach(GW, path, kw.get("json"))
        r = SESSION.request(method, GW + path, headers=h, timeout=180, **kw)
        if expect is not None and r.status_code not in (expect if isinstance(expect, (tuple, list, set)) else (expect,)):
            raise StepFail(f"{self.who} {method} {path} -> {r.status_code} {r.text[:300]}")
        if raw:
            return r
        try:
            return r.json()
        except ValueError:
            return r.content

    def get(self, p, **kw):
        return self.call("GET", p, **kw)

    def post(self, p, **kw):
        return self.call("POST", p, **kw)

    def put(self, p, **kw):
        return self.call("PUT", p, **kw)

    def patch(self, p, **kw):
        return self.call("PATCH", p, **kw)


ANON = Api(None, "anon")
LIST_KEYS = ("items", "rows", "employees", "results", "requests", "runs", "balances", "punches", "schedules", "data")


def items(x) -> list:
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in LIST_KEYS:
            if isinstance(x.get(k), list):
                return x[k]
    return []


def tally(module: str, created: int = 0, skipped: int = 0, note: str | None = None):
    m = SUMMARY.setdefault(module, {"created": 0, "skipped": 0, "notes": []})
    m["created"] += created
    m["skipped"] += skipped
    if note and note not in m["notes"]:
        m["notes"].append(note)


def attempt(module: str, label: str, fn):
    try:
        return fn()
    except StepFail as e:
        print(f"   ! {module}: {label}: {str(e)[:240]}", flush=True)
        tally(module, skipped=1, note=f"{label}: {str(e)[:160]}")
    except Exception as e:  # noqa: BLE001
        print(f"   ! {module}: {label}: {type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}", flush=True)
        tally(module, skipped=1, note=f"{label}: {type(e).__name__}: {str(e)[:120]}")
    return None


def section(name: str):
    print(f"\n=== {name} ===", flush=True)


def token_of(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]


def login(email: str, who: str | None = None, password: str = PW) -> Api:
    for i in range(6):
        r = ANON.post("/auth/login", json={"org_code": ORG_CODE, "email": email, "password": password}, expect=None, raw=True)
        if r.status_code == 200 and r.json().get("access_token"):
            return Api(r.json()["access_token"], who or email)
        if r.status_code == 429:
            time.sleep(5 + i * 5)
            continue
        raise StepFail(f"login {email} -> {r.status_code} {r.text[:200]}")
    raise StepFail(f"login {email}: rate limited")


def sin(i: int) -> str:
    """A made-up 9-digit number that passes the Luhn check (first digit 1–7, never a real person)."""
    body = f"{(i % 7) + 1}{(i * 7919) % 10000000:07d}"
    total = 0
    for k, ch in enumerate(reversed(body + "0")):
        n = int(ch)
        if k % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return body + str((10 - total % 10) % 10)


# ══════════════════════════════════════════════════════════════════════════════
# the company
# ══════════════════════════════════════════════════════════════════════════════
# num|first|last|gender|province|city|department|title|manager|hire|pay (annual $ or hourly rate h:..)|dob
PEOPLE_RAW = """
001|Claire|Martin|F|ON|Toronto|Executive|Chief Executive Officer||2019-03-04|185000|1975-04-12
002|Daniel|Roy|M|ON|Toronto|Human Resources|HR Director|001|2020-01-13|118000|1980-09-03
003|Priyanka|Shah|F|ON|Toronto|Finance|Payroll Manager|001|2020-06-01|96000|1984-02-21
004|Marc|Bouchard|M|ON|Toronto|Operations|Operations Manager|001|2019-09-16|105000|1979-11-08
005|Sarah|Fraser|F|BC|Vancouver|Operations|Western Region Manager|001|2021-02-01|102000|1983-06-30
006|Chloe|Tremblay|F|ON|Toronto|Operations|Logistics Coordinator|004|2022-04-04|62000|1993-07-19
007|Liam|Wong|M|BC|Vancouver|Operations|Dispatch Supervisor|005|2021-08-09|71000|1990-01-25
008|Emma|Gagnon|F|QC|Montréal|Customer Service|Customer Service Lead|004|2022-11-07|64000|1992-03-14
009|Noah|Singh|M|AB|Calgary|Operations|Fleet Coordinator|005|2023-01-16|67000|1994-10-02
010|Olivia|Brown|F|ON|Toronto|Finance|Payroll Specialist|003|2023-03-06|58000|1996-05-27
011|Ethan|MacDonald|M|ON|Mississauga|Warehouse|Warehouse Associate|004|2023-05-01|h:24.50|1998-12-11
012|Ava|Lee|F|ON|Mississauga|Warehouse|Warehouse Associate|004|2023-06-19|h:24.50|1999-08-04
013|Lucas|Pelletier|M|QC|Laval|Warehouse|Forklift Operator|008|2022-09-12|h:26.75|1991-04-22
014|Sophie|Lavoie|F|QC|Montréal|Customer Service|Customer Service Representative|008|2024-02-05|52000|1997-09-09
015|Jacob|Nguyen|M|BC|Surrey|Warehouse|Warehouse Associate|007|2023-10-02|h:25.25|1995-02-17
016|Mia|Thompson|F|BC|Vancouver|Sales|Account Executive|005|2022-01-10|78000|1989-11-30
017|Benjamin|Clark|M|AB|Edmonton|Warehouse|Forklift Operator|009|2024-04-15|h:27.00|1993-06-06
018|Charlotte|Wilson|F|AB|Calgary|Sales|Account Executive|005|2023-07-24|76000|1991-01-15
019|William|Côté|M|QC|Québec|Operations|Route Planner|008|2021-05-17|69000|1988-08-28
020|Amelia|Campbell|F|ON|Ottawa|Human Resources|HR Generalist|002|2024-01-08|66000|1994-12-03
021|James|Anderson|M|ON|Toronto|IT|Systems Administrator|004|2020-10-05|92000|1986-03-19
022|Harper|Morin|F|QC|Montréal|Finance|Accountant|003|2023-09-11|74000|1990-07-07
023|Alexander|Taylor|M|ON|Hamilton|Warehouse|Shipping Lead|004|2018-08-20|h:29.50|1982-05-01
024|Evelyn|Martel|F|AB|Calgary|Customer Service|Customer Service Representative|009|2025-03-03|51000|2000-10-21
025|Henry|Patel|M|BC|Burnaby|IT|Developer|021|2024-09-03|98000|1995-04-09
"""
LOGIN_EMAIL = {"001": "admin@maple.example", "002": "hr@maple.example", "003": "payroll@maple.example",
               "004": "ops.manager@maple.example", "005": "west.manager@maple.example"}
LOGIN_ROLE = {"001": "org_admin", "002": "hr_admin", "003": "payroll_admin", "004": "manager", "005": "manager",
              "020": "hr_manager", "022": "accountant"}
EMPLOYEE_LOGINS = ["006", "007", "008", "009"]
LEAVER = ("012", "2026-09-25", "E")          # Ava Lee resigns (ROE reason E = quit), last day worked 25 Sep 2026
POSTAL = {"Toronto": "M5V 3L9", "Mississauga": "L5B 1M2", "Ottawa": "K1P 1J1", "Hamilton": "L8P 4R5",
          "Vancouver": "V6B 1A1", "Surrey": "V3T 0A3", "Burnaby": "V5H 4M1", "Montréal": "H3B 1A7",
          "Laval": "H7N 5L1", "Québec": "G1R 4P5", "Calgary": "T2P 1J9", "Edmonton": "T5J 0N3"}
BANKS = [("004", "TD"), ("001", "BMO"), ("003", "RBC"), ("002", "Scotiabank"), ("010", "CIBC"), ("815", "Desjardins")]


def people() -> list[dict]:
    out = []
    for i, line in enumerate([l for l in PEOPLE_RAW.strip().splitlines() if l.strip()], 1):
        n, f, l, g, prov, city, dept, title, mgr, hire, pay, dob = [x.strip() for x in line.split("|")]
        email = LOGIN_EMAIL.get(n) or f"{f.lower()}.{l.lower().replace('ô', 'o').replace('é', 'e')}@maple.example"
        hourly = pay.startswith("h:")
        out.append({"num": f"MAP{n}", "key": n, "first": f, "last": l, "gender": "female" if g == "F" else "male",
                    "prov": prov, "city": city, "dept": dept, "title": title, "mgr": mgr or None, "hire": date.fromisoformat(hire),
                    "hourly": hourly, "pay": float(pay[2:] if hourly else pay), "dob": dob, "email": email, "i": i})
    return out


PEOPLE = people()
BY_KEY = {p["key"]: p for p in PEOPLE}


def emp_body(p: dict) -> dict:
    i = p["i"]
    inst, _bank = BANKS[i % len(BANKS)]
    years = (date(2026, 1, 1) - p["hire"]).days / 365.25
    body = {"employee_number": p["num"], "first_name": p["first"], "last_name": p["last"], "email": p["email"],
            "gender": p["gender"], "date_of_birth": p["dob"], "phone": f"+1 416 555 {1000 + i:04d}",
            "hire_date": str(p["hire"]), "address_country": "CA", "province_of_employment": p["prov"],
            "province_of_residence": p["prov"], "job_title": p["title"], "department": p["dept"], "work_location": p["city"],
            "employment_type": "full_time", "pay_frequency": "biweekly", "status": "active",
            "address_line1": f"{100 + i * 7} {['King St W', 'Main St', 'Rue Sainte-Catherine', '8 Ave SW', 'Granville St'][i % 5]}",
            "city": p["city"], "postal_code": POSTAL[p["city"]], "sin": sin(i), "work_eligibility": "citizen",
            "bank_institution": inst, "bank_transit": f"{10000 + i * 37:05d}", "bank_account": f"{5000000 + i * 1013}",
            "vacation_percent": 6 if years >= 5 else 4, "emergency_name": f"Alex {p['last']}", "emergency_relationship": "Spouse",
            "emergency_phone": f"+1 647 555 {2000 + i:04d}"}
    if p["hourly"]:
        body.update({"pay_type": "hourly", "hourly_rate": p["pay"], "standard_hours_per_week": 40})
    else:
        body.update({"pay_type": "salary", "annual_salary": p["pay"]})
    if p["mgr"] and p["mgr"] in CTX.get("emp", {}):
        body["manager_id"] = CTX["emp"][p["mgr"]]["id"]
    return body


# ══════════════════════════════════════════════════════════════════════════════
def org_and_settings():
    section("1 organisation + company settings")
    M = "org"
    info = ANON.get(f"/auth/login-info?org_code={ORG_CODE}")
    if info.get("org_name"):
        print(f"   org {ORG_CODE} exists ({info['org_name']}) — signing in")
        CTX["admin"] = login(LOGIN_EMAIL["001"], "admin")
        tally(M, skipped=1, note="organisation already registered")
    else:
        r = ANON.post("/auth/register-org", json={
            "org_name": ORG_NAME, "country": "CA", "province": "ON", "plan": "enterprise",
            "admin_email": LOGIN_EMAIL["001"], "admin_password": PW, "admin_first_name": "Claire", "admin_last_name": "Martin"})
        CTX["admin"] = Api(r["access_token"], "admin")
        me = CTX["admin"].get("/auth/me")
        if me.get("org_code") != ORG_CODE:
            print(f"!! the product generated org code {me.get('org_code')} instead of {ORG_CODE} — stopping")
            sys.exit(4)
        tally(M, created=1, note=f"registered {ORG_NAME} (enterprise plan, CA / ON)")
    adm = CTX["admin"]
    if (adm.get("/notifications/status") or {}).get("configured"):
        print("\nSMTP is switched ON in this stack — start it with SMTP blanked (SMTP_HOST= SMTP_USER= SMTP_PASSWORD=).")
        sys.exit(3)

    def settings():
        got = adm.get("/settings/company")
        v = got.get("value", got) or {}
        if v.get("legal_name") == ORG_NAME:
            tally(M, skipped=1, note="company settings already saved")
            return
        adm.put("/settings/company", json={"value": {"legal_name": ORG_NAME, "trade_name": "Maple Logistics", "phone": "+1 416 555 0100",
                                                     "email": "hr@maple.example", "website": "https://maple.example",
                                                     "addresses": [{"label": "Head office", "line1": "100 King St W, Suite 2500",
                                                                    "city": "Toronto", "state": "ON", "postal_code": "M5X 1A9", "country": "CA"}]}})
        adm.put("/settings/locale", json={"value": {"country": "CA", "currency": "CAD", "fy_start_month": 1, "timezone": "America/Toronto"}})
        adm.put("/settings/work_week", json={"value": {"working_days": [0, 1, 2, 3, 4], "weekly_offs": [5, 6]}})
        adm.put("/settings/work_hours", json={"value": {"start": "09:00", "end": "17:00", "hours_per_day": 7.5, "break_minutes": 30}})
        adm.put("/letters/settings", json={"company_name": ORG_NAME, "address": "100 King St W, Suite 2500\nToronto ON  M5X 1A9",
                                           "signatory_name": "Daniel Roy", "signatory_designation": "HR Director", "number_prefix": "MAP"})
        tally(M, created=5, note="company, locale (CAD, January fiscal year, America/Toronto), work week Mon-Fri, hours, letterhead")
    attempt(M, "company settings", settings)


def masters():
    section("2 master data (Canadian starter set)")
    M = "masters"
    adm = CTX["admin"]

    def starter():
        have = adm.get("/masters/pay-components")
        if any((c.get("code") or "").upper() == "VACP" for c in items(have) or have or []):
            tally(M, skipped=1, note="starter set already loaded")
            return
        r = adm.post("/masters/seed-defaults", json={})
        tally(M, created=sum(int(v) for v in (r.get("added") or {}).values()) if isinstance(r.get("added"), dict) else 1,
              note="Canadian starter set (/masters/seed-defaults): pay components REG/OT/STAT/VACP/RRSP…, leave types, stat holidays")
    attempt(M, "starter set", starter)

    def schedules():
        have = {s["name"]: s for s in items(adm.get("/time/schedules"))}
        if "Office (Mon-Fri 9-5)" not in have:
            have["Office (Mon-Fri 9-5)"] = adm.post("/time/schedules", json={
                "name": "Office (Mon-Fri 9-5)", "code": "OFF", "days_of_week": [0, 1, 2, 3, 4], "start_time": "09:00", "end_time": "17:00",
                "break_minutes": 30, "daily_hours": 7.5, "timezone": "America/Toronto", "is_default": True})
            tally(M, created=1, note="shift Office Mon-Fri 9-5")
        else:
            tally(M, skipped=1)
        CTX["sched"] = have
    attempt(M, "shifts", schedules)


def load_employees():
    rows = items(CTX["admin"].get("/employees?include_inactive=true")) or items(CTX["admin"].get("/employees"))
    CTX["emp"] = {str(r.get("employee_number"))[3:]: r for r in rows if str(r.get("employee_number") or "").startswith("MAP")}


def employees():
    section("3 employees (25) + logins")
    M = "employees"
    adm = CTX["admin"]
    load_employees()
    created = 0
    for p in PEOPLE:
        if p["key"] in CTX["emp"]:
            continue

        def mk(p=p):
            e = adm.post("/employees", json=emp_body(p))
            CTX["emp"][p["key"]] = e
            return e
        if attempt(M, f"create {p['num']}", mk) is not None:
            created += 1
    tally(M, created=created, skipped=len(PEOPLE) - created,
          note="25 people: ON 11 · QC 6 · BC 5 · AB 4 (approx.), 7 hourly, bi-weekly, SIN + direct deposit on everyone")

    L = "logins"
    users = {u["email"].lower(): u for u in items(adm.get("/auth/users"))}
    CTX["api"] = {"001": adm}

    def make_login(key: str, role: str):
        p, e = BY_KEY[key], CTX["emp"].get(key)
        if not e:
            raise StepFail(f"{p['num']} not created")
        existing = users.get(p["email"].lower())
        if existing and existing.get("employee_id") == e["id"]:
            try:
                CTX["api"][key] = login(p["email"], p["first"].lower())
                tally(L, skipped=1)
                return
            except StepFail:
                pass
        res = adm.post("/auth/users", json={"employee_id": e["id"], "role": role, "send_invite": True, "email": p["email"]})
        link = res.get("link")
        if not link and res.get("user_id"):
            inv = adm.post(f"/auth/users/{res['user_id']}/invite", json={}, expect=None, raw=True)
            link = inv.json().get("link") if inv.status_code in (200, 201) else None
        if link:
            ANON.post(f"/auth/invite/{token_of(link)}", json={"password": PW})
        CTX["api"][key] = login(p["email"], p["first"].lower())
        tally(L, created=1)

    attempt(L, "link the admin login to the CEO", lambda: make_login("001", "org_admin"))
    for key, role in LOGIN_ROLE.items():
        if key != "001":
            attempt(L, f"login {BY_KEY[key]['email']}", lambda key=key, role=role: make_login(key, role))
    for key in EMPLOYEE_LOGINS:
        attempt(L, f"login {BY_KEY[key]['email']}", lambda key=key: make_login(key, "employee"))
    CTX["hr"] = CTX["api"].get("002", adm)
    CTX["pay"] = CTX["api"].get("003", adm)

    def shifts():
        gen = (CTX.get("sched") or {}).get("Office (Mon-Fri 9-5)")
        if not gen:
            raise StepFail("no schedule")
        current = {str(a.get("employee_id")): str(a.get("schedule_id")) for a in items(adm.get("/time/assignments"))}
        n = 0
        for p in PEOPLE:
            e = CTX["emp"].get(p["key"])
            if e and current.get(e["id"]) != gen["id"]:
                adm.put(f"/time/assignments/{e['id']}", json={"schedule_id": gen["id"], "effective_from": str(p["hire"])})
                n += 1
        tally("attendance", created=n, skipped=0 if n else 1, note="everyone on the Office schedule")
    attempt("attendance", "shift assignment", shifts)


def periods_2026() -> list[dict]:
    """Bi-weekly periods Sunday → Saturday, paid the following Friday (anchor: period ending 3 Jan 2026)."""
    out, end = [], date(2026, 1, 3)
    while end.year == 2026 or end <= date(2027, 1, 2):
        out.append({"period_start": end - timedelta(days=13), "period_end": end, "pay_date": end + timedelta(days=6)})
        end += timedelta(days=14)
        if end > date(2026, 12, 31):
            break
    return out


def pay_runs():
    section("4 bi-weekly pay runs 2026 (every pay date that has passed) + next period preview")
    M = "pay runs"
    pay = CTX["pay"]
    have = {str(r.get("period_end"))[:10] for r in items(pay.get("/payroll/runs")) if r.get("status") != "void"}
    leaver_key, leaver_day, reason = LEAVER
    created = skipped = 0
    upcoming = None
    for per in periods_2026():
        if per["pay_date"] > TODAY:
            upcoming = upcoming or per
            continue
        if str(per["period_end"]) in have:
            skipped += 1
            continue
        if per["period_start"] > date.fromisoformat(leaver_day) and not CTX.get("leaver_done"):
            attempt("exits", "resignation", lambda: terminate(leaver_key, leaver_day))
        ids = [CTX["emp"][p["key"]]["id"] for p in PEOPLE if p["key"] in CTX["emp"] and p["hire"] <= per["period_end"]]

        def run(per=per, ids=ids):
            body = {"pay_frequency": "biweekly", "period_start": str(per["period_start"]), "period_end": str(per["period_end"]),
                    "pay_date": str(per["pay_date"]), "year": per["pay_date"].year, "employee_ids": ids,
                    "acknowledge_unverified": True, "acknowledge_warnings": True}
            r = pay.post("/payroll/runs", json=body, expect=None, raw=True)
            if r.status_code not in (200, 201):
                raise StepFail(f"run {per['period_end']} -> {r.status_code} {r.text[:300]}")
            j = r.json()
            print(f"   ran period ending {per['period_end']}: {j.get('employee_count')} people · net ${float(j.get('total_net') or 0):,.2f} "
                  f"· {(j.get('compliance') or {}).get('mode')}", flush=True)
            return j
        if attempt(M, f"run {per['period_end']}", run) is not None:
            created += 1
    tally(M, created=created, skipped=skipped, note="bi-weekly runs Jan → the last pay date that has passed (acknowledged; TEST where a province is still pending verification)")
    if not CTX.get("leaver_done"):
        attempt("exits", "resignation", lambda: terminate(leaver_key, leaver_day))
    attempt("exits", "final pay + ROE", lambda: final_pay(leaver_key, leaver_day, reason))
    if upcoming:
        def preview(per=upcoming):
            pv = pay.post("/payroll/runs/preview", json={"pay_frequency": "biweekly", "year": per["pay_date"].year,
                                                          "period_start": str(per["period_start"]), "period_end": str(per["period_end"])})
            tally(M, note=f"next period {per['period_start']} → {per['period_end']} left as a PREVIEW ({pv.get('employee_count')} people)")
        attempt(M, "preview", preview)


def terminate(key: str, last_day: str):
    e = CTX["emp"][key]
    if (e.get("status") or "") == "terminated":
        CTX["leaver_done"] = True
        tally("exits", skipped=1, note="leaver already terminated")
        return
    CTX["hr"].patch(f"/employees/{e['id']}", json={"status": "terminated", "termination_date": last_day})
    CTX["emp"][key] = {**e, "status": "terminated", "termination_date": last_day}
    CTX["leaver_done"] = True
    tally("exits", created=1, note=f"{BY_KEY[key]['first']} {BY_KEY[key]['last']} resigned, last day {last_day}")


def final_pay(key: str, last_day: str, reason: str):
    e = CTX["emp"][key]
    pay = CTX["pay"]
    offs = items(pay.get("/payroll/off-cycle"))
    if any(o.get("employee_number") == BY_KEY[key]["num"] for o in offs):
        tally("exits", skipped=1, note="final cheque already issued")
        return
    r = pay.post("/payroll/final-pay", json={"employee_id": e["id"], "last_day": last_day, "reason": reason, "year": 2026,
                                             "acknowledge_unverified": True})
    roe = r.get("roe") or {}
    tally("exits", created=1, note=f"final cheque: vacation paid ${float(r.get('vacation_paid') or 0):,.2f}; ROE block 15A "
                                   f"{roe.get('block15a_total_insurable_hours')} h / 15B ${float(roe.get('block15b_total_insurable_earnings') or 0):,.2f}, "
                                   f"reason {(roe.get('block16_reason') or {}).get('code')}")


def leave():
    section("5 leave: balances, approved and pending requests")
    M = "leave"
    hr = CTX["hr"]
    active = [p for p in PEOPLE if p["key"] in CTX["emp"] and p["key"] != LEAVER[0]]

    def allocate():
        r = hr.post("/leave/balances/allocate", json={"employee_ids": [CTX["emp"][p["key"]]["id"] for p in active]})
        tally(M, created=int(r.get("allocated") or r.get("created") or 0), note=f"balances allocated by policy for {len(active)} people")
    attempt(M, "balances", allocate)

    def requests_():
        types = [t.get("code") for t in items(hr.get("/leave/types")) or hr.get("/leave/types")]
        if not types:
            raise StepFail("no leave types")
        vac = next((c for c in types if c and c.upper().startswith("VAC")), types[0])
        sick = next((c for c in types if c and ("SICK" in c.upper() or c.upper() == "SL")), types[-1])
        have = {(str(r.get("employee_id")), str(r.get("start_date"))[:10]) for r in items(hr.get("/leave/requests?scope=all"))}
        nxt = TODAY + timedelta(days=10)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        plan = [("006", vac, date(2026, 7, 6), date(2026, 7, 10), "Summer vacation", True),
                ("007", vac, date(2026, 8, 17), date(2026, 8, 21), "Family trip", True),
                ("008", sick, date(2026, 9, 14), date(2026, 9, 14), "Unwell", True),
                ("009", vac, nxt, nxt + timedelta(days=2), "Long weekend away", False),
                ("006", vac, nxt + timedelta(days=7), nxt + timedelta(days=7), "Appointment", False)]
        n = s = 0
        for key, code, a, b, why, approve in plan:
            e = CTX["emp"].get(key)
            if not e or (e["id"], str(a)) in have:
                s += 1
                continue
            api = CTX["api"].get(key, hr)
            body = {"leave_type_code": code, "start_date": str(a), "end_date": str(b), "reason": why}
            if api is hr:
                body["employee_id"] = e["id"]
            r = api.post("/leave/requests", json=body, expect=None, raw=True)
            if r.status_code not in (200, 201):
                tally(M, skipped=1, note=f"leave {key} {a}: {r.status_code} {r.text[:120]}")
                continue
            if approve:
                hr.post(f"/leave/requests/{r.json()['id']}/approve", json={"note": "Approved"}, expect=None)
            n += 1
        tally(M, created=n, skipped=s, note="3 approved (summer vacation, sick day), 2 pending in the managers' inbox")
    attempt(M, "requests", requests_)


def attendance():
    section("6 attendance: one week of punches + an attendance fix waiting for approval")
    M = "attendance"
    hr = CTX["hr"]
    days = []
    d = TODAY - timedelta(days=8)
    while len(days) < 5:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    group = ["006", "011", "013", "015", "017"]

    def utc(day: date, hh: int, mm: int, prov: str) -> str:
        from zoneinfo import ZoneInfo
        tz = {"ON": "America/Toronto", "QC": "America/Toronto", "BC": "America/Vancouver", "AB": "America/Edmonton"}[prov]
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def punches():
        first = CTX["emp"][group[0]]["id"]
        r = hr.get(f"/time/punches?employee_id={first}&day={days[-1]}", expect=None, raw=True)
        if r.status_code == 200 and items(r.json()):
            tally(M, skipped=1, note="punches already present")
            return
        n = 0
        for k in group:
            e, prov = CTX["emp"][k], BY_KEY[k]["prov"]
            for idx, day in enumerate(days):
                out_h = 19 if (k == "011" and idx in (1, 3)) else 17      # two long days → daily overtime in ON
                hr.post("/time/punch", json={"employee_id": e["id"], "direction": "in", "at": utc(day, 8, 55, prov), "source": "web"})
                hr.post("/time/punch", json={"employee_id": e["id"], "direction": "out", "at": utc(day, out_h, 5, prov), "source": "web"})
                n += 2
        tally(M, created=n, note=f"{len(group)} people × {len(days)} days (two 10-hour days for one warehouse associate)")
    attempt(M, "punches", punches)

    def fix():
        e = CTX["emp"]["013"]
        if [r for r in items(hr.get("/time/regularizations")) if r.get("employee_id") == e["id"]]:
            tally(M, skipped=1)
            return
        hr.post("/time/regularizations", json={"employee_id": e["id"], "work_date": str(days[1]), "reason": "missed_punch",
                                               "requested_in": "09:00", "requested_out": "17:00", "note": "Forgot to punch out"})
        tally(M, created=1, note="1 attendance fix pending in Approvals")
    attempt(M, "attendance fix", fix)


def verify():
    section("VERIFY")
    pay = CTX["pay"]
    runs = [r for r in items(pay.get("/payroll/runs")) if r.get("status") != "void"]
    print(f"   pay runs: {len(runs)}  ·  employees: {len(CTX['emp'])}")
    t4 = pay.get("/payroll/t4?year=2026")
    print(f"   T4 slips so far: {t4.get('slip_count')}  ·  box 14 total ${float((t4.get('summary') or {}).get('box14_employment_income') or 0):,.2f}")
    rl1 = pay.get("/payroll/rl1?year=2026")
    print(f"   RL-1 slips (Québec): {rl1.get('slip_count')}")


def print_summary(t0: float):
    print("\n" + "=" * 80)
    for m, v in SUMMARY.items():
        print(f"  {m:14s} created {v['created']:4d}  skipped {v['skipped']:4d}")
        for n in v["notes"]:
            print(f"      - {n}")
    print(f"done in {time.time() - t0:.0f}s — org code {ORG_CODE}, password {PW}")


def main() -> int:
    t0 = time.time()
    org_and_settings()
    masters()
    employees()
    pay_runs()
    leave()
    attendance()
    attempt("verify", "verify", verify)
    print_summary(t0)
    failed = sum(1 for v in SUMMARY.values() for n in v["notes"] if "->" in n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
