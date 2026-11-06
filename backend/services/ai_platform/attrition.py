"""Flight-risk signals — a RULES-BASED, explainable score per employee.

This is a heuristic, not a prediction and not machine learning. Each factor adds a
fixed number of points and is listed with the rows it came from, so a manager can
see exactly why a person scores what they score. Thresholds below are the defaults.

Factors (points):
  tenure band           <1 y +10 · 1–2 y +15 · 2–5 y +5 · 5+ y 0
  no increment          last approved revision older than N months (default 18) +20;
                        +10 more when peers in the same department got one in the last 12 months
  rating drop           latest final/overall score at least 0.5 below the previous one +15
  leave pattern change  approved leave days in the last 90 days more than double the previous 90 (and ≥ 3) +10
  overtime load         average overtime per approved timesheet over the last quarter above 8 h +10
  manager changed       a career change in the last 180 days that set a new manager +10
  teammates leaving     open exit cases under the same manager: 1 → +10, 2+ → +15
  pay below peers       annual pay under 85% of the median of the same department + grade +10; under 75% +15   (D7)
  survey signal         a NAMED survey answer in the last 180 days with eNPS 0–6 or a 1–5 score of 2 or less +10 (D7)
  training overdue      a mandatory course past its due date +5                                                 (D7)

Bands: low < 25 · watch 25–49 · high ≥ 50. Score capped at 100.

D7 — "why" list (plain sentences per factor), per-person endpoint, and CALIBRATION from the organisation's
own leavers: once at least 50 exits exist, each factor's exit rate among flagged vs. not-flagged people is
reported (a lift, descriptive). A logistic model is fitted only when scikit-learn is already installed —
it is not on this installation, so the score stays rules-only and says so.
"""
import sys
from datetime import date, datetime, timedelta

sys.path.append("/app")

BANDS = [(50, "high"), (25, "watch"), (0, "low")]
DEFAULTS = {"no_increment_months": 18, "rating_drop": 0.5, "leave_multiplier": 2.0, "leave_min_days": 3,
            "ot_hours_per_sheet": 8, "manager_change_days": 180,
            "pay_ratio_watch": 0.85, "pay_ratio_low": 0.75, "survey_days": 180, "survey_nps_max": 6, "survey_scale_max": 2}
MIN_EXITS_FOR_CALIBRATION = 50


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


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def band(score: int) -> str:
    for lo, name in BANDS:
        if score >= lo:
            return name
    return "low"


def score_employee(emp: dict, ctx: dict, today: date, th: dict | None = None) -> dict:
    """``emp``: {id, name, department, hire_date, manager_id}. ``ctx`` (all optional, keyed by employee id):
      revisions:        id -> [effective_from dates of approved/applied revisions]
      dept_revised_12m: set of departments where someone got a revision in the last 12 months
      ratings:          id -> [scores oldest→newest]
      leave_days:       id -> (days last 90, days previous 90)
      ot:               id -> (total OT hours last quarter, number of timesheets)
      manager_changes:  id -> latest date a manager change was applied
      exits_by_manager: manager_id -> number of open exit cases among their reports
    """
    th = {**DEFAULTS, **(th or {})}
    eid = str(emp["id"])
    factors: list[dict] = []

    hd = _d(emp.get("hire_date"))
    if hd:
        yrs = (today - hd).days / 365.25
        pts = 10 if yrs < 1 else 15 if yrs < 2 else 5 if yrs < 5 else 0
        factors.append({"factor": "Tenure band", "points": pts, "evidence": f"Joined {hd.isoformat()} — {yrs:.1f} years",
                        "rule": "<1 y +10, 1–2 y +15, 2–5 y +5, 5+ y 0"})

    revs = sorted(d for d in (ctx.get("revisions", {}).get(eid) or []) if _d(d)) if ctx.get("revisions") else []
    last_rev = _d(revs[-1]) if revs else None
    months_since = ((today - last_rev).days / 30.4) if last_rev else ((today - hd).days / 30.4 if hd else None)
    if months_since is not None and months_since > th["no_increment_months"]:
        pts = 20
        ev = (f"Last approved revision {last_rev.isoformat()}" if last_rev else "No approved revision on record") + f" — {months_since:.0f} months"
        if emp.get("department") and emp["department"] in (ctx.get("dept_revised_12m") or set()):
            pts += 10
            ev += f"; others in {emp['department']} got a revision in the last 12 months"
        factors.append({"factor": "No increment", "points": pts, "evidence": ev,
                        "rule": f"No revision for more than {th['no_increment_months']} months +20; peers revised +10"})

    ratings = [r for r in (ctx.get("ratings", {}).get(eid) or []) if r is not None]
    if len(ratings) >= 2 and _f(ratings[-2]) - _f(ratings[-1]) >= th["rating_drop"]:
        factors.append({"factor": "Rating drop", "points": 15,
                        "evidence": f"Previous review {_f(ratings[-2]):g}, latest {_f(ratings[-1]):g}",
                        "rule": f"Latest score at least {th['rating_drop']:g} below the previous one +15"})

    ld = (ctx.get("leave_days") or {}).get(eid)
    if ld:
        recent, before = _f(ld[0]), _f(ld[1])
        if recent >= th["leave_min_days"] and recent > before * th["leave_multiplier"]:
            factors.append({"factor": "Leave pattern change", "points": 10,
                            "evidence": f"Approved leave {recent:g} day(s) in the last 90 days versus {before:g} in the 90 before",
                            "rule": f"More than {th['leave_multiplier']:g}× the previous 90 days and at least {th['leave_min_days']} days +10"})

    ot = (ctx.get("ot") or {}).get(eid)
    if ot and ot[1]:
        avg = _f(ot[0]) / ot[1]
        if avg > th["ot_hours_per_sheet"]:
            factors.append({"factor": "Overtime load", "points": 10,
                            "evidence": f"{_f(ot[0]):g} overtime hours over {ot[1]} timesheet(s) this quarter — {avg:.1f} h each",
                            "rule": f"Average above {th['ot_hours_per_sheet']} h per timesheet +10"})

    mc = _d((ctx.get("manager_changes") or {}).get(eid))
    if mc and (today - mc).days <= th["manager_change_days"]:
        factors.append({"factor": "Manager changed", "points": 10, "evidence": f"New manager applied {mc.isoformat()}",
                        "rule": f"A manager change in the last {th['manager_change_days']} days +10"})

    n_exits = int((ctx.get("exits_by_manager") or {}).get(str(emp.get("manager_id") or ""), 0))
    if n_exits >= 1:
        factors.append({"factor": "Teammates leaving", "points": 15 if n_exits >= 2 else 10,
                        "evidence": f"{n_exits} open exit case(s) among people with the same manager",
                        "rule": "1 open exit +10, 2 or more +15"})

    # D7: pay vs the department + grade median
    pr = (ctx.get("pay_ratio") or {}).get(eid)
    if pr and pr.get("ratio") is not None and pr.get("peers", 0) >= 3 and pr["ratio"] < th["pay_ratio_watch"]:
        pts = 15 if pr["ratio"] < th["pay_ratio_low"] else 10
        factors.append({"factor": "Pay below peers", "points": pts,
                        "evidence": f"Annual pay is {pr['ratio'] * 100:.0f}% of the median of {pr['peers']} peers in {pr.get('group') or 'the same department'}",
                        "rule": f"Under {th['pay_ratio_watch'] * 100:.0f}% of the peer median +10; under {th['pay_ratio_low'] * 100:.0f}% +15 (needs 3+ peers)"})

    # D7: named survey signal
    sg = (ctx.get("survey_signals") or {}).get(eid)
    if sg:
        factors.append({"factor": "Survey signal", "points": 10, "evidence": sg,
                        "rule": f"A named survey in the last {th['survey_days']} days with eNPS ≤ {th['survey_nps_max']} or a 1–5 answer ≤ {th['survey_scale_max']} +10"})

    # D7: mandatory training overdue
    od = int((ctx.get("training_overdue") or {}).get(eid, 0))
    if od:
        factors.append({"factor": "Training overdue", "points": 5, "evidence": f"{od} mandatory course(s) past the due date",
                        "rule": "Any mandatory course overdue +5"})

    score = min(100, sum(f["points"] for f in factors))
    return {"employee_id": eid, "name": emp.get("name"), "employee_number": emp.get("employee_number"),
            "department": emp.get("department"), "job_title": emp.get("job_title"), "manager_name": emp.get("manager_name"),
            "hire_date": hd.isoformat() if hd else None, "score": score, "band": band(score), "factors": factors,
            "top_factor": max(factors, key=lambda f: f["points"])["factor"] if factors else "",
            "why": why_list(factors),
            "label": "Heuristic score — rules with fixed points, not a prediction"}


def why_list(factors: list[dict]) -> list[str]:
    """Plain sentences, biggest factor first — what a manager reads before a conversation."""
    out = []
    for f in sorted(factors, key=lambda x: -x["points"]):
        out.append(f"{f['factor']} (+{f['points']}): {f['evidence']}")
    return out


# ══════════════════════════ D7: calibration from the org's own exits ═══════════

FACTOR_KEYS = ("Tenure band", "No increment", "Rating drop", "Leave pattern change", "Overtime load", "Manager changed",
               "Teammates leaving", "Pay below peers", "Survey signal", "Training overdue")


def sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401
        return True
    except ImportError:
        return False


def calibrate(history: list[dict], min_exits: int = MIN_EXITS_FOR_CALIBRATION) -> dict:
    """history: [{left: bool, factors: [factor names that fired], score}] for leavers AND current staff.
    Returns per-factor exit rates (flagged vs not) and a lift — descriptive, only when ≥ min_exits leavers."""
    exits = sum(1 for h in history if h.get("left"))
    base = {"available": False, "exits": exits, "needed": min_exits, "people": len(history),
            "model": "rules only" + ("" if sklearn_available() else " (scikit-learn is not installed — no model is fitted)")}
    if exits < min_exits or not history:
        base["message"] = f"Calibration needs at least {min_exits} exits on record; this organisation has {exits}."
        return base
    overall = exits / len(history)
    rows = []
    for k in FACTOR_KEYS:
        flagged = [h for h in history if k in (h.get("factors") or [])]
        not_flagged = [h for h in history if k not in (h.get("factors") or [])]
        r_f = (sum(1 for h in flagged if h.get("left")) / len(flagged)) if flagged else None
        r_n = (sum(1 for h in not_flagged if h.get("left")) / len(not_flagged)) if not_flagged else None
        rows.append({"factor": k, "flagged": len(flagged), "exit_rate_flagged": round(r_f * 100, 1) if r_f is not None else None,
                     "exit_rate_not_flagged": round(r_n * 100, 1) if r_n is not None else None,
                     "lift": round(r_f / r_n, 2) if (r_f is not None and r_n) else None})
    by_band: dict[str, dict] = {}
    for h in history:
        b = band(int(h.get("score") or 0))
        d = by_band.setdefault(b, {"band": b, "people": 0, "left": 0})
        d["people"] += 1
        d["left"] += 1 if h.get("left") else 0
    bands = [{**d, "exit_rate": round(d["left"] * 100 / d["people"], 1)} for d in by_band.values()]
    out = {**base, "available": True, "overall_exit_rate": round(overall * 100, 1), "factors": rows, "bands": bands,
           "message": "Exit rates among people a factor flagged versus everyone else, from this organisation's own records. "
                      "A lift above 1 means the factor went with leaving more often here."}
    if sklearn_available():
        out["model"] = fit_model(history)
    return out


def fit_model(history: list[dict]) -> dict | str:
    """Only when scikit-learn is installed: logistic regression on factor flags → per-factor odds ratios."""
    try:
        from sklearn.linear_model import LogisticRegression  # type: ignore
        import math
        X = [[1.0 if k in (h.get("factors") or []) else 0.0 for k in FACTOR_KEYS] for h in history]
        y = [1 if h.get("left") else 0 for h in history]
        if len(set(y)) < 2:
            return "rules only (every record has the same outcome)"
        m = LogisticRegression(max_iter=500).fit(X, y)
        return {"kind": "logistic", "odds_ratios": {k: round(math.exp(c), 2) for k, c in zip(FACTOR_KEYS, m.coef_[0])},
                "intercept": round(float(m.intercept_[0]), 3), "n": len(history)}
    except Exception as e:                                   # noqa: BLE001
        return f"rules only (model fit failed: {type(e).__name__})"


def pay_ratios(emps: list[dict]) -> dict[str, dict]:
    """id → {ratio, peers, group}: annual pay ÷ median of the same department + grade (department alone when a grade
    group has fewer than 3 people). People without pay are skipped."""
    def med(xs):
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    groups: dict[tuple, list[float]] = {}
    depts: dict[str, list[float]] = {}
    for e in emps:
        pay = _f(e.get("annual_salary"))
        if pay <= 0:
            continue
        groups.setdefault((e.get("department") or "", e.get("grade") or ""), []).append(pay)
        depts.setdefault(e.get("department") or "", []).append(pay)
    out = {}
    for e in emps:
        pay = _f(e.get("annual_salary"))
        if pay <= 0:
            continue
        g = groups.get((e.get("department") or "", e.get("grade") or ""), [])
        label = f"{e.get('department') or 'department'} / grade {e.get('grade')}" if e.get("grade") else (e.get("department") or "department")
        if len(g) < 3:
            g = depts.get(e.get("department") or "", [])
            label = e.get("department") or "department"
        if len(g) < 3:
            out[str(e["id"])] = {"ratio": None, "peers": len(g), "group": label}
            continue
        m = med(g)
        out[str(e["id"])] = {"ratio": round(pay / m, 3) if m else None, "peers": len(g), "group": label}
    return out


def survey_signal(rows: list[dict], today: date, th: dict | None = None) -> dict[str, str]:
    """rows: [{employee_id, submitted_at, questions, answers, title}] from NAMED surveys → id → evidence sentence
    for the latest low answer within the window."""
    th = {**DEFAULTS, **(th or {})}
    since = today - timedelta(days=th["survey_days"])
    out: dict[str, tuple[date, str]] = {}
    for r in rows:
        d = _d(r.get("submitted_at"))
        if not d or d < since or not r.get("employee_id"):
            continue
        lows = []
        for q in r.get("questions") or []:
            a = (r.get("answers") or {}).get(q.get("key"))
            if a is None or a == "":
                continue
            try:
                v = float(a)
            except (TypeError, ValueError):
                continue
            if q.get("type") == "nps" and v <= th["survey_nps_max"]:
                lows.append(f"eNPS {v:g}")
            elif q.get("type") == "scale" and v <= th["survey_scale_max"]:
                lows.append(f"'{str(q.get('text') or '')[:40]}' = {v:g}")
        if lows:
            eid = str(r["employee_id"])
            ev = f"{r.get('title') or 'Survey'} on {d.isoformat()}: " + "; ".join(lows[:3])
            if eid not in out or d > out[eid][0]:
                out[eid] = (d, ev)
    return {k: v[1] for k, v in out.items()}


# ══════════════════════════ data assembly (SELECT only) ════════════════════════

async def build_context(org: str, today: date, include_leavers: bool = False) -> tuple[list[dict], dict]:
    from watchdogs import ORG, rows, _nm
    status_sql = "" if include_leavers else " AND status = 'active'"
    emps = await rows(f"SELECT id, employee_number, first_name, last_name, department, job_title, hire_date, manager_id, manager_name, "
                      f"grade, annual_salary, status, termination_date "
                      f"FROM employees WHERE org_id = {ORG} AND is_active IS TRUE{status_sql}", {"org": org})
    for e in emps:
        e["name"] = _nm(e)
    ctx: dict = {"revisions": {}, "dept_revised_12m": set(), "ratings": {}, "leave_days": {}, "ot": {}, "manager_changes": {}, "exits_by_manager": {},
                 "pay_ratio": pay_ratios([e for e in emps if (e.get("status") or "active") == "active"]), "survey_signals": {}, "training_overdue": {}}
    # D7: named survey answers (anonymous surveys carry no employee id and are never used here)
    ctx["survey_signals"] = survey_signal(await rows(
        f"SELECT r.employee_id, r.submitted_at, r.answers, s.questions, s.title FROM eng_survey_responses r "
        f"JOIN eng_surveys s ON s.id = r.survey_id WHERE r.org_id = {ORG} AND r.employee_id IS NOT NULL AND r.submitted_at >= :s",
        {"org": org, "s": today - timedelta(days=DEFAULTS["survey_days"])}), today)
    for r in await rows(f"SELECT a.employee_id, COUNT(*) AS n FROM eng_course_assignments a JOIN eng_courses c ON c.id = a.course_id "
                        f"WHERE a.org_id = {ORG} AND c.mandatory IS TRUE AND a.status <> 'completed' AND a.due_on < :t GROUP BY a.employee_id",
                        {"org": org, "t": today}):
        ctx["training_overdue"][str(r["employee_id"])] = int(r["n"])
    dept_of = {str(e["id"]): e.get("department") for e in emps}
    for r in await rows(f"SELECT employee_id, effective_from FROM comp_revisions WHERE org_id = {ORG} AND status IN ('approved','applied')", {"org": org}):
        eid = str(r["employee_id"])
        ctx["revisions"].setdefault(eid, []).append(r["effective_from"])
        d = _d(r["effective_from"])
        if d and (today - d).days <= 365 and dept_of.get(eid):
            ctx["dept_revised_12m"].add(dept_of[eid])
    for r in await rows(f"SELECT employee_id, COALESCE(final_score, overall_score) AS score FROM perf_reviews WHERE org_id = {ORG} "
                        "AND COALESCE(final_score, overall_score) IS NOT NULL ORDER BY created_at", {"org": org}):
        ctx["ratings"].setdefault(str(r["employee_id"]), []).append(r["score"])
    d90, d180 = today - timedelta(days=90), today - timedelta(days=180)
    for r in await rows(f"SELECT employee_id, start_date, days FROM leave_requests WHERE org_id = {ORG} AND status = 'approved' AND start_date >= :s",
                        {"org": org, "s": d180}):
        eid = str(r["employee_id"])
        cur = ctx["leave_days"].get(eid, (0.0, 0.0))
        if _d(r["start_date"]) and _d(r["start_date"]) >= d90:
            ctx["leave_days"][eid] = (cur[0] + _f(r["days"]), cur[1])
        else:
            ctx["leave_days"][eid] = (cur[0], cur[1] + _f(r["days"]))
    q_start = today - timedelta(days=91)
    for r in await rows(f"SELECT employee_id, SUM(ot_hours + ot2_hours) AS ot, COUNT(*) AS n FROM ta_timesheets WHERE org_id = {ORG} "
                        "AND period_end >= :s AND status IN ('approved','locked') GROUP BY employee_id", {"org": org, "s": q_start}):
        ctx["ot"][str(r["employee_id"])] = (_f(r["ot"]), int(r["n"]))
    for r in await rows(f"SELECT employee_id, effective_date, new_values FROM career_changes WHERE org_id = {ORG} AND status = 'applied' "
                        "AND effective_date >= :s", {"org": org, "s": d180}):
        nv = r.get("new_values") or {}
        if isinstance(nv, dict) and ("manager_id" in nv or "manager_name" in nv):
            eid = str(r["employee_id"])
            if not ctx["manager_changes"].get(eid) or _d(r["effective_date"]) > _d(ctx["manager_changes"][eid]):
                ctx["manager_changes"][eid] = r["effective_date"]
    for r in await rows(f"SELECT manager_id, COUNT(*) AS n FROM exit_cases WHERE org_id = {ORG} "
                        "AND status NOT IN ('closed','withdrawn','rejected') AND manager_id IS NOT NULL GROUP BY manager_id", {"org": org}):
        ctx["exits_by_manager"][str(r["manager_id"])] = int(r["n"])
    return emps, ctx


async def calibration_history(org: str, today: date) -> list[dict]:
    """Score every person on record (leavers scored as of their last day) → [{left, factors, score}] for calibrate()."""
    emps, ctx = await build_context(org, today, include_leavers=True)
    out = []
    for e in emps:
        left = (e.get("status") or "") == "terminated" or bool(e.get("termination_date"))
        as_of = _d(e.get("termination_date")) or today if left else today
        r = score_employee(e, ctx, as_of)
        out.append({"left": left, "factors": [f["factor"] for f in r["factors"]], "score": r["score"]})
    return out
