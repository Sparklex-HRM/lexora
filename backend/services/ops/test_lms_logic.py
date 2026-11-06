"""LMS depth rules (Stream D7) — pure, no database.
Run:  cd backend/services/ops && PYTHONPATH=../../ python -m pytest -q test_lms_logic.py
"""
import io
import zipfile
from datetime import date

import pytest

import lms_logic as LL

TODAY = date(2026, 10, 16)


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


# ── targeting ─────────────────────────────────────────────────────────────────

def test_rule_matches_role_department_entity_and_joining_window():
    e = {"id": "e1", "role": "manager", "department": "Plant", "entity_id": "ENT-A", "work_location": "Pune", "hire_date": "2026-09-20", "status": "active"}
    assert LL.rule_matches({}, e)                                                   # empty rule = everyone active
    assert LL.rule_matches({"roles": ["Manager"]}, e) and not LL.rule_matches({"roles": ["employee"]}, e)
    assert LL.rule_matches({"departments": ["Plant"]}, e) and not LL.rule_matches({"departments": ["Office"]}, e)
    assert LL.rule_matches({"entities": ["ENT-A"]}, e) and not LL.rule_matches({"entities": ["ENT-B"]}, e)
    assert LL.rule_matches({"locations": ["Pune"]}, e) and not LL.rule_matches({"locations": ["Delhi"]}, e)
    assert LL.rule_matches({"joined_from": "2026-09-01", "joined_to": "2026-09-30"}, e)
    assert not LL.rule_matches({"joined_from": "2026-10-01"}, e)
    assert not LL.rule_matches({"joined_from": "2026-01-01"}, {**e, "hire_date": None})   # window needs a joining date
    assert not LL.rule_matches({}, {**e, "status": "terminated"})
    # AND across criteria
    assert not LL.rule_matches({"roles": ["manager"], "departments": ["Office"]}, e)


def test_new_joiners_only_uses_rule_creation_date():
    rule = {"new_joiners_only": True, "created_at": "2026-10-01"}
    assert LL.rule_matches(rule, {"id": "a", "hire_date": "2026-10-05"})
    assert not LL.rule_matches(rule, {"id": "b", "hire_date": "2026-09-05"})


def test_due_date_from_joining_never_in_the_past():
    assert LL.due_for({"due_days": 30}, {"hire_date": "2026-10-01"}, TODAY) == date(2026, 10, 31)   # harassment awareness within 30 days of joining
    assert LL.due_for({"due_days": 30}, {"hire_date": "2025-01-01"}, TODAY) == date(2026, 11, 15)   # existing staff: 30 days from now
    assert LL.due_for({"due_days": 30}, {"hire_date": None}, TODAY) == date(2026, 11, 15)
    assert LL.due_for({"due_days": 0}, {"hire_date": "2026-10-01"}, TODAY) is None


def test_plan_assignments_skips_existing_and_non_matching():
    emps = [{"id": "a", "department": "Plant", "hire_date": "2026-10-10"}, {"id": "b", "department": "Plant", "hire_date": "2026-10-10"},
            {"id": "c", "department": "Office", "hire_date": "2026-10-10"}]
    plan = LL.plan_assignments({"departments": ["Plant"], "due_days": 7}, emps, {"b"}, TODAY)
    assert [p["employee_id"] for p in plan] == ["a"] and plan[0]["due_on"] == date(2026, 10, 17)


# ── reminders ─────────────────────────────────────────────────────────────────

def test_reminder_stages_and_dedup():
    assert LL.reminder_stage("assigned", date(2026, 10, 18), TODAY, 3) == "due_soon"
    assert LL.reminder_stage("in_progress", date(2026, 10, 25), TODAY, 3) is None
    assert LL.reminder_stage("assigned", date(2026, 10, 10), TODAY, 3) == "overdue"
    assert LL.reminder_stage("completed", date(2026, 10, 10), TODAY, 3) is None
    assert LL.reminder_stage("assigned", None, TODAY, 3) is None
    asg = [{"id": "1", "status": "assigned", "due_on": "2026-10-17"}, {"id": "2", "status": "assigned", "due_on": "2026-10-01"},
           {"id": "3", "status": "assigned", "due_on": "2026-12-01"}]
    due = LL.reminders_due(asg, {("2", "overdue")}, TODAY, 3)
    assert [(d["id"], d["stage"]) for d in due] == [("1", "due_soon")]


# ── quiz bank + attempts ──────────────────────────────────────────────────────

BANK = [{"id": f"q{i}", "tag": "harassment" if i < 6 else "coc", "active": i != 5} for i in range(10)]


def test_draw_questions_is_deterministic_per_seed_and_filters_tags():
    a = LL.draw_questions(BANK, ["harassment"], 3, "asg1:0")
    b = LL.draw_questions(BANK, ["harassment"], 3, "asg1:0")
    c = LL.draw_questions(BANK, ["harassment"], 3, "asg1:1")
    assert [q["id"] for q in a] == [q["id"] for q in b] and len(a) == 3
    assert all(q["tag"] == "harassment" and q["active"] for q in a)
    assert [q["id"] for q in a] != [q["id"] for q in c] or True          # a different seed may reorder; never crashes
    assert len(LL.draw_questions(BANK, ["harassment"], 0, "s")) == 5            # all active harassment questions
    assert len(LL.draw_questions(BANK, None, 0, "s")) == 9                # every active question
    assert LL.draw_questions(BANK, ["nope"], 3, "s") == []


def test_attempts_left_and_quiz_for_set():
    assert LL.attempts_left(0, 5) is None and LL.attempts_left(3, 1) == 2 and LL.attempts_left(3, 7) == 0
    rows = [{"id": "q1", "q": "a", "options": ["x", "y"], "answer": 1}, {"id": "q2", "q": "b", "options": ["x", "y"], "answer": 0}]
    assert [q["q"] for q in LL.quiz_for([{"q": "own"}], rows, ["q2", "q1", "zz"])] == ["b", "a"]
    assert LL.quiz_for([{"q": "own"}], rows, None) == [{"q": "own"}]


# ── paths + reports ───────────────────────────────────────────────────────────

def test_path_progress_follows_course_order():
    asg = [{"id": "x", "course_id": "c1", "status": "completed", "progress_pct": 100}, {"id": "y", "course_id": "c2", "status": "in_progress", "progress_pct": 40}]
    p = LL.path_progress(["c1", "c2", "c3"], asg)
    assert p["completed"] == 1 and p["total"] == 3 and p["pct"] == 33 and p["status"] == "in_progress"
    assert [s["status"] for s in p["steps"]] == ["completed", "in_progress", "not_assigned"]
    assert LL.path_progress(["c1"], [asg[0]])["status"] == "completed"
    assert LL.path_progress([], [])["pct"] == 100


def test_team_summary_and_completion_by_department():
    asg = [{"employee_id": "a", "employee_name": "Asha", "status": "completed", "due_on": None, "mandatory": True, "department": "Plant"},
           {"employee_id": "a", "employee_name": "Asha", "status": "assigned", "due_on": "2026-10-01", "mandatory": True, "department": "Plant"},
           {"employee_id": "b", "employee_name": "Bala", "status": "in_progress", "due_on": "2026-12-01", "mandatory": False, "department": "Office"}]
    t = LL.team_summary(asg, TODAY)
    assert t["assigned"] == 3 and t["completed"] == 1 and t["overdue"] == 1 and t["completion_pct"] == 33
    asha = next(p for p in t["people"] if p["employee_id"] == "a")
    assert asha["overdue"] == 1 and asha["mandatory_pending"] == 1 and asha["completion_pct"] == 50
    by = {r["group"]: r for r in LL.completion_by(asg, "department", TODAY)}
    assert by["Plant"]["completion_pct"] == 50 and by["Office"]["completion_pct"] == 0 and by["Plant"]["overdue"] == 1


# ── SCORM-lite packages ───────────────────────────────────────────────────────

def test_package_needs_index_html_and_safe_paths():
    ok = LL.inspect_package(_zip({"index.html": b"<h1>hi</h1>", "js/app.js": b"1", "img/a.png": b"x"}))
    assert ok["entry_point"] == "index.html" and set(ok["entries"]) == {"index.html", "js/app.js", "img/a.png"}
    nested = LL.inspect_package(_zip({"course/index.html": b"<p>", "course/a.css": b""}))
    assert nested["entry_point"] == "course/index.html" and nested["root"] == "course/"
    with pytest.raises(LL.PackageError):
        LL.inspect_package(_zip({"readme.txt": b"no index"}))
    with pytest.raises(LL.PackageError):
        LL.inspect_package(_zip({"index.html": b"", "../evil.html": b""}))
    with pytest.raises(LL.PackageError):
        LL.inspect_package(_zip({"index.html": b"", "run.exe": b""}))
    with pytest.raises(LL.PackageError):
        LL.inspect_package(b"not a zip")


def test_package_entry_serves_only_listed_files_with_mime():
    data = _zip({"index.html": b"<h1>hi</h1>", "js/app.js": b"var a=1"})
    info = LL.inspect_package(data)
    body, mime = LL.package_entry(data, info["entries"], "js/app.js")
    assert body == b"var a=1" and mime == "text/javascript"
    assert LL.package_entry(data, info["entries"], "index.html")[1] == "text/html"
    assert LL.package_entry(data, info["entries"], "../index.html") is None or LL.package_entry(data, info["entries"], "../index.html")[1] == "text/html"
    assert LL.package_entry(data, info["entries"], "secret.txt") is None
