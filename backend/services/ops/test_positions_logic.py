"""Headcount planning maths (Stream D7) — pure, no database.
Run:  cd backend/services/ops && PYTHONPATH=../../ python -m pytest -q test_positions_logic.py
"""
import positions_logic as PL

POS = [{"department": "Plant", "entity": "A", "status": "filled", "budgeted_ctc": 600000, "actual_ctc": 650000},
       {"department": "Plant", "entity": "A", "status": "open", "budgeted_ctc": 500000, "actual_ctc": None},
       {"department": "Plant", "entity": "B", "status": "frozen", "budgeted_ctc": 900000, "actual_ctc": None},
       {"department": "Office", "entity": "B", "status": "filled", "budgeted_ctc": 400000, "actual_ctc": 380000}]


def test_budget_summary_by_department():
    s = PL.budget_summary(POS, "department")
    plant = next(r for r in s["rows"] if r["group"] == "Plant")
    assert (plant["open"], plant["filled"], plant["frozen"], plant["total"]) == (1, 1, 1, 3)
    assert plant["budgeted"] == 1100000.0           # frozen seat excluded
    assert plant["open_budget"] == 500000.0 and plant["filled_budget"] == 600000.0 and plant["actual"] == 650000.0
    assert plant["variance"] == -50000.0            # holder costs more than the seat's budget
    assert plant["fill_pct"] == 50
    t = s["total"]
    assert t["total"] == 4 and t["budgeted"] == 1500000.0 and t["actual"] == 1030000.0 and t["variance"] == -30000.0 and t["fill_pct"] == 67


def test_budget_summary_by_entity_and_empty():
    s = PL.budget_summary(POS, "entity")
    assert [r["group"] for r in s["rows"]] == ["A", "B"]
    assert next(r for r in s["rows"] if r["group"] == "B")["budgeted"] == 400000.0
    e = PL.budget_summary([], "department")
    assert e["rows"] == [] and e["total"]["fill_pct"] is None and e["total"]["budgeted"] == 0.0


def test_transitions_codes_and_validation():
    assert PL.can_move("open", "filled") and PL.can_move("open", "frozen") and PL.can_move("frozen", "open") and PL.can_move("filled", "open")
    assert not PL.can_move("frozen", "filled") and not PL.can_move("filled", "frozen") and PL.can_move("open", "open")
    assert PL.position_code(7) == "POS-0007"
    assert PL.over_budget(600000, 650000) and not PL.over_budget(600000, 600000) and PL.over_budget(0, 1)
    assert not PL.over_budget(600000, 620000, tolerance_pct=5) and PL.over_budget(600000, 640000, tolerance_pct=5)
    assert PL.validate({"title": " ", "budgeted_ctc": -1, "status": "weird"}) == ["Give the position a title", "Budgeted salary cannot be negative",
                                                                                   "Status must be open, filled or frozen"]
    assert PL.validate({"title": "Fitter", "budgeted_ctc": 300000, "status": "open"}) == []
