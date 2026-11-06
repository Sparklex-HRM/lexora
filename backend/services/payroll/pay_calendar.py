"""Lexora HRM — pay-calendar generator.

Materializes pay periods for a year directly from the pay FREQUENCY. This is the
concrete place pay frequency turns into real dates: weekly=52, biweekly=26,
semi_monthly=24 (15th + month-end), monthly=12.
"""
import calendar as _cal
from datetime import date, timedelta


def generate_periods(pay_frequency: str, first_pay_date: date, year: int) -> list[dict]:
    if pay_frequency == "weekly":
        return _recurring(first_pay_date, year, step_days=7)
    if pay_frequency == "biweekly":
        return _recurring(first_pay_date, year, step_days=14)
    if pay_frequency == "semi_monthly":
        return _semi_monthly(year)
    if pay_frequency == "monthly":
        return _monthly(year)
    raise ValueError(f"Unknown pay frequency '{pay_frequency}'")


def _recurring(anchor: date, year: int, step_days: int) -> list[dict]:
    # walk back to the first pay date on/after Jan 1 of the year
    d = anchor
    while d.year > year or (d.year == year and d - timedelta(days=step_days) >= date(year, 1, 1)):
        d -= timedelta(days=step_days)
    while d.year < year:
        d += timedelta(days=step_days)
    out = []
    while d.year == year:
        out.append({"period_start": (d - timedelta(days=step_days - 1)).isoformat(),
                    "period_end": d.isoformat(), "pay_date": (d + timedelta(days=3)).isoformat()})
        d += timedelta(days=step_days)
    return out


def _semi_monthly(year: int) -> list[dict]:
    out = []
    for m in range(1, 13):
        last = _cal.monthrange(year, m)[1]
        out.append({"period_start": date(year, m, 1).isoformat(),
                    "period_end": date(year, m, 15).isoformat(),
                    "pay_date": date(year, m, 15).isoformat()})
        out.append({"period_start": date(year, m, 16).isoformat(),
                    "period_end": date(year, m, last).isoformat(),
                    "pay_date": date(year, m, last).isoformat()})
    return out


def _monthly(year: int) -> list[dict]:
    out = []
    for m in range(1, 13):
        last = _cal.monthrange(year, m)[1]
        out.append({"period_start": date(year, m, 1).isoformat(),
                    "period_end": date(year, m, last).isoformat(),
                    "pay_date": date(year, m, last).isoformat()})
    return out
