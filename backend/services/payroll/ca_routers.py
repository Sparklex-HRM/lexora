"""Stream G6 — Canada completeness HTTP surface, all under /payroll/ca/*.

  statutory-values  GET / PUT / verify            org-maintained figures + sign-off (statutory_rule_verification)
  settings          GET / PUT                      remitter type, CRA / RQ accounts (org choices)
  remittances       GET schedule / pd7a / rq       by remitter type, due dates only from entered rules
  stat-holiday      GET rules · POST compute       per-province org rule, line-by-line calculation
  accruals          rate groups · assignments · GET run/{id} · GET summary (WCB + EHT)
  contractors       CRUD · payments · t4a          the T4A path alongside employees
  year-end          GET checklist                  due dates (org values), ROE outstanding, remittances outstanding
  roe/{id}/validate GET                            field checks on the ROE draft + XML package
"""
from __future__ import annotations

import json
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402

import ca_accruals  # noqa: E402
import ca_remit  # noqa: E402
import ca_stat_holiday  # noqa: E402
import ca_statutory as CS  # noqa: E402
import ca_t4a  # noqa: E402
from ca_models import (CaContractor, CaContractorPayment, CaOrgSettings, CaStatutoryValue,  # noqa: E402
                       CaWcbAssignment, CaWcbRateGroup)
from ca_roe_validate import validate_roe, validate_roe_xml  # noqa: E402
from constants_ca_eht import employer_health_tax  # noqa: E402
from database import get_db  # noqa: E402
from models import EmployeeYtd, PayRun, PayStub, RuleVerification  # noqa: E402
import routers as R  # noqa: E402  (shared helpers: _org, _fetch_employees, _run_or_404, _roe_data, _roe_xml, ROE_REASONS)

from pay_access import pay_office_only  # noqa: E402
from shared.utils.org_time import org_today  # org-TZ business date (UTC store, local today)

# Every /payroll/ca route is the pay office's (WASA 2026-10-21); _who still narrows the writes.
router = APIRouter(prefix="/payroll/ca", tags=["canada"],
                   dependencies=[Depends(pay_office_only("open the Canada payroll pages"))])
_ADMIN = {"superadmin", "org_admin", "payroll_admin", "hr_admin"}


def _who(request: Request) -> str:
    role = (request.headers.get("x-role") or "").lower()
    if role not in _ADMIN:
        raise HTTPException(403, "Only the payroll admin / org admin may change this")
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or role


def _ca_emps(emps: list[dict]) -> list[dict]:
    return list(emps)


# ───────────────────────────── statutory values + gate ─────────────────────────────

async def _values(db, org: str) -> dict:
    """key → stored row (latest effective_from wins)."""
    rows = (await db.execute(select(CaStatutoryValue).where(CaStatutoryValue.org_id == org)
                             .order_by(CaStatutoryValue.effective_from))).scalars().all()
    out: dict = {}
    for r in rows:
        out[r.key] = {"id": str(r.id), "value": r.value, "source_url": r.source_url,
                      "effective_from": r.effective_from.isoformat() if r.effective_from else None, "note": r.note,
                      "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat() if r.updated_at else None}
    return out


async def _verifications(db, org: str) -> dict:
    q = select(RuleVerification).where(RuleVerification.rule_key.like("ca.%"),
                                            or_(RuleVerification.org_id == None, RuleVerification.org_id == org))  # noqa: E711
    rows = (await db.execute(q)).scalars().all()
    out: dict = {}
    for r in sorted(rows, key=lambda x: (x.org_id is not None)):
        out[r.rule_key] = {"status": r.status, "verified_by": r.verified_by,
                           "verified_on": r.verified_on.isoformat() if r.verified_on else None,
                           "value_snapshot": r.value_snapshot, "source_url": r.source_url, "note": r.note}
    return out


async def _gate(db, org: str, keys: list[str]) -> dict:
    return CS.gate(keys, await _values(db, org), await _verifications(db, org))


@router.get("/statutory-values")
async def statutory_values(request: Request, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    rows = CS.rows_with_status(await _values(db, org), await _verifications(db, org))
    return {"rows": rows, "total": len(rows),
            "verified": sum(1 for r in rows if r["status"] == "verified"),
            "entered": sum(1 for r in rows if r["status"] == "entered"),
            "stale": sum(1 for r in rows if r["status"] == "stale"),
            "blank": sum(1 for r in rows if r["status"] == "blank"),
            "shapes": CS.REMIT_SHAPES, "stat_rule_shape": CS.STAT_RULE_SHAPE}


class ValueIn(BaseModel):
    key: str
    value: str | None = None
    source_url: str = ""
    effective_from: str = "2026-01-01"
    note: str = ""


@router.put("/statutory-values")
async def put_value(body: ValueIn, request: Request, db: AsyncSession = Depends(get_db)):
    who = _who(request)
    org = R._org(request)
    cat = next((c for c in CS.catalog() if c["key"] == body.key), None)
    if not cat:
        raise HTTPException(404, "Unknown statutory value key")
    if body.value not in (None, "") and CS.parse_value(cat["kind"], body.value) is None:
        raise HTTPException(400, f"Value does not parse as {cat['kind']}")
    if body.value not in (None, "") and not body.source_url.strip():
        raise HTTPException(400, "A source URL is required for every entered value")
    try:
        eff = date.fromisoformat(body.effective_from[:10])
    except ValueError:
        raise HTTPException(400, "effective_from must be YYYY-MM-DD")
    if cat["kind"] == "json" and body.value:
        body.value = json.dumps(json.loads(body.value), sort_keys=True)
    row = (await db.execute(select(CaStatutoryValue).where(
        CaStatutoryValue.org_id == org, CaStatutoryValue.key == body.key, CaStatutoryValue.effective_from == eff))).scalar_one_or_none()
    if body.value in (None, ""):
        if row:
            await db.delete(row)
        await db.commit()
        return {"ok": True, "key": body.key, "cleared": True}
    if not row:
        row = CaStatutoryValue(org_id=uuid.UUID(org), key=body.key, effective_from=eff)
        db.add(row)
    row.value, row.source_url, row.note, row.updated_by = body.value, body.source_url.strip(), body.note, who
    await db.commit()
    await record_audit(request, "payroll.ca_value.set", entity_type="ca_statutory_value", entity_id=body.key,
                       summary=f"{body.key} = {body.value[:60]} (from {eff})", meta={"source_url": body.source_url})
    return {"ok": True, "key": body.key}


class VerifyIn(BaseModel):
    key: str
    status: str = "verified"       # verified | estimated (undo)
    note: str = ""


@router.post("/statutory-values/verify")
async def verify_value(body: VerifyIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Sign-off = a row in statutory_rule_verification (rule_key 'ca.<key>', org scope) with the
    value snapshot."""
    who = _who(request)
    org = R._org(request)
    if body.status not in ("verified", "estimated"):
        raise HTTPException(400, "status must be verified or estimated")
    vals = await _values(db, org)
    v = vals.get(body.key)
    if not v or v.get("value") in (None, ""):
        raise HTTPException(409, "Enter the value (with its source) before verifying it")
    rk = "ca." + body.key
    row = (await db.execute(select(RuleVerification).where(
        RuleVerification.rule_key == rk, RuleVerification.org_id == org))).scalar_one_or_none()
    if not row:
        row = RuleVerification(org_id=uuid.UUID(org), rule_key=rk)
        db.add(row)
    row.status, row.value_snapshot, row.verified_by = body.status, str(v["value"]), who
    row.verified_on, row.source_url, row.note = datetime.now(timezone.utc), v.get("source_url"), body.note
    await db.commit()
    await record_audit(request, "payroll.ca_value.verify", entity_type="ca_statutory_value", entity_id=body.key,
                       summary=f"{body.status} · {body.key}", meta={"note": body.note})
    return {"ok": True, "key": body.key, "status": body.status}


# ───────────────────────────── org settings ─────────────────────────────

async def _settings(db, org: str) -> CaOrgSettings | None:
    return (await db.execute(select(CaOrgSettings).where(CaOrgSettings.org_id == org))).scalar_one_or_none()


def _settings_out(s: CaOrgSettings | None) -> dict:
    return {"remitter_type": (s.remitter_type if s else "") or "", "rq_remitter_type": (s.rq_remitter_type if s else "") or "",
            "cra_payroll_account": s.cra_payroll_account if s else None, "rq_account": s.rq_account if s else None,
            "remitter_types": list(ca_remit.REMITTER_TYPES)}


class SettingsIn(BaseModel):
    remitter_type: str = ""
    rq_remitter_type: str = ""
    cra_payroll_account: str | None = None
    rq_account: str | None = None


@router.get("/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    return _settings_out(await _settings(db, R._org(request)))


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    _who(request)
    org = R._org(request)
    if body.remitter_type and body.remitter_type not in ca_remit.REMITTER_TYPES:
        raise HTTPException(400, f"remitter_type must be one of {ca_remit.REMITTER_TYPES}")
    s = await _settings(db, org)
    if not s:
        s = CaOrgSettings(org_id=uuid.UUID(org))
        db.add(s)
    s.remitter_type, s.rq_remitter_type = body.remitter_type, body.rq_remitter_type
    s.cra_payroll_account, s.rq_account = body.cra_payroll_account, body.rq_account
    await db.commit()
    return {"ok": True, **_settings_out(s)}


# ───────────────────────────── remittances ─────────────────────────────

async def _stub_rows(db, org: str, year: int, emps: dict) -> list[dict]:
    rows = (await db.execute(select(PayStub, PayRun.pay_date).join(PayRun, PayStub.pay_run_id == PayRun.id)
                             .where(PayStub.org_id == org))).all()
    out = []
    for s, pd in rows:
        if pd.year != year:
            continue
        e = emps.get(str(s.employee_id)) or {}
        det = s.detail or {}
        # CRA fields carry the CRA share only; a Québec stub's QPP / QPIP / Québec tax go to the RQ fields
        p = R.remit_split(s)
        prov = "QC" if p["qc"] else (e.get("province_of_employment") or det.get("employee", {}).get("province") or "ON").upper()
        out.append({"pay_date": pd, "employee_id": str(s.employee_id), "gross": float(s.gross), "province": prov,
                    "income_tax": p["tax"], "cpp_ee": p["cpp_ee"], "cpp_er": p["cpp_er"],
                    "ei_ee": p["ei_ee"], "ei_er": p["ei_er"],
                    "qpip_ee": p["qpip_ee"], "qpip_er": p["qpip_er"],
                    "qc_tax": p["qc_tax"], "qpp_ee": p["qpp_ee"], "qpp_er": p["qpp_er"]})
    return out


@router.get("/remittances")
async def remittances(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    """CRA schedule by the org's remitter type (PD7A-style per period) + AMWA reference +
    suggested type (only when the ceilings are entered) + Revenu Québec summary."""
    org = R._org(request)
    emps = {e["id"]: e for e in await R._fetch_employees(request)}
    stubs = await _stub_rows(db, org, year, emps)
    s = await _settings(db, org)
    vals = await _values(db, org)
    rt = (s.remitter_type if s else "") or "regular"
    rule = CS.typed_value(f"remit.{rt}.rule", vals)
    sched = ca_remit.build_schedule(stubs, rt, rule)
    amwa = ca_remit.average_monthly_withholding(stubs)
    ceilings = {k: CS.typed_value(f"remit.{k}.awma_max", vals) for k in ("quarterly", "regular", "threshold1")}
    qc = [x for x in stubs if x["province"] == "QC"]
    rq = ca_remit.build_rq_summary(qc, CS.typed_value("rq.rule", vals))
    gate_keys = [f"remit.{rt}.rule"] + (["rq.rule"] if qc else [])
    return {"year": year, "settings": _settings_out(s), "schedule": sched, "amwa": amwa,
            "ceilings": ceilings, "suggestion": ca_remit.suggest_remitter_type(amwa["amwa"], ceilings),
            "rq": rq, "compliance": await _gate(db, org, gate_keys),
            "legacy_rule_in_use": rt == "regular" and not rule}


# ───────────────────────────── stat-holiday pay ─────────────────────────────

@router.get("/stat-holiday/rules")
async def stat_rules(request: Request, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    vals, ver = await _values(db, org), await _verifications(db, org)
    rows = [r for r in CS.rows_with_status(vals, ver) if r["group"] == "stat_holiday"]
    return {"rules": rows, "methods": list(ca_stat_holiday.METHODS), "shape": CS.STAT_RULE_SHAPE}


class StatComputeIn(BaseModel):
    employee_id: str
    holiday: str
    eligible: bool = True
    hours_worked_on_holiday: float = 0.0
    days_worked_in_lookback: int | None = None
    manual_amount: float | None = None


@router.post("/stat-holiday/compute")
async def stat_compute(body: StatComputeIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Line-by-line statutory-holiday pay for one employee + the run entry to paste into
    Run Payroll → Adjust. Every line is also carried on the entry (calc_lines) so the
    payslip detail keeps the working."""
    org = R._org(request)
    e = {x["id"]: x for x in await R._fetch_employees(request)}.get(body.employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    try:
        hol = date.fromisoformat(body.holiday[:10])
    except ValueError:
        raise HTTPException(400, "holiday must be YYYY-MM-DD")
    prov = (e.get("province_of_employment") or "ON").upper()
    vals = await _values(db, org)
    rule = CS.typed_value(f"stat.{prov}.rule", vals)
    stubs = (await db.execute(select(PayStub, PayRun.period_end).join(PayRun, PayStub.pay_run_id == PayRun.id)
                              .where(PayStub.org_id == org, PayStub.employee_id == body.employee_id))).all()
    hist = [{"period_end": pe, "gross": float(s.gross), "entries": (s.detail or {}).get("entries")} for s, pe in stubs]
    rate = float(e.get("hourly_rate") or 0) or None
    if not rate and e.get("annual_salary") and e.get("standard_hours_per_week"):
        rate = round(float(e["annual_salary"]) / 52 / float(e["standard_hours_per_week"]), 4)
    std_daily = round(float(e.get("standard_hours_per_week") or 40) / 5, 2)
    res = ca_stat_holiday.compute(rule, province=prov, holiday=hol, stubs=hist, eligible=body.eligible,
                                  hourly_rate=rate, standard_daily_hours=std_daily,
                                  hours_worked_on_holiday=body.hours_worked_on_holiday,
                                  days_worked_in_lookback=body.days_worked_in_lookback, manual_amount=body.manual_amount)
    res["employee"] = {"id": e["id"], "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                       "employee_number": e.get("employee_number"), "province": prov,
                       "hourly_rate_used": rate, "standard_daily_hours": std_daily}
    res["rule"] = rule
    res["compliance"] = await _gate(db, org, [f"stat.{prov}.rule"])
    return res


# ───────────────────────────── WCB rate groups + accruals ─────────────────────────────

def _rg_out(g: CaWcbRateGroup) -> dict:
    return {"id": str(g.id), "province": g.province, "group_code": g.group_code, "description": g.description,
            "rate_per_100": float(g.rate_per_100 or 0), "year": g.year, "account_number": g.account_number}


@router.get("/accruals/rate-groups")
async def rate_groups(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    groups = (await db.execute(select(CaWcbRateGroup).where(CaWcbRateGroup.org_id == org, CaWcbRateGroup.year == year)
                               .order_by(CaWcbRateGroup.province, CaWcbRateGroup.group_code))).scalars().all()
    asg = (await db.execute(select(CaWcbAssignment).where(CaWcbAssignment.org_id == org))).scalars().all()
    emps = _ca_emps(await R._fetch_employees(request))
    by_emp = {str(a.employee_id): str(a.rate_group_id) for a in asg}
    return {"year": year, "groups": [_rg_out(g) for g in groups],
            "employees": [{"id": e["id"], "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                           "employee_number": e.get("employee_number"), "province": e.get("province_of_employment"),
                           "rate_group_id": by_emp.get(e["id"])} for e in emps]}


class RateGroupIn(BaseModel):
    id: str | None = None
    province: str
    group_code: str
    description: str | None = None
    rate_per_100: float
    year: int = 2026
    account_number: str | None = None


@router.put("/accruals/rate-groups")
async def put_rate_group(body: RateGroupIn, request: Request, db: AsyncSession = Depends(get_db)):
    _who(request)
    org = R._org(request)
    if body.province.upper() not in CS.PROVINCES:
        raise HTTPException(400, "Not a Canadian province code")
    if not (0 <= body.rate_per_100 <= 30):
        raise HTTPException(400, "rate_per_100 must be between 0 and 30")
    g = None
    if body.id:
        g = (await db.execute(select(CaWcbRateGroup).where(CaWcbRateGroup.id == uuid.UUID(body.id), CaWcbRateGroup.org_id == org))).scalar_one_or_none()
    if not g:
        g = CaWcbRateGroup(org_id=uuid.UUID(org))
        db.add(g)
    g.province, g.group_code, g.description = body.province.upper(), body.group_code.strip()[:24], body.description
    g.rate_per_100, g.year, g.account_number = body.rate_per_100, body.year, body.account_number
    await db.commit()
    await db.refresh(g)
    return {"ok": True, **_rg_out(g)}


@router.delete("/accruals/rate-groups/{group_id}")
async def delete_rate_group(group_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _who(request)
    org = R._org(request)
    g = (await db.execute(select(CaWcbRateGroup).where(CaWcbRateGroup.id == uuid.UUID(group_id), CaWcbRateGroup.org_id == org))).scalar_one_or_none()
    if g:
        for a in (await db.execute(select(CaWcbAssignment).where(CaWcbAssignment.rate_group_id == g.id))).scalars().all():
            await db.delete(a)
        await db.delete(g)
        await db.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    employee_id: str
    rate_group_id: str | None = None


@router.put("/accruals/assignments")
async def assign(body: AssignIn, request: Request, db: AsyncSession = Depends(get_db)):
    _who(request)
    org = R._org(request)
    a = (await db.execute(select(CaWcbAssignment).where(CaWcbAssignment.org_id == org,
                                                          CaWcbAssignment.employee_id == uuid.UUID(body.employee_id)))).scalar_one_or_none()
    if not body.rate_group_id:
        if a:
            await db.delete(a)
            await db.commit()
        return {"ok": True, "cleared": True}
    if not a:
        a = CaWcbAssignment(org_id=uuid.UUID(org), employee_id=uuid.UUID(body.employee_id))
        db.add(a)
    a.rate_group_id = uuid.UUID(body.rate_group_id)
    await db.commit()
    return {"ok": True}


async def _accrual_ctx(db, org: str, request: Request, year: int):
    emps = {e["id"]: e for e in _ca_emps(await R._fetch_employees(request))}
    groups = {str(g.id): g for g in (await db.execute(select(CaWcbRateGroup).where(
        CaWcbRateGroup.org_id == org, CaWcbRateGroup.year == year))).scalars().all()}
    asg = {str(a.employee_id): str(a.rate_group_id) for a in (await db.execute(
        select(CaWcbAssignment).where(CaWcbAssignment.org_id == org))).scalars().all()}
    vals = await _values(db, org)
    return emps, groups, asg, vals


def _run_accruals(run, stubs, prior_by_emp: dict, prov_ytd_before: dict, emps, groups, asg, vals) -> dict:
    """WCB line per stub + EHT line per province for ONE run. prior_by_emp = YTD assessable
    before this run; prov_ytd_before = provincial gross before this run."""
    wcb_lines, prov_gross = [], defaultdict(float)
    for s in stubs:
        e = emps.get(str(s.employee_id))
        if not e:
            continue
        prov = (e.get("province_of_employment") or "ON").upper()
        g = groups.get(asg.get(e["id"]) or "")
        cap = CS.typed_value(f"wcb.{prov}.max_assessable", vals)
        line = ca_accruals.wcb_line(gross=float(s.gross), ytd_assessable_before=prior_by_emp.get(e["id"], 0.0),
                                    max_assessable=cap, rate_per_100=float(g.rate_per_100) if g else None)
        wcb_lines.append({"employee_id": e["id"], "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                          "employee_number": e.get("employee_number"), "province": prov,
                          "rate_group": g.group_code if g else None, **line})
        prov_gross[prov] += float(s.gross)
    eht_lines = []
    for prov, rg in sorted(prov_gross.items()):
        eht_lines.append(ca_accruals.eht_line(province=prov, run_gross=rg, ytd_before=prov_ytd_before.get(prov, 0.0),
                                              org_rule=CS.typed_value(f"eht.{prov}.rule", vals), code_fallback=employer_health_tax))
    return {"run_id": str(run.id), "period_end": run.period_end.isoformat(), "pay_date": run.pay_date.isoformat(),
            "wcb_lines": wcb_lines, "wcb_by_province": ca_accruals.summarize(wcb_lines, "province"),
            "wcb_by_group": ca_accruals.summarize(wcb_lines, "rate_group"),
            "eht_lines": eht_lines, "eht_total": round(sum(l.get("accrual") or 0 for l in eht_lines), 2),
            "wcb_total": round(sum(l.get("premium") or 0 for l in wcb_lines), 2)}


@router.get("/accruals/summary")
async def accruals_summary(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    """Every run of the year with its WCB + EHT accrual lines (cumulative, so caps and
    exemptions apply in order) + a remittance summary by province / rate group."""
    org = R._org(request)
    emps, groups, asg, vals = await _accrual_ctx(db, org, request, year)
    runs = (await db.execute(select(PayRun).where(PayRun.org_id == org).order_by(PayRun.pay_date, PayRun.created_at))).scalars().all()
    runs = [r for r in runs if r.pay_date.year == year]
    ytd_emp: dict = defaultdict(float)
    ytd_prov: dict = defaultdict(float)
    out, wcb_prov, wcb_grp, eht_prov = [], defaultdict(lambda: {"assessable": 0.0, "premium": 0.0, "employees": set()}), \
        defaultdict(lambda: {"assessable": 0.0, "premium": 0.0}), defaultdict(float)
    for run in runs:
        stubs = (await db.execute(select(PayStub).where(PayStub.pay_run_id == run.id))).scalars().all()
        stubs = [s for s in stubs if str(s.employee_id) in emps]
        if not stubs:
            continue
        acc = _run_accruals(run, stubs, dict(ytd_emp), dict(ytd_prov), emps, groups, asg, vals)
        for l in acc["wcb_lines"]:
            ytd_emp[l["employee_id"]] += l["assessable"]
            wp = wcb_prov[l["province"]]; wp["assessable"] += l["assessable"]; wp["premium"] += l["premium"] or 0; wp["employees"].add(l["employee_id"])
            wg = wcb_grp[f"{l['province']}/{l['rate_group'] or '—'}"]; wg["assessable"] += l["assessable"]; wg["premium"] += l["premium"] or 0
        for l in acc["eht_lines"]:
            ytd_prov[l["province"]] += 0  # provincial gross is updated below
            eht_prov[l["province"]] += l.get("accrual") or 0
        for s in stubs:
            e = emps.get(str(s.employee_id)) or {}
            ytd_prov[(e.get("province_of_employment") or "ON").upper()] += float(s.gross)
        out.append({k: v for k, v in acc.items() if k not in ("wcb_lines",)} | {"employees": len(acc["wcb_lines"])})
    keys = sorted({f"wcb.{p}.max_assessable" for p in wcb_prov} | {f"eht.{p}.rule" for p in eht_prov})
    return {"year": year, "runs": out,
            "wcb_by_province": {p: {"assessable": round(v["assessable"], 2), "premium": round(v["premium"], 2), "employees": len(v["employees"]),
                                    "max_assessable": CS.typed_value(f"wcb.{p}.max_assessable", vals)} for p, v in wcb_prov.items()},
            "wcb_by_group": {k: {"assessable": round(v["assessable"], 2), "premium": round(v["premium"], 2)} for k, v in wcb_grp.items()},
            "eht_by_province": {p: round(v, 2) for p, v in eht_prov.items()},
            "unassigned_employees": [e["employee_number"] for e in emps.values() if e["id"] not in asg],
            "compliance": await _gate(db, org, keys) if keys else None}


@router.get("/accruals/run/{run_id}")
async def accruals_for_run(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    run, stubs = await R._run_or_404(db, org, run_id)
    year = run.pay_date.year
    emps, groups, asg, vals = await _accrual_ctx(db, org, request, year)
    stubs = [s for s in stubs if str(s.employee_id) in emps]
    # YTD before this run = earlier runs' stubs this year (assessable approximated by gross up to the cap)
    earlier = (await db.execute(select(PayStub, PayRun.pay_date, PayRun.created_at).join(PayRun, PayStub.pay_run_id == PayRun.id)
                                .where(PayStub.org_id == org))).all()
    ytd_emp, ytd_prov = defaultdict(float), defaultdict(float)
    for s, pd, ca in sorted(earlier, key=lambda x: (x[1], x[2])):
        if pd.year != year or (pd, ca) >= (run.pay_date, run.created_at):
            continue
        e = emps.get(str(s.employee_id))
        if not e:
            continue
        prov = (e.get("province_of_employment") or "ON").upper()
        cap = CS.typed_value(f"wcb.{prov}.max_assessable", vals)
        ytd_emp[e["id"]] += ca_accruals.wcb_line(gross=float(s.gross), ytd_assessable_before=ytd_emp[e["id"]], max_assessable=cap, rate_per_100=None)["assessable"]
        ytd_prov[prov] += float(s.gross)
    acc = _run_accruals(run, stubs, dict(ytd_emp), dict(ytd_prov), emps, groups, asg, vals)
    provs = sorted({l["province"] for l in acc["wcb_lines"]})
    acc["compliance"] = await _gate(db, org, [f"wcb.{p}.max_assessable" for p in provs] + [f"eht.{p}.rule" for p in provs if p in CS.EHT_PROVINCES])
    return acc


# ───────────────────────────── contractors + T4A ─────────────────────────────

def _c_out(c: CaContractor) -> dict:
    return {"id": str(c.id), "code": c.code, "name": c.name, "business_number": c.business_number, "email": c.email,
            "address": c.address, "province": c.province, "default_box": c.default_box, "active": c.active}


def _p_out(p: CaContractorPayment) -> dict:
    return {"id": str(p.id), "contractor_id": str(p.contractor_id), "pay_date": p.pay_date.isoformat(), "year": p.year,
            "invoice_no": p.invoice_no, "description": p.description, "amount": float(p.amount or 0),
            "gst_hst": float(p.gst_hst or 0), "tax_withheld": float(p.tax_withheld or 0), "box": p.box, "status": p.status}


@router.get("/contractors")
async def list_contractors(request: Request, year: int = 2026, include_inactive: int = 0, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    q = select(CaContractor).where(CaContractor.org_id == org)
    if not include_inactive:
        q = q.where(CaContractor.active == True)  # noqa: E712
    cs = (await db.execute(q.order_by(CaContractor.name))).scalars().all()
    ps = (await db.execute(select(CaContractorPayment).where(CaContractorPayment.org_id == org, CaContractorPayment.year == year,
                                                             CaContractorPayment.status != "cancelled"))).scalars().all()
    tot: dict = defaultdict(lambda: {"paid": 0.0, "tax": 0.0, "n": 0})
    for p in ps:
        t = tot[str(p.contractor_id)]; t["paid"] += float(p.amount or 0); t["tax"] += float(p.tax_withheld or 0); t["n"] += 1
    return [{**_c_out(c), "ytd_paid": round(tot[str(c.id)]["paid"], 2), "ytd_tax": round(tot[str(c.id)]["tax"], 2),
             "payments": tot[str(c.id)]["n"]} for c in cs]


class ContractorIn(BaseModel):
    id: str | None = None
    code: str
    name: str
    business_number: str | None = None
    email: str | None = None
    address: str | None = None
    province: str | None = None
    default_box: str = "048"
    active: bool = True


@router.put("/contractors")
async def put_contractor(body: ContractorIn, request: Request, db: AsyncSession = Depends(get_db)):
    _who(request)
    org = R._org(request)
    if body.default_box not in ca_t4a.INCOME_BOXES:
        raise HTTPException(400, "default_box must be 048 or 020")
    c = None
    if body.id:
        c = (await db.execute(select(CaContractor).where(CaContractor.id == uuid.UUID(body.id), CaContractor.org_id == org))).scalar_one_or_none()
    if not c:
        c = CaContractor(org_id=uuid.UUID(org))
        db.add(c)
    c.code, c.name, c.business_number, c.email = body.code.strip()[:24], body.name.strip()[:160], body.business_number, body.email
    c.address, c.province, c.default_box, c.active = body.address, (body.province or None), body.default_box, body.active
    await db.commit()
    await db.refresh(c)
    return {"ok": True, **_c_out(c)}


@router.get("/contractors/payments")
async def list_payments(request: Request, year: int = 2026, contractor_id: str | None = None, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    q = select(CaContractorPayment).where(CaContractorPayment.org_id == org, CaContractorPayment.year == year)
    if contractor_id:
        q = q.where(CaContractorPayment.contractor_id == uuid.UUID(contractor_id))
    rows = (await db.execute(q.order_by(CaContractorPayment.pay_date.desc()))).scalars().all()
    return [_p_out(p) for p in rows]


class PaymentIn(BaseModel):
    contractor_id: str
    pay_date: str
    invoice_no: str | None = None
    description: str | None = None
    amount: float
    gst_hst: float = 0
    tax_withheld: float = 0
    box: str | None = None


@router.post("/contractors/payments", status_code=201)
async def add_payment(body: PaymentIn, request: Request, db: AsyncSession = Depends(get_db)):
    who = _who(request)
    org = R._org(request)
    c = (await db.execute(select(CaContractor).where(CaContractor.id == uuid.UUID(body.contractor_id), CaContractor.org_id == org))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contractor not found")
    try:
        pd = date.fromisoformat(body.pay_date[:10])
    except ValueError:
        raise HTTPException(400, "pay_date must be YYYY-MM-DD")
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be above 0")
    box = body.box or c.default_box
    if box not in ca_t4a.INCOME_BOXES:
        raise HTTPException(400, "box must be 048 or 020")
    p = CaContractorPayment(org_id=uuid.UUID(org), contractor_id=c.id, pay_date=pd, year=pd.year, invoice_no=body.invoice_no,
                            description=body.description, amount=body.amount, gst_hst=body.gst_hst, tax_withheld=body.tax_withheld,
                            box=box, created_by=who)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await record_audit(request, "payroll.ca_contractor.pay", entity_type="ca_contractor_payment", entity_id=str(p.id),
                       summary=f"{c.name} · {body.amount:,.2f} on {body.pay_date}")
    return _p_out(p)


@router.post("/contractors/payments/{payment_id}/cancel")
async def cancel_payment(payment_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _who(request)
    org = R._org(request)
    p = (await db.execute(select(CaContractorPayment).where(CaContractorPayment.id == uuid.UUID(payment_id),
                                                            CaContractorPayment.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Payment not found")
    p.status = "cancelled"
    await db.commit()
    return {"ok": True}


@router.get("/t4a")
async def t4a(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    cs = (await db.execute(select(CaContractor).where(CaContractor.org_id == org))).scalars().all()
    ps = (await db.execute(select(CaContractorPayment).where(CaContractorPayment.org_id == org))).scalars().all()
    out = ca_t4a.slips([_c_out(c) for c in cs], [_p_out(p) for p in ps], year)
    vals = await _values(db, org)
    out["due_date"] = (vals.get("yearend.t4a_due_date") or {}).get("value")
    out["due_source"] = (vals.get("yearend.t4a_due_date") or {}).get("source_url")
    out["compliance"] = await _gate(db, org, ["yearend.t4a_due_date"])
    return out


# ───────────────────────────── year-end checklist + ROE validation ─────────────────────────────

@router.get("/year-end")
async def year_end(request: Request, year: int = 2026, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    vals = await _values(db, org)
    ver = await _verifications(db, org)
    emps = _ca_emps(await R._fetch_employees(request))
    by_id = {e["id"]: e for e in emps}
    today = (await org_today(org))

    def dv(key):
        v = vals.get(key) or {}
        return {"value": v.get("value"), "source_url": v.get("source_url"),
                "status": next((r["status"] for r in CS.rows_with_status(vals, ver) if r["key"] == key), "blank")}

    # ROE outstanding: terminated this year with pay history, no final-pay audit (we check stubs after termination = final pay)
    roe_days = CS.typed_value("yearend.roe_due_days", vals)
    roe_out = []
    for e in emps:
        td = e.get("termination_date")
        if not td or str(td)[:4] != str(year):
            continue
        t = date.fromisoformat(str(td)[:10])
        due = (t + timedelta(days=int(roe_days))).isoformat() if roe_days else None
        roe_out.append({"employee_id": e["id"], "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                        "employee_number": e.get("employee_number"), "terminated_on": t.isoformat(), "roe_due": due,
                        "overdue": bool(due and date.fromisoformat(due) < today)})
    # remittances outstanding = periods whose due date has passed (from the schedule) — the org marks paid outside Lexora HRM
    stubs = await _stub_rows(db, org, year, by_id)
    s = await _settings(db, org)
    rt = (s.remitter_type if s else "") or "regular"
    sched = ca_remit.build_schedule(stubs, rt, CS.typed_value(f"remit.{rt}.rule", vals))
    remit_due = [p for p in sched["periods"] if p["due_date"] and date.fromisoformat(p["due_date"]) <= today]
    ytd_rows = (await db.execute(select(EmployeeYtd).where(EmployeeYtd.org_id == org, EmployeeYtd.year == year))).scalars().all()
    ytd_rows = [r for r in ytd_rows if str(r.employee_id) in by_id]
    qc_n = sum(1 for r in ytd_rows if (by_id[str(r.employee_id)].get("province_of_employment") or "").upper() == "QC")
    cs = (await db.execute(select(CaContractorPayment).where(CaContractorPayment.org_id == org, CaContractorPayment.year == year,
                                                             CaContractorPayment.status != "cancelled"))).scalars().all()
    items = [
        {"key": "t4", "title": "T4 slips + T4 Summary", "count": len(ytd_rows) - qc_n, "due": dv("yearend.t4_due_date"), "link": "/payroll?tab=yearend&ye=t4",
         "how": "Review every slip, then file through CRA. Count = employees with a YTD ledger row (ROC)."},
        {"key": "rl1", "title": "RL-1 slips + RL-1 Summary (Québec)", "count": qc_n, "due": dv("yearend.rl1_due_date"), "link": "/payroll?tab=yearend&ye=t4",
         "how": "Québec employees only. Box D / H are not tracked yet (see RL-1 notes)."},
        {"key": "t4a", "title": "T4A slips + T4A Summary (contractors)", "count": len({str(p.contractor_id) for p in cs}), "due": dv("yearend.t4a_due_date"),
         "link": "/payroll/canada/contractors", "how": "One slip per contractor paid this year."},
        {"key": "roe", "title": "ROEs outstanding", "count": len(roe_out), "due": dv("yearend.roe_due_days"), "link": "/payroll?tab=yearend&ye=roe",
         "how": "Issue within the entered number of days of the interruption of earnings.", "rows": roe_out},
        {"key": "remit", "title": "Remittance periods past their due date", "count": len(remit_due), "due": None, "link": "/payroll/canada/remittances",
         "how": "Confirm each was paid to CRA. Lexora HRM does not record the payment itself.", "rows": remit_due[-12:]},
        {"key": "wcb", "title": "WCB / WSIB annual return", "count": None, "due": None, "link": "/payroll/canada/accruals",
         "how": "Report assessable earnings by rate group from Employer accruals; the return date is on your board's notice."},
        {"key": "eht", "title": "Employer Health Tax annual return", "count": None, "due": None, "link": "/payroll/canada/accruals",
         "how": "ON / BC / MB / NL / QC only. Reconcile the accrual lines before filing."},
    ]
    keys = ["yearend.t4_due_date", "yearend.t4a_due_date", "yearend.roe_due_days"] + (["yearend.rl1_due_date"] if qc_n else [])
    return {"year": year, "items": items, "compliance": await _gate(db, org, keys),
            "employees": len(emps), "ytd_rows": len(ytd_rows)}


@router.get("/roe/{employee_id}/validate")
async def roe_validate(employee_id: str, request: Request, reason: str = "A", last_day: str | None = None,
                       db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    await R._guard_pay(request, employee_id)
    e = {x["id"]: x for x in await R._fetch_employees(request)}.get(employee_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    roe = await R._roe_data(db, org, e, reason, last_day or e.get("termination_date"))
    if not roe:
        return {"ok": False, "errors": ["No pay history on record for this employee"], "warnings": [], "xml": None}
    fields = validate_roe(roe, R.ROE_REASONS)
    xml = validate_roe_xml(R._roe_xml(roe))
    return {"ok": fields["ok"] and xml["ok"], "fields": fields, "xml": xml,
            "employee": roe["employee"], "reason": roe["block16_reason"]}
