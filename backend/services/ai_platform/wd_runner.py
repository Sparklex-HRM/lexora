"""Runs the watchdogs, keeps the run log, stores findings, tells people.

* ``run_agent`` — one agent for one org: reads (SELECT only), compares the findings
  with the previous run of the same agent (``fingerprint``) so only NEW findings
  trigger a notification, writes the run row + finding rows.
* ``scheduler`` — every tick, for every active org, runs each enabled agent whose
  cadence has elapsed since its last run.
* Notifications go through ``shared.utils.notify`` (in-app + e-mail with honest
  status). The title carries counts only — never a name, an amount or an id.
"""
import asyncio
import logging
import os
import time
from datetime import timedelta

from sqlalchemy import select, text

from shared.utils import platform_models as pm
from shared.utils.notify import notify
from shared.utils.org_settings import get_setting

import watchdogs as W
from g5_models import WatchdogFinding, WatchdogRun

log = logging.getLogger("lexora.watchdogs")
TICK_SECONDS = int(os.getenv("WATCHDOG_TICK_SECONDS", "900") or 900)
LINK = "/ai/compliance-health"


async def is_enabled(org: str, key: str) -> bool:
    if not await get_setting(org, "ai_watchdogs.enabled", True):
        return False
    return key not in (await get_setting(org, "ai_watchdogs.disabled_agents", []) or [])


async def _previous_fingerprints(org: str, key: str) -> set[str]:
    async with pm.session() as s:
        last = (await s.execute(select(WatchdogRun.id).where(WatchdogRun.org_id == org, WatchdogRun.agent_key == key,
                                                             WatchdogRun.status.in_(("ok", "attention")))
                                .order_by(WatchdogRun.started_at.desc()).limit(1))).scalar_one_or_none()
        if not last:
            return set()
        fps = (await s.execute(select(WatchdogFinding.fingerprint).where(WatchdogFinding.run_id == last))).scalars().all()
    return set(fps)


async def run_agent(org: str, key: str, trigger: str = "schedule") -> dict:
    a = W.AGENTS[key]
    country = await W.org_country(org)
    th = await W.effective_thresholds(org, key, country)
    run = WatchdogRun(org_id=org, agent_key=key, trigger=trigger, thresholds=th)
    t0 = time.monotonic()
    findings, read, skip_reason, error = [], 0, None, None
    if a.countries and country not in a.countries:
        skip_reason = f"Only for {', '.join(a.countries)} companies."
    else:
        try:
            findings, read, skip_reason = await a.run(org, {"thresholds": th, "country": country})
        except Exception as e:                               # noqa: BLE001
            error = f"{type(e).__name__}: {str(e)[:300]}"
            log.warning("watchdog %s failed for %s: %s", key, org, error)
    prev = await _previous_fingerprints(org, key) if findings else set()
    new = [f for f in findings if f["fingerprint"] not in prev]
    run.finished_at = pm.now_utc()
    run.duration_ms = int((time.monotonic() - t0) * 1000)
    run.rows_read = read
    run.findings_count = len(findings)
    run.new_findings = len(new)
    run.severity_max = W.R.max_severity(findings)
    if error:
        run.status, run.error, run.summary = "error", error, "The check could not run."
    elif skip_reason and not findings:
        run.status, run.summary = "skipped", skip_reason
    elif not findings and read == 0:
        # Nothing could be read: the module's table may not be installed, or there are no rows.
        # That is "skipped", never a pass.
        run.status, run.summary = "skipped", "No rows to check — the records are empty or that module is not installed here."
    elif findings:
        run.status = "attention"
        run.summary = f"{len(findings)} finding(s) from {read} row(s) read; {len(new)} new since the last run."
    else:
        run.status, run.summary = "ok", f"Clean — {read} row(s) read, nothing to flag."
    async with pm.session() as s:
        s.add(run)
        await s.flush()
        for f in findings:
            s.add(WatchdogFinding(org_id=org, run_id=run.id, agent_key=key, severity=f["severity"], confidence=f["confidence"],
                                  subject_employee_id=f.get("subject_employee_id"), subject_name=f.get("subject_name"),
                                  title=f["title"][:300], explanation=f["explanation"], evidence=f["evidence"],
                                  fingerprint=f["fingerprint"], is_new=f["fingerprint"] not in prev))
        await s.commit()
    if new and await get_setting(org, "ai_watchdogs.notify", True):
        worst = [f for f in new if f["severity"] in ("warn", "high")]
        if worst:
            await notify(org, roles=a.notify_roles, kind="watchdog",
                         title=f"{a.name}: {len(worst)} new finding(s) need a look",
                         body=f"{a.name} found {len(findings)} item(s) in its latest run ({len(new)} new). Open Compliance health to see the evidence.",
                         link=f"{LINK}?agent={key}")
            async with pm.session() as s:
                await s.execute(text("UPDATE ai_wd_runs SET notified = :t WHERE id = :id"), {"t": True, "id": run.id})
                await s.commit()
            run.notified = True
    return run_dict(run)


def run_dict(r: WatchdogRun) -> dict:
    return {"id": r.id, "agent_key": r.agent_key, "trigger": r.trigger, "status": r.status, "started_at": pm.iso(r.started_at),
            "finished_at": pm.iso(r.finished_at), "duration_ms": r.duration_ms, "rows_read": r.rows_read,
            "findings_count": r.findings_count, "new_findings": r.new_findings, "severity_max": r.severity_max,
            "summary": r.summary, "thresholds": r.thresholds or {}, "notified": bool(r.notified), "error": r.error}


async def run_all(org: str, trigger: str = "manual", only_due: bool = False) -> list[dict]:
    country = await W.org_country(org)
    out = []
    for a in W.agents_for(country):
        if not await is_enabled(org, a.key):
            continue
        if only_due and not await _due(org, a):
            continue
        out.append(await run_agent(org, a.key, trigger))
    return out


async def _due(org: str, a: W.Agent) -> bool:
    async with pm.session() as s:
        last = (await s.execute(select(WatchdogRun.started_at).where(WatchdogRun.org_id == org, WatchdogRun.agent_key == a.key)
                                .order_by(WatchdogRun.started_at.desc()).limit(1))).scalar_one_or_none()
    if not last:
        return True
    return pm.aware(last) + timedelta(minutes=a.cadence_minutes) <= pm.now_utc()


async def _active_orgs() -> list[str]:
    try:
        async with pm.session() as s:
            r = (await s.execute(text("SELECT id FROM organizations WHERE is_active IS TRUE LIMIT 500"))).all()
        return [str(x[0]) for x in r]
    except Exception:                                        # noqa: BLE001
        return []


async def scheduler():
    """Background loop started from the service lifespan."""
    await asyncio.sleep(20)                                  # let the other services come up first
    while True:
        try:
            for org in await _active_orgs():
                await run_all(org, trigger="schedule", only_due=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.warning("watchdog tick failed: %s", e)
        await asyncio.sleep(TICK_SECONDS)
