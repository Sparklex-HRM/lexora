"""Performance G4 — pure rules (no DB, no HTTP), so every rule is unit-tested.

  * Objectives & key results: key-result progress per metric type, weighted roll-up to the
    objective and up the alignment tree, confidence bands, alignment without loops, who may
    see / change / check in, the snapshot handed to the appraisal review.
  * 360 feedback: questionnaire, who may nominate whom (and whose nomination needs the
    manager's yes), the ANONYMITY-SAFE aggregate (a group is shown only with >= threshold
    answers; small groups are pooled or hidden; the overall never includes a hidden answer,
    so nothing can be worked out by subtraction), who may read a report.
  * Improvement plans: status machine, outcome rules (an exit outcome never ends employment
    by itself), who may read (employee, manager, HR only) and act, the check-in schedule.
  * 1:1s: who reads what (each side's private notes stay private; HR sees dates only),
    agenda / action items, carrying open actions to the next meeting.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from hiring_logic import Actor, RuleError


def r2(v) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _num(v, what: str, required: bool = False):
    if v in (None, ""):
        if required:
            raise RuleError(f"{what} is required.", 422)
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise RuleError(f"{what} must be a number.", 422)


# ═════════════════════════ objectives & key results ═════════════════════════
LEVELS = ("company", "department", "team", "individual")
LEVEL_LABEL = {"company": "Company", "department": "Department", "team": "Team", "individual": "Individual"}
OBJ_STATUSES = ("draft", "active", "closed", "cancelled")
METRIC_TYPES = ("percent", "number", "currency", "milestone")
METRIC_LABEL = {"percent": "Percentage", "number": "Number", "currency": "Amount ($)", "milestone": "Milestones"}


def clean_key_result(body: dict) -> dict:
    """Validate one key result. Numeric kinds need a start and a target that differ
    (a target BELOW the start is fine: 'cut defects from 40 to 10'). Milestones need >= 1 step."""
    title = str(body.get("title") or "").strip()[:300]
    if not title:
        raise RuleError("Give the key result a title.", 422)
    mt = str(body.get("metric_type") or "number").strip().lower()
    if mt not in METRIC_TYPES:
        raise RuleError("The measure must be a percentage, a number, an amount or milestones.", 422)
    weight = _num(body.get("weight"), "Weight") or 1
    if weight <= 0 or weight > 100:
        raise RuleError("A key result's weight must be between 0.01 and 100.", 422)
    out = {"title": title, "metric_type": mt, "weight": weight, "unit": (str(body.get("unit") or "").strip()[:30] or None)}
    if mt == "milestone":
        steps = []
        for m in body.get("milestones") or []:
            label = str((m or {}).get("label") or "").strip()[:200]
            if label:
                steps.append({"label": label, "done": bool((m or {}).get("done"))})
        if not steps:
            raise RuleError("A milestone key result needs at least one milestone.", 422)
        out.update({"milestones": steps, "start_value": None, "target_value": None, "current_value": None})
        return out
    start = _num(body.get("start_value"), "Start value", required=True)
    target = _num(body.get("target_value"), "Target value", required=True)
    if start == target:
        raise RuleError("The target must be different from the start value.", 422)
    if mt == "percent" and not (0 <= start <= 100 and 0 <= target <= 100):
        raise RuleError("A percentage key result runs between 0 and 100.", 422)
    if mt == "currency" and (start < 0 or target < 0):
        raise RuleError("An amount cannot be negative.", 422)
    cur = _num(body.get("current_value"), "Current value")
    out.update({"start_value": start, "target_value": target, "current_value": start if cur is None else cur,
                "milestones": [], "unit": out["unit"] or ("%" if mt == "percent" else "$" if mt == "currency" else None)})
    return out


def kr_progress(kr: dict) -> float:
    """0..100. Numeric: how far from start toward target (works both directions), clamped.
    Milestones: share of steps done."""
    if kr.get("metric_type") == "milestone":
        steps = kr.get("milestones") or []
        return r2(100 * sum(1 for s in steps if s.get("done")) / len(steps)) if steps else 0.0
    s, t, c = kr.get("start_value"), kr.get("target_value"), kr.get("current_value")
    if s is None or t is None or c is None or float(t) == float(s):
        return 0.0
    pct = (float(c) - float(s)) / (float(t) - float(s)) * 100
    return r2(min(100.0, max(0.0, pct)))


def weighted(rows: list[tuple[float, float]]) -> float:
    """[(progress, weight)] → weighted average, 0 when nothing carries weight."""
    tw = sum(float(w or 0) for _, w in rows)
    return r2(sum(float(p or 0) * float(w or 0) for p, w in rows) / tw) if tw > 0 else 0.0


def rollup(objectives: list[dict], krs_by_obj: dict[str, list[dict]]) -> dict[str, float]:
    """Progress of every objective. An objective with key results = weighted KR progress.
    One without = weighted progress of the objectives aligned under it (child weight).
    Cancelled children do not count. Computed bottom-up; a loop (should never exist) stops at 0."""
    by_id = {str(o["id"]): o for o in objectives}
    kids: dict[str, list[dict]] = {}
    for o in objectives:
        if o.get("parent_id") and str(o["parent_id"]) in by_id and o.get("status") != "cancelled":
            kids.setdefault(str(o["parent_id"]), []).append(o)
    memo: dict[str, float] = {}

    def calc(oid: str, seen: frozenset) -> float:
        if oid in memo:
            return memo[oid]
        if oid in seen:
            return 0.0
        krs = krs_by_obj.get(oid) or []
        if krs:
            v = weighted([(kr_progress(k), k.get("weight") or 1) for k in krs])
        else:
            ch = kids.get(oid) or []
            v = weighted([(calc(str(c["id"]), seen | {oid}), c.get("weight") or 1) for c in ch]) if ch else 0.0
        memo[oid] = v
        return v

    return {oid: calc(oid, frozenset()) for oid in by_id}


def confidence_band(c) -> str | None:
    """1..10 → on_track (7-10) · at_risk (4-6) · off_track (1-3)."""
    if c in (None, ""):
        return None
    c = int(c)
    return "on_track" if c >= 7 else "at_risk" if c >= 4 else "off_track"


def clean_confidence(c) -> int | None:
    if c in (None, ""):
        return None
    try:
        v = int(c)
    except (TypeError, ValueError):
        raise RuleError("Confidence must be a whole number from 1 to 10.", 422)
    if not 1 <= v <= 10:
        raise RuleError("Confidence must be a whole number from 1 to 10.", 422)
    return v


def parent_ok(obj_id: str | None, parent_id: str | None, objectives: list[dict], cycle_id: str) -> None:
    """Alignment rules: the parent exists, is in the same cycle, is not cancelled, and
    aligning does not create a loop (the parent is not this objective or one of its descendants)."""
    if not parent_id:
        return
    by_id = {str(o["id"]): o for o in objectives}
    p = by_id.get(str(parent_id))
    if not p:
        raise RuleError("The objective you align to was not found.", 422)
    if str(p.get("cycle_id")) != str(cycle_id):
        raise RuleError("You can align only to an objective in the same cycle.", 422)
    if p.get("status") == "cancelled":
        raise RuleError("You cannot align to a cancelled objective.", 422)
    if obj_id:
        cur, seen = str(parent_id), set()
        while cur and cur not in seen:
            if cur == str(obj_id):
                raise RuleError("That alignment would make a loop (an objective under itself).", 422)
            seen.add(cur)
            nxt = by_id.get(cur, {}).get("parent_id")
            cur = str(nxt) if nxt else None


def build_tree(objectives: list[dict]) -> list[dict]:
    """Nested [{...objective, children: [...]}]; an objective whose parent is not in the
    list (not visible to this reader, or none) is a root."""
    nodes = {str(o["id"]): {**o, "children": []} for o in objectives}
    roots = []
    order = {"company": 0, "department": 1, "team": 2, "individual": 3}
    for o in sorted(nodes.values(), key=lambda x: (order.get(x.get("level"), 9), x.get("title") or "")):
        pid = str(o.get("parent_id") or "")
        if pid and pid in nodes:
            nodes[pid]["children"].append(o)
        else:
            roots.append(o)
    return roots


def okr_relation(actor: Actor, obj: dict, owner_manager_id: str | None) -> str | None:
    """self | manager | hr | None for one objective."""
    owner = str(obj.get("owner_employee_id") or "")
    if actor.employee_id and owner and owner == actor.employee_id:
        return "self"
    if actor.employee_id and owner_manager_id and str(owner_manager_id) == actor.employee_id:
        return "manager"
    if actor.is_hr:
        return "hr"
    return None


def okr_can(action: str, actor: Actor, obj: dict, owner_manager_id: str | None) -> bool:
    """view: company / department / team objectives are open to everyone in the org
    (people align to them); individual ones to the owner, their manager and HR.
    edit (title, key results, alignment, weight): company / department = HR head;
    team = HR or the owner; individual = owner, their manager or HR.
    checkin: whoever may edit. close / cancel: as edit."""
    level = obj.get("level") or "individual"
    rel = okr_relation(actor, obj, owner_manager_id)
    if action == "view":
        return level != "individual" or rel is not None
    if level in ("company", "department"):
        return actor.is_hr_head or (actor.is_hr and action == "checkin")
    if level == "team":
        return rel in ("self", "hr", "manager")
    return rel in ("self", "manager", "hr")


def review_snapshot(objectives: list[dict], progress: dict[str, float]) -> list[dict]:
    """The goal rows an appraisal review rates for one person: their own active / closed
    individual objectives marked 'include in review', with rolled-up progress.
    Ids are prefixed 'okr:' so the review never confuses them with plain goals."""
    out = []
    for o in objectives:
        if o.get("level") != "individual" or not o.get("include_in_review") or o.get("status") not in ("active", "closed"):
            continue
        out.append({"id": f"okr:{o['id']}", "title": f"Objective: {o.get('title')}", "measure": "Key results (rolled up)",
                    "target": "100% of key results", "weight": float(o.get("weight") or 0),
                    "progress_pct": float(progress.get(str(o["id"]), 0.0)), "source": "okr"})
    return out


# ═════════════════════════ 360 feedback ═════════════════════════════════════
RELATIONSHIPS = ("self", "manager", "peer", "direct_report", "other")
REL_LABEL = {"self": "You (self)", "manager": "Manager", "peer": "Peers", "direct_report": "Direct reports (upward)",
             "other": "Others", "pooled": "Peers, reports and others (combined)"}
ANON_GROUPS = ("peer", "direct_report", "other")
MIN_THRESHOLD = 3
ROUND_STATUSES = ("draft", "nominating", "collecting", "closed", "released")
ROUND_NEXT = {"open_nominations": ("draft", "nominating"), "start_collecting": ("nominating", "collecting"),
              "close": ("collecting", "closed"), "release": ("closed", "released")}

# Our own questionnaire. 1..5 scale: 1 = rarely, 5 = consistently.
DEFAULT_QUESTIONS = [
    {"key": "q1", "competency": "Delivery", "kind": "rating", "text": "Delivers what was agreed, on time and to the expected quality."},
    {"key": "q2", "competency": "Collaboration", "kind": "rating", "text": "Works well with others and shares information openly."},
    {"key": "q3", "competency": "Communication", "kind": "rating", "text": "Explains things clearly and listens before responding."},
    {"key": "q4", "competency": "Ownership", "kind": "rating", "text": "Takes responsibility for problems and follows through."},
    {"key": "q5", "competency": "Growth", "kind": "rating", "text": "Learns from feedback and helps others improve."},
    {"key": "q6", "competency": "Strengths", "kind": "text", "text": "What should this person keep doing?"},
    {"key": "q7", "competency": "Development", "kind": "text", "text": "What one thing would make this person more effective?"},
]
UPWARD_QUESTIONS = [
    {"key": "u1", "competency": "People leadership", "kind": "rating", "text": "Gives me clear direction and priorities.", "upward": True},
    {"key": "u2", "competency": "People leadership", "kind": "rating", "text": "Gives me regular, useful feedback on my work.", "upward": True},
    {"key": "u3", "competency": "People leadership", "kind": "rating", "text": "Supports my growth and removes blockers.", "upward": True},
]


def clean_questions(qs) -> list[dict]:
    if not isinstance(qs, list) or not qs:
        raise RuleError("Add at least one question.", 422)
    out, keys = [], set()
    for i, q in enumerate(qs):
        text = str((q or {}).get("text") or "").strip()[:400]
        if not text:
            raise RuleError("Every question needs its wording.", 422)
        kind = (q or {}).get("kind") or "rating"
        if kind not in ("rating", "text"):
            raise RuleError("A question is either a 1-5 rating or a written answer.", 422)
        key = str((q or {}).get("key") or f"q{i + 1}")[:20]
        if key in keys:
            key = f"q{i + 1}_{len(keys)}"
        keys.add(key)
        out.append({"key": key, "competency": str((q or {}).get("competency") or "General").strip()[:80] or "General",
                    "kind": kind, "text": text, "upward": bool((q or {}).get("upward"))})
    if not any(q["kind"] == "rating" for q in out):
        raise RuleError("Add at least one rating question.", 422)
    return out


def questions_for(questions: list[dict], relationship: str) -> list[dict]:
    """Upward questions go only to direct reports; everyone else gets the rest."""
    if relationship == "direct_report":
        return [q for q in questions if q.get("upward")] or [q for q in questions if not q.get("upward")]
    return [q for q in questions if not q.get("upward")]


def nomination_rule(actor_rel: str, relationship: str, nominate_by: str) -> str:
    """Status a new nomination starts in, or raises. actor_rel = how the nominator relates to
    the subject (self | manager | hr). Employees nominate peers / others, which wait for the
    manager's approval; managers and HR add anyone straight away. Nobody nominates 'self' or
    'manager' by hand (those are added automatically)."""
    if relationship not in ("peer", "direct_report", "other"):
        raise RuleError("Choose peer, direct report or other. Self and manager are added automatically.", 422)
    if actor_rel == "self":
        if nominate_by != "employee":
            raise RuleError("In this round the manager or HR picks your reviewers.", 403)
        if relationship == "direct_report":
            raise RuleError("Your direct reports are added by your manager or HR.", 403)
        return "proposed"
    if actor_rel == "manager":
        if nominate_by == "hr":
            raise RuleError("In this round HR picks the reviewers.", 403)
        return "approved"
    if actor_rel == "hr":
        return "approved"
    raise RuleError("You cannot pick reviewers for this person.", 403)


def check_answers(questions: list[dict], answers: dict) -> dict:
    """Every rating question answered 1..5; written answers optional (trimmed, capped)."""
    out = {}
    for q in questions:
        v = (answers or {}).get(q["key"])
        if q["kind"] == "rating":
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise RuleError(f"Rate: {q['text']}", 422)
            if not 1 <= n <= 5:
                raise RuleError("Ratings run from 1 to 5.", 422)
            out[q["key"]] = n
        else:
            t = str(v or "").strip()[:2000]
            if t:
                out[q["key"]] = t
    return out


def _avg(vals: list) -> float | None:
    vals = [float(v) for v in vals if v is not None]
    return r2(sum(vals) / len(vals)) if vals else None


def aggregate_360(questions: list[dict], responses: list[dict], min_responses: int = MIN_THRESHOLD) -> dict:
    """The report. responses = [{relationship, answers}] — SUBMITTED only, no names.

    * self: shown (it is the person's own answer).
    * manager: shown as its own group and labelled as not anonymous.
    * peer / direct_report / other: each shown only with >= threshold answers. The groups below
      the threshold are pooled; the pool is shown only if IT reaches the threshold, else hidden.
    * overall ('everyone except you') averages ONLY the answers that are shown somewhere, so no
      hidden answer can be worked out by subtracting one figure from another.
    * written answers: only from shown groups, sorted (no order that could match a person).
    """
    k = max(MIN_THRESHOLD, int(min_responses or MIN_THRESHOLD))
    by_rel: dict[str, list[dict]] = {}
    for r in responses:
        by_rel.setdefault(r.get("relationship") or "other", []).append(r.get("answers") or {})
    groups: list[tuple[str, list[dict], bool]] = []          # (key, answers, anonymous)
    hidden_count = 0
    if by_rel.get("self"):
        groups.append(("self", by_rel["self"], False))
    if by_rel.get("manager"):
        groups.append(("manager", by_rel["manager"], False))
    small = []
    for g in ANON_GROUPS:
        rows = by_rel.get(g) or []
        if not rows:
            continue
        if len(rows) >= k:
            groups.append((g, rows, True))
        else:
            small.append(rows)
    pooled = [a for rows in small for a in rows]
    if pooled:
        if len(pooled) >= k:
            groups.append(("pooled", pooled, True))
        else:
            hidden_count = len(pooled)
    rating_qs = [q for q in questions if q["kind"] == "rating"]
    text_qs = [q for q in questions if q["kind"] == "text"]
    out_groups = []
    for key, rows, anon in groups:
        out_groups.append({"group": key, "label": REL_LABEL[key], "responses": len(rows), "anonymous": anon,
                           "scores": {q["key"]: _avg([a.get(q["key"]) for a in rows if isinstance(a.get(q["key"]), (int, float))])
                                      for q in rating_qs}})
    others = [a for key, rows, _ in groups if key != "self" for a in rows]
    anon_shown = sum(len(rows) for key, rows, anon in groups if anon)
    overall = None
    if others and anon_shown >= k:
        overall = {q["key"]: _avg([a.get(q["key"]) for a in others if isinstance(a.get(q["key"]), (int, float))]) for q in rating_qs}
    comps: dict[str, list[str]] = {}
    for q in rating_qs:
        comps.setdefault(q["competency"], []).append(q["key"])
    competency = []
    for comp, keys in comps.items():
        row = {"competency": comp}
        for g in out_groups:
            vals = [g["scores"].get(kk) for kk in keys if g["scores"].get(kk) is not None]
            row[g["group"]] = _avg(vals)
        row["overall"] = _avg([overall.get(kk) for kk in keys]) if overall else None
        competency.append(row)
    comments = []
    for q in text_qs:
        items = []
        for key, rows, _ in groups:
            if key == "self":
                continue
            items += [{"group": REL_LABEL[key], "text": a[q["key"]]} for a in rows if isinstance(a.get(q["key"]), str) and a[q["key"]].strip()]
        comments.append({"key": q["key"], "question": q["text"], "answers": sorted(items, key=lambda x: (x["group"], x["text"].lower()))})
    return {"threshold": k, "groups": out_groups, "overall": overall, "competency": competency, "comments": comments,
            "hidden_responses": hidden_count,
            "hidden_note": (f"{hidden_count} answer(s) are not shown: fewer than {k} people in those groups answered, "
                            f"so showing them could identify someone.") if hidden_count else None,
            "total_responses": len(responses)}


def report_scope(actor: Actor, subject_employee_id: str, subject_manager_id: str | None, round_status: str) -> str | None:
    """self (only once HR releases the round) | manager | hr | None."""
    if actor.employee_id and str(subject_employee_id) == actor.employee_id:
        return "self" if round_status == "released" else None
    if actor.employee_id and subject_manager_id and str(subject_manager_id) == actor.employee_id:
        return "manager" if round_status in ("closed", "released") else None
    if actor.is_hr:
        return "hr"
    return None


# ═════════════════════════ improvement plans ════════════════════════════════
PIP_STATUSES = ("draft", "active", "extended", "closed", "cancelled")
PIP_OUTCOMES = ("successful", "extended", "exit")
OUTCOME_LABEL = {"successful": "Successful: plan closed", "extended": "Extended", "exit": "Not successful: refer to exit process"}
CHECKIN_RATINGS = ("on_track", "some_progress", "off_track")
PIP_MIN_DAYS, PIP_MAX_DAYS = 14, 180


def pip_scope(actor: Actor, pip: dict) -> str | None:
    """Confidential plan: the employee (not while it is a draft), the employee's manager on the
    plan, and HR. No one else — not another manager, not the skip-level, not payroll."""
    if actor.employee_id and str(pip.get("employee_id")) == actor.employee_id:
        return "self" if pip.get("status") != "draft" else None
    if actor.employee_id and pip.get("manager_id") and str(pip["manager_id"]) == actor.employee_id:
        return "manager"
    if actor.is_hr:
        return "hr"
    return None


def pip_can(action: str, scope: str | None, pip: dict) -> bool:
    st = pip.get("status")
    if scope is None:
        return False
    if action == "edit":                       # wording, dates, goal / support lines
        return scope in ("manager", "hr") and st in ("draft", "active", "extended")
    if action == "start":
        return scope == "hr" and st == "draft"
    if action == "checkin":
        return scope in ("manager", "hr") and st in ("active", "extended")
    if action in ("extend", "close"):
        return scope == "hr" and st in ("active", "extended")
    if action == "cancel":
        return scope == "hr" and st in ("draft", "active", "extended")
    if action == "ack":
        return scope == "self" and st in ("active", "extended") and not pip.get("ack_status")
    if action == "link_exit":
        return scope == "hr" and st == "closed" and pip.get("outcome") == "exit"
    return False


def pip_dates(start: date | None, end: date | None) -> None:
    if not start or not end:
        raise RuleError("A plan needs a start date and an end date.", 422)
    days = (end - start).days
    if days < PIP_MIN_DAYS:
        raise RuleError(f"A plan must run for at least {PIP_MIN_DAYS} days so the person has a fair chance.", 422)
    if days > PIP_MAX_DAYS:
        raise RuleError(f"A plan cannot run for more than {PIP_MAX_DAYS} days. Extend it if more time is needed.", 422)


def checkin_schedule(start: date, end: date, every_days: int) -> list[date]:
    """Check-in dates from start + every_days up to (and always including) the end date."""
    every = max(7, min(int(every_days or 14), 60))
    out, d = [], start + timedelta(days=every)
    while d < end:
        out.append(d)
        d += timedelta(days=every)
    out.append(end)
    return out


def decide_outcome(pip: dict, outcome: str, note: str | None, new_end: date | None, goals: list[dict]) -> dict:
    """What the outcome changes. extended → status extended with the new end date;
    successful / exit → closed. Exit is a RECOMMENDATION recorded on the plan: HR starts
    the exit case by hand (we never end employment automatically)."""
    if outcome not in PIP_OUTCOMES:
        raise RuleError("The outcome is successful, extended or exit.", 422)
    if not (note or "").strip():
        raise RuleError("Write down the reason for the outcome; it goes on the record.", 422)
    if outcome == "extended":
        old = pip.get("end_date")
        if not new_end:
            raise RuleError("Pick the new end date.", 422)
        if old and new_end <= old:
            raise RuleError("The new end date must be after the current end date.", 422)
        if pip.get("start_date") and (new_end - pip["start_date"]).days > PIP_MAX_DAYS + 90:
            raise RuleError("A plan cannot be extended past 270 days in total.", 422)
        return {"status": "extended", "end_date": new_end}
    open_goals = [g for g in goals if g.get("kind") == "goal" and g.get("status") == "open"]
    if open_goals:
        raise RuleError("Mark every plan goal as met or not met before closing the plan.", 409)
    return {"status": "closed", "outcome": outcome}


def next_pip_number(existing: list[str], year: int) -> str:
    n = 0
    pre = f"PIP-{year}-"
    for e in existing:
        if e and e.startswith(pre):
            try:
                n = max(n, int(e[len(pre):]))
            except ValueError:
                pass
    return f"{pre}{n + 1:04d}"


# ═════════════════════════ 1:1 conversations ═════════════════════════════════
def one_scope(actor: Actor, meeting: dict) -> str | None:
    if actor.employee_id and str(meeting.get("manager_id")) == actor.employee_id:
        return "manager"
    if actor.employee_id and str(meeting.get("employee_id")) == actor.employee_id:
        return "employee"
    if actor.is_hr:
        return "hr"
    return None


def redact_one(meeting: dict, scope: str) -> dict:
    m = dict(meeting)
    if scope == "manager":
        m.pop("employee_private", None)
        m["my_private"] = m.pop("manager_private", None)
    elif scope == "employee":
        m.pop("manager_private", None)
        m["my_private"] = m.pop("employee_private", None)
    else:                                   # HR: that it happened, never what was said
        for k in ("manager_private", "employee_private", "shared_notes"):
            m.pop(k, None)
        m["agenda"] = [{"id": a.get("id")} for a in m.get("agenda") or []]
        acts = m.get("action_items") or []
        m["action_items"] = []
        m["counts"] = {"agenda": len(m["agenda"]), "actions": len(acts), "actions_open": sum(1 for a in acts if not a.get("done"))}
        m["agenda"] = []
    m["scope"] = scope
    return m


def clean_agenda(items, by: str, existing: list[dict] | None = None) -> list[dict]:
    """Merge an edited agenda. Each side may add items and tick 'discussed'; an item keeps who added it."""
    old = {a.get("id"): a for a in existing or []}
    out = []
    for it in items or []:
        text = str((it or {}).get("text") or "").strip()[:400]
        if not text:
            continue
        iid = (it or {}).get("id") or uuid.uuid4().hex[:12]
        prev = old.get(iid)
        out.append({"id": iid, "text": text, "by": prev.get("by") if prev else by, "discussed": bool((it or {}).get("discussed"))})
    return out[:50]


def clean_actions(items, existing: list[dict] | None = None) -> list[dict]:
    old = {a.get("id"): a for a in existing or []}
    out = []
    for it in items or []:
        text = str((it or {}).get("text") or "").strip()[:400]
        if not text:
            continue
        owner = (it or {}).get("owner") or "employee"
        if owner not in ("manager", "employee"):
            raise RuleError("An action belongs to the manager or the employee.", 422)
        due = (it or {}).get("due") or None
        if due:
            try:
                due = date.fromisoformat(str(due)[:10]).isoformat()
            except ValueError:
                raise RuleError("Dates must look like 2026-10-31.", 422)
        iid = (it or {}).get("id") or uuid.uuid4().hex[:12]
        prev = old.get(iid) or {}
        out.append({"id": iid, "text": text, "owner": owner, "due": due, "done": bool((it or {}).get("done")),
                    "carried_from": prev.get("carried_from") or (it or {}).get("carried_from")})
    return out[:50]


def carry_over(actions: list[dict], from_meeting_id: str) -> list[dict]:
    """Open actions move to the next meeting (same id, marked where they came from)."""
    return [{**a, "carried_from": a.get("carried_from") or from_meeting_id} for a in actions or [] if not a.get("done")]
