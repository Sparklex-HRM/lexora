"""Stream D7 datasets for the custom report builder: learning, surveys, positions, attrition, retention actions.

Registered from report_builder.py with ``DATASETS.update(d7_datasets(col, EMP_COLS, OFFICE, ORG))`` so the
registry section of that file stays a two-line change (D1 edits the same file). All five are OFFICE
datasets (HR / payroll / org admin); money columns stay payroll-only through the usual ``money=True`` flag.
"""


def d7_datasets(col, EMP_COLS, OFFICE, ORG) -> dict[str, dict]:
    return {
        "learning": {
            "title": "Learning assignments", "access": OFFICE, "description": "One row per course assignment (progress, due date, certificate).",
            "from": f"FROM eng_course_assignments a JOIN eng_courses c ON c.id = a.course_id LEFT JOIN employees e ON e.id = a.employee_id WHERE a.org_id = {ORG}",
            "manager_scope": "AND e.manager_id = CAST(:me AS uuid)",
            "columns": EMP_COLS + [col("course_title", "Course", expr="c.title"), col("category", "Category", expr="c.category"),
                                   col("mandatory", "Mandatory", expr="c.mandatory"), col("status", "Status", expr="a.status"),
                                   col("due_on", "Due", "date", "a.due_on"), col("assigned_at", "Assigned on", "date", "CAST(a.assigned_at AT TIME ZONE {TZ} AS date)"),
                                   col("completed_at", "Completed on", "date", "CAST(a.completed_at AT TIME ZONE {TZ} AS date)"),
                                   col("overdue", "Overdue", expr="(a.status <> 'completed' AND a.due_on IS NOT NULL AND a.due_on < CAST(NOW() AT TIME ZONE {TZ} AS date))"),
                                   col("quiz_score", "Quiz score", "number", "a.quiz_score"), col("quiz_attempts", "Quiz attempts", "number", "a.quiz_attempts"),
                                   col("certificate_no", "Certificate", expr="a.certificate_no"), col("assigned_by", "Assigned by", expr="a.assigned_by"),
                                   col("manager_name", "Manager", expr="e.manager_name")]},
        "surveys": {
            "title": "Surveys", "access": OFFICE, "description": "One row per survey with its response count (never individual answers).",
            "from": f"FROM eng_surveys s WHERE s.org_id = {ORG}", "manager_scope": None,
            "columns": [col("title", "Survey", expr="s.title"), col("kind", "Kind", expr="s.kind"), col("status", "Status", expr="s.status"),
                        col("anonymous", "Anonymous", expr="s.anonymous"), col("opens_on", "Opens", "date", "s.opens_on"),
                        col("closes_on", "Closes", "date", "s.closes_on"), col("threshold", "Min. responses", "number", "s.threshold"),
                        col("responses", "Responses", "number", "(SELECT COUNT(*) FROM eng_survey_responses r WHERE r.survey_id = s.id)"),
                        col("created_by", "Created by", expr="s.created_by"), col("created_at", "Created on", "date", "CAST(s.created_at AT TIME ZONE {TZ} AS date)")]},
        "positions": {
            "title": "Positions (headcount plan)", "access": OFFICE, "description": "One row per budgeted seat: open / filled / frozen, budget vs actual.",
            "from": f"FROM eng_positions p WHERE p.org_id = {ORG}", "manager_scope": "AND p.manager_id = CAST(:me AS uuid)",
            "columns": [col("code", "Position", expr="p.code"), col("title", "Title", expr="p.title"), col("grade", "Grade", expr="p.grade"),
                        col("department", "Department", expr="p.department"), col("entity", "Entity", expr="p.entity"), col("location", "Location", expr="p.location"),
                        col("fiscal_year", "FY", expr="p.fiscal_year"), col("status", "Status", expr="p.status"), col("employee_name", "Holder", expr="p.employee_name"),
                        col("filled_on", "Filled on", "date", "p.filled_on"), col("requisition_number", "Requisition", expr="p.requisition_number"),
                        col("manager_name", "Manager", expr="p.manager_name"),
                        col("budgeted_ctc", "Budgeted salary", "money", "p.budgeted_ctc", money=True),
                        col("actual_ctc", "Actual salary", "money", "p.actual_ctc", money=True),
                        col("variance", "Budget − actual", "money", "(p.budgeted_ctc - COALESCE(p.actual_ctc, 0))", money=True)]},
        "attrition": {
            "title": "Attrition (leavers)", "access": OFFICE, "description": "One row per person who has left, with tenure at exit.",
            "from": f"FROM employees e WHERE e.org_id = {ORG} AND (e.status = 'terminated' OR e.termination_date IS NOT NULL)",
            "manager_scope": "AND e.manager_id = CAST(:me AS uuid)",
            "columns": EMP_COLS + [col("job_title", "Designation", expr="e.job_title"), col("grade", "Grade", expr="e.grade"),
                                   col("work_location", "Location", expr="e.work_location"), col("manager_name", "Manager", expr="e.manager_name"),
                                   col("gender", "Gender", expr="e.gender"), col("hire_date", "Joined", "date", "e.hire_date"),
                                   col("termination_date", "Left on", "date", "e.termination_date"),
                                   col("tenure_months", "Tenure (months)", "number",
                                       "ROUND(((COALESCE(e.termination_date, CAST(NOW() AT TIME ZONE {TZ} AS date)) - e.hire_date) / 30.44)::numeric, 1)"),
                                   col("exit_month", "Exit month", expr="to_char(e.termination_date, 'YYYY-MM')"),
                                   col("annual_salary", "Annual salary", "money", "e.annual_salary", money=True)]},
        "retention_actions": {
            "title": "Retention actions", "access": OFFICE, "description": "One row per retention action logged from the flight-risk signals.",
            "from": f"FROM eng_retention_actions r WHERE r.org_id = {ORG}", "manager_scope": None,
            "columns": [col("employee_name", "Name", expr="r.employee_name"), col("department", "Department", expr="r.department"),
                        col("action", "Action", expr="r.action"), col("kind", "Kind", expr="r.kind"), col("owner_name", "Owner", expr="r.owner_name"),
                        col("due_on", "Due", "date", "r.due_on"), col("status", "Status", expr="r.status"), col("outcome", "Outcome", expr="r.outcome"),
                        col("risk_score_at", "Risk score when logged", "number", "r.risk_score_at"), col("risk_band_at", "Risk band", expr="r.risk_band_at"),
                        col("created_at", "Logged on", "date", "CAST(r.created_at AT TIME ZONE {TZ} AS date)"), col("closed_at", "Closed on", "date", "CAST(r.closed_at AT TIME ZONE {TZ} AS date)")]},
    }
