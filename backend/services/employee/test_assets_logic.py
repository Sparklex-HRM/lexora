"""Asset register rules (Stream G4) — pure."""
from datetime import date

import assets_logic as AL

D = date(2026, 10, 13)


def test_status_derivation_respects_old_exit_routes():
    assert AL.derive_status("e1", D, None, None) == "assigned"            # issued via /exit/assets, no status column yet
    assert AL.derive_status("e1", D, D, "assigned") == "returned"        # returned via /exit/assets
    assert AL.derive_status(None, None, None, None) == "in_stock"
    assert AL.derive_status("e1", D, None, "lost") == "lost"
    assert AL.derive_status(None, None, None, "retired") == "retired"
    assert AL.derive_status("e1", None, None, "assigned") == "assigned"


def test_warranty_state():
    assert AL.warranty_state(None, D) == "unknown"
    assert AL.warranty_state(date(2026, 10, 1), D) == "expired"
    assert AL.warranty_state(date(2026, 11, 1), D) == "expiring"
    assert AL.warranty_state(date(2027, 11, 1), D) == "ok"


def test_outstanding_and_clearance_summary():
    assets = [{"item": "Laptop", "tag": "LT-1", "status": "assigned", "returned_on": None, "ack_at": None, "purchase_cost": 60000},
              {"item": "ID card", "status": "assigned", "returned_on": None, "ack_at": "x", "purchase_cost": 100},
              {"item": "Phone", "status": "returned", "returned_on": "2026-10-01", "purchase_cost": 20000}]
    s = AL.clearance_summary(assets)
    assert s["outstanding"] == 2 and s["unacknowledged"] == 1 and not s["clear"]
    assert s["items"] == ["Laptop (LT-1)", "ID card"] and s["cost_outstanding"] == 60100.0
    assert AL.clearance_summary([assets[2]])["clear"]


def test_validate_return():
    assert AL.validate_return("good", D, date(2026, 1, 1), D) is None
    assert "before the issue date" in AL.validate_return("good", date(2025, 1, 1), date(2026, 1, 1), D)
    assert "future" in AL.validate_return("good", date(2027, 1, 1), None, D)
    assert "Condition" in AL.validate_return("broken", D, None, D)


def test_register_summary():
    items = [{"status": "assigned", "category": "Laptop", "purchase_cost": 50000, "warranty_until": date(2026, 10, 20)},
             {"status": "in_stock", "category": "Laptop", "purchase_cost": 45000, "warranty_until": None},
             {"status": "lost", "category": "Phone", "purchase_cost": 0, "warranty_until": None}]
    r = AL.register_summary(items, D)
    assert r["total"] == 3 and r["by_status"]["assigned"] == 1 and r["by_category"]["Laptop"] == 2
    assert r["total_cost"] == 95000.0 and r["warranty_expiring_30d"] == 1
