"""Due clocks the lifecycle agents show and chase (Canada). Each clock says where its day count comes from and whether
it is a confirmed rule (``verified``) or an estimate a person must confirm. Nothing else invents a day count.

    sin_request(doj, today)                         CRA: ask a new employee for their SIN within 3 days of the start date
    final_pay(province, last_day, next_pay, today)  provincial employment standards: when the final pay is due
    roe_electronic(period_end, today)               Service Canada: an electronic ROE within 5 calendar days after the end of
                                                    the pay period in which the interruption of earnings occurs

Clock dict: {kind, label, due, days_left, status ok|due_soon|overdue|undefined, verified, source, rule_key, note, badge}.
"""
from __future__ import annotations

from datetime import date, timedelta


def _status(days_left: int | None, soon: int) -> str:
    if days_left is None:
        return "undefined"
    if days_left < 0:
        return "overdue"
    return "due_soon" if days_left <= soon else "ok"


def _clock(kind: str, label: str, due: date | None, today: date, *, verified: bool, source: str | None, rule_key: str | None,
           note: str | None = None, soon: int = 3) -> dict:
    dl = (due - today).days if due else None
    return {"kind": kind, "label": label, "due": due.isoformat() if due else None, "days_left": dl, "status": _status(dl, soon),
            "verified": bool(verified), "source": source, "rule_key": rule_key, "note": note,
            "badge": None if verified else "estimate — confirm with the provincial employment standards office"}


def sin_request(doj: date, today: date) -> dict:
    return _clock("sin_request", "Get the employee's SIN within 3 days of the start date", doj + timedelta(days=3), today, verified=True,
                  source="Canada Revenue Agency — Hiring employees (Social Insurance Number)", rule_key="cra.sin_within_3_days", soon=1)


def final_pay(province: str | None, last_day: date, next_pay_day: date | None, today: date) -> dict:
    """Ontario: the later of 7 days after employment ends or the employee's next regular pay day (ESA 2000 s.11(5)).
    Elsewhere: the next regular pay day, shown as an estimate until the province's own rule is confirmed."""
    p = (province or "").upper()
    if p == "ON":
        due = max(last_day + timedelta(days=7), next_pay_day or last_day + timedelta(days=7))
        return _clock("final_pay", "Final pay: the later of 7 days after the last day or the next regular pay day (Ontario ESA s.11(5))",
                      due, today, verified=True, source="Employment Standards Act, 2000 (Ontario), s.11(5)", rule_key="esa.on.final_pay", soon=2)
    due = next_pay_day or last_day + timedelta(days=7)
    return _clock("final_pay", f"Final pay by the next regular pay day ({p or 'province not set'})", due, today, verified=False,
                  source=None, rule_key=f"esa.{p.lower() or 'xx'}.final_pay",
                  note="Some provinces require final pay sooner (for example within days of an employer-initiated termination) — confirm the rule.",
                  soon=2)


def roe_electronic(period_end: date | None, today: date) -> dict:
    due = period_end + timedelta(days=5) if period_end else None
    return _clock("roe_electronic", "Issue the ROE within 5 calendar days after the end of the pay period of the last day (ROE Web)",
                  due, today, verified=True, source="Service Canada — Record of Employment (electronic ROE timing)", rule_key="sc.roe.5_days",
                  soon=2)
