"""Attendance Agent — deterministic daily checks (pure: inputs dict in → items out). No rule is invented here: the late
mark is the Time service's own ``status = 'late'``; overtime itself is paid by the Time service's provincial rules."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from agents.lifecycle_common import item

CHECKS = [("AT-01", "Missing / single punches"), ("AT-02", "Late marks"),
          ("AT-05", "Regularisations waiting for the manager"), ("AT-06", "Unapproved leave")]

DEFAULTS: dict = {
    "window_days": 7,                # look back N days (to yesterday) for punches
    "late_marks_per_month": 3,       # AT-02: flag at this many late marks in the month
    "regularise_hours": 48,          # AT-01: the employee raises the regularisation within this window
    "approve_hours": 72,             # AT-05: the manager decides within this window (then escalation)
    "cutoff_blocking_days": 2,       # an item inside the pay period becomes blocking this close to the cut-off
}


def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _aware(v):
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(v, datetime) and v.tzinfo is None:
        from datetime import timezone
        v = v.replace(tzinfo=timezone.utc)
    return v


def _covered(leaves: list[dict], eid: str, d: date) -> bool:
    return any(str(l.get("employee_id")) == eid and _d(l.get("start_date")) and _d(l.get("start_date")) <= d <= (_d(l.get("end_date")) or d) for l in leaves)


def run_checks(inp: dict, cfg: dict | None = None, now: datetime | None = None) -> dict:
    cfg = {**DEFAULTS, **(cfg or {})}
    today: date = inp["today"]
    now = now or datetime.combine(today, datetime.min.time()).replace(hour=9)
    from datetime import timezone
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    pay = inp.get("pay") or {}
    ps, cut = pay.get("period_start"), pay.get("cutoff")
    days_to_cut = (cut - today).days if cut else None
    emps = {str(e["id"]): e for e in inp.get("employees") or []}
    win = inp.get("window") or {"start": today - timedelta(days=int(cfg["window_days"])), "end": today - timedelta(days=1)}
    items: list[dict] = []
    case_keys: set[str] = set()

    def sev_for(d: date | None, base: str = "warning") -> str:
        if d and ps and cut and ps <= d <= pay.get("period_end", d) and days_to_cut is not None and days_to_cut <= int(cfg["cutoff_blocking_days"]):
            return "blocking"
        return base

    def cut_note(d: date | None) -> str:
        if d and ps and cut and ps <= d <= pay.get("period_end", d):
            return f" Payroll cut-off {cut.isoformat()} ({days_to_cut} day(s))."
        return ""

    def add(code, e, title, ref, **kw):
        ck = f"emp:{e['id']}"
        case_keys.add(ck)
        items.append(item(code, case_key=ck, title=title, employee=e, case_label=e.get("name"), ref=ref, **kw))

    pending_reg_days = {(str(r["employee_id"]), _d(r.get("work_date"))) for r in inp.get("regularisations") or [] if r.get("status") in ("pending", "approved")}
    leaves = inp.get("leave") or []
    approved_leave = [l for l in leaves if l.get("status") == "approved"]
    # AT-01 missing / single punch
    for r in inp.get("days") or []:
        eid, d = str(r.get("employee_id")), _d(r.get("work_date"))
        e = emps.get(eid)
        if not e or not d or d < win["start"] or d > win["end"] or r.get("is_holiday"):
            continue
        st = (r.get("status") or "").lower()
        if st in ("leave", "holiday", "rest_day"):
            continue
        single = bool(r.get("first_in")) and not r.get("last_out")
        missing = (not r.get("first_in")) and st in ("absent", "scheduled")
        if not (single or missing) or (eid, d) in pending_reg_days or _covered(approved_leave, eid, d):
            continue
        add("AT-01", e, f"{'Single punch' if single else 'No punch'} on {d.isoformat()} — raise a regularisation (or apply leave)", d.isoformat(),
            severity=sev_for(d), owner_role="employee", due_on=d + timedelta(days=max(1, int(cfg["regularise_hours"]) // 24)),
            what=("Only an in-punch was recorded." if single else "You were rostered but no punch was recorded and no leave covers the day.") + cut_note(d),
            how_to_fix="Time → My attendance → the day → Regularise (missed punch / wrong time), or apply leave.", link="/time/my-attendance",
            detail={"manager_id": e.get("manager_id"), "work_date": d.isoformat(), "kind": "single" if single else "missing"})
    # AT-02 late marks this month
    month = today.strftime("%Y-%m")
    late: dict[str, int] = {}
    for r in inp.get("days") or []:
        d = _d(r.get("work_date"))
        if d and d.strftime("%Y-%m") == month and (r.get("status") or "").lower() == "late":
            late[str(r["employee_id"])] = late.get(str(r["employee_id"]), 0) + 1
    for eid, n in late.items():
        e = emps.get(eid)
        if e and n >= int(cfg["late_marks_per_month"]):
            add("AT-02", e, f"{n} late mark(s) in {month}", f"late:{month}", severity="warning", owner_role="manager", chase=True,
                what=f"Late by the Time service's grace rule on {n} day(s) this month.", how_to_fix="Talk to the employee; apply the late-mark policy if one exists.",
                detail={"manager_id": e.get("manager_id"), "count": n})
    # AT-05 regularisations pending with the manager
    for r in inp.get("regularisations") or []:
        if r.get("status") != "pending":
            continue
        e = emps.get(str(r.get("employee_id")))
        if not e:
            continue
        d = _d(r.get("work_date"))
        created = _aware(r.get("created_at"))
        age_h = (now - created).total_seconds() / 3600 if created else 0
        sev = "blocking" if age_h > float(cfg["approve_hours"]) else sev_for(d)
        add("AT-05", e, f"Approve or reject the regularisation for {d.isoformat() if d else 'a day'} ({int(age_h)} h waiting)", f"reg:{r['id']}",
            severity=sev, owner_role="manager", due_on=(created + timedelta(hours=float(cfg["approve_hours"]))).date() if created else None,
            detail={"manager_id": str(r.get("manager_id") or e.get("manager_id") or "") or None, "regularisation_id": str(r["id"])},
            what=f"Reason: {(r.get('reason') or 'other').replace('_', ' ')}. Decision window {cfg['approve_hours']} h." + cut_note(d),
            how_to_fix="Approvals → Regularisations → Approve / Reject.", link="/approvals?type=regularization")
    # AT-06 unapproved leave inside the pay period (or already started)
    for l in leaves:
        if l.get("status") != "pending":
            continue
        e = emps.get(str(l.get("employee_id")))
        sd = _d(l.get("start_date"))
        if not e or not sd:
            continue
        in_period = bool(ps and pay.get("period_end") and sd <= pay["period_end"])
        if not (in_period or sd <= today):
            continue
        add("AT-06", e, f"Leave {l.get('leave_type_code') or ''} from {sd.isoformat()} is still pending", f"leave:{l['id']}", severity=sev_for(sd), owner_role="manager",
            detail={"manager_id": str(l.get("manager_id") or e.get("manager_id") or "") or None, "leave_id": str(l["id"])},
            what="Pending leave in the pay period is unpaid time or an unexplained absence on the pay stub if it is not decided." + cut_note(sd),
            how_to_fix="Approvals → Leave → Approve / Reject.", link="/approvals?type=leave")
    return {"items": items, "case_keys": case_keys, "checks_run": len(CHECKS),
            "checks": [{"code": k, "name": n, "items": sum(1 for x in items if x["code"] == k)} for k, n in CHECKS],
            "cutoff": {"period": pay.get("period"), "cutoff": cut.isoformat() if cut else None, "days_to_cutoff": days_to_cut,
                       "pay_date": pay["pay_date"].isoformat() if pay.get("pay_date") else None}}
