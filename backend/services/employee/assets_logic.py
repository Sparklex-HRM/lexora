"""Asset register — pure rules (Stream G4). Extends Stream C's simple `employee_assets` list."""
from __future__ import annotations

from datetime import date

CATEGORIES = ["Laptop", "Phone", "SIM", "ID card", "Access card", "Uniform", "Tools", "Vehicle", "Furniture", "Other"]
STATES = ("in_stock", "assigned", "returned", "lost", "retired")
CONDITIONS = ("good", "fair", "damaged", "not_working", "lost")


def derive_status(employee_id, issued_on: date | None, returned_on: date | None, stored: str | None) -> str:
    """Keep the register honest against the older `/exit/assets` routes, which only know
    issued_on / returned_on: a returned date wins, then an assignment, else what is stored."""
    if stored in ("lost", "retired"):
        return stored
    if returned_on is not None:
        return "returned"
    if employee_id is not None and (issued_on is not None or stored == "assigned"):
        return "assigned"
    return stored if stored in STATES else "in_stock"


def warranty_state(warranty_until: date | None, today: date) -> str:
    if warranty_until is None:
        return "unknown"
    if warranty_until < today:
        return "expired"
    if (warranty_until - today).days <= 30:
        return "expiring"
    return "ok"


def outstanding(assets: list[dict]) -> list[dict]:
    """Assets still with the employee (the exit clearance line reads this)."""
    return [a for a in assets if a.get("status") == "assigned" and not a.get("returned_on")]


def clearance_summary(assets: list[dict]) -> dict:
    out = outstanding(assets)
    return {"outstanding": len(out), "unacknowledged": sum(1 for a in assets if a.get("status") == "assigned" and not a.get("ack_at")),
            "items": [f"{a.get('item')}{(' (' + a['tag'] + ')') if a.get('tag') else ''}" for a in out],
            "cost_outstanding": round(sum(float(a.get("purchase_cost") or 0) for a in out), 2),
            "clear": not out}


def validate_return(condition: str | None, returned_on: date | None, issued_on: date | None, today: date) -> str | None:
    if condition and condition not in CONDITIONS:
        return "Condition must be good, fair, damaged, not working or lost"
    if returned_on and issued_on and returned_on < issued_on:
        return "The return date cannot be before the issue date"
    if returned_on and returned_on > today:
        return "The return date cannot be in the future"
    return None


def register_summary(assets: list[dict], today: date) -> dict:
    by_status: dict[str, int] = {s: 0 for s in STATES}
    by_cat: dict[str, int] = {}
    cost = 0.0
    expiring = 0
    for a in assets:
        by_status[a.get("status") or "in_stock"] = by_status.get(a.get("status") or "in_stock", 0) + 1
        by_cat[a.get("category") or "Other"] = by_cat.get(a.get("category") or "Other", 0) + 1
        cost += float(a.get("purchase_cost") or 0)
        if a.get("warranty_until") and warranty_state(a["warranty_until"], today) == "expiring":
            expiring += 1
    return {"total": len(assets), "by_status": by_status, "by_category": by_cat, "total_cost": round(cost, 2),
            "warranty_expiring_30d": expiring}
