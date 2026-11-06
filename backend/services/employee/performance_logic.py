"""Performance — pure rules (no DB, no HTTP), so every rule is unit-tested.

Goal weights, who may act on a goal, the appraisal stage machine, the score
arithmetic (returned line by line so the screen can show it), who may read a
review and what they see, calibration + its audit rows, the rating-band
distribution, the rating -> increment matrix and the payload handed to payroll.

Nothing here is a statutory amount: the only money maths is
`current annual pay x proposed % / 100`, used to compare against the budget HR typed.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from hiring_logic import Actor, RuleError

# ── Settings ────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    # The org's own rating scale: label + numeric value. Highest value = best.
    "rating_scale": [
        {"label": "Outstanding", "value": 5}, {"label": "Exceeds expectations", "value": 4},
        {"label": "Meets expectations", "value": 3}, {"label": "Needs improvement", "value": 2},
        {"label": "Unsatisfactory", "value": 1},
    ],
    "competencies": ["Quality of work", "Ownership", "Teamwork", "Communication"],
    # overall = (goal score x goal_weight + competency score x competency_weight) / (sum of the two)
    "goal_weight": 60,
    "competency_weight": 40,
    # Optional guideline distribution: [{label, pct}] — empty = no guideline.
    "guideline": [],
    # Rating band -> increment % range, optionally per grade: [{band, grade, min_pct, max_pct, default_pct}]
    "increment_matrix": [],
}


def r2(v) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def merge_settings(saved: dict | None) -> dict:
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
    for k, v in (saved or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def clean_scale(scale) -> list[dict]:
    if not isinstance(scale, list) or len(scale) < 2:
        raise RuleError("The rating scale needs at least two levels.", 422)
    out, seen_v, seen_l = [], set(), set()
    for row in scale:
        label = str((row or {}).get("label") or "").strip()
        try:
            value = float((row or {}).get("value"))
        except (TypeError, ValueError):
            raise RuleError("Every rating level needs a number.", 422)
        if not label:
            raise RuleError("Every rating level needs a label.", 422)
        if value in seen_v or label.lower() in seen_l:
            raise RuleError("Rating labels and numbers must be different from each other.", 422)
        seen_v.add(value)
        seen_l.add(label.lower())
        out.append({"label": label[:80], "value": value})
    return sorted(out, key=lambda r: -r["value"])


def clean_settings(body: dict, current: dict) -> dict:
    out = {}
    scale = current["rating_scale"]
    if "rating_scale" in body:
        scale = out["rating_scale"] = clean_scale(body["rating_scale"])
    if "competencies" in body:
        comps = [str(c).strip()[:120] for c in (body["competencies"] or []) if str(c).strip()]
        if len(set(c.lower() for c in comps)) != len(comps):
            raise RuleError("Each competency must be listed once.", 422)
        out["competencies"] = comps
    for k in ("goal_weight", "competency_weight"):
        if k in body:
            try:
                v = float(body[k])
            except (TypeError, ValueError):
                raise RuleError("Weights must be numbers.", 422)
            if v < 0 or v > 100:
                raise RuleError("Weights must be between 0 and 100.", 422)
            out[k] = v
    gw = out.get("goal_weight", current["goal_weight"])
    cw = out.get("competency_weight", current["competency_weight"])
    if float(gw) + float(cw) <= 0:
        raise RuleError("The goal weight and the competency weight cannot both be zero.", 422)
    labels = {s["label"] for s in scale}
    if "guideline" in body:
        g = []
        for row in body["guideline"] or []:
            if row.get("label") not in labels:
                raise RuleError(f"'{row.get('label')}' is not on the rating scale.", 422)
            g.append({"label": row["label"], "pct": float(row.get("pct") or 0)})
        if g and abs(sum(x["pct"] for x in g) - 100) > 0.01:
            raise RuleError(f"The guideline distribution must add up to 100%. It adds up to {r2(sum(x['pct'] for x in g))}%.", 422)
        out["guideline"] = g
    if "increment_matrix" in body:
        out["increment_matrix"] = clean_matrix(body["increment_matrix"], labels)
    return out


# ── Goals ───────────────────────────────────────────────────────────────────
GOAL_STATUSES = ("draft", "proposed", "approved", "returned")
GOAL_TRANSITIONS = {
    "submit": (("draft", "returned"), "proposed"),
    "approve": (("proposed",), "approved"),
    "return": (("proposed", "approved"), "returned"),
}


def goal_next(status: str, action: str) -> str:
    allowed, to = GOAL_TRANSITIONS[action]
    if status not in allowed:
        raise RuleError(f"A goal that is {status} cannot be {'sent for approval' if action == 'submit' else action + 'd'}.")
    return to


def weights_check(weights: list) -> tuple[bool, float]:
    """A person's goal weights must add up to exactly 100."""
    total = r2(sum(float(w or 0) for w in weights))
    return abs(total - 100.0) < 0.005 and all(float(w or 0) > 0 for w in weights), total


def relation(actor: Actor, employee_id: str, manager_id: str | None) -> str | None:
    """How the caller relates to an employee: self | manager | hr | None."""
    if actor.employee_id and str(employee_id) == actor.employee_id:
        return "self"
    if actor.employee_id and manager_id and str(manager_id) == actor.employee_id:
        return "manager"
    if actor.is_hr:
        return "hr"
    return None


def goal_can(action: str, actor: Actor, employee_id: str, manager_id: str | None) -> bool:
    rel = relation(actor, employee_id, manager_id)
    if rel is None:
        return False
    if action in ("view", "edit", "submit", "checkin"):
        return True
    if action in ("approve", "return"):
        # The manager approves. HR may step in. Nobody approves their own goals —
        # except an HR head who has no manager on record.
        if rel == "self":
            return actor.is_hr_head and not manager_id
        return True
    return False


def clean_progress(v) -> float:
    try:
        p = float(v)
    except (TypeError, ValueError):
        raise RuleError("Progress must be a number from 0 to 100.", 422)
    if p < 0 or p > 100:
        raise RuleError("Progress must be a number from 0 to 100.", 422)
    return r2(p)


# ── Appraisal stage machine ─────────────────────────────────────────────────
STAGES = ("self", "manager", "reviewer", "calibration", "published")
STAGE_LABEL = {"self": "Self review", "manager": "Manager review", "reviewer": "Reviewer",
               "calibration": "HR calibration", "published": "Published"}
STAGE_FIELD = {"self": "self_review", "manager": "manager_review", "reviewer": "reviewer_review"}


def next_stage(stage: str, use_reviewer: bool, has_reviewer: bool) -> str:
    if stage == "self":
        return "manager"
    if stage == "manager":
        return "reviewer" if (use_reviewer and has_reviewer) else "calibration"
    if stage == "reviewer":
        return "calibration"
    if stage == "calibration":
        return "published"
    raise RuleError("This review is already published.")


def can_submit(stage: str, actor: Actor, review: dict) -> bool:
    """Only the person whose turn it is. HR does not write someone else's review."""
    who = {"self": review.get("employee_id"), "manager": review.get("manager_id"),
           "reviewer": review.get("reviewer_id")}.get(stage)
    return bool(actor.employee_id) and bool(who) and str(who) == actor.employee_id


def hr_move_ok(frm: str, to: str, reason: str | None) -> None:
    """HR reopening a stage (backwards) or skipping one (forwards). Always with a reason.
    Publishing is its own action, never a side effect of a move."""
    if to not in STAGES[:-1]:
        raise RuleError("Pick a stage: Self review, Manager review, Reviewer or HR calibration.", 422)
    if frm == to:
        raise RuleError(f"The review is already at {STAGE_LABEL[to]}.")
    if not (reason or "").strip():
        raise RuleError("Give a reason. It is kept in the review's history.", 422)


def view_scope(actor: Actor, review: dict) -> str | None:
    """self | manager | reviewer | hr | None. Your own review is always the
    employee view, whatever your role."""
    if actor.employee_id and str(review.get("employee_id")) == actor.employee_id:
        return "self"
    if actor.employee_id and str(review.get("manager_id") or "") == actor.employee_id:
        return "manager"
    if actor.employee_id and str(review.get("reviewer_id") or "") == actor.employee_id:
        return "reviewer"
    if actor.is_hr:
        return "hr"
    return None


def redact(review: dict, scope: str) -> dict:
    """What each kind of reader gets. Confidential by default: fields are removed,
    not just hidden on screen."""
    out = dict(review)
    published = review.get("stage") == "published"
    order = STAGES.index(review.get("stage") or "self")
    if scope == "self":
        for k in ("reviewer_review", "calibration_note", "promotion_recommended", "promotion_note",
                  "reviewer_submitted_at"):
            out.pop(k, None)
        if not published:
            for k in ("manager_review", "scores", "overall_score", "final_score", "final_band", "manager_submitted_at"):
                out.pop(k, None)
        else:
            m = dict(review.get("manager_review") or {})
            for k in HR_ONLY_MANAGER_KEYS:
                m.pop(k, None)
            out["manager_review"] = m
    elif scope in ("manager", "reviewer"):
        if order == 0:                                  # the employee's draft is theirs until they submit
            out.pop("self_review", None)
        out.pop("calibration_note", None)
        if not published:
            out.pop("final_score", None)
            out.pop("final_band", None)
        if scope == "manager" and order < STAGES.index("calibration"):
            out.pop("reviewer_review", None)            # the skip-level's notes come after it is submitted
    out["viewer"] = scope
    return out


def validate_stage_payload(stage: str, payload: dict, goals: list[dict], template: dict) -> dict:
    """Check a submitted self / manager / reviewer form against the cycle template."""
    values = {float(s["value"]) for s in template.get("scale") or []}
    comments = str(payload.get("comments") or "").strip()
    if stage == "reviewer":
        if not comments:
            raise RuleError("Write your comments before submitting.", 422)
        out = {"comments": comments[:4000], "agree": bool(payload.get("agree", True))}
        if payload.get("suggested_score") not in (None, ""):
            try:
                s = float(payload["suggested_score"])
            except (TypeError, ValueError):
                raise RuleError("The suggested score must be a number.", 422)
            if not min(values) <= s <= max(values):
                raise RuleError(f"The suggested score must be between {min(values):g} and {max(values):g}.", 422)
            out["suggested_score"] = r2(s)
        return out
    gr, cr = {}, {}
    for g in goals:
        v = (payload.get("goal_ratings") or {}).get(str(g["id"]))
        if v in (None, ""):
            raise RuleError(f"Rate every goal. Missing: {g.get('title')}.", 422)
        if float(v) not in values:
            raise RuleError("Pick each rating from the scale.", 422)
        gr[str(g["id"])] = float(v)
    for c in template.get("competencies") or []:
        v = (payload.get("competency_ratings") or {}).get(c)
        if v in (None, ""):
            raise RuleError(f"Rate every competency. Missing: {c}.", 422)
        if float(v) not in values:
            raise RuleError("Pick each rating from the scale.", 422)
        cr[c] = float(v)
    if stage == "manager" and not comments:
        raise RuleError("Write your overall comments. The employee sees them when the review is published.", 422)
    out = {"goal_ratings": gr, "competency_ratings": cr, "comments": comments[:4000]}
    if stage == "manager":
        out["promotion_recommended"] = bool(payload.get("promotion_recommended"))
        out["promotion_note"] = str(payload.get("promotion_note") or "").strip()[:400] or None
        if out["promotion_recommended"] and not out["promotion_note"]:
            raise RuleError("Say briefly why you recommend a promotion.", 422)
        # Forms A (2026-10-24): written summary + HR-only recommendations, kept in the same JSON.
        for k in ("strengths_note", "improvement_note"):
            out[k] = str(payload.get(k) or "").strip()[:2000] or None
        out["pip_recommended"] = bool(payload.get("pip_recommended"))
        out["pip_note"] = str(payload.get("pip_note") or "").strip()[:400] or None
        if out["pip_recommended"] and not out["pip_note"]:
            raise RuleError("Say briefly why you recommend an improvement plan.", 422)
        hike = payload.get("recommended_hike_pct")
        if hike not in (None, ""):
            try:
                hike = float(hike)
            except (TypeError, ValueError):
                raise RuleError("The suggested increase must be a number.", 422)
            if not 0 <= hike <= 100:
                raise RuleError("The suggested increase must be between 0 and 100%.", 422)
            out["recommended_hike_pct"] = r2(hike)
        else:
            out["recommended_hike_pct"] = None
    return out


# Manager-review keys the employee never sees (HR / manager inputs only).
HR_ONLY_MANAGER_KEYS = ("promotion_recommended", "promotion_note", "pip_recommended", "pip_note", "recommended_hike_pct")


def calibrate_promotion(review: dict, flag, reason: str | None) -> tuple[dict, list[dict]]:
    """HR sets or clears the promotion recommendation at calibration. A change needs a reason."""
    if review.get("stage") != "calibration":
        raise RuleError("The promotion flag can be changed only while the review is at HR calibration.")
    new, old = bool(flag), bool(review.get("promotion_recommended"))
    if new == old:
        return {}, []
    why = (reason or "").strip()
    if not why:
        raise RuleError("Give a reason for changing the promotion recommendation. It is kept in the audit trail.", 422)
    values = {"promotion_recommended": new}
    if new and not review.get("promotion_note"):
        values["promotion_note"] = why[:400]
    return values, [{"action": "calibrate", "field": "promotion_recommended", "old_value": "yes" if old else "no",
                     "new_value": "yes" if new else "no", "reason": why}]


# ── Score arithmetic ────────────────────────────────────────────────────────
def goal_score(goals: list[dict], ratings: dict) -> dict:
    """Weighted goal score = sum(weight x rating) / sum(weight)."""
    lines, wsum, psum = [], 0.0, 0.0
    for g in goals:
        r = ratings.get(str(g["id"]))
        if r is None:
            continue
        w = float(g.get("weight") or 0)
        lines.append({"title": g.get("title"), "weight": w, "rating": float(r), "points": r2(w * float(r))})
        wsum += w
        psum += w * float(r)
    if not wsum:
        return {"score": None, "lines": [], "weight_total": 0, "text": "No approved goals — the goal section is left out."}
    return {"score": r2(psum / wsum), "lines": lines, "weight_total": r2(wsum), "points_total": r2(psum),
            "text": f"{r2(psum):g} points / {r2(wsum):g} weight = {r2(psum / wsum):g}"}


def competency_score(competencies: list[str], ratings: dict) -> dict:
    """Competency score = plain average of the competency ratings."""
    lines = [{"name": c, "rating": float(ratings[c])} for c in competencies if ratings.get(c) is not None]
    if not lines:
        return {"score": None, "lines": [], "text": "No competencies on the template — the section is left out."}
    total = sum(x["rating"] for x in lines)
    return {"score": r2(total / len(lines)), "lines": lines,
            "text": f"{r2(total):g} / {len(lines)} competencies = {r2(total / len(lines)):g}"}


def overall_score(gs: float | None, cs: float | None, goal_weight: float, competency_weight: float) -> dict:
    """overall = (goal score x goal weight + competency score x competency weight) / (weights used).
    A missing section drops out and the other one carries the whole score."""
    parts = []
    if gs is not None and goal_weight > 0:
        parts.append(("Goals", gs, float(goal_weight)))
    if cs is not None and competency_weight > 0:
        parts.append(("Competencies", cs, float(competency_weight)))
    if not parts:
        return {"score": None, "text": "Nothing was rated."}
    wsum = sum(w for _, _, w in parts)
    total = sum(s * w for _, s, w in parts)
    text = " + ".join(f"{n} {s:g} x {w:g}%" for n, s, w in parts) + f" = {r2(total):g}; / {wsum:g} = {r2(total / wsum):g}"
    return {"score": r2(total / wsum), "text": text, "parts": [{"name": n, "score": s, "weight": w} for n, s, w in parts]}


def compute_scores(goals: list[dict], review: dict, template: dict) -> dict:
    """All the arithmetic for one review, from the MANAGER's ratings."""
    g = goal_score(goals, review.get("goal_ratings") or {})
    c = competency_score(template.get("competencies") or [], review.get("competency_ratings") or {})
    o = overall_score(g["score"], c["score"], float(template.get("goal_weight") or 0),
                      float(template.get("competency_weight") or 0))
    return {"goal": g, "competency": c, "overall": o,
            "band": band_for(o["score"], template.get("scale") or []) if o["score"] is not None else None}


def band_for(score: float | None, scale: list[dict]) -> str | None:
    """The scale level nearest to the score; exactly half-way goes UP."""
    if score is None or not scale:
        return None
    best = min(scale, key=lambda s: (abs(float(s["value"]) - float(score)), -float(s["value"])))
    return best["label"]


# ── Calibration ─────────────────────────────────────────────────────────────
def calibrate(review: dict, new_score, reason: str | None, scale: list[dict]) -> tuple[dict, list[dict]]:
    """HR sets the final score. Returns (values to write, audit rows).
    Any change from what is there now needs a reason."""
    if review.get("stage") != "calibration":
        raise RuleError("A rating can be calibrated only while the review is at HR calibration.")
    values = [float(s["value"]) for s in scale]
    try:
        score = r2(float(new_score))
    except (TypeError, ValueError):
        raise RuleError("The final score must be a number.", 422)
    if not min(values) <= score <= max(values):
        raise RuleError(f"The final score must be between {min(values):g} and {max(values):g}.", 422)
    old_score = review.get("final_score")
    old_band = review.get("final_band")
    band = band_for(score, scale)
    changed = old_score is None or r2(float(old_score)) != score
    if changed and old_score is not None and not (reason or "").strip():
        raise RuleError("Give a reason for changing the rating. It is kept in the audit trail.", 422)
    audit = []
    if changed:
        audit.append({"action": "calibrate", "field": "final_score",
                      "old_value": None if old_score is None else f"{r2(float(old_score)):g}",
                      "new_value": f"{score:g}", "reason": (reason or "").strip() or None})
        if old_band != band:
            audit.append({"action": "calibrate", "field": "final_band", "old_value": old_band, "new_value": band,
                          "reason": (reason or "").strip() or None})
    return {"final_score": score, "final_band": band,
            "calibration_note": (reason or "").strip()[:400] or review.get("calibration_note")}, audit


def distribution(bands: list, scale: list[dict], guideline: list[dict] | None = None) -> list[dict]:
    """Head-count per rating band, against the optional guideline."""
    guide = {g["label"]: float(g.get("pct") or 0) for g in (guideline or [])}
    rated = [b for b in bands if b]
    out = []
    for s in sorted(scale, key=lambda s: -float(s["value"])):
        n = sum(1 for b in rated if b == s["label"])
        pct = r2(100 * n / len(rated)) if rated else 0.0
        row = {"label": s["label"], "value": float(s["value"]), "count": n, "pct": pct}
        if guide:
            row["guideline_pct"] = guide.get(s["label"], 0.0)
            row["guideline_count"] = r2(len(rated) * row["guideline_pct"] / 100)
            row["delta_pct"] = r2(pct - row["guideline_pct"])
        out.append(row)
    return out


# ── Eligibility ─────────────────────────────────────────────────────────────
NOTICE_STATUSES = ("notice", "resigned", "serving_notice")


def eligible(emp: dict, joined_before: date | None, exclude_notice: bool, today: date) -> tuple[bool, str | None]:
    status = (emp.get("status") or "").lower()
    if status == "terminated" or not emp.get("is_active", True):
        return False, "Has left"
    if status == "onboarding":
        return False, "Still onboarding"
    hire = emp.get("hire_date")
    if joined_before:
        if not hire:
            return False, "No joining date on record"
        if hire >= joined_before:
            return False, f"Joined on or after {joined_before.isoformat()}"
    if exclude_notice and (status in NOTICE_STATUSES or emp.get("termination_date") or emp.get("in_notice")):
        return False, "In notice period"
    return True, None


# ── Rating -> increment ─────────────────────────────────────────────────────
def clean_matrix(rows, labels: set) -> list[dict]:
    out, seen = [], set()
    for row in rows or []:
        band = (row or {}).get("band")
        if band not in labels:
            raise RuleError(f"'{band}' is not on the rating scale.", 422)
        grade = str(row.get("grade") or "").strip() or None
        try:
            lo, hi = float(row.get("min_pct")), float(row.get("max_pct"))
        except (TypeError, ValueError):
            raise RuleError("Every matrix row needs a minimum % and a maximum %.", 422)
        if not 0 <= lo <= hi <= 100:
            raise RuleError("Each range must run from a minimum to a maximum between 0 and 100.", 422)
        d = row.get("default_pct")
        d = r2((lo + hi) / 2) if d in (None, "") else float(d)
        if not lo <= d <= hi:
            raise RuleError("The default % must sit inside its range.", 422)
        key = (band, (grade or "").lower())
        if key in seen:
            raise RuleError(f"There are two rows for {band}{' / ' + grade if grade else ''}.", 422)
        seen.add(key)
        out.append({"band": band, "grade": grade, "min_pct": r2(lo), "max_pct": r2(hi), "default_pct": r2(d)})
    return out


def matrix_lookup(matrix: list[dict], band: str | None, grade: str | None) -> dict | None:
    """The row for this band + grade; else the band's any-grade row; else nothing."""
    g = (grade or "").strip().lower()
    any_grade = None
    for row in matrix or []:
        if row.get("band") != band:
            continue
        rg = (row.get("grade") or "").strip().lower()
        if rg and rg == g:
            return row
        if not rg:
            any_grade = row
    return any_grade


def build_items(reviews: list[dict], annual_by_employee: dict, matrix: list[dict]) -> list[dict]:
    """One proposal line per PUBLISHED review, pre-filled from the matrix."""
    items = []
    for rv in reviews:
        if rv.get("stage") != "published":
            continue
        row = matrix_lookup(matrix, rv.get("final_band"), rv.get("grade"))
        eid = str(rv["employee_id"])
        items.append({
            "employee_id": eid, "name": rv.get("employee_name"), "employee_number": rv.get("employee_number"),
            "department": rv.get("department"), "grade": rv.get("grade"), "band": rv.get("final_band"),
            "final_score": rv.get("final_score"), "current_annual": r2(annual_by_employee.get(eid) or 0),
            "min_pct": row["min_pct"] if row else None, "max_pct": row["max_pct"] if row else None,
            "proposed_pct": row["default_pct"] if row else 0.0, "reason": None,
            "include": bool(row) and (annual_by_employee.get(eid) or 0) > 0,
        })
    return items


def check_item(item: dict) -> str | None:
    """None when the line is fine, else why it is not."""
    try:
        pct = float(item.get("proposed_pct") or 0)
    except (TypeError, ValueError):
        return "The % must be a number."
    if pct < 0 or pct > 100:
        return "The % must be between 0 and 100."
    reason = (item.get("reason") or "").strip()
    lo, hi = item.get("min_pct"), item.get("max_pct")
    if lo is None or hi is None:
        return None if (pct == 0 or reason) else "No matrix row for this rating. Give a reason for the %."
    if not float(lo) <= pct <= float(hi):
        return None if reason else f"Outside the {float(lo):g}% to {float(hi):g}% range. Give a reason."
    return None


def item_cost(item: dict) -> float:
    return r2(float(item.get("current_annual") or 0) * float(item.get("proposed_pct") or 0) / 100)


def totals(items: list[dict], budget) -> dict:
    inc = [i for i in items if i.get("include")]
    cost = r2(sum(item_cost(i) for i in inc))
    base = r2(sum(float(i.get("current_annual") or 0) for i in inc))
    b = None if budget in (None, "") else r2(float(budget))
    return {"people": len(inc), "current_annual_total": base, "increase_total": cost,
            "average_pct": r2(100 * cost / base) if base else 0.0, "budget": b,
            "remaining": None if b is None else r2(b - cost), "over_budget": b is not None and cost > b,
            "problems": sum(1 for i in inc if check_item(i))}


def bulk_draft_payload(cycle_id: str, cycle_name: str, items: list[dict], effective_from: date) -> dict:
    """What we POST to payroll `/payroll/revisions/bulk-draft`. Payroll owns the
    review, the approval and the applying — this is a draft request only."""
    out = []
    for i in items:
        if not i.get("include") or float(i.get("proposed_pct") or 0) <= 0:
            continue
        out.append({
            "employee_id": str(i["employee_id"]), "percent": r2(i["proposed_pct"]),
            "effective_from": effective_from.isoformat(),
            "reason": f"Appraisal: {cycle_name} - rating {i.get('band') or 'not set'}",
            "note": (i.get("reason") or "").strip() or None,
        })
    return {"source": f"performance:{cycle_id}", "items": out}
