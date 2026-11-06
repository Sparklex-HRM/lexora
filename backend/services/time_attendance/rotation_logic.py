"""Shift rotation patterns — PURE (gap G2, 2026-10-23). No database, no request.

A PATTERN is an N-day cycle of shift codes (or "OFF") plus the shift definitions:

    {"cycle": ["A","A","A","A","A","A","OFF", "B",…], "shifts": {"A": {"name": "Morning", "start": "06:00",
     "end": "14:00", "break_minutes": 30}, …}}

An ASSIGNMENT puts an employee (or a whole department = a crew) on a pattern from a start date, with an
optional offset into the cycle (crew B starts 7 days into the A/B/C rotation). ``plan()`` turns
assignments into roster rows for a window, respecting — in this order —
    1. manual / swapped / published rows already on the roster (kept unless told to overwrite),
    2. approved leave (no shift is planned on a leave day),
    3. holidays (an OFF row marked "Holiday"),
    4. the company's minimum rest between two shifts (a shift that would start too soon after the
       previous one is not planned — reported).
Everything the generator did NOT do is returned in ``conflicts`` / ``skipped`` with a plain reason.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

OFF = "OFF"
MAX_CYCLE = 84                  # 12 weeks
NIGHT_FROM, NIGHT_TO = "19:00", "06:00"


def _t(s: str) -> int:
    hh, mm = (s or "00:00").split(":")[:2]
    return int(hh) * 60 + int(mm)


def _d(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def shift_hours(sh: dict) -> float:
    """Paid hours of a shift: end − start (overnight wraps) − break."""
    if sh.get("hours") not in (None, ""):
        return round(float(sh["hours"]), 2)
    s, e = _t(sh.get("start") or "09:00"), _t(sh.get("end") or "17:00")
    if e <= s:
        e += 24 * 60
    return round(max(0, e - s - int(sh.get("break_minutes") or 0)) / 60, 2)


def window(sh: dict, d: date) -> tuple[datetime, datetime]:
    """(start, end) of the shift worked on date d (an overnight shift ends the next day)."""
    s, e = _t(sh.get("start") or "09:00"), _t(sh.get("end") or "17:00")
    start = datetime(d.year, d.month, d.day) + timedelta(minutes=s)
    end = datetime(d.year, d.month, d.day) + timedelta(minutes=e if e > s else e + 24 * 60)
    return start, end


def is_night(sh: dict, night_from: str = NIGHT_FROM, night_to: str = NIGHT_TO) -> bool:
    s, e, nf, nt = _t(sh.get("start") or "09:00"), _t(sh.get("end") or "17:00"), _t(night_from), _t(night_to)
    if e <= s:
        e += 24 * 60
    return e > nf or s < nt


# ══════════════════════════ templates ═════════════════════════════════════════

def _sh(name, start, end, brk=30, color=None):
    return {"name": name, "start": start, "end": end, "break_minutes": brk, **({"color": color} if color else {})}


TEMPLATES: dict[str, dict] = {
    "three_shift_weekly": {
        "name": "3-shift A / B / C, weekly rotation",
        "description": "Week 1 mornings, week 2 afternoons, week 3 nights; six days on, the 7th off. Give crews B and C an offset of 7 and 14 days so all three shifts are covered.",
        "shifts": {"A": _sh("Morning", "06:00", "14:00", 30, "#0ea5e9"), "B": _sh("Afternoon", "14:00", "22:00", 30, "#f59e0b"),
                   "C": _sh("Night", "22:00", "06:00", 30, "#6366f1")},
        "cycle": ["A"] * 6 + [OFF] + ["B"] * 6 + [OFF] + ["C"] * 6 + [OFF]},
    "four_on_two_off": {
        "name": "4 on, 2 off",
        "description": "Four day shifts then two days off — a 6-day cycle that walks across the week.",
        "shifts": {"D": _sh("Day", "08:00", "16:00", 30, "#10b981")},
        "cycle": ["D"] * 4 + [OFF] * 2},
    "fixed_weekly_off": {
        "name": "General shift, fixed weekly off (Sunday)",
        "description": "Monday to Saturday general shift, Sunday off. Start the assignment on a Monday.",
        "shifts": {"G": _sh("General", "09:00", "18:00", 60, "#7830ea")},
        "cycle": ["G"] * 6 + [OFF]},
    "rotating_weekly_off": {
        "name": "General shift, rotating weekly off",
        "description": "One day off every week, moving one day later each week (Sun, Mon, Tue …) — a 7-week cycle. Start on a Monday.",
        "shifts": {"G": _sh("General", "09:00", "18:00", 60, "#7830ea")},
        "cycle": [c for w in range(7) for c in ["G" if d != (6 + w) % 7 else OFF for d in range(7)]]},
    "two_shift_fortnightly": {
        "name": "2-shift, fortnightly rotation",
        "description": "Two weeks of first shift, two weeks of second shift; Sundays off.",
        "shifts": {"F": _sh("First", "07:00", "15:30", 30, "#0ea5e9"), "S": _sh("Second", "15:30", "00:00", 30, "#f59e0b")},
        "cycle": (["F"] * 6 + [OFF]) * 2 + (["S"] * 6 + [OFF]) * 2},
}


def template_out(key: str) -> dict:
    t = TEMPLATES[key]
    return {"key": key, "name": t["name"], "description": t["description"], "cycle_days": len(t["cycle"]),
            "cycle": list(t["cycle"]), "shifts": {k: {**v, "hours": shift_hours(v)} for k, v in t["shifts"].items()}}


# ══════════════════════════ validation ════════════════════════════════════════

class PatternError(ValueError):
    pass


_HHMM = __import__("re").compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_pattern(cycle, shifts) -> tuple[list[str], dict]:
    """Normalise + check. Raises PatternError with one plain sentence."""
    if not isinstance(shifts, dict) or not shifts:
        raise PatternError("Define at least one shift (code, start and end time)")
    out_sh = {}
    for code, sh in shifts.items():
        c = str(code or "").strip().upper()[:6]
        if not c or c == OFF:
            raise PatternError("Each shift needs a short code (A, B, N …); OFF is reserved for a day off")
        if not isinstance(sh, dict):
            raise PatternError(f"Shift {c} is not valid")
        st, en = str(sh.get("start") or ""), str(sh.get("end") or "")
        if not _HHMM.match(st) or not _HHMM.match(en):
            raise PatternError(f"Shift {c}: start and end must be HH:MM (24-hour)")
        if st == en:
            raise PatternError(f"Shift {c}: start and end cannot be the same time")
        brk = int(sh.get("break_minutes") or 0)
        if brk < 0 or brk > 180:
            raise PatternError(f"Shift {c}: break must be 0–180 minutes")
        row = {"name": str(sh.get("name") or c)[:40], "start": st, "end": en, "break_minutes": brk}
        if sh.get("color"):
            row["color"] = str(sh["color"])[:9]
        row["hours"] = shift_hours(row)
        if row["hours"] <= 0:
            raise PatternError(f"Shift {c}: the break is longer than the shift")
        out_sh[c] = row
    if not isinstance(cycle, list) or not cycle:
        raise PatternError("The cycle needs at least one day")
    if len(cycle) > MAX_CYCLE:
        raise PatternError(f"A cycle can be at most {MAX_CYCLE} days")
    out_cy = []
    for i, c in enumerate(cycle, start=1):
        k = str(c or OFF).strip().upper()
        if k != OFF and k not in out_sh:
            raise PatternError(f"Day {i} uses shift {k}, which is not defined")
        out_cy.append(k)
    if all(k == OFF for k in out_cy):
        raise PatternError("The cycle has no working day")
    return out_cy, out_sh


# ══════════════════════════ expansion ═════════════════════════════════════════

def code_on(cycle: list[str], start: date, offset: int, d: date) -> str | None:
    """The cycle entry for date d of an assignment starting on ``start`` (None before the start)."""
    if d < start:
        return None
    return cycle[((d - start).days + int(offset or 0)) % len(cycle)]


def preview(cycle: list[str], shifts: dict, start: date, days: int, offset: int = 0) -> list[dict]:
    out = []
    for i in range(max(1, min(int(days or 28), 366))):
        d = start + timedelta(days=i)
        c = code_on(cycle, start, offset, d)
        sh = shifts.get(c) if c and c != OFF else None
        out.append({"date": d.isoformat(), "weekday": d.strftime("%a"), "code": c,
                    "name": (sh or {}).get("name") or ("Off" if c == OFF else ""),
                    "start": (sh or {}).get("start"), "end": (sh or {}).get("end"),
                    "hours": shift_hours(sh) if sh else 0, "night": bool(sh and is_night(sh)), "color": (sh or {}).get("color")})
    return out


def coverage(cycle: list[str], crews: int, crew_offset: int) -> dict:
    """How many crews are on each shift code per cycle day when ``crews`` crews start ``crew_offset`` days apart."""
    n = len(cycle)
    out = []
    for day in range(n):
        cnt: dict[str, int] = {}
        for k in range(max(1, crews)):
            c = cycle[(day + k * crew_offset) % n]
            cnt[c] = cnt.get(c, 0) + 1
        out.append(cnt)
    return {"days": n, "per_day": out}


# ══════════════════════════ the planner ═══════════════════════════════════════

def rest_between(prev_end: datetime | None, nxt_start: datetime) -> float | None:
    if prev_end is None:
        return None
    return round((nxt_start - prev_end).total_seconds() / 3600, 2)


def plan(*, assignments: list[dict], patterns: dict[str, dict], employees: dict[str, dict], start: date, end: date,
         leave: dict[str, set] | None = None, holidays: dict[str, dict] | None = None, existing: dict | None = None,
         min_rest_hours: float = 0, overwrite_manual: bool = False, overwrite_published: bool = False) -> dict:
    """Roster rows for [start, end].

    assignments: [{id, pattern_id, employee_id, start_date, end_date|None, offset_days}] — ONE per employee
                 (the caller resolves team vs personal: a personal assignment wins).
    patterns:    {pattern_id: {cycle, shifts, name}}
    employees:   {employee_id: {first_name, last_name, status}}
    leave:       {employee_id: {date, …}} approved full-day leave
    holidays:    {employee_id: {date: name}}
    existing:    {(employee_id, date iso): {id, source, status, kind, start_time, end_time}} roster rows already there
                 (include the day BEFORE ``start`` so the first rest check sees it)
    Returns {"rows": [...], "deletes": [row ids], "kept": [...], "skipped": [...], "conflicts": [...]}.
    """
    leave, holidays, existing = leave or {}, holidays or {}, existing or {}
    rows, deletes, kept, skipped, conflicts = [], [], [], [], []
    for a in sorted(assignments, key=lambda x: str(x["employee_id"])):
        eid = str(a["employee_id"])
        emp = employees.get(eid) or {}
        if (emp.get("status") or "") in ("terminated", "inactive"):
            continue
        pat = patterns.get(str(a["pattern_id"]))
        if not pat:
            continue
        cycle, shifts = pat["cycle"], pat["shifts"]
        a_start, a_end = _d(a["start_date"]), (_d(a["end_date"]) if a.get("end_date") else None)
        name = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip() or eid[:8]
        # the last worked shift end before the window (existing roster) for the first rest check
        prev_end = None
        prev = existing.get((eid, (start - timedelta(days=1)).isoformat()))
        if prev and (prev.get("kind") or "work") == "work" and prev.get("start_time"):
            prev_end = window({"start": prev["start_time"], "end": prev["end_time"]}, start - timedelta(days=1))[1]
        d = start
        while d <= end:
            iso = d.isoformat()
            ex = existing.get((eid, iso))
            c = code_on(cycle, a_start, int(a.get("offset_days") or 0), d)
            in_range = c is not None and (a_end is None or d <= a_end)

            def _remember(row):
                nonlocal prev_end
                if row and (row.get("kind") or "work") == "work" and row.get("start_time"):
                    prev_end = window({"start": row["start_time"], "end": row["end_time"]}, d)[1]

            if not in_range:
                _remember(ex)
                d += timedelta(days=1)
                continue
            # 1. rows a person put there (manual / swap) or already published stay unless told otherwise
            if ex and ((ex.get("source") or "manual") != "pattern" and not overwrite_manual):
                kept.append({"employee_id": eid, "name": name, "date": iso, "reason": "kept — set by hand or swapped"})
                _remember(ex)
                d += timedelta(days=1)
                continue
            if ex and ex.get("status") == "published" and not overwrite_published:
                kept.append({"employee_id": eid, "name": name, "date": iso, "reason": "kept — already published"})
                _remember(ex)
                d += timedelta(days=1)
                continue
            base = {"employee_id": eid, "work_date": iso, "pattern_id": str(a["pattern_id"]), "assignment_id": str(a.get("id") or ""),
                    "existing_id": (ex or {}).get("id"), "name": name}
            # 2. approved leave
            if d in leave.get(eid, set()):
                skipped.append({"employee_id": eid, "name": name, "date": iso, "reason": "on approved leave"})
                if ex:
                    deletes.append(ex["id"])
                d += timedelta(days=1)
                continue
            # 3. holiday
            hol = holidays.get(eid, {}).get(d)
            if hol:
                rows.append({**base, "kind": "off", "shift_code": "HOL", "start_time": "00:00", "end_time": "00:00",
                             "planned_hours": 0, "note": f"Holiday: {hol}"[:200]})
                d += timedelta(days=1)
                continue
            if c == OFF:
                rows.append({**base, "kind": "off", "shift_code": OFF, "start_time": "00:00", "end_time": "00:00",
                             "planned_hours": 0, "note": "Weekly off (rotation)"})
                d += timedelta(days=1)
                continue
            sh = shifts[c]
            w0, w1 = window(sh, d)
            # 4. minimum rest after the previous shift
            gap = rest_between(prev_end, w0)
            if min_rest_hours and gap is not None and gap < float(min_rest_hours):
                conflicts.append({"employee_id": eid, "name": name, "date": iso, "code": "rest", "shift": c,
                                  "reason": f"only {gap:g} h of rest after the previous shift (company minimum "
                                            f"{float(min_rest_hours):g} h) — {sh['name']} not planned"})
                if ex:
                    deletes.append(ex["id"])
                d += timedelta(days=1)
                continue
            rows.append({**base, "kind": "work", "shift_code": c, "start_time": sh["start"], "end_time": sh["end"],
                         "planned_hours": shift_hours(sh), "note": f"{pat.get('name') or 'Rotation'}: {sh['name']}"[:200]})
            prev_end = w1
            d += timedelta(days=1)
    return {"rows": rows, "deletes": sorted(set(str(x) for x in deletes)), "kept": kept, "skipped": skipped, "conflicts": conflicts}


# ══════════════════════════ swaps ═════════════════════════════════════════════

def swap_problems(*, a_emp: dict, b_emp: dict, a_shift: dict, b_shift: dict, a_neighbours: list[dict], b_neighbours: list[dict],
                  min_rest_hours: float = 0) -> list[str]:
    """After the swap A works B's shift and B works A's. Each *_shift: {employee_id, work_date, start_time, end_time, kind};
    *_neighbours: that person's OTHER work shifts (dicts like above) around the dates. Returns plain problems ([] = fine)."""
    out = []

    def check(person: dict, takes: dict, gives: dict, neighbours: list[dict], who: str):
        if (takes.get("kind") or "work") != "work":
            return
        sh = {"start": takes["start_time"], "end": takes["end_time"]}
        d = _d(takes["work_date"])
        if not min_rest_hours:
            return
        w0, w1 = window(sh, d)
        for n in neighbours:
            if str(n.get("id")) in (str(gives.get("id")), str(takes.get("id"))) or (n.get("kind") or "work") != "work":
                continue
            n0, n1 = window({"start": n["start_time"], "end": n["end_time"]}, _d(n["work_date"]))
            if n1 <= w0:
                gap = (w0 - n1).total_seconds() / 3600
            elif w1 <= n0:
                gap = (n0 - w1).total_seconds() / 3600
            else:
                out.append(f"{who} would have two shifts overlapping on {d.isoformat()}")
                continue
            if gap < float(min_rest_hours):
                out.append(f"{who} would get only {gap:g} h of rest next to the {n['work_date']} shift (company minimum {float(min_rest_hours):g} h)")
    check(a_emp, b_shift, a_shift, a_neighbours, f"{a_emp.get('first_name', 'The requester')}")
    check(b_emp, a_shift, b_shift, b_neighbours, f"{b_emp.get('first_name', 'The colleague')}")
    return out
