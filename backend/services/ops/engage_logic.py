"""Engagement — pure rules (helpdesk SLA, survey maths, quiz scoring, compliance). Stream G4.

No database, no HTTP: everything here is unit-tested in test_engage_logic.py.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# ── defaults an org starts with (editable on the Helpdesk → Settings tab) ─────

DEFAULT_CATEGORIES = [
    {"key": "payroll", "label": "Salary and payslip", "sla_hours": 48, "assign_role": "payroll"},
    {"key": "leave", "label": "Leave and attendance", "sla_hours": 24, "assign_role": "hr"},
    {"key": "documents", "label": "Letters and documents", "sla_hours": 72, "assign_role": "hr"},
    {"key": "benefits", "label": "Benefits and insurance", "sla_hours": 72, "assign_role": "hr"},
    {"key": "it_access", "label": "Login and access", "sla_hours": 8, "assign_role": "org_admin"},
    {"key": "policy", "label": "Policy question", "sla_hours": 48, "assign_role": "hr"},
    {"key": "grievance", "label": "Grievance (confidential)", "sla_hours": 24, "assign_role": "hr"},
    {"key": "other", "label": "Something else", "sla_hours": 72, "assign_role": "hr"},
]
DEFAULT_VALUES = ["Teamwork", "Customer first", "Ownership", "Integrity", "Learning", "Safety"]
DEFAULT_ANONYMITY_THRESHOLD = 5
TICKET_STATES = ("open", "in_progress", "waiting", "resolved", "closed")
PRIORITIES = ("low", "normal", "high", "urgent")

SURVEY_TEMPLATES = {
    "enps": {"title": "eNPS pulse", "kind": "enps", "anonymous": True, "questions": [
        {"key": "nps", "type": "nps", "text": "How likely are you to recommend this company as a place to work? (0-10)"},
        {"key": "why", "type": "text", "text": "What is the main reason for your score?"},
    ]},
    "pulse": {"title": "Weekly pulse", "kind": "pulse", "anonymous": True, "questions": [
        {"key": "energy", "type": "scale", "text": "I had the energy I needed this week. (1-5)"},
        {"key": "support", "type": "scale", "text": "My manager supported me this week. (1-5)"},
        {"key": "workload", "type": "scale", "text": "My workload was manageable. (1-5)"},
        {"key": "note", "type": "text", "text": "Anything you want to share?"},
    ]},
    "exit": {"title": "Exit survey", "kind": "exit", "anonymous": False, "questions": [
        {"key": "reason", "type": "choice", "text": "What is the main reason you are leaving?",
         "options": ["Better opportunity", "Pay", "Growth", "Manager", "Work-life balance", "Relocation", "Personal", "Other"]},
        {"key": "again", "type": "scale", "text": "Would you work here again? (1-5)"},
        {"key": "manager", "type": "scale", "text": "How would you rate your manager? (1-5)"},
        {"key": "improve", "type": "text", "text": "What should the company improve?"},
    ]},
    "onboarding": {"title": "30-day check-in", "kind": "pulse", "anonymous": False, "questions": [
        {"key": "clear", "type": "scale", "text": "I know what is expected of me. (1-5)"},
        {"key": "tools", "type": "scale", "text": "I have the tools I need. (1-5)"},
        {"key": "note", "type": "text", "text": "What would have made your first month better?"},
    ]},
}


# ── helpdesk ──────────────────────────────────────────────────────────────────

def category_for(categories: list[dict], key: str) -> dict:
    for c in categories or []:
        if c.get("key") == key:
            return c
    return {"key": key or "other", "label": "Something else", "sla_hours": 72, "assign_role": "hr"}


def sla_due(created_at: datetime, sla_hours: int | float) -> datetime:
    return created_at + timedelta(hours=float(sla_hours or 0))


def sla_state(now: datetime, due_at: datetime | None, status: str, resolved_at: datetime | None = None) -> str:
    """breached | at_risk | ok | met | n/a — judged at resolution time once resolved."""
    if due_at is None:
        return "n/a"
    if status in ("resolved", "closed"):
        if resolved_at is None:
            return "met"
        return "met" if resolved_at <= due_at else "breached"
    if now > due_at:
        return "breached"
    if (due_at - now) <= timedelta(hours=4):
        return "at_risk"
    return "ok"


def age_hours(now: datetime, created_at: datetime, resolved_at: datetime | None = None) -> float:
    end = resolved_at or now
    return round(max((end - created_at).total_seconds(), 0) / 3600, 1)


def age_bucket(hours: float) -> str:
    if hours < 24:
        return "0-1 day"
    if hours < 72:
        return "1-3 days"
    if hours < 168:
        return "3-7 days"
    return "over 7 days"


def ageing_report(tickets: list[dict], now: datetime) -> dict:
    """tickets: [{status, created_at, due_at, resolved_at, category}] → counts per bucket, breaches, by category."""
    buckets: dict[str, int] = {"0-1 day": 0, "1-3 days": 0, "3-7 days": 0, "over 7 days": 0}
    by_cat: dict[str, dict] = {}
    open_n = breached_open = breached_total = resolved_n = 0
    for t in tickets:
        st = sla_state(now, t.get("due_at"), t["status"], t.get("resolved_at"))
        cat = by_cat.setdefault(t.get("category") or "other", {"open": 0, "resolved": 0, "breached": 0, "avg_hours": 0.0, "_h": []})
        if t["status"] in ("resolved", "closed"):
            resolved_n += 1
            cat["resolved"] += 1
            cat["_h"].append(age_hours(now, t["created_at"], t.get("resolved_at")))
        else:
            open_n += 1
            cat["open"] += 1
            buckets[age_bucket(age_hours(now, t["created_at"]))] += 1
            if st == "breached":
                breached_open += 1
        if st == "breached":
            breached_total += 1
            cat["breached"] += 1
    for c in by_cat.values():
        hs = c.pop("_h")
        c["avg_hours"] = round(sum(hs) / len(hs), 1) if hs else 0.0
    return {"open": open_n, "resolved": resolved_n, "breached_open": breached_open, "breached_total": breached_total,
            "buckets": buckets, "by_category": by_cat}


def next_number(prefix: str, seq: int, pad: int = 4) -> str:
    return f"{prefix}-{str(int(seq)).zfill(pad)}"


# ── surveys ───────────────────────────────────────────────────────────────────

def anonymity_ok(responses: int, threshold: int) -> bool:
    return responses >= max(int(threshold or 0), 1)


def enps(scores: list[int | float]) -> dict:
    """0-6 detractor, 7-8 passive, 9-10 promoter. eNPS = %promoters − %detractors."""
    vals = [int(s) for s in scores if s is not None and 0 <= int(s) <= 10]
    n = len(vals)
    if not n:
        return {"n": 0, "promoters": 0, "passives": 0, "detractors": 0, "enps": None}
    p = sum(1 for v in vals if v >= 9)
    d = sum(1 for v in vals if v <= 6)
    return {"n": n, "promoters": p, "passives": n - p - d, "detractors": d, "enps": round((p - d) * 100 / n)}


def distribution(values: list, options: list | None = None) -> dict:
    """{value: count} with every option present (0 when nobody picked it)."""
    out: dict = {str(o): 0 for o in (options or [])}
    for v in values:
        if v is None or v == "":
            continue
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def average(values: list) -> float | None:
    nums = [float(v) for v in values if v is not None and str(v).strip() != ""]
    return round(sum(nums) / len(nums), 2) if nums else None


def survey_results(questions: list[dict], responses: list[dict], threshold: int, anonymous: bool) -> dict:
    """Results per question. Anonymous surveys hide everything until `threshold` responses exist."""
    n = len(responses)
    if anonymous and not anonymity_ok(n, threshold):
        return {"responses": n, "visible": False, "threshold": threshold,
                "message": f"Results show once at least {threshold} people have answered ({n} so far).", "questions": []}
    qs = []
    for q in questions:
        answers = [r.get("answers", {}).get(q["key"]) for r in responses]
        answers = [a for a in answers if a is not None and a != ""]
        row = {"key": q["key"], "text": q["text"], "type": q["type"], "answered": len(answers)}
        if q["type"] == "nps":
            row.update(enps(answers))
            row["distribution"] = distribution(answers, list(range(0, 11)))
        elif q["type"] == "scale":
            row["average"] = average(answers)
            row["distribution"] = distribution(answers, [1, 2, 3, 4, 5])
        elif q["type"] == "choice":
            row["distribution"] = distribution(answers, q.get("options") or [])
        else:
            row["texts"] = [str(a)[:500] for a in answers][:200]
        qs.append(row)
    return {"responses": n, "visible": True, "threshold": threshold, "questions": qs}


def in_target(target: dict | None, emp: dict) -> bool:
    """target: {departments: [], employee_ids: [], locations: []} — empty = everyone."""
    t = target or {}
    ids, deps, locs = t.get("employee_ids") or [], t.get("departments") or [], t.get("locations") or []
    if not (ids or deps or locs):
        return True
    if ids and str(emp.get("id")) in [str(i) for i in ids]:
        return True
    if deps and (emp.get("department") or "") in deps:
        return True
    if locs and (emp.get("work_location") or "") in locs:
        return True
    return False


def survey_open(status: str, opens_on: date | None, closes_on: date | None, today: date) -> bool:
    if status != "open":
        return False
    if opens_on and today < opens_on:
        return False
    if closes_on and today > closes_on:
        return False
    return True


# ── recognition ───────────────────────────────────────────────────────────────

def monthly_summary(kudos: list[dict], month: str) -> dict:
    """kudos: [{created_at (datetime), to_employee_id, to_name, from_name, value_tag, status}] for month 'YYYY-MM'."""
    rows = [k for k in kudos if k.get("status") == "approved" and k["created_at"].strftime("%Y-%m") == month]
    by_person: dict[str, dict] = {}
    by_value: dict[str, int] = {}
    givers: dict[str, int] = {}
    for k in rows:
        p = by_person.setdefault(str(k["to_employee_id"]), {"employee_id": str(k["to_employee_id"]), "name": k.get("to_name"), "count": 0})
        p["count"] += 1
        by_value[k.get("value_tag") or "—"] = by_value.get(k.get("value_tag") or "—", 0) + 1
        givers[k.get("from_name") or "—"] = givers.get(k.get("from_name") or "—", 0) + 1
    top = sorted(by_person.values(), key=lambda r: (-r["count"], r["name"] or ""))[:10]
    return {"month": month, "total": len(rows), "people_recognised": len(by_person), "top": top,
            "by_value": by_value, "top_givers": sorted(givers.items(), key=lambda kv: -kv[1])[:5]}


# ── LMS ───────────────────────────────────────────────────────────────────────

def quiz_score(quiz: list[dict], answers: dict | list) -> dict:
    """quiz: [{q, options: [...], answer: index}] ; answers: {idx: choice} or [choice,...] → {score, correct, total, passed?}"""
    total = len(quiz or [])
    if not total:
        return {"score": 100, "correct": 0, "total": 0}
    if isinstance(answers, list):
        answers = {i: a for i, a in enumerate(answers)}
    correct = 0
    for i, q in enumerate(quiz):
        a = answers.get(i, answers.get(str(i)))
        try:
            if a is not None and int(a) == int(q.get("answer", -1)):
                correct += 1
        except (TypeError, ValueError):
            pass
    return {"score": round(correct * 100 / total), "correct": correct, "total": total}


def passed(score: int, pass_mark: int) -> bool:
    return int(score) >= int(pass_mark or 0)


def progress_pct(modules: list[dict], completed: list[str], quiz_done: bool, has_quiz: bool) -> int:
    steps = len(modules or []) + (1 if has_quiz else 0)
    if not steps:
        return 100
    done = len([m for m in (modules or []) if m.get("key") in (completed or [])]) + (1 if (has_quiz and quiz_done) else 0)
    return round(done * 100 / steps)


def assignment_state(status: str, due_on: date | None, today: date) -> str:
    if status == "completed":
        return "completed"
    if due_on and today > due_on:
        return "overdue"
    return status or "assigned"


def compliance_report(courses: list[dict], assignments: list[dict], today: date) -> dict:
    """Mandatory-training compliance: per course → assigned / completed / overdue / % compliant."""
    rows = []
    for c in courses:
        if not c.get("mandatory"):
            continue
        a = [x for x in assignments if str(x["course_id"]) == str(c["id"])]
        done = sum(1 for x in a if x["status"] == "completed")
        overdue = sum(1 for x in a if assignment_state(x["status"], x.get("due_on"), today) == "overdue")
        rows.append({"course_id": str(c["id"]), "title": c["title"], "assigned": len(a), "completed": done,
                     "overdue": overdue, "pending": len(a) - done,
                     "compliance_pct": round(done * 100 / len(a)) if a else None})
    total_a = sum(r["assigned"] for r in rows)
    total_d = sum(r["completed"] for r in rows)
    return {"courses": rows, "assigned": total_a, "completed": total_d,
            "compliance_pct": round(total_d * 100 / total_a) if total_a else None}


def certificate_number(prefix: str, seq: int, year: int) -> str:
    return f"{prefix}-{year}-{str(int(seq)).zfill(4)}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
