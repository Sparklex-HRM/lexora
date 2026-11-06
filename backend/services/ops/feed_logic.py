"""Company feed — pure rules (no I/O). Employee portal batch 3, spec item 12.

Who may post / moderate / delete, which items a caller may see, auto-post generation
(birthday · work anniversary · new joiner) from the roster for a date window, opt-out
filtering and poll vote rules. Everything here is tested in test_ess_portal_b3_feed.py.

PRIVACY: a birthday item carries the day and month only ("16 October") — never the year,
never the age, never the full date of birth.
"""
from __future__ import annotations

import calendar
import re
import uuid
from datetime import date, datetime, timedelta, timezone

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}

POST_KINDS = ("post", "poll")
AUTO_KINDS = ("birthday", "anniversary", "new_joiner")
ALL_KINDS = POST_KINDS + AUTO_KINDS + ("kudos",)
# filter chips: a chip can stand for several kinds
KIND_FILTERS = {"post": {"post"}, "poll": {"poll"}, "birthday": {"birthday"}, "anniversary": {"anniversary"},
                "new_joiner": {"new_joiner"}, "kudos": {"kudos"}, "celebrations": set(AUTO_KINDS)}

REACTIONS = ("like", "celebrate", "support", "insightful")
WINDOW_DAYS = 7                       # auto posts for today and the last 7 days
MAX_BODY = 3000
MAX_COMMENT = 1000
POLL_MIN_OPTIONS, POLL_MAX_OPTIONS = 2, 6
MAX_OPTION = 120
INACTIVE_STATUSES = {"terminated", "exited", "resigned", "inactive", "separated", "relieved", "absconded"}

_ORG_SUFFIX = re.compile(r"[\s,]+(incorporated|inc\.?|limited|ltd\.?|lt[ée]e\.?|corporation|corp\.?|ulc|llp|co\.?)\s*$", re.I)


def is_hr(role: str | None) -> bool:
    return (role or "").lower() in HR_ROLES


def actor_key(employee_id: str | None, user_id: str | None) -> str | None:
    """One stable key per person for reactions / votes: the employee record, else the login."""
    if employee_id:
        return str(employee_id)
    if user_id:
        return f"u:{user_id}"
    return None


def short_org(name: str | None) -> str:
    """'Maple Logistics Inc.' → 'Maple Logistics' (what a work-anniversary line says)."""
    n = (name or "").strip()
    prev = None
    while n and n != prev:
        prev = n
        n = _ORG_SUFFIX.sub("", n).strip()
    return n or "the company"


def day_month(d: date | None) -> str | None:
    """'16 October' — the ONLY form a birthday may take in any response."""
    if not d:
        return None
    return f"{d.day} {calendar.month_name[d.month]}"


def _occurrence(month: int, day: int, year: int) -> date:
    """This year's date for a month/day; 29 Feb falls on 28 Feb in a non-leap year."""
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def window(today: date, days: int = WINDOW_DAYS) -> list[date]:
    return [today - timedelta(days=i) for i in range(days, -1, -1)]


def _active(e: dict) -> bool:
    return (str(e.get("status") or "").lower() not in INACTIVE_STATUSES) and e.get("is_active", True) is not False


def opted_out(prefs: dict | None, kind: str) -> bool:
    p = prefs or {}
    return bool(p.get(kind))


def name_of(e: dict | None) -> str:
    if not e:
        return "A colleague"
    return f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip() or "A colleague"


def auto_events(roster: list[dict], today: date, org_name: str | None, prefs: dict[str, dict] | None = None,
                days: int = WINDOW_DAYS) -> list[dict]:
    """Auto posts due in [today - days, today], one per (kind, employee, occurrence).

    roster rows: id, first_name, last_name, department, work_location, date_of_birth, hire_date, status.
    prefs: employee_id → {"birthday": bool, "anniversary": bool, "new_joiner": bool} (True = opted out).
    Returns dicts with a stable ``auto_key`` (the idempotency key), the subject, the day it is
    celebrated and the text — the text never carries a birth year or an age."""
    prefs = prefs or {}
    org = short_org(org_name)
    days_set = window(today, days)
    first = days_set[0]
    out: list[dict] = []
    for e in roster:
        if not e.get("id") or not _active(e):
            continue
        eid = str(e["id"])
        p = prefs.get(eid) or {}
        nm = name_of(e)
        base = {"subject_employee_id": eid, "subject_name": nm, "department": e.get("department"),
                "location": e.get("work_location")}
        dob = e.get("date_of_birth")
        if isinstance(dob, date) and not opted_out(p, "birthday"):
            for y in {first.year, today.year}:
                d = _occurrence(dob.month, dob.day, y)
                if first <= d <= today:
                    out.append({**base, "kind": "birthday", "auto_key": f"birthday:{eid}:{y}", "event_date": d,
                                "day_month": day_month(d), "body": f"Happy birthday, {nm}! Wishing you a wonderful year ahead."})
        hire = e.get("hire_date")
        if isinstance(hire, date) and hire <= today:
            if not opted_out(p, "anniversary"):
                for y in {first.year, today.year}:
                    d = _occurrence(hire.month, hire.day, y)
                    years = y - hire.year
                    if years >= 1 and first <= d <= today:
                        unit = "year" if years == 1 else "years"
                        out.append({**base, "kind": "anniversary", "auto_key": f"anniversary:{eid}:{y}", "event_date": d,
                                    "years": years, "day_month": day_month(d),
                                    "body": f"{nm} completes {years} {unit} at {org}. Congratulations!"})
            if first <= hire <= today and not opted_out(p, "new_joiner"):
                where = f" in {e['department']}" if e.get("department") else ""
                out.append({**base, "kind": "new_joiner", "auto_key": f"new_joiner:{eid}", "event_date": hire,
                            "day_month": day_month(hire),
                            "body": f"Please welcome {nm}, who joined us{where}. Say hello!"})
    out.sort(key=lambda x: (x["event_date"], x["kind"], x["subject_employee_id"]))
    return out


def missing_events(events: list[dict], existing_keys: set[str]) -> list[dict]:
    """Idempotency: only the events whose auto_key has never been written."""
    seen = set(existing_keys)
    out = []
    for ev in events:
        if ev["auto_key"] in seen:
            continue
        seen.add(ev["auto_key"])
        out.append(ev)
    return out


def event_created_at(d: date) -> datetime:
    """Auto posts sort on the day they celebrate: 09:00 Eastern time that day (13:00 or 14:00 UTC)."""
    from zoneinfo import ZoneInfo
    return datetime(d.year, d.month, d.day, 9, 0, tzinfo=ZoneInfo("America/Toronto")).astimezone(timezone.utc)


# ── posting / moderation / deletion ─────────────────────────────────────────

def can_post(role: str | None, employee_id: str | None, user_id: str | None) -> bool:
    return bool(employee_id or user_id) and bool(role)


def check_audience(role: str | None, me: dict | None, department: str | None, location: str | None) -> str | None:
    """Employees may narrow a post to THEIR OWN department / location; HR may target any."""
    if is_hr(role):
        return None
    me = me or {}
    if department and (department or "").strip().lower() != (me.get("department") or "").strip().lower():
        return "You can only address a post to your own department"
    if location and (location or "").strip().lower() != (me.get("work_location") or "").strip().lower():
        return "You can only address a post to your own location"
    return None


def is_owner(row_author_emp: str | None, row_author_user: str | None, employee_id: str | None, user_id: str | None) -> bool:
    if employee_id and row_author_emp and str(row_author_emp) == str(employee_id):
        return True
    return bool(user_id and row_author_user and str(row_author_user) == str(user_id))


def can_delete(kind: str, row_author_emp, row_author_user, employee_id, user_id) -> bool:
    """Only the author deletes, and only a post or poll they wrote (auto posts: opt out instead;
    HR hides with a reason rather than deleting)."""
    if kind in AUTO_KINDS or kind == "kudos":
        return False
    return is_owner(row_author_emp, row_author_user, employee_id, user_id)


def can_moderate(role: str | None) -> bool:
    return is_hr(role)


def check_hide_reason(reason: str | None) -> str | None:
    r = (reason or "").strip()
    if len(r) < 3:
        return "Give a reason (at least 3 characters) — the author sees it"
    if len(r) > 300:
        return "Reason is too long (300 characters at most)"
    return None


def visible_to(item: dict, role: str | None, me_emp: str | None, me: dict | None) -> bool:
    """item keys: hidden, deleted, audience_department, audience_location, author_employee_id."""
    if item.get("deleted"):
        return False
    hr = is_hr(role)
    if item.get("hidden") and not hr:
        return False
    if hr:
        return True
    if me_emp and item.get("author_employee_id") and str(item["author_employee_id"]) == str(me_emp):
        return True
    me = me or {}
    ad, al = item.get("audience_department"), item.get("audience_location")
    if ad and (ad or "").strip().lower() != (me.get("department") or "").strip().lower():
        return False
    if al and (al or "").strip().lower() != (me.get("work_location") or "").strip().lower():
        return False
    return True


def matches_filters(item: dict, department: str | None, location: str | None, kind: str | None) -> bool:
    """Department / location filter on the item's own department/location or its audience."""
    if kind:
        want = KIND_FILTERS.get(kind)
        if want is None or item.get("kind") not in want:
            return False
    if department:
        d = department.strip().lower()
        if d not in {(item.get("department") or "").strip().lower(), (item.get("audience_department") or "").strip().lower()}:
            return False
    if location:
        loc = location.strip().lower()
        if loc not in {(item.get("location") or "").strip().lower(), (item.get("audience_location") or "").strip().lower()}:
            return False
    return True


def suppressed_by_optout(item: dict, prefs: dict[str, dict]) -> bool:
    """An auto post disappears if its subject opted out AFTER it was written."""
    if item.get("kind") not in AUTO_KINDS:
        return False
    return opted_out(prefs.get(str(item.get("subject_employee_id") or "")), item["kind"])


def order_items(items: list[dict], first_page: bool) -> list[dict]:
    """Pinned first (first page only), then newest first."""
    def ts(i):
        return i.get("created_at") or ""
    items = sorted(items, key=ts, reverse=True)
    if first_page:
        items = [i for i in items if i.get("pinned")] + [i for i in items if not i.get("pinned")]
    return items


# ── posts & polls ────────────────────────────────────────────────────────────

def validate_post(kind: str, body: str | None, options: list[str] | None, closes_at: datetime | None,
                  now: datetime) -> tuple[str | None, list[dict]]:
    """→ (error, poll options as [{id, label}])."""
    if kind not in POST_KINDS:
        return "kind must be post or poll", []
    text = (body or "").strip()
    if not text:
        return ("Write something to post" if kind == "post" else "Ask a question for the poll"), []
    if len(text) > MAX_BODY:
        return f"Post is too long ({MAX_BODY} characters at most)", []
    if kind == "post":
        if options:
            return "Only a poll has options", []
        return None, []
    labels = [(o or "").strip() for o in (options or []) if (o or "").strip()]
    if len(labels) < POLL_MIN_OPTIONS or len(labels) > POLL_MAX_OPTIONS:
        return f"A poll needs {POLL_MIN_OPTIONS} to {POLL_MAX_OPTIONS} options", []
    if any(len(lbl) > MAX_OPTION for lbl in labels):
        return f"Each option is {MAX_OPTION} characters at most", []
    if len({lbl.lower() for lbl in labels}) != len(labels):
        return "Poll options must be different", []
    if closes_at is not None:
        ca = closes_at if closes_at.tzinfo else closes_at.replace(tzinfo=timezone.utc)
        if ca <= now:
            return "The poll must close in the future", []
    return None, [{"id": f"o{i + 1}", "label": lbl} for i, lbl in enumerate(labels)]


def poll_open(closes_at: datetime | None, now: datetime) -> bool:
    if closes_at is None:
        return True
    ca = closes_at if closes_at.tzinfo else closes_at.replace(tzinfo=timezone.utc)
    return now < ca


def check_vote(options: list[dict], option_id: str | None, closes_at: datetime | None, now: datetime,
               already_voted: bool) -> str | None:
    if not poll_open(closes_at, now):
        return "This poll has closed"
    if already_voted:
        return "You have already voted in this poll"
    if option_id not in {o.get("id") for o in (options or [])}:
        return "Pick one of the poll's options"
    return None


def poll_results(options: list[dict], votes: list[dict], me_key: str | None, closes_at: datetime | None,
                 now: datetime) -> dict:
    counts = {o["id"]: 0 for o in options or []}
    mine = None
    for v in votes:
        if v.get("option_id") in counts:
            counts[v["option_id"]] += 1
        if me_key and v.get("voter_key") == me_key:
            mine = v.get("option_id")
    total = sum(counts.values())
    return {"options": [{"id": o["id"], "label": o["label"], "count": counts[o["id"]],
                         "pct": round(100 * counts[o["id"]] / total) if total else 0} for o in options or []],
            "total": total, "my_vote": mine, "closes_at": closes_at.isoformat() if closes_at else None,
            "open": poll_open(closes_at, now)}


def reaction_summary(rows: list[dict], me_key: str | None) -> dict:
    out = {e: {"count": 0, "mine": False} for e in REACTIONS}
    for r in rows:
        e = r.get("emoji")
        if e in out:
            out[e]["count"] += 1
            if me_key and r.get("actor_key") == me_key:
                out[e]["mine"] = True
    return out


def check_comment(body: str | None) -> str | None:
    t = (body or "").strip()
    if not t:
        return "Write a comment"
    if len(t) > MAX_COMMENT:
        return f"Comment is too long ({MAX_COMMENT} characters at most)"
    return None


def parse_item_id(item_id: str) -> tuple[str, uuid.UUID] | None:
    """'kudos:<uuid>' → ("kudos", uuid); '<uuid>' → ("post", uuid); anything else → None."""
    s = (item_id or "").strip()
    kind = "post"
    if s.startswith("kudos:"):
        kind, s = "kudos", s[6:]
    try:
        return kind, uuid.UUID(s)
    except (ValueError, TypeError, AttributeError):
        return None
