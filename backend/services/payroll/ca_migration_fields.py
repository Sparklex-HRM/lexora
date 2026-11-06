"""Canada migration file types + the generic mapping preset.

The preset below is an UNVERIFIED MAPPING: common Canadian register labels, editable on the
Map step, saved per org once confirmed. `verified` stays False until someone checks a real
export.
"""
from __future__ import annotations

from migration_kit import F, norm_header  # noqa: F401

CA_PROVINCES = ("ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT")
PAY_FREQS = ["weekly", "biweekly", "semi_monthly", "monthly"]

CA_FILE_TYPES: dict[str, dict] = {
    "ca_employees": {
        "label": "Employees (Canada)",
        "help": "One row per employee. Employee Code, First name, Province, Pay frequency and Hire date are a must. "
                "Salary OR hourly rate. No SIN in this file — enter SINs on the employee record.",
        "fields": [
            F("employee_code", "Employee Code", True, example="E001"),
            F("first_name", "First name", True, example="Maya"),
            F("last_name", "Last name", example="Tremblay"),
            F("full_name", "Full name", example="Maya Tremblay", help="Only if your file has one Name column."),
            F("email", "Email", example="maya@acme.ca"),
            F("date_of_birth", "Date of birth", kind="date", example="1991-03-02"),
            F("hire_date", "Hire date", True, kind="date", example="2024-01-15"),
            F("termination_date", "Termination date", kind="date", example=""),
            F("province", "Province of employment", True, example="ON", help="2-letter code: ON BC AB QC MB SK NS NB NL PE NT NU YT."),
            F("department", "Department", example="Operations"),
            F("job_title", "Job title", example="Analyst"),
            F("pay_type", "Pay type", kind="choice", example="salary", choices=["salary", "hourly"]),
            F("annual_salary", "Annual salary (CA$)", kind="number", example="78000"),
            F("hourly_rate", "Hourly rate (CA$)", kind="number", example="0"),
            F("standard_hours_per_week", "Standard hours / week", kind="number", example="40"),
            F("pay_frequency", "Pay frequency", True, kind="choice", example="biweekly", choices=PAY_FREQS),
            F("td1_federal_claim", "TD1 federal claim (CA$)", kind="number", example="", help="Blank = basic personal amount."),
            F("td1_provincial_claim", "TD1 provincial claim (CA$)", kind="number", example=""),
            F("additional_tax_per_period", "Extra tax per pay (CA$)", kind="number", example="0"),
            F("cpp_exempt", "CPP exempt?", kind="yesno", example="N"),
            F("ei_exempt", "EI exempt?", kind="yesno", example="N"),
            F("vacation_percent", "Vacation pay %", kind="number", example="4"),
            F("bank_institution", "Bank institution (3 digits)", example="004"),
            F("bank_transit", "Bank transit (5 digits)", example="12345"),
            F("bank_account", "Bank account number", example="1234567"),
            F("status", "Status", kind="choice", example="active", choices=["active", "terminated", "on_leave", "onboarding"]),
        ],
    },
    "ca_td1": {
        "label": "TD1 claims (Canada)",
        "help": "One row per employee: the TD1 total claim amounts from the signed federal and provincial forms.",
        "fields": [
            F("employee_code", "Employee Code", True, example="E001"),
            F("td1_federal_claim", "TD1 federal claim (CA$)", True, kind="number", example="16452"),
            F("td1_provincial_claim", "TD1 provincial claim (CA$)", kind="number", example="12989"),
            F("additional_tax_per_period", "Extra tax per pay (CA$)", kind="number", example="0"),
            F("cpp_exempt", "CPP exempt?", kind="yesno", example="N"),
            F("ei_exempt", "EI exempt?", kind="yesno", example="N"),
        ],
    },
    "ca_salary_history": {
        "label": "Salary history (Canada)",
        "help": "One row per salary in force: effective-from date and the annual salary or hourly rate. Oldest first is fine; rows are sorted.",
        "fields": [
            F("employee_code", "Employee Code", True, example="E001"),
            F("effective_from", "Effective from", True, kind="date", example="2025-01-01"),
            F("effective_to", "Effective to", kind="date", example="", help="Blank = still in force (or until the next row)."),
            F("pay_type", "Pay type", kind="choice", example="salary", choices=["salary", "hourly"]),
            F("annual_amount", "Annual salary (CA$)", kind="number", example="72000"),
            F("hourly_rate", "Hourly rate (CA$)", kind="number", example=""),
            F("reason", "Reason", example="annual_increment", help="opening, annual_increment, promotion, correction …"),
            F("note", "Note", example=""),
        ],
    },
    "ca_ytd_monthly": {
        "label": "YTD payroll per month (Canada)",
        "help": "One row per employee per month already paid by the old system THIS calendar year. "
                "These roll into the T4 ledger so CPP/EI caps and year-end slips are right.",
        "fields": [
            F("employee_code", "Employee Code", True, example="E001"),
            F("month", "Month", True, kind="month", example="Jan-2026"),
            F("gross", "Gross pay (CA$)", True, kind="number", example="6500"),
            F("pensionable", "CPP pensionable earnings (CA$)", kind="number", example="6500", help="Blank = same as gross."),
            F("insurable", "EI insurable earnings (CA$)", kind="number", example="6500", help="Blank = same as gross."),
            F("cpp", "CPP employee (CA$)", kind="number", example="369.49"),
            F("cpp2", "CPP2 employee (CA$)", kind="number", example="0"),
            F("ei", "EI employee (CA$)", kind="number", example="105.95"),
            F("qpip", "QPIP employee (CA$)", kind="number", example="0", help="Québec only."),
            F("income_tax", "Income tax withheld (CA$)", kind="number", example="1180.20"),
            F("vac_accrued", "Vacation pay accrued (CA$)", kind="number", example="260"),
            F("vac_paid", "Vacation pay paid (CA$)", kind="number", example="0"),
            F("net", "Net pay (CA$)", kind="number", example="4844.36"),
        ],
    },
    "ca_leave_balances": {
        "label": "Leave balances (Canada)",
        "help": "One row per employee per leave type: opening balance for this year and days already used.",
        "fields": [
            F("employee_code", "Employee Code", True, example="E001"),
            F("leave_type", "Leave type code", True, example="VAC", help="Must match a leave type in Master Data."),
            F("opening", "Opening balance (days)", True, kind="number", example="15"),
            F("used", "Used so far (days)", kind="number", example="3"),
            F("fiscal_year", "Year", kind="number", example="2026"),
        ],
    },
}
CA_FILE_TYPE_ORDER = ["ca_employees", "ca_td1", "ca_salary_history", "ca_ytd_monthly", "ca_leave_balances"]

NOT_BUILT = {"documents": "Employee documents (contracts, TD1 PDFs) — not built: the file store is not reachable in this build; "
                          "keep the documents in your vault and link them later."}


def ca_fields_of(ft: str) -> list[dict]:
    return CA_FILE_TYPES[ft]["fields"]


# Canada keys first; migration_kit.SYNONYMS covers the generic register labels.
CA_SYNONYMS = {
    "hire_date": ["hiredate", "dateofhire", "startdate", "joindate", "originalhiredate"],
    "termination_date": ["terminationdate", "dateoftermination", "enddate", "lastday", "lastdayworked", "separationdate"],
    "province": ["province", "provinceofemployment", "prov", "workprovince", "taxprovince"],
    "job_title": ["jobtitle", "title", "position", "designation", "role"],
    "pay_type": ["paytype", "salaryorhourly", "employeetype", "compensationtype"],
    "annual_salary": ["annualsalary", "salary", "annualpay", "yearlysalary", "basesalary", "annualrate"],
    "hourly_rate": ["hourlyrate", "rate", "wage", "hourlywage", "payrate"],
    "standard_hours_per_week": ["standardhoursperweek", "hoursperweek", "weeklyhours", "stdhours", "hours"],
    "pay_frequency": ["payfrequency", "frequency", "payschedule", "paycycle", "payperiodtype"],
    "td1_federal_claim": ["td1federalclaim", "td1federal", "federalclaim", "federaltd1", "td1fed", "federalclaimamount", "claimcodefederal"],
    "td1_provincial_claim": ["td1provincialclaim", "td1provincial", "provincialclaim", "provincialtd1", "td1prov", "provincialclaimamount"],
    "additional_tax_per_period": ["additionaltaxperperiod", "additionaltax", "extratax", "extrataxperpay", "additionalwithholding"],
    "cpp_exempt": ["cppexempt", "cppexemption", "exemptcpp"],
    "ei_exempt": ["eiexempt", "eiexemption", "exemptei"],
    "vacation_percent": ["vacationpercent", "vacationpay", "vacationrate", "vacpct", "vacation"],
    "bank_institution": ["bankinstitution", "institution", "institutionnumber", "bankno", "banknumber"],
    "bank_transit": ["banktransit", "transit", "transitnumber", "branchnumber"],
    "bank_account": ["bankaccount", "accountnumber", "accountno", "account"],
    "effective_to": ["effectiveto", "todate", "enddate", "until"],
    "annual_amount": ["annualamount", "annualsalary", "salary", "amount", "annual"],
    "reason": ["reason", "changereason", "revisionreason", "type"],
    "note": ["note", "notes", "comment", "comments", "remarks"],
    "pensionable": ["pensionable", "cpppensionable", "pensionableearnings", "cppearnings", "box26"],
    "insurable": ["insurable", "eiinsurable", "insurableearnings", "eiearnings", "box24"],
    "cpp": ["cpp", "cppemployee", "cppcontribution", "cppee", "box16", "qpp", "qppemployee"],
    "cpp2": ["cpp2", "cpp2employee", "secondcpp", "box16a", "qpp2"],
    "ei": ["ei", "eiemployee", "eipremium", "eipremiums", "eiee", "box18"],
    "qpip": ["qpip", "rqap", "qpipemployee", "ppip"],
    "income_tax": ["incometax", "tax", "federaltax", "taxwithheld", "incometaxdeducted", "box22", "totaltax"],
    "vac_accrued": ["vacaccrued", "vacationaccrued", "vacationearned", "vacationpayaccrued"],
    "vac_paid": ["vacpaid", "vacationpaid", "vacationpaypaid", "vacationtaken"],
}


def ca_auto_map(headers: list[str], file_type: str) -> dict[str, str | None]:
    """Exact-synonym then prefix/suffix match, Canada keys first, generic synonyms as fallback."""
    from migration_kit import SYNONYMS
    normed = {norm_header(h): h for h in headers if h is not None and str(h).strip()}
    out: dict[str, str | None] = {}
    taken: set[str] = set()
    keys = [f["key"] for f in ca_fields_of(file_type)]
    for k in keys:
        cands = [k.replace("_", "")] + CA_SYNONYMS.get(k, []) + SYNONYMS.get(k, [])
        hit = next((normed[c] for c in cands if c in normed and normed[c] not in taken), None)
        if hit:
            out[k] = hit; taken.add(hit)
    for k in keys:
        if k in out:
            continue
        cands = sorted([k.replace("_", "")] + CA_SYNONYMS.get(k, []) + SYNONYMS.get(k, []), key=len, reverse=True)
        hit = None
        for c in cands:
            if len(c) < 4:
                continue
            for nh, raw in normed.items():
                if raw not in taken and (nh.startswith(c) or nh.endswith(c)):
                    hit = raw; break
            if hit:
                break
        out[k] = hit
        if hit:
            taken.add(hit)
    return out


# ───────────── mapping presets (canonical → export header) — UNVERIFIED ─────────────
PRESETS: dict[str, dict] = {
    "generic_ca": {"name": "Generic Canadian register (CSV)", "verified": False, "source_url": "",
                   "note": "Column names most Canadian payroll registers use; confirm on the Map step.",
                   "mappings": {
                       "ca_employees": {"employee_code": "Employee ID", "first_name": "First Name", "last_name": "Last Name", "email": "Email",
                                        "hire_date": "Hire Date", "province": "Province", "pay_frequency": "Pay Frequency",
                                        "annual_salary": "Annual Salary", "hourly_rate": "Hourly Rate", "td1_federal_claim": "Federal Claim",
                                        "td1_provincial_claim": "Provincial Claim"},
                       "ca_ytd_monthly": {"employee_code": "Employee ID", "month": "Pay Month", "gross": "Gross", "cpp": "CPP", "cpp2": "CPP2",
                                          "ei": "EI", "income_tax": "Income Tax", "net": "Net Pay"},
                       "parallel_run": {"employee_code": "Employee ID", "gross": "Gross", "income_tax": "Income Tax", "cpp": "CPP", "cpp2": "CPP2",
                                        "ei": "EI", "net": "Net Pay"}}},
}


def preset_mapping(vendor: str, file_type: str) -> dict:
    return dict(((PRESETS.get(vendor) or {}).get("mappings") or {}).get(file_type) or {})


def presets_summary() -> list[dict]:
    return [{"code": c, "name": v["name"], "verified": v["verified"], "source_url": v["source_url"], "note": v["note"],
             "file_types": sorted(v["mappings"].keys())} for c, v in PRESETS.items()]
