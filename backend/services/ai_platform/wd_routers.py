"""`/ai/watchdogs` — the fleet, the run log, the findings with evidence, the settings.

Who may look: org admin, HR, payroll, finance (the office roles). Only an org admin
changes the switches and thresholds. Every read is org-scoped from the gateway header.
"""
import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_settings import GROUPS, get_setting, invalidate, merged  # noqa: E402
from shared.utils.platform_models import OrgSetting, OrgSettingAudit  # noqa: E402

import watchdogs as W  # noqa: E402
import wd_runner as RN  # noqa: E402
from g5_models import WatchdogFinding, WatchdogRun  # noqa: E402

router = APIRouter(prefix="/ai/watchdogs", tags=["watchdogs"])
OFFICE = ["org_admin", "hr", "payroll", "finance"]


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _role(request: Request) -> str:
    return request.headers.get("x-role", "") or ""


def _office(request: Request) -> str:
    role = _role(request)
    # WASA 2026-10-21: the accountant is in the "finance" family (payroll exports only); findings carry per-person pay
    # variance, and the Compliance-health screen (App.tsx) is admins + HR + payroll — not the accountant.
    if role == "accountant" or (role != "superadmin" and not directory.role_in(role, OFFICE)):
        raise HTTPException(403, "Compliance health is for org admin, HR, payroll and finance")
    return role


def _admin(request: Request):
    if _role(request) not in ("org_admin", "superadmin"):
        raise HTTPException(403, "Only an org admin can change watchdog settings")


async def _latest_runs(org: str) -> dict[str, WatchdogRun]:
    async with pm.session() as s:
        rows = (await s.execute(select(WatchdogRun).where(WatchdogRun.org_id == org)
                                .order_by(WatchdogRun.started_at.desc()).limit(400))).scalars().all()
    out: dict[str, WatchdogRun] = {}
    for r in rows:
        out.setdefault(r.agent_key, r)
    return out


def _finding_dict(f: WatchdogFinding) -> dict:
    return {"id": f.id, "run_id": f.run_id, "agent_key": f.agent_key, "agent": W.AGENTS.get(f.agent_key, W.Agent(f.agent_key, f.agent_key, "", "", 0, [])).name,
            "severity": f.severity, "confidence": f.confidence, "subject_employee_id": f.subject_employee_id,
            "subject_name": f.subject_name, "title": f.title, "explanation": f.explanation, "evidence": f.evidence or [],
            "is_new": bool(f.is_new), "created_at": pm.iso(f.created_at)}


@router.get("")
async def fleet(request: Request):
    org = _org(request)
    _office(request)
    country = await W.org_country(org)
    latest = await _latest_runs(org)
    disabled = set(await get_setting(org, "ai_watchdogs.disabled_agents", []) or [])
    all_on = bool(await get_setting(org, "ai_watchdogs.enabled", True))
    out = []
    for a in W.agents_for(country):
        r = latest.get(a.key)
        out.append({"key": a.key, "name": a.name, "domain": a.domain, "checks": a.checks, "cadence_minutes": a.cadence_minutes,
                    "notify_roles": a.notify_roles, "countries": list(a.countries), "enabled": all_on and a.key not in disabled,
                    "thresholds": await W.effective_thresholds(org, a.key, country), "threshold_help": a.threshold_help,
                    "last_run": RN.run_dict(r) if r else None, "engine": "read-only SQL, no tokens"})
    return {"country": country, "fleet_enabled": all_on, "agents": out, "as_of": pm.iso(pm.now_utc())}


@router.get("/health")
async def health(request: Request):
    """Compliance health: the latest run per agent and open findings by severity."""
    org = _org(request)
    _office(request)
    country = await W.org_country(org)
    latest = await _latest_runs(org)
    run_ids = [r.id for r in latest.values()]
    counts: dict[str, dict[str, int]] = {}
    if run_ids:
        async with pm.session() as s:
            rows = (await s.execute(select(WatchdogFinding.agent_key, WatchdogFinding.severity, func.count())
                                    .where(WatchdogFinding.run_id.in_(run_ids))
                                    .group_by(WatchdogFinding.agent_key, WatchdogFinding.severity))).all()
        for key, sev, n in rows:
            counts.setdefault(key, {})[sev] = int(n)
    disabled = set(await get_setting(org, "ai_watchdogs.disabled_agents", []) or [])
    agents, tot = [], {"info": 0, "warn": 0, "high": 0}
    never, errors, attention = 0, 0, 0
    for a in W.agents_for(country):
        r = latest.get(a.key)
        c = counts.get(a.key, {})
        for k in tot:
            tot[k] += c.get(k, 0)
        if not r:
            never += 1
        elif r.status == "error":
            errors += 1
        elif r.status == "attention":
            attention += 1
        agents.append({"key": a.key, "name": a.name, "domain": a.domain, "enabled": a.key not in disabled,
                       "status": (r.status if r else "never_run"), "last_run_at": pm.iso(r.started_at) if r else None,
                       "summary": r.summary if r else "Has not run yet.", "findings": c, "severity_max": r.severity_max if r else None,
                       "rows_read": r.rows_read if r else 0})
    score = max(0, 100 - tot["high"] * 10 - tot["warn"] * 3 - errors * 5)
    headline = ("All watchdogs are clean" if not (tot["high"] or tot["warn"] or errors)
                else f"{tot['high']} high, {tot['warn']} warn, {tot['info']} info finding(s) across {attention} agent(s)")
    return {"as_of": pm.iso(pm.now_utc()), "country": country, "score": score, "headline": headline,
            "totals": tot, "agents": agents, "never_run": never, "errors": errors,
            "note": "Every finding below lists the rows it was computed from. The score is 100 minus 10 per high and 3 per warn finding; it is a convenience, not a legal measure."}


@router.post("/{key}/run")
async def run_one(key: str, request: Request):
    org = _org(request)
    _office(request)
    if key not in W.AGENTS:
        raise HTTPException(404, "Unknown watchdog")
    if not await RN.is_enabled(org, key):
        raise HTTPException(409, "This watchdog is switched off for your company")
    run = await RN.run_agent(org, key, trigger="manual")
    async with pm.session() as s:
        fs = (await s.execute(select(WatchdogFinding).where(WatchdogFinding.run_id == run["id"])
                              .order_by(WatchdogFinding.severity.desc(), WatchdogFinding.subject_name))).scalars().all()
    return {"run": run, "findings": [_finding_dict(f) for f in fs]}


@router.post("/run-all")
async def run_every(request: Request):
    org = _org(request)
    _office(request)
    return {"runs": await RN.run_all(org, trigger="manual")}


@router.get("/runs")
async def runs(request: Request, agent: str | None = None, limit: int = 100):
    org = _org(request)
    _office(request)
    q = select(WatchdogRun).where(WatchdogRun.org_id == org)
    if agent:
        q = q.where(WatchdogRun.agent_key == agent)
    async with pm.session() as s:
        rs = (await s.execute(q.order_by(WatchdogRun.started_at.desc()).limit(min(limit, 500)))).scalars().all()
    return {"items": [{**RN.run_dict(r), "agent": W.AGENTS[r.agent_key].name if r.agent_key in W.AGENTS else r.agent_key} for r in rs]}


@router.get("/findings")
async def findings(request: Request, agent: str | None = None, severity: str | None = None, run_id: str | None = None,
                   latest: bool = True, limit: int = 500):
    """Findings with their evidence. By default only from each agent's latest run."""
    org = _org(request)
    _office(request)
    q = select(WatchdogFinding).where(WatchdogFinding.org_id == org)
    if run_id:
        q = q.where(WatchdogFinding.run_id == run_id)
    elif latest:
        ids = [r.id for r in (await _latest_runs(org)).values()]
        if not ids:
            return {"items": [], "total": 0}
        q = q.where(WatchdogFinding.run_id.in_(ids))
    if agent:
        q = q.where(WatchdogFinding.agent_key == agent)
    if severity:
        q = q.where(WatchdogFinding.severity == severity)
    async with pm.session() as s:
        fs = (await s.execute(q.order_by(WatchdogFinding.created_at.desc()).limit(min(limit, 2000)))).scalars().all()
    items = [_finding_dict(f) for f in fs]
    order = {"high": 0, "warn": 1, "info": 2}
    items.sort(key=lambda f: (order.get(f["severity"], 3), f["agent"], f["subject_name"] or ""))
    return {"items": items, "total": len(items)}


@router.get("/settings")
async def get_settings(request: Request):
    org = _org(request)
    _office(request)
    country = await W.org_country(org)
    value = await get_setting(org, "ai_watchdogs", {}) or {}
    return {"value": value, "can_edit": _role(request) in ("org_admin", "superadmin"),
            "defaults": {a.key: await W.effective_thresholds(org, a.key, country) for a in W.agents_for(country)}}


class SettingsIn(BaseModel):
    value: dict


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request):
    """Writes the ``ai_watchdogs`` company-settings group (same table + audit the settings screen uses)."""
    org = _org(request)
    _admin(request)
    v = body.value or {}
    value = {"enabled": bool(v.get("enabled", True)),
             "disabled_agents": [k for k in (v.get("disabled_agents") or []) if k in W.AGENTS][:50],
             "thresholds": {}, "notify": bool(v.get("notify", True))}
    for key, th in (v.get("thresholds") or {}).items():
        if key not in W.AGENTS or not isinstance(th, dict):
            continue
        clean = {}
        for name, val in th.items():
            if name not in W.AGENTS[key].thresholds and name not in W.AGENTS[key].threshold_help:
                continue
            if val in (None, ""):
                continue
            if isinstance(val, list):
                clean[name] = [str(x)[:10] for x in val][:40]
            else:
                try:
                    clean[name] = float(val) if float(val) != int(float(val)) else int(float(val))
                except (TypeError, ValueError):
                    raise HTTPException(422, f"{name} must be a number")
        if clean:
            value["thresholds"][key] = clean
    async with pm.session() as s:
        row = (await s.execute(select(OrgSetting).where(OrgSetting.org_id == org, OrgSetting.key == "ai_watchdogs"))).scalar_one_or_none()
        old = merged("ai_watchdogs", row.value if row else None)
        if row is None:
            row = OrgSetting(org_id=org, key="ai_watchdogs", value=value)
            s.add(row)
        row.value = value
        row.updated_by, row.updated_at = request.headers.get("x-user-email"), pm.now_utc()
        if old != value:
            s.add(OrgSettingAudit(org_id=org, key="ai_watchdogs", old_value=old, new_value=value,
                                  actor_id=request.headers.get("x-user-id"), actor_email=request.headers.get("x-user-email")))
        await s.commit()
    invalidate(org)
    return {"key": "ai_watchdogs", "value": value, "saved": True, "label": GROUPS["ai_watchdogs"]["label"]}
