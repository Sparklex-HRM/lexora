"""Watchdog RULES — pure functions from rows to findings (no I/O, unit-tested).

Each rule takes plain dict rows (already read from the database by ``watchdogs.py``)
plus the thresholds in force and returns a list of findings:

    {"severity": "info|warn|high", "confidence": "high|medium|low",
     "subject_employee_id": ..., "subject_name": ..., "title": ...,
     "explanation": plain words incl. the rule and the numbers, "evidence": [rows],
     "fingerprint": stable key so the same finding is not re-notified every run}

A finding is only produced when the evidence rows prove it. When a rule cannot
decide (a value is missing) it either skips the row or marks confidence "medium"
and says what was missing — it never calls something "detected" without the rows.
"""
from datetime import date, datetime, timedelta

from shared.utils.org_time import local_today


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _name(r: dict) -> str:
    n = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
    return n or r.get("employee_name") or r.get("name") or r.get("employee_number") or "Employee"


def _ev(**kw) -> dict:
    """One evidence row: plain values only (dates as ISO text)."""
    out = {}
    for k, v in kw.items():
        if isinstance(v, (date, datetime)):
            v = v.isoformat()[:10] if isinstance(v, date) and not isinstance(v, datetime) else v.isoformat()
        out[k] = v
    return out


def finding(*, severity, title, explanation, evidence, fingerprint, subject_id=None, subject_name=None,
            confidence="high") -> dict:
    return {"severity": severity, "confidence": confidence, "subject_employee_id": str(subject_id) if subject_id else None,
            "subject_name": subject_name, "title": title, "explanation": explanation,
            "evidence": evidence, "fingerprint": fingerprint[:120]}


# ══════════════════════════ 1. payroll variance ════════════════════════════════

def payroll_variance(this_month: list[dict], last_month: list[dict], explanations: dict, th: dict) -> list[dict]:
    """``this_month`` / ``last_month``: rows {employee_id, name, employee_number, gross, net, pay_date} — one per
    employee for the latest regular pay date and the one before it. ``explanations``: employee_id ->
    {"revision": date|None, "hire_date", "termination_date", "leave_days"} (approved leave in the window)."""
    pct_limit = _f(th.get("variance_pct", 15))
    high_at = _f(th.get("high_pct", 40))
    prev = {str(r["employee_id"]): r for r in last_month}
    out = []
    for r in this_month:
        eid = str(r["employee_id"])
        p = prev.get(eid)
        if not p or _f(p.get("gross")) <= 0:
            continue                                         # nothing to compare with — no claim
        g0, g1 = _f(p["gross"]), _f(r["gross"])
        change = (g1 - g0) / g0 * 100
        if abs(change) < pct_limit:
            continue
        ex = explanations.get(eid, {})
        why = []
        if ex.get("revision"):
            why.append(f"a salary revision effective {ex['revision']}")
        if _f(ex.get("leave_days")) > 0:
            why.append(f"{_f(ex.get('leave_days')):g} day(s) of approved leave")
        m0, m1 = _d(p.get("pay_date")), _d(r.get("pay_date"))
        hd, td = _d(ex.get("hire_date")), _d(ex.get("termination_date"))
        if hd and m0 and m1 and (m0.replace(day=1) <= hd <= m1):
            why.append(f"joined on {hd.isoformat()}")
        if td and m0 and m1 and (m0.replace(day=1) <= td <= m1):
            why.append(f"last day {td.isoformat()}")
        if why:
            continue                                         # explained — not a finding
        sev = "high" if abs(change) >= high_at else "warn"
        out.append(finding(
            severity=sev, subject_id=eid, subject_name=r.get("name") or _name(r),
            title=f"Gross pay moved {change:+.1f}% with no revision, leave, joining or leaving recorded",
            explanation=(f"Rule: flag when gross pay changes by more than {pct_limit:g}% from the previous pay period and "
                         f"no revision, approved leave, joining date or last day explains it. "
                         f"Previous pay gross {g0:,.2f}; this pay {g1:,.2f}; change {change:+.1f}%. "
                         f"Checked: salary revisions (none effective in the window), approved leave (none), "
                         f"joining/leaving dates (none in the window)."),
            evidence=[_ev(employee=r.get("name"), employee_number=r.get("employee_number"), pay_date=p.get("pay_date"),
                          gross=g0, net=_f(p.get("net"))),
                      _ev(employee=r.get("name"), employee_number=r.get("employee_number"), pay_date=r.get("pay_date"),
                          gross=g1, net=_f(r.get("net")), change_pct=round(change, 1))],
            fingerprint=f"payroll_variance:{eid}:{(m1 or local_today()).isoformat()}"))
    return out


# ══════════════════════════ 2. CPP / EI check (Canada) ════════════════════════

def cpp_ei_check(rows: list[dict], th: dict) -> list[dict]:
    """``rows``: one per employee for the latest regular pay date — {employee_id, name, pay_date, province, cpp, cpp2, ei,
    ytd_cpp, ytd_cpp2, ytd_ei, cpp_exempt, ei_exempt}. Thresholds: the year's employee maxima (editable; seeded from the
    payroll engine's verified 2026 constants) and a tolerance in dollars. Québec employees pay QPP (not CPP) and the
    Québec EI rate, so only their EI is compared with ``ei_max_qc``."""
    tol = _f(th.get("tolerance", 1))
    out = []
    for r in rows:
        eid, name = str(r["employee_id"]), r.get("name")
        prov = (r.get("province") or "").upper()
        cpp, cpp2, ei = _f(r.get("cpp")), _f(r.get("cpp2")), _f(r.get("ei"))
        when = str(r.get("pay_date") or "")[:10]
        if r.get("cpp_exempt") and cpp + cpp2 > tol:
            out.append(finding(severity="high", subject_id=eid, subject_name=name,
                               title=f"CPP deducted although the employee is marked CPP-exempt ({cpp + cpp2:,.2f})",
                               explanation=f"Rule: an employee marked CPP-exempt (e.g. receiving a CPP disability pension) has no CPP deducted. Pay date {when}.",
                               evidence=[_ev(employee=name, pay_date=when, cpp=cpp, cpp2=cpp2, cpp_exempt=True)],
                               fingerprint=f"cpp_exempt:{eid}:{when}"))
        if r.get("ei_exempt") and ei > tol:
            out.append(finding(severity="high", subject_id=eid, subject_name=name,
                               title=f"EI deducted although the employee is marked EI-exempt ({ei:,.2f})",
                               explanation=f"Rule: an employee in non-insurable employment has no EI premium deducted. Pay date {when}.",
                               evidence=[_ev(employee=name, pay_date=when, ei=ei, ei_exempt=True)],
                               fingerprint=f"ei_exempt:{eid}:{when}"))
        checks = [("ei", _f(r.get("ytd_ei")), _f(th.get("ei_max_qc" if prov == "QC" else "ei_max")), "EI premium")]
        if prov != "QC":
            checks += [("cpp", _f(r.get("ytd_cpp")), _f(th.get("cpp_max")), "CPP contribution"),
                       ("cpp2", _f(r.get("ytd_cpp2")), _f(th.get("cpp2_max")), "CPP2 contribution")]
        for code, ytd, cap, label in checks:
            if cap and ytd > cap + tol:
                out.append(finding(severity="high", subject_id=eid, subject_name=name,
                                   title=f"Year-to-date {label} {ytd:,.2f} is above the {cap:,.2f} maximum",
                                   explanation=(f"Rule: deductions stop once the employee's yearly maximum is reached. YTD {ytd:,.2f} vs maximum {cap:,.2f} "
                                                f"(threshold from the payroll engine's verified constants; edit it if CRA publishes a change)."),
                                   evidence=[_ev(employee=name, pay_date=when, province=prov, deduction=code, ytd=ytd, maximum=cap)],
                                   fingerprint=f"{code}_max:{eid}:{when[:4]}"))
    return out


# ══════════════════════════ 3. OT breach ═══════════════════════════════════════

def ot_breach(days: list[dict], timesheets: list[dict], th: dict) -> list[dict]:
    """``days``: {employee_id, name, work_date, actual_hours}; ``timesheets``: {employee_id, name,
    period_start, period_end, ot_hours, ot2_hours}. Thresholds: daily_hours_max, weekly_hours_max,
    quarter_ot_hours_max (None = not checked)."""
    daily_max = _f(th.get("daily_hours_max", 12))
    weekly_max = _f(th.get("weekly_hours_max", 60))
    q_max = th.get("quarter_ot_hours_max")
    out = []
    by_emp: dict[str, list[dict]] = {}
    for d in days:
        by_emp.setdefault(str(d["employee_id"]), []).append(d)
    for eid, rows in by_emp.items():
        name = rows[0].get("name")
        over_day = [r for r in rows if _f(r.get("actual_hours")) > daily_max]
        if over_day:
            worst = max(over_day, key=lambda r: _f(r.get("actual_hours")))
            out.append(finding(
                severity="warn" if len(over_day) < 3 else "high", subject_id=eid, subject_name=name,
                title=f"{len(over_day)} day(s) over {daily_max:g} hours (longest {_f(worst.get('actual_hours')):g} h)",
                explanation=(f"Rule: a working day above {daily_max:g} hours (the company's daily limit) is a breach. "
                             f"{len(over_day)} such day(s) in the window."),
                evidence=[_ev(employee=name, date=r.get("work_date"), hours=_f(r.get("actual_hours"))) for r in
                          sorted(over_day, key=lambda r: str(r.get("work_date")))[:20]],
                fingerprint=f"ot_day:{eid}:{str(worst.get('work_date'))}"))
        weeks: dict[str, float] = {}
        for r in rows:
            wd = _d(r.get("work_date"))
            if wd:
                wk = (wd - timedelta(days=wd.weekday())).isoformat()
                weeks[wk] = weeks.get(wk, 0.0) + _f(r.get("actual_hours"))
        over_wk = {w: h for w, h in weeks.items() if h > weekly_max}
        if over_wk:
            w0 = max(over_wk, key=over_wk.get)
            out.append(finding(
                severity="warn", subject_id=eid, subject_name=name,
                title=f"{len(over_wk)} week(s) over {weekly_max:g} hours (highest {over_wk[w0]:g} h)",
                explanation=f"Rule: total worked hours in a Monday-to-Sunday week above {weekly_max:g} is a breach.",
                evidence=[_ev(employee=name, week_starting=w, hours=round(h, 2)) for w, h in sorted(over_wk.items())],
                fingerprint=f"ot_week:{eid}:{w0}"))
    if q_max:
        q_tot: dict[str, dict] = {}
        for t in timesheets:
            eid = str(t["employee_id"])
            q_tot.setdefault(eid, {"name": t.get("name"), "ot": 0.0, "sheets": 0})
            q_tot[eid]["ot"] += _f(t.get("ot_hours")) + _f(t.get("ot2_hours"))
            q_tot[eid]["sheets"] += 1
        for eid, v in q_tot.items():
            if v["ot"] > _f(q_max):
                out.append(finding(
                    severity="high", subject_id=eid, subject_name=v["name"],
                    title=f"Quarter overtime {v['ot']:g} h is over the {_f(q_max):g} h cap",
                    explanation=f"Rule: overtime across the quarter's timesheets must stay within {_f(q_max):g} hours. Summed {v['sheets']} timesheet(s).",
                    evidence=[_ev(employee=v["name"], quarter_overtime_hours=round(v["ot"], 2), timesheets=v["sheets"], cap=_f(q_max))],
                    fingerprint=f"ot_quarter:{eid}:{local_today().isoformat()[:7]}"))
    return out


# ══════════════════════════ 4. missing / single punches ════════════════════════

def missing_punches(days: list[dict], punch_counts: dict, th: dict, today: date) -> list[dict]:
    """``days``: {employee_id, name, work_date, status, first_in, last_out}; ``punch_counts``:
    (employee_id, work_date iso) -> number of punches."""
    out = []
    by_emp: dict[str, list[dict]] = {}
    for d in days:
        wd = _d(d.get("work_date"))
        if not wd or wd >= today:
            continue
        if d.get("status") not in ("present", "late", "on_shift"):
            continue
        n = punch_counts.get((str(d["employee_id"]), wd.isoformat()))
        problem = None
        if n == 1:
            problem = "only one punch"
        elif not d.get("first_in") or not d.get("last_out"):
            problem = "clock-in without clock-out" if d.get("first_in") else "clock-out without clock-in"
        if problem:
            by_emp.setdefault(str(d["employee_id"]), []).append(_ev(employee=d.get("name"), date=wd, status=d.get("status"),
                                                                    first_in=d.get("first_in"), last_out=d.get("last_out"),
                                                                    punches=n, problem=problem))
    for eid, ev in by_emp.items():
        out.append(finding(severity="warn" if len(ev) >= int(th.get("warn_after_days", 2)) else "info",
                           subject_id=eid, subject_name=ev[0]["employee"],
                           title=f"{len(ev)} day(s) with a missing or single punch",
                           explanation=("Rule: a worked day needs both a clock-in and a clock-out. Days with only one punch, "
                                        "or an in without an out, are listed. Fix via Attendance → Regularisation."),
                           evidence=ev[:30], fingerprint=f"punch:{eid}:{ev[-1]['date']}"))
    return out


# ══════════════════════════ 5. leave without approval on a worked day ══════════

def leave_without_approval(pending: list[dict], absent_days: list[dict], approved: list[dict], today: date, th: dict) -> list[dict]:
    """``pending``: leave requests still pending whose start_date ≤ today. ``absent_days``: attendance
    rows with status 'absent'. ``approved``: approved leave requests (start_date, end_date, employee_id)."""
    out = []
    for r in pending:
        sd = _d(r.get("start_date"))
        if sd and sd <= today:
            out.append(finding(severity="warn", subject_id=r.get("employee_id"), subject_name=r.get("employee_name"),
                               title=f"Leave from {sd.isoformat()} has started but is still waiting for approval",
                               explanation="Rule: a leave request whose first day has arrived must be approved or rejected; it is still pending.",
                               evidence=[_ev(employee=r.get("employee_name"), leave_type=r.get("leave_type_name"), start=sd,
                                             end=r.get("end_date"), days=_f(r.get("days")), status="pending",
                                             waiting_on=r.get("manager_name") or "manager")],
                               fingerprint=f"leave_pending:{r.get('id')}"))
    cover = {}
    for a in approved:
        cover.setdefault(str(a["employee_id"]), []).append((_d(a.get("start_date")), _d(a.get("end_date"))))
    by_emp: dict[str, list[dict]] = {}
    for d in absent_days:
        wd = _d(d.get("work_date"))
        if not wd:
            continue
        if any(s and e and s <= wd <= e for s, e in cover.get(str(d["employee_id"]), [])):
            continue
        by_emp.setdefault(str(d["employee_id"]), []).append(_ev(employee=d.get("name"), date=wd, attendance="absent",
                                                                approved_leave="none"))
    for eid, ev in by_emp.items():
        if len(ev) >= int(th.get("absent_days_min", 1)):
            out.append(finding(severity="info", confidence="medium", subject_id=eid, subject_name=ev[0]["employee"],
                               title=f"{len(ev)} absent day(s) with no approved leave",
                               explanation=("Rule: a day marked absent with no approved leave covering it is unexplained absence. "
                                            "Confidence medium — a regularisation or a late leave request may still be coming."),
                               evidence=ev[:30], fingerprint=f"absent:{eid}:{ev[-1]['date']}"))
    return out


# ══════════════════════════ 7–13. people lifecycle rules ═══════════════════════

def roe_required(leavers: list[dict], cases: dict, today: date, th: dict) -> list[dict]:
    """Canada: ``leavers`` {id, name, termination_date}; ``cases``: employee_id -> exit case row."""
    out = []
    for e in leavers:
        td = _d(e.get("termination_date"))
        if not td:
            continue
        case = cases.get(str(e["id"]))
        if case and case.get("roe_reason_code"):
            continue
        days = (today - td).days
        out.append(finding(severity="high" if days > int(th.get("deadline_days", 5)) else "warn",
                           subject_id=e["id"], subject_name=e.get("name"),
                           title=f"No Record of Employment reason code {days} day(s) after the last day",
                           explanation=(f"Rule: a separation needs an ROE within 5 calendar days of the interruption of earnings. "
                                        f"Last day {td.isoformat()}; " + ("the exit case has no ROE reason code." if case else
                                                                          "there is no exit case for this leaver.")),
                           evidence=[_ev(employee=e.get("name"), last_day=td, days_since=days,
                                         exit_case=(case or {}).get("case_number") or "none", roe_reason_code=(case or {}).get("roe_reason_code") or "")],
                           fingerprint=f"roe:{e['id']}"))
    return out


def document_expiry(emps: list[dict], today: date, th: dict, country: str) -> list[dict]:
    ahead = int(th.get("days_ahead", 60))
    out = []
    for e in emps:
        exp = _d(e.get("work_permit_expiry"))
        if exp and (exp - today).days <= ahead:
            left = (exp - today).days
            out.append(finding(severity="high" if left < 0 else "warn", subject_id=e["id"], subject_name=e.get("name"),
                               title=("Work permit expired " if left < 0 else "Work permit expires in ") + f"{abs(left)} day(s)",
                               explanation=f"Rule: a work permit / visa expiring within {ahead} days, or already expired, needs action. Expiry {exp.isoformat()}.",
                               evidence=[_ev(employee=e.get("name"), document="work permit", expiry=exp, days_left=left,
                                             eligibility=e.get("work_eligibility"))],
                               fingerprint=f"permit:{e['id']}:{exp.isoformat()}"))
        if country == "CA" and e.get("td1_federal_claim") in (None, "") and e.get("status") == "active":
            out.append(finding(severity="info", subject_id=e["id"], subject_name=e.get("name"),
                               title="No TD1 federal claim on file",
                               explanation="Rule: an active Canadian employee needs a TD1 claim amount for tax; the field is empty.",
                               evidence=[_ev(employee=e.get("name"), td1_federal_claim="", status=e.get("status"))],
                               fingerprint=f"td1:{e['id']}"))
    return out


def onboarding_stuck(sessions: list[dict], today: datetime, th: dict) -> list[dict]:
    limit = int(th.get("days", 7))
    out = []
    for s in sessions:
        c = s.get("created_at")
        if not isinstance(c, datetime):
            continue
        age = (today - c).days
        if s.get("status") in ("invited", "submitted") and age > limit:
            out.append(finding(severity="warn", subject_name=f"{s.get('first_name') or ''} {s.get('last_name') or ''}".strip() or s.get("email"),
                               title=f"Onboarding '{s.get('status')}' for {age} days",
                               explanation=f"Rule: an onboarding still '{s.get('status')}' after {limit} days is stuck. Started {c.date().isoformat()}.",
                               evidence=[_ev(person=f"{s.get('first_name') or ''} {s.get('last_name') or ''}".strip(), status=s.get("status"),
                                             started=c.date(), days=age, step=s.get("current_step"))],
                               fingerprint=f"onb:{s.get('id')}"))
    return out


def probation_no_action(emps: list[dict], actions: dict, today: date, th: dict) -> list[dict]:
    ahead = int(th.get("days_ahead", 7))
    out = []
    for e in emps:
        end = _d(e.get("probation_end_date"))
        if not end or e.get("status") != "active":
            continue
        left = (end - today).days
        if left > ahead:
            continue
        last = actions.get(str(e["id"]))
        if last and last.get("action") in ("confirm", "extend", "do_not_confirm") and _d(last.get("created_at")) and _d(last["created_at"]) >= end - timedelta(days=60):
            continue
        out.append(finding(severity="high" if left < 0 else "warn", subject_id=e["id"], subject_name=e.get("name"),
                           title=("Probation ended " + f"{-left} day(s) ago" if left < 0 else f"Probation ends in {left} day(s)") + " with no decision",
                           explanation=(f"Rule: within {ahead} days of the probation end (or after it) there must be a confirm / extend / "
                                        f"do-not-confirm action. End date {end.isoformat()}; last action: "
                                        f"{(last or {}).get('action') or 'none'}."),
                           evidence=[_ev(employee=e.get("name"), probation_end=end, days_left=left,
                                         last_action=(last or {}).get("action") or "none", last_action_on=(last or {}).get("created_at"))],
                           fingerprint=f"prob:{e['id']}:{end.isoformat()}"))
    return out


def approvals_ageing(items: list[dict], now: datetime, th: dict) -> list[dict]:
    limit = int(th.get("days", 5))
    out = []
    for i in items:
        o = i.get("opened_at")
        if not isinstance(o, datetime):
            continue
        age = (now - o).days
        if age >= limit:
            out.append(finding(severity="high" if age >= limit * 2 else "warn", subject_name=i.get("requester_name"),
                               title=f"{i.get('kind_label') or i.get('kind')} waiting {age} day(s): {i.get('title')}",
                               explanation=f"Rule: an approval waiting {limit} days or more is overdue. Opened {o.date().isoformat()}.",
                               evidence=[_ev(request=i.get("title"), kind=i.get("kind"), requested_by=i.get("requester_name"),
                                             opened=o.date(), days_waiting=age, reminders_sent=i.get("reminders_sent", 0))],
                               fingerprint=f"apr:{i.get('id')}"))
    return out


def exit_past_lwd(cases: list[dict], active_leavers: list[dict], today: date, th: dict) -> list[dict]:
    out = []
    for c in cases:
        lwd = _d(c.get("lwd"))
        if lwd and lwd < today and c.get("status") not in ("closed", "withdrawn", "rejected"):
            out.append(finding(severity="warn", subject_id=c.get("employee_id"), subject_name=c.get("employee_name"),
                               title=f"Exit case still '{c.get('status')}' {(today - lwd).days} day(s) after the last working day",
                               explanation=f"Rule: once the last working day has passed the exit case should move to settled / closed. Last day {lwd.isoformat()}.",
                               evidence=[_ev(employee=c.get("employee_name"), case=c.get("case_number"), status=c.get("status"),
                                             last_working_day=lwd, days_past=(today - lwd).days, settlement=c.get("settlement_status"))],
                               fingerprint=f"exit:{c.get('id')}"))
    for e in active_leavers:
        td = _d(e.get("termination_date"))
        if td and td < today:
            out.append(finding(severity="high", subject_id=e["id"], subject_name=e.get("name"),
                               title=f"Still 'active' {(today - td).days} day(s) after the recorded last day",
                               explanation="Rule: an employee whose termination date has passed must not remain active (they would be paid again).",
                               evidence=[_ev(employee=e.get("name"), status=e.get("status"), termination_date=td, days_past=(today - td).days)],
                               fingerprint=f"active_leaver:{e['id']}"))
    return out


def max_severity(findings: list[dict]) -> str | None:
    order = {"info": 0, "warn": 1, "high": 2}
    best = None
    for f in findings:
        if best is None or order.get(f["severity"], 0) > order.get(best, 0):
            best = f["severity"]
    return best
