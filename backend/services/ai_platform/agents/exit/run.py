"""Exit Agent — run orchestration + triggers.

One run = start_run → inputs.collect (read only) → checks.run_checks (EX-01 … EX-05) → items stored → chase (tier 0, the
executor opens the deduplicated task to the clearance owner / HR / payroll; SHADOW: recorded only) → proposals the checks
asked for: exit.run_final_pay (2) · exit.draft_exit_letters (1), each once per case → finish_run.

Triggers (``tick_hook``): ``exit_case`` — an exit case created / updated after the last run; ``daily`` — once per org local
day (the final-pay and ROE clocks are in days, so the daily pass keeps them honest). ``POST /ai/agents/exit/run`` = manual.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

sys.path.append("/app")
from shared.utils.org_time import org_today  # noqa: E402

import agentic_proposals as PR  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
from agents import lifecycle_common as LC  # noqa: E402
from watchdogs import ORG, rows  # noqa: E402

from . import AGENT, UI_LINK  # noqa: E402
from . import executors as EX  # noqa: F401,E402
from . import inputs as IN  # noqa: E402
from .checks import DEFAULTS, run_checks  # noqa: E402

log = logging.getLogger("lexora.agentic.exit")


def settings_of(st: dict) -> dict:
    return LC.agent_settings(st, AGENT, DEFAULTS)


async def run_exit(org: str, *, trigger: str, requested_by: str | None = None, cases: list[dict] | None = None, today=None,
                   logins: dict | None = None) -> dict:
    st = await ST.agentic_state(org)
    cfg = settings_of(st)
    today = today or await org_today(org)
    run = await RUNS.start_run(org, agent=AGENT, trigger=trigger, requested_by_user_id=requested_by)
    try:
        sources = {"cases": "fixture"} if cases is not None else {}
        full_scan = cases is None                                # the run reads the whole population itself
        if cases is None:
            got = await IN.collect(org, today=today)
            cases, sources = got["cases"], got["sources"]
        res = run_checks(cases, today, cfg)
        stored = await LC.store_items(org, AGENT, run["id"], res["items"], scanned_cases={f"case:{c['id']}" for c in cases},
                                      full_scan=full_scan)
        lg = logins if logins is not None else await LC.logins(org)
        ch = await LC.chase(org, run, AGENT, stored, lg, cfg, link_base=UI_LINK,
                            manager_of={str(c["employee"]["id"]): c["employee"].get("manager_id") for c in cases},
                            hours_for={"employee": cfg["chase_owner_hours"], "manager": cfg["chase_owner_hours"], "it": cfg["chase_owner_hours"],
                                       "admin": cfg["chase_owner_hours"], "finance": cfg["chase_owner_hours"], "hr": cfg["chase_hr_hours"],
                                       "payroll": cfg["chase_payroll_hours"], "default": cfg["chase_every_hours"]},
                            data_sources=["exit_cases", "exit_clearance_items", "pay_runs", "issued_letters"])
        await LC.set_task_ids(org, {x["id"]: x["task_id"] for x in stored if x.get("task_id") and x.get("id")})
        proposed = []
        for a in res["actions"]:
            match = {"case_id": a["args"]["case_id"]}
            if await LC.already_proposed(org, AGENT, a["tool"], match, mode=st["mode"]):
                continue
            impact = {"headcount": a.get("headcount", 1)}
            p = await PR.propose(org, agent=AGENT, tool_name=a["tool"], run_id=run["id"], tool_args=a["args"], plain_action=a["plain_action"][:600],
                                 reason=a["reason"], confidence="high", aggregate_impact=impact, requested_by_user_id=requested_by,
                                 evidence_refs=[{"label": "Exit case", "link": f"/exits/{a['args']['case_id']}"}],
                                 reversal_plan=({"tool": "payroll.off_cycle_void", "note": "A final-pay run that was not yet paid out can be voided in Payroll → Off-cycle."}
                                                if a["tool"] == "exit.run_final_pay" else None))
            proposed.append({"tool": a["tool"], "case_id": a["args"]["case_id"], "proposal_id": p["id"], "status": p["status"], "tier": p.get("risk_tier")})
        counts = LC.summary_counts(stored)
        overdue = sum(1 for c in res["clocks"].values() if c.get("status") == "overdue")
        headline = f"{len(cases)} open exit(s): {counts['blocking']} blocking, {counts['warning']} to do, {overdue} final pay(s) past the clock"
        summary = {"headline": headline, "run_date": today.isoformat(), "cases": len(cases), "counts": counts, "clocks": res["clocks"], "checks": res["checks"],
                   "sources": sources, "chase": {k: v for k, v in ch.items() if k != "would_open"}, "would_open": ch["would_open"][:100],
                   "proposed": proposed, "mode": st["mode"], "trigger": trigger,
                   "shadow_note": ("Shadow mode — nothing was sent; this is what I would have done." if st["mode"] == "shadow" else None)}
        out = await RUNS.finish_run(org, run["id"], summary=summary, checks_run=res["checks_run"], findings=counts["blocking"] + counts["warning"])
        return {**out, "chase": ch, "items": len(stored), "proposed": proposed}
    except Exception as e:                                       # noqa: BLE001
        log.exception("exit run failed for %s", org)
        try:
            await RUNS.finish_run(org, run["id"], summary={"headline": f"Run failed: {type(e).__name__}", "run_date": today.isoformat()},
                                  error=f"{type(e).__name__}: {str(e)[:300]}")
        except Exception:                                        # noqa: BLE001
            pass
        raise


async def tick_hook(org: str, st: dict) -> None:
    if not st.get("enabled"):
        return
    today = await org_today(org)
    if not await LC.ran_today(org, AGENT, "daily", today):
        await run_exit(org, trigger="daily")
        return
    runs = await RUNS.list_runs(org, agent=AGENT, limit=1)
    if runs and runs[0].get("started_at"):
        since = datetime.fromisoformat(str(runs[0]["started_at"]).replace("Z", "+00:00"))
        if await rows(f"SELECT id FROM exit_cases WHERE org_id = {ORG} AND updated_at > :t LIMIT 1", {"org": org, "t": since}):
            await run_exit(org, trigger="exit_case")


def install_hook() -> None:
    import agentic_scheduler
    if tick_hook not in agentic_scheduler.tick_hooks:
        agentic_scheduler.tick_hooks.append(tick_hook)
