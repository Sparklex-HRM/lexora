"""Report builder — the safe query builder, the file writers, schedule arithmetic.

Run:  cd backend/services/approvals && python -m pytest -q test_report_builder.py
"""
import io
import os
import sys
import zipfile
from datetime import date, datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import report_builder as rb  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"


def test_org_scope_always_and_manager_scope_for_managers():
    q = rb.build_query({"dataset": "employees", "columns": ["employee_number", "department"]}, role="hr", org=ORG, me=None)
    assert "e.org_id = CAST(:org AS uuid)" in q["sql"] and q["params"]["org"] == ORG and "me" not in q["params"]
    q = rb.build_query({"dataset": "employees", "columns": ["employee_number"]}, role="manager", org=ORG, me="emp-1")
    assert "e.manager_id = CAST(:me AS uuid)" in q["sql"] and q["params"]["me"] == "emp-1"
    with pytest.raises(rb.SpecError):                                         # a manager with no employee link gets nothing
        rb.build_query({"dataset": "employees", "columns": ["employee_number"]}, role="manager", org=ORG, me=None)


def test_money_columns_only_for_pay_roles():
    assert "annual_salary" not in {c["key"] for c in rb.columns_for("employees", "hr")}
    assert "annual_salary" in {c["key"] for c in rb.columns_for("employees", "payroll")}
    with pytest.raises(rb.SpecError):
        rb.build_query({"dataset": "employees", "columns": ["annual_salary"]}, role="hr", org=ORG, me=None)
    with pytest.raises(rb.SpecError):                                         # cannot filter on it either
        rb.build_query({"dataset": "employees", "columns": ["employee_number"], "filters": [{"col": "annual_salary", "op": "gt", "value": 1}]}, role="hr", org=ORG, me=None)
    with pytest.raises(rb.SpecError):                                         # payroll datasets are closed to hr
        rb.build_query({"dataset": "payroll_lines", "columns": ["name"]}, role="hr", org=ORG, me=None)
    assert {d["key"] for d in rb.catalog("manager")} == {"employees", "attendance", "leave", "performance"}
    assert "payroll_lines" in {d["key"] for d in rb.catalog("org_admin")}


def test_filters_are_parameterised_never_inlined():
    spec = {"dataset": "leave", "columns": ["name", "status", "days"],
            "filters": [{"col": "status", "op": "eq", "value": "approved'; DROP TABLE x; --"},
                        {"col": "name", "op": "contains", "value": "ra"},
                        {"col": "start_date", "op": "between", "value": ["2026-01-01", "2026-03-31"]},
                        {"col": "leave_type_name", "op": "in", "value": "Casual, Sick"},
                        {"col": "reviewed_by_name", "op": "is_null"}],
            "sort": [{"col": "days", "dir": "desc"}, {"col": "nope", "dir": "asc"}], "limit": 99999}
    q = rb.build_query(spec, role="hr", org=ORG, me=None)
    assert "DROP" not in q["sql"] and q["params"]["p0"] == "approved'; DROP TABLE x; --"
    assert q["params"]["p1"] == "%ra%" and q["params"]["p2"] == date(2026, 1, 1) and q["params"]["p4"] == "Casual"
    assert "IS NULL" in q["sql"] and "ORDER BY days DESC NULLS LAST" in q["sql"] and "nope" not in q["sql"]
    assert q["sql"].endswith(f"LIMIT {rb.MAX_ROWS}") and q["limit"] == rb.MAX_ROWS
    with pytest.raises(rb.SpecError):
        rb.build_query({"dataset": "leave", "columns": ["name"], "filters": [{"col": "days", "op": "gt", "value": "abc"}]}, role="hr", org=ORG, me=None)
    with pytest.raises(rb.SpecError):
        rb.build_query({"dataset": "nope", "columns": ["name"]}, role="hr", org=ORG, me=None)


def test_group_by_and_aggregates():
    q = rb.build_query({"dataset": "payroll_lines", "group_by": ["department", "pay_month"],
                        "aggregates": [{"fn": "sum", "col": "gross"}, {"fn": "avg", "col": "net_pay"}, {"fn": "count"}]},
                       role="payroll", org=ORG, me=None)
    assert "GROUP BY e.department, to_char(r.pay_date, 'YYYY-MM')" in q["sql"]
    assert [c["key"] for c in q["columns"]] == ["department", "pay_month", "sum_gross", "avg_net_pay", "count"]
    assert q["grouped"]
    q2 = rb.build_query({"dataset": "employees", "group_by": ["department"]}, role="hr", org=ORG, me=None)
    assert "COUNT(*) AS count" in q2["sql"]                                   # a bare group-by counts
    with pytest.raises(rb.SpecError):                                         # sum of a text column
        rb.build_query({"dataset": "employees", "group_by": ["department"], "aggregates": [{"fn": "sum", "col": "status"}]}, role="hr", org=ORG, me=None)


def test_csv_and_xlsx_writers():
    cols = [{"key": "name", "label": "Name", "type": "text"}, {"key": "days", "label": "Days", "type": "number"}, {"key": "d", "label": "Date", "type": "date"}]
    rows = [{"name": "=cmd()", "days": 2.5, "d": date(2026, 1, 2)}, {"name": "A, B", "days": None, "d": None}]
    csv = rb.to_csv(cols, rows)
    assert csv.startswith("﻿Name,Days,Date") and "'=cmd()" in csv and '"A, B"' in csv
    data = rb.to_xlsx(cols, rows, sheet="Leave")
    z = zipfile.ZipFile(io.BytesIO(data))
    assert set(z.namelist()) >= {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml"}
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert ">Name</t>" in sheet and "<v>2.5</v>" in sheet and "2026-01-02" in sheet and "=cmd()" in sheet


def test_next_run():
    after = datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc)                 # a Tuesday
    assert rb.next_run("daily", 6, 0, 1, after) == datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
    assert rb.next_run("daily", 9, 0, 1, after) == datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    assert rb.next_run("weekly", 6, 0, 1, after) == datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)   # next Monday
    assert rb.next_run("weekly", 6, 1, 1, after) == datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)   # Tuesday 06:00 already passed → next week
    assert rb.next_run("monthly", 6, 0, 1, after) == datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
    assert rb.next_run("monthly", 6, 0, 1, datetime(2026, 12, 2, 0, 0, tzinfo=timezone.utc)) == datetime(2027, 1, 1, 6, 0, tzinfo=timezone.utc)


# ── 2026-10-25: schedule send time on the org's wall clock; report dates in the org zone ──
def test_next_run_local_is_toronto_wall_clock():
    after = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)                 # Tue 13:00 EDT
    # daily 09:00 Toronto → Wed 14 Oct 09:00 EDT = 13:00 UTC
    assert rb.next_run_local("daily", "09:00", "America/Toronto", 0, 1, after) == datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc)
    # Sunday 22:00 Vancouver is Monday 05:00 UTC — the send day follows the org's wall clock, not UTC
    assert rb.next_run_local("weekly", "22:00", "America/Vancouver", 6, 1, after) == datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
    # monthly on the 1st at 00:30 (default zone Toronto) → 1 Nov 00:30 EDT = 04:30 UTC
    assert rb.next_run_local("monthly", "00:30", None, 0, 1, after) == datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc)
    # December rolls into January (EST, UTC-5)
    assert rb.next_run_local("monthly", "09:00", "America/Toronto", 0, 1, datetime(2026, 12, 2, 0, 0, tzinfo=timezone.utc))         == datetime(2027, 1, 1, 14, 0, tzinfo=timezone.utc)


def test_schedule_next_falls_back_to_legacy_hour_utc():
    class S:
        cadence, hour_utc, weekday, day_of_month, local_time, tz = "daily", 6, 0, 1, None, None
    after = datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc)
    assert rb.schedule_next(S, after) == datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
    S.local_time, S.tz = "09:00", "America/Toronto"
    assert rb.schedule_next(S, after) == datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)   # 03:30 EDT → 09:00 the same day
    assert not rb.valid_local_time("9:00") and not rb.valid_local_time("24:00") and rb.valid_local_time("23:30")


def test_report_dates_use_the_org_zone_literal():
    q = rb.build_query({"dataset": "leave", "columns": ["created_at"]}, role="hr", org=ORG, me=None)
    assert "AT TIME ZONE 'America/Toronto' AS date" in q["sql"] and "{TZ}" not in q["sql"]
    q = rb.build_query({"dataset": "leave", "columns": ["created_at"]}, role="hr", org=ORG, me=None, tz="America/Vancouver")
    assert "AT TIME ZONE 'America/Vancouver'" in q["sql"]
    q = rb.build_query({"dataset": "leave", "columns": ["created_at"]}, role="hr", org=ORG, me=None, tz="x'; drop table t;--")
    assert "AT TIME ZONE 'America/Toronto'" in q["sql"] and "drop" not in q["sql"]
