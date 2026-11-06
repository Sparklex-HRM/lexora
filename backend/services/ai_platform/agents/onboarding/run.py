"""Onboarding Agent — run orchestration + triggers.

    from agents.onboarding.run import run_onboarding
    await run_onboarding(org, trigger="manual", requested_by=uid)

One run = start_run → inputs.collect (read only) → checks.run_checks (OB-00 … OB-14) → items stored (resolved when fixed)
→ chase: one tier-0 ``onboarding.chase`` proposal per chaseable item (LIVE: the executor opens the deduplicated task for the
joiner / HR / IT / payroll; SHADOW: recorded only — nothing opened, nothing sent) → ``onboarding.draft_appointment_letter``
(tier 1) for each activated joiner without one → finish_run.

Triggers (``tick_hook`` on the foundation scheduler, every 15 min per enabled org):
* ``daily`` — once per org local day;
* ``joiner`` — an onboarding session / accepted offer / joiner record created after the last run started
  (offer accepted, joiner created).
``POST /ai/agents/onboarding/run`` is the manual trigger.
"""
from __future__ import annotations

import logging
import sys

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import agentic_proposals as PR  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
from agents import lifecycle_common as LC  # noqa: E402
from watchdogs import ORG, rows  # noqa: E402

from . import AGENT, UI_LINK  # noqa: E402
from . import executors as EX  # noqa: F401,E402  (registers the executors)
from . import inputs as IN  # noqa: E402
from .checks import DEFAULTS, run_checks  # noqa: E402

log = logging.getLogger("lexora.agentic.onboarding")


def settings_of(st: dict) -> dict:
    return LC.agent_settings(st, AGENT, DEFAULTS)


async def run_onboarding(org: str, *, trigger: str, requested_by: str | None = None, cases: list[dict] | None = None, today=None,
                         logins: dict | None = None) -> dict:
    st = await ST.agentic_state(org)
    cfg = settings_of(st)
    today = today or await org_today(org)
    run = await RUNS.start_run(org, agent=AGENT, trigger=trigger, requested_by_user_id=requested_by)
    try:
        sources = {"cases": "fixture"} if cases is not None else {}
        full_scan = cases is None                                # the run reads the whole population itself
        if cases is None:
            got = await IN.collect(org, today=today, lookback_days=cfg["lookback_days"], ahead_days=cfg["ahead_days"])
            cases, sources = got["cases"], got["sources"]
        res = run_checks(cases, today, cfg)
        stored = await LC.store_items(org, AGENT, run["id"], res["items"], scanned_cases={c["case_key"] for c in cases},
                                      full_scan=full_scan)
        lg = logins if logins is not None else await LC.logins(org)
        manager_of = {str(c["employee"]["id"]): c["employee"].get("manager_id") for c in cases if (c.get("employee") or {}).get("id")}
        ch = await LC.chase(org, run, AGENT, stored, lg, cfg, link_base=UI_LINK, manager_of=manager_of,
                            hours_for={"employee": cfg["chase_joiner_hours"], "hr": cfg["chase_hr_hours"], "it": cfg["chase_it_hours"],
                                       "admin": cfg["chase_it_hours"], "payroll": cfg["chase_hr_hours"], "default": cfg["chase_every_hours"]},
                            data_sources=["onboarding_sessions", "employees", "employee_documents", "onboarding_consents", "issued_letters", "onboarding_tasks"])
        await LC.set_task_ids(org, {x["id"]: x["task_id"] for x in stored if x.get("task_id") and x.get("id")})
        letters = []
        for c in cases:
            e = c.get("employee") or {}
            if not e.get("id") or c.get("letter") or c.get("kind") == "offer":
                continue
            if await LC.already_proposed(org, AGENT, "onboarding.draft_appointment_letter", {"employee_id": e["id"]}, mode=st["mode"]):
                continue
            p = await PR.propose(org, agent=AGENT, tool_name="onboarding.draft_appointment_letter", run_id=run["id"],
                                 tool_args={"employee_id": e["id"], "template_key": "appointment_letter", "case_key": c["case_key"]},
                                 plain_action=f"Draft the appointment letter for {e.get('name') or 'the joiner'} from your template (HR reviews and issues it)"[:600],
                                 reason="Joiner has no appointment letter on record; the draft carries no number and a DRAFT watermark until HR issues it.",
                                 before={"appointment_letter": None}, after={"appointment_letter": "draft"}, confidence="high",
                                 aggregate_impact={"headcount": 1}, requested_by_user_id=requested_by,
                                 evidence_refs=[{"label": "Joiner record", "link": f"/employees/{e['id']}?tab=letters"}],
                                 reversal_plan={"tool": "letters.void", "note": "A draft can be deleted / voided from Letters before it is issued."})
            letters.append({"employee_ref": e.get("name"), "proposal_id": p["id"], "status": p["status"]})
        counts = LC.summary_counts([x for x in stored])
        headline = (f"{len(cases)} joiner(s): {len(res['complete_for_payroll'])} complete for payroll, "
                    f"{counts['blocking']} blocking item(s), {counts['warning']} to do")
        summary = {"headline": headline, "run_date": today.isoformat(), "cases": len(cases), "counts": counts,
                   "complete_for_payroll": res["complete_for_payroll"][:200], "checks": res["checks"], "sources": sources,
                   "chase": {k: v for k, v in ch.items() if k != "would_open"}, "would_open": ch["would_open"][:100],
                   "letters_proposed": letters, "mode": st["mode"], "trigger": trigger,
                   "shadow_note": ("Shadow mode — nothing was sent; this is what I would have done." if st["mode"] == "shadow" else None)}
        out = await RUNS.finish_run(org, run["id"], summary=summary, checks_run=res["checks_run"], findings=counts["blocking"] + counts["warning"])
        return {**out, "chase": ch, "items": len(stored), "letters_proposed": letters}
    except Exception as e:                                       # noqa: BLE001 — the run record must say what broke
        log.exception("onboarding run failed for %s", org)
        try:
            await RUNS.finish_run(org, run["id"], summary={"headline": f"Run failed: {type(e).__name__}", "run_date": today.isoformat()},
                                  error=f"{type(e).__name__}: {str(e)[:300]}")
        except Exception:                                        # noqa: BLE001
            pass
        raise


async def _new_joiner_since(org: str, since) -> bool:
    for sql in (f"SELECT id FROM onboarding_sessions WHERE org_id = {ORG} AND created_at > :t LIMIT 1",
                f"SELECT id FROM hire_offers WHERE org_id = {ORG} AND status = 'accepted' AND responded_at > :t LIMIT 1",
                f"SELECT id FROM employees WHERE org_id = {ORG} AND created_at > :t LIMIT 1"):
        if await rows(sql, {"org": org, "t": since}):
            return True
    return False


async def tick_hook(org: str, st: dict) -> None:
    if not st.get("enabled"):
        return
    today = await org_today(org)
    runs = await RUNS.list_runs(org, agent=AGENT, limit=5)
    if not await LC.ran_today(org, AGENT, "daily", today):
        await run_onboarding(org, trigger="daily")
        return
    last = runs[0] if runs else None
    if last and last.get("started_at"):
        from datetime import datetime
        since = datetime.fromisoformat(str(last["started_at"]).replace("Z", "+00:00"))
        if await _new_joiner_since(org, since):
            await run_onboarding(org, trigger="joiner")


def install_hook() -> None:
    import agentic_scheduler
    if tick_hook not in agentic_scheduler.tick_hooks:
        agentic_scheduler.tick_hooks.append(tick_hook)
