"""Stream G2 — PSA arithmetic (pure): rate resolution, line amounts, available hours,
utilisation, bench, invoice grouping, leakage, FX, numbering, due dates."""
from datetime import date

import psa_logic as L


def _card(**kw):
    base = {"id": kw.pop("id", "c"), "rate": 100, "unit": "hour", "currency": "CAD", "effective_from": "2026-01-01",
            "effective_to": None, "is_active": True}
    base.update(kw)
    return base


def test_most_specific_card_wins():
    cards = [_card(id="client", client="Acme", rate=100),
             _card(id="proj", project_id="p1", rate=120),
             _card(id="emp", employee_id="e1", rate=150),
             _card(id="emp_proj", employee_id="e1", project_id="p1", rate=200),
             _card(id="role", role="Senior Developer", rate=90)]
    pick = L.resolve_rate(cards, employee_id="e1", project_id="p1", client="Acme", role="Senior Developer", on="2026-05-05")
    assert pick["id"] == "emp_proj"
    pick = L.resolve_rate(cards, employee_id="e2", project_id="p1", client="Acme", on="2026-05-05")
    assert pick["id"] == "proj"
    pick = L.resolve_rate(cards, employee_id="e2", project_id="p9", client="Acme", on="2026-05-05")
    assert pick["id"] == "client"
    assert L.resolve_rate(cards, employee_id="e2", project_id="p9", client="Other", on="2026-05-05") is None


def test_effective_dates_and_latest_wins():
    cards = [_card(id="old", client="Acme", rate=100, effective_from="2026-01-01", effective_to="2026-06-30"),
             _card(id="new", client="Acme", rate=130, effective_from="2026-07-01")]
    assert L.resolve_rate(cards, client="Acme", on="2026-06-30")["id"] == "old"
    assert L.resolve_rate(cards, client="Acme", on="2026-07-01")["id"] == "new"
    assert L.resolve_rate([_card(client="Acme", is_active=False)], client="Acme", on="2026-07-01") is None


def test_line_amount_hour_and_day():
    assert L.line_amount(7.5, 100, "hour") == 750.0
    assert L.line_amount(4, 8000, "day", hours_per_day=8) == 4000.0


def test_available_hours_and_utilisation():
    # Oct 2026: 22 weekdays × 8 h = 176
    a, b = L.month_bounds("2026-10")
    assert L.available_hours(a, b, [0, 1, 2, 3, 4], 8) == 176.0
    # joined 19 Oct (Mon) → 10 weekdays
    assert L.available_hours(a, b, [0, 1, 2, 3, 4], 8, join_date="2026-10-19") == 80.0
    emps = [{"id": "e1", "first_name": "A", "last_name": "B", "department": "Eng"},
            {"id": "e2", "first_name": "C", "last_name": "D", "department": "Eng", "hire_date": "2026-10-19"}]
    rows = L.utilisation_rows(emps, {"e1": {"billable": 132, "non_billable": 20}, "e2": {"billable": 40}}, a, b, [0, 1, 2, 3, 4], 8)
    assert rows[0]["utilisation_pct"] == 75.0 and rows[0]["productive_pct"] == 86.36
    assert rows[1]["available"] == 80.0 and rows[1]["utilisation_pct"] == 50.0
    team = L.team_rollup(rows)
    assert team[0]["people"] == 2 and team[0]["utilisation_pct"] == round(172 * 100 / 256, 2)


def test_bench():
    emps = [{"id": "e1", "first_name": "A", "last_name": "B"}, {"id": "e2", "first_name": "C", "last_name": "D"},
            {"id": "e3", "first_name": "X", "last_name": "Y", "status": "terminated"}]
    asg = [{"employee_id": "e1", "project_name": "P1", "allocation_pct": 60, "start_date": "2026-01-01", "end_date": None},
           {"employee_id": "e1", "project_name": "P2", "allocation_pct": 40, "start_date": "2026-01-01", "end_date": "2026-09-30"}]
    rows = L.bench_rows(emps, asg, date(2026, 10, 13))
    assert [r["employee_id"] for r in rows] == ["e2", "e1"]          # most free first; leaver excluded
    assert rows[1]["allocated_pct"] == 60 and rows[1]["free_pct"] == 40 and rows[1]["projects"] == "P1"
    assert rows[0]["fully_on_bench"] is True
    assert L.bench_rows(emps, asg, date(2026, 9, 1))[0]["employee_id"] == "e2" and len(L.bench_rows(emps, asg, date(2026, 9, 1))) == 1


def _line(i, emp="e1", proj="p1", hours=8, **kw):
    d = {"id": f"l{i}", "employee_id": emp, "employee_name": "Asha Rao", "project_id": proj, "project_name": "Portal",
         "hours": hours, "billable": True, "work_date": f"2026-10-{i:02d}", "bill_rate": None}
    d.update(kw)
    return d


def test_price_and_group_lines_snapshot_kept():
    cards = [_card(id="c1", client="Acme", rate=100)]
    lines = [_line(1), _line(2, hours=4), _line(3, bill_rate=90, bill_unit="hour", bill_currency="CAD"),
             _line(4, proj="p2", project_name="Other")]
    priced = L.price_lines(lines, cards, hours_per_day=8, client=None, default_currency="CAD",
                           project_client={"p1": "Acme"})
    assert priced[0]["rate_source"] == "card" and priced[0]["amount"] == 800.0
    assert priced[2]["rate_source"] == "snapshot" and priced[2]["rate"] == 90 and priced[2]["amount"] == 720.0
    assert priced[3]["missing_rate"] is True and priced[3]["amount"] == 0.0
    g = L.group_tm_lines(priced)
    assert len(g) == 3                                   # (e1,p1,100) (e1,p1,90) (e1,p2,0)
    main = next(x for x in g if x["rate"] == 100)
    assert main["hours"] == 12 and main["amount"] == 1200.0 and set(main["line_ids"]) == {"l1", "l2"}
    assert main["period_start"] == date(2026, 10, 1) and main["period_end"] == date(2026, 10, 2)
    assert "12 h at CAD 100.00/hour" in main["description"]
    tot = L.invoice_totals(g, 18)
    assert tot == {"amount": 1920.0, "tax": 345.6, "total": 2265.6}


def test_invoice_number_and_due_date():
    assert L.next_invoice_number([], 2026) == "INV-2026-0001"
    assert L.next_invoice_number(["INV-2026-0001", "INV-2026-0002", "INV-2025-0009"], 2026) == "INV-2026-0003"
    assert L.due_date(date(2026, 10, 1), "Net 30") == date(2026, 10, 31)
    assert L.due_date(date(2026, 10, 1), "Due on receipt") == date(2026, 10, 1)
    assert L.due_date(date(2026, 10, 1), None) == date(2026, 10, 31)


def test_leakage_and_fx_and_revenue():
    priced = [{"client": "Acme", "project_name": "Portal", "employee_name": "Asha", "hours": 8, "amount": 800, "missing_rate": False},
              {"client": "Acme", "project_name": "Portal", "employee_name": "Bala", "hours": 4, "amount": 0, "missing_rate": True}]
    invs = [{"id": "i1", "name": "INV-1", "status": "sent", "total": 1000, "paid_amount": 300},
            {"id": "i2", "name": "INV-2", "status": "paid", "total": 500, "paid_amount": 500},
            {"id": "i3", "name": "INV-3", "status": "draft", "total": 200, "paid_amount": 0}]
    lk = L.leakage_summary(priced, invs)
    assert lk["uninvoiced"] == {"hours": 12.0, "amount": 800.0, "lines": 2, "missing_rate": 1}
    assert lk["by_client"][0]["client"] == "Acme" and lk["by_client"][0]["missing_rate"] == 1
    assert [u["name"] for u in lk["invoiced_unpaid"]] == ["INV-1"] and lk["outstanding_total"] == 700.0
    assert L.fx_convert(100, "USD", "CAD", {"USD": 1.37}) == (137.0, True)
    assert L.fx_convert(100, "CAD", "CAD", {}) == (100.0, True)
    assert L.fx_convert(100, "EUR", "CAD", {}) == (100.0, False)
    lines = [{"invoice_id": "i1", "amount": 100, "currency": "USD", "hours": 1, "project_name": "Portal"},
             {"invoice_id": "i2", "amount": 500, "currency": "CAD", "hours": 5, "project_name": "Portal"},
             {"invoice_id": "i4", "amount": 10, "currency": "EUR", "hours": 1, "project_name": "Other"}]
    inv_map = {"i1": {"client": "Acme", "issue_date": "2026-10-01", "status": "sent"},
               "i2": {"client": "Acme", "issue_date": "2026-09-15", "status": "paid"},
               "i4": {"client": "Zed", "issue_date": "2026-10-20", "status": "sent"}}
    rows = L.revenue_rows(lines, inv_map, "CAD", {"USD": 80}, "client")
    acme = next(r for r in rows if r["client"] == "Acme")
    assert acme["amount"] == 8500.0 and acme["paid"] == 500.0 and acme["invoices"] == 2 and acme["unconverted_lines"] == 0
    zed = next(r for r in rows if r["client"] == "Zed")
    assert zed["unconverted_lines"] == 1
    by_month = L.revenue_rows(lines, inv_map, "CAD", {"USD": 80}, "month")
    assert [r["month"] for r in by_month] == ["2026-09", "2026-10"]
