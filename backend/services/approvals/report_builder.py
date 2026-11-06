"""Custom report builder — the SAFE query builder (pure, unit-tested) + file writers.

The client never sends SQL. It sends a spec:

    {"dataset": "employees", "columns": ["employee_number", "department"],
     "filters": [{"col": "status", "op": "eq", "value": "active"}],
     "group_by": ["department"], "aggregates": [{"col": "annual_salary", "fn": "sum"}],
     "sort": [{"col": "department", "dir": "asc"}], "limit": 500}

``build_query`` turns it into parameterised SQL over a WHITELIST of columns, always
scoped to the caller's org (and to direct reports for a manager). Money columns exist
only for payroll / finance / org admin — for anyone else they are not in the dataset
at all, so they cannot be selected, filtered on or aggregated.
"""
import csv
import io
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

PEOPLE, PAY, OFFICE = "people", "pay", "office"
PAY_ROLES = {"org_admin", "superadmin", "payroll", "payroll_admin", "finance", "finance_admin"}
OFFICE_ROLES = PAY_ROLES | {"hr", "hr_admin", "hr_manager", "hr_staff"}
MAX_ROWS = 5000
OPS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
AGG = {"count", "sum", "avg", "min", "max"}
ORG = "CAST(:org AS uuid)"


def col(key, label, type_="text", expr=None, money=False):
    return {"key": key, "label": label, "type": type_, "expr": expr or key, "money": money}


EMP_COLS = [col("employee_number", "Employee no.", expr="e.employee_number"), col("name", "Name", expr="TRIM(e.first_name || ' ' || e.last_name)"),
            col("department", "Department", expr="e.department")]

DATASETS: dict[str, dict] = {
    "employees": {
        "title": "Employees", "access": PEOPLE, "description": "One row per person on record (leavers included).",
        "from": f"FROM employees e WHERE e.org_id = {ORG}", "manager_scope": "AND e.manager_id = CAST(:me AS uuid)",
        "custom_entity": ("employee", "e"),
        "columns": EMP_COLS + [
            col("job_title", "Designation", expr="e.job_title"), col("grade", "Grade", expr="e.grade"),
            col("work_location", "Location", expr="e.work_location"), col("branch_name", "Branch", expr="e.branch_name"),
            col("employment_type", "Employment type", expr="e.employment_type"), col("status", "Status", expr="e.status"),
            col("hire_date", "Joining date", "date", "e.hire_date"), col("termination_date", "Last day", "date", "e.termination_date"),
            col("probation_end_date", "Probation ends", "date", "e.probation_end_date"), col("manager_name", "Manager", expr="e.manager_name"),
            col("pay_frequency", "Pay frequency", expr="e.pay_frequency"), col("pay_type", "Pay type", expr="e.pay_type"),
            col("province_of_employment", "State / province", expr="e.province_of_employment"),
            col("address_country", "Country", expr="e.address_country"), col("gender", "Gender", expr="e.gender"),
            col("standard_hours_per_week", "Hours per week", "number", "e.standard_hours_per_week"),
            col("annual_salary", "Annual salary", "money", "e.annual_salary", money=True),
            col("hourly_rate", "Hourly rate", "money", "e.hourly_rate", money=True)]},
    "attendance": {
        "title": "Attendance days", "access": PEOPLE, "description": "One row per person per day.",
        "from": f"FROM ta_attendance_days d LEFT JOIN employees e ON e.id = d.employee_id WHERE d.org_id = {ORG}",
        "manager_scope": "AND e.manager_id = CAST(:me AS uuid)",
        "columns": EMP_COLS + [col("work_date", "Date", "date", "d.work_date"), col("status", "Status", expr="d.status"),
                               col("planned_hours", "Planned hours", "hours", "d.planned_hours"), col("actual_hours", "Actual hours", "hours", "d.actual_hours"),
                               col("late_minutes", "Late (min)", "number", "d.late_minutes"), col("is_holiday", "Holiday", expr="d.is_holiday")]},
    "leave": {
        "title": "Leave requests", "access": PEOPLE, "description": "One row per leave request.",
        "from": f"FROM leave_requests l LEFT JOIN employees e ON e.id = l.employee_id WHERE l.org_id = {ORG}",
        "manager_scope": "AND e.manager_id = CAST(:me AS uuid)",
        "columns": EMP_COLS + [col("leave_type_name", "Leave type", expr="l.leave_type_name"), col("start_date", "From", "date", "l.start_date"),
                               col("end_date", "To", "date", "l.end_date"), col("days", "Days", "number", "l.days"), col("half_day", "Half day", expr="l.half_day"),
                               col("status", "Status", expr="l.status"), col("reviewed_by_name", "Decided by", expr="l.reviewed_by_name"),
                               col("created_at", "Requested on", "date", "CAST(l.created_at AT TIME ZONE {TZ} AS date)"), col("fiscal_year", "Year", "number", "l.fiscal_year")]},
    "payroll_runs": {
        "title": "Payroll runs", "access": PAY, "description": "One row per pay run.",
        "from": f"FROM pay_runs r WHERE r.org_id = {ORG}", "manager_scope": None,
        "columns": [col("period_start", "Period from", "date", "r.period_start"), col("period_end", "Period to", "date", "r.period_end"),
                    col("pay_date", "Pay date", "date", "r.pay_date"), col("status", "Status", expr="r.status"),
                    col("total_gross", "Total gross", "money", "r.total_gross", money=True), col("total_net", "Total net", "money", "r.total_net", money=True)]},
    "payroll_lines": {
        "title": "Payroll lines", "access": PAY, "description": "One row per person per pay run.",
        "from": f"FROM pay_stubs s JOIN pay_runs r ON r.id = s.pay_run_id LEFT JOIN employees e ON e.id = s.employee_id WHERE s.org_id = {ORG} AND r.org_id = {ORG}",
        "manager_scope": None,
        "columns": EMP_COLS + [col("pay_date", "Pay date", "date", "r.pay_date"), col("pay_month", "Pay month", expr="to_char(r.pay_date, 'YYYY-MM')"),
                               col("run_status", "Run status", expr="r.status"), col("gross", "Gross", "money", "s.gross", money=True),
                               col("pf_or_cpp", "CPP / QPP", "money", "s.cpp", money=True), col("cpp2", "CPP2", "money", "s.cpp2", money=True),
                               col("esi_or_ei", "EI", "money", "s.ei", money=True), col("income_tax", "Income tax", "money", "s.income_tax", money=True),
                               col("net_pay", "Net pay", "money", "s.net_pay", money=True)]},
    "revisions": {
        "title": "Salary revisions", "access": PAY, "description": "One row per revision request.",
        "from": f"FROM comp_revisions c LEFT JOIN employees e ON e.id = c.employee_id WHERE c.org_id = {ORG}", "manager_scope": None,
        "columns": EMP_COLS + [col("effective_from", "Effective", "date", "c.effective_from"), col("reason", "Reason", expr="c.reason"),
                               col("status", "Status", expr="c.status"), col("source", "Source", expr="c.source"), col("rating", "Rating", expr="c.rating"),
                               col("pay_type", "Pay type", expr="c.pay_type"), col("old_amount", "Old amount", "money", "c.old_amount", money=True),
                               col("new_amount", "New amount", "money", "c.new_amount", money=True),
                               col("change_pct", "Change %", "percent", "CASE WHEN c.old_amount > 0 THEN ROUND((c.new_amount - c.old_amount) / c.old_amount * 100, 1) END", money=True),
                               col("created_at", "Raised on", "date", "CAST(c.created_at AT TIME ZONE {TZ} AS date)")]},
    "exits": {
        "title": "Exit cases", "access": OFFICE, "description": "One row per exit case.",
        "from": f"FROM exit_cases x WHERE x.org_id = {ORG}", "manager_scope": "AND x.manager_id = CAST(:me AS uuid)",
        "columns": [col("case_number", "Case", expr="x.case_number"), col("employee_name", "Name", expr="x.employee_name"),
                    col("employee_number", "Employee no.", expr="x.employee_number"), col("department", "Department", expr="x.department"),
                    col("exit_type", "Exit type", expr="x.exit_type"), col("status", "Status", expr="x.status"), col("initiated_by", "Initiated by", expr="x.initiated_by"),
                    col("submitted_on", "Submitted", "date", "x.submitted_on"), col("lwd", "Last working day", "date", "x.lwd"),
                    col("notice_days", "Notice days", "number", "x.notice_days"), col("notice_shortfall_days", "Notice shortfall", "number", "x.notice_shortfall_days"),
                    col("settlement_status", "Settlement", expr="x.settlement_status"), col("rehire_eligible", "Rehire eligible", expr="x.rehire_eligible"),
                    col("settlement_net", "Settlement net", "money", "x.settlement_net", money=True)]},
    "hiring": {
        "title": "Hiring requisitions", "access": OFFICE, "description": "One row per requisition.",
        "from": f"FROM hire_requisitions h WHERE h.org_id = {ORG}", "manager_scope": "AND h.hiring_manager_id = CAST(:me AS uuid)",
        "columns": [col("req_number", "Requisition", expr="h.req_number"), col("designation", "Designation", expr="h.designation"),
                    col("department", "Department", expr="h.department"), col("location", "Location", expr="h.location"),
                    col("employment_type", "Employment type", expr="h.employment_type"), col("openings", "Openings", "number", "h.openings"),
                    col("reason_type", "Reason", expr="h.reason_type"), col("status", "Status", expr="h.status"),
                    col("hiring_manager_name", "Hiring manager", expr="h.hiring_manager_name"), col("target_date", "Target date", "date", "h.target_date"),
                    col("created_at", "Raised on", "date", "CAST(h.created_at AT TIME ZONE {TZ} AS date)"), col("filled_at", "Filled on", "date", "CAST(h.filled_at AT TIME ZONE {TZ} AS date)"),
                    col("budget_min", "Budget min", "money", "h.budget_min", money=True), col("budget_max", "Budget max", "money", "h.budget_max", money=True)]},
    "performance": {
        "title": "Performance reviews", "access": PEOPLE, "description": "One row per review in a cycle.",
        "from": f"FROM perf_reviews p WHERE p.org_id = {ORG}", "manager_scope": "AND p.manager_id = CAST(:me AS uuid)",
        "columns": [col("employee_name", "Name", expr="p.employee_name"), col("employee_number", "Employee no.", expr="p.employee_number"),
                    col("department", "Department", expr="p.department"), col("designation", "Designation", expr="p.designation"),
                    col("grade", "Grade", expr="p.grade"), col("manager_name", "Manager", expr="p.manager_name"), col("stage", "Stage", expr="p.stage"),
                    col("overall_score", "Score", "number", "p.overall_score"), col("final_score", "Final score", "number", "p.final_score"),
                    col("final_band", "Band", expr="p.final_band"), col("promotion_recommended", "Promotion recommended", expr="p.promotion_recommended"),
                    col("published_at", "Published", "date", "CAST(p.published_at AT TIME ZONE {TZ} AS date)"), col("ack_status", "Acknowledged", expr="p.ack_status")]},
    # D1 — the three other entities that carry custom fields
    "candidates": {
        "title": "Candidates", "access": OFFICE, "description": "One row per candidate on file (hiring).",
        "from": f"FROM hire_candidates c WHERE c.org_id = {ORG} AND c.is_active IS TRUE", "manager_scope": None,
        "custom_entity": ("candidate", "c"),
        "columns": [col("name", "Name", expr="TRIM(c.first_name || ' ' || COALESCE(c.last_name, ''))"), col("email", "E-mail", expr="c.email"),
                    col("phone", "Phone", expr="c.phone"), col("current_employer", "Current employer", expr="c.current_employer"),
                    col("current_title", "Current title", expr="c.current_title"), col("city", "City", expr="c.city"),
                    col("created_at", "Added on", "date", "CAST(c.created_at AT TIME ZONE {TZ} AS date)")]},
    "assets": {
        "title": "Assets", "access": OFFICE, "description": "One row per asset in the register.",
        "from": f"FROM employee_assets a LEFT JOIN employees e ON e.id = a.employee_id WHERE a.org_id = {ORG}", "manager_scope": None,
        "custom_entity": ("asset", "a"),
        "columns": EMP_COLS + [col("item", "Item", expr="a.item"), col("tag", "Tag", expr="a.tag"), col("category", "Category", expr="a.category"),
                               col("serial", "Serial", expr="a.serial"), col("status", "Status", expr="a.status"),
                               col("issued_on", "Issued on", "date", "a.issued_on"), col("returned_on", "Returned on", "date", "a.returned_on"),
                               col("location", "Location", expr="a.location"), col("warranty_until", "Warranty until", "date", "a.warranty_until"),
                               col("purchase_cost", "Purchase cost", "money", "a.purchase_cost", money=True)]},
}

# ── D7: learning / surveys / positions / attrition / retention datasets (report_builder_d7.py) ──
from report_builder_d7 import d7_datasets  # noqa: E402
DATASETS.update(d7_datasets(col, EMP_COLS, OFFICE, ORG))

# ── D1: custom fields (cf_definitions) become selectable columns ───────────────
CUSTOM_PREFIX = "cf_"
_CUSTOM_TYPE = {"number": "number", "date": "date"}          # everything else is text


def custom_column(defn: dict, alias: str) -> dict:
    """A cf_definitions row → a builder column over ``<alias>.custom_data->>'<key>'``."""
    key = defn["key"]
    if not _KEY.match(key):
        raise SpecError(f"Bad custom field key '{key}'")
    base = f"({alias}.custom_data->>'{key}')"
    t = _CUSTOM_TYPE.get(defn.get("type") or "text", "text")
    expr = {"number": f"CAST(NULLIF({base}, '') AS numeric)", "date": f"CAST(NULLIF({base}, '') AS date)"}.get(t, base)
    return col(CUSTOM_PREFIX + key, f"{defn.get('label') or key} (custom)", t, expr)


def custom_columns_for(dataset_key: str, defs: list[dict]) -> list[dict]:
    """The custom columns of one dataset for the given (already role-filtered) definitions."""
    ds = DATASETS.get(dataset_key) or {}
    ent = ds.get("custom_entity")
    if not ent:
        return []
    entity, alias = ent
    return [custom_column(d, alias) for d in defs if d.get("entity") == entity and d.get("active", True)]


# ══════════════════════════ access ═════════════════════════════════════════════

def can_see_pay(role: str) -> bool:
    # org RBAC 2026-10-26: a custom role acting as a pay role sees money columns only with data.pay
    from shared.utils.roles import role_holds
    return role in PAY_ROLES and role_holds(role, "pay")


def is_office(role: str) -> bool:
    return role in OFFICE_ROLES


def may_open(dataset: dict, role: str) -> bool:
    a = dataset["access"]
    if a == PAY:
        return can_see_pay(role)
    if a == OFFICE:
        return is_office(role)
    return is_office(role) or role == "manager"


def columns_for(dataset_key: str, role: str, custom_defs: list[dict] | None = None) -> list[dict]:
    ds = DATASETS[dataset_key]
    base = [c for c in ds["columns"] if not c["money"] or can_see_pay(role)]
    return base + custom_columns_for(dataset_key, custom_defs or [])


def catalog(role: str, custom_defs: list[dict] | None = None) -> list[dict]:
    out = []
    for key, ds in DATASETS.items():
        if may_open(ds, role):
            out.append({"key": key, "title": ds["title"], "description": ds["description"], "access": ds["access"],
                        "columns": [{**{k: c[k] for k in ("key", "label", "type", "money")}, "custom": c["key"].startswith(CUSTOM_PREFIX)}
                                    for c in columns_for(key, role, custom_defs)],
                        "scope": "Whole company" if is_office(role) else "Your direct reports"})
    return out


# ══════════════════════════ the query builder ══════════════════════════════════

class SpecError(ValueError):
    pass


_KEY = re.compile(r"^[a-z_][a-z0-9_]*$")


def build_query(spec: dict, *, role: str, org: str, me: str | None, custom_defs: list[dict] | None = None,
                tz: str = "America/Toronto") -> dict:
    """Returns {sql, params, columns:[{key,label,type}], grouped: bool, limit}. Raises SpecError.
    ``custom_defs`` = the org's custom-field definitions the caller's role may see (D1).
    ``tz`` = the org's IANA zone: "<timestamp> on" columns and "today" are that zone's calendar date
    (the database session runs in UTC; stored instants stay UTC)."""
    key = spec.get("dataset")
    if key not in DATASETS:
        raise SpecError("Pick a dataset")
    ds = DATASETS[key]
    if not may_open(ds, role):
        raise SpecError("Your role cannot open this dataset")
    allowed = {c["key"]: c for c in columns_for(key, role, custom_defs)}
    cols = [c for c in (spec.get("columns") or []) if isinstance(c, str)]
    group_by = [c for c in (spec.get("group_by") or []) if isinstance(c, str)]
    aggs = [a for a in (spec.get("aggregates") or []) if isinstance(a, dict)]
    for c in cols + group_by:
        if c not in allowed:
            raise SpecError(f"Unknown column '{c}'")
    params: dict = {"org": org}
    where = [ds["from"]]
    if not is_office(role):
        if not ds.get("manager_scope") or not me:
            raise SpecError("This dataset is not available for your scope")
        where.append(ds["manager_scope"])
        params["me"] = me
    # filters
    n = 0
    for f in spec.get("filters") or []:
        if not isinstance(f, dict):
            continue
        c, op = f.get("col"), f.get("op")
        if c not in allowed:
            raise SpecError(f"Unknown filter column '{c}'")
        expr = allowed[c]["expr"]
        v = f.get("value")
        if op in OPS:
            if v in (None, ""):
                raise SpecError(f"A value is needed for {c}")
            p = f"p{n}"; n += 1
            params[p] = _typed(allowed[c]["type"], v)
            where.append(f"AND {expr} {OPS[op]} :{p}")
        elif op in ("contains", "starts"):
            p = f"p{n}"; n += 1
            params[p] = (str(v) + "%") if op == "starts" else ("%" + str(v) + "%")
            where.append(f"AND CAST({expr} AS text) ILIKE :{p}")
        elif op == "in":
            vals = v if isinstance(v, list) else [x.strip() for x in str(v or "").split(",") if x.strip()]
            if not vals:
                raise SpecError(f"A list is needed for {c}")
            names = []
            for x in vals[:50]:
                p = f"p{n}"; n += 1
                params[p] = _typed(allowed[c]["type"], x)
                names.append(f":{p}")
            where.append(f"AND {expr} IN ({', '.join(names)})")
        elif op == "between":
            vals = v if isinstance(v, list) else str(v or "").split(",")
            if len(vals) != 2:
                raise SpecError(f"Two values are needed for {c}")
            p1, p2 = f"p{n}", f"p{n + 1}"; n += 2
            params[p1], params[p2] = _typed(allowed[c]["type"], vals[0]), _typed(allowed[c]["type"], vals[1])
            where.append(f"AND {expr} BETWEEN :{p1} AND :{p2}")
        elif op == "is_null":
            where.append(f"AND {expr} IS NULL")
        elif op == "not_null":
            where.append(f"AND {expr} IS NOT NULL")
        else:
            raise SpecError(f"Unknown filter '{op}'")
    # select
    out_cols: list[dict] = []
    if group_by:
        select = [f"{allowed[c]['expr']} AS {c}" for c in group_by]
        out_cols += [{"key": c, "label": allowed[c]["label"], "type": allowed[c]["type"]} for c in group_by]
        has_count = False
        for a in aggs:
            fn, c = (a.get("fn") or "").lower(), a.get("col")
            if fn not in AGG:
                raise SpecError(f"Unknown aggregate '{fn}'")
            if fn == "count":
                if not has_count:
                    select.append("COUNT(*) AS count")
                    out_cols.append({"key": "count", "label": "Count", "type": "number"})
                    has_count = True
                continue
            if c not in allowed:
                raise SpecError(f"Unknown column '{c}'")
            if allowed[c]["type"] not in ("number", "money", "hours", "percent"):
                raise SpecError(f"{allowed[c]['label']} is not a number — only count works on it")
            k = f"{fn}_{c}"
            select.append(f"{fn.upper()}({allowed[c]['expr']}) AS {k}")
            out_cols.append({"key": k, "label": f"{fn.capitalize()} of {allowed[c]['label']}", "type": allowed[c]["type"]})
        if not has_count:
            # A grouped report always carries the row count next to any sum / avg (the page and
            # the tester script read a "Count" column on every grouped run).
            select.append("COUNT(*) AS count")
            out_cols.append({"key": "count", "label": "Count", "type": "number"})
        sql = "SELECT " + ", ".join(select) + " " + " ".join(where) + " GROUP BY " + ", ".join(allowed[c]["expr"] for c in group_by)
    else:
        if not cols:
            raise SpecError("Pick at least one column")
        select = [f"{allowed[c]['expr']} AS {c}" for c in cols]
        out_cols += [{"key": c, "label": allowed[c]["label"], "type": allowed[c]["type"]} for c in cols]
        sql = "SELECT " + ", ".join(select) + " " + " ".join(where)
    # sort
    keys = {c["key"] for c in out_cols}
    order = []
    for s in spec.get("sort") or []:
        if not isinstance(s, dict) or s.get("col") not in keys:
            continue
        order.append(f"{s['col']} {'DESC' if (s.get('dir') or 'asc').lower() == 'desc' else 'ASC'} NULLS LAST")
    if order:
        sql += " ORDER BY " + ", ".join(order)
    limit = max(1, min(int(spec.get("limit") or 1000), MAX_ROWS))
    sql += f" LIMIT {limit}"
    # the org zone goes in as a quoted literal (validated name), so GROUP BY sees identical expressions
    sql = sql.replace("{TZ}", "'" + _safe_tz(tz) + "'")
    return {"sql": sql, "params": params, "columns": out_cols, "grouped": bool(group_by), "limit": limit}


_TZ_NAME = re.compile(r"^[A-Za-z]+(?:/[A-Za-z0-9_+\-]+){0,2}$")


def _safe_tz(tz: str | None) -> str:
    """An IANA zone name safe to inline in SQL; anything else is Eastern (Toronto) time."""
    return tz if tz and _TZ_NAME.match(tz) else "America/Toronto"


def _typed(type_: str, v):
    if type_ == "date":
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            raise SpecError("Dates must look like 2026-10-12")
    if type_ in ("number", "money", "hours", "percent"):
        try:
            return float(v)
        except (TypeError, ValueError):
            raise SpecError(f"'{v}' is not a number")
    if isinstance(v, bool):
        return v
    s = str(v)
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    return s


# ══════════════════════════ files ══════════════════════════════════════════════

def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10] if isinstance(v, date) and not isinstance(v, datetime) else v.isoformat()
    return str(v)


def to_csv(columns: list[dict], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c["label"] for c in columns])
    for r in rows:
        line = []
        for c in columns:
            s = _cell_text(r.get(c["key"]))
            if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
                s = "'" + s                                  # no formula injection
            line.append(s)
        w.writerow(line)
    return "﻿" + buf.getvalue()


def to_xlsx(columns: list[dict], rows: list[dict], sheet: str = "Report") -> bytes:
    """A minimal, dependency-free .xlsx (inline strings, numbers as numbers)."""
    def cell(ref, v):
        if isinstance(v, bool):
            return f'<c r="{ref}" t="inlineStr"><is><t>{"TRUE" if v else "FALSE"}</t></is></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        s = _cell_text(v)
        if not s:
            return ""
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(s)}</t></is></c>'

    def ref(ci, ri):
        letters = ""
        n = ci + 1
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return f"{letters}{ri + 1}"

    lines = ['<row r="1">' + "".join(cell(ref(i, 0), c["label"]) for i, c in enumerate(columns)) + "</row>"]
    for ri, r in enumerate(rows, start=1):
        lines.append(f'<row r="{ri + 1}">' + "".join(cell(ref(ci, ri), r.get(c["key"])) for ci, c in enumerate(columns)) + "</row>")
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                 + "".join(lines) + "</sheetData></worksheet>")
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets><sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
             '</Types>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


# ══════════════════════════ schedules ══════════════════════════════════════════

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def valid_local_time(v: str | None) -> bool:
    return bool(v and _HHMM.match(v))


def next_run_local(cadence: str, local_time: str, tz: str | None, weekday: int, day_of_month: int,
                   after: datetime) -> datetime:
    """The first run strictly after ``after`` for a send time on the org's WALL clock.

    ``local_time`` "HH:MM", weekday (Mon = 0) and day of month are all read in ``tz`` (default
    America/Toronto), so "Monday 09:00" really is Monday morning on the org's clock. Returns a UTC instant (stored as is)."""
    from shared.utils.org_time import as_utc, tz_for
    z = tz_for(tz)
    hh, mm = (int(x) for x in (local_time if valid_local_time(local_time) else "09:00").split(":"))
    a = as_utc(after).astimezone(z)
    base = a.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cadence == "daily":
        d = base if base > a else base + timedelta(days=1)
    elif cadence == "weekly":
        wd = max(0, min(6, int(weekday or 0)))
        d = base + timedelta(days=(wd - base.weekday()) % 7)
        d = d if d > a else d + timedelta(days=7)
    else:
        dom = max(1, min(28, int(day_of_month or 1)))
        d = base.replace(day=dom)
        if d <= a:
            m = d.month % 12 + 1
            d = d.replace(year=d.year + (1 if m == 1 else 0), month=m)
    return d.astimezone(timezone.utc)


def schedule_next(s, after: datetime) -> datetime:
    """Next run for a ReportSchedule row: local wall time when set, else the legacy UTC hour."""
    if valid_local_time(getattr(s, "local_time", None)):
        return next_run_local(s.cadence, s.local_time, getattr(s, "tz", None), s.weekday, s.day_of_month, after)
    return next_run(s.cadence, s.hour_utc, s.weekday, s.day_of_month, after)


def next_run(cadence: str, hour_utc: int, weekday: int, day_of_month: int, after: datetime) -> datetime:
    """The first run time strictly after ``after`` (UTC). Legacy rows only — new ones use next_run_local."""
    hour_utc = max(0, min(23, int(hour_utc or 0)))
    base = after.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if cadence == "daily":
        return base if base > after else base + timedelta(days=1)
    if cadence == "weekly":
        wd = max(0, min(6, int(weekday or 0)))
        d = base + timedelta(days=(wd - base.weekday()) % 7)
        return d if d > after else d + timedelta(days=7)
    dom = max(1, min(28, int(day_of_month or 1)))
    d = base.replace(day=dom)
    if d > after:
        return d
    m = d.month % 12 + 1
    y = d.year + (1 if m == 1 else 0)
    return d.replace(year=y, month=m)
