"""Engagement depth — pure rules (no database, no HTTP). Stream D7. Tested in test_engage2_logic.py.

Survey schedules, segment (heatmap) results behind the anonymity threshold, eNPS trend maths,
manager insights, recognition budgets, onboarding (30/60/90-day) check-ins.
"""
from __future__ import annotations

from datetime import date, timedelta

import engage_logic as L

CADENCES = ("weekly", "monthly", "quarterly")
ONBOARDING_STAGES = (30, 60, 90)

ONBOARDING_QUESTIONS = {
    30: [{"key": "clear", "type": "scale", "text": "I know what is expected of me. (1-5)"},
         {"key": "tools", "type": "scale", "text": "I have the tools and access I need. (1-5)"},
         {"key": "welcome", "type": "scale", "text": "My team made me feel welcome. (1-5)"},
         {"key": "note", "type": "text", "text": "What would have made your first month better?"}],
    60: [{"key": "manager", "type": "scale", "text": "My manager gives me useful feedback. (1-5)"},
         {"key": "training", "type": "scale", "text": "The training I received prepared me for my work. (1-5)"},
         {"key": "role", "type": "scale", "text": "The role matches what I was told when I joined. (1-5)"},
         {"key": "note", "type": "text", "text": "Anything we should fix in your first 60 days?"}],
    90: [{"key": "nps", "type": "nps", "text": "How likely are you to recommend this company as a place to work? (0-10)"},
         {"key": "stay", "type": "scale", "text": "I see myself here in a year. (1-5)"},
         {"key": "growth", "type": "scale", "text": "I can see how I will grow here. (1-5)"},
         {"key": "note", "type": "text", "text": "What should we know after your first 90 days?"}],
}


# ── schedules ─────────────────────────────────────────────────────────────────

def next_run_on(cadence: str, weekday: int, day_of_month: int, after: date) -> date:
    """The first run date strictly after `after`."""
    if cadence == "weekly":
        wd = max(0, min(6, int(weekday or 0)))
        d = after + timedelta(days=(wd - after.weekday()) % 7)
        return d if d > after else d + timedelta(days=7)
    dom = max(1, min(28, int(day_of_month or 1)))
    step = 3 if cadence == "quarterly" else 1
    y, m = after.year, after.month
    d = date(y, m, dom)
    while d <= after:
        m += step
        while m > 12:
            m -= 12
            y += 1
        d = date(y, m, dom)
    return d


def survey_title(base: str, run_on: date, cadence: str) -> str:
    if cadence == "weekly":
        return f"{base} — week of {run_on.isoformat()}"
    if cadence == "quarterly":
        return f"{base} — Q{(run_on.month - 1) // 3 + 1} {run_on.year}"
    return f"{base} — {run_on.strftime('%b %Y')}"


def schedules_due(schedules: list[dict], today: date) -> list[dict]:
    """[{id, active, next_run_on}] → the ones to run now."""
    return [s for s in schedules if s.get("active", True) and s.get("next_run_on") and s["next_run_on"] <= today]


# ── results by segment (heatmap) ─────────────────────────────────────────────

def question_score(q: dict, answers: list) -> float | None:
    """One number per question: eNPS for nps, average for scale, None for text / choice."""
    vals = [a for a in answers if a is not None and a != ""]
    if q["type"] == "nps":
        return L.enps(vals)["enps"]
    if q["type"] == "scale":
        return L.average(vals)
    return None


def segment_results(questions: list[dict], responses: list[dict], threshold: int, anonymous: bool = True) -> list[dict]:
    """responses: [{answers, segment}] → one row per segment; a segment below the threshold shows no numbers
    (its row stays so HR sees participation, never the answers). Overall row first."""
    thr = max(int(threshold or 1), 1)
    groups: dict[str, list[dict]] = {}
    for r in responses:
        groups.setdefault(str(r.get("segment") or "—"), []).append(r)
    scored = [q for q in questions if q["type"] in ("nps", "scale")]
    rows = []
    for seg, rs in sorted(groups.items()):
        hidden = anonymous and len(rs) < thr
        scores = {}
        if not hidden:
            for q in scored:
                scores[q["key"]] = question_score(q, [x.get("answers", {}).get(q["key"]) for x in rs])
        rows.append({"segment": seg, "responses": len(rs), "hidden": hidden, "scores": scores})
    all_scores = {q["key"]: question_score(q, [x.get("answers", {}).get(q["key"]) for x in responses]) for q in scored}
    overall = {"segment": "All", "responses": len(responses), "hidden": anonymous and len(responses) < thr,
               "scores": {} if (anonymous and len(responses) < thr) else all_scores}
    return [overall] + rows


# ── trend ─────────────────────────────────────────────────────────────────────

def trend_with_delta(points: list[dict]) -> list[dict]:
    """[{date, score, visible}] oldest first → adds `delta` vs the previous VISIBLE point."""
    out, prev = [], None
    for p in points:
        row = dict(p)
        if p.get("visible") and p.get("score") is not None:
            row["delta"] = None if prev is None else round(p["score"] - prev, 1)
            prev = p["score"]
        else:
            row["delta"] = None
        out.append(row)
    return out


def participation(responses: int, invited: int) -> float | None:
    return round(responses * 100 / invited, 1) if invited else None


# ── manager insights ──────────────────────────────────────────────────────────

def manager_insights(team_ids: set[str], surveys: list[dict], kudos: list[dict], threshold: int, manager_id: str) -> dict:
    """surveys: [{id, title, kind, date, questions, responses: [{answers, manager_id}]}] (all org responses with
    their stamped manager segment); kudos: [{from_employee_id, to_employee_id, points, created_at, status}].
    Team eNPS / pulse per survey is shown only when at least `threshold` team members answered."""
    thr = max(int(threshold or 1), 1)
    n_team = len(team_ids)
    out_s = []
    for s in surveys:
        team_rs = [r for r in s.get("responses", []) if str(r.get("manager_id") or "") == str(manager_id)]
        hidden = len(team_rs) < thr
        score = None
        if not hidden:
            for q in s.get("questions", []):
                if q["type"] in ("nps", "scale"):
                    score = question_score(q, [r.get("answers", {}).get(q["key"]) for r in team_rs])
                    break
        out_s.append({"survey_id": s["id"], "title": s["title"], "kind": s["kind"], "date": s.get("date"),
                      "team_responses": len(team_rs), "participation_pct": participation(len(team_rs), n_team),
                      "hidden": hidden, "score": score,
                      "company_score": s.get("company_score")})
    approved = [k for k in kudos if k.get("status", "approved") == "approved"]
    given = [k for k in approved if str(k.get("from_employee_id") or "") == str(manager_id)]
    received = [k for k in approved if str(k.get("to_employee_id") or "") in team_ids]
    recognised = {str(k.get("to_employee_id")) for k in received}
    return {"team_size": n_team, "surveys": out_s,
            "recognition": {"given": len(given), "given_points": sum(int(k.get("points") or 0) for k in given),
                            "received_by_team": len(received), "team_members_recognised": len(recognised),
                            "team_members_not_recognised": max(n_team - len(recognised), 0)}}


# ── recognition budgets ───────────────────────────────────────────────────────

def budget_remaining(points_budget: int, points_used: int) -> int:
    return max(int(points_budget or 0) - int(points_used or 0), 0)


def can_spend(points_budget: int, points_used: int, points: int) -> bool:
    return int(points or 0) >= 0 and int(points or 0) <= budget_remaining(points_budget, points_used)


def points_leaderboard(kudos: list[dict], month: str) -> list[dict]:
    """[{to_employee_id, to_name, points, created_at, status}] → top receivers by points this month."""
    tot: dict[str, dict] = {}
    for k in kudos:
        if k.get("status", "approved") != "approved" or k["created_at"].strftime("%Y-%m") != month:
            continue
        p = tot.setdefault(str(k["to_employee_id"]), {"employee_id": str(k["to_employee_id"]), "name": k.get("to_name"), "points": 0, "count": 0})
        p["points"] += int(k.get("points") or 0)
        p["count"] += 1
    return sorted(tot.values(), key=lambda r: (-r["points"], -r["count"], r["name"] or ""))[:20]


# ── onboarding check-ins ──────────────────────────────────────────────────────

def onboarding_due(employees: list[dict], sent: set[tuple[str, int]], today: date, grace_days: int = 7) -> list[dict]:
    """employees: [{id, hire_date, status}] → [{employee_id, stage}] for people whose joining date + stage days is
    today or within the last `grace_days` (so a service that was down still sends it), never twice."""
    out = []
    for e in employees:
        if (e.get("status") or "active") == "terminated":
            continue
        hd = e.get("hire_date")
        if isinstance(hd, str):
            try:
                hd = date.fromisoformat(hd[:10])
            except ValueError:
                hd = None
        if not hd:
            continue
        for st in ONBOARDING_STAGES:
            due = hd + timedelta(days=st)
            if due <= today <= due + timedelta(days=grace_days) and (str(e["id"]), st) not in sent:
                out.append({"employee_id": str(e["id"]), "stage": st, "due": due})
    return out
