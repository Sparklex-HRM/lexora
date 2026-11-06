"""Attendance Agent — run orchestration + the daily trigger.

One run = start_run → inputs.collect → checks.run_checks (AT-01, AT-02, AT-05, AT-06) → items stored (resolved when the
person fixed them: a regularisation raised / approved, leave decided) → chase (tier 0; employee first for punches, manager
for decisions — escalation by the foundation after N reminders) → finish_run with the pay-period cut-off so the pay run
sees resolved hours.

Trigger: ``daily`` — once per org local day (tick_hook); ``POST /ai/agents/attendance/run`` = manual.
"""
from __future__ import annotations

import logging
import sys

sys.path.append("/app")
from shared.utils.org_time import org_today  # noqa: E402

import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
from agents import lifecycle_common as LC  # noqa: E402

from . import AGENT, UI_LINK  # noqa: E402
from . import executors as EX  # noqa: F401,E402
from . import inputs as IN  # noqa: E402
from .checks import DEFAULTS, run_checks  # noqa: E402

log = logging.getLogger("lexora.agentic.attendance")


def settings_of(st: dict) -> dict:
    return LC.agent_settings(st, AGENT, DEFAULTS)


async def run_attendance(org: str, *, trigger: str, requested_by: str | None = None, inputs: dict | None = None, today=None,
                         logins: dict | None = None, now=None) -> dict:
    st = await ST.agentic_state(org)
    cfg = settings_of(st)
    today = today or (inputs or {}).get("today") or await org_today(org)
    run = await RUNS.start_run(org, agent=AGENT, trigger=trigger, requested_by_user_id=requested_by)
    try:
        inp = inputs or await IN.collect(org, today=today, window_days=cfg["window_days"])
        res = run_checks(inp, cfg, now=now)
        scanned = {f"emp:{e['id']}" for e in inp.get("employees") or []} | res["case_keys"]
        stored = await LC.store_items(org, AGENT, run["id"], res["items"], scanned_cases=scanned, full_scan=inputs is None)
        lg = logins if logins is not None else await LC.logins(org)
        ch = await LC.chase(org, run, AGENT, stored, lg, cfg, link_base=UI_LINK,
                            manager_of={str(e["id"]): e.get("manager_id") for e in inp.get("employees") or []},
                            hours_for={"employee": float(cfg["regularise_hours"]) / 2, "manager": float(cfg["approve_hours"]) / 3, "hr": cfg["chase_every_hours"],
                                       "default": cfg["chase_every_hours"]},
                            data_sources=["ta_attendance_days", "ta_regularizations", "leave_requests"])
        await LC.set_task_ids(org, {x["id"]: x["task_id"] for x in stored if x.get("task_id") and x.get("id")})
        counts = LC.summary_counts(stored)
        by = {c["code"]: c["items"] for c in res["checks"]}
        headline = (f"{by.get('AT-01', 0)} punch gap(s), {by.get('AT-02', 0)} late-mark case(s), {by.get('AT-05', 0)} regularisation(s) and "
                    f"{by.get('AT-06', 0)} leave waiting — cut-off {res['cutoff']['cutoff'] or '—'}")
        summary = {"headline": headline, "run_date": today.isoformat(), "counts": counts, "checks": res["checks"], "cutoff": res["cutoff"],
                   "sources": inp.get("sources") or {"inputs": "fixture"}, "chase": {k: v for k, v in ch.items() if k != "would_open"},
                   "would_open": ch["would_open"][:100], "mode": st["mode"], "trigger": trigger,
                   "pay_inputs_open": by.get("AT-01", 0) + by.get("AT-05", 0) + by.get("AT-06", 0),
                   "shadow_note": ("Shadow mode — nothing was sent; this is what I would have done." if st["mode"] == "shadow" else None)}
        out = await RUNS.finish_run(org, run["id"], summary=summary, checks_run=res["checks_run"], findings=counts["blocking"] + counts["warning"])
        return {**out, "chase": ch, "items": len(stored)}
    except Exception as e:                                       # noqa: BLE001
        log.exception("attendance run failed for %s", org)
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
        await run_attendance(org, trigger="daily")


def install_hook() -> None:
    import agentic_scheduler
    if tick_hook not in agentic_scheduler.tick_hooks:
        agentic_scheduler.tick_hooks.append(tick_hook)
