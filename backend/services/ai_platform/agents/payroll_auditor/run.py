"""Payroll Auditor — run orchestration + triggers.

    from agents.payroll_auditor.run import run_payroll_audit
    await run_payroll_audit(org, trigger="manual", pay_period="biweekly:2026-10-04..2026-10-17", requested_by=uid)

One run = start_run → collect inputs (read only; the DRAFT comes from the engine's dry run) → 17 checks → deterministic
linking (+ validated model rephrase) → findings stored → chase proposals (tier 0: one per fixable gap, the executor opens
the task; in SHADOW mode they are only recorded, nothing is opened or sent) → freeze / send-for-approval / ready-to-release
proposals when the gates allow → finish_run with counts + cost.

Triggers (``tick_hook`` on the foundation scheduler, every 15 min per enabled org), per pay frequency in use:
* T-N: ``today >= pay_date - thresholds.lead_days`` (default 3) and no T-N run for the period yet;
* timesheet lock: every timesheet of every active employee on the frequency who keeps timesheets is LOCKED for the period;
* shadow cycles: when a committed engine run appears for a period the agent audited in shadow mode, ``bump_shadow_cycles``
  once per period (recorded as a value event ``shadow_cycle_completed``).
``POST /ai/agents/payroll-auditor/run`` is the manual trigger (router.py).
"""
from __future__ import annotations

import logging
import sys
from datetime import timedelta

from sqlalchemy import select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_proposals as PR  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_state as ST  # noqa: E402
import agentic_value as VAL  # noqa: E402
from agentic_models import AgentValueEvent  # noqa: E402

from . import executors as EX  # noqa: F401,E402  (registers the executors on import)
from . import explain as XP  # noqa: E402
from . import inputs as IN  # noqa: E402
from . import pack as PK  # noqa: E402
from . import thresholds as TH  # noqa: E402
from .checks import money, run_checks  # noqa: E402

log = logging.getLogger("lexora.agentic.payroll")
AGENT = "payroll_auditor"
LINK = "/ai/agents/payroll-auditor"
MAX_TASKS_PER_RUN = 200
CHASE_HOURS = {"employee": "chase_employee_hours", "manager": "chase_manager_hours", "hr": "chase_hr_hours", "payroll": "chase_hr_hours"}
# Warnings a PERSON has to act on (joiner data, final pay / ROE timing, unpaid-leave gaps, retro approval); every other
# warning is review-pack material for the checker. Unexplained variances are chased so somebody writes the reason.
CHASE_WARNINGS = ("PA-02", "PA-03", "PA-04", "PA-10")
CHASE_UNEXPLAINED = ("PA-05", "PA-06", "PA-16")


# ══════════════════════════ model client ════════════════════════════════════════

async def _model_client(org: str):
    """(client, model, effort, cfg) from the org's AI settings — None client when no key / SDK / feature off."""
    try:
        import ai_config as CFG
        import claude_client as LLM
        cfg = await CFG.load(org)
        key, _src = CFG.resolve_key(cfg)
        if key and CFG.sdk_available() and (cfg.get("features") or {}).get("agents", True):
            return LLM.client_for(key), cfg["model"], cfg.get("effort") or "low", cfg
        return None, cfg.get("model"), cfg.get("effort") or "low", cfg
    except Exception as e:                                       # noqa: BLE001
        log.info("model client unavailable for %s: %s", org, e)
        return None, None, "low", {}


def _cost(cfg: dict, model: str | None, usage: dict) -> dict:
    try:
        from metering import cost_of
        return cost_of(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0), overrides=(cfg or {}).get("prices"),
                       usd_cad_rate=(cfg or {}).get("usd_cad_rate"), cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0))
    except Exception:                                            # noqa: BLE001
        return {"cost_usd": 0.0, "cost_cad": None}


# ══════════════════════════ owners + chase ══════════════════════════════════════

def _office_user(inputs: dict, role: str) -> str | None:
    lg = inputs.get("logins") or {}
    office = (lg.get("hr") or []) if role == "hr" else (lg.get("payroll") or [])
    office = office or (lg.get("payroll") or []) or (lg.get("hr") or []) or (lg.get("org_admin") or [])
    return office[0] if office else None


def _owner_for(f: dict, inputs: dict) -> tuple[str | None, str | None, str]:
    """→ (owner_user_id, owner_employee_id, resolved_role). Employee for their own punches / leave, manager for a pending
    regularisation / leave, HR for joiner data / letters / bank details, payroll for variances and engine inputs."""
    lg = inputs.get("logins") or {}
    by_emp = lg.get("by_employee") or {}
    emps = {str(e["id"]): e for e in inputs.get("employees") or []}
    role = f.get("owner_role") or "payroll"
    eid = str(f.get("employee_id") or "")
    if role == "employee" and eid:
        return by_emp.get(eid), (None if by_emp.get(eid) else eid), "employee"
    if role == "manager":
        mid = str((f.get("linked_event") or {}).get("manager_id") or (emps.get(eid) or {}).get("manager_id") or "")
        if mid:
            return by_emp.get(mid), (None if by_emp.get(mid) else mid), "manager"
        role = "hr"
    return _office_user(inputs, role), None, role


def _task_args(f: dict, inputs: dict, th: dict) -> dict | None:
    uid, emp_owner, role = _owner_for(f, inputs)
    if not uid and not emp_owner:
        return None
    hours = float(th.get(CHASE_HOURS.get(role, "chase_hr_hours")) or 24)
    period = inputs["period"]
    key_bits = [f["code"], f.get("employee_id") or f.get("cost_centre") or f.get("component") or "-", f.get("component") or "-", period]
    if (f.get("linked_event") or {}).get("type") in ("regularisation_pending", "leave_pending"):
        key_bits.append(str((f.get("linked_event") or {}).get("type")))
    what = f.get("title") or f.get("rule")
    unexplained = f.get("status") == "unexplained" and f["code"] in XP.LINKABLE
    title = (f"Explain the change: {what}" if unexplained else what)[:300]
    return {"kind": ("variance_unexplained" if unexplained else f["code"].lower().replace("-", "_")), "title": title,
            "detail": {"what": what, "why": f.get("rule"),
                       "how_to_fix": f.get("fix_hint") or ("Write the reason on the review pack (Payroll Auditor → the row → Explain)." if unexplained else None),
                       "link": f"{LINK}?period={period}&finding={f.get('id') or ''}", "code": f["code"], "severity": f["severity"], "component": f.get("component"),
                       "delta_cad": f.get("delta_cad"), "dollars_at_risk": f.get("dollars_at_risk"), "evidence_refs": (f.get("evidence_refs") or [])[:5]},
            "owner_user_id": uid, "owner_employee_id": emp_owner, "employee_id": f.get("employee_id"), "run_ref": str((inputs.get("current_run") or {}).get("id") or ""),
            "dedupe_key": ":".join(str(x) for x in key_bits)[:160], "due_hours": hours * 2, "first_chase_in_hours": hours, "finding_id": f.get("id"), "owner_role": role}


def _chaseable(f: dict) -> bool:
    if f["severity"] == "critical":
        return True
    if f["code"] in CHASE_WARNINGS:
        return True
    return f.get("status") == "unexplained" and f["code"] in CHASE_UNEXPLAINED


async def _chase(org: str, run: dict, inputs: dict, findings: list[dict], th: dict) -> dict:
    """One tier-0 proposal per fixable gap. Live: the executor opens the task (in-app + e-mail). Shadow: recorded only."""
    out = {"proposed": 0, "opened": 0, "deduplicated": 0, "skipped_no_owner": 0, "would_open": []}
    n = 0
    for f in findings:
        if f["severity"] == "info" or f.get("status") in ("explained", "confirmed", "dismissed") or not _chaseable(f):
            continue
        if n >= MAX_TASKS_PER_RUN:
            break
        args = _task_args(f, inputs, th)
        if not args:
            out["skipped_no_owner"] += 1
            continue
        n += 1
        p = await PR.propose(org, agent=AGENT, tool_name="payroll.chase_input", run_id=run["id"], tool_args=args,
                             plain_action=f"Ask {args['owner_role']} to fix: {args['title']}"[:600], reason=f.get("rule") or "", confidence="high",
                             # a chase opens a work item and moves no money: the dollars at risk are on the card, never the impact
                             aggregate_impact={"headcount": 1 if f.get("employee_id") else 0},
                             evidence_refs=(f.get("evidence_refs") or [])[:5], requested_by_user_id=run.get("requested_by_user_id"),
                             data_sources_read=["employees", "ta_regularizations", "leave_requests", "pay_stubs", "payroll dry run"])
        out["proposed"] += 1
        if p["status"] == "shadow":
            out["would_open"].append({"owner_role": args["owner_role"], "title": args["title"], "code": f["code"], "employee_ref": f.get("employee_ref"), "proposal_id": p["id"]})
        elif p["status"] == "executed":
            res = (p.get("result") or {}).get("detail") or p.get("result") or {}
            out["opened"] += 0 if res.get("deduplicated") else 1
            out["deduplicated"] += 1 if res.get("deduplicated") else 0
            if res.get("task_id"):
                f["task_id"] = res["task_id"]
    return out


async def _already_proposed(org: str, tool: str, period: str, *, include_executed: bool = True) -> bool:
    statuses = "proposed,second_approval,edited,approved" + (",executed" if include_executed else "")
    for p in await PR.list_proposals(org, agent=AGENT, status=statuses, limit=300):
        if p["tool_name"] == tool and str((p.get("tool_args") or {}).get("period")) == period:
            return True
    return False


# ══════════════════════════ the run ═════════════════════════════════════════════

async def run_payroll_audit(org: str, *, trigger: str, pay_period: str | None = None, pay_frequency: str | None = None, requested_by: str | None = None,
                            inputs: dict | None = None, client=None, model: str | None = None) -> dict:
    st = await ST.agentic_state(org)
    th = TH.effective(st)
    run = await RUNS.start_run(org, agent=AGENT, trigger=trigger, requested_by_user_id=requested_by)
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    cfg, used_model = {}, None
    try:
        inp = inputs or await IN.collect(org, period=pay_period, pay_frequency=pay_frequency, th=th)
        res = run_checks(inp, th)
        findings = XP.link_all(res["findings"], inp)
        inp.setdefault("timing", {})["unexplained"] = sum(1 for f in findings if f.get("status") == "unexplained")
        if client is None:
            client, model, effort, cfg = await _model_client(org)
        else:
            effort = "low"
        for i, f in enumerate(findings):
            f["_id"] = f"f{i}"
        rp = await XP.rephrase(client, model=model or "", effort=effort, findings=findings, org_label=str(org)[:8], today=str(inp.get("today") or ""))
        usage = rp["usage"]
        used_model = rp.get("model")
        if rp["attempted"] and client is not None:
            await LOG.append_event(org, event_type="model.rephrase", agent_name=AGENT, run_id=run["id"], tool_name="explain_variance", model_id=used_model,
                                   prompt_template_id=rp["prompt_template_id"], prompt_hash=rp["prompt_hash"], **usage,
                                   tool_input={"attempted": rp["attempted"], "accepted": rp["accepted"], "rejected": rp["rejected"], "fallback": rp["fallback_reason"]},
                                   risk_tier=0, policy_decision="allow")
        for f in findings:
            f.pop("_id", None)
        stored = await PK.store_findings(org, run["id"], inp["period"], findings)
        chase = await _chase(org, run, inp, findings, th)
        await PK.set_task_ids(org, {f["id"]: f["task_id"] for f in findings if f.get("task_id") and f.get("id")})
        explained_ids = [f["id"] for f in findings if f.get("status") == "explained" and f["code"] in XP.LINKABLE]
        if explained_ids:
            await PR.propose(org, agent=AGENT, tool_name="payroll.mark_variance_explained", run_id=run["id"],
                             tool_args={"run_id": run["id"], "finding_ids": explained_ids, "by": "model" if rp["accepted"] else "template", "period": inp["period"]},
                             plain_action=f"Record {len(explained_ids)} variance(s) as explained by a linked event ({rp['accepted']} rephrased by the model, validated)",
                             reason="Deterministic link: joiner / leaver / revision / retro / unpaid days / hours / one-time / TD1 / bonus / maximum / new year / income",
                             confidence="high", aggregate_impact={"items": len(explained_ids)}, requested_by_user_id=requested_by)
        gates = _gates(inp, res, findings)
        proposals_made = []
        att = inp.get("attendance") or {}
        proj = PK.summary_totals(inp.get("current") or {}, inp.get("previous") or {})
        if gates["can_lock"] and not await _already_proposed(org, "payroll.lock_inputs", inp["period"], include_executed=False):
            p = await PR.propose(org, agent=AGENT, tool_name="payroll.lock_inputs", run_id=run["id"],
                                 tool_args={"period": inp["period"], "period_start": inp["period_start"].isoformat(), "period_end": inp["period_end"].isoformat(),
                                            "hr_user_id": _office_user(inp, "hr")},
                                 plain_action=f"Freeze the inputs for {inp['period']}: every timesheet is approved and no blocking finding is open — HR is asked to lock them",
                                 reason=f"T-{th['lead_days']} freeze — {att.get('ready')}/{att.get('total')} timesheets approved; locking stops late edits before the run",
                                 before={"inputs_frozen": False}, after={"inputs_frozen": True}, confidence="high", requested_by_user_id=requested_by,
                                 aggregate_impact={"headcount": 0})
            proposals_made.append(p)
        if gates["can_send"] and not await _already_proposed(org, "payroll.send_for_approval", inp["period"], include_executed=False):
            net = proj["this_period"]["net"]
            p = await PR.propose(org, agent=AGENT, tool_name="payroll.send_for_approval", run_id=run["id"],
                                 tool_args={"period": inp["period"], "pay_frequency": inp.get("pay_frequency"), "period_start": inp["period_start"].isoformat(),
                                            "period_end": inp["period_end"].isoformat(), "pay_date": inp["pay_date"].isoformat(),
                                            "employees": len(inp.get("current") or {}), "net_label": money(net), "payroll_user_id": _office_user(inp, "payroll")},
                                 plain_action=f"Approve the {inp['period']} draft for commit in Payroll: {len(inp.get('current') or {})} employees, net {money(net)}"[:600],
                                 reason="Timesheets locked (or not tracked), no blocking finding, every critical variance explained. A person commits the run in Payroll; the engine's own gates still apply.",
                                 before={"engine_run": None}, after={"ready_for_commit": True, "net": net, "gross": proj["this_period"]["gross"]},
                                 aggregate_impact={"headcount": len(inp.get("current") or {}), "dollars": net}, confidence="high" if not gates["warnings"] else "medium",
                                 evidence_refs=[{"label": "Review pack", "link": f"{LINK}?period={inp['period']}"}], requested_by_user_id=requested_by)
            proposals_made.append(p)
        if gates["can_release"] and not await _already_proposed(org, "payroll.ready_to_release", inp["period"]):
            p = await PR.propose(org, agent=AGENT, tool_name="payroll.ready_to_release", run_id=run["id"],
                                 tool_args={"period": inp["period"], "engine_run_id": str((inp.get("current_run") or {}).get("id") or "")},
                                 plain_action=f"Mark {inp['period']} ready to release pay stubs and deposits (release itself stays in Payroll)",
                                 reason="The committed run ties out (register = GL journal) and no blocking finding is open.",
                                 aggregate_impact={"headcount": len(inp.get("current") or {}), "dollars": proj["this_period"]["net"]},
                                 confidence="high", evidence_refs=[{"label": "Tie-out block", "link": f"{LINK}?period={inp['period']}"}], requested_by_user_id=requested_by)
            proposals_made.append(p)
        summary = PK.pack_summary(inp, findings, [dict(c) for c in res["checks"]])
        summary.update({"timing": {"lead_days": th["lead_days"], "freeze_by": (inp["pay_date"] - timedelta(days=th["lead_days"])).isoformat() if inp.get("pay_date") else None,
                                   "approve_by": (inp["pay_date"] - timedelta(days=th["approval_lead_days"])).isoformat() if inp.get("pay_date") else None,
                                   "attendance_locked_at": summary["attendance"].get("locked_at"), "draft_at": pm.iso(pm.now_utc())},
                        "gates": gates, "chase": {k: v for k, v in chase.items() if k != "would_open"}, "would_open": chase["would_open"][:100],
                        "thresholds": {k: th.get(k) for k in ("preset", "net_delta_pct", "net_delta_floor_cad", "deduction_floor_cad", "bank_change_pct", "cost_centre_pct",
                                                                "tax_delta_pct", "tax_floor_cad", "rate_tolerance_cad", "lead_days", "approval_lead_days")},
                        "model": {"model": used_model, "rephrased": rp["accepted"], "rejected": rp["rejected"], "fallback": rp["fallback_reason"]},
                        "waiting_on": ([f"{w['owner_role']}: {w['title']}" for w in chase["would_open"][:20]] if st["mode"] == "shadow" else
                                       [f"{_owner_for(f, inp)[2]}: {f.get('title')}" for f in findings if f.get("task_id")][:20]),
                        "trigger": trigger, "mode": st["mode"], "shadow_note": ("Shadow mode — nothing was sent; this is what I would have done." if st["mode"] == "shadow" else None)})
        cost = {"cost_usd": 0.0, "cost_cad": None}
        if usage.get("input_tokens") or usage.get("output_tokens"):
            cost = _cost(cfg, used_model, usage)
            if cost.get("cost_usd") is None and model:
                cost = _cost(cfg, model, usage)
        out = await RUNS.finish_run(org, run["id"], summary=summary, checks_run=res["checks_run"], findings=len([f for f in findings if f["severity"] != "info"]),
                                    input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0), cache_read_tokens=usage.get("cache_read_tokens", 0),
                                    cache_write_tokens=usage.get("cache_write_tokens", 0), cost_usd=float(cost.get("cost_usd") or 0), cost_cad=cost.get("cost_cad"))
        return {**out, "findings_stored": len(stored), "chase": chase, "gates": gates, "proposals_made": [p["id"] for p in proposals_made]}
    except Exception as e:                                       # noqa: BLE001 — the run record must say what broke
        log.exception("payroll audit failed for %s", org)
        try:
            await RUNS.finish_run(org, run["id"], summary={"headline": f"Run failed: {type(e).__name__}", "pay_period": pay_period}, error=f"{type(e).__name__}: {str(e)[:300]}")
        except Exception:                                        # noqa: BLE001
            pass
        raise


def _gates(inp: dict, res: dict, findings: list[dict]) -> dict:
    att = inp.get("attendance") or {}
    blocking = [f for f in res["blocking"] if f.get("status") not in ("dismissed",)]
    unexplained = [f for f in findings if f.get("status") == "unexplained" and f["severity"] == "critical" and f["code"] in XP.LINKABLE]
    has_draft = bool(inp.get("current")) and not inp.get("current_run")
    committed = bool(inp.get("current_run"))
    tracked, locked = bool(att.get("tracked")), bool(att.get("locked"))
    tie_bad = [f for f in findings if f["code"] == "PA-11" and f["severity"] == "critical"]
    return {"blocking": len(blocking), "blocking_codes": sorted({f["code"] for f in blocking}), "critical_unexplained": len(unexplained),
            "warnings": sum(1 for f in findings if f["severity"] == "warning"), "attendance_tracked": tracked, "attendance_locked": locked,
            "can_lock": tracked and not locked and int(att.get("ready") or 0) >= int(att.get("total") or 0) > 0 and not blocking,
            "can_send": has_draft and (locked or not tracked) and not blocking and not unexplained,
            "can_release": committed and not blocking and not tie_bad,
            "why_not": (("blocking findings open; " if blocking else "") + ("timesheets not locked; " if (tracked and not locked) else "") +
                        ("no draft from the engine; " if not (has_draft or committed) else "") + ("critical variance unexplained; " if unexplained else "")).strip("; ") or None}


# ══════════════════════════ triggers ════════════════════════════════════════════

async def _runs_for_period(org: str, period: str) -> list[dict]:
    return [r for r in await RUNS.list_runs(org, agent=AGENT, limit=300) if (r.get("summary") or {}).get("pay_period") == period]


async def _shadow_bumped(org: str, period: str) -> bool:
    async with pm.session() as s:
        got = (await s.execute(select(AgentValueEvent).where(AgentValueEvent.org_id == str(org), AgentValueEvent.agent == AGENT,
                                                             AgentValueEvent.kind == "shadow_cycle_completed", AgentValueEvent.note == period))).scalars().all()
    return bool(got)


async def complete_shadow_cycle(org: str, period: str) -> int | None:
    """Once per pay period in shadow mode: the committed engine run closes the cycle → bump the gate counter. Only a
    period the agent actually audited in shadow counts."""
    if await _shadow_bumped(org, period):
        return None
    if not any(r.get("mode") == "shadow" and r.get("status") != "error" for r in await _runs_for_period(org, period)):
        return None
    n = await ST.bump_shadow_cycles(org)
    await VAL.record_outcome(org, agent=AGENT, kind="shadow_cycle_completed", units=1, note=period)
    await LOG.append_event(org, event_type="shadow.cycle", agent_name=AGENT, tool_name="bump_shadow_cycles", tool_input={"period": period, "shadow_cycles_done": n}, risk_tier=0, policy_decision="allow")
    return n


async def _frequencies(org: str) -> list[str]:
    from watchdogs import ORG, rows
    got = [r.get("pay_frequency") for r in await rows(f"SELECT DISTINCT pay_frequency FROM employees WHERE org_id = {ORG} AND is_active IS TRUE", {"org": org})]
    return [f for f in IN.PAY_FREQUENCIES if f in got] or [await IN.default_frequency(org)]


async def tick_hook(org: str, st: dict) -> None:
    """Registered on ``agentic_scheduler.tick_hooks``: T-N, timesheet lock, shadow-cycle completion — per pay frequency."""
    if not st.get("enabled"):
        return
    th = TH.effective(st)
    today = await org_today(org)
    for freq in await _frequencies(org):
        P = await IN.resolve_period(org, today=today, pay_frequency=freq)
        if st.get("mode") == "shadow" and P.get("previous_run"):
            await complete_shadow_cycle(org, P["prev_period"])
        runs = await _runs_for_period(org, P["period"])
        triggers = {r["trigger"] for r in runs if r["status"] != "error"}
        if "pay_date_minus_n" not in triggers and P["pay_date"] - timedelta(days=th["lead_days"]) <= today <= P["pay_date"] + timedelta(days=10):
            await run_payroll_audit(org, trigger="pay_date_minus_n", pay_period=P["period"])
            continue
        if "attendance_lock" not in triggers:
            emps = await IN.employees(org)
            active = {e["id"] for e in emps if e.get("is_active", True) and e.get("status") in ("active", "on_leave") and (e.get("pay_frequency") or freq) == freq}
            _, _, _, att = await IN.attendance(org, P["period_start"], P["period_end"], active)
            if att["tracked"] and att["locked"]:
                await run_payroll_audit(org, trigger="attendance_lock", pay_period=P["period"])


async def on_attendance_locked(org: str, period: str) -> dict | None:
    """Explicit event entry (the time service's ``timesheet.locked`` emit can call it); idempotent per period."""
    if any(r["trigger"] == "attendance_lock" for r in await _runs_for_period(org, period)):
        return None
    return await run_payroll_audit(org, trigger="attendance_lock", pay_period=period)


def install_hook() -> None:
    import agentic_scheduler
    if tick_hook not in agentic_scheduler.tick_hooks:
        agentic_scheduler.tick_hooks.append(tick_hook)
