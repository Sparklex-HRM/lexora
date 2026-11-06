"""Payroll Auditor input collection — READ ONLY (Canada).

Follows the watchdogs' data-access pattern: SELECT-only SQL through ``watchdogs.rows`` (org-scoped, a missing table or
column = empty result) for employees / leave / time / payroll tables, and the payroll ENGINE over HTTP for anything
computed — the agent never calculates pay:

* ``POST {PAYROLL_URL}/payroll/runs/preview``     — the DRAFT (dry run, nothing persisted) for the pay period;
* ``GET  {PAYROLL_URL}/payroll/runs/{id}/gl-journal`` — the GL leg of the tie-out, once a committed run exists;
* ``GET  {PAYROLL_URL}/payroll/compliance``      — the Verification Center (verified flags; constants fallback).

One pay frequency per run (Canada pays weekly / bi-weekly / semi-monthly / monthly, one pay group each). The period
key is ``"<frequency>:<start>..<end>"``. Encrypted SIN / account numbers are never decrypted — presence only; a SIN
duplicate is compared on a blind index only when the employee record keeps one (``sin_hash``).

Service calls carry the services' shared identity (``shared.utils.internal.internal_headers``, role payroll_admin).
Every source that did not answer is listed in ``inputs["sources"]`` so the pack says what was NOT checked.
"""
from __future__ import annotations

import calendar
import logging
import os
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta

import httpx

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.enums import PAY_FREQUENCIES  # noqa: E402
from shared.utils.internal import internal_headers  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

from watchdogs import ORG, rows  # noqa: E402

from agents import pay_calendar as PC  # noqa: E402

from . import statutory as SX  # noqa: E402
from .checks import _d, _f  # noqa: E402

log = logging.getLogger("lexora.agentic.payroll")
SERVICE = "ai_platform"
HTTP_TIMEOUT = float(os.getenv("PAYROLL_AUDITOR_HTTP_TIMEOUT", "90") or 90)
COMMITTED = ("calculated", "approved", "paid")
DEFAULT_FREQ = "biweekly"


# ══════════════════════════ pay groups + periods ════════════════════════════════

def pay_group_id(org: str, freq: str) -> str:
    """The payroll service's pay-group id for a frequency (payroll/routers.py ``_paygroup_id``)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{org}:paygroup:{freq}"))


def offcycle_group_id(org: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{org}:paygroup:offcycle"))


def period_key(freq: str, ps: date, pe: date) -> str:
    return f"{freq}:{ps.isoformat()}..{pe.isoformat()}"


def parse_key(key: str | None) -> tuple[str | None, date | None, date | None]:
    if not key or ":" not in key or ".." not in key:
        return None, None, None
    freq, _, rng = key.partition(":")
    a, _, b = rng.partition("..")
    return (freq if freq in PAY_FREQUENCIES else None), _d(a), _d(b)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def period_after(freq: str, ps: date, pe: date) -> tuple[date, date]:
    nps = pe + timedelta(days=1)
    if freq == "monthly":
        return nps, _month_end(nps)
    if freq == "semi_monthly":
        return nps, (date(nps.year, nps.month, 15) if nps.day == 1 else _month_end(nps))
    length = max((pe - ps).days + 1, 1)
    return nps, nps + timedelta(days=length - 1)


def period_before(freq: str, ps: date, pe: date) -> tuple[date, date]:
    ppe = ps - timedelta(days=1)
    if freq == "monthly":
        return date(ppe.year, ppe.month, 1), ppe
    if freq == "semi_monthly":
        return (date(ppe.year, ppe.month, 16) if ppe.day > 15 else date(ppe.year, ppe.month, 1)), ppe
    length = max((pe - ps).days + 1, 1)
    return ppe - timedelta(days=length - 1), ppe


def period_containing(freq: str, today: date) -> tuple[date, date]:
    if freq == "monthly":
        return date(today.year, today.month, 1), _month_end(today)
    if freq == "semi_monthly":
        return (date(today.year, today.month, 1), date(today.year, today.month, 15)) if today.day <= 15 else (date(today.year, today.month, 16), _month_end(today))
    ps = today - timedelta(days=(today.weekday() + 1) % 7)            # the Sunday that starts this week
    return ps, ps + timedelta(days=(6 if freq == "weekly" else 13))


_RUN_COLS = "id, pay_group_id, period_start, period_end, pay_date, status, created_at, total_gross, total_net, meta"


async def last_regular_run(org: str, freq: str, before: date | None = None) -> dict | None:
    sql = (f"SELECT {_RUN_COLS} FROM pay_runs WHERE org_id = {ORG} AND pay_group_id = CAST(:pg AS uuid) "
           "AND status IN ('calculated','approved','paid') ")
    params: dict = {"org": org, "pg": pay_group_id(org, freq)}
    if before:
        sql += "AND period_end < :b "
        params["b"] = before
    sql += "ORDER BY period_end DESC, created_at DESC LIMIT 1"
    r = await rows(sql, params)
    return r[0] if r else None


async def run_for_period(org: str, freq: str, pe: date) -> dict | None:
    r = await rows(f"SELECT {_RUN_COLS} FROM pay_runs WHERE org_id = {ORG} AND pay_group_id = CAST(:pg AS uuid) AND period_end = :e "
                   "AND status IN ('calculated','approved','paid') ORDER BY created_at DESC LIMIT 1", {"org": org, "pg": pay_group_id(org, freq), "e": pe})
    return r[0] if r else None


async def default_frequency(org: str) -> str:
    """The frequency of the most recent committed regular run, else the most common employee pay frequency."""
    groups = {pay_group_id(org, f): f for f in PAY_FREQUENCIES}
    for r in await rows(f"SELECT pay_group_id FROM pay_runs WHERE org_id = {ORG} AND status IN ('calculated','approved','paid') "
                        "ORDER BY period_end DESC LIMIT 20", {"org": org}):
        f = groups.get(str(r.get("pay_group_id")))
        if f:
            return f
    c = Counter(r.get("pay_frequency") for r in await rows(f"SELECT pay_frequency FROM employees WHERE org_id = {ORG} AND is_active IS TRUE", {"org": org}))
    for f, _n in c.most_common():
        if f in PAY_FREQUENCIES:
            return f
    return DEFAULT_FREQ


async def resolve_period(org: str, *, today: date, period: str | None = None, pay_frequency: str | None = None) -> dict:
    """Which pay period the agent works on and when it is paid. Given a period key → that period. Else the period after
    the last committed regular run of the frequency (or the one containing today). Pay date: the last run's lag after the
    period end carried forward; before the first run, six days after the period end (weekly / bi-weekly) or the period end."""
    k_freq, k_ps, k_pe = parse_key(period)
    freq = k_freq or (pay_frequency if pay_frequency in PAY_FREQUENCIES else None) or await default_frequency(org)
    last = await last_regular_run(org, freq)
    if k_ps and k_pe:
        ps, pe = k_ps, k_pe
    elif last and _d(last.get("period_start")) and _d(last.get("period_end")):
        ps, pe = period_after(freq, _d(last["period_start"]), _d(last["period_end"]))
    else:
        ps, pe = period_containing(freq, today)
    if last and _d(last.get("pay_date")) and _d(last.get("period_end")):
        lag = max((_d(last["pay_date"]) - _d(last["period_end"])).days, 0)
    else:
        lag = 6 if freq in ("weekly", "biweekly") else 0
    prev = await last_regular_run(org, freq, before=ps)
    if prev and _d(prev.get("period_start")) and _d(prev.get("period_end")):
        pps, ppe = _d(prev["period_start"]), _d(prev["period_end"])
        ppay = _d(prev.get("pay_date"))
    else:
        pps, ppe = period_before(freq, ps, pe)
        ppay = None
    cur = await run_for_period(org, freq, pe)
    pay = _d((cur or {}).get("pay_date")) or (pe + timedelta(days=lag))
    return {"pay_frequency": freq, "period": period_key(freq, ps, pe), "period_start": ps, "period_end": pe, "pay_date": pay,
            "prev_period": period_key(freq, pps, ppe), "prev_period_start": pps, "prev_period_end": ppe, "prev_pay_date": ppay,
            "previous_run": prev, "current_run": cur, "today": today}


# ══════════════════════════ HTTP (engine + sibling services) ════════════════════

async def _get(url: str, org: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as c:
            r = await c.get(url, headers=internal_headers(org, SERVICE, role="payroll_admin"), params=params)
            if r.status_code == 200:
                return r.json(), None
            try:
                return None, f"HTTP {r.status_code}: {str(r.json().get('detail'))[:160]}"
            except Exception:                                    # noqa: BLE001
                return None, f"HTTP {r.status_code}"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, ValueError) as e:
        return None, f"{type(e).__name__}"


async def _post(url: str, org: str, body: dict):
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as c:
            r = await c.post(url, headers=internal_headers(org, SERVICE, role="payroll_admin"), json=body)
            if r.status_code in (200, 201):
                return r.json(), None
            try:
                return None, f"HTTP {r.status_code}: {str(r.json().get('detail'))[:200]}"
            except Exception:                                    # noqa: BLE001
                return None, f"HTTP {r.status_code}"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, ValueError) as e:
        return None, f"{type(e).__name__}"


async def engine_preview(org: str, freq: str, ps: date, pe: date, pay: date) -> tuple[dict | None, str | None]:
    """The draft, computed by the payroll engine's own dry run (never persisted)."""
    body = {"pay_frequency": freq, "year": pay.year, "period_start": ps.isoformat(), "period_end": pe.isoformat()}
    return await _post(f"{settings.PAYROLL_URL}/payroll/runs/preview", org, body)


# ══════════════════════════ lines ═══════════════════════════════════════════════

def line_from_preview(ln: dict, freq: str, pay: date) -> dict:
    payable = ln.get("payable") or None
    return {"employee_id": str(ln.get("employee_id")), "gross": _f(ln.get("gross")), "net": _f(ln.get("net")), "province": ln.get("province"),
            "pay_frequency": freq, "pay_date": pay.isoformat(), "earnings": {}, "hours": {},
            "deductions": {"cpp": _f(ln.get("cpp")), "cpp2": _f(ln.get("cpp2")), "ei": _f(ln.get("ei")), "qpip": 0.0,
                           "income_tax": _f(ln.get("income_tax")), "payroll_tax": 0.0, "pre_tax": 0.0, "after_tax": 0.0},
            "bases": None, "ytd": None, "td1": None, "vacation": None, "payable": payable,
            "unpaid_days": _f((payable or {}).get("lop_days")), "bonus_taxable": 0.0, "bank_last4": None,
            "flags": ln.get("flags") or [], "source": "preview"}


def line_from_stub(s: dict) -> dict:
    det = s.get("detail") or {}
    emp, d, entries = det.get("employee") or {}, det.get("deductions") or {}, det.get("entries") or {}
    earnings, hours = defaultdict(float), defaultdict(float)
    for it in (entries.get("earnings") or []) if isinstance(entries, dict) else []:
        if isinstance(it, dict) and it.get("code"):
            earnings[str(it["code"]).upper()] += _f(it.get("amount"))
            if it.get("hours") is not None:
                hours[str(it["code"]).upper()] += _f(it.get("hours"))
    payable = det.get("payable") or None
    bases = det.get("bases") or None
    return {"employee_id": str(s.get("employee_id")), "gross": _f(s.get("gross")), "net": _f(s.get("net_pay")),
            "province": emp.get("province"), "pay_frequency": emp.get("pay_frequency"), "pay_date": (det.get("period") or {}).get("pay_date"),
            "earnings": {k: round(v, 2) for k, v in earnings.items()}, "hours": {k: round(v, 2) for k, v in hours.items()},
            "deductions": {"cpp": _f(d.get("cpp") if d.get("cpp") is not None else s.get("cpp")), "cpp2": _f(d.get("cpp2") if d.get("cpp2") is not None else s.get("cpp2")),
                           "ei": _f(d.get("ei") if d.get("ei") is not None else s.get("ei")), "qpip": _f(d.get("qpip")),
                           "income_tax": _f(d.get("income_tax") if d.get("income_tax") is not None else s.get("income_tax")),
                           "payroll_tax": _f(d.get("territorial_payroll_tax")), "pre_tax": _f(d.get("pre_tax")), "after_tax": _f(d.get("after_tax"))},
            "bases": ({"pensionable": _f(bases.get("pensionable")), "insurable": _f(bases.get("insurable"))} if isinstance(bases, dict) and bases.get("pensionable") is not None else None),
            "ytd": det.get("ytd") or None, "td1": det.get("td1") or None, "vacation": det.get("vacation") or None,
            "payable": payable, "unpaid_days": _f((payable or {}).get("lop_days")),
            "bonus_taxable": _f((det.get("tax_detail") or {}).get("bonus_taxable")), "bank_last4": emp.get("bank_account_last4"),
            "department": emp.get("department"), "flags": [], "source": "run"}


async def stubs_of_run(org: str, run_id) -> list[dict]:
    return await rows(f"SELECT s.employee_id, s.gross, s.net_pay, s.cpp, s.cpp2, s.ei, s.income_tax, s.detail FROM pay_stubs s "
                      f"WHERE s.org_id = {ORG} AND s.pay_run_id = CAST(:r AS uuid)", {"org": org, "r": str(run_id)})


# ══════════════════════════ collectors ══════════════════════════════════════════

def _present(v) -> bool:
    return v is not None and str(v).strip() not in ("", "None", "null")


async def employees(org: str) -> list[dict]:
    out = await rows(
        "SELECT id, employee_number, first_name, last_name, department, cost_center, status, hire_date, termination_date, manager_id, "
        "province_of_employment, pay_frequency, is_active, annual_salary, sin_enc, sin_encrypted, bank_institution, bank_transit, "
        "bank_account_last4, bank_account_enc, td1_federal_claim, td1_provincial_claim, vacation_percent, cpp_exempt, ei_exempt "
        f"FROM employees WHERE org_id = {ORG}", {"org": org})
    # SIN blind index — only when the employee record keeps one (a missing column = no rows, the leg is skipped)
    keys = {str(r["id"]): r.get("sin_hash") for r in await rows(f"SELECT id, sin_hash FROM employees WHERE org_id = {ORG} AND sin_hash IS NOT NULL", {"org": org})}
    res = []
    for e in out:
        eid = str(e["id"])
        res.append({"id": eid, "employee_number": e.get("employee_number"), "name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
                    "department": e.get("department"), "cost_center": e.get("cost_center"), "status": e.get("status"),
                    "hire_date": _d(e.get("hire_date")), "termination_date": _d(e.get("termination_date")),
                    "manager_id": str(e["manager_id"]) if e.get("manager_id") else None, "province": (e.get("province_of_employment") or "").upper() or None,
                    "pay_frequency": e.get("pay_frequency"), "is_active": e.get("is_active", True), "annual_salary": _f(e.get("annual_salary")) or None,
                    "sin": _present(e.get("sin_enc")) or _present(e.get("sin_encrypted")), "sin_key": keys.get(eid),
                    "bank_institution": (str(e.get("bank_institution")).strip() if _present(e.get("bank_institution")) else None),
                    "bank_transit": (str(e.get("bank_transit")).strip() if _present(e.get("bank_transit")) else None),
                    "bank_account": _present(e.get("bank_account_last4")) or _present(e.get("bank_account_enc")),
                    "bank_last4": (str(e.get("bank_account_last4")).strip() if _present(e.get("bank_account_last4")) else None),
                    "td1_federal": e.get("td1_federal_claim") not in (None, ""), "td1_provincial": e.get("td1_provincial_claim") not in (None, ""),
                    "td1_federal_claim": _f(e.get("td1_federal_claim")) if e.get("td1_federal_claim") not in (None, "") else None,
                    "td1_provincial_claim": _f(e.get("td1_provincial_claim")) if e.get("td1_provincial_claim") not in (None, "") else None,
                    "vacation_percent": _f(e.get("vacation_percent")) if e.get("vacation_percent") is not None else None,
                    "cpp_exempt": bool(e.get("cpp_exempt")), "ei_exempt": bool(e.get("ei_exempt"))})
    return res


def _weekdays(s: date, e: date) -> int:
    return sum(1 for i in range((e - s).days + 1) if (s + timedelta(days=i)).weekday() < 5)


async def unpaid_leave(org: str, ps: date, pe: date) -> dict:
    """Approved UNPAID leave days in the period: leave requests whose type is unpaid on the employee's balance."""
    unpaid_types = {(str(r["employee_id"]), int(_f(r.get("fiscal_year"))), str(r.get("leave_type_code") or "").upper())
                    for r in await rows(f"SELECT employee_id, fiscal_year, leave_type_code FROM leave_balances WHERE org_id = {ORG} AND paid IS FALSE", {"org": org})}
    out: dict = defaultdict(float)
    for r in await rows(f"SELECT employee_id, fiscal_year, leave_type_code, start_date, end_date, days, half_day FROM leave_requests WHERE org_id = {ORG} "
                        "AND status = 'approved' AND start_date <= :e AND end_date >= :s", {"org": org, "s": ps, "e": pe}):
        k = (str(r["employee_id"]), int(_f(r.get("fiscal_year"))), str(r.get("leave_type_code") or "").upper())
        if k not in unpaid_types:
            continue
        s_, e_ = _d(r["start_date"]), _d(r["end_date"])
        if s_ >= ps and e_ <= pe and r.get("days") is not None:
            n = _f(r.get("days"))
        else:
            n = float(_weekdays(max(s_, ps), min(e_, pe)))
            if r.get("half_day") and s_ == e_:
                n = 0.5
        out[k[0]] = round(out[k[0]] + n, 2)
    return dict(out)


async def attendance(org: str, ps: date, pe: date, active_ids: set[str]) -> tuple[list, list, dict, dict]:
    pend_reg = await rows(f"SELECT id, employee_id, work_date, manager_id FROM ta_regularizations WHERE org_id = {ORG} AND status = 'pending' AND work_date BETWEEN :s AND :e",
                          {"org": org, "s": ps, "e": pe})
    pend_leave = await rows(f"SELECT id, employee_id, start_date, end_date, days, manager_id FROM leave_requests WHERE org_id = {ORG} AND status = 'pending' "
                            "AND start_date <= :e AND end_date >= :s", {"org": org, "s": ps, "e": pe})
    approved = await rows(f"SELECT employee_id, start_date, end_date FROM leave_requests WHERE org_id = {ORG} AND status = 'approved' AND start_date <= :e AND end_date >= :s",
                          {"org": org, "s": ps, "e": pe})
    absent = await rows(f"SELECT employee_id, work_date FROM ta_attendance_days WHERE org_id = {ORG} AND status = 'absent' AND work_date BETWEEN :s AND :e",
                        {"org": org, "s": ps, "e": pe})
    cover: dict = defaultdict(list)
    for a in approved:
        cover[str(a["employee_id"])].append((_d(a["start_date"]), _d(a["end_date"])))
    absent_days: dict = defaultdict(list)
    for d in absent:
        eid, wd = str(d["employee_id"]), _d(d["work_date"])
        if wd and not any(s_ <= wd <= e_ for s_, e_ in cover.get(eid, [])):
            absent_days[eid].append(wd.isoformat())
    ts = await rows(f"SELECT employee_id, status, locked_at FROM ta_timesheets WHERE org_id = {ORG} AND period_start >= :s AND period_end <= :e", {"org": org, "s": ps, "e": pe})
    # Who keeps timesheets: the active employees with at least one timesheet in the period. READY = every one of their
    # timesheets in the period approved or locked; LOCKED = every one locked.
    statuses: dict = defaultdict(list)
    locked_at = None
    for t in ts:
        statuses[str(t["employee_id"])].append(t.get("status"))
        if t.get("locked_at") and (locked_at is None or t["locked_at"] > locked_at):
            locked_at = t["locked_at"]
    population = (set(statuses) & active_ids) if active_ids else set(statuses)
    ready = {e for e in population if all(s in ("approved", "locked") for s in statuses[e])}
    locked_emps = {e for e in population if all(s == "locked" for s in statuses[e])}
    total = len(population)
    att = {"tracked": total > 0, "ready": len(ready), "total": total, "locked": total > 0 and locked_emps == population,
           "locked_count": len(locked_emps), "locked_at": locked_at, "missing": sorted(population - ready)[:200]}
    for r in pend_reg + pend_leave:
        r["id"], r["employee_id"] = str(r["id"]), str(r["employee_id"])
        r["manager_id"] = str(r["manager_id"]) if r.get("manager_id") else None
    return pend_reg, pend_leave, dict(absent_days), att


async def pay_inputs(org: str, prev_ps: date, pe: date) -> dict:
    out: dict = {"revisions": [], "retro": [], "exits": []}
    for r in await rows(f"SELECT id, employee_id, effective_from, status, letter_id, old_amount, new_amount FROM comp_revisions WHERE org_id = {ORG} "
                        "AND status IN ('approved','applied') AND effective_from <= :e", {"org": org, "e": pe}):
        if _d(r.get("effective_from")) and _d(r["effective_from"]) >= prev_ps - timedelta(days=400):
            out["revisions"].append({**r, "id": str(r["id"]), "employee_id": str(r["employee_id"])})
    for r in await rows(f"SELECT id, employee_id, revision_id, code, amount, status, paid_period_end FROM retro_pay_proposals WHERE org_id = {ORG} "
                        "AND status IN ('proposed','accepted','paid')", {"org": org}):
        out["retro"].append({**r, "id": str(r["id"]), "employee_id": str(r["employee_id"]), "revision_id": str(r.get("revision_id") or "")})
    for r in await rows(f"SELECT employee_id, case_number, status, lwd, settlement_status, roe_reason_code FROM exit_cases WHERE org_id = {ORG} "
                        "AND lwd BETWEEN :s AND :e AND status NOT IN ('withdrawn','rejected','cancelled')", {"org": org, "s": prev_ps, "e": pe}):
        out["exits"].append({**r, "employee_id": str(r["employee_id"]), "lwd": _d(r.get("lwd"))})
    return out


async def ytd_rows(org: str, year: int) -> tuple[dict, dict]:
    """(ytd on file before this pay, vacation balance) per employee, from the payroll YTD ledger of the pay year."""
    ytd, vac = {}, {}
    for r in await rows(f"SELECT employee_id, cpp, cpp2, ei, qpip, pensionable, insurable, vac_accrued, vac_paid FROM employee_ytd WHERE org_id = {ORG} AND year = :y",
                        {"org": org, "y": year}):
        eid = str(r["employee_id"])
        ytd[eid] = {k: _f(r.get(k)) for k in ("cpp", "cpp2", "ei", "qpip", "pensionable", "insurable")}
        vac[eid] = {"accrued": _f(r.get("vac_accrued")), "paid": _f(r.get("vac_paid")), "balance": round(_f(r.get("vac_accrued")) - _f(r.get("vac_paid")), 2)}
    return ytd, vac


async def org_vacation_rules(org: str, on: date) -> dict:
    """The org's own ``vacation.<PROV>.rule`` values (Payroll → Statutory values) in force on ``on``, with the sign-off state."""
    import json as _json
    out: dict = {}
    signed = {str(r.get("rule_key")): r for r in await rows(f"SELECT rule_key, status, value_snapshot FROM statutory_rule_verification WHERE org_id = {ORG} "
                                                             "AND rule_key LIKE 'ca.vacation.%'", {"org": org})}
    for r in await rows(f"SELECT key, value, source_url, effective_from FROM ca_statutory_values WHERE org_id = {ORG} AND key LIKE 'vacation.%' "
                        "AND effective_from <= :d ORDER BY effective_from", {"org": org, "d": on}):
        parts = str(r.get("key") or "").split(".")
        if len(parts) != 3 or parts[2] != "rule":
            continue
        try:
            val = r["value"] if isinstance(r.get("value"), dict) else _json.loads(r.get("value") or "")
        except (TypeError, ValueError):
            continue
        sg = signed.get(f"ca.{r['key']}") or {}
        out[parts[1].upper()] = {"tiers": (val or {}).get("tiers") or [], "source": f"org value {r['key']} ({r.get('source_url') or 'no source URL'})",
                                 "verified": sg.get("status") == "verified" and sg.get("value_snapshot") in (None, "", str(r.get("value")))}
    return out


async def statutory(org: str, pay: date) -> tuple[dict, str]:
    report, err = await _get(f"{settings.PAYROLL_URL}/payroll/compliance", org)
    block = SX.from_engine(pay.year)
    if block is not None:
        SX.merge_verified(block, report)
        src = block["source"] + (" + Verification Center flags" if report else f" (Verification Center unavailable: {err})")
    else:
        block = SX.from_compliance(report, pay.year) or SX.empty(pay.year, f"payroll engine constants not on disk; Verification Center: {err or 'no edition for ' + str(pay.year)}")
        src = block["source"]
    try:
        SX.merge_org_vacation(block, await org_vacation_rules(org, pay))
    except Exception as e:                                       # noqa: BLE001
        log.info("org vacation rules unreadable for %s: %s", org, e)
    return block, src


async def tieout(org: str, run: dict | None, lines: dict) -> tuple[dict, dict]:
    """Register (the committed stubs) vs the payroll GL journal — only when a committed run exists. There is no
    direct-deposit file generator, so the bank leg is reported as not available."""
    notes = {"bank": "not available — no direct-deposit (EFT) file generator in Lexora HRM"}
    if not run:
        return {}, {**notes, "gl": "pending — runs on the committed run"}
    reg = {"net": round(sum(_f(ln.get("net")) for ln in lines.values()), 2), "gross": round(sum(_f(ln.get("gross")) for ln in lines.values()), 2),
           "income_tax": round(sum(_f((ln.get("deductions") or {}).get("income_tax")) for ln in lines.values()), 2), "employees": len(lines)}
    out = {"register": reg}
    jv, err = await _get(f"{settings.PAYROLL_URL}/payroll/runs/{run['id']}/gl-journal", org)
    if jv is not None:
        jl = [x for x in (jv.get("journal") or []) if isinstance(x, dict)]
        out["gl"] = {"net_credit": sum(_f(x.get("credit")) for x in jl if str(x.get("account")) == "2100"),
                     "gross_debit": sum(_f(x.get("debit")) for x in jl if str(x.get("account")) == "6000"),
                     "tax_credit": sum(_f(x.get("credit")) for x in jl if str(x.get("account")) in ("2220", "2225")),   # CRA + Revenu Québec
                     "total_debit": _f(jv.get("total_debit")), "total_credit": _f(jv.get("total_credit")), "balanced": jv.get("balanced")}
    else:
        notes["gl"] = err
    return out, notes


async def off_cycle_runs_this_quarter(org: str, today: date) -> int:
    qs = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    r = await rows(f"SELECT COUNT(*) AS n FROM pay_runs WHERE org_id = {ORG} AND created_at >= :q AND status <> 'void' "
                   "AND (pay_group_id = CAST(:oc AS uuid) OR period_start = period_end)", {"org": org, "q": qs, "oc": offcycle_group_id(org)})
    return int(_f((r[0] if r else {}).get("n")))


# ══════════════════════════ the collector ═══════════════════════════════════════

async def collect(org: str, *, period: str | None = None, pay_frequency: str | None = None, today: date | None = None, th: dict | None = None) -> dict:
    th = th or {}
    today = today or await org_today(org)
    P = await resolve_period(org, today=today, period=period, pay_frequency=pay_frequency)
    freq, ps, pe, pay = P["pay_frequency"], P["period_start"], P["period_end"], P["pay_date"]
    sources: dict = {}
    emps = await employees(org)
    sources["employees"] = f"{len(emps)} rows" if emps else "none (employees table unreadable or empty)"
    active_ids = {e["id"] for e in emps if e.get("is_active", True) and e.get("status") in ("active", "on_leave") and (e.get("pay_frequency") or freq) == freq}
    lgn = await PC.logins(org)
    cur_lines, excluded = {}, {}
    if P["current_run"]:
        for s in await stubs_of_run(org, P["current_run"]["id"]):
            ln = line_from_stub(s)
            cur_lines[ln["employee_id"]] = ln
        sources["draft"] = f"committed run {P['current_run']['id']} ({P['current_run']['status']})"
    else:
        data, err = await engine_preview(org, freq, ps, pe, pay)
        if data is not None:
            for ln in data.get("lines") or []:
                x = line_from_preview(ln, freq, pay)
                cur_lines[x["employee_id"]] = x
            for x in data.get("excluded") or []:
                excluded[str(x.get("employee_id"))] = x.get("reason") or x.get("reason_code") or "left out by the date rules"
            sources["draft"] = f"engine dry run ({len(cur_lines)} lines, anomalies {data.get('anomalies')})"
        else:
            sources["draft"] = f"engine dry run unavailable: {err}"
    prev_lines: dict = {}
    if P["previous_run"]:
        for s in await stubs_of_run(org, P["previous_run"]["id"]):
            ln = line_from_stub(s)
            prev_lines[ln["employee_id"]] = ln
        sources["previous"] = f"run {P['previous_run']['id']} ending {P['previous_run']['period_end']}"
    else:
        sources["previous"] = "no previous regular run of this pay frequency"
    pend_reg, pend_leave, absent_days, att = await attendance(org, ps, pe, active_ids)
    sources["attendance"] = "timesheets tracked" if att["tracked"] else "no timesheets for the period (lock state not tracked)"
    unpaid = await unpaid_leave(org, ps, pe)
    pay_in = await pay_inputs(org, P["prev_period_start"], pe)
    ytd_before, vac = await ytd_rows(org, pay.year)
    if P["current_run"]:
        ytd_before = {}                                          # the committed stubs carry their own YTD
    stat, sources["statutory"] = await statutory(org, pay)
    tie, tie_notes = await tieout(org, P["current_run"], cur_lines)
    sources.update({f"tieout.{k}": v for k, v in tie_notes.items()})
    oc = await off_cycle_runs_this_quarter(org, today)
    timing = {"attendance_locked_at": att.get("locked_at"), "draft_at": pm.now_utc(), "approved_at": (P["current_run"] or {}).get("created_at"),
              "off_cycle_runs_quarter": oc, "headcount": len(cur_lines), "unexplained": 0}
    return {"org": str(org), "pay_frequency": freq, "period": P["period"], "period_start": ps, "period_end": pe, "pay_date": pay, "today": today,
            "prev_period": P["prev_period"], "prev_period_start": P["prev_period_start"], "prev_period_end": P["prev_period_end"],
            "prev_pay_date": P["prev_pay_date"], "employees": emps, "logins": lgn, "current": cur_lines, "previous": prev_lines,
            "excluded": excluded, "unpaid_leave": unpaid, "pending_regularisations": pend_reg, "pending_leave": pend_leave,
            "absent_days": absent_days, "attendance": att, "revisions": pay_in["revisions"], "retro": pay_in["retro"], "exits": pay_in["exits"],
            "vacation_balance": vac, "ytd_before": ytd_before, "statutory": stat, "min_wage": {}, "tieout": tie, "timing": timing,
            "sources": sources, "previous_run": P["previous_run"], "current_run": P["current_run"]}
