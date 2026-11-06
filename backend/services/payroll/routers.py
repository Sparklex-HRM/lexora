"""Payroll service — the calculator PLUS the real run engine: execute a pay run
by frequency, persist runs + stubs, roll YTD forward, and serve payslips + YTD.
"""
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from xml.sax.saxutils import escape as _xml_escape
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.audit_trail import audit_change  # noqa: E402
from shared.utils.config import settings  # noqa: E402
from shared.utils.enums import PAY_FREQUENCIES, periods_per_year  # noqa: E402

from calc import calculate_paystub, calculate_paystub_components, calculate_paystub_qc  # noqa: E402
from compliance import (  # noqa: E402
    gate_status, qc_gate_status, prov_gate_status, EXTENDED_PROVINCES,
)
from constants_ca import stat_tables  # noqa: E402
from constants_qc import STAT_QC_2026  # noqa: E402
from database import get_db  # noqa: E402
from models import EmployeeYtd, PayGroup, PayRun, PayStub, VoidedPayStub  # noqa: E402
from pay_calendar import generate_periods  # noqa: E402
import payable_days as PD  # noqa: E402  (Stream C: who is paid + pro-rating — pure)
import exit_pay  # noqa: E402  (Stream C: date-rule settings, settlements vs regular run)
import revisions_hook  # noqa: E402  (Stream B: apply due salary revisions + salary history for the run)
import entity_scope as ES  # noqa: E402  (D4: legal entities — run / stub scoping)
from shared.utils.org_time import local_today  # org-TZ business date (stream S1 helper)
from shared.utils.internal import is_internal  # noqa: E402
from pay_access import require_pay_office as _require_pay_office, guard_office_or_own as _guard_office_or_own  # noqa: E402

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-branch-id", "x-employee-id")
            if request.headers.get(k)}


# ── Whose pay may the caller read? (stream A) ────────────────────────────────
_PAY_OFFICE_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"}
_NOBODY = "00000000-0000-0000-0000-000000000000"


async def _own_pay_only(request: Request) -> str | None:
    """None → hr / payroll / admin (or another service): every employee's pay.
    Otherwise the caller's OWN employee id — the only pay records they may read
    (a manager does not see a report's payslip). A login with no employee record
    gets an id that matches nothing."""
    role = (request.headers.get("x-role") or "").lower()
    # "No user" is another service only with the services' shared token (WASA 2026-10-21).
    if role in _PAY_OFFICE_ROLES or (not role and not request.headers.get("x-user-id")
                                     and is_internal(request.headers)):
        return None
    own = (request.headers.get("x-employee-id") or "").strip()
    if not own and request.headers.get("x-user-id"):
        from shared.utils.identity import employee_id_for_user
        own = await employee_id_for_user(request.headers["x-user-id"]) or ""
    return own or _NOBODY


async def _guard_pay(request: Request, employee_id) -> None:
    """404 (same as 'not found') when the caller asks for someone else's pay."""
    own = await _own_pay_only(request)
    if own is not None and str(employee_id) != own:
        raise HTTPException(404, "Not found")


async def _fetch_employees(request: Request) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _fetch_pay_components(request: Request) -> dict:
    """Org pay-component master keyed by code → statutory flags. Empty on failure
    (base salary still runs as fully-statutory)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/masters/pay-components", headers=_fwd(request))
            if r.status_code == 200:
                return {row["code"]: row for row in r.json() if row.get("code")}
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return {}


def _period_gross(e: dict, freq: str, entry: dict | None = None) -> float:
    per = periods_per_year(freq)
    # Date rules (Stream C): a mid-period joiner / leaver is paid for the payable days
    # only. 1.0 when the run set no `payable` on the entry (off-cycle, calculators).
    _pf = PD.entry_factor(entry)
    if e.get("pay_type") == "hourly":
        # If Time & Attendance supplied actual worked hours as a REG earning, that
        # entry carries the real pay — don't also add a synthetic standard-hours
        # base (which would double-count). Salary base is untouched (OT/STAT add on top).
        if entry and any(it.get("code") == "REG" for it in (entry.get("earnings") or [])):
            return 0.0
        rate = e.get("hourly_rate") or 0
        hrs = e.get("standard_hours_per_week") or 40
        return round(rate * hrs * (52 / per) * _pf, 2)
    return round((e.get("annual_salary") or 0) / per * _pf, 2)


def _vacationable_earnings(e: dict, freq: str, entry: dict | None, comp_map: dict) -> float:
    """Earnings that accrue ESA vacation pay: the periodic base wage (always
    vacationable) plus any extra earning whose pay-component is flagged
    vacationable (REG/OT yes; STAT/VACP/bonus no)."""
    vac = _period_gross(e, freq, entry)
    for it in (entry or {}).get("earnings", []):
        c = comp_map.get(it.get("code"))
        amt = float(it.get("amount") or 0)
        if amt > 0 and c and c.get("vacationable"):
            vac += amt
    return round(vac, 2)


def _employee_tax_kwargs(e: dict) -> dict:
    """Per-employee tax profile → engine kwargs (TD1 claims, exemptions, extra tax)."""
    kw: dict = {}
    if e.get("td1_federal_claim") is not None:
        kw["td1_federal"] = float(e["td1_federal_claim"])
    if e.get("td1_provincial_claim") is not None:
        kw["td1_provincial"] = float(e["td1_provincial_claim"])
    if e.get("cpp_exempt"):
        kw["cpp_exempt"] = True
    if e.get("ei_exempt"):
        kw["ei_exempt"] = True
    if e.get("additional_tax_per_period"):
        kw["additional_tax"] = float(e["additional_tax_per_period"])
    return kw


def _employee_tax_kwargs_qc(e: dict) -> dict:
    """Per-employee tax profile → QC engine kwargs. The Québec engine takes the
    same TD1-federal claim, but the provincial claim maps to `qc_claim` and the CPP/
    EI exemptions map to `qpp_exempt`/`ei_exempt`; `additional_tax_per_period` → `additional_tax`."""
    kw: dict = {}
    if e.get("td1_federal_claim") is not None:
        kw["td1_federal"] = float(e["td1_federal_claim"])
    if e.get("td1_provincial_claim") is not None:
        kw["qc_claim"] = float(e["td1_provincial_claim"])
    if e.get("cpp_exempt"):
        kw["qpp_exempt"] = True
    if e.get("ei_exempt"):
        kw["ei_exempt"] = True
    if e.get("additional_tax_per_period"):
        kw["additional_tax"] = float(e["additional_tax_per_period"])
    return kw


# Earning codes that are a NON-CASH taxable benefit (employer-paid group-life premium, T4 box 40):
# taxed + pensionable but never paid out. The pay-component master has no `non_cash` column, so the
# seeded code is recognised here; a component / entry line carrying `non_cash: true` works too.
NON_CASH_CODES = {"TBEN"}


def _resolve_bases(base_gross: float, entry: dict, comp_map: dict) -> dict:
    """Fold base salary + optional extra pay-components into the four statutory
    bases the engine needs. Each earning/deduction takes its flags from the master
    (unknown code → treated as fully statutory / after-tax, the safe default).
    Deduction rule: a deduction whose master `taxable` flag is False is PRE-TAX
    (reduces taxable income, e.g. RRSP); taxable=True is after-tax (net only)."""
    cash = pens = ins = tax = base_gross          # base salary = periodic, fully statutory cash
    pre = after = non_cash = bonus = 0.0
    for it in (entry or {}).get("earnings", []):
        c = comp_map.get(it.get("code"), {})
        amt = float(it.get("amount") or 0)
        if amt <= 0:
            continue
        nc = bool(c.get("non_cash") or it.get("non_cash") or it.get("code") in NON_CASH_CODES)
        if nc:
            if c.get("taxable", True):
                non_cash += amt   # taxed via non_cash_taxable (not also via `tax`), never paid out
        else:
            cash += amt
        if c.get("pensionable", True):
            pens += amt
        if c.get("insurable", True) and not nc:
            ins += amt
        if c.get("taxable", True) and not nc:
            # non-periodic (bonus/commission/retro) → CRA bonus method, not annualized
            if c.get("non_periodic"):
                bonus += amt
            else:
                tax += amt
    for it in (entry or {}).get("deductions", []):
        c = comp_map.get(it.get("code"), {})
        amt = float(it.get("amount") or 0)
        if amt <= 0:
            continue
        if c.get("taxable", True):
            after += amt          # after-tax: net only (union dues, garnishment, benefit premium)
        else:
            pre += amt            # pre-tax: also reduces taxable income (RRSP)
    return {"cash_earnings": round(cash, 2), "pensionable_earnings": round(pens, 2),
            "insurable_earnings": round(ins, 2), "taxable_earnings": round(tax, 2),
            "non_cash_taxable": round(non_cash, 2), "bonus_taxable": round(bonus, 2),
            "pre_tax_deductions": round(pre, 2), "after_tax_deductions": round(after, 2)}


def _resolve_periods(freq: str, override: int | None) -> int:
    """Pays-per-year for the calc. Defaults to the frequency's standard count, but a
    27-pay biweekly / 53-pay weekly calendar year can override it (CRA annualizes by
    the ACTUAL number of pays). Only the extra-pay year (base or base+1) is allowed."""
    base = periods_per_year(freq)
    if override is None:
        return base
    allowed = {base, base + 1} if freq in ("weekly", "biweekly") else {base}
    if override not in allowed:
        raise HTTPException(400, f"periods_in_year for {freq} must be one of {sorted(allowed)}")
    return override


def _compute_for(e: dict, freq: str, year: int, ytd: dict, entries: dict | None,
                 comp_map: dict, periods_in_year: int | None = None):
    """Resolve an employee's bases + tax profile and run the CRA engine (or the Québec
    engine for QC). Returns (province, result, bases_used). `periods_in_year` overrides
    the frequency default for a 27-pay biweekly / 53-pay weekly year."""
    prov = e.get("province_of_employment", "ON")
    entry = (entries or {}).get(e["id"], {})
    bases = _resolve_bases(_period_gross(e, freq, entry), entry, comp_map)
    if prov == "QC":
        # Québec runs its OWN engine (QPP/QPP2, QPIP, reduced EI, QC tax, 16.5%
        # federal abatement). A bonus / retro is taxed there by the bonus method
        # (bonus_taxable is passed through — dropping it left the bonus untaxed).
        r = calculate_paystub_qc(
            pay_frequency=freq, ytd=ytd, year=year, periods_in_year=periods_in_year,
            prior_bonus_ytd=float(ytd.get("bonus", 0) or 0), **bases, **_employee_tax_kwargs_qc(e))
    else:
        r = calculate_paystub_components(
            pay_frequency=freq, province=prov, ytd=ytd, year=year, periods_in_year=periods_in_year,
            prior_bonus_ytd=float(ytd.get("bonus", 0) or 0), **bases, **_employee_tax_kwargs(e))
    return prov, r, bases


async def _latest_prior_stub(db, org, emp_ids: list) -> dict:
    """Most recent prior pay stub per employee (for swing detection). Keyed by
    employee_id string → {gross, net}."""
    if not emp_ids:
        return {}
    rows = (await db.execute(
        select(PayStub, PayRun.pay_date).join(PayRun, PayStub.pay_run_id == PayRun.id)
        .where(PayStub.org_id == org, PayStub.employee_id.in_(emp_ids)))).all()
    best: dict = {}
    for stub, pay_date in rows:
        eid = str(stub.employee_id)
        if eid not in best or pay_date > best[eid][1]:
            best[eid] = (stub, pay_date)
    return {eid: {"gross": float(s.gross), "net": float(s.net_pay)} for eid, (s, _) in best.items()}


def _anomalies(r: dict, prior: dict | None, no_tax: bool = False) -> list:
    """Pre-commit checks on a computed stub. errors should block a clean run;
    warnings are worth a human glance (large swings, no tax, cap hit)."""
    flags = []
    gross = r["gross"]; net = r["net_pay"]; d = r["deductions"]; tax = d["income_tax"]
    if net < 0:
        flags.append({"level": "error", "code": "negative_net", "message": "Net pay is negative — deductions exceed gross."})
    if gross > 0 and tax > gross:
        flags.append({"level": "error", "code": "tax_over_gross", "message": "Income tax exceeds gross pay."})
    if not prior:
        flags.append({"level": "info", "code": "first_pay", "message": "First pay on record for this employee."})
    else:
        pg, pn = prior["gross"], prior["net"]
        if pg > 0 and abs(gross - pg) / pg > 0.30:
            flags.append({"level": "warn", "code": "gross_swing", "message": f"Gross {(gross - pg) / pg * 100:+.0f}% vs last pay."})
        if pn > 0 and abs(net - pn) / pn > 0.35:
            flags.append({"level": "warn", "code": "net_swing", "message": f"Net {(net - pn) / pn * 100:+.0f}% vs last pay."})
    if gross > 2000 and tax == 0 and not no_tax:
        flags.append({"level": "warn", "code": "no_tax", "message": "No income tax withheld on a sizeable gross — check TD1 / exemption."})
    y = r.get("ytd", {})
    if y.get("cpp", 0) >= CAPS["cpp"] - 0.01:
        flags.append({"level": "info", "code": "cpp_maxed", "message": "CPP annual maximum reached this year."})
    if y.get("ei", 0) >= CAPS["ei"] - 0.01:
        flags.append({"level": "info", "code": "ei_maxed", "message": "EI annual maximum reached this year."})
    return flags


SALARY_BASES = ("current", "as_voided")


async def _voided_rates(db, org: str, pg_id: uuid.UUID, p_end: date, periods: int) -> dict:
    """employee_id → ANNUAL salary as the LATEST voided run of this pay group + period paid it
    (the stub's full-month rate × periods). Lets a voided TEST run be re-run 'as originally
    paid' when a back-dated revision was applied after it — otherwise the re-run would pay the
    new salary AND the queued arrears would pay the increment again."""
    vr = (await db.execute(select(PayRun.id).where(
        PayRun.org_id == org, PayRun.pay_group_id == pg_id, PayRun.period_end == p_end, PayRun.status == "void")
        .order_by(PayRun.created_at.desc()))).scalars().first()
    if not vr:
        return {}
    out = {}
    for s in (await db.execute(select(VoidedPayStub).where(VoidedPayStub.pay_run_id == vr))).scalars().all():
        det = s.detail or {}
        b = det.get("bases") or {}
        rate = b.get("full_month_gross") or (b.get("structure") or {}).get("gross")
        if not rate:
            f = float(((det.get("payable") or {}).get("factor")) or 1) or 1.0
            rate = float(s.gross) / f
        out[str(s.employee_id)] = round(float(rate) * periods, 2)
    return out


def _apply_voided_rates(emps: list, rates: dict) -> tuple[list, int]:
    """PURE: employees in `rates` get that annual salary and NO salary history for the period
    (exit_pay then builds one slice from annual_salary, so the payable factor is days only)."""
    out, n = [], 0
    for e in emps:
        a = rates.get(e["id"])
        if a:
            out.append({**e, "annual_salary": a, "comp_history": [], "salary_basis": "as_voided"})
            n += 1
        else:
            out.append(e)
    return out, n


def _paygroup_id(org: str, freq: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{org}:paygroup:{freq}")


def _require_gate(acknowledged: bool) -> dict:
    """The 'verify before real pay' gate. A persisting pay run is BLOCKED while the
    compliance gate is open (constants not CRA-verified or PDOC cases unconfirmed),
    unless explicitly acknowledged as a TEST run. Preview/calculate never gate — no
    money moves. Returns the gate so the run can be stamped."""
    gate = gate_status()
    if gate["gate_open"] and not acknowledged:
        raise HTTPException(status_code=409, detail={
            "error": "compliance_gate_open",
            "message": "Payroll is NOT verified for real pay — this run is blocked.",
            "reasons": gate["reasons"],
            "hint": "Verify statutory constants + CRA-PDOC golden cases (Payroll → Year-end → "
                    "Compliance), or pass acknowledge_unverified=true to run in TEST mode "
                    "(stubs marked unverified — not for remittance/filing).",
        })
    return gate


def _stamp_compliance(gate: dict) -> dict:
    return {"verified": not gate["gate_open"], "mode": "live" if not gate["gate_open"] else "test",
            "constants_verified": gate["constants_verified"], "constants_total": gate["constants_total"],
            "pdoc_confirmed": gate["pdoc_confirmed"], "pdoc_total": gate["pdoc_total"]}


def _require_qc_gate(acknowledged: bool) -> dict:
    """The Québec 'verify before real pay' gate — the QC analogue of _require_gate.
    A persisting QC pay run is BLOCKED while the QC compliance gate is open (QC
    constants not WebRAS/PDOC-QC-verified or cases unconfirmed) unless explicitly
    acknowledged as a TEST run. Only invoked when a run contains a QC employee; ON/
    BC/AB gating is unchanged. Returns the QC gate so QC stubs can be stamped."""
    gate = qc_gate_status()
    if gate["gate_open"] and not acknowledged:
        raise HTTPException(status_code=409, detail={
            "error": "qc_compliance_gate_open",
            "message": "Québec payroll is NOT verified for real pay — this run is blocked.",
            "reasons": gate["reasons"],
            "hint": "Verify Québec statutory constants against Revenu Québec WebRAS + PDOC-Quebec "
                    "(qc_webras_cases.py / verify_qc.py), or pass acknowledge_unverified=true to run "
                    "in TEST mode (QC stubs marked unverified — not for remittance/RL-1 filing).",
        })
    return gate


def _stamp_compliance_qc(gate: dict) -> dict:
    return {"verified": not gate["gate_open"], "mode": "live" if not gate["gate_open"] else "test",
            "constants_verified": gate["constants_verified"], "constants_total": gate["constants_total"],
            "webras_confirmed": gate["webras_confirmed"], "webras_total": gate["webras_total"],
            "plan": "QC"}


def _require_prov_gate(prov: str, acknowledged: bool) -> dict:
    """Per-province 'verify before real pay' gate for an extended jurisdiction (MB/
    SK/NS/NB/NL/PE/NT/YT/NU). A persisting run touching this province is BLOCKED
    while its gate is open (its constants aren't CRA-verified yet) unless explicitly
    acknowledged as a TEST run. ON/BC/AB and QC gating are unchanged. Returns the
    province gate so its stubs can be stamped."""
    gate = prov_gate_status(prov)
    if gate["gate_open"] and not acknowledged:
        raise HTTPException(status_code=409, detail={
            "error": "province_compliance_gate_open",
            "province": prov.upper(),
            "message": f"{prov.upper()} payroll is NOT verified for real pay — this run is blocked.",
            "reasons": gate["reasons"],
            "hint": f"Verify {prov.upper()} statutory constants against CRA T4127 2026 (Payroll → "
                    "Year-end → Compliance), or pass acknowledge_unverified=true to run in TEST mode "
                    "(stubs marked unverified — not for remittance/filing).",
        })
    return gate


def _stamp_compliance_prov(gate: dict) -> dict:
    return {"verified": not gate["gate_open"], "mode": "live" if not gate["gate_open"] else "test",
            "constants_verified": gate["constants_verified"], "constants_total": gate["constants_total"],
            "pdoc_confirmed": gate["pdoc_confirmed"], "pdoc_total": gate["pdoc_total"],
            "plan": gate["province"]}


def _prov_gates_for(emps: list) -> dict:
    """Compute the per-province gate once for each DISTINCT extended province present
    in a set of employees. Keyed by province code. Empty when the run is pure ON/BC/
    AB/QC (the common case), so nothing changes for existing tenants."""
    provs = {(e.get("province_of_employment") or "ON").upper() for e in emps}
    return {p: prov_gate_status(p) for p in provs if p in EXTENDED_PROVINCES}


def _stamp_for(prov: str, roc_gate: dict, qc_gate: dict | None, prov_gates: dict) -> dict:
    """Pick the right compliance stamp for an employee: QC → QC gate, extended province
    → its own gate, else the ROC (ON/BC/AB) gate."""
    prov = (prov or "ON").upper()
    if prov == "QC" and qc_gate:
        return _stamp_compliance_qc(qc_gate)
    if prov in prov_gates:
        return _stamp_compliance_prov(prov_gates[prov])
    return _stamp_compliance(roc_gate)


# ═══ Net-pay calculator (frequency-driven preview) ══════════════════════════
class CalcRequest(BaseModel):
    gross_period: float
    pay_frequency: str = "biweekly"
    province: str = "ON"
    ytd: dict | None = None


@router.post("/calculate")
async def calculate(body: CalcRequest, request: Request):
    _org(request)
    _require_pay_office(request, "use the pay calculator")
    try:
        return calculate_paystub(body.gross_period, body.pay_frequency, body.province, body.ytd)
    except ValueError as e:
        raise HTTPException(400, str(e))


class AnnualPreview(BaseModel):
    annual_salary: float
    pay_frequency: str = "biweekly"
    province: str = "ON"


@router.post("/preview-annual")
async def preview_annual(body: AnnualPreview, request: Request):
    _org(request)
    _require_pay_office(request, "use the pay calculator")
    if body.pay_frequency not in PAY_FREQUENCIES:
        raise HTTPException(400, f"pay_frequency must be one of {PAY_FREQUENCIES}")
    periods = periods_per_year(body.pay_frequency)
    gross_period = round(body.annual_salary / periods, 2)
    ytd, runs = None, []
    for i in range(periods):
        r = calculate_paystub(gross_period, body.pay_frequency, body.province, ytd)
        ytd = r["ytd"]
        runs.append({"period": i + 1, "gross": r["gross"], **r["deductions"], "net": r["net_pay"]})
    return {"pay_frequency": body.pay_frequency, "periods_per_year": periods,
            "gross_per_period": gross_period, "annual_gross": body.annual_salary,
            "year_end_ytd": ytd, "periods": runs}


# ═══ Run engine ═════════════════════════════════════════════════════════════
class RunPreviewRequest(BaseModel):
    pay_frequency: str = "biweekly"
    year: int = 2026
    # optional per-employee extra components: {employee_id: {earnings:[{code,amount}], deductions:[...]}}
    entries: dict | None = None
    # optional subset — run only these employees (multi-select / individual).
    # None = the whole frequency, as before.
    employee_ids: list[str] | None = None
    periods_in_year: int | None = None     # 27-pay biweekly / 53-pay weekly year
    period_end: str | None = None          # the pay-period end (date rules + the voided-rate lookup)
    period_start: str | None = None        # with period_end: the date rules (joiners / leavers) are applied
    entity_id: str | None = None           # D4: one legal entity only (None / "" = every entity)
    # "as_voided": re-run a voided TEST run at the salary rates it paid (see _voided_rates)
    salary_basis: str = "current"


def _payable_flags(p: dict | None) -> list:
    """Date-rule notes for one stub: the pro-rating reason (info) and every review line (warn)."""
    if not p:
        return []
    out = []
    if p.get("reason_code") not in (None, "full"):
        out.append({"level": "info", "code": "payable_days", "message": p.get("reason") or ""})
    out += [{"level": "warn", "code": "payable_review", "message": m} for m in (p.get("review") or [])]
    return out


@router.post("/runs/preview")
async def preview_run(body: RunPreviewRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Dry-run a pay run for a frequency: who's included and what each nets,
    WITHOUT persisting. Powers the confirm screen."""
    org = _org(request)
    _require_pay_office(request, "preview a pay run")
    if body.pay_frequency not in PAY_FREQUENCIES:
        raise HTTPException(400, f"pay_frequency must be one of {PAY_FREQUENCIES}")
    # WHO is paid is decided by the date rules below (exit_pay.decide_run), not by a
    # bare status filter: a leaver is paid up to the real last working day, a joiner from
    # the joining date, and `onboarding` / future joiners are not paid at all.
    emps = [e for e in await _fetch_employees(request) if e.get("pay_frequency") == body.pay_frequency]
    # D4: one legal entity (body / entity picker) or every entity.
    _view_entity = (body.entity_id or "").strip() or ES.request_entity(request)
    emps = await ES.filter_employees(emps, org, _view_entity)
    if body.employee_ids:            # run a selected subset, not the whole frequency
        _sel = set(body.employee_ids)
        emps = [e for e in emps if e["id"] in _sel]
    comp_map = await _fetch_pay_components(request)
    periods = _resolve_periods(body.pay_frequency, body.periods_in_year)
    _p_end = _parse_date(body.period_end) or date(body.year, 12, 31)
    # Stream B hook: salary revisions that are due are applied, salary history is attached.
    emps, body.entries, _rev_notes = await revisions_hook.before_payroll(
        request, db, emps, _parse_date(body.period_start), _parse_date(body.period_end), body.entries)
    _voided_n = 0
    if body.salary_basis == "as_voided":     # re-run of a voided run at the rates it paid
        emps, _voided_n = _apply_voided_rates(emps, await _voided_rates(db, org, _paygroup_id(org, body.pay_frequency), _p_end, periods))
    # Date rules: payable days + reason per employee; who is left out and why.
    _dr_end = _parse_date(body.period_end)
    _dr_start = _parse_date(body.period_start) or (
        date(_dr_end.year, _dr_end.month, 1) if (_dr_end and body.pay_frequency == "monthly") else None)
    emps, body.entries, _excluded, _date_info = await exit_pay.decide_run(
        db, org, emps, body.entries, _dr_start, _dr_end,
        selected=set(body.employee_ids or []), headers=_fwd(request))
    priors = await _latest_prior_stub(db, org, [uuid.UUID(e["id"]) for e in emps])
    lines, tg, tn, tcpp, tcpp2, tei, ttax, er = [], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    n_err = n_warn = 0
    for e in emps:
        name = f"{e['first_name']} {e['last_name']}"
        _payable = exit_pay.payable_summary(((body.entries or {}).get(e["id"]) or {}).get("payable"))
        try:
            ytd = await _load_ytd(db, org, e["id"], body.year)
            prov, r, _ = _compute_for(e, body.pay_frequency, body.year, ytd, body.entries, comp_map, periods)
        except ValueError as ex:
            lines.append({"employee_id": e["id"], "name": name, "employee_number": e.get("employee_number"),
                          "province": e.get("province_of_employment"), "gross": 0, "cpp": 0, "cpp2": 0,
                          "ei": 0, "income_tax": 0, "net": 0, "payable": _payable,
                          "flags": [{"level": "error", "code": "calc_error", "message": str(ex)}]})
            n_err += 1
            continue
        d, ec = r["deductions"], r["employer_cost"]
        flags = _anomalies(r, priors.get(e["id"])) + _payable_flags(_payable)
        n_err += sum(1 for f in flags if f["level"] == "error")
        n_warn += sum(1 for f in flags if f["level"] == "warn")
        lines.append({"employee_id": e["id"], "name": name, "payable": _payable,
                      "employee_number": e["employee_number"], "province": prov, "gross": r["gross"],
                      "cpp": d["cpp"], "cpp2": d["cpp2"], "ei": d["ei"], "income_tax": d["income_tax"],
                      "net": r["net_pay"], "flags": flags})
        tg += r["gross"]; tn += r["net_pay"]; tcpp += d["cpp"]; tcpp2 += d["cpp2"]
        tei += d["ei"]; ttax += d["income_tax"]; er += ec["cpp"] + ec.get("cpp2", 0) + ec["ei"]
    return {"pay_frequency": body.pay_frequency, "periods_per_year": periods,
            "employee_count": len(lines), "lines": lines, "entity_id": _view_entity or None,
            # Date rules: employees NOT paid in this period, each with a plain reason.
            "excluded": [{"employee_id": x["employee_id"], "name": x["name"], "employee_number": x["employee_number"],
                          "reason": x["payable"].get("reason"), "reason_code": x["payable"].get("reason_code")}
                         for x in _excluded],
            "date_rules": _date_info,
            "salary_basis": {"basis": body.salary_basis, "employees_at_voided_rates": _voided_n},
            "anomalies": {"errors": n_err, "warnings": n_warn},
            "totals": {"gross": round(tg, 2), "net": round(tn, 2), "cpp": round(tcpp, 2),
                       "cpp2": round(tcpp2, 2), "ei": round(tei, 2), "income_tax": round(ttax, 2),
                       "employer_cost": round(er, 2),
                       "cra_remittance": round(ttax + tcpp * 2 + tcpp2 * 2 + tei + er - tei, 2)}}


class RunRequest(BaseModel):
    pay_frequency: str = "biweekly"
    period_start: str
    period_end: str
    pay_date: str
    year: int = 2026
    # optional per-employee extra components: {employee_id: {earnings:[{code,amount}], deductions:[...]}}
    entries: dict | None = None
    # optional subset — run only these employees (multi-select / individual).
    # None = the whole frequency, as before.
    employee_ids: list[str] | None = None
    acknowledge_unverified: bool = False   # run despite the open compliance gate (TEST)
    # Warning-level anomalies (gross / net swing, no income tax on a sizeable gross …) must be acknowledged
    # before the run commits; ERROR-level anomalies (negative net, tax > gross) always refuse.
    acknowledge_warnings: bool = False
    # "as_voided": re-run a voided TEST run at the salary rates it paid, ignoring salary history
    # applied after it (its queued arrears stay as they are); "current" = today's salary / history.
    salary_basis: str = "current"
    periods_in_year: int | None = None     # 27-pay biweekly / 53-pay weekly year
    # D4: run ONE legal entity (its employees only; the run is stamped with it) or, when blank,
    # every entity — each stub still carries its employee's entity.
    entity_id: str | None = None


def _ytd_from_row(row) -> dict:
    """EmployeeYtd row → the dict the engines read (CPP/EI caps, bonus stacking, YTD gross)."""
    if not row:
        return {"cpp": 0.0, "cpp2": 0.0, "ei": 0.0, "pensionable": 0.0, "insurable": 0.0,
                "bonus": 0.0, "income_tax": 0.0, "gross": 0.0, "qpip": 0.0}
    return {"cpp": float(row.cpp), "cpp2": float(row.cpp2), "ei": float(row.ei),
            "pensionable": float(row.pensionable), "insurable": float(row.insurable),
            "bonus": float(row.bonus or 0), "income_tax": float(row.income_tax or 0),
            "qpip": float(getattr(row, "qpip", 0) or 0),
            "gross": float(row.gross or 0)}


async def _load_ytd(db, org, emp_id, year) -> dict:
    row = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.employee_id == emp_id,
        EmployeeYtd.year == year))).scalar_one_or_none()
    return _ytd_from_row(row)


@router.post("/runs", status_code=201)
async def execute_run(body: RunRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Execute + PERSIST a pay run for a frequency. Computes each employee by
    their own YTD, writes PayRun + PayStub rows, and rolls YTD forward."""
    org = _org(request)
    _require_pay_office(request, "run payroll")
    gate = _require_gate(body.acknowledge_unverified)   # blocks a real run while unverified
    try:
        stat_tables(body.year)          # fail cleanly (400) if no statutory edition for the year
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if body.pay_frequency not in PAY_FREQUENCIES:
        raise HTTPException(400, f"pay_frequency must be one of {PAY_FREQUENCIES}")
    try:
        p_start = date.fromisoformat(body.period_start)
        p_end = date.fromisoformat(body.period_end)
        p_pay = date.fromisoformat(body.pay_date)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")

    pg_id = _paygroup_id(org, body.pay_frequency)
    # D4: which legal entity this run is for ("" = all). One run per (frequency, period, entity);
    # an all-entities run and a single-entity run for the same period may not both exist.
    _run_entity = (body.entity_id or "").strip() or ES.request_entity(request)
    _ents = await ES.entities(org)
    if _run_entity and _run_entity not in _ents:
        _ents = await ES.entities(org, fresh=True)          # created seconds ago (per-process cache)
    if _run_entity and _run_entity not in _ents:
        raise HTTPException(404, "Legal entity not found in this organization")
    _dup_q = select(PayRun).where(PayRun.org_id == org, PayRun.pay_group_id == pg_id, PayRun.period_end == p_end,
                                  PayRun.status != "void")          # a voided TEST run may be re-run
    if _run_entity:
        _dup_q = _dup_q.where(or_(PayRun.entity_id == uuid.UUID(_run_entity), PayRun.entity_id.is_(None)))
    dup = (await db.execute(_dup_q)).scalars().first()
    if dup:
        raise HTTPException(409, f"A {body.pay_frequency} run for period ending {p_end} already exists"
                                 + (f" for {ES.entity_label(_ents, dup.entity_id)}" if dup.entity_id else ""))

    # Candidates = everyone on the frequency who COULD be paid for this period. The date
    # rules (exit_pay.decide_run, below — after LOP is known) then drop joiners after the
    # period / onboarding / leavers before it / employees whose salary is in a settlement,
    # and pro-rate the rest. The cheap pre-filter keeps the compliance gates honest.
    emps = [e for e in await _fetch_employees(request)
            if e.get("pay_frequency") == body.pay_frequency
            and PD.payable_days(period_start=p_start, period_end=p_end, hire_date=e.get("hire_date"),
                                last_working_day=e.get("termination_date"), status=e.get("status") or "active")["paid"]]
    emps = await ES.filter_employees(emps, org, _run_entity)
    _ent_dflt = await ES.default_id(org)
    if body.employee_ids:            # run a selected subset, not the whole frequency
        _sel = set(body.employee_ids)
        emps = [e for e in emps if e["id"] in _sel]
    if not emps:
        raise HTTPException(400, f"No employees to pay on the {body.pay_frequency} frequency for this period")

    # If any employee is in Quebec, enforce the QC gate too (ROC gating unchanged).
    has_qc = any(e.get("province_of_employment") == "QC" for e in emps)
    qc_gate = _require_qc_gate(body.acknowledge_unverified) if has_qc else None
    # Enforce each distinct EXTENDED province's own gate (MB/SK/…/NU). Empty for a
    # pure ON/BC/AB/QC run, so nothing changes for existing tenants.
    prov_gates = _prov_gates_for(emps)
    for _p, _g in prov_gates.items():
        _require_prov_gate(_p, body.acknowledge_unverified)

    comp_map = await _fetch_pay_components(request)
    periods = _resolve_periods(body.pay_frequency, body.periods_in_year)
    # Stream B hook: salary revisions that are due are applied, salary history is attached.
    emps, body.entries, _rev_notes = await revisions_hook.before_payroll(
        request, db, emps, p_start, p_end, body.entries)
    if body.salary_basis not in SALARY_BASES:
        raise HTTPException(400, f"salary_basis must be one of {SALARY_BASES}")
    _voided_n = 0
    if body.salary_basis == "as_voided":     # re-run of a voided run at the rates it paid
        emps, _voided_n = _apply_voided_rates(emps, await _voided_rates(db, org, pg_id, p_end, periods))
        if not _voided_n:
            raise HTTPException(409, {"code": "PAYROLL_NO_VOIDED_RUN",
                                      "message": "salary_basis=as_voided needs a voided run for this period and frequency."})
    # Date rules: payable days per employee; employees whose salary days are carried by a
    # final-pay cheque are left out — no double pay.
    emps, body.entries, _excluded, _date_info = await exit_pay.decide_run(
        db, org, emps, body.entries, p_start, p_end,
        selected=set(body.employee_ids or []), headers=_fwd(request))
    if not emps:
        raise HTTPException(400, "Nobody is payable for this period: "
                                 + "; ".join(f"{x['name']} – {x['payable'].get('reason')}" for x in _excluded[:5]))
    run = PayRun(org_id=org, pay_group_id=pg_id, period_start=p_start, period_end=p_end,
                 pay_date=p_pay, status="calculated",
                 entity_id=uuid.UUID(_run_entity) if _run_entity else None)
    db.add(run)
    await db.flush()
    _priors = await _latest_prior_stub(db, org, [uuid.UUID(e["id"]) for e in emps])

    tot_gross = tot_net = 0.0
    _errors, _warnings = [], []
    for e in emps:
        ytd = await _load_ytd(db, org, e["id"], body.year)
        prov, r, bases = _compute_for(e, body.pay_frequency, body.year, ytd, body.entries, comp_map, periods)
        d, ec, new_ytd = r["deductions"], r["employer_cost"], r["ytd"]
        # The same checks the preview shows. A run never commits with an ERROR-level anomaly
        # (negative net, tax above gross); warnings need an explicit acknowledgement.
        _pay_sum = exit_pay.payable_summary(((body.entries or {}).get(e["id"]) or {}).get("payable"))
        for fl in _anomalies(r, _priors.get(e["id"])) + _payable_flags(_pay_sum):
            if fl["level"] in ("error", "warn"):
                (_errors if fl["level"] == "error" else _warnings).append(
                    {"employee_id": e["id"], "employee_number": e.get("employee_number"),
                     "name": f"{e['first_name']} {e['last_name']}", "code": fl["code"], "message": fl["message"]})
        _e_ent = ES._uuid(e.get("entity_id")) or _ent_dflt        # D4: snapshot on the stub

        # Real worked hours from a Time & Attendance feed (if the run carried them) —
        # makes ROE block-15A insurable hours real instead of a synthetic estimate.
        _entry = (body.entries or {}).get(e["id"]) or {}
        _worked = round(sum(float(it.get("hours") or 0) for it in _entry.get("earnings", [])
                            if it.get("code") in ("REG", "OT", "OT2", "STAT")), 2)
        # ESA vacation accrual: % of vacationable earnings this pay; VACP earnings are payout.
        _vac_pct = float(e.get("vacation_percent") or 0)
        _vacationable = _vacationable_earnings(e, body.pay_frequency, _entry, comp_map)
        _vac_accr = round(_vacationable * _vac_pct / 100, 2)
        _vac_paid = round(sum(float(it.get("amount") or 0) for it in _entry.get("earnings", [])
                              if it.get("code") == "VACP"), 2)
        detail = {
            "employee": {"id": e["id"], "name": f"{e['first_name']} {e['last_name']}",
                         "employee_number": e["employee_number"], "province": prov,
                         "pay_frequency": body.pay_frequency, "pay_type": e.get("pay_type"),
                         "department": e.get("department"), "job_title": e.get("job_title"),
                         "bank_account_last4": e.get("bank_account_last4"),
                         "entity_id": _e_ent, "entity_name": ES.entity_label(_ents, _e_ent) or None},
            "period": {"start": body.period_start, "end": body.period_end, "pay_date": body.pay_date},
            "gross": r["gross"], "bases": r.get("bases"), "entries": (body.entries or {}).get(e["id"]),
            # payable days, basis and reason — printed on the payslip; `review` = lines to check
            "payable": exit_pay.payable_summary(_entry.get("payable")),
            "worked_hours": _worked or None,
            "vacation": {"percent": _vac_pct, "vacationable": _vacationable,
                         "accrued": _vac_accr, "paid": _vac_paid},
            "compliance": _stamp_for(prov, gate, qc_gate, prov_gates),
            # the TD1 claim amounts this run used (the HR Desk explains a tax change after a new TD1)
            "td1": {"federal": e.get("td1_federal_claim"), "provincial": e.get("td1_provincial_claim")},
            "deductions": d, "tax_detail": r.get("tax_detail"),
            "employer_cost": ec, "net_pay": r["net_pay"], "ytd": new_ytd,
        }
        db.add(PayStub(org_id=org, pay_run_id=run.id, employee_id=uuid.UUID(e["id"]),
                       gross=r["gross"], cpp=d["cpp"], cpp2=d["cpp2"], ei=d["ei"],
                       income_tax=d["income_tax"], net_pay=r["net_pay"], detail=detail,
                       entity_id=uuid.UUID(_e_ent) if _e_ent else None))

        row = (await db.execute(select(EmployeeYtd).where(
            EmployeeYtd.org_id == org, EmployeeYtd.employee_id == e["id"],
            EmployeeYtd.year == body.year))).scalar_one_or_none()
        if not row:
            row = EmployeeYtd(org_id=org, employee_id=uuid.UUID(e["id"]), year=body.year)
            db.add(row)
        row.cpp = new_ytd["cpp"]; row.cpp2 = new_ytd["cpp2"]; row.ei = new_ytd["ei"]
        if "qpip" in new_ytd:
            row.qpip = new_ytd["qpip"]
        row.pensionable = new_ytd["pensionable"]; row.insurable = new_ytd["insurable"]
        row.gross = float(row.gross or 0) + r["gross"]
        row.income_tax = float(row.income_tax or 0) + d["income_tax"]
        row.bonus = float(row.bonus or 0) + r["tax_detail"].get("bonus_taxable", 0)
        row.vac_accrued = round(float(row.vac_accrued or 0) + _vac_accr, 2)
        row.vac_paid = round(float(row.vac_paid or 0) + _vac_paid, 2)

        tot_gross += r["gross"]; tot_net += r["net_pay"]

    if _errors:
        await db.rollback()           # nothing of this run (stubs, YTD, carried feed rows) is kept
        raise HTTPException(status_code=422, detail={
            "code": "PAYROLL_RUN_HAS_ERRORS",
            "message": f"The run was not committed: {len(_errors)} error-level anomal{'y' if len(_errors) == 1 else 'ies'} "
                       "(negative net pay / tax above gross). Fix the entries or leave those employees out of this run.",
            "errors": _errors, "warnings": _warnings})
    if _warnings and not body.acknowledge_warnings:
        await db.rollback()
        raise HTTPException(status_code=422, detail={
            "code": "PAYROLL_RUN_HAS_WARNINGS",
            "message": f"The run was not committed: {len(_warnings)} warning{'s' if len(_warnings) != 1 else ''} need "
                       "a look (pay swings, no income tax on a sizeable gross …). Review them and send acknowledge_warnings=true to commit.",
            "errors": [], "warnings": _warnings})

    run.total_gross = round(tot_gross, 2)
    run.total_net = round(tot_net, 2)
    if _voided_n:
        run.meta = {**(run.meta or {}), "salary_basis": {"basis": "as_voided", "employees_at_voided_rates": _voided_n}}
    if _warnings:
        run.meta = {**(run.meta or {}), "warnings_acknowledged": {"count": len(_warnings),
                    "by": request.headers.get("x-user-email") or request.headers.get("x-user-id"),
                    "codes": sorted({w["code"] for w in _warnings})}}
    # D4: per-entity roll-up on the run (which group company paid whom).
    _by_ent: dict = {}
    for e in emps:
        _k = ES._uuid(e.get("entity_id")) or _ent_dflt
        _by_ent[_k] = _by_ent.get(_k, 0) + 1
    run.meta = {**(run.meta or {}), "entity_id": _run_entity or None,
                "entity_name": ES.entity_label(_ents, _run_entity) if _run_entity else None,
                "entities": [{"entity_id": k, "name": ES.entity_label(_ents, k) or None,
                              "short_code": (_ents.get(k) or {}).get("short_code") if k else None, "employees": n}
                             for k, n in sorted(_by_ent.items(), key=lambda kv: ES.entity_label(_ents, kv[0]))]}
    # Date rules on the run: the basis used and who was left out (with the reason).
    run.meta = {**(run.meta or {}), "date_rules": {
        **_date_info,
        "prorated": sum(1 for e in emps if (((body.entries or {}).get(e["id"]) or {}).get("payable") or {}).get("prorated")),
        "excluded": [{"employee_id": x["employee_id"], "name": x["name"], "reason": x["payable"].get("reason"),
                      "reason_code": x["payable"].get("reason_code")} for x in _excluded]}}
    await db.commit()
    await db.refresh(run)
    try:                                                   # G3: outbound webhooks (counts and dates only, no amounts)
        from shared.utils.events import emit as _g3_emit
        await _g3_emit(org, "payroll.run.completed", {"run_id": str(run.id), "pay_frequency": body.pay_frequency,
                                                      "period_start": body.period_start, "period_end": body.period_end,
                                                      "pay_date": body.pay_date, "employees": len(emps)})
    except Exception:  # noqa: BLE001
        pass
    # Run-level compliance = the STRICTEST gate the run actually touched: test-mode if
    # ANY enforced gate (ROC / QC / extended province) was open + acknowledged.
    run_open = (gate["gate_open"] or bool(qc_gate and qc_gate["gate_open"])
                or any(g["gate_open"] for g in prov_gates.values()))
    gate = {"verified": not run_open, "mode": "test" if run_open else "live",
            "qc_included": has_qc,
            "extended_provinces": list(prov_gates.keys())}
    # create → audit_change (masked field diff; S4 audit trail) — runs are committed at
    # creation (status "calculated"); there is no separate approve / finalise endpoint.
    await audit_change(request, "payroll.run.execute", entity_type="pay_run", entity_id=str(run.id),
                       before=None,
                       after={"status": run.status, "pay_frequency": body.pay_frequency,
                              "period_start": body.period_start, "period_end": body.period_end,
                              "pay_date": body.pay_date, "employee_count": len(emps),
                              "total_gross": run.total_gross, "total_net": run.total_net},
                       summary=f"Ran {body.pay_frequency} payroll · {len(emps)} employees · net {run.total_net}",
                       extra={"period_end": body.period_end, "pay_date": body.pay_date,
                              "total_gross": run.total_gross, "total_net": run.total_net,
                              "compliance_mode": gate.get("mode"), "verified": gate.get("verified"),
                              "qc_included": has_qc})
    return {"id": str(run.id), "pay_frequency": body.pay_frequency, "status": run.status,
            "period_start": body.period_start, "period_end": body.period_end, "pay_date": body.pay_date,
            "employee_count": len(emps), "total_gross": run.total_gross, "total_net": run.total_net,
            "entity_id": _run_entity or None, "entity_name": (run.meta or {}).get("entity_name"),
            "entities": (run.meta or {}).get("entities"),
            "compliance": gate, "meta": run.meta}


def _offcycle_pg(org: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{org}:paygroup:offcycle")


class OffCycleRequest(BaseModel):
    employee_id: str
    pay_date: str
    year: int = 2026
    earnings: list[dict] = []      # [{code, amount}]
    deductions: list[dict] = []
    acknowledge_unverified: bool = False


@router.post("/off-cycle", status_code=201)
async def off_cycle_run(body: OffCycleRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """One-off cheque for a SINGLE employee outside the regular calendar (bonus,
    correction, final pay). No base salary — the amount comes entirely from the
    entered components. Taxed by the employee's frequency (bonus method applies to
    non-periodic codes) and rolls YTD forward like any run."""
    org = _org(request)
    _require_pay_office(request, "run an off-cycle cheque")
    gate = _require_gate(body.acknowledge_unverified)   # blocks a real cheque while unverified
    try:
        stat_tables(body.year)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    try:
        pay = date.fromisoformat(body.pay_date)
    except ValueError:
        raise HTTPException(400, "pay_date must be YYYY-MM-DD")
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(body.employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    if not body.earnings and not body.deductions:
        raise HTTPException(400, "Add at least one earning or deduction")

    prov = e.get("province_of_employment", "ON")
    # A QC off-cycle cheque enforces the QC gate too (ROC gating unchanged above).
    qc_gate = _require_qc_gate(body.acknowledge_unverified) if prov == "QC" else None
    # An extended-province off-cycle cheque enforces that province's own gate.
    prov_gate = (_require_prov_gate(prov, body.acknowledge_unverified)
                 if prov.upper() in EXTENDED_PROVINCES else None)

    comp_map = await _fetch_pay_components(request)
    freq = e.get("pay_frequency", "biweekly")
    ytd = await _load_ytd(db, org, e["id"], body.year)
    bases = _resolve_bases(0.0, {"earnings": body.earnings, "deductions": body.deductions}, comp_map)
    # a bonus-only cheque stacks on the employee's regular projected annual income
    reg_annual = round(_period_gross(e, freq) * periods_per_year(freq), 2)
    if prov == "QC":
        # QC engine — bonus method on federal (abated) + Québec tax; QC has no surtax/OHP.
        r = calculate_paystub_qc(pay_frequency=freq, ytd=ytd, year=body.year,
                                 prior_bonus_ytd=float(ytd.get("bonus", 0) or 0), bonus_base_income=reg_annual,
                                 **bases, **_employee_tax_kwargs_qc(e))
    else:
        r = calculate_paystub_components(
            pay_frequency=freq, province=prov, ytd=ytd,
            year=body.year, prior_bonus_ytd=float(ytd.get("bonus", 0) or 0), bonus_base_income=reg_annual,
            **bases, **_employee_tax_kwargs(e))
    d, ec, new_ytd = r["deductions"], r["employer_cost"], r["ytd"]

    _oc_ent = await ES.entity_of(e, org)                    # D4: the employee's legal entity
    _oc_ents = await ES.entities(org)
    run = PayRun(org_id=org, pay_group_id=_offcycle_pg(org), period_start=pay, period_end=pay,
                 pay_date=pay, status="calculated", total_gross=r["gross"], total_net=r["net_pay"],
                 entity_id=uuid.UUID(_oc_ent) if _oc_ent else None)
    db.add(run)
    await db.flush()
    detail = {
        "employee": {"id": e["id"], "name": f"{e['first_name']} {e['last_name']}",
                     "employee_number": e["employee_number"], "province": e.get("province_of_employment"),
                     "pay_frequency": freq, "department": e.get("department"), "job_title": e.get("job_title"),
                     "entity_id": _oc_ent, "entity_name": ES.entity_label(_oc_ents, _oc_ent) or None},
        "period": {"start": body.pay_date, "end": body.pay_date, "pay_date": body.pay_date},
        "off_cycle": True, "gross": r["gross"], "bases": r.get("bases"),
        "entries": {"earnings": body.earnings, "deductions": body.deductions},
        "compliance": _stamp_for(prov, gate, qc_gate, {prov.upper(): prov_gate} if prov_gate else {}),
        "deductions": d, "tax_detail": r.get("tax_detail"), "employer_cost": ec,
        "net_pay": r["net_pay"], "ytd": new_ytd,
    }
    db.add(PayStub(org_id=org, pay_run_id=run.id, employee_id=uuid.UUID(e["id"]),
                   gross=r["gross"], cpp=d["cpp"], cpp2=d["cpp2"], ei=d["ei"],
                   income_tax=d["income_tax"], net_pay=r["net_pay"], detail=detail,
                   entity_id=uuid.UUID(_oc_ent) if _oc_ent else None))

    row = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.employee_id == e["id"],
        EmployeeYtd.year == body.year))).scalar_one_or_none()
    if not row:
        row = EmployeeYtd(org_id=org, employee_id=uuid.UUID(e["id"]), year=body.year)
        db.add(row)
    row.cpp = new_ytd["cpp"]; row.cpp2 = new_ytd["cpp2"]; row.ei = new_ytd["ei"]
    if "qpip" in new_ytd:
        row.qpip = new_ytd["qpip"]
    row.pensionable = new_ytd["pensionable"]; row.insurable = new_ytd["insurable"]
    row.gross = float(row.gross or 0) + r["gross"]
    row.income_tax = float(row.income_tax or 0) + d["income_tax"]
    row.bonus = float(row.bonus or 0) + r["tax_detail"].get("bonus_taxable", 0)
    # Off-cycle vacation: no base salary here, so accrue only on the off-cycle's own
    # vacationable earnings; VACP is a payout (drawdown of the accrued balance).
    _oc_vacationable = round(sum(float(it.get("amount") or 0) for it in body.earnings
                                 if (comp_map.get(it.get("code")) or {}).get("vacationable")), 2)
    _oc_vac_accr = round(_oc_vacationable * float(e.get("vacation_percent") or 0) / 100, 2)
    _oc_vac_paid = round(sum(float(it.get("amount") or 0) for it in body.earnings if it.get("code") == "VACP"), 2)
    row.vac_accrued = round(float(row.vac_accrued or 0) + _oc_vac_accr, 2)
    row.vac_paid = round(float(row.vac_paid or 0) + _oc_vac_paid, 2)
    detail["vacation"] = {"percent": float(e.get("vacation_percent") or 0), "accrued": _oc_vac_accr, "paid": _oc_vac_paid}
    await db.commit()
    await db.refresh(run)
    gate = _stamp_for(prov, gate, qc_gate, {prov.upper(): prov_gate} if prov_gate else {})
    await audit_change(request, "payroll.offcycle.execute", entity_type="pay_run", entity_id=str(run.id),
                       before=None,
                       after={"status": run.status, "pay_date": body.pay_date, "employee_id": e.get("id"),
                              "gross": r["gross"], "net_pay": r["net_pay"]},
                       summary=f"Off-cycle pay · {detail['employee']['name']} · net {r['net_pay']}",
                       extra={"pay_date": body.pay_date, "gross": r["gross"], "net_pay": r["net_pay"],
                             "compliance_mode": gate.get("mode"), "verified": gate.get("verified"),
                             "province": prov,
                             "qc_verified": (not qc_gate["gate_open"]) if qc_gate else None})
    return {"id": str(run.id), "employee": detail["employee"]["name"], "pay_date": body.pay_date,
            "gross": r["gross"], "net_pay": r["net_pay"], "deductions": d, "tax_detail": r.get("tax_detail"),
            "compliance": gate}


@router.get("/off-cycle")
async def list_off_cycle(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    _require_pay_office(request, "see the off-cycle cheques")
    rows = (await db.execute(select(PayRun).where(
        PayRun.org_id == org, PayRun.pay_group_id == _offcycle_pg(org))
        .order_by(PayRun.pay_date.desc()))).scalars().all()
    out = []
    for run in rows:
        s = (await db.execute(select(PayStub).where(PayStub.pay_run_id == run.id))).scalars().first()
        emp = (s.detail.get("employee", {}) if s and s.detail else {})
        out.append({"id": str(run.id), "pay_date": run.pay_date.isoformat(),
                    "employee": emp.get("name"), "employee_number": emp.get("employee_number"),
                    "gross": float(run.total_gross), "net_pay": float(run.total_net),
                    "stub_id": str(s.id) if s else None})
    return out


@router.get("/runs")
async def list_runs(request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    _require_pay_office(request, "see the pay runs")
    q = select(PayRun).where(PayRun.org_id == org)
    # D4: entity view — the entity's own runs plus all-entities runs (which contain its stubs).
    view = ES.request_entity(request)
    if view:
        q = q.where(or_(PayRun.entity_id == uuid.UUID(view), PayRun.entity_id.is_(None)))
    rows = (await db.execute(q.order_by(PayRun.pay_date.desc()))).scalars().all()
    ents = await ES.entities(org)
    out = []
    for r in rows:
        stubs = (await db.execute(select(PayStub).where(PayStub.pay_run_id == r.id))).scalars().all()
        if view and r.entity_id is None:
            stubs = [s for s in stubs if str(s.entity_id or "") == view] or stubs
        freq = next((f for f in PAY_FREQUENCIES if _paygroup_id(org, f) == r.pay_group_id), "—")
        _void = (r.meta or {}).get("void") or {}
        out.append({"id": str(r.id), "pay_frequency": freq, "status": r.status,
                    "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
                    "pay_date": r.pay_date.isoformat(),
                    "employee_count": len(stubs) if r.status != "void" else int(_void.get("employees") or 0),
                    "total_gross": float(r.total_gross), "total_net": float(r.total_net),
                    "compliance_mode": _run_mode(stubs) if r.status != "void" else _void.get("mode"),
                    "void": {"reason": _void.get("reason"), "by": _void.get("by"), "at": _void.get("at")} if r.status == "void" else None,
                    "entity_id": str(r.entity_id) if r.entity_id else None,
                    "entity_name": ES.entity_label(ents, r.entity_id) if r.entity_id else ("All entities" if ents else None),
                    "entities": (r.meta or {}).get("entities")})
    return out


def _run_mode(stubs) -> str:
    """'live' when any stub of the run was stamped live, else 'test' (a run with no stubs is 'test')."""
    for s in stubs:
        if (((s.detail or {}).get("compliance") or {}).get("mode") or "test") == "live":
            return "live"
    return "test"


def _reverse_ytd_row(row, s, det: dict) -> None:
    """Take ONE stub back out of an EmployeeYtd row (the exact inverse of what execute_run
    added): gross, income tax, CPP / CPP2 (or QPP / QPP2), EI, pensionable / insurable
    (= gross), bonus (taxable one-offs), vacation accrued / paid. Sub-cent residue clamps to 0."""
    def _sub(cur, amt):
        v = round(float(cur or 0) - float(amt or 0), 2)
        return 0.0 if -0.01 < v < 0 else v
    row.gross = _sub(row.gross, s.gross)
    row.income_tax = _sub(row.income_tax, s.income_tax)
    row.cpp = _sub(row.cpp, s.cpp); row.cpp2 = _sub(row.cpp2, s.cpp2); row.ei = _sub(row.ei, s.ei)
    row.pensionable = _sub(row.pensionable, s.gross); row.insurable = _sub(row.insurable, s.gross)
    row.bonus = _sub(row.bonus, (det.get("tax_detail") or {}).get("bonus_taxable", 0))
    row.vac_accrued = _sub(row.vac_accrued, (det.get("vacation") or {}).get("accrued", 0))
    row.vac_paid = _sub(row.vac_paid, (det.get("vacation") or {}).get("paid", 0))
    if getattr(row, "qpip", None) is not None:
        row.qpip = _sub(row.qpip, (det.get("deductions") or {}).get("qpip", 0))


class VoidRunBody(BaseModel):
    reason: str


@router.post("/runs/{run_id}/void")
async def void_run(run_id: str, body: VoidRunBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Void a TEST run so the month can be run again (never a LIVE run — those are filed and
    paid; use an off-cycle correction). Reverses the employee YTD ledger (gross, income tax,
    CPP / CPP2, EI, pensionable / insurable, bonus, vacation), archives the stubs
    (pay_stubs_voided) and removes them so every reader (payslips, T4, remittances, statements)
    stops seeing the run. Only the LATEST non-void run of the
    pay group may be voided (void later months first), so the YTD stays consistent."""
    org = _org(request)
    actor = _require_payroll_admin(request)
    reason = (body.reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(422, {"code": "PAYROLL_VOID_REASON_REQUIRED", "message": "Give a reason for voiding this run (at least 3 characters)."})
    run = (await db.execute(select(PayRun).where(PayRun.id == run_id, PayRun.org_id == org))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Pay run not found")
    if run.status == "void":
        raise HTTPException(409, {"code": "PAYROLL_RUN_ALREADY_VOID", "message": "This run is already void."})
    stubs = (await db.execute(select(PayStub).where(PayStub.pay_run_id == run.id, PayStub.org_id == org))).scalars().all()
    mode = _run_mode(stubs)
    if mode == "live":
        raise HTTPException(409, {"code": "PAYROLL_RUN_LIVE",
                                  "message": "Only a TEST run can be voided. This run was committed in LIVE mode — "
                                             "correct it with an off-cycle adjustment."})
    later = (await db.execute(select(PayRun).where(
        PayRun.org_id == org, PayRun.pay_group_id == run.pay_group_id, PayRun.status != "void",
        PayRun.period_end > run.period_end).order_by(PayRun.period_end))).scalars().all()
    if later:
        raise HTTPException(409, {"code": "PAYROLL_RUN_NOT_LATEST",
                                  "message": "Void the later run(s) first so the year-to-date ledger unwinds in order: "
                                             + ", ".join(x.period_end.isoformat() for x in later),
                                  "later_runs": [{"id": str(x.id), "period_end": x.period_end.isoformat()} for x in later]})
    # YTD ledger rows are kept by calendar year (the T4 year).
    years = set()
    reversed_rows = 0
    for s in stubs:
        det = s.detail or {}
        year = int(det.get("ytd_year") or run.period_end.year)
        years.add(year)
        row = (await db.execute(select(EmployeeYtd).where(
            EmployeeYtd.org_id == org, EmployeeYtd.employee_id == s.employee_id, EmployeeYtd.year == year))).scalar_one_or_none()
        if row:
            _reverse_ytd_row(row, s, det)
            reversed_rows += 1
        db.add(VoidedPayStub(org_id=org, pay_run_id=run.id, stub_id=s.id, employee_id=s.employee_id, gross=s.gross,
                             cpp=s.cpp, cpp2=s.cpp2, ei=s.ei, income_tax=s.income_tax, net_pay=s.net_pay, detail=det,
                             entity_id=s.entity_id, voided_by=actor, reason=reason))
        await db.delete(s)
    run.status = "void"
    run.meta = {**(run.meta or {}), "void": {
        "reason": reason, "by": actor, "at": datetime.now(timezone.utc).isoformat(), "mode": mode,
        "employees": len(stubs), "total_gross": float(run.total_gross), "total_net": float(run.total_net),
        "ytd_rows_reversed": reversed_rows, "ytd_years": sorted(years)}}
    await db.commit()
    await audit_change(request, "payroll.run.void", entity_type="pay_run", entity_id=str(run.id),
                       before={"status": "calculated", "total_gross": float(run.total_gross), "total_net": float(run.total_net)},
                       after={"status": "void", "reason": reason},
                       summary=f"Voided TEST run {run.period_start}→{run.period_end} · {len(stubs)} employees · {reason}",
                       extra={"period_end": run.period_end.isoformat(), "employees": len(stubs),
                              "ytd_rows_reversed": reversed_rows, "compliance_mode": mode})
    return {"id": str(run.id), "status": run.status, "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(), "void": run.meta["void"], "warnings": []}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    _require_pay_office(request, "see a pay run")
    run = (await db.execute(select(PayRun).where(
        PayRun.id == run_id, PayRun.org_id == org))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Pay run not found")
    stubs = (await db.execute(select(PayStub).where(PayStub.pay_run_id == run.id))).scalars().all()
    freq = next((f for f in PAY_FREQUENCIES if _paygroup_id(org, f) == run.pay_group_id), "—")
    ents = await ES.entities(org)
    _void = (run.meta or {}).get("void") or {}
    _later = (await db.execute(select(PayRun.id).where(
        PayRun.org_id == org, PayRun.pay_group_id == run.pay_group_id, PayRun.status != "void",
        PayRun.period_end > run.period_end))).first() if run.status != "void" else None
    return {
        "id": str(run.id), "pay_frequency": freq, "status": run.status,
        "compliance_mode": _run_mode(stubs) if run.status != "void" else _void.get("mode"),
        "void": _void or None,
        "can_void": bool(run.status != "void" and _run_mode(stubs) == "test" and not _later),
        "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
        "pay_date": run.pay_date.isoformat(), "total_gross": float(run.total_gross),
        "total_net": float(run.total_net), "employee_count": len(stubs), "meta": run.meta,
        "entity_id": str(run.entity_id) if run.entity_id else None,
        "entity_name": ES.entity_label(ents, run.entity_id) if run.entity_id else None,
        "entities": await ES.summary(org, stubs, ents) if ents else [],
        "stubs": [{"stub_id": str(s.id), "employee_id": str(s.employee_id),
                   "name": s.detail.get("employee", {}).get("name"),
                   "employee_number": s.detail.get("employee", {}).get("employee_number"),
                   "entity_id": str(s.entity_id) if s.entity_id else None,
                   "entity_name": ES.entity_label(ents, s.entity_id) if s.entity_id else None,
                   "gross": float(s.gross), "cpp": float(s.cpp), "cpp2": float(s.cpp2),
                   "ei": float(s.ei), "income_tax": float(s.income_tax), "net_pay": float(s.net_pay)}
                  for s in stubs],
    }


# ═══ Reports: payroll register + CRA remittance ═════════════════════════════
async def _run_or_404(db, org, run_id):
    run = (await db.execute(select(PayRun).where(
        PayRun.id == run_id, PayRun.org_id == org))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Pay run not found")
    stubs = (await db.execute(select(PayStub).where(PayStub.pay_run_id == run.id))).scalars().all()
    return run, stubs


@router.get("/runs/{run_id}/register")
async def payroll_register(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Payroll register — per-employee earnings/deductions/employer cost for a run,
    with totals and a by-department roll-up for the GL journal."""
    org = _org(request)
    _require_pay_office(request, "see the payroll register")
    run, stubs = await _run_or_404(db, org, run_id)
    freq = next((f for f in PAY_FREQUENCIES if _paygroup_id(org, f) == run.pay_group_id), "—")
    rows, depts = [], {}
    tot = {k: 0.0 for k in ("gross", "cpp", "cpp2", "ei", "income_tax", "pre_tax",
                            "after_tax", "net", "er_cpp", "er_cpp2", "er_ei")}
    for s in stubs:
        det = s.detail or {}
        emp, d, ec = det.get("employee", {}), det.get("deductions", {}), det.get("employer_cost", {})
        dept = emp.get("department") or "—"
        row = {"employee_id": str(s.employee_id), "name": emp.get("name"),
               "employee_number": emp.get("employee_number"), "department": dept,
               "gross": float(s.gross), "cpp": float(s.cpp), "cpp2": float(s.cpp2),
               "ei": float(s.ei), "income_tax": float(s.income_tax),
               "pre_tax": float(d.get("pre_tax", 0) or 0), "after_tax": float(d.get("after_tax", 0) or 0),
               "net": float(s.net_pay), "er_cpp": float(ec.get("cpp", 0) or 0),
               "er_cpp2": float(ec.get("cpp2", 0) or 0), "er_ei": float(ec.get("ei", 0) or 0)}
        rows.append(row)
        for k in tot:
            tot[k] += row[k]
        dd = depts.setdefault(dept, {"department": dept, "employees": 0, "gross": 0.0, "net": 0.0, "employer_cost": 0.0})
        dd["employees"] += 1; dd["gross"] += row["gross"]; dd["net"] += row["net"]
        dd["employer_cost"] += row["er_cpp"] + row["er_cpp2"] + row["er_ei"]
    rows.sort(key=lambda x: (x["department"], x["name"] or ""))
    tot = {k: round(v, 2) for k, v in tot.items()}
    tot["employer_cost"] = round(tot["er_cpp"] + tot["er_cpp2"] + tot["er_ei"], 2)
    tot["total_cost"] = round(tot["gross"] + tot["employer_cost"], 2)
    by_dept = [{**v, "gross": round(v["gross"], 2), "net": round(v["net"], 2),
                "employer_cost": round(v["employer_cost"], 2)}
               for v in sorted(depts.values(), key=lambda x: x["department"])]
    return {"run_id": str(run.id), "pay_frequency": freq, "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(), "pay_date": run.pay_date.isoformat(),
            "employee_count": len(rows), "rows": rows, "totals": tot, "by_department": by_dept}


def remit_split(s) -> dict:
    """One pay stub → who is owed what. CRA gets the federal income tax, CPP / CPP2
    (employee + employer) and EI (employee + employer). For a QUÉBEC stub the QPP / QPP2
    (they sit in the cpp slots), the QPIP and the Québec income tax are owed to Revenu
    Québec instead — so they are NOT part of the CRA (PD7A) amount. Same split as the GL journal."""
    det = s.detail or {}
    d, ec = det.get("deductions") or {}, det.get("employer_cost") or {}
    prov = str((det.get("employee") or {}).get("province") or det.get("province") or "").upper()
    qc = prov == "QC" or "qpip" in d
    pen_ee = float(s.cpp) + float(s.cpp2)
    pen_er = float(ec.get("cpp", 0) or 0) + float(ec.get("cpp2", 0) or 0)
    qc_tax = float((det.get("tax_detail") or {}).get("provincial_qc", 0) or 0) if qc else 0.0
    return {"qc": qc, "tax": float(s.income_tax) - qc_tax,
            "cpp_ee": 0.0 if qc else pen_ee, "cpp_er": 0.0 if qc else pen_er,
            "ei_ee": float(s.ei), "ei_er": float(ec.get("ei", 0) or 0),
            "qc_tax": qc_tax, "qpp_ee": pen_ee if qc else 0.0, "qpp_er": pen_er if qc else 0.0,
            "qpip_ee": float(d.get("qpip", 0) or 0) if qc else 0.0,
            "qpip_er": float(ec.get("qpip", 0) or 0) if qc else 0.0}


@router.get("/runs/{run_id}/remittance")
async def remittance(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """CRA source-deduction remittance (PD7A) for a run: federal + provincial income tax
    (Québec tax excluded) + employee & employer CPP + EI. Québec employees' QPP / QPIP /
    Québec income tax are owed to Revenu Québec — shown separately under `revenu_quebec`."""
    org = _org(request)
    _require_pay_office(request, "see a run's remittance")
    run, stubs = await _run_or_404(db, org, run_id)
    inc = cpp_ee = ei_ee = er_cpp = er_ei = 0.0
    rq = {"qc_income_tax": 0.0, "qpp_employee": 0.0, "qpp_employer": 0.0, "qpip_employee": 0.0, "qpip_employer": 0.0}
    for s in stubs:
        p = remit_split(s)
        inc += p["tax"]
        cpp_ee += p["cpp_ee"]
        ei_ee += p["ei_ee"]
        er_cpp += p["cpp_er"]
        er_ei += p["ei_er"]
        rq["qc_income_tax"] += p["qc_tax"]
        rq["qpp_employee"] += p["qpp_ee"]; rq["qpp_employer"] += p["qpp_er"]
        rq["qpip_employee"] += p["qpip_ee"]; rq["qpip_employer"] += p["qpip_er"]
    total = inc + cpp_ee + er_cpp + ei_ee + er_ei
    rq = {k: round(v, 2) for k, v in rq.items()}
    rq["total_remittance"] = round(sum(rq.values()), 2)
    return {"run_id": str(run.id), "period_end": run.period_end.isoformat(),
            "pay_date": run.pay_date.isoformat(), "employee_count": len(stubs),
            "income_tax": round(inc, 2), "cpp_employee": round(cpp_ee, 2), "cpp_employer": round(er_cpp, 2),
            "cpp_total": round(cpp_ee + er_cpp, 2), "ei_employee": round(ei_ee, 2),
            "ei_employer": round(er_ei, 2), "ei_total": round(ei_ee + er_ei, 2),
            "total_remittance": round(total, 2), "revenu_quebec": rq}


# ═══ Payslips ═══════════════════════════════════════════════════════════════
@router.get("/payslips")
async def payslips(request: Request, employee_id: str | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    own = await _own_pay_only(request)      # employees and managers: own payslips only
    if own is not None:
        employee_id = own
    q = select(PayStub).where(PayStub.org_id == org)
    if employee_id:
        q = q.where(PayStub.employee_id == employee_id)
    stubs = (await db.execute(q)).scalars().all()
    out = [{"stub_id": str(s.id), "employee_id": str(s.employee_id),
            "name": s.detail.get("employee", {}).get("name"),
            "period_end": s.detail.get("period", {}).get("end"),
            "pay_date": s.detail.get("period", {}).get("pay_date"),
            "pay_frequency": s.detail.get("employee", {}).get("pay_frequency"),
            "gross": float(s.gross), "net_pay": float(s.net_pay)} for s in stubs]
    out.sort(key=lambda x: x.get("pay_date") or "", reverse=True)
    return out


@router.get("/payslips/{stub_id}")
async def payslip_detail(stub_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    s = (await db.execute(select(PayStub).where(
        PayStub.id == stub_id, PayStub.org_id == org))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Payslip not found")
    await _guard_pay(request, s.employee_id)
    return {"stub_id": str(s.id), "employee_id": str(s.employee_id), **s.detail}


@router.get("/payslips/{stub_id}/pdf")
async def payslip_pdf(stub_id: str, request: Request, org: str = "",
                      db: AsyncSession = Depends(get_db)):
    """The payslip as a real PDF (for the in-app viewer / download). `org` is the
    display name of the employer (the frontend passes it — payroll doesn't store
    the org's name); falls back to 'Lexora HRM'."""
    org_id = _org(request)
    s = (await db.execute(select(PayStub).where(
        PayStub.id == stub_id, PayStub.org_id == org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Payslip not found")
    await _guard_pay(request, s.employee_id)
    detail = {"stub_id": str(s.id), "employee_id": str(s.employee_id), **(s.detail or {})}
    from payslip_pdf import render_payslip_pdf
    # D4: the employer on the slip is the stub's LEGAL ENTITY when it has one, else the org label.
    _ent_name = ES.entity_label(await ES.entities(org_id), s.entity_id) if s.entity_id else ""
    pdf_bytes = render_payslip_pdf(detail, _ent_name or org or "Lexora HRM")
    emp_no = ((detail.get("employee") or {}).get("employee_number")) or str(s.id)[:8]
    pay_date = (detail.get("period") or {}).get("pay_date") or ""
    fname = f"payslip-{emp_no}-{pay_date}.pdf".replace(" ", "_")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})


@router.get("/employee-ledger/{employee_id}")
async def employee_ledger(employee_id: str, request: Request, year: int = 2026,
                          db: AsyncSession = Depends(get_db)):
    """Per-payslip CPP / EI / income-tax ledger for one employee in a year — the
    source records behind their YTD deduction totals. Every deduction traces back
    to a pay stub (drill-through audit trail). Ordered by pay date."""
    org = _org(request)
    await _guard_pay(request, employee_id)
    rows = (await db.execute(
        select(PayStub, PayRun.pay_date).join(PayRun, PayStub.pay_run_id == PayRun.id)
        .where(PayStub.org_id == org, PayStub.employee_id == employee_id)
        .order_by(PayRun.pay_date))).all()
    out = []
    for s, pay_date in rows:
        if pay_date.year != year:
            continue
        out.append({"stub_id": str(s.id), "pay_date": pay_date.isoformat(),
                    "gross": float(s.gross), "cpp": float(s.cpp), "cpp2": float(s.cpp2),
                    "ei": float(s.ei), "income_tax": float(s.income_tax),
                    "net_pay": float(s.net_pay)})
    return out


# ═══ YTD ledger (the T4 in progress) ════════════════════════════════════════
CAPS = {"cpp": 4230.45, "cpp2": 416.0, "ei": 1123.07}


@router.get("/ytd")
async def ytd_ledger(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    rows = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.year == year))).scalars().all()
    names = {e["id"]: e for e in await _fetch_employees(request)}
    out = []
    for r in rows:
        e = names.get(str(r.employee_id), {})
        out.append({"employee_id": str(r.employee_id), "year": r.year,
                    "name": (f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or "—"),
                    "employee_number": e.get("employee_number"),
                    "gross": float(r.gross), "income_tax": float(r.income_tax),
                    "cpp": float(r.cpp), "cpp2": float(r.cpp2), "ei": float(r.ei),
                    "qpip": float(getattr(r, "qpip", 0) or 0),
                    "pensionable": float(r.pensionable), "insurable": float(r.insurable),
                    "vac_accrued": float(r.vac_accrued or 0), "vac_paid": float(r.vac_paid or 0),
                    "vac_balance": round(float(r.vac_accrued or 0) - float(r.vac_paid or 0), 2)})
    own = await _own_pay_only(request)
    if own is not None:
        out = [x for x in out if x["employee_id"] == own]
    out.sort(key=lambda x: x["name"])
    return {"year": year, "caps": CAPS, "employees": out}


class YtdPatch(BaseModel):
    year: int = 2026
    cpp: float | None = None
    cpp2: float | None = None
    ei: float | None = None
    pensionable: float | None = None
    insurable: float | None = None
    gross: float | None = None
    income_tax: float | None = None
    qpip: float | None = None


@router.patch("/ytd/{employee_id}")
async def modify_ytd(employee_id: str, body: YtdPatch, request: Request, db: AsyncSession = Depends(get_db)):
    """Set opening / corrected YTD balances (e.g. mid-year onboarding from a
    prior provider). Only provided fields are changed."""
    org = _org(request)
    _require_pay_office(request, "change YTD balances")
    row = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.employee_id == employee_id,
        EmployeeYtd.year == body.year))).scalar_one_or_none()
    if not row:
        row = EmployeeYtd(org_id=org, employee_id=uuid.UUID(employee_id), year=body.year)
        db.add(row)
    changed = []
    for f in ("cpp", "cpp2", "ei", "pensionable", "insurable", "gross", "income_tax", "qpip"):
        v = getattr(body, f)
        if v is not None:
            setattr(row, f, v)
            changed.append(f)
    await db.commit()
    await db.refresh(row)
    await record_audit(request, "payroll.ytd.adjust", entity_type="employee_ytd", entity_id=employee_id,
                       summary=f"Adjusted {row.year} YTD balances ({', '.join(changed) or 'none'})",
                       meta={"year": row.year, "fields": changed})
    return {"employee_id": employee_id, "year": row.year, "gross": float(row.gross),
            "income_tax": float(row.income_tax), "cpp": float(row.cpp), "cpp2": float(row.cpp2),
            "ei": float(row.ei), "pensionable": float(row.pensionable), "insurable": float(row.insurable)}


# ═══ Year-end: T4 slips, ROE, remittance schedule ═══════════════════════════
T4_BOXES = [
    ("box14_employment_income", "gross"), ("box16_cpp", "cpp"), ("box16a_cpp2", "cpp2"),
    ("box18_ei", "ei"), ("box22_income_tax", "income_tax"),
]


async def _qc_tax_ytd(db, org, year: int) -> dict:
    """Per employee, the Québec income tax withheld in `year`, summed from the Québec pay stubs
    (tax_detail.provincial_qc). The YTD ledger keeps ONE income_tax total (federal + Québec);
    the T4 box 22 needs the federal part and the RL-1 box E the Québec part. Voided runs' stubs
    are deleted, so every stub found counts."""
    rows = (await db.execute(select(PayStub, PayRun.pay_date).join(PayRun, PayStub.pay_run_id == PayRun.id)
                             .where(PayStub.org_id == org))).all()
    out: dict = {}
    for s, pay_date in rows:
        if pay_date.year != year:
            continue
        p = remit_split(s)
        if p["qc"]:
            o = out.setdefault(str(s.employee_id), {"qc_tax": 0.0})
            o["qc_tax"] += p["qc_tax"]
    return out


@router.get("/t4")
async def t4_slips(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    """T4 (Statement of Remuneration Paid) generated from the YTD ledger. Box 24
    (EI insurable) / box 26 (CPP pensionable) are capped at MIE / YMPE. Extended
    boxes (20 RPP, 44 dues, …) need per-component YTD tracking — future."""
    org = _org(request)
    t = stat_tables(year)
    ympe, mie = t["cpp"]["ympe"], t["ei"]["mie"]
    rows = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.year == year))).scalars().all()
    own = await _own_pay_only(request)       # an employee gets only their own slip
    if own is not None:
        rows = [r for r in rows if str(r.employee_id) == own]
    names = {e["id"]: e for e in await _fetch_employees(request)}
    qc_split = await _qc_tax_ytd(db, org, year)
    qpip_max_ins = STAT_QC_2026["qpip"]["max_insurable"]
    slips = []
    summary = {k: 0.0 for k, _ in T4_BOXES}
    summary["box24_ei_insurable"] = summary["box26_cpp_pensionable"] = 0.0
    summary.update({"box17_qpp": 0.0, "box17a_qpp2": 0.0, "box55_ppip": 0.0, "box56_ppip_insurable": 0.0})
    for r in rows:
        e = names.get(str(r.employee_id), {})
        boxes = {k: float(getattr(r, attr)) for k, attr in T4_BOXES}
        boxes["box24_ei_insurable"] = round(min(float(r.insurable), mie), 2)
        boxes["box26_cpp_pensionable"] = round(min(float(r.pensionable), ympe), 2)
        boxes.update({"box17_qpp": 0.0, "box17a_qpp2": 0.0, "box55_ppip": 0.0, "box56_ppip_insurable": 0.0})
        qs = qc_split.get(str(r.employee_id))
        if qs or e.get("province_of_employment") == "QC":
            # Québec employment: QPP / QPP2 go in boxes 17 / 17A (not 16 / 16A), the QPIP premium in
            # box 55 (+ insurable in 56), and box 22 is the FEDERAL tax only — the Québec tax is on the RL-1.
            boxes["box17_qpp"], boxes["box17a_qpp2"] = boxes["box16_cpp"], boxes["box16a_cpp2"]
            boxes["box16_cpp"] = boxes["box16a_cpp2"] = 0.0
            boxes["box55_ppip"] = round(float(getattr(r, "qpip", 0) or 0), 2)
            boxes["box56_ppip_insurable"] = round(min(float(r.insurable), qpip_max_ins), 2)
            boxes["box22_income_tax"] = round(boxes["box22_income_tax"] - (qs or {}).get("qc_tax", 0.0), 2)
        for k in summary:
            summary[k] += boxes[k]
        slips.append({"employee_id": str(r.employee_id),
                      "name": (f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or "—"),
                      "employee_number": e.get("employee_number"),
                      "province": e.get("province_of_employment"), "boxes": boxes})
    slips.sort(key=lambda x: x["name"])
    return {"year": year, "employer": {"org_id": org}, "slip_count": len(slips),
            "slips": slips, "summary": {k: round(v, 2) for k, v in summary.items()}}


# ── RL-1 (Relevé 1) — Québec year-end slip, the QC analogue of the T4 ──
# Box → YTD-ledger column. QC runs use the QPP/QPIP engine, which stores the QPP
# contribution in the `cpp` slot (see calculate_paystub_qc), so box B reads `cpp`.
RL1_BOXES = [
    ("box_a_employment_income", "gross"), ("box_b_qpp", "cpp"),
    ("box_c_ei", "ei"), ("box_e_quebec_income_tax", "income_tax"),
]


@router.get("/rl1")
async def rl1_slips(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    """RL-1 (Relevé 1 — Revenus d'emploi) for QUÉBEC employees, generated from the
    YTD ledger — the QC analogue of /payroll/t4. Only employees whose province of
    employment is QC are included (QC runs use the QPP/QPIP engine; the QPP amount
    lives in the `cpp` YTD slot). Box G (QPP pensionable) is capped at the QPP
    maximum and box I (QPIP insurable) at the QPIP max insurable.

    The YTD `income_tax` column holds the TOTAL tax withheld for a QC employee (federal +
    Québec), so box E is summed from the Québec pay stubs instead (`_qc_tax_ytd`); the
    T4 box 22 gets the remainder (federal). Box D (RPP) has no dedicated accumulator yet → reported 0.00 with a note; box H is
    the QPIP premium YTD (employee_ytd.qpip). No figure is ever fabricated. NB: while the QC compliance gate
    is open, QC pay is TEST-mode only, so these slips are not for filing."""
    org = _org(request)
    _require_pay_office(request, "see the RL-1 slips")
    qpp_ympe = STAT_QC_2026["qpp"]["ympe"]
    qpip_max_ins = STAT_QC_2026["qpip"]["max_insurable"]
    rows = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.year == year))).scalars().all()
    names = {e["id"]: e for e in await _fetch_employees(request)}
    qc_split = await _qc_tax_ytd(db, org, year)
    slips = []
    summary = {k: 0.0 for k, _ in RL1_BOXES}
    summary["box_d_rpp"] = summary["box_g_qpp_pensionable"] = 0.0
    summary["box_h_qpip"] = summary["box_i_qpip_insurable"] = 0.0
    for r in rows:
        e = names.get(str(r.employee_id), {})
        if e.get("province_of_employment") != "QC":
            continue                                     # RL-1 is Québec-only
        boxes = {k: float(getattr(r, attr)) for k, attr in RL1_BOXES}
        qs = qc_split.get(str(r.employee_id))
        if qs:                                            # box E = the Québec income tax only (from the stubs)
            boxes["box_e_quebec_income_tax"] = round(qs["qc_tax"], 2)
        boxes["box_d_rpp"] = 0.0                          # RPP not tracked in the YTD ledger yet
        boxes["box_g_qpp_pensionable"] = round(min(float(r.pensionable), qpp_ympe), 2)
        boxes["box_h_qpip"] = round(float(getattr(r, "qpip", 0) or 0), 2)   # QPIP premiums YTD
        boxes["box_i_qpip_insurable"] = round(min(float(r.insurable), qpip_max_ins), 2)
        for k in summary:
            summary[k] += boxes[k]
        slips.append({"employee_id": str(r.employee_id),
                      "name": (f"{e.get('first_name', '')} {e.get('last_name', '')}".strip() or "—"),
                      "employee_number": e.get("employee_number"),
                      "province": e.get("province_of_employment"), "boxes": boxes})
    slips.sort(key=lambda x: x["name"])
    return {"year": year, "employer": {"org_id": org}, "slip_count": len(slips), "slips": slips,
            "summary": {k: round(v, 2) for k, v in summary.items()},
            "notes": [
                "RL-1 is generated from the shared YTD ledger (same source as the T4).",
                "Box E is the Québec income tax withheld, summed from the year's Québec pay stubs; "
                "the federal share is on the T4 (box 22), QPP in T4 box 17 and QPIP in T4 box 55.",
                "Box D (RPP) has no dedicated YTD accumulator yet and is reported as 0.00 pending "
                "per-component tracking; box H is the QPIP premium YTD.",
            ]}


ROE_REASONS = {
    "A": "Shortage of work / end of contract or season", "B": "Strike or lockout", "C": "Return to school",
    "D": "Illness or injury", "E": "Quit", "F": "Maternity", "G": "Retirement", "H": "Work-Sharing",
    "J": "Apprentice training", "K": "Other", "M": "Dismissal", "N": "Leave of absence", "P": "Parental",
    "Z": "Compassionate care / family caregiver",
}
# Block 15C lists the insurable earnings of the last N pay periods, N by pay-period type (Service Canada ROE guide).
ROE_PERIODS = {"weekly": 53, "biweekly": 27, "semi_monthly": 25, "monthly": 13}


async def _roe_data(db, org: str, e: dict, reason: str = "A", last_day: str | None = None) -> dict | None:
    """ROE block data from an employee's pay-stub history — reused by the ROE view
    and the offboarding final-pay flow. None if there's no pay history."""
    employee_id = e["id"]
    stubs = (await db.execute(
        select(PayStub, PayRun.period_end, PayRun.pay_date)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .where(PayStub.org_id == org, PayStub.employee_id == employee_id)
        .order_by(PayRun.period_end))).all()
    if not stubs:
        return None
    freq = e.get("pay_frequency", "biweekly")
    per = periods_per_year(freq)
    hours_per_period = round((e.get("standard_hours_per_week") or 40) * 52 / per, 2)
    by_period, any_real = [], False
    block17a, extra = 0.0, []
    for s, period_end, _pay in stubs:
        det = s.detail or {}
        ins = float(det.get("bases", {}).get("insurable", s.gross) or 0)
        if det.get("off_cycle"):
            # An off-cycle cheque is NOT a pay period and no hours were worked for it. Vacation pay
            # paid on it (VACP — the final-pay payout) is ROE Block 17A; any other insurable amount
            # is added to the pay period it was paid in (no extra hours).
            vac = sum(float(it.get("amount") or 0) for it in (det.get("entries") or {}).get("earnings") or []
                      if it.get("code") == "VACP")
            block17a += vac
            rest = round(ins - vac, 2)
            if rest > 0.005:
                if by_period:
                    by_period[-1]["insurable_earnings"] = round(by_period[-1]["insurable_earnings"] + rest, 2)
                else:
                    extra.append(rest)
            continue
        wh = det.get("worked_hours")
        if wh:                     # real Time & Attendance hours beat the synthetic estimate
            hrs = round(float(wh), 2); any_real = True
        else:
            hrs = hours_per_period
        by_period.append({"period_end": period_end.isoformat(),
                          "insurable_earnings": round(ins + sum(extra), 2), "insurable_hours": hrs})
        extra = []
    if not by_period:
        by_period = [{"period_end": stubs[-1][1].isoformat(), "insurable_earnings": round(sum(extra), 2),
                      "insurable_hours": 0.0}]
    recent = by_period[-ROE_PERIODS.get(freq, 27):]   # last N pay periods for the pay-period type
    return {
        "employee": {"id": employee_id, "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                     "employee_number": e.get("employee_number"), "province": e.get("province_of_employment")},
        "employer": {"org_id": org},
        "block6_pay_period_type": freq,
        "block10_first_day_worked": e.get("hire_date"),
        # Block 11 = the REAL last day for which paid (the exit case's last working day) when
        # the caller supplies it; otherwise the last pay-period end on record, as before.
        "block11_last_day_paid": (PD.to_date(last_day) or stubs[-1][1]).isoformat(),
        "block11_source": "last working day" if PD.to_date(last_day) else "last pay period end",
        "block12_final_pay_period_end": by_period[-1]["period_end"],
        "block15a_total_insurable_hours": round(sum(p["insurable_hours"] for p in recent), 2),
        "block15a_hours_source": "actual" if any_real else "estimated",
        "block15b_total_insurable_earnings": round(sum(p["insurable_earnings"] for p in recent), 2),
        "block15c_by_period": recent,
        "block15c_max_periods": ROE_PERIODS.get(freq, 27),
        "block17a_vacation_pay": round(block17a, 2),
        "block16_reason": {"code": reason.upper(), "label": ROE_REASONS.get(reason.upper(), "Other")},
        "reason_options": [{"code": k, "label": v} for k, v in ROE_REASONS.items()],
    }


@router.get("/roe/{employee_id}")
async def roe_draft(employee_id: str, request: Request, reason: str = "A", last_day: str | None = None,
                    db: AsyncSession = Depends(get_db)):
    """Draft Record of Employment from the employee's pay-stub history. A real ROE
    is filed via ROE Web / XML; this produces the block data for review."""
    org = _org(request)
    await _guard_pay(request, employee_id)
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    roe = await _roe_data(db, org, e, reason, last_day or e.get("termination_date"))
    if not roe:
        raise HTTPException(400, "No pay history on record for this employee")
    return roe


def _roe_xml(roe: dict) -> str:
    """Serialise ROE block data to a self-describing XML package.

    NOT the certified Service Canada 'ROE Web — Bulk / XML' submission file: the
    official schema requires the employer CRA payroll account, SIN, and passes a
    published XSD. This is the block data for review / mapping — it MUST be
    validated against the current ROE Web XSD before it is filed. The dollar and
    hour figures come from real pay-stub history; only the wrapper is uncertified.
    """
    def esc(v):
        return _xml_escape(str(v)) if v is not None else ""
    emp, per = roe["employee"], roe["block15c_by_period"]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- DRAFT ROE data package generated by Lexora HRM. This is NOT the certified",
        "     Service Canada ROE Web bulk-transfer file. Validate against the current",
        "     ROE Web XSD and add the employer payroll account + employee SIN before filing. -->",
        '<RecordOfEmployment status="draft" source="lexora">',
        f"  <Employer><OrgId>{esc(roe['employer']['org_id'])}</OrgId></Employer>",
        "  <Employee>",
        f"    <Name>{esc(emp['name'])}</Name>",
        f"    <EmployeeNumber>{esc(emp.get('employee_number'))}</EmployeeNumber>",
        f"    <Province>{esc(emp.get('province'))}</Province>",
        "  </Employee>",
        f"  <Block6PayPeriodType>{esc(roe['block6_pay_period_type'])}</Block6PayPeriodType>",
        f"  <Block10FirstDayWorked>{esc(roe.get('block10_first_day_worked'))}</Block10FirstDayWorked>",
        f"  <Block11LastDayPaid>{esc(roe['block11_last_day_paid'])}</Block11LastDayPaid>",
        f"  <Block12FinalPayPeriodEnd>{esc(roe['block12_final_pay_period_end'])}</Block12FinalPayPeriodEnd>",
        f'  <Block15ATotalInsurableHours source="{esc(roe["block15a_hours_source"])}">{esc(roe["block15a_total_insurable_hours"])}</Block15ATotalInsurableHours>',
        f"  <Block15BTotalInsurableEarnings>{esc(roe['block15b_total_insurable_earnings'])}</Block15BTotalInsurableEarnings>",
        f'  <Block16Reason code="{esc(roe["block16_reason"]["code"])}">{esc(roe["block16_reason"]["label"])}</Block16Reason>',
        f"  <Block17AVacationPay>{esc(roe.get('block17a_vacation_pay', 0.0))}</Block17AVacationPay>",
        "  <Block15CInsurableByPeriod>",
    ]
    for i, p in enumerate(per, 1):
        lines.append(f'    <Period n="{i}" endDate="{esc(p["period_end"])}" '
                     f'insurableEarnings="{esc(p["insurable_earnings"])}" insurableHours="{esc(p["insurable_hours"])}"/>')
    lines += ["  </Block15CInsurableByPeriod>", "</RecordOfEmployment>"]
    return "\n".join(lines)


@router.get("/roe/{employee_id}/xml")
async def roe_xml(employee_id: str, request: Request, reason: str = "A", last_day: str | None = None,
                  db: AsyncSession = Depends(get_db)):
    """Draft ROE as an XML data package (see _roe_xml for the compliance caveat)."""
    org = _org(request)
    await _guard_pay(request, employee_id)
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    roe = await _roe_data(db, org, e, reason, last_day or e.get("termination_date"))
    if not roe:
        raise HTTPException(400, "No pay history on record for this employee")
    safe = (roe["employee"].get("employee_number") or employee_id)
    return Response(content=_roe_xml(roe), media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="roe-{safe}-draft.xml"'})


class FinalPayRequest(BaseModel):
    employee_id: str
    last_day: str                       # last day worked / final pay date (YYYY-MM-DD)
    reason: str = "A"                   # ROE reason code
    year: int = 2026
    extra_earnings: list[dict] = []     # any final amounts beyond the vacation payout
    extra_deductions: list[dict] = []   # recoveries from the exit clearance checklist [{code, amount}]
    case_id: str | None = None          # the exit case this final pay settles (Stream C)
    acknowledge_unverified: bool = False


@router.get("/vacation-owed/{employee_id}")
async def vacation_owed(employee_id: str, request: Request, year: int = 2026,
                        db: AsyncSession = Depends(get_db)):
    """The outstanding ESA vacation balance owed to an employee (accrued − paid)."""
    org = _org(request)
    await _guard_pay(request, employee_id)
    row = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.employee_id == employee_id,
        EmployeeYtd.year == year))).scalar_one_or_none()
    accrued = float(row.vac_accrued or 0) if row else 0.0
    paid = float(row.vac_paid or 0) if row else 0.0
    return {"employee_id": employee_id, "year": year, "accrued": round(accrued, 2),
            "paid": round(paid, 2), "balance": round(accrued - paid, 2)}


@router.post("/final-pay", status_code=201)
async def final_pay(body: FinalPayRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Offboarding: run the final cheque (paying out the outstanding vacation balance
    + any extras via an off-cycle run, which draws the vacation liability down) and
    return the ROE draft — the whole lifecycle end in one call."""
    org = _org(request)
    _require_pay_office(request, "run a final pay")
    e = {x["id"]: x for x in await _fetch_employees(request)}.get(body.employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")

    row = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.employee_id == body.employee_id,
        EmployeeYtd.year == body.year))).scalar_one_or_none()
    vac_balance = round(float(row.vac_accrued or 0) - float(row.vac_paid or 0), 2) if row else 0.0

    earnings = list(body.extra_earnings or [])
    if vac_balance > 0.01:
        earnings.append({"code": "VACP", "amount": vac_balance})

    final = None
    _recoveries = [d for d in (body.extra_deductions or []) if float(d.get("amount") or 0) > 0]
    if earnings:
        oc = OffCycleRequest(employee_id=body.employee_id, pay_date=body.last_day,
                             year=body.year, earnings=earnings, deductions=_recoveries,
                             acknowledge_unverified=body.acknowledge_unverified)
        final = await off_cycle_run(oc, request, db)   # reuses off-cycle calc + vacation drawdown (gate enforced there)
    if body.case_id:
        # Tell the exit case the final pay is done (best effort — the case re-reads it too).
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                await c.post(f"{settings.EMPLOYEE_URL}/exit/cases/{body.case_id}/settlement/paid", headers=_fwd(request),
                             json={"settlement_ref": (final or {}).get("id"), "kind": "canada_final",
                                   "paid_run_id": (final or {}).get("id"), "net_paid": (final or {}).get("net_pay"),
                                   "pay_date": body.last_day})
        except Exception:  # noqa: BLE001
            pass

    roe = await _roe_data(db, org, e, body.reason, body.last_day)      # includes the just-run final cheque; Block 11 = the real last day
    await record_audit(request, "payroll.finalpay", entity_type="employee", entity_id=body.employee_id,
                       summary=f"Final pay for {e['first_name']} {e['last_name']} · vacation paid {vac_balance if vac_balance > 0 else 0.0} · ROE {body.reason}",
                       meta={"last_day": body.last_day, "reason": body.reason, "vacation_paid": vac_balance})
    return {"employee": f"{e['first_name']} {e['last_name']}", "employee_number": e.get("employee_number"),
            "last_day": body.last_day, "reason": body.reason,
            "vacation_paid": vac_balance if vac_balance > 0 else 0.0,
            "final_pay": final, "roe": roe}


@router.get("/remittance-schedule")
async def remittance_schedule(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    """Monthly CRA remittance schedule with due dates. Assumes a REGULAR remitter
    (due the 15th of the month following the pay). Accelerated types differ."""
    org = _org(request)
    _require_pay_office(request, "see the remittance schedule")
    rows = (await db.execute(
        select(PayStub, PayRun.pay_date).join(PayRun, PayStub.pay_run_id == PayRun.id)
        .where(PayStub.org_id == org))).all()
    months: dict = {}
    for s, pay_date in rows:
        if pay_date.year != year:
            continue
        key = f"{pay_date.year}-{pay_date.month:02d}"
        m = months.setdefault(key, {"month": key, "income_tax": 0.0, "cpp": 0.0, "ei": 0.0, "remittance": 0.0})
        p = remit_split(s)                    # CRA share only (Québec QPP / QPIP / QC tax → Revenu Québec)
        tax, cpp_ee, ei_ee, er_cpp, er_ei = p["tax"], p["cpp_ee"], p["ei_ee"], p["cpp_er"], p["ei_er"]
        m["income_tax"] += tax; m["cpp"] += cpp_ee + er_cpp; m["ei"] += ei_ee + er_ei
        m["remittance"] += tax + cpp_ee + er_cpp + ei_ee + er_ei
    out = []
    for key in sorted(months):
        m = months[key]
        y, mo = map(int, key.split("-"))
        dy, dmo = (y, mo + 1) if mo < 12 else (y + 1, 1)
        m["due_date"] = f"{dy}-{dmo:02d}-15"
        out.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()})
    return {"year": year, "remitter_type": "Regular (monthly)", "months": out,
            "total_remittance": round(sum(m["remittance"] for m in out), 2)}


# ═══ Compliance gate ════════════════════════════════════════════════════════
@router.get("/compliance")
async def compliance(request: Request, db: AsyncSession = Depends(get_db)):
    """Statutory-constant verification status — the 'verify before real pay' gate.
    See compliance.py; PDOC golden cases in verify_pdoc.py. Includes the Canada-wide
    jurisdictions roll-up (which provinces are live / gated / not built) and the QC
    gate, so the UI can show exactly how much of Canada can run real pay today."""
    _org(request)
    from compliance import compliance_report, pdoc_status, jurisdictions_overview
    report = compliance_report()
    report["gate"] = gate_status()
    report["pdoc"] = pdoc_status()
    report["qc_gate"] = qc_gate_status()
    report["jurisdictions"] = jurisdictions_overview()
    return report


@router.get("/compliance/province/{prov}")
async def compliance_province(prov: str, request: Request):
    """Per-province verification detail for an extended jurisdiction (MB/SK/NS/NB/NL/
    PE/NT/YT/NU): every statutory constant, its CRA source, verified/estimated
    status, and the province's own real-pay gate."""
    _org(request)
    from compliance import prov_compliance_report
    report = prov_compliance_report(prov)
    report["gate"] = prov_gate_status(prov)
    return report


_PDOC_URL = "https://apps.cra-arc.gc.ca/ebci/rhpd/"
_WEBRAS_URL = "https://entreprises.revenuquebec.ca/EntNA/SX/SX03/SX03.WebRAS/"


@router.get("/verification")
async def verification(request: Request):
    """Guided verification data for the GATED jurisdictions (QC + the 9 extended
    provinces/territories): each golden case with the ENGINE's own computed figures,
    so an operator can run the same case in CRA PDOC / Revenu Québec WebRAS and confirm
    the engine matches before the gate is opened. Read-only — computes, never verifies."""
    _org(request)
    _require_pay_office(request, "open the payroll verification cases")
    result = {"quebec": {"source": "Revenu Québec WebRAS + CRA PDOC-Quebec", "url": _WEBRAS_URL, "cases": []},
              "provinces": {"source": "CRA Payroll Deductions Online Calculator (PDOC)", "url": _PDOC_URL, "cases": []}}

    try:
        from qc_webras_cases import CASES as QC
    except Exception:
        QC = []
    for c in QC:
        inp = c["input"]; g = round(inp["gross_period"], 2); freq = inp.get("pay_frequency", "biweekly")
        try:
            r = calculate_paystub_qc(cash_earnings=g, pensionable_earnings=g, insurable_earnings=g,
                                     taxable_earnings=g, pay_frequency=freq, ytd=inp.get("ytd"))
            d = r["deductions"]
            eng = {"qpp": d["cpp"], "qpp2": d["cpp2"], "qpip": d.get("qpip", 0.0),
                   "qc_tax": r["tax_detail"]["provincial_qc"], "fed": r["tax_detail"]["federal_qc"],
                   "ei": d["ei"], "net": r["net_pay"]}
        except Exception as ex:
            eng = {"error": f"engine error ({type(ex).__name__})"}   # go-live: no exception text to clients
        result["quebec"]["cases"].append({
            "name": c["name"], "gross_period": g, "frequency": freq, "engine": eng,
            "recorded": {**(c.get("webras") or {}), **({"fed": c["pdoc"]["fed"], "ei": c["pdoc"]["ei"]} if c.get("pdoc") else {})},
            "confirmed": c.get("webras") is not None})

    try:
        from prov_pdoc_cases import CASES as PROV
    except Exception:
        PROV = []
    for c in PROV:
        inp = c["input"]; g = round(inp["gross_period"], 2); freq = inp.get("pay_frequency", "biweekly"); prov = inp["province"]
        try:
            r = calculate_paystub(g, freq, prov)
            d = r["deductions"]
            eng = {"cpp": round(d["cpp"] + d["cpp2"], 2), "ei": d["ei"], "tax": d["income_tax"],
                   "payroll_tax": d.get("territorial_payroll_tax", 0.0), "net": r["net_pay"]}
        except Exception as ex:
            eng = {"error": f"engine error ({type(ex).__name__})"}   # go-live: no exception text to clients
        result["provinces"]["cases"].append({
            "province": prov, "name": c["name"], "gross_period": g, "frequency": freq,
            "engine": eng, "recorded": c.get("pdoc") or {}, "confirmed": c.get("pdoc") is not None})
    return result


@router.get("/employer-health-tax")
async def employer_health_tax_report(request: Request, year: int = 2026,
                                     db: AsyncSession = Depends(get_db)):
    """Employer Health Tax — the employer-side payroll tax levied per province on TOTAL
    annual payroll (ON EHT, BC EHT, MB HE Levy, NL HAPSET, QC HSF). Aggregates the org's
    YTD gross by province and applies each rule. ⚠️ Estimated rates — verify per province
    against its finance ministry before a real remittance (employer-cost, not withholding)."""
    org = _org(request)
    _require_pay_office(request, "see the employer health tax report")
    from collections import defaultdict
    from constants_ca_eht import employer_health_tax
    emps = {e["id"]: e for e in await _fetch_employees(request)}
    rows = (await db.execute(select(EmployeeYtd).where(
        EmployeeYtd.org_id == org, EmployeeYtd.year == year))).scalars().all()
    payroll: dict = defaultdict(float)
    for r in rows:
        e = emps.get(str(r.employee_id))
        prov = ((e.get("province_of_employment") if e else "ON") or "ON").upper()
        payroll[prov] += float(r.gross or 0)
    out, total_tax = [], 0.0
    for prov, pay in sorted(payroll.items()):
        eht = employer_health_tax(prov, pay)
        out.append(eht)
        total_tax += eht["tax"]
    return {"year": year, "estimated": True,
            "provinces": out,
            "total_payroll": round(sum(payroll.values()), 2),
            "total_eht": round(total_tax, 2),
            "applicable_count": sum(1 for x in out if x["applicable"] and x["tax"] > 0)}


# ═══ Shared guards / parsing ══════════════════════════════════════════════
_PAYROLL_ADMIN_ROLES = {"org_admin", "payroll_admin", "superadmin"}


def _require_payroll_admin(request: Request) -> str:
    role = (request.headers.get("x-role") or "").lower()
    if role not in _PAYROLL_ADMIN_ROLES:
        raise HTTPException(403, "Only the payroll admin / org admin may change this")
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or role


def _parse_date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ═══ Pay groups + calendar ══════════════════════════════════════════════════
class PayGroupCreate(BaseModel):
    name: str
    pay_frequency: str = "biweekly"
    province: str = "ON"
    first_pay_date: str | None = None


@router.post("/pay-groups", status_code=201)
async def create_pay_group(body: PayGroupCreate, request: Request, db: AsyncSession = Depends(get_db)):
    _require_pay_office(request, "create a pay group")
    if body.pay_frequency not in PAY_FREQUENCIES:
        raise HTTPException(400, f"pay_frequency must be one of {PAY_FREQUENCIES}")
    pg = PayGroup(org_id=_org(request), name=body.name, pay_frequency=body.pay_frequency,
                  province=body.province.upper(), first_pay_date=body.first_pay_date)
    db.add(pg)
    await db.commit()
    await db.refresh(pg)
    return {"id": str(pg.id), "name": pg.name, "pay_frequency": pg.pay_frequency,
            "periods_per_year": periods_per_year(pg.pay_frequency), "province": pg.province}


@router.get("/pay-groups")
async def list_pay_groups(request: Request, db: AsyncSession = Depends(get_db)):
    _require_pay_office(request, "see the pay groups")
    res =await db.execute(select(PayGroup).where(PayGroup.org_id == _org(request)))
    return [{"id": str(p.id), "name": p.name, "pay_frequency": p.pay_frequency,
             "periods_per_year": periods_per_year(p.pay_frequency), "province": p.province}
            for p in res.scalars().all()]


@router.get("/calendar")
async def calendar(request: Request, pay_frequency: str = "biweekly",
                   first_pay_date: str = "2026-01-09", year: int = 2026):
    _org(request)
    try:
        y, m, d = map(int, first_pay_date.split("-"))
        periods = generate_periods(pay_frequency, date(y, m, d), year)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"pay_frequency": pay_frequency, "count": len(periods), "periods": periods}


@router.get("/runs/{run_id}/gl-journal")
async def gl_journal(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Double-entry GL journal for a pay run — salaries + employer costs (debits)
    against net-pay, CRA / Revenu Québec and deduction payables (credits). Balances to the cent.
    Québec stubs: QPP + QPIP + the Québec income tax are payable to Revenu Québec (EI + federal tax stay CRA);
    NT / NU: the employee-paid territorial payroll tax is its own payable. The payroll→finance hand-off."""
    org = _org(request)
    _require_pay_office(request, "see a run's GL journal")
    run, stubs = await _run_or_404(db, org, run_id)
    dept_gross: dict = defaultdict(float)
    t = {"gross": 0.0, "cpp_ee": 0.0, "cpp_er": 0.0, "ei_ee": 0.0, "ei_er": 0.0,
         "tax": 0.0, "net": 0.0, "pre_tax": 0.0, "after_tax": 0.0,
         "qpp_ee": 0.0, "qpp_er": 0.0, "qpip_ee": 0.0, "qpip_er": 0.0, "qc_tax": 0.0, "terr_tax": 0.0}
    for s in stubs:
        det = s.detail or {}
        emp, d, ec = det.get("employee", {}), det.get("deductions", {}), det.get("employer_cost", {})
        qc = str(emp.get("province") or det.get("province") or "").upper() == "QC" or "qpip" in d
        dept_gross[emp.get("department") or "Unassigned"] += float(s.gross)
        t["gross"] += float(s.gross)
        pen_ee = float(s.cpp) + float(s.cpp2)
        pen_er = float(ec.get("cpp", 0) or 0) + float(ec.get("cpp2", 0) or 0)
        if qc:                                                # QPP / QPP2 → Revenu Québec
            t["qpp_ee"] += pen_ee
            t["qpp_er"] += pen_er
            t["qpip_ee"] += float(d.get("qpip", 0) or 0)
            t["qpip_er"] += float(ec.get("qpip", 0) or 0)
            qc_tax = float((det.get("tax_detail") or {}).get("provincial_qc", 0) or 0)
            t["qc_tax"] += qc_tax
            t["tax"] += float(s.income_tax) - qc_tax          # federal (after the Québec abatement) → CRA
        else:
            t["cpp_ee"] += pen_ee
            t["cpp_er"] += pen_er
            t["tax"] += float(s.income_tax)
        t["ei_ee"] += float(s.ei)
        t["terr_tax"] += float(d.get("territorial_payroll_tax", 0) or 0)
        t["net"] += float(s.net_pay)
        t["ei_er"] += float(ec.get("ei", 0) or 0)
        t["pre_tax"] += float(d.get("pre_tax", 0) or 0)
        t["after_tax"] += float(d.get("after_tax", 0) or 0)

    def line(acct, name, debit=0.0, credit=0.0, memo=None):
        return {"account": acct, "name": name, "debit": round(debit, 2), "credit": round(credit, 2), "memo": memo}

    journal = []
    for dept, g in sorted(dept_gross.items()):
        journal.append(line("6000", "Salaries & Wages Expense", debit=g, memo=dept))
    if t["cpp_er"] > 0.005:
        journal.append(line("6200", "Employer CPP Expense", debit=t["cpp_er"]))
    if t["qpp_er"] > 0.005:
        journal.append(line("6205", "Employer QPP Expense", debit=t["qpp_er"]))
    if t["ei_er"] > 0.005:
        journal.append(line("6210", "Employer EI Expense", debit=t["ei_er"]))
    if t["qpip_er"] > 0.005:
        journal.append(line("6220", "Employer QPIP Expense", debit=t["qpip_er"]))
    # credits
    ded_payable = round(t["pre_tax"] + t["after_tax"], 2)
    journal.append(line("2100", "Net Pay Payable (bank)", credit=t["net"]))
    if t["cpp_ee"] + t["cpp_er"] > 0.005:
        journal.append(line("2200", "CPP Payable — CRA", credit=t["cpp_ee"] + t["cpp_er"]))
    journal.append(line("2210", "EI Payable — CRA", credit=t["ei_ee"] + t["ei_er"]))
    journal.append(line("2220", "Income Tax Payable — CRA", credit=t["tax"]))
    if t["qpp_ee"] + t["qpp_er"] > 0.005:
        journal.append(line("2230", "QPP Payable — Revenu Québec", credit=t["qpp_ee"] + t["qpp_er"]))
    if t["qpip_ee"] + t["qpip_er"] > 0.005:
        journal.append(line("2235", "QPIP Payable — Revenu Québec", credit=t["qpip_ee"] + t["qpip_er"]))
    if t["qc_tax"] > 0.005:
        journal.append(line("2225", "Québec Income Tax Payable — Revenu Québec", credit=t["qc_tax"]))
    if t["terr_tax"] > 0.005:
        journal.append(line("2240", "Territorial Payroll Tax Payable (NT / NU)", credit=t["terr_tax"]))
    if ded_payable > 0.005:
        journal.append(line("2300", "Employee Deductions Payable", credit=ded_payable))

    total_debit = round(sum(j["debit"] for j in journal), 2)
    total_credit = round(sum(j["credit"] for j in journal), 2)
    freq = next((f for f in PAY_FREQUENCIES if _paygroup_id(org, f) == run.pay_group_id), "off-cycle")
    return {"run_id": str(run.id), "pay_frequency": freq, "pay_date": run.pay_date.isoformat(),
            "period_end": run.period_end.isoformat(), "employee_count": len(stubs),
            "journal": journal, "total_debit": total_debit, "total_credit": total_credit,
            "balanced": abs(total_debit - total_credit) < 0.01,
            "cra_remittance": round(t["tax"] + t["cpp_ee"] + t["cpp_er"] + t["ei_ee"] + t["ei_er"], 2),
            "rq_remittance": round(t["qc_tax"] + t["qpp_ee"] + t["qpp_er"] + t["qpip_ee"] + t["qpip_er"], 2)}
