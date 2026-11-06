"""D7 report-builder datasets: learning, surveys, positions, attrition, retention actions.
Run:  cd backend/services/approvals && PYTHONPATH=../../ python -m pytest -q test_report_builder_d7.py
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import report_builder as rb  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
D7 = {"learning", "surveys", "positions", "attrition", "retention_actions"}


def test_d7_datasets_registered_for_office_roles_not_managers():
    assert D7 <= set(rb.DATASETS)
    assert D7 <= {d["key"] for d in rb.catalog("hr")}
    assert D7 <= {d["key"] for d in rb.catalog("org_admin")}
    assert not (D7 & {d["key"] for d in rb.catalog("manager")})          # manager catalog unchanged (team views live on their screens)
    for k in D7:
        with pytest.raises(rb.SpecError):
            rb.build_query({"dataset": k, "columns": [rb.DATASETS[k]["columns"][0]["key"]]}, role="manager", org=ORG, me="emp-1")


def test_money_columns_hidden_from_hr_on_positions_and_attrition():
    assert "budgeted_ctc" not in {c["key"] for c in rb.columns_for("positions", "hr")}
    assert {"budgeted_ctc", "actual_ctc", "variance"} <= {c["key"] for c in rb.columns_for("positions", "payroll")}
    assert "annual_salary" not in {c["key"] for c in rb.columns_for("attrition", "hr")}
    with pytest.raises(rb.SpecError):
        rb.build_query({"dataset": "positions", "columns": ["code", "budgeted_ctc"]}, role="hr", org=ORG, me=None)


def test_learning_query_is_org_scoped_and_grouped_by_course():
    q = rb.build_query({"dataset": "learning", "group_by": ["course_title"], "aggregates": [{"fn": "count"}],
                        "filters": [{"col": "status", "op": "ne", "value": "completed"}, {"col": "mandatory", "op": "eq", "value": "true"}]},
                       role="hr", org=ORG, me=None)
    assert "a.org_id = CAST(:org AS uuid)" in q["sql"] and "GROUP BY c.title" in q["sql"] and "COUNT(*) AS count" in q["sql"]
    assert q["params"]["p0"] == "completed" and q["params"]["p1"] is True
    assert [c["key"] for c in q["columns"]] == ["course_title", "count"]


def test_surveys_dataset_exposes_counts_never_answers():
    keys = {c["key"] for c in rb.DATASETS["surveys"]["columns"]}
    assert "responses" in keys and not ({"answers", "respondent_hash", "employee_id"} & keys)
    q = rb.build_query({"dataset": "surveys", "columns": ["title", "responses"], "sort": [{"col": "responses", "dir": "desc"}]}, role="hr", org=ORG, me=None)
    assert "SELECT COUNT(*) FROM eng_survey_responses" in q["sql"] and "ORDER BY responses DESC" in q["sql"]


def test_attrition_tenure_and_positions_variance_expressions():
    q = rb.build_query({"dataset": "attrition", "group_by": ["department"], "aggregates": [{"fn": "avg", "col": "tenure_months"}]}, role="hr", org=ORG, me=None)
    assert "AVG(ROUND(" in q["sql"] and "termination_date" in q["sql"]
    q = rb.build_query({"dataset": "positions", "group_by": ["department"], "aggregates": [{"fn": "sum", "col": "budgeted_ctc"}, {"fn": "sum", "col": "variance"}]},
                       role="org_admin", org=ORG, me=None)
    assert "SUM(p.budgeted_ctc) AS sum_budgeted_ctc" in q["sql"] and "COALESCE(p.actual_ctc, 0)" in q["sql"]
    q = rb.build_query({"dataset": "retention_actions", "columns": ["employee_name", "status"], "filters": [{"col": "status", "op": "eq", "value": "open"}]},
                       role="hr", org=ORG, me=None)
    assert "r.org_id = CAST(:org AS uuid)" in q["sql"] and q["params"]["p0"] == "open"
