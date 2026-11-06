"""Time & Attendance — Canadian ESA rules engine (effective-dated).

⚠️  DESIGN REFERENCES, NOT VERIFIED PAYROLL LAW. Every threshold, divisor and
    eligibility rule below is marked TODO-VERIFY and must be confirmed against
    each province's Employment Standards Act (and kept current) before it drives
    a real paycheque. The *structure* is production-shaped — per-province,
    effective-dated, with documented interpretations — so verification is a data
    edit, not a rewrite.

Effective-dating: each rule set is keyed by province, and `_effective(table,
province, year)` picks the newest year ≤ the requested year (falling back to the
province default, then the national default). Add a 2027 block to change a rate.

Covers: overtime (daily/weekly/double-time by province), statutory-holiday
eligibility + average-daily-wage pay, and rest/break rules (reference only —
Phase 4 enforces them at punch time).
"""
from __future__ import annotations

# ── Overtime rules ────────────────────────────────────────────────────────────
# daily_ot_after     : hours/day beyond which 1.5× applies (None = no daily rule)
# daily_double_after : hours/day beyond which 2× applies   (None = no 2× tier)
# weekly_ot_after    : hours/week beyond which 1.5× applies
# combine            : "weekly"     → weekly threshold only (ON, QC, MB, SK, …)
#                      "greater_of" → pay the greater of daily-only vs weekly-only (AB)
#                      "both"       → daily tiers PLUS weekly on straight-time (BC)
_OT_RULES = {
    "ON": {2026: {"daily_ot_after": None, "daily_double_after": None, "weekly_ot_after": 44, "combine": "weekly", "banked_allowed": True}},   # TODO-VERIFY
    "BC": {2026: {"daily_ot_after": 8, "daily_double_after": 12, "weekly_ot_after": 40, "combine": "both", "banked_allowed": True}},          # TODO-VERIFY
    "AB": {2026: {"daily_ot_after": 8, "daily_double_after": None, "weekly_ot_after": 44, "combine": "greater_of", "banked_allowed": True}},  # TODO-VERIFY
    "QC": {2026: {"daily_ot_after": None, "daily_double_after": None, "weekly_ot_after": 40, "combine": "weekly", "banked_allowed": False}},  # TODO-VERIFY
    "MB": {2026: {"daily_ot_after": 8, "daily_double_after": None, "weekly_ot_after": 40, "combine": "greater_of", "banked_allowed": True}},  # TODO-VERIFY
    "SK": {2026: {"daily_ot_after": 8, "daily_double_after": None, "weekly_ot_after": 40, "combine": "greater_of", "banked_allowed": True}},  # TODO-VERIFY
}
_OT_DEFAULT = {"daily_ot_after": None, "daily_double_after": None, "weekly_ot_after": 44, "combine": "weekly", "banked_allowed": False}  # TODO-VERIFY (federal-ish)

# ── Statutory-holiday pay ─────────────────────────────────────────────────────
# avg_wage_method : how the "general holiday pay" amount is computed
#   "four_week_div20"  → regular wages in the 4 work weeks before ÷ 20        (ON)
#   "thirty_day_avg"   → wages in 30 calendar days before ÷ days worked        (BC)
#   "avg_daily_worked" → wages in the lookback ÷ days actually worked          (AB/def)
# worked_premium  : multiplier for hours actually worked on the holiday
# eligibility     : "last_and_first" → must work the scheduled shift before AND after
_STAT_RULES = {
    "ON": {2026: {"avg_wage_method": "four_week_div20", "lookback_days": 28, "worked_premium": 1.5, "eligibility": "last_and_first"}},   # TODO-VERIFY
    "BC": {2026: {"avg_wage_method": "thirty_day_avg", "lookback_days": 30, "worked_premium": 1.5, "eligibility": "30day_15worked"}},    # TODO-VERIFY
    "AB": {2026: {"avg_wage_method": "avg_daily_worked", "lookback_days": 28, "worked_premium": 1.5, "eligibility": "last_and_first"}},  # TODO-VERIFY
    "QC": {2026: {"avg_wage_method": "four_week_div20", "lookback_days": 28, "worked_premium": 1.0, "eligibility": "none"}},             # TODO-VERIFY (QC: indemnity or comp day)
}
_STAT_DEFAULT = {"avg_wage_method": "avg_daily_worked", "lookback_days": 28, "worked_premium": 1.5, "eligibility": "last_and_first"}  # TODO-VERIFY

# ── Rest / break rules (reference only — Phase 4 enforces at punch time) ───────
_BREAK_RULES = {
    "ON": {2026: {"meal_after_hours": 5, "meal_minutes": 30, "daily_rest_hours": 11, "weekly_rest_hours": 24}},   # TODO-VERIFY
    "BC": {2026: {"meal_after_hours": 5, "meal_minutes": 30, "daily_rest_hours": 8, "weekly_rest_hours": 32}},    # TODO-VERIFY
    "AB": {2026: {"meal_after_hours": 5, "meal_minutes": 30, "daily_rest_hours": 8, "weekly_rest_hours": 24}},    # TODO-VERIFY
    "QC": {2026: {"meal_after_hours": 5, "meal_minutes": 30, "daily_rest_hours": 11, "weekly_rest_hours": 32}},   # TODO-VERIFY
}
_BREAK_DEFAULT = {"meal_after_hours": 5, "meal_minutes": 30, "daily_rest_hours": 8, "weekly_rest_hours": 24}  # TODO-VERIFY


def _effective(table: dict, default: dict, province: str, year: int | None) -> dict:
    prov = (province or "ON").upper()
    by_year = table.get(prov)
    if not by_year:
        return dict(default)
    yr = year or max(by_year)
    pick = max((y for y in by_year if y <= yr), default=max(by_year))
    return dict(by_year[pick])


def ot_rules(province: str, year: int | None = None) -> dict:
    return _effective(_OT_RULES, _OT_DEFAULT, province, year)


def stat_rules(province: str, year: int | None = None) -> dict:
    return _effective(_STAT_RULES, _STAT_DEFAULT, province, year)


def break_rules(province: str, year: int | None = None) -> dict:
    return _effective(_BREAK_RULES, _BREAK_DEFAULT, province, year)


# ── The overtime engine ───────────────────────────────────────────────────────
def overtime_split(province: str, daily_hours: list[float], year: int | None = None) -> tuple[float, float, float]:
    """Split a week's worked hours into (regular, ot_1.5x, ot_2x) per province.

    INTERPRETATION (TODO-VERIFY): for "both" (BC) we compute daily tiers first,
    then apply the weekly threshold to straight-time hours only, so daily-OT
    hours aren't counted twice. For "greater_of" (AB) we pay the larger of the
    all-daily-OT total vs the all-weekly-OT total.
    """
    r = ot_rules(province, year)
    total = round(sum(daily_hours), 2)
    combine = r["combine"]
    dt, ddt, wt = r.get("daily_ot_after"), r.get("daily_double_after"), r.get("weekly_ot_after")

    if combine == "weekly":
        ot15 = max(0.0, total - wt) if wt else 0.0
        return round(total - ot15, 2), round(ot15, 2), 0.0

    if combine == "greater_of":
        daily_ot = sum(max(0.0, h - dt) for h in daily_hours) if dt else 0.0
        weekly_ot = max(0.0, total - wt) if wt else 0.0
        ot15 = max(daily_ot, weekly_ot)
        return round(total - ot15, 2), round(ot15, 2), 0.0

    # "both" — BC: daily 1.5×/2× tiers, then weekly 1.5× on the straight-time part
    ot20 = daily15 = straight = 0.0
    for h in daily_hours:
        if ddt and h > ddt:
            ot20 += h - ddt
            daily15 += ddt - dt
            straight += dt
        elif dt and h > dt:
            daily15 += h - dt
            straight += dt
        else:
            straight += h
    weekly15 = max(0.0, straight - wt) if wt else 0.0
    ot15 = daily15 + weekly15
    reg = total - ot15 - ot20
    return round(reg, 2), round(ot15, 2), round(ot20, 2)


# ── The statutory-holiday pay engine ──────────────────────────────────────────
def stat_eligibility(province: str, worked_before: bool, worked_after: bool,
                     days_worked_in_lookback: int, year: int | None = None) -> bool:
    """Is the employee entitled to holiday pay? (TODO-VERIFY per province.)"""
    rule = stat_rules(province, year)["eligibility"]
    if rule == "none":
        return True
    if rule == "30day_15worked":
        # BC: employed 30 days AND worked/earned wages on 15 of the last 30 days.
        return days_worked_in_lookback >= 15
    # "last_and_first" — worked the scheduled day before AND after (leniency: if we
    # have no schedule signal, don't punish; require at least one adjacent day).
    return worked_before and worked_after


def stat_holiday_pay(province: str, wages_in_lookback: float, days_worked_in_lookback: int,
                     rate: float, standard_daily_hours: float, year: int | None = None) -> float:
    """The general-holiday-pay amount (the paid day off), per the province's
    average-wage method. (TODO-VERIFY the exact divisor / wage base.)"""
    method = stat_rules(province, year)["avg_wage_method"]
    if method == "four_week_div20" and wages_in_lookback > 0:
        return round(wages_in_lookback / 20, 2)
    if method == "thirty_day_avg" and days_worked_in_lookback > 0:
        return round(wages_in_lookback / days_worked_in_lookback, 2)
    if method == "avg_daily_worked" and days_worked_in_lookback > 0:
        return round(wages_in_lookback / days_worked_in_lookback, 2)
    # Fallback: a standard day at the base rate.
    return round((standard_daily_hours or 0) * (rate or 0), 2)


def stat_worked_premium_multiplier(province: str, year: int | None = None) -> float:
    return stat_rules(province, year)["worked_premium"]
