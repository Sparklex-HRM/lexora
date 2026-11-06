"""Time & Attendance — interim rule constants.

⚠️  DESIGN REFERENCES, NOT VERIFIED PAYROLL LAW. These provincial overtime and
    stat-holiday figures are placeholders so the module produces sane numbers in
    Phase 2. Phase 3 replaces this file with a proper effective-dated
    `constants_ca_time` rules engine (daily AND weekly OT, banked time, stat-pay
    averaging, rest/break rules) — verified against each province's ESA before
    it drives a real paycheque. Every threshold below is marked TODO-VERIFY.
"""

# ── Pay-component codes the payroll engine already understands (masters seed) ──
CODE_REG = "REG"      # regular worked hours  (hourly: carries actual pay)
CODE_OT = "OT"        # overtime hours        (1.5×)
CODE_STAT = "STAT"    # statutory holiday pay
CODE_VACP = "VACP"    # vacation pay

OT_MULTIPLIER = 1.5   # TODO-VERIFY: BC/others have a 2× tier above a daily cap

# Grace + break defaults (minutes)
DEFAULT_LATE_GRACE_MIN = 7
DEFAULT_BREAK_MIN = 30

# Default full-time shift pattern seeded for a fresh org.
DEFAULT_SCHEDULE = {
    "name": "Standard 9–5 (Mon–Fri)", "code": "STD-95",
    "days_of_week": [0, 1, 2, 3, 4],   # Mon..Fri  (Python weekday())
    "start_time": "09:00", "end_time": "17:00",
    "break_minutes": 60, "daily_hours": 7.5, "timezone": "America/Toronto",
    "is_default": True,
}

# ── Provincial overtime thresholds (TODO-VERIFY — Phase 3 owns the real engine) ─
# weekly_ot_after : hours per week beyond which OT applies
# daily_ot_after  : hours per day beyond which OT applies (None = no daily rule)
# combine         : "greater_of" pays the larger of daily/weekly OT (AB);
#                   "both" pays daily and weekly independently (BC).
PROVINCE_OT = {
    "ON": {"weekly_ot_after": 44, "daily_ot_after": None, "combine": "weekly"},
    "BC": {"weekly_ot_after": 40, "daily_ot_after": 8, "combine": "both"},
    "AB": {"weekly_ot_after": 44, "daily_ot_after": 8, "combine": "greater_of"},
    "QC": {"weekly_ot_after": 40, "daily_ot_after": None, "combine": "weekly"},
}
_DEFAULT_OT = {"weekly_ot_after": 44, "daily_ot_after": None, "combine": "weekly"}


def province_ot_rule(province: str) -> dict:
    return PROVINCE_OT.get((province or "ON").upper(), _DEFAULT_OT)


def split_regular_overtime(province: str, daily_hours: list[float]) -> tuple[float, float]:
    """Split a week's worked hours into (regular, overtime).

    INTERIM heuristic only — Phase 3 replaces with the verified provincial engine.
    `daily_hours` is the list of hours worked each day in the week.
    """
    rule = province_ot_rule(province)
    total = round(sum(daily_hours), 2)
    weekly_cap = rule["weekly_ot_after"]
    weekly_ot = max(0.0, total - weekly_cap)

    daily_ot = 0.0
    if rule["daily_ot_after"] is not None:
        daily_ot = round(sum(max(0.0, h - rule["daily_ot_after"]) for h in daily_hours), 2)

    combine = rule["combine"]
    if combine == "both":
        ot = round(weekly_ot + daily_ot, 2)          # BC pays both (interim)
    elif combine == "greater_of":
        ot = round(max(weekly_ot, daily_ot), 2)       # AB greater-of
    else:
        ot = round(weekly_ot, 2)                       # weekly only
    ot = min(ot, total)
    return round(total - ot, 2), ot
