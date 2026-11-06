"""Headcount planning — pure rules (no database). Stream D7. Tested in test_positions_logic.py."""
from __future__ import annotations

STATUSES = ("open", "filled", "frozen")
TRANSITIONS = {"open": {"filled", "frozen"}, "frozen": {"open"}, "filled": {"open"}}   # filled → open = vacate


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def can_move(cur: str, new: str) -> bool:
    return cur == new or new in TRANSITIONS.get(cur, set())


def position_code(seq: int) -> str:
    return f"POS-{str(int(seq)).zfill(4)}"


def budget_summary(positions: list[dict], group: str = "department") -> dict:
    """positions: [{status, budgeted_ctc, actual_ctc, department, entity, grade, ...}].
    Per group: seats by status, budgeted total (all non-frozen seats), actual (filled seats' salary snapshot),
    variance = budgeted − actual on FILLED seats (over / under hire cost), open budget = budget of open seats."""
    g: dict[str, dict] = {}
    for p in positions:
        k = str(p.get(group) or "—")
        r = g.setdefault(k, {"group": k, "open": 0, "filled": 0, "frozen": 0, "total": 0, "budgeted": 0.0, "actual": 0.0,
                             "open_budget": 0.0, "filled_budget": 0.0, "variance": 0.0})
        st = p.get("status") or "open"
        r[st if st in STATUSES else "open"] += 1
        r["total"] += 1
        b = _f(p.get("budgeted_ctc"))
        if st != "frozen":
            r["budgeted"] += b
        if st == "open":
            r["open_budget"] += b
        if st == "filled":
            r["filled_budget"] += b
            r["actual"] += _f(p.get("actual_ctc"))
    rows = sorted(g.values(), key=lambda r: r["group"])
    for r in rows:
        r["variance"] = round(r["filled_budget"] - r["actual"], 2)
        r["fill_pct"] = round(r["filled"] * 100 / (r["open"] + r["filled"])) if (r["open"] + r["filled"]) else None
        for k in ("budgeted", "actual", "open_budget", "filled_budget"):
            r[k] = round(r[k], 2)
    tot = {"group": "All", "open": sum(r["open"] for r in rows), "filled": sum(r["filled"] for r in rows),
           "frozen": sum(r["frozen"] for r in rows), "total": sum(r["total"] for r in rows),
           "budgeted": round(sum(r["budgeted"] for r in rows), 2), "actual": round(sum(r["actual"] for r in rows), 2),
           "open_budget": round(sum(r["open_budget"] for r in rows), 2), "filled_budget": round(sum(r["filled_budget"] for r in rows), 2)}
    tot["variance"] = round(tot["filled_budget"] - tot["actual"], 2)
    tot["fill_pct"] = round(tot["filled"] * 100 / (tot["open"] + tot["filled"])) if (tot["open"] + tot["filled"]) else None
    return {"by": group, "rows": rows, "total": tot}


def over_budget(budgeted: float, actual: float, tolerance_pct: float = 0.0) -> bool:
    """A filled seat whose holder costs more than its budget (beyond the tolerance)."""
    b, a = _f(budgeted), _f(actual)
    if b <= 0:
        return a > 0
    return a > b * (1 + _f(tolerance_pct) / 100)


def validate(body: dict) -> list[str]:
    errs = []
    if not str(body.get("title") or "").strip():
        errs.append("Give the position a title")
    if _f(body.get("budgeted_ctc")) < 0:
        errs.append("Budgeted salary cannot be negative")
    if body.get("status") and body["status"] not in STATUSES:
        errs.append("Status must be open, filled or frozen")
    return errs
