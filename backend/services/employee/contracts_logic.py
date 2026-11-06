"""Fixed-term / contract employees — PURE logic (no DB). 2026-10-22.

A "term-bound" employee is employment_type contract or fixed_term with a contract end date.
The current term lives on the employee row (contract_start_date / contract_end_date /
contract_notice_days / contract_ref); every change is a row in employee_contract_terms.

Buckets (days left to the end date, org business date):
    expired  < 0      ·  "7"  0–7   ·  "30"  8–30   ·  "60"  31–60   ·  "90"  61–90   ·  later  > 90
Alerts go out once per (employee, end date, threshold) at 60, 30, 7 and 0 days left.

Statutory notes shown on screen: fixed-term employment is governed by the provincial
employment standards act of the province of employment — the screen states the general rule;
Lexora HRM does not decide notice or severance entitlements here.
"""
from __future__ import annotations

from datetime import date, timedelta

TERM_TYPES = ("contract", "fixed_term")
ALERT_DAYS = (60, 30, 7, 0)
RECOMMENDATIONS = ("renew", "convert", "end")
ACTIONS = ("start", "correct", "recommend", "renew", "convert", "end", "alert")


def is_term_bound(employment_type: str | None) -> bool:
    return (employment_type or "").strip().lower() in TERM_TYPES


def bucket(end: date | None, today: date) -> tuple[str, int | None]:
    if not end:
        return "no_end_date", None
    left = (end - today).days
    if left < 0:
        return "expired", left
    if left <= 7:
        return "7", left
    if left <= 30:
        return "30", left
    if left <= 60:
        return "60", left
    if left <= 90:
        return "90", left
    return "later", left


def due_alert(end: date | None, today: date, already: set[int]) -> int | None:
    """The tightest threshold (60/30/7/0) reached today that was not alerted yet for this end date."""
    if not end:
        return None
    left = (end - today).days
    reached = [d for d in ALERT_DAYS if left <= d]
    if not reached:
        return None
    d = min(reached)
    # a tighter (or the same) threshold already sent covers the looser ones skipped on the way
    return None if any(a is not None and a <= d for a in already) else d


def validate_term(start: date | None, end: date | None, hire: date | None) -> list[str]:
    errs = []
    if not end:
        errs.append("Enter the contract end date")
    if start and end and end < start:
        errs.append("The contract end date cannot be before its start date")
    if hire and start and start < hire:
        errs.append("The contract cannot start before the joining date")
    return errs


def renewal_window(old_end: date, new_end: date) -> tuple[date, date]:
    """A renewal continues the day after the old end date (no gap, no overlap)."""
    if new_end <= old_end:
        raise ValueError("The new end date must be after the current end date")
    return old_end + timedelta(days=1), new_end


def notice_date(end: date | None, notice_days: int | None) -> date | None:
    """Last day to tell the employee about non-renewal, if the contract carries a notice period."""
    if not end or not notice_days:
        return None
    return end - timedelta(days=int(notice_days))


def tenure_days(start: date | None, end: date | None) -> int | None:
    if not start or not end:
        return None
    return (end - start).days + 1


def summarise(rows: list[dict]) -> dict:
    keys = ("expired", "7", "30", "60", "90", "later", "no_end_date")
    return {k: sum(1 for r in rows if r.get("bucket") == k) for k in keys}
