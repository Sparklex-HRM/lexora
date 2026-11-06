"""Compensation cycles — the pure rules (no database, no HTTP).

Matrix maths, compa-ratio bands, eligibility, budget usage and limits, outlier flags,
calibration distributions, analytics (descriptive only), the payroll bulk-draft
payload, CSV in/out and the approval chain fallback.

Money is annual CAD (the cycle's currency). A line's ``proposed_pct`` and
``proposed_amount`` are always kept in step: amount = current × pct / 100.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from hiring_logic import RuleError

STATUSES = ("draft", "open", "calibration", "approval", "applying", "applied", "closed")
STATUS_LABEL = {"draft": "Draft", "open": "Open to managers", "calibration": "HR calibration",
                "approval": "Awaiting approval", "applying": "Applying", "applied": "Applied", "closed": "Closed"}
# what → from which statuses
MOVES = {
    "open": ("draft",),
    "calibrate": ("open",),
    "reopen": ("calibration",),          # back to the managers
    "submit": ("calibration",),          # to the approval chain
    "withdraw": ("approval",),           # back to calibration
    "apply": ("approval",),              # needs approval.state == approved
    "close": ("applied",),
}
HR_HEAD_ROLES = {"superadmin", "org_admin", "hr_admin"}
HR_ROLES = HR_HEAD_ROLES | {"hr_manager"}
ADMIN_ROLES = {"superadmin", "org_admin"}

DEFAULT_COMPA_BANDS = [
    {"key": "low", "label": "Below 0.90", "min_cr": None, "max_cr": 0.9},
    {"key": "mid", "label": "0.90 to 1.10", "min_cr": 0.9, "max_cr": 1.1},
    {"key": "high", "label": "Above 1.10", "min_cr": 1.1, "max_cr": None},
]
DEFAULT_SETTINGS = {
    "compa_bands": DEFAULT_COMPA_BANDS,
    "grade_midpoints": {},            # {grade: annual midpoint $}
    "default_matrix": [],             # [{rating, cr_band, rec_pct, min_pct, max_pct}]
    "budget_mode": "soft",            # hard: a proposal that overruns its budget is refused
    "default_budget_pct": 10.0,       # a department with no explicit budget gets this % of its base
    "allow_lump_sum": True,
    "guidelines": "",
}
CSV_COLUMNS = ["employee_number", "employee_name", "department", "grade", "rating", "current_annual", "compa_ratio",
               "rec_pct", "min_pct", "max_pct", "proposed_pct", "proposed_amount", "new_annual", "lump_sum",
               "promotion", "promo_designation", "promo_grade", "comment"]
IMPORT_COLUMNS = ("proposed_pct", "proposed_amount", "lump_sum", "promotion", "promo_designation", "promo_grade", "comment")


def r2(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def _f(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── settings ─────────────────────────────────────────────────────────────────
def merge_settings(saved: dict | None) -> dict:
    out = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULT_SETTINGS.items()}
    for k, v in (saved or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def clean_compa_bands(rows) -> list[dict]:
    out, seen = [], set()
    for r in rows or []:
        key = str((r or {}).get("key") or "").strip().lower()
        if not key:
            raise RuleError("Every compa-ratio band needs a key (for example low / mid / high).", 422)
        if key in seen:
            raise RuleError(f"The band '{key}' is listed twice.", 422)
        lo, hi = _f(r.get("min_cr")), _f(r.get("max_cr"))
        if lo is not None and hi is not None and lo >= hi:
            raise RuleError(f"Band '{key}': the lower bound must be below the upper bound.", 422)
        seen.add(key)
        out.append({"key": key, "label": str(r.get("label") or key)[:60], "min_cr": lo, "max_cr": hi})
    if not out:
        raise RuleError("Keep at least one compa-ratio band.", 422)
    return out


def clean_matrix(rows, band_keys: set) -> list[dict]:
    """[{rating, cr_band ('' = any band), rec_pct, min_pct, max_pct}] — validated."""
    out, seen = [], set()
    for r in rows or []:
        rating = str((r or {}).get("rating") or "").strip()
        if not rating:
            raise RuleError("Every matrix row needs a rating.", 422)
        cr = str(r.get("cr_band") or "").strip().lower()
        if cr and cr not in band_keys:
            raise RuleError(f"'{cr}' is not one of the compa-ratio bands.", 422)
        lo, hi = _f(r.get("min_pct")), _f(r.get("max_pct"))
        if lo is None or hi is None:
            raise RuleError(f"Row '{rating}': give a minimum % and a maximum %.", 422)
        if not 0 <= lo <= hi <= 100:
            raise RuleError(f"Row '{rating}': the range must run from a minimum to a maximum between 0 and 100.", 422)
        rec = _f(r.get("rec_pct"))
        rec = r2((lo + hi) / 2) if rec is None else rec
        if not lo <= rec <= hi:
            raise RuleError(f"Row '{rating}': the recommended % must sit inside its range.", 422)
        key = (rating.lower(), cr)
        if key in seen:
            raise RuleError(f"There are two rows for {rating}{' / ' + cr if cr else ''}.", 422)
        seen.add(key)
        out.append({"rating": rating, "cr_band": cr or None, "rec_pct": r2(rec), "min_pct": r2(lo), "max_pct": r2(hi)})
    return out


def clean_budgets(rows) -> list[dict]:
    out, seen = [], set()
    for r in rows or []:
        scope = str((r or {}).get("scope") or "department").strip().lower()
        if scope not in ("department", "entity", "total"):
            raise RuleError("A budget scope must be department, entity or total.", 422)
        key = "" if scope == "total" else str(r.get("key") or "").strip()
        if scope != "total" and not key:
            raise RuleError(f"A {scope} budget needs the {scope} it applies to.", 422)
        amount, pct = _f(r.get("amount")), _f(r.get("pct"))
        if amount is None and pct is None:
            raise RuleError(f"Budget for {key or 'the whole cycle'}: give an amount or a %.", 422)
        if (amount is not None and amount < 0) or (pct is not None and not 0 <= pct <= 100):
            raise RuleError(f"Budget for {key or 'the whole cycle'}: the amount must be positive and the % between 0 and 100.", 422)
        k = (scope, key.lower())
        if k in seen:
            raise RuleError(f"There are two budgets for {key or 'the whole cycle'}.", 422)
        seen.add(k)
        out.append({"scope": scope, "key": key, "amount": r2(amount) if amount is not None else None, "pct": pct})
    return out


# ── compa-ratio + matrix ─────────────────────────────────────────────────────
def compa_ratio(annual, midpoint) -> float | None:
    a, m = _f(annual, 0.0), _f(midpoint, 0.0)
    if not m or m <= 0 or a <= 0:
        return None
    return round(a / m, 3)


def compa_band_key(cr: float | None, bands: list[dict]) -> str | None:
    if cr is None:
        return None
    for b in bands or []:
        lo, hi = _f(b.get("min_cr")), _f(b.get("max_cr"))
        if (lo is None or cr >= lo) and (hi is None or cr < hi):
            return b["key"]
    return None


def matrix_lookup(matrix: list[dict], rating: str | None, cr_band: str | None) -> dict | None:
    """The row for this rating + band; else the rating's any-band row; else nothing."""
    if not rating:
        return None
    rl, any_band = rating.strip().lower(), None
    for row in matrix or []:
        if str(row.get("rating") or "").strip().lower() != rl:
            continue
        rb = (row.get("cr_band") or "").strip().lower()
        if rb and cr_band and rb == cr_band:
            return row
        if not rb:
            any_band = row
    return any_band


def recommend(line: dict, cycle: dict) -> dict:
    """compa_ratio / compa_band / rec / min / max for one line from the cycle's frozen rules."""
    mid = _f((cycle.get("grade_midpoints") or {}).get(line.get("grade") or ""), None)
    cr = compa_ratio(line.get("current_annual"), mid)
    band = compa_band_key(cr, cycle.get("compa_bands") or DEFAULT_COMPA_BANDS)
    row = matrix_lookup(cycle.get("matrix") or [], line.get("rating"), band)
    return {"midpoint": mid, "compa_ratio": cr, "compa_band": band,
            "rec_pct": row["rec_pct"] if row else None, "min_pct": row["min_pct"] if row else None,
            "max_pct": row["max_pct"] if row else None}


# ── eligibility ──────────────────────────────────────────────────────────────
NOTICE_STATUSES = ("notice", "resigned", "serving_notice")


def eligible(emp: dict, elig: dict, today: date, rating: str | None) -> tuple[bool, str | None]:
    """emp: {status, is_active, hire_date, department, grade, entity_id, annual_salary, pay_type}."""
    elig = elig or {}
    status = (emp.get("status") or "").lower()
    if status == "terminated" or not emp.get("is_active", True):
        return False, "Has left"
    if status == "onboarding":
        return False, "Still onboarding"
    if (emp.get("pay_type") or "salary") == "hourly":
        return False, "Hourly paid"
    if not _f(emp.get("annual_salary"), 0):
        return False, "No salary on record"
    if elig.get("exclude_notice", True) and (status in NOTICE_STATUSES or emp.get("termination_date") or emp.get("in_notice")):
        return False, "In notice period"
    jb = elig.get("joined_before")
    if jb:
        jb = date.fromisoformat(str(jb)[:10]) if not isinstance(jb, date) else jb
        hire = emp.get("hire_date")
        if not hire:
            return False, "No joining date on record"
        if hire >= jb:
            return False, f"Joined on or after {jb.isoformat()}"
    for key, label in (("departments", "department"), ("grades", "grade"), ("entity_ids", "entity_id")):
        wanted = [str(x).strip().lower() for x in (elig.get(key) or []) if str(x).strip()]
        if wanted and str(emp.get(label) or "").strip().lower() not in wanted:
            return False, f"Not in the selected {label.replace('_id', '')}s"
    if elig.get("require_rating") and not rating:
        return False, "No published rating"
    return True, None


# ── proposals, flags ─────────────────────────────────────────────────────────
def set_proposal(line: dict, pct=None, amount=None) -> dict:
    """Keep % and amount in step. Returns the updated line."""
    cur = _f(line.get("current_annual"), 0.0)
    if pct is not None:
        p = _f(pct)
        if p is None or p < 0 or p > 100:
            raise RuleError("The % must be a number between 0 and 100.", 422)
        line["proposed_pct"] = r2(p)
        line["proposed_amount"] = r2(cur * p / 100)
    elif amount is not None:
        a = _f(amount)
        if a is None or a < 0:
            raise RuleError("The amount must be a positive number.", 422)
        line["proposed_amount"] = r2(a)
        line["proposed_pct"] = r2(a * 100 / cur) if cur else 0.0
    return line


def new_annual(line: dict) -> float:
    return r2(_f(line.get("current_annual"), 0.0) + _f(line.get("proposed_amount"), 0.0))


def line_cost(line: dict) -> float:
    """What this line costs the budget per year: increase + lump sum."""
    if not line.get("include", True):
        return 0.0
    return r2(_f(line.get("proposed_amount"), 0.0) + _f(line.get("lump_sum"), 0.0))


def line_flags(line: dict) -> list[dict]:
    """Outlier flags for one line (budget overruns are added by `flag_budget`)."""
    out = []
    if not line.get("include", True):
        return out
    pct = _f(line.get("proposed_pct"), 0.0)
    lo, hi = _f(line.get("min_pct")), _f(line.get("max_pct"))
    if not line.get("rating"):
        out.append({"code": "no_rating", "text": "No published rating"})
    elif lo is None or hi is None:
        out.append({"code": "no_matrix", "text": "No matrix row for this rating"})
    else:
        if pct > hi:
            out.append({"code": "above_max", "text": f"Above the matrix maximum ({hi:g}%)"})
        if pct < lo and pct > 0:
            out.append({"code": "below_min", "text": f"Below the matrix minimum ({lo:g}%)"})
    if line.get("promotion") and not (line.get("promo_designation") or line.get("promo_grade")):
        out.append({"code": "promotion_incomplete", "text": "Promotion without a new designation or grade"})
    if line.get("compa_ratio") is None:
        out.append({"code": "no_midpoint", "text": "No grade midpoint, compa-ratio unknown"})
    return out


# ── budgets ──────────────────────────────────────────────────────────────────
def scopes_of(line: dict) -> list[tuple[str, str]]:
    out = [("total", "")]
    if line.get("department"):
        out.append(("department", str(line["department"])))
    if line.get("entity_id"):
        out.append(("entity", str(line["entity_id"])))
    return out


def budget_usage(lines: list[dict], budgets: list[dict], default_pct: float | None, promotion_budget=None) -> dict:
    """{scope:key → {scope, key, base, allowed, allowed_pct, used, used_pct, remaining, over, explicit, people}}
    plus "promotion" → {allowed, used, over}. A department / entity with no explicit budget
    gets default_pct of its base; the total is the sum of what is explicit or defaulted."""
    explicit = {(b["scope"], (b.get("key") or "").lower()): b for b in budgets or []}
    agg: dict = {}
    for ln in lines:
        for scope, key in scopes_of(ln):
            k = (scope, key.lower())
            a = agg.setdefault(k, {"scope": scope, "key": key, "base": 0.0, "used": 0.0, "people": 0})
            if not ln.get("include", True):
                continue                                  # an excluded person is outside the base and the spend
            a["base"] += _f(ln.get("current_annual"), 0.0)
            a["used"] += line_cost(ln)
            a["people"] += 1
    out: dict = {}
    for k, a in agg.items():
        b = explicit.get(k)
        if b and b.get("amount") is not None:
            allowed, pct, is_explicit = float(b["amount"]), (b["amount"] * 100 / a["base"] if a["base"] else None), True
        elif b and b.get("pct") is not None:
            allowed, pct, is_explicit = a["base"] * float(b["pct"]) / 100, float(b["pct"]), True
        elif k[0] == "total":
            allowed, pct, is_explicit = None, None, False          # filled below from the parts
        else:
            p = _f(default_pct)
            allowed, pct, is_explicit = (a["base"] * p / 100 if p is not None else None), p, False
        out[f"{k[0]}:{a['key']}"] = {**a, "base": r2(a["base"]), "used": r2(a["used"]), "allowed": r2(allowed) if allowed is not None else None,
                                     "allowed_pct": round(pct, 2) if pct is not None else None, "explicit": is_explicit}
    tot = out.get("total:")
    if tot and tot["allowed"] is None:
        parts = [v for key, v in out.items() if key.startswith("department:") and v["allowed"] is not None]
        if parts:
            tot["allowed"] = r2(sum(v["allowed"] for v in parts))
            tot["allowed_pct"] = round(tot["allowed"] * 100 / tot["base"], 2) if tot["base"] else None
    for v in out.values():
        v["used_pct"] = round(v["used"] * 100 / v["base"], 2) if v["base"] else 0.0
        v["remaining"] = r2(v["allowed"] - v["used"]) if v["allowed"] is not None else None
        v["over"] = bool(v["allowed"] is not None and v["used"] > v["allowed"] + 0.005)
    promo_used = r2(sum(_f(ln.get("proposed_amount"), 0.0) for ln in lines if ln.get("promotion") and ln.get("include", True)))
    pb = _f(promotion_budget)
    out["promotion"] = {"scope": "promotion", "key": "", "allowed": pb, "used": promo_used,
                        "remaining": r2(pb - promo_used) if pb is not None else None,
                        "over": bool(pb is not None and promo_used > pb + 0.005),
                        "people": sum(1 for ln in lines if ln.get("promotion") and ln.get("include", True))}
    return out


def overruns_for(line: dict, usage: dict) -> list[str]:
    """The budgets this line sits in that are over."""
    out = []
    for scope, key in scopes_of(line):
        u = usage.get(f"{scope}:{key}")
        if u and u["over"]:
            out.append("the whole cycle" if scope == "total" else f"{scope} {key}")
    if line.get("promotion") and usage.get("promotion", {}).get("over"):
        out.append("the promotion budget")
    return out


def check_hard_limit(line: dict, usage: dict, mode: str) -> str | None:
    """Hard mode: a saved proposal may never leave a budget over. → the refusal text or None."""
    if (mode or "soft") != "hard":
        return None
    over = overruns_for(line, usage)
    if not over:
        return None
    return "This proposal would overrun the budget for " + ", ".join(over) + ". Lower it or ask HR to raise the budget."


def flag_budget(lines: list[dict], usage: dict) -> None:
    for ln in lines:
        over = overruns_for(ln, usage)
        if over and ln.get("include", True) and line_cost(ln) > 0:
            ln.setdefault("flags", []).append({"code": "over_budget", "text": "Budget overrun: " + ", ".join(over)})


# ── calibration + analytics (descriptive only) ───────────────────────────────
def _avg(vals: list[float]) -> float | None:
    return r2(sum(vals) / len(vals)) if vals else None


def group_stats(lines: list[dict], key: str) -> list[dict]:
    """people / base / increase / avg % / avg compa before+after per value of `key`."""
    groups: dict = {}
    for ln in lines:
        if not ln.get("include", True):
            continue
        g = groups.setdefault(str(ln.get(key) or "Not set"), {"key": str(ln.get(key) or "Not set"), "people": 0, "base": 0.0,
                                                                "increase": 0.0, "lump_sum": 0.0, "pcts": [], "cr_before": [], "cr_after": [],
                                                                "promotions": 0})
        cur, inc = _f(ln.get("current_annual"), 0.0), _f(ln.get("proposed_amount"), 0.0)
        g["people"] += 1
        g["base"] += cur
        g["increase"] += inc
        g["lump_sum"] += _f(ln.get("lump_sum"), 0.0)
        g["pcts"].append(_f(ln.get("proposed_pct"), 0.0))
        g["promotions"] += 1 if ln.get("promotion") else 0
        mid = _f(ln.get("midpoint"))
        if mid:
            g["cr_before"].append(cur / mid)
            g["cr_after"].append((cur + inc) / mid)
    out = []
    for g in groups.values():
        out.append({"key": g["key"], "people": g["people"], "base": r2(g["base"]), "increase": r2(g["increase"]),
                    "lump_sum": r2(g["lump_sum"]), "promotions": g["promotions"],
                    "avg_pct": _avg(g["pcts"]), "weighted_pct": round(g["increase"] * 100 / g["base"], 2) if g["base"] else 0.0,
                    "avg_cr_before": round(sum(g["cr_before"]) / len(g["cr_before"]), 3) if g["cr_before"] else None,
                    "avg_cr_after": round(sum(g["cr_after"]) / len(g["cr_after"]), 3) if g["cr_after"] else None})
    return sorted(out, key=lambda x: x["key"])


def rating_matrix(lines: list[dict]) -> dict:
    """department × rating → {people, avg_pct} for the calibration heat-grid."""
    cells: dict = {}
    ratings: list[str] = []
    for ln in lines:
        if not ln.get("include", True):
            continue
        d, r = str(ln.get("department") or "Not set"), str(ln.get("rating") or "No rating")
        if r not in ratings:
            ratings.append(r)
        c = cells.setdefault(d, {}).setdefault(r, {"people": 0, "pcts": []})
        c["people"] += 1
        c["pcts"].append(_f(ln.get("proposed_pct"), 0.0))
    rows = [{"department": d, "cells": {r: {"people": c["people"], "avg_pct": _avg(c["pcts"])} for r, c in by.items()}}
            for d, by in sorted(cells.items())]
    return {"ratings": ratings, "rows": rows}


def compa_distribution(lines: list[dict], bands: list[dict]) -> list[dict]:
    """How many people sit in each compa-ratio band before and after the cycle."""
    out = {b["key"]: {"key": b["key"], "label": b.get("label") or b["key"], "before": 0, "after": 0} for b in bands or DEFAULT_COMPA_BANDS}
    unknown = {"key": "unknown", "label": "No midpoint", "before": 0, "after": 0}
    for ln in lines:
        if not ln.get("include", True):
            continue
        mid = _f(ln.get("midpoint"))
        if not mid:
            unknown["before"] += 1
            unknown["after"] += 1
            continue
        for which, amount in (("before", _f(ln.get("current_annual"), 0.0)), ("after", new_annual(ln))):
            k = compa_band_key(compa_ratio(amount, mid), bands or DEFAULT_COMPA_BANDS)
            (out[k] if k in out else unknown)[which] += 1
    rows = list(out.values())
    if unknown["before"]:
        rows.append(unknown)
    return rows


def pay_equity(lines: list[dict], min_group: int = 3, flag_at_pct: float = 5.0) -> list[dict]:
    """Per grade: average pay before and after by gender and the gap in %. DESCRIPTIVE
    ONLY — a flag means 'look at this', it is not a finding of unequal pay."""
    by_grade: dict = {}
    for ln in lines:
        if not ln.get("include", True):
            continue
        g = str(ln.get("grade") or "No grade")
        sex = str(ln.get("gender") or "").strip().lower() or "not stated"
        cur, aft = _f(ln.get("current_annual"), 0.0), new_annual(ln)
        d = by_grade.setdefault(g, {}).setdefault(sex, {"people": 0, "before": [], "after": [], "pcts": []})
        d["people"] += 1
        d["before"].append(cur)
        d["after"].append(aft)
        d["pcts"].append(_f(ln.get("proposed_pct"), 0.0))
    out = []
    for g, by_sex in sorted(by_grade.items()):
        groups = {s: {"gender": s, "people": d["people"], "avg_before": _avg(d["before"]), "avg_after": _avg(d["after"]),
                      "avg_pct": _avg(d["pcts"])} for s, d in by_sex.items()}
        m, f = groups.get("male"), groups.get("female")
        gap_before = gap_after = None
        flag = False
        if m and f and m["people"] >= min_group and f["people"] >= min_group and m["avg_before"]:
            gap_before = round((f["avg_before"] - m["avg_before"]) * 100 / m["avg_before"], 2)
            gap_after = round((f["avg_after"] - m["avg_after"]) * 100 / m["avg_after"], 2) if m["avg_after"] else None
            flag = abs(gap_after if gap_after is not None else gap_before) >= flag_at_pct
        out.append({"grade": g, "groups": sorted(groups.values(), key=lambda x: x["gender"]),
                    "gap_before_pct": gap_before, "gap_after_pct": gap_after, "flag": flag,
                    "note": ("Groups smaller than %d people are not compared." % min_group
                             if not (m and f and m["people"] >= min_group and f["people"] >= min_group) else None)})
    return out


def summary(lines: list[dict], usage: dict) -> dict:
    inc = [ln for ln in lines if ln.get("include", True)]
    base = r2(sum(_f(x.get("current_annual"), 0.0) for x in inc))
    increase = r2(sum(_f(x.get("proposed_amount"), 0.0) for x in inc))
    lump = r2(sum(_f(x.get("lump_sum"), 0.0) for x in inc))
    tot = usage.get("total:") or {}
    return {"people": len(inc), "excluded": len(lines) - len(inc), "base": base, "increase": increase, "lump_sum": lump,
            "spent": r2(increase + lump), "budget": tot.get("allowed"), "remaining": tot.get("remaining"),
            "over_budget": bool(tot.get("over")), "avg_pct": _avg([_f(x.get("proposed_pct"), 0.0) for x in inc]),
            "weighted_pct": round(increase * 100 / base, 2) if base else 0.0,
            "promotions": sum(1 for x in inc if x.get("promotion")), "with_increase": sum(1 for x in inc if _f(x.get("proposed_amount"), 0.0) > 0),
            "flagged": sum(1 for x in inc if x.get("flags"))}


# ── approval chain ───────────────────────────────────────────────────────────
def default_chain() -> list[dict]:
    """Until the no-code chain registry (D1) lands: HR head, then the org admin."""
    return [{"roles": ["hr_admin"], "label": "HR head"}, {"roles": ["org_admin"], "label": "Org admin"}]


def may_decide(step: dict, role: str, requester_id: str | None, actor_id: str | None) -> str | None:
    """None when this actor may decide this step, else why not."""
    role = (role or "").lower()
    if role not in ADMIN_ROLES and role not in [r.lower() for r in (step.get("roles") or [])] \
            and str(actor_id or "") not in [str(u) for u in (step.get("user_ids") or [])]:
        return "This step is for " + ", ".join(step.get("roles") or step.get("user_ids") or ["someone else"]) + "."
    if requester_id and actor_id and str(requester_id) == str(actor_id) and role not in ADMIN_ROLES:
        return "You submitted this cycle. Someone else must approve it."
    return None


def chain_metadata(lines: list[dict], usage: dict) -> dict:
    """What the chain registry routes on: total amount, departments, entities."""
    inc = [ln for ln in lines if ln.get("include", True)]
    return {"total_amount": r2(sum(line_cost(x) for x in inc)),
            "department": sorted({str(x.get("department")) for x in inc if x.get("department")}),
            "entity": sorted({str(x.get("entity_id")) for x in inc if x.get("entity_id")}),
            "people": len(inc)}


# ── hand-off to the EXISTING bulk salary-revision path ──────────────────────
def bulk_payload(cycle_id: str, cycle_name: str, lines: list[dict], effective_from: date) -> dict:
    """What goes to payroll POST /payroll/revisions/bulk-draft (PUBLISHED CONTRACT).
    One row per included line with an increase; lump sums are not salary and never go here."""
    items = []
    for ln in lines:
        if not ln.get("include", True) or _f(ln.get("proposed_amount"), 0.0) <= 0:
            continue
        items.append({"employee_id": str(ln["employee_id"]), "new_annual": new_annual(ln),
                      "effective_from": effective_from.isoformat(),
                      "reason": "promotion" if ln.get("promotion") else "annual_increment",
                      "note": f"Compensation cycle: {cycle_name} - rating {ln.get('rating') or 'not set'}"
                              + (f" - {ln['hr_comment']}" if ln.get("hr_comment") else ""),
                      "rating": ln.get("rating")})
    return {"source": f"comp_cycle:{cycle_id}", "name": f"{cycle_name} - increments", "items": items}


def revision_letter_data(line: dict, effective_from: date, components: dict | None) -> dict:
    cur, new = _f(line.get("current_annual"), 0.0), new_annual(line)
    salary = {"old_annual": cur, "new_annual": new, "effective_from": effective_from.isoformat()}
    if components and cur:
        ratio = new / cur
        salary["components"] = {k: round(float(v or 0) * ratio, 2) for k, v in components.items()}
    return {"salary": salary}


def promotion_letter_data(line: dict, effective_from: date, old: dict) -> dict:
    return {"career": {"change_type": "Promotion", "effective_date": effective_from.isoformat(),
                       "old_designation": old.get("job_title"), "new_designation": line.get("promo_designation") or old.get("job_title"),
                       "old_grade": old.get("grade"), "new_grade": line.get("promo_grade") or old.get("grade")}}


# ── CSV ──────────────────────────────────────────────────────────────────────
def to_csv(lines: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for ln in lines:
        w.writerow([ln.get("employee_number"), ln.get("employee_name"), ln.get("department"), ln.get("grade"), ln.get("rating"),
                    ln.get("current_annual"), ln.get("compa_ratio"), ln.get("rec_pct"), ln.get("min_pct"), ln.get("max_pct"),
                    ln.get("proposed_pct"), ln.get("proposed_amount"), new_annual(ln), ln.get("lump_sum"),
                    "yes" if ln.get("promotion") else "no", ln.get("promo_designation") or "", ln.get("promo_grade") or "",
                    ln.get("manager_comment") or ""])
    return buf.getvalue()


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """→ (rows keyed by employee_number with only the editable columns, errors)."""
    rows, errors = [], []
    rd = csv.DictReader(io.StringIO(text or ""))
    if not rd.fieldnames or "employee_number" not in [f.strip() for f in rd.fieldnames]:
        return [], ["The file needs an employee_number column (export the worksheet first and edit that file)."]
    for i, raw in enumerate(rd, start=2):
        r = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        num = r.get("employee_number")
        if not num:
            continue
        row: dict = {"employee_number": num}
        for k in IMPORT_COLUMNS:
            if k in r and r[k] not in (None, ""):
                row[k] = r[k]
        if "promotion" in row:
            row["promotion"] = str(row["promotion"]).strip().lower() in ("yes", "y", "true", "1")
        for k in ("proposed_pct", "proposed_amount", "lump_sum"):
            if k in row and _f(row[k]) is None:
                errors.append(f"Line {i}: {k} is not a number.")
                row.pop(k)
        rows.append(row)
    return rows, errors
