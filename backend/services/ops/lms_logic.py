"""LMS depth — pure rules (no database, no HTTP). Stream D7. Tested in test_lms_logic.py.

Assignment targeting, due dates from joining, reminders, question-bank draws, attempts,
learning-path progress, team / compliance reports, SCORM-lite zip validation.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from datetime import date, timedelta

import engage_logic as L

SAFE_EXT = {".html", ".htm", ".js", ".css", ".json", ".xml", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
            ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".woff", ".woff2", ".ttf", ".otf", ".txt", ".vtt", ".ico", ".pdf", ".map"}
MIME = {".html": "text/html", ".htm": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
        ".xml": "application/xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".svg": "image/svg+xml", ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg", ".wav": "audio/wav", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
        ".otf": "font/otf", ".txt": "text/plain", ".vtt": "text/vtt", ".ico": "image/x-icon", ".pdf": "application/pdf",
        ".map": "application/json"}
MAX_PACKAGE_BYTES = 60 * 1024 * 1024
MAX_ENTRY_BYTES = 40 * 1024 * 1024
MAX_ENTRIES = 2000


# ── targeting ─────────────────────────────────────────────────────────────────

def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def rule_matches(rule: dict, emp: dict) -> bool:
    """rule: {roles, departments, entities, locations, joined_from, joined_to, new_joiners_only, created_at}.
    emp: {role, department, entity_id | entity_name | entity, work_location, hire_date, status}.
    Every non-empty criterion must match (AND); an empty rule matches every active employee."""
    if (emp.get("status") or "active") == "terminated":
        return False
    roles = [r.lower() for r in (rule.get("roles") or [])]
    if roles and (emp.get("role") or "").lower() not in roles:
        return False
    deps = rule.get("departments") or []
    if deps and (emp.get("department") or "") not in deps:
        return False
    ents = [str(x) for x in (rule.get("entities") or [])]
    if ents:
        mine = {str(emp.get(k)) for k in ("entity_id", "entity_name", "entity", "legal_entity_id") if emp.get(k)}
        if not (mine & set(ents)):
            return False
    locs = rule.get("locations") or []
    if locs and (emp.get("work_location") or "") not in locs:
        return False
    hd = _d(emp.get("hire_date"))
    jf, jt = _d(rule.get("joined_from")), _d(rule.get("joined_to"))
    if (jf or jt) and not hd:
        return False
    if jf and hd < jf:
        return False
    if jt and hd > jt:
        return False
    if rule.get("new_joiners_only"):
        made = _d(rule.get("created_at"))
        if not hd or (made and hd < made):
            return False
    return True


def due_for(rule: dict, emp: dict, today: date) -> date | None:
    """Due = joining date + due_days, but never before today (existing staff get the same N days from now)."""
    days = int(rule.get("due_days") or 0)
    if days <= 0:
        return None
    hd = _d(emp.get("hire_date"))
    d = (hd + timedelta(days=days)) if hd else (today + timedelta(days=days))
    if d < today:
        d = today + timedelta(days=days)
    return d


def plan_assignments(rule: dict, employees: list[dict], existing: set[str], today: date) -> list[dict]:
    """Who the rule should assign now (skipping people who already have the course): [{employee_id, due_on}]."""
    out = []
    for e in employees:
        eid = str(e.get("id"))
        if eid in existing or not rule_matches(rule, e):
            continue
        out.append({"employee_id": eid, "due_on": due_for(rule, e, today)})
    return out


# ── reminders ─────────────────────────────────────────────────────────────────

def reminder_stage(status: str, due_on: date | None, today: date, remind_days: int) -> str | None:
    """'due_soon' within `remind_days` of the due date, 'overdue' after it; None otherwise / when done."""
    if status == "completed" or not due_on:
        return None
    if today > due_on:
        return "overdue"
    if (due_on - today).days <= max(int(remind_days or 0), 0):
        return "due_soon"
    return None


def reminders_due(assignments: list[dict], sent: set[tuple[str, str]], today: date, remind_days: int) -> list[dict]:
    """assignments: [{id, status, due_on, employee_id, course_title}] → the ones to remind now, with their stage."""
    out = []
    for a in assignments:
        st = reminder_stage(a.get("status") or "assigned", _d(a.get("due_on")), today, remind_days)
        if st and (str(a["id"]), st) not in sent:
            out.append({**a, "stage": st})
    return out


# ── quiz: bank draws + attempts ───────────────────────────────────────────────

def draw_questions(bank: list[dict], tags: list[str] | None, n: int, seed: str) -> list[dict]:
    """Deterministic draw of up to `n` active questions whose tag is in `tags` (all tagged when n <= 0).
    Same seed → same set; a new seed (next attempt) → a different order / subset."""
    tags = [t for t in (tags or []) if t]
    pool = [q for q in bank if q.get("active", True) and (not tags or q.get("tag") in tags)]
    if not pool:
        return []
    keyed = sorted(pool, key=lambda q: hashlib.sha256(f"{seed}:{q.get('id')}".encode()).hexdigest())
    return keyed[:n] if n and n > 0 else keyed


def attempts_left(max_attempts: int, attempts: int) -> int | None:
    """None = unlimited."""
    if not max_attempts or int(max_attempts) <= 0:
        return None
    return max(int(max_attempts) - int(attempts or 0), 0)


def quiz_for(course_quiz: list[dict], bank_rows: list[dict], quiz_set: list | None) -> list[dict]:
    """The questions that count for this attempt: the drawn bank set when there is one, else the course's own."""
    if quiz_set:
        by_id = {str(q.get("id")): q for q in bank_rows}
        return [by_id[str(i)] for i in quiz_set if str(i) in by_id]
    return list(course_quiz or [])


# ── learning paths ────────────────────────────────────────────────────────────

def path_progress(course_ids: list, assignments: list[dict]) -> dict:
    """assignments: [{course_id, status, progress_pct}] of one person. Steps in path order."""
    by = {str(a["course_id"]): a for a in assignments}
    steps, done = [], 0
    for i, cid in enumerate(course_ids or []):
        a = by.get(str(cid))
        st = (a or {}).get("status") or "not_assigned"
        if st == "completed":
            done += 1
        steps.append({"n": i + 1, "course_id": str(cid), "status": st, "progress_pct": (a or {}).get("progress_pct", 0),
                      "assignment_id": (a or {}).get("id")})
    n = len(steps)
    return {"steps": steps, "completed": done, "total": n, "pct": round(done * 100 / n) if n else 100,
            "status": "completed" if n and done == n else ("in_progress" if done or any(s["status"] == "in_progress" for s in steps) else "assigned")}


# ── reports ───────────────────────────────────────────────────────────────────

def team_summary(assignments: list[dict], today: date) -> dict:
    """assignments: [{employee_id, employee_name, status, due_on, mandatory}] → per person + totals."""
    people: dict[str, dict] = {}
    for a in assignments:
        p = people.setdefault(str(a["employee_id"]), {"employee_id": str(a["employee_id"]), "employee_name": a.get("employee_name"),
                                                        "assigned": 0, "completed": 0, "overdue": 0, "in_progress": 0, "mandatory_pending": 0})
        p["assigned"] += 1
        st = L.assignment_state(a.get("status") or "assigned", _d(a.get("due_on")), today)
        if st == "completed":
            p["completed"] += 1
        elif st == "overdue":
            p["overdue"] += 1
        elif st == "in_progress":
            p["in_progress"] += 1
        if a.get("mandatory") and st != "completed":
            p["mandatory_pending"] += 1
    rows = sorted(people.values(), key=lambda r: (-r["overdue"], -r["mandatory_pending"], r["employee_name"] or ""))
    for r in rows:
        r["completion_pct"] = round(r["completed"] * 100 / r["assigned"]) if r["assigned"] else None
    tot_a = sum(r["assigned"] for r in rows)
    tot_c = sum(r["completed"] for r in rows)
    return {"people": rows, "assigned": tot_a, "completed": tot_c, "overdue": sum(r["overdue"] for r in rows),
            "completion_pct": round(tot_c * 100 / tot_a) if tot_a else None}


def completion_by(assignments: list[dict], key: str, today: date) -> list[dict]:
    """Group {key: department|course_title|manager_name} → assigned / completed / overdue / pct."""
    g: dict[str, dict] = {}
    for a in assignments:
        k = str(a.get(key) or "—")
        r = g.setdefault(k, {"group": k, "assigned": 0, "completed": 0, "overdue": 0})
        r["assigned"] += 1
        st = L.assignment_state(a.get("status") or "assigned", _d(a.get("due_on")), today)
        if st == "completed":
            r["completed"] += 1
        elif st == "overdue":
            r["overdue"] += 1
    rows = sorted(g.values(), key=lambda r: r["group"])
    for r in rows:
        r["completion_pct"] = round(r["completed"] * 100 / r["assigned"]) if r["assigned"] else None
    return rows


# ── SCORM-lite packages ───────────────────────────────────────────────────────

class PackageError(ValueError):
    pass


def _norm(name: str) -> str:
    return posixpath.normpath(name.replace("\\", "/")).lstrip("/")


def inspect_package(data: bytes) -> dict:
    """Validate a zip: index.html at the root (or one top folder), safe paths, safe extensions, size caps.
    Returns {entries: [paths], entry_point, root}."""
    if not data:
        raise PackageError("The file is empty")
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageError(f"The package is too large. The limit is {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("Upload a .zip package")
    entries = []
    for info in z.infolist():
        if info.is_dir():
            continue
        raw = info.filename
        name = _norm(raw)
        if raw.startswith("/") or raw.startswith("\\") or ".." in name.split("/") or name.startswith("../") or ":" in name.split("/")[0]:
            raise PackageError(f"Unsafe path in package: {raw[:80]}")
        if name.startswith("__MACOSX/") or name.endswith(".DS_Store"):
            continue
        ext = posixpath.splitext(name)[1].lower()
        if ext not in SAFE_EXT:
            raise PackageError(f"File type not allowed in a package: {name[:80]}")
        if info.file_size > MAX_ENTRY_BYTES:
            raise PackageError(f"A file in the package is too large: {name[:80]}")
        entries.append(name)
        if len(entries) > MAX_ENTRIES:
            raise PackageError(f"Too many files in the package (limit {MAX_ENTRIES})")
    if not entries:
        raise PackageError("The package has no files")
    root = ""
    if "index.html" not in entries:
        tops = {e.split("/")[0] for e in entries if "/" in e}
        cands = [t for t in tops if f"{t}/index.html" in entries]
        if len(cands) != 1:
            raise PackageError("The package needs an index.html at its root")
        root = cands[0] + "/"
    return {"entries": entries, "entry_point": root + "index.html", "root": root}


def package_entry(data: bytes, entries: list[str], path: str) -> tuple[bytes, str] | None:
    """Read one file from the stored zip. None when it is not in the validated entry list."""
    name = _norm(path or "")
    if name not in set(entries):
        return None
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if _norm(info.filename) == name:
                return z.read(info), MIME.get(posixpath.splitext(name)[1].lower(), "application/octet-stream")
    return None
