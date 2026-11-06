"""Review pack — persistence of findings + the summary + the checker's actions.

    await store_findings(org, run_id, period, findings)              # after the checks + linking
    pack = await review_pack(org, run_id)                            # summary · grid · waiting-on · statutory · tie-out · timing
    await confirm(org, fid, actor=...) / dismiss(org, fid, actor=..., reason=...) / note(org, fid, actor=..., text=...)

* confirm → value outcome ``exception_confirmed`` with the dollars at risk, confirmed by the actor → counts in the
  AVOIDED layer of the value ledger;
* dismiss needs a reason (≥ 3 chars) — the next reviewer and the agent read it;
* every action is one row in the append-only action log.
"""
from __future__ import annotations

import sys
from datetime import datetime

from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

import agentic_log as LOG  # noqa: E402
import agentic_runs as RUNS  # noqa: E402
import agentic_tasks as TK  # noqa: E402
import agentic_value as VAL  # noqa: E402
from agentic_models import AgentRun  # noqa: E402

from .checks import BLOCKING_CODES, CHECKS, RANK  # noqa: E402
from .models import FINDING_STATUSES, PayrollFinding  # noqa: E402

AGENT = "payroll_auditor"


class PackError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def finding_dict(f: PayrollFinding) -> dict:
    return {"id": f.id, "run_id": f.run_id, "pay_period": f.pay_period, "code": f.code, "check": f.check_name, "severity": f.severity, "rank": f.rank,
            "employee_id": f.employee_id, "employee_ref": f.employee_ref, "department": f.department, "cost_centre": f.cost_centre, "component": f.component,
            "prev": f.prev, "curr": f.curr, "delta_cad": f.delta_cad, "delta_pct": f.delta_pct, "rule": f.rule, "threshold": f.threshold or {},
            "linked_event": f.linked_event, "evidence_refs": f.evidence_refs or [], "fix_hint": f.fix_hint, "owner_role": f.owner_role,
            "explanation": f.explanation, "explained_by": f.explained_by, "verified": f.verified, "dollars_at_risk": f.dollars_at_risk, "status": f.status,
            "checker_note": f.checker_note, "decided_by": f.decided_by, "decided_at": pm.iso(f.decided_at), "value_event_id": f.value_event_id,
            "task_id": f.task_id, "created_at": pm.iso(f.created_at), "title": (f.threshold or {}).get("_title") or f.rule}


def _json_safe(v):
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


async def store_findings(org: str, run_id: str, period: str, findings: list[dict]) -> list[dict]:
    """Persist the run's findings (status from the linking step); returns them with ids. Counts only in the log."""
    made = []
    async with pm.session() as s:
        for f in findings:
            th = _json_safe(dict(f.get("threshold") or {}))
            th["_title"] = f.get("title")
            st = f.get("status") or ("info" if f["severity"] == "info" else "unexplained")
            r = PayrollFinding(org_id=str(org), run_id=run_id, pay_period=period, code=f["code"], check_name=f.get("check") or f["code"], severity=f["severity"],
                               rank=int(f.get("rank", RANK.get(f["severity"], 2))), employee_id=f.get("employee_id"), employee_ref=f.get("employee_ref"),
                               department=f.get("department"), cost_centre=f.get("cost_centre"), component=f.get("component"), prev=f.get("prev"), curr=f.get("curr"),
                               delta_cad=f.get("delta_cad"), delta_pct=f.get("delta_pct"), rule=f.get("rule") or "", threshold=th,
                               linked_event=_json_safe(f.get("linked_event")), evidence_refs=f.get("evidence_refs") or [], fix_hint=f.get("fix_hint"),
                               owner_role=f.get("owner_role"), explanation=f.get("explanation"), explained_by=f.get("explained_by"), verified=f.get("verified"),
                               dollars_at_risk=f.get("dollars_at_risk"), status=st if st in FINDING_STATUSES else "unexplained", task_id=f.get("task_id"))
            s.add(r)
            made.append(r)
        await s.commit()
        out = [finding_dict(r) for r in made]
    for f, o in zip(findings, out):
        f["id"] = o["id"]
    by_code: dict = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    await LOG.append_event(org, event_type="run.findings", agent_name=AGENT, run_id=run_id, tool_name="store_findings",
                           tool_input={"period": period, "count": len(findings), "by_code": by_code,
                                       "critical": sum(1 for f in findings if f["severity"] == "critical"), "unexplained": sum(1 for f in findings if f.get("status") == "unexplained")},
                           risk_tier=0, policy_decision="allow")
    return out


async def set_task_ids(org: str, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    async with pm.session() as s:
        got = (await s.execute(select(PayrollFinding).where(PayrollFinding.org_id == str(org), PayrollFinding.id.in_(list(mapping))))).scalars().all()
        for r in got:
            r.task_id = mapping.get(r.id)
        await s.commit()


async def mark_explained(org: str, finding_ids: list[str], explanations: dict[str, str] | None = None, by: str = "template") -> int:
    async with pm.session() as s:
        got = (await s.execute(select(PayrollFinding).where(PayrollFinding.org_id == str(org), PayrollFinding.id.in_(list(finding_ids or []))))).scalars().all()
        for r in got:
            if r.status in ("confirmed", "dismissed"):
                continue
            if explanations and r.id in explanations:
                r.explanation = explanations[r.id]
            r.explained_by = by
            if r.status == "unexplained":
                r.status = "explained"
        await s.commit()
        return len(got)


# ══════════════════════════ the pack ════════════════════════════════════════════

def summary_totals(cur: dict, prev: dict) -> dict:
    def tot(lines):
        g = sum(float(ln.get("gross") or 0) for ln in lines.values())
        n = sum(float(ln.get("net") or 0) for ln in lines.values())
        stat = sum(sum(float((ln.get("deductions") or {}).get(k) or 0) for k in ("cpp", "cpp2", "ei", "qpip")) for ln in lines.values())
        tax = sum(float((ln.get("deductions") or {}).get("income_tax") or 0) for ln in lines.values())
        return {"gross": round(g, 2), "deductions": round(g - n, 2), "net": round(n, 2), "statutory": round(stat, 2), "income_tax": round(tax, 2),
                "headcount": len(lines)}
    c, p = tot(cur), tot(prev)
    return {"this_period": c, "last_period": p if prev else None,
            "delta": ({k: round(c[k] - p[k], 2) for k in c} if prev else None),
            "delta_pct": ({k: (round((c[k] - p[k]) / p[k] * 100, 1) if p[k] else None) for k in c} if prev else None)}


def pack_summary(inputs: dict, findings: list[dict], checks: list[dict]) -> dict:
    """Stored on the run (summary) at finish time — the tiles + blocks that do not need a DB read."""
    crit = [f for f in findings if f["severity"] == "critical"]
    warn = [f for f in findings if f["severity"] == "warning"]
    unexpl = [f for f in findings if f.get("status") == "unexplained"]
    cur = inputs.get("current") or {}
    settled = ("explained", "confirmed", "dismissed")
    flagged = {f.get("employee_id") for f in findings if f.get("employee_id") and f["severity"] != "info" and f.get("status") not in settled}
    handled = max(0, len(cur) - len(flagged))
    blocking = [f for f in crit if f["code"] in BLOCKING_CODES and f.get("status") != "dismissed"]
    st = inputs.get("statutory") or {}
    ver = st.get("verified") or {}
    plans = sorted({("QPP" if (ln.get("province") or "").upper() == "QC" else "CPP") for ln in cur.values()})
    stat_block = {"source": st.get("source"), "year": st.get("year"), "edition_found": st.get("edition_found"), "editions": st.get("editions") or [],
                  "plans": plans, "verified": sum(1 for v in ver.values() if v), "tracked": len(ver),
                  "roc": st.get("roc"), "qc": st.get("qc"),
                  "vacation_min": {p: {"source": r.get("source"), "tiers": r.get("tiers"), "verified": r.get("verified")} for p, r in (st.get("vacation_min") or {}).items()},
                  "findings": [f.get("id") for f in findings if f["code"] in ("PA-13", "PA-14", "PA-15", "PA-17")],
                  "note": (None if ver and all(ver.values()) else "Some statutory figures are not yet verified in the Payroll Verification Center (verified flag false).")}
    tie = inputs.get("tieout") or {}
    att = inputs.get("attendance") or {}
    return {"headline": f"{handled} ready, {len(flagged)} need a look" + (f" · {len(blocking)} blocking" if blocking else ""),
            "pay_period": inputs["period"], "pay_frequency": inputs.get("pay_frequency"),
            "period_start": inputs["period_start"].isoformat() if inputs.get("period_start") else None,
            "period_end": inputs["period_end"].isoformat() if inputs.get("period_end") else None,
            "pay_date": inputs["pay_date"].isoformat() if inputs.get("pay_date") else None, "prev_period": inputs.get("prev_period"),
            "handled": handled, "needs_attention": len(flagged), "critical": len(crit), "blocking": len(blocking), "warnings": len(warn),
            "info": sum(1 for f in findings if f["severity"] == "info"), "unexplained": len(unexpl),
            "explained": sum(1 for f in findings if f.get("status") == "explained"),
            "explained_by_model": sum(1 for f in findings if f.get("explained_by") == "model"),
            "projected": summary_totals(cur, inputs.get("previous") or {}),
            "attendance": {k: att.get(k) for k in ("tracked", "locked", "ready", "total")} | {"locked_at": pm.iso(att.get("locked_at")) if isinstance(att.get("locked_at"), datetime) else att.get("locked_at")},
            "draft_source": (inputs.get("sources") or {}).get("draft"), "sources": {k: str(v) for k, v in (inputs.get("sources") or {}).items()},
            "statutory": stat_block, "tieout": _json_safe(tie), "checks": checks,
            "timing": {"lead_days": None, "freeze_by": None, "approve_by": None},
            "dollars_at_risk": round(sum(float(f.get("dollars_at_risk") or 0) for f in findings if f["severity"] != "info"), 2)}


async def findings_for_run(org: str, run_id: str, *, severity: str | None = None, status: str | None = None, code: str | None = None) -> list[dict]:
    q = select(PayrollFinding).where(PayrollFinding.org_id == str(org), PayrollFinding.run_id == run_id)
    if severity:
        q = q.where(PayrollFinding.severity.in_([x for x in severity.split(",") if x]))
    if status:
        q = q.where(PayrollFinding.status.in_([x for x in status.split(",") if x]))
    if code:
        q = q.where(PayrollFinding.code.in_([x for x in code.split(",") if x]))
    async with pm.session() as s:
        got = (await s.execute(q.order_by(PayrollFinding.rank, PayrollFinding.code, PayrollFinding.delta_cad.desc().nulls_last()))).scalars().all()
    return [finding_dict(r) for r in got]


async def latest_run_for_period(org: str, period: str | None) -> dict | None:
    async with pm.session() as s:
        got = (await s.execute(select(AgentRun).where(AgentRun.org_id == str(org), AgentRun.agent == AGENT).order_by(AgentRun.started_at.desc()).limit(120))).scalars().all()
    for r in got:
        if r.status == "running":
            continue
        if period is None or (r.summary or {}).get("pay_period") == period:
            return RUNS.run_dict(r)
    return None


async def periods(org: str) -> list[dict]:
    async with pm.session() as s:
        got = (await s.execute(select(AgentRun).where(AgentRun.org_id == str(org), AgentRun.agent == AGENT, AgentRun.status != "running")
                               .order_by(AgentRun.started_at.desc()).limit(200))).scalars().all()
    seen, out = set(), []
    for r in got:
        p = (r.summary or {}).get("pay_period")
        if p and p not in seen:
            seen.add(p)
            out.append({"pay_period": p, "run_id": r.id, "mode": r.mode, "started_at": pm.iso(r.started_at), "headline": (r.summary or {}).get("headline")})
    return out


async def review_pack(org: str, run_id: str) -> dict:
    """The run's pack: tiles (stored summary) + the live exception grid + waiting-on (open tasks of the run)."""
    async with pm.session() as s:
        r = (await s.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == str(org), AgentRun.agent == AGENT))).scalar_one_or_none()
    if not r:
        raise PackError(404, "Run not found")
    run = RUNS.run_dict(r)
    grid = await findings_for_run(org, run_id)
    tasks = [t for t in await TK.list_tasks(org, agent=AGENT, status="open,waiting,escalated") if t.get("created_by_run") == run_id]
    now = pm.now_utc()
    waiting = [{**t, "age_hours": round((now - datetime.fromisoformat(t["created_at"])).total_seconds() / 3600, 1) if t.get("created_at") else None} for t in tasks]
    shadow = run["mode"] == "shadow"
    would = (await RUNS.shadow_report(org, run_id))["would_have_done"] if shadow else []
    summary = dict(run.get("summary") or {})
    by_status = {k: sum(1 for g in grid if g["status"] == k) for k in FINDING_STATUSES}
    summary.update({"grid_status": by_status, "confirmed_dollars": round(sum(float(g.get("dollars_at_risk") or 0) for g in grid if g["status"] == "confirmed"), 2)})
    return {"run": run, "mode": run["mode"], "shadow": shadow, "summary": summary, "grid": grid, "waiting_on": waiting,
            "would_have_done": would, "checks": CHECKS,
            "statutory": summary.get("statutory") or {}, "tieout": summary.get("tieout") or {}, "timing": summary.get("timing") or {},
            "actions": {"confirm": "exception_confirmed → the dollars at risk count in the value ledger", "dismiss": "needs a reason", "note": "free text"}}


# ══════════════════════════ checker actions ═════════════════════════════════════

async def _load(s, org: str, fid: str) -> PayrollFinding:
    r = (await s.execute(select(PayrollFinding).where(PayrollFinding.id == fid, PayrollFinding.org_id == str(org)))).scalar_one_or_none()
    if not r:
        raise PackError(404, "Finding not found")
    return r


async def confirm(org: str, fid: str, *, actor_user_id: str, note: str = "") -> dict:
    now = pm.now_utc()
    async with pm.session() as s:
        f = await _load(s, org, fid)
        if f.status == "confirmed":
            raise PackError(409, "Already confirmed")
        dollars = float(f.dollars_at_risk if f.dollars_at_risk is not None else abs(f.delta_cad or 0))
        f.status, f.decided_by, f.decided_at = "confirmed", actor_user_id, now
        if note:
            f.checker_note = ((f.checker_note + "\n") if f.checker_note else "") + note.strip()[:600]
        await s.commit()
        out = finding_dict(f)
    ev = await VAL.record_outcome(org, agent=AGENT, kind="exception_confirmed", units=1, dollars_avoided=dollars if dollars > 0 else None,
                                  basis=f"{out['code']} {out['check']}: {out['title']}"[:300], confirmed_by_user_id=actor_user_id, run_id=out["run_id"], note=note or None)
    async with pm.session() as s:
        f = await _load(s, org, fid)
        f.value_event_id = ev["id"]
        await s.commit()
        out = finding_dict(f)
    await LOG.append_event(org, event_type="finding.confirmed", agent_name=AGENT, run_id=out["run_id"], tool_name="confirm_finding", approver_user_id=actor_user_id,
                           decision="confirm", decision_ts=now, tool_input={"finding_id": fid, "code": out["code"], "dollars_at_risk": dollars},
                           aggregate_impact={"dollars": dollars, "headcount": 1 if out["employee_id"] else 0},
                           risk_tier=0, policy_decision="allow", agent_reason_text=note or None)
    return out


async def dismiss(org: str, fid: str, *, actor_user_id: str, reason: str) -> dict:
    if len((reason or "").strip()) < 3:
        raise PackError(422, "Say why in a few words — the agent and the next reviewer read it")
    now = pm.now_utc()
    async with pm.session() as s:
        f = await _load(s, org, fid)
        if f.status == "dismissed":
            raise PackError(409, "Already dismissed")
        f.status, f.decided_by, f.decided_at = "dismissed", actor_user_id, now
        f.checker_note = ((f.checker_note + "\n") if f.checker_note else "") + reason.strip()[:600]
        await s.commit()
        out = finding_dict(f)
    await LOG.append_event(org, event_type="finding.dismissed", agent_name=AGENT, run_id=out["run_id"], tool_name="dismiss_finding", approver_user_id=actor_user_id,
                           decision="dismiss", decision_ts=now, deny_message=reason.strip()[:600], tool_input={"finding_id": fid, "code": out["code"]}, risk_tier=0, policy_decision="allow")
    return out


async def note(org: str, fid: str, *, actor_user_id: str, text: str) -> dict:
    if not (text or "").strip():
        raise PackError(422, "Write the note")
    async with pm.session() as s:
        f = await _load(s, org, fid)
        f.checker_note = ((f.checker_note + "\n") if f.checker_note else "") + text.strip()[:600]
        await s.commit()
        out = finding_dict(f)
    await LOG.append_event(org, event_type="finding.noted", agent_name=AGENT, run_id=out["run_id"], tool_name="note_finding", approver_user_id=actor_user_id,
                           tool_input={"finding_id": fid, "code": out["code"], "note": text.strip()[:600]}, risk_tier=0, policy_decision="allow")
    return out


async def explain_manually(org: str, fid: str, *, actor_user_id: str, text: str) -> dict:
    """A person writes the reason for an unexplained variance (the chase task's outcome)."""
    if len((text or "").strip()) < 3:
        raise PackError(422, "Write the reason")
    async with pm.session() as s:
        f = await _load(s, org, fid)
        f.explanation, f.explained_by, f.status = text.strip()[:600], "person", ("explained" if f.status in ("unexplained", "explained") else f.status)
        f.decided_by, f.decided_at = actor_user_id, pm.now_utc()
        await s.commit()
        out = finding_dict(f)
    if out.get("task_id"):
        try:
            await TK.complete_task(org, out["task_id"], actor_user_id=actor_user_id, result={"explained": True})
        except TK.TaskError:
            pass
    await VAL.record_outcome(org, agent=AGENT, kind="variance_explained", units=1, run_id=out["run_id"], note=f"{out['code']} by person")
    await LOG.append_event(org, event_type="finding.explained", agent_name=AGENT, run_id=out["run_id"], tool_name="explain_finding", approver_user_id=actor_user_id,
                           tool_input={"finding_id": fid, "code": out["code"]}, risk_tier=0, policy_decision="allow", agent_reason_text=text.strip()[:600])
    return out


async def counts_for_run(org: str, run_id: str) -> dict:
    async with pm.session() as s:
        got = (await s.execute(select(PayrollFinding.status, func.count()).where(PayrollFinding.org_id == str(org), PayrollFinding.run_id == run_id).group_by(PayrollFinding.status))).all()
    return {k: int(n) for k, n in got}
