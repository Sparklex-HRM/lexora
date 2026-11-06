"""Lexora HRM — shared domain enums.

PAY FREQUENCY is load-bearing: periods-per-year drives BOTH the pay calendar
(how many runs occur) AND the CRA T4127 tax annualization (annualized income =
period income x periods_per_year; per-period tax = annual tax / periods_per_year).
Get this wrong and every deduction is wrong.
"""

PAY_FREQUENCY_PERIODS = {
    "weekly": 52,          # every week
    "biweekly": 26,        # every 2 weeks  (most common in Canada)
    "semi_monthly": 24,    # twice a month (e.g. 15th + last day)
    "monthly": 12,         # once a month
}
PAY_FREQUENCIES = tuple(PAY_FREQUENCY_PERIODS.keys())


def periods_per_year(freq: str) -> int:
    if freq not in PAY_FREQUENCY_PERIODS:
        raise ValueError(f"Unknown pay frequency '{freq}'. Use one of {PAY_FREQUENCIES}.")
    return PAY_FREQUENCY_PERIODS[freq]


EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "fixed_term", "seasonal", "apprentice")
PAY_TYPES = ("salary", "hourly")
EMPLOYEE_STATUSES = ("onboarding", "active", "on_leave", "terminated")

# Core provinces (single CRA/T4127 mechanism, ROC gate) + every jurisdiction (QC has its own engine).
PROVINCES_MVP = ("ON", "BC", "AB")
PROVINCES_ALL = ("ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT")

# Every province / territory a pay record or employee may carry (all 13 jurisdictions).
REGIONS_ALL = PROVINCES_ALL
