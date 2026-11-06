"""Report builder — custom-field columns (D1) and the three new datasets.

Run:  cd backend/services/approvals && PYTHONPATH=../../ python -m pytest -q test_report_builder_custom.py
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import report_builder as rb  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
DEFS = [{"entity": "employee", "key": "blood_group", "label": "Blood group", "type": "select"},
        {"entity": "employee", "key": "shirt_size", "label": "Shirt size", "type": "text"},
        {"entity": "employee", "key": "laptop_budget", "label": "Laptop budget", "type": "number"},
        {"entity": "employee", "key": "visa_expiry", "label": "Visa expiry", "type": "date"},
        {"entity": "asset", "key": "insured_till", "label": "Insured till", "type": "date"}]


def test_custom_columns_join_the_catalog_per_entity():
    cat = {d["key"]: d for d in rb.catalog("hr", DEFS)}
    emp_keys = [c["key"] for c in cat["employees"]["columns"] if c["custom"]]
    assert emp_keys == ["cf_blood_group", "cf_shirt_size", "cf_laptop_budget", "cf_visa_expiry"]
    assert [c["key"] for c in cat["assets"]["columns"] if c["custom"]] == ["cf_insured_till"]
    assert not [c for c in cat["leave"]["columns"] if c["custom"]]                  # no custom entity → none
    assert next(c for c in cat["employees"]["columns"] if c["key"] == "cf_blood_group")["label"] == "Blood group (custom)"
    assert not [c for c in rb.catalog("hr")[0]["columns"] if c["custom"]]          # no definitions → unchanged catalog


def test_custom_columns_select_filter_group_and_aggregate():
    q = rb.build_query({"dataset": "employees", "columns": ["employee_number", "cf_blood_group", "cf_laptop_budget"],
                        "filters": [{"col": "cf_blood_group", "op": "eq", "value": "O+"}, {"col": "cf_laptop_budget", "op": "gte", "value": "50000"},
                                    {"col": "cf_visa_expiry", "op": "lt", "value": "2027-01-01"}],
                        "sort": [{"col": "cf_blood_group", "dir": "asc"}]}, role="hr", org=ORG, me=None, custom_defs=DEFS)
    assert "(e.custom_data->>'blood_group') AS cf_blood_group" in q["sql"]
    assert "CAST(NULLIF((e.custom_data->>'laptop_budget'), '') AS numeric) AS cf_laptop_budget" in q["sql"]
    assert "CAST(NULLIF((e.custom_data->>'visa_expiry'), '') AS date) <" in q["sql"]
    assert q["params"]["p0"] == "O+" and q["params"]["p1"] == 50000.0 and str(q["params"]["p2"]) == "2027-01-01"
    assert "ORDER BY cf_blood_group ASC" in q["sql"]
    g = rb.build_query({"dataset": "employees", "group_by": ["cf_shirt_size"], "aggregates": [{"fn": "avg", "col": "cf_laptop_budget"}]},
                       role="hr", org=ORG, me=None, custom_defs=DEFS)
    assert "GROUP BY (e.custom_data->>'shirt_size')" in g["sql"] and "AVG(CAST(NULLIF((e.custom_data->>'laptop_budget')" in g["sql"]
    assert [c["key"] for c in g["columns"]] == ["cf_shirt_size", "avg_cf_laptop_budget", "count"]


def test_custom_columns_are_not_there_without_definitions_or_for_other_entities():
    with pytest.raises(rb.SpecError):
        rb.build_query({"dataset": "employees", "columns": ["cf_blood_group"]}, role="hr", org=ORG, me=None)
    with pytest.raises(rb.SpecError):                       # an asset field cannot be selected on the employees dataset
        rb.build_query({"dataset": "employees", "columns": ["cf_insured_till"]}, role="hr", org=ORG, me=None, custom_defs=DEFS)
    with pytest.raises(rb.SpecError):                       # keys are validated before they touch SQL
        rb.custom_column({"key": "x'; DROP TABLE employees; --", "label": "bad", "type": "text"}, "e")


def test_new_datasets_scope_and_money():
    assert {"candidates", "assets"} <= {d["key"] for d in rb.catalog("hr")}
    assert not {"candidates", "assets"} & {d["key"] for d in rb.catalog("manager")}   # office only
    assert "expenses" not in {d["key"] for d in rb.catalog("hr")}                     # no expense-claim module
    q = rb.build_query({"dataset": "assets", "columns": ["item", "status", "cf_insured_till"]}, role="hr", org=ORG, me=None, custom_defs=DEFS)
    assert "FROM employee_assets a" in q["sql"] and "(a.custom_data->>'insured_till')" in q["sql"]
